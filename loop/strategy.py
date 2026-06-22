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
import os
from typing import Any

from context.assembler import assemble_context
from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from llm.provider import _parse_json_object
from llm.tools import build_tool_registry
from engine.log import get_logger
from loop.lore_disclosure import station_push_fragment

log = get_logger("loop.strategy")

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

_SYSTEM_PROMPT = """\
你是主持人（DM），以主角视角叙事，同时记录世界变化。

【narration 文风】融合细腻描写与戏剧张力：重环境氛围、角色神态内心、有张力的对话；具体可感、不空泛；篇幅随情境而定，不设字数限制；展示而非告知；推进局面但绝不替玩家决定下一步；严守保密事实，绝不在 narration 中直接揭露。

【结构】除 narration 外，按本回合【真实发生】的变化给出可选段落（每段都是对象数组）：
- moves: [{"who":移动的实体id, "to":目标地点id}]   （who、to 均必填）
- places: [{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]（kind 只能取列出的五个值之一，别自造如 ruin/forest 等）
- cast: [{"id":..., "op":"create"|"evolve", "sketch":..., "goal":...}]
- entities: [{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填）
- facts: [{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]（subject/predicate/value 必填；只记确有意义的客观事实，勿把布景滥造成 fact）。secrecy 可选，取 "public"|"restricted"|"secret"：街坊皆知的常识/明面事实标 "public"（路人/打听才转述得到）；需特定人才知的秘密/真相/谎言标 "secret"（或 "restricted"）；拿不准就【不写】（默认不进公开层、绝不外泄）
- relations: [{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
- knowledge: 记录"谁知道了什么"（可选）——详见下【信息视野】
- world: 区域/世界级事件波及的地点（可选）——详见下【世界事件】
- quests: 记录"任务的开启/浮现/推进/收束"（可选）——详见下【任务系统】
- clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**每回合必给，恰好一个元素**）——本回合游戏内时间推进多少：advance 是否推进；一天分四段（晨→中午→下午→夜晚），days=过了几整天、bands=【跨过了几个时段】（只在时段名真正切换时才计一段，可>3，引擎自动进位）；reason 必填，写清推进这么多的依据，或【为何本回合不推进】。判定要诀：先想清动作结束时落在哪个时段，再据此给 days/bands。同一时段内的细碎动作（几秒几分钟、一次冲刺/夺取/交谈、拂晓动手随即脱身）不构成推进，给 {"advance":false,"days":0,"bands":0,"reason":"..."}——切勿为小动作多推一段。


【必填·防遗漏】moves / places / cast / facts 四项每回合都要交代：有变化就给对象数组；若确无变化，必须在顶层 reasons 对象里写明【为什么】没有（强制你逐项确认、而非漏写），例如 reasons:{"moves":"主角停在原地未移动","places":"未离开当前地点，无新地点"}。不允许某必填项既无内容又无 reason。另外 clock 段每回合必给（恰好一个元素，描述本回合时间推进），不可省略、不可为空。

【信息视野·knowledge（可选段）】本引擎追踪"谁知道什么"。当本回合有角色【得知 / 识破 / 被告知 / 无意获悉 / 主动透露】重要信息——尤其是秘密、线索、真相或谎言——用 knowledge 段记录信息的流动：
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

规则：
1. 只在剧情真正发生该变化时才给对应段落；不要把布景细节（石板、树冠、手掌等）滥造成 entity。
2. 输出合法 JSON 对象，必含 "narration" 字段；只输出 JSON，不附 markdown 代码块或其他包装。
"""

_NARRATE_PROMPT = """\
你是主持人（DM），以主角视角进行沉浸式叙事。

【文风】融合细腻描写与戏剧张力：重环境氛围、角色的神态动作与内心、以及有张力的对话；多用具体可感的细节，少堆空泛形容。
【写法】
1. 第一/第三人称散文皆可（以中文为主）；篇幅随情境而定，不设字数限制——该浓墨铺陈就铺陈，该干脆利落就收住。
2. 展示而非告知：设定、过往、人物关系通过此刻的细节、动作与后果自然流露，不要直接复述资料。
3. 严守 ⚠️只约束·勿泄露 中的保密事实——绝不直接揭露，可暗示、可让其后果显现。
4. 推进当前局面、给玩家可回应的钩子，但绝不替玩家决定下一步行动。
5. 只输出叙事散文本身，不要任何 JSON / 结构化数据 / 元说明。
"""

