import test from "node:test";
import assert from "node:assert/strict";
import { ReportFormController, emptyDraft, submission } from "../../app/ui/reporter.mjs";

const id = "00000000-0000-4000-8000-000000000001";
const draft = () => ({ ...emptyDraft(), description: "A driver changed the agreed fare after we arrived at our destination.", provenance: "FIRST_HAND", unknown: true });
const receipt = { report_id: id, status: "queued", moderation_status: "pending", queue_pending: false, replayed: false };
function form(call = async () => receipt) {
  const calls = []; let keys = 0;
  const state = new ReportFormController({ call: async (name,args) => { calls.push({name,args:structuredClone(args)}); return call(name,args); }, draw: () => {}, key: () => `test-submission-key-${++keys}`, today: "2026-11-21" });
  state.draft = draft(); return { state, calls };
}

test("supporting material is part of the exact confirmed text, not separate corroboration", () => {
  const result = submission({ ...draft(), support: "Receipt wording: fixed fare.", links: "https://example.com/source", date: "2026-10-02" }, null, "2026-11-21");
  assert.match(result.report_text, /occurrence date \(day only; exact time unknown\): 2026-10-02/);
  assert.match(result.report_text, /supporting text \(unverified\)/);
  assert.match(result.report_text, /source links \(not checked\)/);
  assert.equal("occurred_at" in result, false);
  assert.equal("location_id" in result, false);
});

test("missing provenance, bad dates, unsafe links and overlong reports cannot be reviewed", () => {
  for (const change of [{provenance:""},{description:"too short"},{date:"2026-11-22"},{date:"2026-02-30"},{links:"javascript:alert(1)"},{links:"https://me:password@example.com"},{links:"https://a.com\nhttps://b.com\nhttps://c.com\nhttps://d.com"},{description:"x".repeat(6000),support:"x".repeat(1500),links:`https://example.com/${"x".repeat(1000)}`}])
    assert.throws(() => submission({...draft(),...change},null,"2026-11-21"));
});

test("location is explicitly unknown, resolved or unresolved; dates and country are never guessed", () => {
  assert.throws(() => submission({...draft(),unknown:false},null));
  assert.equal(submission({...draft(),unknown:false}, {id,name:"City"}).location_id,id);
  const unresolved = submission({...draft(),unknown:false,unresolved:true,place:"Unknown village"},null);
  assert.equal(unresolved.location_text,"Unknown village"); assert.equal("country_code" in unresolved,false);
});

test("review is editable, never submits, and changed content gets a new key", async () => {
  const {state,calls} = form(); state.review(); const key = state.submissionKey;
  assert.equal(calls.length,0); await state.submit(false); assert.equal(calls.length,0);
  state.edit(); state.change("description","The driver refused to honor the fare that we had agreed before departure."); state.review();
  assert.notEqual(state.submissionKey,key); assert.match(state.payload.report_text,/refused/);
  await state.submit(true); assert.equal(calls.length,1); assert.equal(state.phase,"success");
  assert.equal(calls[0].args.confirmed_by_user,true); assert.equal(calls[0].args.report_text,state.payload.report_text);
});

test("unknown network outcome freezes the exact payload/key and recovers an idempotent receipt", async () => {
  let attempt = 0;
  const {state,calls} = form(async () => { if (!attempt++) throw new Error("private failure text"); return {...receipt,replayed:true}; });
  state.review(); await state.submit(true); assert.equal(state.phase,"uncertain");
  assert.ok(!state.error.includes("private")); const previous = state.draft.description;
  state.edit(); state.change("description","Do not change an uncertain submission."); assert.equal(state.draft.description,previous);
  await state.submit(true); assert.deepEqual(calls[0],calls[1]); assert.equal(state.phase,"success"); assert.equal(state.receipt.replayed,true);
  await state.submit(true); assert.equal(calls.length,2);
});

test("definitive validation errors preserve entered data for correction and another review", async () => {
  const {state} = form(async () => { throw Object.assign(new Error("bad"),{code:"invalid_request"}); });
  state.review(); await state.submit(true); assert.equal(state.phase,"edit");
  assert.equal(state.draft.description,draft().description); assert.equal(state.submissionKey,null);
});

test("double click and delayed responses cannot submit twice or resurrect a closed draft", async () => {
  let resolve;
  const {state,calls} = form(() => new Promise(r => {resolve=r;})); state.review();
  const pending = state.submit(true); await state.submit(true); assert.equal(calls.length,1);
  state.close(); resolve(receipt); await pending; assert.equal(state.receipt,null); assert.equal(state.draft.description,"");
});

test("location lookup handles ambiguity and invalidates a selected location after edits", async () => {
  let ambiguous = true;
  const {state,calls} = form(async () => ambiguous ? {status:"ambiguous",candidates:[id]} : {status:"resolved",location_id:id,hierarchy:[{id,name:"Fiji"}]});
  state.change("unknown",false); state.change("place","Fiji"); await state.resolve();
  assert.equal(state.resolved,null); assert.match(state.error,/context/);
  ambiguous=false; await state.resolve(); assert.equal(state.resolved.id,id);
  assert.equal(calls[0].args.country_code,undefined); state.change("place","Another place"); assert.equal(state.resolved,null);
});

test("only a successful receipt allows a fresh separate report", async () => {
  const {state} = form(); state.review(); state.reset(); assert.equal(state.phase,"review");
  await state.submit(true); state.reset(); assert.equal(state.phase,"edit"); assert.equal(state.payload,null); assert.equal(state.draft.description,"");
});
