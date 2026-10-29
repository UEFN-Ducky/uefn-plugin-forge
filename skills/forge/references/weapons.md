---
description: "Guns and melee weapons in one Forge call: weapon(kind, style, length_cm, parts) builds a chamfered, unwrapped SM_ mesh with the grip at the origin, +X forward and Muzzle/Grip/Magazine/Sight (melee: Grip/TrailBase/TrailTip) sockets; real proportions, budgets, QA + collision + export chain"
metadata:
  label: "Weapons"
  default_enabled: false
  load_condition: "Making a gun, rifle, pistol, shotgun, SMG, sniper, knife, sword, axe, hammer or sci-fi weapon in Forge, or weapon sockets, origin and tri budgets for UEFN / UE5"
---

# Weapons

One `forge_hq` step makes the whole weapon: ~5-35 parts (receiver, barrel, handguard, stock, grip, magazine, sights, muzzle, rail, trigger guard; the rifle adds the dust cover open under the ejection port, forward assist, brass deflector, bolt catch, mag release, selector, 4 pins, a sling stud, a QD end plate, buffer-tube notches, mag ribs and grip grooves; the pistol adds front and rear serrations, extractor, slide stop, takedown lever, mag release, grip grooves and stipple; blade, guard, handle, pommel), each chamfered and then cut with `boolean` / `hardsurface` details. The parts are joined into one `SM_Weapon_<Kind>` with up to 3 material slots, given weighted normals and smart UVs. Lengths are cm.

```json
{"tool": "forge_hq", "op": "weapon", "params": {"kind": "rifle", "style": "realistic"}}
```
→ `rifle realistic 92 cm, 33 parts, 5.9k tris, sockets Grip Magazine Muzzle Sight`

| param | values (default) |
|---|---|
| `kind` | rifle, pistol, shotgun, smg, sniper, scifi_rifle, scifi_pistol, knife, sword, axe, hammer |
| `style` | `realistic` (2.2 mm 3-segment chamfer; satin-black anodised receiver, matte grey parkerised steel, matte black polymer, wear layers) \| `stylized` (4 mm 3-segment chamfer, cross-sections x1.25 wide x1.2 tall, lighter colours) |
| `length_cm` | the kind's default below; 0.5-2x allowed; the whole weapon scales, the grip stays at 0 |
| `parts` (guns) | `stock` ar\|skeleton\|wood\|precision\|block\|none · `sight` iron\|red_dot\|holo\|scope\|bead\|none · `muzzle` flash\|brake\|suppressor\|emitter\|none · `mag` std\|extended\|drum\|tube\|none · `rail` true\|false. Pistols: `sight` iron\|red_dot\|none, `muzzle` suppressor\|emitter\|none |
| `seed` | 0 = canonical; other values vary handguard, grip angle, mag tilt, stock and blade by 4-15 % |
| `name`, `at` | mesh name; position (the origin stays at the grip) |

## Proportions (defaults, real references)

