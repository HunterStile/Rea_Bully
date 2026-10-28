"""Preflight source-bound scene inputs before opening or changing generated maps."""


def require_ambient_npcs(ambient_plan, imported):
    if not imported or imported.get('status') != 'npc_assets_verified':
        raise ValueError('Ambient placement requires a verified NPC import report')
    npcs = {npc['ped_id']: npc for npc in imported['npcs']}
    if len(npcs) != len(imported['npcs']):
        raise ValueError('Duplicate imported NPC identity')
    requested = [placement['ped_id'] for placement in ambient_plan['placements']]
    if len(requested) != len(set(requested)):
        raise ValueError('The scene would duplicate a named pedestrian')
    missing = set(requested)-set(npcs)
    if missing:
        raise ValueError('Ambient NPCs have not been imported: ' + ', '.join(map(str, sorted(missing))))
    return npcs
