---
description: "Forge HQ materials: 40 one-word presets (metal, wood, stone, fabric, skin, scales, plastic, glass, emissive, ground), layer specs with overrides, and baking to engine-ready PNGs"
metadata:
  label: "HQ materials"
  default_enabled: false
  load_condition: "Giving a Forge object a realistic material, tuning a preset, or baking textures for UEFN, UE5, Unity or Godot"
---

# HQ materials

`forge_hq(op="material", id=<object>, params={material: "Name", preset: "worn_steel"})` makes the material and assigns it. Presets are one word; an unknown one answers "Did you mean" and lists them.

- **metal:** worn_steel, polished_steel, brushed_aluminum, copper, gold, rusty_iron, chrome
- **wood:** oak, walnut, pine, painted_wood · **stone:** granite, marble, concrete, brick
- **fabric:** cotton, leather, velvet · **skin:** skin_light, skin_tan, skin_dark · **scales:** scales_green, scales_red
- **plastic:** plastic_white, plastic_black, rubber · **glass:** glass_clear, glass_frosted
- **emissive:** neon_blue, neon_pink, lava · **ground:** dirt, sand, snow (plus more: an unknown name lists all)

`overrides` patches the preset: base values (`color`, `metallic`, `roughness`, `emissive`, `emissive_strength`, `opacity`) or one layer type's params (`{"edge_wear": {"amount": 0.9}}`). `layers=[...]` replaces the preset's layers. Layer types: base, noise (`scale`, `detail`, `amount`, `target` color|roughness|bump), edge_wear, dirt, triplanar, scratches, sss, clearcoat, normal. A bad layer fails before anything is stored.

## Bake

`forge_hq(op="bake", id=..., params={size: 1024, maps: "all", from_high, target: "ue"})` writes BaseColor, Normal, ORM, Emissive and Opacity PNGs into the material's textures (a job; the call returns `job`, poll with `forge_job`). `size` is a power of two, 64-8192. `from_high` is another object id, a .blend path, or `true` for the `sculpt_hd` sidecar. `target`: ue (DirectX normals), dx, unity, godot, gl. Unwraps first if the object has no UVs.
