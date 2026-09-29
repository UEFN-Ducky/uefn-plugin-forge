# Forge local test builds

- Bump plugin.json to a new patch version for every changed build installed for user testing. Never replace an installed test version with different code under the same version.
- Build the UI before packaging; the panel loads ui/forge.bundle.js. Keep the bottom-left version label derived from plugin.json.
- Run py -3 -m backend.test_forge and npm run build:ui before installation. Keep the 20,000-instance performance budget unchanged.
- Use the normal plugin ZIP importer for local testing. Do not publish unless explicitly authorized. Do not restart UEFN or write digest Verse files.
- Reuse parent UEFN-Ducky appearance tokens; do not add standalone palettes.
