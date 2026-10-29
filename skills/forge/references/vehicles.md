---
description: "Cars and car parts in Forge: every part on its own and swappable (wheel, tire from a size code, rim spokes, brake disc, caliper, lights, grille, mirror, door, hood, trunk, bumpers, fender, spoiler, diffuser, exhaust, seat, steering wheel, dashboard, engine, suspension, plate, wiper, window), whole cars from a body style (sedan to limo and kart) with panel gaps, UE wheel bones, LODs and UCX, and car_swap"
metadata:
  label: "Vehicles"
  default_enabled: false
  load_condition: "Making a car, limo, truck, kart or any vehicle, or a single car part (wheel, tire, rim, headlight, door, bumper, spoiler, seat, engine...) in Forge, or swapping a part on a car"
---

# Vehicles

Three `forge_hq` ops, all cm. Car frame: **-Y is the front, +X the car's left, Z up, tyres on z=0.** Every part's origin is its mount point and `node.sockets` names it.

| op | params (defaults) | Result |
|---|---|---|
| `car_part` | `kind`, `style` sedan, `tire` by style, `spokes`, `tread` 64, `disc` mm, `side` left, `width`/`height`/`length` cm, `text`, `region` eu\|us, `at`, `name`, `paint` | `car_part tire sedan 225/45R17 r=31.7cm, tread 64, 10.9k tris, socket Hub` |
| `car` | `body` sedan, `style` (from body), `length_cm`, `wheelbase_cm`, `track_cm`, `doors`, `parts` {}, `interior` true, `paint`, `name`, `at` | `car limo 717x190x149cm, 58 parts, 4 wheels rigged, 90.1k tris, LOD x4, UCX 1` |
| `car_swap` | `car` group id, `slot`, then any `car_part` param | `car_swap rim: 4 x wheel rebuilt (spokes=mesh), sockets kept` |

Styles: `sport` `sedan` `suv` `truck` `classic` `scifi`. Bodies: `sedan` `coupe` `hatch` `suv` `pickup` `limo` `sports` `van` `classic` `kart`.

## Parts and sockets

| kind | socket | what is in it |
|---|---|---|
| `wheel` | Hub | tire + rim + brake disc, face on +X (right side turned, so sidewall text reads) |
| `tire` | Hub | ISO size code: 225/45R17 = 22.5 cm wide, sidewall 45 % = 10.1 cm, rim 17 in, r = 31.7 cm. Tread blocks by style, embossed sidewall text (`label`, "" for none; `car` wheels have none and 48 blocks) |
| `rim` | Hub | barrel, lip, hub, lug nuts; `spokes` 5\|10\|mesh\|dish\|2-20 |
| `brake_disc` / `caliper` | Hub | disc 280-380 mm (rim in × 25.4 − 110), 28 mm thick, hat; caliper on the rear-upper arc |
| `headlight` / `taillight` | Mount | housing, chrome reflector bowls, projector, emissive DRL, lens (`glass` / `lens_red`) |
| `grille` | Mount | frame + honeycomb (sport), slats (sedan, classic), bars (suv, truck), light lines (scifi) |
| `mirror` | Mount | arm, housing (paint), mirror glass, turn-signal strip |
| `door` | Hinge | outer skin with thickness, inner trim, armrest, handles, shut faces, glass; hinge on the front edge |
| `hood` / `trunk` | Hinge | crowned panel 1 cm thick; vents (sport), power bulge (suv/truck), lip (sport trunk) |
| `bumper_front` / `bumper_rear` | Mount | wrap-around loft, intake, splitter, fog lights, reflectors |
| `fender` | Hub | side panel with the wheel arch (r + 4.5 cm) and a flared lip |
| `spoiler` | Mount | airfoil wing + end plates + uprights (sport, scifi double), ducktail (sedan, classic) |
| `diffuser` `exhaust` `seat` `steering_wheel` (Column) `dashboard` `engine` `suspension` (Hub + Chassis) `license_plate` `wiper` (Pivot) `window` | | each its own SM_ with real sizes |

