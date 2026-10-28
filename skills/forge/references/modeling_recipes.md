---
description: "Batch recipes for common Fortnite-scale props (crate, barrel, table, wall with openings, lamp post, sword, rock, tree), scale and triangle budgets, boolean and merge patterns, and routing to Blender style packs for art direction"
metadata:
  label: "Modeling recipes"
  default_enabled: false
  load_condition: "Building a prop, kit piece, weapon, or environment asset in Forge"
---

# Modeling recipes

Every recipe is one `forge_batch`. Change sizes and colors, keep the shape of the calls. Render after each one (`forge_render`), then adjust with `forge_edit` or `forge_mesh_op`.

## Scale (centimetres)

| Thing | Size |
|---|---|
| Player | 190 tall, about 50 wide |
| Door opening | 120 W x 250 H |
| Wall / floor tile | 512 x 512; wall 384 tall; floor 20 thick |
| Stair riser / tread | about 18 / 28 |
| Crate | 60-100 cube |
| Barrel | 90 H x 60 across |
| Table | 75 H top; chair seat 45 |
| Rifle / sword | 80-120 long; pistol about 20 |
| Tree (game) | 400-1200 tall |

Build next to a 190 cm box when unsure, then delete it. Props that ignore player scale read as toys or monuments.

## Triangle budgets

| Asset | Triangles |
|---|---|
| Small pickup (coin, ammo) | 100-500 |
| Standard prop | 500-2,000 |
| Hero prop you interact with | 2,000-8,000 |
| Kit wall or floor | 200-800 |
| Blocky character | 1,000-5,000 |
| Hard ceiling per Forge object | 20,000 (`forge_check` flags it) |

Bevel only edges the camera sees in silhouette. `segments=1` bevels read well at game distance.

## Crate with recessed panels

```json
[
  {"tool": "forge_material_set", "material": "Wood", "base_color": [0.52, 0.34, 0.18], "roughness": 0.85},
  {"tool": "forge_add", "kind": "box", "object_name": "Crate", "params": {"w": 100, "d": 100, "h": 100}, "material": "$0"},
  {"tool": "forge_select", "object": "$1", "query": "all"},
  {"tool": "forge_mesh_op", "op": "inset", "amount": 10},
  {"tool": "forge_mesh_op", "op": "extrude", "distance": -4},
  {"tool": "forge_uv_project", "mode": "box"}
]
```

Inset keeps the inner faces selected, so the extrude pushes the panels in.

## Barrel with iron hoops

```json
[
  {"tool": "forge_material_set", "material": "BarrelWood", "base_color": [0.45, 0.28, 0.14], "roughness": 0.8},
  {"tool": "forge_material_set", "material": "Iron", "base_color": [0.3, 0.3, 0.32], "metallic": 0.9, "roughness": 0.4},
  {"tool": "forge_add", "kind": "lathe", "object_name": "Staves", "params": {"profile": [[0, 0], [27, 0], [31, 20], [32, 45], [31, 70], [27, 90], [0, 90]], "seg": 16}, "material": "$0"},
  {"tool": "forge_add", "kind": "torus", "object_name": "HoopLow", "params": {"major": 30.5, "minor": 1.5, "seg": 24, "rings": 6}, "pos": [0, 0, 16], "material": "$1"},
  {"tool": "forge_add", "kind": "torus", "object_name": "HoopHigh", "params": {"major": 31, "minor": 1.5, "seg": 24, "rings": 6}, "pos": [0, 0, 66], "material": "$1"},
  {"tool": "forge_group", "ids": ["$2", "$3", "$4"], "object_name": "Barrel"}
]
```

A lathe profile is `[radius, z]` pairs from the bottom up; a 0 radius at an end closes it.

## Table from one leg

