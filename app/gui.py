"""A small, loopback-only player dashboard for the offline RPG demo.

This is intentionally a *player* surface, not a GM/editor console.  It never
serializes the event store or canonical world.  Every game-facing graph read is
made from ``context.access.pov_world`` and the only prose catalogue exposed is
the public CodexSystem index.

Run it locally with::

    python -m app.gui --campaign ./campaigns/gui-demo --demo

The server always binds to 127.0.0.1.  The demo uses a deterministic local
strategy and FakeLLMProvider; it makes no network or model request.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

from app.engine import build_engine, last_turn, rewind
from app.play import _build_scene
from context.access import pov_world
from kernel.events import kernel_event
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.turn import REQUIRED_SECTIONS, run_turn


log = logging.getLogger(__name__)
_MAX_ACTION_BYTES = 2_000


class GuiRequestError(ValueError):
    """A safe, player-facing request error (never contains provider/store text)."""


class OfflineDemoStrategy:
    """Deterministic, local narrator used by the GUI demo.

    It deliberately exercises the normal turn publication path, but does not
    pretend to be an AI narrator.  Each action moves the protagonist to the
    next visible adjacent place when one exists, so the map has observable,
    offline behaviour without a remote provider.
    """

    def produce(self, registry, world, scene, player_input, *, provider, embedder=None, repair=None):
        # The demo has no special authority: its movement proposal must use
        # the same known-exit view that is serialized to the player, never the
        # backstage/canonical adjacency graph.
        view = pov_world(world, scene, pov=scene.get("protagonist"), redact_facts=True)
        graph = view.get("systems", {}).get("ontology")
        day = scene.get("day") or 1
        location = scene.get("location")
        exits = graph.neighbors(location, "adjacent_to", day) if graph and location else []
        moves: list[dict[str, str]] = []
        destination = exits[0] if exits else None
        if destination and scene.get("protagonist"):
            moves.append({"who": scene["protagonist"], "to": destination})
        if destination:
            narration = f"离线演示已保存你的行动：{player_input}。你沿着已知道路来到 {destination}。"
        else:
            narration = f"离线演示已保存你的行动：{player_input}。这里暂时没有已知出口。"
        return TurnCommit(narration=narration, sections={
            "moves": moves,
            "places": [],
            "cast": [],
            "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "离线演示中的即时行动"}],
        })

    def repair_sections(self, *args, **kwargs):  # pragma: no cover - demo output is already valid
        return {}

    def commit_to_thread(self, narration: str) -> None:
        return None


def _seed_demo_campaign(engine) -> None:
    """Seed a small existing-engine world only when the campaign is empty."""
    if any(True for _ in engine.store.iter_events()):
        return
    events = [
        kernel_event("character_created", day=1, scene="s1", turn=0,
                     summary="离线演示主角", deltas={
                         "id": "protagonist", "sketch": "一位初来石桥镇的旅人",
                         "goal": "在黎明前找到旧塔的线索", "tier": "tracked",
                     }),
        kernel_event("place_created", day=1, scene="s1", turn=0,
                     summary="石桥客栈", deltas={"id": "stonebridge_inn", "level": 2,
                                                   "kind": "venue", "seed": "石桥镇边的暖灯客栈", "tier": "tracked"}),
        kernel_event("place_created", day=1, scene="s1", turn=0,
                     summary="石桥集市", deltas={"id": "stonebridge_market", "level": 2,
                                                   "kind": "settlement", "seed": "清晨刚开张的石桥集市", "tier": "tracked"}),
        kernel_event("place_linked", day=1, scene="s1", turn=0,
                     summary="客栈与集市相连", deltas={"a": "stonebridge_inn", "b": "stonebridge_market", "travel_cost": 1}),
        kernel_event("entity_moved", day=1, scene="s1", turn=0,
                     summary="主角在客栈", deltas={"who": "protagonist", "to": "stonebridge_inn"}),
        kernel_event("object_created", day=1, scene="s1", turn=0,
                     summary="旧铜灯", deltas={"id": "old_brass_lantern", "tier": "tracked", "label": "旧铜灯"}),
        kernel_event("item_transferred", day=1, scene="s1", turn=0,
                     summary="主角持有旧铜灯", deltas={"item": "old_brass_lantern", "to": "protagonist"}),
        # Deliberately retained only in the canonical store.  The browser
        # acceptance test proves this never crosses the player DTO boundary.
        kernel_event("fact_asserted", day=1, scene="s1", turn=0,
                     summary="未公开的演示秘密", deltas={"subject": "protagonist", "predicate": "hidden",
                                                          "value": "GUI_DEMO_PRIVATE_SENTINEL", "secrecy": "secret"}),
        kernel_event("codex_entry_added", day=1, scene="s1", turn=0,
                     summary="石桥镇设定", deltas={"id": "stonebridge", "kind": "地理志",
                                                    "title": "石桥镇", "body": "河谷中的商旅小镇，旧塔耸立在北岸。"}),
        kernel_event("narration_recorded", day=1, scene="s1", turn=0,
                     summary="离线演示开场", deltas={"scene": "s1", "text": "晨雾笼罩石桥镇。你的旧铜灯仍在手边，客栈外通往集市的小路已经醒来。"}),
    ]
    engine.store.append_many(events)
    # Projection through build_engine's configured registry keeps this seed in
    # the same store/replay format as normal campaigns.
    from kernel.projection import project
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision


def _safe_value(value: Any) -> str:
    """Make a bounded plain-text display value; never pass structured objects through."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:500]


