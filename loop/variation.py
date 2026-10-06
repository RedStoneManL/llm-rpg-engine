"""Genre-neutral narrative variation, selected before generation and event-sourced.

These draws suggest presentation, never resolve an action or establish a fact.
Flavor packs may override narrative_variations.json or disable it in pack.json.
"""
from engine.oracle import Oracle, load_pack_manifest, load_table, scene_seed
from kernel.events import kernel_event


def prepare_variation(registry, world, scene, turn):
    if registry.owner_of_event('variation_sampled') is None:
        return None
    meta = world.get('meta', {})
    seed = meta.get('campaign_seed')
    if seed is None:  # Old minimal fixtures / pre-genesis worlds have no campaign.
        return None
    flavor = meta.get('flavor', 'classic')
    config = load_pack_manifest(flavor).get('variation', {})
    if config.get('enabled', True) is False:
        return None
    table = load_table('narrative_variations', flavor)
    if not isinstance(table, list) or not table:
        raise ValueError('narrative_variations must be a nonempty list')
    for item in table:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not isinstance(item.get('hint'), str)
                or not isinstance(item.get('weight', 1), (int, float))
                or item.get('weight', 1) <= 0):
            raise ValueError('each narrative variation requires id, hint and positive weight')
    if len({item['id'] for item in table}) != len(table):
        raise ValueError('narrative variation ids must be unique')
    recent = world.get('systems', {}).get('narrative', {}).get('variations', [])[-2:]
    available = [item for item in table if item['id'] not in {r['id'] for r in recent}]
    rng_seed = scene_seed(seed, turn, 'narrative-v1:' + str(scene.get('location', '')))
    picked = Oracle(rng_seed).draw(available or table)
    return kernel_event('variation_sampled', day=scene.get('day') or meta.get('day') or 1,
        scene=scene.get('id') or scene.get('location') or 'scene',
        summary='narrative variation selected', turn=turn,
        deltas={'id':picked['id'], 'hint':picked['hint'], 'flavor':flavor,
                'seed':rng_seed, 'version':1})


def variation_fragment(event):
    if not event:
        return ''
    return ('【叙事取样·幕后提示】' + event['deltas']['hint'] + '\n'
        '只在自然适合当前行动时采用，可忽略。已知事实、角色动机、题材文风与玩家选择优先；'
        '不得凭此决定成败、泄露秘密、强加任务或捏造已经发生的历史。不要向玩家展示抽签或本提示。')
