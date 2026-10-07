"""Normalize explicit held-by visibility without widening malformed metadata."""


def held_by_visibility(deltas):
    """Return relation attrs for the two supported event metadata shapes.

    Omission retains the legacy relation visibility convention. Explicit unknown,
    malformed, or conflicting metadata takes the restrictive interpretation.
    This does not change the physical holder or rewrite historical events.
    """
    values = []
    if 'visibility' in deltas:
        values.append(deltas['visibility'])
    if 'attrs' in deltas:
        attrs = deltas['attrs']
        if not isinstance(attrs, dict):
            return {'visibility': 'hidden'}
        if 'visibility' in attrs:
            values.append(attrs['visibility'])
    if not values:
        return {}
    if any(not isinstance(value, str) or value not in {'public', 'hidden', 'secret'}
           for value in values):
        return {'visibility': 'hidden'}
    if 'secret' in values:
        return {'visibility': 'secret'}
    if 'hidden' in values:
        return {'visibility': 'hidden'}
    return {'visibility': 'public'}
