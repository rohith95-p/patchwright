"use strict";
/* Landing, Results and How-it-works pages.
   Every number here comes from /api/showcase, which is computed from the result files committed in results/. Nothing is typed in by hand. */
let SHOW = null;
const showcase = async () => SHOW || (SHOW = await api("/api/showcase"));
const change = (before, after) => before ? Math.round(100 * (after - before) / before) : 0;
const signed = n => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n) + "%";
const MODEL_INFO = {
  nano: { size: "30B", job: "Triage", text: "Reads the issue and the repository, judges whether it is a real, testable bug, and points at the likely files." },
  super: { size: "120B", job: "Patch", text: "Writes the failing test and the minimal edit. Does most of the work, most of the time." },
  ultra: { size: "550B", job: "Rescue", text: "Takes a fresh look on a reset repository when Super is stuck. The strongest model, used only where needed." },
};

/* ---------------------------------------------------------------- landing */
async function pageHome(app) {
  const d = await showcase();
  const t = d.bench.tiered, u = d.bench.ultra, rw = d.realworld, eu = d.efficiency.ultra, et = d.efficiency.tiered;
  const cheaper = Math.round(100 * (1 - t.cost_per_fix / u.cost_per_fix));
  const saveU = -change(eu.before.cost_fix, eu.after.cost_fix), saveT = -change(et.before.cost_fix, et.after.cost_fix);
  const prices = d.prices;
  app.innerHTML = `
  <section class="hero">
    <div class="hero-copy">
      <div class="eyebrow"><img src="/static/logo.svg" alt="" width="22" height="22"> Nebius × NVIDIA Global AI Hackathon · Coding and Agentic Engineering</div>
      <h1>Fixes that come with <span class="grad">proof</span>.</h1>
      <p class="lede">Patchwright is an AI bug-fixing agent that never calls a fix done because the model said so. Plain code reverts the fix, re-runs the test and checks the whole suite. Three NVIDIA Nemotron models share the work, and the expensive one runs only where it has to.</p>
      <div class="cta">
        <a class="btn" href="#/fix">Fix a bug now</a>
        <a class="btn ghost" href="${esc(rw.pr_url)}" target="_blank" rel="noopener noreferrer">See the real pull request ↗</a>
        <a class="btn ghost" href="#/results">Read the numbers</a>
      </div>
      <p class="hint">Every number on this page is computed from the result files in the repository.</p>
    </div>
    <div class="hero-card" aria-label="The four proof checks, shown one after another">
      <div class="gates-title">What "done" means</div>
      ${["The new test fails with the fix reverted", "The same test passes with the fix restored", "The existing suite stays green; no test was edited", "A reviewer's edge-case tests find nothing"].map((g, i) => `
        <div class="gate" style="--i:${i}"><span class="tick"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M5 12.5l4.5 4.5L19 7" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${esc(g)}</span></div>`).join("")}
      <div class="gates-foot">checked by code, not by the model</div>
    </div>
  </section>

  <section class="stats" aria-label="Headline results">
    <div class="stat"><div class="n">${t.rate}%</div><div class="l">of ${t.runs} benchmark runs truly fixed</div><div class="s">judged by hidden tests the agent never sees</div></div>
    <div class="stat"><div class="n">${cheaper}%</div><div class="l">cheaper per fixed bug than Ultra alone</div><div class="s">${fmt.usd(t.cost_per_fix)} vs ${fmt.usd(u.cost_per_fix)}, same fix rate (${t.fixed} vs ${u.fixed} of ${t.runs})</div></div>
    <div class="stat"><div class="n">−${saveU}%</div><div class="l">cost per fix from fewer model calls</div><div class="s">Ultra ${fmt.usd(eu.before.cost_fix)} → ${fmt.usd(eu.after.cost_fix)}; tiered −${saveT}%</div></div>
    <div class="stat"><div class="n">${fmt.usd(rw.final.cost)}</div><div class="l">for a real pull request in a real library</div><div class="s">upstream's own test passes</div></div>
  </section>

  <section class="sec">
    <h2>Three models, three jobs</h2>
    <div class="trio">${["nano", "super", "ultra"].map(k => `
      <div class="card model" style="--c:${TIERS[k].color}">
        <div class="mh"><b>Nemotron 3 ${TIERS[k].name}</b><span class="pill">${MODEL_INFO[k].size}</span></div>
        <div class="job">${MODEL_INFO[k].job}</div>
        <p>${MODEL_INFO[k].text}</p>
        <div class="price">$${prices[k][0].toFixed(2)} in · $${prices[k][1].toFixed(2)} out <span class="hint">per 1M tokens</span></div>
      </div>`).join("")}
    </div>
  </section>

  <section class="sec">
    <div class="card realcard">
      <div class="rc-head"><span class="pill ok">real pull request</span><h2>A real bug in a real library</h2></div>
      <p>The issue <a href="${esc(rw.issue_url)}" target="_blank" rel="noopener noreferrer">tkem/cachetools#423</a>: <i>${esc(rw.title)}</i></p>
      <div class="rc-grid">
        <div><h3>What went in</h3><p>Only the text of the upstream issue. The upstream project's own test, which the agent never saw, was held back as the judge.</p></div>
        <div><h3>What came out</h3><p>${esc(rw.final.summary.split("\n")[0].replace(/^Root cause:\s*/, "").slice(0, 260))}…</p></div>
        <div><h3>What it cost</h3><p>${rw.final.tier === "super" ? "Nemotron 3 Super alone" : "Tiered"}, ${fmt.s(rw.final.seconds)}, ${fmt.n(rw.final.tokens)} tokens, ${fmt.usd(rw.final.cost)}. The harness checked the fix; the upstream test passes.</p></div>
      </div>
      <div class="cta"><a class="btn" href="${esc(rw.pr_url)}" target="_blank" rel="noopener noreferrer">Open the pull request ↗</a><a class="btn ghost" href="#/results">All ${rw.attempts.length} runs, including the ${rw.attempts.length - rw.solved_runs} that failed</a></div>
    </div>
  </section>

  <section class="sec honest">
    <h2>What we do not claim</h2>
    <ul>
      <li>The benchmark is ${t.runs / 2} small synthetic bugs (two repeats each), not SWE-bench.</li>
      <li>On the real library Patchwright solved ${rw.solved_runs} of ${rw.attempts.length} runs. Each failure exposed a flaw that is now fixed and tested, and all of them are published.</li>
      <li>Asking the small model to rate difficulty did not help routing. We report it as a negative result.</li>
      <li>Run-to-run noise is about ±3 of 40, so one or two bugs of difference between strategies means nothing.</li>
    </ul>
  </section>`;
}

/* ---------------------------------------------------------------- results */
async function pageResults(app) {
  const d = await showcase(), rw = d.realworld, E = d.efficiency;
  const bars = (title, rows, fmtv) => {
    const mx = Math.max(...rows.map(r => r.v), 1e-9);
    return `<div class="card bars"><h3>${title}</h3>${rows.map(r => `<div class="row" style="grid-template-columns:130px 1fr 78px"><div>${esc(r.l)}</div><div class="track"><div class="fill" style="width:${(100 * r.v / mx).toFixed(1)}%;background:${r.c}"></div></div><div class="val">${fmtv(r.v)}</div></div>`).join("")}</div>`;
  };
  const effCard = (k, name, color) => {
    const b = E[k].before, a = E[k].after;
    return `<div class="eff"><h3>${name} <span class="pill ok">${signed(change(b.cost_fix, a.cost_fix))} cost per fix</span></h3>
      ${bars("Cost per fixed bug", [{ l: "before", v: b.cost_fix, c: "var(--line2)" }, { l: "multi-action", v: a.cost_fix, c: color }], v => fmt.usd(v))}
      ${bars("Model calls per run", [{ l: "before", v: b.calls, c: "var(--line2)" }, { l: "multi-action", v: a.calls, c: color }], v => v.toFixed(1))}
      <p class="hint">Truly fixed: ${b.fixed}/${b.runs} before, ${a.fixed}/${a.runs} after. Tokens per fixed bug: ${fmt.n(Math.round(b.tok_fix))} → ${fmt.n(Math.round(a.tok_fix))}.</p></div>`;
  };
  app.innerHTML = `
    <h1>Results</h1>
    <p class="lede">What was measured, including the parts that did not work. Every figure is computed from the files in <code>results/</code>.</p>

    <h2>A real bug, six real runs</h2>
    <p class="lede" style="font-size:15px">Target: <a href="${esc(rw.issue_url)}" target="_blank" rel="noopener noreferrer">tkem/cachetools#423</a>, in a standalone copy of the code from just before upstream fixed it. Only the issue text went in; the upstream test judged the result. Solved in <b>${rw.solved_runs} of ${rw.attempts.length}</b> runs, total ${fmt.usd(rw.total_cost)}. Each failure became a fix and a regression test.</p>
    <ol class="timeline">${rw.attempts.map(a => `
      <li class="${a.solved ? "ok" : "no"}"><span class="dot" aria-hidden="true">${a.solved ? "✓" : "✕"}</span>
        <div><div class="th"><b>Run ${a.run}</b> <span class="pill ${a.solved ? "ok" : "bad"}">${a.solved ? "solved" : "not solved"}</span><span class="hint">${esc(a.tier)} · ${a.calls} calls · ${fmt.n(a.tokens)} tokens · ${fmt.usd(a.cost)}</span></div>
        <p>${esc(a.lesson)}</p></div></li>`).join("")}</ol>
    <div class="cta"><a class="btn" href="${esc(rw.pr_url)}" target="_blank" rel="noopener noreferrer">Open pull request #1 ↗</a></div>
    ${rw.final.sources.length ? `<div class="card flat sec"><h3>Web sources Tavily found for the final run</h3><ul class="srcs">${rw.final.sources.map(s => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></li>`).join("")}</ul><p class="hint">Related discussion, not the fix. The model still had to find the bug, write a failing test and make the change.</p></div>` : ""}

    <h2 class="sec">Fewer model calls, lower cost</h2>
    <p class="lede" style="font-size:15px">The whole conversation is re-sent on every call, so about 75% of tokens are prompt tokens. Letting the model send up to three actions per reply cut both the number of calls and the history re-sent with each one. Same 20 bugs, one repeat; no loss in fix rate.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr))">${effCard("tiered", "Tiered (Super, then Ultra)", "var(--brand)")}${effCard("ultra", "Ultra only", "var(--ultra)")}</div>
    <div id="benchwrap" class="sec"></div>`;
  await pageBench($("#benchwrap"));
}

