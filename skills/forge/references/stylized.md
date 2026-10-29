---
description: "Forge stylized characters: a chibi cartoon animal in 3 forge_batch calls (cartoon_body, cartoon_face, tufts, paint_bake, toon), plus paint_strokes hand-painted shading and ref_parts colouring from a drawing"
metadata:
  label: "Stylized characters"
  default_enabled: false
  load_condition: "Making a cartoon, chibi or toon character or animal, hand-painting strokes onto a model, or colouring a blockout from a reference drawing"
---

# Stylized characters (cartoon lion)

A chibi cartoon animal in **3 `forge_batch` calls**, no reference pictures needed: `cartoon_body` (sitting body, legs, toed paws, haunches, tail), a head ball with `cartoon_face` (glossy eyes, ears in front of the mane), `tufts` (leaf mane with a forehead peak, tail tuft), `paint_bake` (colour bands, crevices, dashes in ONE texture) and `toon`. Verified by `backend/test_cartoon_lion.py` (22k tris, tris must stay 20k-45k, each op result under 300 chars). `project_ref` and `blockout_from_refs` are optional extras for matching a drawing, not part of this path.

Coordinates are cm, Z up, front = -Y. `forge_add` pos is the bottom centre of a primitive.

## Call 1: the body

`cartoon_body` returns `head@(x,y,z)` and `tail_tip@(x,y,z)` in its summary: read them for call 2. Kinds: lion, cat, dog, bear. Poses: sit, stand.

```json
{"name": "CartoonLion", "steps": [
 {"tool": "forge_hq", "op": "cartoon_body", "params": {"kind": "lion", "pose": "sit", "height": 60, "name": "Lion"}}
]}
```

## Call 2: head, face, mane, tail tuft

The head ball goes at the head socket (pos z = head z - radius, radius ~0.22 x height). Order matters: `cartoon_face` first on the untouched ball, then the mane. `ears_forward` puts the ears inside the mane shell.

```json
{"name": "CartoonLion", "steps": [
 {"tool": "forge_add", "kind": "sphere", "object_name": "Head", "pos": [0, -4.2, 36.2], "params": {"radius": 13, "seg": 32, "rings": 20}},
 {"tool": "forge_hq", "op": "cartoon_face", "id": "$0", "params": {"species": "cat", "style": "cute", "ears_forward": true}},
 {"tool": "forge_hq", "op": "tufts", "id": "$0", "params": {"mane": "lion", "length": 13, "width": 9}},
 {"tool": "forge_hq", "op": "tufts", "id": "<body>", "params": {"target": "tail", "at": [12, 30, 24]}}
]}
```

## Call 3: paint, outline, render

`paint_bake` paints every material slot, joins the parts and bakes one texture and one material. `presets` maps a material name to fur_warm | mane | cream | pink. Leave the face object (`Head_face`, glossy eyes) out of `ids` so its eyes keep their own materials. Use `size` 512 for a quick pass: 2048 takes about 5 minutes.

```json
{"name": "CartoonLion", "steps": [
 {"tool": "forge_hq", "op": "paint_bake", "params": {"ids": ["<body>", "<head>", "<ears>"], "presets": {"body": "fur_warm", "Default": "mane", "belly": "cream", "paw": "cream", "EarInner": "pink"}, "name": "Lion", "size": 2048}},
 {"tool": "forge_hq", "op": "toon", "params": {"ids": ["$0"]}}
], "look": {"view": "front", "style": "toon"}}
```

Read the object ids from the scene (`Lion`, `Head`, `Head_ears`, `Head_face`).

## paint_strokes: hand-painted shading on one object

AO shading plus tapered brush strokes along a flow, written as BaseColor + Normal on the object's own material. Colours are `#rrggbb` or 0-1 lists. Flows: down (fur, cloth), normal, radial (spheres, flowers), tufts (manes).

```json
{"tool": "forge_hq", "op": "paint_strokes", "params": {"id": "Lion", "base": "#d9a04a", "shadow": "#8a5226", "highlight": "#f6d58f", "flow": "tufts", "density": 1, "ao": 0.6, "rim": 0.3, "size": 1024}}
```

Done when: the summary says `N strokes, 80%+ covered` and `look` shows strokes, not flat colour. Low cover: raise `density` or `width`.

## ref_parts: colour a blockout from a drawing

Clusters the front drawing into `k` colours and gives each face the colour behind it (front view, X-Z). `palette` pins named colours; `split=true` makes one object per part.

```json
{"tool": "forge_hq", "op": "ref_parts", "params": {"id": "Lion", "front": "C:/refs/lion_front.png", "k": 5, "palette": [{"name": "fur", "color": "#d9a04a"}, {"name": "mane", "color": "#8a4b1f"}], "split": false}}
```

Done when: the summary lists each part with its face count (`5 materials: fur 812, mane 404, ...`) and `look view=front` matches the drawing.

## Costs and limits

| Step | Time | Tris |
|---|---|---|
| Call 1 (cartoon_body) | ~3 s | 16k |
| Call 2 (head, face, mane, tail tuft) | ~10 s | +6k |
| Call 3 (paint_bake 2048, toon, look) | ~5 min (512 px: ~10 s) | outline hulls not counted |

- `forge_render` has no back view: group everything, `forge_edit rot=[0,0,180]`, render `front`, rotate back.
- The mane and the head ball share one material, so the face and the mane get the same paint preset.
- Without `ears_forward` the ears sit on the ball and the mane hides them.
