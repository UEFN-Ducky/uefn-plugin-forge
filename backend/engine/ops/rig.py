"""Engine-side rig ops: auto_weights (bone-heat skinning with clean limits). bpy/numpy load only inside the op.

The weight clean-up (finish) and the non-manifold fallback are plain Python so the host tests run them.
"""
import math


def _seg_dist(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    n = sum(c * c for c in ab)
    t = 0.0 if n < 1e-12 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / n))
    return math.dist(p, [a[i] + ab[i] * t for i in range(3)])


def fallback_weights(verts, bones, near=6):
    """Inverse-distance skinning to the bone segments: works on any mesh, open or not.

    bones: [(name, head, tail)]. Returns one {bone: weight} per vertex (unnormalized).
    """
    span = max((max(v[i] for v in verts) - min(v[i] for v in verts)) for i in range(3)) if verts else 1.0
    eps = 0.002 * span + 1e-9
    rows = []
    for v in verts:
        ds = sorted((_seg_dist(v, h, t), n) for n, h, t in bones)[:near]
        top = {n: 1.0 / (d + eps) ** 3 for d, n in ds}
        peak = max(top.values())
        rows.append({n: w for n, w in top.items() if w >= 0.02 * peak})
    return rows


def _normalize(row):
    total = sum(row.values())
    return {k: w / total for k, w in row.items()} if total > 1e-12 else {}


def finish(rows, adj=None, limit=4, smooth=1, clean=0.01):
    """smooth -> normalize -> keep the `limit` strongest -> drop below `clean` -> normalize.

    adj[i] lists the neighbours of vertex i. Every row ends summing to 1 (empty rows stay empty).
    """
    rows = [_normalize(r) for r in rows]
    for _ in range(smooth if adj else 0):
        out = []
        for i, row in enumerate(rows):
            near = adj[i]
            if not near:
                out.append(row)
                continue
            mixed = {k: w * 0.5 for k, w in row.items()}
            for j in near:
                for k, w in rows[j].items():
                    mixed[k] = mixed.get(k, 0.0) + 0.5 * w / len(near)
            out.append(mixed)
        rows = [_normalize(r) for r in out]
    done = []
    for row in rows:
        for _ in range(4):  # dropping the small ones can push a neighbour under `clean`
            row = _normalize(dict(sorted(row.items(), key=lambda kv: -kv[1])[:limit]))
            small = [k for k, w in row.items() if w < clean]
            if not small or len(small) == len(row):
                break
            row = {k: w for k, w in row.items() if k not in small}
        done.append(_normalize(row))
    return done


def op_auto_weights(p):
    """Heat-map weights for every armature-bound mesh; open or failed regions use the distance fallback."""
    import bpy
    import numpy as np
    from . import bridge

    objs, _ = bridge.load(p["scene"])
    limit, smooth, clean = int(p.get("limit", 4)), int(p.get("smooth", 1)), float(p.get("clean", 0.01))
    heat = fell = tris = 0
    for obj in objs.values():
        mod = next((m for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
        if mod is None:
            continue
        arm = mod.object
        bones = [b for b in arm.data.bones if b.use_deform and not (b.name == "root" and len(arm.data.bones) > 1)]
        me = obj.data
        nv = len(me.vertices)
        co = np.empty(nv * 3, np.float32)
        me.vertices.foreach_get("co", co)
        world = (np.array(obj.matrix_world) @ np.c_[co.reshape(-1, 3), np.ones(nv)].T).T[:, :3]
        rows = _heat(obj, arm, {b.name for b in bones}, nv)
        missing = [i for i, r in enumerate(rows) if not r]
        if missing:
            seg = [(b.name, list(arm.matrix_world @ b.head_local), list(arm.matrix_world @ b.tail_local)) for b in bones]
            near = fallback_weights([world[i].tolist() for i in missing], seg)
            for i, row in zip(missing, near):
                rows[i] = row
        heat += nv - len(missing)
        fell += len(missing)
        edges = np.empty(len(me.edges) * 2, np.int32)
        me.edges.foreach_get("vertices", edges)
        adj = [[] for _ in range(nv)]
        for a, b in edges.reshape(-1, 2).tolist():
            adj[a].append(b)
            adj[b].append(a)
        rows = finish(rows, adj, limit, smooth, clean)
        obj.vertex_groups.clear()
        groups = {}
        for i, row in enumerate(rows):
            for name, w in row.items():
                if name not in groups:
                    groups[name] = obj.vertex_groups.new(name=name)
                groups[name].add([i], w, "REPLACE")
        obj["forge_auto_weights"] = False
        tris += len(me.polygons)
    return {"scene": bridge.dump(p["scene"]), "heat": heat, "fallback": fell, "tris": tris}


def _heat(obj, arm, names, nv):
    """Blender's bone-heat on a throwaway copy: one {bone: weight} per vertex; {} where heat failed."""
    import bpy

    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.vertex_groups.clear()
    for m in list(dup.modifiers):
        dup.modifiers.remove(m)
    bpy.context.scene.collection.objects.link(dup)
    rows = [{} for _ in range(nv)]
    try:
        with bpy.context.temp_override(active_object=arm, object=arm, selected_objects=[dup, arm], selected_editable_objects=[dup, arm]):
            bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        by_index = {g.index: g.name for g in dup.vertex_groups}
        for v in dup.data.vertices:
            for g in v.groups:
                if by_index.get(g.group) in names and g.weight > 0:
                    rows[v.index][by_index[g.group]] = g.weight
    except Exception:  # heat can fail on open meshes: those vertices fall back to distance weights
        rows = [{} for _ in range(nv)]
    finally:
        data = dup.data
        bpy.data.objects.remove(dup)
        bpy.data.meshes.remove(data)
    return rows
