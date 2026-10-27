"""Forge's live layer: duckies fly the viewport and show up as flyers, people see each other,
and the panel renders what a camera sees (one picture, or a shot frame by frame to MP4).

The panel polls forge_live a few times a second while it is on screen: it hands in this
PC's viewport camera and gets back the duckies' cameras, pings, and any picture or shot
frames a tool is waiting for. Teammates' duckies arrive through the live group's shared
values (key "ai:<chat>"), people through each PC's live state."""

from __future__ import annotations

import base64
import binascii
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .scene import _LOCK as SCENE_LOCK, _captures, load as load_scene, scenes_root
from .shots import find_camera, fov_of, shot_duration, vec3

AI_TTL_S = 20.0  # a ducky's flyer fades this long after its last Forge call
PING_S = 3.0
VIEW_WAIT_S = 20.0
FRAME_RETRY_S = 10.0  # a frame handed out and not back by then goes out again
MAX_FRAMES = 60 * 120  # two minutes at 60 fps
SIZES = (320, 3840)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_api: Any = None
_ai: dict[str, dict] = {}  # "ai:<chat>" → that ducky's camera in a scene
_user: dict[str, dict] = {}  # scene → this PC's viewport camera, as the panel last said
_pings: list[dict] = []
_views: dict[str, dict] = {}  # picture requests waiting for the panel
_jobs: dict[str, dict] = {}  # shot renders: frames in, MP4 out


def bind(api: Any) -> None:
    global _api
    _api = api


def reset() -> None:
    """Tests start clean."""
    with _lock:
        for table in (_ai, _user, _views, _jobs):
            table.clear()
        _pings.clear()


def who() -> tuple[str, str]:
    """(key, name) of the ducky calling now, from the host's run identity."""
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:  # noqa: BLE001 — not inside the app (tests) or no agent run
        ctx = None
    conv = str(getattr(ctx, "conv_id", "") or "")
    name = str(getattr(ctx, "ducky_name", "") or "")
    return "ai:" + (conv[:40] or "agent"), (name or "AI")[:40]


def _visible() -> bool:
    try:
        return bool(_api is not None and (_api.panel_state() or {}).get("visible"))
    except Exception:  # noqa: BLE001
        return False


def _live(method: str, *args: Any) -> Any:
    """Best effort: off a team copy or out of a live group these calls just say no."""
    try:
        return getattr(_api.live, method)(*args) if _api is not None else None
    except Exception:  # noqa: BLE001
        return None


def _wire(rec: dict) -> dict:
    keys = ("key", "name", "scene", "pos", "target", "fov", "doing", "ts", "v", "drive", "path")
    return {k: rec.get(k) for k in keys if rec.get(k) is not None}


def _publish(rec: dict) -> None:
    out = _wire(rec)
    if out.get("path") and len(str(out["path"])) > 6000:
        out.pop("path")  # the 8 KB shared-value cap: teammates see where it ends up
    _live("share", rec["key"], out)


def _descendants(scene: Any, ids: list[str]) -> list[dict]:
    want = set(ids)
    out = []
    for node in scene.data.get("nodes") or []:
        cur, seen = node, set()
        while cur and cur["id"] not in seen:
            if cur["id"] in want:
                if node.get("type") == "object":
                    out.append(node)
                break
            seen.add(cur["id"])
            cur = scene.node(cur.get("parent"))
    return out


def bounds(name: str, ids: list[str] | None = None) -> tuple[list[float], list[float]] | None:
    """World bounds of objects (groups count their children); all objects when ids is empty."""
    with SCENE_LOCK:
        scene = load_scene(name)
        nodes = _descendants(scene, list(ids)) if ids else [n for n in scene.data.get("nodes") or [] if n.get("type") == "object"]
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for node in nodes[:200]:  # ponytail: framing reads 200 objects; a 20k scene frames from those
            if not node.get("visible", True):
                continue
            mesh = scene.world_mesh(node)
            if not mesh.verts:
                continue
            a, b = mesh.bounds()
            lo = [min(lo[i], a[i]) for i in range(3)]
            hi = [max(hi[i], b[i]) for i in range(3)]
    return (lo, hi) if lo[0] <= hi[0] else None


