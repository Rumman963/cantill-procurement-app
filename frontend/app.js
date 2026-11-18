const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const state = { user: store.get("user", ""), chatSession: null, chatLog: [], run: null, sel: null, filter: "todo" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, dp) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const inr = (v) => (v === null || v === undefined || v === "") ? "—" : "₹" + fmt(v, 2);
const inr0 = (v) => (v === null || v === undefined || v === "") ? "—" : "₹" + fmt(Math.round(v), 0);
const when = (iso) => { const d = new Date(iso.endsWith("Z") ? iso : iso + "Z"); return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); };
const PO_ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO"];
const routeCls = (r) => PO_ROUTES.includes(r) ? "PO" : (r === "NOT_PROCESSED" ? "NP" : "SRC");
const routeBadge = (r) => `<span class="route ${routeCls(r)}">${esc(String(r).replace(/_/g, " "))}</span>`;
const table = (head, rows, cls = "") => `<table class="${cls}"><thead><tr>${head.map((h) => {
  const [t, c] = Array.isArray(h) ? h : [h, ""]; return `<th class="${c}">${t}</th>`; }).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
const pagehead = (title, sub, actions = "") => `<div class="pagehead"><div class="grow"><h1>${title}</h1><p class="sub">${sub}</p></div>${actions}</div>`;

function drop(id, accept, multiple, title) {
  return `<label class="drop" id="${id}Drop"><input type="file" id="${id}" accept="${accept}" ${multiple ? "multiple" : ""}>
    <span class="ico">⇪</span><span><span class="t">${title}</span><br><span class="s" id="${id}Name">Click to choose${multiple ? " (you can select several)" : ""} or drop here</span></span></label>`;
}
function wireDrop(id) {
  const inp = $("#" + id), box = $("#" + id + "Drop"), name = $("#" + id + "Name");
  const show = () => { const n = inp.files.length; name.textContent = n ? (n === 1 ? inp.files[0].name : `${n} files selected`) : "Click to choose or drop here"; };
  inp.addEventListener("change", show);
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("over"); });
  box.addEventListener("dragleave", () => box.classList.remove("over"));
  box.addEventListener("drop", (e) => { e.preventDefault(); box.classList.remove("over"); inp.files = e.dataTransfer.files; show(); });
}

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 4500);
}

async function api(path, opts = {}) {
  const headers = { "X-User": state.user || "anonymous", ...(opts.headers || {}) };
  let body = opts.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") { body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, { ...opts, headers, body });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    let d = data && data.detail !== undefined ? data.detail : data;
    if (d && d.errors) d = d.errors.join("; ");
    if (d && d.error) d = typeof d.error === "string" ? d.error : JSON.stringify(d.error);
    throw new Error(typeof d === "string" ? d : JSON.stringify(d));
  }
  return data;
}

function needUser() {
  if (!state.user) { toast("Please enter your name (top right) — every action is recorded with who did it.", true); $("#userName").focus(); return false; }
  return true;
}

// router
const routes = { chat: renderChat, workspace: renderWorkspace, batch: renderBatch, policy: renderPolicy, contracts: renderContracts, suppliers: renderSuppliers, data: renderData, audit: renderAudit };
async function route() {
  const name = (location.hash.replace("#/", "").split("?")[0]) || "chat";
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  view.innerHTML = `<div class="empty">Loading…</div>`;
  try { await (routes[name] || renderChat)(); } catch (e) { view.innerHTML = `<div class="card">Error: ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
$("#userName").value = state.user;
$("#userName").addEventListener("change", (e) => { state.user = e.target.value.trim(); store.set("user", state.user); if (state.user) toast(`Acting as ${state.user}`); });

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    $("#policyBadge").textContent = `Policy v${h.current_policy ?? "?"} in force`;
    state.health = h;
    return h;
  } catch { return null; }
}

// chat
async function renderChat() {
  const h = await refreshHealth();
  view.innerHTML = pagehead("Raise a purchase request", "Tell the assistant what you need in plain words. It matches the item master, asks for anything missing, and confirms before creating the PR.") + `
    <div class="split">
      <div class="card chat">
        <div class="msgs" id="msgs"></div>
        <div class="composer"><input id="chatIn" placeholder='e.g. need 200 nitrile gloves for Chakan by next Friday' autocomplete="off"><button class="btn primary" id="chatSend">Send</button></div>
      </div>
      <div>
        <div class="card flush"><div class="cardhead"><h2>My recent requests</h2></div><div id="myPrs" class="tablewrap short"></div></div>
        <div class="card"><h3>Tips</h3><div class="small muted" style="line-height:1.6">Mention the <b>item</b>, <b>quantity</b>, <b>plant</b> (Bhosari / PUN1 or Chakan / CHK1), <b>need-by date</b>, and whether it is an <b>emergency</b>. Type <code>start over</code> at any time.</div></div>
      </div>
    </div>`;
  if (h && !h.data.ready) { $("#msgs").innerHTML = `<div class="msg bot">Master data is not loaded yet. Ask an admin to upload it on the <a href="#/data">Data</a> page.</div>`; }
  if (!state.chatLog.length) state.chatLog.push({ role: "bot", text: "Hi! What do you need to buy? For example: \"need 200 nitrile gloves for Chakan by next Friday\"." });
  drawChat();
  const send = () => { const v = $("#chatIn").value.trim(); if (v) { $("#chatIn").value = ""; chatSend(v); } };
  $("#chatSend").onclick = send;
  $("#chatIn").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  $("#chatIn").focus();
  loadMyPrs();
}

function drawChat() {
  const box = $("#msgs"); if (!box) return;
  box.innerHTML = state.chatLog.map((m, i) => {
    let html = `<div class="msg ${m.role}">${esc(m.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")}`;
    if (m.summary) {
      const s = m.summary;
      html += `<div class="summary kv small"><div>Item</div><div><b>${esc(s.description)}</b> <span class="tag">${esc(s.item_code)}</span></div>
        <div>Quantity</div><div>${esc(s.quantity)} ${esc(s.uom)}</div><div>Plant</div><div>${esc(s.plant)} — ${esc(s.plant_name)}</div>
        <div>Need by</div><div>${esc(s.need_by_date)}</div><div>Urgency</div><div>${s.urgency === "Emergency" ? '<span class="tag bad">Emergency</span>' : esc(s.urgency)}</div>
        ${s.item_code === "NEW" ? `<div>Est. unit price</div><div>${inr(s.estimated_unit_price)}</div>` : ""}</div>`;
    }
    if (m.options && m.options.length && i === state.chatLog.length - 1) {
      html += `<div class="opts">${m.options.map((o) => `<button data-v="${esc(o.value)}">${esc(o.label)}</button>`).join("")}</div>`;
    }
    return html + "</div>";
  }).join("");
  $$(".opts button", box).forEach((b) => (b.onclick = () => chatSend(b.dataset.v, b.textContent)));
  box.scrollTop = box.scrollHeight;
}

