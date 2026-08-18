"use strict";
const $ = (id) => document.getElementById(id);
const show = (el, on) => el.classList.toggle("hidden", !on);
let attachedImages = [];   // {name,type,data_url,size} staged image attachments (sent to the model)
let runSoftDeadlineSec = 0;   // per-run soft deadline from /api/me (0 = off) -> drives the time-limit warning banner
let backupBlob = null, backupUrl = null, backupBuilding = null, reportUrl = null, modelUrl = null;   // in-browser copy of the unified zip (serves Download/View AND restores Continue)
let runWarnTimer = null;
let reasonLine = null;     // streaming line for the Model reasoning box
let DEMO = false;          // Demo build: examples only, brief locked, uploads off, hard time cap
let demoExamples = [];     // [{key,label,building}] from the server -- the ONLY runnable briefs
let demoContinued = false; // the Demo auto-continues ONCE at the soft deadline, then stops for good
let demoAutoResume = false; // soft-deadline resume in flight -- finishRun must NOT end the demo (race fix 2026-08-16)
let demoEnded = false;     // hard time cap hit -> no further runs this session
let currentBuilding = "";  // the job this tab is working on (DEMO hides #building, so read this)

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  return r;
}
async function jpost(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                     body: JSON.stringify(body || {}) });
}
// The job name for /api/stop, /api/download, restore etc. DEMO hides the project-name input, so
// fall back to whatever startRun last launched.
function curBuilding() {
  if (DEMO) return currentBuilding || "";
  return ($("building").value || currentBuilding || "Project").trim();
}

// ---------------- bootstrap ----------------
let turnstileToken = "";
let turnstileSiteKey = "";

function mountTurnstile() {
  // No site key configured -> no widget, and the server lets everyone through (fail-open by design,
  // see app/turnstile.py). The demo must never be un-enterable because a key isn't provisioned.
  if (!turnstileSiteKey || !$("turnstileBox")) { $("signinBtn").disabled = !_acksOk(); return; }
  const s = document.createElement("script");
  s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
  s.async = true; s.defer = true;
  s.onload = () => {
    try {
      window.turnstile.render("#turnstileBox", {
        sitekey: turnstileSiteKey,
        callback: (t) => { turnstileToken = t; $("signinBtn").disabled = !_acksOk(); },
        "expired-callback": () => { turnstileToken = ""; $("signinBtn").disabled = true; },
      });
    } catch (_) { turnstileToken = ""; }
  };
  s.onerror = () => { turnstileToken = ""; };   // Cloudflare unreachable -> server also fails open
  document.head.appendChild(s);
}

async function boot() {
  let cfg = {};
  try { cfg = await (await api("/api/config")).json(); } catch (_) {}
  DEMO = !!cfg.demo;
  turnstileSiteKey = cfg.turnstile_site_key || "";
  if (DEMO) document.body.classList.add("demo");

  let me = {};
  try { me = await (await api("/api/me")).json(); } catch (_) {}
  if (me.executor) $("execBadge").textContent = "sandbox: " + me.executor;
  if (me.user) { enterApp(me); return; }
  // Legacy landing-page links still carry #promo=CODE. Promo codes are gone (open access now), so
  // just strip it and show the door rather than confusing the visitor with a failed redemption.
  if ((location.hash || "").indexOf("promo=") !== -1) {
    history.replaceState(null, "", location.pathname + location.search);
  }
  show($("login"), true); show($("app"), false);
  mountTurnstile();
}

function enterApp(me) {
  show($("login"), false);
  show($("app"), true);
  $("who").textContent = me.user ? "· " + me.user : "";
  if (me.has_creds) {
    $("model").value = me.model || "";
    $("baseUrl").value = me.base_url || "";
  } else {
    setSettings(true);  // first thing: ask for the LLM connection
  }
  if (me.reasoning) $("reasoning").value = me.reasoning;
  if (me.max_tokens) $("maxTokens").value = me.max_tokens;
  if (me.provider) $("provider").value = me.provider;
  runSoftDeadlineSec = me.run_soft_deadline_sec || 0;
  if (me.demo) { DEMO = true; demoExamples = me.examples || []; applyDemoMode(); }
  applyConsent(me.consented);
}

// ---------------- demo mode ----------------
function _disable(el, tip) {
  if (!el) return;
  el.setAttribute("aria-disabled", "true");
  el.title = tip;
  el.style.opacity = ".45";
  el.style.cursor = "not-allowed";
  el.style.pointerEvents = "auto";              // keep hover so the tooltip still shows
  el.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); }, true);
}

function applyDemoMode() {
  document.body.classList.add("demo");

  // Uploads + restore are off: the Demo accepts no user files at all (the server refuses them too).
  _disable($("briefFileLabel"), "Disabled for Demo version");
  _disable($("restoreFileLabel"), "Disabled for Demo version");
  if ($("briefFile")) $("briefFile").disabled = true;
  if ($("restoreFile")) $("restoreFile").disabled = true;
  show($("restoreInfo"), false);

  // fidelity is pinned server-side to the preflight recommendation
  const frow = $("fidelity") && $("fidelity").closest(".row");
  if (frow) frow.style.display = "none";
  // The project name is the example's -- not the visitor's to choose.
  show($("buildingRow"), false);

  // Rebuild the example list from the server so the dropdown can never offer something /api/run
  // would reject, and make it the primary control.
  const sel = $("exampleSelect");
  if (sel && demoExamples.length) {
    sel.innerHTML = '<option value="" selected>Choose an example building…</option>' +
      demoExamples.map((e) => `<option value="${e.key}">${e.label}</option>`).join("");
  }

  // The brief is read-only. Explain why, in the box itself.
  const ta = $("brief");
  if (ta) {
    ta.readOnly = true;
    ta.value = DEMO_BLURB;
    ta.style.opacity = ".85";
  }
  $("runBtn").textContent = "Run this example";
  $("runBtn").disabled = true;                  // until an example is chosen
}

