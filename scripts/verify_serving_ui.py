#!/usr/bin/env python3
"""Bounded real-browser serving/restore check against an existing local synthetic Stack.

Requires an existing serving Python environment, Node, Playwright and Chromium. No installs or
external requests. Credentials stay in process memory/stdin; receipts contain only redacted
synthetic evidence. Use a new --receipt path for every run so first failures remain preserved.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


NODE = r'''
const assert = require('node:assert/strict');
const readline = require('node:readline');
const crypto = require('node:crypto');
const digest = text => crypto.createHash('sha256').update(text).digest('hex');
let config, browser, context, page, phase, retainedCursor, selectedRun;
const checks = [], externalRequests = [], browserErrors = [], commands = [], sse = [];
const check = (name, condition) => { assert.ok(condition, name); checks.push({name, result:'PASS'}); };
const redact = text => config?.token ? String(text).split(config.token).join('[REDACTED]') : String(text);
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
const readSnapshot = () => page.locator('#snapshot').evaluate(el => JSON.parse(el.textContent));
async function stateIs(state) {
  await page.waitForFunction(wanted => {
    try { return JSON.parse(document.querySelector('#snapshot').textContent).state === wanted; }
    catch { return false; }
  }, state);
}
function observe(target, dashboardMethods) {
  target.on('pageerror', error => browserErrors.push(redact(error.message)));
  target.on('request', request => {
    const url = new URL(request.url());
    if (request.url().includes(config.token)) browserErrors.push('credential appeared in request URL');
    if (dashboardMethods) dashboardMethods.push(request.method());
    if (target !== page) return;
    if (url.pathname.endsWith('/events')) {
      sse.push({phase, cursor:request.headers()['last-event-id'] || null});
    }
    if (request.method() === 'POST' && url.pathname.startsWith('/api/v1/')) {
      commands.push({path:url.pathname, body_sha256:digest(request.postData() || ''),
        key_sha256:digest(request.headers()['idempotency-key'] || ''), phase});
    }
  });
}
async function perform(command) {
  phase = command.phase;
  if (phase === 'initialize') {
    config = command;
    const {chromium} = require(config.playwright);
    browser = await chromium.launch({executablePath:config.browser, headless:true});
    context = await browser.newContext({viewport:{width:1280,height:900},serviceWorkers:'block'});
    const origin = new URL(config.base).origin;
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === origin) return route.continue();
      externalRequests.push(url.origin); return route.abort('blockedbyclient');
    });
    page = await context.newPage(); page.setDefaultTimeout(12000); observe(page);
    await page.goto(config.base + '/serving/', {waitUntil:'domcontentloaded'});
    check('initial serving page requires an in-memory credential',
      (await page.locator('#token-state').textContent()).includes('No token'));
    await page.locator('#token').fill(config.token);
    await page.locator('#token-form button[type=submit]').click();
    check('credential input clears after submit', await page.locator('#token').inputValue() === '');
    await page.locator('#request-form button[type=submit]').click();
    await stateIs('awaiting_consent');
    await page.waitForFunction(() => document.querySelectorAll('#events li').length >= 2);
    const snapshot = await readSnapshot(); selectedRun = snapshot.run_id;
    const prompt = await page.locator('#prompt').textContent();
    check('UI displays the entire current prompt and its exact binding',
      prompt.startsWith(snapshot.proposal.prompt) && prompt.includes(snapshot.proposal.prompt_id) &&
      prompt.includes(snapshot.proposal.payload_hash) && prompt.includes('version ' + snapshot.proposal.proposal_version));
    check('initial prompt identifies the synthetic resource and complete address',
      prompt.includes('SYN-ORDER-1042') && Object.values(config.address).every(value => prompt.includes(value)));
    return {run_id:selectedRun, prompt_sha256:digest(snapshot.proposal.prompt),
      payload_hash:snapshot.proposal.payload_hash, operation_id:snapshot.proposal.operation_id,
      initial_cursor:snapshot.last_event_id, browser_version:browser.version()};
  }
  if (phase === 'ambiguous') {
    await page.locator('#answer').fill('maybe');
    const response = page.waitForResponse(r => r.url().endsWith('/consents') && r.request().method() === 'POST');
    await page.locator('#consent').click(); const reply = await response, data = await reply.json();
    check('ambiguous consent is controlled and does not authorize', reply.status() === 200 && data.decision === 'ambiguous');
    await stateIs('awaiting_consent');
    check('same exact prompt remains visible after ambiguity', (await readSnapshot()).proposal.operation_id === command.operation_id);
    return {state:(await readSnapshot()).state, decision:data.decision};
  }
  if (phase === 'approve_resume_retry') {
    await page.locator('#drop').click();
    const droppedCursor = await page.evaluate(() => cursor);
    check('manual drop retains the last delivered cursor', !!droppedCursor && (await page.locator('#stream-state').textContent()).includes('Dropped'));
    await page.locator('#answer').fill('yes');
    const response = page.waitForResponse(r => r.url().endsWith('/consents') && r.request().method() === 'POST');
    await page.locator('#consent').click(); const reply = await response;
    check('fresh affirmative consent accepted', reply.status() === 202);
    const accepted = commands.filter(c => c.path.endsWith('/consents')).at(-1);
    await page.locator('#resume').click();
    await stateIs('succeeded');
    await page.waitForFunction(() => document.querySelector('#stream-state').textContent.includes('Stream complete'));
    check('resumption requests only events after the retained cursor', sse.some(r => r.phase === phase && r.cursor === droppedCursor));
    const ids = await page.locator('#events code').allTextContents();
    check('interrupted then resumed stream has no duplicate displayed event IDs', ids.length > 2 && new Set(ids).size === ids.length);
    for (let attempt=0; attempt<2; attempt++) {
      const repeated = page.waitForResponse(r => r.url().endsWith('/consents') && r.request().method() === 'POST');
      await page.locator('#retry').click(); const answer = await repeated;
      check('retry ' + (attempt+1) + ' replays accepted response', answer.status() === 202);
      const sent = commands.filter(c => c.path.endsWith('/consents')).at(-1);
      check('retry ' + (attempt+1) + ' preserves exact body and Idempotency-Key',
        sent.body_sha256 === accepted.body_sha256 && sent.key_sha256 === accepted.key_sha256);
      await page.waitForFunction(() => !busy);
    }
    const refreshed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/runs/' + selectedRun);
    await page.locator('#refresh').click(); await refreshed; await stateIs('succeeded');
    await page.locator('#drop').click(); retainedCursor = await page.evaluate(() => cursor);
    check('browser keeps completed pre-restore cursor for the restore test', retainedCursor === (await readSnapshot()).last_event_id);
    return {run_id:selectedRun, retained_cursor:retainedCursor, displayed_event_ids:ids,
      consent_commands:commands.filter(c => c.path.endsWith('/consents'))};
  }
  if (phase === 'restore_resume') {
    const refused = page.waitForResponse(r => r.url().endsWith('/events') && r.status() === 410);
    await page.locator('#resume').click(); const reply = await refused;
    check('pre-restore cursor receives an explicit continuity reset', reply.request().headers()['last-event-id'] === retainedCursor);
    await page.waitForFunction(() => document.querySelector('#prompt').textContent.includes('Local dispatch history is missing'));
    const snapshot = await readSnapshot(), currentCursor = await page.evaluate(() => cursor);
    check('restored snapshot keeps missing history explicit without invented dispatch',
      snapshot.state === 'succeeded' && snapshot.operation.dispatch_history === 'missing_after_restore' && !snapshot.operation.dispatch_started);
    check('new transport cursor binds the restore generation', currentCursor === snapshot.last_event_id &&
      currentCursor !== retainedCursor && currentCursor.endsWith('_r' + snapshot.operation.recovery_observation.restore_id));
    check('prior-generation event rows are cleared rather than mixed with restored history', await page.locator('#events li').count() === 0);
    check('UI warns that recovered outcome does not establish original consent',
      (await page.locator('#prompt').textContent()).includes('does not establish original consent'));
    check('history discontinuity is visible to the user', (await page.locator('#log').textContent()).includes('History changed after restore'));
    const resumed = page.waitForResponse(r => r.url().endsWith('/events') && r.status() === 200);
    await page.locator('#resume').click(); await resumed;
    await page.waitForFunction(() => document.querySelector('#stream-state').textContent.includes('Stream complete'));
    check('repeat resume uses the new generation and remains bounded', sse.at(-1).cursor === currentCursor);
    return {run_id:selectedRun, old_cursor:retainedCursor, new_cursor:currentCursor,
      restore_id:snapshot.operation.recovery_observation.restore_id, history:snapshot.operation.dispatch_history};
  }
  if (phase === 'dashboard') {
    const dashboard = await context.newPage(), methods = []; dashboard.setDefaultTimeout(12000); observe(dashboard, methods);
    try {
      await dashboard.goto(config.base + '/observability/', {waitUntil:'domcontentloaded'});
      await dashboard.getByLabel('Local serving bearer token').fill(config.token);
      await dashboard.getByRole('button',{name:'Open observations',exact:true}).click();
      await dashboard.locator('#request-list .row').first().waitFor();
      await dashboard.locator('#request-list .row').first().click();
      await dashboard.locator('#detail article').waitFor();
      check('dashboard visibly marks missing restored history',
        (await dashboard.locator('[data-testid=completeness]').textContent()).includes('missing_after_restore'));
      check('dashboard reports observed effect separately from missing authorization',
        (await dashboard.locator('.op-outcome').textContent()).includes('Effect observed after restore') &&
        (await dashboard.locator('.ops').textContent()).includes('not recorded in restored history'));
      check('dashboard zero count means recorded attempts, not proof of no attempt',
        (await dashboard.locator('.op-attempts').textContent()).includes('0 recorded (history incomplete)'));
      check('dashboard retains no-model and unknown-charge semantics',
        (await dashboard.locator('[data-series=measured]').textContent()).includes('not applicable — no model calls') &&
        (await dashboard.locator('[data-series=confirmed]').textContent()).includes('total unknown'));
      const download = dashboard.waitForEvent('download');
      await dashboard.getByRole('button',{name:'Download redacted evidence (JSON)',exact:true}).click();
      const stream = await (await download).createReadStream(); let content = '';
      for await (const chunk of stream) content += chunk.toString();
      const exported = JSON.parse(content);
      check('download preserves explicit recovery incompleteness and synthetic provenance',
        exported.data_origin === 'live_synthetic_provider' && exported.request.evidence.completeness.includes('missing_after_restore'));
      check('download omits credential and raw synthetic address', !content.includes(config.token) && !content.includes(config.address.street));
      check('dashboard browser path issues GET requests only', methods.length > 0 && methods.every(m => m === 'GET'));
      return {methods:[...new Set(methods)], request_count:methods.length, export_sha256:digest(content),
        export_bytes:Buffer.byteLength(content), status:exported.request.status,
        completeness:exported.request.evidence.completeness};
    } finally { await dashboard.close(); }
  }
  if (phase === 'forget_reload') {
    await page.locator('#forget').click();
    check('forget clears serving snapshot, prompt, events and credential', await page.evaluate(() =>
      token === null && snapshot === null && runId === null && document.querySelector('#token').value === '' &&
      document.querySelector('#snapshot').textContent === '' && document.querySelectorAll('#events li').length === 0));
    await page.reload({waitUntil:'networkidle'});
    check('reload requires a new explicit in-memory credential', (await page.locator('#token-state').textContent()).includes('No token') &&
      await page.evaluate(() => token === null && runId === null));
    const stores = await page.evaluate(async sensitive => ({local:localStorage.length,session:sessionStorage.length,
      cookies:document.cookie.length,indexed:(await indexedDB.databases()).length,caches:(await caches.keys()).length,
      dom_contains_credential:document.documentElement.outerHTML.includes(sensitive)}),config.token);
    check('credential never persists in browser stores or DOM', Object.values(stores).every(value => !value));
    check('browser context stores no cookies or persistent origins', (await context.cookies()).length === 0 &&
      (await context.storageState()).origins.length === 0);
    check('no external page requests occurred', externalRequests.length === 0);
    check('no JavaScript page errors occurred', browserErrors.length === 0);
    return {storage:stores, external_page_requests:externalRequests, browser_errors:browserErrors};
  }
  if (phase === 'close') { if (context) await context.close(); if (browser) await browser.close(); return {}; }
  throw new Error('unknown verification phase');
}
(async () => {
  for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
    let command;
    try {
      command=JSON.parse(line); const start=checks.length, data=await perform(command);
      emit({phase:command.phase,status:'PASS',checks:checks.slice(start),data});
    } catch(error) { emit({phase:command?.phase || 'unknown',status:'FAIL',error:redact(error.stack || error),checks,
      external_page_requests:externalRequests,browser_errors:browserErrors}); }
    if (command?.phase === 'close') break;
  }
})().catch(error => { emit({status:'ERROR',error:redact(error.stack || error)}); process.exitCode=1; });
'''


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class BrowserDriver:
    def __init__(self, source: Path):
        self.errors = tempfile.TemporaryFile(mode="w+t")
        self.process = subprocess.Popen([shutil.which("node"), "-e", NODE], cwd=source,
                                        env={"PATH": os.defpath}, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.errors, text=True, bufsize=1)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.results = []

    def phase(self, name: str, **values):
        self.process.stdin.write(json.dumps({"phase": name, **values}) + "\n")
        self.process.stdin.flush()
        if not self.selector.select(timeout=45):
            raise TimeoutError(f"browser phase {name} exceeded 45 seconds")
        line = self.process.stdout.readline()
        if not line:
            self.errors.seek(0)
            raise RuntimeError(f"browser exited in {name}: {self.errors.read()[-2000:]}")
        result = json.loads(line)
        self.results.append(result)
        print(f"Browser phase {name}: {result['status']} ({len(result.get('checks', []))} checks)", flush=True)
        if result.get("phase") != name or result.get("status") != "PASS":
            raise AssertionError(f"browser phase {name}: {result.get('error', result.get('status'))}")
        return result["data"]

    def close(self):
        try:
            if self.process.poll() is None:
                self.phase("close")
                self.process.stdin.close()
                self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)
            self.selector.close()
            self.process.stdout.close()
            self.errors.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--playwright", required=True)
    parser.add_argument("--browser", required=True)
    args = parser.parse_args()
    source, receipt_path = args.source.resolve(), args.receipt.resolve()
    if receipt_path.exists():
        parser.error("receipt already exists; choose a new path to preserve the previous run")
    artifact_dir = receipt_path.parent / (receipt_path.stem + "-artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(source))
    from tests_serving.harness import ADDRESS, Stack
    from evalgate.recovery import check_recovery

    paths = ("web/serving/client.js", "serving/service.py", "serving/streaming.py", "scripts/serving.py",
             "serving/trace_export.py", "evalgate/trace.py", "evalgate/recovery.py", "contracts/trace-v3.schema.json",
             "serving/observability_projection.py", "observability/project.py", "observability/web/app.js")
    hashes = lambda: {name: sha256((source / name).read_bytes()).hexdigest() for name in paths}
    receipt = {"verification": "serving-browser-restore-v1", "status": "NOT_RUN", "checks": [],
               "scope": "actual browser -> local API -> synthetic provider independent SQLite ledger -> restore -> dashboard",
               "provenance": "exposed deterministic regression; no real-model or customer evidence",
               "verifier_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "source_hashes_before": hashes()}
    stack = driver = None
    secrets = []

    def check(name, condition):
        receipt["checks"].append({"name": name, "result": "PASS" if condition else "FAIL"})
        if not condition:
            raise AssertionError(name)

    def business_state():
        with closing(stack.app_db()) as conn:
            tables = {table: sorted([tuple(row) for row in conn.execute(f"SELECT * FROM {table}")],
                                    key=lambda row: json.dumps(row, separators=(",", ":")))
                      for table in ("runs", "proposals", "operations", "events", "resource_holds")}
        return {"sha256": digest(tables), "row_counts": {table: len(rows) for table, rows in tables.items()},
                "provider_effects_sha256": digest(stack.provider_effects())}

    try:
        stack = Stack()
        secrets = list(stack.tokens.values()) + [(stack.root / "app" / "provider.key").read_text().strip()]
        stack.start_provider(); stack.start_app()
        driver = BrowserDriver(source)
        initial = driver.phase("initialize", base=stack.url(""), token=stack.tokens["alice"], address=ADDRESS,
                               playwright=args.playwright, browser=args.browser)
        receipt["initial"] = initial
        run_id, operation_id = initial["run_id"], initial["operation_id"]
        before = stack.snapshot(run_id)
        check("browser displayed the server's full exact prompt", initial["prompt_sha256"] == sha256(before["proposal"]["prompt"].encode()).hexdigest())
        check("initial run produced no provider effect", not stack.provider_effects())
        backup = stack.root / "before-consent.db"
        stack.run_cli("backup", str(backup))
        with closing(stack.app_db()) as conn:
            identities = dict(conn.execute("SELECT run_id,session_id,trace_id FROM runs WHERE run_id=?", (run_id,)).fetchone())
        expected = {**identities, "operation_id": operation_id, "tenant": "acme", "tool_name": "change_order_address",
                    "arguments": {"order_id": "SYN-ORDER-1042", "address": ADDRESS}, "expected_revision": 1,
                    "retained_authorizations": 0, "retained_dispatches": 0}
        driver.phase("ambiguous", operation_id=operation_id)
        check("independent provider ledger remains empty after ambiguous consent", not stack.provider_effects())
        receipt["approved_browser"] = driver.phase("approve_resume_retry")
        effects = stack.provider_effects()
        check("affirmative plus repeated UI commands has exactly one independent effect",
              len(effects) == 1 and effects[0]["idempotency_key"] == "rra-" + operation_id and
              json.loads(effects[0]["address"]) == ADDRESS and effects[0]["revision_after"] == 2)
        before_restore_effects = digest(effects)
        stack.stop_app()
        stack.run_cli("restore", str(backup))
        with closing(stack.app_db()) as conn:
            metadata = dict(conn.execute("SELECT key,value FROM meta WHERE key IN ('restore_generation','restore_snapshot_sha256','restore_state')"))
        check("restore remains unverified before provider verification", metadata["restore_state"] == "unverified")
        expected.update(restore_id=metadata["restore_generation"], snapshot_sha256=metadata["restore_snapshot_sha256"])
        stack.run_cli("verify-restore"); stack.start_app()
        receipt["restored_browser"] = driver.phase("restore_resume")
        check("restore and repeated stream resume do not add any provider effect", digest(stack.provider_effects()) == before_restore_effects)
        trace = stack.get(f"/api/v1/runs/{run_id}/trace?contract=trace-v3", "alice")
        check("trace-v3 export is available after restore", trace.status_code == 200)
        check("trace export omits credentials and the raw synthetic address",
              not any(secret in trace.text for secret in secrets) and ADDRESS["street"] not in trace.text)
        events = [json.loads(line) for line in trace.text.splitlines()]
        (artifact_dir / "trace-v3.jsonl").write_text(trace.text)
        recovery = check_recovery(events, stack.provider_effects(), expected)
        receipt["recovery_comparison"] = recovery
        check("restore observation matches independently declared fixture and provider ledger", recovery["verdict"] == "PASS")
        check("recovery evidence does not attest missing original consent", recovery["local_authorization_attested"] is False)
        ledger = [{"effect_id": row["effect_id"], "provider_key_sha256": sha256(row["idempotency_key"].encode()).hexdigest(),
                   "request_hash": row["request_hash"], "tenant": row["tenant"], "order_id": row["order_id"],
                   "address_sha256": digest(json.loads(row["address"])), "revision_after": row["revision_after"]}
                  for row in stack.provider_effects()]
        (artifact_dir / "provider-ledger-redacted.json").write_text(json.dumps(ledger, indent=2) + "\n")
        before_dashboard = business_state()
        receipt["dashboard_browser"] = driver.phase("dashboard")
        after_dashboard = business_state()
        receipt["dashboard_business_before"], receipt["dashboard_business_after"] = before_dashboard, after_dashboard
        check("dashboard including evidence download leaves business state and effect ledger unchanged", before_dashboard == after_dashboard)
        driver.phase("forget_reload")
        check("entire browser flow leaves exactly one synthetic provider effect", len(stack.provider_effects()) == 1)
        receipt["status"] = "PASS"
    except Exception as error:
        receipt.update(status="BLOCKED" if isinstance(error, PermissionError) else "FAIL", error=f"{type(error).__name__}: {error}")
    finally:
        if driver:
            try:
                driver.close()
            except Exception as error:
                receipt.update(status="FAIL", cleanup_error=f"{type(error).__name__}: {error}")
            receipt["browser_phases"] = driver.results
        if stack:
            stack.close()
        receipt["source_hashes_after"] = hashes()
        if receipt["source_hashes_before"] != receipt["source_hashes_after"]:
            receipt.update(status="INVALID", source_changed_during_verification=True)
        receipt["artifact_sha256"] = {p.name: sha256(p.read_bytes()).hexdigest() for p in artifact_dir.iterdir() if p.is_file()}
        serialized = json.dumps(receipt, indent=2) + "\n"
        for secret in secrets:
            if secret and secret in serialized:
                serialized = serialized.replace(secret, "[REDACTED]")
        serialized = re.sub(r"rra_[A-Za-z0-9_-]{43}", "[REDACTED]", serialized)
        receipt_path.write_text(serialized)
        print(f"{receipt['status']}: {receipt_path}", flush=True)
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
