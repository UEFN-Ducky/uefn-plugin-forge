---
description: "Sculpting in Forge: subdividing for resolution, every brush and its strength units, mirrored and whole-mesh brushes, decimating to budget, and shape keys (morph targets) for faces and squash, keyed in clips and shipped on the SK_"
metadata:
  label: "Sculpt and shape keys"
  default_enabled: false
  load_condition: "Sculpting or adding organic detail (rocks, creatures, faces, folds), or making or animating shape keys (morph targets, blend shapes, expressions) in Forge"
---

# Sculpting and shape keys

## Resolution first

Brushes move the vertices a mesh already has; they never add any. Round a block-out before sculpting it: select it, then `forge_mesh_op(name, op="subdivide", smooth=true, levels=3)`.

| levels | A box becomes | Good for |
|---|---|---|
| 1 | 26 vertices, 48 triangles | simple shape keys |
| 2 | 98 vertices, 192 triangles | pebbles, blobs |
| 3 | 386 vertices, 768 triangles | rocks, heads, creature parts |
| 4 | 1,538 vertices, 3,072 triangles | hero pieces |

- Smooth subdivide shrinks the shape toward its middle, so the bottom lifts off z = 0. Finish with `forge_mesh_op(op="origin")` so the pivot sits on the new base.
- `forge_mesh_op(op="decimate", ratio=0.5)` keeps that share of the vertices, to get a finished sculpt back under budget.

## Brushes

`forge_sculpt(name, id, brush, points=[[x, y, z], ...], radius=20, strength=...)`. Points are world centimetres on or near the surface. A list of points is one stroke, with a dab every `spacing` (0.3) of the radius. Vertices within `radius` of a dab move, most at its centre.

| brush | strength | Does |
|---|---|---|
| `draw` | cm at the centre | pushes out along the surface; `invert=true` pushes in |
| `clay` | cm | builds up toward a level above the surface: layered rock, muscle mass |
| `inflate` | cm | swells each vertex along its own normal |
| `crease` | cm | cuts a groove along the stroke and pinches its sides: cracks, seams, folds |
| `smooth` | 0-1 | relaxes toward the neighbours |
| `flatten` | 0-1 | presses onto the average plane: cut faces on rock, worn edges |
| `pinch` | 0-1 | pulls the sides toward the stroke: sharp ridges |
| `grab` | none | drags the vertices under the first point along the stroke |

Whole-mesh brushes take no points:

| brush | Parameters | Use |
|---|---|---|
| `noise` | `strength` cm, `scale` cm (feature size), `seed`, `octaves` | break up a clean block-out: rock, bark, ground |
| `smooth_all` | `iterations`, `strength` 0-1 | calm noise or facets |
| `inflate_all` | `strength` cm | fatten or thin the whole shape |

`mirror="x"` repeats every dab across the object's own X = 0. Characters face -Y, so a face's left cheek is +X.

Work big to small: `noise` or `draw` for mass, `clay` and `flatten` for planes, `crease` for detail, `smooth` to finish. Render between passes (`forge_render(name, views=["three", "front"])`).

A boulder, sitting on its base:

```json
[
  {"tool": "forge_material_set", "material": "Stone", "base_color": [0.46, 0.45, 0.43], "roughness": 0.9},
  {"tool": "forge_add", "kind": "box", "object_name": "Boulder", "params": {"w": 160, "d": 120, "h": 90}, "material": "$0"},
  {"tool": "forge_select", "object": "$1"},
  {"tool": "forge_mesh_op", "op": "subdivide", "smooth": true, "levels": 3},
  {"tool": "forge_sculpt", "id": "$1", "brush": "noise", "strength": 9, "scale": 45, "seed": 4},
  {"tool": "forge_sculpt", "id": "$1", "brush": "flatten", "points": [[0, 0, 82]], "radius": 40, "strength": 0.8},
  {"tool": "forge_sculpt", "id": "$1", "brush": "crease", "points": [[-40, -47, 40], [0, -51, 36], [40, -47, 44]], "radius": 10, "strength": 3},
  {"tool": "forge_mesh_op", "op": "origin"}
]
```

