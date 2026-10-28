"""Recover source ONE/ONE materials using one shared shader and exact imported textures."""
import hashlib
import json
import re
import unreal
from unreal_patch_school_materials import _expression, _connect, _constant

lib = unreal.MaterialEditingLibrary
ROOT = '/Game/RecoveredMaterials/World'
_instancing_cache = {}
_instancing_active = set()
_instancing_changes = {}


def _parameters(material):
    """Effective values must survive copying a parent and assigning it to an instance."""
    if not isinstance(material, unreal.MaterialInstanceConstant):
        return None
    textures = {}
    for name in lib.get_texture_parameter_names(material):
        value = lib.get_material_instance_texture_parameter_value(material, name)
        textures[str(name)] = value.get_path_name() if value else None
    scalars = {str(name): lib.get_material_instance_scalar_parameter_value(material, name)
               for name in lib.get_scalar_parameter_names(material)}
    vectors = {}
    for name in lib.get_vector_parameter_names(material):
        value = lib.get_material_instance_vector_parameter_value(material, name)
        vectors[str(name)] = [value.r, value.g, value.b, value.a]
    switches = {str(name): lib.get_material_instance_static_switch_parameter_value(material, name)
                for name in lib.get_static_switch_parameter_names(material)}
    return {'textures': textures, 'scalars': scalars, 'vectors': vectors, 'switches': switches}


def ensure_instancing(original):
    """Enable HISM shaders while saving only project assets; preserve all parameters.

    Stock Interchange parents live in an engine plugin. Duplicate their entire
    parent chain before enabling usage; never mark or save the plugin originals.
    The imported /Game material instance stays at its existing checkpoint path.
    """
    if not isinstance(original, (unreal.Material, unreal.MaterialInstanceConstant)):
        raise RuntimeError('World material is not a supported material asset')
    original_path = original.get_path_name()
    if original_path in _instancing_cache:
        return unreal.load_asset(_instancing_cache[original_path])
    if original_path in _instancing_active:
        raise RuntimeError('Material parent cycle: ' + original_path)
    _instancing_active.add(original_path)
    usage = unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES
    parameters_before = _parameters(original)
    material = original
    cloned = False
    if not original_path.startswith('/Game/'):
        name = re.sub(r'[^A-Za-z0-9_]', '_', original.get_name())
        suffix = hashlib.sha256(original_path.encode()).hexdigest()[:12]
        destination = ROOT + '/Instancing/' + name + '_' + suffix
        if unreal.EditorAssetLibrary.does_asset_exist(destination):
            material = unreal.load_asset(destination)
            if material.get_class() != original.get_class() or unreal.EditorAssetLibrary.get_metadata_tag(material, 'REAInstancingSource') != original_path:
                raise RuntimeError('Preserve unrelated local material: ' + destination)
        else:
            material = unreal.EditorAssetLibrary.duplicate_loaded_asset(original, destination)
            if material is None:
                raise RuntimeError('Could not copy external material: ' + original_path)
            unreal.EditorAssetLibrary.set_metadata_tag(material, 'REAInstancingSource', original_path)
        cloned = True
    if not material.get_path_name().startswith('/Game/'):
        raise RuntimeError('Instancing patch must not modify an external material')
    changed = cloned
    had_usage = lib.has_material_usage(material, usage)
    parent_before = parent_after = None
    if isinstance(material, unreal.MaterialInstanceConstant):
        parent = material.get_editor_property('parent')
        if parent is None:
            raise RuntimeError('Material instance has no parent: ' + original_path)
        parent_before = parent.get_path_name()
        local_parent = ensure_instancing(parent)
        parent_after = local_parent.get_path_name()
        if parent_after != parent_before:
            lib.set_material_instance_parent(material, local_parent)
            changed = True
        if not lib.has_material_usage(material, usage):
            # UE 5.8 permits an imported instance to override usage flags itself.
            lib.set_material_usage_override(material, usage, True, True)
            changed = True
        if changed:
            lib.update_material_instance(material)
    elif not had_usage:
        lib.set_base_material_usage(material, usage, True)
        changed = True
    if not lib.has_material_usage(material, usage):
        raise RuntimeError('InstancedStaticMeshes usage was not enabled: ' + original_path)
    parameters_after = _parameters(material)
    if parameters_after != parameters_before:
        raise RuntimeError('Material parameters changed while copying parent: ' + original_path)
    if changed and not unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False):
        raise RuntimeError('Could not save instancing material: ' + material.get_path_name())
    _instancing_changes[original_path] = {'source': original_path, 'material': material.get_path_name(),
        'cloned_external': cloned, 'saved': changed, 'usage_before': had_usage, 'usage_after': True,
        'parent_before': parent_before, 'parent_after': parent_after, 'parameters_preserved': True,
        'texture_parameters': parameters_after['textures'] if parameters_after else {}}
    _instancing_cache[original_path] = material.get_path_name()
    _instancing_active.remove(original_path)
    return material


