---
description: "Forge retopology and UVs for game assets: retopo then topology check (quads, poles, ngons), edge-flow rules, uv unwrap with seams on sharp edges packed to a texel target (px/m), margins per resolution, lightmap-safe UVs, and the UV report"
metadata:
  label: "Retopo and UVs"
  default_enabled: false
  load_condition: "Retopologising a sculpt or AI mesh, checking edge flow or poles, unwrapping for a bake, setting texel density, lightmap UVs, or UVs overlap or stretch"
---

# Retopo and UVs

Order: **retopo → topology → uv → bake**. Unwrap again after any topology change.

## One batch

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "retopo", "params": {"id": "body", "faces": 4000, "symmetry": true}},
  {"tool": "forge_hq", "op": "topology", "params": {"id": "body"}},
  {"tool": "forge_hq", "op": "uv", "params": {"id": "body", "method": "seams", "texel": 512, "res": 2048}}
], look="three")
```

Done when: `topology` says 90 %+ quads and 0 ngons, and `uv` says `0.0% overlap`, texel within 10 % of the target, `spread` ≤ 1.3. Check later with `{"op": "uv", "params": {"id": "body", "check": true}}`.

## retopo and topology

| Mesh | Do |
|---|---|
| sculpt, scan, organic | `remesh` (voxel ~1 % of height) then `retopo(faces, symmetry)`; re-UV after |
| AI or heavy mesh you keep the UVs of | `lods` ratios (decimate keeps UVs) |
| hard surface | model it clean; `modifiers` bevel + weighted_normal, no retopo |

`topology(id)` → `faces, quads %, tris, ngons, poles`. A pole is an inner vertex with 3 or 5+ edges.

Edge flow for anything that bends:
- 3 edge loops at every joint (elbow, knee, finger, jaw); 3+ rings round eyes and mouth.
- Keep poles off the bend: put them on flat spans (chest, back of the head).
- Even quads where it deforms; triangles only on flat, hidden spots.
- Budget by tier: see `modeling_recipes`.

## uv

`uv(id, method, angle=60, res=2048, texel=512, margin_px, lightmap=false, check=false)`

| method | Cuts | Use for |
|---|---|---|
| `seams` | edges sharper than `angle` degrees | hard surface, props (default). A closed smooth part with no sharp edge falls back to smart |
| `smart` | by face angle | organics, rocks, quick bakes |
| `min_stretch` | `seams`, then minimum-stretch unwrap | curved panels, cloth |

- `texel` is pixels per metre on a `res` texture: mobile 256, third-person game 512, hero / first-person 1024. `texel: 0` fills the square.
- `margin_px` defaults to res / 256: 1k → 4, 2k → 8, 4k → 16 px. Bake `margin_px` (dilation) should be 2× this.
- Too big for the texture: it packs to fill and warns `does not fit`. Raise `res` or lower `texel`.
- `lightmap: true`: 0-1, no overlaps, margin 0.02, fills the square. Forge keeps one UV set, so this UV0 is lightmap-safe; UEFN builds the lightmap channel from it on import.
- Mirrored halves may share UVs (overlap is fine there); move one half +1 in U before a bake so the baker sees one copy. Never overlap on a lightmap.

Result: `6 islands, 0.0% overlap, 512 px/m, spread 1.00, 41% packed`. Warnings: overlap, texel off target, spread > 1.3.

Seams go where nobody looks: under arms, inside legs, back of the head, along hard edges. Hard edges must be seams, or the normal bake shows a line.

---
Adapted from Blender Agent Skills by EL4CTEO (MIT): retopology-uv. Ducky packs `retopology`, `uv-workflow`.
