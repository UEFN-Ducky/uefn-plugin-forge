"""HQ ops: Blender-engine features behind one entry point, run_hq(scene, op, params).

A feature adds backend/hq/<feature>.py (and its engine side in engine/ops/<feature>.py):

    from . import op, engine

    @op("remesh", "remesh(id, voxel=1) voxel remesh, keeps UVs", long=False)
    def remesh(scene, p):
        result, changed = engine(scene, "remesh", [p["id"]], p)
        return {"ids": changed, "tris": result["tris"]}

Files here are imported automatically; nothing else changes.
"""
import difflib
import importlib
import json
import pkgutil

HQ_OPS = {}  # name -> fn(scene, params) -> dict
_SIGS = {}
_LONG = set()
_BROKEN = {}
_KEEP = ("ok", "summary", "ids", "stats", "warnings", "path", "job", "state", "progress")
_LIMIT = 300


def op(name, signature="", long=False):
    """Register fn(scene, params) -> dict. long=True runs it as a Forge job and returns {job}."""
    def wrap(fn):
        HQ_OPS[name] = fn
        _SIGS[name] = signature or name
        if long:
            _LONG.add(name)
        return fn
    return wrap


def engine(scene, engine_op, ids, params=None, timeout=600.0):
    """Send the objects under ids to an engine op, write its scene changes back: (result, changed ids)."""
    from ..engine import bridge
    result, data = bridge.run(scene, engine_op, ids, params, timeout)
    return result, (bridge.apply(scene, data) if data else [])


def _discover():
    for info in pkgutil.iter_modules(__path__):
        try:
            importlib.import_module(__name__ + "." + info.name)
        except Exception as exc:  # one broken feature never hides the others
            _BROKEN[info.name] = "%s: %s" % (type(exc).__name__, exc)


_ORDER = ("mesh", "materials", "rig", "groom", "export", "recipe")


def _feature(fn):
    """Feature file name (backend/hq/<feature>.py) the op was registered from."""
    return getattr(fn, "__module__", "").rsplit(".", 1)[-1] or "other"


def help_text(target=""):
    """No target: op names by feature (<= 1.5k chars). target: that op's signature (<= 400 chars)."""
    if target:
        if target not in HQ_OPS:
            close = difflib.get_close_matches(target, HQ_OPS, n=1)
            hint = " Did you mean %r?" % close[0] if close else ""
            raise ValueError("No HQ op %r.%s Ops: %s" % (target, hint, ", ".join(sorted(HQ_OPS)) or "none"))
        text = _SIGS[target] + (" (runs as a job)" if target in _LONG else "")
        return text if len(text) <= 400 else text[:397] + "..."
    groups = {}
    for name in sorted(HQ_OPS):
        groups.setdefault(_feature(HQ_OPS[name]), []).append(name)
    keys = sorted(groups, key=lambda g: (_ORDER.index(g) if g in _ORDER else len(_ORDER), g))
    text = " | ".join("%s: %s" % (g, " ".join(groups[g])) for g in keys)
    text += "\nhelp(op=<name>) for one op's params. See skill forge/hq."
    text += "".join("\nbroken %s: %s" % item for item in sorted(_BROKEN.items()))
    return text if len(text) <= 1500 else text[:1497] + "..."


def compact(result, verbose=False):
    """ok, summary, ids, stats, warnings, path (+ short scalars) under 300 chars; verbose keeps everything."""
    out = {"ok": True, **(result or {})}
    if verbose:
        return out
    small = {k: v for k, v in out.items() if k in _KEEP or isinstance(v, (int, float, bool))}
    if len(json.dumps(small)) > _LIMIT and isinstance(small.get("ids"), list) and len(small["ids"]) > 4:
        small["ids"] = small["ids"][:4] + ["+%d" % (len(small["ids"]) - 4)]
    for key in ("warnings", "stats"):
        if len(json.dumps(small)) > _LIMIT:
            small.pop(key, None)
    if len(json.dumps(small)) > _LIMIT and isinstance(small.get("summary"), str):
        small["summary"] = small["summary"][:max(20, _LIMIT - len(json.dumps(dict(small, summary=""))) - 3)] + "..."
    return small


def run_hq(scene, name, params=None):
    """One HQ op. Starts the engine install when it is missing ({state: installing, progress})."""
    params = dict(params or {})
    if name in ("", "help"):
        return {"ok": True, "summary": help_text(str(params.get("op") or ""))}
    fn = HQ_OPS.get(name)
    if fn is None:
        close = difflib.get_close_matches(name, HQ_OPS, n=1)
        hint = " Did you mean %r?" % close[0] if close else ""
        raise ValueError("No HQ op %r.%s Ops: %s" % (name, hint, ", ".join(sorted(HQ_OPS)) or "none"))
    from ..engine import runtime
    status = runtime.engine_status()
    if status["state"] != "ready":
        status = runtime.install()
        if status["state"] != "ready":
            if status["state"] == "error":
                return {"ok": False, "state": "error", "summary": "Blender engine install failed: " + status.get("error", "")[:200]}
            return {"ok": True, "state": status["state"], "progress": status.get("progress", 0),
                    "summary": "Blender engine is downloading (%s%%); call again when it is ready" % status.get("progress", 0)}
    verbose = bool(params.pop("verbose", False))
    if name in _LONG and not params.pop("wait", False):
        from ..jobs import submit
        job = submit(lambda **p: compact(fn(scene, p), verbose), params)
        if job.get("done"):
            return job["result"]
        if not job.get("ok"):
            return {"ok": False, "summary": job.get("error", "")}
        return {"ok": True, "job": job["job"], "summary": name + " running; poll forge_job(job)"}
    return compact(fn(scene, params), verbose)


_discover()
