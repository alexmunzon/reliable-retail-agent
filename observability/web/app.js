"use strict";
// Read-only dashboard client. All text from data goes through textContent (el()); no HTML strings are parsed.

const state = { page: 1, pageSize: 20, filters: {}, items: [], selected: null, meta: null };

const STATUS_TEXT = {
  completed: "Completed", failed: "Failed", unknown_outcome: "Unknown outcome", cancelled: "Cancelled",
  incomplete: "Incomplete evidence", invalid_evidence: "Invalid evidence",
};
const OUTCOME_TEXT = {
  effect_confirmed: "Effect confirmed", no_effect_confirmed: "No effect (confirmed)", unknown: "Unknown",
  outcome_missing: "Outcome missing from evidence", not_attempted: "Not attempted",
};

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function ms(ns) {
  if (ns === null || ns === undefined) return "unknown";
  const value = ns / 1e6;
  return `${Number.isInteger(value) ? value : value.toFixed(3)} ms`;
}

function duration(item) {
  if (item.duration_kind === "measured") return ms(item.request_duration_ns);
  if (item.duration_kind === "lower_bound") return `≥ ${ms(item.lower_bound_ns)} (lower bound)`;
  return "unknown";
}

function moneyMap(map, empty) {
  const entries = Object.entries(map || {});
  if (!entries.length) return empty;
  return entries.map(([currency, amount]) => `${currency} ${amount}`).join(" + ");
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" });
  const body = await response.json().catch(() => ({ error: "unreadable_response" }));
  if (!response.ok) throw Object.assign(new Error(body.error || `http_${response.status}`), { status: response.status });
  return body;
}

function query(extra) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...state.filters, ...extra })) if (value) params.set(key, value);
  const text = params.toString();
  return text ? `?${text}` : "";
}

function toUtc(local) {
  return local ? `${local.length === 16 ? local : local.slice(0, 16)}:00Z` : "";
}

function statusChip(status) {
  return el("span", { class: `chip s-${status}`, text: STATUS_TEXT[status] || status });
}

// ---------- list