const DEMO_BLURB = [
  "This is the free Demo of Steltic — the Open Source steel design assistant.",
  "",
  "Choose one of the 25 example buildings above and click \"Run this example\". Steltic CFS will drive",
  "your LLM through the full AISI S100/S240/S400 design: build the model, run the analysis, design",
  "the members and connections, check lateral stability, and write a stamped-style report. When it",
  "finishes, download the package — report, 3D model and the OpenSees model files.",
  "",
  "Demo limits",
  "  • You can run one example, once. The brief cannot be edited and the design cannot be continued",
  "    with your own changes afterwards.",
  "  • Uploads are disabled — you cannot enter your own building data, so none of your data is ever",
  "    on our servers.",
  "  • Up to 2 hours per design. Slower or more verbose models need the time; Steltic continues",
  "    automatically once, in the background, so you don't have to do anything.",
  "  • You bring your own LLM API key. It lives in server memory only and is never written to disk.",
  "",
  "Want no limits? Steltic is open source and runs locally on your own machine, with your own",
  "buildings and no time caps: github.com/Steltic/steltic_cfs",
].join("\n");

function applyConsent(consented) {
  // consent is accepted at sign-in and stored in the signed session cookie, so it is instance-independent on Cloud Run
  // In DEMO the button additionally needs an example chosen, so never enable it here.
  $("runBtn").disabled = !consented || (DEMO && !($("exampleSelect").value || "").trim());
  $("runStatus").textContent = consented ? (DEMO ? "choose an example building" : "")
                                         : "please sign out and sign back in, ticking the Terms box";
}

// ---------------- login ----------------
function _acksOk() { return $("ackSafety").checked && $("ackAccept").checked; }
["ackSafety", "ackAccept"].forEach((id) => $(id).addEventListener("change", () => { $("signinBtn").disabled = !_acksOk(); }));
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("loginErr").textContent = "";
  if (!_acksOk()) { $("loginErr").textContent = "Please tick both acknowledgements to continue."; return; }
  if (turnstileSiteKey && !turnstileToken) {
    $("loginErr").textContent = "Please complete the verification check above.";
    return;
  }
  const r = await jpost("/api/enter", { turnstile: turnstileToken, consented: true });
  if (r.ok) { const me = await (await api("/api/me")).json(); enterApp(me); }
  else {
    $("loginErr").textContent = (await r.json().catch(() => ({}))).detail || "could not start the demo";
    turnstileToken = "";
    try { window.turnstile && window.turnstile.reset(); } catch (_) {}
  }
});

$("logoutBtn").addEventListener("click", async () => { await jpost("/api/logout"); location.reload(); });

// ---------------- keep-alive heartbeat (Cloud Run scale-to-zero mitigation) ----------------
// Cloud Run has no idle-timeout setting; we keep THIS user's (session-affinity) instance warm by pinging
// while they're active, so per-session job files survive the gap between a run finishing and a
// download/Continue. The timer lives in a Web Worker so it KEEPS FIRING when the app tab is backgrounded
// (e.g. the user switched to the report tab to read) -- a main-thread setInterval gets throttled/frozen
// there, which is exactly when we most need to stay warm. We SKIP pinging during a run (the SSE stream
// already keeps us warm, and a ping then could spawn a 2nd instance under concurrency=1), and we stop
// after a spell of inactivity so a tab left open all day doesn't keep an instance (and billing) alive.
const HEARTBEAT_MS = 45000, HEARTBEAT_IDLE_MS = 55 * 60 * 1000;   // testing: keep warm ~1h of idle (drop back to ~20min for prod)
let lastActivity = Date.now();
function markActivity() { lastActivity = Date.now(); }
["click", "keydown", "mousemove", "scroll"].forEach((ev) => document.addEventListener(ev, markActivity, { passive: true }));
function pingNow() {
  try { fetch("/api/ping", { cache: "no-store", keepalive: true, credentials: "same-origin" }).catch(() => {}); } catch (_) {}
}
function heartbeatTick() {
  if ($("app").classList.contains("hidden") || runController) return;   // not signed in, or a run keeps us warm
  if (Date.now() - lastActivity > HEARTBEAT_IDLE_MS) return;            // idle too long -> allow scale-to-zero
  pingNow();
}
(function startHeartbeat() {
  try {
    const src = "let t=null;onmessage=function(e){var ms=(e.data&&e.data.ms)||45000;if(t)clearInterval(t);" +
                "t=setInterval(function(){postMessage('tick')},ms)};";
    const w = new Worker(URL.createObjectURL(new Blob([src], { type: "application/javascript" })));
    w.onmessage = heartbeatTick;
    w.postMessage({ ms: HEARTBEAT_MS });
  } catch (_) {
    setInterval(heartbeatTick, HEARTBEAT_MS);   // fallback for environments without Web Workers
  }
})();
// Re-warm the instant the user returns to the tab (switching back from the report to type a follow-up),
// and send one last ping as the tab goes to the background so the idle gap starts warm.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") { markActivity(); pingNow(); }
  else if (!runController && !$("app").classList.contains("hidden")) pingNow();
});

