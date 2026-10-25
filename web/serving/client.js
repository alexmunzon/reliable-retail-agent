// Minimal fetch-SSE client for the local serving pilot.
//
// The bearer token is held only in the `token` variable below: never in localStorage,
// sessionStorage, cookies, the URL, or the DOM after it is read. EventSource cannot send an
// Authorization header, so the stream is read with fetch() and parsed here. All server text is
// rendered with textContent, never as HTML.
//
// Every asynchronous result is checked against the run, token and stream it was started for
// before it is applied, so a late answer can never relabel what the page shows or acts on.
"use strict";

const TERMINAL = new Set(["succeeded", "failed", "rejected", "cancelled", "expired", "invalidated"]);
const TOKEN_PATTERN = /^rra_[A-Za-z0-9_-]{43}$/;

let token = null;
let runId = null;
let cursor = null;          // Last delivered event id; sent as Last-Event-ID on resume.
let retryMs = 2000;         // Updated from the server's `retry:` field.
let streamGen = 0;          // Bumped by every open/close; a continuation with an old value stops.
let stream = null;          // AbortController of the open stream.
let reconnectTimer = null;
let refreshTimer = null;
let busy = false;           // One command in flight at a time: no double submission.
let pending = null;         // {label, runId, attempt()}: the last command, retried with the SAME keys.
let snapshot = null;

const $ = (id) => document.getElementById(id);

function say(text, kind = "") {
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
  if (kind) item.className = kind;
  $("log").prepend(item);
}

function newKey() {
  return "demo-" + crypto.randomUUID();
}

// Returns {status, data}, or {status: 0} when no answer arrived (it may or may not have landed).
async function request(method, path, body, key) {
  const used = token;
  if (!used) return { status: -1, data: { code: "no_token" } };
  const init = { method, headers: { Authorization: `Bearer ${used}` }, credentials: "omit", cache: "no-store",
                 redirect: "error" };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (key) init.headers["Idempotency-Key"] = key;
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    return { status: 0, data: { code: "no_answer", message: error.message } };
  }
  let data = null;
  try { data = await response.json(); } catch { /* Non-JSON error body. */ }
  if (response.status === 401 && token === used) forget("Token rejected (revoked, rotated or wrong). Paste a current one.");
  return { status: response.status, data: data || {} };
}

function report(label, { status, data }) {
  if (status === -1) return say(`${label}: set a bearer token first.`, "bad");
  if (status === 0) {
    return say(`${label}: no answer (${data.message}). It may or may not have landed; ` +
               "Retry resends the same Idempotency-Key.", "warn");
  }
  const detail = data.code || data.decision || data.state || "";
  const retryAfter = data.retry_after_seconds ? ` Retry after ${data.retry_after_seconds}s with the same key.` : "";
  say(`${label}: HTTP ${status} ${detail}${data.message ? " - " + data.message : ""}` +
      `${data.detail ? " - " + data.detail : ""}${retryAfter}`,
      status >= 500 || status === 429 ? "warn" : status >= 400 ? "bad" : "ok");
}

// Runs `attempt` with the busy guard. The same `pending` object (same keys) is what Retry resends.
async function run(cmd) {
  if (busy) return say("Wait for the previous command to finish.", "warn");
  busy = true;
  pending = cmd;
  try {
    await cmd.attempt();
  } finally {
    busy = false;
  }
}

function keyed(label, method, path, body, forRun = runId) {
  const key = newKey();
  return { label, runId: forRun, attempt: async () => {
    report(label, await request(method, path, body, key));
    if (runId === forRun) scheduleRefresh(0);
  } };
}

// ------------------------------------------------------------------ token
$("token-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("token").value.trim();
  $("token").value = "";
  if (!TOKEN_PATTERN.test(value)) {
    $("token-state").textContent = "That does not look like an rra_ token.";
    return;
  }
  token = value;
  $("token-state").textContent = "Token held in memory for this page only.";
  if (runId) openStream();
});

function forget(reason = "No token.") {
  token = null;
  closeStream();
  $("token-state").textContent = reason;
  setStreamState("Closed: no token.");
}
$("forget").addEventListener("click", () => forget());