async function chatSend(value, label) {
  if (!needUser()) return;
  state.chatLog.push({ role: "user", text: label || value }); drawChat();
  try {
    const r = await api("/api/chat", { method: "POST", body: { session_id: state.chatSession, message: value } });
    state.chatSession = r.session_id;
    state.chatLog.push({ role: "bot", text: r.text, options: r.options, summary: r.summary });
    if (r.created) loadMyPrs();
  } catch (e) { state.chatLog.push({ role: "bot", text: "Sorry: " + e.message }); }
  drawChat();
}

async function loadMyPrs() {
  const prs = await api("/api/prs");
  const mine = prs.filter((p) => !state.user || p.requester === state.user).slice(0, 8);
  const el = $("#myPrs"); if (!el) return;
  el.innerHTML = mine.length ? table(["PR", "Item", ["Qty", "num"], "Need by"], mine.map((p) =>
    `<tr><td class="nowrap mono">${esc(p.pr_no)}</td><td>${esc(p.description)}</td><td class="num">${esc(p.quantity)} ${esc(p.uom)}</td><td class="nowrap">${esc(p.need_by_date)}</td></tr>`)) : `<div class="empty small">No requests yet.</div>`;
}

// workspace
async function renderWorkspace() {
  await refreshHealth();
  const runs = await api("/api/runs");
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const rid = Number(q.get("run")) || (state.run && state.run.id) || (runs[0] && runs[0].id);
  const actions = `<div class="row">
      ${runs.length ? `<select id="runSel" title="Choose a run">${runs.map((r) => `<option value="${r.id}" ${r.id === rid ? "selected" : ""}>Run #${r.id} · ${esc(r.label)} · ${esc(r.policy_mode)}</option>`).join("")}</select>` : ""}
      <button class="btn" id="procChat">Process open chat requests</button>
      ${rid ? `<a class="btn" href="/api/runs/${rid}/export.csv">⭳ Export CSV</a>` : ""}</div>`;
  view.innerHTML = pagehead("Buyer workspace", "Each requirement is one or more clubbed PR lines. Review the decision and why, then approve or override.", actions) + `<div id="ws"></div>`;
  $("#procChat").onclick = async () => { if (!needUser()) return; try { const r = await api("/api/prs/process", { method: "POST" }); state.run = null; location.hash = `#/workspace?run=${r.run_id}`; } catch (e) { toast(e.message, true); } };
  if ($("#runSel")) $("#runSel").onchange = (e) => { location.hash = `#/workspace?run=${e.target.value}`; };
  if (!rid) { $("#ws").innerHTML = `<div class="card empty">No decisions yet. Upload a PR batch on <a href="#/batch">Batch &amp; API</a>, or raise requests in chat and click “Process open chat requests”.</div>`; return; }
  if (!state.run || state.run.id !== rid) { state.run = await api(`/api/runs/${rid}`); state.sel = null; }
  drawWorkspace();
}

function reqStatus(g) { return (state.run.actions[g] || []).slice(-1)[0]; }

function drawWorkspace() {
  const run = state.run, reqs = run.result.requirements, np = run.result.not_processed;
  const c = { todo: 0, done: 0, po: 0, src: 0, all: reqs.length, np: np.length };
  reqs.forEach((r) => { reqStatus(r.group_id) ? c.done++ : c.todo++; r.decision === "PO" ? c.po++ : c.src++; });
  const f = state.filter;
  let list = reqs.filter((r) => f === "all" || (f === "todo" && !reqStatus(r.group_id)) || (f === "done" && reqStatus(r.group_id)) || (f === "po" && r.decision === "PO") || (f === "src" && r.decision !== "PO"));
  const search = (state.search || "").toLowerCase();
  if (search) list = list.filter((r) => JSON.stringify([r.group_id, r.item_code, r.item_description, r.plant, r.route, r.supplier_id, r.po_id, r.lines.map((l) => l.pr_no)]).toLowerCase().includes(search));
  $("#ws").innerHTML = `
    <div class="stats">${[["Requirements", reqs.length], ["Awaiting action", c.todo], ["Purchase orders", run.result.purchase_orders.length], ["Sourcing requests", c.src], ["Lines not processed", np.length]].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")}</div>
    <div class="split">
      <div class="card flush">
        <div class="cardhead"><div class="tabs">${[["todo", "Awaiting"], ["done", "Actioned"], ["po", "POs"], ["src", "Sourcing"], ["all", "All"], ["np", "Not processed"]].map(([k, l]) => `<button data-f="${k}" class="${f === k ? "on" : ""}">${l}<span class="n">${c[k]}</span></button>`).join("")}</div><div class="spacer"></div><input id="wsSearch" placeholder="Search item, PR, supplier…" value="${esc(state.search || "")}" style="width:220px"></div>
        <div class="tablewrap">${f === "np" ? npTable(np) : reqTable(list)}</div>
      </div>
      <div id="detail" class="sticky">${state.sel ? "" : `<div class="card empty">Select a requirement to see the decision and act on it.</div>`}</div>
    </div>`;
  $$(".tabs button").forEach((b) => (b.onclick = () => { state.filter = b.dataset.f; drawWorkspace(); }));
  $("#wsSearch").oninput = (e) => { state.search = e.target.value; clearTimeout(state._st); state._st = setTimeout(() => { drawWorkspace(); const s = $("#wsSearch"); s.focus(); s.setSelectionRange(999, 999); }, 250); };
  $$("tr.click").forEach((tr) => (tr.onclick = () => { state.sel = tr.dataset.g; drawWorkspace(); }));
  if (state.sel) drawDetail();
}

function npTable(np) {
  if (!np.length) return `<div class="empty">All lines were processed.</div>`;
  return table(["PR / line", "Reason"], np.map((n) => `<tr><td class="nowrap mono">${esc(n.pr_no)}/${esc(n.pr_line)}</td><td>${esc(n.reason)}</td></tr>`));
}

