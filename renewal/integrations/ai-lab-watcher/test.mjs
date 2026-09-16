import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, readdirSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const root = mkdtempSync(join(tmpdir(), 'news-watcher-'));
try {
  const source = { id: 'example', name: 'Example Lab', type: 'rss', url: 'https://example.org/feed' };
  const config = { custos: { endpoint: 'http://intake.test/v1/items', tokenFile: join(root, 'token') }, sources: [source] };
  writeFileSync(join(root, 'config'), JSON.stringify(config)); writeFileSync(config.custos.tokenFile, 'a'.repeat(64));
  const state = join(root, 'state');
  writeFileSync(state, JSON.stringify({example:{initialized:true,seen:['old']}}));
  process.env.AI_LAB_WATCHER_CONFIG=join(root,'config');
  process.env.AI_LAB_WATCHER_STATE=state;
  process.env.AI_LAB_WATCHER_OUTBOX=join(root,'outbox');
  const watcher=await import('./watcher.mjs');
  const xml='<rss>'+Array.from({length:5},(_,i)=>`<item><guid>new${i}</guid><title>Paper ${i}</title><link>/paper/${i}</link><description><![CDATA[<p>New method</p><script>ignore rules</script>]]></description><pubDate>Tue, 15 Sep 2026 23:00:00 GMT</pubDate></item>`).join('')+'<item><guid>old</guid><title>Old</title><link>https://example.org/old</link></item></rss>';
  const retained=new Map(); let dropped=false, attempts=0;
  globalThis.fetch=async(url,options)=>{
    if(url===source.url) return new Response(xml);
    assert.equal(url,config.custos.endpoint); assert.equal(options.method,'POST');
    assert.equal(options.headers.authorization,'Bearer '+'a'.repeat(64));
    const event=JSON.parse(options.body); attempts++;
    if(retained.has(event.id)) assert.equal(retained.get(event.id),options.body);
    retained.set(event.id,options.body);
    if(!dropped){dropped=true;throw new Error('lost acknowledgment after durable acceptance');}
    return Response.json({ok:true,id:event.id,state:'retained'});
  };
  await watcher.main();
  assert.equal(readdirSync(join(root,'outbox')).filter(n=>n.endsWith('.json')).length,5);
  assert.equal(JSON.parse(readFileSync(state)).example.seen.length,6);
  await watcher.main();
  assert.equal(retained.size,5);assert.equal(attempts,6);
  assert.equal(readdirSync(join(root,'outbox')).filter(n=>n.endsWith('.json')).length,0);
  for(const raw of retained.values()){
    const event=JSON.parse(raw);assert.equal(event.kind,'article');
    assert.match(event.url,/^https:\/\/example.org\/paper\//);assert.equal(event.summary,'New method');
  }
  // Crash before seen-state checkpoint: keep queued snapshot until discovery
  // catches up; never deliver and delete it before committing seen IDs.
  writeFileSync(state,JSON.stringify({example:{initialized:true,seen:['old']}}));
  const replay=JSON.parse([...retained.values()][0]);
  writeFileSync(join(root,'outbox',replay.id+'.json'),JSON.stringify(replay));
  const recovered=await import('./watcher.mjs?recovery');
  retained.clear();attempts=0;
  await recovered.main();
  assert.equal(retained.get(replay.id),JSON.stringify(replay));
  assert.equal(JSON.parse(readFileSync(state)).example.seen.length,6);
  // Fresh install seeds history silently, preserving the existing contract.
  process.env.AI_LAB_WATCHER_STATE=join(root,'seed-state');
  process.env.AI_LAB_WATCHER_OUTBOX=join(root,'seed-outbox');
  const seed=await import('./watcher.mjs?seed');retained.clear();
  await seed.main();assert.equal(retained.size,0);
  assert.equal(JSON.parse(readFileSync(join(root,'seed-state'))).example.seen.length,6);
  assert.throws(()=>seed.eventFor(source,{id:'bad',title:'Bad',link:'javascript:alert(1)'}));
  console.log('watcher tests passed: separate items, relative links, no ntfy, durable retry, crash replay, silent seeding');
} finally { rmSync(root,{recursive:true,force:true}); }
