"""Host-authored resource outcomes; optional per-scenario fact rules."""
from kernel.contextsystem import ContextSystem


class ResourceSystem(ContextSystem):
    name='resources'
    def requires(self): return {'ontology'}
    def event_types(self): return {'resources_resolved', 'resources_configured'}
    def commit_sections(self): return set()
    def empty_state(self): return {}

    def apply(self, world, event):
        d=event['deltas'];graph=world['systems']['ontology']
        if event['type']=='resources_configured':
            entity=graph.get_entity(d['subject'])
            if entity is None:
                raise ValueError('resource owner must exist before configuration')
            rules=entity.attrs.setdefault('fact_rules',{})
            for predicate,spec in d['resources'].items():
                rule={k:v for k,v in spec.items() if k!='initial'}
                rule['resource']=True
                if predicate in rules and rules[predicate]!=rule:
                    raise ValueError('resource rule is already configured')
                rules[predicate]=rule
                graph.assert_fact(d['subject'],predicate,spec['initial'],day=event['day'],
                    turn=event.get('turn') or 0,source_event=event['id'],secrecy='public')
            return
        for predicate,value in d['after'].items():
            if value != d['before'][predicate]:
                graph.assert_fact(d['subject'],predicate,value,day=event['day'],
                    turn=event.get('turn') or 0,source_event=event['id'],secrecy='public')
        world['systems'][self.name]['last_resolution']=dict(d)