function reqTable(list) {
  if (!list.length) return `<div class="empty">Nothing here.</div>`;
  return table(["Requirement", "Item", ["Value", "num"], "Decision", "Supplier"], list.map((r) => {
    const a = reqStatus(r.group_id);
    const st = a ? `<span class="status ${a.action}">${a.action === "approve" ? "✓ Approved" : "✎ Overridden"}</span>` : `<span class="small muted">Awaiting</span>`;
    return `<tr class="click ${state.sel === r.group_id ? "sel" : ""}" data-g="${r.group_id}">
      <td class="nowrap"><span class="strong">${esc(r.group_id)}</span><div>${st}</div></td>
      <td>${esc(r.item_description)}${r.is_emergency ? ' <span class="tag bad">Emergency</span>' : ""}<div class="small muted">${esc(r.item_code)} · ${esc(r.plant)} · ${esc(r.created_at)} · ${r.lines.length} line${r.lines.length > 1 ? "s" : ""}</div></td>
      <td class="num">${inr0(r.value)}</td>
      <td class="nowrap">${routeBadge(r.route)}<div class="small muted mono">${esc(a ? a.document_no : r.po_id)}</div></td>
      <td>${r.supplier_id ? `<span class="nowrap">${esc(r.supplier_id)}</span><div class="small muted nowrap">@ ${inr(r.unit_price)}</div>` : `<span class="small">${esc((r.suggested_supplier_ids || []).join(", ")) || "—"}</span>`}</td></tr>`;
  }));
}

function drawDetail() {
  const r = state.run.result.requirements.find((x) => x.group_id === state.sel);
  if (!r) return;
  const acts = state.run.actions[r.group_id] || [];
  const last = acts.slice(-1)[0];
  const isPO = r.decision === "PO";
  const what = isPO ? `Create ${r.po_id} to ${r.supplier_id}` : (r.route === "NEW_ITEM_SOURCING" ? "Start new-item sourcing" : `Create ${r.route === "CAPEX_SOURCING" ? "capex sourcing request" : r.route}`);
  $("#detail").innerHTML = `
    <div class="card">
      <div class="row" style="align-items:flex-start"><div style="flex:1"><h2>${esc(r.group_id)} · ${esc(r.item_description)}</h2>
        <div class="small muted" style="margin-top:4px">${esc(r.item_code)} · ${esc(r.plant)} · requirement date ${esc(r.requirement_date)} · ${r.total_quantity} ${esc(r.uom)} ${r.is_emergency ? '<span class="tag bad">Emergency</span>' : ""}</div></div>${routeBadge(r.route)}</div>
      <div class="kv" style="margin:14px 0">
        <div>Value</div><div class="strong">${inr(r.value)}</div>
        ${isPO ? `<div>Supplier</div><div>${esc(r.supplier_id)} ${esc(r.supplier_name)} @ ${inr(r.unit_price)}</div><div>PO</div><div class="mono">${esc(r.po_id)}</div>` : `<div>Suggested</div><div>${(r.suggested_supplier_ids || []).map((s) => `<span class="tag blue">${esc(s)}</span>`).join("") || "Buyer to find suppliers"}</div>`}
      </div>
      ${last ? `<div class="done ${last.action}"><b>${last.action === "approve" ? "✓ Approved" : "✎ Overridden"}</b> by ${esc(last.actor)} · ${when(last.at)} → <b class="mono">${esc(last.document_no)}</b>${last.comment ? `<div class="small" style="margin-top:4px">Reason: ${esc(last.comment)}</div>` : ""}</div>` : `
      <div class="next"><div class="row"><div style="flex:1"><div class="small muted">Next action</div><b>${esc(what)}</b></div>
        <button class="btn primary" id="approveBtn">Approve</button><button class="btn" id="ovBtn">Override…</button></div>
        <div id="ovForm"></div></div>`}
      <h3>Why</h3>
      <div class="why">${esc(r.reason)}</div>
      <div class="kv small" style="margin-top:10px">
        <div>Policy</div><div>v${esc(r.policy_version)}, clause ${esc(r.rule_id)} ${esc(r.rule_name)}</div>
        <div>Valued at</div><div>${inr(r.value_price)} — ${esc(r.value_source)}</div>
        ${r.contract_id ? `<div>Contract</div><div class="mono">${esc(r.contract_id)}</div>` : ""}
        <div>Purchase history</div><div>${r.facts.days_since_last_active_purchase === null ? "None from an Active supplier" : `Last Active-supplier PO ${r.facts.days_since_last_active_purchase} days before`}</div>
      </div>
      <h3>PR lines (${r.lines.length})</h3>
      <div class="tablewrap short">${table(["PR / line", "Created", "Requester", "As typed", ["Qty", "num"], "Need by"], r.lines.map((l) => `<tr class="small"><td class="nowrap mono">${esc(l.pr_no)}/${esc(l.pr_line)}</td><td class="nowrap">${esc(l.created_at)}</td><td>${esc(l.requester)}<div class="muted">${esc(l.department)}</div></td><td>${esc(l.description)}${l.item_code_given ? "" : `<div class="muted">matched (${esc(l.match_method)})</div>`}</td><td class="num">${l.quantity}</td><td class="nowrap">${esc(l.need_by_date)}${l.urgency === "Emergency" ? ' <span class="tag bad">E</span>' : ""}</td></tr>`))}</div>
      ${r.preferred.length ? `<h3>Preferred suppliers on ${esc(r.requirement_date)}</h3>
      <div class="tablewrap short">${table(["#", "Supplier", ["Score", "num"], ["Ref. price", "num"]], r.preferred.map((p) => `<tr class="small" title="${esc(p.explain)}"><td>${p.rank}</td><td>${esc(p.supplier_id)} ${esc(p.name)}<div class="muted">${esc(p.reference_source)}</div></td><td class="num strong">${p.score.toFixed(3)}</td><td class="num">${inr(p.reference_price)}</td></tr>`))}</div><div class="small muted" style="margin-top:6px">Hover a row for the score breakdown, or see <a href="#/suppliers">Preferred suppliers</a>.</div>` : ""}
      ${acts.length > 1 ? `<h3>History</h3>${acts.map((a) => `<div class="small" style="padding:3px 0">${when(a.at)} · ${esc(a.actor)} · ${esc(a.action)} · <span class="mono">${esc(a.document_no)}</span> ${a.comment ? "· " + esc(a.comment) : ""}</div>`).join("")}` : ""}
    </div>`;
  const ab = $("#approveBtn"); if (ab) ab.onclick = () => act(r, { action: "approve" });
  const ob = $("#ovBtn"); if (ob) ob.onclick = () => overrideForm(r);
}

