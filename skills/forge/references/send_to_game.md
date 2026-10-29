---
description: "Getting Forge assets into a UEFN island: Send modes, what lands where (SM_, SK_, AS_, MI_, T_, morph targets, M_Forge_Blend), placing in the level, Animated Mesh playback, collision, validation, and fixes for failed sends"
metadata:
  label: "Send to game"
  default_enabled: false
  load_condition: "Sending a Forge scene to UEFN, placing Forge assets in the level, or a send failed or looks wrong"
---

# Send to the game

`forge_send_to_uefn(name, mode="each", place=false, location=None)` needs UEFN open with an island project. It runs many editor calls in order, so don't run other editor tools in the same turn. It returns every asset path, validator output, and, with `place`, what it spawned.

## Modes (static meshes)

| mode | One SM_ per | Use for |
|---|---|---|
| `each` | unique shape and material combination (instances share one mesh) | kits and repeated props |
| `group` | top-level group, baked with its arrays and mirrors | assembled props (table, lamp post) |
| `all` | the whole scene | one landmark or set piece |

Arrays and shared instances bake into one mesh only with `group` or `all`. With `each`, a four-leg array ships one leg.

Objects bound to a rig always ship as that rig's `SK_`, whatever the mode.

## What lands

Under `<project>/Forge` (or `folder`):

- `SM_<name>`: static meshes, pivot on the base centre, simple collision.
- `SK_<scene>/`: per rig, a SkeletalMesh, its Skeleton, a PhysicsAsset, and one AnimSequence per clip (named from the clip). Shape keys ride on the mesh as morph targets, and clips that key them carry their curves.
- `Materials/M_Forge_Master` plus one `MI_<mesh>_<material>` per material slot: base color, metallic, roughness, emissive, and textures.
- `Materials/M_Forge_Blend`, built the first time a blend material is sent: its `MI_` mixes up to three layer materials (colour, roughness, metallic, texture) over the base by the mesh's vertex colour R, G, and B, which carry the painted masks.
- `Textures/`: base color (sRGB), normal (normal map), and ORM (linear masks), imported once per send. Painted textures go the same way.

Sending again replaces the assets in place, so meshes already in the level update.

## Place in the level

`place=true` spawns each asset 8 m in front of the editor camera, or at `location=[x, y, z]` in UEFN centimetres. Assets are spaced 4 m apart on X and dropped to the ground. They land in the `Forge` outliner folder.

- Static meshes become mesh actors.
- Rigged assets with clips go on an **Animated Mesh device** set to the `SK_` and its first clip, looping. That needs the Animation plugin; without it the skeletal mesh is placed and `notes` says so.
- To trigger the clip in play, bind the device's Play and Pause through Direct Event Binding or Verse (`skill_read_subskill("animation", "runtime_playback")`).
- For other clips on the same device, call `configure_animated_mesh` with another `anim_path` from the result.

## Collision

Static meshes get simple collision (`CTF_UseSimpleAsComplex`). Objects on the `collision` layer are for seeing blockers in the dock; they are not imported as custom collision yet. `forge_hq(op="collision", params={"id": ...})` makes such a `UCX_<name>_01` hull; it ships as real collision in the UE5 FBX (`export target=ue5`, skill `hq_export`). Skeletal meshes get the generated PhysicsAsset.

## Validate

Before sending, run `forge_hq(op="qa", params={"profile": "uefn", "fix": true})` (skill `asset_qa`): it catches n-gons, inward normals, z-fighting, pivots and the 20k-tris-per-mesh cap. Every asset in the result carries `validate_uefn_asset` output. Fix its errors before publishing. To inspect afterwards: `get_static_mesh_info`, `get_skeletal_mesh_info`, or `get_anim_sequence_info`.

## Orientation

Assets land in UEFN looking exactly as they do in the dock, text and one-sided details included. UEFN's axes are mirrored against the dock's (and Blender's), so Send converts between them: a front that faces -Y in the dock faces +Y in UEFN, and a Forge character stands like the UE mannequin, facing +Y with its left side on +X. Assets sent by an older Forge came out mirrored; sending them again replaces them in place.

## Problems

| Symptom | Fix |
|---|---|
| "UEFN has no project open" | Open the island in UEFN, wait for it to load, send again |
| "Import did not return a skeletal mesh" | Nothing visible is bound to the rig, or the bound objects are on a hidden layer |
| Clip not offered on the device | The clip belongs to another skeleton; send the mesh and clips together |
| Table ships one leg | Use `mode="group"` so arrays bake |
| Asset floats or sinks | Pivot not on the base; `forge_fix` (pivot) or `forge_mesh_op op="origin"` |
| Mirrored text or a swapped left and right | The asset came from an older Forge; send it again |
| Asset faces the other way than expected | The dock's -Y front faces +Y in UEFN; rotate the placed actor, or model the front toward -Y |
| Blend material shows only the base | The mesh arrived without vertex colours; reimport it with Vertex Color Import Option set to Replace |
| Shape keys don't move in UEFN | They ship only on an `SK_`: bind the mesh to a rig (`template="single"`), send again, and look for morph targets in `get_skeletal_mesh_info` |
| Placed in the wrong spot | Pass `location`, or move it with the level tools |
