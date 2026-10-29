---
description: "Forge HQ mesh ops on the Blender engine: remesh, retopo, sculpt, sculpt_hd (high-res detail in a sidecar), modifiers (midpoly/subd/render presets), boolean, hardsurface, origin, lods"
metadata:
  label: "HQ mesh"
  default_enabled: false
  load_condition: "Cleaning topology, adding high-poly detail, applying a modifier stack, or making LODs on a Forge object"
---

# HQ mesh

All through `forge_hq(op=..., id=<object>, params={...}, look=...)`. Each op is one undo step; the Blender engine installs itself on first use (the call answers `installing` with a progress, then call again).

| op | params (defaults) | Use |
|---|---|---|
| `remesh` | voxel 5 (cm), adaptivity 0 | watertight even mesh from a messy boolean/sculpt; keeps UVs. About 1 % of the height (2 on a 2 m figure) |
| `retopo` | faces 4000, symmetry false | clean low-poly from a dense mesh |
| `sculpt_hd` | levels 3 (1-6, 4x faces each), detail rock\|pores\|scales\|wood\|cloth, strokes, path | detail in a sidecar .blend; the base mesh stays light. Feeds `bake from_high=true` |
| `sculpt` | ops [remesh:cm, inflate:cm, smooth:n, symmetrize:+x, detail:{strength, scale, seed}, multires:n] | headless sculpt chain on the base mesh, see [sculpt_shapes](sculpt_shapes.md) |
| `modifiers` | stack [] or `midpoly`\|`subd`\|`render`; types bevel subsurf solidify weighted_normal displace array mirror triangulate; bevel `weight_angle` | non-destructive stack, see [hardsurface](hardsurface.md) |
| `boolean` | cutters [ids or {shape, size, at}], mode difference, solver exact | applied cut + cleanup |
| `hardsurface` | preset panel\|vent\|bolt\|grille, at, size, depth, count | detail kit along the surface normal |
| `origin` | at bottom\|center\|corner, ground false | pivot moves, object stays; `ground` puts its lowest point on z = 0 (imports, kit pieces) |
| `lods` | ratios [1, .5, .25, .1], nanite false | LOD chain |

`help` with `params={op: "retopo"}` prints one op's signature. Typical asset: model native, `sculpt_hd`, `retopo`, `material` + `bake from_high=true`, `lods`.
