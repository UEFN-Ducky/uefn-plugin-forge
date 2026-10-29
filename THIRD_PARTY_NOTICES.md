# Third-party notices

Ducky Forge is licensed under the GNU General Public License, version 3 or (at your option) any later version (GPL-3.0-or-later); see `LICENSE`. The viewer bundles Three.js. esbuild is a development build tool and is not distributed with the plugin.

## Blender (bpy)

Forge's HQ engine runs Blender as the `bpy` Python module. It is not inside this package: on first use Forge downloads `bpy` 5.2.3 from PyPI (https://pypi.org/project/bpy/) and the CPython 3.13 embeddable runtime from python.org into `%LOCALAPPDATA%/UEFN-Ducky/forge-engine/`.

Blender is Copyright © Blender Foundation and contributors, licensed GPL-2.0-or-later (distributed here under GPL-3.0-or-later). Source: https://projects.blender.org/blender/blender (tag `v5.2.3`), mirror https://github.com/blender/blender. Python is under the PSF License Agreement (https://docs.python.org/3/license.html).

## MakeHuman base head (assets/heads)

`assets/heads/makehuman_head.npz` is derived from MakeHuman assets: https://github.com/makehumancommunity/makehuman, commit `a8bc2d54ff0ac92e78ff71431b1023eda42bf482`. Those assets (base mesh and targets) are released under **CC0 1.0 Universal** (repository `LICENSE.md` section C and `LICENSE.ASSETS.md`; each file's header: "This asset was explicitly released as CC0 in september 2020", copyright holders at release: Data Collection AB and the MakeHuman team). Only assets were taken, no MakeHuman program code (that is AGPL); `assets/heads/extract.py` is Forge's own converter.

Taken: from `makehuman/data/3dobjs/base.obj` the head and neck of the `body` group (above y = 5.75 dm, the neck base) and the `helper-l/r-eye`, `helper-upper/lower-teeth`, `helper-tongue` and `helper-l/r-eyelashes` groups; from `makehuman/data/targets/` the head-region deltas of `macrodetails/universal-{female,male}-{child,young,old}-averagemuscle-{min,average,max}weight`, `macrodetails/{african,asian,caucasian}-{female,male}-{child,young,old}` (averaged), and the `head`, `chin`, `cheek`, `eyebrows`, `nose`, `mouth`, `eyes`, `ears` targets listed in `extract.py`. Converted to Forge axes and stored as float16 sparse deltas.

## Three.js

The MIT License

Copyright © 2010-2026 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
