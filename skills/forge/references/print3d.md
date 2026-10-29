---
description: "3D printing from Forge: print_check (watertight, self-intersections, thin walls, overhangs, loose shells, volume) and STL / OBJ export in millimetres"
metadata:
  label: "3D print"
  default_enabled: false
  load_condition: "Making a Forge model for 3D printing, checking it is printable, or exporting STL"
---

# 3D print

```json
forge_batch(name="Figure", steps=[
  {"tool": "forge_hq", "op": "qa", "params": {"profile": "print", "fix": true}},
  {"tool": "forge_hq", "op": "print_check", "params": {"min_wall": 0.08, "overhang": 45}},
  {"tool": "forge_hq", "op": "export", "params": {"format": "stl", "verify": true}}
])
```

## print_check(id?, min_wall=0.08, overhang=45)

Centimetres. Runs on Blender's BVH tree over all the given objects together.

| Check | Severity | Fix |
|---|---|---|
| open or non-manifold edges | error | close holes, or `remesh` voxel |
| self-intersecting faces | error | `remesh` voxel 0.5-1 % of height, or boolean union |
| zero-area faces | error | `qa fix=true` |
| walls thinner than `min_wall` (0.08 cm = 0.8 mm, FDM; resin 0.05) | warning | `modifiers` solidify, thicken |
| over 10 % of faces overhang more than `overhang` degrees | warning | rotate the model, or plan supports |
| more shells than objects | warning | join or union |

The result carries `volume_cm3` (resin or filament estimate).

## Export

`export(format="stl")` writes binary STL in **millimetres**, Z up (what slicers expect); `format="obj"` writes metres, Y up. Scale before export: a 10 cm figure is `h=10` in Forge. `verify=true` reads the file back.

## Done when

`print_check` returns `pass: true` and the STL exists.

Adapted from AgentPMT Blender 3D Modeling (3D-print checks; feature list only) and HoldMyBeer-gg/blenderwright (print checks; MIT).
