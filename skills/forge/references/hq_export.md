---
description: "Forge HQ export: one call writes an engine-ready FBX, glTF/GLB or Alembic for UE5, Unity or Godot with skin, morph targets and clips"
metadata:
  label: "HQ export"
  default_enabled: false
  load_condition: "Exporting a Forge asset for UE5, Unity or Godot (not UEFN: that is forge_send_to_uefn)"
---

# HQ export

`forge_hq(op="export", params={target: "ue5"|"unity"|"godot"|"generic", format, path, tangents})` exports the given `id`s (default: the whole scene and its rigs).

| target | default format | Axes and normals |
|---|---|---|
| `ue5` | fbx | -Y forward, Z up, DirectX normals |
| `unity` | fbx | -Z forward, Y up, GL normals, units applied |
| `godot` | glb | GL normals |
| `generic` | glb | GL normals |

`format` overrides: fbx, glb, gltf, abc. Default path is `<scene folder>/exports/<scene>.<ext>`. The result lists skins, clips, morphs, tris, bones and warnings. Bake textures first (`hq_materials`), and `lods` if the engine needs them. For a UEFN project use `forge_send_to_uefn` (see `send_to_game`).
