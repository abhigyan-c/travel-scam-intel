import { Bridge } from "../../app/ui/bridge.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { ReportController, ending, filtered, safeUrl } from "../../app/ui/explorer.mjs";

const run = "00000000-0000-4000-8000-000000000001";
function report(extra = {}) {
  return { contract_version: "research-report-v1", run_id: run, location: { id: run, name: "Test City" },
    revision: "0".repeat(64), latest_run: { status: "researching", terminal: false },
    retained_report: { coverage: "partial", freshness: "unknown" }, items: [], offset: 0,
    limit: 20, total: 0, next_check_after_seconds: 1, ...extra };
}
function harness({ finishAt = 0, response, saved } = {}) {
  let now = 1000000, timerId = 0, data = saved, polls = 0;
  const timers = new Map(), calls = [], draws = [];
  const controller = new ReportController({
    now: () => now,
    schedule: (fn, delay) => { timers.set(++timerId, { fn, at: now + delay }); return timerId; },
    cancel: id => timers.delete(id),
    store: (id, value) => { data = structuredClone(value); }, load: () => data,
    draw: state => draws.push({ terminal: state.report?.latest_run.terminal, error: state.error }),
    call: async (name, args) => {
      calls.push({ name, args }); if (response) return response(name, args);
      const watch = { id: run, expires_at: new Date(1600000).toISOString(), waits_remaining: 60 - polls };
      if (name === "wait_for_scam_report") { now += 5000; polls++; }
      const terminal = finishAt > 0 && now >= finishAt;
      return report({ offset: args.offset || 0, watch: name === "get_scam_report" ? null : watch,
        latest_run: { status: terminal ? "completed" : "researching", terminal },
        outcome: terminal ? "nothing_found" : "research_incomplete", wait_status: terminal ? "terminal" : "unchanged" });
    },
  });
  return { controller, calls, draws, timers, saved: () => data, now: () => now,
    advance: ms => { now += ms; },
    async step() {
      const [id, timer] = timers.entries().next().value || [];
      if (!timer) return false;
      timers.delete(id); now = Math.max(now, timer.at); await timer.fn(); return true;
    } };
}

test("five-minute delayed completion automatically updates one view within bounded calls", async () => {
  const h = harness({ finishAt: 1300000 });
  await h.controller.start(report());
  for (let count = 0; count < 65 && await h.step(); count++);
  assert.equal(h.controller.report.latest_run.terminal, true);
  assert.equal(h.controller.completions, 1);
  assert.equal(h.controller.calls, 50);
  assert.equal(h.calls.filter(x => x.name === "watch_scam_report").length, 1);
  assert.equal(h.calls.filter(x => x.name === "request_scam_report").length, 0);
  assert.equal(h.timers.size, 0);
  await h.controller.start(report());
  assert.equal(h.controller.completions, 1);
  assert.equal(h.timers.size, 0);
});

test("terminal partial, failure and negative endings are distinct", () => {
  assert.equal(ending(report({ latest_run: { status: "completed", terminal: true }, outcome: "nothing_found" })), "Nothing found in the sources checked");
  for (const status of ["partial", "failed"]) {
    assert.match(ending(report({ latest_run: { status, terminal: true }, total: 2 })), /partial results/);
    assert.match(ending(report({ latest_run: { status, terminal: true }, total: 0 })), /incomplete/);
  }
  assert.match(ending(report()), /Researching/);
});

test("closing, hiding, pausing and reconnecting preserve root and stop timers", async () => {
  const h = harness(); await h.controller.start(report()); await h.step();
  await h.controller.visibility(true); assert.equal(h.timers.size, 0);
  await h.controller.visibility(false); assert.equal(h.calls.at(-1).name, "get_scam_report");
  h.controller.pause(); assert.equal(h.timers.size, 0);
  await h.controller.resume(); assert.equal(h.timers.size, 1);
  h.controller.close(); assert.equal(h.timers.size, 0);
  assert.ok(h.saved().watch);
  assert.equal("report" in h.saved(), false);
  const reconnected = harness({ saved: h.saved() }); await reconnected.controller.start(report());
  assert.equal(reconnected.calls[0].name, "get_scam_report"); await reconnected.step();
  assert.equal(reconnected.calls.filter(x => x.name === "watch_scam_report").length, 0);
  assert.ok(reconnected.calls.every(x => x.args.run_id === run)); reconnected.controller.close();
});

