"""Engine side of asset QA: the 3D-print check (needs Blender's BVH tree for rays and self-overlap)."""
import math


def op_print_check(p):
    import bmesh
    import bpy
    from mathutils.bvhtree import BVHTree

    from . import bridge

    objs, _ = bridge.load(p["scene"])
    unit = float(p["scene"].get("unit", 0.01))
    bm = bmesh.new()
    for obj in objs.values():
        if obj.type != "MESH":
            continue
        me = obj.data.copy()
        me.transform(obj.matrix_world)
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm)
    min_wall = float(p.get("min_wall", 0.08)) * unit
    cos_over = math.cos(math.radians(float(p.get("overhang", 45))))
    low = min((v.co.z for v in bm.verts), default=0.0)
    thin = down = 0
    area = sum(f.calc_area() for f in bm.faces) or 1.0
    step = max(1, len(bm.faces) // 2000)  # ponytail: thickness on a 2000-face sample; all faces if it must be exact
    for i, f in enumerate(bm.faces):
        if f.normal.z < -cos_over and min(v.co.z for v in f.verts) > low + 1e-4:
            down += f.calc_area()
        if i % step == 0 and f.calc_area() > 1e-12:
            hit = tree.ray_cast(f.calc_center_median() - f.normal * 1e-6, -f.normal, min_wall)
            if hit[0] is not None:
                thin += step
    bm.verts.index_update()
    shells, seen = 0, set()
    for v in bm.verts:
        if v.index in seen:
            continue
        shells += 1
        stack = [v]
        seen.add(v.index)
        while stack:
            for e in stack.pop().link_edges:
                for w in e.verts:
                    if w.index not in seen:
                        seen.add(w.index)
                        stack.append(w)
    out = {"open": sum(1 for e in bm.edges if e.is_boundary),
           "nonmanifold": sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary),
           "degenerate": sum(1 for f in bm.faces if f.calc_area() < 1e-10),
           "intersect": len({i for pair in tree.overlap(tree) for i in pair}),
           "thin": min(thin, len(bm.faces)), "overhang_pct": round(100.0 * down / area, 1), "shells": shells,
           "volume_cm3": round(abs(bm.calc_volume(signed=False)) / unit ** 3, 1)}
    bm.free()
    return out
