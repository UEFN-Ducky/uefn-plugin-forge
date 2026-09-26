---
name: forge
description: Model UEFN props inside Ducky Forge (no Blender). Build with forge_* tools, watch the dock update live, check and fix, then send SM_ meshes and MI_ materials into UEFN.
---

# Ducky Forge

Forge is a modeler in the UEFN-Ducky panel. You and the user edit the **same scene**. Every `forge_*` change shows up in the Forge dock within a quarter second. Do the work with the tools. Do not tell the user to open Blender.

Sizes are **centimetres**, Z-up. A player is about 190 cm. Walls and floors tile at 512 cm. A door is about 250 cm tall.

## Loop

1. `forge_scene_list`. Reuse a scene or `forge_scene_new`.
2. `forge_add` primitives (box, cylinder, sphere, torus, wedge, stairs, arch, lathe, extrude). Put the pivot on the ground: primitives already sit on z = 0.
3. `forge_select` with `query="top"` (or pass face ids) before extrude, inset, or bevel.
4. `forge_mesh_op` for extrude, inset, bevel, loopcut, subdivide, fill, merge, delete, normals, origin.
5. `forge_material_set` then `forge_material_assign`. `forge_texture_generate` for a tileable albedo plus normal and ORM. That uses the saved OpenAI or Google key.
6. `forge_render` and look at the image. `forge_check`, then `forge_fix`.
7. `forge_send_to_uefn`. This needs the editor open. It imports `SM_*`, `T_*` textures (sRGB color, normal map, linear ORM), and `MI_*` instances of `M_Forge_Master`.

## Names

- Meshes export as `SM_<Name>`.
- Materials in the scene can be anything; instances become `MI_SM_<Name>_<Material>`.
- Put blocker meshes on the `collision` layer (`forge_edit` `layer="collision"`). Send still builds simple convex collision. The collision layer is there so a person can see the blocker in the outliner.

## Groups

`forge_group` parents pieces. `forge_send_to_uefn(mode="group")` bakes each group into one mesh. `mode="each"` is one mesh per object. `mode="all"` is one mesh.

`forge_join` bakes immediately. `forge_boolean` is difference / union / intersect and only works on convex pieces (box, sphere, cylinder, cone, capsule, wedge).

## Selection is shared

`forge_scene_get` returns the user's current selection. "Bevel the faces I selected" means `forge_mesh_op` with no face list, which uses that selection.

## What this is not

No sculpting, no rig, no real UV unwrap (use `forge_uv_project`: box, planar, or cylinder). Booleans are for props, not dense meshes. If a check reports more than about 20k triangles, simplify before sending.
