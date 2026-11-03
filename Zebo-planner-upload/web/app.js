/* Norbae AI Annual Sales Planning Advisor — dashboard client.
 * Vanilla JS, no external libraries, no network access beyond this local server.
 * Every figure displayed is fetched from the local API, which calculates it from the CSVs.
 * All server-provided text is HTML-escaped before rendering.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const money = (v) => (v === null || v === undefined ? "n/a" : usd.format(v));
  const signedMoney = (v) => (v === null || v === undefined ? "n/a" : (v > 0 ? "+" : "") + usd.format(v));
  const pct = (v) => (v === null || v === undefined ? "n/a" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "%");
  const num = (v, d = 2) => (v === null || v === undefined ? "n/a" : Number(v).toFixed(d));
  const ruleName = (r) => String(r).replace(/_/g, " ");
  const fmtTime = (iso) => (iso ? String(iso).replace("T", " ").replace("+00:00", " UTC") : "—");

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable: ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.method = opts.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let res;
    try { res = await fetch(path, init); } catch (e) { throw new Error("Cannot reach the local server. Is `python server.py` still running?"); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) { const err = new Error((data && (data.detail || data.error)) || `Request failed (${res.status})`); err.data = data; throw err; }
    return data;
  }

  function showGlobalError(msg) {
    const el = $("#global-error");
    if (!msg) { el.hidden = true; el.textContent = ""; return; }
    el.hidden = false; el.innerHTML = `<strong>Something went wrong</strong>${esc(msg)}`;
  }

  // Minimal, safe Markdown renderer (escape first, then a small set of inline/block rules).
  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  }
  function md(text) {
    const lines = String(text || "").split("\n");
    let html = "", list = null, depth = 0, para = [];
    const flushPara = () => { if (para.length) { html += `<p>${para.map(inline).join("<br>")}</p>`; para = []; } };
    const closeLists = () => { while (depth > 0) { html += list === "ol" && depth === 1 ? "</li></ol>" : "</li></ul>"; depth--; } list = null; };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if (/^\s*$/.test(line)) { flushPara(); closeLists(); continue; }
      if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flushPara(); closeLists(); const n = m[1].length + 1; html += `<h${n}>${inline(m[2])}</h${n}>`; continue; }
      if (/^\|.*\|\s*$/.test(line)) {
        flushPara(); closeLists();
        const rows = [];
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        i--;
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r) => !/^\|\s*-/.test(r));
        html += "<table><thead><tr>" + cells(body[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
          body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
        continue;
      }
      if ((m = line.match(/^>\s?(.*)$/))) {
        flushPara(); closeLists();
        const q = [m[1]];
        while (i + 1 < lines.length && /^>/.test(lines[i + 1])) { q.push(lines[++i].replace(/^>\s?/, "")); }
        html += `<blockquote>${q.map(inline).join(" ")}</blockquote>`;
        continue;
      }
      if ((m = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/))) {
        flushPara();
        const level = m[1].length >= 2 ? 2 : 1;
        const type = /\d/.test(m[2]) ? "ol" : "ul";
        if (depth === 0) { list = type; html += `<${type}><li>`; depth = 1; }
        else if (level > depth) { html += "<ul><li>"; depth = 2; }
        else if (level < depth) { html += "</li></ul></li><li>"; depth = 1; }
        else { html += "</li><li>"; }
        html += inline(m[3]);
        continue;
      }
      if (depth > 0 && /^\s+\S/.test(line)) { html += " " + inline(line.trim()); continue; }
      closeLists();
      para.push(line);
    }
    flushPara(); closeLists();
    return html;
  }

  // ------------------------------------------------------------------ state
  const state = {
    meta: null,
    thresholds: store.get("norbae.thresholds", null),
    territories: [],
    selected: null,
    loaded: {},
  };
  const thrQuery = () => (state.thresholds ? "?" + new URLSearchParams(state.thresholds).toString() : "");

  // ------------------------------------------------------------------ tabs
  const loaders = {
    overview: loadOverview, exceptions: loadExceptions, advisor: initAdvisor, review: loadReview,
    eval: loadEvalState, architecture: loadArchitecture, casestudy: loadCaseStudy,
  };
  function selectTab(name, push = true) {
    if (!loaders[name]) name = "overview";
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
    if (push) history.replaceState(null, "", `#${name}`);
    showGlobalError(null);
    Promise.resolve(loaders[name]()).catch((e) => showGlobalError(e.message));
    window.scrollTo({ top: 0 });
  }
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));

  // ------------------------------------------------------------------ meta
  async function loadMeta() {
    state.meta = await api("/api/meta");
    $("#mode-badge").textContent = state.meta.llm.enabled ? `Optional local model: ${state.meta.llm.model}` : "No-LLM baseline · no model connected";
    $("#mode-badge").title = state.meta.mode_label;
    $("#max-calls").textContent = state.meta.max_tool_calls;
    if (!state.thresholds) state.thresholds = { ...state.meta.default_thresholds };
  }

  // ------------------------------------------------------------------ A. overview
  async function loadOverview() {
    const o = await api("/api/overview" + thrQuery());
    const s = o.summary;
    state.territories = o.territories;
    const gapCls = s.within_target ? "" : "warn";
    $("#kpis").innerHTML = [
      kpi("Company target", money(s.company_target_usd), `Sum of ${s.territory_count} territory targets`),
      kpi("Proposed quota total", money(s.proposed_quota_total_usd), "Sum of proposed quotas"),
      kpi("Quota minus target", signedMoney(s.quota_minus_target_usd), s.quota_minus_target_pct === null ? "" : `${pct(s.quota_minus_target_pct)} vs target`, gapCls),
      kpi("Territories", String(s.territory_count), "In data/quota_plan.csv"),
      kpi("Planning exceptions", String(o.exception_count), o.exception_ids.length ? o.exception_ids.join(", ") : "None flagged", o.exception_count ? "warn" : ""),
    ].join("");
    $("#overview-warning").innerHTML = s.within_target
      ? `<div class="alert alert-ok"><strong>Proposed quotas match the company target.</strong>No plan-level gap detected.</div>`
      : `<div class="alert alert-warn" role="status"><strong>Proposed quota total differs from the company target.</strong>${esc(s.interpretation)} Exceptions are review prompts, not conclusions.</div>`;
    renderChart(o.territories, o.thresholds.variance_pct);
    $("#chart-threshold-note").textContent = `Dashed lines: ±${o.thresholds.variance_pct}% review threshold`;
    $("#threshold-footnote").textContent = o.threshold_label;
    const v = state.meta.data_validation.files;
    $("#data-status").innerHTML = `<table class="data"><tbody>${Object.entries(v).map(([f, r]) =>
      `<tr><td><code>${esc(f)}</code></td><td>${r.ok ? `<span class="pill pill-ok">Valid</span> ${r.rows} rows` : `<span class="pill pill-crit">Error</span> ${esc(r.error)}`}</td></tr>`).join("")}</tbody></table>
      <p class="footnote">${o.consistency_findings} informational cross-file finding(s) — see <a href="#exceptions" data-goto="exceptions">Planning Exceptions</a>.</p>`;
    $("#overview-integrity").innerHTML = integrityHtml(o.integrity);
    state.loaded.overview = true;
  }
  function kpi(label, value, sub, cls = "") {
    return `<div class="kpi ${cls}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`;
  }
  function integrityHtml(i) {
    if (!i) return "";
    return i.unchanged_since_start
      ? `<div class="alert alert-ok"><strong>✓ Source CSVs unchanged since the app started</strong><span class="hint">SHA-256 fingerprints re-checked ${esc(fmtTime(i.checked_at))}. Draft requests and review decisions never modify planning data.</span></div>`
      : `<div class="alert alert-crit"><strong>Source CSVs changed since the app started</strong>The files were edited outside the app. Restart the server to re-baseline.</div>`;
  }
  function renderChart(rows, thr) {
    const W = 640, H = 240, padL = 44, padR = 10, padT = 18, padB = 34;
    const vals = rows.map((r) => r.variance_pct ?? 0);
    const max = Math.max(thr * 1.4, ...vals.map(Math.abs)) || 1;
    const y = (v) => padT + (H - padT - padB) * (1 - (v + max) / (2 * max));
    const bw = (W - padL - padR) / rows.length;
    let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
    const ticks = [-max, -max / 2, 0, max / 2, max].map((t) => Math.round(t * 10) / 10);
    ticks.forEach((t) => { svg += `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 6}" y="${y(t) + 4}" text-anchor="end">${t}%</text>`; });
    [thr, -thr].forEach((t) => { svg += `<line class="thr" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/>`; });
    rows.forEach((r, i) => {
      const v = r.variance_pct ?? 0, x = padL + i * bw + bw * 0.18, w = bw * 0.64;
      const top = Math.min(y(v), y(0)), h = Math.max(1, Math.abs(y(v) - y(0)));
      const flagged = r.flags.includes("quota_variance");
      svg += `<rect class="bar ${flagged ? "flag" : ""}" x="${x}" y="${top}" width="${w}" height="${h}" rx="2"><title>${esc(r.territory_id)} ${esc(r.territory_name)}: ${pct(v)}</title></rect>`;
      svg += `<text class="val" x="${x + w / 2}" y="${v >= 0 ? top - 4 : top + h + 11}" text-anchor="middle">${num(v, 1)}</text>`;
      svg += `<text x="${x + w / 2}" y="${H - 12}" text-anchor="middle">${esc(r.territory_id)}</text>`;
    });
    svg += `<line class="zero" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/></svg>`;
    $("#variance-chart").innerHTML = svg;
  }

  // ------------------------------------------------------------------ B. exceptions
  async function loadExceptions() {
    fillThresholdForm();
    const data = await api("/api/territories" + thrQuery());
    state.territories = data.territories;
    const regions = [...new Set(data.territories.map((t) => t.region))].sort();
    const rules = [...new Set(data.territories.flatMap((t) => t.rules.map((r) => r.rule_id)))];
    keepOptions("#f-region", regions.map((r) => [r, r]), "All regions");
    keepOptions("#f-rule", rules.map((r) => [r, ruleName(r)]), "Any rule");
    keepOptions("#detail-select", data.territories.map((t) => [t.territory_id, `${t.territory_id} — ${t.territory_name}${t.flags.length ? " ⚑" : ""}`]), null);
    renderExceptionTable();
    const c = data.consistency;
    $("#consistency").innerHTML = c.findings.length
      ? `<p class="hint">${c.finding_count} finding(s) across ${c.files_checked.map(esc).join(", ")}. ${esc(c.note)}</p>
         <div class="table-wrap"><table class="data"><thead><tr><th>Territory</th><th>Check</th><th>Detail</th></tr></thead><tbody>
         ${c.findings.map((f) => `<tr><td class="tid">${esc(f.territory_id)}</td><td>${esc(ruleName(f.check))}</td><td>${esc(f.detail)}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">No cross-file inconsistencies found.</p>`;
    const first = state.selected || (data.territories.find((t) => t.flags.length) || data.territories[0]).territory_id;
    $("#detail-select").value = first;
    await loadDetail(first);
  }
  function keepOptions(sel, pairs, allLabel) {
    const el = $(sel), cur = el.value;
    el.innerHTML = (allLabel ? `<option value="">${esc(allLabel)}</option>` : "") + pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    if ([...el.options].some((o) => o.value === cur)) el.value = cur;
  }
  function renderExceptionTable() {
    const scope = $("#f-scope").value, region = $("#f-region").value, rule = $("#f-rule").value, q = $("#f-search").value.trim().toLowerCase();
    const rows = state.territories.filter((t) =>
      (scope === "all" || t.flags.length) && (!region || t.region === region) && (!rule || t.flags.includes(rule)) &&
      (!q || t.territory_id.toLowerCase().includes(q) || t.territory_name.toLowerCase().includes(q)));
    $("#exceptions-table tbody").innerHTML = rows.map((t) => `
      <tr class="clickable ${t.territory_id === state.selected ? "selected" : ""}" data-tid="${esc(t.territory_id)}" tabindex="0" aria-label="Show detail for ${esc(t.territory_id)}">
        <td><span class="tid">${esc(t.territory_id)}</span><span class="tname">${esc(t.territory_name)} · ${esc(t.region)}</span></td>
        <td class="num">${money(t.company_target_usd)}</td><td class="num">${money(t.proposed_quota_usd)}</td>
        <td class="num ${t.variance_usd < 0 ? "neg-num" : ""}">${signedMoney(t.variance_usd)}</td>
        <td class="num ${t.variance_usd < 0 ? "neg-num" : ""}">${pct(t.variance_pct)}</td>
        <td class="num">${esc(t.rep_capacity)}</td><td class="num">${money(t.quota_per_rep_usd)}</td>
        <td class="num">${money(t.historical_revenue_per_rep_usd)}</td><td class="num">${num(t.opportunity_index)}</td>
        <td>${t.reasons.length ? `<ul class="reasons">${t.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : `<span class="muted">—</span>`}</td>
        <td>${t.review_required ? `<span class="pill pill-flag">Review required</span>` : `<span class="pill pill-neutral">No rule triggered</span>`}</td>
      </tr>`).join("");
    $("#exceptions-empty").hidden = rows.length > 0;
  }
  ["#f-scope", "#f-region", "#f-rule"].forEach((s) => $(s).addEventListener("change", renderExceptionTable));
  $("#f-search").addEventListener("input", renderExceptionTable);
  $("#exceptions-table tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-tid]"); if (tr) pickTerritory(tr.dataset.tid); });
  $("#exceptions-table tbody").addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-tid]"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pickTerritory(tr.dataset.tid); } });
  $("#detail-select").addEventListener("change", (e) => pickTerritory(e.target.value));
  function pickTerritory(tid) {
    $("#detail-select").value = tid;
    loadDetail(tid).then(() => $("#detail-card").scrollIntoView({ behavior: "smooth", block: "start" })).catch((e) => showGlobalError(e.message));
  }
  async function loadDetail(tid) {
    state.selected = tid;
    $$("#exceptions-table tbody tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.tid === tid));
    $("#territory-detail").innerHTML = `<div class="loading">Loading ${esc(tid)}…</div>`;
    const d = await api(`/api/territory/${encodeURIComponent(tid)}` + thrQuery());
    const ro = d.roster_summary, p = d.pipeline_sample;
    $("#territory-detail").innerHTML = `
      <div class="card-head"><h3>${esc(d.territory_id)} — ${esc(d.territory_name || "")} <span class="muted">${esc(d.region || "")}</span></h3>
        ${d.rule_triggered ? `<span class="pill pill-flag">Review required: ${d.flags.length} rule(s) triggered</span>` : `<span class="pill pill-neutral">No illustrative rule triggered</span>`}</div>
      <p>${esc(d.interpretation)}</p>
      <div class="detail-grid">
        <div><h3>Calculation logic</h3><ol class="steps">${d.calculation_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>
        <div><h3>Rule evaluation <span class="hint">(illustrative)</span></h3>
          <div class="table-wrap"><table class="data"><thead><tr><th>Rule</th><th>Threshold</th><th>Observed</th><th></th></tr></thead><tbody>
          ${d.rules.map((r) => `<tr><td>${esc(r.label)}${r.note ? `<span class="tname">${esc(r.note)}</span>` : ""}</td><td>${esc(r.threshold)}</td><td class="num">${esc(r.observed)}</td>
            <td>${r.triggered ? `<span class="pill pill-flag">Triggered</span>` : `<span class="pill pill-ok">Not triggered</span>`}</td></tr>`).join("")}</tbody></table></div></div>
        <div><h3>Source data</h3>${Object.entries(d.source_rows).map(([f, row]) => `<div class="hint"><code>${esc(f)}</code></div><pre class="src">${esc(JSON.stringify(row, null, 2))}</pre>`).join("")}</div>
        <div><h3>Roster <span class="hint">(aggregate only)</span></h3>${ro ? `<div class="kv">
            <span class="k">Active reps / planned capacity</span><span class="v">${ro.active_reps} / ${esc(ro.planned_capacity)}</span>
            <span class="k">Roster historical revenue</span><span class="v">${money(ro.roster_historical_revenue_usd)}</span>
            <span class="k">Territory historical revenue</span><span class="v">${money(ro.territory_historical_revenue_usd)}</span>
            <span class="k">Difference</span><span class="v">${signedMoney(ro.difference_usd)} (${pct(ro.difference_pct)})</span></div>
            <p class="footnote">${esc(ro.privacy_note)}</p>` : `<p class="muted">Unavailable.</p>`}
          <h3 class="mt">Pipeline sample</h3>${p ? `<div class="kv"><span class="k">Accounts</span><span class="v">${p.account_count}</span>
            <span class="k">Total opportunity</span><span class="v">${money(p.total_opportunity_usd)}</span>
            ${Object.entries(p.by_stage).map(([s, v]) => `<span class="k">${esc(s)}</span><span class="v">${v.count} · ${money(v.amount_usd)}</span>`).join("")}</div>
            <p class="footnote">${esc(p.caveat)}</p>` : `<p class="muted">Unavailable.</p>`}</div>
      </div>
      ${d.data_notes.length ? `<div class="alert alert-info mt"><strong>Data notes</strong><ul class="notes">${d.data_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
      <p class="footnote">${esc(d.caveat)} ${esc(d.threshold_label)}</p>
      <div class="draft-actions"><button class="btn" data-draft="${esc(d.territory_id)}">Draft a review request for ${esc(d.territory_id)}</button></div>`;
  }
  $("#territory-detail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-draft]");
    if (b) { state.reviewPreselect = b.dataset.draft; selectTab("review"); }
  });

  // thresholds
  function fillThresholdForm() {
    const f = $("#threshold-form");
    Object.entries(state.thresholds || {}).forEach(([k, v]) => { if (f.elements[k]) f.elements[k].value = v; });
  }
  $("#threshold-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, next = {};
    ["variance_pct", "quota_to_hist_per_rep_ratio", "opportunity_index_min", "opportunity_index_max", "reconciliation_pct"].forEach((k) => { next[k] = f.elements[k].value; });
    const prev = state.thresholds;
    state.thresholds = next;
    try {
      await loadExceptions();
      $("#threshold-error").textContent = "";
      store.set("norbae.thresholds", next);
      state.loaded.overview = false;
    } catch (err) {
      state.thresholds = prev;
      $("#threshold-error").textContent = err.message;
    }
  });
  $("#threshold-reset").addEventListener("click", async () => {
    state.thresholds = { ...state.meta.default_thresholds };
    store.del("norbae.thresholds");
    $("#threshold-error").textContent = "";
    await loadExceptions();
  });

  // ------------------------------------------------------------------ C. advisor
  function initAdvisor() {
    if (state.loaded.advisor) return;
    $("#advisor-mode-text").textContent = state.meta.mode_label + " " + state.meta.fallback_text;
    $("#examples").innerHTML = state.meta.example_questions.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
    state.loaded.advisor = true;
  }
  $("#examples").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (c) { $("#question").value = c.textContent; ask(); }
  });
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
  async function ask() {
    const q = $("#question").value.trim();
    if (!q) { $("#question").focus(); return; }
    const btn = $("#ask-btn");
    btn.disabled = true;
    const turn = document.createElement("div");
    turn.className = "turn";
    turn.innerHTML = `<div class="q">${esc(q)}</div><div class="a"><div class="loading">Routing question and calling tools…</div></div>`;
    $("#conversation").prepend(turn);
    try {
      const r = await api("/api/ask", { body: { question: q, thresholds: state.thresholds } });
      $(".a", turn).innerHTML = renderAnswer(r);
      $("#question").value = "";
    } catch (err) {
      $(".a", turn).innerHTML = `<div class="alert alert-crit"><strong>Couldn't answer</strong>${esc(err.message)}</div>`;
    } finally { btn.disabled = false; }
  }
  function renderAnswer(r) {
    const g = r.grounding || { figures_checked: 0, all_traceable: true, untraceable: [] };
    const gPill = g.figures_checked === 0 ? `<span class="pill pill-neutral">No figures to check</span>`
      : g.all_traceable ? `<span class="pill pill-ok">${g.figures_checked} figures checked · all traceable to tool output</span>`
        : `<span class="pill pill-crit">Untraceable figures: ${esc(g.untraceable.join(", "))}</span>`;
    const trace = r.tool_calls.length ? `<details class="trace"><summary>Tool trace (${r.tool_calls.length} call${r.tool_calls.length > 1 ? "s" : ""})</summary>
        <table class="data"><thead><tr><th>#</th><th>Tool</th><th>Arguments</th><th>Status</th><th>Time (UTC)</th></tr></thead><tbody>
        ${r.tool_calls.map((c, i) => `<tr><td>${i + 1}</td><td><code>${esc(c.tool)}</code></td><td><code>${esc(JSON.stringify(c.args))}</code></td>
          <td>${c.status === "ok" ? `<span class="pill pill-ok">ok</span>` : `<span class="pill pill-warn">${esc(c.status)}</span>`}</td><td>${esc(fmtTime(c.timestamp))}</td></tr>`).join("")}
        </tbody></table></details>` : `<p class="footnote">No tools were called for this answer.</p>`;
    const draft = r.draft && r.draft.status === "PENDING_HUMAN_APPROVAL"
      ? `<div class="draft-actions"><span class="pill pill-warn">${esc(r.draft.status)}</span><span class="pill pill-neutral">executed: false</span>
          <button class="btn btn-small" data-save-draft="${esc(r.draft.request_id)}">Save to review queue</button></div>` : "";
    return `<div class="a-meta"><span class="pill pill-accent">${r.mode === "optional_llm" ? "Optional local model" : "No-LLM baseline"}</span>
        <span class="pill pill-neutral">intent: ${esc(ruleName(r.intent))}</span>${gPill}</div>
      ${r.llm_note ? `<div class="alert alert-info">${esc(r.llm_note)}</div>` : ""}
      <div class="md">${md(r.answer)}</div>${draft}${trace}`;
  }
  $("#conversation").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-save-draft]");
    if (!b) return;
    b.disabled = true;
    try {
      const r = await api("/api/requests/save-draft", { body: { request_id: b.dataset.saveDraft } });
      b.outerHTML = `<span class="hint">Saved to the local demo queue as <strong>${esc(r.request.request_id)}</strong> — still pending human approval, not applied. <a href="#review" data-goto="review">Open Scenario Review</a></span>`;
      state.loaded.review = false;
    } catch (err) { b.disabled = false; b.insertAdjacentHTML("afterend", `<span class="form-error"> ${esc(err.message)}</span>`); }
  });

  // ------------------------------------------------------------------ D. review
  async function loadReview() {
    if (!state.territories.length) { const o = await api("/api/overview" + thrQuery()); state.territories = o.territories; }
    const sel = $("#cr-territory");
    const cur = state.reviewPreselect || sel.value || state.territories[0].territory_id;
    sel.innerHTML = state.territories.map((t) => `<option value="${esc(t.territory_id)}">${esc(t.territory_id)} — ${esc(t.territory_name)}</option>`).join("");
    sel.value = cur;
    state.reviewPreselect = null;
    await Promise.all([loadCrTerritory(), loadQueue()]);
  }
  async function loadCrTerritory() {
    const tid = $("#cr-territory").value;
    $("#cr-preview-out").innerHTML = "";
    const d = await api(`/api/territory/${encodeURIComponent(tid)}` + thrQuery());
    $("#cr-current").innerHTML = `<span class="k">Current proposed quota</span><span class="v">${money(d.proposed_quota_usd)}</span>
      <span class="k">Territory target</span><span class="v">${money(d.company_target_usd)}</span>
      <span class="k">Variance</span><span class="v">${signedMoney(d.variance_usd)} (${pct(d.variance_pct)})</span>
      <span class="k">Rules triggered</span><span class="v">${d.flags.length ? d.flags.map(ruleName).map(esc).join(", ") : "none"}</span>`;
    $("#cr-evidence").innerHTML = d.draft_evidence.map((x) => `<li>${esc(x)}</li>`).join("");
  }
  $("#cr-territory").addEventListener("change", () => loadCrTerritory().catch((e) => showGlobalError(e.message)));
  $("#cr-preview").addEventListener("click", async () => {
    const alt = $("#cr-alt").value;
    const out = $("#cr-preview-out");
    if (!alt) { out.innerHTML = `<p class="form-error">Enter a suggested alternative quota to preview.</p>`; return; }
    try {
      const p = await api("/api/scenario", { body: { territory_id: $("#cr-territory").value, alternative_quota_usd: alt, thresholds: state.thresholds } });
      out.innerHTML = `<div class="alert alert-info"><strong>Hypothetical — not applied</strong>
        <div class="kv"><span class="k">Territory change</span><span class="v">${signedMoney(p.territory_change_usd)}</span>
        <span class="k">Variance vs target</span><span class="v">${pct(p.alternative_variance_pct)}</span>
        <span class="k">Rules triggered</span><span class="v">${p.flags_under_alternative.length ? p.flags_under_alternative.map(ruleName).map(esc).join(", ") : "none"}</span>
        <span class="k">Plan total</span><span class="v">${money(p.plan_total_current_usd)} → ${money(p.plan_total_under_alternative_usd)}</span>
        <span class="k">Plan gap vs target</span><span class="v">${signedMoney(p.plan_gap_current_usd)} → ${signedMoney(p.plan_gap_under_alternative_usd)}</span></div>
        <span class="hint">${esc(p.note)}</span></div>`;
    } catch (err) { out.innerHTML = `<p class="form-error">${esc(err.message)}</p>`; }
  });
  $("#cr-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#cr-error");
    err.textContent = "";
    if (!f.elements.rationale.value.trim()) { err.textContent = "A business rationale is required."; f.elements.rationale.focus(); return; }
    const body = {
      territory_id: f.elements.territory_id.value, suggested_quota_usd: f.elements.suggested_quota_usd.value || null,
      rationale: f.elements.rationale.value, assumptions: f.elements.assumptions.value,
      additional_evidence: f.elements.additional_evidence.value, reviewer: f.elements.reviewer.value, thresholds: state.thresholds,
    };
    const btn = $("button[type=submit]", f);
    btn.disabled = true;
    try {
      await api("/api/requests", { body });
      ["suggested_quota_usd", "rationale", "assumptions", "additional_evidence"].forEach((k) => { f.elements[k].value = ""; });
      $("#cr-preview-out").innerHTML = "";
      await loadQueue();
    } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
  });
  const statusPill = (s) => s === "PENDING_HUMAN_APPROVAL" ? `<span class="pill pill-warn">PENDING HUMAN APPROVAL</span>`
    : s === "REVIEWED_IN_DEMO" ? `<span class="pill pill-ok">REVIEWED (demo record)</span>` : `<span class="pill pill-crit">RETURNED FOR REVISION</span>`;
  async function loadQueue() {
    const r = await api("/api/requests");
    $("#review-integrity").innerHTML = integrityHtml(r.integrity);
    $("#queue-count").textContent = `${r.requests.length} draft(s) · stored locally in local_state/`;
    $("#queue").innerHTML = r.requests.length ? r.requests.map((q) => `
      <div class="req-card" data-rid="${esc(q.request_id)}">
        <div class="req-head"><strong>${esc(q.territory_id)}</strong><span class="id">${esc(q.request_id)}</span>${statusPill(q.status)}</div>
        <div class="not-applied">Not applied · executed: ${String(q.executed)} · applied to source data: ${String(q.applied_to_source_data)} · simulation</div>
        <div class="kv"><span class="k">Current proposed quota</span><span class="v">${money(q.current_proposed_quota_usd)}</span>
          <span class="k">Suggested alternative</span><span class="v">${q.suggested_quota_usd ? money(q.suggested_quota_usd) : "none"}</span>
          <span class="k">Created</span><span class="v">${esc(fmtTime(q.created_at))}</span>
          <span class="k">Reviewer</span><span class="v">${esc(q.reviewer || "not specified")}</span></div>
        <div><strong>Rationale:</strong> ${esc(q.rationale)}</div>
        ${q.assumptions.length ? `<div class="mt"><strong>Assumptions</strong><ul>${q.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}
        ${q.evidence.length ? `<details><summary>Evidence (${q.evidence.length})</summary><ul>${q.evidence.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>` : ""}
        ${(q.review_history || []).map((h) => `<div class="history">${esc(fmtTime(h.timestamp))} · ${esc(ruleName(h.action))} by ${esc(h.reviewer)} — “${esc(h.note)}” (demo record only)</div>`).join("")}
        ${q.status === "PENDING_HUMAN_APPROVAL" ? `<form class="review-form">
            <input name="reviewer" maxlength="80" placeholder="Reviewer name / role" aria-label="Reviewer name or role">
            <span class="hint">Demo-only: updates this local record; never changes the plan.</span>
            <textarea name="note" rows="2" maxlength="500" placeholder="Audit note (required)" aria-label="Audit note"></textarea>
            <div class="btns"><button type="button" class="btn btn-small" data-act="mark_reviewed">Mark reviewed</button>
            <button type="button" class="btn btn-small" data-act="return_for_revision">Return for revision</button>
            <span class="form-error" role="alert"></span></div></form>` : ""}
      </div>`).join("") : `<div class="empty">No draft requests yet. Create one with the form, or ask the advisor to draft one.</div>`;
  }
  $("#queue").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const card = b.closest("[data-rid]"), form = b.closest("form"), errEl = $(".form-error", form);
    const note = form.elements.note.value.trim();
    if (!note) { errEl.textContent = "An audit note is required."; form.elements.note.focus(); return; }
    b.disabled = true;
    try {
      await api(`/api/requests/${encodeURIComponent(card.dataset.rid)}/review`, { body: { action: b.dataset.act, note, reviewer: form.elements.reviewer.value } });
      await loadQueue();
    } catch (ex) { errEl.textContent = ex.message; b.disabled = false; }
  });

  // ------------------------------------------------------------------ E. evaluation lab
  async function loadEvalState() {
    const r = await api("/api/evaluations");
    if (r.last_run) renderEval(r.last_run, true);
    if (r.last_test_run) renderTests(r.last_test_run);
  }
  $("#run-evals").addEventListener("click", async () => {
    const b = $("#run-evals"); b.disabled = true; $("#eval-status").textContent = "Running automated checks…";
    try { renderEval(await api("/api/evaluations/run", { body: {} }), false); }
    catch (e) { $("#eval-status").textContent = e.message; } finally { b.disabled = false; }
  });
  $("#run-tests").addEventListener("click", async () => {
    const b = $("#run-tests"); b.disabled = true; $("#eval-status").textContent = "Running unit test suite (this can take ~10–30 s)…";
    try { const r = await api("/api/tests/run", { body: {} }); renderTests(r); $("#eval-status").textContent = `Unit tests finished: ${r.status_line}`; }
    catch (e) { $("#eval-status").textContent = e.message; } finally { b.disabled = false; }
  });
  function renderEval(run, fromHistory) {
    const s = run.summary;
    $("#eval-status").textContent = `${fromHistory ? "Last recorded run" : "Run completed"} at ${fmtTime(s.run_at)} (${s.duration_ms} ms).`;
    $("#eval-summary").innerHTML = [
      kpi("Automated passed", `${s.automated_passed} / ${s.automated_total}`, "Actually executed this run"),
      kpi("Automated failed", String(s.automated_failed), "Failures or errors", s.automated_failed ? "warn" : ""),
      kpi("Manual pending", String(s.manual_pending), "Manual evaluation required", s.manual_pending ? "warn" : ""),
      kpi("Manual recorded", String(s.manual_recorded), "Human-recorded results"),
    ].join("");
    $("#eval-table tbody").innerHTML = run.results.map((r) => {
      const pill = r.status === "Passed" ? "pill-ok" : r.status === "Manual evaluation required" ? "pill-neutral" : r.status === "Manual: Pass" ? "pill-ok" : "pill-crit";
      const sev = r.severity === "Critical" ? "pill-crit" : r.severity === "High" ? "pill-warn" : "pill-neutral";
      const manual = r.evaluation_type === "manual" ? `<details><summary class="hint">Record result</summary>
          <form class="manual-form" data-tc="${esc(r.test_id)}">
            <label class="hint" for="o-${esc(r.test_id)}">Outcome</label><select id="o-${esc(r.test_id)}" name="outcome"><option>Pass</option><option>Fail</option></select>
            <label class="hint" for="r-${esc(r.test_id)}">Reviewer</label><input id="r-${esc(r.test_id)}" name="reviewer" maxlength="80" required>
            <textarea class="full" name="notes" rows="2" maxlength="500" placeholder="What was tested, which model/prompt, evidence"></textarea>
            <button class="btn btn-small full" type="submit">Save manual result</button><span class="form-error full"></span></form></details>` : "";
      return `<tr><td class="tid">${esc(r.test_id)}</td><td>${esc(r.category)}<span class="tname">${esc(r.component)} · ${esc(r.evaluation_type)}</span></td>
        <td>${esc(r.test)}</td><td>${esc(r.expected_result)}</td><td>${esc(r.actual)}</td>
        <td><span class="pill ${pill}">${esc(r.status)}</span>${manual}</td><td><span class="pill ${sev}">${esc(r.severity)}</span></td><td class="hint">${esc(fmtTime(r.ran_at))}</td></tr>`;
    }).join("");
  }
  $("#eval-table").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $(".form-error", f);
    try {
      await api("/api/evaluations/manual", { body: { test_id: f.dataset.tc, outcome: f.elements.outcome.value, reviewer: f.elements.reviewer.value, notes: f.elements.notes.value } });
      renderEval(await api("/api/evaluations/run", { body: {} }), false);
    } catch (ex) { err.textContent = ex.message; }
  });
  function renderTests(r) {
    $("#test-output-card").hidden = false;
    $("#test-output").innerHTML = `<p>${r.ok ? `<span class="pill pill-ok">All passing</span>` : `<span class="pill pill-crit">Failures</span>`}
      Ran ${r.total} tests at ${esc(fmtTime(r.run_at))}: ${r.passed} passed, ${r.failed} failed, ${r.errors} errors, ${r.skipped} skipped.</p>
      ${r.output_tail ? `<pre class="out">${esc(r.output_tail)}</pre>` : ""}`;
  }

  // ------------------------------------------------------------------ F. architecture
  async function loadArchitecture() {
    $("#prohibited").innerHTML = state.meta.prohibited_actions.map((p) => `<li>${esc(p.charAt(0).toUpperCase() + p.slice(1))}</li>`).join("");
    $("#tools-table tbody").innerHTML = state.meta.tools.map((t) => `<tr><td><code>${esc(t.name)}</code></td>
      <td>${t.kind === "read_only" ? `<span class="pill pill-ok">read-only</span>` : `<span class="pill pill-warn">draft-only · simulation</span>`}</td>
      <td>${esc(t.purpose)}</td><td>${Object.keys(t.args).length ? Object.entries(t.args).map(([k, a]) => `<code>${esc(k)}</code> <span class="hint">${esc(a.type)}${a.required ? "" : ", optional"}</span>`).join("<br>") : `<span class="muted">none</span>`}</td></tr>`).join("");
    await loadAudit();
  }
  async function loadAudit() {
    const r = await api("/api/audit");
    $("#audit-table tbody").innerHTML = r.entries.length ? r.entries.slice(0, 60).map((e) => {
      const { timestamp, event, ...rest } = e;
      return `<tr><td class="hint">${esc(fmtTime(timestamp))}</td><td><code>${esc(event)}</code></td><td><code>${esc(JSON.stringify(rest))}</code></td></tr>`;
    }).join("") : `<tr><td colspan="3" class="muted">No audit entries yet. Ask the advisor a question or create a draft.</td></tr>`;
  }
  $("#refresh-audit").addEventListener("click", () => loadAudit().catch((e) => showGlobalError(e.message)));

  // ------------------------------------------------------------------ G. case study
  async function loadCaseStudy() {
    const r = await api("/api/case-study");
    $("#case-study").innerHTML = md(r.markdown);
  }

  // cross-links
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-goto]");
    if (a) { e.preventDefault(); selectTab(a.dataset.goto); }
  });

  // ------------------------------------------------------------------ boot
  (async function boot() {
    try {
      await loadMeta();
      selectTab((location.hash || "#overview").slice(1), false);
    } catch (e) {
      showGlobalError(e.message);
      $("#kpis").innerHTML = `<div class="empty">Could not load plan data. ${esc(e.message)}</div>`;
    }
  })();
})();
