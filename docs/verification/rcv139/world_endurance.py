"""100 ordinary actions, deterministic author and secondary providers, no network.

Run from the repository root. Prints JSON, using a new temporary save each run.
"""
from collections import Counter
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.engine import build_engine
from app.gui import build_dashboard
from app.play import _build_scene
from context.native_authoring import native_authoring_context
from kernel.events import kernel_event
from llm.provider import FakeLLMProvider
from loop.lore import create_lore_line
from loop.outcome_narration import _REVIEW
from systems.knowledge import player_statement_fact_key


HERO, INN, MARKET, HILL = "protagonist", "inn", "market", "hill"
ALICE, BOB = "keeper", "clerk"
BLUE, RED = "蓝印留给守门人。", "红印留给集市书记。"


class OfflineAuthor(FakeLLMProvider):
    def __init__(self):
        super().__init__()
        self.plan = {}
        self.calls_by_role = Counter()

    def complete_messages(self, messages, **kw):
        system = messages[0]["content"]
        if system == _REVIEW:
            self.calls_by_role["prose_review"] += 1
            return '{"supported":true,"reason":"固定的测试候选"}'
        if "根据 source 原文独立判断" in system:
            self.calls_by_role["evidence_review"] += 1
            return json.dumps(dict.fromkeys(json.loads(messages[-1]["content"]), True))
        self.calls_by_role["author"] += 1
        assert "HILL_PRIVATE_CAUSE" not in str(messages), "dark line secret leaked into the ordinary author"
        return json.dumps(self.plan, ensure_ascii=False)

    def complete(self, system, user, **kw):
        self.calls_by_role["final_prose"] += 1
        return self.plan["narration"]


class OfflineBackground(FakeLLMProvider):
    def complete_messages(self, messages, **kw):
        system = messages[0]["content"]
        if "剧情摘要员" in system:
            return '{"summary":"主角持续查验账簿，按照实际观察记录经过。"}'
        if "状态推演模块" in system:
            return '{"changed":false}'
        return '{"evolve":false}'


def seed(directory, writer):
    engine = build_engine(directory, provider=writer, embedder=None)
    events = []
    def add(event_type, **delta):
        events.append(kernel_event(event_type, day=1, turn=0, scene=INN, summary="合成开局", deltas=delta))
    add("campaign_seeded", campaign_seed=137)
    add("place_created", id="county", level=1, kind="region", density=0.0)
    for pid, name in ((INN,"石桥客栈"),(MARKET,"石桥集市"),(HILL,"远方驿站")):
        add("place_created", id=pid, label=name, seed=name, level=2, kind="settlement", parent="county")
    add("place_linked", a=INN, b=MARKET, travel_cost=1)
    for pid, name, place in ((HERO,"旅人",INN),(ALICE,"阿岚",INN),(BOB,"白禾",MARKET)):
        add("character_created", op="create", id=pid, name=name, sketch="旅人", goal="查验公开来信", tier="tracked")
        add("entity_moved", who=pid, to=place)
    engine.store.append_many(events)
    for lid, place in (("bridge",INN),("bookstalls",MARKET),("mail",HILL)):
        stages = [{"hint":f"{lid}的第{i}次变化"} for i in range(1,11)]
        if lid == "bridge":
            for index, passable in enumerate([False,True]):
                stages[index]["route_effect"] = {"a":INN,"b":MARKET,"passable":passable}
        line = {"id":lid,"complexity":"medium","cadence":"day","threshold":100,"lifespan_days":35,
                "secret":"HILL_PRIVATE_CAUSE" if lid=="mail" else "幕后原因", "about":lid, "description":lid,
                "trigger":"实地核验","stages":stages,"anchor":place,"l3_anchor":place}
        create_lore_line(engine.store,line,day=1,scene=place,turn=0)
    engine.store.close()


def observation(place, feature, quote, result):
    return {"areas":[place], "level":1, "summary":quote, "visibility":"public", "public_observation":True,
            "observation_kind":"single_observation","observation_range":"眼前", "feature":feature,
            "investigation":{"quest":"books","result":result}}