// ---------------- session-timeout countdown (mirrors the keep-alive idle window) ----------------
function _mmss(ms) { ms = Math.max(0, ms); const s = Math.round(ms / 1000); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
function updateSessionNote() {
  const note = $("sessionNote"); if (!note) return;
  if ($("app").classList.contains("hidden")) { show(note, false); return; }
  show(note, true);
  const lead = $("sessionLead"); if (!lead) return;
  if (runController) { lead.textContent = "Your session stays active while your design runs."; return; }   // SSE keeps the instance warm
  lead.innerHTML = "Your session times out in <b id=\"sessionCountdown\">" + _mmss(HEARTBEAT_IDLE_MS - (Date.now() - lastActivity)) + "</b>.";
}
setInterval(updateSessionNote, 1000);
function setSettings(on) { show($("settings"), on); $("settingsBtn").classList.toggle("active", on); }
$("settingsBtn").addEventListener("click", () => setSettings($("settings").classList.contains("hidden")));

// ---------------- creds ----------------
$("saveCreds").addEventListener("click", async () => {
  const body = { base_url: $("baseUrl").value, api_key: $("apiKey").value, model: $("model").value,
                 reasoning: $("reasoning").value, max_tokens: parseInt($("maxTokens").value) || 32000,
                 provider: $("provider").value.trim() };
  const r = await jpost("/api/creds", body);
  const msg = $("credsMsg");
  if (r.ok) { msg.textContent = "saved ✓"; setTimeout(() => setSettings(false), 600); }
  else { msg.textContent = (await r.json().catch(() => ({}))).detail || "error"; }
});

// ---------------- run ----------------
const logEl = () => $("log");
function logLine(cls, text) {
  const d = document.createElement("div");
  if (cls) d.className = cls;
  d.textContent = text;
  logEl().appendChild(d);
  logEl().scrollTop = logEl().scrollHeight;
  return d;
}
let tokenLine = null;
// small looping mark shown inline (≈1 line tall) at the end of whichever line is actively streaming
function _gifFor(lineEl) {
  let g = lineEl.querySelector(".streamgif");
  if (!g) { g = document.createElement("img"); g.className = "streamgif"; g.src = "/static/Steltic-mark-loop.gif"; g.alt = ""; }
  lineEl.appendChild(g);   // (re)place at the end, after the latest text
}
function appendToken(t) {
  if (!tokenLine) { tokenLine = logLine("tok", ""); tokenLine._txt = document.createElement("span"); tokenLine.appendChild(tokenLine._txt); }
  tokenLine._txt.textContent += t;
  _gifFor(tokenLine);
  logEl().scrollTop = logEl().scrollHeight;
}
const reasonEl = () => $("reason");
const REASON_CAP = 120000;   // chars; drop the oldest reasoning when the box grows past this (keeps the DOM light + scrolling)
function appendReason(t) {
  const el = reasonEl();
  if (!reasonLine) { reasonLine = document.createElement("div"); reasonLine.className = "rtok"; reasonLine._txt = document.createElement("span"); reasonLine.appendChild(reasonLine._txt); el.appendChild(reasonLine); }
  reasonLine._txt.textContent += t;
  _gifFor(reasonLine);
  while (el.textContent.length > REASON_CAP && el.firstChild && el.firstChild !== reasonLine) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}
const fmtMs = (ms) => ms < 1000 ? ms + "ms" : (ms / 1000).toFixed(1) + "s";

// ---------------- agent activity lights ----------------
function setLights(keys) {
  const on = new Set(keys || []);
  document.querySelectorAll("#activity .light").forEach((el) => el.classList.toggle("on", on.has(el.dataset.k)));
}
function updateLights(ev) {
  switch (ev.type) {
    case "token": case "reasoning": setLights(["thinking"]); break;
    case "tool": {
      const n = ev.name;
      if (n === "run_python") {
        const k = ["python"];
        if (/opensees|ops\.|pipeline|design|report|preview|build|analyz|eigen|recorder|modal/i.test((ev.code || "") + " " + (ev.title || ""))) k.push("opensees");
        setLights(k);
      } else if (n === "search_engineering_standards") { setLights(["rag"]); }
      break;
    }
    case "tool_result": setLights(["thinking"]); break;
    case "assistant": case "done": case "error": case "paused": setLights([]); break;
  }
}

function handleEvent(ev, building) {
  if (ev.type !== "token") { if (tokenLine) { const g = tokenLine.querySelector(".streamgif"); if (g) g.remove(); } tokenLine = null; }
  if (ev.type !== "reasoning") { if (reasonLine) { const g = reasonLine.querySelector(".streamgif"); if (g) g.remove(); } reasonLine = null; }
  updateLights(ev);
  switch (ev.type) {
    case "status":      logLine("status", "· " + ev.text); break;
    case "token":       appendToken(ev.text); break;
    case "tool":        logLine("tool", `▶ step ${ev.step} · ${ev.title || ev.name}`); break;
    case "tool_result": logLine("res", "↳ " + ev.summary + (ev.ms ? "  (" + fmtMs(ev.ms) + ")" : "")); break;
    case "milestone":   logLine("milestone", "▸ " + ev.text); break;
    case "reasoning":   appendReason(ev.text); break;
    case "assistant":   logLine("assistant", ev.text); break;
    case "error":       logLine("err", "✖ " + ev.text); break;
    case "demo_limit": {
      // Fleet-wide 2 h cap hit. The server has already flipped the cancel flag, so the agent is
      // wrapping up and a final bundle is on its way -- just make sure we never auto-continue again.
      demoEnded = true; demoContinued = true; demoAutoResume = false;
      logLine("paused", "⏱ " + (ev.text || "Demo time limit reached."));
      $("runStatus").textContent = "demo time limit reached — download your package below";
      break;
    }
    case "paused": {
      logLine("paused", "⏸ paused — " + (ev.reason || "loop guard"));
      if (ev.detail) logLine("paused", "   " + ev.detail);
      if (stopWaitTimer) { clearTimeout(stopWaitTimer); stopWaitTimer = null; }
      // DEMO: the platform's 60-min request cap pauses long designs. The visitor is not here to
      // click Continue -- resume once, automatically, and let the server's fleet-wide clock decide
      // when 2 h is up. A pause for any OTHER reason (loop guard, stall) ends the demo run: there is
      // no human in the loop to give the agent the instruction it is asking for.
      if (DEMO) {
        const softDeadline = /deadline|time limit|soft/i.test((ev.reason || "") + (ev.detail || ""));
        if (softDeadline && !demoContinued && !demoEnded) {
          demoContinued = true;
          demoAutoResume = true;          // finishRun (stream end) races the timer below -- flag first
          logLine("paused", "↻ continuing automatically — up to one more hour, then the demo run ends");
          $("runStatus").textContent = "continuing automatically (final hour)…";
          setTimeout(() => startRun(true), 1200);
        } else {
          demoEnded = true;
          $("runStatus").textContent = "demo run finished — download your package below";
        }
        break;
      }
      $("runStatus").textContent = /stopped/.test(ev.reason || "")
        ? "stopped — your files arrived below (Download); click Continue to resume"
        : "paused — review the log, type a fix or instruction, then click Continue";
      show($("resumeBtn"), true);
      // the 'bundle' SSE event (arrives next) captures the paused state to the browser for Download + restore
      break;
    }
    case "usage": {
      const f = (n) => Number(n || 0).toLocaleString();
      $("tokensLast").textContent = `last call  input ${f(ev.last_in)} · output ${f(ev.last_out)}`;
      $("tokensCum").textContent = `cumulative  input ${f(ev.cum_in)} · output ${f(ev.cum_out)}`;
      show($("tokensSep"), true); show($("tokensInfoCtx"), true); show($("tokensInfo"), true);
      break;
    }
    case "done": {
      const a = $("reportLink");
      a.href = `/api/report/${encodeURIComponent(building)}/report.html`;
      show(a, true);
      $("modelLink").href = `/api/report/${encodeURIComponent(building)}/viewer_3d.html`;
      show($("modelLink"), true);
      $("downloadLink").href = `/api/download/${encodeURIComponent(building)}`;
      show($("downloadLink"), true);
      show($("downloadInfo"), true);
      // the 'bundle' SSE event (arrives next, on this same connection) replaces these with an in-browser copy
      $("runStatus").textContent = "done — type a change above and click Continue to iterate";
      $("brief").value = "";
      $("brief").placeholder = "Follow-up (continues THIS design with full context): e.g. \"make the exterior columns W24X146 and rerun\", \"pin the interior beam-to-column joints\", or \"run an optimisation pass to lighten the members\". Then click Continue.";
      $("briefFileName").textContent = "";
      attachedImages = []; renderFileList();
      break;
    }
    case "bundle": {
      // The run-instance shipped the whole design (report + resumable state) over THIS connection. Capture it
      // so Download / View / Continue all work from the browser copy, even if that instance is later gone.
      try {
        const bin = atob(ev.zip_b64 || "");
        const arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const blob = new Blob([arr], { type: "application/zip" });
        if (!blob.size) break;
        if (backupUrl) { try { URL.revokeObjectURL(backupUrl); } catch (_) {} }
        backupBlob = blob; backupBuilding = ev.building || building; backupUrl = URL.createObjectURL(blob);
        const dl = $("downloadLink");
        dl.href = backupUrl; dl.setAttribute("download", backupBuilding + "_design.zip"); show(dl, true); show($("downloadInfo"), true);
        const tw = $("timeWarnDownload"); if (tw) { tw.href = backupUrl; tw.setAttribute("download", backupBuilding + "_design.zip"); }
        const note = $("backupNote"); if (note) { note.textContent = "✓ saved to your browser — Download & View work even if the session resets"; show(note, true); }
        zipExtract(blob, "report.html").then((html) => {
          if (!html) return;
          if (reportUrl) { try { URL.revokeObjectURL(reportUrl); } catch (_) {} }
          reportUrl = URL.createObjectURL(html);
          const a = $("reportLink"); a.href = reportUrl; a.setAttribute("target", "_blank"); show(a, true);
        }).catch(() => {});
        blobifyViewer(blob);
      } catch (_) {}
      break;
    }
  }
}

function showTimeWarn() {
  const dl = $("timeWarnDownload");
  if (!$("downloadLink").classList.contains("hidden")) { dl.href = $("downloadLink").href; show(dl, true); }
  else show(dl, false);
  show($("timeWarn"), true);
}
function clearTimeWarn() {
  if (runWarnTimer) { clearTimeout(runWarnTimer); runWarnTimer = null; }
  show($("timeWarn"), false);
}

let runController = null;
function setRunning(on) {
  $("runBtn").disabled = on; $("resumeBtn").disabled = on;
  $("brand").classList.toggle("running", on);
  show($("stopBtn"), on); show($("stopInfo"), on);
  if (!on) setLights([]);
}

async function startRun(resume, _retry) {
  markActivity();
  let building, brief, exampleKey = null;
  if (DEMO) {
    if (resume) demoAutoResume = false;      // the scheduled auto-continue is now running
    if (demoEnded) { $("runStatus").textContent = "demo time limit reached — download your package"; return; }
    exampleKey = ($("exampleSelect").value || "").trim();
    if (!exampleKey) { $("runStatus").textContent = "choose an example building first"; return; }
    // The server picks the brief from the key; whatever is in the textarea is explainer text.
    const ex = demoExamples.find((e) => e.key === exampleKey);
    building = (ex && ex.building) || exampleKey;
    brief = "";
  } else {
    building = ($("building").value || "Project").trim();
    brief = $("brief").value.trim();
    if (!resume && !brief) { $("runStatus").textContent = "enter a brief first"; return; }
  }
  currentBuilding = building;
  setRunning(true);
  setLights(["thinking"]);
  $("runStatus").textContent = resume ? "resuming…" : "running…";
  show($("reportLink"), false); show($("downloadLink"), false); show($("downloadInfo"), false);
  if (!resume) { logEl().innerHTML = ""; reasonEl().innerHTML = ""; reasonLine = null; $("tokensLast").textContent = ""; $("tokensCum").textContent = ""; show($("tokensSep"), false); show($("tokensInfoCtx"), false); show($("tokensInfo"), false); }
  clearTimeWarn();
  if (runSoftDeadlineSec > 0) runWarnTimer = setTimeout(showTimeWarn, Math.max(5000, (runSoftDeadlineSec - 120) * 1000));
  tokenLine = null;
  runController = new AbortController();
  let snapB64 = null;   // on resume, carry the in-browser snapshot so the server can rehydrate /tmp in THIS request
  if (resume && backupBlob && backupBuilding === building) { try { snapB64 = await _blobToB64(backupBlob); } catch (_) {} }
  let r;
  try {
    const payload = DEMO
      ? { example: exampleKey, resume: !!resume, snapshot_b64: snapB64 }
      : { building, brief, analysis_fidelity: ($("fidelity") ? $("fidelity").value : "auto"), resume: !!resume,
          images: attachedImages.map(({ name, type, data_url }) => ({ name, type, data_url })),
          snapshot_b64: snapB64 };
    r = await api("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload), signal: runController.signal });
  } catch (e) { finishRun("stopped"); return; }
  if (!r.ok) {
    const detail = ((await r.json().catch(() => ({}))).detail) || ("HTTP " + r.status);
    // Cloud Run may have swapped to a cold instance that lost our in-memory creds -> re-send them from the Settings
    // fields (still held in the browser, never persisted server-side) and retry the run once, transparently.
    if (!_retry && /base-url|API key|Settings/i.test(detail) && $("apiKey").value.trim()) {
      await jpost("/api/creds", { base_url: $("baseUrl").value.trim(), api_key: $("apiKey").value,
          model: $("model").value.trim(), reasoning: $("reasoning").value,
          max_tokens: parseInt($("maxTokens").value, 10) || 32000, provider: $("provider").value.trim() });
      return startRun(resume, true);
    }
    logLine("err", "✖ " + detail);
    finishRun("failed"); return;
  }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
  try {
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = block.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        try { handleEvent(JSON.parse(line.slice(5).trim()), building); } catch (_) {}
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") {
      logLine("err", "✖ connection lost mid-run: " + e);
      logLine("status", "· your progress IS saved on the server (and snapshotted to this tab every ~10 min) — click Continue to resume from where it stopped");
      $("runStatus").textContent = "connection lost — click Continue to resume";
      backupWithRetry(building, [1500, 6000]);
      finishRun("connection lost"); return;
    }
  }
  finishRun("done");
}
function finishRun(status) {
  markActivity();   // reset the keep-alive idle clock so the instance stays warm through the post-run download/Continue gap
  setRunning(false); clearTimeWarn();
  // DEMO is single-shot: one example, one run. Continue exists only for the automatic mid-design
  // resume, which startRun triggers itself -- never offer the button.
  show($("resumeBtn"), !DEMO);
  // Single-shot rule, EXCEPT while the automatic soft-deadline resume is in flight: the stream
  // ends (finishRun) BEFORE the 1.2 s resume timer fires, and ending the demo here killed the
  // auto-continue (race fixed 2026-08-16).
  if (DEMO && !demoAutoResume) { demoEnded = true; $("runBtn").disabled = true; }
  const cur = $("runStatus").textContent;
  if (cur === "running…" || cur === "resuming…") $("runStatus").textContent = status;
  runController = null;
}

// ---------------- in-browser backup + recovery (the failsafe) ----------------
// The unified zip from /api/download is BOTH the user's deliverable and the resume snapshot. At each safe
// checkpoint we pull it into the tab's memory and point Download at that in-memory copy, so Download keeps
// working even if the instance later recycles. On Continue, if the server has lost the session (cold
// instance, /tmp wiped), we push this snapshot back via /api/restore before resuming.
async function backupResults(building) {
  if (!building) return false;
  try {
    const r = await api("/api/download/" + encodeURIComponent(building));
    if (!r.ok) return false;
    const blob = await r.blob();
    if (!blob || !blob.size) return false;
    if (backupUrl) { try { URL.revokeObjectURL(backupUrl); } catch (_) {} }
    backupBlob = blob; backupBuilding = building; backupUrl = URL.createObjectURL(blob);
    const dl = $("downloadLink");
    dl.href = backupUrl; dl.setAttribute("download", building + "_design.zip"); show(dl, true);
    show($("downloadInfo"), true);
    const tw = $("timeWarnDownload");
    if (tw) { tw.href = backupUrl; tw.setAttribute("download", building + "_design.zip"); }
    const note = $("backupNote");
    if (note) { note.textContent = "✓ saved to your browser — Download anytime; also restores Continue if the session resets"; show(note, true); }
    zipExtract(blob, "report.html").then((html) => {   // enable offline View from the backup too
      if (!html) return;
      if (reportUrl) { try { URL.revokeObjectURL(reportUrl); } catch (_) {} }
      reportUrl = URL.createObjectURL(html);
      const a = $("reportLink"); a.href = reportUrl; a.setAttribute("target", "_blank"); show(a, true);
    }).catch(() => {});
    blobifyViewer(blob);
    return true;
  } catch (_) { return false; }
}
// Stop/Pause/connection-loss path: the SSE stream is gone, so pull the bundle over a plain request instead;
// retry a few times (the instance may still be finishing its last tool call) and say so if nothing exists yet.
async function backupWithRetry(building, delays = [1200, 4000, 10000]) {
  for (const d of delays) {
    await new Promise((r) => setTimeout(r, d));
    if (await backupResults(building)) return true;
  }
  const note = $("backupNote");
  if (note) { note.textContent = "nothing to download yet — the run stopped before any results were saved"; show(note, true); }
  return false;
}
function _blobToB64(blob) {   // -> base64 (no data: prefix), for shipping the snapshot inside the run request
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",", 2)[1] || ""); r.onerror = rej; r.readAsDataURL(blob); });
}
// Pull one file out of an in-memory zip Blob (DEFLATE) so the report can be VIEWED offline from the backup,
// no server round-trip. Parses the zip central directory and inflates via DecompressionStream.
async function zipExtract(blob, wantName) {
  try {
    const buf = new Uint8Array(await blob.arrayBuffer());
    const dv = new DataView(buf.buffer);
    let eocd = -1;
    for (let i = buf.length - 22; i >= 0; i--) { if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; } }
    if (eocd < 0) return null;
    let p = dv.getUint32(eocd + 16, true); const count = dv.getUint16(eocd + 10, true);
    for (let n = 0; n < count && dv.getUint32(p, true) === 0x02014b50; n++) {
      const method = dv.getUint16(p + 10, true), compSize = dv.getUint32(p + 20, true);
      const nameLen = dv.getUint16(p + 28, true), extraLen = dv.getUint16(p + 30, true), commentLen = dv.getUint16(p + 32, true);
      const lho = dv.getUint32(p + 42, true);
      const fname = new TextDecoder().decode(buf.subarray(p + 46, p + 46 + nameLen));
      if (fname === wantName || fname.endsWith("/" + wantName)) {
        const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
        const comp = buf.subarray(start, start + compSize);
        if (method === 0) return new Blob([comp], { type: "text/html" });
        const inflated = await new Response(new Blob([comp]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).arrayBuffer();
        return new Blob([inflated], { type: "text/html" });
      }
      p += 46 + nameLen + extraLen + commentLen;
    }
  } catch (_) {}
  return null;
}
$("runBtn").addEventListener("click", () => startRun(false));
$("resumeBtn").addEventListener("click", () => startRun(true));
async function loadExample(which, label) {
  try {
    const j = await (await api("/api/example/" + which)).json();
    if (j && j.brief) {
      $("brief").value = j.brief;
      if (!$("building").value.trim()) $("building").value = (which || "example").replace(/^ex/, "Ex");
      $("runStatus").textContent = "example loaded (" + (label || which) + ") — edit if you like, then click Design building";
    } else { $("runStatus").textContent = "could not load example"; }
  } catch (_) { $("runStatus").textContent = "could not load example"; }
}
$("exampleSelect").addEventListener("change", (e) => {
  const sel = e.target;
  if (DEMO) {
    // The selection IS the run input -- keep it selected and arm the button. Show the brief
    // READ-ONLY so the visitor can see what will run (2026-08-12, parity with the CFS demo);
    // the server still resolves the example key itself and ignores any client brief.
    $("runBtn").disabled = !sel.value || demoEnded;
    $("runStatus").textContent = sel.value ? "" : "choose an example building";
    if (sel.value) loadExampleReadonly(sel.value, sel.options[sel.selectedIndex].text);
    return;
  }
  if (!sel.value) return;
  loadExample(sel.value, sel.options[sel.selectedIndex].text);
  sel.selectedIndex = 0;                     // reset so the same example can be re-picked
});
async function loadExampleReadonly(which, label) {
  try {
    const j = await (await api("/api/example/" + which)).json();
    $("brief").value = (j && j.brief) || "(brief unavailable)";
    $("runStatus").textContent = "example loaded (" + (label || which) +
                                 ") — read-only in Demo; click Run this example";
  } catch (_) { $("runStatus").textContent = "could not load example"; }
}
async function stopServerRun() {
  const building = curBuilding();
  if (!building) return;
  try { await jpost("/api/stop", { building }); } catch (_) {}
}
let stopWaitTimer = null;
$("stopBtn").addEventListener("click", () => {
  const b = curBuilding();
  stopServerRun();                                   // fleet-wide flag (control plane); the run halts itself in ~3 s
  $("runStatus").textContent = "stopping — saving your progress…";
  logLine("status", "· stop requested — the run will halt within a few seconds and send your files to this tab");
  if (stopWaitTimer) clearTimeout(stopWaitTimer);
  stopWaitTimer = setTimeout(() => {                 // fallback (control plane unreachable): disconnect teardown
    stopWaitTimer = null;
    if (runController) runController.abort();
    $("runStatus").textContent = "stopped — progress saved on the server; click Continue to resume";
    backupWithRetry(b);
  }, 20000);
});
$("timeWarnClose").addEventListener("click", () => show($("timeWarn"), false));