```json
[
  {"tool": "forge_add", "kind": "box", "object_name": "Top", "params": {"w": 160, "d": 90, "h": 5}, "pos": [0, 0, 70]},
  {"tool": "forge_add", "kind": "box", "object_name": "Legs", "params": {"w": 6, "d": 6, "h": 70}, "pos": [-72, -37, 0]},
  {"tool": "forge_select", "object": "$1"},
  {"tool": "forge_modifier", "action": "add", "type": "array", "count": 2, "offset": [144, 0, 0]},
  {"tool": "forge_modifier", "action": "add", "type": "array", "count": 2, "offset": [0, 74, 0]},
  {"tool": "forge_group", "ids": ["$0", "$1"], "object_name": "Table"}
]
```

Two arrays multiply: one leg becomes four instances. Send with `mode="group"` so the four legs bake into the table mesh.

## Wall with a door and a window

```json
[
  {"tool": "forge_add", "kind": "box", "object_name": "Wall", "params": {"w": 512, "d": 20, "h": 384}},
  {"tool": "forge_add", "kind": "box", "object_name": "DoorCut", "params": {"w": 120, "d": 60, "h": 250}, "pos": [-120, 0, -1]},
  {"tool": "forge_add", "kind": "box", "object_name": "WindowCut", "params": {"w": 100, "d": 60, "h": 90}, "pos": [130, 0, 150]},
  {"tool": "forge_boolean", "a": "$0", "b": ["$1", "$2"], "op": "difference"},
  {"tool": "forge_uv_project", "mode": "box"}
]
```

Make cutters deeper than the wall and start them just below the floor (`z = -1`) so no faces are exactly coplanar.

## Lamp post with a glowing lamp

```json
[
  {"tool": "forge_material_set", "material": "Metal", "base_color": [0.2, 0.22, 0.25], "metallic": 0.8, "roughness": 0.45},
  {"tool": "forge_material_set", "material": "Glow", "base_color": [1, 0.85, 0.55], "emissive": [1, 0.8, 0.5], "emissive_strength": 8},
  {"tool": "forge_add", "kind": "cylinder", "object_name": "Pole", "params": {"radius": 6, "height": 380, "seg": 12}, "material": "$0"},
  {"tool": "forge_add", "kind": "box", "object_name": "Arm", "params": {"w": 80, "d": 8, "h": 8}, "pos": [36, 0, 360], "material": "$0"},
  {"tool": "forge_add", "kind": "sphere", "object_name": "Lamp", "params": {"radius": 14, "seg": 16, "rings": 8}, "pos": [70, 0, 330], "material": "$1"},
  {"tool": "forge_group", "ids": ["$2", "$3", "$4"], "object_name": "LampPost"}
]
```

## Sword (tip up)

```json
[
  {"tool": "forge_material_set", "material": "Steel", "base_color": [0.75, 0.77, 0.8], "metallic": 1, "roughness": 0.25},
  {"tool": "forge_material_set", "material": "Leather", "base_color": [0.25, 0.14, 0.08], "roughness": 0.9},
  {"tool": "forge_add", "kind": "extrude", "object_name": "Blade", "params": {"points": [[-4, 0], [4, 0], [4, 70], [0, 82], [-4, 70]], "depth": 1.5}, "rot": [90, 0, 0], "pos": [0, 0.75, 20], "material": "$0"},
  {"tool": "forge_add", "kind": "box", "object_name": "Guard", "params": {"w": 22, "d": 4, "h": 3}, "pos": [0, 0, 17], "material": "$0"},
  {"tool": "forge_add", "kind": "cylinder", "object_name": "Grip", "params": {"radius": 1.6, "height": 16, "seg": 10}, "pos": [0, 0, 1], "material": "$1"},
  {"tool": "forge_add", "kind": "sphere", "object_name": "Pommel", "params": {"radius": 2.5, "seg": 10, "rings": 6}, "pos": [0, 0, -4], "material": "$0"},
  {"tool": "forge_group", "ids": ["$2", "$3", "$4", "$5"], "object_name": "Sword"}
]
```

`extrude` draws an outline in XY and extrudes it up Z. Concave outlines work. Rotating 90 degrees about X stands it up with the outline's +Y becoming +Z.

## Stylized rock and tree

