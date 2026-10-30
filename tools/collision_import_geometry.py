"""Pure geometry proof for UE's unavoidable coincident-position face filter.

No filesystem or engine calls. Quantized original coordinates, oriented face
multiplicity, and original surface/flag indices are all retained in the proof.
Three-distinct-point collinear faces are not removed by this editor filter.
"""
from collections import Counter, defaultdict
import copy
import math

POLICY = 'ue58_static_mesh_exact_coincident_vertices_v07'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cross(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return [u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0]]


def oriented_key(points):
    """Cyclic order may change during build; reversing winding may not."""
    row = tuple(tuple(p) for p in points)
    return min(row, row[1:] + row[:1], row[2:] + row[:2])


def prove_coincident_vertex_pruning(vertices_quantized, source_triangles, geometry, *,
                                    mesh, index_mapping, surface_face_indices, triangle_flags):
    require(isinstance(vertices_quantized, (list, tuple)) and vertices_quantized,
            'Original quantized coordinates are required')
    require(all(isinstance(p, (list, tuple)) and len(p) == 3
                and all(type(v) is int and -32768 <= v <= 32767 for v in p)
                for p in vertices_quantized), 'Original coordinates must be int16 triples')
    require(isinstance(source_triangles, (list, tuple)) and source_triangles,
            'Original triangle indices are required')
    require(all(isinstance(face, (list, tuple)) and len(face) == 3
                and all(type(v) is int and 0 <= v < len(vertices_quantized) for v in face)
                for face in source_triangles), 'Invalid original triangle indices')
    require(isinstance(index_mapping, (list, tuple)) and all(type(v) is int for v in index_mapping)
            and list(index_mapping) in ([0, 1, 2], [0, 2, 1]), 'Unapproved collision winding mapping')
    count = len(source_triangles)
    require(isinstance(surface_face_indices, dict) and surface_face_indices,
            'Original surface face indices are required')
    surface_by_face = {}
    for surface, indices in surface_face_indices.items():
        require(isinstance(surface, str) and surface.isdecimal()
                and str(int(surface)) == surface and 0 <= int(surface) <= 255
                and isinstance(indices, (list, tuple)), 'Invalid original surface metadata')
        for index in indices:
            require(type(index) is int and 0 <= index < count and index not in surface_by_face,
                    'Original surface face indices overlap or are invalid')
            surface_by_face[index] = int(surface)
    require(len(surface_by_face) == count, 'Original surface face indices are incomplete')
    require(isinstance(triangle_flags, (list, tuple)) and len(triangle_flags) == count
            and all(type(v) is int and 0 <= v <= 255 for v in triangle_flags),
            'Original triangle flags are invalid')

    require(isinstance(geometry, dict) and geometry.get('available') is True
            and geometry.get('mesh') == mesh and isinstance(mesh, str) and bool(mesh)
            and geometry.get('flip_normals') is True, 'Engine collision geometry identity/flip flag differs')
    positions, faces = geometry.get('positions_cm'), geometry.get('triangles')
    require(isinstance(positions, list) and positions
            and all(isinstance(p, list) and len(p) == 3
                    and all(type(v) in (int, float) and math.isfinite(v) for v in p)
                    for p in positions), 'Engine collision coordinates must be finite numeric triples')
    require(isinstance(faces, list) and faces
            and all(isinstance(face, list) and len(face) == 3
                    and all(type(v) is int and 0 <= v < len(positions) for v in face)
                    for face in faces), 'Engine collision triangle indices are invalid')
    require({v for face in faces for v in face} == set(range(len(positions))),
            'Engine collision geometry contains unreferenced vertices')

    # int16 * 100/128 = int16 * 25/32 is exact in float32 at this range.
    # This comparison needs no positional epsilon and allows no new vertices.
    source_cm = [(x*25/32, -y*25/32, z*25/32) for x, y, z in vertices_quantized]
    source_points = set(source_cm)
    require(all(tuple(p) in source_points for p in positions),
            'Engine collision coordinates differ from original quantized vertices')
    expected, actual = defaultdict(list), defaultdict(list)
    omitted, distinct_collinear = [], []
    for source_index, face in enumerate(source_triangles):
        points = [vertices_quantized[v] for v in face]
        pairs = [[a, b] for a in range(3) for b in range(a + 1, 3)
                 if tuple(points[a]) == tuple(points[b])]
        integer_cross = cross(*points)
        if pairs:
            require(integer_cross == [0, 0, 0], 'Coincident face has unexpected nonzero integer area')
            omitted.append({'source_face_index': source_index, 'source_vertex_indices': list(face),
                'quantized_xyz_int16': [list(p) for p in points], 'coincident_corner_pairs': pairs,
                'integer_cross_product': integer_cross, 'surface_id': surface_by_face[source_index],
                'triangle_flag': triangle_flags[source_index]})
        else:
            if integer_cross == [0, 0, 0]:
                distinct_collinear.append(source_index)
            key = oriented_key([source_cm[face[k]] for k in index_mapping])
            expected[key].append(source_index)
    require(omitted, 'No original coincident-position faces authorize an engine triangle adaptation')
    require(len(faces) == count - len(omitted), 'Engine triangle count differs from the exact coincident-position filter')
    for engine_index, face in enumerate(faces):
        actual[oriented_key([positions[v] for v in face])].append(engine_index)
    require(Counter({key: len(rows) for key, rows in actual.items()})
            == Counter({key: len(rows) for key, rows in expected.items()}),
            'Engine face geometry, winding or duplicate multiplicity differs from original survivors')
    remap = [[] for _ in source_triangles]
    for key, source_indices in expected.items():
        for source_index in source_indices:
            remap[source_index] = actual[key][:]
    return {'policy': POLICY, 'source_triangle_count': count, 'observed_triangle_count': len(faces),
        'index_mapping': list(index_mapping),
        'omitted_source_face_indices': [row['source_face_index'] for row in omitted],
        'omitted_source_faces': omitted,
        'retained_distinct_collinear_source_face_indices': distinct_collinear,
        'source_face_to_engine_face_candidates': remap,
        'correspondence_policy': 'Oriented coordinate multiset; cyclic rotations allowed; duplicate buckets retain multiplicity without inventing individual face identity.',
        'original_surface_face_indices': copy.deepcopy(surface_face_indices),
        'original_triangle_flags': list(triangle_flags),
        'surface_metadata_policy': 'Surface IDs and flags retain original source-face indices; candidates are geometry correspondence, not UE material/friction/audio assignments.',
        'coordinate_policy': 'COL int16 XYZ /128 -> UE(X,-Y,Z) centimetres; exact float32-representable integer*25/32; no positional tolerance.',
        'geometry': copy.deepcopy(geometry),
        'runtime_physics_validation': 'pending; geometry is collision input before Chaos cooking'}
