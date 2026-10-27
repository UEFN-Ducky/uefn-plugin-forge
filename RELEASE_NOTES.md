# Forge 1.2.12 — Together in the viewport, and a camera for videos

## Live in the viewport

- In a live group, everyone in the same scene sees each other fly around as a small character in their own colour with their name above it (or as a camera: the Avatars / Cameras chip). What a teammate has selected is outlined in their colour.
- AI duckies show up the same way, as a duck with their name and what they are doing ("Developer 🦆 · Extruded roof"). Every Forge call moves the duck to its work; it fades 10 s after the last one. Your own duckies show even when you are not live.
- The People bar (top right of the viewport): click someone to glide to their view, Shift-click to follow them until you move.
- Live edits: you watch a teammate's drag slide the object while they drag it, and their brush ring and "Ben is sculpting" while they sculpt or paint, until the change syncs. An object a teammate is moving can't be grabbed at the same time.
- Alt-click pings a spot for everyone for 3 s, with an arrow at the edge when it's off screen.

## Cameras and shots

- New Shots tab: save cameras from your view (drawn as 🎥 frustums), look through them, and build shots: key the camera at a few times and Forge glides between them on a smooth curve with an ease. Scrub and Play preview the move.
- Render MP4 draws the shot frame by frame (not a screen recording) at 1920×1080, 1280×720, 1080×1920 vertical or 3840×2160, 24–60 fps, using Ducky's ffmpeg and the fastest working H.264 encoder. Videos go to `%LOCALAPPDATA%\UEFN-Ducky\forge\videos`. Keep the Forge tab open while it renders.
- Cameras and shots are saved in the scene: they sync with the team copy and Undo takes them back.

## For agents

- `forge_camera`: frame, orbit, goto, look_at, get, people, ping, save, cameras; `drive=true` moves the person's viewport with the ducky (for recording).
- `forge_view`: a picture of the whole scene through a camera, rendered by the open tab, into the chat.
- `forge_shot` and `forge_shot_render`; `imk_project` adds the finished clip to an Island Media Kit project.

## Checks

`py -3 -m backend.test_forge` (with the new `backend/test_live.py`: shared path numbers, cameras/shots as undoable edits, ducky flyer and people, a panel round trip for pictures, and a real 3-frame MP4 encode) and `npm test` (`tests/shots.test.mjs`, `tests/presence.test.mjs`). The 20,000-instance payload budget is unchanged.
