---
description: "Forge HQ rigging and animation on the Blender engine: rig_template (UE5 Manny, quadruped, bird, tail chain), bone-heat auto_weights, pose with IK, clips (walk, run, jump, attack, hit, death, idle) with loop-seam checks, retarget from another rig or BVH, bake_clip for game export, stress poses, rig_check, ARKit face shapes and visemes"
metadata:
  label: "HQ rig"
  default_enabled: false
  load_condition: "Rigging a character or creature to a UE5-compatible skeleton, generating, retargeting or cleaning clips, checking a rig, or adding ARKit face shapes and visemes in Forge"
---

# HQ rig

Through `forge_hq(op=..., params={...})`. Native `forge_rig` / `forge_anim` (skills `rigging`, `animation`) do blocky parts and hand keys; these make a game-ready skeleton.

```json
forge_batch(name="Hero", steps=[
  {"tool": "forge_hq", "op": "rig_template", "params": {"template": "ue5_manny"}},
  {"tool": "forge_hq", "op": "auto_weights", "params": {"ids": ["<body id>"], "target": "uefn"}},
  {"tool": "forge_hq", "op": "clip", "params": {"kind": "walk"}},
  {"tool": "forge_hq", "op": "stress", "params": {}},
  {"tool": "forge_hq", "op": "rig_check", "params": {"profile": "uefn"}}
])
```

Then `forge_render(clip="Stress", frames=[10, 20, 30, 40])`: arms up, elbows 140, knees 150, spine twist. Pinching or candy-wrapping there means weights to fix.

| op | params | Notes |
|---|---|---|
| `rig_template` | template ue5_manny\|quadruped\|bird\|tail_chain, ids, landmarks {bone:[x,y,z]}, bind true, count 8 | fits to the parts' bounds; `landmarks` pins bones exactly; `count` is the tail chain length |
| `auto_weights` | ids, target uefn (4 influences)\|ue5 (8), smooth 1, clean 0.01 | bone heat; under 50 cm it runs on a 10x copy; when heat fails it uses a voxel proxy, then distance weights |
| `pose` | rig, clip, frame, controls {hand_l: {ik:[x,y,z], pole, chain}, foot_r: {pin:true}, spine: {curve:.2}} | solved into FK keys |
| `clip` | kind walk\|run\|jump\|attack\|hit\|death\|idle, speed 1, style heavy\|light\|cartoony, root_motion false, name, rig, map | loops report `seam_deg` (first vs last frame; 0 is seamless) |
| `stress` | rig, map | a 50-frame `Stress` clip for weight checks |
| `retarget` | src = clip name on another rig \| path to .bvh, rig, map {their bone: bone}, name | auto names (Manny, Mixamo, CMU, Unity); root travel scaled by leg ratio; BVH resampled to 30 fps. Rest poses must match (T-pose to T-pose) |
| `bake_clip` | clip, rig, step 1, tol 0.1, to game | linear keys every `step`, no bone scale keys, loop end = start, keys reduced within `tol` |
| `rig_check` | rig, profile uefn\|ue5\|unity\|roblox | score + fixes (skill `asset_qa`) |
| `face_shapes` | ids, set arkit52\|basic12\|none, visemes false, split [names], report false, head_frac | ARKit keys; `visemes` adds 14 `viseme_` keys (PP FF TH DD kk CH SS nn RR aa E ih oh ou; sil = rest); `split` makes `_L`/`_R` halves with a soft centre; `report` counts keys that move nothing |

Shape keys reach UEFN on a rig: `face_shapes` on a head that is part of a rigged character needs nothing more.

## Done when

`rig_check` passes and the `Stress` frames look clean in `forge_render`.

Adapted from Blender Agent Skills by EL4CTEO (rigging, animation; MIT).
