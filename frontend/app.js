const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const state = { user: store.get("user", ""), chatSession: null, chatLog: [], run: null, sel: null, filter: "todo" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (v) => (v === null || v === undefined || v === "") ? "—" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const PO_ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO"];
const routeCls = (r) => PO_ROUTES.includes(r) ? "PO" : (r === "NOT_PROCESSED" ? "NP" : "SRC");
const routeBadge = (r) => `<span class="route ${routeCls(r)}">${esc(r)}</span>`;

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
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  view.innerHTML = `<div class="empty">Loading…</div>`;
  try { await (routes[name] || renderChat)(); } catch (e) { view.innerHTML = `<div class="card">Error: ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
$("#userName").value = state.user;
$("#userName").addEventListener("change", (e) => { state.user = e.target.value.trim(); store.set("user", state.user); toast(`Signed in as ${state.user}`); });

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
  view.innerHTML = `
    <h1>Raise a purchase request</h1>
    <p class="sub">Tell the assistant what you need in plain words. It matches the item master, asks for anything missing, and confirms before creating the PR.</p>
    <div class="split">
      <div class="card chat">
        <div class="msgs" id="msgs"></div>
        <div class="composer"><input id="chatIn" placeholder='e.g. need 200 nitrile gloves for Chakan by next Friday' autocomplete="off"><button class="btn primary" id="chatSend">Send</button></div>
      </div>
      <div>
        <div class="card"><h3>My recent requests</h3><div id="myPrs" class="small muted">—</div></div>
        <div class="card small muted">Tips: give the item, quantity, plant (Bhosari/PUN1 or Chakan/CHK1), need-by date, and say if it is an emergency. Type <code>start over</code> any time.</div>
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
      html += `<div class="summary kv small"><div>Item</div><div><b>${esc(s.description)}</b> <span class="muted">${esc(s.item_code)}</span></div>
        <div>Quantity</div><div>${esc(s.quantity)} ${esc(s.uom)}</div><div>Plant</div><div>${esc(s.plant)} — ${esc(s.plant_name)}</div>
        <div>Need by</div><div>${esc(s.need_by_date)}</div><div>Urgency</div><div>${esc(s.urgency)}</div>
        ${s.item_code === "NEW" ? `<div>Est. unit price</div><div>${inr(s.estimated_unit_price)}</div>` : ""}</div>`;
    }
    if (m.options && m.options.length && i === state.chatLog.length - 1) {
      html += `<div class="opts">${m.options.map((o) => `<button data-v="${esc(o.value)}">${esc(o.label)}</button>`).join("")}</div>`;
    }
    return html + "</div>";
  }).join("");
  box.querySelectorAll(".opts button").forEach((b) => (b.onclick = () => chatSend(b.dataset.v, b.textContent)));
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
  el.innerHTML = mine.length ? `<table><tr><th>PR</th><th>Item</th><th>Qty</th><th>Need by</th></tr>${mine.map((p) => `<tr><td>${esc(p.pr_no)}</td><td>${esc(p.description)}</td><td>${esc(p.quantity)} ${esc(p.uom)}</td><td>${esc(p.need_by_date)}</td></tr>`).join("")}</table>` : "No requests yet.";
}

// workspace
async function renderWorkspace() {
  await refreshHealth();
  const runs = await api("/api/runs");
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const rid = Number(q.get("run")) || (state.run && state.run.id) || (runs[0] && runs[0].id);
  view.innerHTML = `
    <div class="row"><div><h1>Buyer workspace</h1><p class="sub">Each requirement is one or more clubbed PR lines. Review the decision and why, then approve or override.</p></div><div class="spacer"></div>
      <select id="runSel">${runs.map((r) => `<option value="${r.id}" ${r.id === rid ? "selected" : ""}>Run #${r.id} · ${esc(r.label)} · ${esc(r.policy_mode)} · ${new Date(r.created_at + "Z").toLocaleString()}</option>`).join("")}</select>
      <button class="btn" id="procChat">Process open chat requests</button>
      ${rid ? `<a class="btn" href="/api/runs/${rid}/export.csv">Export CSV</a>` : ""}</div>
    <div id="ws"></div>`;
  $("#procChat").onclick = async () => { if (!needUser()) return; try { const r = await api("/api/prs/process", { method: "POST" }); location.hash = `#/workspace?run=${r.run_id}`; state.run = null; route(); } catch (e) { toast(e.message, true); } };
  $("#runSel") && ($("#runSel").onchange = (e) => { location.hash = `#/workspace?run=${e.target.value}`; });
  if (!rid) { $("#ws").innerHTML = `<div class="card empty">No decisions yet. Upload a PR batch on <a href="#/batch">Batch &amp; API</a>, or raise requests in chat and click “Process open chat requests”.</div>`; return; }
  if (!state.run || state.run.id !== rid) { state.run = await api(`/api/runs/${rid}`); state.sel = null; }
  drawWorkspace();
}

