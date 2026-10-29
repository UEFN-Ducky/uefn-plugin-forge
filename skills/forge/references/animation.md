---
description: "Forge animation clips: keys and interpolation, shape-key keys, IK, every generator (idle, walk, wave, sway, spin, bob, swing, open) and its parameters, timing and spacing, a walk cycle by frame, seamless loops, and checking motion with renders"
metadata:
  label: "Animation"
  default_enabled: false
  load_condition: "Creating or editing an animation clip on a Forge rig, or a Forge motion looks wrong"
---

# Animation in Forge

## Clips and keys

- A clip belongs to a rig: `{name, fps, frames, loop, tracks}`. The defaults are 30 fps, 60 frames, looping.
- A track holds keys per bone and channel: `rot` (degrees in the parent bone's frame), `loc` (cm offset from rest, in the parent's frame), and `scale` (multiplier).
- `interp` on a key shapes the move to the next key: `smooth` (ease in and out, the default), `linear` (constant speed, for spins and conveyors), and `step` (hold, then snap, for mechanical beats).
- Rotations blend the short way round. For a full turn, key every 90 degrees or less (the `spin` generator does).

```json
forge_anim(name="Guy", action="key", clip="Wave", keys=[
  {"bone": "upperarm_r", "frame": 0, "rot": [0, 0, 0]},
  {"bone": "upperarm_r", "frame": 10, "rot": [0, 70, 0]},
  {"bone": "lowerarm_r", "frame": 10, "rot": [0, 30, 0]},
  {"bone": "lowerarm_r", "frame": 16, "rot": [0, 60, 0]},
  {"bone": "lowerarm_r", "frame": 22, "rot": [0, 30, 0]}
])
```

Keys at an existing frame replace it. A key past the clip's end extends the clip. `unkey` removes one frame, or the whole track when you leave out `frame`.

Shape keys animate in the same clips: a row `{"shape": "Smile", "frame": 12, "value": 1}` keys a weight from 0 to 1, `interp` included, and `unkey` with `shape=` removes it. Make the keys first (`sculpt_shapes`).

## IK

`forge_anim(action="ik", clip="Reach", bone="hand_r", target=[x, y, z], frame=10)` bends the bones above `bone` so its head lands on `target` (world cm), then keys those rotations at that frame. It is solved once and baked into keys; nothing stays live.

- `chain` (2): how many bones above bend. Two (forearm and upper arm, or calf and thigh) solve exactly; longer chains (spines, tails, tentacles) solve by steps.
- `pole=[x, y, z]`: the elbow or knee bends toward this point, such as in front of the knee. Without it the joint keeps the side it already bends to.
- `keep_rotation` (true): the hand or foot keeps its world orientation, so a planted foot stays flat.
- `span=[first, last]`: pins the target at every frame in the range, like a foot planted through a crouch or a hand on a lever while the body moves.
- A target out of reach straightens the chain toward it.

## Generators

`forge_anim(action="generate", kind=...)` makes a new clip named after the kind, or fills `clip=` if you pass one. `amount` scales the motion and `frames` sets the length.

| kind | Needs | Default | What it keys |
|---|---|---|---|
| `idle` | biped | 60 frames | breathing spine and head, arms down (`arms_down`, 70 degrees), a slight pelvis dip |
| `walk` | biped | 32 frames | thighs, knees, arms down and counter-swinging, pelvis bob, spine twist |
| `wave` | biped | 40 frames | right arm up, forearm waving twice, left arm down |
| `sway` | a chain | 48 frames, 15 degrees | a sine wave travelling down `bone`'s chain (or `bones`), `axis` x, `phase` 0.6 between bones |
| `spin` | one bone | 60 frames, 1 turn | `amount` turns about `axis` (z), linear |
| `bob` | one bone | 60 frames, 20 cm | up and down on `axis` (z) |
| `swing` | one bone | 48 frames, 30 degrees | pendulum about `axis` (x) |
| `open` | one bone | 30 frames, 90 degrees, no loop | rotate from 0 to `amount` about `axis` (z) |

Without `bone`, one-bone kinds use the rig's root. Examples: a run is `walk` with `amount=1.6, frames=20`; a floating pickup is `bob` then `spin` into the same clip (`clip="Bob"`); a door is `open` on its hinge with `amount=-95`.

Generators write keys you can edit, so treat them as a starting pose. Add a key to exaggerate a beat, or `unkey` a bone to calm it.

## Timing at 30 fps