def instancing_changes():
    return list(_instancing_changes.values())


def master():
    path = ROOT + '/M_SourceAdditive'
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return ensure_instancing(unreal.load_asset(path))
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        'M_SourceAdditive', ROOT, unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    sample = _expression(material, unreal.MaterialExpressionTextureSampleParameter2D)
    sample.set_editor_property('parameter_name', 'OriginalTexture')
    # A valid default is necessary for shader compilation before child instances supply their textures.
    sample.set_editor_property('texture', unreal.load_asset('/Engine/EngineResources/WhiteSquareTexture'))
    vertex = _expression(material, unreal.MaterialExpressionVertexColor)
    color = _expression(material, unreal.MaterialExpressionMultiply)
    _connect(sample, 'RGB', color, 'A')
    _connect(vertex, '', color, 'B')
    lib.connect_material_property(color, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    threshold = _expression(material, unreal.MaterialExpressionScalarParameter)
    threshold.set_editor_property('parameter_name', 'AlphaThreshold')
    threshold.set_editor_property('default_value', -1.0)
    gate = _expression(material, unreal.MaterialExpressionIf)
    gate.set_editor_property('equals_threshold', 0.0)
    _connect(sample, 'A', gate, 'A')
    _connect(threshold, '', gate, 'B')
    _connect(_constant(material, 1), '', gate, 'A > B')
    _connect(_constant(material, 0), '', gate, 'A == B')
    _connect(_constant(material, 0), '', gate, 'A < B')
    lib.connect_material_property(gate, '', unreal.MaterialProperty.MP_OPACITY)
    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return ensure_instancing(material)


def recover(original, source, model_key, index):
    state = source.get('extras', {}).get('REA_originalAlpha')
    if not state or not (state['flags'] & 1) or (state['sourceBlend'], state['destinationBlend']) != (0, 0):
        return ensure_instancing(original), False
    if state['testEnabled'] and state['testFunction'] != 4:
        raise RuntimeError('Unsupported source additive alpha-test comparison')
    name = f'M_{model_key}_{index:04d}'
    path = ROOT + '/' + name
    signature = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        material = unreal.load_asset(path)
        if unreal.EditorAssetLibrary.get_metadata_tag(material, 'REAWorldMaterial') != signature:
            raise RuntimeError('Recovered material source changed')
        # Its texture parameter is OriginalTexture, whereas the incoming glTF
        # instance uses BaseColorTexture. A resumed slot already uses this asset.
        return ensure_instancing(material), True
    names = lib.get_texture_parameter_names(original)
    base = [name for name in names if 'basecolor' in str(name).lower() and 'texture' in str(name).lower()]
    if len(base) != 1:
        raise RuntimeError(f'Original additive base texture ambiguous: {original.get_path_name()} {names}')
    texture = lib.get_material_instance_texture_parameter_value(original, base[0])
    if not isinstance(texture, unreal.Texture2D):
        raise RuntimeError('Original additive base texture is not a Texture2D')
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, ROOT, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    lib.set_material_instance_parent(material, master())
    lib.set_material_instance_texture_parameter_value(material, 'OriginalTexture', texture)
    lib.set_material_instance_scalar_parameter_value(material, 'AlphaThreshold',
        state['thresholdByte']/255 if state['testEnabled'] else -1.0)
    unreal.EditorAssetLibrary.set_metadata_tag(material, 'REAWorldMaterial', signature)
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return ensure_instancing(material), True
