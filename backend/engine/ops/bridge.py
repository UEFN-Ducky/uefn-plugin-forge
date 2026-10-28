"""Engine side of the Forge <-> bpy bridge, plus file import. Runs in the bpy worker only.

Forge is centimetres, Z-up, -Y front; Blender is metres, Z-up, -Y front, so only units
convert, here at the edge. Forge-only fields ride along as JSON custom properties.

A feature op:  objs, arms = bridge.load(p["scene"]) ... return {..., "scene": bridge.dump(p["scene"])}
objs maps Forge ids to bpy objects (meshes carry an Armature modifier when skinned). An op
that rewrites vertex groups sets obj["forge_auto_weights"] = False so they come back painted.
New objects without obj["forge_id"] come back as new Forge objects.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from bpy_extras import anim_utils
from mathutils import Euler, Matrix, Quaternion, Vector

_INTERP = {"smooth": "BEZIER", "linear": "LINEAR", "step": "CONSTANT"}
_FORGE_INTERP = {v: k for k, v in _INTERP.items()}
_SOCKET = {"base_color": "Base Color", "roughness": "Roughness", "metallic": "Metallic",
           "normal": "Normal", "emissive": "Emission Color"}


def op_import_file(p):
    """Run the Blender export script on a .blend/.fbx/.gltf/.glb; it writes one Forge scene.json."""
    source = str(p["source"])
    if source.lower().endswith(".blend"):
        bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)
    argv = sys.argv
    sys.argv = ["blender", "--", str(p["output"]), source]
    try:
        script = Path(p["script"])
        exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), {"__name__": "__main__"})
    finally:
        sys.argv = argv
    return {"output": str(p["output"])}


def op_bridge_echo(p):
    """Load and dump unchanged: the bridge's own round-trip check."""
    load(p["scene"])
    return {"scene": dump(p["scene"], force=True)}


# ---- Forge -> bpy -----------------------------------------------------------


def load(payload):
    unit = float(payload.get("unit", 0.01))
    folder = Path(payload["folder"])
    mats = {m["forge"]["id"]: _material(m) for m in payload.get("materials", [])}
    arms = {r["id"]: _armature(r, unit) for r in payload.get("rigs", [])}
    objs = {o["id"]: _object(o, folder, unit, mats, arms) for o in payload.get("objects", [])}
    for rig in payload.get("rigs", []):
        _clips(rig, arms[rig["id"]], list(objs.values()), unit)
    for obj in objs.values():
        obj["forge_digest"] = _digest(_arrays(obj, unit)[0])
    return objs, arms


def _material(m):
    forge = m["forge"]
    mat = bpy.data.materials.new(forge.get("name") or forge["id"])
    mat["forge"] = json.dumps(forge)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return mat
    color = list(forge.get("base_color") or [0.8, 0.8, 0.8, 1.0])
    bsdf.inputs["Base Color"].default_value = (color + [1.0] * 4)[:4]
    bsdf.inputs["Metallic"].default_value = float(forge.get("metallic", 0.0))
    bsdf.inputs["Roughness"].default_value = float(forge.get("roughness", 0.5))
    bsdf.inputs["Alpha"].default_value = float(forge.get("opacity", 1.0))
    bsdf.inputs["Emission Color"].default_value = (list(forge.get("emissive") or [0, 0, 0]) + [0, 0, 0])[:3] + [1.0]
    bsdf.inputs["Emission Strength"].default_value = float(forge.get("emissive_strength", 0.0))
    tree = mat.node_tree
    for slot, path in (m.get("textures") or {}).items():
        socket = _SOCKET.get(slot)
        if not socket or not Path(path).is_file():
            continue
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.label = slot
        tex.image = bpy.data.images.load(path, check_existing=True)
        if slot not in ("base_color", "emissive"):
            tex.image.colorspace_settings.name = "Non-Color"
        out = tex.outputs["Color"]
        if slot == "normal":
            normal = tree.nodes.new("ShaderNodeNormalMap")
            tree.links.new(out, normal.inputs["Color"])
            out = normal.outputs["Normal"]
        tree.links.new(out, bsdf.inputs[socket])
    return mat


