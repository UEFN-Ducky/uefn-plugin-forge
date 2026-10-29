---
description: "Forge HQ export: one call writes an engine-ready FBX, glTF/GLB, OBJ, STL, USDZ or Alembic for UE5, Unity, Roblox, Godot or the web, with skin, morphs, clips, UCX_ collision, LOD names and a re-import check; convert turns any file into another format"
metadata:
  label: "HQ export"
  default_enabled: false
  load_condition: "Exporting a Forge asset for UE5, Unity, Roblox, Godot, a web viewer or another tool (not UEFN: that is forge_send_to_uefn), adding UCX collision, or converting a 3D file"
---

# HQ export

```json
forge_batch(name="Crate", steps=[
  {"tool": "forge_hq", "op": "qa", "params": {"profile": "ue5", "fix": true}},
  {"tool": "forge_hq", "op": "collision", "params": {"id": "<crate id>", "kind": "box"}},
  {"tool": "forge_hq", "op": "lods", "params": {"id": "<crate id>"}},
  {"tool": "forge_hq", "op": "export", "params": {"target": "ue5", "verify": true, "lods": true}}
])
```

Result: `ue5 -> .fbx, 0 skin, 0 clips, 0 morphs, 4 LODs, 1 UCX`, plus `verified` and `mb`.

## export(id?, target, format?, path?, tangents=true, verify=false, lods=false)

| target | Format | Axes, scale | Normals | UCX_ hulls |
|---|---|---|---|---|
| `ue5` | fbx | -Y forward, Z up, cm | DirectX | kept (UE builds collision) |
| `unity` | fbx | -Z forward, Y up, units applied | GL | dropped |
| `roblox` | fbx | -Z forward, Y up, cm (Studio converts to studs) | GL | dropped; triangulated; warns over 20k tris or 10k verts per mesh |
| `godot` | glb | Y up, m | GL | renamed `<name>-colonly` (Godot import hint) |
| `web` | glb | Y up, m, WebP textures q85 | GL | dropped; warns over 30k tris per mesh or 5 MB |
| `generic` | glb | Y up, m | GL | dropped |

`format` overrides: `fbx glb gltf abc obj stl usdz`. `stl` is millimetres Z up (print), `obj` metres Y up, `usdz` for Apple / AR. Default path: `<scene folder>/exports/<scene>.<ext>`.

- `verify=true` reads the file back into an empty Blender: meshes, skins, morphs, bones must match; size within 2 %; armature scale 1 (not 100); clips survive; no missing images; warns over 8 MB.
- `lods=true` writes `<name>_LOD0..n` from each object's `lods` ratios (default 1 .5 .25 .1). Unity builds a LODGroup from the names.
- Warnings also land in the summary, so they survive the 300-char result.

## collision(id, kind=hull|box, max_verts=32, shrink=.98)

Adds `UCX_<name>_01` next to the object on the `collision` layer: a convex hull of up to `max_verts` points (`box` = bounding box), shrunk 2 % so it never pokes out. Running again replaces it. One hull per part: call it on each part of a concave prop (a table = top + 4 legs).

## convert(path, to, out?)

Any `fbx glb gltf obj stl usd usdz abc` file into `glb fbx obj stl usdz gltf`, no scene needed: `{"op": "convert", "params": {"path": "C:/in/chair.fbx", "to": "glb"}}`.

## Order

Bake textures (`hq_materials`) and `lods` first, `qa` before export, `collision` before export. For a UEFN project use `forge_send_to_uefn` (skill `send_to_game`).

## Done when

`export(verify=true)` returns `verified: true` with no warnings.

Adapted from Blender Agent Skills by EL4CTEO (export-pipelines, optimization; MIT), arjun988/blender-skills (UCX_, LOD names; MIT), majidmanzarpour/blender-game-skills (round-trip check; MIT) and RobLe3/cc-blender-skill (export caps; MIT).
