import { Bridge } from "./bridge.mjs";
// MCP Apps bridge and report controller. No network access outside host-proxied tools.
export const PAGE_SIZE = 20;
const UUID = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
export function publicReport(value) {
  return value?.contract_version === "research-report-v1" && UUID.test(value.run_id)
    && UUID.test(value.location?.id) && typeof value.revision === "string"
    && Array.isArray(value.items) && value.latest_run && value.retained_report;
}
export function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
export function filtered(items, filters) {
  const rank = { strong: 4, moderate: 3, limited: 2, no_support: 1, unknown: 0 };
  const number = x => Number.isFinite(x) ? x : -1;
  return items.filter(x => (!filters.confidence || x.metrics?.confidence_label === filters.confidence)
    && (!filters.scope || x.scope === filters.scope) && (!filters.category || x.category === filters.category)
    && (!filters.area || x.location?.id === filters.area)
    && (!filters.freshness || x.freshness === filters.freshness)).slice().sort((a, b) => {
    if (filters.sort === "checked") return (Date.parse(b.source_checked_at) || 0) - (Date.parse(a.source_checked_at) || 0);
    if (filters.sort === "reports") return number(b.metrics?.all_time?.raw_report_count) - number(a.metrics?.all_time?.raw_report_count);
    return (rank[b.metrics?.confidence_label] || 0) - (rank[a.metrics?.confidence_label] || 0)
      || number(b.metrics?.confidence_score) - number(a.metrics?.confidence_score);
  });
}
export function ending(report) {
  if (!report.latest_run.terminal) return report.latest_run.status === "queued" ? "Research queued — available findings below" : "Researching — available findings below";
  if (report.outcome === "nothing_found") return "Nothing found in the sources checked";
  if (report.latest_run.status === "completed") return "Research finished — sourced findings available";
  return report.total ? "Research finished with partial results" : "Research incomplete — no findings available";
}

