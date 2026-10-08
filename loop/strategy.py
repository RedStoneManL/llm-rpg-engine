"""loop.strategy — TurnStrategy ABC + AuthorStrategy (甲) + HybridStrategy (丙).

TurnStrategy:
    ABC defining produce(registry, world, scene, player_input, *, provider,
                         embedder=None, repair=None) -> TurnCommit.

AuthorStrategy (甲):
    Calls assemble_context, builds system+user prompts, calls
    provider.complete_json, and returns TurnCommit.from_dict(data).

HybridStrategy (丙):
    Two-call approach: (1) provider.complete for free-form prose narration;
    (2) provider.complete_json with a grounded author prompt (full context +
    the prose) to produce structured TurnCommit sections.  narration is
    forced to the prose from call 1.  Prose is frozen across repair attempts;
    only the structure conversation continues (agent loop).
"""
from __future__ import annotations

from loop.physical_contracts import LINKS_GUIDANCE

import abc
import json
import re
from typing import Any

from context.assembler import assemble_context
from kernel.registry import Registry
from kernel.clock import band_name
from kernel.turncommit import TurnCommit
from llm.provider import _parse_json_object, json_call
from llm.tools import build_tool_registry
from engine.log import get_logger
from engine import settings as _settings
from loop.lore_disclosure import station_push_fragment

log = get_logger("loop.strategy")


def _bound_actor_id(world, scene):
    """Narrator calls require an actual actor; generic DM assembly is separate."""
    actor = scene.get('protagonist')
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('Narrator generation requires a bound protagonist id')
    graph = world.get('systems', {}).get('ontology')
    # Empty-world author fixtures/bootstrap may declare the first actor in the
    # generated commit. Once a world has entities, never guess a missing actor
    # or treat a place/object as the observer of private world information.
    if graph is not None and graph.entities:
        entity = graph.get_entity(actor)
        if entity is None or entity.etype != 'Person':
            raise ValueError('Narrator protagonist must be an existing Person')
    return actor

# Compaction: when a turn's prompt tokens cross COMPACTION_RATIO of the model
# context window, flag the next fresh turn to rebuild full context (re-assemble
# the index/recent/summary tiers + reset the running thread).
CONTEXT_WINDOW = 200_000
COMPACTION_RATIO = 0.70

# Neutral fallback shown when the model output can't be parsed AND no narration
# can be salvaged. NEVER show the raw blob — it carries the structured commit
# (incl. secrecy="secret" facts). (#R5)
_PARSE_FAIL_NARRATION = "（这一刻，周遭并无明显变化。）"


class AuthorOutputError(ValueError):
    """An unusable whole-turn response, never player-facing fallback prose.

    Only a fixed status code crosses this boundary; the raw response stays in
    the strategy's transient messages, where structured secrets belong.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Unusable Author output: {code}")


def _author_narration(value) -> str:
    # Preserve the established array-of-paragraphs compatibility, without
    # turning null, objects, numbers or mixed arrays into display strings.
    if isinstance(value, list) and all(isinstance(p, str) for p in value):
        value = "\n\n".join(value)
    if not isinstance(value, str) or not value.strip():
        raise AuthorOutputError("invalid_narration")
    return value


def _author_commit(raw) -> TurnCommit:
    """Strict production contract; generic salvage helpers remain separate."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        raise AuthorOutputError("invalid_json") from None
    if not isinstance(data, dict):
        raise AuthorOutputError("non_object_json")
    if "narration" not in data:
        raise AuthorOutputError("missing_narration")
    data["narration"] = _author_narration(data["narration"])
    return TurnCommit.from_dict(data)

# Re-ask sent after a native tool loop returned bare prose (no JSON envelope). It
# reuses the researched context already in the working messages, so no re-research
# happens — it only asks the model to wrap the turn it just wrote as the structured
# commit. A genuine "nothing changed" turn is expressed as empty sections ([]), not
# a parse failure.
_REASK_JSON = (
    "停。你刚才用散文写了这一回合，但漏了要求的 JSON 外壳。现在请【只】输出一个 JSON 对象，"
    "不要任何额外文字、不要 ``` 代码围栏：\n"
    "- narration：就用你刚写的那段叙事（原文照搬）。\n"
    "- 其余结构化段（moves/places/cast/facts/knowledge/clock 等）：只写这一回合【真正发生】"
    "的变更；某段这一回合没有变化就给空数组 []（空数组=合法的“本段无变化”）。\n"
    "直接输出以 { 开头的 JSON。"
)


def _salvage_narration(raw) -> str | None:
    """Best-effort extract ONLY the "narration" string value from malformed JSON.

    Reads the value char-by-char (honoring the common \\-escapes \\n\\t\\r\\"\\\\/;
    \\uXXXX passes through literally — this is the degraded salvage path), stopping
    at the closing quote — so it never includes the structured sections that follow
    (facts/relations/knowledge/clock). Returns the text, or None if absent.
    """
    if not isinstance(raw, str):
        return None
    m = re.search(r'"narration"\s*:\s*"', raw)
    if not m:
        return None
    i, out, esc = m.end(), [], {"n": "\n", "t": "\t", "r": "\r",
                                '"': '"', "\\": "\\", "/": "/"}
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append(esc.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    text = "".join(out).strip()
    return text or None


def _data_or_safe(raw) -> dict:
    """Parse the model output into a commit dict, falling back WITHOUT leaking the
    raw blob. Never returns {"narration": raw}: that would dump the structured
    commit (incl. secret facts) into the player-facing narration. (#R5)
    """
    data = _parse_json_object(raw)
    if data is not None:
        return data
    salvaged = _salvage_narration(raw)
    if salvaged:
        log.warning("produce: model output not valid JSON; salvaged narration only, "
                    "dropped structured sections (no raw leak)")
        return {"narration": salvaged}
    # Reasoning models — especially after a native tool loop — often drop the JSON
    # envelope entirely and answer in pure prose. Bare prose is NOT the #R5 leak
    # risk: that risk is a broken JSON commit blob carrying secrecy='secret' facts,
    # which always contains '{'. So when there is no JSON object anywhere, use the
    # whole output as the turn's narration rather than discarding a good turn to the
    # neutral fallback. (Structured sections are simply absent this turn.)
    stripped = raw.strip() if isinstance(raw, str) else ""
    if stripped and "{" not in stripped:
        log.warning("produce: model emitted bare prose (no JSON envelope); using it "
                    "as narration — no structured sections this turn")
        return {"narration": stripped}
    log.warning("produce: model output not valid JSON and no narration salvageable; "
                "using neutral fallback")
    return {"narration": _PARSE_FAIL_NARRATION}

# ---------------------------------------------------------------------------
# Schema for complete_json — permissive; real checking is validate_commit.
# ---------------------------------------------------------------------------

TURNCOMMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "narration": {"type": "string"},
    },
    "required": ["narration"],
    "additionalProperties": True,
}

