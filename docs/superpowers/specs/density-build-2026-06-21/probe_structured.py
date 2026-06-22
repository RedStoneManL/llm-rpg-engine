"""Live-probe: do cascade _node_verdict + catchup conform on glm-5.1 via the
validate→repair loop? (Confirms the per-site validators aren't over-strict.)

Run: cd /root/rpg-engine-app && set -a; . ./.env.local; set +a
     export PYTHONPATH=/root/rpg-engine-app
     python3 docs/superpowers/specs/density-build-2026-06-21/probe_structured.py
"""
import os
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from llm.provider import make_provider
from llm.structured import complete_structured
from loop.cascade import _node_verdict, _node_validate, _NODE_SYSTEM
from loop.time import _CATCHUP_SYSTEM, _catchup_prompt, _catchup_validate

P = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                  base_url=os.environ["GLM_BASE_URL"],
                  max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))

print("=" * 60)
print("CASCADE _node_verdict (parent 盐港大火 → child 渔港码头):")
v = _node_verdict("渔港码头", "父地点幽港镇盐仓大火,烧了三天,盐价飞涨,流民涌入", P)
print(f"  → {v}")
print(f"  conforms: {not _node_validate(v) if isinstance(v, dict) and 'evolve' in v else 'N/A (forced id only → pruned)'}")

print("=" * 60)
print("CATCHUP Person (角色离场 8 天):")
obj, errs = complete_structured(
    P, system=_CATCHUP_SYSTEM,
    user=_catchup_prompt("阿庚", "Person", 8, "幽港镇盐价飞涨,码头戒严"),
    validate=_catchup_validate("Person"), max_repairs=1, log_label="catchup")
print(f"  → obj={obj}  errors={errs}")

print("=" * 60)
print("CATCHUP Place (地点离场 8 天):")
obj2, errs2 = complete_structured(
    P, system=_CATCHUP_SYSTEM,
    user=_catchup_prompt("盐商会馆", "Place", 8, "幽港镇盐价飞涨,码头戒严"),
    validate=_catchup_validate("Place"), max_repairs=1, log_label="catchup")
print(f"  → obj={obj2}  errors={errs2}")
print("=" * 60)
print("DONE")
