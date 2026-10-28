"""Pure identity checks shared by new and resumed world imports."""
import re


def material_index(model_key, material_count, *names):
    """Resolve original slot names or this model's already recovered material."""
    patterns = (r'REA_Mat_(\d+)', r'M_' + re.escape(model_key) + r'_(\d+)')
    if not names or not any(re.fullmatch(pattern, str(names[0])) for pattern in patterns):
        raise ValueError(f'Assigned material is not an original or recovered material for {model_key}: {names}')
    indices = set()
    for name in names:
        for pattern in patterns:
            match = re.fullmatch(pattern, str(name))
            if match:
                indices.add(int(match[1]))
    if len(indices) != 1:
        raise ValueError(f'Original material index missing or conflicting for {model_key}: {names}')
    index = indices.pop()
    if not 0 <= index < material_count:
        raise ValueError(f'Original material index outside source catalog: {model_key}/{index}')
    return index


def verify_material_path(model_key, index, path, imported_prefix):
    """Slot names alone cannot prove that an assigned material is ours."""
    original_name = f'REA_Mat_{index:04d}'
    recovered_name = f'M_{model_key}_{index:04d}'
    recovered_path = f'/Game/RecoveredMaterials/World/{recovered_name}.{recovered_name}'
    if path == recovered_path:
        return
    if path.startswith(imported_prefix) and path.rsplit('/', 1)[-1] == original_name + '.' + original_name:
        return
    raise ValueError('Assigned material belongs to a different source/model: ' + path)


def verify_checkpoint(model, checkpoint):
    key = model['model_key']
    if (checkpoint.get('status') != 'imported' or checkpoint.get('model_key') != key or
            checkpoint.get('source_sha256') != model.get('output_sha256') or
            not checkpoint.get('meshes')):
        raise ValueError('Existing world checkpoint does not match exported source: ' + key)
    prefix = '/Game/Original/World/Models/' + key + '/'
    if any(not mesh.get('path', '').startswith(prefix) for mesh in checkpoint['meshes']):
        raise ValueError('Existing world checkpoint references another model: ' + key)
