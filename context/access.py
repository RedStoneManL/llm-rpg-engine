"""Shared read boundary for narrator context and player-facing queries.

The authoritative graph is retained for backstage simulation. A POV view filters
entities before matching and replaces private truth with the observer's belief.
Legacy places without visibility metadata keep their public topology; newly
authored hidden entities opt in with attrs.visibility='hidden'.
"""
import copy

from facts.graph import FactGraph
from systems.knowledge import knows


def entity_visible(graph, entity, scene, pov, day):
    eid = entity.id
    if eid == pov or eid == scene.get('location') or eid in scene.get('present', []):
        return True
    if entity.etype == 'Person' and scene.get('location') in graph.neighbors(eid, 'located_in', day):
        return True
    if pov in (entity.attrs.get('discovered_by') or []):
        return True
    if knows(graph, pov, f'{eid}.discovered', day):
        return True
    # Knowledge of a facet establishes that the person/place has been encountered.
    if any(f.predicate.startswith(f'knows:{eid}.') for f in graph.current_facts(pov)):
        return True
    visibility = entity.attrs.get('visibility')
    if visibility in {'hidden', 'secret', 'undiscovered'}:
        return False
    if entity.etype == 'Person':
        return visibility == 'public'
    return True


def pov_world(world, scene, *, pov=None, redact_facts=True):
    """Return an independent graph projection; never modify canonical world data."""
    graph = world.get('systems', {}).get('ontology')
    pov = pov or scene.get('protagonist')
    if graph is None or not pov:
        return world
    day = scene.get('day') or world.get('meta', {}).get('day') or 1
    present = set(scene.get('present', []))
    if scene.get('location'):
        present.update(eid for eid,e in graph.entities.items() if e.etype == 'Person'
                       and scene['location'] in graph.neighbors(eid, 'located_in', day))
    view = FactGraph()
    view.entities = {eid:copy.deepcopy(entity) for eid,entity in graph.entities.items()
                     if entity_visible(graph, entity, scene, pov, day)}
    for fact in graph.facts:
        if fact.subject not in view.entities:
            continue
        if not redact_facts:
            view.facts.append(copy.deepcopy(fact))
            continue
        if not fact.valid_at(day):
            continue
        if fact.predicate.startswith('knows:'):
            if fact.subject == pov:
                exposed = copy.deepcopy(fact)
                own_prefix = f'knows:{pov}.'
                if fact.predicate.startswith(own_prefix):
                    predicate = fact.predicate[len(own_prefix):]
                    direct = graph._fact_current.get((pov, predicate))
                    if direct is not None and predicate != 'hidden' and direct.secrecy != 'secret':
                        exposed.value = copy.deepcopy(direct.value)
                view.facts.append(exposed)
            continue
        believed = knows(graph, pov, f'{fact.subject}.{fact.predicate}', day)
        self_visible = fact.subject == pov and fact.predicate != 'hidden' and fact.secrecy != 'secret'
        entity = graph.get_entity(fact.subject)
        observable = {'sketch', 'name', '真名', 'mood', 'state'} | set(entity.attrs.get('observable_fields') or [])
        co_present_visible = (fact.subject in present and
            fact.predicate in observable and fact.secrecy not in {'secret', 'restricted'})
        if believed is not None or fact.secrecy == 'public' or self_visible or co_present_visible:
            exposed = copy.deepcopy(fact)
            if believed is not None and not self_visible:
                exposed.value = copy.deepcopy(believed)
            view.facts.append(exposed)
    view.relations = [copy.deepcopy(r) for r in graph.relations
                      if r.src in view.entities and r.dst in view.entities
                      and r.attrs.get('visibility') not in {'hidden', 'secret'}]
    view.reindex_facts()
    return {**world, 'systems': {**world['systems'], 'ontology': view}}