export class ReportController {
  constructor({ call, draw, store, load, now = Date.now,
    schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id) }) {
    Object.assign(this, { call, draw, store, load, now, schedule, cancel });
    this.report = null; this.watch = null; this.timer = null; this.busy = false;
    this.paused = false; this.hidden = false; this.closed = false; this.epoch = 0;
    this.verified = false;
    this.offset = 0; this.error = ""; this.done = false; this.completions = 0;
    this.calls = 0; this.deadline = 0; this.filters = { sort: "confidence" };
  }
  persist() {
    if (!this.report) return;
    // Persist addresses and view choices only. Re-read publication eligibility on reconnect.
    this.store(this.report.run_id, { watch: this.watch, paused: this.paused, offset: this.offset,
      deadline: this.deadline, calls: this.calls, done: this.done, filters: this.filters });
  }
  paint() { if (!this.closed) this.draw(this); }
  stop() { if (this.timer !== null) this.cancel(this.timer); this.timer = null; }
  accept(report, watch = false) {
    if (!publicReport(report) || (this.report && report.run_id !== this.report.run_id)) throw new Error("Invalid report response");
    this.report = report; this.offset = report.offset;
    this.verified = true;
    if (watch) this.watch = report.watch;
    if (report.latest_run.terminal && !this.done) { this.done = true; this.completions++; }
    this.persist(); this.paint();
  }
  async start(report) {
    if (!publicReport(report) || this.closed) return;
    if (this.report) return; // Host echoes from UI reads must not create duplicate loops.
    const saved = this.load(report.run_id) || {};
    this.report = report; this.watch = saved.watch || null; this.offset = saved.offset || 0;
    this.filters = saved.filters || this.filters; this.paused = !!saved.paused;
    this.deadline = saved.deadline || this.now() + 600000; this.calls = saved.calls || 0;
    this.done = !!saved.done; this.paint();
    await this.read(); // Never trust persisted public content or the host's cached initial result.
  }
  async read() {
    if (this.closed || this.busy || !this.report) return;
    this.busy = true; const epoch = this.epoch;
    try {
      const value = await this.call("get_scam_report", { run_id: this.report.run_id, limit: PAGE_SIZE, offset: this.offset });
      if (epoch !== this.epoch || this.closed) return;
      this.error = ""; this.accept(value);
    } catch { if (!this.closed) this.error = "Report read failed. Retained data may be out of date. Resume to retry safely."; }
    finally { this.busy = false; this.paint(); }
    this.queue();
  }
  queue() {
    this.stop();
    if (this.closed || this.paused || this.hidden || this.error || !this.report || this.report.latest_run.terminal) return;
    if (this.calls >= 60 || this.now() >= this.deadline || (this.watch && (this.watch.waits_remaining <= 0 || Date.parse(this.watch.expires_at) <= this.now()))) {
      this.error = "Automatic watching ended. Available results remain below. Resume to follow this same run."; this.paint(); return;
    }
    const delay = Math.max(1000, Math.min(30000, (this.report.next_check_after_seconds || 1) * 1000));
    this.timer = this.schedule(() => this.tick(), delay);
  }
  async tick() {
    this.timer = null;
    if (this.closed || this.paused || this.hidden || this.busy || this.now() >= this.deadline || this.calls >= 60) { this.queue(); return; }
    this.busy = true; const epoch = this.epoch;
    try {
      if (!this.watch) {
        const followed = await this.call("watch_scam_report", { run_id: this.report.run_id });
        if (epoch !== this.epoch || this.closed) return;
        if (!publicReport(followed) || followed.run_id !== this.report.run_id || !followed.watch)
          throw new Error("Invalid watch response");
        this.watch = followed.watch; this.persist();
      }
      if (!this.report.latest_run.terminal && !this.hidden && !this.paused) {
        this.calls++; this.persist();
        const value = await this.call("wait_for_scam_report", { run_id: this.report.run_id, watch_id: this.watch.id,
          after_revision: this.report.revision, wait_seconds: 5, limit: PAGE_SIZE, offset: this.offset });
        if (epoch !== this.epoch || this.closed) return;
        this.accept(value, true);
        if (["expired", "limit_reached", "capacity"].includes(value.wait_status) && !value.latest_run.terminal)
          this.error = "Watching paused by the service. Available results remain below. Resume to retry this run.";
      }
    } catch { if (!this.closed) this.error = "Updates unavailable. Resume to reconnect to this run; no new research will start."; }
    finally { this.busy = false; this.paint(); }
    this.queue();
  }
  pause() { this.paused = true; this.stop(); this.persist(); this.paint(); }
  async resume() {
    if (this.busy || this.closed || !this.report) return;
    this.paused = false; this.error = "";
    if (this.now() >= this.deadline || this.calls >= 60 || (this.watch && (Date.parse(this.watch.expires_at) <= this.now() || this.watch.waits_remaining <= 0))) {
      this.watch = null; this.calls = 0; this.deadline = this.now() + 600000;
    }
    this.persist(); await this.read();
  }
  async visibility(hidden) {
    this.hidden = hidden; this.stop();
    if (!hidden && !this.paused && !this.busy) await this.read();
    this.paint();
  }
  async page(offset) {
    if (this.busy || offset < 0 || offset > 1000) return;
    this.stop(); this.offset = offset; await this.read();
  }
  close() { this.closed = true; this.epoch++; this.stop(); this.persist(); }
}

