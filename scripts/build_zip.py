#!/usr/bin/env python3
"""Zip plugin.json + backend (+ optional ui/) for Store upload. No secrets."""

from __future__ import annotations

import json
import hashlib
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


if __name__ == "__main__":
    _prune_superseded_zips(build_zip())
