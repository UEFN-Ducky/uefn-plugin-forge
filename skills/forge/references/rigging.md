---
description: "Forge rigs: the bone model, biped, chain, and single templates, custom bones for doors, chests, turrets and fans, rigid, auto, and painted weights, bone naming for UEFN retargeting, and fixes for common rig problems"
metadata:
  label: "Rigging"
  default_enabled: false
  load_condition: "Adding a skeleton, bones, or skin binding to a Forge asset, or a rigged asset deforms wrong"
---

# Rigging in Forge

## The model

- A rig is a list of bones: `{name, parent, head, tail}` in scene centimetres.
- A bone rotates about its **head**. The tail is only for display and auto weights.
- At rest every bone's axes line up with the world axes. A key `rot=[0, 30, 0]` turns the bone 30 degrees about Y in its parent's current frame, the same X-then-Y-then-Z order objects use.
- Objects are bound to a rig. They never change shape in the scene; the pose is applied on top for the dock preview, `forge_render(clip=...)`, and Send.
- One rig becomes one `SK_` skeletal mesh at Send. Several roots get one `root` bone added above them, because UEFN needs a single root.

## Templates

`forge_rig(action="template", template="biped", ids=[...])` fits 23 bones to the bounds of `ids` (groups count as all their parts; no ids means the whole scene):

```
root
  pelvis
    spine_01 > spine_02 > spine_03
      neck_01 > head
      clavicle_l > upperarm_l > lowerarm_l > hand_l      (and _r)
    thigh_l > calf_l > foot_l > ball_l                   (and _r)
```

- The names are the UE mannequin names, so the Animation plugin's retarget presets recognize them.
- It assumes a T-pose facing -Y with the character's left on +X. If the mesh is wider than 0.7 of its height, the arms stretch to reach the fingertips.
- Model arms straight out (T-pose). Arms modeled down would be bound to horizontal bones and bend wrong.
- After fitting, check the joints: `forge_render` from `front`, then move any joint that misses its elbow or knee with `edit_bone`.

`forge_rig(action="template", template="chain", count=5, prefix="tail", start=[x, y, z], end=[x, y, z], parent="pelvis")` adds a chain of `count` bones from `start` to `end` (default: bottom centre to top centre of the selection). With `parent`, it hangs off an existing bone, such as a tail on the pelvis. Use chains for tails, tentacles, ropes, antennas, hair strands, plants, and flags.

`forge_rig(action="template", template="single", ids=[...])` adds one `root` bone at the base and binds the objects to it: enough skeleton for a prop to spin, bob, or carry shape keys into UEFN.

Templates add to the first rig (or `rig=`). `action="new"` starts an empty rig; use one rig per asset you want as its own `SK_`.

## Custom bones

`forge_rig(action="add_bone", bone="lid", parent="", head=[...], tail=[...])`. Put the head where the thing pivots.

| Asset | Bones | Bind |
|---|---|---|
| Door | `hinge` at the hinge edge, bottom | door leaf, rigid |
| Chest | `base` at the centre bottom; `lid` at the back top edge, parent `base` | box to `base`, lid to `lid` |
| Turret | `yaw` at the base centre; `pitch` at the barrel pivot, parent `yaw` | base to `yaw`, gun to `pitch` |
| Fan, windmill, coin | one bone at the hub | blades rigid |
| Drawbridge, lever, trapdoor | one bone on the pivot line | rigid |
| Floating pickup | one bone at the base | rigid; animate with `bob` and `spin` |

A single batch builds the chest:

```json
[
  {"tool": "forge_add", "kind": "box", "object_name": "Base", "params": {"w": 90, "d": 50, "h": 40}},
  {"tool": "forge_add", "kind": "box", "object_name": "Lid", "params": {"w": 90, "d": 50, "h": 12}, "pos": [0, 0, 40]},
  {"tool": "forge_rig", "action": "new", "new_name": "Chest"},
  {"tool": "forge_rig", "action": "add_bone", "rig": "$2", "bone": "base", "head": [0, 0, 0], "tail": [0, 0, 20]},
  {"tool": "forge_rig", "action": "add_bone", "rig": "$2", "bone": "lid", "parent": "base", "head": [0, 25, 40], "tail": [0, 25, 52]},
  {"tool": "forge_rig", "action": "bind", "rig": "$2", "ids": ["$0"], "bone": "base"},
  {"tool": "forge_rig", "action": "bind", "rig": "$2", "ids": ["$1"], "bone": "lid"},
  {"tool": "forge_anim", "action": "generate", "rig": "$2", "kind": "open", "bone": "lid", "axis": "x", "amount": -100}
]
```