def main():
    writer = OfflineAuthor()
    with TemporaryDirectory(prefix="rpg-world-") as directory:
        seed(directory, writer)
        def reopen():
            dashboard = build_dashboard(directory, provider=writer)
            dashboard.engine.cascade_provider = OfflineBackground()
            return dashboard
        dash = reopen()
        checks = {}
        for step in range(1, 101):
            action = f"第{step}次留意周围变化。"
            draft = {"narration":f"你进行第{step}次观察。","moves":[],"places":[],"cast":[],"facts":[],
                     "clock":[{"advance":False,"reason":"观察"}]}
            speech = {}
            if step == 1:
                draft["quests"] = [{"op":"open","id":"books","summary":"找出遗失账簿的去处","objective":{
                    "kind":"investigate","place":INN,"feature":"旧柜","alternatives":[{"place":MARKET,"feature":"寄存簿"}]}}]
            if step in (2,22,41):
                npc, words = (BOB,RED) if step==22 else (ALICE,BLUE)
                speech = {"recipient":npc,"statement":words}
                action = "我交代一句原话。"
                # Deliberately omit the model's knowledge section.
            if step in (3, 23):
                action = f"查证 books {1 if step==3 else 2}"
                draft["world"] = [observation(INN,"旧柜","柜子是空的。","miss") if step==3 else
                                  observation(MARKET,"寄存簿","旧账簿寄存在七号柜。","confirmed")]
            if step in (21,35):
                dest = MARKET if step==21 else INN
                action = f"前往{dest}。"
                draft["moves"] = [{"who":HERO,"to":dest}]
            if step==24:
                source = dash.state()["objectives"]["active_quests"][0]["leads"][1]
                draft["quests"] = [{"op":"resolve","id":"books","summary":"账簿存放于七号柜","evidence":{
                    "source_event_id":source["source_event_id"],"quote":source["quote"]}}]
            if step % 10 == 0:
                action = "我等到明天早晨。"
                draft["clock"] = [{"advance":True,"days":1,"bands":0,"reason":"等候一日"}]
            writer.plan = draft
            request = dict(player_input=action,action_id=f"step-{step}",expected_revision=dash.engine.store.revision,**speech)
            reply = dash.action(**request)
            if step in (3,22,41,81):
                assert dash.action(**request)["duplicate"]
            if step==50:
                dash.undo(expected_revision=dash.engine.store.revision)
                assert dash.state()["scene"]["time"]["day"] == 5
                reply = dash.action(**{**request,"action_id":"replacement-50","expected_revision":dash.engine.store.revision})
            state, world = reply["state"], dash.engine.world
            day = 1 + step // 10
            assert world["meta"]["day"] == day
            assert {world["systems"]["lore"]["lines"][k]["stage_idx"] for k in ("bridge","bookstalls","mail")} == {day-2}
            if step in (10,20):
                route = state["objectives"]["active_quests"][0]["leads"][1]
                assert ("action" in route) == (step==20)
            if step in (22,41,100):
                mind = native_authoring_context(world, _build_scene(dash.engine),
                                                trusted_events=dash.engine.store.iter_events())
                for npc in mind["present_people"]:
                    text = json.dumps(npc["speakable_evidence"],ensure_ascii=False)
                    assert (RED if npc["id"]==ALICE else BLUE) not in text
            if step in (25,50,75,100):
                revision = dash.engine.store.revision
                dash.engine.store.close()
                dash = reopen()
                history = dash.action(player_input="第一天我对阿岚说了什么？",action_id=f"query-{step}", expected_revision=revision)
                assert history["read_only"] and history["revision"]==revision
                assert [r["source"]["text"] for r in history["recall"]["opened"]] == [BLUE]
                assert dash.state()["objectives"]["completed_quests"][0]["id"] == "books"
                checks[step] = {"day":day, "stage":day-2, "reopened_revision":revision}
        events = list(dash.engine.store.iter_events())
        totals = Counter(e["type"] for e in events)
        assert totals["lore_advanced"] == 30 and totals["route_changed"] == 2 and totals["quest_resolved"] == 1
        assert totals["narration_recorded"] == 100
        speech = [e for e in events if e["deltas"].get("source_kind")=="player_statement"]
        assert [(e["day"],e["deltas"]["knower"]) for e in speech] == [(1,ALICE),(3,BOB),(5,ALICE)]
        graph = dash.engine.world["systems"]["ontology"]
        assert graph.value_at(ALICE, "knows:"+player_statement_fact_key(HERO,RED),11) is None
        assert graph.value_at(BOB, "knows:"+player_statement_fact_key(HERO,BLUE),11) is None
        print(json.dumps({"ordinary_actions":100,"cold_reopens":checks,"hooks_disabled":[],
              "model":"deterministic offline fixtures","active_events":len(events),"types":totals,
              "foreground_provider_calls":writer.calls_by_role},ensure_ascii=False,indent=2))
        dash.engine.store.close()


if __name__ == "__main__":
    main()
