# Stylized characters from reference art (cartoon lion)

A chibi cartoon character from 3 reference pictures in **2 `forge_batch` calls** (about 25 s, ~19k tris, each result under 300 chars). Verified by `backend/test_cartoon_lion.py` (front silhouette IoU 0.8 against the drawing).

Prepare first (no Forge call): cut plain PNGs from the references, with the subject on a white page.
- `face.png`: the head disc only, everything outside it white (it becomes the head blockout).
- `body_front.png`, `body_back.png`: the body below the chin. Crop out the mane and the tail: the silhouette is mirrored left/right, so a tail or one-sided part would be doubled.
- `head_front.png`, `head_back.png`: the whole mane + face (project_ref maps the picture onto the object's bounds).

Coordinates are cm, Z up, front = -Y. 1 reference pixel ~ 0.12 cm when the character is 100 cm tall.

## Call 1: shapes

`blockout_from_refs` traces a silhouette into a closed body (`res` = grid height: 18 for a head ball, 40 for a body, `smooth` rounds the steps). `forge_duplicate` makes the mane host. The tail is a chain of overlapping spheres joined into one object.

```json
{"name": "CartoonLion", "steps": [
 {"tool": "forge_hq", "op": "blockout_from_refs", "params": {"front": "C:/tmp/face.png", "height": 40, "name": "Head", "res": 18, "smooth": 6}},
 {"tool": "forge_hq", "op": "blockout_from_refs", "params": {"front": "C:/tmp/body_front.png", "height": 26, "name": "Body", "res": 40, "smooth": 8}},
 {"tool": "forge_edit", "id": "$0", "pos": [-9, 0, 41]},
 {"tool": "forge_edit", "id": "$1", "pos": [0, 0, 0], "scale": [0.9, 0.6, 1]},
 {"tool": "forge_duplicate", "id": "$0", "offset": [0, 7, 0]},
 {"tool": "forge_edit", "id": "$4", "object_name": "Mane"},
 {"tool": "forge_add", "kind": "sphere", "object_name": "Tail0", "pos": [26, 16, 10], "params": {"radius": 2.8, "seg": 10, "rings": 6}},
 {"tool": "forge_add", "kind": "sphere", "object_name": "Tail1", "pos": [30, 16, 14], "params": {"radius": 2.7, "seg": 10, "rings": 6}},
 {"tool": "forge_join", "ids": ["$6", "$7"], "weld": true}
]}
```

Add a sphere every ~2 cm along the curve (20-25 spheres), radius thinning 2.8 -> 1.8. Read the new ids from the scene: `Head`, `Mane`, `Body`, `Tail0`.

## Call 2: the look

Order matters. The face goes on the untouched head ball: tufts voxel-remeshes its host and would bury the 0.1-0.3 cm face parts. The mane grows on the duplicate, 7 cm behind the face, so the face ball shows in front of it.

```json
{"name": "CartoonLion", "steps": [
 {"tool": "forge_hq", "op": "retopo", "id": "<body>", "params": {"faces": 2800}},
 {"tool": "forge_hq", "op": "ref_parts", "id": "<body>", "params": {"front": "C:/tmp/body_front.png", "k": 4}},
 {"tool": "forge_hq", "op": "cartoon_face", "id": "<head>", "params": {"species": "cat", "style": "cute", "eye_size": 0.2}},
 {"tool": "forge_hq", "op": "tufts", "id": "<mane>", "params": {"region": [{"ring": [0, 7, 61, 15, 10]}, {"sphere": [0, 22, 61, 20]}], "length": 22, "width": 12, "count": 190, "layers": 2, "sweep": 0.4, "budget": 3800, "curl": 0.3, "smooth": 6}},
 {"tool": "forge_hq", "op": "tufts", "id": "<mane>", "params": {"region": {"sphere": [0, 5, 45, 15]}, "length": 21, "width": 12, "count": 60, "layers": 2, "sweep": 0.7, "budget": 4300, "curl": 0.2, "seed": 3}},
 {"tool": "forge_hq", "op": "tufts", "id": "<tail>", "params": {"region": {"sphere": [30, 16, 45, 4.5]}, "length": 6, "width": 4, "count": 50, "budget": 1800}},
 {"tool": "forge_uv_project", "mode": "smart", "id": "<head>"},
 {"tool": "forge_uv_project", "mode": "smart", "id": "<mane>"},
 {"tool": "forge_uv_project", "mode": "smart", "id": "<body>"},
 {"tool": "forge_uv_project", "mode": "smart", "id": "<tail>"},
 {"tool": "forge_hq", "op": "paint_strokes", "id": "<head>", "params": {"base": [0.98, 0.72, 0.27], "shadow": [0.8, 0.5, 0.15], "highlight": [1.0, 0.88, 0.5], "flow": "normal", "size": 1024}},
 {"tool": "forge_hq", "op": "project_ref", "id": "<mane>", "params": {"images": {"front": "C:/tmp/head_front.png", "back": "C:/tmp/head_back.png"}, "size": 1024}},
 {"tool": "forge_hq", "op": "project_ref", "id": "<body>", "params": {"images": {"front": "C:/tmp/body_front.png", "back": "C:/tmp/body_back.png"}, "size": 1024}},
 {"tool": "forge_hq", "op": "paint_strokes", "id": "<tail>", "params": {"base": [0.95, 0.65, 0.2], "shadow": [0.7, 0.4, 0.1], "highlight": [1.0, 0.85, 0.4], "flow": "down", "size": 512}},
 {"tool": "forge_hq", "op": "toon", "params": {"ids": ["<head>", "<mane>", "<body>", "<tail>"]}}
], "look": {"view": "front", "style": "toon"}}
```

Tuft regions are in world cm around the head ball centre (x, y of the mane host, z of the head centre) with r = head radius: a rim ring `[x, y, z, 0.7r, 0.45r]`, the back `{"sphere": [x, y + 0.7r, z, 0.95r]}`, a chest bib `{"sphere": [x, y - 0.1r, z - 0.75r, 0.7r]}`. Fewer, wider blades read as flame tufts; many thin ones read as scraps.

## Costs and limits

| Step | Time | Tris |
|---|---|---|
| Call 1 (blockouts, tail) | ~3 s | 3k head, 5k body |
| Call 2 (retopo, face, 3 tufts, UV, paint, toon) | ~15 s | +4k mane, +4k face and ears, 1.7k tail |
| Render (`look`, 768 px, toon) | ~2 s | outline hulls not counted |

- `forge_render` has no back view: group everything, `forge_edit rot=[0,0,180]`, render `front`, rotate back.
- The result of a batch with ~20 sphere ids is over 300 chars; keep it to the second call's size by using fewer spheres.
- Known rough spots: faint UV tile seams on painted skin, pixelated fill under the body where no picture sees it.
