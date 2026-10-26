"""The Forge: Character folder template: shape, wiring between its workflows, and the Forge tools its prompts name."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE_TYPES = {
    "start.chat", "flow.input", "flow.output", "workflow.call", "llm.ask", "llm.extract", "llm.vision", "text.template",
    "flow.repeat", "flow.choose", "flow.branch", "logic.if", "image.generate", "image.edit", "image.remove_bg", "list.make",
    "pipeline.agent", "pipeline.finish", "notify.message", "tool.call", "uefn.open_project", "uefn.import", "mesh.rig", "input.text", "logic.expression",
}


def row():
    plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    return next(t for t in plugin["contributes"]["automations"]["templates"] if t["id"] == "forge-character")


def test_bundle_shape():
    r = row()
    assert r["kind"] == "bundle" and r["root"] == "Forge Character"
    names = [w["name"] for w in r["workflows"]]
    assert names == ["Character", "Spec", "Concept sheet", "Make 3D", "Rig", "Gear", "Ship to UEFN"]
    assert len({w["key"] for w in r["workflows"]}) == 7
    assert {w["folder"] for w in r["workflows"]} <= {""} | set(r["folders"])
    assert "category" in r and r["kind"] == "bundle"


def test_main_path_is_forge_only_and_ship_is_conditional():
    g = graph("main")
    calls = [n["label"] for n in g["nodes"] if n["type"] == "workflow.call"]
    assert calls == ["Spec", "Make 3D", "Rig", "Gear", "Ship to UEFN"]
    assert not [n for n in g["nodes"] if n["type"].startswith(("image.", "mesh."))]
    nxt = {(e["source"], e["kind"]): e["target"] for e in g["edges"] if e["kind"] != "data"}
    assert nxt[("gear", "main")] == "go" and nxt[("go", "true")] == "ship" and nxt[("go", "false")] == "fin"


def test_vision_checks_get_render_paths_and_notifies_have_messages():
    for key in ("make3d", "rig"):
        g = graph(key)
        wires = {(e["source"], e["target"]) for e in g["edges"] if e["kind"] == "data"}
        assert ("agent", "rp") in wires and ("rp", "vis") in wires, key
        agent = next(n for n in g["nodes"] if n["id"] == "agent")
        assert "RENDERS:" in agent["config"]["prompt"]
    for w in row()["workflows"]:
        for n in w["graph"]["nodes"]:
            if n["type"] == "notify.message":
                assert n["config"].get("inputs", {}).get("message"), (w["name"], n["id"])


def test_no_background_removal_anywhere():
    assert "image.remove_bg" not in json.dumps(row())


def test_calls_and_edges_resolve():
    r = row()
    keys = {w["key"] for w in r["workflows"]}
    for w in r["workflows"]:
        ids = {n["id"] for n in w["graph"]["nodes"]}
        assert len(ids) == len(w["graph"]["nodes"]), w["name"]
        for n in w["graph"]["nodes"]:
            assert n["type"] in NODE_TYPES, (w["name"], n["type"])
            if n["type"] == "workflow.call":
                assert n["config"]["workflow_id"].lstrip("@") in keys
        for e in w["graph"]["edges"]:
            assert e["source"] in ids and e["target"] in ids, (w["name"], e)


TRIPO = ROOT.parent / "uefn-plugin-tripo" / "plugin.json"
CORE = ROOT.parent.parent / "UEFN-Ducky-Release" / "ducky_app"


def test_spec_and_sheet_are_tripos_unchanged():
    if not TRIPO.exists():
        return pytest.skip("tripo plugin not checked out")
    tripo = json.loads(TRIPO.read_text(encoding="utf-8"))
    rows = next(t for t in tripo["contributes"]["automations"]["templates"] if t["id"] == "tripo-character")["bundle"]["workflows"]
    mine = {w["key"]: w for w in row()["workflows"]}
    for w in rows:
        if w["key"] in ("spec", "sheet"):
            assert mine[w["key"]] == w, w["key"]


CORE_CHECK = """
import json, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])  # core's own `backend` package, not this plugin's
from backend.automations import store, templates
from backend.uefn_plugins import host, store as pstore
tmp = Path(tempfile.mkdtemp())
(tmp / "workflows").mkdir()
parsed = host._automation_template_row(json.loads(sys.stdin.read()), "forge")
with mock.patch.object(store, "use_db", lambda *_: False), mock.patch.object(store, "_files_dir", lambda: tmp / "workflows"), \\
     mock.patch.object(store, "_announce_graphs_changed", lambda: None), \\
     mock.patch.object(templates, "_dir", lambda *, for_write=False: tmp), \\
     mock.patch.object(templates, "_announce_templates_changed", lambda: None), \\
     mock.patch.object(host, "get_ui_contributions", lambda: {"automations_templates": [parsed]}), \\
     mock.patch.object(pstore, "get_enabled_plugin_ids", lambda: ["forge", "meshy", "studio3d", "uefn"]):
    listed = next(t for t in templates.list_templates() if t["id"].endswith("forge-character"))
    tid = listed["id"]
    need = set(listed.get("requires_plugins") or [])
    assert not need & {"meshy", "studio3d"}, need
    out = templates.use_template(tid, "local", "")
    assert out["shape"] == "bundle" and len(store.list_workflows()) == 7, out
    main = store.get_workflow(out["main"])
    called = {n["config"]["workflow_id"] for n in main["graph"]["nodes"] if n["type"] == "workflow.call"}
    assert len(called) == 5 and all(store.get_workflow(i) for i in called)
    from backend.automations.catalog import node_specs
    from backend.automations.pins import check_wires
    from backend.automations.runner import _signature
    for wf in store.list_workflows():
        bad = check_wires(store.get_workflow(wf["id"])["graph"], node_specs(), _signature)
        assert bad == [], (wf["name"], bad)
