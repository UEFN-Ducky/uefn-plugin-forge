---
description: "Forge HQ hair, fur and cloth: strand hair styles, hair cards for games, baked cloth (cape, scarf, skirt, flag) and alpha fur shells"
metadata:
  label: "HQ hair and cloth"
  default_enabled: false
  load_condition: "Adding hair, a beard, fur or cloth to a Forge character, or converting strands to game-ready cards"
---

# HQ hair, fur and cloth

| op | params | Result |
|---|---|---|
| `hair` | id (the head), style short\|long\|ponytail\|afro\|fur\|beard, region, color | strands grown on the head region |
| `hair_cards` | id, cards 120, atlas 1K | strands to alpha cards: the engine-ready form |
| `cloth` | preset cape\|scarf\|skirt\|flag, pin, collide, frames 32, bake_to bones\|shapes | simulated, then baked to bones or shape keys as a clip |
| `fur_shells` | id, layers 8, length 2 | alpha-noise shells for short fur |

Order for a haired character: `recipe humanoid_base hair=...`, or rig first, then `hair` on the head, then `hair_cards` before export. Cloth uses `pin` for what stays attached and `collide` for the bodies it drapes over; a rig is required to bake to bones. Strands export to Alembic (`export` picks `.abc` for curve-only selections); cards, shells and baked cloth ship in the normal FBX/glTF.
