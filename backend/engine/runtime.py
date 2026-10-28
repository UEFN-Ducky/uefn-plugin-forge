"""Provision the embedded Blender engine (CPython + bpy) into AppData on first use."""
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BPY = "5.2.3"
VERSION = "bpy " + BPY
# name: (url, sha256, approximate bytes for progress)
_FILES = {
    "python.zip": ("https://www.python.org/ftp/python/3.13.13/python-3.13.13-embed-amd64.zip",
                   "8766a8775746235e23cf5aee5027ab1060bb981d93110577adcf3508aa0cbd55", 10_950_201),
    "pip-26.2.2-py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl",
        "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70", 1_800_000),
    "bpy-5.2.3-cp313-cp313-win_amd64.whl": (
        "https://files.pythonhosted.org/packages/95/82/c0403458ffdb3fd8119e643819cc942f7e1b250c3265ba57d3b9d0eb3d8a/bpy-5.2.3-cp313-cp313-win_amd64.whl",
        "81c6aed7a0f2f6e755a51b507687e5b719b15dfea818859b59af351fac8cce96", 339_609_561),
}
_STALE_LOCK_S = 3600
_state = {"state": "", "version": VERSION, "progress": 0}
_lock = threading.Lock()
_thread = None


def root():
    """Engine folder; never the plugin folder or an island."""
    configured = os.environ.get("FORGE_ENGINE_DIR")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "UEFN-Ducky" / "forge-engine"


def python():
    return Path(os.environ.get("FORGE_ENGINE_PYTHON") or root() / "py" / "python.exe")


def engine_status():
    """{state: missing|installing|ready|error, version, progress[, error]}."""
    with _lock:
        if _state["state"] in ("installing", "error"):
            return dict(_state)
    if os.environ.get("FORGE_ENGINE_PYTHON"):
        if python().is_file():
            return {"state": "ready", "version": "FORGE_ENGINE_PYTHON", "progress": 100}
        return {"state": "error", "version": VERSION, "progress": 0,
                "error": "FORGE_ENGINE_PYTHON points to a missing file: " + str(python())}
    try:
        ready = json.loads((root() / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ready = {}
    if ready.get("version") == VERSION and python().is_file():
        return {"state": "ready", "version": VERSION, "progress": 100, "blender": ready.get("blender", "")}
    return {"state": "missing", "version": VERSION, "progress": 0}


def install(wait=False):
    """Start (or join) the background install; returns engine_status()."""
    global _thread
    status = engine_status()
    if status["state"] == "ready" or os.environ.get("FORGE_ENGINE_PYTHON"):
        return status
    with _lock:
        if _thread is None or not _thread.is_alive():
            _state.update(state="installing", progress=0, stage="Starting")
            _state.pop("error", None)
            _thread = threading.Thread(target=_install, name="forge-engine-install", daemon=True)
            _thread.start()
        thread = _thread
    if wait:
        thread.join()
    return engine_status()


def _set(**values):
    with _lock:
        _state.update(values)


def _install():
    try:
        with _install_lock():
            if engine_status()["state"] != "ready":
                _provision()
        _set(state="", progress=100)
    except Exception as exc:  # surfaced through engine_status, the retry is install()
        _set(state="error", error=str(exc)[-500:] + " Retry starts the download again where it stopped.")


class _install_lock:
    """Cross-process lock file so two Forge processes never install at once."""

    def __enter__(self):
        self.path = root() / "install.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _STALE_LOCK_S
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > _STALE_LOCK_S:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise ValueError("Another Forge has held the engine install lock for an hour. Delete " + str(self.path))
                _set(stage="Waiting for another Forge to finish installing")
                time.sleep(1)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, sha, dest, on_bytes=lambda n: None):
    """Resumable download into dest, verified by SHA256 before it gets its final name."""
    if dest.is_file() and _sha256(dest) == sha:
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    on_bytes(have)
    headers = {"Range": "bytes=%d-" % have, "User-Agent": "UEFN-Ducky-Forge"} if have else {"User-Agent": "UEFN-Ducky-Forge"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            append = have and getattr(response, "status", 200) == 206
            if have and not append:
                on_bytes(-have)
            with open(part, "ab" if append else "wb") as out:
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    out.write(chunk)
                    on_bytes(len(chunk))
    except urllib.error.HTTPError as exc:
        if exc.code != 416:  # 416: the part file is already complete
            raise ValueError("Download failed (%s) for %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise ValueError("Download failed for %s: %s. Check the connection." % (url, exc.reason)) from exc
    if _sha256(part) != sha:
        part.unlink()
        raise ValueError("Checksum mismatch for %s; the partial file was removed." % dest.name)
    part.replace(dest)
    return dest


def _run(command, stage):
    _set(stage=stage)
    env = {**os.environ, "PYTHONNOUSERSITE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(command, capture_output=True, text=True, timeout=1800, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode:
        raise ValueError(stage + " failed: " + (proc.stderr or proc.stdout)[-400:])
    return proc.stdout.strip()


def _provision():
    base = root()
    downloads = base / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    total = sum(size for _, _, size in _FILES.values())
    done = [0]

    def on_bytes(n):
        done[0] += n
        _set(progress=min(90, int(90 * done[0] / total)))

    paths = {}
    for name, (url, sha, _) in _FILES.items():
        _set(stage="Downloading " + name)
        paths[name] = download(url, sha, downloads / name, on_bytes)
    py_dir = base / "py"
    _set(stage="Unpacking Python", progress=90)
    with zipfile.ZipFile(paths["python.zip"]) as archive:
        archive.extractall(py_dir)
    for pth in py_dir.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8").replace("#import site", "import site")
        pth.write_text(text, encoding="utf-8")
    exe = py_dir / "python.exe"
    # pip runs straight from its wheel; the engine never needs pip afterwards.
    pip = str(paths["pip-26.2.2-py3-none-any.whl"]) + "/pip"
    _run([str(exe), pip, "install", "--no-cache-dir", "--no-warn-script-location",
          str(paths["bpy-5.2.3-cp313-cp313-win_amd64.whl"])], "Installing Blender")
    _set(progress=98)
    blender = _run([str(exe), "-c", "import bpy; print(bpy.app.version_string)"], "Checking Blender")
    (base / "ready.json").write_text(json.dumps({"version": VERSION, "blender": blender.splitlines()[-1]}), encoding="utf-8")
    for path in paths.values():
        path.unlink(missing_ok=True)
