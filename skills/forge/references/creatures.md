# Walking creatures: built vs generated

Two paths to a rigged, walking quadruped. Pick by look, not by speed.

| | Built (`creature`) | Generated (`creature_from_prompt`) |
|---|---|---|
| Calls | 2 (`forge_batch` x2) | 1 |
| Time | about 20 s | minutes (provider queue) + about 20 s cleanup/rig |
| Credits | none | spends Tripo/Meshy credits per run |
| Result | clean quads, known anatomy, UV'd face | organic look, messy topology; cleanup retopos it |
| Choose when | you need control, budget, or no credits | you need a specific species or look from a prompt |

Always `look="three"` on the last call: one Eevee image plus a `seen` line (tris, materials). A `seen` ending in `fallback renderer` means the picture is not a quality judgement.

## Built: lion in 2 calls

Call 1 returns the body id. Faces stay low (2500): fur shells add the body's tris again per layer, and UEFN's budget is 20k.

```json
{"name": "Lion_Built", "steps": [{"tool": "forge_hq", "op": "creature", "params": {"kind": "big_cat", "length": 220, "faces": 2500}}]}
```

Call 2: mane on the head (sphere region in world cm, around the eyes), cards, undecimated shells, baked fur, rig, weights, walk. Replace `BODY` and `[0,0,0]` with the body id and head position from call 1.

```json
{"name": "Lion_Built", "steps": [
  {"tool": "forge_hq", "op": "hair", "id": "BODY", "params": {"style": "mane", "region": {"sphere": [0, 0, 0, 48]}}},
  {"tool": "forge_hq", "op": "hair_cards", "id": "BODY", "params": {}},
  {"tool": "forge_hq", "op": "fur_shells", "id": "BODY", "params": {"layers": 2, "decimate_shells": false, "budget": 20000}},
  {"tool": "forge_hq", "op": "fur_bake", "id": "BODY", "params": {"size": 512}},
  {"tool": "forge_hq", "op": "rig_template", "params": {"template": "quadruped", "ids": ["BODY"]}},
  {"tool": "forge_hq", "op": "auto_weights", "params": {"ids": ["BODY"]}},
  {"tool": "forge_hq", "op": "clip", "params": {"kind": "walk"}}
], "look": "three"}
```

Expect about 19.6k tris, 25 bones, a looping Walk. Over budget: lower `faces` or `layers`, never raise `budget`.

## Generated: lion in 1 call

```json
{"name": "Lion_Generated", "steps": [{"tool": "forge_hq", "op": "creature_from_prompt", "params": {"prompt": "adult male lion, full mane, standing, realistic", "kind": "quadruped", "faces": 12000, "provider": "tripo"}}], "look": "three"}
```

Spends credits and takes minutes: check the provider balance first, and run it once per asset. Re-run `clip` or `auto_weights` alone to change the animation; do not generate again.
