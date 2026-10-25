#!/usr/bin/env node
"use strict";
// Deterministic exposed regression checks on actual app.js functions. No browser/network dependency.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, "../observability/web/app.js"), "utf8");
const results = [];

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener() {}
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = values; }
  focus() {}
}

function context() {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  const sandbox = { Node: Element, URLSearchParams, console,
    location: { pathname: "/", hash: "" }, history: { replaceState() {} },
    document: { createElement: tag => new Element(tag), createTextNode: text => ({ textContent: text }),
      getElementById: node, querySelectorAll: () => [], body: { classList: { add() {}, remove() {} } } } };
  vm.createContext(sandbox);
  const marker = "\nif (liveServing) {\n  const token";
  assert.ok(source.includes(marker), "bootstrap marker retained; fail loudly if test seam changes");
  vm.runInContext(source.replace(marker, `\nboot = () => {};${marker}`), sandbox);
  const run = text => vm.runInContext(text, sandbox);
  run(`const actualApi = api; const pending = []; api = path => new Promise((resolve, reject) => pending.push({ path, resolve, reject }));
       renderDetail = request => ({ renderedKey: request.key });
       listRow = item => ({ renderedKey: item.key });
       aggregateCard = (key, value) => ({ renderedKey: key });`);
  return { node, run };
}

const flush = () => new Promise(setImmediate);
const page = key => ({ items: [{ key }], page: 1, pages: 1, total: 1 });

async function detailRace(rejectOlder = false) {
  const { node, run } = context();
  run("openRequest('older'); openRequest('newer'); pending[1].resolve({key:'newer'});");
  await flush();
  run(rejectOlder ? "pending[0].reject(new Error('old failure'));" : "pending[0].resolve({key:'older'});");
  await flush();
  assert.equal(run("state.selected"), "newer");
  assert.equal(node("detail").children[0].renderedKey, "newer");
}

async function listRace(rejectOlder = false) {
  const { node, run } = context();
  run("state.filters={status:'failed'}; loadList(); state.filters={status:'completed'}; loadList();");
  assert.ok(run("pending[0].path").includes("failed"));
  assert.ok(run("pending[1].path").includes("completed"));
  run(`pending[1].resolve(${JSON.stringify(page("newer"))});`);
  await flush();
  run(rejectOlder ? "pending[0].reject(new Error('old failure'));" : `pending[0].resolve(${JSON.stringify(page("older"))});`);
  await flush();
  assert.equal(node("request-list").children[0].renderedKey, "newer");
  assert.equal(run("state.items[0].key"), "newer");
  assert.equal(run("pending.length"), 3, "superseded list cannot issue aggregate fetch");
}

async function staleCompletionCannotClearLoading() {
  const { node, run } = context();
  run("loadList(); loadList();");
  run(`pending[0].resolve(${JSON.stringify(page("older"))});`);
  await flush();
  assert.equal(node("request-list").attributes["aria-busy"], "true");
  assert.equal(node("list-status").textContent, "Loading requests…");
  run(`pending[1].resolve(${JSON.stringify(page("newer"))});`);
  await flush();
  assert.equal(node("request-list").attributes["aria-busy"], undefined);
}

async function aggregateRace(rejectOlder = false) {
  const { node, run } = context();
  run("state.listRevision=1; loadAggregates(); state.listRevision=2; loadAggregates(); pending[1].resolve({newer:{}});");
  await flush();
  run(rejectOlder ? "pending[0].reject(new Error('old failure'));" : "pending[0].resolve({older:{}});");
  await flush();
  assert.equal(node("aggregates").children[0].renderedKey, "newer");
}

async function credentialChangeInvalidatesPendingRendering() {
  const { node, run } = context();
  run("openRequest('before-clear'); loadList(); state.authRevision++;");
  const beforeDetail = node("detail").children;
  const beforeList = node("request-list").children;
  run(`pending[0].resolve({key:'before-clear'}); pending[1].resolve(${JSON.stringify(page("before-clear"))});`);
  await flush();
  assert.equal(node("detail").children, beforeDetail);
  assert.equal(node("request-list").children, beforeList);
  assert.equal(run("pending.length"), 2);
}

async function credentialChangeInvalidatesApiResponse() {
  const { run } = context();
  run(`const fetches = []; fetch = () => new Promise(resolve => fetches.push(resolve));
       let settled; actualApi('/api/meta').then(value => settled=value, error => settled=error.message);
       state.authRevision++; fetches[0]({ok:true,json:async()=>({private:'old viewer'})});`);
  await flush();
  assert.equal(run("settled"), "credentials_changed");
}

async function closeDetailInvalidatesPendingRendering() {
  const { node, run } = context();
  run("openRequest('closed'); closeDetail();");
  const before = node('detail').children;
  run("pending[0].resolve({key:'closed'});");
  await flush();
  assert.equal(node('detail').children, before, 'a closed pane must not render or refocus from a pending response');
}

async function credentialChangeSuppressesExportError() {
  const { run } = context();
  run(`const alerts = []; alert = message => alerts.push(message);
       exportRequest('old'); state.authRevision++; pending[0].reject(new Error('old credentials'));`);
  await flush();
  assert.equal(run("alerts.length"), 0);
}

(async () => {
  for (const [name, test] of [
    ["detail reversed success", () => detailRace()], ["detail stale error", () => detailRace(true)],
    ["list reversed success", () => listRace()], ["list stale error", () => listRace(true)],
    ["loading belongs to latest list", staleCompletionCannotClearLoading],
    ["aggregate reversed success", () => aggregateRace()], ["aggregate stale error", () => aggregateRace(true)],
    ["credential change invalidates rendering", credentialChangeInvalidatesPendingRendering],
    ["credential change invalidates API response", credentialChangeInvalidatesApiResponse],
    ["credential change suppresses export error", credentialChangeSuppressesExportError],
    ["closing detail invalidates pending rendering", closeDetailInvalidatesPendingRendering],
  ]) {
    try { await test(); results.push({ name, status: "PASS" }); }
    catch (error) { results.push({ name, status: "FAIL", error: error.message }); }
  }
  const status = results.every(r => r.status === "PASS") ? "PASS" : "FAIL";
  console.log(JSON.stringify({ status, scope: "actual app.js asynchronous functions, controlled completion and DOM facade; not full browser evidence", results }, null, 2));
  process.exitCode = status === "PASS" ? 0 : 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