def frame_pose(lo: list, hi: list, fov: float = 45.0, toward: list | None = None) -> dict:
    """A camera that fits the box, looking from `toward` (a position) or the default corner."""
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    radius = max(1.0, math.dist(lo, hi) / 2)
    d = [toward[i] - center[i] for i in range(3)] if toward else [1200.0, -1600.0, 900.0]
    n = math.hypot(*d) or 1.0
    dist = max(128.0, radius / math.sin(math.radians(fov) / 2) * 1.15)
    return {"pos": [center[i] + d[i] / n * dist for i in range(3)], "target": center, "fov": fov}


def orbit_path(center: list, start: list, seconds: float, turns: float = 1.0) -> list[dict]:
    """Keys circling `center` once from `start`, same height and distance."""
    dx, dy = start[0] - center[0], start[1] - center[1]
    radius, a0, steps = max(64.0, math.hypot(dx, dy)), math.atan2(dy, dx), 12
    return [{"t": round(seconds * i / steps, 3),
             "pos": [center[0] + radius * math.cos(a0 + 2 * math.pi * turns * i / steps),
                     center[1] + radius * math.sin(a0 + 2 * math.pi * turns * i / steps), start[2]],
             "target": list(center), "fov": 45.0} for i in range(steps + 1)]


def _current(name: str, key: str) -> dict:
    rec = _ai.get(key)
    if rec and rec.get("scene") == name:
        return rec
    user = _user.get(name)
    base = {"pos": [1200.0, -1600.0, 900.0], "target": [0.0, 0.0, 256.0], "fov": 45.0}
    if user:
        base = {k: user[k] for k in ("pos", "target", "fov")}
    return base


def note_activity(name: str, summary: str, ids: list | None = None) -> None:
    """Every Forge edit a ducky makes shows its flyer at the work, saying what it does."""
    if not name:
        return
    key, label = who()
    with _lock:
        rec = _ai.get(key)
        fresh = rec is None or rec.get("scene") != name
    pose = None
    if fresh:
        box = None
        try:
            box = bounds(name, [str(i) for i in ids or [] if isinstance(i, str)][:20])
        except Exception:  # noqa: BLE001 — a flyer never fails an edit
            box = None
        if box:
            pose = frame_pose(*box)
    with _lock:
        rec = _ai.get(key)
        if rec is None or rec.get("scene") != name:
            rec = _ai[key] = {"key": key, "name": label, "scene": name, "v": 0, **(pose or _current(name, key))}
        rec.update(name=label, doing=str(summary or "")[:80], ts=time.time())
        out = dict(rec)
    _publish(out)


