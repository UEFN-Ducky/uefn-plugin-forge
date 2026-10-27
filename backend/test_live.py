"""Cameras, shots, flyers and panel renders. No host: a fake api stands in for the live group."""

from __future__ import annotations

import base64
import io
import os
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

from backend import live_view
from backend.scene import dispatch
from backend.shots import ease, eval_path


class FakeLive:
    def __init__(self) -> None:
        self.values: dict = {}
        self.sent: list = []
        self.states: list = []

    def share(self, key, value):
        self.values[key] = value
        return {"ok": True}

    def shared(self):
        return {k: {"value": v, "by": {}} for k, v in self.values.items()}

    def send(self, topic, data):
        self.sent.append((topic, data))
        return {"ok": True}

    def member_states(self):
        return self.states


class FakeApi:
    def __init__(self) -> None:
        self.live = FakeLive()
        self.visible = False
        self.tools: list = []

    def panel_state(self):
        return {"visible": self.visible}

    def call_tool(self, name, arguments=None):
        self.tools.append((name, arguments))
        raise ValueError("Island Media Kit isn't installed")


def _fresh(name: str) -> FakeApi:
    root = Path(tempfile.mkdtemp(prefix="forge-live-"))
    os.environ["FORGE_SCENES_DIR"] = str(root / "scenes")
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")
    live_view.reset()
    api = FakeApi()
    live_view.bind(api)
    dispatch("", "scene_new", {"name": name}, actor="user")
    return api


def _b64(fmt: str, size=(64, 36)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 120, 200)).save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_path_matches_the_panel() -> None:
    # ui/src/shots.js asserts the same numbers (tests/shots.test.mjs).
    two = [{"t": 0, "pos": [0, 0, 0], "target": [0, 0, 0], "fov": 30}, {"t": 2, "pos": [100, 0, 0], "target": [0, 0, 0], "fov": 60}]
    assert eval_path(two, 1)["pos"][0] == 50 and eval_path(two, 1)["fov"] == 45
    assert eval_path(two, 0.5)["pos"][0] == 20.3125
    assert abs(eval_path(two, 0.5, "in_out")["pos"][0] - 11.0931396484375) < 1e-9
    assert eval_path(two, 5)["pos"] == [100, 0, 0] and eval_path(two, -1)["pos"] == [0, 0, 0]
    three = two[:1] + [{"t": 1, "pos": [100, 0, 0], "target": [0, 0, 0], "fov": 30}, {"t": 2, "pos": [100, 100, 0], "target": [0, 0, 0], "fov": 30}]
    mid = eval_path(three, 0.5)["pos"]
    assert mid[0] == 50 and mid[1] == -6.25, mid
    assert eval_path(three, 1)["pos"] == [100, 0, 0]
    assert ease(0.5, "in_out") == 0.5 and ease(2, "in") == 1 and ease(-1, "out") == 0


