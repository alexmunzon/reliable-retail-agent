#!/usr/bin/env python3
"""Headless local-only dashboard check. Requires existing serving venv, Node, Playwright and Chromium.

No installation or remote requests. Creates only a temporary synthetic Stack and the requested receipt.
Tokens travel to Node over stdin; receipts never contain tokens, headers, or browser storage contents.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time


NODE = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
const { chromium } = require(config.playwright);
const result = { checks: [], rejected_external_requests: [], browser_errors: [], observed_http_methods: {} };
function check(name, condition, detail) {
  assert.ok(condition, name);
  result.checks.push({ name, result: 'PASS', ...(detail ? { detail } : {}) });
}
(async () => {
  const browser = await chromium.launch({ executablePath: config.browser, headless: true });
  result.browser_version = browser.version();
  let context;
  try {
    context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' });
    const origins = new Set([new URL(config.fixture).origin, new URL(config.live).origin]);
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (origins.has(url.origin)) return route.continue();
      result.rejected_external_requests.push(url.origin);
      return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => result.browser_errors.push(error.message));
    page.on('request', request => {
      result.observed_http_methods[request.method()] = (result.observed_http_methods[request.method()] || 0) + 1;
      assert.ok(!request.url().includes(config.token), 'credential never placed in URL');
    });
    async function settledList() {
      await page.waitForFunction(() => !document.getElementById('request-list').hasAttribute('aria-busy') &&
        /request\(s\) match|No requests match/.test(document.getElementById('list-status').textContent));
    }
    async function applyFilter(status) {
      const response = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/requests') && r.status() === 200);
      await page.locator('#f-status').selectOption(status);
      await page.locator('#filters button[type=submit]').click();
      await response; await settledList();
    }
    async function openFixture(key) {
      await applyFilter('');
      for (let i = 0; i < 10; i++) {
        const row = page.locator(`#request-list .row[data-key="${key}"]`);
        if (await row.count()) {
          await row.click();
          await page.locator(`#detail article[data-key="${key}"]`).waitFor();
          return;
        }
        assert.equal(await page.locator('#next').isDisabled(), false, 'fixture request is reachable by pagination');
        const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/requests' && r.status() === 200);
        await page.locator('#next').click(); await response; await settledList();
      }
      throw new Error('fixture pagination bound exceeded');
    }
    await page.goto(config.fixture, { waitUntil: 'networkidle' });
    await settledList();
    check('fixture badge distinguishes fixture data', (await page.locator('#origin-badge').textContent()).includes('SYNTHETIC FIXTURES'));
    await openFixture('healthy/run-healthy');
    check('fixture healthy row opens detail', (await page.locator('.t-request').textContent()) === `${config.oracle.healthy.duration_ms} ms`);
    check('fixture estimate agrees with frozen oracle', (await page.locator('[data-series=estimated]').textContent()).includes(`USD ${config.oracle.healthy.estimated_charge}`));
    check('fixture receipt agrees with frozen oracle', (await page.locator('[data-series=confirmed]').textContent()).includes(`USD ${config.oracle.healthy.confirmed_charge}`));
    await page.keyboard.press('Escape');
    await applyFilter('unknown_outcome');
    const unknownKeys = await page.locator('#request-list .row').evaluateAll(rows => rows.map(r => r.dataset.key).sort());
    check('fixture status filter agrees with frozen oracle', JSON.stringify(unknownKeys) === JSON.stringify(config.oracle.unknown_keys));
    await page.locator('#request-list .row').first().focus();
    await page.keyboard.press('Enter');
    await page.locator('#detail article').waitFor();
    check('keyboard row navigation opens uncertain detail', (await page.locator('#detail .op-outcome').allTextContents()).includes('Unknown'));
    await page.keyboard.press('Escape');
    await openFixture('missing-tokens/run-missing-tokens');
    check('missing fixture tokens show unknown estimate', (await page.locator('[data-series=estimated]').textContent()).startsWith('unknown'));
    await page.setViewportSize({ width: 375, height: 812 });
    check('narrow detail has a usable back control', await page.locator('#detail .back').isVisible());
    await page.locator('#detail .back').click();
    check('narrow back restores request list', await page.locator('#request-list').isVisible());
    await page.setViewportSize({ width: 1280, height: 900 });

    let observationRequests = 0, badAuthorization = 0;
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/observability/')) {
        observationRequests++;
        if (request.headers().authorization !== `Bearer ${config.token}`) badAuthorization++;
      }
    });
    await page.goto(config.live, { waitUntil: 'networkidle' });
    check('live initial state requires authentication', await page.getByLabel('Local serving bearer token').isVisible());
    check('live page makes no observation call before token entry', observationRequests === 0);
    await page.getByLabel('Local serving bearer token').fill(config.token);
    await page.getByRole('button', { name: 'Open observations', exact: true }).click();
    await settledList();
    await page.waitForFunction(() => document.querySelectorAll('#aggregates article.agg').length > 0);
    check('live aggregates wrapper renders groups', await page.locator('#aggregates article.agg').count() > 0);
    const sampleBanner = await page.locator('#set-banner').textContent();
    check('live bounded sample banner is visible', await page.locator('#set-banner').isVisible());
    check('live banner explains limit, exclusions, and truncation',
      sampleBanner.includes('newest 100 owned runs') && sampleBanner.includes('Older history is excluded') && sampleBanner.includes('More owned runs exist.'), sampleBanner);
    check('live newest-100 sample count is shown', (await page.locator('#list-status').textContent()) === '100 request(s) match');
    await page.locator('#request-list .row').first().click();
    await page.locator('#detail article').waitFor();
    check('live distinct run and trace row opens detail', /serving\/run_[a-f0-9]{32}\/trc_[a-f0-9]{32}/.test(await page.locator('#detail article').getAttribute('data-key')));
    check('live usage explains no model calls', (await page.locator('[data-series=measured]').textContent()).includes('not applicable — no model calls'));
    check('live unknown cost is not zero', (await page.locator('[data-series=estimated]').textContent()).startsWith('unknown') && !(await page.locator('[data-series=estimated]').textContent()).includes('USD 0'));
    check('live billing total remains unknown without receipts', (await page.locator('[data-series=confirmed]').textContent()).includes('total unknown'));
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download redacted evidence (JSON)' }).click();
    const exported = await download;
    const stream = await exported.createReadStream();
    let content = ''; for await (const chunk of stream) content += chunk.toString();
    const decoded = JSON.parse(content);
    check('live redacted export uses actual observation origin', decoded.data_origin === 'live_synthetic_provider');
    check('export excludes test credential and address', !content.includes(config.token) && !content.includes('18 Example Lane'));
    const storage = await page.evaluate(async token => ({
      local: localStorage.length, session: sessionStorage.length, cookie: document.cookie.length,
      indexed: (await indexedDB.databases()).length,
      domContainsToken: document.documentElement.outerHTML.includes(token),
      passwordInputs: document.querySelectorAll('input[type=password]').length,
      cacheNames: (await caches.keys()).length,
    }), config.token);
    check('token absent from persistent browser stores and DOM after submit',
      storage.local === 0 && storage.session === 0 && storage.cookie === 0 && storage.indexed === 0 &&
      !storage.domContainsToken && storage.passwordInputs === 0 && storage.cacheNames === 0);
    check('context has no stored credentials or cookies', (await context.cookies()).length === 0 &&
      (await context.storageState()).origins.length === 0);
    check('authenticated observations use bearer header', observationRequests > 0 && badAuthorization === 0);
    await page.getByRole('button', { name: 'Clear token', exact: true }).click();
    await page.getByLabel('Local serving bearer token').waitFor();
    check('clear token returns to authentication without retained token', await page.getByLabel('Local serving bearer token').inputValue() === '');
    check('browser actions were GET-only', Object.keys(result.observed_http_methods).every(m => m === 'GET'));
    check('no external browser requests occurred', result.rejected_external_requests.length === 0);
    check('no JavaScript page errors occurred', result.browser_errors.length === 0);
    result.status = 'PASS';
  } catch (error) {
    result.status = 'FAIL';
    result.error = String(error.stack || error).split(config.token).join('[REDACTED]');
  } finally {
    if (context) await context.close();
    await browser.close();
  }
  console.log(JSON.stringify(result));
  process.exitCode = result.status === 'PASS' ? 0 : 1;
})().catch(error => { console.error(String(error).split(config.token).join('[REDACTED]')); process.exitCode = 1; });
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--playwright", required=True, help="Path to an existing Playwright Node package")
    parser.add_argument("--browser", required=True, help="Path to an existing headless Chromium executable")
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    from observability.report import Dataset
    from observability.server import DashboardServer
    from tests_serving.harness import ADDRESS, Stack

    files = ("observability/web/app.js", "observability/web/index.html", "serving/observability_api.py",
             "serving/observability_projection.py", "observability/report.py")
    hashes = lambda: {p: sha256((source / p).read_bytes()).hexdigest() for p in files}
    receipt = {"verification": "local-headless-observability-v1", "status": "NOT_RUN",
               "source_hashes_before": hashes(), "synthetic_runs_created": 0,
               "scope": "fixture UI and authenticated local synthetic provider; no real customer or provider data"}
    fixture = stack = None
    try:
        fixture = DashboardServer(Dataset(), "tenant-a", port=0)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        stack = Stack()
        stack.start_provider(); stack.start_app()
        session = stack.post("/api/v1/sessions", "alice", "ui-session-bound", {"resource_id": "SYN-ORDER-1042"})
        assert session.status_code == 201
        session_id = session.json()["session_id"]
        for i in range(101):
            response = stack.post(f"/api/v1/sessions/{session_id}/runs", "alice", f"ui-create-run-{i:04}",
                                  {"request": {"type": "change_order_address", "address": ADDRESS}})
            assert response.status_code == 202, f"synthetic run {i}: HTTP {response.status_code}"
            receipt["synthetic_runs_created"] += 1
            if i < 100:
                cancelled = stack.post(f"/api/v1/runs/{response.json()['run_id']}/cancel", "alice", f"ui-cancel-run-{i:04}")
                assert cancelled.status_code == 200

        def business_snapshot():
            with stack.app_db() as conn:
                data = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                        for table in ("runs", "proposals", "operations", "events", "resource_holds")}
                prompts = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='prompt'").fetchone()[0]
            return {"digest": sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
                    "prompt_events": prompts, "provider_effects": len(stack.provider_effects())}

        before = business_snapshot()
        oracle = json.loads((source / "observability/fixtures/v1/oracle.json").read_text())
        config = {"fixture": f"http://127.0.0.1:{fixture.server_address[1]}/", "live": stack.url("/observability/"),
                  "token": stack.tokens["alice"], "playwright": args.playwright, "browser": args.browser,
                  "oracle": {"healthy": oracle["requests"]["healthy/run-healthy"],
                             "unknown_keys": sorted(k for k, v in oracle["requests"].items()
                                                    if v["status"] == "unknown_outcome" and v.get("owner", "tenant-a") == "tenant-a")}}
        result = subprocess.run([shutil.which("node"), "-e", NODE], input=json.dumps(config),
                                capture_output=True, text=True, timeout=120)
        receipt["browser"] = json.loads(result.stdout) if result.stdout.strip() else {"status": "ERROR", "error": result.stderr[-4000:]}
        after = business_snapshot()
        receipt["business_before"], receipt["business_after"] = before, after
        receipt["business_unchanged"] = before == after
        receipt["status"] = "PASS" if result.returncode == 0 and before == after else "FAIL"
    except Exception as error:
        receipt.update(status="BLOCKED" if isinstance(error, PermissionError) else "ERROR", error=f"{type(error).__name__}: {error}")
    finally:
        if stack:
            stack.close()
        if fixture:
            fixture.shutdown(); fixture.server_close()
        receipt["source_hashes_after"] = hashes()
        receipt["source_unchanged_during_run"] = receipt["source_hashes_before"] == receipt["source_hashes_after"]
        if not receipt["source_unchanged_during_run"]:
            receipt["status"] = "SOURCE_CHANGED"
        text = json.dumps(receipt, indent=2).replace(str(source), "<canonical>")
        text = re.sub(r"rra_[A-Za-z0-9_-]{43}", "[REDACTED]", text)
        args.receipt.write_text(text + "\n")
        print(text)
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
