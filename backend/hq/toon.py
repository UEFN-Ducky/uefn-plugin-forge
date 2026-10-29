"""HQ toon: an inverted-hull outline mesh per object plus a cel ramp stored on its materials.

The hull is the same mesh pushed out along its normals with its faces flipped and a backface-culled dark
material, so only a rim shows behind the silhouette. It works in UEFN (M_Forge_Toon reads `material.toon`).
Renders: forge_render(style="toon") in backend/render.py + engine/ops/toon.py.
"""
import math

from ..kernel.mesh import Mesh
from . import op
from .groom import _add_object, _material, _node

_KEY = 1000  # welds verts closer than 0.001 cm so a hard-edged box grows as one shell, not six


def hull_mesh(mesh: Mesh, distance: float, mat: str) -> Mesh:
    """mesh pushed out distance along its welded vertex normals, faces flipped, all faces on material mat."""
    sums = {}
    for fi, face in enumerate(mesh.faces):
        n = mesh.face_normal(fi)
        for v in face:
            k = tuple(round(c * _KEY) for c in mesh.verts[v])
            s = sums.setdefault(k, [0.0, 0.0, 0.0])
            for i in range(3):
                s[i] += n[i]
    hull = mesh.copy()
    for v, p in enumerate(mesh.verts):
        s = sums[tuple(round(c * _KEY) for c in p)]
        length = math.sqrt(sum(c * c for c in s)) or 1.0
        hull.verts[v] = [p[i] + s[i] / length * distance for i in range(3)]
    hull.faces = [f[::-1] for f in mesh.faces]
    hull.uvs = [u[::-1] for u in mesh.uvs]
    hull.face_mat = [mat] * len(mesh.faces)
    hull.attrs.pop("nrm:source", None)
    return hull


@op("toon", "toon(ids, outline=0.6, color=[0.3,0.13,0.05], bands=3) outline hull + cel ramp; render with forge_render(style='toon')")
def toon(scene, p):
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [])
    outline = float(p.get("outline", 0.6))
    color = [float(c) for c in (p.get("color") or [0.3, 0.13, 0.05])][:3]
    bands = int(p.get("bands", 3))
    if not ids or outline <= 0 or not 2 <= bands <= 8 or len(color) != 3:
        raise ValueError("toon needs ids=[object ids], outline>0 (cm), bands 2-8 and color=[r,g,b] 0-1. Example: toon(ids=['o1'], outline=0.6)")
    scene.data["nodes"][:] = [n for n in scene.data["nodes"] if not (n.get("toon_hull") in ids)]  # re-running replaces
    ref = _material(scene, "ToonOutline", color, toon_outline=True, roughness=1.0, metallic=0.0)
    made, used = [], set()
    for ident in ids:
        node = _node(scene, ident, "toon")
        if node.get("toon_hull"):
            raise ValueError("%r is an outline hull already. Pass the object it outlines." % ident)
        mesh = scene.local_mesh(node)
        used.update(mesh.face_mat)
        scale = sum(abs(c) for c in node["transform"]["scale"]) / 3 or 1.0
        hull = _add_object(scene, (node.get("name") or ident) + "_outline", hull_mesh(mesh, outline / scale, ref))
        hull.update(parent=ident, toon_hull=ident, material=ref)
        if node.get("skin"):
            hull["skin"] = dict(node["skin"])  # the rim bends with the body
        made.append(hull["id"])
    for m in scene.data["materials"]:
        if m["id"] in used and m["id"] != ref:
            m["toon"] = {"bands": bands, "outline": color}
    return {"ids": made, "summary": "outlined %d object(s), %.2f cm, %d bands" % (len(made), outline, bands)}
