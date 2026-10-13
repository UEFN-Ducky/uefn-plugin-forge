"""Convert static Blender, FBX and glTF assets into new Forge scenes."""

import json
import os
import struct
from urllib.parse import unquote, urlsplit
import shutil
import subprocess
import tempfile
from pathlib import Path

from .files import save_forge
from .scene import _safe, forget, scenes_root

_SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "blend_export_script.txt"
if not _SCRIPT.is_file():  # source checkout, before packaging
    _SCRIPT = Path(__file__).with_name("blend_export_script.py")
_FORMATS = {".blend", ".fbx", ".glb", ".gltf"}


def _blender():
    configured = os.environ.get("FORGE_BLENDER", "")
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise ValueError("FORGE_BLENDER must point to a Blender executable")
        return path
    executable = shutil.which("blender")
    if executable:
        return Path(executable)
    root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation"
    found = sorted(root.glob("*/blender.exe"))
    if not found:
        raise ValueError("Mesh import requires Blender. Install Blender or set FORGE_BLENDER.")
    return found[-1]


def _validate_source(src):
    """Reject remote/traversing glTF resources before a decoder opens them."""
    limit = 512 * 1024 * 1024
    if src.suffix.lower() not in _FORMATS or not src.is_file():
        raise ValueError("Choose an existing .blend, .fbx, .glb or .gltf file")
    size = src.stat().st_size
    if size > limit:
        raise ValueError("Import files must be 512 MB or smaller")
    suffix = src.suffix.lower()
    doc = {}
    if suffix == ".gltf":
        doc = json.loads(src.read_text(encoding="utf-8"))
    elif suffix == ".glb":
        with src.open("rb") as stream:
            header = stream.read(20)
            if len(header) != 20:
                raise ValueError("Invalid GLB header")
            magic, version, length, chunk_length, kind = struct.unpack("<4sIIII", header)
            if magic != b"glTF" or version != 2 or length != size or kind != 0x4E4F534A or chunk_length > size - 20:
                raise ValueError("Invalid GLB header or JSON chunk")
            doc = json.loads(stream.read(chunk_length).decode("utf-8"))
    elif suffix == ".fbx":
        with src.open("rb") as stream:
            header = stream.read(32)
        if not header.startswith(b"Kaydara FBX Binary"):
            raise ValueError("ASCII FBX is unsupported by the Blender decoder. Export binary FBX or GLB.")
    if suffix in {".gltf", ".glb"}:
        if str(doc.get("asset", {}).get("version", "")) != "2.0":
            raise ValueError("Only glTF 2.0 is supported")
        unsupported = set(doc.get("extensionsRequired", [])) - {"KHR_materials_unlit"}
        if unsupported:
            raise ValueError("Unsupported required glTF extensions: " + ", ".join(sorted(unsupported)))
        total = size
        for resource in doc.get("buffers", []) + doc.get("images", []):
            uri = resource.get("uri", "")
            if not uri or uri.startswith("data:"):
                continue
            decoded = unquote(uri)
            parsed = urlsplit(decoded)
            relative = Path(decoded)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or chr(92) in decoded or relative.is_absolute():
                raise ValueError("glTF resources must be local relative files")
            target = (src.parent / relative).resolve()
            if not target.is_relative_to(src.parent.resolve()):
                raise ValueError("glTF resource escapes its source folder")
            if not target.is_file():
                raise ValueError("Missing glTF resource: " + uri)
            actual = target.stat().st_size
            if resource.get("byteLength", 0) > actual:
                raise ValueError("Truncated glTF buffer: " + uri)
            total += actual
            if total > limit:
                raise ValueError("glTF file and resources exceed 512 MB")
    return doc


def _inspect_data(data, folder):
    from .scene import Scene
    scene = Scene(data, folder)
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    triangles = 0
    for node in data["nodes"]:
        if node.get("type") != "object" or not node.get("visible", True):
            continue
        mesh = scene.world_mesh(node)
        triangles += mesh.tri_count()
        a, b = mesh.bounds()
        low = [min(low[i], a[i]) for i in range(3)]
        high = [max(high[i], b[i]) for i in range(3)]
    return {"dimensions_cm": [round(high[i] - low[i], 3) if triangles else 0 for i in range(3)],
            "triangles": triangles, "materials": len(data.get("materials", [])),
            "objects": sum(n.get("type") == "object" for n in data["nodes"])}


