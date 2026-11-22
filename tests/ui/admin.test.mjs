import test from 'node:test';
import assert from 'node:assert/strict';
import { AdminClient } from '../../app/ui/admin.mjs';

const ok = value => ({ok:true,status:200,json:async()=>value});
test('admin token travels only in headers with no caching, cookies or redirects',async()=>{
 const calls=[];const client=new AdminClient(async(...args)=>{calls.push(args);return ok({accepted:true});});
 await assert.rejects(client.request('/v1/admin/overview'));
 client.token='synthetic-admin';
 await client.request('/v1/admin/commands',{confirmed:true},'fixed-review-key');
 const [path,options]=calls[0];assert.equal(path,'/v1/admin/commands');
 assert.equal(options.headers.Authorization,'Bearer synthetic-admin');assert.equal(options.headers['Idempotency-Key'],'fixed-review-key');
 assert.equal(options.cache,'no-store');assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
 for(const path of ['https://example.com/v1/admin/a','//example.com/v1/admin/a','/v1/admin/../../outside'])await assert.rejects(client.request(path));
 assert.equal(calls.length,1);
});
test('locking aborts pending requests and refuses late private responses',async()=>{
 let finish,signal;const client=new AdminClient(async(path,options)=>{signal=options.signal;return new Promise(resolve=>finish=resolve);});
 client.token='synthetic';const request=client.request('/v1/admin/items/reports');
 client.lock();assert.equal(signal.aborted,true);assert.equal(client.token,'');
 finish(ok({report_text:'private fixture'}));await assert.rejects(request,/locked/);
 assert.equal(client.controllers.size,0);
});
test('errors do not reflect server narratives and exact retries retain their key and body',async()=>{
 const calls=[];let count=0;const client=new AdminClient(async(path,options)=>{
  calls.push({path,key:options.headers['Idempotency-Key'],body:options.body});
  if(!count++)throw new Error('transport failed');return ok({replayed:true});
 });client.token='synthetic';const body={operation:'set_priority',priority:4,reason:'Reviewed'};
 await assert.rejects(client.request('/v1/admin/commands',body,'same-key'));
 assert.deepEqual(await client.request('/v1/admin/commands',body,'same-key'),{replayed:true});
 assert.deepEqual(calls[0],calls[1]);
 client.fetcher=async()=>({ok:false,status:409,json:async()=>({detail:'private server content'})});
 await assert.rejects(client.request('/v1/admin/overview'),error=>error.status===409&&!error.message.includes('private server content'));
});

test('default browser fetch is invoked without binding it to the client',async()=>{
 const original=globalThis.fetch;let called=false;
 globalThis.fetch=function(){assert.equal(this,undefined);called=true;return Promise.resolve(ok({}));};
 try {const client=new AdminClient();client.token='synthetic';await client.request('/v1/admin/overview');assert.equal(called,true);}
 finally {globalThis.fetch=original;}
});
