"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.json();
};
const TIERS = { nano: { name: "Nano", role: "triage", color: "var(--nano)" }, super: { name: "Super", role: "patch", color: "var(--super)" }, ultra: { name: "Ultra", role: "rescue", color: "var(--ultra)" } };
const fmt = { n: x => (x ?? 0).toLocaleString(), usd: x => x == null ? "n/a" : x < 0.01 ? "$" + x.toFixed(4) : "$" + x.toFixed(3), s: x => x >= 60 ? Math.floor(x / 60) + "m " + Math.round(x % 60) + "s" : (x ?? 0).toFixed(0) + "s" };
let CONFIG = null, EXAMPLES = [], stream = null, timer = null;

function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2200); }

/* ---------- theme ---------- */
(function theme() {
  let saved = null; try { saved = localStorage.getItem("pw-theme"); } catch (e) {}
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme").onclick = () => {
    const dark = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() === "#0c0e0b";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("pw-theme", next); } catch (e) {}
  };
})();

/* ---------- router ---------- */
async function route() {
  if (stream) { stream.close(); stream = null; }
  clearInterval(timer);
  const [_, page, arg] = location.hash.split("/");
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === (page || "run") || (page === "r" && a.dataset.nav === "run")));
  const app = $("#app");
  try {
    if (!page || page === "") await pageRun(app);
    else if (page === "r") await pageRun(app, arg);
    else if (page === "bench") await pageBench(app);
    else if (page === "how") pageHow(app);
    else if (page === "monitor") await pageMonitor(app);
    else if (page === "history") await pageHistory(app);
    else app.innerHTML = `<div class="empty">Page not found.</div>`;
  } catch (e) { app.innerHTML = `<div class="card"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

async function boot() {
  [CONFIG, EXAMPLES] = await Promise.all([api("/api/config"), api("/api/examples")]);
  const m = CONFIG.models;
  $("#env").innerHTML = `<span class="pill ${CONFIG.provider === "nebius" ? "ok" : "warn"}" title="Model provider">${CONFIG.provider === "nebius" ? "Nebius Token Factory" : "NVIDIA dev endpoint"}</span>` +
    `<span class="pill" title="${esc(JSON.stringify(m))}">Nemotron ×3 tiers</span>`;
  route();
}

/* ---------- run page ---------- */
let form = { tab: "example", example: null, mode: "tiered" };

async function pageRun(app, runId) {
  const dots = n => `<span class="diff-dots">${[1, 2, 3].map(i => `<i class="${i <= n ? "f" : ""}"></i>`).join("")}</span>`;
  app.innerHTML = `
    <h1>Fixes you can trust, because they come with proof.</h1>
    <p class="lede">Paste a bug or pick one below. Patchwright reproduces it in a sandbox, writes a failing test, fixes the code, and only calls it done when the test turns green. Nemotron Nano triages, Super writes the patch, and Ultra steps in only when Super is stuck.</p>
    <div class="cols">
      <section class="card" id="formcard">
        <div class="tabs" role="tablist"><button data-tab="example" class="${form.tab === "example" ? "on" : ""}">Example bugs</button>${CONFIG.demo_mode ? "" : `<button data-tab="repo" class="${form.tab === "repo" ? "on" : ""}">Your repo</button>`}</div>
        <div id="tabbody"></div>
        <label>Strategy</label>
        <div class="seg" id="seg">
          <button data-m="tiered" class="${form.mode === "tiered" ? "on" : ""}" title="Super first, escalate to Ultra if stuck">Tiered</button>
          <button data-m="super" class="${form.mode === "super" ? "on" : ""}">Super only</button>
          <button data-m="ultra" class="${form.mode === "ultra" ? "on" : ""}">Ultra only</button>
          <button data-m="routed" class="${form.mode === "routed" ? "on" : ""}" title="Nano judges the task first: refuses non-bugs, sends hard bugs straight to Ultra">Routed</button>
        </div>
        <button class="btn block" id="go">Fix this bug</button>
        <p class="hint" id="formhint"></p>
      </section>
      <section id="console"></section>
    </div>`;
  const body = $("#tabbody");
  const drawTab = () => {
    if (form.tab === "example") {
      body.innerHTML = `<div class="examples">${EXAMPLES.map(e => `<button class="ex ${form.example === e.id ? "on" : ""}" data-id="${esc(e.id)}"><b>${esc(e.id)}</b>${dots(e.difficulty)}<p>${esc(e.issue)}</p></button>`).join("")}</div>`;
      body.querySelectorAll(".ex").forEach(b => b.onclick = () => { form.example = b.dataset.id; drawTab(); });
    } else {
      body.innerHTML = `<label for="repo">Public Git repository (https)</label><input type="text" id="repo" placeholder="https://github.com/owner/repo" value="${esc(form.repo || "")}">
        <label for="issue">What is the bug?</label><textarea id="issue" placeholder="One specific bug: what you did, what you expected, what happened. Example: parse_date('2024-02-30') returns a date instead of raising ValueError.">${esc(form.issue || "")}</textarea>
        <p class="hint">Describe <b>one specific bug</b>. Patchwright fixes a bug you describe; it does not search for unknown bugs. Python projects with a pytest suite work best. The repo is cloned into a throwaway Docker container.</p>`;
      $("#repo").oninput = e => form.repo = e.target.value; $("#issue").oninput = e => form.issue = e.target.value;
    }
  };
  drawTab();
  app.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { form.tab = b.dataset.tab; app.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b)); drawTab(); });
  app.querySelectorAll("#seg button").forEach(b => b.onclick = () => { form.mode = b.dataset.m; app.querySelectorAll("#seg button").forEach(x => x.classList.toggle("on", x === b)); });
  $("#go").onclick = async () => {
    const body = { mode: form.mode };
    if (form.tab === "example") { if (!form.example) return toast("Pick an example first"); body.example_id = form.example; }
    else { body.repo_url = (form.repo || "").trim(); body.issue = (form.issue || "").trim(); }
    $("#go").disabled = true;
    try { const r = await api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); location.hash = "#/r/" + r.id; }
    catch (e) { toast(e.message); $("#go").disabled = false; }
  };
  if (!runId) { $("#console").innerHTML = idleConsole(); return; }
  await watchRun(runId);
}

function idleConsole() {
  return `<div class="card"><h2>Live console</h2><div class="empty">Pick a bug and press <b>Fix this bug</b>.<br>You will watch the agent work here: each command, each model hand-off, and the final proof.</div></div>`;
}

async function watchRun(id) {
  const box = $("#console");
  box.innerHTML = `<div class="card"><div class="empty">Loading run…</div></div>`;
  const run = await api("/api/runs/" + id);
  let events = [...run.events], result = run.result || null, status = run.status;
  const render = () => { box.innerHTML = consoleHTML(run, events, result, status); wireConsole(box); const f = $("#feed"); if (f && status === "running") f.scrollTop = f.scrollHeight; };
  render();
  if (status === "queued" || status === "running") {
    stream = new EventSource(`/api/runs/${id}/stream`);
    let seen = events.length;
    stream.onmessage = m => { const ev = JSON.parse(m.data); if (seen > 0) { seen--; return; } events.push(ev); if (ev.type === "end") status = ev.status; render(); };
    stream.addEventListener("close", async () => { stream.close(); stream = null; const r = await api("/api/runs/" + id); result = r.result; status = r.status; run.error = r.error; events = r.events; render(); });
    timer = setInterval(() => { if (status === "running" || status === "queued") render(); }, 1000);
  }
}

function consoleHTML(run, events, result, status) {
  const last = [...events].reverse().find(e => e.usage);
  const u = last?.usage || { tokens: 0, cost_usd: 0, calls: 0 };
  const steps = events.filter(e => e.type === "step" || e.type === "verify").length;
  const live = status === "queued" || status === "running";
  const elapsed = live && run.started ? Date.now() / 1000 - run.started : (result?.seconds ?? last?.t ?? 0);
  const statusPill = { queued: `<span class="pill">queued</span>`, running: `<span class="pill live"><i class="dot"></i>working</span>`, solved: `<span class="pill ok">verified fix</span>`, unsolved: `<span class="pill bad">not solved</span>`, error: `<span class="pill bad">error</span>`, interrupted: `<span class="pill warn">interrupted</span>` }[status] || "";
  const startEv = events.find(e => e.type === "start");
  const models = startEv?.models || CONFIG.models;
  const routeEv = [...events].reverse().find(e => e.type === "route");
  const tiersUsed = routeEv ? routeEv.tiers : (startEv?.tiers || (run.mode === "tiered" ? ["super", "ultra"] : [run.mode]));
  // pipeline state
  const st = { nano: "idle", super: "idle", ultra: "idle" };
  if (events.some(e => e.type === "triage")) st.nano = "done";
  else if (live) st.nano = "active";
  let cur = null;
  events.forEach(e => { if (e.type === "tier_start") { cur = e.tier; st[cur] = "active"; } if (e.type === "escalate") st[e.from] = "failed"; if (e.type === "verify" && e.ok) st[e.tier] = "done"; });
  if (!live) Object.keys(st).forEach(t => { if (st[t] === "active") st[t] = "failed"; });
  const label = { idle: "waiting", active: "working", done: "done", failed: "gave up", skip: "not used" };
  const node = t => {
    const used = t === "nano" || tiersUsed.includes(t);
    const s = used ? st[t] : "skip";
    const cls = s === "active" ? "active" : s === "done" ? "done" : s === "skip" ? "skip" : "";
    const tu = u.by_tier?.[t];
    return `<div class="node ${cls}" style="--c:${TIERS[t].color}"><div class="t">${TIERS[t].name}<span class="pill ${s === "done" ? "ok" : s === "active" ? "live" : s === "failed" ? "bad" : ""}">${label[s] || s}</span></div><div class="m">${esc(models[t] || "model not set")}</div><div class="s">${TIERS[t].role}${tu ? ` · ${tu.calls} calls · ${fmt.n(tu.tokens)} tok` : ""}</div></div>`;
  };
  // proof
  const verifies = events.filter(e => e.type === "verify");
  const okV = verifies.find(v => v.ok), badV = verifies[verifies.length - 1];
  const chk = (title, desc, state, extra = "") => `<div class="chk ${state}"><div class="ic">${state === "ok" ? "✓" : state === "no" ? "✕" : "·"}</div><div><b>${title}</b><span>${desc}${extra}</span></div></div>`;
  let c1 = "", c2 = "", c3 = "", why = "";
  if (okV) { c1 = c2 = c3 = "ok"; }
  else if (badV && !live) {
    const w = badV.why || ""; why = w.slice(0, 300);
    if (/Repro test still fails/.test(w)) { c1 = "ok"; c2 = "no"; }
    else if (/suite fails/.test(w)) { c1 = c2 = "ok"; c3 = "no"; }
    else c1 = "no";
  }
  const hiddenState = result?.hidden_ok === true ? "ok" : result?.hidden_ok === false ? "no" : "";
  const proof = `<div class="proof">
    ${chk("New test fails without the fix", "The harness reverts the code change and re-runs the test it wrote. If it still passes, the test proves nothing.", c1)}
    ${chk("Same test passes with the fix", "The change is restored and the test must now be green.", c2)}
    ${chk("Existing test suite still passes", "No regressions, and no existing test file may be edited.", c3, why && c3 === "no" ? ` <br><code>${esc(why)}</code>` : "")}
    ${run.example ? chk("Hidden test the agent never saw", "Each bundled bug has a secret test that independently checks correctness.", hiddenState) : ""}
  </div>`;
  const feed = events.filter(e => !["start", "end", "done", "budget"].includes(e.type)).map(evHTML).join("") || `<div class="empty">Starting sandbox…</div>`;
  const diff = (result?.diff || (events.find(e => e.type === "done")?.diff) || "").trim();
  return `
    <div class="card">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px"><h2 style="margin:0">${esc(run.label)}</h2>${statusPill}<span class="pill">${esc(run.mode)}</span></div>
      <p style="margin:0 0 14px;color:var(--mute)">${esc(run.issue)}</p>
      <div class="meter"><div><b>${fmt.s(elapsed)}</b><span>elapsed</span></div><div><b>${steps}</b><span>steps</span></div><div><b>${fmt.n(u.tokens)}</b><span>tokens</span></div><div><b>${fmt.usd(u.cost_usd)}</b><span>cost${CONFIG.prices_are_estimates ? " (est.)" : ""}</span></div></div>
      <div class="pipeline">${node("nano")}<div class="arrow">→</div>${node("super")}<div class="arrow">→</div>${node("ultra")}</div>
      ${run.error ? `<div class="banner" style="background:var(--bad-soft);color:var(--bad)">${esc(run.error)}</div>` : ""}
      ${routeEv && !routeEv.valid && !live ? `<div class="banner" style="background:var(--warn-soft)">Nano judged this is not a specific, testable bug report, so nothing more was spent. Triage can be wrong. <button class="btn ghost" id="runanyway" style="margin-left:8px;padding:6px 12px">Run anyway (tiered)</button></div>` : ""}
    </div>
    <div class="card sec"><h2>Proof</h2>${proof}${result?.summary && status === "solved" ? `<p style="margin:14px 0 0"><b>Root cause and fix.</b> ${esc(result.summary)}</p>` : ""}</div>
    ${diff ? `<div class="card sec"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px"><h2 style="margin:0">Patch</h2>${result?.proof_card ? `<button class="btn ghost" id="copycard">Copy PR description</button>` : ""}</div>${diffHTML(diff)}</div>` : ""}
    <div class="card sec"><h2>Activity</h2><div class="feed" id="feed">${feed}</div></div>`;
}

function wireConsole(box) {
  const ra = $("#runanyway", box);
  if (ra) ra.onclick = async () => {
    ra.disabled = true;
    try {
      const id = location.hash.split("/")[2], old = await api("/api/runs/" + id);
      const body = old.example ? { example_id: old.label.replace("example: ", ""), mode: "tiered" } : { repo_url: old.label, issue: old.issue, mode: "tiered" };
      const r = await api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      location.hash = "#/r/" + r.id;
    } catch (e) { toast(e.message); ra.disabled = false; }
  };
  const b = $("#copycard", box); if (!b) return;
  b.onclick = async () => { const id = location.hash.split("/")[2]; const r = await api("/api/runs/" + id); await navigator.clipboard.writeText(r.result.proof_card); toast("PR description copied"); };
}

function evHTML(e) {
  const t = e.tier ? TIERS[e.tier] : null, c = t ? t.color : "var(--line2)";
  const head = (k, extra = "") => `<div class="h"><span class="k">${k}</span>${t ? `<span class="pill" style="color:${c}">${t.name}</span>` : ""}${extra}<span class="time mono">${e.t ?? ""}s</span></div>`;
  if (e.type === "triage") return `<div class="ev" style="--c:${TIERS.nano.color}">${head("Triage")}<pre>${esc((e.text || "").trim().slice(0, 700))}</pre></div>`;
  if (e.type === "route") return e.valid
    ? `<div class="ev" style="--c:var(--brand)">${head("Routing decision", `<span class="pill ${e.difficulty === "hard" ? "warn" : "ok"}">${esc(e.difficulty)}</span><span>${e.tiers.length === 1 ? "straight to " + TIERS[e.tiers[0]].name : "Super first, Ultra if stuck"}</span>`)}${e.why ? `<pre>${esc(e.why)}</pre>` : ""}</div>`
    : `<div class="ev verify no">${head("Not a specific bug report: nothing was spent beyond triage")}<pre>${esc(e.why || "The task could not be turned into a testable bug.")}</pre></div>`;
  if (e.type === "tier_start") return `<div class="ev" style="--c:${c}">${head("Starting", `<span class="mono" style="color:var(--mute)">${esc(e.model)}</span>`)}</div>`;
  if (e.type === "escalate") return `<div class="ev esc">${head("Escalating", `<span>${TIERS[e.from].name} could not finish, handing the original problem to ${TIERS[e.to].name}</span>`)}</div>`;
  if (e.type === "review") return `<div class="ev verify ${e.ok ? "ok" : "no"}">${head(e.ok ? "Edge-case review passed" : "Edge-case review found a problem")}${e.ok ? "" : `<pre>${esc((e.why || "").slice(0, 600))}</pre>`}</div>`;
  if (e.type === "abort") return `<div class="ev esc">${head("Stopped this attempt early", `<span>${esc(e.reason)}. Stopping early saves tokens.</span>`)}</div>`;
  if (e.type === "bad_reply") return `<div class="ev" style="--c:var(--warn)">${head("Unparseable reply, retrying")}</div>`;
  if (e.type === "verify") return `<div class="ev verify ${e.ok ? "ok" : "no"}">${head(e.ok ? "Proof verified" : "Finish rejected", "")}${e.ok ? "" : `<pre>${esc((e.why || "").slice(0, 600))}</pre>`}</div>`;
  const label = { run: "Run", write: "Write file", search: "Search the web" }[e.action] || e.action;
  const detail = (e.detail || "").trim();
  return `<div class="ev" style="--c:${c}">${head(label)}${detail ? `<pre>${esc(detail.slice(0, 500))}</pre>` : ""}${e.obs ? `<details><summary>Output</summary><pre>${esc(e.obs.slice(0, 1500))}</pre></details>` : ""}</div>`;
}

function diffHTML(d) {
  return `<div class="diff">${d.split("\n").map(l => {
    const cls = /^(diff |index |\+\+\+|---)/.test(l) ? "hd" : l.startsWith("@@") ? "at" : l.startsWith("+") ? "a" : l.startsWith("-") ? "d" : "";
    return `<div class="l ${cls}">${esc(l) || " "}</div>`;
  }).join("")}</div>`;
}

/* ---------- benchmark page ---------- */
const STRAT = { tiered: { name: "Tiered (Super → Ultra)", color: "var(--brand)" }, super: { name: "Super only", color: "var(--super)" }, ultra: { name: "Ultra only", color: "var(--ultra)" }, routed: { name: "Routed (triage decides)", color: "var(--nano)" } };

async function pageBench(app) {
  const data = await api("/api/bench");
  const R = data.results, keys = ["tiered", "routed", "super", "ultra"].filter(k => R[k]);
  const note = data.prices_are_estimates ? `<div class="banner">Dollar figures use estimated per-token prices (see <code>patchwright/prices.json</code>), not official Nebius billing. Fix rates, steps and times are measured.</div>` : "";
  const rnote = R.routed ? `<div class="banner">Read the Routed row with care: Nano rated every benchmark bug easy or medium, so routed took the same path as Tiered. The gap between those two rows is run-to-run noise (about ±3 of 40), not a routing benefit.</div>` : "";
  app.innerHTML = `
    <h1>Does routing between models actually pay off?</h1>
    <p class="lede">Every strategy runs the same bugs. A fix counts only if a hidden test the agent never saw passes. The question: can a cheap-first, escalate-when-stuck route match the strongest model for less money?</p>
    ${note}${rnote}
    <div id="benchbody"></div>
    ${CONFIG.demo_mode ? "" : `<div class="card sec"><h2>Run a benchmark</h2>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <select id="bcfg" style="padding:9px;border-radius:9px;border:1px solid var(--line2);background:var(--panel2);color:var(--ink)"><option value="tiered">Tiered</option><option value="super">Super only</option><option value="ultra">Ultra only</option></select>
        <button class="btn" id="bstart">Start</button><span class="hint" id="bstat"></span></div>
      <div class="logbox sec" id="blog" style="display:none"></div></div>`}`;
  const body = $("#benchbody");
  if (!keys.length) body.innerHTML = `<div class="card"><div class="empty">No benchmark results yet. Start one below.</div></div>`;
  else {
    const maxCpf = Math.max(...keys.map(k => R[k].cost_per_fix_usd || 0), 1e-9), maxT = Math.max(...keys.map(k => R[k].avg_seconds), 1);
    const bar = (k, w, v) => `<div class="row"><div>${esc(STRAT[k].name.split(" (")[0])}</div><div class="track"><div class="fill" style="width:${w}%;background:${STRAT[k].color}"></div></div><div class="val">${v}</div></div>`;
    const tasks = data.tasks.map(t => t.id);
    body.innerHTML = `
      <div class="kpis">${keys.map(k => `<div class="card kpi"><div class="l">${esc(STRAT[k].name)}</div><div class="v">${R[k].fixed}/${R[k].tasks}</div><div class="l">truly fixed · ${R[k].fix_rate}% · avg ${R[k].avg_seconds}s${R[k].repeats > 1 ? ` · ${R[k].repeats} runs per bug` : ""}</div></div>`).join("")}</div>
      <div class="grid sec" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
        <div class="card bars"><h3>Fix rate</h3>${keys.map(k => bar(k, R[k].fix_rate, R[k].fix_rate + "%")).join("")}</div>
        <div class="card bars"><h3>Cost per fixed bug${data.prices_are_estimates ? " (est.)" : ""}</h3>${keys.map(k => bar(k, R[k].cost_per_fix_usd ? 100 * R[k].cost_per_fix_usd / maxCpf : 0, fmt.usd(R[k].cost_per_fix_usd))).join("")}</div>
        <div class="card bars"><h3>Average time per bug</h3>${keys.map(k => bar(k, 100 * R[k].avg_seconds / maxT, fmt.s(R[k].avg_seconds))).join("")}</div>
      </div>
      <div class="card sec"><h2>Task by task</h2><div class="tbl"><table><tr><th>Bug</th>${keys.map(k => `<th>${esc(STRAT[k].name.split(" (")[0])}</th>`).join("")}</tr>
        ${tasks.map(id => `<tr><td class="mono">${esc(id)}</td>${keys.map(k => { const rs = R[k].rows.filter(x => x.id === id); if (!rs.length) return "<td>–</td>"; const ok = rs.filter(x => x.hidden_ok).length, r = rs.find(x => x.hidden_ok && x.diff) || rs[0]; const cls = ok === rs.length ? "ok" : ok === 0 ? "bad" : "warn"; return `<td><span class="pill ${cls}">${ok}/${rs.length} fixed</span> <span class="hint">avg ${Math.round(rs.reduce((a, x) => a + (x.steps || 0), 0) / rs.length)} steps</span>${r.diff ? `<details><summary class="hint">patch</summary>${diffHTML(r.diff)}</details>` : ""}</td>`; }).join("")}</tr>`).join("")}</table></div></div>`;
  }
  if (CONFIG.demo_mode) return;
  const poll = async () => {
    const s = await api("/api/bench/status");
    $("#bstat").textContent = s.running ? `Running ${s.config}…` : "";
    $("#bstart").disabled = s.running;
    const lg = $("#blog"); lg.style.display = s.log.length ? "block" : "none"; lg.textContent = s.log.join("\n");
    return s.running;
  };
  $("#bstart").onclick = async () => { try { await api("/api/bench/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config: $("#bcfg").value, workers: 2 }) }); } catch (e) { toast(e.message); } await poll(); };
  await poll();
  timer = setInterval(async () => { const running = await poll(); if (!running && $("#bstart") && !$("#bstart").disabled) { /* idle */ } }, 3000);
}

/* ---------- live monitor of background benchmarks ---------- */
async function pageMonitor(app) {
  app.innerHTML = `<h1>Benchmarks, live</h1>
    <p class="lede">What the background benchmark runs are doing right now. Updates every 3 seconds. Results are written as bugs finish; the dollar figures use estimated prices.</p>
    <div class="kpis" id="mtop"></div><div id="mcards" class="grid sec"></div>`;
  const draw = async () => {
    let d; try { d = await api("/api/monitor"); } catch (e) { return; }
    if (!$("#mtop")) return;
    $("#mtop").innerHTML = [["Sandboxes running", d.containers], ["Tokens used", fmt.n(d.tokens)], ["Est. cost so far", fmt.usd(d.cost)]]
      .map(([l, v]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("");
    const pill = c => c.status === "finished" ? `<span class="pill ok">${c.stopped ? "stopped: cap reached" : "finished"}</span>` : c.status === "running" ? `<span class="pill live"><i class="dot"></i>running</span>` : `<span class="pill">waiting</span>`;
    $("#mcards").innerHTML = d.cards.map(c => {
      const pct = Math.round(100 * c.done / c.total);
      return `<div class="card"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px"><h2 style="margin:0">${esc(c.label)}</h2>${pill(c)}<span class="hint">${c.workers} in parallel</span></div>
        <div class="bars"><div class="row" style="grid-template-columns:1fr 110px"><div class="track"><div class="fill" style="width:${pct}%;background:var(--brand)"></div></div><div class="val">${c.done}/${c.total}</div></div></div>
        <div class="meter" style="margin:6px 0 12px"><div><b>${c.fixed}</b><span>truly fixed so far</span></div><div><b>${fmt.n(c.tokens)}</b><span>tokens</span></div><div><b>${fmt.usd(c.cost)}</b><span>est. cost</span></div><div><b>${c.errors}</b><span>crashed</span></div></div>
        ${c.running_now.length ? `<h3>Working on now</h3><div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">${c.running_now.map(x => `<span class="pill live" title="repeat ${x.rep + 1}"><i class="dot"></i>${esc(x.id)} · ${fmt.s(x.seconds)}</span>`).join("")}</div>` : ""}
        ${c.recent.length ? `<h3>Latest results</h3><div class="tbl"><table><tr><th>Bug</th><th>Result</th><th>Tier</th><th>Steps</th><th>Time</th></tr>${c.recent.map(r => `<tr><td class="mono">${esc(r.id)}${r.rep ? ` <span class="hint">#${r.rep + 1}</span>` : ""}</td><td>${r.error ? `<span class="pill bad">crashed</span>` : r.fixed ? `<span class="pill ok">fixed</span>` : `<span class="pill bad">not fixed</span>`}</td><td>${esc(r.tier || "–")}</td><td>${r.steps ?? "–"}</td><td>${fmt.s(r.seconds)}</td></tr>`).join("")}</table></div>` : ""}
        ${c.stopped ? `<div class="banner" style="margin-top:12px">Stopped early: ${esc(c.stopped)}. Partial results kept.</div>` : ""}</div>`;
    }).join("");
  };
  await draw();
  timer = setInterval(draw, 3000);
}

/* ---------- history ---------- */
async function pageHistory(app) {
  const runs = await api("/api/runs");
  app.innerHTML = `<h1>Past runs</h1><p class="lede">Every run is saved with its full activity log.</p>
    ${runs.length ? `<div class="tbl"><table><tr><th>When</th><th>Bug</th><th>Strategy</th><th>Result</th><th></th></tr>${runs.map(r => `<tr><td>${new Date(r.created * 1000).toLocaleString()}</td><td>${esc(r.label)}</td><td>${esc(r.mode)}</td><td><span class="pill ${r.status === "solved" ? "ok" : r.status === "running" || r.status === "queued" ? "live" : "bad"}">${esc(r.status)}</span> ${r.result ? `<span class="hint">${r.result.steps} steps · ${fmt.usd(r.result.cost_usd)}</span>` : ""}</td><td><a href="#/r/${r.id}">Open</a></td></tr>`).join("")}</table></div>` : `<div class="card"><div class="empty">No runs yet. Go fix a bug.</div></div>`}`;
}

/* ---------- how it works ---------- */
function pageHow(app) {
  app.innerHTML = `
    <h1>How Patchwright earns trust</h1>
    <p class="lede">Most coding assistants write a plausible fix and ask you to believe it. Patchwright never asks you to. The model proposes; a separate harness verifies.</p>
    <div class="how-grid">
      <div class="card"><div class="step-n">1</div><h3>Sandbox</h3><p>The repo is cloned into a throwaway Docker container with memory and CPU limits. Nothing touches your machine.</p></div>
      <div class="card"><div class="step-n">2</div><h3>Triage with Nano</h3><p>A small, fast Nemotron model reads the issue and the repo and points at the likely files. Cheap work goes to the cheap model.</p></div>
      <div class="card"><div class="step-n">3</div><h3>Reproduce and patch with Super</h3><p>Super writes a new test that fails because of the bug, then fixes the source. It may search the web with Tavily for unfamiliar errors.</p></div>
      <div class="card"><div class="step-n">4</div><h3>Rescue with Ultra</h3><p>If Super runs out of steps, the repo is reset and the strongest model takes a fresh look, with notes on what failed.</p></div>
      <div class="card"><div class="step-n">5</div><h3>Independent proof</h3><p>The harness, not the model, reverts the fix and confirms the new test fails, restores it and confirms it passes, runs the whole suite, and rejects any edit to existing tests.</p></div>
      <div class="card"><div class="step-n">6</div><h3>Deliver evidence</h3><p>You get the patch and a PR description with the proof, tokens and cost. If the agent could not fix it, it says so instead of guessing.</p></div>
    </div>
    <div class="card sec"><h2>Why tiers?</h2><p>The strongest model is expensive and slow, and most bugs do not need it. Routing sends each bug to the cheapest model that can fix it. The Benchmark page measures whether that holds: fix rate, time and cost per fixed bug for tiered, Super-only and Ultra-only runs on the same bugs, scored by hidden tests.</p></div>
    <div class="card sec"><h2>What it does not do</h2><p>It will not fix every bug, and it says so when it cannot. It is built for Python projects with a test suite. It never merges anything: a human reviews the evidence.</p></div>`;
}

boot().catch(e => { $("#app").innerHTML = `<div class="card"><h2>Cannot reach the server</h2><p>${esc(e.message)}</p></div>`; });
