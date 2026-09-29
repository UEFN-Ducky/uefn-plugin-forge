---
description: "UVs and painting in Forge: smart unwrap versus tiling projections, texel density, moving UVs, painting a base-colour texture through the UVs, blend materials with up to three layers painted by procedural masks, and what each becomes in UEFN"
metadata:
  label: "Paint and UVs"
  default_enabled: false
  load_condition: "Unwrapping UVs, painting a texture, adding moss, dirt, rust, snow, or wear, making a blend material, or a texture looks stretched or seamed"
---

# UVs and painting

## UVs

| Call | Gives | Use for |
|---|---|---|
| `forge_uv_project(name, id, mode="smart")` | charts split where faces turn more than `angle` (66 degrees), packed into 0-1 at even texel density with a `margin` | anything you paint; unique textures |
| `mode="box"`, `"planar"`, or `"cylinder"` | tiling UVs at `density` UV units per cm | walls, floors, trim, repeating materials |

- Smart returns `charts` and `px_per_cm_1024`, the texels per cm on a 1024 texture. About 1 px/cm suits a prop seen up close; small props can go higher.
- `forge_uv_transform(name, id, faces, offset=[u, v], rotate=90, scale=[su, sv])` moves UVs about their centre: turn a plank's grain, fit a decal.
- Unwrap again after big topology edits.

## Paint a texture

`forge_paint(name, id, mode="texture", material, ...)` paints the material's base-colour texture through the object's UVs. Unwrap with `smart` first; projected UVs overlap, so paint would land in several places.

| Arguments | Does |
|---|---|
| `fill="all"`, `color`, `size` | starts a new solid texture, `size` px square |
| `faces=[...]`, `color` | fills those faces |
| `points=[[x, y, z], ...]`, `radius`, `color` | soft dabs along the stroke, measured in 3D, so it runs across UV seams without a break |
| `grain` | adds noise (0-1) on top |
| `derive_maps=true` | also makes normal and ORM maps from the result |

Colours are `[r, g, b]` from 0 to 1, or `"#rrggbb"`. Every paint saves new files, and undo goes back to the earlier pixels.

A crate with a dark X brace on its front:

```json
[
  {"tool": "forge_material_set", "material": "Wood", "base_color": [0.55, 0.36, 0.2], "roughness": 0.85},
  {"tool": "forge_add", "kind": "box", "object_name": "Crate", "params": {"w": 100, "d": 100, "h": 100}, "material": "$0"},
  {"tool": "forge_select", "object": "$1"},
  {"tool": "forge_uv_project", "mode": "smart"},
  {"tool": "forge_paint", "id": "$1", "mode": "texture", "material": "$0", "fill": "all", "color": [0.55, 0.36, 0.2], "size": 1024, "grain": 0.12},
  {"tool": "forge_paint", "id": "$1", "mode": "texture", "material": "$0", "points": [[-45, -50, 95], [45, -50, 5]], "radius": 6, "color": "#3a2412"},
  {"tool": "forge_paint", "id": "$1", "mode": "texture", "material": "$0", "points": [[-45, -50, 5], [45, -50, 95]], "radius": 6, "color": "#3a2412"}
]
```

## Blend materials

A blend material shows up to three other materials over itself wherever its R, G, or B mask is painted: moss on rock, dirt at the foot of a wall, snow on top, rust in patches.

1. Make the layer materials, then the base with `layers=[R, G, B]`: `forge_material_set(material="Stone", layers=["Moss", "Dirt"])`.
2. Paint the masks: `forge_paint(name, id, mode="blend", channel="r", mask=..., combine=...)`.

| mask | Parameters | Covers |
|---|---|---|
| `all` | none | everything |
| `up`, `down` | `threshold` (0.5), `softness` (0.3) | faces pointing up (moss, snow) or down (grime, drips) |
| `facing` | `direction`, `threshold`, `softness` | faces toward a direction: wind-blown snow, sun bleaching |
| `height` | `low`, `high` (the object's own cm) | full at `low`, gone by `high`: dirt at the base, a water line |
| `noise` | `scale` cm, `seed`, `contrast` | patches |
| `sphere` | `center` (world cm), `radius` | one spot |
| `faces` | `faces` | picked faces |
| `cavity` | none | creases and dents |

`combine` is `set` (the default), `add`, `subtract`, `max`, `min`, or `multiply`; `strength` scales the paint. Multiply a coverage mask by `noise` to break it into patches.

The masks live on the vertices, so they need vertices where an edge should fall: subdivide first. A mossy rock with dirt at its foot:

```json
[
  {"tool": "forge_material_set", "material": "Moss", "base_color": [0.22, 0.5, 0.14], "roughness": 0.8},
  {"tool": "forge_material_set", "material": "Dirt", "base_color": [0.3, 0.22, 0.15], "roughness": 0.95},
  {"tool": "forge_material_set", "material": "Stone", "base_color": [0.46, 0.45, 0.43], "roughness": 0.9, "layers": ["$0", "$1"]},
  {"tool": "forge_add", "kind": "box", "object_name": "Rock", "params": {"w": 160, "d": 120, "h": 90}, "material": "$2"},
  {"tool": "forge_select", "object": "$3"},
  {"tool": "forge_mesh_op", "op": "subdivide", "smooth": true, "levels": 3},
  {"tool": "forge_sculpt", "id": "$3", "brush": "noise", "strength": 9, "scale": 45, "seed": 4},
  {"tool": "forge_mesh_op", "op": "origin"},
  {"tool": "forge_paint", "id": "$3", "mode": "blend", "channel": "r", "mask": "up", "threshold": 0.35, "softness": 0.35},
  {"tool": "forge_paint", "id": "$3", "mode": "blend", "channel": "r", "mask": "noise", "combine": "multiply", "scale": 30, "contrast": 3},
  {"tool": "forge_paint", "id": "$3", "mode": "blend", "channel": "g", "mask": "height", "low": 0, "high": 25}
]
```

## In UEFN

- Painted textures import as `T_` textures on the material's `MI_`, like generated ones.
- A blend material becomes an `MI_` of `M_Forge_Blend`, which mixes each layer's colour, roughness, metallic, and texture by the mesh's vertex colour. `forge_send_to_uefn` builds that master the first time it needs it.

## The user in the dock

The Materials tab has the same tools: Unwrap, Box, Planar, Cylinder, Rotate 90, and Scale beside a view of the texture with the UV charts drawn on it; "Paint it" opens Texture paint; the R, G, and B layer pickers and "Paint layers" open Blend paint. The user can paint over your masks and textures and you over theirs; each stroke is one undo step.