async function overrideForm(r) {
  const sup = state.suppliers || (state.suppliers = await api("/api/suppliers"));
  const active = sup.filter((s) => s.status === "Active");
  $("#ovForm").innerHTML = `<div class="grid2" style="margin-top:6px">
    <div><label>Route</label><select id="ovRoute" class="full">${["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"].map((x) => `<option ${x === r.route ? "selected" : ""}>${x}</option>`).join("")}</select></div>
    <div><label>Supplier (POs; Active only)</label><select id="ovSup" class="full"><option value="">—</option>${active.map((s) => `<option value="${s.supplier_id}" ${s.supplier_id === r.supplier_id ? "selected" : ""}>${s.supplier_id} ${esc(s.name)}</option>`).join("")}</select></div>
    <div><label>Unit price (₹)</label><input id="ovPrice" class="full" type="number" step="0.01" value="${r.unit_price ?? ""}"></div>
    <div><label>Suggested suppliers (sourcing)</label><input id="ovSugg" class="full" value="${esc((r.suggested_supplier_ids || []).join(","))}"></div>
  </div><label>Reason for override (required, kept with the original decision)</label><textarea id="ovComment"></textarea>
  <div class="row" style="margin-top:10px"><button class="btn primary" id="ovSave">Save override</button><button class="btn" id="ovCancel">Cancel</button></div>`;
  $("#ovCancel").onclick = () => ($("#ovForm").innerHTML = "");
  $("#ovSave").onclick = () => act(r, { action: "override", route: $("#ovRoute").value, supplier_id: $("#ovSup").value, unit_price: $("#ovPrice").value ? Number($("#ovPrice").value) : null, suggested_supplier_ids: $("#ovSugg").value.split(",").map((x) => x.trim()).filter(Boolean), comment: $("#ovComment").value });
}

async function act(r, body) {
  if (!needUser()) return;
  try {
    const res = await api(`/api/runs/${state.run.id}/requirements/${r.group_id}/action`, { method: "POST", body });
    toast(`${body.action === "approve" ? "Approved" : "Overridden"} → ${res.document_no}`);
    state.run = await api(`/api/runs/${state.run.id}`);
    const next = state.run.result.requirements.find((x) => !reqStatus(x.group_id));
    state.sel = next ? next.group_id : r.group_id;
    drawWorkspace();
  } catch (e) { toast(e.message, true); }
}

// batch
async function renderBatch() {
  const pol = await api("/api/policies");
  const versions = pol.versions.filter((v) => v.status === "active");
  view.innerHTML = pagehead("Batch processing & API", "Upload a PR batch CSV in the sample format. Decisions use the policy version effective on each requirement date, unless you force one version.") + `
    <div class="grid2">
      <div class="card"><div class="cardhead"><h2>Process a PR batch</h2></div>
        ${drop("bFile", ".csv", false, "PR batch CSV")}
        <label>Policy</label><select id="bPol" class="full"><option value="">Version effective on each requirement date (recommended)</option>${versions.map((v) => `<option value="${esc(v.version)}">Force v${esc(v.version)} (effective ${esc(v.effective_date)})</option>`).join("")}</select>
        <label>Label (optional)</label><input id="bLabel" class="full" placeholder="e.g. Run 1 — blind batch under v5.0">
        <div class="row" style="margin-top:16px"><button class="btn primary" id="bRun">Process batch</button></div>
        <div id="bOut"></div></div>
      <div class="card"><div class="cardhead"><h2>API</h2><div class="spacer"></div><a class="btn small" href="/docs" target="_blank">Open API docs ↗</a></div>
        <p class="small" style="margin-top:0"><code>POST /api/process-batch</code> with multipart field <code>file</code>. Optional query: <code>policy_version=5.0</code>, <code>format=csv</code>.</p>
        <pre class="rule">curl -F "file=@batch.csv" \\\n  "${location.origin}/api/process-batch?format=csv" -o decisions.csv</pre>
        <h3>Other endpoints</h3>
        <div class="small" style="line-height:1.9"><code>GET /api/runs/{id}/export.csv</code><br><code>GET /api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD</code><br><code>POST /api/policies/amend</code> · <code>POST /api/policies/{id}/approve</code><br><code>POST /api/chat</code> · <code>GET /api/health</code> · <code>GET /api/llm/check</code></div></div>
    </div>`;
  wireDrop("bFile");
  $("#bRun").onclick = async () => {
    if (!needUser()) return;
    const f = $("#bFile").files[0]; if (!f) return toast("Choose a CSV first", true);
    const fd = new FormData(); fd.append("file", f);
    const qs = new URLSearchParams({ label: $("#bLabel").value || f.name }); if ($("#bPol").value) qs.set("policy_version", $("#bPol").value);
    $("#bRun").disabled = true;
    $("#bOut").innerHTML = `<p class="muted">Processing…</p>`;
    try {
      const r = await api(`/api/process-batch?${qs}`, { method: "POST", body: fd });
      state.run = null;
      $("#bOut").innerHTML = `<div class="done" style="margin-top:16px"><b>Run #${r.run_id}</b> · ${esc(r.policy_mode)} · ${r.summary.lines} lines → ${r.summary.requirements} requirements, ${r.summary.purchase_orders} POs
        <div style="margin-top:8px">${Object.entries(r.summary.routes).map(([k, v]) => `${routeBadge(k)} <b>${v}</b>`).join(" &nbsp; ")}</div>
        <div class="row" style="margin-top:12px"><a class="btn primary" href="/api/runs/${r.run_id}/export.csv">⭳ Download decisions CSV</a><a class="btn" href="#/workspace?run=${r.run_id}">Open in workspace</a></div></div>`;
    } catch (e) { $("#bOut").innerHTML = ""; toast(e.message, true); }
    $("#bRun").disabled = false;
  };
}

