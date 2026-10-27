"""Saved cameras and keyed camera shots. They live in scene.json, so they sync with the
team copy and Undo takes them back like any other edit. ui/src/shots.js evaluates the
same path for the viewport and the frame-by-frame render."""

from __future__ import annotations

import math
import secrets
from typing import Any

EASES = ("linear", "in_out", "in", "out")
FOV_RANGE = (5.0, 120.0)


def ease(u: float, kind: str = "linear") -> float:
    u = min(1.0, max(0.0, float(u)))
    if kind == "in_out":
        return u * u * (3 - 2 * u)
    if kind == "in":
        return u * u
    if kind == "out":
        return 1 - (1 - u) * (1 - u)
    return u


def _catmull(p0: list, p1: list, p2: list, p3: list, u: float) -> list[float]:
    u2, u3 = u * u, u * u * u
    return [0.5 * (2 * p1[i] + (p2[i] - p0[i]) * u + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * u2
                   + (3 * p1[i] - p0[i] - 3 * p2[i] + p3[i]) * u3) for i in range(3)]


def eval_path(keys: list[dict], t: float, kind: str = "linear") -> dict[str, Any]:
    """Camera {pos, target, fov} at t seconds: a smooth curve through every key."""
    keys = sorted(keys, key=lambda k: float(k["t"]))
    if not keys:
        raise ValueError("a shot needs at least one key")
    first, last = float(keys[0]["t"]), float(keys[-1]["t"])
    if len(keys) == 1 or last <= first:
        k = keys[0]
        return {"pos": list(k["pos"]), "target": list(k["target"]), "fov": float(k["fov"])}
    tt = first + ease((float(t) - first) / (last - first), kind) * (last - first)
    i = 0
    while i < len(keys) - 2 and float(keys[i + 1]["t"]) < tt:
        i += 1
    a, b = keys[i], keys[i + 1]
    span = float(b["t"]) - float(a["t"])
    u = 0.0 if span <= 0 else min(1.0, max(0.0, (tt - float(a["t"])) / span))
    before, after = keys[max(0, i - 1)], keys[min(len(keys) - 1, i + 2)]
    return {
        "pos": _catmull(before["pos"], a["pos"], b["pos"], after["pos"], u),
        "target": _catmull(before["target"], a["target"], b["target"], after["target"], u),
        "fov": float(a["fov"]) + (float(b["fov"]) - float(a["fov"])) * u,
    }


