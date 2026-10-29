---
description: "Forge realistic human head: one head call shapes a CC0 MakeHuman head + neck with its sex, age and weight targets and 16 face sliders, adds eyeballs with cornea and iris parallax, teeth, tongue, brow and lash cards, a hair cap or draped long hair and SSS skin; proportions, hero pass (uv, bake, face_shapes, lods) and checks"
metadata:
  label: "Realistic head"
  default_enabled: false
  load_condition: "Building a realistic (not cartoon) human head or face, adding realistic eyes, teeth or skin, or making face blendshapes on a realistic head"
---

# Realistic head

One call builds a game-ready human bust: a scanned-proportion quad head with neck and ears, eyeballs, teeth, tongue, brows, lashes, hair and SSS skin. Cartoon faces use `cartoon_face` instead.

## Build (one batch)

```json
{"tool": "forge_batch", "name": "Hero", "look": "three", "steps": [
  {"tool": "forge_hq", "op": "head", "params": {"sex": "male", "age": "adult", "build": "average",
   "shape": {"jaw_width": 0.3, "nose_bridge": 0.4}, "tone": 0.35, "eye_color": "hazel", "hair": "short", "hair_color": "black"}}
]}
```

Result: `head adult male (base=makehuman) 12.1k tris 99% quads, eyes 2, teeth, 6 groups, short hair`.

| Param | Values |
|---|---|
| `base` | `makehuman` (default: the bundled CC0 MakeHuman head, real skull, ears and neck) · `procedural` (the older code-built head with nostril tunnels and a mouth bag) |
| `sex` | `male` · `female` · `neutral` (MakeHuman's macro targets, the three races averaged) |
| `age` | `child` (~11 y, 19 cm) · `adult` (~25 y, 24 cm) · `elder` (~70 y) |
| `build` | `slim` · `average` · `heavy` (weight targets + head fat) |
| `shape` | sliders −1..1: `face_width skull_depth jaw_width chin cheekbone brow nose_width nose_length nose_bridge nose_tip lip_full mouth_width eye_size eye_tilt eye_spacing ear_size` |
| `height_cm` | chin to crown (default by age) |
| `tone` | 0 light … 1 deep skin albedo |
| `eye_color` | `brown hazel green blue grey amber` |
| `hair` | `short` (default, 0.6 cm cap) · `buzz` (0.25 cm cap) · `long` (cap + strips that slide down the skull, swept to the sides and back, hanging to the neck) · `none` |
| `hair_color` | `black brown auburn blonde red white gray` (also brows and lashes) |

Each slider moves only its region (`nose_width` widens the nose and leaves the ears). Mix sliders for any face. Never encode ethnicity as a preset. Build the look from measured features of your reference.

## What you get (base=makehuman)

- **Head** (`<name>`): about 4.3k quads, closed. The neck is cut at the clavicle and capped. The bust stands on z=0, with the pivot at its base. Slots: `Skin Lips`.
- **Groups** for `face_shapes`: `eyes lips brows jaw nose cheeks`, with at most 4 per vertex.
- **`_eye_L/_R`**: the sclera and cornea dome are one shell. The iris and pupil sit under the cornea for real parallax, sized to MakeHuman's eye.
- **`_details`** (one hair material):
  - Brow cards: 3 staggered rows, ray-cast onto the brow.
  - Upper-lash cards on the lid margin.
  - The hair cap on the scalp faces. Its edge follows a smooth hairline (0.80 H at the forehead, 0.66 over the ears, 0.42 at the nape) and tapers onto the skin.
- **`_teeth_U/_L`**: teeth and gums fitted to MakeHuman's teeth boxes.
- **`_tongue`**: MakeHuman's tongue.
- **Skin**:
  - SSS weight 1 with radius R 1.0 / G 0.35 / B 0.2 cm.
  - A colour map gives a redder nose and cheeks, a beard shadow on adult males, and a hair tint under the cap edge.
  - A roughness map gives an oily T-zone (0.32) against cheeks (0.58).
  - The pore bump is procedural.
- **Materials**: 8 (the char budget).

## Proportions it holds

| Check | Value (H = chin to crown) |
|---|---|
| Head height | `height_cm` (adult 24 cm) |
| Eye line | 0.44–0.56 H (MakeHuman's measured average ≈ 0.53) |
| Neck | ≥ 3.5 cm below the chin, closed |
| Nose tip | on the mid-line, between the chin and the eyes |

## Hero pass (after `head`)

Order matters: `head` → `face_shapes` → `lods`. LOD1 and later drop shape keys, so add blendshapes before building LODs.

1. `{"op": "qa", "params": {"ids": ["<head ids>"], "profile": "uefn", "budget": "char"}}`: expect 0 errors. Scores:
   - Short hair 97, long hair 97, head alone 100.
   - The one warning is the open hair cards.
   - On `base: procedural`, about 85.
2. `{"op": "sculpt_hd", "id": "<head>", "params": {"levels": 3, "detail": "pores"}}` adds tertiary pores in a sidecar.
3. `{"op": "uv", "id": "<head>", "params": {"method": "seams", "res": 2048, "texel": 0}}`: `texel: 0` fills the map (about 72% packed). The default 512 px/m packs a 24 cm head into about 3% of the map.
4. `{"op": "bake", "id": "<head>", "params": {"from_high": true, "maps": ["normal"]}}` bakes a pore normal.
5. For a styled groom instead of the cap, use `"hair": "none"`. Then call `{"op": "hair", "id": "<head>", "params": {"style": "afro", "region": "top"}}` and `hair_cards`.
6. `{"op": "face_shapes", "params": {"ids": ["<head>"], "set": "arkit52"}}` adds blendshapes on the named groups.
7. `{"op": "lods", "id": "<head>", "params": {"ratios": [1, 0.5, 0.25, 0.1]}}`, last.
8. `{"op": "ref_compare", "params": {"id": "<head>", "front": "front.png", "side": "side.png", "min_iou": 0.85}}` checks likeness.

## Done when

- `look` front, three and side show:
  - a skull with a real back and ears level with the nose and brow;
  - a hair edge with no steps;
  - lips with a border, and a neck.
- `{"op": "topology", "id": "<head>"}` reports quads ≥ 95%.
- `qa` (step 1) scores ≥ 90 with 0 errors.
- `uv` reports more than 40% packed.

## Do / don't

- **Do** shape faces with the sliders and `sex`/`age`/`build`. They move only their own region, so the topology and loops stay intact.
- **Do** sculpt in order: primary forms, then secondary (wrinkles, folds), then tertiary (pores, baked).
- **Do** keep roughness 0.3–0.6 on skin. A single flat roughness reads as plastic.
- **Don't** fill the eye with one sphere. Without a cornea over a recessed iris, the eyes look dead.
- **Don't** push the SSS radius past about 1 cm on red, or the skin turns to wax.

---
Base head: MakeHuman base mesh and targets (CC0 1.0, github.com/makehumancommunity/makehuman); see THIRD_PARTY_NOTICES.md. Proportion canon (Loomis, Reilly) and topology practice summarised in our own words. Written for Forge, no third-party text copied.