def _armature(rig, unit):
    data = bpy.data.armatures.new(rig.get("name") or rig["id"])
    arm = bpy.data.objects.new(data.name, data)
    bpy.context.scene.collection.objects.link(arm)
    arm["forge"] = json.dumps({k: v for k, v in rig.items() if k not in ("bones", "actions")})
    arm["forge_order"] = json.dumps([b["name"] for b in rig.get("bones") or []])  # Blender sorts by hierarchy
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in rig.get("bones") or []:
        edit = data.edit_bones.new(bone["name"])
        edit.head = Vector(bone["head"]) * unit
        edit.tail = Vector(bone["tail"]) * unit
        edit.roll = 0.0
    for bone in rig.get("bones") or []:
        if bone.get("parent") and bone["parent"] in data.edit_bones:
            data.edit_bones[bone["name"]].parent = data.edit_bones[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def _object(o, folder, unit, mats, arms):
    a = o["arrays"]
    verts = np.load(folder / a["verts"]) * unit
    sizes = np.load(folder / a["sizes"]).astype(np.int32)
    loops = np.load(folder / a["loops"]).astype(np.int32)
    me = bpy.data.meshes.new(o.get("name") or o["id"])
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(len(sizes))
    me.polygons.foreach_set("loop_start", (np.cumsum(sizes) - sizes).astype(np.int32))
    me.update(calc_edges=True)
    me["forge_mats"] = json.dumps(o["mats"])
    for mid in o["mats"]:
        me.materials.append(mats.get(mid))
    me.polygons.foreach_set("material_index", np.load(folder / a["face_mat"]).astype(np.int32))
    me.polygons.foreach_set("use_smooth", np.full(len(sizes), bool(o.get("smooth"))))
    if "uv" in a:
        me.uv_layers.new(name="UVMap").data.foreach_set("uv", np.load(folder / a["uv"]).astype(np.float32).ravel())
    if "normals" in a:
        me.normals_split_custom_set_from_vertices(np.load(folder / a["normals"]).tolist())
    if "colors" in a:
        for name, values in zip(o["colors"], np.load(folder / a["colors"])):
            me.color_attributes.new(name, "FLOAT_COLOR", "POINT").data.foreach_set("color", values.astype(np.float32).ravel())
    obj = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj["forge_id"] = o["id"]
    obj["forge_skin"] = json.dumps(o.get("skin"))
    obj["forge_auto_weights"] = bool(o.get("auto_weights"))
    matrix = Matrix(o["matrix"])
    matrix.translation = matrix.translation * unit
    obj.matrix_world = matrix
    if "weights" in a:
        weights = np.load(folder / a["weights"])
        for g, name in enumerate(o["groups"]):
            group = obj.vertex_groups.new(name=name)
            column = weights[:, g]
            idx = np.nonzero(column)[0]
            for w in np.unique(column[idx]):  # one add per distinct weight, not per vertex
                group.add(idx[column[idx] == w].tolist(), float(w), "REPLACE")
    if "shapes" in a:
        obj.shape_key_add(name="Basis", from_mix=False)
        for name, delta in zip(o["shapes"], np.load(folder / a["shapes"])):
            obj.shape_key_add(name=name, from_mix=False).data.foreach_set("co", (verts + delta * unit).astype(np.float32).ravel())
    rig = (o.get("skin") or {}).get("rig")
    if rig in arms:
        obj.modifiers.new("Armature", "ARMATURE").object = arms[rig]
    return obj


def _rest(arm):
    return {b.name: b.matrix_local.to_3x3() for b in arm.data.bones}


def _curves(bag, path, group, keys):
    """keys: [(frame, [values], interp)]. Smooth = flat handles a third of the way to each neighbour,
    which is exactly Forge's smoothstep per segment on a scalar channel."""
    for i in range(len(keys[0][1])):
        fc = bag.fcurves.new(path, index=i, group_name=group)
        fc.keyframe_points.add(len(keys))
        for k, (frame, values, interp) in enumerate(keys):
            point = fc.keyframe_points[k]
            point.co = (frame, values[i])
            point.interpolation = _INTERP.get(interp, "BEZIER")
            point.handle_left_type = point.handle_right_type = "FREE"
            prev = keys[k - 1][0] if k else frame - 1.0
            nxt = keys[k + 1][0] if k + 1 < len(keys) else frame + 1.0
            point.handle_left = (frame - (frame - prev) / 3.0, values[i])
            point.handle_right = (frame + (nxt - frame) / 3.0, values[i])


def _interp(key):
    return key[2] if len(key) > 2 else "smooth"


def _clips(rig, arm, objs, unit):
    rest = _rest(arm)
    keyed = [o for o in objs if o.data.shape_keys]
    first = None
    for clip in rig.get("actions") or []:
        act = bpy.data.actions.new(clip.get("name") or clip["id"])
        act.use_fake_user = True
        act["forge"] = json.dumps({k: v for k, v in clip.items() if k not in ("tracks", "morphs")})
        act["forge_rig"] = rig["id"]
        act.use_frame_range = True
        act.frame_start, act.frame_end = 0, max(1, float(clip.get("frames") or 1))
        act.use_cyclic = bool(clip.get("loop"))
        slot = act.slots.new(id_type="OBJECT", name=arm.name)
        bag = anim_utils.action_ensure_channelbag_for_slot(act, slot)
        extra = {"tracks": {}, "morphs": {}}
        for bone, track in (clip.get("tracks") or {}).items():
            basis = rest.get(bone)
            if basis is None:
                extra["tracks"][bone] = track
                continue
            inv = basis.transposed()
            base = 'pose.bones["%s"].' % bone
            if track.get("rot"):
                arm.pose.bones[bone].rotation_mode = "QUATERNION"
                _curves(bag, base + "rotation_quaternion", bone, [
                    (k[0], list((inv @ Euler([math.radians(v) for v in k[1]], "XYZ").to_matrix() @ basis).to_quaternion()), _interp(k))
                    for k in track["rot"]])
            if track.get("loc"):
                _curves(bag, base + "location", bone, [(k[0], list(inv @ Vector(k[1]) * unit), _interp(k)) for k in track["loc"]])
            if track.get("scale"):
                # ponytail: diagonal of the scale seen in bone space; exact for uniform scale.
                _curves(bag, base + "scale", bone, [
                    (k[0], [(inv @ Matrix.Diagonal(Vector(k[1])) @ basis)[i][i] for i in range(3)], _interp(k)) for k in track["scale"]])
        morphs = dict(clip.get("morphs") or {})
        slots = []
        for obj in keyed:
            names = {kb.name for kb in obj.data.shape_keys.key_blocks}
            mine = {n: keys for n, keys in morphs.items() if n in names and keys}
            if not mine:
                continue
            kslot = act.slots.new(id_type="KEY", name=obj.data.shape_keys.name)
            kbag = anim_utils.action_ensure_channelbag_for_slot(act, kslot)
            for name, keys in mine.items():
                _curves(kbag, 'key_blocks["%s"].value' % name, "", [(k[0], [k[1][0]], _interp(k)) for k in keys])
            slots.append((obj, kslot))
        written = {n for obj, _ in slots for n in morphs if n in {kb.name for kb in obj.data.shape_keys.key_blocks}}
        extra["morphs"] = {n: keys for n, keys in morphs.items() if n not in written}
        act["forge_extra"] = json.dumps(extra)
        if first is None:
            first = act
            arm.animation_data_create()
            arm.animation_data.action = act
            arm.animation_data.action_slot = slot
            for obj, kslot in slots:
                obj.data.shape_keys.animation_data_create()
                obj.data.shape_keys.animation_data.action = act
                obj.data.shape_keys.animation_data.action_slot = kslot


# ---- bpy -> Forge -----------------------------------------------------------


def _arrays(obj, unit):
    me = obj.data
    inv = 1.0 / unit
    nv, nf, nl = len(me.vertices), len(me.polygons), len(me.loops)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    sizes = np.empty(nf, np.int32)
    me.polygons.foreach_get("loop_total", sizes)
    loops = np.empty(nl, np.int32)
    me.loops.foreach_get("vertex_index", loops)
    face_mat = np.empty(nf, np.int32)
    me.polygons.foreach_get("material_index", face_mat)
    smooth = np.empty(nf, bool)
    me.polygons.foreach_get("use_smooth", smooth)
    arrays = {"verts": co.astype(np.float64).reshape(-1, 3) * inv, "sizes": sizes, "loops": loops, "face_mat": face_mat}
    meta = {"smooth": bool(nf and smooth.mean() >= 0.5), "colors": [], "groups": [], "shapes": []}
    layer = me.uv_layers.active or (me.uv_layers[0] if len(me.uv_layers) else None)
    if layer is not None:
        uv = np.empty(nl * 2, np.float32)
        layer.data.foreach_get("uv", uv)
        arrays["uv"] = uv.astype(np.float64).reshape(-1, 2)
    if me.has_custom_normals:
        corner = np.empty(nl * 3, np.float32)
        me.corner_normals.foreach_get("vector", corner)
        normals = np.zeros((nv, 3))
        normals[loops] = corner.reshape(-1, 3)
        arrays["normals"] = normals
    colors = []
    for attr in me.color_attributes:
        if attr.domain not in ("POINT", "CORNER"):
            continue
        values = np.empty(len(attr.data) * 4, np.float32)
        attr.data.foreach_get("color", values)
        values = values.reshape(-1, 4).astype(np.float64)
        if attr.domain == "CORNER":
            per_vertex = np.zeros((nv, 4))
            per_vertex[loops] = values
            values = per_vertex
        meta["colors"].append(attr.name)
        colors.append(values)
    if colors:
        arrays["colors"] = np.stack(colors)
    if obj.vertex_groups:
        weights = np.zeros((nv, len(obj.vertex_groups)))
        for v in me.vertices:
            for g in v.groups:
                weights[v.index, g.group] = g.weight
        meta["groups"] = [g.name for g in obj.vertex_groups]
        arrays["weights"] = weights
    if me.shape_keys:
        ref = me.shape_keys.reference_key
        base = np.empty(nv * 3, np.float32)
        ref.data.foreach_get("co", base)
        deltas = []
        for kb in me.shape_keys.key_blocks:
            if kb == ref:
                continue
            values = np.empty(nv * 3, np.float32)
            kb.data.foreach_get("co", values)
            deltas.append((values.astype(np.float64) - base.astype(np.float64)).reshape(-1, 3) * inv)
            meta["shapes"].append(kb.name)
        if deltas:
            arrays["shapes"] = np.stack(deltas)
    return arrays, meta


def _digest(arrays):
    h = hashlib.sha1()
    for key in sorted(arrays):
        h.update(key.encode())
        h.update(np.ascontiguousarray(arrays[key]).tobytes())
    return h.hexdigest()


def _material_out(mat):
    if mat.get("forge"):
        return json.loads(mat["forge"])
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if mat.node_tree else None
    out = {"id": "m" + hashlib.sha1(mat.name.encode()).hexdigest()[:6], "name": mat.name,
           "base_color": list(mat.diffuse_color), "metallic": 0.0, "roughness": 0.5, "emissive": [0, 0, 0],
           "emissive_strength": 0.0, "opacity": 1.0, "textures": {}}
    if bsdf is not None:
        out.update(base_color=list(bsdf.inputs["Base Color"].default_value), metallic=bsdf.inputs["Metallic"].default_value,
                   roughness=bsdf.inputs["Roughness"].default_value, opacity=bsdf.inputs["Alpha"].default_value)
    return out


def _read_tracks(bag, rest, inv):
    curves = {}
    for fc in bag.fcurves:
        if not fc.data_path.startswith('pose.bones["'):
            continue
        bone, _, prop = fc.data_path[len('pose.bones["'):].partition('"].')
        curves.setdefault((bone, prop), {})[fc.array_index] = fc
    tracks = {}
    for (bone, prop), fcs in curves.items():
        basis = rest.get(bone)
        channel = {"rotation_quaternion": "rot", "rotation_euler": "rot", "location": "loc", "scale": "scale"}.get(prop)
        if basis is None or channel is None:
            continue
        frames = sorted({kp.co[0] for fc in fcs.values() for kp in fc.keyframe_points})
        interp = {kp.co[0]: kp.interpolation for kp in fcs[min(fcs)].keyframe_points}
        default = [1.0, 0.0, 0.0, 0.0] if prop == "rotation_quaternion" else [1.0] * 3 if prop == "scale" else [0.0] * 3
        keys = []
        for frame in frames:
            vals = [fcs[i].evaluate(frame) if i in fcs else default[i] for i in range(len(default))]
            if prop == "rotation_quaternion":
                rot = Quaternion(vals).normalized().to_matrix()
            elif prop == "rotation_euler":
                rot = Euler(vals, "XYZ").to_matrix()
            if channel == "rot":
                value = [math.degrees(v) for v in (basis @ rot @ basis.transposed()).to_euler("XYZ")]
            elif channel == "loc":
                value = list(basis @ Vector(vals) * inv)
            else:
                value = [(basis @ Matrix.Diagonal(Vector(vals)) @ basis.transposed())[i][i] for i in range(3)]
            f = int(frame) if float(frame).is_integer() else float(frame)
            keys.append([f, value, _FORGE_INTERP.get(interp.get(frame), "smooth")])
        tracks.setdefault(bone, {})[channel] = keys
    return tracks


def _read_morphs(bag):
    morphs = {}
    for fc in bag.fcurves:
        if fc.data_path.startswith('key_blocks["') and fc.data_path.endswith('"].value'):
            name = fc.data_path[len('key_blocks["'):-len('"].value')]
            morphs[name] = [[int(kp.co[0]) if float(kp.co[0]).is_integer() else float(kp.co[0]), [kp.co[1]],
                             _FORGE_INTERP.get(kp.interpolation, "smooth")] for kp in fc.keyframe_points]
    return morphs


def _rig_out(arm, unit):
    inv = 1.0 / unit
    rig = json.loads(arm.get("forge") or "{}") or {"id": "", "name": arm.name}
    rig["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                     "head": list(b.head_local * inv), "tail": list(b.tail_local * inv)} for b in arm.data.bones]
    order = {name: i for i, name in enumerate(json.loads(arm.get("forge_order") or "[]"))}
    rig["bones"].sort(key=lambda b: order.get(b["name"], len(order)))
    rest = _rest(arm)
    active = arm.animation_data.action if arm.animation_data else None
    clips = []
    for act in bpy.data.actions:
        if act.get("forge_rig", "") != rig.get("id") and not (act.get("forge_rig") is None and act == active):
            continue
        clip = json.loads(act.get("forge") or "{}") or {
            "id": "", "name": act.name, "fps": bpy.context.scene.render.fps,
            "frames": int(act.frame_range[1]), "loop": bool(act.use_cyclic)}
        extra = json.loads(act.get("forge_extra") or "{}")
        tracks, morphs = dict(extra.get("tracks") or {}), dict(extra.get("morphs") or {})
        for slot in act.slots:
            bag = anim_utils.action_get_channelbag_for_slot(act, slot)
            if bag is None:
                continue
            if slot.target_id_type == "OBJECT" and slot.identifier == "OB" + arm.name:
                tracks.update(_read_tracks(bag, rest, inv))
            elif slot.target_id_type == "KEY":
                for name, keys in _read_morphs(bag).items():
                    morphs.setdefault(name, keys)
        clip["tracks"] = tracks
        if morphs:
            clip["morphs"] = morphs
        clips.append(clip)
    rig["actions"] = clips
    return rig


def dump(payload, force=False):
    """Every mesh and armature in the bpy scene, as Forge data. Unchanged meshes come back as ids only."""
    unit = float(payload.get("unit", 0.01))
    folder = Path(payload["folder"])
    out = {"objects": [], "rigs": [], "materials": []}
    seen = {}
    for i, obj in enumerate(o for o in bpy.data.objects if o.type == "MESH"):
        arrays, meta = _arrays(obj, unit)
        entry = {"id": obj.get("forge_id", ""), "name": obj.name}
        if not force and obj.get("forge_digest") == _digest(arrays):
            out["objects"].append(dict(entry, unchanged=True))
            continue
        files = {}
        for key, values in arrays.items():
            files[key] = "r%d_%s.npy" % (i, key)
            np.save(folder / files[key], values)
        forge_mats = json.loads(obj.data.get("forge_mats") or "[]")
        mats = []
        for k, mat in enumerate(obj.data.materials):
            if mat is None:
                mats.append(forge_mats[k] if k < len(forge_mats) else "default")
                continue
            forge = seen.setdefault(mat.name, _material_out(mat))
            mats.append(forge["id"])
        matrix = obj.matrix_world.copy()
        matrix.translation = matrix.translation / unit
        entry.update(meta, arrays=files, mats=mats or ["default"], matrix=[list(row) for row in matrix],
                     skin=json.loads(obj.get("forge_skin") or "null"), auto_weights=bool(obj.get("forge_auto_weights")))
        out["objects"].append(entry)
    out["materials"] = list(seen.values())
    out["rigs"] = [_rig_out(arm, unit) for arm in bpy.data.objects if arm.type == "ARMATURE"]
    return out