// ---------------- file upload (text/PDF -> brief, images -> attachments; max 3 files, 5 MB each) ----------------
const MAX_FILES = 3, MAX_BYTES = 5 * 1024 * 1024;
const IMG_RE = /\.(png|jpe?g|gif|webp)$/i, TXT_RE = /\.(txt|md|markdown|text)$/i, PDF_RE = /\.pdf$/i;
function fmtSize(n) { return n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
function renderFileList() {
  const el = $("fileList"); el.innerHTML = "";
  attachedImages.forEach((im, i) => {
    const c = document.createElement("span"); c.className = "filechip";
    const b = document.createElement("b"); b.textContent = im.name;
    const s = document.createElement("span"); s.className = "sz"; s.textContent = fmtSize(im.size);
    const x = document.createElement("span"); x.className = "x"; x.title = "remove"; x.textContent = "✕";
    x.addEventListener("click", () => { attachedImages.splice(i, 1); renderFileList(); });
    c.append(b, s, x); el.appendChild(c);
  });
}
const _readFile = (f, how) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r[how](f); });
// pdf.js is only fetched the first time a PDF is added (keeps normal page loads light)
let _pdfjsP = null;
function _loadPdfjs() {
  if (_pdfjsP) return _pdfjsP;
  _pdfjsP = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    s.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js"; res(window.pdfjsLib); };
    s.onerror = () => { _pdfjsP = null; rej(new Error("pdf.js failed to load")); };
    document.head.appendChild(s);
  });
  return _pdfjsP;
}
async function _pdfText(f) {                        // extract the text layer, page by page
  const lib = await _loadPdfjs();
  const doc = await lib.getDocument({ data: await _readFile(f, "readAsArrayBuffer") }).promise;
  const pages = [];
  for (let p = 1; p <= doc.numPages; p++) {
    const tc = await (await doc.getPage(p)).getTextContent();
    pages.push(tc.items.map((it) => it.str).join(" "));
  }
  try { doc.destroy(); } catch (_) {}
  return pages.join("\n\n").replace(/[ \t]+/g, " ").trim();
}
async function addFiles(files) {
  files = Array.from(files || []); if (!files.length) return;
  if (files.length > MAX_FILES) { $("briefFileName").textContent = `max ${MAX_FILES} files at once`; return; }
  const textChunks = [], txtNames = []; let note = "";
  for (const f of files) {
    if (f.size > MAX_BYTES) { note = `"${f.name}" exceeds 5 MB — skipped`; continue; }
    const isImg = IMG_RE.test(f.name) || /^image\//.test(f.type);
    const isPdf = PDF_RE.test(f.name) || f.type === "application/pdf";
    const isTxt = TXT_RE.test(f.name) || /^text\//.test(f.type) || f.type === "";
    if (isImg) {
      if (attachedImages.length >= MAX_FILES) { note = `max ${MAX_FILES} files`; continue; }
      attachedImages.push({ name: f.name, type: f.type || "image/*", data_url: await _readFile(f, "readAsDataURL"), size: f.size });
    } else if (isPdf) {
      try {
        const txt = await _pdfText(f);
        if (txt) { textChunks.push(`# ${f.name}\n` + txt); txtNames.push(f.name); }
        else note = `"${f.name}" has no extractable text (scanned PDF?) — skipped`;
      } catch (_) { note = `"${f.name}" could not be read as a PDF — skipped`; }
    } else if (isTxt) {
      textChunks.push((files.length > 1 ? `# ${f.name}\n` : "") + await _readFile(f, "readAsText")); txtNames.push(f.name);
    } else { note = `"${f.name}" type not supported — skipped`; }
  }
  if (textChunks.length) {
    const cur = $("brief").value.trim();
    $("brief").value = (cur ? cur + "\n\n" : "") + textChunks.join("\n\n");
  }
  $("briefFileName").textContent = txtNames.length ? "loaded " + txtNames.join(", ") + (note ? " · " + note : "") : note;
  renderFileList();
}
$("briefFile").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