// ------------------------------------------------------------------ run lifecycle
// Creating a session and then a run is one intent with two keys, fixed when the form is
// submitted. Retry replays both: the server returns the stored session, then creates or
// returns the run, so a lost response never leaves the request half done or doubled.
$("request-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const field = (name) => String(form.get(name) || "").trim();
  const address = {};
  for (const name of ["street", "apartment", "city", "state", "postal_code", "country"]) address[name] = field(name);
  const sessionKey = newKey(), runKey = newKey(), resource = field("resource_id");
  run({ label: "Create session and run", runId: null, attempt: async () => {
    const session = await request("POST", "/api/v1/sessions", { resource_id: resource }, sessionKey);
    report("Create session", session);
    if (session.status !== 201) return;
    const created = await request("POST", `/api/v1/sessions/${session.data.session_id}/runs`,
                                  { request: { type: "change_order_address", address } }, runKey);
    report("Create run", created);
    if (created.status === 202 && created.data.run_id) adoptRun(created.data.run_id);
  } });
});

// The page switches runs only once the server has accepted the new one.
function adoptRun(id) {
  if (id === runId) return;
  closeStream();
  runId = id;
  cursor = snapshot = null;
  if (pending && pending.runId !== null) pending = null;  // Never retry an old run's command on this one.
  $("events").replaceChildren();
  $("prompt").textContent = "Loading the prompt...";
  refresh().then((ok) => { if (ok) openStream(); });
}

$("consent-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const proposal = snapshot && snapshot.run_id === runId && snapshot.proposal;
  if (!runId || !proposal || !proposal.prompt) return say("No prompt is awaiting an answer.", "bad");
  // Bound to the exact prompt, version and payload hash rendered from this same snapshot.
  run(keyed("Consent", "POST", `/api/v1/runs/${runId}/consents`, {
    response: $("answer").value, prompt_id: proposal.prompt_id,
    proposal_version: proposal.proposal_version, payload_hash: proposal.payload_hash,
  }));
});

$("cancel").addEventListener("click", () => {
  if (!runId) return say("No run to cancel.", "bad");
  run(keyed("Cancel", "POST", `/api/v1/runs/${runId}/cancel`, {}));
});

$("retry").addEventListener("click", () => {
  if (!pending) return say("Nothing to retry for this run.", "bad");
  run(pending);
});

// Applies a snapshot only if it is still for the run on screen. Returns true on success.
async function refresh() {
  const forRun = runId;
  if (!forRun || !token) return false;
  const result = await request("GET", `/api/v1/runs/${forRun}`);
  if (runId !== forRun) return false;
  if (result.status !== 200) {
    report("Snapshot", result);
    return false;
  }
  snapshot = result.data;
  $("snapshot").textContent = JSON.stringify(snapshot, null, 2);
  const proposal = snapshot.proposal;
  $("prompt").textContent = proposal.prompt
    ? `${proposal.prompt}\n\nprompt_id ${proposal.prompt_id} · version ${proposal.proposal_version}\n` +
      `payload_hash ${proposal.payload_hash}`
    : `No prompt is awaiting an answer (proposal state: ${proposal.state}; run state: ${snapshot.state}).`;
  return true;
}
$("refresh").addEventListener("click", () => refresh());

function scheduleRefresh(delayMs) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, delayMs);
}

// ------------------------------------------------------------------ fetch-SSE
function setStreamState(text) {
  $("stream-state").textContent = text;
}

function closeStream() {
  streamGen += 1;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (stream) stream.abort();
  stream = null;
}

function scheduleReconnect(gen, delayMs, why) {
  if (gen !== streamGen) return;
  clearTimeout(reconnectTimer);
  setStreamState(`${why} Reconnecting in ${Math.round(delayMs / 1000)}s from ${cursor || "the start"}.`);
  reconnectTimer = setTimeout(() => { if (gen === streamGen) openStream(); }, delayMs);
}

$("drop").addEventListener("click", () => {
  closeStream();
  setStreamState(`Dropped by you. Last-Event-ID kept: ${cursor || "none"}.`);
});
$("resume").addEventListener("click", () => openStream());