# 丙 HybridStrategy 结构提示(call 2:作者为自己刚写的散文补结构,带全上下文)
_SYSTEM_PROMPT_HYBRID = """\
你是主持人（DM）。你刚写完下面这段叙事散文。现在以"懂世界规则的作者"身份，为这段散文忠实地补出结构化 turn-commit。
规则：
1. 只记录散文中【真实发生】的世界变化；不要新增散文里没有的人物/地点/事件。
2. 上文给出了当前世界状态与已存在实体的 canonical id——散文指向已知对象（主角、已知 NPC、已知地点）时必须复用其原有 id，只为散文中首次出现的新对象创建新 id。
3. 每个段落都是对象数组：
   - moves: [{"who":实体id, "to":地点id}]
   - places: [{"id":..., "level":1|2|3, "kind":settlement|wilderness|dungeon|venue|region, "seed":一句话描述}]（kind 只能取列出五值之一）
   - cast: [{"id":..., "op":"create"|"evolve", "sketch":..., "goal":...}]
   - entities: [{"id":..., "etype":"Person"|"Place"|"Object"等}]（etype 必填）
   - facts: [{"subject":实体id, "predicate":属性名, "value":值, "secrecy":可选}]（subject/predicate/value 必填）。secrecy 可选 "public"|"restricted"|"secret"：街坊常识标 public（路人可转述），秘密/真相标 secret，拿不准不写（默认不公开）
   - relations: [{"src":实体id, "rel":关系名, "dst":实体id}]（三者必填）
   - knowledge: 记录"谁知道了什么"（可选）——见第 5 条
   - world: 区域/世界级事件波及的地点（可选）——见第 7 条
   - quests: 记录"任务的开启/浮现/推进/收束"（可选）——见第 8 条
   - clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**每回合必给，恰好一个元素**）——本回合游戏内时间推进多少（一天四段：晨→中午→下午→夜晚；bands=跨过的时段数，只在时段名真正切换时才计，可>3，引擎自动进位）；reason 必填。散文里时间明显流逝（入夜、次日、三日后）就按量给出；同一时段内的细碎动作（连续紧接、一次冲刺/夺取）不算推进，给 advance:false 且写 reason，切勿为小动作多推一段。
4. 【必填·防遗漏】moves / places / cast / facts 四项每回合都要交代：散文有对应变化就给数组；确无变化则在顶层 reasons 里写明为什么没有（如 reasons:{"moves":"散文中主角未移动"}）；不允许既无内容又无 reason。尤其——散文里主角移动了就必须有 moves、出现新地点就必须有 places，绝不能写了却漏记。clock 段每回合必给（恰好一个元素），不可省略。
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


# ---------------------------------------------------------------------------
# AuthorStrategy (甲) — one main-LLM call
# ---------------------------------------------------------------------------

class AuthorStrategy(TurnStrategy):
    """Strategy 甲: author prose+structure in one conversational thread.

    First attempt opens a [system, user] conversation; each repair CONTINUES it —
    the model sees its own prior output + the precise validation errors and fixes
    incrementally (agent loop), instead of re-prompting blind each round.
    """

    _messages: list | None = None  # authoring conversation for the current turn

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
        if repair is None or self._messages is None:
            # Fresh turn: open the conversation with context + player input.
            ctx = assemble_context(registry, world, scene,
                                   query=player_input, embedder=embedder)

            # Always append station_push_fragment (暗 ambient B disclosure).
            # Returns None when no 暗 lines are in range or no LoreSystem →
            # no-op for worlds without lore (existing tests unaffected).
            frag = station_push_fragment(registry, world, scene)
            if frag:
                ctx = (ctx + "\n\n" + frag) if ctx else frag

            parts = []
            if ctx:
                parts.append(ctx)
            parts.append(f"[player] {player_input}")
            self._messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ]
        else:
            # Repair: append the validation errors as the next user turn; the prior
            # assistant output is already in the thread, so the model fixes in place.
            self._messages.append({"role": "user", "content": repair})

        log.debug("AuthorStrategy.produce msgs=%d repair=%r", len(self._messages), bool(repair))

        # DD6 capability gate: use the tool loop ONLY on fresh turns when the
        # provider supports it and the POV tool registry is non-empty.
        # Repair rounds always use plain complete_messages (no re-research).
        if repair is None and provider.supports_tools():
            tool_reg = build_tool_registry(registry, world, scene)  # POV set (dm=False)
            schemas = tool_reg.schemas()
            if schemas:
                rounds = int(os.environ.get("RPG_MAX_TOOL_ROUNDS", "3"))
                raw = provider.complete_with_tools(
                    self._messages, schemas, tool_reg.execute,
                    max_tool_rounds=rounds,
                )
                self._messages.append({"role": "assistant", "content": raw})
                data = _parse_json_object(raw) or {"narration": raw}
                return TurnCommit.from_dict(data)

        # Existing path: plain complete_messages (unchanged for all non-tool providers
        # and all repair turns — DD6 guarantees the 1180-test suite is byte-for-byte
        # identical when provider.supports_tools() is False).
        raw = provider.complete_messages(self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        data = _parse_json_object(raw) or {"narration": raw}
        return TurnCommit.from_dict(data)


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
            narrate_parts.append(f"[player] {player_input}")
            prose = provider.complete(_NARRATE_PROMPT, "\n\n".join(narrate_parts))
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

        raw = provider.complete_messages(self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        data = _parse_json_object(raw) or {}
        data["narration"] = prose
        return TurnCommit.from_dict(data)