# ---------------------------------------------------------------------------
# Verbosity fragments — injected into the system prompt at produce-time.
# ---------------------------------------------------------------------------

_VERBOSITY_FRAGMENT: dict[str, str] = {
    "concise": (
        "每回合明显推进剧情/场景/时钟；氛围一两笔带过；克制篇幅，直奔关键。"
    ),
    "medium": (
        "推进为主，适度氛围；不要长篇铺陈。"
    ),
    "rich": (
        "浓墨铺陈，不设字数限制；重环境氛围、角色神态内心、有张力的对话；展示而非告知。"
    ),
}

_NARRATE_VERBOSITY_FRAGMENT: dict[str, str] = {
    "concise": (
        "篇幅克制：氛围一两笔，直奔当回合最关键的进展；不堆叠长描写。"
    ),
    "medium": (
        "适度叙事：氛围到位即止，情节推进优先；避免长篇铺陈。"
    ),
    "rich": (
        "浓墨铺陈，篇幅随情境而定，不设字数限制——该浓就浓，该收就收。"
    ),
}


# ---------------------------------------------------------------------------
# Prompt template strings — __VERBOSITY__ placeholder is substituted per-call
# (using str.replace, not str.format, to avoid conflicts with JSON {} examples).
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
你是主持人（DM），以主角视角叙事，同时记录世界变化。

__STYLE__【narration 文风】__VERBOSITY__具体可感、不空泛；展示而非告知；推进局面但绝不替玩家决定下一步；严守保密事实，绝不在 narration 中直接揭露。