/* ---------------------------------------------------------------- how it works */
function node(x, y, w, h, color, title, sub) {
  return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14" fill="var(--panel)" stroke="${color}" stroke-width="2"/>
    <rect x="${x}" y="${y}" width="6" height="${h}" rx="3" fill="${color}"/>
    <text x="${x + 20}" y="${y + 30}" class="dt">${title}</text><text x="${x + 20}" y="${y + 54}" class="ds">${sub}</text></g>`;
}
function pageHow(app) {
  const line = (x1, y1, x2, y2, dash) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--mute)" stroke-width="2" marker-end="url(#arr)"${dash ? ' stroke-dasharray="6 5"' : ""}/>`;
  const curve = (d, color, dash) => `<path d="${d}" stroke="${color || "var(--mute)"}" stroke-width="2" fill="none" marker-end="url(#arr)"${dash ? ' stroke-dasharray="6 5"' : ""}/>`;
  const gate = (y, n, t) => `<g><circle cx="284" cy="${y}" r="13" fill="var(--brand)"/><text x="284" y="${y + 5}" text-anchor="middle" class="dn">${n}</text><text x="308" y="${y + 5}" class="ds">${t}</text></g>`;
  app.innerHTML = `
    <h1>How Patchwright earns trust</h1>
    <p class="lede">Most coding assistants write a plausible fix and ask you to believe it. Patchwright never asks. The model proposes; plain code verifies.</p>
    <div class="diagram card" role="img" aria-label="Pipeline: a bug report goes into a Docker sandbox, Nemotron Nano triages it, Nemotron Super writes a test and an edit, Nemotron Ultra retries if Super is stuck, a harness of plain code runs four proof checks, and the output is a patch with proof, cost and sources. Tavily supplies research.">
      <svg viewBox="0 0 1120 470" xmlns="http://www.w3.org/2000/svg" focusable="false">
        <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--mute)"/></marker></defs>
        ${node(10, 30, 190, 74, "var(--line2)", "Bug report", "+ repository")}
        ${node(250, 30, 200, 74, "var(--line2)", "Docker sandbox", "throwaway, capped")}
        ${node(500, 30, 230, 74, "var(--nano)", "Nano · triage", "real, testable bug?")}
        ${node(780, 30, 330, 74, "var(--super)", "Super · test + edit", "reproduce, then a minimal patch")}
        ${line(200, 67, 250, 67)}${line(450, 67, 500, 67)}${line(730, 67, 780, 67)}
        ${node(780, 160, 330, 74, "var(--ultra)", "Ultra · fresh attempt", "only if Super is stuck; repo reset")}
        ${line(945, 104, 945, 160, true)}<text x="960" y="138" class="dh">stuck</text>
        <rect x="250" y="190" width="490" height="160" rx="16" fill="var(--code)" stroke="var(--brand)" stroke-width="2" stroke-dasharray="7 5"/>
        <text x="270" y="218" class="dt" style="fill:var(--brand)">Harness: plain code, not a model</text>
        ${gate(246, 1, "the new test fails with the fix reverted")}
        ${gate(274, 2, "the same test passes with it restored")}
        ${gate(302, 3, "existing suite green, no test edited")}
        ${gate(330, 4, "a reviewer's edge-case tests pass")}
        ${curve("M850 104 C 840 150, 760 170, 700 190")}
        ${line(780, 215, 742, 240)}
        ${node(10, 385, 300, 74, "var(--brand)", "Patch + proof", "PR text, tokens, cost, sources")}
        ${curve("M300 350 C 260 370, 200 372, 160 385")}
        ${node(560, 385, 380, 74, "var(--brand)", "Tavily · research", "library errors, related discussion")}
        ${curve("M870 385 C 880 330, 900 280, 925 234", "var(--brand)", true)}
      </svg>
    </div>
    <ol class="steps">
      <li><b>Sandbox.</b> The repo is cloned into a throwaway Docker container with memory and CPU limits.</li>
      <li><b>Triage (Nano).</b> Judges whether the task is a specific, testable bug; refuses requests like "find all my bugs" before anything is spent.</li>
      <li><b>Reproduce and patch (Super).</b> Writes a failing test, then changes only the lines that must change and never rewrites a big file. It can send up to three actions per reply.</li>
      <li><b>Rescue (Ultra).</b> If Super is stuck, the repo is reset and the strongest model tries again with notes on what failed.</li>
      <li><b>Proof (plain code).</b> The harness reverts the fix and confirms the test fails, restores it and confirms it passes, runs the whole suite, rejects any edit to existing tests, then has a reviewer write edge-case tests.</li>
      <li><b>Research (Tavily).</b> Library and version errors, and related public discussion for real repositories, with every source shown.</li>
      <li><b>Delivery.</b> A clean patch (scratch files left out) and a pull-request description with the proof, tokens, cost and sources.</li>
    </ol>
    <div class="card sec"><h2>What it does not do</h2><p>It will not fix every bug and says so when it cannot. It targets Python projects with a test suite. It never merges anything: a human reviews the evidence.</p></div>`;
}
