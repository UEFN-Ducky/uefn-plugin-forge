---
description: "Procedural modeling in Forge: buildings, modular wall/floor kits, stairs, fences, pipes and hanging cables, scatter on surfaces or along paths, geometry-nodes graphs as JSON, smooth SDF-style unions, and rigid-body drop for clutter"
metadata:
  label: "Procedural"
  default_enabled: false
  load_condition: "Making buildings, modular kits, stairs, fences, pipes, cables, scattering rocks or props, geometry nodes, blending parts into one organic mesh, or settling clutter with physics in Forge"
---

# Procedural

All `forge_hq` ops, cm unless noted. Each makes or rewrites one mesh, so one `forge_batch` builds a whole set.

## Ops

| op | params (defaults) | Result |
|---|---|---|
| `procedural` | `kind` building\|wall\|floor\|stairs\|fence\|pipe\|cable; building: `floors` 4, `width` 1000, `depth` 800, `floor_height` 300, `bay` 300; wall: `width` 512, `height` 384, `thickness` 20, `opening` none\|door\|window; floor: 512 x 512 x 20; stairs: `steps` 12, `width` 100, `rise` 17.5, `run` 28; fence: `length` 400, `height` 100, `spacing` 100; pipe/cable: `path` [[x,y,z]...], `radius` 5, `sag` 40; `at`, `name` | `6F 60 windows: 1192 tris`. One new `SM_` object. Wall and floor pivot on the min corner for 512-grid snapping |
| `scatter` | `on` id or `path`, `items` [ids], `density` 20 per m², `count`, `seed` 1, `scale` [.8, 1.2], `align` normal\|up, `max_slope` 90, `spacing` 50, `name` | `scatter 80 copies: 1600 tris`. One merged new object; same seed, same result. `path` = an array along a line, each copy turned to the heading |
| `gn` | `id` (or none: a new object), `preset` pebbles\|wire, or `spec`, `inputs` {"node.Socket": value}, `name` | Geometry-nodes graph applied, smooth by angle 30° |
| `sdf_blend` | `ids` (2+), `fillet` 2, `voxel` 1, `name`, `hide` true | One watertight new mesh, rounded only where parts meet; sources hidden |
| `drop` | `ids`, `onto` [ids], `frames` 60 | Rigid-body settle (convex hulls on mesh colliders); objects keep where they land |

Keep `scatter` under budget: count × item tris. 2000 copies is the cap. Scatter low-poly items (a 20-tri pebble, a 60-tri tuft).

## gn spec

Nodes by Blender type name, links as `"node.Socket"`; `in` and `out` are the group's Geometry sockets. Socket values are Blender units (metres: 0.03 = 3 cm).

```json
{"nodes": [
  {"id": "pts", "type": "GeometryNodeDistributePointsOnFaces", "in": {"Density": 10}},
  {"id": "rock", "type": "GeometryNodeMeshIcoSphere", "in": {"Radius": 0.03, "Subdivisions": 1}},
  {"id": "inst", "type": "GeometryNodeInstanceOnPoints"},
  {"id": "real", "type": "GeometryNodeRealizeInstances"}],
 "links": [["in.Geometry", "pts.Mesh"], ["pts.Points", "inst.Points"], ["rock.Mesh", "inst.Instance"],
           ["inst.Instances", "real.Geometry"], ["real.Geometry", "out.Geometry"]]}
```

Realize instances last. A wrong node or socket name answers with the valid list. Tweak a preset with `inputs`, e.g. `{"pts.Density": 200}` on `pebbles`, `{"ring.Radius": 0.02}` on `wire`.

## Street corner in one batch

```json
[
  {"tool": "forge_add", "kind": "box", "object_name": "Ground", "params": {"w": 3000, "d": 3000, "h": 10}, "pos": [0, 0, -10]},
  {"tool": "forge_hq", "op": "procedural", "params": {"kind": "building", "floors": 6, "at": [0, 800, 0]}},
  {"tool": "forge_hq", "op": "procedural", "params": {"kind": "fence", "length": 1200, "at": [-600, -200, 0]}},
  {"tool": "forge_hq", "op": "procedural", "params": {"kind": "cable", "path": [[-600, 300, 900], [600, 300, 900]], "radius": 2, "sag": 80}},
  {"tool": "forge_add", "kind": "sphere", "object_name": "Rock", "params": {"radius": 15, "seg": 8, "rings": 4}},
  {"tool": "forge_hq", "op": "scatter", "params": {"on": "$0", "items": ["$4"], "density": 0.5, "max_slope": 20, "name": "SM_Rocks"}},
  {"tool": "forge_delete", "ids": ["$4"]}
]
```

Run it with `forge_batch(steps=[...], look="three")`. Clutter that should rest naturally: place the props above the floor, then `{"tool": "forge_hq", "op": "drop", "params": {"ids": [...], "onto": ["$0"]}}`.

**Done when:** the `look` shows the building's window rows and parapet, the fence and the sagging cable from the front and above, the scatter sits on the ground with no copies floating, and each `procedural` summary has no `open`.

## Organic unions

Merge a head, ears and a snout (or tree limbs) into one mesh: `sdf_blend(ids, fillet=3, voxel=1)`. Voxel 1 cm ≈ 1 % of a 1 m prop; halve it for small props. Follow with `retopo` for a game mesh.

---
Adapted from Blender Agent Skills (geometry-nodes, procedural, physics) by EL4CTEO, MIT licence. Modular-kit and scale conventions from the R2 survey (game-asset agent skill repos).
