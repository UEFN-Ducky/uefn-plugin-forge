---
name: forge
description: Model, sculpt, paint, rig, and animate UEFN assets inside Ducky Forge (no Blender), side by side with the user in the dock. forge_batch builds a whole prop in one call; forge_sculpt, forge_shape, forge_uv_project, and forge_paint add organic form, shape keys, real UVs, textures, and blend-material masks; forge_rig and forge_anim add skeletons, painted weights, IK, and motion; forge_send_to_uefn lands SM_/SK_ meshes, AS_ clips, and MI_ materials, optionally placed in the level.
metadata:
  label: Ducky Forge
  version: 3
---

# Ducky Forge

Forge is a modeler, sculptor, painter, rigger, and animator in the UEFN-Ducky panel. You and the user edit the **same scene**; every `forge_*` change shows in the dock within a quarter second. The user can do everything you can from the dock: sculpt, paint textures, blend masks, and weights, pose with IK, and key shape keys. Each of their strokes is one undo step, like each of your calls. Do the work with the tools. Do not tell the user to open Blender.

## Import reference buildings

Use `forge_import_mesh(path, name="", destination="", scale=1, preview=false)` for
local .blend, binary .fbx, .glb or .gltf files. Requires a local Blender executable
(PATH, standard Windows install, or FORGE_BLENDER); no Blender install is bundled.
`preview=true` reports centimetre dimensions, triangle/object/material counts and
warnings without writing a scene. `destination` merges into an existing scene in
one undo step; otherwise a unique new scene is created, never overwritten.
The File panel and `forge.import_mesh` workflow node use the same importer.
The panel adds Browse, Inspect, destination, scale, progress and Cancel controls.
Cancellation stops conversion before commit; a completed commit remains undoable.

Static meshes, hierarchy, centimetre/Z-up conversion, corner normals, vertex colors,
UVs and per-face material slots are preserved. Base-color images connected directly
or multiplied by vertex color are supported. Collision helper meshes and alternate
LODs are retained but hidden. Measurements exclude hidden meshes. ASCII FBX,
sheared transforms, remote/traversing/missing glTF sidecars and unsupported required
glTF extensions fail explicitly. File plus referenced resource size is limited to
512 MB; conversion times out at 15 minutes. Rigs, animation, procedural shaders and
other texture channels are not transferred. Inspect the warnings before treating
an imported material as equivalent to the original Unreal shader.
A pasted UEFN actor/T3D block is a reference, not mesh geometry: export its mesh to
a supported format first. Do not claim the whole tower was imported from one module.

## Units and axes

- Centimetres, Z-up. Player about 190 cm. Floor and wall tiles 512 cm; a wall is 384 cm tall. Door about 250 x 120 cm.
- Primitives sit on z = 0, centred on X and Y. `pos` is the base centre.
- The front view looks from -Y, so a prop's front faces -Y. Characters face -Y with their left side (`_l` bones) on +X.
- Assets land in UEFN looking exactly as they do in the dock. UEFN's axes are mirrored against the dock's, so a front facing -Y here faces +Y there, and a character stands like the UE mannequin: facing +Y, left side on +X.
- Rotations are degrees applied X, then Y, then Z.

## One call per idea: forge_batch

Whenever you would make more than two edits, send them as one `forge_batch`. Each step is a tool's own arguments plus `"tool"`. `"$N"` is step N's id and `"$N.ids"` its id list. The batch is one undo step, and the first failing step undoes all of it, so a batch never leaves a half-built prop.

```json
forge_batch(name="Crate", steps=[
  {"tool": "forge_material_set", "material": "Wood", "base_color": [0.52, 0.34, 0.18], "roughness": 0.85},
  {"tool": "forge_add", "kind": "box", "object_name": "Crate", "params": {"w": 100, "d": 100, "h": 100}, "material": "$0"},
  {"tool": "forge_select", "object": "$1", "query": "all"},
  {"tool": "forge_mesh_op", "op": "inset", "amount": 10},
  {"tool": "forge_mesh_op", "op": "extrude", "distance": -4},
  {"tool": "forge_uv_project", "mode": "box"}
])
```

Materials can be named by name or id anywhere a tool takes one.

## The loop

1. `forge_scene_list`, then reuse a scene or `forge_scene_new`. `forge_scene_merge` copies another scene in (props into a set, a sword into a character scene).
2. Block out with one batch: `forge_add` (box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude), `forge_edit`, `forge_group`.
3. Shape: `forge_select` (`query="top"` or `"all"`, or face ids), then `forge_mesh_op` (extrude, inset, bevel, loopcut, subdivide, fill, merge, dissolve, delete, normals, shade, origin). `forge_boolean` cuts or fuses any closed shapes, concave included; `b` can be a list.
4. Sculpt organic forms: `forge_mesh_op(op="subdivide", smooth=true, levels=3)`, then `forge_sculpt` (draw, clay, inflate, crease, smooth, flatten, pinch, grab, noise), then `op="decimate"` to budget. `forge_shape` adds shape keys.
5. Surface: `forge_material_set`, `forge_material_assign`, `forge_uv_project(mode="smart")`, then `forge_paint` for textures or blend-material masks, or `forge_texture_generate` for a tileable AI texture.
6. Look at it: `forge_render` (views three, front, side, top) shows materials, painted textures, and blend masks. Then `forge_check` and `forge_fix`.
7. If it moves, rig and animate it (below).
8. `forge_send_to_uefn(name, place=true)` puts it in the level.