def vec3(value: Any, what: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{what} is [x, y, z] in centimetres")
    out = [float(v) for v in value]
    if not all(math.isfinite(v) for v in out):
        raise ValueError(f"{what} must be finite numbers")
    return out


def fov_of(value: Any, default: float = 45.0) -> float:
    fov = float(default if value is None else value)
    if not FOV_RANGE[0] <= fov <= FOV_RANGE[1]:
        raise ValueError(f"fov is {FOV_RANGE[0]:g} to {FOV_RANGE[1]:g} degrees")
    return fov


def _pose(p: dict, base: dict | None = None) -> dict[str, Any]:
    base = base or {}
    return {
        "pos": vec3(p["pos"], "pos") if p.get("pos") is not None else list(base.get("pos") or [1200, -1600, 900]),
        "target": vec3(p["target"], "target") if p.get("target") is not None else list(base.get("target") or [0, 0, 256]),
        "fov": fov_of(p.get("fov"), base.get("fov", 45.0)),
    }


def _find(items: list[dict], ref: str, what: str) -> dict:
    ref = str(ref or "").strip()
    for item in items:
        if item.get("id") == ref or str(item.get("name") or "").lower() == ref.lower():
            return item
    raise ValueError(f"No {what} named {ref!r}")


def _unique(items: list[dict], name: str) -> str:
    taken = {str(i.get("name") or "").lower() for i in items}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def find_camera(data: dict, ref: str) -> dict:
    return _find(data.get("cameras") or [], ref, "camera")


def op_camera(scene: Any, p: dict) -> dict[str, Any]:
    """action add | edit | delete on the scene's saved cameras."""
    cams = scene.data.setdefault("cameras", [])
    action = str(p.get("action") or "add")
    if action == "add":
        cam = {"id": "c" + secrets.token_hex(3), "name": _unique(cams, str(p.get("camera_name") or "Camera").strip() or "Camera"), **_pose(p)}
        cams.append(cam)
        return {"summary": f"Added camera {cam['name']}", "ids": [], "camera": cam}
    cam = find_camera(scene.data, str(p.get("camera") or ""))
    if action == "edit":
        cam.update(_pose(p, cam))
        if p.get("camera_name"):
            cam["name"] = _unique([c for c in cams if c is not cam], str(p["camera_name"]).strip())
        return {"summary": f"Moved camera {cam['name']}", "ids": [], "camera": cam}
    if action == "delete":
        cams.remove(cam)
        for shot in scene.data.get("shots") or []:
            if shot.get("camera") == cam["id"]:
                shot.pop("camera", None)
        return {"summary": f"Deleted camera {cam['name']}", "ids": []}
    raise ValueError("camera action is add, edit or delete")


def shot_duration(shot: dict) -> float:
    keys = shot.get("keys") or []
    last = max((float(k["t"]) for k in keys), default=0.0)
    return max(float(shot.get("duration") or 0.0), last)


def op_shot(scene: Any, p: dict) -> dict[str, Any]:
    """action add | edit | delete | key | unkey on the scene's shots."""
    shots = scene.data.setdefault("shots", [])
    action = str(p.get("action") or "add")
    if action == "add":
        shot = {"id": "s" + secrets.token_hex(3), "name": _unique(shots, str(p.get("shot_name") or "Shot").strip() or "Shot"),
                "duration": max(0.1, float(p.get("duration") or 4.0)), "ease": "in_out", "keys": []}
        if p.get("ease") in EASES:
            shot["ease"] = p["ease"]
        shots.append(shot)
        return {"summary": f"Added shot {shot['name']}", "ids": [], "shot": shot}
    shot = _find(shots, str(p.get("shot") or ""), "shot")
    if action == "edit":
        if p.get("duration") is not None:
            shot["duration"] = max(0.1, float(p["duration"]))
        if p.get("ease") is not None:
            if p["ease"] not in EASES:
                raise ValueError("ease is linear, in_out, in or out")
            shot["ease"] = p["ease"]
        if p.get("shot_name"):
            shot["name"] = _unique([s for s in shots if s is not shot], str(p["shot_name"]).strip())
        if "camera" in p:
            shot["camera"] = find_camera(scene.data, str(p["camera"]))["id"] if p["camera"] else None
        return {"summary": f"Changed shot {shot['name']}", "ids": [], "shot": shot}
    if action == "delete":
        shots.remove(shot)
        return {"summary": f"Deleted shot {shot['name']}", "ids": []}
    if action == "key":
        t = float(p.get("t") or 0.0)
        if not math.isfinite(t) or t < 0:
            raise ValueError("t is seconds from the shot's start")
        key = {"t": round(t, 3), **_pose(p)}
        shot["keys"] = sorted([k for k in shot["keys"] if abs(float(k["t"]) - key["t"]) > 1e-3] + [key], key=lambda k: k["t"])
        shot["duration"] = max(float(shot.get("duration") or 0.0), key["t"])
        return {"summary": f"Keyed {shot['name']} at {key['t']:g} s", "ids": [], "shot": shot}
    if action == "unkey":
        t = float(p.get("t") or 0.0)
        kept = [k for k in shot["keys"] if abs(float(k["t"]) - t) > 1e-3]
        if len(kept) == len(shot["keys"]):
            raise ValueError(f"{shot['name']} has no key at {t:g} s")
        shot["keys"] = kept
        return {"summary": f"Removed the key at {t:g} s from {shot['name']}", "ids": [], "shot": shot}
    raise ValueError("shot action is add, edit, delete, key or unkey")


def op_cameras(scene: Any, _p: dict) -> dict[str, Any]:
    """Read-only: saved cameras and shots."""
    return {"summary": "cameras", "ids": [], "cameras": scene.data.get("cameras") or [], "shots": scene.data.get("shots") or []}
