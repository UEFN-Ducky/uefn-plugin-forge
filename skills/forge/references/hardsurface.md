---
description: "Hard-surface modeling in Forge: bevel + weighted-normal stack presets (midpoly, subd, render), applied booleans with cleanup, and detail kits (panel lines, vents, bolts, grilles) cut along the surface normal"
metadata:
  label: "Hard surface"
  default_enabled: false
  load_condition: "Modeling machines, weapons, vehicles, sci-fi props, crates, panels, vents, bolts, or anything with bevels and booleans in Forge"
---

# Hard surface

Order: block-out (native `forge_add`) → `boolean` cuts → `hardsurface` details → `modifiers` stack → `lods`. Lengths are cm. All ops run as `forge_hq` steps in one `forge_batch`.

## Ops

| op | params (defaults) | Result |
|---|---|---|
| `boolean` | `cutters` [ids or `{shape: box\|cylinder\|sphere, size: cm or [x,y,z], at: [x,y,z], rot: [deg], seg: 32}`], `mode` difference\|union\|intersect, `solver` exact\|manifold\|fast, `hide` true | `difference x2 (exact): 412 tris, manifold`. Welds, drops slivers, fixes normals. Id cutters are hidden, not deleted |
| `hardsurface` | `preset` panel\|vent\|bolt\|grille, `at` [x,y,z] (default top centre), `size` (30 % of the object), `depth` (1 %), `count` (panel 2 = 2x2, vent 5, grille 4 = 4x4, bolt 4), `radius` | `panel x4, depth 0.4: 188 tris`. Cuts along the normal of the surface nearest `at`; bolts are joined on and stand `depth` proud |
| `modifiers` | `stack` = `midpoly`\|`subd`\|`render` or a list | Non-destructive, stored on the node |

Stack presets (bevel width = 1 % of the largest side, angle 30°, clamp overlap):

| preset | Stack | Use |
|---|---|---|
| `midpoly` | bevel 3 seg + harden normals → weighted normal (face area, keep sharp) | game props: the default |
| `subd` | bevel 2 seg at half width (support loops) → subsurf 2 | smooth hero shapes, renders |
| `render` | bevel 5 seg + harden → weighted normal | close-up stills |

List items also take `{"type": "bevel", "weight_angle": 45}` (bevel only edges sharper than 45°) and `"triangulate"` (last, for export checks). Unwrap UVs before the bevel: the stack keeps them.

Segments: bolts 6-8, handles 12-16, barrels 24-32 (`seg` on a cutter). Cutters must pass through the surface by a few cm on both sides, or the cut leaves skins.

## Sci-fi crate in one batch

```json
[
  {"tool": "forge_material_set", "material": "Hull", "base_color": [0.32, 0.36, 0.4], "metallic": 0.8, "roughness": 0.45},
  {"tool": "forge_add", "kind": "box", "object_name": "SM_Crate", "params": {"w": 100, "d": 100, "h": 80}, "pos": [0, 0, 0], "material": "$0"},
  {"tool": "forge_hq", "op": "boolean", "id": "$1", "params": {"cutters": [{"shape": "box", "size": [110, 20, 8], "at": [0, 0, 80]}]}},
  {"tool": "forge_hq", "op": "hardsurface", "id": "$1", "params": {"preset": "panel", "at": [0, -60, 40], "size": 70, "depth": 0.6}},
  {"tool": "forge_hq", "op": "hardsurface", "id": "$1", "params": {"preset": "vent", "at": [60, 0, 40], "size": 40, "depth": 1.5}},
  {"tool": "forge_hq", "op": "hardsurface", "id": "$1", "params": {"preset": "bolt", "at": [0, -60, 40], "size": 80, "depth": 1, "radius": 2}},
  {"tool": "forge_hq", "op": "modifiers", "id": "$1", "params": {"stack": "midpoly"}}
]
```

Run it with `forge_batch(steps=[...], look="three")`.

**Done when:** the `look` shows crisp grooves and rounded edge highlights from the front and side, every `boolean`/`hardsurface` summary says `manifold` (no `OPEN edges`), and tris fit the budget in [modeling_recipes](modeling_recipes.md).

## Problems

| Symptom | Fix |
|---|---|
| `OPEN edges` after a cut | cutter grazes a face: move it or make it longer; or `solver: "manifold"` on closed meshes |
| Shading streaks after a boolean | add `modifiers stack="midpoly"` (weighted normals) |
| Bevel eats small parts | lower `width` in a list stack, or use `weight_angle` 60 |
| Cut lands on the wrong face | give `at` just outside the face you want |

---
Adapted from Blender Agent Skills (hardsurface-modeling) by EL4CTEO, MIT licence.
