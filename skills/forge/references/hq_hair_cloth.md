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
| `cloth` | preset cape\|scarf\|skirt\|flag, fabric cotton\|denim\|leather\|silk\|rubber, pin, collide, frames 32, bake_to bones\|shapes | simulated, then baked to bones or shape keys as a clip; `preset="denim"` alone = a denim cape |
| `fur_shells` | id, layers 8, length 2, budget 20000 | alpha-noise shells for short fur |
| `fur_bake` | id, size 1024, coat lion | fur colour streaks + direction normal baked into BaseColor and Normal: texture-only fur, 0 extra tris |
| `tufts` | id, mane lion \| target tail + at, style leaf (default) \| flame, count 160, rows 6, length | layered leaf tufts (mane, tail tip) on their own Mane material; flame = pointed blades fused into the host |

## Fabrics

`{"op": "cloth", "params": {"preset": "denim", "pin": "shoulders", "frames": 8}}` → `cape denim: 6-bone chain, 8-frame clip`, plus `drop` (cm below the pin; stiffer fabric drops less).

| fabric | g/m² | tension / shear | bending | damping | Use |
|---|---|---|---|---|---|
| `silk` | 60 | 5 | 0.05 | 0 | scarves, flowing capes |
| `cotton` | 150 | 15 | 0.5 | 5 | shirts, flags |
| `denim` | 400 | 40 | 10 | 25 | jackets, heavy capes |
| `leather` | 900 | 80 | 150 | 25 | coats, belts, armour flaps |
| `rubber` | 1200 | 15 | 25 | 25 | tarps, rain capes |

Numbers are Blender's cloth presets (stiffness, damping) plus fabric weight; heavier fabric flutters less in `flag` wind. No fabric = the old rigid sheet.

Order for a haired character: `recipe humanoid_base hair=...`, or rig first, then `hair` on the head, then `hair_cards` before export. Cloth uses `pin` for what stays attached and `collide` for the bodies it drapes over; a rig is required to bake to bones. Strands export to Alembic (`export` picks `.abc` for curve-only selections); cards, shells and baked cloth ship in the normal FBX/glTF.