function reqStatus(g) { const a = (state.run.actions[g] || []).slice(-1)[0]; return a; }

function drawWorkspace() {
  const run = state.run, reqs = run.result.requirements, np = run.result.not_processed;
  const counts = { todo: 0, done: 0, po: 0, src: 0 };
  reqs.forEach((r) => { reqStatus(r.group_id) ? counts.done++ : counts.todo++; r.decision === "PO" ? counts.po++ : counts.src++; });
  const f = state.filter;
  let list = reqs.filter((r) => f === "all" || (f === "todo" && !reqStatus(r.group_id)) || (f === "done" && reqStatus(r.group_id)) || (f === "po" && r.decision === "PO") || (f === "src" && r.decision !== "PO"));
  const search = (state.search || "").toLowerCase();
  if (search) list = list.filter((r) => JSON.stringify([r.group_id, r.item_code, r.item_description, r.plant, r.route, r.supplier_id, r.lines.map((l) => l.pr_no)]).toLowerCase().includes(search));
  $("#ws").innerHTML = `
    <div class="grid3" style="grid-template-columns:repeat(5,1fr)">
      ${[["Requirements", reqs.length], ["Awaiting action", counts.todo], ["Purchase orders", run.result.purchase_orders.length], ["Sourcing requests", counts.src], ["Not processed lines", np.length]].map(([k, v]) => `<div class="card"><div class="muted small">${k}</div><div style="font-size:22px;font-weight:600">${v}</div></div>`).join("")}
    </div>
    <div class="split">
      <div class="card">
        <div class="row"><div class="tabs">${[["todo", "Awaiting action"], ["done", "Actioned"], ["po", "POs"], ["src", "Sourcing"], ["all", "All"], ["np", "Not processed"]].map(([k, l]) => `<button data-f="${k}" class="${f === k ? "on" : ""}">${l}</button>`).join("")}</div><div class="spacer"></div><input id="wsSearch" placeholder="Search item, PR, supplier…" value="${esc(state.search || "")}"></div>
        <div class="scroll">${f === "np" ? npTable(np) : reqTable(list)}</div>
      </div>
      <div id="detail">${state.sel ? "" : `<div class="card empty">Select a requirement to see the decision and act on it.</div>`}</div>
    </div>`;
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => { state.filter = b.dataset.f; drawWorkspace(); }));
  $("#wsSearch").oninput = (e) => { state.search = e.target.value; clearTimeout(state._st); state._st = setTimeout(() => { drawWorkspace(); $("#wsSearch").focus(); $("#wsSearch").setSelectionRange(999, 999); }, 250); };
  document.querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => { state.sel = tr.dataset.g; drawWorkspace(); }));
  if (state.sel) drawDetail();
}

function npTable(np) {
  if (!np.length) return `<div class="empty">All lines were processed.</div>`;
  return `<table><tr><th>PR / line</th><th>Reason</th></tr>${np.map((n) => `<tr><td>${esc(n.pr_no)}/${esc(n.pr_line)}</td><td>${esc(n.reason)}</td></tr>`).join("")}</table>`;
}

function reqTable(list) {
  if (!list.length) return `<div class="empty">Nothing here.</div>`;
  return `<table><tr><th>Req</th><th>Item</th><th>Plant</th><th class="num">Value</th><th>Decision</th><th>Supplier / suggested</th><th>Status</th></tr>
    ${list.map((r) => { const a = reqStatus(r.group_id); return `<tr class="click ${state.sel === r.group_id ? "sel" : ""}" data-g="${r.group_id}">
      <td>${esc(r.group_id)}<div class="small muted">${r.lines.length} line${r.lines.length > 1 ? "s" : ""}${r.is_emergency ? " · <b style='color:var(--bad)'>Emergency</b>" : ""}</div></td>
      <td>${esc(r.item_description)}<div class="small muted">${esc(r.item_code)} · ${esc(r.created_at)}</div></td>
      <td>${esc(r.plant)}</td><td class="num">${inr(r.value)}</td><td>${routeBadge(r.route)}${r.po_id ? `<div class="small muted">${esc(r.po_id)}</div>` : ""}</td>
      <td>${r.supplier_id ? `${esc(r.supplier_id)} <span class="muted small">@ ${inr(r.unit_price)}</span>` : `<span class="small">${esc((r.suggested_supplier_ids || []).join(", ")) || "—"}</span>`}</td>
      <td>${a ? `<span class="status ${a.action}">${a.action === "approve" ? "✓ Approved" : "✎ Overridden"}<br>${esc(a.document_no)}</span>` : `<span class="muted small">Awaiting</span>`}</td></tr>`; }).join("")}</table>`;
}

