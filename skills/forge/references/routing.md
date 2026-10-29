---
description: Every Forge task mapped to its reference, section and first op, plus every forge_hq op grouped by feature. Load when SKILL.md doesn't name the reference you need.
---

# Routing: task → reference → first op

Load the reference with `skill_read_subskill("forge", "<file>")` (add `section=` for one heading).

| Task | Reference#section | First op |
|---|---|---|
| common asset in one call (crate, well, humanoid, tree, sword) | `recipes` | `forge_hq op=recipe` |
| everyday prop from primitives, scale, tri budgets, kits | `modeling_recipes#crate-with-recessed-panels` | `forge_add` |
| sci-fi crate, panels, vents, bolts | `hardsurface#ops` | `forge_hq op=hardsurface` |
| realistic human head, face, eyes, teeth, hair | `realistic_head` | `forge_hq op=head` |
| rifle, pistol, shotgun, SMG, sniper, melee, sci-fi gun | `weapons` | `forge_hq op=weapon` |
| whole car from a body style; one car part; swap parts | `vehicles` | `forge_hq op=car` / `car_part` / `car_swap` |
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

## forge_hq ops by feature

`forge_hq(op, id, params, look)`. Presets and recipes are one word; errors name the fix. `op="help"` lists ops, `params={op: "<name>"}` shows one.

- **mesh** `remesh sculpt sculpt_hd modifiers boolean hardsurface origin lods` → `hq_mesh`, `hardsurface`
- **assets** `head` → `realistic_head`; `weapon` → `weapons`; `car car_part car_swap` → `vehicles`
- **procedural** `procedural scatter gn sdf_blend drop` → `procedural`
- **topology/UV** `retopo topology uv` → `retopo_uv`
- **materials** `material bake light` → `hq_materials`, `baking`, `lighting`; stylized `paint_strokes paint_bake project_ref toon tufts cartoon_body cartoon_face` → `stylized`
- **reference** `blockout_from_refs ref_parts ref_compare` → `stylized`, `asset_qa`; **creature** `creature` → `creatures`
- **rig** `rig_template auto_weights pose clip face_shapes retarget bake_clip stress` → `hq_rig`
- **groom** `hair hair_cards cloth fur_shells fur_bake` → `hq_hair_cloth`
- **QA** `qa rig_check print_check` → `asset_qa`, `print3d`; **export** `export collision convert` → `hq_export`; **recipe** `recipe`
- `generate cleanup creature_from_prompt` call paid text-to-3D providers: only when the user asks for one.

## Native tools

`forge_add` (box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude), `forge_edit`, `forge_group`, `forge_boolean`, `forge_select` + `forge_mesh_op`, `forge_sculpt`, `forge_shape`, `forge_uv_project`, `forge_paint`, `forge_material_set`, `forge_check`, `forge_fix`. Args: `modeling_recipes`, `sculpt_shapes`, `paint_uv`.

## Rig and animate

Separate parts: `forge_rig(action="bind", bone=...)` per part. One organic mesh: `auto=true`. Hand-painted weights fix bends.

```json
forge_batch(steps=[
  {"tool": "forge_rig", "action": "template", "template": "biped", "ids": ["<all parts>"], "auto": true},
  {"tool": "forge_anim", "action": "generate", "kind": "walk"}
], look="three")
forge_render(clip="Walk", frames=[0, 8, 16, 24])
```

Generators: `idle walk wave` (biped), `sway` (chain), `spin bob swing open` (one bone). Game-ready skeletons and combat clips: `hq_rig`. Sculpting, blend paint and texture paint subdivide and unwrap by themselves; shape keys get a single-bone rig on send.
