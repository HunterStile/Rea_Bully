"""Source-bound civilian, greaser and dropout clip choices for outdoor previews."""
from bully_agr import FormatError
import catalog_npc_locomotion as cat

_ORIGINAL_PROVE_PROFILE = cat.prove_profile

TOWN_IDS = {76: 'civilian_male', 77: 'civilian_male', 100: 'civilian_male', 101: 'civilian_male',
            21: 'greaser', 26: 'greaser', 42: 'dropout', 43: 'dropout'}
CAT_HASHES = {
    'CV_Male_A': '1fb8b33d37dbcdd6b6e3859b76a9e95b5b1dbd325ce4137e00588fbd9db324de',
    'G_Striker_A': '002ef8128f9f9da1aebf5e3a582ce9a5f4735effa54cfb9543f61c2823656403',
    'Walk_Greasers': '078b878c663850aba9cc0db93f22f86da5725b32bc8121d8e26288925c6ddcd6',
    'DO_Striker_A': '19d11bf534b617e13ce46c8205bc4bb3aae0b7b136ef587850dbcd381f64b26b',
    'Walk_Dout': '73475cafc0563212c42bfcbd2f14a9fb83ead4f21d6c5b2b1d2d59357017ebef'}
PROFILES = {
    'civilian_male': {'action': 'CV_Male_A', 'walk': 'Walk_Default', 'include_node': 305, 'clips': [
        ('CV_Male', 'CV_MALE\\IDLE', 'idle', 'CV_Male_A', 275, 874),
        ('C_Player', 'C_PLAYER\\WALK', 'walk', 'Walk_Default', 797, 2004),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Default', 694, 1873),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Default', 612, 2286),
        ('IDLE_CIVM_A', 'CV_MALE\\GA_IDLE_A_01', 'ambient_idle', 'Walk_Default', 3450, 1558)]},
    'greaser': {'action': 'G_Striker_A', 'walk': 'Walk_Greasers', 'include_node': 560, 'clips': [
        ('F_Greas', 'F_GREAS\\IDLE', 'idle', 'G_Striker_A', 530, 715),
        ('F_Greas', 'F_GREAS\\WALK', 'walk', 'Walk_Greasers', 423, 1203),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Greasers', 402, 554),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Greasers', 381, 1340),
        ('IDLE_GREAS_A', 'F_GREAS\\GR_IDLE_A', 'ambient_idle', 'Walk_Greasers', 700, 434)]},
    'dropout': {'action': 'DO_Striker_A', 'walk': 'Walk_Dout', 'include_node': 186, 'clips': [
        ('F_Douts', 'F_DOUTS\\IDLE', 'idle', 'DO_Striker_A', 156, 375),
        ('F_Douts', 'F_DOUTS\\WALK', 'walk', 'Walk_Dout', 288, 461),
        ('C_Player', 'C_PLAYER\\RUN', 'run', 'Walk_Dout', 267, 493),
        ('C_Player', 'C_PLAYER\\RUNFAST', 'sprint', 'Walk_Dout', 246, 641),
        ('IDLE_DOUT_A', 'F_DOUTS\\DO_IDLE_A_01', 'ambient_idle', 'Walk_Dout', 516, 700)]}}


def prove_profile(name, hxd, cats):
    if name != 'civilian_male':
        return _ORIGINAL_PROVE_PROFILE(name, hxd, cats)
    duplicates = [r for r in hxd['animations'] if r['name'] == 'CV_MALE\\IDLE']
    if (len(duplicates) != 2 or
            {hxd['groups'][r['group']]['name'] for r in duplicates} != {'CV_Male', 'F_Adult'}):
        raise FormatError('Civilian idle ambiguity differs from the inspected original source')
    selected = {**hxd, 'animations': [r for r in hxd['animations'] if
                r['name'] != 'CV_MALE\\IDLE' or hxd['groups'][r['group']]['name'] == 'CV_Male']}
    proof = _ORIGINAL_PROVE_PROFILE(name, selected, cats)
    proof['original_group_resolution'] = {
        'native_precedence_recovered': False, 'selected_group': 'CV_Male',
        'policy': 'Project choice of the first original animation group declared by default.ide; native lookup precedence is unresolved',
        'same_hash_alternatives': [dict(group=hxd['groups'][r['group']]['name'], record=r) for r in duplicates]}
    return proof


def register(peds):
    import export_npc
    from world_npc_profiles import register as register_school
    by_id = register_school(peds)
    for ped_id, family in TOWN_IDS.items():
        ped, profile = by_id[ped_id], PROFILES[family]
        if (ped['female'] or ped['action_root'] != '/Global/'+profile['action'] or
                ped['action_file'].replace('\\', '/').lower() != ('Act/Anim/'+profile['action']+'.act').lower()):
            raise FormatError('Town identity does not use the selected original CAT action tree')
        if family == 'civilian_male' and ped['animation_groups'][:2] != ['CV_Male', 'F_Adult']:
            raise FormatError('Civilian original primary/secondary animation groups changed')
    cat.CAT_SHA256.update(CAT_HASHES)
    cat.PROFILES.update(PROFILES)
    cat.PED_PROFILES.update(TOWN_IDS)
    cat.prove_profile = prove_profile
    for ped_id, family in TOWN_IDS.items():
        export_npc.SELECTION[ped_id] = [tuple(clip[:3]) for clip in PROFILES[family]['clips']]
    # Exact D/S/N slot names inspected in each model and its IDE-selected NFT.
    for ped_id, requested, available in (
            (76, 'to_business1_md', 'to_business_01_md'),
            (77, 'to_business2_md', 'to_business_02_md'),
            (100, 'to_richm_md', 'to_richm1_w_md'),
            (21, 'gr2nd_peanut_md', 'gr2nd_peanutwinter_md'),
            (26, 'grh3_lucky_md', 'grh3_lucky_md_w'),
            (42, 'doh1a_otto_md', 'doh1a_otto_md_w')):
        export_npc.WINTER_TEXTURE_ALIASES[ped_id] = {
            f'{requested}_{slot}.tga': f'{available}_{slot}.tga' for slot in ('d', 's', 'n')}
    return by_id