def test_cameras_and_shots_are_scene_edits() -> None:
    _fresh("Film")
    cam = dispatch("Film", "camera", {"action": "add", "camera_name": "Wide", "pos": [0, -900, 300], "target": [0, 0, 100], "fov": 40}, actor="user")["camera"]
    assert cam["name"] == "Wide" and cam["fov"] == 40
    again = dispatch("Film", "camera", {"action": "add", "camera_name": "Wide", "pos": [1, 2, 3], "target": [0, 0, 0]}, actor="user")["camera"]
    assert again["name"] == "Wide 2"
    shot = dispatch("Film", "shot", {"action": "add", "shot_name": "Orbit", "duration": 3}, actor="user")["shot"]
    for t, x in ((0, -500), (3, 500)):
        dispatch("Film", "shot", {"action": "key", "shot": "orbit", "t": t, "pos": [x, -900, 300], "target": [0, 0, 0]}, actor="user")
    dispatch("Film", "shot", {"action": "key", "shot": shot["id"], "t": 3, "pos": [600, -900, 300], "target": [0, 0, 0]}, actor="user")
    got = dispatch("Film", "cameras", {})
    keys = got["shots"][0]["keys"]
    assert [k["t"] for k in keys] == [0, 3] and keys[1]["pos"][0] == 600, keys  # a key at the same time replaces it
    assert [c["name"] for c in got["cameras"]] == ["Wide", "Wide 2"]
    rev = got["rev"]
    assert dispatch("Film", "cameras", {})["rev"] == rev  # reading is not an edit
    dispatch("Film", "undo", {}, actor="user")
    assert dispatch("Film", "cameras", {})["shots"][0]["keys"][1]["pos"][0] == 500  # undo takes a key back
    for bad in ({"action": "key", "shot": "orbit", "t": -1}, {"action": "edit", "shot": "orbit", "ease": "wobble"}, {"action": "edit", "shot": "nope"}):
        try:
            dispatch("Film", "shot", bad, actor="user")
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    try:
        dispatch("Film", "camera", {"action": "add", "pos": [0, 0], "target": [0, 0, 0]}, actor="user")
        raise AssertionError("accepted a 2D position")
    except ValueError:
        pass
    dispatch("Film", "camera", {"action": "delete", "camera": "Wide 2"}, actor="user")
    assert [c["name"] for c in dispatch("Film", "cameras", {})["cameras"]] == ["Wide"]


def test_ducky_camera_flyer_and_people() -> None:
    api = _fresh("Yard")
    tower = dispatch("Yard", "add", {"kind": "box", "name": "Tower", "params": {"w": 100, "d": 100, "h": 400}}, actor="agent")["id"]
    out = live_view.camera("Yard", "frame", ids=[tower])
    pos, target = out["camera"]["pos"], out["camera"]["target"]
    assert abs(target[2] - 200) < 1 and pos[2] > target[2], out  # looks at the middle of the tower, from above
    rec = api.live.values["ai:agent"]
    assert rec["name"] == "AI" and rec["scene"] == "Yard" and rec["v"] == 1 and len(rec["path"]) == 2
    orbit = live_view.camera("Yard", "orbit", ids=[tower], seconds=6)
    assert orbit["seconds"] == 6 and len(api.live.values["ai:agent"]["path"]) == 13
    live_view.camera("Yard", "goto", pos=[0, -800, 300], target=[0, 0, 200], seconds=0, drive=True)
    rec = api.live.values["ai:agent"]
    assert rec["pos"] == [0, -800, 300] and rec["drive"] is True and "path" not in rec

    polled = live_view.live_poll(scene="Yard", me={"pos": [10, 20, 30], "target": [0, 0, 0], "fov": 50})
    assert [a["key"] for a in polled["ai"]] == ["ai:agent"] and polled["view"] is None and polled["job"] is None
    assert live_view.camera("Yard", "get")["viewport"]["fov"] == 50
    api.live.states = [{"from": {"name": "Ben", "device": "d2"}, "state": {"scene": "Yard", "pos": [1, 2, 3], "target": [0, 0, 0], "fov": 45}},
                       {"from": {"name": "Ana", "device": "d3"}, "state": {"scene": "Other", "pos": [1, 2, 3], "target": [0, 0, 0], "fov": 45}}]
    api.live.values["ai:far"] = {"key": "ai:far", "name": "Builder", "scene": "Yard", "pos": [5, 5, 5], "target": [0, 0, 0], "fov": 45}
    names = [p["name"] for p in live_view.camera("Yard", "people")["people"]]
    assert names == ["This PC's viewport", "Ben", "AI", "Builder"], names

    live_view.camera("Yard", "ping", target=[0, 0, 400])
    assert api.live.sent[-1][0] == "forge.ping" and live_view.live_poll(scene="Yard")["pings"][0]["by"] == "AI"
    saved = live_view.camera("Yard", "save", camera_name="Hero")
    assert saved["camera"]["pos"] == [0, -800, 300]
    assert live_view.resolve_pose("Yard", "Hero")["target"] == [0, 0, 200]
    assert live_view.resolve_pose("Yard", "viewport")["pos"] == [10, 20, 30]

    live_view.reset()
    live_view.note_activity("Yard", "Extruded roof", [tower])
    rec = api.live.values["ai:agent"]
    assert rec["doing"] == "Extruded roof" and rec["target"][2] > 100, rec  # an editing ducky shows up at its work


