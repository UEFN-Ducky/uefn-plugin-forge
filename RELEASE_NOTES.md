# Forge 1.2.6 — Mesh import candidate

Adds local GLB, glTF 2.0 and binary FBX imports using the existing Blender converter. The File panel offers Browse, Inspect, scale multiplier, new/existing scene destination, progress and cancellation. Agent tool `forge_import_mesh` and workflow node `forge.import_mesh` share the same converter.

Preserves static mesh hierarchy, UVs, corner normals, vertex colors and per-face material slots. The viewport now renders those material slots. Base-color images and texture × vertex-color combinations are supported. Alternate Unreal LOD and collision meshes remain available but start hidden to avoid overlaps. Dimensions describe visible object geometry in centimetres, automatically converted to Z-up.

## Compatibility and migration

- Existing scenes retain their storage format. No destructive migration runs.
- New imports use unique scene names; existing-scene imports form one undo entry, including reopen/redo support.
- Requires a locally installed Blender executable via standard Windows installation, PATH or FORGE_BLENDER.
- Binary FBX only; ASCII FBX reports an explicit conversion requirement.
- glTF sidecars must be local, present and contained under the model folder. Required unsupported extensions are rejected. Input and referenced resources are limited to 512 MB; conversion has a 15-minute timeout.
- Sheared transforms are rejected rather than silently changed. Rigs, animation, shape keys and procedural shader graphs are not transferred. Texture channels other than base color use material factors.
- Cancellation stops conversion before scene commit. Once committed, an existing-scene import is reversed with Undo.
- The external Blender script ships as assets/blend_export_script.txt so Store compilation will not remove it. Three.js MIT notice is included.

## Real reference

The exported CP_NeonCity_Building_A_B FBX contains LOD0–3 plus UCX collision. Its LOD0 module is approximately 30.72 × 30.72 × 15.36 metres. It is not the assembled 100.31-metre tower. MegaCity_Reference_Module is imported and rendered in Forge. Its FBX contains no original texture images; its material is explicitly labeled reference gray. Existing Miami scenes were not edited.

## Release gates

This is a local source release candidate, not a published or installed Store update. Store draft validation and testing are blocked because no local Forge draft exists; opening the Store source requires team manage permission. Compiled Store packaging and live verification of the updated panel remain pending. Automated converter, scene, performance and package checks are run separately; source-level tests do not substitute for the installed-panel gate.