def camera(name: str, action: str = "get", pos: list | None = None, target: list | None = None, fov: float | None = None,
           seconds: float = 1.0, ids: list | None = None, drive: bool = False, doing: str = "",
           camera_name: str = "") -> dict:
    """The ducky's own camera in a scene; see forge_camera."""
    key, label = who()
    now = time.time()
    if action == "people":
        return {"people": people(name), "you": {"key": key, "name": label}}
    if action == "ping":
        point = vec3(target or pos, "target")
        ping = {"scene": name, "point": point, "by": label, "ai": True, "ts": now}
        with _lock:
            _pings.append(ping)
            del _pings[:-20]
        _live("send", "forge.ping", {k: ping[k] for k in ("scene", "point", "by", "ai")})
        return {"pinged": point}
    if action in ("save", "cameras"):
        from .scene import dispatch

        if action == "cameras":
            return dispatch(name, "cameras", {})
        with _lock:
            pose = dict(_current(name, key))
        made = dispatch(name, "camera", {"action": "add", "camera_name": camera_name or "Camera",
                                         "pos": pose["pos"], "target": pose["target"], "fov": pose["fov"]}, actor="agent")
        return {"camera": made.get("camera"), "summary": made.get("summary")}
    with _lock:
        start = dict(_current(name, key))
    seconds = max(0.0, min(30.0, float(seconds or 0.0)))
    path = None
    if action == "get":
        with _lock:
            user = dict(_user.get(name) or {})
        return {"camera": {k: start.get(k) for k in ("pos", "target", "fov")},
                "viewport": {k: user.get(k) for k in ("pos", "target", "fov")} if user else None}
    if action == "goto":
        pose = {"pos": vec3(pos, "pos") if pos is not None else start["pos"],
                "target": vec3(target, "target") if target is not None else start["target"],
                "fov": fov_of(fov, start.get("fov", 45.0))}
    elif action == "look_at":
        if target is None and not ids:
            raise ValueError("look_at needs target or ids")
        aim = vec3(target, "target") if target is not None else None
        if aim is None:
            box = bounds(name, list(ids or []))
            if not box:
                raise ValueError("Those ids have no geometry to look at")
            aim = [(box[0][i] + box[1][i]) / 2 for i in range(3)]
        pose = {"pos": vec3(pos, "pos") if pos is not None else start["pos"], "target": aim, "fov": fov_of(fov, start.get("fov", 45.0))}
    elif action in ("frame", "orbit"):
        box = bounds(name, list(ids or []))
        if not box:
            raise ValueError("Nothing to frame: the scene (or those ids) has no geometry")
        pose = frame_pose(box[0], box[1], fov_of(fov, 45.0), start["pos"])
        if action == "orbit":
            path = orbit_path(pose["target"], pose["pos"], max(1.0, seconds or 6.0))
            seconds = path[-1]["t"]
    else:
        raise ValueError("action is get, goto, look_at, frame, orbit, people, ping, save or cameras")
    if path is None and seconds > 0:
        path = [{"t": 0.0, **{k: start[k] for k in ("pos", "target")}, "fov": float(start.get("fov", 45.0))},
                {"t": round(seconds, 3), **pose}]
    with _lock:
        rec = _ai.setdefault(key, {"key": key, "v": 0})
        rec.update(name=label, scene=name, ts=now, drive=bool(drive), path=path,
                   doing=(doing or {"orbit": "orbiting", "frame": "looking around"}.get(action, rec.get("doing") or "looking"))[:80],
                   **({k: path[-1][k] for k in ("pos", "target", "fov")} if path else pose))
        rec["v"] = int(rec.get("v") or 0) + 1
        out = dict(rec)
    _publish(out)
    return {"camera": {k: out[k] for k in ("pos", "target", "fov")}, "seconds": seconds,
            "drives_viewport": bool(drive), "panel_open": _visible()}


def people(name: str) -> list[dict]:
    """Who is in this scene: this PC's viewport, teammates live in it, and duckies."""
    now = time.time()
    out = []
    with _lock:
        user = dict(_user.get(name) or {})
        ai = [dict(r) for r in _ai.values() if r.get("scene") == name and now - float(r.get("ts") or 0) < AI_TTL_S]
    if user and now - float(user.get("ts") or 0) < 10:
        out.append({"kind": "person", "name": "This PC's viewport", **{k: user.get(k) for k in ("pos", "target", "fov")}})
    for row in _live("member_states") or []:
        state = row.get("state") if isinstance(row, dict) else None
        if isinstance(state, dict) and state.get("scene") == name:
            out.append({"kind": "person", "name": str((row.get("from") or {}).get("name") or "Teammate"),
                        **{k: state.get(k) for k in ("pos", "target", "fov", "mode", "picked")}})
    for (key, row) in ((_live("shared") or {}) or {}).items():
        value = row.get("value") if isinstance(row, dict) else None
        if str(key).startswith("ai:") and isinstance(value, dict) and value.get("scene") == name and all(a["key"] != key for a in ai):
            ai.append(value)
    out += [{"kind": "ducky", "name": r.get("name"), "doing": r.get("doing"), **{k: r.get(k) for k in ("pos", "target", "fov")}} for r in ai]
    return out


# ---------------------------------------------------------------- the panel's side


