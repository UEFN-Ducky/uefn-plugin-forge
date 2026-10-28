---
name: forge
description: Model, sculpt, paint, rig, animate and groom high-quality assets in Ducky Forge (Blender engine built in, no Blender for the user) and ship them to UEFN, UE5, Unity or Godot. A recipe or one forge_batch builds a whole asset and shows it with look.
metadata:
  label: Ducky Forge
  version: 4
---

# Ducky Forge

You and the user edit the **same scene**; every change shows in the dock. Never send the user to Blender.

## Cheapest path

1. A recipe fits: `forge_hq(op="recipe", params={recipe: "humanoid_base", height: 180, hair: "ponytail"}, look="three")`. 16 recipes (crate, well, humanoid, robot, creature, tree, sword...); `recipe=""` lists them. Load `recipes`.
2. Otherwise one `forge_batch(steps=[...], look="three")` for the whole asset: native ops and `forge_hq` ops mix in a batch.
3. Read `seen` (bounds, tris, issues) and fix what it names. Look at the image only for shape and colour.
4. UEFN: `forge_send_to_uefn(place=true)`. Other engines: `forge_hq(op="export", params={target: "ue5"|"unity"|"godot"})`.

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

## forge_hq ops

`forge_hq(op, id, params, look)`. Presets and recipes are one word; errors name the fix.

- **mesh** `remesh retopo sculpt_hd modifiers lods` → `hq_mesh`
- **materials** `material` (40 presets: worn_steel, oak, granite, skin_tan, neon_blue, ...), `bake` → `hq_materials`
- **rig** `rig_template auto_weights pose clip face_shapes` → `hq_rig`
- **groom** `hair hair_cards cloth fur_shells` → `hq_hair_cloth`
- **export** `export`; **recipe** `recipe`. `op="help"` lists ops, `params={op: "<name>"}` shows one.

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

- Check a result with `look` before claiming it; check a clip at several frames.
- Mesh ops, UVs and modifiers act on the selected object: `forge_select` first. Selection is shared with the user.
- Join and boolean make a new object without the old binding or weights: bind after them.
- Locked objects and layers refuse edits. Keep props under about 20k triangles (`forge_check`, `lods`).
- Sculpting, blend paint and texture paint subdivide and unwrap by themselves; shape keys get a single-bone rig on send. Don't do these by hand.

## Reference files

Load with `skill_read_subskill("forge", "<id>")`: `recipes`, `hq_mesh`, `hq_materials`, `hq_rig`, `hq_hair_cloth`, `hq_export`, `modeling_recipes`, `sculpt_shapes`, `paint_uv`, `rigging`, `animation`, `camera` (views, shots, MP4), `send_to_game` (UEFN modes, placement, collision), `import_mesh`.