function mount(win, doc) {
  const $ = id => doc.getElementById(id);
  const element = (tag, text, className) => { const node = doc.createElement(tag); node.textContent = String(text ?? "Unknown"); if (className) node.className = className; return node; };
  const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" }) : "Unknown";
  const pair = (list, title, value) => list.append(element("dt", title), element("dd", value));
  const bridge = new Bridge(win);
  let evidenceId = null, evidenceOffset = 0, evidenceEpoch = 0, renderedRevision = null, renderedPage = null, contextKey = null, observer, invocation = "shared";
  const clearEvidence = () => { evidenceId = null; evidenceEpoch++; $("evidence").hidden = true; $("evidence-items").replaceChildren(); };
  const storageKey = run => `travel-scam:explorer:v1:${run}:${invocation}`;
  const controller = new ReportController({
    call: (name, args) => bridge.tool(name, args),
    load: run => { try { return JSON.parse(win.sessionStorage.getItem(storageKey(run))); } catch { return null; } },
    store: (run, value) => { try { win.sessionStorage.setItem(storageKey(run), JSON.stringify(value)); } catch { /* Storage can be disabled in host sandboxes. */ } },
    draw: state => {
      const report = state.report; if (!report) return;
      if (!state.verified) {
        $("destination").textContent = report.location.name;
        $("status").textContent = state.error ? "Current public findings could not be checked" : "Checking current public findings…";
        $("error").hidden = !state.error; $("error").textContent = state.error;
        $("resume").hidden = !state.error; $("resume").disabled = state.busy;
        $("cards").replaceChildren(); return;
      }
      $("destination").textContent = `${report.location.name}${report.location.country_code ? ` · ${report.location.country_code}` : ""}`;
      $("status").textContent = ending(report);
      $("coverage").textContent = `Coverage: ${report.retained_report.coverage} · Source freshness: ${report.retained_report.freshness} · Run checked: ${date(report.latest_run.last_checked_at)}${report.latest_run.stop_reasons.length ? ` · Limits: ${report.latest_run.stop_reasons.join(", ").replaceAll("_", " ")}` : ""}`;
      $("error").hidden = !state.error; $("error").textContent = state.error;
      $("pause").disabled = state.busy || report.latest_run.terminal || state.paused;
      $("resume").hidden = !(state.paused || state.error); $("resume").disabled = state.busy;
      $("watch-state").textContent = report.latest_run.terminal ? "Final available report" : state.paused || state.hidden ? "Updates paused; shared research continues" : "Updates while this view is active";
      $("run-info").textContent = `Run ${report.run_id}${report.superseded ? " · A newer run exists; this view keeps its original run" : ""}. Last completed research: ${date(report.retained_report.last_completed_at)}.`;
      if (renderedRevision !== report.revision) { clearEvidence(); renderedRevision = report.revision; }
      $("previous").disabled = state.busy || report.offset === 0;
      $("next").disabled = state.busy || !report.has_more || report.offset + PAGE_SIZE > 1000;
      const pageKey = JSON.stringify([report.revision, report.offset, state.filters]);
      if (bridge.ready && bridge.capabilities.updateModelContext?.structuredContent && contextKey !== pageKey) {
        contextKey = pageKey;
        bridge.request("ui/update-model-context", { structuredContent: {
          run_id: report.run_id, research_status: report.latest_run.status,
          coverage: report.retained_report.coverage, freshness: report.retained_report.freshness,
          offset: report.offset, filters: state.filters,
          visible_pattern_ids: filtered(report.items, state.filters).map(item => item.id),
        } }).catch(() => {}); // Context is for future turns; it does not trigger a narrative.
      }
      if (renderedPage === pageKey) return; // Preserve focus and open selectors during unchanged polls.
      renderedPage = pageKey;
      const category = $("category"), selected = state.filters.category || "";
      category.replaceChildren(element("option", "All categories")); category.firstChild.value = "";
      for (const label of new Set([...report.items.map(x => x.category), ...(selected ? [selected] : [])])) { const option = element("option", label); option.value = label; category.append(option); }
      const areas = new Map(report.items.map(item => [item.location.id, item.location.name]));
      if (state.filters.area && !areas.has(state.filters.area)) areas.set(state.filters.area, "Selected area (not on this page)");
      $("area").replaceChildren(element("option", "All areas")); $("area").firstChild.value = "";
      for (const [id, name] of areas) { const option = element("option", name); option.value = id; $("area").append(option); }
      for (const id of ["confidence", "scope", "category", "area", "freshness", "sort"]) $(id).value = state.filters[id] || "";
      const items = filtered(report.items, state.filters);
      $("page-info").textContent = `${report.total} available findings · Page ${Math.floor(report.offset / PAGE_SIZE) + 1} · ${items.length} shown. Filters and sorting apply to this page only.${report.membership_may_be_truncated ? " Retained membership may be truncated." : ""}`;
      const cards = $("cards"); cards.replaceChildren();
      if (!items.length) cards.append(element("p", report.items.length ? "No findings match these page filters." : report.outcome === "nothing_found" ? "Nothing found in the sources checked. This is not proof that scams do not exist." : "No findings available yet; research is incomplete."));
      for (const item of items) {
        const card = doc.createElement("article"); card.append(element("h3", item.title), element("span", `${item.status} · ${item.metrics.confidence_label} confidence`, "badge"), element("p", item.description));
        const details = doc.createElement("dl");
        pair(details, "Actual location", (item.location_path || [item.location]).map(x => x.name).join(" › "));
        pair(details, "Scope", `${item.scope}${item.scope === "descendant" ? " — specific child area; not citywide" : item.scope === "ancestor" ? " — broader parent area; not a local incident" : ""}`);
        pair(details, "Source checked", `${date(item.source_checked_at)} · ${item.freshness}`);
        pair(details, "Metrics as of", date(item.metrics.as_of));
        pair(details, "Evidence records", item.metrics.all_time?.raw_report_count ?? "Unavailable");
        pair(details, "Dated / undated", `${item.metrics.dated_reports ?? "?"} / ${item.metrics.undated_or_invalid_reports ?? "?"}`);
        pair(details, "Limitations", (item.metrics.limitations || []).join("; ") || "Candidate status does not verify an incident or establish current activity."); card.append(details);
        const button = element("button", "Inspect evidence and source guidance"); button.addEventListener("click", () => showEvidence(item.id, 0)); card.append(button); cards.append(card);
      }
    },
  });
  async function showEvidence(id, offset) {
    const epoch = ++evidenceEpoch; evidenceId = id; evidenceOffset = offset;
    $("evidence").hidden = false; $("evidence-status").textContent = "Loading current public evidence…";
    $("evidence-items").replaceChildren(); $("evidence-next").disabled = true; $("evidence-previous").disabled = true;
    try {
      const page = await bridge.tool("get_scam_evidence", { pattern_id: id, limit: 5, offset });
      if (epoch !== evidenceEpoch || controller.closed) return;
      if (page.hypothesis_id !== id || !Array.isArray(page.items)) throw new Error("Invalid evidence");
      $("evidence-status").textContent = `${page.total} evidence records · Actual location: ${page.location.name}. Records may share a source; this is not independent corroboration.`;
      for (const item of page.items) {
        const card = doc.createElement("article"); card.append(element("h3", `${item.label.replaceAll("_", " ")} · ${item.stance}`));
        card.append(element("blockquote", item.excerpt || item.claim || "No public excerpt available."));
        const details = doc.createElement("dl"); pair(details, "Provenance", item.provenance_class);
        pair(details, "Publication date", date(item.published_at)); pair(details, "Occurrence date", date(item.occurred_at));
        pair(details, "Source quality", item.source_quality); pair(details, "Independence credit", item.independence_credit); card.append(details);
        const url = safeUrl(item.source_url); if (url) { const link = element("a", `Read source · ${new URL(url).hostname}`); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link); }
        $("evidence-items").append(card);
      }
      $("evidence-previous").disabled = offset === 0; $("evidence-next").disabled = !page.has_more || offset + 5 > 1000;
      $("evidence-title").tabIndex = -1; $("evidence-title").focus();
    } catch { if (epoch === evidenceEpoch) $("evidence-status").textContent = "Evidence unavailable or no longer public. Retry by opening the finding again."; }
  }
  for (const id of ["confidence", "scope", "category", "area", "freshness", "sort"]) $(id).addEventListener("change", () => { controller.filters[id] = $(id).value; controller.persist(); controller.paint(); });
  $("pause").addEventListener("click", () => controller.pause()); $("resume").addEventListener("click", () => controller.resume());
  $("previous").addEventListener("click", () => controller.page(controller.offset - PAGE_SIZE)); $("next").addEventListener("click", () => controller.page(controller.offset + PAGE_SIZE));
  $("evidence-previous").addEventListener("click", () => showEvidence(evidenceId, evidenceOffset - 5)); $("evidence-next").addEventListener("click", () => showEvidence(evidenceId, evidenceOffset + 5));
  $("close-evidence").addEventListener("click", clearEvidence);
  bridge.onresult = result => { if (publicReport(result?.structuredContent)) controller.start(result.structuredContent); };
  bridge.oncontext = context => {
    if (context.toolInfo?.id !== undefined) invocation = String(context.toolInfo.id);
    if (context.theme) doc.documentElement.style.colorScheme = context.theme;
    for (const [key, value] of Object.entries(context.styles?.variables || {})) if (key.startsWith("--") && typeof value === "string") doc.documentElement.style.setProperty(key, value);
    for (const side of ["top", "bottom", "left", "right"]) { const inset = context.safeAreaInsets?.[side]; if (Number.isFinite(inset)) doc.documentElement.style.setProperty(`--safe-${side}`, `${Math.max(0, Math.min(200, inset))}px`); }
  };
  const close = () => { controller.close(); clearEvidence(); observer?.disconnect(); };
  bridge.onclose = close;
  controller.hidden = doc.hidden;
  doc.addEventListener("visibilitychange", () => { clearEvidence(); controller.visibility(doc.hidden); });
  win.addEventListener("pagehide", () => { close(); bridge.close(); });
  observer = new ResizeObserver(() => bridge.post({ method: "ui/notifications/size-changed", params: { height: Math.ceil($("explorer").getBoundingClientRect().height) } }));
  observer.observe($("explorer"));
  bridge.connect().catch(() => { $("status").textContent = "This host could not connect the interactive view. The conversation tools remain available."; });
}
if (typeof window !== "undefined" && typeof document !== "undefined") mount(window, document);
