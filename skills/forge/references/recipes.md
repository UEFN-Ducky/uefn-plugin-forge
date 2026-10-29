---
description: "Forge recipes: whole parametric assets (crate, well, humanoid, creature, tree, sword...) built by one forge_hq(op='recipe') call, their params, and how to write one"
metadata:
  label: "Recipes"
  default_enabled: false
  load_condition: "Building a common asset in Forge in one call, or adding a recipe"
---

# Recipes

`forge_hq(op="recipe", params={recipe: "<name>", ...params}, look="three")` runs a recipe as one undo step. `recipe=""` lists them with their params. Unknown names and params answer with "Did you mean" and the valid list.

| Recipe | Params (defaults) | Makes |
|---|---|---|
| `scifi_crate` | size 100, metal worn_steel, glow neon_blue | panelled crate with a glowing strip |
| `stone_well` | radius 80, height 90, stone granite, wood oak | well with posts and roof |
| `humanoid_base` | height 180, skin skin_tan, cloth cotton, hair none | rigged T-pose biped with idle clip; hair: short, long, ponytail, afro, fur, beard |
| `robot_walker` | height 180, body polished_steel, glow neon_blue | rigged biped with a walk cycle |
| `creature_quadruped` | length 160, height 90, skin scales_green | rigged on the quadruped template, running clip |
| `tree_stylized` | height 400, bark walnut, leaf [r,g,b] | trunk and two crowns |
| `weapon_sword` | length 82, steel polished_steel, grip leather | tip-up sword |
| `barrel`, `table`, `lamp_post`, `wall_door`, `treasure_chest`, `wheel_wood` | size and material presets | props |
| `rock_boulder` | radius 60, stone granite | sculpted, decimated, smart-unwrapped |
| `crystal_cluster`, `mushroom_giant` | size/height, glow or cap colour | set dressing |

## Pipeline recipes

Each pipeline recipe ends with `qa` (score in the summary). Call it with `look="three"` to see the result.

```json
forge_batch(name="Props", steps=[{"tool": "forge_hq", "op": "recipe", "params": {"recipe": "prop_midpoly", "material": "oak"}}], look="three")
```

| Recipe | Params (defaults) | Pipeline |
|---|---|---|
| `prop_midpoly` | w 60, d 60, h 50, material worn_steel, bevel 1.5 | box → bevel(2 seg) + weighted normals → smart UV → pivot on floor → `qa uefn prop` |
| `prop_baked` | size 60, material worn_steel, detail panel\|vent\|bolt\|grille, res 1024 | low box + panelled high copy → smart UV → `bake from_high` → high deleted → pivot → `qa` |
| `ai_cleanup` | mesh (object id, required), faces 1400 (quads, ~2.8k tris), res 1024, budget prop | weld (`forge_fix`) → quadriflow copy → smart UV → bake the original onto it → original deleted → pivot → `qa` |
| `character_game` | height 180, skin skin_tan, cloth cotton | blocked body → `rig_template ue5_manny` → `auto_weights uefn` → idle + walk clips → `rig_check` → `qa uefn char` |

`ai_cleanup` is for meshes you imported or scanned (`forge_import_mesh`); it never generates one. Done when: the summary's `qa` score is 80 or more and the look shows the shape you meant.

Material params are preset names (see `hq_materials`). Change a look after the fact with `forge_hq(op="material", id=..., params={preset: ...})`.

## Writing a recipe

Add `assets/recipes/<name>.json`: `{"about", "params": {name: default}, "let": {name: "=expr"}, "steps": [forge_batch steps]}`. In steps, a whole string `"$name"` is a param, `"=a*b+2"` is arithmetic over params and `let` values, and `"$0"` / `"$0.ids"` stay batch step refs. A step with `"if": "<param>"` is skipped when the param is unset or "none": keep those last so step numbers do not move. Add the recipe to `test_tokens`' run (it runs every file) and keep material, rig and hair choices as params.
