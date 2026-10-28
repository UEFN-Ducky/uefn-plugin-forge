---
description: "Forge cameras and videos: your own camera and duck in a scene, forge_view, saved cameras, keyed shots and rendering them to MP4"
metadata:
  label: "Camera and shots"
  default_enabled: false
  load_condition: "Flying or framing a camera in a Forge scene, looking at the whole scene, or filming a shot or video in Forge"
---

# Camera and shots

## Tool args

`forge_camera(action=...)`: your own camera. People with the Forge tab open see you as a duck with your name and what you do (teammates too when live). Centimetres, Z-up. `seconds` = glide time.
- `get` (yours and the person's viewport); `goto` (`pos`/`target`/`fov`); `look_at` (`target` or `ids`); `frame` (fit `ids`, or the whole scene); `orbit` (circle `ids` or the scene over `seconds`).
- `people` (who is in the scene and where they look); `ping` (mark `target` for everyone for 3 s); `save` (keep your view as a scene camera named `camera_name`); `cameras` (saved cameras and shots).
- `drive=true` moves the person's viewport with you: only when they asked you to show or film something.

`forge_view(camera=...)`: a picture of the whole scene, rendered by the open Forge tab. `camera`: `"ai"` (yours, default), `"viewport"` (what the person sees), a saved camera's name, or `{pos, target, fov}`. `clean` hides the grid, gizmos and flyers. With the tab closed, use `forge_render` for single objects.

`forge_shot(action=...)`: keyed camera shots for videos, saved in the scene. The camera glides through the keys on a smooth curve.
- `add` (`shot_name`, `duration` seconds, `ease`); `key` (`shot`, `t` seconds, and `pos`/`target`/`fov` or `from_view` = `"ai"` | `"viewport"` | a saved camera's name); `unkey` (`shot`, `t`); `edit` (`duration`, `ease` linear|in_out|in|out, `shot_name`, `camera`); `delete`.
- The person plays and scrubs it in the Forge tab's Shots panel.

`forge_shot_render`: renders a shot frame by frame to an MP4 (the Forge tab must be open). `fps` 24|25|30|50|60. Waits up to `wait_seconds`; a longer render returns `job`, so call again with `job` to keep waiting. `imk_project` also adds the clip to that Island Media Kit project. 1080×1920 for Shorts.
