# Ducky Forge

A modeler, sculptor, painter, rigger, and animator inside UEFN-Ducky. Primitives, extrude, bevel, solid booleans, groups, smooth subdivision and sculpt brushes, shape keys, smart UV unwrap, texture painting, blend materials with painted masks, AI textures, biped and chain skeletons with painted weights and IK, generated and hand-keyed clips, and a Send that lands SM_ and SK_ meshes, AS_ clips, morph targets, and MI_ materials in UEFN, placed in the level if you ask. No Blender.

In a live group, teammates and AI duckies fly the viewport with their names; their drags and brush strokes show while they happen, and Alt-click pings a spot. The Shots tab keeps cameras and keyed camera moves and renders them frame by frame to MP4 (`forge_camera`, `forge_view`, `forge_shot`, `forge_shot_render` for agents).

Open the Forge dock (header button or the right sidebar). The same scene is editable from chat via the `forge_*` tools; `forge_batch` runs many edits as one undo step. The Mode menu switches between Object, Edit, Sculpt, Weights, Blend paint, and Texture paint; each brush stroke is one undo step. The Materials tab unwraps and shows the UVs over the texture. The Rig tab shows bones, plays and scrubs clips, poses a picked bone with the rotate gizmo (or drags a hand or foot with IK), and keys shape keys.

`npm run build:ui` checks the dock's brushes (`ui/src/brush.check.mjs`) before it bundles.

```bash
py -3 -m backend.test_forge
npm install
npm run build:ui
py -3 scripts/build_zip.py
```

`scripts/release.py --publish` uploads the compiled, signed build to the Store (`--plain` ships readable source, escape hatch only); only run it when a release is approved. Blender reads `assets/blend_export_script.txt` by path, and `listener/` stays readable for UEFN's Python.