【输出格式】返回**一个 JSON 对象**；下列每段都显式标了【必填】或【可选】，照此输出（文末另有一个完整范例，照抄它的结构）：
- 【必填】narration：字符串，本回合面向玩家的叙事散文。
- 【必填】moves：[{"who":移动的实体id, "to":目标地点id}]——谁移动到哪；**没人移动 → 给 []**。新人物在正文中实际来到/首次出现在某地点，也必须用同一个新 id 写 moves 的 who/to；cast.create 不会自动安放位置。仅被提及或远程交谈的人物，不得默认搬到主角身边。
- 【必填】places：[{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]——本回合**新出现**的地点；**没有 → 给 []**；kind 只能取列出的五个值，别自造（如 ruin/forest）。
- 【必填】cast：新登场且有戏的 NPC 用 [{"id":...,"op":"create","sketch":...,"goal":...,"name":可选}]（id/sketch/goal 必填）。主角本回合实际同场见到且正文介绍的新人，name 填正文逐字出现的可见称呼；不知真名时可用职务称呼，不要编造真名。后台或未介绍的人物可省略 name；已有角色的某个属性实际改变，用 [{"id":已有角色id,"op":"evolve","predicate":"goal","value":"新的目标"}]，更新人物素描则 predicate="sketch"、value=新素描。每条 evolve 必须有 id/predicate/value；多个属性分多条。没有实际变化就给 []；纯路人可省略。characters_query 显示已在场的 NPC 时复用返回的 id，有属性变化才 evolve，避免重复创建同一人物。
- 【必填】facts：[{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]——本回合确立的**客观事实**（subject/predicate/value 必填）；**没有 → 给 []**；只记确有意义的事实，勿把布景滥造成 fact；**同一事物用一条 fact 说清，别拆成多条近义事实灌水**。secrecy 取 "public"|"restricted"|"secret"：街坊皆知的标 "public"（路人/打听才转述得到）；需特定人才知的秘密/真相/谎言标 "secret"（或 "restricted"）；拿不准就【不写该字段】（默认不进公开层、绝不外泄）。
- 【必填】clock：[{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**恰好一个元素，永不为空**）——一天分四段（晨→中午→下午→夜晚），days=过了几整天、bands=【跨过了几个时段】（只在时段名真正切换时才计一段，可>3，引擎自动进位）；reason 必填。明确结束时刻（如睡到明天清晨）优先用 {"advance":true,"target":{"day":绝对天数,"band":0到3},"reason":"为什么"}，由引擎算经过时间；target 与 days/bands 不能同时出现，晨=0、中午=1、下午=2、夜晚=3。只有持续时长才用 days/bands，避免把“明天”又与跨夜时段重复相加。同一时段内的细碎动作（几分钟、一次交谈、拂晓动手随即脱身）不构成推进，给 {"advance":false,"days":0,"bands":0,"reason":"..."}。
- 【可选】__LINKS_GUIDANCE__
- 【可选】entities：[{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填；仅在需要凭空声明实体时用）
- 【可选】items：创建物品用 {"op":"create","id":"物品id"}；转移用 {"op":"transfer","item":"物品id","from":"转移前持有者id","to":"新持有者id"}。item 必须是 Object，to 必须是 Person 或 Place。已有人持有的物品必须给出与当前账本一致的 from；首次放置未被持有的物品才可省略 from 或给 null。先创建实体，再按顺序转移；A→B→C 的第二次 from 是 B。不要通过 relations 写 held_by，不要用重复创建实体改变其类型。from 仅表示来源，不代表同意、授权或合法性；只记录正文中实际发生的转移。
- 【可选】relations：[{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
- 【可选】promises：只允许 [{"op":"fulfill","id":"已登记归还约定id"}]，且物品必须确实按 canonical 持有记录返回该约定对象。新约定仅由引擎从玩家原话和确认答复识别并登记，不能凭你生成的正文创建；不能改写物品、双方或绝对截止日。实际归还后引擎也会自动记录完成，没有需要确认的完成项就省略此段。
- 【可选】knowledge：记录"谁知道了什么"——详见下【信息视野】
- 【可选】world：区域/世界级事件波及的地点——详见下【世界事件】
- 【可选】quests：任务的开启/浮现/推进/收束——详见下【任务系统】


【铁律】上面 6 个【必填】段每回合都必须出现：narration 给散文、clock 给恰好一个元素、moves/places/cast/facts **没有该类变化就给 []（空数组）**。条目**要么字段齐全、要么根本别放**——宁可给 [] 也别塞一个缺字段的半成品（缺字段会被打回、拖慢一整局）。【可选】段没有就直接省略、不要硬凑。

【信息视野·knowledge】本引擎追踪"谁知道什么",并据此决定下回合对主角【保密 / 可见】。当本回合有角色【得知 / 识破 / 被告知 / 无意获悉 / 主动透露】重要信息——秘密、线索、真相、谎言、关键数值——用 knowledge 段记录信息的流动。**尤其:凡本回合主角刚【得知/亲历】的重要事实，只写进 facts 是不够的——应在 knowledge 里给【引擎绑定】actor_id 所指的实际主角记一条 told，不要把角色称谓当实体 id。物品归属只通过 items/held_by 记录，不在 facts/knowledge 另造同义持有者记录；物品颜色、材质等描述性事实仍可记录。**
- told:      [{"op":"told","knower":知情者id,"fact_key":"实体.属性","value":其所知内容,"via":得知途径(可选)}]
- broadcast: [{"op":"broadcast","fact_key":...,"value":...,"audience":{"faction":阵营id}或{"place":地点id}}]（一群人同时获悉）
fact_key 尽量用 "实体.属性" 形式（如 "断桥.是否可通行"、"商队首领.真实身份"），与世界事实同名——系统据此判断主角是否已知、并在叙事中对其未知之事保密。无人获得新信息时本段可省略（不必写 reason）。

【世界事件·world（可选段）】当本回合发生区域级或世界级的大事——灾难、战争、瘟疫、政权更替、重大变故——用 world 段点名所有受影响的地点，引擎据此向下波及这些地点的子地点。你有完整剧情视野，可点名任意位置的地点（不限当前场景的邻居）：
- world: [{"areas":[受影响地点id, ...], "level":1|2|3, "summary":"一句话事件"}]
areas 用已存在或本回合刚创建的地点 id；level 表示烈度（1 最轻、3 最重）；summary 一句话描述这件事。寻常的个人回合（赶路、对话、独自行动）不必给本段，省略即可（无需写 reason）。

【任务系统·quests（可选段）】本回合若有任务变化，用 quests 段记录：[{"op":"open"|"surface"|"advance"|"resolve","id":任务标识,"summary":"一句话摘要"}]
- open: 玩家刚接取了一条全新的明线任务（NPC 托付/玩家决定追查）；id 必须是全新的（不在当前明账中）；必须提供 summary
- surface: 玩家正在追查的暗线浮出水面（进入明账）；id 须与上文【本地暗线】中的 [id] 标签完全一致——环境推送的每条暗线都标有 [id]，玩家触碰了哪条就 surface 哪个 id，切勿 open 新 id
- advance: 推进一条已在"任务明账"中的明线任务（只能推进明线，不能推进背景暗线）
- resolve: 收束一条已在"任务明账"中的明线任务
无任务变化时省略本段。

【完整范例】一个"对话中得知一个秘密、没移动、没新地点"的回合长这样——照抄这个结构（注意：空的必填段就给 []）：
{"narration":"你压低声音问起那场大火。老者的手停在药罐上，半晌才道：「纵火的，是镖局的人。」",
 "moves":[],
 "places":[],
 "cast":[],
 "facts":[{"subject":"npc_laozhe","predicate":"火灾真凶","value":"镖局所为","secrecy":"secret"}],
 "knowledge":[{"op":"told","knower":__ACTOR_ID_JSON__,"fact_key":"npc_laozhe.火灾真凶","value":"镖局所为","via":"老者亲口"}],
 "clock":[{"advance":false,"days":0,"bands":0,"reason":"同一段对话，时间未实质推进"}]}
——若主角移动了：moves 给 [{"who":__ACTOR_ID_JSON__,"to":"<地点id>"}]；若来了个有戏的新人：cast 给一条齐全的 {"id":"...","op":"create","sketch":"...","goal":"...","name":"..."}，并在 moves 中写 {"who":"同一个新人id","to":"其实际登场地点id"}；不能只创建角色而漏记其实际在场位置。

规则：
1. 只在剧情真正发生该变化时才给对应段落；不要把布景细节（石板、树冠、手掌等）滥造成 entity。
2. 输出合法 JSON 对象，必含 "narration" 字段；只输出 JSON，不附 markdown 代码块或其他包装。
"""

_NARRATE_PROMPT_TEMPLATE = """\
你是主持人（DM），以主角视角进行沉浸式叙事。

__STYLE__【文风】以具体可感的细节叙事，少堆空泛形容。
【写法】
1. 第一/第三人称散文皆可（以中文为主）；__NARRATE_VERBOSITY__
2. 展示而非告知：设定、过往、人物关系通过此刻的细节、动作与后果自然流露，不要直接复述资料。
3. 严守 ⚠️只约束·勿泄露 中的保密事实——绝不直接揭露，可暗示、可让其后果显现。
4. 推进当前局面、给玩家可回应的钩子，但绝不替玩家决定下一步行动。
5. 只输出叙事散文本身，不要任何 JSON / 结构化数据 / 元说明。
"""

# ---------------------------------------------------------------------------
# Prompt builder functions — called per-produce to inject current verbosity.
# ---------------------------------------------------------------------------


def _style_fragment(style: str | None) -> str:
    """Build the optional overarching style/voice directive (#R8).

    Generic: wraps whatever style string the player set, so any voice works
    ("日式轻小说", "冷硬派侦探", ...). Blank -> "" (neutral; byte-identical to the
    pre-#R8 prompt)."""
    s = (style or "").strip()
    if not s:
        return ""
    return f"【文风基调】整体以「{s}」的风格叙述，贯穿全篇。\n\n"


_MACHINE_BOUNDARY = (
    '\n【正文与引擎信息分离】上下文中的引擎绑定、JSON 协议、校验错误和修复要求仅供你内部创作与结构输出，'
    '不得复述进 narration。正文只呈现角色可感知的行动、反应和环境；'
    '没有实际动作的改账请求可以写成没有发生交接，不向玩家讲解内部字段或段落协议。'
    '主角与持有者引用以最新引擎绑定为准，旧正文和自由事实中的别名不覆盖 canonical 记录。'
)

_PLAYER_GOAL_PROGRESS = (
    '\n【回应玩家的办事目标】优先回应玩家本回合明确的请求，区分已有事实、本回合新发生的变化与仍未知的事项。'
    '查询空结果只说明这次未取到可返回记录，不证明世界中不存在、某人不知道，也不授权补写隐藏事实。'
    '沿用 DM 的创作职责：尚未确立且不受已有设定限制的普通虚构细节，可以通过符合情境的新行动或新公开安排确立；'
    '不能把新安排伪装成旧告示早已写明或人物过去已经知道，不能无叙事依据改写既有状态、约定或秘密。'
    '无法直接答复时，情境允许就给出能实际执行的核实动作、明确的再询条件或可行替代路径；'
    '有充分剧情理由时仍可保留未知或拒绝。人物答应核实时，应表现具体行动或后续条件，避免只复述问题而没有推进。'
    '转折应结合当前行动，不用重复线索挤掉主要回应，不强加新任务或替玩家选择。'
    '新发生的重要事实与信息传达须在正文中清楚表现；本调用仍严格遵守原有输出格式，要求结构化记录时才依既有事实/知识协议落账。'
)


def _system_prompt(verbosity: str | None = None, style: str | None = None, *, actor_id=None) -> str:
    """Build the 甲 system prompt with the current (or given) verbosity + style."""
    v = verbosity or _settings.get_verbosity()
    frag = _VERBOSITY_FRAGMENT.get(v, _VERBOSITY_FRAGMENT["medium"])
    s = _settings.get_style() if style is None else style
    return (_SYSTEM_PROMPT_TEMPLATE
            .replace("__LINKS_GUIDANCE__", LINKS_GUIDANCE)
            .replace("__STYLE__", _style_fragment(s))
            .replace("__VERBOSITY__", frag)
            .replace("__ACTOR_ID_JSON__", json.dumps(actor_id if isinstance(actor_id, str)
                     and actor_id else '<当前主角的实际id>', ensure_ascii=False))
            + _PLAYER_GOAL_PROGRESS + _MACHINE_BOUNDARY)


def _narrate_prompt(verbosity: str | None = None, style: str | None = None) -> str:
    """Build the 丙 narration prompt with the current (or given) verbosity + style."""
    v = verbosity or _settings.get_verbosity()
    frag = _NARRATE_VERBOSITY_FRAGMENT.get(v, _NARRATE_VERBOSITY_FRAGMENT["medium"])
    s = _settings.get_style() if style is None else style
    return (_NARRATE_PROMPT_TEMPLATE
            .replace("__STYLE__", _style_fragment(s))
            .replace("__NARRATE_VERBOSITY__", frag) + _PLAYER_GOAL_PROGRESS + _MACHINE_BOUNDARY)


# ---------------------------------------------------------------------------
# Module-level backward-compat aliases — used by existing tests that import
# _SYSTEM_PROMPT / _NARRATE_PROMPT directly.  Reflect the *current* medium
# default so all existing prompt-content assertions still pass.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = _system_prompt("medium")
_NARRATE_PROMPT = _narrate_prompt("medium")

# 丙 HybridStrategy 结构提示(call 2:作者为自己刚写的散文补结构,带全上下文)
_SYSTEM_PROMPT_HYBRID = """\
你是主持人（DM）。你刚写完下面这段叙事散文。现在以"懂世界规则的作者"身份，为这段散文忠实地补出结构化 turn-commit。
规则：
1. 只记录散文中【真实发生】的世界变化；不要新增散文里没有的人物/地点/事件。
2. 上文给出了当前世界状态与已存在实体的 canonical id——散文指向已知对象（主角、已知 NPC、已知地点）时必须复用其原有 id，只为散文中首次出现的新对象创建新 id。
3. 每个段落都是对象数组，下面标了【必填】/【可选】：
   - 【必填】moves: [{"who":实体id, "to":地点id}]——散文里谁移动了；**没有就给 []**。新人物实际来到/首次出现在某地点，同样用新 id 写 who/to；cast.create 不会自动安放位置。仅提及或远程人物不得默认移到主角身边
   - 【必填】places: [{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]——散文里**新出现**的地点；**没有就给 []**；kind 只能取列出五值之一
   - 【必填】cast: 新登场且有戏的 NPC 用 [{"id":...,"op":"create","sketch":...,"goal":...,"name":可选}]（id/sketch/goal 必填）。主角本回合实际同场见到且正文介绍的新人，name 填正文逐字出现的可见称呼；不知真名时可用职务称呼，不要编造真名。后台或未介绍的人物可省略 name；已有角色属性实际改变，用 [{"id":已有角色id,"op":"evolve","predicate":"goal","value":"新的目标"}]，更新素描则 predicate="sketch"、value=新素描。每条 evolve 必须有 id/predicate/value；多个属性分多条，复用原角色 id。散文没有属性变化就给 []；纯路人可省略。
   - 【必填】facts: [{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]——散文确立的客观事实（subject/predicate/value 必填）；**没有就给 []**。secrecy 可选 "public"|"restricted"|"secret"：街坊常识标 public（路人可转述），秘密/真相标 secret，拿不准不写（默认不公开）
   - 【必填】clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**恰好一个元素，永不为空**）——本回合游戏内时间推进多少（一天四段：晨→中午→下午→夜晚；bands=跨过的时段数，只在时段名真正切换时才计，可>3，引擎自动进位）；reason 必填。明确结束时刻优先用 {"advance":true,"target":{"day":绝对天数,"band":0到3},"reason":"为什么"}（晨=0、中午=1、下午=2、夜晚=3），target 不能与 days/bands 同时出现；引擎负责从当前时刻计算推进量。只有持续时长才用 days/bands，勿重复计算跨夜进位。散文里时间明显流逝（入夜、次日、三日后）就按量给出；同一时段内的细碎动作（连续紧接、一次冲刺/夺取）不算推进，给 advance:false 且写 reason，切勿为小动作多推一段。
   - 【可选】__LINKS_GUIDANCE__
   - 【可选】entities: [{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填）
   - 【可选】items: 创建物品用 {"op":"create","id":"物品id"}；转移用 {"op":"transfer","item":"物品id","from":"当前持有者id","to":"新持有者id"}。物品必须是 Object，持有者为 Person 或 Place；未被持有的物品首次放置才可省略 from 或给 null。按行顺序记录 A→B→C，第二次 from 为 B。归属只通过 items/held_by 记录，不另造同义 facts/knowledge；物品颜色材质等描述不受此限制。
   - 【可选】relations: [{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
   - 【可选】promises: 仅 [{"op":"fulfill","id":"已登记归还约定id"}]；必须已有真实物品归还记录，不得凭散文新建约定或改写双方、物品、绝对截止日。新登记只能来自引擎已确认的玩家原话；实际归还由引擎自动同步完成。
   - 【可选】knowledge: 记录"谁知道了什么"——见第 5 条
   - 【可选】world: 区域/世界级事件波及的地点——见第 7 条
   - 【可选】quests: 记录"任务的开启/浮现/推进/收束"——见第 8 条
4. 【铁律】上面 5 个【必填】段（moves/places/cast/facts/clock）每回合都必须出现：clock 给恰好一个元素，moves/places/cast/facts **散文里没有该类变化就给 [](空数组)**。条目要么字段齐全、要么根本别放——宁可 [] 也别塞缺字段的半成品。尤其：散文里主角移动了就必须有 moves、出现新地点就必须有 places，写了却漏记不行。【可选】段没有就直接省略。
5. 【信息视野·knowledge（可选段）】散文中若有角色【得知/识破/被告知/无意获悉/主动透露】重要信息（秘密、线索、真相、谎言），记录到 knowledge 段：told 项 {"op":"told","knower":知情者id,"fact_key":"实体.属性","value":其所知,"via":途径(可选)}；一群人同时获悉用 broadcast 项 {"op":"broadcast","fact_key":...,"value":...,"audience":{"faction":id}或{"place":id}}。fact_key 尽量用 "实体.属性" 形式、与世界事实同名。散文未提及信息易手时省略本段。
6. 只输出合法 JSON（不含 narration），不附任何 markdown 代码块或其他包装。
7. 【世界事件·world（可选段）】散文中若描写了区域级或世界级的大事（灾难、战争、瘟疫、政权更替、重大变故），用 world 段点名所有受影响地点：world: [{"areas":[受影响地点id,...],"level":1|2|3,"summary":"一句话事件"}]。areas 用已存在或本回合刚创建的地点 id；你有完整世界视野，可点名任意位置。寻常个人场景省略本段。
8. 【任务系统·quests（可选段）】散文中若有任务变化，用 quests 段记录：[{"op":"open"|"surface"|"advance"|"resolve","id":任务标识,"summary":"一句话摘要"}]；open=玩家接取全新明线任务（id必须全新，必须提供summary）；surface=暗线浮现进入明账（id须与上文【本地暗线】中 [id] 标签一致，切勿 open 新 id——暗线每条都标有 [id]，散文中玩家触碰了哪条就 surface 该 id）；advance=推进已有明线任务；resolve=收束已有明线任务。id 须与上文【任务·明账】中已列的 id 保持一致（open 除外）。寻常个人场景无任务变化时省略本段。
"""

_SYSTEM_PROMPT_HYBRID = _SYSTEM_PROMPT_HYBRID.replace("__LINKS_GUIDANCE__", LINKS_GUIDANCE)
_SYSTEM_PROMPT_HYBRID += _MACHINE_BOUNDARY


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class TurnStrategy(abc.ABC):
    """Abstract base for turn-commit production strategies."""

    @abc.abstractmethod
    def produce(
        self,
        registry: Registry,
        world: dict,
        scene: dict,
        player_input: str,
        *,
        provider,
        embedder=None,
        repair: str | None = None,
    ) -> TurnCommit:
        """Produce a TurnCommit for the current turn.

        Args:
            registry:     Kernel registry.
            world:        Projected world state.
            scene:        Current scene dict (protagonist, present, day, location).
            player_input: Raw player action string.
            provider:     LLMProvider to call.
            embedder:     Optional embedder for recall ranking.
            repair:       If set, a repair instruction string to append to the
                          user prompt (the previous commit had validation errors).
        """

    def repair_sections(
        self,
        failing_sections: set[str],
        errors: list,
        *,
        provider,
    ) -> dict:
        """Re-emit ONLY the failing sections; keep narration + passing sections intact.

        Continues the existing conversation (self._messages) with a focused
        instruction that asks the LLM to output ONLY a JSON object containing
        the failing section keys — no narration, no other sections.

        Returns a dict of {section_name: new_decl} for the failing sections.
        The caller merges this into the existing commit via commit.sections.update().

        Default implementation falls back to whole-commit re-generation for
        strategies that haven't opted into modular repair.
        """
        raise NotImplementedError("repair_sections not implemented")

    def commit_to_thread(self, narration: str) -> None:
        """Append the just-succeeded turn to the strategy's persistent multi-turn
        thread (if it keeps one). No-op by default."""
        return None


def _build_delta(registry, world: dict, scene: dict, player_input: str) -> str:
    """Compact continuing-turn message for the running conversation.

    The model already holds the world in-thread; this carries only what's new:
    a time/place header, any backstage 暗线 push (station_push_fragment), and the
    player's action. Cheap (no embedder recall — that is a compaction-time cost).
    """
    meta = (world or {}).get("meta", {}) or {}
    day = (scene or {}).get("day") or meta.get("day") or 1
    band = meta.get("band") or 0
    loc = (scene or {}).get("location") or ""
    header = f"【此刻】第 {day} 天 · {band_name(band)}"
    if loc:
        header += f" · 在 {loc}"
    parts = [header]
    # Conversation history is a narrative cache, not the source of world truth.
    # Include current projections on every turn so backstage changes, discoveries
    # and repaired state reach the narrator without waiting for compaction.
    current = assemble_context(registry, world, scene, query=player_input) if registry is not None else ''
    if current:
        parts.append('【最新已提交的世界状态；若与旧对话冲突，以此为准】\n' + current)
    try:
        frag = station_push_fragment(registry, world, scene)
    except Exception:
        frag = None
    if frag:
        parts.append(frag)
    if scene.get('_variation_prompt'):
        parts.append(scene['_variation_prompt'])
    if scene.get('_resolution_prompt'):
        parts.append(scene['_resolution_prompt'])
    parts.append(f"[player] {player_input}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# AuthorStrategy (甲) — one main-LLM call
# ---------------------------------------------------------------------------

class AuthorStrategy(TurnStrategy):
    """Strategy 甲: author prose+structure in one conversational thread.

    First attempt opens a [system, user] conversation; each repair CONTINUES it —
    the model sees its own prior output + the precise validation errors and fixes
    incrementally (agent loop), instead of re-prompting blind each round.
    """

    _messages: list | None = None       # transient working list for the current turn
    _thread: list | None = None         # persistent multi-turn conversation (multiturn)
    _pending_user: str | None = None    # user msg for the in-flight turn (committed on success)
    _compaction_due: bool = False       # set when usage crosses 70%; consumed next fresh turn

    def reset(self) -> None:
        """Discard derived conversation after rewind, reload or a failed cache write."""
        self._messages = self._thread = self._pending_user = None
        self._pending_action = None
        self._compaction_due = False
        self._bound_actor = None
        self._invalid_response_indices = set()

    def commit_to_thread(self, narration: str) -> None:
        """Append [pending user delta, narration prose] to the persistent thread on
        a successful turn. Raw narration stays exact in this cache; copied history
        is JSON-enveloped at request construction. Effects / repair / tool messages
        stay in transient _messages. No-op in stateless mode (_thread is None)."""
        if self._thread is not None and self._pending_user is not None:
            pending_action = getattr(self, '_pending_action', None)
            content = ('[player] ' + pending_action) if pending_action is not None else self._pending_user
            self._thread.append({"role": "user", "content": content})
            self._thread.append({"role": "assistant", "content": narration})
            self._pending_user = None
            self._pending_action = None
            # Keep eight recent exchanges. Facts and long-term recall live in
            # projections and are refreshed independently of this bounded cache.
            if len(self._thread) > 17:
                self._thread = self._thread[:1] + self._thread[-16:]

    def _maybe_flag_compaction(self, provider) -> None:
        """Flag the next fresh turn to rebuild full context when the last call's
        prompt size crossed the compaction threshold. Relies on provider.last_usage
        (None when the provider reports no usage → never flags)."""
        usage = getattr(provider, "last_usage", None)
        tok = usage.get("input") if usage else None
        if tok and tok > CONTEXT_WINDOW * COMPACTION_RATIO:
            self._compaction_due = True

    def _request_messages(self) -> list:
        """Keep rejected raw replies for audit, but never replay them as history.

        Only final response indices are excluded, so researched tool-call/result
        groups and committed history retain their original ordering and shape.
        """
        invalid = getattr(self, "_invalid_response_indices", set())
        if not invalid:
            return self._messages
        return [message for i, message in enumerate(self._messages) if i not in invalid]

    def _parse_response(self, raw) -> TurnCommit:
        try:
            return _author_commit(raw)
        except AuthorOutputError:
            self._invalid_response_indices.add(len(self._messages) - 1)
            raise

    def produce(
        self,
        registry: Registry,
        world: dict,
        scene: dict,
        player_input: str,
        *,
        provider,
        embedder=None,
        repair: str | None = None,
    ) -> TurnCommit:
        actor_id = _bound_actor_id(world, scene)
        previous_actor = getattr(self, '_bound_actor', actor_id)
        if previous_actor != actor_id:
            self.reset()
        self._bound_actor = actor_id
        multiturn = (_settings.get_conversation_mode() == "multiturn")
        if repair is None or self._messages is None:
            self._invalid_response_indices = set()
        if repair is None:
            self._pending_action = player_input

        if repair is not None and self._messages is not None:
            # Repair: continue the working list in place (prior assistant output is
            # already there, so the model fixes in place). Never touches the thread.
            self._messages.append({"role": "user", "content": repair})
        elif multiturn and self._thread is not None and not self._compaction_due:
            # Continuing turn: append a compact delta onto a copy of the thread.
            delta = _build_delta(registry, world, scene, player_input)
            self._pending_user = delta
            # Both warm legacy caches and newly committed turns store exact prose.
            # Wrap only these copied historical assistant entries, exactly once;
            # never serialize current raw JSON, repair replies or tool messages.
            history = [
                {**message, "content": json.dumps({"narration": message["content"]},
                                                   ensure_ascii=False)}
                if message.get("role") == "assistant" else dict(message)
                for message in self._thread
            ]
            self._messages = history + [{"role": "user", "content": delta}]
        else:
            # First turn / compaction / stateless: full context rebuild.
            ctx = assemble_context(registry, world, scene,
                                   query=player_input, embedder=embedder)
            # station_push_fragment: 暗 ambient B disclosure; None when no 暗 lines
            # in range or no LoreSystem → no-op for worlds without lore.
            frag = station_push_fragment(registry, world, scene)
            if frag:
                ctx = (ctx + "\n\n" + frag) if ctx else frag
            parts = []
            if ctx:
                parts.append(ctx)
            if scene.get('_variation_prompt'):
                parts.append(scene['_variation_prompt'])
            if scene.get('_resolution_prompt'):
                parts.append(scene['_resolution_prompt'])
            parts.append(f"[player] {player_input}")
            full_user = "\n\n".join(parts)
            self._pending_user = full_user
            self._messages = [
                {"role": "system", "content": _system_prompt(actor_id=actor_id)},
                {"role": "user", "content": full_user},
            ]
            if multiturn:
                # Reset the thread to a bare system base; the turn's user + narration
                # are appended by commit_to_thread on success (so no duplication).
                self._thread = [{"role": "system", "content": _system_prompt(actor_id=actor_id)}]
                self._compaction_due = False

        log.debug("AuthorStrategy.produce msgs=%d repair=%r multiturn=%s",
                  len(self._messages), bool(repair), multiturn)

        # DD6 capability gate: tool loop ONLY on fresh turns when the provider
        # supports it and the POV tool registry is non-empty. Repairs use plain
        # complete_messages (no re-research).
        if repair is None and provider.supports_tools():
            tool_reg = build_tool_registry(registry, world, scene)  # POV set (dm=False)
            schemas = tool_reg.schemas()
            if schemas:
                rounds = _settings.get_max_tool_rounds()
                raw = json_call(provider.complete_with_tools,
                    self._messages, schemas, tool_reg.execute,
                    max_tool_rounds=rounds,
                )
                self._messages.append({"role": "assistant", "content": raw})
                if repair is None and multiturn:
                    self._maybe_flag_compaction(provider)
                # Unusable tool-final output uses produce_turn's shared repair
                # budget, just like the non-tool route; no hidden re-ask here.
                return self._parse_response(raw)

        # Plain complete_messages (all non-tool providers + all repair turns).
        # Stateless turns rebuild context above and do not retain a thread.
        raw = json_call(provider.complete_messages, self._request_messages())
        self._messages.append({"role": "assistant", "content": raw})
        if repair is None and multiturn:
            self._maybe_flag_compaction(provider)
        return self._parse_response(raw)

    def _reask_json(self, prose: str, provider) -> str:
        """After a tool loop returned bare prose, ask once for the JSON commit.

        Reuses the current working messages (tool results already in them → no
        re-research). Returns the re-asked output if it parses as JSON, else the
        original prose (which `_data_or_safe` salvages as narration). The re-ask
        exchange is NOT persisted onto `self._messages`; only the final chosen
        `raw` is appended by the caller, keeping the thread clean.
        """
        reask_msgs = self._messages + [
            {"role": "assistant", "content": prose},
            {"role": "user", "content": _REASK_JSON},
        ]
        try:
            reasked = json_call(provider.complete_messages, reask_msgs)
        except Exception:
            log.warning("produce: JSON re-ask call failed; keeping bare prose")
            return prose
        data = _parse_json_object(reasked)
        if data is not None:
            # This call wraps existing prose; it may omit narration while
            # producing valid JSON. Preserve the already-authored visible text.
            # Only bare prose is safe to preserve verbatim. A malformed JSON
            # envelope may contain private fields and must never become prose.
            if prose.strip() and '{' not in prose:
                data['narration'] = prose
            else:
                recovered = data.get('narration')
                if not isinstance(recovered, str) or '{' in recovered:
                    data['narration'] = _salvage_narration(prose) or _PARSE_FAIL_NARRATION
            log.info("produce: recovered structured commit via JSON re-ask")
            return json.dumps(data, ensure_ascii=False)
        log.warning("produce: JSON re-ask still not valid JSON; keeping bare prose")
        return prose

    def repair_sections(
        self,
        failing_sections: set[str],
        errors: list,
        *,
        provider,
    ) -> dict:
        """Modular repair: re-emit ONLY the failing sections.

        Continues the existing authoring conversation (self._messages) with a
        focused instruction.  The LLM is asked to return a JSON object that
        contains ONLY the failing section keys — no narration, no passing sections.

        Returns the partial dict of {section_name: new_decl}; the caller merges
        it into the existing commit via commit.sections.update().
        """
        if self._messages is None:
            raise RuntimeError("repair_sections called before produce (no conversation)")

        # Build a compact error summary grouped by section
        by_section: dict[str, list] = {}
        for e in errors:
            by_section.setdefault(e.section, []).append(e)

        error_lines = []
        for sec in sorted(failing_sections):
            errs = by_section.get(sec, [])
            error_lines.append(f"[{sec}]")
            for e in errs:
                loc = f"{sec}{e.field}" if e.field else sec
                error_lines.append(f"  - {loc} ({e.code}): {e.hint}")

        section_list = "、".join(sorted(failing_sections))
        repair_instruction = (
            f"上一条提交中以下段有校验错误：\n"
            + "\n".join(error_lines)
            + f"\n\n只重新输出这些段 [{section_list}] 的合法 JSON（一个对象，仅含这些键）"
            + (
                "，narration 给本回合新散文字符串，只回应玩家最后一条动作，不复制历史；其它段保持不变。"
                if "narration" in failing_sections else
                "，不要重写 narration 或其它段，不要包含任何其它内容。"
            )
        )

        self._messages.append({"role": "user", "content": repair_instruction})
        log.debug("AuthorStrategy.repair_sections failing=%s msgs=%d",
                  sorted(failing_sections), len(self._messages))

        raw = json_call(provider.complete_messages, self._request_messages())
        self._messages.append({"role": "assistant", "content": raw})

        data = _parse_json_object(raw) or {}
        if "narration" in failing_sections and "narration" in data:
            try:
                data["narration"] = _author_narration(data["narration"])
            except AuthorOutputError:
                self._invalid_response_indices.add(len(self._messages) - 1)
                raise
        # Keep only the expected section keys to avoid contamination
        return {k: v for k, v in data.items() if k in failing_sections}


# ---------------------------------------------------------------------------
# HybridStrategy (丙) — free prose, then grounded authoring of its structure
# ---------------------------------------------------------------------------

class HybridStrategy(TurnStrategy):
    """Strategy 丙: 乙's free prose (call 1, frozen) + 甲's grounded authoring of
    the structure FOR that prose (call 2 sees the FULL assembled context + the
    prose, with an author framing — NOT a blind 史官). Aims for 乙's prose freedom
    + 甲's structural tightness, at 乙's 2-call cost. Repairs continue the
    structure conversation (agent loop); prose stays frozen."""

    _frozen_prose: str | None = None
    _messages: list | None = None

    def reset(self) -> None:
        self._frozen_prose = None
        self._messages = None
        self._bound_actor = None

    def produce(
        self,
        registry: Registry,
        world: dict,
        scene: dict,
        player_input: str,
        *,
        provider,
        embedder=None,
        repair: str | None = None,
    ) -> TurnCommit:
        actor_id = _bound_actor_id(world, scene)
        previous_actor = getattr(self, '_bound_actor', actor_id)
        if previous_actor != actor_id:
            self.reset()
        self._bound_actor = actor_id
        if repair is None or self._frozen_prose is None:
            ctx = assemble_context(registry, world, scene,
                                   query=player_input, embedder=embedder)
            narrate_parts = []
            if ctx:
                narrate_parts.append(ctx)
            if scene.get('_variation_prompt'):
                narrate_parts.append(scene['_variation_prompt'])
            if scene.get('_resolution_prompt'):
                narrate_parts.append(scene['_resolution_prompt'])
            narrate_parts.append(f"[player] {player_input}")
            prose = provider.complete(_narrate_prompt(), "\n\n".join(narrate_parts))
            self._frozen_prose = prose

            # Structure call sees the SAME full context the author had + the prose.
            struct_parts = []
            if ctx:
                struct_parts.append(ctx)
            struct_parts.append(f"[你刚写的叙事散文]\n{prose}")
            self._messages = [
                {"role": "system", "content": _SYSTEM_PROMPT_HYBRID},
                {"role": "user", "content": "\n\n".join(struct_parts)},
            ]
            log.debug("HybridStrategy.produce: fresh prose + grounded structure conversation")
        else:
            prose = self._frozen_prose
            self._messages.append({"role": "user", "content": repair})
            log.debug("HybridStrategy.produce: re-structure on repair (frozen prose, msgs=%d)",
                      len(self._messages))

        raw = json_call(provider.complete_messages, self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        data = _parse_json_object(raw) or {}
        data["narration"] = prose
        return TurnCommit.from_dict(data)

    def repair_sections(
        self,
        failing_sections: set[str],
        errors: list,
        *,
        provider,
    ) -> dict:
        """Modular repair for HybridStrategy: re-emit ONLY the failing sections.

        Continues the structure conversation (self._messages); the frozen prose
        is already in the thread context. The LLM is asked to output ONLY the
        failing section keys as a JSON object — no narration, no other sections.
        """
        if self._messages is None:
            raise RuntimeError("repair_sections called before produce (no conversation)")

        by_section: dict[str, list] = {}
        for e in errors:
            by_section.setdefault(e.section, []).append(e)

        error_lines = []
        for sec in sorted(failing_sections):
            errs = by_section.get(sec, [])
            error_lines.append(f"[{sec}]")
            for e in errs:
                loc = f"{sec}{e.field}" if e.field else sec
                error_lines.append(f"  - {loc} ({e.code}): {e.hint}")

        section_list = "、".join(sorted(failing_sections))
        repair_instruction = (
            f"上一条提交中以下段有校验错误：\n"
            + "\n".join(error_lines)
            + f"\n\n只重新输出这些段 [{section_list}] 的合法 JSON（一个对象，仅含这些键）"
            + (
                "，narration 给本回合新散文字符串，只回应玩家最后一条动作，不复制历史；其它段保持不变。"
                if "narration" in failing_sections else
                "，不要重写 narration 或其它段，不要包含任何其它内容。"
            )
        )

        self._messages.append({"role": "user", "content": repair_instruction})
        log.debug("HybridStrategy.repair_sections failing=%s msgs=%d",
                  sorted(failing_sections), len(self._messages))

        raw = json_call(provider.complete_messages, self._messages)
        self._messages.append({"role": "assistant", "content": raw})

        data = _parse_json_object(raw) or {}
        return {k: v for k, v in data.items() if k in failing_sections}