```json
[
  {"tool": "forge_material_set", "material": "Stone", "base_color": [0.45, 0.45, 0.43], "roughness": 0.95},
  {"tool": "forge_add", "kind": "sphere", "object_name": "Rock", "params": {"radius": 60, "seg": 7, "rings": 5}, "scale": [1.4, 1, 0.7], "rot": [0, 8, 23], "material": "$0"},
  {"tool": "forge_material_set", "material": "Bark", "base_color": [0.35, 0.22, 0.12], "roughness": 0.9},
  {"tool": "forge_material_set", "material": "Leaves", "base_color": [0.2, 0.5, 0.2], "roughness": 0.8},
  {"tool": "forge_add", "kind": "cylinder", "object_name": "Trunk", "params": {"radius": 18, "height": 260, "seg": 8}, "pos": [300, 0, 0], "material": "$2"},
  {"tool": "forge_add", "kind": "cone", "object_name": "Crown1", "params": {"radius": 150, "height": 220, "seg": 8}, "pos": [300, 0, 180], "material": "$3"},
  {"tool": "forge_add", "kind": "cone", "object_name": "Crown2", "params": {"radius": 110, "height": 190, "seg": 8}, "pos": [300, 0, 300], "material": "$3"},
  {"tool": "forge_group", "ids": ["$4", "$5", "$6"], "object_name": "Tree"}
]
```

Low segment counts plus non-uniform scale give a faceted, stylized read at almost no triangle cost.

## Tool args

- `forge_add` kinds: box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude. `count` makes instances of one shared prototype. Centimetres; `pos` is the base centre; `rot` is degrees; `material` is a name or id.
- `forge_mesh_op(op=...)`: extrude, inset, bevel, loopcut, subdivide (`smooth=true` rounds the whole object with Catmull-Clark, `levels` 1-4, for sculpting), decimate (`ratio` of vertices to keep, for budget), fill, merge, dissolve, delete, normals, shade, origin.
- `forge_join`: combines in world space; `weld=true` merges vertices closer than 0.05 cm. For one solid shell use `forge_boolean` union.
- `forge_boolean`: difference|union|intersect on closed meshes, concave included; `b` may be a list.
- `forge_select(query="top")` selects upward faces so the next edit hits them. `forge_modifier`: mirror or array; action add|apply|clear. `forge_layer`: collision layers export as UCX.
- `forge_batch`: each step is `{"tool": "forge_add", ...args without name}`; `$0` is step 0's id, `$0.ids` its id list; the first failing step undoes the whole batch.

## Merging pieces

| Goal | Use |
|---|---|
| One solid shell from overlapping parts (no inner faces) | `forge_boolean` `op="union"` with `b` as a list |
| Holes, slots, windows | `forge_boolean` `op="difference"` with every cutter in `b` |
| Keep only the overlap | `forge_boolean` `op="intersect"` |
| Several parts become one object but stay separate shells | `forge_join` (`weld=true` merges touching vertices) |
| Parts stay separate but travel together | `forge_group` |
| Split a mesh by material | `forge_separate` |
| Bring another scene's asset in | `forge_scene_merge(source=..., offset=[...])` |

Booleans need closed shapes. Planes and open grids give holes. Join and boolean results lose rig bindings, so bind afterwards.

## Kits and grids

Snap kit pieces to the 512 grid: walls 512 wide and 384 tall, floors 512 x 512. Keep each piece's pivot on its base (`forge_mesh_op op="origin"`, or `forge_fix`) so they snap in UEFN. `forge_add count=N` lays out N instances of one shared shape on a 512 grid: edit one and the others follow until you change it directly.

## Art direction

For a look (low-poly, stylized, horror, cartoon, voxel, and so on), load that Blender style pack's core, for example `skill_read_subskill("lowpoly-style", "core")`. Use its rules for silhouette, palette, and detail density. Ignore its bpy code and translate the intent into forge ops: fewer segments for low-poly, bevels and saturated palettes for stylized, emissive accents for sci-fi.
