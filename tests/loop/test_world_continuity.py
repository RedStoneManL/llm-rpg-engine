"""Long-running threads advance by world time and show evidence at their venue."""
import pytest

from kernel.events import kernel_event, open_store
from kernel.projection import project
from loop.lore import create_lore_line, run_lore
from loop.lore_disclosure import station_push_fragment
from tests.loop.test_dormancy import _base_world, _reg, _skeleton, _move_ev, _char_ev


@pytest.fixture
def campaign(tmp_path):
    registry = _reg()
    store = open_store(tmp_path / 'events.db', tmp_path / 'events.jsonl',
                       allowed_types=registry.event_types())
    try:
        yield registry, store
    finally:
        store.close()


def world_at(reg, store, day):
    world = project(reg, store.iter_events())
    world['meta']['day'] = day
    return world


def advances(events):
    return [(e['deltas']['id'], e['day'], e['deltas']['stage_idx']) for e in events if e['type'] == 'lore_advanced']


def test_each_remote_line_advances_on_its_own_days_without_rerolling_during_conversation(campaign):
    r, s = campaign
    _base_world(s, r, hero_location='town_a_venue')
    for lid, complexity in [('bridge', 'simple'), ('clinic', 'medium')]:
        create_lore_line(s, _skeleton(lid, complexity, 'town_b'), day=1, scene='s1', turn=1, lifespan_days=20)
    w = world_at(r, s, 3)
    result = advances(run_lore(r, s, w, previous_day=1, protagonist='hero'))
    assert sorted(result) == sorted([('bridge', 2, 0), ('clinic', 2, 0), ('bridge', 3, 1), ('clinic', 3, 1)])
    assert not run_lore(r, s, w, previous_day=1, protagonist='hero')  # stale snapshot replay
    w = world_at(r, s, 3)
    assert not run_lore(r, s, w, previous_day=3, protagonist='hero')
    assert not station_push_fragment(r, w, {'protagonist': 'hero'})


def test_return_after_expiry_preserves_dated_local_clue_without_hidden_solution(campaign):
    r, s = campaign
    _base_world(s, r, hero_location='town_a_venue')
    sk = {**_skeleton('wheel', 'simple', 'town_b'), 'secret': '幕后主使名字',
          'stages': [{'hint': '水车拆掉旧叶片'}, {'hint': '水车装上铜轴'}]}
    create_lore_line(s, sk, day=1, scene='s1', turn=1, lifespan_days=3)
    result = run_lore(r, s, world_at(r, s, 11), previous_day=1, protagonist='hero')
    assert advances(result) == [('wheel', 2, 0), ('wheel', 3, 1)]
    assert [e['type'] for e in result][-1] == 'quest_expired'
    w = world_at(r, s, 11)
    assert not station_push_fragment(r, w, {'protagonist': 'hero'})
    s.append(kernel_event('entity_moved', day=11, scene='s1', turn=12, summary='回访', deltas={'who': 'hero', 'to': 'town_b_venue'}))
    w = project(r, s.iter_events())
    text = station_push_fragment(r, w, {'protagonist': 'hero'})
    assert '第3天' in text and '水车装上铜轴' in text and '幕后主使名字' not in text
    assert 'wheel' not in text and w['systems']['lore']['lines']['wheel']['state'] == '了结'


def test_resequenced_line_gets_a_fresh_stage_zero(campaign):
    r, s = campaign
    _base_world(s, r, hero_location='town_b_venue')
    create_lore_line(s, _skeleton('bridge', 'simple', 'town_b'), day=1, scene='s1', turn=1)
    assert advances(run_lore(r, s, world_at(r, s, 1))) == [('bridge', 1, 0)]
    for kind, payload in [('quest_surfaced', {}), ('quest_demoted', {'new_stages': [{'hint': '桥栏换新'}]})]:
        s.append(kernel_event(kind, day=1, scene='s1', turn=3, summary=kind, deltas={'id': 'bridge', **payload}))
    result = run_lore(r, s, world_at(r, s, 1))
    assert advances(result) == [('bridge', 1, 0)]
    assert result[0]['deltas']['hint'] == '桥栏换新'
    assert project(r, s.iter_events())['systems']['lore']['lines']['bridge']['clues_dropped'] == ['h0', '桥栏换新']


def test_scheduler_uses_active_player_not_first_tracked_npc(campaign):
    r, s = campaign
    s.append(_char_ev('npc'))
    _base_world(s, r, hero_location='town_a_venue')
    s.append(_move_ev('npc', 'town_b_venue'))
    for lid, location in [('local', 'town_a'), ('far', 'town_b')]:
        create_lore_line(s, _skeleton(lid, 'simple', location), day=1, scene='s1', turn=1)
    assert advances(run_lore(r, s, world_at(r, s, 1), previous_day=1, protagonist='hero')) == [('local', 1, 0)]


def test_background_threshold_is_split_wait_invariant_independent_of_row_order(tmp_path):
    def run(days, ids):
        reg = _reg()
        store = open_store(tmp_path / (ids[0] + ".db"), tmp_path / (ids[0] + ".jsonl"),
                           allowed_types=reg.event_types())
        try:
            _base_world(store, reg, hero_location='town_a_venue')
            for lid in ids:
                sk = _skeleton(lid, 'medium', 'town_b', threshold=35)
                sk['stages'] = [{'hint': f'{lid}:{i}'} for i in range(40)]
                create_lore_line(store, sk, day=1, scene='s1', turn=1, lifespan_days=110)
            history = []
            start = 1
            for end in days:
                history.extend(advances(run_lore(reg, store, world_at(reg, store, end),
                                                 previous_day=start, protagonist='hero')))
                start = end
            return sorted(history)
        finally:
            store.close()
    frequent = run(range(2, 102), ['boats', 'clinic'])
    jumped = run([101], ['clinic', 'boats'])
    assert frequent == jumped
    for lid in ('boats', 'clinic'):
        dates = [day for name, day, _ in frequent if name == lid]
        assert len(dates) == len(set(dates)) == 35


def test_personal_arc_does_not_end_from_background_rescue(campaign, monkeypatch):
    reg, store = campaign
    _base_world(store, reg, hero_location='town_a_venue')
    create_lore_line(store, _skeleton('pthread_home', 'complex', 'town_b'),
                     day=1, scene='s1', turn=1)
    monkeypatch.setattr('loop.endgame.roll_world_rescue', lambda *args: True)
    for _ in range(2):
        run_lore(reg, store, world_at(reg, store, 1), protagonist='hero')
    line = project(reg, store.iter_events())['systems']['lore']['lines']['pthread_home']
    assert line['stage_idx'] == 1 and line['state'] == '暗'


def test_an_explicitly_accepted_investigation_does_not_vanish_while_elsewhere(campaign):
    from loop.turn import _run_demote_on_leave
    reg, store = campaign
    _base_world(store, reg, hero_location='town_b_venue')
    store.append(kernel_event('quest_opened', day=1, scene='s1', turn=1, summary='接取',
                              deltas={'id': 'old_ledger', 'summary': '查清北塔旧账', 'anchor': 'town_a'}))
    _run_demote_on_leave(reg, store, world_at(reg, store, 17), 'hero', None,
                         turn_num=80, day=17, scene='town_b_venue')
    line = project(reg, store.iter_events())['systems']['lore']['lines']['old_ledger']
    assert line['state'] == '明'
