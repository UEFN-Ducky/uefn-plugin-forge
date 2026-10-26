"""The Forge: Character folder template: shape, wiring between its workflows, and the Forge tools its prompts name."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_TYPES = {
    "start.chat", "flow.input", "flow.output", "workflow.call", "llm.ask", "llm.extract", "llm.vision", "text.template",
    "flow.repeat", "flow.choose", "flow.branch", "logic.if", "image.generate", "image.edit", "image.remove_bg", "list.make",
    "pipeline.agent", "pipeline.finish", "notify.message", "tool.call", "uefn.open_project", "uefn.import", "mesh.rig",
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
