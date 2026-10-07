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

# Compaction: when a turn's prompt tokens cross COMPACTION_RATIO of the model
# context window, flag the next fresh turn to rebuild full context (re-assemble
# the index/recent/summary tiers + reset the running thread).
CONTEXT_WINDOW = 200_000
COMPACTION_RATIO = 0.70

# Neutral fallback shown when the model output can't be parsed AND no narration
# can be salvaged. NEVER show the raw blob — it carries the structured commit
# (incl. secrecy="secret" facts). (#R5)
_PARSE_FAIL_NARRATION = "（这一刻，周遭并无明显变化。）"

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
- 【必填】moves：[{"who":移动的实体id, "to":目标地点id}]——谁移动到哪；**没人移动 → 给 []**。
- 【必填】places：[{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]——本回合**新出现**的地点；**没有 → 给 []**；kind 只能取列出的五个值，别自造（如 ruin/forest）。
- 【必填】cast：[{"id":..., "op":"create"|"evolve", "sketch":..., "goal":..., "name":可选}]——**新登场且有戏**的 NPC（op=create，必须 id+sketch+goal 三者齐全；给 name 便于后续引用）或既有角色的变化（op=evolve）；**没有 → 给 []**；只是路过的纯路人可不写，引擎会按名字自动建轻量占位。**若 characters_query 显示某 NPC 已在场（co_present），就用它返回的那个 id 以 op=evolve 推进，切勿为同一个已在场的人另起新 id（会造成重复实体）。**
- 【必填】facts：[{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]——本回合确立的**客观事实**（subject/predicate/value 必填）；**没有 → 给 []**；只记确有意义的事实，勿把布景滥造成 fact；**同一事物用一条 fact 说清，别拆成多条近义事实灌水**。secrecy 取 "public"|"restricted"|"secret"：街坊皆知的标 "public"（路人/打听才转述得到）；需特定人才知的秘密/真相/谎言标 "secret"（或 "restricted"）；拿不准就【不写该字段】（默认不进公开层、绝不外泄）。
- 【必填】clock：[{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**恰好一个元素，永不为空**）——一天分四段（晨→中午→下午→夜晚），days=过了几整天、bands=【跨过了几个时段】（只在时段名真正切换时才计一段，可>3，引擎自动进位）；reason 必填。诀窍：先想清动作结束时落在哪个时段，再据此给 days/bands。同一时段内的细碎动作（几分钟、一次交谈、拂晓动手随即脱身）不构成推进，给 {"advance":false,"days":0,"bands":0,"reason":"..."}。
- 【可选】entities：[{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填；仅在需要凭空声明实体时用）
- 【可选】items：创建物品用 {"op":"create","id":"物品id"}；转移用 {"op":"transfer","item":"物品id","from":"转移前持有者id","to":"新持有者id"}。item 必须是 Object，to 必须是 Person 或 Place。已有人持有的物品必须给出与当前账本一致的 from；首次放置未被持有的物品才可省略 from 或给 null。先创建实体，再按顺序转移；A→B→C 的第二次 from 是 B。不要通过 relations 写 held_by，不要用重复创建实体改变其类型。from 仅表示来源，不代表同意、授权或合法性；只记录正文中实际发生的转移。
- 【可选】relations：[{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
- 【可选】knowledge：记录"谁知道了什么"——详见下【信息视野】
- 【可选】world：区域/世界级事件波及的地点——详见下【世界事件】
- 【可选】quests：任务的开启/浮现/推进/收束——详见下【任务系统】


【铁律】上面 6 个【必填】段每回合都必须出现：narration 给散文、clock 给恰好一个元素、moves/places/cast/facts **没有该类变化就给 []（空数组）**。条目**要么字段齐全、要么根本别放**——宁可给 [] 也别塞一个缺字段的半成品（缺字段会被打回、拖慢一整局）。【可选】段没有就直接省略、不要硬凑。

【信息视野·knowledge】本引擎追踪"谁知道什么",并据此决定下回合对主角【保密 / 可见】。当本回合有角色【得知 / 识破 / 被告知 / 无意获悉 / 主动透露】重要信息——秘密、线索、真相、谎言、关键数值——用 knowledge 段记录信息的流动。**尤其:凡本回合主角刚【得知/亲历】的事(包括关于他自己的发现),只写进 facts 是不够的——必须同时在 knowledge 里给 protagonist 记一条 told;否则引擎不知道主角已经知道,下回合他等于"失忆"、POV 工具也查不到。**
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
 "knowledge":[{"op":"told","knower":"protagonist","fact_key":"npc_laozhe.火灾真凶","value":"镖局所为","via":"老者亲口"}],
 "clock":[{"advance":false,"days":0,"bands":0,"reason":"同一段对话，时间未实质推进"}]}
——若主角移动了：moves 给 [{"who":"protagonist","to":"<地点id>"}]；若来了个有戏的新人：cast 给一条齐全的 {"id":"...","op":"create","sketch":"...","goal":"...","name":"..."}。

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


def _system_prompt(verbosity: str | None = None, style: str | None = None) -> str:
    """Build the 甲 system prompt with the current (or given) verbosity + style."""
    v = verbosity or _settings.get_verbosity()
    frag = _VERBOSITY_FRAGMENT.get(v, _VERBOSITY_FRAGMENT["medium"])
    s = _settings.get_style() if style is None else style
    return (_SYSTEM_PROMPT_TEMPLATE
            .replace("__STYLE__", _style_fragment(s))
            .replace("__VERBOSITY__", frag))


def _narrate_prompt(verbosity: str | None = None, style: str | None = None) -> str:
    """Build the 丙 narration prompt with the current (or given) verbosity + style."""
    v = verbosity or _settings.get_verbosity()
    frag = _NARRATE_VERBOSITY_FRAGMENT.get(v, _NARRATE_VERBOSITY_FRAGMENT["medium"])
    s = _settings.get_style() if style is None else style
    return (_NARRATE_PROMPT_TEMPLATE
            .replace("__STYLE__", _style_fragment(s))
            .replace("__NARRATE_VERBOSITY__", frag))


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
   - 【必填】moves: [{"who":实体id, "to":地点id}]——散文里谁移动了；**没有就给 []**
   - 【必填】places: [{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]——散文里**新出现**的地点；**没有就给 []**；kind 只能取列出五值之一
   - 【必填】cast: [{"id":..., "op":"create"|"evolve", "sketch":..., "goal":..., "name":可选}]——散文里**新登场且有戏**的 NPC（create 须 id+sketch+goal 齐全；name 便于后续引用）或既有角色变化（evolve）；**没有就给 []**；纯路人可不写，引擎按名字自动占位
   - 【必填】facts: [{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]——散文确立的客观事实（subject/predicate/value 必填）；**没有就给 []**。secrecy 可选 "public"|"restricted"|"secret"：街坊常识标 public（路人可转述），秘密/真相标 secret，拿不准不写（默认不公开）
   - 【必填】clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**恰好一个元素，永不为空**）——本回合游戏内时间推进多少（一天四段：晨→中午→下午→夜晚；bands=跨过的时段数，只在时段名真正切换时才计，可>3，引擎自动进位）；reason 必填。散文里时间明显流逝（入夜、次日、三日后）就按量给出；同一时段内的细碎动作（连续紧接、一次冲刺/夺取）不算推进，给 advance:false 且写 reason，切勿为小动作多推一段。
   - 【可选】entities: [{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填）
   - 【可选】relations: [{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
   - 【可选】knowledge: 记录"谁知道了什么"——见第 5 条
   - 【可选】world: 区域/世界级事件波及的地点——见第 7 条
   - 【可选】quests: 记录"任务的开启/浮现/推进/收束"——见第 8 条
4. 【铁律】上面 5 个【必填】段（moves/places/cast/facts/clock）每回合都必须出现：clock 给恰好一个元素，moves/places/cast/facts **散文里没有该类变化就给 [](空数组)**。条目要么字段齐全、要么根本别放——宁可 [] 也别塞缺字段的半成品。尤其：散文里主角移动了就必须有 moves、出现新地点就必须有 places，写了却漏记不行。【可选】段没有就直接省略。
5. 【信息视野·knowledge（可选段）】散文中若有角色【得知/识破/被告知/无意获悉/主动透露】重要信息（秘密、线索、真相、谎言），记录到 knowledge 段：told 项 {"op":"told","knower":知情者id,"fact_key":"实体.属性","value":其所知,"via":途径(可选)}；一群人同时获悉用 broadcast 项 {"op":"broadcast","fact_key":...,"value":...,"audience":{"faction":id}或{"place":id}}。fact_key 尽量用 "实体.属性" 形式、与世界事实同名。散文未提及信息易手时省略本段。
6. 只输出合法 JSON（不含 narration），不附任何 markdown 代码块或其他包装。
7. 【世界事件·world（可选段）】散文中若描写了区域级或世界级的大事（灾难、战争、瘟疫、政权更替、重大变故），用 world 段点名所有受影响地点：world: [{"areas":[受影响地点id,...],"level":1|2|3,"summary":"一句话事件"}]。areas 用已存在或本回合刚创建的地点 id；你有完整世界视野，可点名任意位置。寻常个人场景省略本段。
8. 【任务系统·quests（可选段）】散文中若有任务变化，用 quests 段记录：[{"op":"open"|"surface"|"advance"|"resolve","id":任务标识,"summary":"一句话摘要"}]；open=玩家接取全新明线任务（id必须全新，必须提供summary）；surface=暗线浮现进入明账（id须与上文【本地暗线】中 [id] 标签一致，切勿 open 新 id——暗线每条都标有 [id]，散文中玩家触碰了哪条就 surface 该 id）；advance=推进已有明线任务；resolve=收束已有明线任务。id 须与上文【任务·明账】中已列的 id 保持一致（open 除外）。寻常个人场景无任务变化时省略本段。
"""


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

    def commit_to_thread(self, narration: str) -> None:
        """Append [pending user delta, narration prose] to the persistent thread on
        a successful turn. Narration only — raw JSON / repair / tool messages stay
        in the transient _messages. No-op in stateless mode (_thread is None)."""
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
        multiturn = (_settings.get_conversation_mode() == "multiturn")
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
            self._messages = list(self._thread) + [{"role": "user", "content": delta}]
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
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": full_user},
            ]
            if multiturn:
                # Reset the thread to a bare system base; the turn's user + narration
                # are appended by commit_to_thread on success (so no duplication).
                self._thread = [{"role": "system", "content": _system_prompt()}]
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
                # Reasoning models routinely answer the final turn in bare prose
                # after a tool loop, dropping the JSON envelope. Re-ask ONCE for the
                # structured commit before falling back to prose-as-narration.
                if _parse_json_object(raw) is None:
                    raw = self._reask_json(raw, provider)
                self._messages.append({"role": "assistant", "content": raw})
                if repair is None and multiturn:
                    self._maybe_flag_compaction(provider)
                data = _data_or_safe(raw)
                return TurnCommit.from_dict(data)

        # Plain complete_messages (all non-tool providers + all repair turns).
        # In stateless mode the else-branch above runs every fresh turn and _thread
        # stays None → this reproduces the original control flow byte-for-byte.
        raw = json_call(provider.complete_messages, self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        if repair is None and multiturn:
            self._maybe_flag_compaction(provider)
        data = _data_or_safe(raw)
        return TurnCommit.from_dict(data)

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

        raw = json_call(provider.complete_messages, self._messages)
        self._messages.append({"role": "assistant", "content": raw})

        data = _parse_json_object(raw) or {}
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
