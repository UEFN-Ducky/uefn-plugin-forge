---
description: "Forge realistic human head: one head call (sex, age, build, 14 anatomy sliders, tone, eye colour) builds a quad head with eye, mouth and nasolabial loops, eyeballs with cornea and iris parallax, teeth, tongue, mouth bag, ears and SSS skin; proportions, hero pass (uv, bake, face_shapes, lods) and checks"
metadata:
  label: "Realistic head"
  default_enabled: false
  load_condition: "Building a realistic (not cartoon) human head or face, adding realistic eyes, teeth or skin, or making face blendshapes on a realistic head"
---

# Realistic head

One call builds a game-ready human head: a quad shell with real edge loops, eyeballs, teeth, tongue, mouth bag, ears and SSS skin. Cartoon faces use `cartoon_face` instead.

## Build (one batch)

```json
{"tool": "forge_batch", "name": "Hero", "look": "three", "steps": [
  {"tool": "forge_hq", "op": "head", "params": {"sex": "male", "age": "adult", "build": "average",
   "shape": {"jaw_width": 0.3, "nose_bridge": 0.4}, "tone": 0.35, "eye_color": "hazel"}}
]}
```

Result: `head adult male 11.7k tris 97% quads, eyes 2, teeth, mouth bag, 6 groups` (all parts; the head shell is about 7k).

| Param | Values |
|---|---|
| `sex` | `male` (heavier brow, jaw, chin) · `female` (softer brow, fuller lips) · `neutral` |
| `age` | `child` (19 cm, face lower on the skull, big eyes) · `adult` (24 cm) · `elder` (deep folds, thin lips, bigger nose/ears) |
| `build` | `slim` · `average` · `heavy` (cheek fat, wider jaw) |
| `shape` | sliders −1..1: `face_width skull_depth jaw_width chin cheekbone brow nose_width nose_length nose_bridge lip_full mouth_width eye_size eye_tilt eye_spacing` |
| `tone` | 0 light … 1 deep skin albedo |
| `eye_color` | `brown hazel green blue grey amber` |

Mix sliders for any face. Never encode ethnicity as a preset. Build the look from measured features of your reference.

## What you get

- **Head**: about 7k tris, 90% quads or more. The grid crowds 2x toward the face, so the forms have resolution:
  - Nose: bridge with flat sides, nasion dip, supratip break, alar wings with a groove round them.
  - Brow ridge with a sharp lower edge; zygoma and submalar hollow; square jaw angle; chin pad.
  - Mouth: philtrum columns and groove, mouth-corner pits.
  - Loops: circular loops round each eye (4 lid rings, the last hugging the eyeball, an upper-lid crease) and round the mouth (5 rings: nasolabial, cheek, vermilion border, lip middle, seam). 5-poles sit at the patch corners, outside the loops.
  - Bags and holes: an eye-socket bag, an inner mouth bag, and **2 nostril holes** with tunnels. The tunnels are open deep inside the nose; they are the only open edges.
  - Slots: `Skin Lips MouthBag EyeSocket`.
- **Groups** for `face_shapes`: `eyes lips brows jaw nose cheeks`.
- **`_eye_L/_R`**: the pivot sits at the eyeball centre (rotate it to aim). The sclera and cornea dome are one shell. The iris and pupil disc sit 2 mm under the cornea, which gives real parallax.
- **`_details`**: caruncles and lower-lid tear lines, plus hair cards:
  - Eyebrows: 3 staggered rows per brow. Hairs rise at the head and sweep out to the tail; the tail thins to 2 rows.
  - Lashes: one card per lid-margin vertex. Upper lashes are 0.024 H and curl up; lower lashes are 0.011 H, every other vertex.
  - For groomed hair on the scalp, use `hair` then `hair_cards`.
- **`_teeth_U/_L`**: teeth on a parabolic arch, plus gums.
- **`_tongue`**.
- **`_ears`**: thick helix, antihelix, deep concha, tragus, lobe. They use their own redder `Ears` slot.
- **Skin**:
  - SSS weight 1 with radius R 1.0 / G 0.35 / B 0.2 cm.
  - A colour map gives a redder nose and cheeks, plus a blue-grey beard shadow on adult males.
  - A roughness map gives an oily T-zone (0.32) against cheeks (0.58), with noise breakup on top.
  - The pore bump is procedural. For sculpted pores, run `sculpt_hd` with `pores` and bake (hero pass).
- **Cornea**: wet (opacity 0.06 + clear coat).

## Proportions it holds (Loomis / Reilly)

| Check | Value (H = chin to crown) |
|---|---|
| Eye line | 0.5 H |
| Brow, nose base, chin | thirds: 0.585, 0.30, 0 |
| Mouth | one third from nose base to chin (0.205 H) |
| Eye gap | one eye width (fissure about 0.125 H) |
| Mouth corners | under the irises |
| Ears | brow to nose base, just behind the head centre |

## Hero pass (after `head`)

Order matters: `head` → `face_shapes` → `lods`. LOD1 and later drop shape keys, so add blendshapes before building LODs.

1. `{"op": "qa", "params": {"ids": ["<head ids>"], "profile": "uefn", "budget": "char"}}`: expect a score of 85 with 0 errors (head alone: 97, the nostril tunnels). The warnings left are intended: the eye pivots sit at the eyeball centre so the eyes can aim, the cards and tear lines are open strips, the nostrils are open, and the set uses 16 slots.
2. `{"op": "sculpt_hd", "id": "<head>", "params": {"levels": 3, "detail": "pores"}}` adds tertiary pores in a sidecar.
3. `{"op": "uv", "id": "<head>", "params": {"method": "seams", "res": 2048, "texel": 0}}`: `texel: 0` fills the map (about 47% packed, 3.9k px/m). The default 512 px/m packs a 24 cm head into about 3% of the map.
4. `{"op": "bake", "id": "<head>", "params": {"from_high": true, "maps": ["normal"]}}` bakes a pore normal.
5. `{"op": "hair", "id": "<head>", "params": {"style": "short"}}`, then `hair_cards`.
6. `{"op": "face_shapes", "params": {"ids": ["<head>"], "set": "arkit52"}}` adds blendshapes on the named groups.
7. `{"op": "lods", "id": "<head>", "params": {"ratios": [1, 0.5, 0.25, 0.1]}}`, last.
8. `{"op": "ref_compare", "params": {"id": "<head>", "front": "front.png", "side": "side.png", "min_iou": 0.85}}` checks likeness.

## Done when

- `look` front, three and side show the eyes on the half line, lips with a border, and ears level with the nose and brow.
- `{"op": "topology", "id": "<head>"}` reports quads ≥ 85%.
- `qa` (step 1) scores ≥ 85 with 0 errors.
- `uv` reports more than 40% packed.

## Do / don't

- **Do** keep loops at valence 4 on the eye and mouth rings. Park poles outside them, where they never deform.
- **Do** sculpt in order: primary forms, then secondary (wrinkles, folds), then tertiary (pores, baked).
- **Do** keep roughness 0.3–0.6 on skin. A single flat roughness reads as plastic.
- **Don't** fill the eye with one sphere. Without a cornea over a recessed iris, the eyes look dead.
- **Don't** leave the mouth as a slit. The bag, teeth and tongue are what make a smile or viseme read.
- **Don't** push the SSS radius past about 1 cm on red, or the skin turns to wax.

---
Proportion canon: Andrew Loomis and Steve Huston / Reilly method, summarised in our own words. Topology practice is common game-art knowledge (edge loops for FACS deformation). Written for Forge, no third-party text copied.