def build_player_state(engine) -> dict[str, Any]:
    """Build the narrow, player-safe JSON DTO used by the browser.

    Do not return a graph/world here.  Keeping this hand-shaped makes accidental
    additions of EventStore, private interpretations, or DM-only state visible
    during code review and tests.
    """
    scene = _build_scene(engine)
    view = pov_world(engine.world, scene, pov=scene.get("protagonist"), redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    day = scene.get("day") or 1
    protagonist = scene.get("protagonist")

    character_facts: list[dict[str, str]] = []
    inventory: list[dict[str, str]] = []
    places: dict[str, dict[str, str]] = {}
    exits: list[dict[str, str]] = []
    if graph is not None:
        for fact in graph.current_facts(protagonist):
            if fact.predicate != "hidden" and fact.secrecy != "secret":
                character_facts.append({"name": str(fact.predicate), "value": _safe_value(fact.value)})
        for entity_id, entity in graph.entities.items():
            if entity.etype == "Object" and protagonist in graph.neighbors(entity_id, "held_by", day):
                inventory.append({"id": entity_id, "label": str(entity.attrs.get("label") or entity_id)})
            if entity.etype == "Place":
                places[entity_id] = {"id": entity_id, "label": str(entity.attrs.get("label") or entity_id),
                                     "kind": str(entity.attrs.get("kind") or "地点")}
        location = scene.get("location")
        for destination in graph.neighbors(location, "adjacent_to", day) if location else []:
            if destination in places:
                exits.append(places[destination])

    narrations: list[str] = []
    for event in engine.public_history():
        # ``public_history`` is a privacy DTO, not an active-history DTO: it
        # intentionally retains the retracted marker for callers that need an
        # audit view.  The player transcript must instead follow the projected
        # world and never resurrect a rewound line on refresh/reopen.
        if not event.get("retracted") and event.get("type") == "narration_recorded":
            text = (event.get("deltas") or {}).get("text")
            if isinstance(text, str):
                narrations.append(text)
    entries = (engine.world.get("systems", {}).get("codex", {}) or {}).get("entries", [])
    codex = [{"id": str(item.get("id", "")), "kind": str(item.get("kind", "")),
              "title": str(item.get("title") or item.get("id", "")), "body": str(item.get("body", ""))}
             for item in entries if isinstance(item, dict)]
    return {
        "revision": int(engine.world.get("_revision") or engine.store.revision),
        "turn": last_turn(engine),
        "scene": {"day": day, "location": places.get(scene.get("location")), "exits": exits},
        "character": {"id": protagonist, "facts": character_facts},
        "inventory": inventory,
        "narration": narrations[-8:],
        "codex": codex,
    }


@dataclass
class _RequestContext:
    player_input: str
    expected_revision: int | None
    scene: dict[str, Any]
    prev_scene: dict[str, Any] | None


class PlayerDashboard:
    """In-process player UI controller; all mutations stay in ``run_turn``."""

    def __init__(self, engine):
        self.engine = engine
        self.strategy = OfflineDemoStrategy()
        self.previous_scene: dict[str, Any] | None = None
        self.requests: dict[str, _RequestContext] = {}
        self.lock = threading.RLock()

    def state(self) -> dict[str, Any]:
        with self.lock:
            return build_player_state(self.engine)

    def action(self, *, player_input: str, action_id: str, expected_revision: int | None) -> dict[str, Any]:
        if not isinstance(player_input, str) or not player_input.strip():
            raise GuiRequestError("请输入一个行动。")
        if len(player_input.encode("utf-8")) > _MAX_ACTION_BYTES:
            raise GuiRequestError("行动过长，请缩短后再试。")
        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
            raise GuiRequestError("请求标识无效，请刷新后重试。")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise GuiRequestError("页面状态无效，请刷新后重试。")
        with self.lock:
            existing = self.requests.get(action_id)
            if existing is not None and existing.player_input == player_input and existing.expected_revision == expected_revision:
                context = existing
            else:
                context = _RequestContext(player_input, expected_revision, _build_scene(self.engine), self.previous_scene)
                if existing is None:
                    self.requests[action_id] = context
            result = run_turn(
                self.engine.registry, self.engine.store, self.engine.world, context.scene, player_input,
                strategy=self.strategy, provider=self.engine.provider, embedder=self.engine.embedder,
                required_sections=REQUIRED_SECTIONS, cascade_provider=self.engine.cascade_provider,
                prev_scene=context.prev_scene, action_id=action_id, expected_revision=expected_revision,
            )
            self.engine.world = result.world
            self.previous_scene = context.scene
            return {
                "ok": True,
                "duplicate": bool(getattr(result, "receipt", {}).get("duplicate")),
                "narration": result.narration,
                "revision": result.receipt.get("revision"),
                "recap_status": result.recap_status,
                "state": build_player_state(self.engine),
            }

    def undo(self, *, expected_revision: int | None) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise GuiRequestError("撤销请求无效，请刷新后重试。")
        with self.lock:
            if expected_revision != self.engine.store.revision:
                raise GuiRequestError("世界已变化；请刷新后再决定是否撤销。")
            turn = last_turn(self.engine)
            if turn <= 0:
                raise GuiRequestError("当前没有可撤销的玩家回合。")
            rewind(self.engine, turn)
            self.previous_scene = None
            return {"ok": True, "message": "已撤销最后一回合。", "state": build_player_state(self.engine)}


_INDEX = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>LLM RPG · 离线玩家仪表盘</title><link rel=\"stylesheet\" href=\"/app.css\"></head><body><main><header><p class=\"eyebrow\">LOCAL · PLAYER VIEW · OFFLINE DEMO</p><h1>石桥镇冒险</h1><p id=\"status\" role=\"status\">正在读取已保存的世界…</p></header><section class=\"panel story\"><h2>叙事</h2><div id=\"narration\" class=\"narration\"></div><form id=\"action-form\"><label for=\"action-input\">你的行动</label><textarea id=\"action-input\" maxlength=\"2000\" placeholder=\"例如：走向集市，向路人打听旧塔。\" required></textarea><div class=\"actions\"><button id=\"submit-action\" type=\"submit\">保存行动</button><button id=\"undo\" type=\"button\" class=\"subtle\">撤销上一回合</button></div></form></section><section class=\"grid\"><article class=\"panel\"><h2>角色状态</h2><dl id=\"character\"></dl></article><article class=\"panel\"><h2>背包</h2><ul id=\"inventory\"></ul></article><article class=\"panel\"><h2>当前位置与地图</h2><div id=\"map\"></div></article><article class=\"panel codex\"><h2>公开世界设定典</h2><div id=\"codex\"></div></article></section><footer>这是离线玩家试用界面：不会显示 GM、原始事件、其他角色私有解释或编辑功能。</footer></main><script src=\"/app.js\"></script></body></html>"""

_CSS = """*{box-sizing:border-box}body{margin:0;background:#10151d;color:#e9edf2;font:16px/1.55 system-ui,-apple-system,sans-serif}main{max-width:1120px;margin:auto;padding:26px 18px 42px}.eyebrow{color:#8ec5ff;letter-spacing:.08em;font-size:.78rem}h1{margin:.1rem 0}h2{font-size:1.08rem;margin-top:0}.panel{background:#19222e;border:1px solid #2d3c4e;border-radius:12px;padding:18px;box-shadow:0 4px 18px #0003}.story{margin:18px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.narration p{white-space:pre-wrap;border-left:3px solid #75b8ee;padding-left:12px}.actions{display:flex;gap:10px;margin-top:10px}textarea{width:100%;min-height:88px;margin-top:7px;padding:10px;background:#0d131b;border:1px solid #405469;border-radius:8px;color:inherit}button{border:0;border-radius:8px;background:#75b8ee;color:#09233a;padding:9px 13px;font-weight:700;cursor:pointer}.subtle{background:#33465a;color:#eef5fa}button:disabled{opacity:.55;cursor:wait}dt{color:#9eb1c5}dd{margin:0 0 10px}ul{padding-left:20px}.place{font-weight:700}.empty{color:#9eb1c5}details{border-top:1px solid #304052;padding:8px 0}summary{cursor:pointer;color:#b7dcfb}footer{margin-top:24px;color:#9eb1c5;font-size:.9rem}@media(max-width:700px){main{padding:16px 12px}.grid{grid-template-columns:1fr}.actions{flex-direction:column}button{width:100%}}"""

_JS = """(() => {let state=null;const $=id=>document.getElementById(id);const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};function clear(n){n.replaceChildren()}function setStatus(t,err=false){const n=$('status');n.textContent=t;n.style.color=err?'#ffadb0':'#e9edf2'}function render(s){state=s;clear($('narration'));(s.narration.length?s.narration:['尚无公开叙事。']).forEach(t=>$('narration').append(el('p',t)));clear($('character'));if(!s.character.facts.length){$('character').append(el('p','暂无可展示的角色状态。'))}s.character.facts.forEach(f=>{const dt=el('dt',f.name),dd=el('dd',f.value);$('character').append(dt,dd)});clear($('inventory'));if(!s.inventory.length)$('inventory').append(el('li','背包为空。'));s.inventory.forEach(x=>$('inventory').append(el('li',x.label)));clear($('map'));const loc=s.scene.location;$('map').append(el('p',loc?`当前位置：${loc.label}`:'当前位置尚未确定。'));const exits=el('ul');if(!s.scene.exits.length)exits.append(el('li','没有已知相邻地点。'));s.scene.exits.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));$('map').append(exits);clear($('codex'));if(!s.codex.length)$('codex').append(el('p','暂无公开设定条目。'));s.codex.forEach(x=>{const d=el('details'),sum=el('summary',`${x.kind?`[${x.kind}] `:''}${x.title}`),p=el('p',x.body);d.append(sum,p);$('codex').append(d)});setStatus(`第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)}async function api(url,options){const r=await fetch(url,options);const data=await r.json().catch(()=>({message:'响应格式错误，请刷新页面。'}));if(!r.ok)throw new Error(data.message||'请求未完成，请刷新后重试。');return data}async function load(){try{render(await api('/api/state'))}catch(e){setStatus(e.message,true)}}$('action-form').addEventListener('submit',async e=>{e.preventDefault();if(!state)return;const input=$('action-input'),button=$('submit-action'),text=input.value.trim();if(!text)return;button.disabled=true;$('undo').disabled=true;setStatus('正在保存行动…');const actionId=crypto.randomUUID?crypto.randomUUID():`gui-${Date.now()}-${Math.random()}`;try{const d=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,action_id:actionId,expected_revision:state.revision})});render(d.state);input.value='';setStatus(d.duplicate?'已恢复此前保存的行动。':`行动已保存（版本 ${d.revision}）。`)}catch(err){setStatus(err.message,true)}finally{button.disabled=false;$('undo').disabled=false}});$('undo').addEventListener('click',async()=>{try{setStatus('正在撤销…');const d=await api('/api/undo',{method:'POST'});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}});load()})();"""

# Static text is kept separate from all game data.  The replacement adds an
# explicit recovery action without interpolating a player/world string into the
# HTML document.
_INDEX = _INDEX.replace(
    '<button id="undo" type="button" class="subtle">撤销上一回合</button>',
    '<button id="undo" type="button" class="subtle">撤销上一回合</button><button id="resume-action" type="button" class="subtle" hidden>恢复上次请求</button>',
)
_JS = r"""(() => {
let state=null,busy=false; const pendingKey='llm-rpg.offline-player.pending-action.v1';
const $=id=>document.getElementById(id); const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
function clear(n){n.replaceChildren()} function setStatus(t,err=false){const n=$('status');n.textContent=t;n.style.color=err?'#ffadb0':'#e9edf2'}
function pending(){try{const p=JSON.parse(sessionStorage.getItem(pendingKey)||'null');return p&&typeof p.text==='string'&&typeof p.action_id==='string'&&Number.isInteger(p.expected_revision)?p:null}catch(_){sessionStorage.removeItem(pendingKey);return null}}
function syncPending(){const p=pending();$('resume-action').hidden=!p;if(p&&!busy)setStatus('上次行动尚未得到确认；可恢复原请求，或先刷新查看当前状态。',true)}
function setBusy(next){busy=next;$('submit-action').disabled=next;$('undo').disabled=next;$('resume-action').disabled=next}
function render(s){state=s;clear($('narration'));(s.narration.length?s.narration:['尚无公开叙事。']).forEach(t=>$('narration').append(el('p',t)));clear($('character'));if(!s.character.facts.length)$('character').append(el('p','暂无可展示的角色状态。'));s.character.facts.forEach(f=>$('character').append(el('dt',f.name),el('dd',f.value)));clear($('inventory'));if(!s.inventory.length)$('inventory').append(el('li','背包为空。'));s.inventory.forEach(x=>$('inventory').append(el('li',x.label)));clear($('map'));const loc=s.scene.location;$('map').append(el('p',loc?`当前位置：${loc.label}`:'当前位置尚未确定。'));const exits=el('ul');if(!s.scene.exits.length)exits.append(el('li','没有已知相邻地点。'));s.scene.exits.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));$('map').append(exits);clear($('codex'));if(!s.codex.length)$('codex').append(el('p','暂无公开设定条目。'));s.codex.forEach(x=>{const d=el('details');d.append(el('summary',`${x.kind?`[${x.kind}] `:''}${x.title}`),el('p',x.body));$('codex').append(d)});setStatus(`第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)}
async function api(url,options){const r=await fetch(url,options);const data=await r.json().catch(()=>({message:'响应格式错误，请刷新页面。'}));if(!r.ok)throw new Error(data.message||'请求未完成，请刷新后重试。');return data}
async function sendAction(envelope,recovering){if(busy)return;setBusy(true);setStatus(recovering?'正在恢复上次请求…':'正在保存行动…');try{const d=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envelope)});sessionStorage.removeItem(pendingKey);render(d.state);syncPending();$('action-input').value='';setStatus(d.duplicate?'已恢复此前保存的行动。':`行动已保存（版本 ${d.revision}）。`)}catch(err){syncPending();setStatus(`请求尚未得到确认；请刷新查看当前状态或恢复上次请求。${err.message}`,true)}finally{setBusy(false)}}
async function load(){try{render(await api('/api/state'));syncPending()}catch(e){setStatus(e.message,true)}}
$('action-form').addEventListener('submit',e=>{e.preventDefault();if(!state||busy)return;const old=pending();if(old){setStatus('请先恢复或检查上次未确认的请求。',true);return}const text=$('action-input').value.trim();if(!text)return;const action_id=crypto.randomUUID?crypto.randomUUID():`gui-${Date.now()}-${Math.random()}`;const envelope={text,action_id,expected_revision:state.revision};sessionStorage.setItem(pendingKey,JSON.stringify(envelope));sendAction(envelope,false)});
$('resume-action').addEventListener('click',()=>{const p=pending();if(p)sendAction(p,true)});
$('undo').addEventListener('click',async()=>{if(!state||busy)return;setBusy(true);setStatus('正在撤销…');try{const d=await api('/api/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision})});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}finally{setBusy(false)}});load()
})();"""


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        log.info("gui http %s", format % args)


def _json_response(start_response, status: str, payload: dict[str, Any]):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(raw))),
                            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")])
    return [raw]