## Shape keys

A shape key stores another position for every vertex: a smile, a blink, a squash. It blends in by a weight from 0 to 1 and ships as a morph target.

1. `forge_shape(name, id, action="add", key="Smile")`.
2. Sculpt into it with `shape="Smile"` on any brush, whole-mesh ones included. The base mesh does not change.
3. Or build it from a copy: `forge_duplicate` the object, reshape the copy without adding or removing vertices, then `forge_shape(action="from_object", key="Smile", source=<copy id>)` and delete the copy.
4. Key it in a clip: rows `{"shape": "Smile", "frame": 12, "value": 1}` in `forge_anim(action="key", keys=[...])`.

`rename` (with `new_name`) and `delete` manage keys. Mesh edits carry the keys along; mirror swaps `_l` and `_r` names.

Shape keys reach UEFN only on a skeletal mesh. For a prop with no bones, `forge_rig(action="template", template="single", ids=[...])` adds one root bone and binds the objects to it.

A head with a smile and a puff, and a clip that plays the smile:

```json
[
  {"tool": "forge_material_set", "material": "Skin", "base_color": [0.86, 0.66, 0.52], "roughness": 0.6},
  {"tool": "forge_add", "kind": "box", "object_name": "Head", "params": {"w": 50, "d": 50, "h": 56}, "material": "$0"},
  {"tool": "forge_select", "object": "$1"},
  {"tool": "forge_mesh_op", "op": "subdivide", "smooth": true, "levels": 3},
  {"tool": "forge_mesh_op", "op": "origin"},
  {"tool": "forge_shape", "id": "$1", "action": "add", "key": "Smile"},
  {"tool": "forge_sculpt", "id": "$1", "shape": "Smile", "brush": "draw", "points": [[9, -19, 15]], "radius": 7, "strength": 3, "mirror": "x"},
  {"tool": "forge_shape", "id": "$1", "action": "add", "key": "Puff"},
  {"tool": "forge_sculpt", "id": "$1", "shape": "Puff", "brush": "inflate_all", "strength": 3},
  {"tool": "forge_rig", "action": "template", "template": "single", "ids": ["$1"]},
  {"tool": "forge_anim", "action": "new", "new_name": "Grin", "frames": 30},
  {"tool": "forge_anim", "action": "key", "clip": "Grin", "keys": [
    {"shape": "Smile", "frame": 0, "value": 0}, {"shape": "Smile", "frame": 12, "value": 1}, {"shape": "Smile", "frame": 30, "value": 0}
  ]}
]
```

`origin` moves the pivot, not the object, so world stroke points stay where they were. `forge_stats` gives the bounds to aim at.

## Tool args

`forge_sculpt`
- Stroke brushes run along `points` (world cm, near the surface): draw, clay, inflate, crease (strength = cm at the centre), smooth, flatten, pinch (strength 0-1), grab (drags along the stroke).
- Whole-mesh brushes need no points: noise (strength cm, `scale` = feature size cm, `seed`), smooth_all (`iterations`, strength 0-1), inflate_all (strength cm).
- `mirror="x"` sculpts both sides of the object's own X=0. `shape="Smile"` sculpts into that shape key instead of the base mesh.

`forge_shape(action=...)`
- `add`: an empty key to sculpt into with `forge_sculpt shape=key`.
- `from_object`: key = the source object minus this one. Duplicate, reshape without adding vertices, then use it as `source`.
- `delete`; `rename` (`new_name`).
- Animate with `forge_anim` key rows `{shape, frame, value 0-1}`. A rig is needed to reach UEFN (`forge_rig template=single` is enough).

## The user in the dock

The user sculpts the same mesh from the dock: Mode, then Sculpt, pick a brush and a target (Base mesh or a shape key), and drag. `[` and `]` resize the brush, "+ Key" adds a shape key, and Subdivide and Decimate step the resolution up or down. Each finished stroke is one undo step, like each of yours, and both of you can work on the same object in turns.