// Manual recovery for a closed tab / different device: load the design .zip into the browser; the next
// Continue ships it inside the run request, which restores + resumes on ONE instance (no routing race).
$("restoreFile").addEventListener("change", async (e) => {
  const f = (e.target.files || [])[0]; e.target.value = "";
  if (!f) return;
  const building = curBuilding();
  if (!building) { $("runStatus").textContent = "enter the project name first, then choose its resume .zip"; return; }
  if (backupUrl) { try { URL.revokeObjectURL(backupUrl); } catch (_) {} }
  backupBlob = f; backupBuilding = building; backupUrl = URL.createObjectURL(f);
  const dl = $("downloadLink"); dl.href = backupUrl; dl.setAttribute("download", building + "_design.zip"); show(dl, true); show($("downloadInfo"), true);
  zipExtract(f, "report.html").then((html) => { if (!html) return; if (reportUrl) { try { URL.revokeObjectURL(reportUrl); } catch (_) {} } reportUrl = URL.createObjectURL(html); const a = $("reportLink"); a.href = reportUrl; a.setAttribute("target", "_blank"); show(a, true); }).catch(() => {});
  blobifyViewer(f);
  show($("resumeBtn"), true);
  $("runStatus").textContent = "loaded — type your change and click Continue to resume from this file";
});