async function openStream() {
  if (!runId || !token) return;
  closeStream();
  const gen = streamGen;
  const controller = new AbortController();
  stream = controller;
  const headers = { Authorization: `Bearer ${token}`, Accept: "text/event-stream" };
  if (cursor) headers["Last-Event-ID"] = cursor;
  let response;
  try {
    response = await fetch(`/api/v1/runs/${runId}/events`, {
      headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal,
    });
  } catch (error) {
    return scheduleReconnect(gen, retryMs, "Connection failed.");
  }
  if (gen !== streamGen) return;
  if (response.status !== 200) {
    let data = {};
    try { data = await response.json(); } catch { /* ignore */ }
    if (gen !== streamGen) return;
    if (response.status === 401) return forget("Stream refused: token rejected. Paste a current one.");
    if (response.status === 410) return recoverFromSnapshot(gen, "Cursor past retention (HTTP 410).");
    if (response.status === 429) {
      return scheduleReconnect(gen, (data.retry_after_seconds || 5) * 1000, "Too many streams.");
    }
    return setStreamState(`Stream refused: HTTP ${response.status} ${data.code || ""}.`);
  }
  setStreamState(`Connected${cursor ? `, resuming after ${cursor}` : ""}.`);
  let expired = false;
  try {
    for await (const event of parseSSE(response.body, controller.signal)) {
      if (gen !== streamGen) return;
      if (event.event === "serving.replay_expired") { expired = true; break; }
      if (event.id) cursor = event.id;
      showEvent(event);
    }
  } catch (error) { /* Interrupted: decided below from the snapshot. */ }
  if (gen !== streamGen) return;  // Superseded by a newer stream or closed by the user.
  stream = null;
  if (expired) return recoverFromSnapshot(gen, "Events needed for this cursor were purged.");
  const ok = await refresh();
  if (gen !== streamGen || !token) return;
  // Complete only when the run is terminal AND every event up to its last one was delivered.
  if (ok && TERMINAL.has(snapshot.state) && cursor === snapshot.last_event_id) {
    return setStreamState(`Run ${snapshot.state}. Stream complete.`);
  }
  scheduleReconnect(gen, retryMs, "Stream ended.");
}

// After a replay gap the snapshot is the record: show it and tail from its last event. Only a
// snapshot fetched now, for this run, counts; a stale one would reuse the expired cursor.
async function recoverFromSnapshot(gen, why) {
  const ok = await refresh();
  if (gen !== streamGen || !token) return;
  if (!ok) return setStreamState(`${why} Snapshot unavailable; press Resume to try again.`);
  cursor = snapshot.last_event_id;
  say(`${why} Earlier events are no longer retained; recovered from the snapshot, tailing after ${cursor}.`, "warn");
  if (TERMINAL.has(snapshot.state)) return setStreamState(`${why} Run ${snapshot.state}.`);
  scheduleReconnect(gen, retryMs, why);
}

function showEvent(event) {
  let envelope = {};
  try { envelope = JSON.parse(event.data); } catch { /* Show the raw type only. */ }
  const payload = envelope.payload || {};
  const item = document.createElement("li");
  const id = document.createElement("code");
  id.textContent = event.id || "";
  const type = document.createElement("strong");
  type.textContent = ` ${event.event} `;
  const text = document.createElement("span");
  text.textContent = [payload.title, payload.reason, payload.classification, payload.outcome, payload.note]
    .filter(Boolean).join(" · ");
  item.append(id, type, text);
  $("events").append(item);
  scheduleRefresh(250);  // One snapshot fetch per burst of events, not one per event.
}

// Incremental text/event-stream parser: tolerates any chunk boundary and CR, LF or CRLF line ends.
async function* parseSSE(body, signal) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fields = {};
  signal.addEventListener("abort", () => reader.cancel().catch(() => {}), { once: true });
  for (;;) {
    const { value, done } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    if (done && buffer.endsWith("\r")) buffer += "\n";  // At EOF a trailing CR is a complete line end.
    let match;
    while ((match = /\r\n|\r|\n/.exec(buffer))) {
      if (match[0] === "\r" && match.index + 1 === buffer.length) break;  // Maybe half of a CRLF.
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      if (line === "") {
        if (fields.data !== undefined || fields.id !== undefined) {
          yield { id: fields.id, event: fields.event || "message", data: (fields.data || []).join("\n") };
        }
        fields = {};
      } else if (!line.startsWith(":")) {
        const colon = line.indexOf(":");
        const name = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (name === "data") (fields.data = fields.data || []).push(value);
        else if (name === "retry" && /^\d+$/.test(value)) retryMs = Number(value);
        else if (name === "id" || name === "event") fields[name] = value;
      }
    }
    if (done) return;
  }
}