// policy
async function renderPolicy() {
  await refreshHealth();
  const pol = await api("/api/policies");
  state.policyMeta = pol;
  const vs = pol.versions;
  const inForce = vs.find((v) => v.in_force_today);
  const drafts = vs.filter((v) => v.status === "draft");
  const latest = vs.filter((v) => v.status === "active").sort((a, b) => (a.effective_date + a.approved_at).localeCompare(b.effective_date + b.approved_at)).slice(-1)[0];
  const stTag = (s) => `<span class="tag ${s === "active" ? "ok" : s === "draft" ? "warn" : ""}">${esc(s)}</span>`;
  view.innerHTML = pagehead("Procurement policy", "The policy is data. Amend it by uploading a document, describing the change, or editing the rules — then review what will change and approve. A version applies to requirements dated on or after its effective date.") + `
    ${drafts.map((d) => `<div class="next" style="margin-bottom:16px"><div class="row"><div style="flex:1"><b>Draft v${esc(d.version)} awaiting approval</b><div class="small muted">effective ${esc(d.effective_date)} · by ${esc(d.created_by)} · from ${esc(d.source_type)}</div></div><button class="btn primary" data-review="${d.id}">Review &amp; approve</button></div></div>`).join("")}
    <div class="grid2">
      <div class="card"><div class="cardhead"><h2>Amend the policy</h2><div class="spacer"></div><span class="small muted">${pol.llm ? `AI reader: ${esc(pol.llm)}` : "Rule-based reader"}</span></div>
        ${drop("amFile", ".pdf,.txt,.md,.json", false, "Amendment document (PDF, TXT, MD or policy JSON)")}
        <label>…or describe the change</label><textarea id="amText" placeholder="e.g. From 1 Dec 2026, spot POs at Chakan (CHK1) are allowed only below ₹1,00,000. Version 5.1."></textarea>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="amGo">Read amendment</button><button class="btn" id="edGo">Edit rules directly…</button></div>
        <div id="amOut"></div></div>
      <div class="card flush"><div class="cardhead"><h2>Versions</h2></div><div class="tablewrap" style="max-height:340px">${table(["Version", "Effective", "Status", "Approved by"], vs.map((v) =>
        `<tr class="click" data-view="${v.id}"><td class="nowrap"><b>v${esc(v.version)}</b> ${v.in_force_today ? '<span class="pill">in force today</span>' : ""}</td><td class="nowrap">${esc(v.effective_date)}</td><td>${stTag(v.status)}</td><td>${esc(v.approved_by || "—")}<div class="small muted">${v.approved_at ? when(v.approved_at) : ""}</div></td></tr>`))}</div></div>
    </div>
    <div class="card" id="polView"></div>`;
  wireDrop("amFile");
  $$("[data-review]").forEach((b) => (b.onclick = () => showVersion(Number(b.dataset.review), true)));
  $$("[data-view]").forEach((b) => (b.onclick = () => showVersion(Number(b.dataset.view), true)));
  $("#amGo").onclick = readAmendment;
  $("#edGo").onclick = () => openEditor(latest);
  showVersion((drafts[0] || inForce || latest).id);
}

async function readAmendment() {
  if (!needUser()) return;
  const fd = new FormData(); const f = $("#amFile").files[0];
  if (f) fd.append("file", f); fd.append("text", $("#amText").value);
  if (!f && !$("#amText").value.trim()) return toast("Upload a document or describe the change", true);
  $("#amGo").disabled = true;
  $("#amOut").innerHTML = `<p class="muted">Reading the amendment… with AI this can take up to a minute.</p>`;
  try {
    const r = await api("/api/policies/amend", { method: "POST", body: fd });
    await renderPolicy(); showVersion(r.draft.id, true);
    toast(`Draft v${r.draft.version} created (${r.method}). Review it below.`);
  } catch (e) { $("#amOut").innerHTML = `<div class="why" style="margin-top:12px;color:var(--bad)">${esc(e.message)}<br>Use “Edit rules directly…” to make this change.</div>`; $("#amGo").disabled = false; }
}

async function showVersion(id, scroll = false) {
  const v = await api(`/api/policies/${id}`);
  const isDraft = v.status === "draft";
  $("#polView").innerHTML = `<div class="cardhead"><h2>v${esc(v.version)} · ${esc(v.status)}${v.in_force_today ? " · in force today" : ""}</h2><div class="spacer"></div>
      ${isDraft ? `<button class="btn" id="edDraft">Adjust in editor</button><button class="btn danger" id="rej">Reject</button><button class="btn primary" id="appr">Approve &amp; activate from ${esc(v.effective_date)}</button>` : ""}</div>
    <p class="muted small" style="margin-top:0">Effective ${esc(v.effective_date)} · created by ${esc(v.created_by)} from ${esc(v.source_type)}${v.approved_by ? ` · approved by ${esc(v.approved_by)}` : ""}${v.review_note ? ` · note: ${esc(v.review_note)}` : ""}</p>
    <h3>${isDraft ? "What will change" : "Changes in this version"}</h3>
    ${(v.changes || []).map((c) => `<div class="change">${c.startsWith("NEW") ? '<span class="tag ok">new</span> ' : c.startsWith("CHANGED") ? '<span class="tag warn">changed</span> ' : c.startsWith("REMOVED") ? '<span class="tag bad">removed</span> ' : c.startsWith("Reader note") ? '<span class="tag blue">note</span> ' : ""}${esc(c.replace(/^(NEW|CHANGED|REMOVED) /, "").replace(/^Reader note: /, ""))}</div>`).join("")}
    ${v.source_text ? `<details style="margin-top:8px"><summary class="small muted">Show source text</summary><pre class="rule">${esc(v.source_text)}</pre></details>` : ""}
    <h3>Decision rules (checked in this order; the first match decides)</h3>
    ${v.rules_text.map((t) => `<div class="rule">${esc(t)}</div>`).join("")}
    <h3>Parameters</h3><div class="kv small">${Object.entries(v.body.parameters || {}).map(([k, x]) => `<div>${esc(k.replace(/_/g, " "))}</div><div>${esc(x)}</div>`).join("")}
      <div>supplier status overrides</div><div class="mono">${esc(JSON.stringify(v.body.supplier_status_overrides || {}))}</div>
      <div>ranking weights</div><div class="mono">${esc(JSON.stringify((v.body.preferred_suppliers || {}).weights || {}))}</div></div>`;
  if (scroll) $("#polView").scrollIntoView({ behavior: "smooth", block: "start" });
  if (isDraft) {
    $("#appr").onclick = async () => { if (!needUser()) return; try { await api(`/api/policies/${id}/approve`, { method: "POST", body: { note: "" } }); toast(`v${v.version} approved; applies from ${v.effective_date}`); renderPolicy(); } catch (e) { toast(e.message, true); } };
    $("#rej").onclick = async () => { const note = prompt("Reason for rejecting?"); if (note === null) return; try { await api(`/api/policies/${id}/reject`, { method: "POST", body: { note } }); renderPolicy(); } catch (e) { toast(e.message, true); } };
    $("#edDraft").onclick = () => openEditor(v, true);
  }
}

