---
description: "Forge asset QA: one qa call grades a mesh for UEFN, Unreal, Roblox, render or print (doubles, n-gons, inward normals, z-fighting, floating, UV overlap, influences, scale, pivot, .001 names, tri and material budgets) and fixes the safe ones; rig_check grades a skeleton; ref_compare checks the silhouette against drawings"
metadata:
  label: "Asset QA"
  default_enabled: false
  load_condition: "Checking that a Forge asset is game-ready before export or send, grading a rig, comparing a model against reference drawings, or picking a polycount budget"
---

# Asset QA

`qa` is the Done-when of every build: run it last in the batch, read the score, fix, run again.

```json
forge_batch(name="Crate", steps=[
  {"tool": "forge_hq", "op": "qa", "params": {"profile": "uefn", "budget": "prop", "fix": true}},
  {"tool": "forge_hq", "op": "rig_check", "params": {"profile": "uefn"}}
], look="three")
```

Result: `uefn score 92: Crate 2 n-gons (fix: fix); Crate pivot not at base (fix: origin=base)`. `pass` is true when nothing is an error. Score = 100 - 10 per error - 3 per warning.

## qa(id?, profile, budget?, fix, eps, tol)

No `id` = every object off the collision layer. `UCX_` hulls are never graded.

| profile | Tri cap per mesh | Influences | Extra |
|---|---|---|---|
| `uefn` (default) | 20k | 4 | pivot on the base |
| `game` | - | 4 | |
| `ue5` | - | 8 | |
| `roblox` | 20k, 10k verts | 4 | n-gons are errors |
| `render` | - | - | no pivot, UV or ground checks |
| `print` | - | - | open edges are errors (then `print_check`, skill `print3d`) |

| budget | Tris | Materials |
|---|---|---|
| `prop` | 3k | 2 |
| `module` | 5k | 1 |
| `mobile` | 10k | 2 |
| `weapon` | 15k | 3 |
| `vehicle` | 50k | 5 |
| `char` | 60k | 8 |
| `hero` | 100k | 5 |

Checks: open and non-manifold edges, zero-area faces, doubles (0.05 cm), loose verts, n-gons, missing UVs, UV overlap over 5 % (only on textured materials), inward normals (closed meshes), z-fighting face pairs (centres within `eps` cm), floating more than `tol` cm above z=0, negative or non-uniform scale, more influences than the profile allows, `.001` names, tri and material budget.

`fix=true` runs the safe fixes and grades again: merge doubles, drop zero-area faces and loose verts, flip inward normals, triangulate n-gons, box-UV faces without UVs, move the pivot to the base (the object stays put), strip `.001`. Not auto-fixed: z-fighting, floating, open edges, budgets (`lods` / `retopo`), influences (`auto_weights target=`).

## rig_check(rig?, profile=uefn|ue5|unity|roblox)

Bone names (UE5 Manny for uefn/ue5/unity; R15 `LowerTorso`… for roblox), one root, `_l`/`_r` mirrored within 1 cm, painted skins: unweighted verts and influences over the limit, leaf bones that weigh nothing, bone scale keys, loop seams over 1 degree. Fixes it names: `auto_weights`, `bake_clip`, `rig_template ue5_manny`.

## ref_compare(id?, front, side?, min_iou=0.85)

Projects the mesh (front = X across, side = -Y across) and compares it with the drawing's silhouette (alpha, or the colour away from the border): IoU, 10 height bands within 5 %, width/height drift over 3 %. Under `min_iou` is an error. Pair it with `blockout_from_refs`: build, compare, reshape, compare.

## Looks for review

`look` is a view name or `{"view", "style", "px"}` on any forge tool or `forge_batch`. Every look's `seen` line ends with `fill N%` (object pixels in the frame).

| look | Gives |
|---|---|
| `"three"` (default), `"front"`, `"side"`, `"top"`, `"back"`, `"left"`, `"three_back"` | one view |
| `"ai"` | three + front + side + top tiled |
| `"around"` | front, side, back, left tiled |
| `"turntable"` | 8 stills az0, az45 … az315 tiled |

| style | Shows | Use for |
|---|---|---|
| `wire` | hidden-line edges, real edges only | topology, edge flow, n-gons |
| `islands` | each UV island its own colour | seams, island count |
| `silhouette` | flat white shape | reading at 128 px, `ref_compare` by eye |
| `checker` | checker through the UVs, red UV origin square | stretch, texel density, flipped islands |
| `xray` | see-through body, hidden edges faint | inside faces, overlapping parts |

`px` = long side 128..1024 (default 768). `{"look": "turntable"}` → 8 stills. `{"look": {"view": "three", "style": "silhouette", "px": 128}}` → the gameplay-size read. QA styles skip lighting, so they are instant.

## Checklist after each look

Silhouette reads at 128 px; pivot on the floor; front faces -Y; nothing floats or sinks; no flicker where parts overlap; budget fits the class; `qa` passes.

## Done when

`qa` returns `pass: true` with the asset's profile and budget, and the batch `look` shows the silhouette you meant.

Adapted from Blender Agent Skills by EL4CTEO (asset-qa, optimization; MIT), majidmanzarpour/blender-game-skills (silhouette IoU gate, tri budgets; MIT), arjun988/blender-skills (QA list; MIT) and HoldMyBeer-gg/blenderwright (mesh quality; MIT).