def _require_same_origin_json(environ) -> None:
    """Reject cross-site/simple-form writes before they reach a controller.

    Loopback binding prevents remote network peers, but browsers can still send
    a cross-site form to localhost.  This single-player server has no accounts,
    so strict expected Host + Origin + JSON is the appropriate narrow guard.
    """
    host = environ.get("HTTP_HOST")
    # wsgiref's SERVER_PORT is not reliable when the listener is wrapped by
    # the local execution sandbox, so validate the actual Host supplied by the
    # browser rather than comparing it with that internal default.  It must be
    # an explicit numeric 127.0.0.1 authority, and Origin (when supplied) must
    # match it exactly.
    valid_loopback_host = isinstance(host, str) and host.startswith("127.0.0.1:") and host[10:].isdigit()
    expected_origin = f"http://{host}" if valid_loopback_host else None
    content_type = (environ.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    # Same-origin ``fetch`` is permitted to omit Origin by browsers.  A
    # cross-site script sends it, while a cross-site form cannot manufacture
    # application/json; Host remains exact in both cases.
    origin = environ.get("HTTP_ORIGIN")
    if not valid_loopback_host or origin not in (None, expected_origin) or content_type != "application/json":
        raise GuiRequestError("此本地页面不能接受跨站或非 JSON 写入请求。")


def make_app(dashboard: PlayerDashboard):
    """Return a closed, no-file-serving WSGI app for exactly the player routes."""
    def app(environ, start_response):
        path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "GET")
        try:
            if method == "GET" and path == "/":
                body = _INDEX.encode("utf-8")
                start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                                            ("Cache-Control", "no-store"), ("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
                                            ("X-Content-Type-Options", "nosniff")])
                return [body]
            if method == "GET" and path == "/app.css":
                body = _CSS.encode("utf-8"); start_response("200 OK", [("Content-Type", "text/css; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]); return [body]
            if method == "GET" and path == "/app.js":
                body = _JS.encode("utf-8"); start_response("200 OK", [("Content-Type", "application/javascript; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]); return [body]
            if method == "GET" and path == "/api/state":
                return _json_response(start_response, "200 OK", dashboard.state())
            if method == "POST" and path == "/api/action":
                _require_same_origin_json(environ)
                size = int(environ.get("CONTENT_LENGTH") or 0)
                if size < 0 or size > _MAX_ACTION_BYTES + 512:
                    raise GuiRequestError("请求过大，请刷新后重试。")
                raw = environ["wsgi.input"].read(size)
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise GuiRequestError("请求格式无效，请刷新后重试。")
                return _json_response(start_response, "200 OK", dashboard.action(
                    player_input=data.get("text"), action_id=data.get("action_id"), expected_revision=data.get("expected_revision")))
            if method == "POST" and path == "/api/undo":
                _require_same_origin_json(environ)
                size = int(environ.get("CONTENT_LENGTH") or 0)
                if size < 2 or size > 256:
                    raise GuiRequestError("撤销请求无效，请刷新后重试。")
                data = json.loads(environ["wsgi.input"].read(size).decode("utf-8"))
                if not isinstance(data, dict):
                    raise GuiRequestError("撤销请求无效，请刷新后重试。")
                return _json_response(start_response, "200 OK", dashboard.undo(
                    expected_revision=data.get("expected_revision")))
            return _json_response(start_response, "404 Not Found", {"message": "找不到此页面。"})
        except GuiRequestError as exc:
            return _json_response(start_response, "400 Bad Request", {"message": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError):
            return _json_response(start_response, "400 Bad Request", {"message": "请求格式无效，请刷新后重试。"})
        except Exception:
            log.exception("player gui request failed")
            return _json_response(start_response, "500 Internal Server Error", {"message": "请求结果未能显示；请刷新查看当前状态。"})
    return app


def build_dashboard(campaign_dir: str | Path, *, demo: bool = False) -> PlayerDashboard:
    """Create an offline-only dashboard. Interpretive memory remains disabled."""
    engine = build_engine(campaign_dir, provider=FakeLLMProvider(responses=["1"]), embedder=None,
                          enable_interpretive_memory=False)
    if demo:
        _seed_demo_campaign(engine)
    return PlayerDashboard(engine)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地离线玩家仪表盘（首个 GUI 切片，非 GM/编辑器）")
    parser.add_argument("--campaign", required=True, help="启动时选择的本地 campaign 目录")
    parser.add_argument("--demo", action="store_true", help="仅在空 campaign 中写入固定离线演示世界")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"], help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help="本机端口（0 可让系统分配）")
    args = parser.parse_args(argv)
    if not (0 <= args.port <= 65535):
        parser.error("--port 必须在 0 到 65535 之间")
    dashboard = build_dashboard(args.campaign, demo=args.demo)
    server = make_server("127.0.0.1", args.port, make_app(dashboard), handler_class=_QuietHandler)
    print(f"玩家仪表盘已在 http://127.0.0.1:{server.server_port} 启动（Ctrl-C 退出）。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        dashboard.engine.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