$("building").addEventListener("change", loadJobLog);
async function loadJobLog() {
  const b = curBuilding(); if (!b) return;
  try {
    const j = await (await api("/api/log/" + encodeURIComponent(b))).json();
    if (j.events && j.events.length) { logEl().innerHTML = ""; tokenLine = null; j.events.forEach((ev) => handleEvent(ev, b)); $("runStatus").textContent = "loaded previous log"; }
    show($("resumeBtn"), !!j.resumable);
    if (j.has_report) { $("reportLink").href = `/api/report/${encodeURIComponent(b)}/report.html`; show($("reportLink"), true);
      $("modelLink").href = `/api/report/${encodeURIComponent(b)}/viewer_3d.html`; show($("modelLink"), true);
      $("downloadLink").href = `/api/download/${encodeURIComponent(b)}`; show($("downloadLink"), true); show($("downloadInfo"), true); }
    if (j.resumable || j.has_report) backupResults(b);   // keep an in-browser copy so this project survives an instance reset
  } catch (_) {}
}

function wireExpand(btnId, targetId) {
  const b = $(btnId), t = $(targetId);
  if (!b || !t) return;
  b.addEventListener("click", () => {
    const ex = t.classList.toggle("expanded");
    b.textContent = ex ? "⤡" : "⤢";
    b.title = ex ? "Shrink" : "Expand";
  });
}
wireExpand("logExpand", "log");
wireExpand("reasonExpand", "reason");

