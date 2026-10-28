"""Check saved UE import evidence against original GLB bounds and existing packages."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def mesh_bounds(mesh):
    if 'bounds_cm' in mesh:
        return mesh['bounds_cm']
    # Older import checkpoints retained the engine's struct representation.
    values = []
    for field in ('origin', 'box_extent'):
        match = re.search(field + r': \{x: ([^,]+), y: ([^,]+), z: ([^}]+)\}', mesh['bounds'])
        if not match:
            raise ValueError('Older checkpoint has no readable Unreal bounds')
        values.append([float(value) for value in match.groups()])
    origin, extent = values
    return {'min': [origin[i] - extent[i] for i in range(3)],
            'max': [origin[i] + extent[i] for i in range(3)]}


def validate(catalog):
    models = {model['model_key']: model for model in catalog['models']}
    results, errors = [], []
    for path in sorted((ROOT/'local/world-import/models').glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            source = models[path.stem]
            if record['status'] != 'imported' or record['source_sha256'] != source.get('output_sha256'):
                raise ValueError('Checkpoint does not match current exported source')
            if not record['meshes']:
                raise ValueError('Checkpoint has no meshes')
            bounds = []
            for mesh in record['meshes']:
                package = mesh['path'].split('.')[0]
                if not package.startswith('/Game/Original/World/Models/' + path.stem + '/'):
                    raise ValueError('Mesh belongs to a different model folder')
                asset_file = ROOT/'unreal/Content'/Path(package.removeprefix('/Game/') + '.uasset')
                if not asset_file.is_file():
                    raise ValueError('Saved Unreal mesh package is missing: ' + package)
                bounds.append(mesh_bounds(mesh))
            combined = {side: [function(box[side][axis] for box in bounds) for axis in range(3)]
                        for side, function in [('min', min), ('max', max)]}
            error = max(abs(combined[side][axis] - source['bounds_entity_unreal_cm'][side][axis])
                        for side in ('min', 'max') for axis in range(3))
            if not 0 <= error <= .1:
                raise ValueError(f'Original/imported bounds differ by {error} cm')
            results.append({'model_key': path.stem, 'meshes': len(bounds), 'max_bounds_error_cm': error,
                            'checkpoint_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append({'model_key': path.stem, 'error': str(exc)})
    return {'passed': not errors, 'models_checked': len(results),
            'exported_models': sum(model['status'] == 'exported' for model in models.values()),
            'max_bounds_error_cm': max((item['max_bounds_error_cm'] for item in results), default=None),
            'models': results, 'errors': errors,
            'scope': 'Saved import evidence, coordinate-frame bounds and package existence; not a fresh Unreal runtime test.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'evidence/world-export.json')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = validate(json.loads(args.catalog.read_text(encoding='utf-8')))
    result['catalog_sha256'] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != 'models'}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
