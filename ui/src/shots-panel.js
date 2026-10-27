// The Shots tab: saved cameras, keyed camera moves, preview, and Render MP4.
import { EASES, evalPath, shotDuration } from "./shots.js";

const SIZES = [["1920x1080", "1920×1080"], ["1280x720", "1280×720"], ["1080x1920", "1080×1920 vertical"], ["3840x2160", "3840×2160"]];
const FPS = [24, 25, 30, 50, 60];
const EASE_LABEL = { linear: "Linear", in_out: "Ease in-out", in: "Ease in", out: "Ease out" };

/** ctx: { rpc, call, op, state, presence, esc, repaint, setStatus } */
export function createShotsPanel(ctx) {
  const { esc } = ctx;
  let data = { cameras: [], shots: [] }, seen = "", fetchedRev = null, fetchedScene = "", fetching = false, lastFetch = 0;
  let shotId = "", scrub = 0, size = "1920x1080", fps = 30;

  const shot = () => data.shots.find(s => s.id === shotId) || null;

  async function refresh(now) {
    const name = ctx.state.name;
    if (fetching || !name || (ctx.state.rev === fetchedRev && name === fetchedScene) || now - lastFetch < 400) return;
    fetching = true;
    lastFetch = now;
    const rev = ctx.state.rev;
    try {
      const out = await ctx.rpc("forge_call", { op: "cameras", name, params: {}, actor: "user" });
      if (!out || out.ok === false || name !== ctx.state.name) return;
      fetchedRev = rev;
      fetchedScene = name;
      const next = { cameras: out.cameras || [], shots: out.shots || [] };
      const text = JSON.stringify(next);
      if (text === seen) return;
      seen = text;
      data = next;
      if (!shot()) shotId = data.shots[0]?.id || "";
      ctx.presence.setCameras(data.cameras);
      if (ctx.state.tab === "shots") ctx.repaint();
    } catch { /* the next tick asks again */ } finally {
      fetching = false;
    }
  }

  function rendersHtml(list) {
    return (list || []).slice(-3).map(r => {
      const text = r.state === "done" ? `${esc(r.shot)}: saved <code>${esc(r.path)}</code>`
        : r.state === "failed" ? `${esc(r.shot)}: ${esc(r.error)}`
          : r.state === "encoding" ? `${esc(r.shot)}: encoding the MP4…`
            : `${esc(r.shot)}: drawing frame ${r.done} of ${r.frames}…`;
      return `<div class="${r.state === "failed" ? "err" : "dim"}">${text}</div>`;
    }).join("");
  }
  ctx.presence.onRenders = list => {
    const box = document.getElementById("shot-renders");
    if (box) box.innerHTML = rendersHtml(list);
  };

  function html() {
    const s = shot();
    const dur = s ? shotDuration(s) : 0;
    scrub = Math.min(scrub, dur);
    const cams = data.cameras.map(c => `<div class="row cam-row"><span class="grow">🎥 ${esc(c.name)}</span>
      <button type="button" data-cam-look="${esc(c.id)}" title="Look through this camera">Look</button>
      <button type="button" data-cam-move="${esc(c.id)}" title="Move this camera to your view">To view</button>
      <button type="button" data-cam-del="${esc(c.id)}" title="Delete camera" aria-label="Delete camera ${esc(c.name)}">×</button></div>`).join("");
    const keys = s ? s.keys.map(k => `<span class="key-chip"><button type="button" data-key-t="${k.t}" title="Go to this key">${k.t.toFixed(2)} s</button><button type="button" data-unkey="${k.t}" aria-label="Remove the key at ${k.t} s">×</button></span>`).join("") : "";
    return `<div class="shots">
      <h4>Cameras</h4>
      ${cams || `<p class="dim">No saved cameras yet.</p>`}
      <div class="row"><button id="cam-add" type="button" title="Save where you are looking as a camera">+ Camera from view</button></div>
      <h4>Shots</h4>
      <div class="row">
        <select id="shot-pick" aria-label="Shot">${data.shots.map(x => `<option value="${esc(x.id)}"${x.id === shotId ? " selected" : ""}>${esc(x.name)}</option>`).join("") || `<option value="">No shots</option>`}</select>
        <button id="shot-add" type="button">+ Shot</button>
        ${s ? `<button id="shot-del" type="button" aria-label="Delete shot">×</button>` : ""}
      </div>
      ${s ? `
      <div class="row">
        <label class="dim">Length <input id="shot-dur" type="number" min="0.1" step="0.5" value="${dur}" aria-label="Shot length in seconds" /> s</label>
        <select id="shot-ease" aria-label="Ease">${EASES.map(e => `<option value="${e}"${e === s.ease ? " selected" : ""}>${EASE_LABEL[e]}</option>`).join("")}</select>
      </div>
      <div class="row"><input id="shot-scrub" class="grow" type="range" min="0" max="${dur}" step="0.01" value="${scrub}" aria-label="Time in the shot" /><span id="shot-t" class="dim">${scrub.toFixed(2)} s</span></div>
      <div class="row">
        <button id="shot-key" type="button" title="Key the camera where you are looking, at this time">Key view</button>
        <button id="shot-play" type="button"${s.keys.length ? "" : " disabled"}>▶ Play</button>
      </div>
      <div class="keys">${keys || `<span class="dim">No keys: fly to a spot and press Key view, move the time, key again.</span>`}</div>
      <h4>Render</h4>
      <div class="row">
        <select id="shot-size" aria-label="Video size">${SIZES.map(([v, l]) => `<option value="${v}"${v === size ? " selected" : ""}>${l}</option>`).join("")}</select>
        <select id="shot-fps" aria-label="Frames per second">${FPS.map(f => `<option value="${f}"${f === fps ? " selected" : ""}>${f} fps</option>`).join("")}</select>
        <button id="shot-render" type="button"${s.keys.length ? "" : " disabled"}>Render MP4</button>
      </div>` : `<p class="dim">A shot is a camera move: key the camera at a few times and Forge glides between them.</p>`}
      <div id="shot-renders">${rendersHtml(ctx.presence.renders)}</div>
    </div>`;
  }

  function show(t) {
    const s = shot();
    scrub = t;
    if (s?.keys.length) ctx.presence.glideTo(evalPath(s.keys, t, s.ease), 0.01);
    const label = document.getElementById("shot-t");
    if (label) label.textContent = `${t.toFixed(2)} s`;
  }

  function bind(panel) {
    const on = (sel, ev, fn) => panel.querySelectorAll(sel).forEach(el => el.addEventListener(ev, fn));
    const pose = () => ctx.presence.myPose();
    on("#cam-add", "click", () => ctx.op("camera", { action: "add", camera_name: "Camera", ...pose() }));
    on("[data-cam-look]", "click", ev => {
      const cam = data.cameras.find(c => c.id === ev.currentTarget.dataset.camLook);
      if (cam) ctx.presence.glideTo(cam, 0.6);
    });
    on("[data-cam-move]", "click", ev => ctx.op("camera", { action: "edit", camera: ev.currentTarget.dataset.camMove, ...pose() }));
    on("[data-cam-del]", "click", ev => ctx.op("camera", { action: "delete", camera: ev.currentTarget.dataset.camDel }));
    on("#shot-pick", "change", ev => { shotId = ev.target.value; scrub = 0; ctx.repaint(); });
    on("#shot-add", "click", async () => {
      const made = await ctx.op("shot", { action: "add", shot_name: "Shot", duration: 4 });
      if (made?.shot?.id) { shotId = made.shot.id; scrub = 0; }
    });
    on("#shot-del", "click", () => ctx.op("shot", { action: "delete", shot: shotId }));
    on("#shot-dur", "change", ev => ctx.op("shot", { action: "edit", shot: shotId, duration: Math.max(0.1, Number(ev.target.value) || 4) }));
    on("#shot-ease", "change", ev => ctx.op("shot", { action: "edit", shot: shotId, ease: ev.target.value }));
    on("#shot-scrub", "input", ev => show(Number(ev.target.value) || 0));
    on("#shot-key", "click", () => ctx.op("shot", { action: "key", shot: shotId, t: scrub, ...pose() }));
    on("#shot-play", "click", () => { const s = shot(); if (s?.keys.length) ctx.presence.play(s.keys, s.ease); });
    on("[data-key-t]", "click", ev => show(Number(ev.currentTarget.dataset.keyT) || 0));
    on("[data-unkey]", "click", ev => ctx.op("shot", { action: "unkey", shot: shotId, t: Number(ev.currentTarget.dataset.unkey) }));
    on("#shot-size", "change", ev => { size = ev.target.value; });
    on("#shot-fps", "change", ev => { fps = Number(ev.target.value) || 30; });
    on("#shot-render", "click", async () => {
      const [width, height] = size.split("x").map(Number);
      const out = await ctx.call("plugin.call", { method: "forge_shot_render_start", params: { name: ctx.state.name, shot: shotId, fps, width, height } })
        .catch(err => ({ ok: false, error: err.message }));
      ctx.setStatus(out?.ok === false ? out.error : `Rendering ${out.frames} frames: keep the Forge tab open`, out?.ok === false);
    });
  }

  return { tick: refresh, html, bind };
}
