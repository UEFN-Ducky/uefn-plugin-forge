"""HQ UVs and topology: unwrap + pack to a texel target with a UV report, and a quad/pole report for retopo."""
from collections import defaultdict

from . import engine, op
from .materials import _pick

_METHODS = ("seams", "smart", "min_stretch")


def _node(scene, p):
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object") or ""))
    if node is None or node.get("type") != "object":
        raise ValueError("Pass id=<object> to unwrap")
    return node


def _line(r):
    bad = r.get("degenerate_islands")
    return "%d islands, %.1f%% overlap, %d px/m, spread %.2f, %d%% packed%s" % (
        r["islands"], r["overlap_pct"], r["texel_px_m"], r["spread"], r["packed_pct"], ", %d zero-area" % bad if bad else "")


@op("uv", "uv(id, method=seams|smart|min_stretch, angle=60, res=2048, texel=512, margin_px=res/256, lightmap=false, check=false) unwrap (seams on edges sharper than angle), pack to texel px/m; check=true only reports; see skill forge/retopo_uv")
def uv(scene, p):
    node = _node(scene, p)
    res = int(p.get("res", 2048))
    if res < 64 or res > 8192 or res & (res - 1):
        raise ValueError("res %s must be a power of two from 64 to 8192" % res)
    if p.get("check"):
        r, _ = engine(scene, "uv_check", [node["id"]], {"id": node["id"], "res": res})
        return {"summary": _line(r), "ids": [node["id"]], "stats": r}
    texel = float(p.get("texel", 512))
    params = {"id": node["id"], "res": res, "texel": texel, "method": _pick(p.get("method", "seams"), _METHODS, "method"),
              "angle": float(p.get("angle", 60)), "margin_px": float(p.get("margin_px") or max(2, res // 256)),
              "lightmap": bool(p.get("lightmap"))}
    r, changed = engine(scene, "uv", [node["id"]], params)
    scene.touch_geom()
    warnings = list(r.get("warnings") or [])
    if r["overlap_pct"] > 0.5:
        warnings.append("%.1f%% overlap: fine only for mirrored parts" % r["overlap_pct"])
    if texel > 0 and not params["lightmap"] and abs(r["texel_px_m"] - texel) > 0.1 * texel:
        warnings.append("texel %d px/m, target %d" % (r["texel_px_m"], texel))
    if r["spread"] > 1.3:
        warnings.append("spread %.2f > 1.3: islands differ in density" % r["spread"])
    used = "" if r["method"] == params["method"] else " (%s fell back to smart)" % params["method"]
    return {"summary": _line(r) + used + (", lightmap-safe" if params["lightmap"] else ""),
            "ids": changed or [node["id"]], "warnings": warnings,
            "stats": {k: r[k] for k in ("islands", "overlap_pct", "texel_px_m", "spread", "packed_pct", "margin_px", "degenerate_islands")}}


def report(mesh):
    """{quads %, tris, ngons, poles (interior verts with 3 or 5+ edges), boundary verts} of a Forge mesh."""
    edges = defaultdict(int)
    for f in mesh.faces:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[(min(a, b), max(a, b))] += 1
    valence, border = defaultdict(int), set()
    for (a, b), uses in edges.items():
        valence[a] += 1
        valence[b] += 1
        if uses == 1:
            border.update((a, b))
    sizes = [len(f) for f in mesh.faces]
    poles = sum(1 for v, n in valence.items() if v not in border and n != 4)
    return {"faces": len(sizes), "quads_pct": round(100.0 * sizes.count(4) / max(1, len(sizes)), 1),
            "tris": sizes.count(3), "ngons": sum(1 for s in sizes if s > 4), "poles": poles,
            "poles_pct": round(100.0 * poles / max(1, len(valence) - len(border)), 1), "border_verts": len(border)}


@op("topology", "topology(id) quad %, tris, ngons, poles (3/5+ edge verts): the retopo check; see skill forge/retopo_uv")
def topology(scene, p):
    node = _node(scene, p)
    r = report(scene.local_mesh(node))
    warnings = []
    if r["ngons"]:
        warnings.append("%d ngons: triangulate or cut before export" % r["ngons"])
    if r["quads_pct"] < 90:
        warnings.append("only %.0f%% quads: deforming meshes want 90%%+" % r["quads_pct"])
    return {"summary": "%d faces, %.0f%% quads, %d tris, %d ngons, %d poles" % (r["faces"], r["quads_pct"], r["tris"], r["ngons"], r["poles"]),
            "ids": [node["id"]], "stats": r, "warnings": warnings}
