---
description: "Forge walking creatures: a rigged quadruped (creature, hair, hair_cards, fur_shells, fur_bake, rig_template, auto_weights, clip) in 2 calls, plus paint_strokes fur shading and ref_parts colouring from a drawing"
metadata:
  label: "Creatures"
  default_enabled: false
  load_condition: "Building a rigged, walking animal or creature (lion, big cat, dog, quadruped), its fur or mane, or colouring it from a reference drawing"
---

# Walking creatures: a built lion

A rigged, walking quadruped built only with Forge's own ops, default settings, no workarounds.

Always `look="three"` on the last call: one Eevee image plus a `seen` line (tris, materials). A `seen` ending in `fallback renderer` means the picture is not a quality judgement.

## Built: lion in 2 calls

Call 1 returns the body id. Defaults give smooth-shaded lion proportions.

```json
{"name": "Lion_Built", "steps": [{"tool": "forge_hq", "op": "creature", "params": {"kind": "big_cat"}}]}
```

Call 2: mane on the head (sphere region in world cm, around the eyes), cards, fur shells (default decimate), lion coat bake, rig, weights, walk. Replace `BODY` and `[0,0,0]` with the body id and head position from call 1.

```json
{"name": "Lion_Built", "steps": [
  {"tool": "forge_hq", "op": "hair", "id": "BODY", "params": {"style": "mane", "region": {"sphere": [0, 0, 0, 48]}}},
  {"tool": "forge_hq", "op": "hair_cards", "id": "BODY", "params": {}},
  {"tool": "forge_hq", "op": "fur_shells", "id": "BODY", "params": {}},
  {"tool": "forge_hq", "op": "fur_bake", "id": "BODY", "params": {"coat": "lion"}},
  {"tool": "forge_hq", "op": "rig_template", "params": {"template": "quadruped", "ids": ["BODY"]}},
  {"tool": "forge_hq", "op": "auto_weights", "params": {"ids": ["BODY"]}},
  {"tool": "forge_hq", "op": "clip", "params": {"kind": "walk"}}
], "look": "three"}
```

Stays under UEFN's 20k tris with a looping Walk. Over budget: lower `faces` on `creature`; never raise `budget`. Re-run `clip` or `auto_weights` alone to change the animation.

## paint_strokes: painted fur instead of fur_bake

Swap `fur_bake` for hand-painted strokes on the body (BaseColor + Normal). `flow: "down"` for the coat, `"tufts"` for a mane object.

```json
{"tool": "forge_hq", "op": "paint_strokes", "id": "BODY", "params": {"base": "#c89048", "shadow": "#6e4420", "highlight": "#ecd09a", "flow": "down", "density": 1.2, "ao": 0.6, "size": 2048}}
```

Done when: `N strokes, 80%+ covered` in the summary and the `look` shows strokes along the body.

## ref_parts: colour the creature from a drawing

Gives each face the colour of the front drawing behind it (`k` clusters; `palette` pins names). Run it before `rig_template`; `split=true` only for a blockout, never on a rigged body.

```json
{"tool": "forge_hq", "op": "ref_parts", "params": {"id": "BODY", "front": "C:/refs/lion_front.png", "k": 4, "palette": [{"name": "coat", "color": "#c89048"}, {"name": "mane", "color": "#7a4019"}]}}
```

Done when: the summary lists each part with its face count and `look view=front` matches the drawing.
