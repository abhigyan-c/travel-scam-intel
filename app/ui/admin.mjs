// Internal console: token and private records remain in memory, never URLs/storage.
export class AdminClient {
  constructor(fetcher = (...args) => fetch(...args)) { this.fetcher=fetcher; this.token=""; this.epoch=0; this.controllers=new Set(); }
  lock() { this.token=""; this.epoch++; for(const control of this.controllers)control.abort(); this.controllers.clear(); }
  async request(path, body, key) {
    if(!path.startsWith("/v1/admin/")||!new URL(path,"http://admin.invalid").pathname.startsWith("/v1/admin/")||!this.token)throw new Error("Unlock the dashboard first.");
    const generation=this.epoch, controller=new AbortController(); this.controllers.add(controller);
    try {
      const response=await this.fetcher(path,{method:body?"POST":"GET",headers:{Authorization:`Bearer ${this.token}`,...(body?{"Content-Type":"application/json","Idempotency-Key":key}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:controller.signal,cache:"no-store",credentials:"omit",redirect:"error"});
      const value=await response.json(); if(generation!==this.epoch)throw new Error("Dashboard locked.");
      if(!response.ok)throw Object.assign(new Error(response.status===401?"Admin authorization failed.":response.status===409?"Record changed or a safeguard prevented this action. Reread before making a new decision.":"Request failed. Check the fields or retry safely."),{status:response.status});
      return value;
    } finally {this.controllers.delete(controller);}
  }
}

function mount(doc) {
 const $=id=>doc.getElementById(id), client=new AdminClient();
 let kind="hypotheses", offset=0, record=null, target=null, evidenceOffset=0, selected=new Set(), pending=null, submitting=false, uncertain=false, viewEpoch=0, blobs=[];
 const pageFlags={prev:true,next:true,"e-prev":true,"e-next":true};
 const node=(tag,value)=>{const el=doc.createElement(tag);if(value!==undefined)el.textContent=String(value);return el;};
 const showError=error=>{$("error").hidden=false;$("error").textContent=error.message||"Request unavailable.";};
 const clearError=()=>{$("error").hidden=true;$("error").textContent="";};
 const clearMedia=()=>{for(const url of blobs)URL.revokeObjectURL(url);blobs=[];$("media").replaceChildren();};
 const resetDetail=()=>{viewEpoch++;record=null;target=null;selected.clear();pending=null;uncertain=false;$("detail").hidden=true;$("confirmation").hidden=true;$("action-form").hidden=false;$("record").textContent="";$("target-summary").textContent="";$("command-preview").textContent="";$("evidence").replaceChildren();$("reason").value="";for(const id of ["title","description","category","target-id","priority"])$(id).value="";$("edit-action").disabled=false;disableNavigation(false);clearMedia();};
 function lock() {client.lock();resetDetail();$("private").hidden=true;$("login").hidden=false;$("token").value="";$("rows").replaceChildren();$("overview").textContent="";$("page-info").textContent="";$("search").value="";$("status-filter").value="";$("message").textContent="Locked. Private view cleared.";clearError();}
 function drawRows(items) {
  $("rows").replaceChildren();$("action-heading").textContent=kind==="audit"?"Decision / audit":"Inspect";for(const item of items){
   const row=node("tr");row.append(node("td",item.title||item.name||item.hypothesis_id||item.id),node("td",item.status||item.moderation_status||item.review?.status||item.operation||item.decision||""));
   const cell=node("td");if(kind!=="audit"){const button=node("button","Inspect");button.onclick=()=>inspect(item).catch(showError);cell.append(button);}else cell.append(node("span",`${item.created_at} · ${item.source} · subject ${item.subject_id} · ${item.actor}: ${item.reason}`));row.append(cell);$("rows").append(row);
  }
 }
 async function load() {
  if(pending)throw new Error("Finish or edit the pending action before changing the view.");
  resetDetail();clearError();const epoch=viewEpoch;
  const status=$("status-filter").value.trim(), q=$("search").value.trim();
  const params=new URLSearchParams({limit:"20",offset:String(offset)});if(status)params.set("status",status);if(q)params.set("q",q);
  const path=kind==="emerging"?"/v1/admin/emerging-patterns":kind==="audit"?"/v1/admin/audit":`/v1/admin/items/${kind}`;
  const page=await client.request(path+"?"+params);if(epoch!==viewEpoch||!client.token)return;
  drawRows(page.items);$("page-info").textContent=`${page.total} records · Page ${Math.floor(offset/20)+1}`;pageFlags.prev=offset===0;pageFlags.next=!page.has_more;disableNavigation(false);$("message").textContent="Private records loaded.";
 }
 async function inspect(item) {
  if(pending)throw new Error("Finish or edit the pending action first.");resetDetail();clearError();const epoch=viewEpoch;
  const value=kind==="emerging"?item:await client.request(`/v1/admin/items/${kind}/${encodeURIComponent(item.id)}`);if(epoch!==viewEpoch||!client.token)return;
  record=value;$("record").textContent=JSON.stringify(record,null,2);$("detail").hidden=false;$("evidence-pager").hidden=true;
  $("load-evidence").hidden=kind!=="hypotheses";$("load-media").hidden=kind!=="reports";
  const choices={hypotheses:[["edit_hypothesis","Edit hypothesis"],["set_status","Review status"],["merge","Merge into target"],["split","Split selected evidence"],["move_evidence","Move selected evidence"]],resolutions:[["apply_resolution","Apply unchanged reviewed resolution"]],reports:[["moderate_report","Moderate report"]],jobs:[["retry_job","Reissue failed non-research job"]],locations:[["set_priority","Set eligible scheduler priority"]],documents:[["reprocess_document","Re-normalize stored raw document"]],emerging:[["emerging_decision","Review emerging candidate"]]};
  $("operation").replaceChildren();for(const [value,label]of choices[kind]||[]){const option=node("option",label);option.value=value;$("operation").append(option);}
  $("title").value=record.title||"";$("description").value=record.description||"";$("category").value=record.category||"";$("priority").value=String(record.research?.priority_override||0);$("target-id").value="";
  configure();$("detail-title").focus();
 }
 function configure(){
  const op=$("operation").value;$("draft-fields").hidden=!["edit_hypothesis","split"].includes(op);$("target-fields").hidden=!["merge","move_evidence"].includes(op);$("status-field").hidden=!["set_status","moderate_report","emerging_decision"].includes(op);$("priority-field").hidden=op!=="set_priority";
  const states=op==="set_status"?["candidate","active","disputed","inactive"]:op==="moderate_report"?["needs_review","rejected","accepted"]:["acknowledged","dismissed"];
  $("new-status").replaceChildren();for(const state of states){const option=node("option",state);option.value=state;$("new-status").append(option);}
  $("action-help").textContent=({set_priority:"Ranking adjustment only. Dormancy, freshness, retry cooldown, scheduler enablement and all budgets still apply.",retry_job:"Only failed ingest/process/report jobs can be reissued. Report retries may use Sarvam within existing per-job limits. Research jobs, leases and root budgets cannot be reset here.",reprocess_document:"Normalize stored raw external content again; this does not re-extract semantic claims. Private report documents use their guarded report retry.",apply_resolution:"Existing claim, candidate, geography and embedding snapshots must still match; this does not override held/sensitive publication rules.",split:"Select current evidence below, including at least one supporting record, and describe the child hypothesis.",move_evidence:"Select current evidence below. Claims and original attachments are retained in history."})[op]||"Review current source/target state and give a reason. Stale records are rejected.";
 }
 async function loadEvidence(){
  const epoch=viewEpoch,id=record.id;const page=await client.request(`/v1/admin/hypotheses/${id}/evidence?limit=20&offset=${evidenceOffset}`);if(epoch!==viewEpoch||!client.token)return;
  $("evidence").replaceChildren();for(const item of page.items){const box=node("article"),label=node("label");label.className="check";const check=node("input");check.type="checkbox";check.checked=selected.has(item.id);check.disabled=item.archived||!!pending;check.onchange=()=>check.checked?selected.add(item.id):selected.delete(item.id);label.append(check,node("span",`${item.id} · ${item.stance}${item.archived?" · archived":""}`));box.append(label,node("pre",JSON.stringify(item,null,2)));$("evidence").append(box);}
  $("evidence-pager").hidden=false;pageFlags["e-prev"]=evidenceOffset===0;pageFlags["e-next"]=!page.has_more;disableNavigation(!!pending);
 }
 async function loadMedia(){
  const epoch=viewEpoch,id=record.id;const page=await client.request(`/v1/admin/reports/${id}/attachments`);if(epoch!==viewEpoch||!client.token)return;clearMedia();
  for(const item of page.attachments){const article=node("article");article.append(node("p",`${item.id} · ${item.status} · ${item.width}×${item.height}`));const button=node("button","View private normalized image");button.onclick=async()=>{try{const response=await fetch(`/v1/admin/reports/${id}/attachments/${item.id}`,{headers:{Authorization:`Bearer ${client.token}`},cache:"no-store",credentials:"omit",redirect:"error"});if(!response.ok)throw new Error("Private image unavailable.");const blob=await response.blob();if(epoch!==viewEpoch||!client.token)return;const url=URL.createObjectURL(blob);blobs.push(url);const img=node("img");img.alt="Private normalized report attachment";img.src=url;article.append(img);button.disabled=true;}catch(error){showError(error);}};article.append(button);$("media").append(article);}
 }
 function build(){
  const operation=$("operation").value,reason=$("reason").value.trim();if(!reason)throw new Error("A review reason is required.");
  const base={operation,subject_id:record.id,reason,confirmed:true};
  if(["edit_hypothesis","set_status","merge","split","move_evidence","retry_job","reprocess_document"].includes(operation))base.expected_updated_at=record.updated_at;
  if(["edit_hypothesis","split"].includes(operation))base.draft={title:$("title").value,description:$("description").value,category:$("category").value};
  if(["merge","move_evidence"].includes(operation)){if(!target||target.id!==$("target-id").value.trim())throw new Error("Inspect the current target first.");base.target_id=target.id;base.expected_target_at=target.updated_at;}
  if(["split","move_evidence"].includes(operation)){if(!selected.size)throw new Error("Inspect and select evidence first.");base.evidence_ids=[...selected];}
  if(operation==="set_status")base.status=$("new-status").value;
  if(operation==="set_priority"){base.expected_updated_at=record.research.updated_at;base.priority=Number($("priority").value);}
  if(operation==="moderate_report"){base.status=$("new-status").value;base.expected_revision=record.moderation_revision;}
  if(operation==="emerging_decision"){base.subject_id=record.hypothesis_id;base.revision=record.review.revision;base.action=$("new-status").value;}
  return{path:"/v1/admin/commands",body:base};
 }
 function disableNavigation(value){for(const id of ["kind","status-filter","search","refresh","prev","next","load-target","load-evidence","load-media","e-prev","e-next"])$(id).disabled=value||!!pageFlags[id];}
 $("login").onsubmit=async event=>{event.preventDefault();clearError();client.lock();client.token=$("token").value;$("token").value="";try{const summary=await client.request("/v1/admin/overview");$("overview").textContent=`Jobs: ${JSON.stringify(summary.jobs)} · Reports: ${JSON.stringify(summary.reports)}`;$("login").hidden=true;$("private").hidden=false;await load();}catch(error){client.lock();showError(error);}};
 $("logout").onclick=lock;$("refresh").onclick=()=>load().catch(showError);
 $("kind").onchange=()=>{kind=$("kind").value;offset=0;$("status-filter").value="";load().catch(showError);};
 $("prev").onclick=()=>{offset=Math.max(0,offset-20);load().catch(showError);};$("next").onclick=()=>{offset+=20;load().catch(showError);};
 $("operation").onchange=configure;$("target-id").oninput=()=>{target=null;$("target-summary").textContent="";};
 $("load-target").onclick=async()=>{try{const epoch=viewEpoch,id=$("target-id").value.trim();const loaded=await client.request(`/v1/admin/items/hypotheses/${encodeURIComponent(id)}`);if(epoch!==viewEpoch||id!==$("target-id").value.trim())return;target=loaded;$("target-summary").textContent=JSON.stringify(target,null,2);}catch(error){target=null;showError(error);}};
 $("load-evidence").onclick=()=>{evidenceOffset=0;loadEvidence().catch(showError);};$("e-prev").onclick=()=>{evidenceOffset=Math.max(0,evidenceOffset-20);loadEvidence().catch(showError);};$("e-next").onclick=()=>{evidenceOffset+=20;loadEvidence().catch(showError);};$("load-media").onclick=()=>loadMedia().catch(showError);
 $("action-form").onsubmit=event=>{event.preventDefault();try{pending={...build(),key:crypto.randomUUID()};uncertain=false;$("edit-action").disabled=false;$("command-preview").textContent=JSON.stringify(pending.body,null,2);$("confirmed").checked=false;$("commit").disabled=true;$("confirmation").hidden=false;$("action-form").hidden=true;disableNavigation(true);clearError();}catch(error){showError(error);}};
 $("confirmed").onchange=()=>$("commit").disabled=!$("confirmed").checked||submitting;
 $("edit-action").onclick=()=>{if(uncertain||submitting)return;pending=null;$("confirmation").hidden=true;$("action-form").hidden=false;disableNavigation(false);};
 $("commit").onclick=async()=>{if(!pending||!$("confirmed").checked||submitting)return;submitting=true;$("commit").disabled=true;$("edit-action").disabled=true;const epoch=viewEpoch;try{const result=await client.request(pending.path,pending.body,pending.key);if(epoch!==viewEpoch||!client.token)return;pending=null;uncertain=false;disableNavigation(false);await load();$("message").textContent=`Action accepted: ${JSON.stringify(result)}`;}catch(error){if(epoch!==viewEpoch||!client.token)return;uncertain=!error.status||error.status>=500;showError(new Error(uncertain?"Outcome uncertain. Retry the exact reviewed action with the same key; do not edit or lock this view until reconciled.":error.message));$("confirmed").checked=false;$("edit-action").disabled=uncertain;}finally{submitting=false;$("commit").disabled=!$("confirmed").checked;}};
 window.addEventListener("pagehide",lock);
}
if(typeof document!=="undefined")mount(document);