function openEditor(base, fromDraft = false) {
  const meta = state.policyMeta;
  const b = JSON.parse(JSON.stringify(base.body));
  if (!fromDraft) { const p = String(b.version).split("."); b.version = `${p[0]}.${Number(p[1] || 0) + 1}`; b.effective_date = new Date().toISOString().slice(0, 10); }
  const ruleBox = (r) => `<div class="ruleedit"><div class="row">
      <input class="rId" value="${esc(r.id)}" style="width:72px" title="Rule id"><input class="rName" value="${esc(r.name || "")}" placeholder="Name" style="flex:1;min-width:160px">
      <select class="rDec">${["PO", "SOURCING_REQUEST"].map((x) => `<option ${x === r.decision ? "selected" : ""}>${x}</option>`).join("")}</select>
      <select class="rRoute">${meta.routes.map((x) => `<option ${x === r.route ? "selected" : ""}>${x}</option>`).join("")}</select>
      <button class="btn small" data-mv="-1" title="Move up">↑</button><button class="btn small" data-mv="1" title="Move down">↓</button><button class="btn small danger" data-del title="Delete">✕</button></div>
      <label>Conditions (JSON)</label><textarea class="rWhen">${esc(JSON.stringify(r.when || { all: [] }))}</textarea>
      <div class="grid2"><div><label>Supplier (JSON)</label><input class="rSup full mono" value='${esc(JSON.stringify(r.supplier || { mode: "none" }))}'></div><div><label>Note (optional)</label><input class="rNote full" value="${esc(r.note || "")}"></div></div></div>`;
  $("#polView").innerHTML = `<div class="cardhead"><h2>Policy editor — new version based on v${esc(base.version)}</h2><div class="spacer"></div><button class="btn" id="edCancel">Cancel</button><button class="btn primary" id="edSave">Create draft for review</button></div>
    <div class="grid3"><div><label>New version</label><input id="edVer" class="full" value="${esc(b.version)}"></div><div><label>Effective date</label><input id="edEff" class="full" type="date" value="${esc(b.effective_date)}"></div><div><label>Note</label><input id="edNote" class="full" placeholder="Why this change"></div></div>
    <h3>Parameters</h3><div class="grid3">${Object.entries(b.parameters).map(([k, x]) => `<div><label>${esc(k.replace(/_/g, " "))}</label><input class="edParam full" data-k="${k}" type="number" value="${x}"></div>`).join("")}</div>
    <div class="grid2"><div><label>Supplier status overrides (JSON, e.g. {"S014":"Blocked"})</label><input id="edOver" class="full mono" value='${esc(JSON.stringify(b.supplier_status_overrides || {}))}'></div>
    <div><label>Preferred-supplier ranking weights (JSON)</label><input id="edW" class="full mono" value='${esc(JSON.stringify(b.preferred_suppliers.weights))}'></div></div>
    <h3>Rules (the first match decides)</h3>
    <p class="small muted" style="margin-top:0">Facts: ${Object.keys(meta.facts).map((f) => `<code title="${esc(meta.facts[f])}">${f}</code>`).join(" ")}<br>Operators: ${meta.ops.map((o) => `<code>${o}</code>`).join(" ")}</p>
    <button class="btn" id="edAdd" style="margin-bottom:10px">+ Add rule at the top</button>
    <div id="edRules">${b.rules.map(ruleBox).join("")}</div>`;
  const collect = () => {
    const rules = $$(".ruleedit").map((el) => ({
      id: $(".rId", el).value.trim(), name: $(".rName", el).value.trim(), decision: $(".rDec", el).value, route: $(".rRoute", el).value,
      when: JSON.parse($(".rWhen", el).value || "{}"), supplier: JSON.parse($(".rSup", el).value || '{"mode":"none"}'), ...($(".rNote", el).value ? { note: $(".rNote", el).value } : {}) }));
    const nb = JSON.parse(JSON.stringify(b));
    nb.version = $("#edVer").value.trim(); nb.effective_date = $("#edEff").value; nb.rules = rules;
    $$(".edParam").forEach((i) => (nb.parameters[i.dataset.k] = Number(i.value)));
    nb.supplier_status_overrides = JSON.parse($("#edOver").value || "{}");
    nb.preferred_suppliers.weights = JSON.parse($("#edW").value || "{}");
    return nb;
  };
  const rewire = () => {
    $$("[data-mv]").forEach((btn) => (btn.onclick = () => { const el = btn.closest(".ruleedit"); const d = Number(btn.dataset.mv); if (d < 0 && el.previousElementSibling) el.parentNode.insertBefore(el, el.previousElementSibling); if (d > 0 && el.nextElementSibling) el.parentNode.insertBefore(el.nextElementSibling, el); }));
    $$("[data-del]").forEach((btn) => (btn.onclick = () => btn.closest(".ruleedit").remove()));
  };
  rewire();
  $("#edCancel").onclick = () => showVersion(base.id);
  $("#edAdd").onclick = () => { $("#edRules").insertAdjacentHTML("afterbegin", ruleBox({ id: "new", name: "", decision: "SOURCING_REQUEST", route: "RFQ", when: { all: [{ fact: "plant", op: "eq", value: "CHK1" }] }, supplier: { mode: "suggest", count: 3 } })); rewire(); };
  $("#edSave").onclick = async () => {
    if (!needUser()) return;
    let nb; try { nb = collect(); } catch (e) { return toast("Invalid JSON in a rule: " + e.message, true); }
    try { const r = await api("/api/policies/draft", { method: "POST", body: { body: nb, based_on: fromDraft ? base.based_on : base.id, replaces_draft: fromDraft ? base.id : null, note: $("#edNote").value } }); toast("Draft created — review and approve"); await renderPolicy(); showVersion(r.draft.id, true); } catch (e) { toast(e.message, true); }
  };
  $("#polView").scrollIntoView({ behavior: "smooth", block: "start" });
}

