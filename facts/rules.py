"""Scenario-defined structured-state constraints, independent of story genre."""
import math


def check_value(entity, predicate, value, prior=None):
    if entity is None:
        return None
    rule = entity.attrs.get('fact_rules', {}).get(predicate)
    if rule is None:
        return None
    if not isinstance(rule, dict):
        return f'{predicate}: fact_rules entry must be an object'
    kind = rule.get('type')
    matches = {
        'integer': isinstance(value, int) and not isinstance(value, bool),
        'number': isinstance(value, (int, float)) and not isinstance(value, bool),
        'string': isinstance(value, str),
        'boolean': isinstance(value, bool),
    }
    if kind is not None and not matches.get(kind, False):
        return f'{predicate} must be {kind}'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return f'{predicate} must be finite'
        if 'min' in rule and value < rule['min']:
            return f'{predicate} must be >= {rule["min"]}'
        if 'max' in rule and value > rule['max']:
            return f'{predicate} must be <= {rule["max"]}'
    if 'enum' in rule and value not in rule['enum']:
        return f'{predicate} must be one of {rule["enum"]}'
    if rule.get('immutable') and prior is not None and value != prior.value:
        return f'{predicate} is immutable; keep the established value'
    return None
