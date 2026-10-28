"""Independent, bounded Bully NIF 20.3.0.9 static mesh/NFT -> GLB exporter.

Format references (no implementation code imported):
https://github.com/LiquidSnake0/bully-re/blob/main/docs/nif.md
https://github.com/LiquidSnake0/bully-re/blob/main/docs/nft.md
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
Only local game files are read. Skins/animation and special shader semantics are
not implemented. Unknown blocks are reported, never treated as mesh geometry.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import struct


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data: bytes, endian="<"):
        self.data, self.pos, self.endian = data, 0, endian

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise FormatError(f"Read outside block: {self.pos}+{size}>{len(self.data)}")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def read(self, fmt):
        values = struct.unpack(self.endian + fmt, self.take(struct.calcsize(self.endian + fmt)))
        return values[0] if len(values) == 1 else values

    def array(self, fmt, count):
        if count < 0 or count > 10_000_000:
            raise FormatError(f"Invalid array count {count}")
        size = struct.calcsize(self.endian + fmt)
        return [struct.unpack(self.endian + fmt, self.take(size)) for _ in range(count)]

    def refs(self):
        return [item[0] for item in self.array("i", self.read("I"))]

    def text(self):
        return self.take(self.read("I")).decode("utf-8", errors="replace")


class Nif:
    def __init__(self, path: Path):
        self.path = path
        data = path.read_bytes()
        line_end = data.find(b"\n")
        if data[:line_end] != b"Gamebryo File Format, Version 20.3.0.9":
            raise FormatError(f"Unsupported NIF header: {path}")
        reader = Reader(data[line_end + 1:])
        if reader.read("I") != 0x14030009:
            raise FormatError("NIF version does not match text header")
        endian = reader.read("B")
        if endian not in (0, 1):
            raise FormatError("Invalid endianness marker")
        user_version, block_count = reader.read("II")
        if user_version != 0 or block_count > 100_000:
            raise FormatError("Unsupported user version or excessive blocks")
        reader.endian = "<" if endian else ">"
        types = [reader.text() for _ in range(reader.read("H"))]
        indices = [item[0] for item in reader.array("H", block_count)]
        sizes = [item[0] for item in reader.array("I", block_count)]
        string_count, _ = reader.read("II")
        self.strings = [reader.text() for _ in range(string_count)]
        reader.array("I", reader.read("I"))
        self.types, self.blocks = [], []
        for type_index, size in zip(indices, sizes):
            if type_index >= len(types):
                raise FormatError("Invalid block type reference")
            self.types.append(types[type_index])
            self.blocks.append(Reader(reader.take(size), reader.endian))
        self.roots = reader.refs()
        # Archive extraction retains zero sector padding; it is not NIF content.
        if any(reader.take(len(reader.data) - reader.pos)):
            raise FormatError("Unexpected nonzero NIF footer")
        self.decoded = {}

    def string(self, index):
        if index == -1:
            return ""
        if not 0 <= index < len(self.strings):
            raise FormatError(f"Invalid string index {index}")
        return self.strings[index]

    def block(self, index):
        if index == -1:
            return None
        if not 0 <= index < len(self.blocks):
            raise FormatError(f"Invalid block index {index}")
        if index in self.decoded:
            return self.decoded[index]
        reader = self.blocks[index]
        reader.pos = 0
        kind = self.types[index]
        result = {"type": kind}
        self.decoded[index] = result

        def net():
            result["name"] = self.string(reader.read("i"))
            result["extra"] = reader.refs()
            result["controller"] = reader.read("i")

        if kind in ("NiNode", "NiTriShape", "NiTriStrips"):
            net()
            result["flags"] = reader.read("H")
            result["translation"] = reader.read("3f")
            result["rotation"] = reader.read("9f")
            result["scale"] = reader.read("f")
            result["properties"] = reader.refs()
            result["collision"] = reader.read("i")
            if kind == "NiNode":
                result["children"] = reader.refs()
                result["effects"] = reader.refs()
            else:
                result["data"], result["skin"] = reader.read("ii")
                material_count = reader.read("I")
                result["material_names"] = [self.string(item[0]) for item in reader.array("i", material_count)]
                reader.array("i", material_count)
                reader.read("iB")
        elif kind in ("NiTriShapeData", "NiTriStripsData"):
            reader.read("i")
            count = reader.read("H")
            reader.read("BB")
            result["vertices"] = reader.array("3f", count) if reader.read("B") else []
            flags = reader.read("H")
            result["normals"] = reader.array("3f", count) if reader.read("B") else []
            if result["normals"] and flags & 0x1000:
                reader.array("3f", count * 2)
            reader.read("4f")
            result["colors"] = reader.array("4f", count) if reader.read("B") else []
            result["uv_sets"] = [reader.array("2f", count) for _ in range(flags & 63)]
            reader.read("Hi")
            triangle_count = reader.read("H")
            if kind == "NiTriShapeData":
                point_count = reader.read("I")
                result["triangles"] = reader.array("3H", triangle_count) if reader.read("B") else []
                if result["triangles"] and point_count != triangle_count * 3:
                    raise FormatError("Triangle point count mismatch")
                for _ in range(reader.read("H")):
                    reader.array("H", reader.read("H"))
            else:
                lengths = [item[0] for item in reader.array("H", reader.read("H"))]
                strips = [[item[0] for item in reader.array("H", size)] for size in lengths] if reader.read("B") else []
                result["triangles"] = []
                for strip in strips:
                    for i in range(len(strip) - 2):
                        triangle = (strip[i + (i % 2)], strip[i + 1 - (i % 2)], strip[i + 2])
                        if len(set(triangle)) == 3:
                            result["triangles"].append(triangle)
            if any(i >= count for triangle in result["triangles"] for i in triangle):
                raise FormatError("Triangle index exceeds vertex count")
        elif kind in ("NiMaterialProperty", "NiTexturingProperty", "NiAlphaProperty", "NiStencilProperty", "NiSourceTexture", "NiSourceCubeMap"):
            net()
            if kind == "NiMaterialProperty":
                result["ambient"], result["diffuse"], result["specular"], result["emissive"] = reader.array("3f", 4)
                result["gloss"], result["alpha"] = reader.read("ff")
            elif kind == "NiTexturingProperty":
                result["flags"] = reader.read("H")
                result["slots"] = []
                for slot in range(reader.read("I")):
                    if not reader.read("B"):
                        result["slots"].append(None)
                        continue
                    source, flags, transformed = reader.read("iHB")
                    descriptor = {"source": source, "flags": flags}
                    if transformed:
                        descriptor["uv_transform"] = reader.read("5fI2f")
                        if slot == 0:
                            result["uv_transform_unsupported"] = True
                    if slot == 5:
                        reader.read("6f")
                    if slot == 7:
                        reader.read("f")
                    result["slots"].append(descriptor)
                shader_count = reader.read("I")
                if shader_count > 4096:
                    raise FormatError("Excessive custom shader texture count")
                result["shader_slots"] = []
                for _ in range(shader_count):
                    if not reader.read("B"):
                        result["shader_slots"].append(None)
                        continue
                    source, flags, transformed = reader.read("iHB")
                    descriptor = {"source": source, "flags": flags}
                    if transformed:
                        descriptor["uv_transform"] = reader.read("5fI2f")
                    descriptor["map_id"] = reader.read("I")
                    result["shader_slots"].append(descriptor)
            elif kind == "NiAlphaProperty":
                result["flags"], result["threshold"] = reader.read("HB")
            elif kind == "NiStencilProperty":
                result["flags"], result["reference"], result["mask"] = reader.read("HII")
            else:
                result["external"], filename, result["pixels"] = reader.read("Bii")
                result["filename"] = self.string(filename)
                reader.read("IIIBBB")
        elif kind == "NiPixelData":
            result["format"], result["bits"] = reader.read("IB")
            reader.read("iIBIB")
            result["channels"] = reader.array("IIBB", 4)
            result["palette"] = reader.read("i")
            mipmaps, result["bytes_per_pixel"] = reader.read("II")
            result["mips"] = reader.array("III", mipmaps)
            size, result["faces"] = reader.read("II")
            result["pixels"] = reader.take(size * result["faces"])
        elif kind == "NiPalette":
            result["alpha"] = reader.read("B")
            result["colors"] = reader.array("4B", reader.read("I"))
        else:
            result["unsupported"] = True
            return result
        if reader.pos != len(reader.data):
            raise FormatError(f"{self.path.name}: {kind} #{index} consumed {reader.pos}/{len(reader.data)} bytes")
        return result


def texture_basename(value):
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def decode_pixels(nif, pixel):
    from PIL import Image
    width, height, offset = pixel["mips"][0]
    if width < 1 or height < 1 or width * height > 100_000_000:
        raise FormatError("Invalid image dimensions")
    data = pixel["pixels"][offset:]
    kind = pixel["format"]
    if kind in (4, 6):
        fourcc = b"DXT1" if kind == 4 else b"DXT5"
        block_size = 8 if kind == 4 else 16
        size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_size
        dds = b"DDS " + struct.pack("<7I", 124, 0x81007, height, width, size, 0, 0)
        dds += bytes(44) + struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0)
        dds += struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data[:size]
        image = Image.open(BytesIO(dds)).convert("RGBA")
    elif kind in (0, 1):
        channel_types = [c[0] for c in pixel["channels"] if c[2] > 0]
        # Gamebryo ChannelType: RED=0 GREEN=1 BLUE=2 ALPHA=3.
        mode = "".join({0: "R", 1: "G", 2: "B", 3: "A"}.get(c, "?") for c in channel_types)
        expected = "RGB" if kind == 0 else "RGBA"
        if len(mode) != len(expected) or set(mode) != set(expected):
            raise FormatError(f"Unsupported raw channel layout {channel_types}")
        image = Image.frombytes(expected, (width, height), data[:width * height * len(mode)], "raw", mode).convert("RGBA")
    elif kind == 2:
        palette = nif.block(pixel["palette"])["colors"]
        rgba = bytes(v for index in data[:width * height] for v in palette[index])
        image = Image.frombytes("RGBA", (width, height), rgba)
    else:
        raise FormatError(f"Unsupported pixel format {kind}")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), width, height


def load_textures(paths):
    textures = {}
    for path in paths:
        nif = Nif(path)
        for index, kind in enumerate(nif.types):
            if kind != "NiSourceTexture":
                continue
            source = nif.block(index)
            if source["external"] or source["pixels"] < 0:
                continue
            name = texture_basename(source["filename"])
            png, width, height = decode_pixels(nif, nif.block(source["pixels"]))
            if name in textures and textures[name]["png"] != png:
                raise FormatError(f"Conflicting duplicate texture name: {name}")
            textures[name] = {"png": png, "width": width, "height": height, "source": str(path)}
    return textures


IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def matmul(a, b):
    return tuple(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4))


def matrix(node):
    rot, pos, scale = node["rotation"], node["translation"], node["scale"]
    return tuple(rot[r * 3 + c] * scale if c < 3 and r < 3 else pos[r] if c == 3 and r < 3 else int(c == r) for r in range(4) for c in range(4))


def point(m, p, normal=False):
    transform = m
    if normal:
        a, b, c, d, e, f, g, h, i = (m[x] for x in (0, 1, 2, 4, 5, 6, 8, 9, 10))
        cofactors = (e*i-f*h, f*g-d*i, d*h-e*g, c*h-b*i, a*i-c*g, b*g-a*h, b*f-c*e, c*d-a*f, a*e-b*d)
        determinant = a*cofactors[0]+b*cofactors[1]+c*cofactors[2]
        if abs(determinant) < 1e-12:
            raise FormatError("Singular normal transform")
        transform = tuple(cofactors[r*3+c]/determinant if r < 3 and c < 3 else int(r == c) for r in range(4) for c in range(4))
    values = [sum(transform[r * 4 + c] * p[c] for c in range(3)) + (0 if normal else m[r * 4 + 3]) for r in range(3)]
    if normal:
        length = math.sqrt(sum(v * v for v in values))
        values = [v / length for v in values] if length else [0, 0, 1]
    return (values[0], values[2], -values[1])  # Z up to glTF Y up, orientation preserved.


def determinant3(m):
    a, b, c, d, e, f, g, h, i = (m[x] for x in (0, 1, 2, 4, 5, 6, 8, 9, 10))
    return a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)


def read_instances(path):
    """Decode only the first inst section, whose offset was measured in 85 IPBs.

    Later section formats are deliberately not guessed or silently traversed.
    """
    data = path.read_bytes()
    if data[:4] != b"Ipl$" or data[23:27] != b"inst":
        raise FormatError("Unsupported IPB header/first-section layout")
    count = struct.unpack_from("<I", data, 27)[0]
    if count > 100_000 or 31 + 120 * count > len(data):
        raise FormatError("Invalid IPB instance extent")
    result = []
    for index in range(count):
        values = struct.unpack_from("<i64sf3f3f4fII", data, 31 + 120 * index)
        _, raw_name, draw_distance, *rest = values
        position, scale, rotation = rest[:3], rest[3:6], rest[6:10]
        if not all(math.isfinite(v) for v in (draw_distance, *position, *scale, *rotation)):
            raise FormatError("Nonfinite IPB instance transform")
        if abs(sum(v*v for v in rotation) - 1) > 0.01:
            raise FormatError("Nonunit IPB instance quaternion")
        result.append({"model_id": values[0], "name": raw_name.split(b"\0")[0].decode("ascii"),
                       "position": position, "scale": scale, "quaternion_xyzw": rotation})
    return result


def placement_matrix(instance):
    x, y, z, w = instance["quaternion_xyzw"]
    # IPB quaternion is conjugated relative to the usual active rotation.
    x, y, z = -x, -y, -z
    rotation = (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
                2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
                2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))
    return tuple(rotation[r*3+c]*instance["scale"][c] if r < 3 and c < 3
                 else instance["position"][r] if r < 3 and c == 3 else int(r == c)
                 for r in range(4) for c in range(4))


class Glb:
    def __init__(self):
        self.data = bytearray()
        self.json = {"asset": {"version": "2.0", "generator": "Rea_Bully independent NIF exporter"},
                     "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
                     "bufferViews": [], "accessors": [], "materials": []}

    def view(self, data):
        self.data.extend(bytes((-len(self.data)) % 4))
        index = len(self.json["bufferViews"])
        self.json["bufferViews"].append({"buffer": 0, "byteOffset": len(self.data), "byteLength": len(data)})
        self.data.extend(data)
        return index

    def accessor(self, values, fmt, kind, component):
        flat = [v for item in values for v in item]
        if fmt == "f" and not all(math.isfinite(v) for v in flat):
            raise FormatError(f"Nonfinite {kind} attribute cannot be exported")
        data = struct.pack("<" + fmt * len(flat), *flat)
        accessor = {"bufferView": self.view(data), "componentType": component, "count": len(values), "type": kind}
        if kind == "VEC3":
            accessor["min"] = [min(item[i] for item in values) for i in range(3)]
            accessor["max"] = [max(item[i] for item in values) for i in range(3)]
        self.json["accessors"].append(accessor)
        return len(self.json["accessors"]) - 1

    def image(self, name, png):
        images = self.json.setdefault("images", [])
        images.append({"name": name, "bufferView": self.view(png), "mimeType": "image/png"})
        textures = self.json.setdefault("textures", [])
        textures.append({"source": len(images) - 1})
        return len(textures) - 1

    def write(self, path):
        self.json["buffers"] = [{"byteLength": len(self.data)}]
        encoded = json.dumps(self.json, separators=(",", ":"), allow_nan=False).encode("utf-8")
        encoded += b" " * ((-len(encoded)) % 4)
        self.data.extend(bytes((-len(self.data)) % 4))
        total = 12 + 8 + len(encoded) + 8 + len(self.data)
        payload = struct.pack("<III", 0x46546C67, 2, total)
        payload += struct.pack("<I4s", len(encoded), b"JSON") + encoded
        payload += struct.pack("<I4s", len(self.data), b"BIN\0") + self.data
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def select_static_geometry(nif, node, mesh, invalid_geometry_policy="reject", invalid_color_policy="reject"):
    """Select the source base-map UV channel; optionally omit invalid faces.

    TexturingMapFlags low byte is the source coordinate index (NIF 20.3).
    Omission is an explicit recovery policy, never a fabricated UV or colour.
    The exact original vertex/triangle correspondence is retained in metadata.
    """
    if invalid_geometry_policy not in {"reject", "omit_invalid_faces"}:
        raise FormatError("Unknown invalid geometry policy")
    if invalid_color_policy not in {"reject", "base_material_only"}:
        raise FormatError("Unknown invalid vertex colour policy")
    uv_index = 0
    base_transform = None
    for index in node["properties"]:
        prop = nif.block(index)
        if prop["type"] == "NiTexturingProperty" and prop["slots"] and prop["slots"][0]:
            uv_index = prop["slots"][0]["flags"] & 255
            base_transform = prop["slots"][0].get("uv_transform")
            if uv_index >= len(mesh["uv_sets"]):
                raise FormatError(f"Base texture refers to absent source UV set {uv_index}")
    selected = dict(mesh)
    selected["uv_sets"] = [mesh["uv_sets"][uv_index]] if mesh["uv_sets"] else []
    metadata = {"selected_base_uv_set": uv_index if mesh["uv_sets"] else None}
    if invalid_color_policy == "base_material_only" and mesh["colors"]:
        rgb = [value for color in mesh["colors"] for value in color[:3]]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in rgb):
            color_bytes = b"".join(struct.pack("<4f", *color) for color in mesh["colors"])
            metadata["vertex_color_omission"] = {"policy": invalid_color_policy,
                "reason": "Original RGB cannot be represented as valid glTF COLOR_0; geometry and base material retained",
                "source_color_float32_rgba_sha256": hashlib.sha256(color_bytes).hexdigest(),
                "source_vertex_count": len(mesh["colors"]),
                "source_rgb_min": min(rgb) if all(math.isfinite(v) for v in rgb) else None,
                "source_rgb_max": max(rgb) if all(math.isfinite(v) for v in rgb) else None,
                "source_nonfinite_component_count": sum(not math.isfinite(v) for v in rgb),
                "shading_pending": True}
            selected["colors"] = []
    if base_transform is not None:
        tx, ty, sx, sy, angle, method, cx, cy = base_transform
        if method != 1 or not all(math.isfinite(value) for value in base_transform):
            raise FormatError("Only finite original Max-method base UV transforms are implemented")
        cosine, sine = math.cos(angle), math.sin(angle)
        # NIF Max method: Center * Scale * Rotation * Translate * Back.
        selected["uv_sets"] = [[(cx + sx*(cosine*(u+tx-cx)-sine*(v+ty-cy)),
                                  cy + sy*(sine*(u+tx-cx)+cosine*(v+ty-cy)))
                                 for u, v in selected["uv_sets"][0]]]
        metadata["base_uv_transform"] = base_transform
    if invalid_geometry_policy == "reject":
        return selected, metadata
    channels = [mesh["vertices"], mesh["normals"], selected["colors"], *selected["uv_sets"]]
    invalid = {index for channel in channels if channel for index, values in enumerate(channel)
               if not all(math.isfinite(value) for value in values)}
    if not invalid:
        return selected, metadata
    kept = [(index, tri) for index, tri in enumerate(mesh["triangles"]) if not invalid.intersection(tri)]
    retained = sorted({v for _, tri in kept for v in tri})
    lookup = {original: converted for converted, original in enumerate(retained)}
    selected["triangles"] = [tuple(lookup[v] for v in tri) for _, tri in kept]
    for key in ("vertices", "normals", "colors"):
        selected[key] = [selected[key][index] for index in retained] if selected[key] else []
    selected["uv_sets"] = [[uvs[index] for index in retained] for uvs in selected["uv_sets"]]
    metadata.update(policy=invalid_geometry_policy, source_vertex_count=len(mesh["vertices"]),
        source_triangle_count=len(mesh["triangles"]), nonfinite_source_vertex_indices=sorted(invalid),
        omitted_source_triangle_indices=[index for index, tri in enumerate(mesh["triangles"]) if invalid.intersection(tri)],
        retained_source_triangle_indices=[index for index, _ in kept],
        exported_vertex_to_source_vertex=retained)
    return selected, metadata


def export(nif_path, nft_paths, output, entity_space=True, ipb_path=None, *,
           instances=None, texture_cache=None, loose_textures=True, require_textures=False,
           invalid_geometry_policy="reject", invalid_color_policy="reject"):
    cache_key = tuple(str(p.resolve()) for p in nft_paths)
    if texture_cache is not None:
        if cache_key not in texture_cache:
            texture_cache[cache_key] = load_textures(nft_paths)
        textures = texture_cache[cache_key]
    else:
        textures = load_textures(nft_paths)
    nif, glb = Nif(nif_path), Glb()
    stats = {"source": str(nif_path), "source_sha256": hashlib.sha256(nif_path.read_bytes()).hexdigest(),
             "texture_sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in nft_paths],
             "output": str(output), "shapes": 0, "vertices": 0, "triangles": 0,
             "texture_count": 0, "missing_textures": [], "warnings": [], "blocks_by_type": dict(Counter(nif.types)),
             "bounds_min_gltf": None, "bounds_max_gltf": None, "mirrored_shapes_corrected": 0,
             "original_vertex_alpha_range_ignored": None,
             "invalid_geometry_policy": invalid_geometry_policy, "geometry_omissions": [],
             "invalid_color_policy": invalid_color_policy, "vertex_color_omissions": [],
             "source_uv_selection": [],
             "limitations": ["Static geometry only; no skin, AGR animation, collision, Lua or original shaders",
                             "Special texture slots and original shader/lighting behavior are not reproduced"]}
    image_indices = {}
    active = set()

    def visit(index, parent, depth):
        if index < 0:
            return
        if index in active or depth > 256:
            raise FormatError("Cyclic or excessive NIF hierarchy")
        active.add(index)
        node = nif.block(index)
        if node["type"] not in ("NiNode", "NiTriShape", "NiTriStrips"):
            stats["warnings"].append(f"Unsupported scene node {index}: {node['type']}")
            active.remove(index)
            return
        local = IDENTITY if entity_space and depth <= 1 else matrix(node)
        world = matmul(parent, local)
        if node["type"] == "NiNode":
            for child in node["children"]:
                visit(child, world, depth + 1)
        else:
            if node["skin"] >= 0:
                raise FormatError(f"Skinned shape {node['name']} cannot be exported as static silently")
            mesh = nif.block(node["data"])
            mesh, geometry_metadata = select_static_geometry(nif, node, mesh, invalid_geometry_policy, invalid_color_policy)
            stats["source_uv_selection"].append({"shape_block": index, "data_block": node["data"],
                "shape_name": node["name"], "selected_base_uv_set": geometry_metadata["selected_base_uv_set"],
                "base_uv_transform": geometry_metadata.get("base_uv_transform")})
            if "policy" in geometry_metadata:
                stats["geometry_omissions"].append({"shape_block": index, "data_block": node["data"],
                    "shape_name": node["name"], **geometry_metadata})
            if "vertex_color_omission" in geometry_metadata:
                stats["vertex_color_omissions"].append({"shape_block": index, "data_block": node["data"],
                    "shape_name": node["name"], **geometry_metadata["vertex_color_omission"]})
            if not mesh["vertices"] or not mesh["triangles"]:
                active.remove(index)
                return
            positions = [point(world, p) for p in mesh["vertices"]]
            low = [min(p[i] for p in positions) for i in range(3)]
            high = [max(p[i] for p in positions) for i in range(3)]
            stats["bounds_min_gltf"] = [min(a, b) for a, b in zip(stats["bounds_min_gltf"], low)] if stats["bounds_min_gltf"] else low
            stats["bounds_max_gltf"] = [max(a, b) for a, b in zip(stats["bounds_max_gltf"], high)] if stats["bounds_max_gltf"] else high
            attributes = {"POSITION": glb.accessor(positions, "f", "VEC3", 5126)}
            if mesh["normals"]:
                attributes["NORMAL"] = glb.accessor([point(world, p, True) for p in mesh["normals"]], "f", "VEC3", 5126)
            if mesh["uv_sets"]:
                attributes["TEXCOORD_0"] = glb.accessor([(u, 1 - v) for u, v in mesh["uv_sets"][0]], "f", "VEC2", 5126)
            if mesh["colors"]:
                if not all(math.isfinite(c) for color in mesh["colors"] for c in color):
                    raise FormatError("Nonfinite original vertex color")
                if not all(0 <= c <= 1 for color in mesh["colors"] for c in color[:3]):
                    raise FormatError("Vertex RGB outside glTF 0..1 range")
                # Bully data often stores alpha=100.0. Its shader meaning is
                # unverified, so preserve RGB as VEC3 rather than guess units.
                alpha_range = [min(c[3] for c in mesh["colors"]), max(c[3] for c in mesh["colors"])]
                previous = stats["original_vertex_alpha_range_ignored"]
                stats["original_vertex_alpha_range_ignored"] = [min(previous[0], alpha_range[0]), max(previous[1], alpha_range[1])] if previous else alpha_range
                attributes["COLOR_0"] = glb.accessor([c[:3] for c in mesh["colors"]], "f", "VEC3", 5126)
            material = {"name": node["name"], "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 0.8}}
            if "vertex_color_omission" in geometry_metadata:
                material.setdefault("extras", {})["REA_vertexLightingPending"] = geometry_metadata["vertex_color_omission"]
            for prop_index in node["properties"]:
                prop = nif.block(prop_index)
                if prop["type"] == "NiMaterialProperty":
                    material["pbrMetallicRoughness"]["baseColorFactor"] = [*prop["diffuse"], prop["alpha"]]
                elif prop["type"] == "NiTexturingProperty" and prop["slots"] and prop["slots"][0]:
                    if prop["slots"][0].get("uv_transform"):
                        material.setdefault("extras", {})["REA_originalBaseUVTransform"] = prop["slots"][0]["uv_transform"]
                    source = nif.block(prop["slots"][0]["source"])
                    if any(prop.get("shader_slots", [])):
                        material.setdefault("extras", {})["REA_originalShaderMaps"] = prop["shader_slots"]
                        stats["warnings"].append(f"{node['name']}: original custom shader maps retained as metadata; only base material evaluated")
                    name = texture_basename(source["filename"])
                    if name in textures:
                        if name not in image_indices:
                            image_indices[name] = glb.image(name, textures[name]["png"])
                        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_indices[name]}
                    else:
                        stats["missing_textures"].append(name)
                elif prop["type"] == "NiAlphaProperty":
                    flags = prop["flags"]
                    source_blend, destination_blend = (flags >> 1) & 15, (flags >> 5) & 15
                    material.setdefault("extras", {})["REA_originalAlpha"] = {
                        "flags": flags, "sourceBlend": source_blend,
                        "destinationBlend": destination_blend,
                        "testEnabled": bool(flags & 0x200),
                        "testFunction": (flags >> 10) & 7,
                        "thresholdByte": prop["threshold"],
                    }
                    if prop["flags"] & 1:
                        material["alphaMode"] = "BLEND"
                        if (source_blend, destination_blend) != (6, 7):
                            warning = (f"{node['name']}: glTF BLEND cannot encode NIF blend factors "
                                       f"{source_blend}/{destination_blend}; explicit material recovery required")
                            if warning not in stats["warnings"]:
                                stats["warnings"].append(warning)
                        elif flags & 0x200:
                            warning = f"{node['name']}: combined NIF alpha blend/test requires explicit material recovery"
                            if warning not in stats["warnings"]:
                                stats["warnings"].append(warning)
                    elif prop["flags"] & 0x200:
                        material["alphaMode"] = "MASK"
                        material["alphaCutoff"] = prop["threshold"] / 255
                elif prop["type"] == "NiStencilProperty":
                    material["doubleSided"] = ((prop["flags"] >> 10) & 3) == 3
            glb.json["materials"].append(material)
            triangles = mesh["triangles"]
            if determinant3(world) < 0:
                triangles = [(a, c, b) for a, b, c in triangles]
                stats["mirrored_shapes_corrected"] += 1
            primitive = {"attributes": attributes, "indices": glb.accessor([(i,) for tri in triangles for i in tri], "H", "SCALAR", 5123), "material": len(glb.json["materials"]) - 1}
            glb.json["meshes"].append({"name": node["name"], "primitives": [primitive]})
            glb.json["scenes"][0]["nodes"].append(len(glb.json["nodes"]))
            glb.json["nodes"].append({"name": node["name"], "mesh": len(glb.json["meshes"]) - 1})
            stats["shapes"] += 1
            stats["vertices"] += len(mesh["vertices"])
            stats["triangles"] += len(mesh["triangles"])
        active.remove(index)

    placements = list(instances) if instances is not None else [instance for instance in read_instances(ipb_path) if instance["name"].lower() == nif_path.stem.lower()] if ipb_path else []
    if ipb_path and not placements:
        raise FormatError(f"Model {nif_path.stem} not named in the IPB inst section")
    stats["placement_source"] = str(ipb_path) if ipb_path else None
    stats["placements"] = placements
    for transform in [placement_matrix(p) for p in placements] if placements else [IDENTITY]:
        for root in nif.roots:
            visit(root, transform, 0)
    if not stats["shapes"]:
        raise FormatError("No supported original mesh geometry found")
    stats["texture_count"] = len(image_indices)
    stats["missing_textures"] = sorted(set(stats["missing_textures"]))
    if require_textures and stats["missing_textures"]:
        raise FormatError("Unresolved base textures: " + ", ".join(stats["missing_textures"]))
    stats["unparsed_block_types"] = sorted({kind for i, kind in enumerate(nif.types) if i not in nif.decoded})
    glb.write(output)
    if loose_textures:
        for name, texture in textures.items():
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(name).stem) + ".png"
            (output.parent / safe).write_bytes(texture["png"])
    output.with_suffix(".report.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nif", type=Path, required=True)
    parser.add_argument("--nft", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ipb", type=Path, help="Optional actual placement for this named model (first inst section only)")
    parser.add_argument("--retain-root-transform", action="store_true")
    parser.add_argument("--invalid-geometry-policy", choices=["reject", "omit_invalid_faces"], default="reject")
    parser.add_argument("--invalid-color-policy", choices=["reject", "base_material_only"], default="reject")
    args = parser.parse_args()
    output = args.output.resolve()
    local = Path(__file__).resolve().parents[1] / "local"
    if local not in output.parents or output.suffix.lower() != ".glb":
        parser.error("Derived assets must be written as .glb under this workspace's local directory")
    try:
        stats = export(args.nif, args.nft, output, not args.retain_root_transform, args.ipb,
            invalid_geometry_policy=args.invalid_geometry_policy, invalid_color_policy=args.invalid_color_policy)
    except (FormatError, OSError, KeyError, struct.error) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
