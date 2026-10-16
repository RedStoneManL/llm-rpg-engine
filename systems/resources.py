"""Host-authored resource outcomes; optional per-scenario fact rules."""
from __future__ import annotations

import math

from kernel.contextsystem import ContextSystem


class ResourceConfigurationError(ValueError):
    """A host-supplied numeric resource rule is malformed."""


_RESOURCE_FIELDS = frozenset({"initial", "type", "min", "max", "label"})
_RESOURCE_TYPES = frozenset({"integer", "number"})


def _finite_number(value: object, *, field: str) -> int | float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value)):
        raise ResourceConfigurationError(f"{field} must be a finite number")
    return value


def _typed_number(value: object, *, kind: str, field: str) -> int | float:
    value = _finite_number(value, field=field)
    if kind == "integer" and type(value) is not int:
        raise ResourceConfigurationError(f"{field} must be an integer")
    return value


def validate_resource_configuration(resources: object) -> dict[str, dict]:
    """Return a checked copy of a host-owned numeric-resource configuration.

    Resource rules become durable ``resources_configured`` events and are later
    replayed without the source pack.  Validate the complete payload in one
    place before either genesis publication or projection mutates a fact graph.
    Only numeric, finite resource facts are meaningful to the resolver.
    """
    if not isinstance(resources, dict) or not resources:
        raise ResourceConfigurationError("resources must be a non-empty object")
    checked: dict[str, dict] = {}
    for predicate, raw_spec in resources.items():
        if not isinstance(predicate, str) or not predicate.strip():
            raise ResourceConfigurationError("resource name must be a non-empty string")
        if not isinstance(raw_spec, dict):
            raise ResourceConfigurationError(f"resource {predicate!r} must be an object")
        unknown = set(raw_spec) - _RESOURCE_FIELDS
        if unknown:
            raise ResourceConfigurationError(
                f"resource {predicate!r} has unsupported fields: {', '.join(sorted(unknown))}")
        missing = {"initial", "type", "min"} - set(raw_spec)
        if missing:
            raise ResourceConfigurationError(
                f"resource {predicate!r} is missing fields: {', '.join(sorted(missing))}")
        kind = raw_spec.get("type")
        if kind not in _RESOURCE_TYPES:
            raise ResourceConfigurationError(
                f"resource {predicate!r} type must be integer or number")
        initial = _typed_number(raw_spec.get("initial"), kind=kind,
                                field=f"resource {predicate!r} initial")
        minimum = _typed_number(raw_spec.get("min"), kind=kind,
                                field=f"resource {predicate!r} min")
        if initial < minimum:
            raise ResourceConfigurationError(
                f"resource {predicate!r} initial must be >= min")
        maximum = raw_spec.get("max")
        if maximum is not None:
            maximum = _typed_number(maximum, kind=kind,
                                    field=f"resource {predicate!r} max")
            if maximum < minimum:
                raise ResourceConfigurationError(
                    f"resource {predicate!r} max must be >= min")
            if initial > maximum:
                raise ResourceConfigurationError(
                    f"resource {predicate!r} initial must be <= max")
        label = raw_spec.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ResourceConfigurationError(
                f"resource {predicate!r} label must be a non-empty string")
        checked[predicate] = dict(raw_spec)
    return checked