def live_poll(scene: str = "", me: dict | None = None, **_kwargs: Any) -> dict:
    """forge_live: the panel's camera in, everything it should draw or render out."""
    now = time.time()
    with _lock:
        if scene and isinstance(me, dict):
            try:
                _user[scene] = {"pos": vec3(me.get("pos"), "pos"), "target": vec3(me.get("target"), "target"),
                                "fov": fov_of(me.get("fov")), "ts": now}
            except (TypeError, ValueError):
                pass
        for key in [k for k, r in _ai.items() if now - float(r.get("ts") or 0) > AI_TTL_S * 3]:
            del _ai[key]
        ai = [_wire(r) for r in _ai.values() if r.get("scene") == scene and now - float(r.get("ts") or 0) < AI_TTL_S]
        pings = [p for p in _pings if p["scene"] == scene and now - p["ts"] < PING_S]
        view = next(({"id": rid, "camera": v["camera"], "width": v["width"], "height": v["height"], "clean": v["clean"]}
                     for rid, v in _views.items() if v["scene"] == scene and not v["event"].is_set()), None)
        job = next((_hand_out(j, now) for j in _jobs.values() if j["scene"] == scene and j["state"] == "frames"), None)
        progress = [{"id": j["id"], "shot": j["shot_name"], "state": j["state"], "done": len(j["got"]), "frames": j["frames"],
                     "path": j.get("path") or "", "error": j.get("error") or ""}
                    for j in _jobs.values() if j["scene"] == scene and now - j["started"] < 600]
    return {"ok": True, "ai": ai, "pings": pings, "view": view, "job": job, "renders": progress, "now": now}


