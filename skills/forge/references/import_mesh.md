---
description: "Importing .blend, .fbx, .glb and .gltf files into a Forge scene with forge_import_mesh: what is kept, what is not, limits"
metadata:
  label: "Import meshes"
  default_enabled: false
  load_condition: "Importing a local .blend, .fbx, .glb or .gltf file (reference buildings, existing props) into Forge"
---

# Import meshes

`forge_import_mesh(path, name="", destination="", scale=1, preview=false)` reads a local .blend, binary .fbx, .glb or .gltf. It needs a local Blender executable (PATH, the standard Windows install, or `FORGE_BLENDER`).

- `preview=true` reports centimetre dimensions, triangle/object/material counts and warnings without writing a scene.
- `destination` merges into an existing scene in one undo step. Otherwise a unique new scene is created, never overwritten.
- The File panel and the `forge.import_mesh` workflow node use the same importer. The panel adds Browse, Inspect, destination, scale, progress and Cancel. Cancelling stops conversion before commit; a completed commit stays undoable.

## What comes across

Static meshes, hierarchy, centimetre/Z-up conversion, corner normals, vertex colours, UVs and per-face material slots. Base-colour images connected directly or multiplied by vertex colour. Collision helper meshes and alternate LODs are kept but hidden; measurements exclude hidden meshes.

Not transferred: rigs, animation, procedural shaders, other texture channels. Read the warnings before treating an imported material as equal to the original Unreal shader.

## Limits

ASCII FBX, sheared transforms, remote or missing glTF sidecars and unsupported required glTF extensions fail with a message. File plus referenced resources are limited to 512 MB; conversion times out at 15 minutes.

A pasted UEFN actor/T3D block is a reference, not geometry: export its mesh to a supported format first. Do not claim a whole tower was imported from one module.
