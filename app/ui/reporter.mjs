import { Bridge } from "./bridge.mjs";
const UUID = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
export function emptyDraft() {
  return { place: "", country: "", type: "", date: "", provenance: "", description: "", support: "", links: "", unresolved: false, unknown: false };
}
export function submission(draft, resolved, today = new Date().toLocaleDateString("en-CA")) {
  if (!["FIRST_HAND", "WITNESSED", "SECOND_HAND"].includes(draft.provenance)) throw new Error("Choose how you know about this incident.");
  const description = draft.description.trim();
  if (description.length < 20 || description.length > 6000) throw new Error("Describe the incident in 20–6000 characters.");
  if (draft.date) {
    const parsed = new Date(`${draft.date}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.date) || !Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0,10) !== draft.date || draft.date > today)
      throw new Error("Choose a valid date that is not in the future, or leave it unknown.");
  }
  let location = {};
  if (!draft.unknown) {
    if (draft.unresolved && draft.place.trim()) location.location_text = draft.place.trim();
    else if (resolved && UUID.test(resolved.id)) location.location_id = resolved.id;
    else throw new Error("Check the location, mark its name as unresolved, or select location unknown.");
  }
  const urls = draft.links.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  if (urls.length > 3) throw new Error("Use no more than three source links.");
  for (const value of urls) {
    let url; try { url = new URL(value); } catch { throw new Error("Source links must be absolute HTTP or HTTPS URLs."); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Use HTTP/HTTPS links without embedded credentials.");
  }
  const parts = [description];
  if (draft.date) parts.push(`Submitter-reported occurrence date (day only; exact time unknown): ${draft.date}`);
  if (draft.support.trim()) parts.push(`Submitter-supplied supporting text (unverified):\n${draft.support.trim()}`);
  if (urls.length) parts.push(`Submitter-supplied source links (not checked):\n${urls.join("\n")}`);
  const report_text = parts.join("\n\n");
  if (report_text.length > 8000) throw new Error("The complete report exceeds 8,000 characters. Shorten the description or supporting material.");
  return { report_text, provenance_class: draft.provenance, ...location };
}
export class ReportFormController {
  constructor({ call, draw, key = () => crypto.randomUUID(), today }) {
    Object.assign(this, { call, draw, key, today });
    this.draft = emptyDraft(); this.resolved = null; this.phase = "edit"; this.payload = null;
    this.submissionKey = null; this.receipt = null; this.error = ""; this.busy = false; this.closed = false; this.epoch = 0;
  }
  paint() { if (!this.closed) this.draw(this); }
  change(field, value) {
    if (this.busy || this.phase !== "edit") return;
    this.draft[field] = value; this.error = "";
    if (["place", "country", "type", "unknown", "unresolved"].includes(field)) this.resolved = null;
    if (field === "unknown" && value) this.draft.unresolved = false;
    if (field === "unresolved" && value) this.draft.unknown = false;
    this.epoch++; this.paint();
  }
  async resolve() {
    if (this.busy || this.closed || this.phase !== "edit") return;
    const { place, country, type } = this.draft;
    if (!place.trim()) { this.error = "Enter a place name first."; this.paint(); return; }
    if (country && !/^[A-Z]{2}$/.test(country)) { this.error = "Use a two-letter uppercase country code, or leave it blank."; this.paint(); return; }
    this.busy = true; this.error = ""; this.paint(); const epoch = this.epoch;
    try {
      const result = await this.call("resolve_scam_location", { location: place.trim(), ...(country ? { country_code: country } : {}), ...(type ? { location_type: type } : {}) });
      if (epoch !== this.epoch || this.closed) return;
      const leaf = result.hierarchy?.find(x => x.id === result.location_id);
      if (!["resolved", "provisional"].includes(result.status) || !UUID.test(result.location_id) || !leaf)
        throw new Error("Location needs more country/parent context. Be more specific or mark it as unresolved.");
      this.resolved = { id: result.location_id, name: result.hierarchy.map(x => x.name).join(" › "), provisional: result.status === "provisional" };
      this.draft.unresolved = false; this.draft.unknown = false;
    } catch (error) { if (!this.closed) this.error = error.code || !error.message.startsWith("Location needs") ? "Location lookup failed. Retry safely or submit its name as unresolved." : error.message; }
    finally { this.busy = false; this.paint(); }
  }
  review() {
    if (this.busy || this.closed || this.phase !== "edit") return;
    try {
      this.payload = submission(this.draft, this.resolved, this.today);
      this.submissionKey = this.key(); this.phase = "review"; this.error = "";
    } catch (error) { this.error = error.message; }
    this.paint();
  }
  edit() {
    if (this.busy || this.phase !== "review") return;
    this.phase = "edit"; this.payload = null; this.submissionKey = null; this.error = ""; this.paint();
  }
  async submit(confirmed) {
    if (!confirmed || this.busy || this.closed || !["review", "uncertain"].includes(this.phase) || !this.payload || !this.submissionKey) return;
    this.busy = true; this.phase = "sending"; this.error = ""; this.paint();
    try {
      const receipt = await this.call("submit_scam_report", { ...this.payload, idempotency_key: this.submissionKey, confirmed_by_user: true });
      if (this.closed) return;
      if (!UUID.test(receipt?.report_id) || typeof receipt.replayed !== "boolean" || typeof receipt.queue_pending !== "boolean") throw new Error("Receipt unavailable");
      this.receipt = receipt; this.phase = "success";
    } catch (error) {
      if (!this.closed) {
        if (error.code === "invalid_request") { this.phase = "edit"; this.payload = null; this.submissionKey = null; this.error = "The report was not accepted. Check its text and fields, then review it again."; }
        else { this.phase = "uncertain"; this.error = "We could not confirm acceptance. Keep this view open and retry this exact submission; it will reuse the same key to avoid duplicates. Editing is locked until a receipt is confirmed."; }
      }
    } finally { this.busy = false; this.paint(); }
  }
  reset() {
    if (this.busy || this.phase !== "success") return;
    this.draft = emptyDraft(); this.resolved = null; this.payload = null; this.submissionKey = null;
    this.receipt = null; this.phase = "edit"; this.error = ""; this.epoch++; this.paint();
  }
  close() { this.closed = true; this.epoch++; this.draft = emptyDraft(); this.resolved = null; this.payload = null; this.submissionKey = null; this.receipt = null; }
}
function mount(win, doc) {
  const $ = id => doc.getElementById(id), bridge = new Bridge(win); let started = false, lastPhase = "", observer;
  const text = (tag, value) => { const element = doc.createElement(tag); element.textContent = value; return element; };
  const controller = new ReportFormController({ call: (name,args) => bridge.tool(name,args), draw: state => {
    $("status").textContent = state.busy ? state.phase === "sending" ? "Submitting your confirmed report…" : "Checking the location…" : state.phase === "success" ? "Report received" : state.phase === "uncertain" ? "Acceptance not yet confirmed" : state.phase === "review" ? "Ready for your confirmation" : "Draft — not submitted";
    $("error").hidden = !state.error; $("error").textContent = state.error;
    $("draft").hidden = state.phase !== "edit"; $("review-section").hidden = !["review","sending","uncertain"].includes(state.phase); $("receipt").hidden = state.phase !== "success";
    for (const [field,id] of Object.entries({place:"place",country:"country",type:"place-type",date:"date",provenance:"provenance",description:"description",support:"support",links:"links",unresolved:"unresolved",unknown:"unknown-location"})) {
      const input = $(id); if (input.type === "checkbox") input.checked = state.draft[field]; else if (input.value !== state.draft[field]) input.value = state.draft[field]; input.disabled = state.busy;
    }
    $("resolve").disabled = state.busy || state.draft.unknown; $("review").disabled = state.busy;
    $("location-status").textContent = state.resolved ? `Selected: ${state.resolved.name}${state.resolved.provisional ? " (provisional)" : ""}` : state.draft.unknown ? "Location unknown" : state.draft.unresolved ? "Place name will be submitted as unresolved context" : "No checked location selected";
    if (state.payload) {
      $("review-text").textContent = state.payload.report_text;
      const details = $("review-details"); details.replaceChildren();
      for (const [label,value] of [["Location",state.payload.location_id ? state.resolved.name : state.payload.location_text ? `${state.payload.location_text} (unresolved)` : "Unknown"],["Provenance",{FIRST_HAND:"Personally experienced",WITNESSED:"Witnessed",SECOND_HAND:"Heard/read second-hand"}[state.payload.provenance_class]],["Occurrence date",state.draft.date ? `${state.draft.date} (day only)` : "Unknown"]]) details.append(text("dt",label),text("dd",value));
    } else { $("review-text").textContent = ""; $("review-details").replaceChildren(); }
    if (state.phase !== lastPhase) { $("confirm").checked = false; if (state.phase === "review") $("review-title").focus(); if (state.phase === "success") $("receipt-title").focus(); lastPhase = state.phase; }
    $("edit").disabled = state.busy || state.phase === "uncertain";
    $("confirm").disabled = state.busy; $("submit").disabled = state.busy || !$("confirm").checked;
    $("submit").textContent = state.phase === "uncertain" ? "Retry exact confirmed submission" : "Submit confirmed report";
    if (state.receipt) $("receipt-details").textContent = `Receipt ${state.receipt.report_id}. Processing: ${state.receipt.status}. Review: ${state.receipt.moderation_status}.${state.receipt.replayed ? " Existing receipt recovered; no duplicate report was created." : ""}${state.receipt.queue_pending ? " Processing will be retried by the service." : ""}`;
    else $("receipt-details").textContent = "";
  } });
  const fields = {place:"place",country:"country",type:"place-type",date:"date",provenance:"provenance",description:"description",support:"support",links:"links",unresolved:"unresolved",unknown:"unknown-location"};
  for (const [field,id] of Object.entries(fields)) $(id).addEventListener("input", () => controller.change(field, $(id).type === "checkbox" ? $(id).checked : field === "country" ? $(id).value.toUpperCase() : $(id).value));
  $("resolve").addEventListener("click", () => controller.resolve());
  $("draft").addEventListener("submit", event => { event.preventDefault(); controller.review(); });
  $("edit").addEventListener("click", () => controller.edit());
  $("confirm").addEventListener("change", () => { $("submit").disabled = !$("confirm").checked || controller.busy; });
  $("submit").addEventListener("click", () => controller.submit($("confirm").checked));
  $("new-report").addEventListener("click", () => controller.reset()); $("clear").addEventListener("click", () => controller.reset());
  bridge.onresult = result => {
    const form = result?.structuredContent;
    if (started || form?.contract_version !== "report-form-v1" || !UUID.test(form.form_id)) return;
    started = true;
    if (form.location && UUID.test(form.location.id)) {
      controller.draft.place = form.location.name; controller.draft.country = form.location.country_code || ""; controller.draft.type = form.location.type;
      controller.resolved = { id:form.location.id,name:form.location.name,provisional:form.location.provisional };
    }
    controller.paint();
  };
  bridge.oncontext = context => {
    if (context.theme) doc.documentElement.style.colorScheme = context.theme;
    for (const [key,value] of Object.entries(context.styles?.variables || {})) if (key.startsWith("--") && typeof value === "string") doc.documentElement.style.setProperty(key,value);
    for (const side of ["top","bottom","left","right"]) if (Number.isFinite(context.safeAreaInsets?.[side])) doc.documentElement.style.setProperty(`--safe-${side}`,`${Math.max(0,Math.min(200,context.safeAreaInsets[side]))}px`);
  };
  const close = () => { controller.close(); observer?.disconnect(); for (const id of Object.values(fields)) { if ($(id).type === "checkbox") $(id).checked=false; else $(id).value=""; } $("review-text").textContent=""; $("review-details").replaceChildren(); $("receipt-details").textContent=""; };
  bridge.onclose = close; win.addEventListener("pagehide", () => { close(); bridge.close(); });
  observer = new ResizeObserver(() => bridge.post({method:"ui/notifications/size-changed",params:{height:Math.ceil($("reporter").getBoundingClientRect().height)}})); observer.observe($("reporter"));
  bridge.connect().catch(() => { $("status").textContent = "This host could not connect the form. No report was submitted."; });
}
if (typeof window !== "undefined" && typeof document !== "undefined") mount(window, document);