test("poll outages stop, preserve report and resume reads without buying research", async () => {
  let fail = false;
  const h = harness({ response: (name) => {
    if (fail) throw new Error("private upstream error");
    return report({ watch: { id: run, expires_at: new Date(1600000).toISOString(), waits_remaining: 60 } });
  } });
  await h.controller.start(report()); fail = true; await h.step();
  assert.match(h.controller.error, /Updates unavailable/); assert.equal(h.timers.size, 0);
  assert.ok(!h.controller.error.includes("private")); fail = false;
  await h.controller.resume(); assert.equal(h.calls.at(-1).name, "get_scam_report"); h.controller.close();
});

test("watch limit stops; only explicit resume opens another watch on the same run", async () => {
  const h = harness(); await h.controller.start(report());
  for (let count = 0; count < 65 && await h.step(); count++);
  assert.equal(h.controller.calls, 60); assert.match(h.controller.error, /watching ended/);
  assert.equal(h.timers.size, 0); await h.controller.resume(); await h.step();
  assert.equal(h.calls.filter(x => x.name === "watch_scam_report").length, 2);
  assert.ok(h.calls.every(x => x.args.run_id === run)); h.controller.close();
});

test("wall-clock expiry survives browser suspension and requires explicit resume", async () => {
  const h = harness(); await h.controller.start(report());
  await h.controller.visibility(true); h.advance(700000);
  await h.controller.visibility(false); assert.equal(h.timers.size, 0);
  assert.match(h.controller.error, /watching ended/); assert.equal(h.controller.calls, 0);
});

test("late response after teardown cannot render or schedule another poll", async () => {
  let resolve;
  const h = harness({ response: () => new Promise(r => { resolve = r; }) });
  const opening = h.controller.start(report()); h.controller.close(); resolve(report()); await opening;
  assert.equal(h.timers.size, 0); assert.equal(h.draws.length, 1);
});

test("cached initial content is not verified when the fresh eligibility read fails", async () => {
  const h = harness({ response: () => { throw new Error("Unavailable"); } });
  await h.controller.start(report({ items: [{ title: "formerly public" }] }));
  assert.equal(h.controller.verified, false);
  assert.match(h.controller.error, /read failed/);
  assert.equal(h.timers.size, 0);
});

test("pagination preserves page when creating its own watch and ignores root substitution", async () => {
  const h = harness(); await h.controller.start(report()); await h.controller.page(20); await h.step();
  assert.equal(h.controller.offset, 20); assert.equal(h.calls.at(-1).args.offset, 20);
  assert.throws(() => h.controller.accept(report({ run_id: "00000000-0000-4000-8000-000000000002" })), /Invalid/);
  h.controller.close();
});

test("filters preserve actual scope and unknown dates; source links reject script URLs", () => {
  const items = [{ id: 1, scope: "ancestor", category: "Taxi", freshness: "unknown", metrics: { confidence_label: "limited" } },
    { id: 2, scope: "descendant", category: "Taxi", freshness: "fresh", metrics: { confidence_label: "moderate" } }];
  assert.deepEqual(filtered(items, { scope: "ancestor" }).map(x => x.id), [1]);
  assert.deepEqual(filtered(items, { sort: "confidence" }).map(x => x.id), [2, 1]);
  for (const url of ["javascript:alert(1)", "data:text/html,bad", "/relative", "https://user:secret@example.com"]) assert.equal(safeUrl(url), null);
  assert.equal(safeUrl("https://example.com/source"), "https://example.com/source");
});

test("bridge accepts only parent responses, negotiates capabilities and clears pending on teardown", async () => {
  const sent = [], listeners = new Map();
  const win = { parent: { postMessage: message => sent.push(message) }, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  const bridge = new Bridge(win), connecting = bridge.connect();
  const receive = (data, source = win.parent) => listeners.get("message")({ source, data: { jsonrpc: "2.0", ...data } });
  receive({ id: 1, result: {} }, {}); assert.equal(bridge.pending.size, 1);
  receive({ id: 1, result: { hostCapabilities: { serverTools: {} } } }); await connecting;
  assert.equal(sent.at(-1).method, "ui/notifications/initialized");
  receive({ id: "host-ping", method: "ping" }); assert.deepEqual(sent.at(-1).result, {});
  const reading = bridge.tool("get_scam_report", { run_id: run });
  receive({ id: 2, result: { structuredContent: report() } }); assert.equal((await reading).run_id, run);
  let closed = false; bridge.onclose = () => { closed = true; };
  receive({ id: 3, method: "ui/resource-teardown", params: {} });
  assert.equal(closed, true); assert.equal(bridge.pending.size, 0); assert.equal(listeners.size, 0);
});