// contracts
async function renderContracts() {
  const cs = await api("/api/contracts");
  view.innerHTML = pagehead("Rate contracts", "Contracts from the register plus signed PDFs. Contracts signed after the monthly register update can be added from their PDF.") + `
    <div class="split wide"><div class="card flush"><div class="cardhead"><h2>${cs.length} contracts</h2></div><div class="tablewrap">${table(["Contract", "Supplier", "Validity", "Ceiling used (register)", "Items"], cs.map((c) => {
      const pct = c.value_cap ? Math.min(100, (100 * c.consumed_value) / c.value_cap) : 0;
      return `<tr><td class="nowrap"><b>${esc(c.contract_id)}</b><div>${c.source !== "register" ? `<span class="tag blue">${esc(c.source)}</span>` : ""}${c.valid_today ? "" : '<span class="tag">not valid today</span>'}</div></td>
        <td>${esc(c.supplier_id)} ${esc(c.supplier_name)}${c.supplier_status !== "Active" ? `<div><span class="tag bad">${esc(c.supplier_status)} — cannot be used</span></div>` : ""}</td>
        <td class="nowrap small">${esc(c.start_date)}<br>→ ${esc(c.end_date)}</td>
        <td><div class="bar ${pct > 90 ? "hot" : ""}"><i style="width:${pct}%"></i></div><div class="small muted nowrap" style="margin-top:4px">${inr0(c.consumed_value)} of ${inr0(c.value_cap)} · ${pct.toFixed(0)}%</div></td>
        <td style="min-width:200px">${c.lines.map((l) => `<span class="tag" title="${esc(l.description || "")}">${esc(l.item_code)} · ${inr(l.rate)}</span>`).join("")}</td></tr>`;
    }))}</div></div>
      <div class="card sticky"><div class="cardhead"><h2>Add a contract</h2></div>${drop("cPdf", ".pdf", false, "Signed contract PDF")}<div class="row" style="margin-top:12px"><button class="btn primary" id="cExtract">Extract from PDF</button><button class="btn" id="cManual">Enter manually</button></div><div id="cForm"></div></div></div>`;
  wireDrop("cPdf");
  $("#cExtract").onclick = async () => { const f = $("#cPdf").files[0]; if (!f) return toast("Choose a PDF", true); const fd = new FormData(); fd.append("file", f); try { contractForm(await api("/api/contracts/extract-pdf", { method: "POST", body: fd })); } catch (e) { toast(e.message, true); } };
  $("#cManual").onclick = () => contractForm({ lines: [{}], warnings: [] });
}

function contractForm(d) {
  $("#cForm").innerHTML = `<div style="margin-top:14px">${(d.warnings || []).map((w) => `<div class="small" style="color:var(--warn)">⚠ ${esc(w)}</div>`).join("")}
    <p class="small muted">Check the values against the PDF before saving.</p>
    <div class="grid2"><div><label>Contract no.</label><input id="cfId" class="full" value="${esc(d.contract_id || "")}"></div><div><label>Supplier ID</label><input id="cfSup" class="full" value="${esc(d.supplier_id || "")}"></div>
    <div><label>Start date</label><input id="cfStart" class="full" type="date" value="${esc(d.start_date || "")}"></div><div><label>End date</label><input id="cfEnd" class="full" type="date" value="${esc(d.end_date || "")}"></div>
    <div><label>Value ceiling (₹)</label><input id="cfCap" class="full" type="number" value="${esc(d.value_cap || "")}"></div><div><label>Consumed so far (₹)</label><input id="cfCons" class="full" type="number" value="${esc(d.consumed_value || 0)}"></div></div>
    <label>Items (one per line: item_code, rate)</label><textarea id="cfLines" class="mono">${esc((d.lines || []).filter((l) => l.item_code).map((l) => `${l.item_code}, ${l.rate}`).join("\n"))}</textarea>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="cfSave">Save contract</button></div></div>`;
  $("#cfSave").onclick = async () => {
    if (!needUser()) return;
    const lines = $("#cfLines").value.split("\n").map((x) => x.split(",").map((y) => y.trim())).filter((x) => x[0]).map(([item_code, rate]) => ({ item_code, rate: Number(rate) }));
    try { await api("/api/contracts", { method: "POST", body: { contract_id: $("#cfId").value.trim(), supplier_id: $("#cfSup").value.trim(), supplier_name: d.supplier_name, start_date: $("#cfStart").value, end_date: $("#cfEnd").value, value_cap: Number($("#cfCap").value), consumed_value: Number($("#cfCons").value || 0), lines, pdf_name: d.pdf_name } }); toast("Contract saved"); renderContracts(); } catch (e) { toast(e.message, true); }
  };
}

// suppliers
const PARTS = [["frequency", "#2563eb"], ["recency", "#0ea5e9"], ["contract", "#8b5cf6"], ["on_time", "#10b981"], ["quality", "#f59e0b"]];
async function renderSuppliers() {
  const items = await api("/api/items");
  const d = store.get("asOf", new Date().toISOString().slice(0, 10));
  view.innerHTML = pagehead("Preferred supplier lists", "Built from PO history (548 days) and contracts, Active suppliers only. Each supplier's score is the sum of five weighted parts set in the policy.",
    `<a class="btn" id="sExp">⭳ Export top-3 CSV for all items</a>`) + `
    <div class="card"><div class="row"><div style="flex:1;min-width:260px"><label style="margin-top:0">Item</label><select id="sItem" class="full">${items.map((i) => `<option value="${i.item_code}">${i.item_code} — ${esc(i.description)}</option>`).join("")}</select></div>
      <div><label style="margin-top:0">As of</label><input type="date" id="sDate" value="${d}"></div></div></div>
    <div class="card flush" id="sOut"></div>`;
  const go = async () => {
    store.set("asOf", $("#sDate").value);
    $("#sExp").href = `/api/preferred-suppliers/export.csv?as_of=${$("#sDate").value}`;
    const r = await api(`/api/preferred-suppliers?item_code=${$("#sItem").value}&as_of=${$("#sDate").value}`);
    $("#sOut").innerHTML = `<div class="cardhead"><h2>${r.suppliers.length} eligible supplier${r.suppliers.length === 1 ? "" : "s"}</h2><span class="small muted">policy v${esc(r.policy_version)} weights · as of ${esc(r.as_of)}</span><div class="spacer"></div><div class="legend">${PARTS.map(([k, c]) => `<span style="--c:${c}">${k.replace("_", "-")}</span>`).join("")}</div></div>` +
      (r.suppliers.length ? `<div class="tablewrap short">${table(["#", "Supplier", ["PO count", "num"], "Last PO", "Contract", ["On-time", "num"], ["Quality", "num"], ["Score", "num"], "Score breakdown", ["Reference price", "num"]], r.suppliers.map((s) =>
        `<tr><td class="strong">${s.rank}</td><td>${esc(s.supplier_id)} ${esc(s.name)}</td><td class="num">${s.po_count}</td><td class="nowrap">${esc(s.last_po_date || "—")}</td><td>${s.has_contract ? '<span class="tag ok">yes</span>' : '<span class="muted">no</span>'}</td><td class="num">${s.on_time_pct}%</td><td class="num">${s.quality_rating}</td><td class="num strong">${s.score.toFixed(3)}</td>
         <td><div class="stack" title="${PARTS.map(([k]) => `${k} ${s.components[k].toFixed(3)}`).join(" + ")}">${PARTS.map(([k, c]) => `<i style="width:${(s.components[k] * 100).toFixed(1)}%;background:${c}"></i>`).join("")}</div></td>
         <td class="num">${inr(s.reference_price)}<div class="small muted">${esc(s.reference_source)}</div></td></tr>`))}</div>`
        : `<div class="empty">No eligible supplier: no PO in 548 days and no contract. Sourcing will suggest category suppliers.</div>`);
  };
  $("#sItem").onchange = go; $("#sDate").onchange = go; go();
}

