# Ducky Forge

A modeler inside UEFN-Ducky. Primitives, extrude, bevel, booleans, groups, materials, AI textures, and Send to UEFN. No Blender.

Open the Forge dock (header button or the right sidebar). The same scene is editable from chat via the `forge_*` tools.

```bash
py -3 -m backend.test_forge
npm install
npm run build:ui
py -3 scripts/release.py --publish --changelog "v1.0.0: Forge modeler"
```