| Motion | Frames |
|---|---|
| Walk cycle | 24-32 (brisk to relaxed) |
| Run cycle | 16-20 |
| Idle loop | 60-90 |
| Door or lid open | 20-40 |
| Hit reaction | 8-15 |
| Wave | 30-45 |

## Spacing and appeal

- **Ease**: `smooth` keys start and stop gently. Use `linear` only for machines and spins.
- **Anticipation**: before a big move, key a small move the other way 4-6 frames earlier.
- **Overshoot and settle**: key past the target (about 110 percent), then the target 4-6 frames later.
- **Overlap**: children lag their parents. Offset child keys 2-4 frames later, as `sway` does with `phase`.
- **Arcs**: rotate a parent and its child together so the hand travels a curve, not a line.
- **Holds**: repeat a key 6 or more frames later to hold a pose.

## Walk cycle by frame (32 frames)

| Frame | Pose | Keys |
|---|---|---|
| 0 | Contact: left heel down in front | `thigh_l` -28, `thigh_r` +28, knees nearly straight, pelvis low |
| 8 | Passing: right leg swings under | right knee bent about 45, pelvis high |
| 16 | Contact: right heel down in front | mirror of frame 0 |
| 24 | Passing: left leg swings under | left knee bent, pelvis high |
| 32 | Same as frame 0 | seamless loop |

X rotation on thighs swings legs: negative is forward, because the character faces -Y. Arms swing opposite to the leg on the same side.

## Loops

A looping clip must end where it starts: frame `frames` equals frame 0. Generators already do. When keying by hand, copy the frame-0 keys to the last frame. `forge_check` flags a loop that pops. Keep `root` still for in-place cycles; UEFN moves the actor.

## Game-ready clips

- Workflow: block in `step` keys on the key poses, switch to `smooth`, then polish spacing; one clip per action.
- Names: `<Rig>_<Action>` or `AN_<Action>` (`name=` on `clip`); Send turns each into `AS_<name>`.
- Never key bone `scale` for a game: engines drop it or stretch the skin. `forge_hq(op="bake_clip", params={"clip": "Run"})` removes scale keys, closes the loop seam and thins the keys.
- `forge_hq(op="clip")` reports `seam_deg` for loops; anything above 1 degree pops.
- Root motion: `root_motion=true` puts the travel on `root` for engines that drive the capsule from it; in-place cycles keep `root` still.
- Mocap or another rig's clip: `forge_hq(op="retarget", params={"src": "C:/mocap/run.bvh"})` (skill `hq_rig`).

## Check the motion

- `forge_render(name, clip="Walk", frames=[0, 8, 16, 24])` returns one image per frame. Look at silhouettes: legs should cross, arms counter-swing, nothing should pass through the body.
- In the dock, the Rig tab plays the clip. The user can scrub it or drag a bone's rotate gizmo, which keys the current frame. With the IK box ticked, dragging a hand or foot solves the chain above it on release. Shape-key sliders preview a weight, and Key stores it at the current frame.
- `forge_check` catches keys on missing bones, keys after the end, and loops that pop.

## Tool args

`forge_anim(action=...)`. Rotations are in the bone's parent frame; check a motion with `forge_render(clip=...)`.
- `generate`: `kind` idle|walk|wave on a biped; sway down a bone chain; spin|bob|swing|open on one bone. `amount` scales it, `axis` x|y|z.
- `new` (`new_name`, `frames`, `fps`, `loop`); `edit` (frames, fps, loop, new_name); `delete`.
- `key`: `bone`, `frame`, `rot` degrees / `loc` cm / `scale`; `shape` + `value` 0-1 keys a shape key; or `keys=[{bone, frame, rot} or {shape, frame, value}]` for many; `interp` smooth|linear|step.
- `ik`: `bone` = hand/foot, `target` = world cm for its head, `chain` = bones to bend, `pole` = world point the elbow/knee bends toward, `keep_rotation` holds the hand/foot orientation; `frame`, or `span=[first, last]` to pin it across frames.
- `unkey` (`bone` or `shape`, optional `frame`/`channel`).

## In UEFN

Each clip becomes an `AS_` AnimSequence on the `SK_` skeleton at Send. `place=true` puts the asset on an Animated Mesh device playing its first clip. Other clips, NPC presets, or playing on the player go through the Animation plugin: `skill_read_subskill("animation", "runtime_playback")`.

"Game-ready clips" adapted from Blender Agent Skills by EL4CTEO (animation; MIT).
