---
description: "Forge look-dev lighting: one-word light presets (studio, product, dramatic, soft, neutral), outdoor times of day (noon, golden, night), view transforms (AgX, Khronos PBR Neutral) and render quality, for judging materials and shapes"
metadata:
  label: "Look-dev lighting"
  default_enabled: false
  load_condition: "Judging a material or model under proper light, product or portfolio shots, checking an asset in daylight or at night, or a render looks flat, washed out or too dark"
---

# Look-dev lighting

`light` renders one Eevee still of the objects under a preset and returns its `path` (open it with `view_image`). The scene keeps the last preset.

```json
forge_batch(steps=[
  {"tool": "forge_hq", "op": "material", "params": {"material": "Hull", "preset": "painted_metal", "id": "hull"}},
  {"tool": "forge_hq", "op": "light", "params": {"id": "hull", "preset": "studio"}},
  {"tool": "forge_hq", "op": "light", "params": {"id": "hull", "env": "golden"}}
])
```

Done when: the still shows the form (a lit side, a shadow side, a rim on the edge), nothing clipped white or lost in black.

## Presets

| preset | Light | Use for |
|---|---|---|
| `studio` | key 45° left and 35° up, fill opposite at ⅓, rim behind; 18 % grey cove | default material check |
| `product` | big soft key, half fill, light backdrop | store shots, thumbnails |
| `dramatic` | hard side key, almost no fill, strong rim, black backdrop | silhouette and edge wear |
| `soft` | overhead soft key, high fill, no rim | colour and albedo check |
| `neutral` | even white world, no lights | flat albedo, find dark or hot colours |

`env` = `noon` (sun 60°), `golden` (sun 8°, warm), `night` (cool moon, exposure +1.5) puts the object outdoors instead.

| Param | Values |
|---|---|
| `view` | three (default), front, side, top |
| `quality` | preview 16, final 64, product 128 samples |
| `transform` | agx (default), neutral (Khronos PBR Neutral: true colours for review), standard |
| `size` | [640, 480] |

## Loop

Light once with `studio`, judge, change one thing, light again. Stop after 3-5 passes: more is guessing. Check every material in `studio` and `neutral`, the hero shot in `product` or `dramatic`, and outdoor props at `noon` and `golden`.

---
Adapted from Blender Agent Skills by EL4CTEO (MIT): lookdev lighting; three-point rig and review loop from the Blender MCP and AgentPMT research notes.