async function loadList(focusIndex) {
  const status = document.getElementById("list-status");
  const list = document.getElementById("request-list");
  status.textContent = "Loading requests…";
  list.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/requests${query({ page: state.page, page_size: state.pageSize })}`);
    state.items = data.items;
    state.page = data.page;
    list.replaceChildren(...data.items.map((item, index) => listRow(item, index)));
    status.textContent = data.total ? `${data.total} request(s) match` : "No requests match these filters.";
    document.getElementById("page-label").textContent = `Page ${data.page} of ${data.pages}`;
    document.getElementById("prev").disabled = data.page <= 1;
    document.getElementById("next").disabled = data.page >= data.pages;
    if (focusIndex !== undefined) focusRow(focusIndex);
  } catch (error) {
    list.replaceChildren();
    status.textContent = `Could not load requests (${error.message}).`;
  } finally {
    list.removeAttribute("aria-busy");
  }
  loadAggregates();
}

function listRow(item, index) {
  const flags = [...item.completeness, ...item.flags];
  const button = el("button", {
    type: "button", class: "row", "data-key": item.key, "data-index": index,
    "aria-current": state.selected === item.key ? "true" : null,
    onclick: () => openRequest(item.key),
    onkeydown: (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); focusRow(index + 1); }
      if (event.key === "ArrowUp") { event.preventDefault(); focusRow(index - 1); }
    },
  },
  el("span", { class: "row-top" }, statusChip(item.status), el("span", { class: "row-key", text: item.key })),
  el("span", { class: "row-meta" },
    el("span", { text: item.route_class }),
    el("span", { text: duration(item) }),
    el("span", { text: item.estimated_charge ? `est. ${moneyMap(item.estimated_charge)}` : item.estimate_reason === "no_usage_records" ? "no usage recorded" : "est. unknown" }),
    el("span", { text: `${item.mutation_attempts} attempt(s)` })),
  flags.length ? el("span", { class: "row-flags", text: flags.join(" · ") }) : null);
  return el("li", {}, button);
}

function focusRow(index) {
  const rows = [...document.querySelectorAll("#request-list .row")];
  if (!rows.length) return;
  rows[Math.max(0, Math.min(index, rows.length - 1))].focus();
}

async function loadAggregates() {
  const box = document.getElementById("aggregates");
  box.replaceChildren(el("p", { class: "hint", text: "Loading aggregates…" }));
  try {
    const filters = { from: state.filters.from, to: state.filters.to };
    const groups = await api(`/api/aggregates${query(filters)}`);
    const names = Object.keys(groups);
    if (!names.length) { box.replaceChildren(el("p", { class: "hint", text: "No requests in this window." })); return; }
    box.replaceChildren(...names.map((name) => aggregateCard(name, groups[name])));
  } catch (error) {
    box.replaceChildren(el("p", { class: "error", text: `Aggregates unavailable (${error.message}).` }));
  }
}

function aggregateCard(name, agg) {
  const lat = agg.latency;
  const excluded = Object.entries(lat.excluded).map(([k, v]) => `${k}: ${v}`).join(", ") || "none";
  const counts = Object.entries(agg.status_counts).map(([k, v]) => `${STATUS_TEXT[k] || k} ${v}`).join(" · ");
  return el("article", { class: "agg", "data-group": name },
    el("h3", { text: name }),
    el("p", { text: `${agg.requests} request(s): ${counts}` }),
    el("dl", { class: "kv" },
      el("dt", { text: "Latency metric" }), el("dd", { text: `${lat.metric}; ${lat.method}; failed requests included` }),
      el("dt", { text: "Eligible samples" }), el("dd", { class: "agg-n", text: String(lat.eligible) }),
      el("dt", { text: "Excluded" }), el("dd", { text: excluded }),
      el("dt", { text: "Warmup excluded / process" }), el("dd", { text: String(lat.warmup_excluded_per_process) }),
      el("dt", { text: "p50" }), el("dd", { class: "agg-p50", text: lat.p50_ns === null ? "unknown (n=0)" : `${ms(lat.p50_ns)} (rank ${lat.p50_rank} of ${lat.eligible})` }),
      el("dt", { text: "p95" }), el("dd", { class: "agg-p95", text: lat.p95_ns === null ? "unknown (n=0)" : `${ms(lat.p95_ns)} (rank ${lat.p95_rank} of ${lat.eligible})` }),
      el("dt", { text: "Estimated charge" }), el("dd", { class: "agg-est", text: `${moneyMap(agg.costs.estimated_charge_complete_requests, "none")} (synthetic prices; complete requests only)` }),
      el("dt", { text: "Unknown estimates" }), el("dd", { text: `${agg.costs.requests_with_unknown_estimate} request(s); known subtotal ${moneyMap(agg.costs.known_subtotal_of_unknown_requests, "none")}` }),
      el("dt", { text: "Confirmed charge" }), el("dd", { text: moneyMap(agg.costs.confirmed_charge, "none (no receipts)") })),
    lat.warning ? el("p", { class: "warn", text: lat.warning }) : null);
}

// ---------- detail

async function openRequest(key, { push = true } = {}) {
  const pane = document.getElementById("detail");
  state.selected = key;
  document.querySelectorAll("#request-list .row").forEach((row) => {
    if (row.dataset.key === key) row.setAttribute("aria-current", "true"); else row.removeAttribute("aria-current");
  });
  pane.replaceChildren(el("p", { class: "hint", text: "Loading request…" }));
  document.body.classList.add("showing-detail");
  if (push) history.replaceState(null, "", `#${encodeURIComponent(key)}`);
  try {
    const request = await api(`/api/requests/${key.split("/").map(encodeURIComponent).join("/")}`);
    pane.replaceChildren(renderDetail(request));
    pane.focus();
  } catch (error) {
    pane.replaceChildren(el("div", { class: "error" },
      el("h2", { text: error.status === 404 ? "Not found" : "Could not load request" }),
      el("p", { text: error.status === 404 ? "No request with this key is visible to this viewer." : error.message })));
  }
}

function closeDetail() {
  document.body.classList.remove("showing-detail");
  const index = state.items.findIndex((item) => item.key === state.selected);
  focusRow(index < 0 ? 0 : index);
}