def test_view_through_the_panel() -> None:
    api = _fresh("Snap")
    closed = live_view.view("Snap", {"pos": [0, -500, 200], "target": [0, 0, 0], "fov": 45})
    assert closed["ok"] is False and "Forge tab" in closed["error"]
    api.visible = True

    def panel() -> None:
        for _ in range(200):
            req = live_view.live_poll(scene="Snap")["view"]
            if req:
                assert req["width"] == 640 and req["height"] == 360 and req["clean"] is True
                live_view.view_put(id=req["id"], b64="data:image/png;base64," + _b64("PNG"))
                return
            time.sleep(0.01)

    threading.Thread(target=panel, daemon=True).start()
    got = live_view.view("Snap", {"pos": [0, -500, 200], "target": [0, 0, 0], "fov": 45}, 641, 360, clean=True)
    assert got["ok"] and Path(got["paths"][0]).read_bytes().startswith(b"\x89PNG"), got
    try:
        live_view.view("Snap", "ai", 100, 100)
        raise AssertionError("accepted a 100 px picture")
    except ValueError:
        pass


def test_shot_render_frames_to_mp4() -> None:
    api = _fresh("Reel")
    closed = live_view.render_shot("Reel", "Shot")
    assert closed["ok"] is False and "Forge tab" in closed["error"]
    dispatch("Reel", "shot", {"action": "add", "shot_name": "Push", "duration": 0.1}, actor="user")
    try:
        live_view.start_render("Reel", "Push")
        raise AssertionError("rendered a shot with no keys")
    except ValueError as exc:
        assert "no keys" in str(exc)
    for t, y in ((0, -900), (0.1, -800)):
        dispatch("Reel", "shot", {"action": "key", "shot": "Push", "t": t, "pos": [0, y, 300], "target": [0, 0, 0]}, actor="user")
    job = live_view.start_render("Reel", "Push", fps=30, width=320, height=180, imk_project="promo")
    assert job["frames"] == 3
    handed = live_view.live_poll(scene="Reel")["job"]
    assert handed["frames"] == [0, 1, 2] and handed["total"] == 3 and handed["ease"] == "in_out"
    assert live_view.live_poll(scene="Reel")["job"] is None  # handed out: not again until FRAME_RETRY_S
    assert live_view.frame_put(job=job["id"], index=0, b64=_b64("PNG"))["ok"] is False  # frames are JPEG
    for i in handed["frames"]:
        live_view.frame_put(job=job["id"], index=i, b64=_b64("JPEG", (320, 180)))
    assert job["done"].wait(120), "encode never finished"
    out = live_view.job_result(job)
    if out["state"] == "failed":
        assert "ffmpeg" in out["error"], out  # this PC has no ffmpeg: the error says so
        return
    assert out["state"] == "done" and Path(out["video"]).stat().st_size > 0, out
    assert out["seconds"] == 0.1 and out["island_media_kit"]["ok"] is False and api.tools[0][0] == "imk_media_link"
    assert not job["folder"].exists()  # the frames are cleaned up


def run() -> None:
    test_path_matches_the_panel()
    test_cameras_and_shots_are_scene_edits()
    test_ducky_camera_flyer_and_people()
    test_view_through_the_panel()
    test_shot_render_frames_to_mp4()
    live_view.reset()
    live_view.bind(None)


if __name__ == "__main__":
    run()
    print("forge live self-check ok")