boot();


// ---- 3D viewer resilience --------------------------------------------------------------------
// In-browser copy of viewer_3d.html (mirrors reportUrl): once any bundle exists, "View model"
// serves from browser memory -- immune to the busy-instance routing race (concurrency=1 means a
// click during a run can spill to a fresh container without the job files).
function blobifyViewer(zipBlob) {
  zipExtract(zipBlob, "viewer_3d.html").then((html) => {
    if (!html) return;
    if (modelUrl) { try { URL.revokeObjectURL(modelUrl); } catch (_) {} }
    modelUrl = URL.createObjectURL(html);
    const a = $("modelLink"); a.href = modelUrl; a.setAttribute("target", "_blank"); show(a, true);
  }).catch(() => {});
}
// No in-browser copy yet (early in a run / restored session): fetch with retries instead of making
// the user press the button repeatedly. The tab must be opened synchronously (popup rules), then
// pointed at the fetched copy.
$("modelLink").addEventListener("click", (e) => {
  const href = $("modelLink").getAttribute("href") || "";
  if (!href || href.startsWith("blob:")) return;              // in-browser copy: default behavior
  e.preventDefault();
  const w = window.open("", "_blank");
  (async () => {
    for (let i = 0; i < 4; i++) {
      try {
        const r = await fetch(href, { cache: "no-store" });
        if (r.ok) {
          const u = URL.createObjectURL(await r.blob());
          if (w) w.location = u; else window.open(u, "_blank", "noopener");
          return;
        }
      } catch (_) {}
      if (w) { try { w.document.body.textContent = "loading 3D viewer\u2026 retry " + (i + 1) + "/4"; } catch (_) {} }
      await new Promise((res) => setTimeout(res, 1200 * (i + 1)));
    }
    if (w) { try { w.close(); } catch (_) {} }
    $("runStatus").textContent = "3D viewer not reachable right now \u2014 open viewer_3d.html from the Download zip";
  })();
});