// data
async function renderData() {
  const h = await refreshHealth();
  const s = h.data;
  const names = { item_master: "Item master", supplier_master: "Supplier master", po_history: "PO history", plants: "Plants", contracts: "Contracts" };
  view.innerHTML = pagehead("Master data", "Upload the item master, supplier master, PO history, plants, contract register, contract lines and contract PDFs. Files are recognised by their columns; re-uploading replaces the previous version.") + `
    <div class="grid2"><div class="card"><div class="cardhead"><h2>Status</h2><div class="spacer"></div>${s.ready ? '<span class="tag ok">Ready</span>' : '<span class="tag bad">Incomplete</span>'}</div>
      <div class="kv">${Object.entries(names).map(([k, l]) => `<div>${l}</div><div>${s[k] ? `<span class="tag ok">✓</span> ${s[k]} rows` : '<span class="tag bad">missing</span>'}</div>`).join("")}</div>
      <p class="small muted" style="margin-bottom:0">AI: ${esc(h.llm || "not configured")} · Today: ${esc(h.today)}</p></div>
      <div class="card"><div class="cardhead"><h2>Upload</h2></div>${drop("dFiles", ".csv,.pdf", true, "Master data CSVs and contract PDFs")}<div class="row" style="margin-top:12px"><button class="btn primary" id="dGo">Upload</button></div><div id="dOut" class="small" style="margin-top:10px"></div></div></div>`;
  wireDrop("dFiles");
  $("#dGo").onclick = async () => {
    if (!needUser()) return;
    const fs = $("#dFiles").files; if (!fs.length) return toast("Choose files", true);
    const fd = new FormData(); [...fs].forEach((f) => fd.append("files", f));
    try { const r = await api("/api/data/upload", { method: "POST", body: fd }); toast("Uploaded"); await renderData(); $("#dOut").innerHTML = `Loaded: ${esc(Object.entries(r.loaded).map(([k, v]) => `${k} (${Array.isArray(v) ? v.length : v})`).join(", "))}${r.skipped.length ? `<br>Skipped: ${esc(r.skipped.join(", "))}` : ""}`; } catch (e) { toast(e.message, true); }
  };
}

// audit
const ACTION_TAG = { approve_requirement: "ok", override_requirement: "warn", approve_policy: "ok", reject_policy: "bad", draft_policy: "warn", process_batch: "blue", create_pr: "blue" };
function auditDetails(a) {
  const d = a.details || {};
  if (a.action.endsWith("_requirement")) return `${esc(d.document_no || "")} · ${esc((d.final || {}).route || "")}${(d.final || {}).supplier_id ? " · " + esc(d.final.supplier_id) : ""}${d.comment ? ` · “${esc(d.comment)}”` : ""}`;
  if (a.action === "create_pr") return `${esc(d.quantity)} ${esc(d.uom)} ${esc(d.description)} · ${esc(d.plant)} · by ${esc(d.need_by_date)}`;
  if (a.action === "process_batch") return `${esc(d.lines)} lines · ${esc(d.policy_mode)}`;
  if (a.action === "upload_data") return `${esc(d.rows)} rows`;
  return Object.entries(d).map(([k, v]) => `${esc(k.replace(/_/g, " "))}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join(" · ");
}
async function renderAudit() {
  const all = await api("/api/audit?limit=1000");
  const actors = [...new Set(all.map((a) => a.actor))].sort();
  const acts = [...new Set(all.map((a) => a.action))].sort();
  view.innerHTML = pagehead("Audit log", "Every upload, policy change, approval, override and PR, with who did it and when.") + `
    <div class="card flush"><div class="cardhead"><select id="fWho"><option value="">Everyone</option>${actors.map((x) => `<option>${esc(x)}</option>`).join("")}</select>
      <select id="fAct"><option value="">All actions</option>${acts.map((x) => `<option>${esc(x)}</option>`).join("")}</select>
      <input id="fQ" placeholder="Search…" style="width:200px"><div class="spacer"></div><span class="small muted" id="fN"></span></div>
      <div class="tablewrap" id="aT"></div></div>`;
  const draw = () => {
    const who = $("#fWho").value, ac = $("#fAct").value, q = $("#fQ").value.toLowerCase();
    const rows = all.filter((a) => (!who || a.actor === who) && (!ac || a.action === ac) && (!q || JSON.stringify(a).toLowerCase().includes(q)));
    $("#fN").textContent = `${rows.length} of ${all.length} entries`;
    $("#aT").innerHTML = rows.length ? table(["When", "Who", "Action", "On", "Details"], rows.map((a) =>
      `<tr><td class="nowrap small">${when(a.at)}</td><td class="nowrap">${esc(a.actor)}</td><td><span class="tag ${ACTION_TAG[a.action] || ""}">${esc(a.action.replace(/_/g, " "))}</span></td><td class="nowrap mono">${esc(a.entity)}</td><td class="small muted" title="${esc(JSON.stringify(a.details))}">${auditDetails(a)}</td></tr>`)) : `<div class="empty">No entries match.</div>`;
  };
  $("#fWho").onchange = draw; $("#fAct").onchange = draw; $("#fQ").oninput = draw; draw();
}

refreshHealth();
route();
