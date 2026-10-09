# Ducky Forge

A modeler, sculptor, painter, rigger, and animator inside UEFN-Ducky. Primitives, extrude, bevel, solid booleans, groups, smooth subdivision and sculpt brushes, shape keys, smart UV unwrap, texture painting, blend materials with painted masks, AI textures, biped and chain skeletons with painted weights and IK, generated and hand-keyed clips, and a Send that lands SM_ and SK_ meshes, AS_ clips, morph targets, and MI_ materials in UEFN, placed in the level if you ask. No Blender.

Open the Forge dock (header button or the right sidebar). The same scene is editable from chat via the `forge_*` tools; `forge_batch` runs many edits as one undo step. The Mode menu switches between Object, Edit, Sculpt, Weights, Blend paint, and Texture paint; each brush stroke is one undo step. The Materials tab unwraps and shows the UVs over the texture. The Rig tab shows bones, plays and scrubs clips, poses a picked bone with the rotate gizmo (or drags a hand or foot with IK), and keys shape keys.

`npm run build:ui` checks the dock's brushes (`ui/src/brush.check.mjs`) before it bundles.

```bash
py -3 -m backend.test_forge
npm install
npm run build:ui
py -3 scripts/build_zip.py
```

`scripts/release.py --publish` uploads to the Store; only run it when a release is approved.

## Next release: ship compiled

This plugin still ships its Python source on the Store. Its next release has to ship compiled and signed, the way Ducky Account and Roguelike do:

1. Give `scripts/release.py` and `scripts/build_zip.py` the compiled build from `uefn-plugin-account` (`build_compiled_zip`, upload by ticket, `--plain` only as an escape hatch).
2. Bump `version` and set `min_app_version` to `1.2.357` or newer (the Store keeps older apps from seeing it).
3. Publish, then check the download with the start-up license check (signature, id and version, compiled, team access), not only the signature.
4. A compiled build can't `importlib.reload` its own modules (Python raises SystemError), so reload only when running from source. Before publishing, install the source and compiled zips into a throwaway Ducky and check they register the same panel calls, tools and workflow nodes, and that those calls still work after the plugin reloads.

For this plugin:

- `backend/blend_import.py` hands `backend/blend_export_script.py` to Blender by path. A compiled build doesn't ship that file, so move it out of `backend/` (for example to `assets/`) first.
- `listener/` runs in UEFN's own Python and stays readable source; only the Ducky-side backend compiles.

Remove this section once a compiled version is live.