def validate_resource_resolution(world: dict, event: dict, deltas: object) -> None:
    """Accept only a ledger outcome that the deterministic resolver could emit.

    ``resources_resolved`` is an event-owned commit, not a free-form fact
    patch.  Replay must therefore check its before/after snapshot against the
    configured resource rules and the graph immediately preceding the event.
    This protects both normal preflight publication and cold projection from a
    corrupted or injected ledger row changing arbitrary canonical facts.
    """
    if not isinstance(deltas, dict):
        raise ResourceConfigurationError('resource resolution deltas must be an object')
    subject = deltas.get('subject')
    if not isinstance(subject, str) or not subject:
        raise ResourceConfigurationError('resource resolution subject must be a non-empty string')
    day = event.get('day')
    if type(day) is not int:
        raise ResourceConfigurationError('resource resolution day must be an integer')
    graph = world['systems']['ontology']
    entity = graph.get_entity(subject)
    if entity is None:
        raise ResourceConfigurationError('resource resolution subject must exist')
    raw_rules = entity.attrs.get('fact_rules', {})
    rules = {
        predicate: rule for predicate, rule in raw_rules.items()
        if isinstance(predicate, str) and isinstance(rule, dict) and rule.get('resource') is True
    } if isinstance(raw_rules, dict) else {}
    if not rules:
        raise ResourceConfigurationError('resource resolution requires configured resources')
    before = deltas.get('before')
    after = deltas.get('after')
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ResourceConfigurationError('resource resolution before and after must be objects')
    if set(before) != set(rules) or set(after) != set(rules):
        raise ResourceConfigurationError('resource resolution must cover exactly the configured resources')
    for predicate, rule in rules.items():
        kind = rule.get('type')
        if kind not in _RESOURCE_TYPES or 'min' not in rule:
            raise ResourceConfigurationError('resource resolution rule is malformed')
        old_value = _typed_number(
            before[predicate], kind=kind,
            field=f'resource resolution before {predicate!r}',
        )
        new_value = _typed_number(
            after[predicate], kind=kind,
            field=f'resource resolution after {predicate!r}',
        )
        minimum = _typed_number(
            rule['min'], kind=kind,
            field=f'resource resolution minimum {predicate!r}',
        )
        maximum = rule.get('max')
        if maximum is not None:
            maximum = _typed_number(
                maximum, kind=kind,
                field=f'resource resolution maximum {predicate!r}',
            )
        if old_value < minimum or new_value < minimum or (
                maximum is not None and (old_value > maximum or new_value > maximum)):
            raise ResourceConfigurationError('resource resolution violates configured bounds')
        if graph.value_at(subject, predicate, day) != old_value:
            raise ResourceConfigurationError('resource resolution before does not match the ledger')

    resource = deltas.get('resource')
    outcome = deltas.get('outcome')
    amount = deltas.get('requested_amount')
    if outcome not in {'none', 'spent', 'insufficient'}:
        raise ResourceConfigurationError('resource resolution outcome is invalid')
    if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
            or not math.isfinite(amount) or amount < 0):
        raise ResourceConfigurationError('resource resolution amount must be finite and nonnegative')
    if outcome == 'none':
        if resource is not None or amount != 0 or after != before:
            raise ResourceConfigurationError('resource resolution none outcome is inconsistent')
        return
    if resource not in rules:
        raise ResourceConfigurationError('resource resolution names an unconfigured resource')
    kind = rules[resource]['type']
    _typed_number(amount, kind=kind, field=f'resource resolution amount {resource!r}')
    expected = dict(before)
    if outcome == 'spent':
        expected[resource] -= amount
        if expected[resource] < rules[resource]['min']:
            raise ResourceConfigurationError('resource resolution spent outcome crosses the minimum')
    elif before[resource] - amount >= rules[resource]['min']:
        raise ResourceConfigurationError('resource resolution insufficient outcome is inconsistent')
    if after != expected:
        raise ResourceConfigurationError('resource resolution after does not match the adjudication')


class ResourceSystem(ContextSystem):
    name='resources'
    def requires(self): return {'ontology'}
    def event_types(self): return {'resources_resolved', 'resources_configured'}
    def commit_sections(self): return set()
    def empty_state(self): return {}

    def apply(self, world, event):
        d=event.get('deltas')
        if not isinstance(d, dict):
            raise ResourceConfigurationError('resource event deltas must be an object')
        graph=world['systems']['ontology']
        if event['type']=='resources_configured':
            subject = d.get('subject')
            if not isinstance(subject, str) or not subject:
                raise ResourceConfigurationError('resource owner must be a non-empty string')
            entity=graph.get_entity(subject)
            if entity is None:
                raise ValueError('resource owner must exist before configuration')
            resources = validate_resource_configuration(d.get('resources'))
            rules=entity.attrs.setdefault('fact_rules',{})
            for predicate,spec in resources.items():
                rule={k:v for k,v in spec.items() if k!='initial'}
                rule['resource']=True
                if predicate in rules and rules[predicate]!=rule:
                    raise ValueError('resource rule is already configured')
                rules[predicate]=rule
                graph.assert_fact(d['subject'],predicate,spec['initial'],day=event['day'],
                    turn=event.get('turn') or 0,source_event=event['id'],secrecy='public')
            return
        validate_resource_resolution(world, event, d)
        for predicate,value in d['after'].items():
            if value != d['before'][predicate]:
                graph.assert_fact(d['subject'],predicate,value,day=event['day'],
                    turn=event.get('turn') or 0,source_event=event['id'],secrecy='public')
        # Preserve the event's durable turn label in the projection.  The
        # adjudication payload deliberately remains unchanged, while player
        # read models can identify which saved action produced this result.
        world['systems'][self.name]['last_resolution']={**d, 'turn': event.get('turn')}
