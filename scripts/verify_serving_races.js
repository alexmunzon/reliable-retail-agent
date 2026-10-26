#!/usr/bin/env node
"use strict";
// Exposed regressions against the actual serving client. Controlled fetch/DOM facade, no network.
// Identity strings are not credentials. Format validation alone is stubbed so none are generated.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const sourcePath = process.argv[2] || path.join(__dirname, "../web/serving/client.js");
const source = fs.readFileSync(sourcePath, "utf8");
const results = [];
const flush = () => new Promise(setImmediate);

class Element {
  constructor() { this.textContent = ""; this.value = ""; this.children = []; this.listeners = {}; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = items; }
  reset() { this.value = ""; }
  dispatch(type) { return this.listeners[type]({ preventDefault() {}, target: this }); }
}

function context() {
  const nodes = new Map(), calls = [], timers = new Map();
  let serial = 0, timerId = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const sandbox = {
    document: { getElementById: node, createElement: () => new Element() },
    crypto: { randomUUID: () => `fixed-request-id-${++serial}` },
    FormData: class { get(name) { return name === "resource_id" ? "SYN-ORDER-1042" : `synthetic-${name}`; } },
    AbortController, TextDecoder,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject })),
  };
  vm.createContext(sandbox);
  const evaluate = text => vm.runInContext(text, sandbox);
  evaluate(source);
  evaluate("TOKEN_PATTERN.test = value => /^test-identity-[ab]$/.test(value)");
  return {
    node, calls, evaluate,
    identity(value = "test-identity-a") { node("token").value = value; node("token-form").dispatch("submit"); },
    seedRun() { evaluate("runId='run-one'"); },
    submit() { node("request-form").dispatch("submit"); },
    async respond(index, status, data) {
      calls[index].resolve({ status, json: async () => data }); await flush();
    },
    async fail(index) { calls[index].reject(new Error("controlled lost response")); await flush(); },
    logs() { return node("log").children.map(x => x.textContent).join("\n"); },
  };
}

function snapshot(state, prompt) {
  return { run_id: "run-one", state, proposal: { prompt, state: prompt ? "awaiting_consent" : "consumed",
    prompt_id: "prompt-one", proposal_version: 1, payload_hash: "synthetic-payload-hash" } };
}

async function test(name, fn) {
  try { await fn(); results.push({ name, status: "PASS" }); }
  catch (error) { results.push({ name, status: "FAIL", error: error.message }); }
}

