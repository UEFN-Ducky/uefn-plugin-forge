---
description: "Forge HQ mesh ops on the Blender engine: remesh, retopo, sculpt_hd (high-res detail in a sidecar), modifiers, lods"
metadata:
  label: "HQ mesh"
  default_enabled: false
  load_condition: "Cleaning topology, adding high-poly detail, applying a modifier stack, or making LODs on a Forge object"
---

# HQ mesh

All through `forge_hq(op=..., id=<object>, params={...}, look=...)`. Each op is one undo step; the Blender engine installs itself on first use (the call answers `installing` with a progress, then call again).

| op | params (defaults) | Use |
|---|---|---|
| `remesh` | voxel 0.05, adaptivity 0 | even quads from a messy boolean/sculpt; keeps UVs |
| `retopo` | faces 4000, symmetry false | clean low-poly from a dense mesh |
| `sculpt_hd` | levels 3 (1-6, 4x faces each), detail rock\|pores\|scales\|wood\|cloth, strokes, path | detail in a sidecar .blend; the base mesh stays light. Feeds `bake from_high=true` |
| `modifiers` | stack [] | apply a modifier stack in order |
| `lods` | ratios [1, .5, .25, .1], nanite false | LOD chain |

`help` with `params={op: "retopo"}` prints one op's signature. Typical asset: model native, `sculpt_hd`, `retopo`, `material` + `bake from_high=true`, `lods`.
