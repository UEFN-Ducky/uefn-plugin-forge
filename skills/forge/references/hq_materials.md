---
description: "Forge HQ materials: 46 one-word presets and category presets (metal, painted_metal, wood, stone, fabric, skin, plastic, glass), procedural wear layers, wiring a downloaded texture folder (Poly Haven, ambientCG, Substance, UE), the PBR lint, value ranges and material budgets"
metadata:
  label: "HQ materials"
  default_enabled: false
  load_condition: "Giving a Forge object a realistic material, tuning a preset, using a downloaded texture set, checking PBR values, or baking textures for UEFN, UE5, Unity or Godot"
---

# HQ materials

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "material", "params": {"material": "Hull", "preset": "painted_metal", "id": "hull"}},
  {"tool": "forge_hq", "op": "material", "params": {"material": "Rock", "textures": "C:/tex/rock_wall_2k", "preset": "stone", "id": "rock"}},
  {"tool": "forge_hq", "op": "light", "params": {"ids": ["hull", "rock"], "preset": "studio"}}
])
```

Done when: no `warnings` in the result (the PBR lint) and the `light` still reads as the material.

## Presets

One word. A category name picks its first preset (`metal` → worn_steel, `wood` → oak, `stone` → granite, `fabric` → cotton, `skin` → skin_light, `plastic` → plastic_white, `glass` → glass_clear). An unknown name answers "Did you mean".

- **metal:** worn_steel, painted_metal, polished_steel, brushed_aluminum, iron, aluminum, silver, titanium, copper, gold, rusty_iron, chrome
- **wood:** oak, walnut, pine, painted_wood · **stone:** granite, marble, concrete, brick
- **fabric:** cotton, leather, velvet · **skin:** skin_light, skin_tan, skin_dark · **scales:** scales_green, scales_red
- **plastic:** plastic_white, plastic_black, rubber, gloss (car paint / product polish) · **glass:** glass_clear, glass_frosted
- **emissive:** neon_blue, neon_pink, lava · **ground:** dirt, sand, snow, grass · **stylized:** toon_red, toon_blue, clay, candy, toon_gold

`overrides` patches base values (`color`, `metallic`, `roughness`, `emissive`, `emissive_strength`, `opacity`) or one layer type (`{"edge_wear": {"amount": 0.9}}`). `layers=[...]` replaces the preset's layers.

Layers: base, noise (`target` color|roughness|bump), edge_wear (`metal: true` shows bare metal under paint), dirt (cavities), scratches, triplanar, sss, clearcoat, normal (`image`, `dx`), image (`image`, `target` color|roughness|metallic|orm|emissive|opacity), wet (`amount`, `porosity`: albedo darkens up to 55 %, roughness drops, clear coat), snow (`amount`, `up` 0-1: faces pointing up get white, rough 0.6). Weather particles stay in UEFN (Niagara). Wear and dirt are Cycles masks: bake them before export.

## Texture folder

`textures: "<folder>"` wires a downloaded set by file name: `_diff/_Color/_BaseColor/_BC`, `_nor_gl/_NormalGL/_NormalDX/_N`, `_rough/_Roughness`, `_metal/_Metalness`, `_ao/_AmbientOcclusion`, `_arm/_ORM`, `_Emissive`, `_Opacity`. It copies them in as `T_<material>_BC/N/ORM/E/O.png`, packs separate AO, roughness and metallic into ORM, and flips the normal to the `target` convention (ue = DirectX). Height and displacement are skipped (warned).

## PBR lint and ranges

The result warns when: metallic is between 0 and 1, a metal's albedo is under sRGB 180, a non-metal's albedo is outside sRGB 30-240, or a map file feeds the wrong slot (a normal map as colour).

| Surface | Albedo sRGB | Metallic | Roughness |
|---|---|---|---|
| charcoal, rubber, tyre | 30-60 | 0 | 0.8-0.95 |
| wood, stone, brick, soil | 60-180 | 0 | 0.5-0.9 |
| plastic, paint | any hue, 40-230 | 0 | 0.2-0.6 (gloss 0.02 + clearcoat) |
| skin | 90-220 | 0 | 0.35-0.5 + sss |
| fresh snow, white paper | 220-240 | 0 | 0.6-0.9 |
| iron, steel, aluminium, silver, gold | 180-255 | 1 | 0.05 (chrome) - 0.6 (worn) |

Material budget per asset: hero 3-5, prop 1-2, modular kit 1 shared atlas, character 3-8 (skin, eyes, hair, cloth, gear). Paint wear, dirt and wet masks into vertex colours (R wear, G dirt, B wet) rather than adding materials.

## Bake

`bake` writes the engine maps (and AO, curvature, ID, thickness) into the material's textures. Cage, rays and artefacts: load `baking`.

---
Adapted from Blender Agent Skills by EL4CTEO (MIT): pbr-materials. Ducky pack `blender-materials`; metal F0 values from physicallybased.info.