(async () => {
  await test("old credential snapshot cannot render after identity replacement", async () => {
    const h = context(); h.identity(); h.seedRun();
    const pending = h.evaluate("refresh()");
    h.identity("test-identity-b");
    await h.respond(0, 200, snapshot("awaiting_consent", "OLD-IDENTITY-PRIVATE-PROMPT")); await pending;
    assert.ok(!h.node("prompt").textContent.includes("OLD-IDENTITY"));
    assert.ok(!h.node("snapshot").textContent.includes("OLD-IDENTITY"));
    assert.equal(h.evaluate("token"), "test-identity-b");
  });

  await test("forget invalidates in-flight snapshot and clears sensitive view", async () => {
    const h = context(); h.identity(); h.seedRun();
    h.evaluate(`snapshot=${JSON.stringify(snapshot("awaiting_consent", "PRIVATE-PROMPT"))}`);
    h.node("prompt").textContent = "PRIVATE-PROMPT"; h.node("snapshot").textContent = "PRIVATE-SNAPSHOT";
    h.node("answer").value = "PRIVATE-ANSWER"; h.node("events").append(new Element());
    h.node("log").append(new Element());
    const pending = h.evaluate("refresh()"); h.node("forget").dispatch("click");
    await h.respond(0, 200, snapshot("awaiting_consent", "LATE-PRIVATE-PROMPT")); await pending;
    assert.equal(h.evaluate("snapshot"), null); assert.equal(h.evaluate("runId"), null);
    assert.ok(!h.node("prompt").textContent.includes("PRIVATE")); assert.equal(h.node("snapshot").textContent, "");
    assert.equal(h.node("answer").value, ""); assert.equal(h.node("events").children.length, 0);
    assert.equal(h.node("log").children.length, 0);
  });

  await test("identity A to B to A cannot apply the first A request's 401", async () => {
    const h = context(); h.identity();
    const old = h.evaluate("request('GET','/synthetic-read')");
    h.identity("test-identity-b"); h.identity("test-identity-a");
    await h.respond(0, 401, { code: "credential_revoked" }); await old;
    assert.equal(h.evaluate("token"), "test-identity-a");
    assert.ok(!h.node("token-state").textContent.includes("rejected"));
  });

  await test("forget clears an unsubmitted credential draft", async () => {
    const h = context(); h.identity();
    h.node("token").value = "test-identity-b";
    h.node("forget").dispatch("click");
    assert.equal(h.node("token").value, "");
  });

  await test("credential replacement resumes selected run only after a current owned snapshot", async () => {
    const h = context(); h.identity(); h.seedRun(); h.identity("test-identity-b");
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, "/api/v1/runs/run-one");
    assert.equal(h.calls[0].options.headers.Authorization, "Bearer test-identity-b");
    await h.respond(0, 200, snapshot("succeeded", null));
    assert.equal(h.calls[1].url, "/api/v1/runs/run-one/events");
    assert.equal(h.calls[1].options.headers.Authorization, "Bearer test-identity-b");
  });

  await test("manual refresh superseding the ownership read still resumes the selected run", async () => {
    const h = context(); h.identity(); h.seedRun(); h.identity("test-identity-b");
    assert.equal(h.calls[0].url, "/api/v1/runs/run-one");
    const current = h.evaluate("refresh()");
    await h.respond(1, 200, snapshot("succeeded", null)); await current;
    assert.equal(h.calls.length, 3); assert.equal(h.calls[2].url, "/api/v1/runs/run-one/events");
    await h.respond(0, 200, snapshot("awaiting_consent", "OLD-ROTATION-PROMPT"));
    assert.equal(h.calls.length, 3, "late read cannot open another stream");
    assert.equal(h.evaluate("snapshot.state"), "succeeded");
  });

  await test("older same-run snapshot cannot replace a newer terminal snapshot", async () => {
    const h = context(); h.identity(); h.seedRun();
    const old = h.evaluate("refresh()"), current = h.evaluate("refresh()");
    await h.respond(1, 200, snapshot("succeeded", null)); await current;
    await h.respond(0, 200, snapshot("awaiting_consent", "OLD-PROMPT")); await old;
    assert.equal(h.evaluate("snapshot.state"), "succeeded");
    assert.ok(!h.node("prompt").textContent.includes("OLD-PROMPT"));
  });

  await test("older snapshot failure cannot overwrite current feedback", async () => {
    const h = context(); h.identity(); h.seedRun();
    const old = h.evaluate("refresh()"), current = h.evaluate("refresh()");
    await h.respond(1, 200, snapshot("succeeded", null)); await current;
    await h.respond(0, 503, { code: "OLD-FAILED-READ" }); await old;
    assert.equal(h.evaluate("snapshot.state"), "succeeded");
    assert.ok(!h.logs().includes("OLD-FAILED-READ"));
  });

  await test("latest failed read does not allow an older prompt to reappear", async () => {
    const h = context(); h.identity(); h.seedRun();
    const old = h.evaluate("refresh()"), current = h.evaluate("refresh()");
    await h.respond(1, 503, { code: "current_read_unavailable" }); await current;
    await h.respond(0, 200, snapshot("awaiting_consent", "OLD-PROMPT")); await old;
    assert.ok(!h.node("prompt").textContent.includes("OLD-PROMPT"));
    assert.ok(h.logs().includes("current_read_unavailable"));
  });

  await test("identity change stops the old session-to-run command chain", async () => {
    const h = context(); h.identity(); h.submit();
    h.identity("test-identity-b");
    await h.respond(0, 201, { session_id: "old-identity-session" });
    assert.equal(h.calls.length, 1, "no second mutation under the replacement identity");
    assert.equal(h.evaluate("pending"), null);
  });

  await test("old create-run answer cannot adopt a run under replacement identity", async () => {
    const h = context(); h.identity(); h.submit();
    await h.respond(0, 201, { session_id: "synthetic-session" });
    assert.equal(h.calls.length, 2);
    h.identity("test-identity-b");
    await h.respond(1, 202, { run_id: "old-identity-run" });
    assert.equal(h.evaluate("runId"), null);
    assert.ok(!h.logs().includes("old-identity-run"));
  });

  await test("old command completion cannot unlock a new identity's busy command", async () => {
    const h = context(); h.identity(); h.submit();
    h.identity("test-identity-b"); h.submit();
    assert.equal(h.calls.length, 2, "new identity can begin its own command");
    await h.respond(0, 503, { code: "old_command_failed" });
    assert.equal(h.evaluate("busy"), true);
    h.submit(); assert.equal(h.calls.length, 2, "new command is still protected against double submission");
    await h.respond(1, 503, { code: "controlled_new_failure" });
    assert.equal(h.evaluate("busy"), false);
  });

  await test("forget drops retry intent and ignores late command feedback", async () => {
    const h = context(); h.identity(); h.seedRun();
    h.node("cancel").dispatch("click"); h.node("forget").dispatch("click");
    await h.respond(0, 200, { state: "cancelled", message: "OLD-PRIVATE-FEEDBACK" });
    assert.equal(h.evaluate("pending"), null);
    assert.ok(!h.logs().includes("OLD-PRIVATE-FEEDBACK"));
    h.node("retry").dispatch("click"); assert.equal(h.calls.length, 1);
  });

  await test("current-identity command retry preserves exact body and idempotency key", async () => {
    const h = context(); h.identity(); h.seedRun();
    h.evaluate(`snapshot=${JSON.stringify(snapshot("awaiting_consent", "Synthetic exact prompt"))}`);
    h.node("answer").value = "yes"; h.node("consent-form").dispatch("submit");
    await h.fail(0); h.node("answer").value = "changed form must not change retry";
    h.node("retry").dispatch("click");
    assert.equal(h.calls.length, 2); assert.equal(h.calls[0].options.body, h.calls[1].options.body);
    assert.equal(h.calls[0].options.headers["Idempotency-Key"], h.calls[1].options.headers["Idempotency-Key"]);
    assert.equal(JSON.parse(h.calls[1].options.body).response, "yes");
    await h.respond(1, 202, { decision: "accepted" });
  });

  await test("lost create-run response retries both original session and run keys", async () => {
    const h = context(); h.identity(); h.submit();
    await h.respond(0, 201, { session_id: "synthetic-session" }); await h.fail(1);
    h.node("retry").dispatch("click");
    await h.respond(2, 201, { session_id: "synthetic-session" });
    assert.equal(h.calls.length, 4);
    for (const [first, repeated] of [[0, 2], [1, 3]]) {
      assert.equal(h.calls[first].url, h.calls[repeated].url);
      assert.equal(h.calls[first].options.body, h.calls[repeated].options.body);
      assert.equal(h.calls[first].options.headers["Idempotency-Key"], h.calls[repeated].options.headers["Idempotency-Key"]);
    }
    await h.respond(3, 503, { code: "controlled_end" });
  });

  await test("double submission sends only one command while current identity is busy", async () => {
    const h = context(); h.identity(); h.submit(); h.submit();
    assert.equal(h.calls.length, 1);
    await h.respond(0, 503, { code: "controlled_end" }); assert.equal(h.evaluate("busy"), false);
  });

  const failed = results.filter(r => r.status !== "PASS").length;
  console.log(JSON.stringify({ verification: "serving-client-ordering-v1", status: failed ? "FAIL" : "PASS",
    scope: "actual client.js with controlled fetch/DOM facade; exposed regressions, not browser or external-effect evidence",
    credentials_generated: false, network_requests: 0,
    source_sha256: crypto.createHash("sha256").update(source).digest("hex"),
    counts: { total: results.length, passed: results.length - failed, failed }, results }, null, 2));
  process.exitCode = failed ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });
