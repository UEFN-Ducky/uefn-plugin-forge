---
name: forge
description: Model, sculpt, paint, rig, animate and groom high-quality assets in Ducky Forge (Blender engine built in, no Blender for the user) and ship them to UEFN, UE5, Unity, Godot, Roblox, the web or a 3D printer. Routes hard-surface, procedural, sculpt, retopo, UV, PBR, baking, lighting, rigging, animation, QA and export to one reference each. A recipe or one forge_batch builds a whole asset and shows it with look.
metadata:
  label: Ducky Forge
  version: 5
---

# Ducky Forge

You and the user edit the **same scene**; every change shows in the dock. Never send the user to Blender.

## Cheapest path

0. One line of intent first: what the player should see, from how far, and the budget (`qa` budgets: prop, hero, char, weapon, vehicle, module, mobile).
1. A recipe fits: `forge_hq(op="recipe", params={recipe: "humanoid_base", height: 180, hair: "ponytail"}, look="three")`. 16 recipes (crate, well, humanoid, robot, creature, tree, sword...); `recipe=""` lists them. Load `recipes`.
2. Otherwise one `forge_batch(steps=[...], look="three")` for the whole asset: native ops and `forge_hq` ops mix in a batch.
3. End the batch with `{"tool": "forge_hq", "op": "qa", "params": {"profile": "uefn", "fix": true}}`. Read `seen` and the `qa` score and fix what they name. Look at the image only for silhouette, proportions and colour.
4. UEFN: `forge_send_to_uefn(place=true)`. Other engines: `forge_hq(op="export", params={target: "ue5"|"unity"|"godot"|"roblox"|"web", verify: true})`.

`look="three"|"ai"|<ids>` on any mutating call or batch adds one tiled picture (at most 768 px) and the `seen` line. Results stay under 300 chars (`ok, summary, ids, stats, warnings, path`); `verbose=true` returns all. `name` may be left out: the last scene is reused.

## Units and axes

- Centimetres, Z-up. Player about 190 cm; tiles 512 cm; wall 384 cm tall; door about 250 x 120 cm.
- Primitives sit on z = 0 centred on X and Y; `pos` is the base centre. Props face -Y. Characters face -Y with `_l` bones on +X. UEFN mirrors this on arrival: the asset looks the same.
- Rotations are degrees, X then Y then Z.

## Batch

Each step is a tool's own arguments plus `"tool"`. `"$N"` is step N's id, `"$N.ids"` its id list. One undo step; a failing step undoes all of it.

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "material", "params": {"material": "Crate", "preset": "worn_steel"}},
  {"tool": "forge_add", "kind": "box", "object_name": "Crate", "params": {"w": 100, "d": 100, "h": 100}, "material": "$0"},
  {"tool": "forge_select", "object": "$1", "query": "all"},
  {"tool": "forge_mesh_op", "op": "inset", "amount": 10},
  {"tool": "forge_mesh_op", "op": "extrude", "distance": -4},
  {"tool": "forge_uv_project", "mode": "box"}
], look="three")
```

## Route: task → reference → first op

Load the reference with `skill_read_subskill("forge", "<file>")` (add `section=` for one heading).

| Task | Reference#section | First op |
|---|---|---|
| common asset in one call (crate, well, humanoid, tree, sword) | `recipes` | `forge_hq op=recipe` |
| everyday prop from primitives, scale, tri budgets, kits | `modeling_recipes#crate-with-recessed-panels` | `forge_add` |
| sci-fi crate, weapon, vehicle panels, vents, bolts | `hardsurface#ops` | `forge_hq op=hardsurface` |
| bevel / weighted-normal stack, booleans, origin, LODs | `hq_mesh` | `forge_hq op=modifiers` |
| buildings, wall/floor kits, stairs, fences, pipes, cables | `procedural#ops` | `forge_hq op=procedural` |
| scatter, geometry-nodes graph, smooth unions, drop clutter | `procedural#gn-spec` | `forge_hq op=scatter` |
| sculpt brushes, sculpt chain, shape keys | `sculpt_shapes#sculpt-chain-one-call` | `forge_sculpt` / `forge_hq op=sculpt` |
| pore / scale / wood detail for a bake | `hq_mesh` | `forge_hq op=sculpt_hd` |
| clean low-poly from a dense or AI mesh, topology check | `retopo_uv#retopo-and-topology` | `forge_hq op=retopo` |
| seams, unwrap, texel density, lightmap UVs | `retopo_uv#uv` | `forge_hq op=uv` |
| quick tiling UVs, texture paint, blend materials | `paint_uv` | `forge_uv_project` |
| PBR preset, texture folder, wear, wet, snow, PBR lint | `hq_materials#presets` | `forge_hq op=material` |
| high→low normal, AO, curvature, ID maps | `baking#maps` | `forge_hq op=bake` |
| studio / product / outdoor light for judging a look | `lighting#presets` | `forge_hq op=light` |
| chibi cartoon animal, hand-painted bands, toon outline | `stylized` | `forge_hq op=cartoon_body` |
| realistic walking quadruped (lion, dog, horse) | `creatures` | `forge_hq op=creature` |
| match a drawing: blockout, colours, silhouette score | `stylized` / `asset_qa#ref_compareid-front-side-min_iou085` | `forge_hq op=blockout_from_refs` |
| hair, fur, cards, cloth | `hq_hair_cloth` | `forge_hq op=hair` |
| bones for parts, doors, turrets; painted weights; mechanical rigs | `rigging#templates` | `forge_rig` |
| UE5 / quadruped skeleton, weights, IK pose, combat clips, retarget | `hq_rig` | `forge_hq op=rig_template` |
| keys, generators, walk cycle, timing, loops | `animation` | `forge_anim` |
| grade a mesh or skeleton before done (any engine, budgets) | `asset_qa#qaid-profile-budget-fix-eps-tol` | `forge_hq op=qa` |
| 3D print: watertight, walls, overhangs, STL | `print3d` | `forge_hq op=print_check` |
| UE5, Unity, Godot, Roblox, web, glTF, OBJ, USDZ; UCX collision; convert | `hq_export` | `forge_hq op=export` |
| into a UEFN island: send modes, placing, collision | `send_to_game#modes-static-meshes` | `forge_send_to_uefn` |
| bring in a .blend / .fbx / .glb | `import_mesh` | `forge_import_mesh` |
| camera, saved views, keyed shots, MP4 | `camera` | `forge_camera` |

## forge_hq ops

`forge_hq(op, id, params, look)`. Presets and recipes are one word; errors name the fix. `op="help"` lists ops, `params={op: "<name>"}` shows one.

- **mesh** `remesh sculpt sculpt_hd modifiers boolean hardsurface origin lods` → `hq_mesh`, `hardsurface`
- **procedural** `procedural scatter gn sdf_blend drop` → `procedural`
- **topology/UV** `retopo topology uv` → `retopo_uv`
- **materials** `material bake light` → `hq_materials`, `baking`, `lighting`; stylized `paint_strokes paint_bake project_ref toon tufts cartoon_body cartoon_face` → `stylized`
- **reference** `blockout_from_refs ref_parts ref_compare` → `stylized`, `asset_qa`; **creature** `creature` → `creatures`
- **rig** `rig_template auto_weights pose clip face_shapes retarget bake_clip stress` → `hq_rig`
- **groom** `hair hair_cards cloth fur_shells fur_bake` → `hq_hair_cloth`
- **QA** `qa rig_check print_check` → `asset_qa`, `print3d`; **export** `export collision convert` → `hq_export`; **recipe** `recipe`
- `generate cleanup creature_from_prompt` call paid text-to-3D providers: only when the user asks for one.

## Native tools

`forge_add` (box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude), `forge_edit`, `forge_group`, `forge_boolean`, `forge_select` + `forge_mesh_op`, `forge_sculpt`, `forge_shape`, `forge_uv_project`, `forge_paint`, `forge_material_set`, `forge_check`, `forge_fix`. Args live in the references: `modeling_recipes`, `sculpt_shapes`, `paint_uv`.

## Rig and animate

Anything that moves in game needs a rig. Separate parts: `forge_rig(action="bind", bone=...)` per part. One organic mesh: `auto=true`. Hand-painted weights fix bends.

```json
forge_batch(steps=[
  {"tool": "forge_rig", "action": "template", "template": "biped", "ids": ["<all parts>"], "auto": true},
  {"tool": "forge_anim", "action": "generate", "kind": "walk"}
], look="three")
forge_render(clip="Walk", frames=[0, 8, 16, 24])
```

Generators: `idle walk wave` (biped), `sway` (chain), `spin bob swing open` (one bone). Game-ready skeletons and combat clips: `hq_rig`. Details: `rigging`, `animation`.

## Hard rules

- Check a result with `look` and `qa` before claiming it; check a clip at several frames. After each look, check the silhouette, proportions, floating parts, shading on hard edges and default grey slots, then fix the worst one. At most 3-5 passes.
- Mesh ops, UVs and modifiers act on the selected object: `forge_select` first. Selection is shared with the user.
- Join and boolean make a new object without the old binding or weights: bind after them.
- Locked objects and layers refuse edits. Keep props under about 20k triangles (`forge_check`, `lods`).
- Sculpting, blend paint and texture paint subdivide and unwrap by themselves; shape keys get a single-bone rig on send. Don't do these by hand.

## Reference files

Load with `skill_read_subskill("forge", "<id>")`: `recipes`, `modeling_recipes`, `hardsurface`, `hq_mesh`, `procedural`, `sculpt_shapes`, `retopo_uv`, `paint_uv`, `hq_materials`, `baking`, `lighting`, `stylized`, `creatures`, `hq_hair_cloth`, `rigging`, `hq_rig`, `animation`, `asset_qa`, `print3d`, `hq_export`, `send_to_game`, `import_mesh`, `camera`.

Forge embeds bpy 5.2; it is not a Blender add-on. Op authors: 5.x props are attributes, GN inputs are `mod.properties.inputs`, and nodes are found by `type`, not by name.