## Rig and animate

Anything that moves in game needs a rig: characters, creatures, doors, chests, turrets, fans, plants, and faces with shape keys.

```json
forge_batch(name="Robot", steps=[
  {"tool": "forge_rig", "action": "template", "template": "biped", "ids": ["<all parts>"]},
  {"tool": "forge_rig", "action": "bind", "ids": ["<left upper arm part>"], "bone": "upperarm_l"},
  {"tool": "forge_rig", "action": "bind", "ids": ["<body mesh>"], "auto": true},
  {"tool": "forge_anim", "action": "generate", "kind": "walk"}
])
forge_render(name="Robot", clip="Walk", frames=[0, 8, 16, 24])
```

| The mesh is | Skin | Why |
|---|---|---|
| Separate parts (robot, blocky character, door, turret) | `bind` with `bone=` per part | Each part moves rigidly; no weight bleed |
| One organic mesh (tentacle, snake, body, plant) | `bind` with `auto=true`, optionally `bones=[...]` | Smooth bend across nearby bones |
| One mesh that bends wrong with auto | `forge_rig(action="weights", ...)` paints it | Exact control where joints need it |

- Motion: generators `idle`, `walk`, `wave` (biped); `sway` (a bone chain); `spin`, `bob`, `swing`, `open` (one bone). Hand keys: `forge_anim(action="key", keys=[{"bone": ..., "frame": ..., "rot": [x, y, z]}])`. Bones rotate about their head, so put the head on the hinge. Rotation keys are in the parent bone's frame.
- IK: `forge_anim(action="ik", bone="hand_l", target=[x, y, z], frame=10)` bends the arm so the hand lands there and keys it. `span=[0, 32]` pins a foot through a clip.
- Shape keys: key rows `{"shape": "Smile", "frame": 12, "value": 1}`.

Load `rigging` for bone layouts, binding, and painted weights, and `animation` for keys, IK, timing, and loops.

## Send to the game

`forge_send_to_uefn` needs UEFN open with a project. Statics become `SM_` meshes. Each rig becomes one `SK_` skeletal mesh with its skeleton, a physics asset, its shape keys as morph targets, and one `AS_` clip per animation. Materials become `MI_` instances on `M_Forge_Master`; blend materials use `M_Forge_Blend`, driven by the painted vertex colours. `place=true` spawns them 8 m in front of the editor camera (or at `location`); a rigged asset goes on an Animated Mesh device that plays its first clip. Send runs many editor calls, so don't run other editor tools in the same turn. Load `send_to_game` for modes, collision, and troubleshooting.

## Hard rules

- Render before you claim a result, and render a clip at several frames before you claim a motion.
- Selection is shared. "Bevel the faces I selected" means `forge_mesh_op` with no face list.
- Mesh ops, UVs, and modifiers act on the selected object: put a `forge_select` step first.
- Brushes move existing vertices and never add any: smooth-subdivide before sculpting or painting blend masks.
- Unwrap with `mode="smart"` before painting a texture; projected UVs overlap.
- Shape keys and clips reach UEFN only on a rig. A prop with shape keys needs `forge_rig(template="single")`.
- Join and boolean make a new object without the old rig binding or painted weights. Bind after them.
- Locked objects and layers refuse edits until unlocked.
- Keep props under about 20k triangles; `forge_check` reports it, and `decimate` fixes a heavy sculpt.
- Array modifiers and shared instances bake into one mesh only with Send mode `group` or `all`.

## What Forge leaves to Blender

Dynamic-topology and multiresolution sculpting (Forge sculpts the vertices the mesh has), retopology, baking high-poly detail into normal maps, painting maps other than base colour, live constraints (IK is solved into keys), and cloth, hair, or physics simulation. Booleans are fine for props but slow above a few thousand faces. For those, use Blender (skill `blender`) and import the result.

## Reference files

Load with `skill_read_subskill("forge", "<id>")`.

- `modeling_recipes`: batch recipes for common props, scale and triangle budgets, boolean and merge patterns, style routing.
- `sculpt_shapes`: resolution, every brush and its units, whole-mesh brushes, decimating, shape keys and how they ship.
- `paint_uv`: smart unwrap and projections, texture painting, blend materials and their masks, what lands in UEFN.
- `rigging`: the rig model, biped and chain templates, custom bones for doors, turrets, and lids, binding, painted weights, pitfalls.
- `animation`: clips and keys, every generator, IK, shape-key keys, timing and spacing, walk cycle, loops, checking motion.
- `send_to_game`: export modes, what lands where, placement, Animated Mesh playback, collision, validation, and failures.
