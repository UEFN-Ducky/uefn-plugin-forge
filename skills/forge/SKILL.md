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

## Route

The full task → reference → first op table, every `forge_hq` op and rig/animate basics: `routing`.

- Realistic head and face: `realistic_head` (`forge_hq op=head`). Guns and melee: `weapons` (`op=weapon`). Cars and car parts: `vehicles` (`op=car`, `car_part`, `car_swap`).
- One-call assets: `recipes`. Panels: `hardsurface`. Quadrupeds: `creatures`. Cartoon: `stylized`. Anything that moves needs a rig: `rigging`, `hq_rig`, `animation`.
- `op="help"` lists `forge_hq` ops, `params={op: "<name>"}` shows one. `generate cleanup creature_from_prompt` call paid text-to-3D providers: only when the user asks.
- Native tools (`forge_add`, `forge_select` + `forge_mesh_op`, `forge_sculpt`, `forge_paint`, `forge_check`, `forge_fix`...): `modeling_recipes`, `sculpt_shapes`, `paint_uv`.

## Hard rules

- Check a result with `look` and `qa` before claiming it; check a clip at several frames. After each look fix the worst of: silhouette, proportions, floating parts, hard-edge shading, grey slots. At most 3-5 passes.
- Mesh ops, UVs and modifiers act on the selected object: `forge_select` first. Selection is shared with the user.
- Join and boolean drop binding and weights: bind after them. Locked objects refuse edits. Props under about 20k triangles.

## Reference files

Load with `skill_read_subskill("forge", "<id>")` (add `section=` for one heading): `routing`, `recipes`, `realistic_head`, `weapons`, `vehicles`, `modeling_recipes`, `hardsurface`, `hq_mesh`, `procedural`, `sculpt_shapes`, `retopo_uv`, `paint_uv`, `hq_materials`, `baking`, `lighting`, `stylized`, `creatures`, `hq_hair_cloth`, `rigging`, `hq_rig`, `animation`, `asset_qa`, `print3d`, `hq_export`, `send_to_game`, `import_mesh`, `camera`.

Forge embeds bpy 5.2, not a Blender add-on: 5.x props are attributes, GN inputs are `mod.properties.inputs`, nodes are found by `type`.