function drawDetail() {
  const r = state.run.result.requirements.find((x) => x.group_id === state.sel);
  if (!r) return;
  const acts = state.run.actions[r.group_id] || [];
  const last = acts.slice(-1)[0];
  const isPO = r.decision === "PO";
  const what = isPO ? `Create ${r.po_id} to ${r.supplier_id}` : (r.route === "NEW_ITEM_SOURCING" ? "Start new-item sourcing" : `Create ${r.route === "CAPEX_SOURCING" ? "capex sourcing request" : r.route}`);
  const contract = r.contract_id ? `Contract <b>${esc(r.contract_id)}</b>` : "";
  $("#detail").innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0">${esc(r.group_id)} · ${esc(r.item_description)}</h2><div class="spacer"></div>${routeBadge(r.route)}</div>
      <p class="muted small">${esc(r.item_code)} · ${esc(r.plant)} · requirement date ${esc(r.requirement_date)} · ${r.total_quantity} ${esc(r.uom)}${r.is_emergency ? " · <b style='color:var(--bad)'>Emergency</b>" : ""}</p>
      ${last ? `<div class="why"><b>${last.action === "approve" ? "Approved" : "Overridden"}</b> by ${esc(last.actor)} on ${new Date(last.at + "Z").toLocaleString()} → <b>${esc(last.document_no)}</b>${last.comment ? `<br>Reason: ${esc(last.comment)}` : ""}</div>` : `
      <div class="next"><div class="row"><div><b>Next action:</b> ${esc(what)}${isPO ? ` at ${inr(r.unit_price)} (value ${inr(r.value)})` : (r.suggested_supplier_ids.length ? ` with ${esc(r.suggested_supplier_ids.join(", "))}` : "")}</div><div class="spacer"></div>
        <button class="btn primary" id="approveBtn">Approve</button><button class="btn" id="ovBtn">Override…</button></div>
        <div id="ovForm"></div></div>`}
      <h3>Why</h3>
      <div class="why">${esc(r.reason)}</div>
      <div class="kv small" style="margin-top:8px">
        <div>Policy</div><div>v${esc(r.policy_version)}, clause ${esc(r.rule_id)} ${esc(r.rule_name)}</div>
        <div>Valued at</div><div>${inr(r.value_price)} — ${esc(r.value_source)}</div>
        ${contract ? `<div>Relied on</div><div>${contract}</div>` : ""}
        <div>Purchase history</div><div>${r.facts.days_since_last_active_purchase === null ? "None from an Active supplier" : `Last Active-supplier PO ${r.facts.days_since_last_active_purchase} days before`}</div>
      </div>
      <h3>PR lines (${r.lines.length})</h3>
      <table class="small"><tr><th>PR/line</th><th>Created</th><th>Requester</th><th>As typed</th><th class="num">Qty</th><th>Need by</th></tr>
        ${r.lines.map((l) => `<tr><td>${esc(l.pr_no)}/${esc(l.pr_line)}</td><td>${esc(l.created_at)}</td><td>${esc(l.requester)}<div class="muted">${esc(l.department)}</div></td><td>${esc(l.description)}${l.item_code_given ? "" : `<div class="muted">matched by ${esc(l.match_method)}</div>`}</td><td class="num">${l.quantity}</td><td>${esc(l.need_by_date)}${l.urgency === "Emergency" ? " <b style='color:var(--bad)'>E</b>" : ""}</td></tr>`).join("")}</table>
      ${r.preferred.length ? `<h3>Preferred suppliers (as of ${esc(r.requirement_date)})</h3>
      <table class="small"><tr><th>#</th><th>Supplier</th><th class="num">Score</th><th class="num">Ref. price</th><th>Why this rank</th></tr>
        ${r.preferred.map((p) => `<tr><td>${p.rank}</td><td>${esc(p.supplier_id)} ${esc(p.name)}</td><td class="num">${p.score.toFixed(3)}</td><td class="num">${inr(p.reference_price)}<div class="muted">${esc(p.reference_source)}</div></td><td class="muted">${esc(p.explain)}</td></tr>`).join("")}</table>` : ""}
      ${acts.length > 1 ? `<h3>History</h3>${acts.map((a) => `<div class="small">${new Date(a.at + "Z").toLocaleString()} · ${esc(a.actor)} · ${esc(a.action)} · ${esc(a.document_no)} ${a.comment ? "· " + esc(a.comment) : ""}</div>`).join("")}` : ""}
    </div>`;
  const ab = $("#approveBtn");
  if (ab) ab.onclick = () => act(r, { action: "approve" });
  const ob = $("#ovBtn");
  if (ob) ob.onclick = () => overrideForm(r);
}

async function overrideForm(r) {
  const sup = state.suppliers || (state.suppliers = await api("/api/suppliers"));
  const active = sup.filter((s) => s.status === "Active");
  $("#ovForm").innerHTML = `<div class="grid2" style="margin-top:10px">
    <div><label>Route</label><select id="ovRoute">${["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"].map((x) => `<option ${x === r.route ? "selected" : ""}>${x}</option>`).join("")}</select></div>
    <div><label>Supplier (for POs; Active only)</label><select id="ovSup"><option value="">—</option>${active.map((s) => `<option value="${s.supplier_id}" ${s.supplier_id === r.supplier_id ? "selected" : ""}>${s.supplier_id} ${esc(s.name)}</option>`).join("")}</select></div>
    <div><label>Unit price (₹)</label><input id="ovPrice" type="number" step="0.01" value="${r.unit_price ?? ""}"></div>
    <div><label>Suggested suppliers (for sourcing; comma-separated)</label><input id="ovSugg" value="${esc((r.suggested_supplier_ids || []).join(","))}"></div>
  </div><label>Reason for override (required, kept with the original decision)</label><textarea id="ovComment"></textarea>
  <button class="btn primary" id="ovSave">Save override</button>`;
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
  view.innerHTML = `<h1>Batch processing &amp; API</h1><p class="sub">Upload a PR batch CSV in the sample format. Decisions use the policy version effective on each requirement date, unless you force one version.</p>
    <div class="grid2">
      <div class="card"><h3>Process a PR batch</h3>
        <label>PR batch CSV</label><input type="file" id="bFile" accept=".csv">
        <label>Policy</label><select id="bPol"><option value="">Version effective on each requirement date (recommended)</option>${versions.map((v) => `<option value="${esc(v.version)}">Force v${esc(v.version)} (effective ${esc(v.effective_date)})</option>`).join("")}</select>
        <label>Label (optional)</label><input id="bLabel" placeholder="e.g. Run 1 — blind batch under v5.0">
        <div class="row" style="margin-top:12px"><button class="btn primary" id="bRun">Process batch</button></div>
        <div id="bOut"></div></div>
      <div class="card"><h3>API</h3>
        <p class="small"><code>POST /api/process-batch</code> — multipart field <code>file</code> (CSV). Optional query: <code>policy_version=5.0</code>, <code>format=csv</code>.</p>
        <pre class="rule">curl -F "file=@batch.csv" "${location.origin}/api/process-batch?format=csv" -o decisions.csv</pre>
        <p class="small">Other endpoints: <code>GET /api/runs/{id}/export.csv</code>, <code>GET /api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD</code>, <code>POST /api/policies/amend</code>, <code>POST /api/policies/{id}/approve</code>. Full docs at <a href="/docs" target="_blank">/docs</a>.</p></div>
    </div>`;
  $("#bRun").onclick = async () => {
    if (!needUser()) return;
    const f = $("#bFile").files[0]; if (!f) return toast("Choose a CSV first", true);
    const fd = new FormData(); fd.append("file", f);
    const qs = new URLSearchParams({ label: $("#bLabel").value || f.name }); if ($("#bPol").value) qs.set("policy_version", $("#bPol").value);
    $("#bOut").innerHTML = `<p class="muted">Processing…</p>`;
    try {
      const r = await api(`/api/process-batch?${qs}`, { method: "POST", body: fd });
      state.run = null;
      $("#bOut").innerHTML = `<div class="why" style="margin-top:12px"><b>Run #${r.run_id}</b> (${esc(r.policy_mode)}): ${r.summary.lines} lines → ${r.summary.requirements} requirements, ${r.summary.purchase_orders} POs.<br>
        ${Object.entries(r.summary.routes).map(([k, v]) => `${routeBadge(k)} ${v}`).join(" &nbsp; ")}
        <div class="row" style="margin-top:10px"><a class="btn primary" href="/api/runs/${r.run_id}/export.csv">Download decisions CSV</a><a class="btn" href="#/workspace?run=${r.run_id}">Open in workspace</a></div></div>`;
    } catch (e) { $("#bOut").innerHTML = ""; toast(e.message, true); }
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
  view.innerHTML = `<h1>Procurement policy</h1><p class="sub">The policy is data. Amend it by uploading a document, describing the change, or editing the rules — then review the changes and approve. A version applies to requirements dated on or after its effective date.</p>
    ${drafts.length ? drafts.map((d) => `<div class="card next"><div class="row"><b>Draft v${esc(d.version)} awaiting approval</b> <span class="muted small">effective ${esc(d.effective_date)} · by ${esc(d.created_by)} · from ${esc(d.source_type)}</span><div class="spacer"></div><button class="btn primary" data-review="${d.id}">Review &amp; approve</button></div></div>`).join("") : ""}
    <div class="grid2">
      <div class="card"><h3>Amend the policy</h3>
        <label>Upload amendment (PDF, TXT, MD, or a full policy JSON)</label><input type="file" id="amFile" accept=".pdf,.txt,.md,.json">
        <label>…or describe the change</label><textarea id="amText" placeholder="e.g. From 1 Dec 2026, spot POs at Chakan (CHK1) are allowed only below ₹1,00,000. Version 5.1."></textarea>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="amGo">Read amendment</button><button class="btn" id="edGo">Edit rules directly…</button><span class="small muted">${pol.llm ? `AI reader: ${esc(pol.llm)}` : "No AI key configured: common patterns are read automatically; use the editor for the rest."}</span></div>
        <div id="amOut"></div></div>
      <div class="card"><h3>Versions</h3><table class="small"><tr><th>Version</th><th>Effective</th><th>Status</th><th>Approved</th></tr>
        ${vs.map((v) => `<tr class="click" data-view="${v.id}"><td>v${esc(v.version)} ${v.in_force_today ? '<span class="pill">in force today</span>' : ""}</td><td>${esc(v.effective_date)}</td><td>${esc(v.status)}</td><td>${esc(v.approved_by || "")}<div class="muted">${v.approved_at ? new Date(v.approved_at + "Z").toLocaleString() : ""}</div></td></tr>`).join("")}</table></div>
    </div>
    <div class="card" id="polView"></div>`;
  document.querySelectorAll("[data-review]").forEach((b) => (b.onclick = () => showVersion(Number(b.dataset.review))));
  document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => showVersion(Number(b.dataset.view))));
  $("#amGo").onclick = readAmendment;
  $("#edGo").onclick = () => openEditor(latest);
  showVersion((drafts[0] || inForce || latest).id);
}

async function readAmendment() {
  if (!needUser()) return;
  const fd = new FormData(); const f = $("#amFile").files[0];
  if (f) fd.append("file", f); fd.append("text", $("#amText").value);
  if (!f && !$("#amText").value.trim()) return toast("Upload a document or describe the change", true);
  $("#amOut").innerHTML = `<p class="muted">Reading the amendment… with AI this can take up to a minute.</p>`;
  try {
    const r = await api("/api/policies/amend", { method: "POST", body: fd });
    $("#amOut").innerHTML = `<p class="small muted">Read with: ${esc(r.method)}</p>`;
    await renderPolicy(); showVersion(r.draft.id);
  } catch (e) { $("#amOut").innerHTML = `<div class="why" style="margin-top:10px;color:var(--bad)">${esc(e.message)}<br>Use “Edit rules directly…” to make this change.</div>`; }
}

async function showVersion(id) {
  const v = await api(`/api/policies/${id}`);
  const isDraft = v.status === "draft";
  $("#polView").innerHTML = `<div class="row"><h2 style="margin:0">v${esc(v.version)} — ${esc(v.status)}${v.in_force_today ? " (in force today)" : ""}</h2><div class="spacer"></div>
      ${isDraft ? `<button class="btn" id="edDraft">Adjust in editor</button><button class="btn danger" id="rej">Reject</button><button class="btn primary" id="appr">Approve &amp; activate from ${esc(v.effective_date)}</button>` : ""}</div>
    <p class="muted small">Effective ${esc(v.effective_date)} · created by ${esc(v.created_by)} from ${esc(v.source_type)}${v.approved_by ? ` · approved by ${esc(v.approved_by)}` : ""}${v.review_note ? ` · note: ${esc(v.review_note)}` : ""}</p>
    <h3>${isDraft ? "What will change" : "Changes in this version"}</h3>
    ${(v.changes || []).map((c) => `<div class="change">${esc(c)}</div>`).join("")}
    ${v.source_text ? `<details><summary class="small muted">Source text</summary><pre class="rule" style="white-space:pre-wrap">${esc(v.source_text)}</pre></details>` : ""}
    <h3>Decision rules (checked in this order; first match decides)</h3>
    ${v.rules_text.map((t) => `<div class="rule">${esc(t)}</div>`).join("")}
    <h3>Parameters</h3><div class="kv small">${Object.entries(v.body.parameters || {}).map(([k, x]) => `<div>${esc(k.replace(/_/g, " "))}</div><div>${esc(x)}</div>`).join("")}
      <div>supplier status overrides</div><div>${esc(JSON.stringify(v.body.supplier_status_overrides || {}))}</div>
      <div>ranking weights</div><div>${esc(JSON.stringify((v.body.preferred_suppliers || {}).weights || {}))}</div></div>`;
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
  const ruleBox = (r, i) => `<div class="ruleedit" data-i="${i}"><div class="row">
      <input class="rId" value="${esc(r.id)}" style="width:70px" title="Rule id"><input class="rName" value="${esc(r.name || "")}" placeholder="Name" style="flex:1">
      <select class="rDec">${["PO", "SOURCING_REQUEST"].map((x) => `<option ${x === r.decision ? "selected" : ""}>${x}</option>`).join("")}</select>
      <select class="rRoute">${meta.routes.map((x) => `<option ${x === r.route ? "selected" : ""}>${x}</option>`).join("")}</select>
      <button class="btn small" data-mv="-1">↑</button><button class="btn small" data-mv="1">↓</button><button class="btn small danger" data-del>✕</button></div>
      <label>Conditions (JSON). Facts: ${Object.keys(meta.facts).join(", ")}. Ops: ${meta.ops.join(", ")}</label>
      <textarea class="rWhen">${esc(JSON.stringify(r.when || { all: [] }))}</textarea>
      <div class="row"><label style="margin:0">Supplier</label><textarea class="rSup" style="min-height:34px">${esc(JSON.stringify(r.supplier || { mode: "none" }))}</textarea><input class="rNote" placeholder="Note (optional)" value="${esc(r.note || "")}" style="flex:1"></div></div>`;
  $("#polView").innerHTML = `<div class="row"><h2 style="margin:0">Policy editor — new version based on v${esc(base.version)}</h2><div class="spacer"></div><button class="btn primary" id="edSave">Create draft for review</button></div>
    <div class="grid3"><div><label>New version</label><input id="edVer" value="${esc(b.version)}"></div><div><label>Effective date</label><input id="edEff" type="date" value="${esc(b.effective_date)}"></div><div><label>Note</label><input id="edNote" placeholder="Why this change"></div></div>
    <h3>Parameters</h3><div class="grid3">${Object.entries(b.parameters).map(([k, x]) => `<div><label>${esc(k.replace(/_/g, " "))}</label><input class="edParam" data-k="${k}" type="number" value="${x}"></div>`).join("")}</div>
    <label>Supplier status overrides (JSON, e.g. {"S014":"Blocked"})</label><input id="edOver" value='${esc(JSON.stringify(b.supplier_status_overrides || {}))}' style="width:100%">
    <label>Preferred-supplier ranking weights (JSON)</label><input id="edW" value='${esc(JSON.stringify(b.preferred_suppliers.weights))}' style="width:100%">
    <h3>Rules (first match decides)</h3><div id="edRules">${b.rules.map(ruleBox).join("")}</div>
    <button class="btn" id="edAdd">+ Add rule</button>`;
  const collect = () => {
    const rules = [...document.querySelectorAll(".ruleedit")].map((el) => ({
      id: $(".rId", el).value.trim(), name: $(".rName", el).value.trim(), decision: $(".rDec", el).value, route: $(".rRoute", el).value,
      when: JSON.parse($(".rWhen", el).value || "{}"), supplier: JSON.parse($(".rSup", el).value || '{"mode":"none"}'), ...($(".rNote", el).value ? { note: $(".rNote", el).value } : {}) }));
    const nb = JSON.parse(JSON.stringify(b));
    nb.version = $("#edVer").value.trim(); nb.effective_date = $("#edEff").value; nb.rules = rules;
    document.querySelectorAll(".edParam").forEach((i) => (nb.parameters[i.dataset.k] = Number(i.value)));
    nb.supplier_status_overrides = JSON.parse($("#edOver").value || "{}");
    nb.preferred_suppliers.weights = JSON.parse($("#edW").value || "{}");
    return nb;
  };
  const rewire = () => {
    document.querySelectorAll("[data-mv]").forEach((btn) => (btn.onclick = () => { const el = btn.closest(".ruleedit"); const d = Number(btn.dataset.mv); if (d < 0 && el.previousElementSibling) el.parentNode.insertBefore(el, el.previousElementSibling); if (d > 0 && el.nextElementSibling) el.parentNode.insertBefore(el.nextElementSibling, el); }));
    document.querySelectorAll("[data-del]").forEach((btn) => (btn.onclick = () => btn.closest(".ruleedit").remove()));
  };
  rewire();
  $("#edAdd").onclick = () => { $("#edRules").insertAdjacentHTML("afterbegin", ruleBox({ id: "new", name: "", decision: "SOURCING_REQUEST", route: "RFQ", when: { all: [{ fact: "plant", op: "eq", value: "CHK1" }] }, supplier: { mode: "suggest", count: 3 } }, 0)); rewire(); };
  $("#edSave").onclick = async () => {
    if (!needUser()) return;
    let nb; try { nb = collect(); } catch (e) { return toast("Invalid JSON in a rule: " + e.message, true); }
    try { const r = await api("/api/policies/draft", { method: "POST", body: { body: nb, based_on: fromDraft ? base.based_on : base.id, replaces_draft: fromDraft ? base.id : null, note: $("#edNote").value } }); toast("Draft created — review and approve"); await renderPolicy(); showVersion(r.draft.id); } catch (e) { toast(e.message, true); }
  };
  $("#polView").scrollIntoView({ behavior: "smooth" });
}

// contracts
async function renderContracts() {
  const cs = await api("/api/contracts");
  view.innerHTML = `<h1>Rate contracts</h1><p class="sub">Contracts from the register plus signed PDFs. Contracts signed after the monthly register update can be added from their PDF.</p>
    <div class="split"><div class="card scroll"><table><tr><th>Contract</th><th>Supplier</th><th>Validity</th><th>Ceiling used (register)</th><th>Items</th><th>Source</th></tr>
      ${cs.map((c) => { const pct = c.value_cap ? Math.min(100, (100 * c.consumed_value) / c.value_cap) : 0; return `<tr><td><b>${esc(c.contract_id)}</b>${c.valid_today ? "" : '<div class="small muted">not valid today</div>'}</td>
        <td>${esc(c.supplier_id)} ${esc(c.supplier_name)}${c.supplier_status !== "Active" ? `<div class="small" style="color:var(--bad)">${esc(c.supplier_status)} — cannot be used</div>` : ""}</td>
        <td class="small">${esc(c.start_date)} → ${esc(c.end_date)}</td>
        <td><div class="bar ${pct > 90 ? "hot" : ""}"><i style="width:${pct}%"></i></div><div class="small muted">${inr(c.consumed_value)} of ${inr(c.value_cap)}</div></td>
        <td class="small">${c.lines.map((l) => `${esc(l.item_code)} @ ${inr(l.rate)}`).join("<br>")}</td><td class="small">${esc(c.source)}${c.pdf_name ? `<div class="muted">${esc(c.pdf_name)}</div>` : ""}</td></tr>`; }).join("")}</table></div>
      <div class="card"><h3>Add a contract</h3><label>Signed contract PDF</label><input type="file" id="cPdf" accept=".pdf"><div class="row" style="margin-top:8px"><button class="btn primary" id="cExtract">Extract from PDF</button><button class="btn" id="cManual">Enter manually</button></div><div id="cForm"></div></div></div>`;
  $("#cExtract").onclick = async () => { const f = $("#cPdf").files[0]; if (!f) return toast("Choose a PDF", true); const fd = new FormData(); fd.append("file", f); try { contractForm(await api("/api/contracts/extract-pdf", { method: "POST", body: fd })); } catch (e) { toast(e.message, true); } };
  $("#cManual").onclick = () => contractForm({ lines: [{}], warnings: [] });
}

function contractForm(d) {
  $("#cForm").innerHTML = `${(d.warnings || []).map((w) => `<div class="small" style="color:var(--warn)">⚠ ${esc(w)}</div>`).join("")}
    <p class="small muted">Check the extracted values against the PDF before saving.</p>
    <div class="grid2"><div><label>Contract no.</label><input id="cfId" value="${esc(d.contract_id || "")}"></div><div><label>Supplier ID</label><input id="cfSup" value="${esc(d.supplier_id || "")}"></div>
    <div><label>Start date</label><input id="cfStart" type="date" value="${esc(d.start_date || "")}"></div><div><label>End date</label><input id="cfEnd" type="date" value="${esc(d.end_date || "")}"></div>
    <div><label>Value ceiling (₹)</label><input id="cfCap" type="number" value="${esc(d.value_cap || "")}"></div><div><label>Consumed so far (₹)</label><input id="cfCons" type="number" value="${esc(d.consumed_value || 0)}"></div></div>
    <label>Items (item_code, rate per line)</label><textarea id="cfLines">${esc((d.lines || []).filter((l) => l.item_code).map((l) => `${l.item_code}, ${l.rate}`).join("\n"))}</textarea>
    <button class="btn primary" id="cfSave">Save contract</button>`;
  $("#cfSave").onclick = async () => {
    if (!needUser()) return;
    const lines = $("#cfLines").value.split("\n").map((x) => x.split(",").map((y) => y.trim())).filter((x) => x[0]).map(([item_code, rate]) => ({ item_code, rate: Number(rate) }));
    try { await api("/api/contracts", { method: "POST", body: { contract_id: $("#cfId").value.trim(), supplier_id: $("#cfSup").value.trim(), supplier_name: d.supplier_name, start_date: $("#cfStart").value, end_date: $("#cfEnd").value, value_cap: Number($("#cfCap").value), consumed_value: Number($("#cfCons").value || 0), lines, pdf_name: d.pdf_name } }); toast("Contract saved"); renderContracts(); } catch (e) { toast(e.message, true); }
  };
}

// suppliers
async function renderSuppliers() {
  const items = await api("/api/items");
  const d = store.get("asOf", new Date().toISOString().slice(0, 10));
  view.innerHTML = `<h1>Preferred supplier lists</h1><p class="sub">Built from PO history (548 days) and contracts, Active suppliers only. Score = frequency + recency + contract + on-time + quality, weighted as set in the policy.</p>
    <div class="card row"><label style="margin:0">Item</label><select id="sItem">${items.map((i) => `<option value="${i.item_code}">${i.item_code} — ${esc(i.description)}</option>`).join("")}</select>
      <label style="margin:0">As of</label><input type="date" id="sDate" value="${d}"><button class="btn primary" id="sGo">Show</button><div class="spacer"></div>
      <a class="btn" id="sExp">Export top-3 CSV for all items</a></div><div class="card" id="sOut"></div>`;
  const go = async () => {
    store.set("asOf", $("#sDate").value);
    $("#sExp").href = `/api/preferred-suppliers/export.csv?as_of=${$("#sDate").value}`;
    const r = await api(`/api/preferred-suppliers?item_code=${$("#sItem").value}&as_of=${$("#sDate").value}`);
    $("#sOut").innerHTML = r.suppliers.length ? `<p class="small muted">Policy v${esc(r.policy_version)} weights, as of ${esc(r.as_of)}</p><table><tr><th>Rank</th><th>Supplier</th><th class="num">POs</th><th>Last PO</th><th>Contract</th><th class="num">On-time</th><th class="num">Quality</th><th class="num">Score</th><th>Score parts</th><th class="num">Reference price</th></tr>
      ${r.suppliers.map((s) => `<tr><td>${s.rank}</td><td>${esc(s.supplier_id)} ${esc(s.name)}</td><td class="num">${s.po_count}</td><td>${esc(s.last_po_date || "—")}</td><td>${s.has_contract ? "yes" : "no"}</td><td class="num">${s.on_time_pct}%</td><td class="num">${s.quality_rating}</td><td class="num"><b>${s.score.toFixed(3)}</b></td><td class="small muted">${Object.entries(s.components).map(([k, v]) => `${k} ${v.toFixed(3)}`).join(" + ")}</td><td class="num">${inr(s.reference_price)}<div class="small muted">${esc(s.reference_source)}</div></td></tr>`).join("")}</table>` : `<div class="empty">No eligible supplier: no PO in 548 days and no contract. Sourcing will suggest category suppliers.</div>`;
  };
  $("#sGo").onclick = go; $("#sItem").onchange = go; go();
}

// data
async function renderData() {
  const h = await refreshHealth();
  const s = h.data;
  view.innerHTML = `<h1>Master data</h1><p class="sub">Upload the item master, supplier master, PO history, plants, contract register, contract lines and contract PDFs. Files are recognised by their columns; re-uploading replaces the previous version.</p>
    <div class="grid2"><div class="card"><h3>Status</h3><div class="kv">${["item_master", "supplier_master", "po_history", "plants", "contracts"].map((k) => `<div>${k.replace("_", " ")}</div><div>${s[k] ? `✓ ${s[k]} rows` : '<span style="color:var(--bad)">missing</span>'}</div>`).join("")}</div>
      <p class="small">${s.ready ? "Ready." : "Upload the missing files to start."} AI: ${esc(h.llm || "not configured")}. Today: ${esc(h.today)}.</p></div>
      <div class="card"><h3>Upload</h3><input type="file" id="dFiles" multiple accept=".csv,.pdf"><div class="row" style="margin-top:10px"><button class="btn primary" id="dGo">Upload</button></div><div id="dOut" class="small"></div></div></div>`;
  $("#dGo").onclick = async () => {
    if (!needUser()) return;
    const fs = $("#dFiles").files; if (!fs.length) return toast("Choose files", true);
    const fd = new FormData(); [...fs].forEach((f) => fd.append("files", f));
    try { const r = await api("/api/data/upload", { method: "POST", body: fd }); toast("Uploaded"); await renderData(); $("#dOut").innerHTML = `Loaded: ${esc(JSON.stringify(r.loaded))}${r.skipped.length ? `<br>Skipped: ${esc(r.skipped.join(", "))}` : ""}`; } catch (e) { toast(e.message, true); }
  };
}

// audit
async function renderAudit() {
  const a = await api("/api/audit");
  view.innerHTML = `<h1>Audit log</h1><p class="sub">Every upload, policy change, approval, override and PR, with who and when.</p>
    <div class="card scroll"><table><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Details</th></tr>${a.map((x) => `<tr><td class="small">${new Date(x.at + "Z").toLocaleString()}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.entity)}</td><td class="small muted">${esc(JSON.stringify(x.details)).slice(0, 220)}</td></tr>`).join("")}</table></div>`;
}

refreshHealth();
route();