print("OK")
"""


def test_installs_and_saves_through_core():
    if not CORE.exists():
        return pytest.skip("core app not checked out")
    done = subprocess.run([sys.executable, "-c", CORE_CHECK, str(CORE)], input=json.dumps(row()), text=True,
                          capture_output=True, cwd=str(CORE), timeout=120)
    assert done.returncode == 0 and "OK" in done.stdout, done.stderr[-2000:]


def graph(key):
    return next(w for w in row()["workflows"] if w["key"] == key)["graph"]


def test_retarget_runs_after_send_and_only_for_humanoids():
    g = graph("ship")
    tools = {n["id"]: n["config"]["name"] for n in g["nodes"] if n["type"] == "tool.call"}
    chain = ["send", "rtb", "hum", "pick", "ikt", "iks", "ikr", "preset", "map", "bake", "face", "npc"]
    assert [tools[i] for i in ("ikt", "iks", "ikr", "preset", "map", "bake")] == [
        "create_ik_rig_asset", "create_ik_rig_asset", "create_ik_retargeter_asset", "get_retarget_preset",
        "auto_map_retarget_chains", "retarget_animation"]
    nxt = {(e["source"], e["kind"]): e["target"] for e in g["edges"] if e["kind"] != "data"}
    for a, b in zip(chain, chain[1:]):
        assert nxt.get((a, "main"), nxt.get((a, "true"))) == b, (a, b)
    nodes = {n["id"]: n["config"] for n in g["nodes"]}
    assert nxt[("rtb", "false")] == "npc" and nxt[("hum", "false")] == "npc"  # nothing to retarget / quadruped: skipped
    assert nodes["hum"]["equals"] == "humanoid" and nodes["rtb"]["field"] == "retarget"


def test_rig_generates_only_idle_and_walk_and_returns_retarget():
    g = graph("rig")
    prompt = next(n for n in g["nodes"] if n["id"] == "agent")["config"]["prompt"]
    assert "ONLY for idle and walk" in prompt and "RETARGET:" in prompt
    names = [o["name"] for n in g["nodes"] if n["type"] == "flow.output" for o in n["config"]["outputs"]]
    assert names.count("retarget") == 2


def test_prompts_name_only_real_forge_tools():
    tools = set(re.findall(r"def (forge_\w+)\(", (ROOT / "backend" / "tools.py").read_text(encoding="utf-8")))
    tools |= set(re.findall(r'"(forge_\w+)":', (ROOT / "backend" / "scene.py").read_text(encoding="utf-8")))
    text = json.dumps(row())
    named = set(re.findall(r"\bforge_\w+", text))
    assert {"forge_rig", "forge_anim", "forge_send_to_uefn", "forge_export", "forge_render", "forge_check"} <= named
    assert named <= tools, named - tools
    assert not (set(re.findall(r"\bblender_\w+", text)))