The lid hinge sits on the back edge (+Y), so a negative X rotation swings the front up and back. Passing `"rig": "$2"` keeps every step on the new rig even when the scene already has one.

## Binding

| Mesh | Call | Result |
|---|---|---|
| Separate hard parts | `bind`, `bone="upperarm_l"` | The part moves rigidly with that bone. Clean for robots, blocky characters, and props. |
| One soft mesh | `bind`, `auto=true` | Each vertex blends up to four nearby bones by distance. The `root` bone is left out. |
| Soft mesh near unrelated bones | `bind`, `auto=true`, `bones=["tail_01", "tail_02"]` | Only those bones pull it, so a tail doesn't drag the legs. |
| Soft mesh that needs exact control | `bind`, then `weights` (below) | Painted per vertex, stored on the mesh. |

- Binding a group binds every part in it.
- `unbind` removes it; binding again replaces the old binding.
- Rigid and auto weights are computed at preview and Send time, so editing the mesh afterwards needs no rebind. Painted weights are stored on the vertices and follow mesh edits.
- Auto weights are distance envelopes. Give soft meshes enough rings (a lathe with many profile points, `subdivide`, `loopcut`) to bend smoothly.

## Painted weights

`forge_rig(action="weights", ids=[mesh])` switches the mesh to painted weights, seeded from its current binding. Then paint one bone at a time:

| Arguments | Does |
|---|---|
| `bone="thigh_l", mode="add", strength=0.3, points=[[x, y, z]], radius=15` | raises that bone's weight under the stroke (world cm) |
| `mode="subtract"` | lowers it |
| `mode="set", value=1, faces=[...]` | moves those faces' vertices toward `value` by `strength` |
| `mode="smooth"` | blends with the neighbours, to soften a hard joint |
| neither `points` nor `faces` | the whole object |
| `normalize=true` | makes each vertex's weights sum to 1 afterwards |
| `init=true` | reseeds from the binding, dropping the painting |

At preview and Send, each vertex keeps its four strongest bones, normalized. A vertex with no paint at all falls back to envelope weights. The user paints the same weights in the dock (Mode, Weights): blue is 0 and red is 1 for the bone in the brush bar.

## Editing and naming

- `edit_bone`: move `head` or `tail`, change `parent`, or rename with `new_name`. Renames update children, keys, and bindings.
- `delete_bone` gives its children and rigid parts to its parent and drops its keys.
- Names are letters, digits, and `_`. Keep `_l` and `_r` suffixes for sides so mirror-minded tools and retargeting pair them.

## Checks

`forge_check` adds rig issues: no bones, a cycle, nothing bound, a binding to a missing bone, keys on missing bones, keys after the last frame, and loops whose last frame doesn't match frame 0.

## Problems

| Symptom | Fix |
|---|---|
| Part doesn't move | It isn't bound, is bound to `root`, or its bone has no keys in this clip |
| Limb bends at the wrong place | The joint's bone head misses the elbow or knee; `edit_bone head=` |
| Soft mesh drags a neighbour | Bind again with `auto=true` and a `bones` list, or paint that bone down with `weights` `mode="subtract"` |
| Mesh folds sharply at a joint | Too few vertices there; add loops, or split into parts and bind rigidly |
| Joint pinches or collapses | `weights` `mode="smooth"` across the joint for both bones |
| Painted weights gone | Join, boolean, and `init=true` start from the binding again |
| Binding gone after join or boolean | Those make a new object; bind it again |
| Whole character slides | Keys on `root` or `pelvis` `loc`; remove them for an in-place cycle |

## Fortnite animations on a Forge character

After Send, the Animation plugin's IK retarget tools (`create_ik_rig_asset`, `get_retarget_preset`, `auto_map_retarget_chains`, `retarget_animation`) can map mannequin clips onto the `SK_`, because the biped uses mannequin bone names. Forge characters face -Y in the dock and land in UEFN facing +Y with their left side on +X, the way the mannequin stands. If a retargeted clip still plays turned around, fix the retarget pose (`skill_read_subskill("animation", "retargeting")`).