def _decode(b64: str) -> bytes:
    text = str(b64 or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("picture is not base64") from exc


def view_put(id: str = "", b64: str = "", error: str = "", **_kwargs: Any) -> dict:  # noqa: A002 — the panel's name
    with _lock:
        req = _views.get(str(id))
    if req is None:
        return {"ok": False, "error": "That picture is no longer wanted"}
    if error:
        req["error"] = str(error)[:200]
    else:
        try:
            raw = _decode(b64)
            if not raw.startswith(b"\x89PNG"):
                raise ValueError("picture is not a PNG")
            path = _captures() / f"forge_view_{uuid.uuid4().hex[:8]}.png"
            path.write_bytes(raw)
            req["path"] = str(path)
        except (OSError, ValueError) as exc:
            req["error"] = str(exc)
    req["event"].set()
    return {"ok": True}


def resolve_pose(name: str, spec: Any) -> dict:
    """A camera spec: a {pos, target, fov} dict, "ai" (the ducky's), "viewport", or a saved camera's name."""
    key, _ = who()
    if isinstance(spec, dict):
        return {"pos": vec3(spec.get("pos"), "pos"), "target": vec3(spec.get("target"), "target"), "fov": fov_of(spec.get("fov"))}
    ref = str(spec or "").strip()
    with _lock:
        if ref in ("", "ai", "me"):
            pose = dict(_current(name, key))
            return {k: pose[k] for k in ("pos", "target", "fov")}
        if ref in ("viewport", "user", "person"):
            user = _user.get(name)
            if not user:
                raise ValueError("The Forge panel hasn't reported its viewport yet: open the Forge tab")
            return {k: user[k] for k in ("pos", "target", "fov")}
    with SCENE_LOCK:
        cam = find_camera(load_scene(name).data, ref)
    return {k: cam[k] for k in ("pos", "target", "fov")}


def _size(width: Any, height: Any) -> tuple[int, int]:
    w, h = int(width or 1280), int(height or 720)
    if not (SIZES[0] <= w <= SIZES[1] and SIZES[0] // 2 <= h <= SIZES[1]):
        raise ValueError(f"width and height are {SIZES[0]} to {SIZES[1]} pixels")
    return w - w % 2, h - h % 2


def view(name: str, spec: Any = None, width: int = 1280, height: int = 720, clean: bool = False) -> dict:
    """One picture of the live viewport's scene from a camera, rendered by the open panel."""
    pose = resolve_pose(name, spec)
    w, h = _size(width, height)
    if not _visible():
        return {"ok": False, "camera": pose, "panel_open": False,
                "error": "The Forge tab isn't on screen, so there is no viewport to look through. Open the Forge tab, "
                         "or use forge_render for a quick preview of single objects."}
    rid = uuid.uuid4().hex[:12]
    req = {"scene": name, "camera": pose, "width": w, "height": h, "clean": bool(clean), "event": threading.Event(),
           "path": "", "error": ""}
    with _lock:
        _views[rid] = req
    try:
        if not req["event"].wait(VIEW_WAIT_S):
            return {"ok": False, "camera": pose, "error": "The Forge panel didn't answer in time; is the scene loaded?"}
    finally:
        with _lock:
            _views.pop(rid, None)
    if req["error"]:
        return {"ok": False, "camera": pose, "error": req["error"]}
    return {"ok": True, "camera": pose, "width": w, "height": h, "paths": [req["path"]]}


# ---------------------------------------------------------------- shots to MP4


def videos_root() -> Path:
    root = scenes_root().parent / "videos"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _hand_out(job: dict, now: float) -> dict | None:
    """The next few frames for the panel to draw; one handed out and lost goes again."""
    todo = []
    for i in range(job["frames"]):
        if i in job["got"] or now - job["claimed"].get(i, 0.0) < FRAME_RETRY_S:
            continue
        todo.append(i)
        if len(todo) == 4:
            break
    for i in todo:
        job["claimed"][i] = now
    if not todo:
        return None
    return {"id": job["id"], "keys": job["keys"], "ease": job["ease"], "fps": job["fps"], "width": job["width"],
            "height": job["height"], "frames": todo, "total": job["frames"]}


def start_render(name: str, shot: str, fps: int = 30, width: int = 1920, height: int = 1080, imk_project: str = "") -> dict:
    with SCENE_LOCK:
        data = load_scene(name).data
        found = next((s for s in data.get("shots") or [] if s.get("id") == shot or str(s.get("name") or "").lower() == str(shot).lower()), None)
        if found is None:
            raise ValueError(f"No shot named {shot!r}")
        found = dict(found, keys=[dict(k) for k in found.get("keys") or []])
    if not found["keys"]:
        raise ValueError(f"Shot {found['name']} has no keys: key it first (forge_shot action=key)")
    fps = int(fps or 30)
    if fps not in (24, 25, 30, 50, 60):
        raise ValueError("fps is 24, 25, 30, 50 or 60")
    w, h = _size(width, height)
    frames = max(1, round(shot_duration(found) * fps))
    if frames > MAX_FRAMES:
        raise ValueError("A shot renders up to two minutes")
    jid = uuid.uuid4().hex[:12]
    folder = videos_root() / f".frames-{jid}"
    folder.mkdir(parents=True, exist_ok=True)
    job = {"id": jid, "scene": name, "shot": found["id"], "shot_name": found["name"], "keys": found["keys"],
           "ease": found.get("ease") or "linear", "fps": fps, "width": w, "height": h, "frames": frames,
           "folder": folder, "got": set(), "claimed": {}, "state": "frames", "started": time.time(),
           "done": threading.Event(), "path": "", "error": "", "imk_project": str(imk_project or ""), "media": None}
    with _lock:
        _jobs[jid] = job
    return job


def frame_put(job: str = "", index: int = -1, b64: str = "", error: str = "", **_kwargs: Any) -> dict:
    with _lock:
        rec = _jobs.get(str(job))
    if rec is None or rec["state"] != "frames":
        return {"ok": False, "error": "That render is over"}
    if error:
        _finish(rec, error=str(error)[:200])
        return {"ok": True}
    i = int(index)
    if not 0 <= i < rec["frames"]:
        return {"ok": False, "error": "frame out of range"}
    raw = _decode(b64)
    if not raw.startswith(b"\xff\xd8"):
        return {"ok": False, "error": "frame is not a JPEG"}
    (rec["folder"] / f"{i:05d}.jpg").write_bytes(raw)
    with _lock:
        rec["got"].add(i)
        complete = len(rec["got"]) == rec["frames"] and rec["state"] == "frames"
        if complete:
            rec["state"] = "encoding"
    if complete:
        threading.Thread(target=_encode_job, args=(rec,), daemon=True, name="forge-shot-encode").start()
    return {"ok": True, "done": len(rec["got"]), "frames": rec["frames"]}


def _ffmpeg() -> tuple[str, list[str]]:
    """Ducky's own ffmpeg and its fastest working H.264 encoder, else one on PATH."""
    try:
        from backend.agent.video import ffmpeg_install
        from backend.agent.video.encoder import pick_encoder

        return str(ffmpeg_install.ensure_installed()[0]), [pick_encoder()]
    except Exception:  # noqa: BLE001 — not inside the app, or its video tools aren't there
        pass
    exe = os.environ.get("FORGE_FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("No ffmpeg on this PC: Ducky's video tools aren't installed")
    return exe, ["libx264", "libopenh264", "mpeg4"]


def encode(folder: Path, fps: int, out: Path) -> Path:
    exe, encoders = _ffmpeg()
    last = ""
    for enc in encoders:
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps), "-i", str(folder / "%05d.jpg"),
               "-c:v", enc, "-b:v", "16M", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
               "-movflags", "+faststart", str(out)]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            last = str(exc)
            continue
        if run.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            return out
        last = (run.stderr or run.stdout or f"{enc} failed").strip()[-300:]
    raise RuntimeError(f"ffmpeg could not encode the shot: {last}")


def _finish(job: dict, *, path: str = "", error: str = "") -> None:
    with _lock:
        job["state"] = "failed" if error else "done"
        job["path"], job["error"] = path, error
    shutil.rmtree(job["folder"], ignore_errors=True)
    job["done"].set()


def _encode_job(job: dict) -> None:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{job['scene']}_{job['shot_name']}")[:80]
    out = videos_root() / f"{safe}_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    try:
        encode(job["folder"], job["fps"], out)
    except Exception as exc:  # noqa: BLE001 — the tool reports it
        _finish(job, error=str(exc))
        return
    if job["imk_project"] and _api is not None:
        try:
            job["media"] = _api.call_tool("imk_media_link", {"project_id": job["imk_project"], "path": str(out)})
        except Exception as exc:  # noqa: BLE001
            job["media"] = {"ok": False, "error": f"Island Media Kit didn't take the clip: {exc}"}
    _finish(job, path=str(out))


def job_result(job: dict) -> dict:
    out = {"job": job["id"], "shot": job["shot_name"], "state": job["state"], "frames": job["frames"], "done": len(job["got"]),
           "fps": job["fps"], "seconds": round(job["frames"] / job["fps"], 2), "size": [job["width"], job["height"]]}
    if job["path"]:
        out["path"] = out["video"] = job["path"]
    if job["error"]:
        out["ok"], out["error"] = False, job["error"]
    if job.get("media") is not None:
        out["island_media_kit"] = job["media"]
    return out


def render_shot(name: str, shot: str = "", fps: int = 30, width: int = 1920, height: int = 1080, imk_project: str = "",
                job: str = "", wait_seconds: float = 300) -> dict:
    if job:
        with _lock:
            rec = _jobs.get(job)
        if rec is None:
            return {"ok": False, "error": f"No render {job!r} on this PC"}
    else:
        if not _visible():
            return {"ok": False, "panel_open": False,
                    "error": "Shots render in the Forge panel: open the Forge tab, then call this again."}
        rec = start_render(name, shot, fps, width, height, imk_project)
    rec["done"].wait(max(0.0, min(1800.0, float(wait_seconds or 0))))
    out = job_result(rec)
    if rec["state"] in ("frames", "encoding"):
        out["note"] = f"Still rendering ({out['done']}/{out['frames']} frames). Call again with job={rec['id']!r} to wait for it."
    return out
