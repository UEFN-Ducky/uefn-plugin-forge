---
description: "Forge HQ rigging and animation on the Blender engine: rig_template (UE5 Manny, quadruped, bird, tail chain), bone-heat auto_weights, pose with IK, clips (run, jump, attack, hit, death, idle) and ARKit face shapes"
metadata:
  label: "HQ rig"
  default_enabled: false
  load_condition: "Rigging a character or creature to a UE5-compatible skeleton, generating combat or locomotion clips, or adding ARKit face shapes in Forge"
---

# HQ rig

Through `forge_hq(op=..., params={...})`. Native `forge_rig` / `forge_anim` (skill `rigging`, `animation`) still do blocky parts and hand keys; use these for a game-ready skeleton.

| op | params | Notes |
|---|---|---|
| `rig_template` | template ue5_manny\|quadruped\|bird\|tail_chain, ids, landmarks {bone:[x,y,z]}, bind true, count 8 | fits to the parts' bounds; `landmarks` pins bones exactly; `count` is the tail chain length |
| `auto_weights` | ids, target uefn (4 influences)\|ue5 (8), smooth 1, clean 0.01 | bone-heat skin weights |
| `pose` | rig, clip, frame, controls {hand_l: {ik:[x,y,z], pole, chain}, foot_r: {pin:true}, spine: {curve:.2}} | solved into FK keys |
| `clip` | kind run\|jump\|attack\|hit\|death\|idle, speed 1, style heavy\|light\|cartoony, root_motion false, rig, map | a named clip on the rig |
| `face_shapes` | ids, set arkit52\|basic12, head_frac | ARKit-named shape keys from landmark regions of a head |

Shape keys reach UEFN on a rig: `face_shapes` on a head that is part of a rigged character needs nothing more. Check motion with `forge_render(clip=...)` or `look="three"` on the clip call.