| kind | default cm | reference | key parts (nominal cm) |
|---|---|---|---|
| rifle | 92 | AR-15/M4, 84-100 overall, barrel 36-51 (16" = 40.6) | receiver 26, handguard 26, mag 30-rd ~18 long, grip 18° |
| pistol | 20.2 | Glock 17: 20.2 L, 13.9 H, 3.2 W, barrel 11.4 | slide 20.4x2.6x3.4, grip 18° |
| shotgun | 100 | pump (870-style) 100-106, barrel 46-71 | pump 22, tube mag under the barrel |
| smg | 68 | MP5-class 55 folded / 70 extended, barrel 22.5 | 30-rd curved mag, red dot |
| sniper | 115 | bolt action 110-125, barrel 61-66 | 24x scope ~38, brake, bolt on the right |
| scifi_rifle | 95 | none: rifle silhouette, chunkier | hex shroud, glow strips, power cell |
| scifi_pistol | 26 | none | big slide, fin, emitter ring |
| knife | 30 | fixed blade, 18 blade + 11 handle | drop point, flat grind |
| sword | 100 | katana: blade (nagasa) 60-73, tsuka 25-30, sori ~1.5 | curved blade, tsuba r 3.8 |
| axe | 70 | hatchet 35-45, felling axe 80-90 | bit drop 15.5, poll |
| hammer | 75 | war hammer 60-80, sledge 90 | head 6.5x6.5x12 + faces |

Change any of these through `length_cm`: a "compact rifle" is `length_cm: 78`, a "great sword" is `kind sword, length_cm: 140`.

## Game conventions (UEFN / UE5)

- **Origin at the grip** (pistol grip top / where the main hand holds a melee weapon), **+X = muzzle / tip**, Z up. The ue5 export maps Forge +X to UE +X, so the mesh attaches to a hand socket without re-orienting.
- **Sockets** are stored on the node (`node.hq.weapon.sockets`, cm): guns get `Muzzle` (barrel exit: muzzle flash, tracer, projectile spawn), `Grip` (0,0,0), `Magazine` (mag well: reload VFX), `Sight` (eye line: ADS camera). Melee gets `Grip`, `TrailBase` and `TrailTip` (the two points of a swing trail). In UE, add them in the Static Mesh editor (Socket Manager) or as `SOCKET_<Name>` empties in a .blend export.
- **Budgets:** FPS hero 8-20k tris, third person 3-6k, pickups / mobile 1-3k. Forge weapons come out at 0.3-6k, already third-person ready. Use `qa budget=weapon` (15k, 3 materials).
- **Materials:** 3 slots or fewer: anodized + metal + polymer (rifle), metal + polymer (pistol: grey slide, black frame), metal + wood + grip (shotgun, axe), metal + polymer + glow (sci-fi). Slots are named `Weapon_<Slot>_<Style>` (`Weapon_Anodized_Realistic`, `Weapon_Metal_Realistic`, `Weapon_Polymer_Realistic`, `Weapon_Grip_Realistic`, `Weapon_Wood_*`, `Weapon_Steel_*`, `Weapon_Glow_*`). Wear goes on the edges in the material, not in geometry. Realistic slots already carry light `edge_wear` / `scratches` / `dirt` layers. Those masks use Pointiness and AO, which only Cycles computes, so they show after `bake` (and in UE once baked), not in an Eevee `look`.

**Wear:** edit each slot by name, **without `id`** (`id` assigns that one material to the whole weapon and the 3 slots become 1). Pass the slot's base values so the layers sit on its colour:

```json
[
  {"tool": "forge_hq", "op": "material", "params": {"material": "Weapon_Anodized_Realistic", "overrides": {"color": [0.045, 0.047, 0.05], "metallic": 0.0, "roughness": 0.3}, "layers": [{"type": "edge_wear", "color": [0.62, 0.63, 0.65], "amount": 0.9, "metal": true}, {"type": "scratches", "amount": 0.6}]}},
  {"tool": "forge_hq", "op": "material", "params": {"material": "Weapon_Polymer_Realistic", "overrides": {"color": [0.022, 0.022, 0.02], "metallic": 0.0, "roughness": 0.8}, "layers": [{"type": "dirt", "amount": 0.6}]}}
]
```
→ rifle keeps 3 slots (anodized, metal, polymer); this is "battle-worn", the built-in wear is lighter.
- **Hard surface:** the chamfer (bevel) and weighted normals are what make a gun read as metal; panel lines and vents are `hardsurface` cuts; n-gons stay only on flat caps (UE triangulates on import). Keep details at 2 mm or more: anything finer is texture.

## Recipe: rifle to game, one batch

```json
[
  {"tool": "forge_hq", "op": "weapon", "params": {"kind": "rifle", "parts": {"sight": "red_dot", "muzzle": "suppressor"}}},
  {"tool": "forge_hq", "op": "qa", "params": {"id": "$0", "profile": "uefn", "budget": "weapon"}},
  {"tool": "forge_hq", "op": "collision", "params": {"id": "$0", "kind": "box"}},
  {"tool": "forge_hq", "op": "lods", "params": {"id": "$0", "ratios": [1, 0.5, 0.25]}},
  {"tool": "forge_hq", "op": "export", "params": {"id": "$0", "target": "ue5"}}
]
```

Run it with `forge_batch(steps=[...], look="side")`. More examples: `{"kind": "sword", "style": "stylized"}`, `{"kind": "sniper", "parts": {"stock": "precision", "sight": "scope", "muzzle": "brake"}}`, `{"kind": "smg", "parts": {"mag": "extended", "stock": "skeleton"}}`.

**Done when:** the `look` side view shows a clear silhouette: the grip, trigger guard, magazine and sight can each be told apart, nothing floats and the chamfers catch the light. The summary has 4 gun sockets (or 3 melee sockets) and `qa` scores 85 or more. A `pivot not at base` warning is expected for weapons.

## Problems

| Symptom | Fix |
|---|---|
| Too few tris for an FPS close-up | `modifiers stack="render"` on the weapon, or `sculpt_hd` then `bake` |
| Floats in a hand / wrong way round | do not `origin`: the grip is the origin by design; turn the hand socket instead |
| Summary says `open` | a custom `boolean` grazed a face: undo, then lengthen the cutter |
| Sci-fi glow too bright in UEFN | lower the emissive of the `Weapon_Glow_*` material |

---
Part breakdown, the bevel + weighted-normal stack and the budget tables are adapted from Blender Agent Skills (hardsurface-modeling, optimization) by EL4CTEO, MIT licence. Proportions are from public manufacturer specifications.
