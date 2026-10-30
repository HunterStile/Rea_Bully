"""Additional distinct pedestrians using already proven original CAT families.

The allowlist selects identities for exploration, not an original spawn schedule.
Registration is process-local; historical exporters and evidence stay unchanged.
"""
from catalog_npc_locomotion import PED_PROFILES, PROFILES
from bully_agr import FormatError

ADDITIONAL = {7: 'nerd', 10: 'nerd', 49: 'prefect', 51: 'prefect', 52: 'prefect',
              66: 'student_male', 69: 'student_male', 70: 'student_male',
              134: 'student_male', 139: 'student_male', 142: 'student_male',
              102: 'bully', 145: 'bully', 146: 'bully', 147: 'bully'}


def register(peds):
    by_id = {ped['id']: ped for ped in peds}
    if len(by_id) != len(peds):
        raise FormatError('Repeated original pedestrian ID')
    for ped_id, family in ADDITIONAL.items():
        ped = by_id[ped_id]
        expected = PROFILES[family]
        if (ped['female'] or ped['action_root'] != '/Global/'+expected['action']
                or ped['action_file'].replace('\\', '/').lower() != ('Act/Anim/'+expected['action']+'.act').lower()):
            raise FormatError('Additional identity does not use the proven original action tree')
    PED_PROFILES.update(ADDITIONAL)
    # These pairs are not inferred materials: export_npc checks every mapped
    # slot exists in this pedestrian's exact original texture dictionary.
    import export_npc
    for ped_id in ADDITIONAL:
        export_npc.SELECTION[ped_id] = [tuple(c[:3]) for c in PROFILES[ADDITIONAL[ped_id]]['clips']]
    for ped_id, number in ((102, '01'), (145, '04'), (146, '05'), (147, '06')):
        export_npc.WINTER_TEXTURE_ALIASES[ped_id] = {
            f'bully{number}_md_{slot}.tga': f'bully{number}_md_w_{slot}.tga' for slot in ('d', 's', 'n')}
    # Exact three-slot names inspected in each original NIF and the NFT named
    # by default.ide. In particular Greekboy's NIF uses generic head labels;
    # the declared dictionary contains only his three matching semantic slots.
    for ped_id, source, destination in (
            (69, 'gn_hispanicboy_md', 'gn_hispanicboy_md_w'),
            (70, 'ss_generic_head_md', 'gn_greekboy_md_w'),
            (102, 'bully01_md', 'bully01_01_md'),
            (139, 'whiteboy_md', 'gn_whiteboy_w_md')):
        export_npc.WINTER_TEXTURE_ALIASES[ped_id] = {
            f'{source}_{slot}.tga': f'{destination}_{slot}.tga' for slot in ('d', 's', 'n')}
    return by_id
