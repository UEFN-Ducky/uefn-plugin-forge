"""Asset QA: one-call game-readiness report (qa), rig check, UCX collision, 3D-print check and reference compare.

Pure Python on Forge meshes (cm, Z-up, -Y front) except print_check, which needs Blender's BVH tree.
Each op returns a score out of 100 and the fixes in one line.
"""
import math
import re

from .. import rig as rg
from ..check import check_mesh, fix_mesh
from ..kernel.mesh import Mesh, _euler
from ..kernel.ops import set_origin
from . import engine, op

# tris, materials per asset class (Blender Agent Skills optimization tables; Ducky polycount_budgets)
BUDGETS = {"prop": (3000, 2), "hero": (100000, 5), "char": (60000, 8), "weapon": (15000, 3),
           "vehicle": (50000, 5), "module": (5000, 1), "mobile": (10000, 2)}
# per profile: tri cap per mesh, verts cap per mesh, max influences, ngons are errors, needs a closed mesh
PROFILES = {"uefn": (20000, 0, 4, False, False), "game": (0, 0, 4, False, False), "ue5": (0, 0, 8, False, False),
            "render": (0, 0, 0, False, False), "print": (0, 0, 0, False, True), "roblox": (20000, 10000, 4, True, False)}
_DUP_NAME = re.compile(r"\.\d{3}$")


def _pick(name, choices, what):
    if name in choices:
        return name
    import difflib
    close = difflib.get_close_matches(str(name), choices, n=1)
    raise ValueError("No %s %r.%s Options: %s" % (what, name, " Did you mean %r?" % close[0] if close else "", ", ".join(choices)))


def targets(scene, p):
    """Objects under id/ids (groups expand), else every object off the collision layer."""
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [])
    ids = [ids] if isinstance(ids, str) else ids
    nodes = rg._objects_under(scene, ids) if ids else [n for n in scene.data["nodes"] if n.get("type") == "object"]
    return [n for n in nodes if n.get("layer") != "collision"]


def signed_volume(mesh):
    """cm^3; negative when the faces point inward."""
    vol = 0.0
    for a, b, c, _ in mesh.triangles():
        p, q, r = mesh.verts[a], mesh.verts[b], mesh.verts[c]
        vol += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0]) + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
    return vol


def uv_overlap(mesh, res=64):
    """% of UV-covered cells hit by more than one face (cell centres in triangles). Mirrored halves count."""
    hits = {}
    for fi, face in enumerate(mesh.faces):
        uvs = mesh.uvs[fi] if fi < len(mesh.uvs) else []
        if len(uvs) != len(face):
            continue
        cells = set()
        for k in range(1, len(face) - 1):
            (ax, ay), (bx, by), (cx, cy) = (uvs[0][:2], uvs[k][:2], uvs[k + 1][:2])
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            x0, x1 = max(0, int(min(ax, bx, cx) * res)), min(res - 1, int(max(ax, bx, cx) * res))
            y0, y1 = max(0, int(min(ay, by, cy) * res)), min(res - 1, int(max(ay, by, cy) * res))
            for i in range(x0, x1 + 1):
                for j in range(y0, y1 + 1):
                    px, py = (i + 0.5) / res, (j + 0.5) / res
                    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
                    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
                    if l1 >= 0 and l2 >= 0 and l1 + l2 <= 1:
                        cells.add(i * res + j)
        for c in cells:
            hits[c] = hits.get(c, 0) + 1
    return round(100.0 * sum(1 for n in hits.values() if n > 1) / len(hits), 1) if hits else 0.0


def _zfight(meshes, eps):
    """Face pairs with the same centre (within eps cm) and parallel normals: they flicker in game."""
    seen, pairs = {}, 0
    for mesh in meshes:
        for fi in range(len(mesh.faces)):
            c, n = mesh.face_center(fi), mesh.face_normal(fi)
            key = tuple(round(v / eps) for v in c)
            for other in seen.get(key, ()):
                if abs(sum(a * b for a, b in zip(n, other))) > 0.99:
                    pairs += 1
                    break
            seen.setdefault(key, []).append(n)
    return pairs


