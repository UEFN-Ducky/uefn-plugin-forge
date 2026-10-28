#!/usr/bin/env python3
"""Zip plugin.json + backend (+ optional ui/) for Store upload. No secrets.

Two build paths:
- default: plain zip with the backend as readable Python.
- ``--compiled``: hand the plugin to the Ducky build engine, which compiles the
  backend to a Windows extension module (no Python source ships) and minifies web
  assets. The engine lives in the UEFN-Ducky checkout, found via ``DUCKY_REPO`` or
  a sibling path. listener/ runs in UEFN's own Python and stays readable.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import zipfile
import pathlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "deploy"


def _prune_superseded_zips(keep: pathlib.Path | None = None) -> None:
    """Delete every built zip except the one just built.

    deploy/ is gitignored build output; superseded zips are stale files that
    pile up one per version bump and can be re-created by re-running this
    script. Keeping them only invites installing an old build by mistake.
    """
    try:
        zips = sorted(OUT_DIR.glob("*.ducky-plugin.zip"))
    except OSError:
        return
    survivor = keep
    if survivor is None and zips:
        survivor = max(zips, key=lambda p: p.stat().st_mtime)
    for z in zips:
        try:
            if survivor is not None and z.samefile(survivor):
                continue
            z.unlink()
        except OSError:
            pass
SKIP_NAMES = {
    ".git",
    ".pytest_cache",
    "scripts",
    "deploy",
    ".gitignore",
    "README.md",
    "AGENTS.md",
    "__pycache__",
    "node_modules",
    "package.json",
    "package-lock.json",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".zip", ".ducky-plugin"}
# Source for the esbuild bundle — ship viewer.bundle.js + draco/, not ui/src/.
SKIP_REL_PREFIXES = ("ui/src/",)
# Run by the embedded Blender engine's own CPython 3.13, never by Ducky's: always shipped as source.
ENGINE_SOURCE = ("backend/engine/worker.py", "backend/engine/ops/")


def engine_source_files() -> list[tuple[str, Path]]:
    found = [ROOT / ENGINE_SOURCE[0]] + sorted((ROOT / ENGINE_SOURCE[1]).rglob("*.py"))
    return [("/".join(p.relative_to(ROOT).parts), p) for p in found
            if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("test_")]


def add_engine_source(zip_path: Path) -> None:
    """The compiled build turns backend/ into a .pyd; put the engine's worker files back as source."""
    with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        have = set(zf.namelist())
        for rel, path in engine_source_files():
            if rel not in have:
                zf.write(path, rel)


def build_zip(*, out: Path | None = None) -> Path:
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    pid = str(manifest.get("id") or "").strip()
    version = manifest.get("version") or 1
    if not pid:
        raise SystemExit("plugin.json missing id")
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".dat", ".env", ".pem", ".key"}:
            raise SystemExit(f"refusing to pack secret-looking file: {path}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = out or (OUT_DIR / f"{pid}-{version}.ducky-plugin.zip")
    # A different test payload must never reuse an already packaged version.
    ledger_path = OUT_DIR / ".test-build-versions.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    entries = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        rel = "/".join(parts)
        # Repo internals never ship: dot folders (.github CI, .pytest_cache test names) at any depth.
        if any(part in SKIP_NAMES or part.startswith(".") for part in parts) or path.suffix.lower() in SKIP_SUFFIX:
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_REL_PREFIXES):
            continue
        if path.name.startswith("test_") and rel.startswith("backend/"):
            continue
        entries.append((rel, path.read_bytes()))
    fingerprint = hashlib.sha256()
    for rel, contents in entries:
        fingerprint.update(rel.encode() + b"\0" + hashlib.sha256(contents).digest())
    digest = fingerprint.hexdigest()
    if str(version) in ledger and ledger[str(version)] != digest:
        raise SystemExit(f"Forge {version} was already packaged with different contents. Bump plugin.json before the next test build.")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, contents in entries:
            zf.writestr(rel, contents)
    ledger[str(version)] = digest
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    _prune_superseded_zips(dest)
    return dest


def _find_ducky_repo() -> Path:
    """Locate the UEFN-Ducky checkout that holds the build engine.

    ``DUCKY_REPO`` wins; otherwise try sibling checkouts next to this repo's parent.
    """
    candidates: list[Path] = []
    env = os.environ.get("DUCKY_REPO", "").strip()
    if env:
        candidates.append(Path(env))
    github = ROOT.parent.parent  # .../GitHub (this repo lives under uefn-plugins/)
    for name in ("UEFN-Ducky-Release", "UEFN-Ducky"):
        candidates.append(github / name)
    for cand in candidates:
        if (cand / "ducky_app" / "backend" / "uefn_plugins" / "compile.py").is_file():
            return cand
    raise SystemExit(
        "UEFN-Ducky checkout not found — set DUCKY_REPO to it (must contain "
        "ducky_app/backend/uefn_plugins/compile.py)."
    )


def _ducky_python(repo: Path) -> str:
    venv = repo / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.is_file() else sys.executable


def build_compiled_zip(*, out: Path | None = None, release: bool = False) -> Path:
    """Build a compiled Store zip by invoking the Ducky build engine CLI.

    ``release`` bakes the Store license check in (with the Store's signing key), so a
    Store upload is never unlicensed.
    """
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    pid = str(manifest.get("id") or "").strip()
    version = manifest.get("version") or 1
    if not pid:
        raise SystemExit("plugin.json missing id")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = out or (OUT_DIR / f"{pid}-{version}-compiled.ducky-plugin.zip")
    repo = _find_ducky_repo()
    ducky_app = repo / "ducky_app"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ducky_app) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [_ducky_python(repo), "-m", "backend.uefn_plugins.compile", str(ROOT), str(dest)]
    if release:
        cmd.append("--release")
    print(f"compiling via {repo}")
    subprocess.run(cmd, cwd=str(ducky_app), env=env, check=True)
    add_engine_source(dest)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    _prune_superseded_zips(dest)
    return dest


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--compiled",
        action="store_true",
        help="compile the backend to a .pyd via the Ducky build engine (no Python source ships)",
    )
    args = ap.parse_args()
    if args.compiled:
        build_compiled_zip()
    else:
        _prune_superseded_zips(build_zip())