function banner(request) {
  const ev = request.evidence;
  const problems = [];
  if (ev.completeness.length) problems.push(`Incomplete evidence: ${ev.completeness.join(", ")}`);
  if (ev.contract_verdict === "INVALID") problems.push(`trace-v1 checker: INVALID (${Object.entries(ev.contract_codes).map(([k, v]) => `${k}×${v}`).join(", ")})`);
  if (ev.diagnostic_view) problems.push(`Diagnostic view: ${ev.duplicate_lines_in_artifact} repeated line(s) collapsed, ${ev.reordered_lines} line(s) reordered by sequence. The INVALID verdict stands.`);
  if (ev.flags.includes("repeated_attempt_across_runs")) problems.push("The same operation was attempted in more than one run (a real repeated provider attempt, not duplicate ingestion).");
  if (request.timing.telemetry_dropped) problems.push(`Telemetry: emitter reported ${request.timing.telemetry_dropped} dropped record(s).`);
  if (ev.sidecar_rejected) problems.push(`Telemetry: ${ev.sidecar_rejected} sidecar record(s) rejected (${Object.keys(request.timing.rejection_codes).join(", ")}).`);
  const level = ev.completeness.length || ev.contract_verdict === "INVALID" ? "bad" : problems.length ? "warn" : "ok";
  return el("div", { class: `banner ${level}`, role: level === "ok" ? "status" : "alert", "data-testid": "completeness" },
    el("strong", { text: level === "ok" ? "Evidence complete and contract-consistent" : "Evidence needs attention" }),
    level === "ok" ? el("p", { text: "trace-v1 checker: CONSISTENT. This is the system's own account, not proof of external effects." }) : el("ul", {}, problems.map((p) => el("li", { text: p }))),
    ev.caveats.length ? el("p", { class: "caveat", text: `Clock caveats: ${ev.caveats.join(", ")}. Wall times order display only; durations use one process's monotonic clock.` }) : null);
}