def _influences(mesh):
    groups = [v for k, v in mesh.attrs.items() if k.startswith("wt:") and len(v) == len(mesh.verts)]
    return [sum(1 for g in groups if g[i] > 1e-4) for i in range(len(mesh.verts))] if groups else []


def _world_scale(scene, node):
    s, cur, seen = [1.0, 1.0, 1.0], node, set()
    while cur and cur["id"] not in seen:
        seen.add(cur["id"])
        t = (cur.get("transform") or {}).get("scale") or [1, 1, 1]
        s = [s[i] * float(t[i]) for i in range(3)]
        cur = scene.node(cur.get("parent"))
    return s


def _pivot_base(mesh):
    lo, hi = mesh.bounds()
    return abs(lo[2]) < 0.5 and abs((lo[0] + hi[0]) / 2) < 1 and abs((lo[1] + hi[1]) / 2) < 1


def _mounted(n):
    """Parts pivot at their mount by design: sockets, car_part / weapon data, or a part inside an assembly."""
    return bool(n.get("sockets") or n.get("car_part") or (n.get("hq") or {}).get("weapon") or n.get("parent"))


def audit(scene, nodes, profile="uefn", budget=None, eps=0.1, tol=0.2):
    """[(severity, kind, text, fix)] for these objects, plus total tris."""
    cap_tris, cap_verts, limit, ngon_err, closed = PROFILES[profile]
    out, worlds, mats, tris = [], [], set(), 0
    game = profile not in ("render", "print")
    for n in nodes:
        name = n.get("name") or n["id"]
        mesh = scene.world_mesh(n)
        if not mesh.faces:
            continue
        worlds.append(mesh)
        mats.update(mesh.face_mat)
        t = mesh.tri_count()
        tris += t
        local = scene.local_mesh(n)
        for issue in check_mesh(mesh, n["id"], has_collision=True, pivot_is_base=_mounted(n) or _pivot_base(local)):
            kind, sev = issue["id"], issue["severity"]
            if kind == "budget" or (kind == "pivot" and not game) or (kind == "uv" and profile == "print"):
                continue
            if kind == "boundary" and closed:
                sev, issue["message"] = "error", issue["message"] + " (not watertight)"
            if kind == "ngon" and ngon_err:
                sev = "error"
            fix = {"pivot": "origin=base", "ngon": "fix", "duplicates": "fix", "degenerate": "fix", "loose": "fix",
                   "uv": "fix", "boundary": "close holes"}.get(kind, "")
            out.append((sev, kind, "%s %s" % (name, issue["message"].lower() if kind != "pivot" else "pivot not at base"), fix))
        edges = {}
        for face in mesh.faces:
            for i, a in enumerate(face):
                key = tuple(sorted((a, face[(i + 1) % len(face)])))
                edges[key] = edges.get(key, 0) + 1
        if all(v == 2 for v in edges.values()) and signed_volume(mesh) < 0:  # only a closed mesh has an inside
            out.append(("error", "normals", "%s normals point inward" % name, "fix"))
        if cap_tris and t > cap_tris:
            out.append(("error", "budget", "%s %d tris > %dk per mesh" % (name, t, cap_tris // 1000), "lods/retopo"))
        if cap_verts and len(mesh.verts) > cap_verts:
            out.append(("error", "budget", "%s %d verts > %dk per mesh" % (name, len(mesh.verts), cap_verts // 1000), "retopo"))
        textured = {m["id"] for m in scene.data["materials"] if m.get("textures")}
        if game and textured & set(mesh.face_mat):  # overlap only matters under a unique (baked or painted) texture
            lap = uv_overlap(mesh)
            if lap > 5:
                out.append(("warn", "uv_overlap", "%s UV overlap %g%%" % (name, lap), "re-unwrap"))
        if limit:
            many = sum(1 for c in _influences(local) if c > limit)
            if many:
                out.append(("warn", "influences", "%s %d verts > %d influences" % (name, many, limit), "auto_weights target=%s" % profile))
        s = _world_scale(scene, n)
        if s[0] * s[1] * s[2] < 0:
            out.append(("error", "negative_scale", "%s negative scale" % name, "fix"))
        elif game and max(abs(v) for v in s) / max(1e-9, min(abs(v) for v in s)) > 1.001:
            out.append(("warn", "scale", "%s non-uniform scale" % name, "apply scale"))
        if _DUP_NAME.search(name):
            out.append(("warn", "name", "%s duplicate-style name" % name, "fix"))
    if worlds and game:
        low = min(m.bounds()[0][2] for m in worlds)
        if low > tol:
            out.append(("warn", "floating", "floats %.1f cm above ground" % low, "drop to z=0"))
        z = _zfight(worlds, eps)
        if z:
            out.append(("error", "zfight", "%d z-fighting face pairs" % z, "delete or offset"))
    if budget:
        max_tris, max_mats = BUDGETS[budget]
        if tris > max_tris:
            out.append(("error", "budget", "%d tris > %s %dk" % (tris, budget, max_tris // 1000), "lods/retopo"))
        if len(mats) > max_mats:
            out.append(("warn", "materials", "%d materials > %s %d" % (len(mats), budget, max_mats), "atlas/merge"))
    return out, tris


WARN_CAP = 3  # warnings of one kind past this cost nothing more, so 50 copies of one nit cannot floor the score


def score(issues):
    errors = sum(1 for s, *_ in issues if s == "error")
    kinds = {}
    for s, kind, *_ in issues:
        if s != "error":
            kinds[kind] = kinds.get(kind, 0) + 1
    return max(0, 100 - 10 * errors - 3 * sum(min(c, WARN_CAP) for c in kinds.values())), errors


def report(label, issues, extra=None):
    """{summary, score, pass, errors, warns}: 'uefn score 92: 2 n-gons (fix); pivot not at base (fix: origin=base)'."""
    sc, errors = score(issues)
    parts = ["%s (fix: %s)" % (text, fix) if fix else text for _, _, text, fix in
             sorted(issues, key=lambda i: i[0] != "error")]
    summary = "%s score %d: %s" % (label, sc, "; ".join(parts) if parts else "clean")
    return dict(extra or {}, summary=summary, score=sc, errors=errors, warns=len(issues) - errors, **{"pass": errors == 0})


def _fix(scene, nodes, issues):
    """Safe fixes: doubles, zero-area faces, loose verts, inward normals, n-gons, missing UVs, pivot, names."""
    kinds = {k for _, k, _, f in issues if f}
    done = 0
    for n in nodes:
        if scene.is_locked(n):
            continue
        mesh = scene._bake(n)
        if not mesh.faces:
            continue
        want = {k for k in ("duplicates", "degenerate", "loose", "ngon", "uv") if k in kinds}
        counts = fix_mesh(mesh, want)
        if "normals" in kinds and signed_volume(mesh) < 0:
            for face in mesh.faces:
                face.reverse()
            for uv in mesh.uvs:
                uv.reverse()
            counts["normals"] = 1
        if "pivot" in kinds and not _mounted(n) and not _pivot_base(mesh):
            shift = set_origin(mesh, "base")  # keep the object where it stands: move it by the shift
            t = n.setdefault("transform", {})
            s, r = t.get("scale") or [1, 1, 1], _euler(t.get("rot") or [0, 0, 0])
            d = [shift[i] * s[i] for i in range(3)]
            pos = t.get("pos") or [0, 0, 0]
            t["pos"] = [pos[i] + sum(r[i][j] * d[j] for j in range(3)) for i in range(3)]
            counts["pivot"] = 1
        if any(counts.values()):
            scene.write_mesh(n, mesh)
            done += 1
        if "name" in kinds and _DUP_NAME.search(n.get("name") or ""):
            n["name"] = _DUP_NAME.sub("", n["name"])
            done += 1
    if done:
        scene.touch_geom()
    return done


@op("qa", "qa(id?, profile=uefn|game|ue5|render|print|roblox, budget?=prop|hero|char|weapon|vehicle|module|mobile, "
          "fix=false, eps=0.1, tol=0.2) score + fixes: doubles, ngons, normals, UV overlap, influences, scale, pivot, names, tris")
def qa(scene, p):
    profile = _pick(str(p.get("profile") or p.get("target") or "uefn"), tuple(PROFILES), "qa profile")
    budget = _pick(str(p["budget"]), tuple(BUDGETS), "budget") if p.get("budget") else None
    nodes = targets(scene, p)
    if not nodes:
        raise ValueError("Nothing to check. Pass id of an object, or add one first")
    eps, tol = float(p.get("eps", 0.1)), float(p.get("tol", 0.2))
    issues, tris = audit(scene, nodes, profile, budget, eps, tol)
    fixed = 0
    if p.get("fix") and issues:
        fixed = _fix(scene, nodes, issues)
        issues, tris = audit(scene, nodes, profile, budget, eps, tol)
    out = report(profile, issues, {"tris": tris, "ids": [n["id"] for n in nodes]})
    if fixed:
        out["fixed"] = fixed
    return out


# ---- collision ------------------------------------------------------------------------------


def _directions(count):
    """Fibonacci sphere, `count` unit vectors."""
    out, golden = [], math.pi * (3 - math.sqrt(5))
    for i in range(count):
        z = 1 - 2 * (i + 0.5) / count
        r = math.sqrt(max(0.0, 1 - z * z))
        out.append((r * math.cos(golden * i), r * math.sin(golden * i), z))
    return out


def hull(points, max_verts=32):
    """Convex hull Mesh of the extreme points along max_verts directions (faces wind outward)."""
    pts = []
    for d in _directions(max(8, int(max_verts))):
        best = max(points, key=lambda v: v[0] * d[0] + v[1] * d[1] + v[2] * d[2])
        if not any(math.dist(best, q) < 1e-6 for q in pts):
            pts.append(best)
    n = len(pts)
    if n < 4:
        raise ValueError("Collision needs a solid shape (got %d distinct points)" % n)
    span = max(math.dist(a, b) for a in pts for b in pts)
    eps = 1e-6 * span + 1e-9
    planes = {}
    for i in range(n):  # ponytail: O(n^4) facet search, fine for <= 64 points; quickhull if max_verts must grow
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                a, b, c = pts[i], pts[j], pts[k]
                u = [b[t] - a[t] for t in range(3)]
                v = [c[t] - a[t] for t in range(3)]
                nm = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
                ln = math.sqrt(sum(x * x for x in nm))
                if ln < eps * span:
                    continue
                nm = [x / ln for x in nm]
                d = [sum(nm[t] * (q[t] - a[t]) for t in range(3)) for q in pts]
                if max(d) > eps:
                    if min(d) < -eps:
                        continue
                    nm, d = [-x for x in nm], [-x for x in d]
                key = tuple(round(x, 4) for x in nm)
                if key not in planes:
                    planes[key] = (nm, [q for q, dd in zip(range(n), d) if abs(dd) <= eps])
    mesh = Mesh()
    for q in pts:
        mesh.add_vert(q)
    for nm, idx in planes.values():
        c = [sum(pts[i][t] for i in idx) / len(idx) for t in range(3)]
        ref = [pts[idx[0]][t] - c[t] for t in range(3)]
        side = [nm[1] * ref[2] - nm[2] * ref[1], nm[2] * ref[0] - nm[0] * ref[2], nm[0] * ref[1] - nm[1] * ref[0]]
        ring = sorted(idx, key=lambda i: math.atan2(sum(side[t] * (pts[i][t] - c[t]) for t in range(3)),
                                                    sum(ref[t] * (pts[i][t] - c[t]) for t in range(3))))
        mesh.add_face(ring)
    return mesh


def box(lo, hi):
    mesh = Mesh()
    for z in (lo[2], hi[2]):
        for x, y in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])):
            mesh.add_vert([x, y, z])
    for f in ([0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]):
        mesh.add_face(f)
    return mesh


@op("collision", "collision(id, kind=box|hull, max_verts=32, shrink=.98) UCX_<name>_NN siblings on the collision layer "
                 "(UE5 FBX picks them up; Godot gets -colonly)")
def collision(scene, p):
    kind = _pick(str(p.get("kind") or "hull"), ("box", "hull", "convex"), "collision kind")
    nodes = targets(scene, dict(p, ids=p.get("ids") or ([p["id"]] if p.get("id") else None)))
    if not (p.get("id") or p.get("ids")) or not nodes:
        raise ValueError("collision needs id of the mesh (or group) to wrap")
    shrink, made = float(p.get("shrink", 0.98)), []
    for n in nodes:
        local = scene.local_mesh(n)
        if not local.verts:
            continue
        if kind == "box":
            part = box(*local.bounds())
        else:
            part = hull(local.verts, min(64, int(p.get("max_verts") or 32)))
        c = [sum(v[t] for v in part.verts) / len(part.verts) for t in range(3)]
        for v in part.verts:
            v[:] = [c[t] + (v[t] - c[t]) * shrink for t in range(3)]
        name = n.get("name") or n["id"]
        old = [x for x in scene.data["nodes"] if (x.get("name") or "").startswith("UCX_%s_" % name)]
        for x in old:  # running again replaces the hull
            scene.data["nodes"].remove(x)
        from ..scene import _nid
        node = {"id": _nid("o"), "type": "object", "name": "UCX_%s_01" % name, "parent": n.get("parent"),
                "layer": "collision", "visible": True, "locked": False,
                "transform": {k: list(v) for k, v in (n.get("transform") or {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]}).items()},
                "prototype": scene.adopt_mesh(part), "modifiers": [], "material": n.get("material") or "default"}
        scene.data["nodes"].append(node)
        made.append(node["id"])
    scene.touch_geom()
    return {"summary": "%d UCX %s hull(s), %d verts" % (len(made), kind, len(part.verts) if made else 0), "ids": made}


# ---- rig check --------------------------------------------------------------------------------

_MANNY = ("pelvis", "spine_01", "head", "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r", "hand_l", "hand_r",
          "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r")
_R15 = ("LowerTorso", "UpperTorso", "Head", "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm",
        "RightHand", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot")
RIG_PROFILES = {"uefn": (_MANNY, 4), "ue5": (_MANNY, 8), "unity": (_MANNY, 4), "roblox": (_R15, 4)}


def loop_seam(clip):
    """Largest first-vs-last difference of a clip: (degrees, cm). Root travel is not a seam."""
    end = float(clip.get("frames") or 1)
    deg = cm = 0.0
    for bone, track in (clip.get("tracks") or {}).items():
        _, q0, _ = rg.local_pose({"tracks": {bone: track}}, bone, 0.0)
        _, q1, _ = rg.local_pose({"tracks": {bone: track}}, bone, end)
        deg = max(deg, math.degrees(2 * math.acos(min(1.0, abs(sum(a * b for a, b in zip(q0, q1)))))))
        if track.get("loc") and not (clip.get("root_motion") and bone in ("root", "forge_root")):
            l0, _, _ = rg.local_pose({"tracks": {bone: track}}, bone, 0.0)
            l1, _, _ = rg.local_pose({"tracks": {bone: track}}, bone, end)
            cm = max(cm, math.dist(l0, l1))
    return round(deg, 2), round(cm, 2)


def rig_audit(scene, rig, profile="uefn"):
    names, limit = RIG_PROFILES[profile]
    bones = {b["name"]: b for b in rig.get("bones") or []}
    out = []
    missing = [n for n in names if n not in bones]
    if missing:
        hint = "rename to R15" if profile == "roblox" else "rig_template ue5_manny"
        out.append(("error" if len(missing) > len(names) // 2 else "warn", "names",
                    "%d %s bones missing (%s)" % (len(missing), profile, ", ".join(missing[:3])), hint))
    roots = [b for b in bones.values() if b.get("parent") not in bones]
    if len(roots) > 1:
        out.append(("warn", "roots", "%d root bones" % len(roots), "one root (export adds root)"))
    bad = []
    for name, b in bones.items():
        if name.endswith("_l"):
            twin = bones.get(name[:-2] + "_r")
            if twin is None or abs(b["head"][0] + twin["head"][0]) > 1.0 or abs(b["head"][2] - twin["head"][2]) > 1.0:
                bad.append(name[:-2])
    if bad:
        out.append(("warn", "mirror", "%d bones not mirrored (%s)" % (len(bad), ", ".join(bad[:3])), "symmetrize landmarks"))
    skinned = [n for n in scene.data["nodes"] if (n.get("skin") or {}).get("rig") == rig["id"]]
    if not skinned:
        out.append(("warn", "unbound", "no mesh bound", "auto_weights"))
    used = set()
    for n in skinned:
        if not n["skin"].get("paint"):
            used.update(bones)
            continue
        mesh = scene.local_mesh(n)
        groups = {k[3:]: v for k, v in mesh.attrs.items() if k.startswith("wt:") and len(v) == len(mesh.verts)}
        used.update(g for g, v in groups.items() if any(w > 1e-4 for w in v))
        zero = sum(1 for i in range(len(mesh.verts)) if not any(v[i] > 1e-4 for v in groups.values()))
        many = sum(1 for c in _influences(mesh) if c > limit)
        if zero:
            out.append(("error", "unweighted", "%s %d unweighted verts" % (n.get("name"), zero), "auto_weights"))
        if many:
            out.append(("warn", "influences", "%s %d verts > %d influences" % (n.get("name"), many, limit), "auto_weights target=%s" % profile))
    idle = [b for b in bones if b not in used and b not in ("root", "forge_root") and not any(c.get("parent") == b for c in bones.values())]
    if skinned and idle:
        out.append(("warn", "no_group", "%d leaf bones weight nothing (%s)" % (len(idle), ", ".join(idle[:3])), "auto_weights"))
    for clip in rig.get("actions") or []:
        if any(t.get("scale") for t in (clip.get("tracks") or {}).values()):
            out.append(("warn", "scale_keys", "%s keys bone scale" % clip["name"], "bake_clip"))
        if clip.get("loop"):
            deg, cm = loop_seam(clip)
            if deg > 1 or cm > 0.5:
                out.append(("warn", "loop", "%s loop seam %g deg %g cm" % (clip["name"], deg, cm), "bake_clip"))
    return out


@op("rig_check", "rig_check(rig?, profile=uefn|ue5|unity|roblox) names, mirror, roots, unweighted verts, influences, "
                 "idle bones, scale keys, loop seams")
def rig_check(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    profile = _pick(str(p.get("profile") or "uefn"), tuple(RIG_PROFILES), "rig profile")
    return report("%s rig" % profile, rig_audit(scene, rig, profile), {"ids": [rig["id"]], "bones": len(rig.get("bones") or [])})


# ---- reference compare ---------------------------------------------------------------------------


def mesh_grid(meshes, view, nz):
    """cols[i][k] (k up from the bottom) of the meshes' silhouette: front = X across, side = -Y across."""
    ax = 0 if view == "front" else 1
    sign = 1 if view == "front" else -1
    pts = [(sign * v[ax], v[2]) for m in meshes for v in m.verts]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
    h = (z1 - z0) or 1.0
    nx = max(2, round(nz * (x1 - x0) / h))
    cols = [[0] * nz for _ in range(nx)]
    for m in meshes:
        for a, b, c, _ in m.triangles():
            tri = [((sign * m.verts[i][ax] - x0) / h * nz, (m.verts[i][2] - z0) / h * nz) for i in (a, b, c)]
            (ax_, ay), (bx, by), (cx, cy) = tri
            den = (by - cy) * (ax_ - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            for i in range(max(0, int(min(ax_, bx, cx))), min(nx - 1, int(max(ax_, bx, cx))) + 1):
                for k in range(max(0, int(min(ay, by, cy))), min(nz - 1, int(max(ay, by, cy))) + 1):
                    px, py = i + 0.5, k + 0.5
                    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
                    l2 = ((cy - ay) * (px - cx) + (ax_ - cx) * (py - cy)) / den
                    if l1 >= -1e-9 and l2 >= -1e-9 and l1 + l2 <= 1 + 1e-9:
                        cols[i][k] = 1
    return cols, (x1 - x0) / h


def compare_grids(a, b, nz):
    """(IoU, bands within 5 % of 10) of two column grids of the same height, centred side by side."""
    w = max(len(a), len(b))

    def pad(g):
        left = (w - len(g)) // 2
        return [[0] * nz] * left + g + [[0] * nz] * (w - len(g) - left)

    a, b = pad(a), pad(b)
    inter = sum(1 for i in range(w) for k in range(nz) if a[i][k] and b[i][k])
    union = sum(1 for i in range(w) for k in range(nz) if a[i][k] or b[i][k])
    good = 0
    for band in range(10):
        ks = range(band * nz // 10, (band + 1) * nz // 10)
        wa = sum(1 for i in range(w) if any(a[i][k] for k in ks))
        wb = sum(1 for i in range(w) if any(b[i][k] for k in ks))
        good += abs(wa - wb) <= max(1, 0.05 * max(wa, wb))
    return (inter / union if union else 0.0), good


@op("ref_compare", "ref_compare(id?, front=<png>, side?=<png>, min_iou=0.85) silhouette IoU against the drawings, "
                   "10 height bands within 5 %, width/height drift")
def ref_compare(scene, p):
    from ..engine.ops.reference import grid_from, load_image, silhouette
    nodes = targets(scene, p)
    meshes = [m for m in (scene.world_mesh(n) for n in nodes) if m.faces]
    if not meshes:
        raise ValueError("Nothing to compare. Pass id of an object, or add one first")
    views = [(v, p[v]) for v in ("front", "side") if p.get(v)]
    if not views:
        raise ValueError("ref_compare needs front=<full path to the front drawing> (and side= for a side view)")
    nz, min_iou = 96, float(p.get("min_iou", 0.85))
    issues, out = [], {"ids": [n["id"] for n in nodes]}
    for view, path in views:
        w, h, px = load_image(path)
        mask, cw, ch, _ = silhouette(w, h, px)
        ref = grid_from(mask, cw, ch, max(2, round(nz * cw / ch)), nz)
        got, aspect = mesh_grid(meshes, view, nz)
        iou, bands = compare_grids(got, ref, nz)
        drift = abs(aspect - cw / ch) / (cw / ch)
        out[view + "_iou"] = round(iou, 3)
        if iou < min_iou:
            issues.append(("error", "iou", "%s IoU %.2f < %.2f" % (view, iou, min_iou), "reshape to the drawing"))
        if bands < 8:
            issues.append(("warn", "bands", "%s %d/10 height bands off" % (view, 10 - bands), "fix widths"))
        if drift > 0.03:
            issues.append(("warn", "drift", "%s width/height off %d%%" % (view, round(100 * drift)), "scale X/Y"))
    return report("ref", issues, out)


# ---- 3D print ----------------------------------------------------------------------------------


@op("print_check", "print_check(id?, min_wall=0.08, overhang=45) cm: watertight, self-intersections, thin walls, "
                   "overhangs, shells, degenerate faces (Blender BVH)")
def print_check(scene, p):
    nodes = targets(scene, p)
    if not nodes:
        raise ValueError("Nothing to check. Pass id of an object, or add one first")
    min_wall, over = float(p.get("min_wall", 0.08)), float(p.get("overhang", 45))
    result, _ = engine(scene, "print_check", [n["id"] for n in nodes], {"min_wall": min_wall, "overhang": over})
    issues = []
    if result["open"] or result["nonmanifold"]:
        issues.append(("error", "watertight", "%d open + %d non-manifold edges" % (result["open"], result["nonmanifold"]), "close holes"))
    if result["intersect"]:
        issues.append(("error", "intersect", "%d self-intersecting faces" % result["intersect"], "remesh voxel"))
    if result["degenerate"]:
        issues.append(("error", "degenerate", "%d zero-area faces" % result["degenerate"], "qa fix=true"))
    if result["thin"]:
        issues.append(("warn", "thin", "%d faces thinner than %g cm" % (result["thin"], min_wall), "solidify/thicken"))
    if result["overhang_pct"] > 10:
        issues.append(("warn", "overhang", "%g%% faces overhang > %g deg" % (result["overhang_pct"], over), "supports or rotate"))
    if result["shells"] > len(nodes):
        issues.append(("warn", "shells", "%d loose shells" % result["shells"], "join/union"))
    return report("print", issues, {"ids": [n["id"] for n in nodes], "volume_cm3": result["volume_cm3"]})
