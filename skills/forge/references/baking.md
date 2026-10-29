---
description: "Forge baking: high-to-low normal, AO, curvature, ID and thickness maps with cage, extrusion and ray distance, the miss % check, lightmaps, sculpt_hd multires normals, bake prep (seams on hard edges, _low/_high, explode) and an artefact → fix table"
metadata:
  label: "Baking"
  default_enabled: false
  load_condition: "Baking a sculpt or high-poly onto a low-poly, making normal, AO, curvature, ID or thickness maps, or a bake shows waves, skew, black spots or seams"
---

# Baking

Order: **low-poly UVs (`retopo_uv`) → material → bake → look**. `bake` runs as a job: poll `forge_job`.

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "uv", "params": {"id": "crate_low", "method": "seams", "texel": 512, "res": 2048}},
  {"tool": "forge_hq", "op": "material", "params": {"material": "Crate", "preset": "painted_metal", "id": "crate_low"}},
  {"tool": "forge_hq", "op": "bake", "params": {"id": "crate_low", "from_high": "crate_high", "size": 2048,
    "maps": ["base_color", "normal", "orm", "curvature", "id"], "extrusion": 0.03, "margin_px": 16}}
], look="three")
```

Done when: the result says `miss` under 3 % with no warning, and `look` shows no seams or black spots.

## Maps

| map | File | What it is |
|---|---|---|
| `base_color`, `normal`, `orm`, `emissive`, `opacity` | `_BC _N _ORM _E _O` | the engine set; `orm` = R AO, G roughness, B metallic |
| `ao` | `_AO` | ambient occlusion alone, from the high-poly when given |
| `curvature` | `_CRV` | convex white, flat 50 % grey, concave dark; drives edge wear in a painter |
| `id` | `_ID` | one flat colour per material of the high-poly: masks for texturing |
| `thickness` | `_TH` | white = thick, dark = thin (ears, cloth, leaves): SSS and translucency |

`maps: "all"` bakes all nine.

`to: "vcol"` bakes `ao`, `base_color`, `curvature`, `thickness` or `id` into point colours `bake_<map>` instead of a texture: no UVs needed, good for mobile AO and LOD tints. `{"op": "bake", "params": {"id": "rock", "to": "vcol", "maps": ["ao"]}}`. `target`: ue / dx (DirectX green, default), unity, godot, gl.

## Lightmap

`to: "lightmap"` bakes combined light (Cycles, 32 samples) into `T_<name>_LM.png`, on the UV set `uv(lightmap=true)` makes (no overlaps, 0-1, wide gutters; Forge keeps that one set as the lightmap UV). The light is the scene's last `light` preset or env, else `noon`. Overlap over 0.5 % warns.

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "uv", "params": {"id": "crate", "method": "smart", "lightmap": true, "res": 512}},
  {"tool": "forge_hq", "op": "light", "params": {"id": "crate", "env": "golden"}},
  {"tool": "forge_hq", "op": "bake", "params": {"id": "crate", "to": "lightmap", "size": 512}}
])
```

Done when: `lightmap 512px T_Crate_LM.png` with no warning. UEFN builds its own lightmaps: bake one only for unlit or mobile materials.

## Multires (sculpt_hd)

`source: "multires"` bakes the normal from the `sculpt_hd` sidecar after checking it is this mesh's own Catmull-Clark levels (faces = corners × 4^(levels-1)); it says `N multires levels`. Blender 5 dropped the legacy Bake from Multires, so the top level reaches the base by rays: `miss`, `extrusion` and `ray` work as below. Other maps listed bake from the same sidecar.

```json
{"op": "sculpt_hd", "params": {"id": "rock", "levels": 3, "detail": "rock"}},
{"op": "bake", "params": {"id": "rock", "source": "multires", "maps": ["normal", "ao"], "size": 2048}}
```

## Cage and rays

| Param | Default | Means |
|---|---|---|
| `from_high` | none | another object id, a .blend path, or `true` for the `sculpt_hd` sidecar |
| `extrusion` | 0.03 | how far rays start outside the low-poly, as a share of its diagonal |
| `ray` | 0 (no limit) | max ray length, share of the diagonal; 2 × extrusion stops hits on far parts |
| `cage` | auto | `auto` pushes out along normals; an object id uses that copy of the low-poly (same topology) |
| `margin_px` | 16 | edge dilation; 2× the UV `margin_px` |

`miss` is the share of texels whose rays found no high-poly. Over 3 % it warns: raise `extrusion`, set a `cage`, or move the low-poly closer to the high.

## Prep

- Every hard edge (> 60°) is a UV seam: `uv(method="seams")` does this. A hard edge inside an island shows a line in the normal map.
- Name pairs `_low` / `_high`. Parts that touch or overlap: move them apart (explode) for the bake, the same offset on both.
- Mirrored halves: move one half +1 in U before the bake.
- Low-poly smooth shading with weighted normals; triangulate the same way as the export.

## Artefacts

| Looks like | Cause | Fix |
|---|---|---|
| wavy lines on cylinders | too few low-poly sides | more segments (24-32) or a cage |
| skewed bolts and details | rays follow smoothed normals | `cage` object, or a support loop near the edge |
| black or empty patches | rays miss | raise `extrusion` / `ray`; check `miss` |
| bumps upside down in UE | wrong green channel | `target: "ue"` (DX) or `"gl"` |
| seam lines | hard edge inside an island; margin too small | seam on hard edges; `margin_px` 16+ |
| banding | 8-bit normal on a smooth gradient | bake 2× the size, or add a little noise |
| detail from the wrong part | ray hits a neighbour | explode parts; `ray` 2 × extrusion |

---
Adapted from Blender Agent Skills by EL4CTEO (MIT): baking.