Left-side parts sit on +X; `side=right` mirrors them. Material slots are shared ids (`mcar_paint`, `mcar_chrome`, `mcar_rubber`, `mcar_glass`, `mcar_plastic`, `mcar_emissive`, `mcar_metal`, `mcar_lens_red`, `mcar_emissive_red`, `mcar_leather`, `mcar_accent`), so one `forge_material` edit recolours every car. Paint carries clearcoat 1.0 / 0.03 for UE. A rebuild never resets materials you changed.

## Real numbers

- Sedan 480 × 185 × 145, wheelbase 280; limo 720 × 190 × 148 (wheelbase stretches with `length_cm`, 600-900); sports 445 × 195 × 120; SUV 480 × 195 × 175; pickup 560 × 200 × 190; kart 180 × 130.
- Panel gaps 4 mm (production 3-5 mm), panels 1 cm thick with a 2.5 mm chamfer, glass 5 mm.
- Track = width − tyre width − 8 cm; wheel arch radius = tyre radius + 4.5 cm; hubs at z = tyre radius.

## A limo in one batch

```json
[
  {"tool": "forge_hq", "op": "car", "params": {"body": "limo", "length_cm": 720, "paint": "#101014", "parts": {"rim": "mesh"}}},
  {"tool": "forge_hq", "op": "car_swap", "params": {"car": "$0", "slot": "mirror", "style": "classic"}},
  {"tool": "forge_hq", "op": "qa", "params": {"id": "$0", "profile": "uefn", "budget": "hero"}}
]
```

Run it with `forge_batch(steps=[...], look="three")`. One part alone, checked as a game part:

```json
[
  {"tool": "forge_hq", "op": "car_part", "params": {"kind": "wheel", "tire": "225/45R17", "spokes": "10"}},
  {"tool": "forge_hq", "op": "qa", "params": {"id": "$0", "profile": "uefn", "budget": "vehicle"}}
]
```

→ `uefn score 97: SM_Wheel pivot not at base`, 0 errors. Every kind passes this alone. The pivot warning is expected: a part's origin is its socket (hub, hinge, mount), not its base. So are the shared `mcar_*` slots: a wheel uses 4 of them (rubber, chrome, metal, plastic), within the vehicle budget's 5.

`parts` keys are part kinds: a style word (`"spoiler": "sport"`), a dict of `car_part` params (`"wheel": {"tread": 48}`), `false` to leave one out (`"engine": false`), `rim` takes a spoke pattern, `tire` a size code.

## Rig, LODs, collision

`car` makes one group (combine on), a rig `<name>_Rig` with `root`, `FL_wheel` `FR_wheel` `RL_wheel` `RR_wheel` (heads at the hub centres, for UE Chaos / UEFN vehicle wheel setups), `door_FL`…, `hood`, `trunk` bones at the hinges; every part is bound to its bone. LOD ratios 1/.5/.25/.1 come from the `lods` op and ride on every part; `UCX_<name>_01` is one convex hull of the body. Hero budget 40-120k tris.

`car_swap` slots: `wheel`/`rim`/`tire` (all four), `caliper`, `headlight`, `taillight`, `grille`, `mirror`, `bumper_front`, `bumper_rear`, `spoiler`, `diffuser`, `exhaust`, `seat`, `steering_wheel`, `dashboard`, `engine`, `license_plate`, `wiper`, or an exact slot like `mirror_L`. The new part keeps the old transform, parent, bone and LODs. Body panels (`body_*`) are the shell: rebuild the car to change them.

**Done when:** the `look` (three, side) shows four wheels on the ground inside their arches, visible panel lines, lights front and back and glass you can see the seats through; the `car` summary is within 5 % of the target size and 40-120k tris; `qa(profile="uefn", budget="hero")` has 0 errors (the limo: 90k tris, pass). Its warnings are the hinge and hub pivots on purpose.

---
Dimensions from ISO tyre size codes and typical OEM specs. Workflow ideas adapted from Blender Agent Skills (hardsurface-modeling, optimization) by EL4CTEO, MIT licence.
