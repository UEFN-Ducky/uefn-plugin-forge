"""AI textures plus the maps UEFN actually needs. Uses a saved OpenAI or Google key."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


def _secret(name: str) -> str:
    try:
        from backend.agent.secrets import get_key
    except Exception:
        return ""
    return get_key(name) or ""


def make_seamless(im: Image.Image) -> Image.Image:
    """Offset so the seam is in the middle, blend it, offset back."""
    im = im.convert("RGB")
    w, h = im.size
    off = ImageChops.offset(im, w // 2, h // 2)
    blur = off.filter(ImageFilter.GaussianBlur(radius=max(2, w // 24)))
    mask = Image.new("L", (w, h), 0)
    band = max(4, w // 10)
    px = mask.load()
    for y in range(h):
        for x in range(w):
            dx = min(abs(x - w // 2), abs((x - w // 2 + w) % w))
            dy = min(abs(y - h // 2), abs((y - h // 2 + h) % h))
            # white where the seam cross is
            t = 0
            if dx < band:
                t = max(t, int(255 * (1 - dx / band)))
            if dy < band:
                t = max(t, int(255 * (1 - dy / band)))
            px[x, y] = t
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, band // 5)))
    blended = Image.composite(blur, off, mask)
    return ImageChops.offset(blended, w // 2, h // 2)


def _luma(im: Image.Image) -> Image.Image:
    return im.convert("L")


def normal_from_height(im: Image.Image, strength: float = 2.0) -> Image.Image:
    """Sobel on luminance. ponytail: 3x3, not a baked high-poly normal."""
    src = _luma(im)
    w, h = src.size
    pix = src.load()
    out = Image.new("RGB", (w, h))
    dst = out.load()
    s = float(strength)

    def at(x: int, y: int) -> int:
        return pix[min(w - 1, max(0, x)), min(h - 1, max(0, y))]

    for y in range(h):
        for x in range(w):
            dx = (
                -at(x - 1, y - 1) - 2 * at(x - 1, y) - at(x - 1, y + 1)
                + at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)
            )
            dy = (
                -at(x - 1, y - 1) - 2 * at(x, y - 1) - at(x + 1, y - 1)
                + at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)
            )
            nx, ny, nz = -dx / 255.0 * s, -dy / 255.0 * s, 1.0
            length = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            dst[x, y] = (
                int(max(0, min(255, (nx / length * 0.5 + 0.5) * 255))),
                int(max(0, min(255, (ny / length * 0.5 + 0.5) * 255))),
                int(max(0, min(255, (nz / length * 0.5 + 0.5) * 255))),
            )
    return out


def roughness_from_color(im: Image.Image) -> Image.Image:
    return _luma(im)


def pack_orm(roughness: Image.Image, metallic: float = 0.0) -> Image.Image:
    rough = roughness.convert("L")
    w, h = rough.size
    metal = int(max(0, min(255, float(metallic) * 255)))
    out = Image.new("RGB", (w, h))
    rp, dp = rough.load(), out.load()
    for y in range(h):
        for x in range(w):
            dp[x, y] = (255, rp[x, y], metal)
    return out


def to_pot(im: Image.Image, size: int = 1024) -> Image.Image:
    size = max(16, int(size))
    pot = 1
    while pot < size:
        pot *= 2
    if pot != size and abs(pot - size) > abs(pot // 2 - size):
        pot //= 2
    return im.convert("RGB").resize((pot, pot), Image.Resampling.LANCZOS)


def _openai(prompt: str, size: str = "1024x1024") -> bytes:
    key = _secret("openai")
    if not key:
        raise RuntimeError("No OpenAI key saved. Add one under Settings → LLMs, or a Google key.")
    body = {"model": "gpt-image-1", "prompt": prompt, "n": 1, "size": size}
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return urllib.request.urlopen(item["url"], timeout=60).read()
    raise RuntimeError("OpenAI image response had no bytes")


def _google(prompt: str) -> bytes:
    key = _secret("gemini")
    if not key:
        raise RuntimeError("No Google key saved.")
    q = urllib.parse.urlencode({"key": key})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?{q}"
    body = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    pred = (data.get("predictions") or [{}])[0]
    b64 = str(pred.get("bytesBase64Encoded") or "")
    if not b64:
        raise RuntimeError("Imagen response had no bytes")
    return base64.b64decode(b64)


def generate_image(prompt: str) -> Image.Image:
    import urllib.parse  # noqa: F401 — used by _google via the module global below

    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    raw = b""
    err = ""
    if _secret("openai"):
        try:
            raw = _openai(prompt)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            err = str(exc)
    if not raw and _secret("gemini"):
        raw = _google(prompt)
    if not raw:
        raise RuntimeError(err or "No OpenAI or Google key saved. Add one under Settings → LLMs.")
    from io import BytesIO

    return Image.open(BytesIO(raw)).convert("RGB")


# _google uses urllib.parse. Import it at module level so the function works.
import urllib.parse  # noqa: E402


def save_set(folder: Path, name: str, color: Image.Image, *, tileable: bool, derive: bool, metallic: float, tag: str = "") -> dict[str, str]:
    """tag makes every save a new file, so undo can still point at the pixels it had."""
    folder.mkdir(parents=True, exist_ok=True)
    color = to_pot(color, max(color.size))
    if tileable:
        color = make_seamless(color)
    paths = {"base_color": str(folder / f"{name}_BaseColor{tag}.png")}
    color.save(paths["base_color"])
    if derive:
        normal = normal_from_height(color)
        rough = roughness_from_color(color)
        orm = pack_orm(rough, metallic)
        paths["normal"] = str(folder / f"{name}_Normal{tag}.png")
        paths["orm"] = str(folder / f"{name}_ORM{tag}.png")
        normal.save(paths["normal"])
        orm.save(paths["orm"])
    return paths
