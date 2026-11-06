"""loop.strategy — TurnStrategy ABC + AuthorStrategy (甲) + HybridStrategy (丙).

TurnStrategy:
    ABC defining produce(registry, world, scene, player_input, *, provider,
                         embedder=None, repair=None) -> TurnCommit.

AuthorStrategy (甲):
    Calls assemble_context, builds system+user prompts, calls
    provider.complete_json, and returns TurnCommit.from_dict(data).

HybridStrategy (丙):
    Two-call approach: (1) propose structured outcomes; (2) after host validation
    and repairs, compose free-form prose from the accepted outcomes. No draft
    prose becomes visible before produce_turn finishes validation.
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
- 【必填】moves：[{"who":移动的实体id, "to":目标地点id}]——主角每次行动只走一段，到达日由 clock 决定。谁移动到哪。首次在现场出现的新人物也要在这里记录出现地点，即使玩家和人物都站着没走。
- 【必填】places：[{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述, "visibility":"public"}]——本回合**新出现**的地点；**没有 → 给 []**；普通玩家只能创建全新 public 前沿地点。若创建，必须在可选 links 段用恰好一条 public 边把它直连 action-start 当前位置；不得修改既有地点拓扑或实体化既有地点。
- 【必填】cast：[{"id":..., "op":"create"|"evolve", "sketch":..., "goal":..., "name":可选}]——可创建全新的公开 NPC（不得复用任何既有 id，也不写 past/hidden）；对既有角色只可更新主角或当前公开在场人物的可观察 ``mood``/``state``。主角对当前公开人物的关系变化可用 op=relationship；**没有 → 给 []**。不得演化远方、隐藏或未发现角色，不能为普通玩家回合直接写其秘密、目标或全局关系。
- 【必填】facts：[{"subject":实体id, "predicate":属性名, "value":值, "secrecy":"public"}]——普通玩家回合只可写主角自身的公开事实，或当前公开在场人物的 ``reported:主题`` 来源陈述；**没有 → 给 []**。局部地形/物理所见请用下方带 public_observation 的 world 段；不得为远方、隐藏地点、物品或 NPC 断言 canonical 真相，更不能写 restricted/secret。**同一事物用一条 fact 说清，别拆成多条近义事实灌水**。
- 【必填】clock：[{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**恰好一个元素，永不为空**）——一天分四段（晨→中午→下午→夜晚），days=过了几整天、bands=【跨过了几个时段】（只在时段名真正切换时才计一段，可>3，引擎自动进位）；reason 必填。诀窍：先想清动作结束时落在哪个时段，再据此给 days/bands。同一时段内的细碎动作（几分钟、一次交谈、拂晓动手随即脱身）不构成推进，给 {"advance":false,"days":0,"bands":0,"reason":"..."}。
- 【可选】entities / relations：普通玩家回合不要输出这两段（省略即可）；通用实体和关系图属于宿主/导演或已受约束的地点、物品系统，不能借此改写远方或隐藏拓扑。
- 【可选】factions：普通玩家回合不要输出本段；势力创建、成员、位阶与群组会改变长期 canonical 组织关系，属于宿主/导演或导入历史权限。
- 【可选】links / materialize：普通玩家通常省略。仅新建 places 时，links 可给一条 `{ "a":"当前位置 id", "b":"新地点 id", "visibility":"public" }`；materialize 始终不得输出。不得移动既有人物；同回合新建公开人物只能放在当前位置或主角的同回合目的地。
- 【可选】items：普通玩家回合只可用 `[{"op":"transfer","item":"主角当前背包物品 id","to":"当前公开人物 id 或当前位置 id"}]` 交接已持有物品；没有物品交接就省略。按顺序核对每一步持有人；自己放在此处的物品可用 transfer 转回主角，不能取走其他人物的东西。
- 【可选】knowledge：记录"谁知道了什么"——详见下【信息视野】
- 【可选】world：主角本回合在本地公开范围内的地点观察——详见下【本地公开观察】
- 【可选】quests：任务的开启/浮现/推进/收束——详见下【任务系统】


【铁律】上面 6 个【必填】段每回合都必须出现：narration 给散文、clock 给恰好一个元素、moves/places/cast/facts **没有该类变化就给 []（空数组）**。条目**要么字段齐全、要么根本别放**——宁可给 [] 也别塞一个缺字段的半成品（缺字段会被打回、拖慢一整局）。【可选】段没有就直接省略、不要硬凑。

【人物连续性】当前角色卡的状态、心情和有方向的关系是本次互动的起点。已经收剑、受伤或等答复的人不能无缘由恢复初见状态；只有本回合实际变化才用 cast.evolve 更新 mood/state，不要每轮重置。卡上的“主角对其态度”不等于对方信任主角。先回应玩家这次行动，再通过人物反应和可回应的后果自然推进，不必每回合制造突发事件。

【信息视野·knowledge】本引擎追踪"谁知道什么",并据此决定下回合对主角【保密 / 可见】。普通玩家回合中，通用 told 只可写给 protagonist：凡主角刚【得知/亲历】的事(包括关于他自己的发现),只写进 facts 是不够的——必须同时在 knowledge 里给 protagonist 记一条 told;否则引擎不知道主角已经知道,下回合他等于"失忆"、POV 工具也查不到。NPC 获知主角的原话只能使用宿主已明确签发的 heard_player receipt；不得把玩家自由行动文字改写为 NPC 的 told、endowment、broadcast 或 knows: fact。NPC 独立来源的既有知识由宿主/导演/导入历史管理，不在普通玩家作者回合中补写。
- 写每个 NPC 的台词时，只采用他自己的经历、现场可观察的事、归在他名下的材料和他实际收到的通信。主持人的幕后暗线、主角心念、旧旁白和别人的通信不会自动传给他。两个角色对同一事件可以有不同或过时的看法；存档中的真相改变后，先保留各人的原有认知，直到有实际消息传到。材料不足时允许询问或保留判断。
- told:      [{"op":"told","knower":"protagonist","fact_key":"实体.属性","value":主角所知内容,"via":合法来源(可选)}]
- heard_player: 仅在宿主明确提供 receipt 时才可写给该 receipt 的唯一收件人；无 receipt 时省略 knowledge。
fact_key 尽量用 "实体.属性" 形式（如 "断桥.是否可通行"、"商队首领.真实身份"），与世界事实同名——系统据此判断主角是否已知、并在叙事中对其未知之事保密。无人获得新信息时本段可省略（不必写 reason）。

【本地公开观察·world（可选段）】普通玩家回合没有区域／全局 world 权限。只有主角在当前地点，或本回合以合法 moves 抵达的公开地点，实际看见一项可公开、局部且有范围的事物时才写本段；否则省略。唯一可发布形状是：
- world: [{"areas":["当前或合法同回合到达的单一公开地点 id"],"level":1,"summary":"一句话本地观察","public_observation":true,"visibility":"public","observation_kind":"stable_layout"|"single_observation","observation_range":"实际可见范围"}]

它不是地图发现、路线结论、全局真相或级联事件。不得点名远方、隐藏或未发现地点；不得使用 level 2/3；不得把 NPC 的旧证言改写为世界事实。需要超出此范围的世界推进，交给宿主／导演系统。寻常赶路、对话或没有新观察的行动直接省略 world。

【任务系统·quests（可选段）】本回合若有任务变化，用 quests 段记录：[{"op":"open"|"surface"|"advance"|"resolve","id":任务标识,"summary":"一句话摘要"}]
- open: 玩家刚接取了一条全新的公开任务（NPC 托付/玩家决定追查）；id 必须是全新的，必须提供 summary
- surface: 只能让上文【本地暗线】明确给出的 [id] 浮出为明账；不能 surface 未在当前地点披露的暗线，也不能借此读取其秘密
- advance: 推进当前地点的本地明态任务，或未绑定地点的玩家公开任务；不能推进远方账目
- resolve: 收束同样范围内的本地明态任务；不能收束远方账目
- 明确承诺交付自己持有的一件物品时，open 还要记录 objective:{"kind":"deliver","item":"物品ID","recipient":"在场人物或当前地点ID","due":{"day":1,"band":1}}；due 可省略，band 为0晨/1中午/2下午/3夜，截止时段含边界。条件必须来自已接受的约定，不要自行添加期限；先接取、后续回合交付。
- 交付任务必须有 items transfer 到约定接收者才算完成，口头声称不算交付。实际转移会由宿主自动结算，可省略 resolve；不要自己填写 resolution。逾期仍可交付，不自动失败；玩家明确放弃时才用 resolve + outcome:"abandoned"，不要把放弃写成成功。
- 【任务·近期结局】用于延续人物反应与后果，不得再次扣物品、重复发奖励或擅自完成其他任务。
无任务变化时省略本段。

【完整范例】一个“向当前公开在场人物听到其来源陈述、没移动、没新地点”的回合长这样——只复用当前上下文实际提供的 id，照抄其结构（注意：空的必填段就给 []）：
{"narration":"你压低声音问起那场大火。老者的手停在药罐上，半晌才道：「我只听说镖局的人那天出现在附近。」",
 "moves":[],
 "places":[],
 "cast":[],
 "facts":[{"subject":"npc_laozhe","predicate":"reported:火灾传闻","value":"我只听说镖局的人那天出现在附近","secrecy":"public"}],
 "knowledge":[{"op":"told","knower":"protagonist","fact_key":"npc_laozhe.reported:火灾传闻","value":"我只听说镖局的人那天出现在附近","via":"老者亲口"}],
 "clock":[{"advance":false,"days":0,"bands":0,"reason":"同一段对话，时间未实质推进"}]}
——若主角移动了：moves 给 [{"who":"protagonist","to":"<地点id>"}]。
——若正文让主人公当面遇见一个新人，同一回合用 cast.create 记录此人，并用 moves 将同一个 id 放在实际相遇的地点（当前位置或主角本回合实际抵达处）。例如在旅店见到领路人：cast 中建 guide，moves 中给 {"who":"guide","to":"该旅店的实际地点id"}。下一回合可据此再找到这个人。如果只是听说远方某个人，保留为转述，不为那个人填写本地位置。

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

# Both authors share one structural contract; Hybrid adds only stage semantics.
def _hybrid_prompt() -> str:
    return _system_prompt() + "\n\n" + (
        "【结果先行·规划阶段】先理解玩家的行动，提出本回合将发生的结构化变化。"
        "narration 仅为内部场景草稿，不会展示给玩家，不必雕琢长篇正文。"
        "宿主将校验并修正结构，之后另写最终散文；本阶段不得把希望发生的事当作已发生的历史。"
    )


_SYSTEM_PROMPT_HYBRID = _hybrid_prompt()


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
    discovery = _host_discovery_fragment(scene)
    if discovery:
        parts.append(discovery)
    steer = _player_steer_fragment(scene)
    if steer:
        parts.append(steer)
    parts.append(f"[player] {player_input}")
    return "\n\n".join(parts)


def _player_steer_fragment(scene: dict) -> str | None:
    """Render a session-local player preference without granting authority.

    ``//steer`` is intentionally not an event, a fact, a route discovery, or a
    durable instruction.  JSON quoting keeps the preference data visibly
    separate from prompt structure; the surrounding language makes clear that
    normal action/POV/publish constraints still prevail.
    """
    steer = scene.get('_player_steer') if isinstance(scene, dict) else None
    if not isinstance(steer, str) or not steer.strip():
        return None
    return (
        "【玩家软方向（仅本会话偏好，不是世界事实或权限）】\n"
        "在不取代本回合明确行动、不过界于已提供 POV/路线/发布约束的前提下，"
        "后续叙事可优先朝这个方向发展："
        + json.dumps(steer, ensure_ascii=False)
    )


def _host_discovery_fragment(scene: dict) -> str | None:
    """Describe a host-owned reveal without exposing its target or authority."""
    if not isinstance(scene, dict) or scene.get('_host_discovery_reveal') is not True:
        return None
    return (
        "【附近线索核验】宿主已决定本回合可记录一项附近的发现；这只会在回合成功后让玩家得知一条出口，"
        "不会在本回合抵达或移动到那里。不要猜测、命名或输出内部地点 ID、发现键或权限记录；"
        "不要在 knowledge 段声明发现，宿主会在发布时写入。"
    )


# ---------------------------------------------------------------------------
# AuthorStrategy (甲) — one main-LLM call
# ---------------------------------------------------------------------------

class AuthorStrategy(TurnStrategy):
    """Strategy 甲: author prose+structure in one conversational thread.

    First attempt opens a [system, user] conversation; each repair CONTINUES it —
    the model sees its own prior output + the precise validation errors and fixes
    incrementally (agent loop), instead of re-prompting blind each round.
    """

    outcome_first = False

    def author_prompt(self) -> str:
        return _system_prompt()

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
            discovery = _host_discovery_fragment(scene)
            if discovery:
                parts.append(discovery)
            steer = _player_steer_fragment(scene)
            if steer:
                parts.append(steer)
            parts.append(f"[player] {player_input}")
            full_user = "\n\n".join(parts)
            self._pending_user = full_user
            self._messages = [
                {"role": "system", "content": self.author_prompt()},
                {"role": "user", "content": full_user},
            ]
            if multiturn:
                # Reset the thread to a bare system base; the turn's user + narration
                # are appended by commit_to_thread on success (so no duplication).
                self._thread = [{"role": "system", "content": self.author_prompt()}]
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

    def finalize_narration(self, commit, *, provider, changed_sections=(), repair=None) -> str:
        """Write only visible prose against an already accepted, frozen proposal.

        Reuse the POV context from this action. Rejected proposals, repair
        conversations and unvalidated draft prose are deliberately not replayed.
        The ordinary one-call path stays one call when its structure is unchanged.
        """
        if not (self.outcome_first or changed_sections or repair):
            return commit.narration
        if self._pending_user is None:
            raise RuntimeError("finalize_narration called before produce")
        result = json.dumps(commit.sections, ensure_ascii=False, sort_keys=True)
        parts = [
            "【行动开始时的语境与玩家意图】\n" + self._pending_user,
            "【本回合已通过校验的结果】\n" + result,
            "以上是本回合唯一可以叙述为已完成的状态变化；语境里的路线、猜测和行动意图不等于成功。"
            "资源以语境内宿主已裁定结果为准，不再扣账。未列出的移动、交接、任务完成或信息传播不能补写成成功。"
            "NPC 的原话回忆必须有提供的来源；来源陈述不升级为世界事实。"
            "现在写最终正文：直接回应这一次行动，用人物反应、可感细节与后果推动局面；"
            "允许自然对白，不增造持久效果，不替玩家决定下一步，不展示结构、校验过程或内部 id。",
        ]
        if changed_sections:
            parts.append("结果曾修正（" + "、".join(sorted(changed_sections)) +
                         "）；请完全按最终结果重新叙述，不沿用先前草稿中的成功结论。")
        if repair:
            parts.append("【正文重写要求·结果不变】\n" + repair)
        return provider.complete(_narrate_prompt(), "\n\n".join(parts))

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
# HybridStrategy (丙) — validate outcomes, then render free-form prose
# ---------------------------------------------------------------------------

class HybridStrategy(AuthorStrategy):
    """Plan and repair structure first; produce_turn requests final prose once
    the proposal is valid. Shares context, tool research and repairs with Author.
    A successful turn still costs two author calls in the no-repair case.
    """

    outcome_first = True

    def author_prompt(self) -> str:
        return _hybrid_prompt()

    def produce(self, registry, world, scene, player_input, *, provider,
                embedder=None, repair=None) -> TurnCommit:
        proposal = super().produce(registry, world, scene, player_input,
                                   provider=provider, embedder=embedder, repair=repair)
        # Narration validation is meaningful only after final composition. This
        # internal placeholder never reaches run_turn/apply when validation fails.
        return TurnCommit(narration="（本回合结果待定。）", sections=proposal.sections)
