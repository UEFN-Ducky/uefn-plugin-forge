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