def _convert(command):
    from .jobs import cancellable, checkpoint
    if not cancellable():
        return subprocess.run(command, capture_output=True, text=True, timeout=900)
    import time
    checkpoint("Converting geometry and materials")
    # File-backed output avoids pipe deadlocks while allowing timely cancellation.
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log)
        deadline = time.monotonic() + 900
        try:
            while process.poll() is None:
                checkpoint()
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(command, 900)
                time.sleep(0.1)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        log.seek(max(0, log.tell() - 4096))
        return subprocess.CompletedProcess(command, process.returncode, log.read().decode("utf-8", errors="replace"), "")


def import_blend(path, name="", preview=False, target=None, scale=1.0):
    """Compatibility entry point; imports a new scene without replacing existing data."""
    src = Path(path).expanduser().resolve()
    if src.suffix.lower() not in _FORMATS or not src.is_file():
        raise ValueError("Choose an existing .blend, .fbx, .glb or .gltf file")
    if src.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Import files must be 512 MB or smaller")
    _validate_source(src)
    root = scenes_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forge-import-") as temporary:
        output = Path(temporary) / "scene.json"
        command = [str(_blender()), "--background", "--factory-startup", "--disable-autoexec"]
        if src.suffix.lower() == ".blend":
            command.append(str(src))
        command += ["--python", str(_SCRIPT), "--", str(output), str(src)]
        try:
            proc = _convert(command)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Import timed out after 15 minutes; no scene was changed") from exc
        if proc.returncode != 0 or not output.is_file():
            raise ValueError((proc.stderr or proc.stdout or "Blender import failed")[-1000:])
        data = json.loads(output.read_text(encoding="utf-8"))
        objects = sum(n.get("type") == "object" for n in data.get("nodes", []))
        if not objects or not data.get("prototypes"):
            raise ValueError("The file contains no supported mesh objects")
        import math
        scale = float(scale)
        if not math.isfinite(scale) or not 0.0001 <= scale <= 10000:
            raise ValueError("Scale multiplier must be between 0.0001 and 10000")
        for node in data["nodes"]:
            if node.get("parent") is None:
                transform = node["transform"]
                transform["scale"] = [v * scale for v in transform["scale"]]
                transform["pos"] = [v * scale for v in transform["pos"]]
        from .jobs import checkpoint
        checkpoint("Measuring geometry")
        inspection = _inspect_data(data, output.parent)
        checkpoint("Saving scene")
        if preview:
            return {**inspection, "format": src.suffix.lower()[1:], "warnings": data.get("import_warnings", [])}
        if target is not None:
            from .scene import Scene, _op_merge
            texture_dir = target.folder / "textures"
            previous = set(texture_dir.iterdir()) if texture_dir.is_dir() else set()
            try:
                merged = _op_merge(target, {}, Scene(data, output.parent))
            except Exception:
                if texture_dir.is_dir():
                    for added in set(texture_dir.iterdir()) - previous:
                        added.unlink()
                raise
            return {**inspection, **merged, "opened": target.name,
                    "warnings": data.get("import_warnings", [])}
        base = _safe(name or src.stem)
        suffix = 0
        while True:
            scene_name = base if not suffix else base[:54] + "_" + str(suffix)
            folder = root / scene_name
            try:
                folder.mkdir()
                break
            except FileExistsError:
                suffix += 1
        try:
            data["name"] = scene_name
            textures = output.parent / "textures"
            if textures.is_dir():
                shutil.copytree(textures, folder / "textures")
            (folder / "scene.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            forget(scene_name)
            saved = save_forge(scene_name)
        except Exception:
            forget(scene_name)
            shutil.rmtree(folder)
            raise
    return {**inspection, "name": scene_name, "opened": scene_name, "path": saved["path"],
            "objects": objects, "prototypes": len(data["prototypes"]),
            "groups": sum(n.get("type") == "group" for n in data["nodes"]),
            "warnings": data.get("import_warnings", [])}