function operationsTable(request) {
  const ops = Object.values(request.operations);
  if (!ops.length) return el("p", { class: "hint", text: "No business operations in this request." });
  return el("div", { class: "table-wrap" }, el("table", { class: "ops" },
    el("thead", {}, el("tr", {}, ["Operation", "Tool", "Proposed", "Authorized", "Mutation attempts", "Outcome", "Confirmed by", "History"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, ops.map((op) => el("tr", { "data-op": op.operation_id },
      el("td", { class: "mono", text: op.operation_id }),
      el("td", { text: op.tool_name || "—" }),
      el("td", { text: `${op.prompts} prompt(s), ${op.distinct_payloads} payload(s)` }),
      el("td", { text: op.authorized ? "yes (not execution)" : "no" }),
      el("td", { class: "op-attempts", text: `${op.mutation_attempts}${op.attempts_across_runs > op.mutation_attempts ? ` (${op.attempts_across_runs} across runs)` : ""}` }),
      el("td", { class: `op-outcome o-${op.outcome}`, text: OUTCOME_TEXT[op.outcome] || op.outcome }),
      el("td", { text: op.confirmed_by || "—" }),
      el("td", { text: [
        op.terminal ? `terminal: ${op.terminal}` : null,
        op.invalidations ? `${op.invalidations} invalidation(s)` : null,
        op.history_has_unknown ? "was unknown" : null,
        op.reconciliations.length ? `reconciliations: ${op.reconciliations.join(" → ")}` : null,
        op.cancellation ? `cancel ${op.cancellation.replace("_", " ")}${op.cancellation === "after_claim" ? " (does not undo an effect)" : ""}` : null,
      ].filter(Boolean).join("; ") || "—" }))))));
}

function eventsTable(request) {
  return el("div", { class: "table-wrap" }, el("table", { class: "events" },
    el("thead", {}, el("tr", {}, ["Seq", "Wall time (display only)", "Event", "Operation", "Detail"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, request.events.map((e) => el("tr", {},
      el("td", { text: e.sequence }),
      el("td", { class: "mono", text: e.occurred_at || "not recorded" }),
      el("td", {}, el("span", { text: e.label }), el("br"), el("span", { class: "mono dim", text: e.event_type })),
      el("td", { class: "mono", text: e.operation_id || "—" }),
      el("td", { text: [e.tool_name, e.reason, e.outcome, e.by && `by ${e.by}`, e.error_class && `error ${e.error_class}`].filter(Boolean).join(" · ") || "—" }))))));
}

function timeline(request) {
  const t = request.timing;
  const lanes = Object.entries(t.lanes);
  const summary = el("dl", { class: "kv" },
    el("dt", { text: "Request completion" }), el("dd", { class: "t-request", text: t.duration_kind === "measured" ? ms(t.request_duration_ns) : t.duration_kind === "lower_bound" ? `unknown; ≥ ${ms(t.lower_bound_ns)} (longest nested span)` : "unknown" }),
    el("dt", { text: "Accepted-command response" }), el("dd", { text: ms(t.command_response_ns) }),
    el("dt", { text: "First event delivered" }), el("dd", { text: `${ms(t.first_event_ns)} (not time-to-first-token)` }),
    el("dt", { text: "Direct children sum" }), el("dd", { text: `${ms(t.direct_children_sum_ns)}${t.children_overlap ? " — children overlap; sums are not elapsed time" : ""}` }),
    el("dt", { text: "Reconciliation lookups" }), el("dd", { text: `${t.reconciliation_lookups} (${t.reconciliation_lookup_errors} failed) — lookups are not mutation retries` }),
    el("dt", { text: "Clock domains" }), el("dd", { text: t.clock_domains.join(", ") || "none" }));
  const classes = Object.entries(t.by_class);
  const byClass = classes.length ? el("div", { class: "table-wrap" }, el("table", { class: "classes" },
    el("thead", {}, el("tr", {}, ["Span class", "Spans", "Known durations", "Known total"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, classes.map(([name, c]) => el("tr", { "data-class": name },
      el("td", { text: name }), el("td", { text: c.count }), el("td", { text: c.known }), el("td", { class: "c-total", text: ms(c.total_ns) })))))) : null;
  const laneNodes = lanes.map(([domain, spans]) => {
    const extent = Math.max(1, ...spans.map((s) => s.offset_ns + (s.duration_ns || 0)));
    const shown = spans.slice(0, 200);
    return el("figure", { class: "lane" },
      el("figcaption", { text: `Clock domain ${domain} — offsets relative to this domain only (${spans.length} span(s)${spans.length > shown.length ? `, first ${shown.length} drawn` : ""})` }),
      el("ol", { class: "bars" }, shown.map((s) => {
        const left = (s.offset_ns / extent) * 100;
        const width = s.duration_ns === null ? Math.max(2, 100 - left) : Math.max(0.6, (s.duration_ns / extent) * 100);
        const bar = el("span", { class: `bar b-${s.span_class} st-${s.status}${s.duration_ns === null ? " open" : ""}` });
        bar.style.marginLeft = `${left}%`;
        bar.style.width = `${Math.min(width, 100 - left)}%`;
        return el("li", { title: `${s.span_id}` },
          el("span", { class: "bar-label", text: `${s.span_class}${s.attempt_no ? ` #${s.attempt_no}` : ""}${s.error_code ? ` (${s.error_code})` : ""} · ${s.duration_ns === null ? (s.end_clock_domain ? "ends in another clock domain: unknown" : "open: unknown") : ms(s.duration_ns)}` }),
          el("span", { class: "track" }, bar));
      })));
  });
  return el("div", {}, summary, byClass, laneNodes.length ? laneNodes : el("p", { class: "hint", text: "No accepted spans." }));
}

function costPanel(request) {
  const c = request.costs;
  const est = c.estimated_charge;
  const estTok = c.estimated_charge_from_estimated_tokens;
  const tokens = Object.entries(c.measured_tokens).map(([k, v]) => `${k} ${v}${c.measured_tokens_missing[k] ? ` (+${c.measured_tokens_missing[k]} not supplied)` : ""}`).join(" · ");
  const series = [
    ["Measured usage", c.usage.filter((u) => u.measurement === "measured").length ? tokens : "no measured usage records", "measured"],
    ["Estimated charge", est.total ? `${moneyMap(est.total)} via ${est.pricing_tables.join(", ")} (SYNTHETIC prices)` : `unknown — ${est.reason}${Object.keys(est.known_subtotal).length ? `; known subtotal ${moneyMap(est.known_subtotal)}` : ""}`, "estimated"],
    ["Estimated from estimated tokens", estTok.usage_records ? (estTok.total ? moneyMap(estTok.total) : `unknown — ${estTok.reason}`) : "none", "estimated-tokens"],
    ["Confirmed charge", Object.keys(c.confirmed_charge).length ? `${moneyMap(c.confirmed_charge)} (${c.confirmed_receipts} SYNTHETIC receipt(s))` : "none — no billing receipt", "confirmed"],
    ["Unknown", `${est.unknown_usage_records} usage record(s) without an estimate${c.duplicate_usage_identities ? `; ${c.duplicate_usage_identities} re-sent usage record(s) counted once` : ""}${c.uncertain_outcome ? "; cost kept although the business outcome is unknown" : ""}`, "unknown"],
  ];
  return el("div", {},
    el("dl", { class: "kv cost" }, series.flatMap(([label, value, id]) => [el("dt", { text: label }), el("dd", { "data-series": id, text: value })])),
    c.usage.length ? el("div", { class: "table-wrap" }, el("table", { class: "usage" },
      el("thead", {}, el("tr", {}, ["Usage record", "Model", "Kind", "Tokens (in/out/cache r/cache w/reasoning)", "Table", "Estimate"].map((h) => el("th", { scope: "col", text: h })))),
      el("tbody", {}, c.usage.map((u) => el("tr", {},
        el("td", { class: "mono", text: u.record_id }), el("td", { class: "mono", text: u.model_id }), el("td", { text: u.measurement }),
        el("td", { text: ["input", "output", "cache_read", "cache_write", "reasoning"].map((k) => u.tokens[k] === null ? "?" : u.tokens[k]).join(" / ") }),
        el("td", { text: u.table_id || "—" }),
        el("td", { text: u.estimate !== null ? `${u.currency} ${u.estimate}` : `unknown (${u.unknown_reason})` })))))) : null);
}

async function exportRequest(key) {
  try {
    const data = await api(`/api/export/${key.split("/").map(encodeURIComponent).join("/")}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const link = el("a", { href: URL.createObjectURL(blob), download: `${key.replace("/", "_")}.redacted.json` });
    document.body.append(link);
    link.click();
    URL.revokeObjectURL(link.href);
    link.remove();
  } catch (error) {
    alert(`Export failed (${error.message}).`);
  }
}

function renderDetail(r) {
  return el("article", { class: "detail", "data-key": r.key },
    el("div", { class: "detail-head" },
      el("button", { type: "button", class: "ghost back", onclick: closeDetail, text: "← Requests" }),
      el("h2", {}, statusChip(r.status), " ", el("span", { class: "mono", text: r.key })),
      el("p", { class: "dim", text: `${r.data_origin} · ${r.evidence_class} · route ${r.route_class} · artifact ${r.artifact_sha256.slice(0, 12)}…` }),
      el("ul", { class: "reasons" }, r.status_reasons.map((reason) => el("li", { text: reason })))),
    banner(r),
    el("section", {}, el("h3", { text: "Operations: proposed → authorized → attempted → confirmed" }), operationsTable(r)),
    el("section", {}, el("h3", { text: "Timeline" }), timeline(r)),
    el("section", {}, el("h3", { text: "Costs" }), costPanel(r)),
    el("section", {}, el("h3", { text: "Events (causal sequence order)" }), eventsTable(r)),
    el("section", { class: "export" },
      el("button", { type: "button", onclick: () => exportRequest(r.key), text: "Download redacted evidence (JSON)" }),
      el("p", { class: "hint", text: "Allowlisted fields only: hashes and ids, never prompt text, arguments or free-form payloads." })));
}

// ---------- boot

async function boot() {
  try {
    state.meta = await api("/api/meta");
  } catch (error) {
    document.getElementById("list-status").textContent = `Dashboard unavailable (${error.message}).`;
    return;
  }
  const meta = state.meta;
  document.getElementById("viewer").textContent = meta.viewer;
  document.getElementById("isolation").textContent = meta.isolation;
  document.getElementById("origin-badge").textContent = meta.synthetic ? "SYNTHETIC FIXTURES" : meta.data_origin;
  const statusSelect = document.getElementById("f-status");
  meta.statuses.forEach((s) => statusSelect.append(el("option", { value: s, text: STATUS_TEXT[s] || s })));
  const routes = await api("/api/aggregates").then((g) => [...new Set(Object.keys(g).map((k) => k.split("/")[2]))]).catch(() => []);
  const routeSelect = document.getElementById("f-route");
  routes.sort().forEach((r) => routeSelect.append(el("option", { value: r, text: r })));
  if (meta.set_problems.length || meta.artifact_redeliveries) {
    const box = document.getElementById("set-banner");
    box.hidden = false;
    box.replaceChildren(el("strong", { text: "Fixture set notes: " }),
      el("span", { text: meta.set_problems.map((p) => `${p.case || "set"}: ${p.code}${p.count ? ` ×${p.count}` : ""}`).join(" · ") }));
  }
  document.getElementById("filters").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    state.filters = { status: form.get("status"), route: form.get("route"), from: toUtc(form.get("from")), to: toUtc(form.get("to")) };
    state.page = 1;
    loadList();
  });
  document.getElementById("f-reset").addEventListener("click", () => {
    document.getElementById("filters").reset();
    state.filters = {};
    state.page = 1;
    loadList();
  });
  document.getElementById("prev").addEventListener("click", () => { state.page -= 1; loadList(0); });
  document.getElementById("next").addEventListener("click", () => { state.page += 1; loadList(0); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("showing-detail")) closeDetail();
  });
  window.addEventListener("hashchange", () => {
    if (location.hash.length > 1) openRequest(decodeURIComponent(location.hash.slice(1)), { push: false });
  });
  await loadList();
  if (location.hash.length > 1) openRequest(decodeURIComponent(location.hash.slice(1)), { push: false });
}

boot();
