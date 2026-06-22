# engine/compact.py
from pathlib import Path

from engine.store import EventStore
from engine.projection import project, write_projections
from engine.log import get_logger

log = get_logger("compact")

def _project(campaign_dir):
    cd = Path(campaign_dir)
    with EventStore(cd / "events.db", cd / "events.jsonl") as s:
        return project(s.iter_events())

def build_working_memory(campaign_dir):
    proj = _project(campaign_dir)
    st = proj["state"]
    lines = ["# 工作记忆", ""]
    lines.append(f"**当前**:Day {st.get('day')} · 地点 {st.get('location')}")
    if proj["characters"]:
        lines.append("\n## 在场/近期角色")
        for name, c in proj["characters"].items():
            lines.append(f"- {name}:信任={c.get('trust')} · {'; '.join(f'{k}={v}' for k,v in c.get('profile',{}).items())}")
    active = [t for t in proj["threads"].values() if t.get("status") != "已解锁" and not t.get("dormant")]
    if active:
        lines.append("\n## 活跃明/暗线")
        for t in active:
            beats = t.get("beats") or []
            nxt = beats[0] if beats else "?"
            lines.append(f"- {t.get('name')}(进度 {t.get('progress')}):下一拍 {nxt}")
    open_p = [p for p in proj["promises"] if not p["kept"]]
    if open_p:
        lines.append("\n## 未兑现承诺")
        for p in open_p:
            lines.append(f"- {p['text']}")
    if proj["villains"]:
        lines.append("\n## 反派能力边界(防全知)")
        for name, v in proj["villains"].items():
            lines.append(f"- {name}:已知 {len(v.get('knows',[]))} 项(每项须有来源)")
    wm = "\n".join(lines) + "\n"
    log.debug("build_working_memory chars=%d threads=%d promises=%d len=%d",
              len(proj["characters"]), len(active), len(open_p), len(wm))
    return wm

def compact(campaign_dir):
    cd = Path(campaign_dir)
    proj = _project(cd)
    write_projections(proj, cd / "projections")
    wm = build_working_memory(cd)
    (cd / "working_memory.md").write_text(wm, encoding="utf-8")
    log.debug("compact wrote projections + working_memory at %s", cd)
