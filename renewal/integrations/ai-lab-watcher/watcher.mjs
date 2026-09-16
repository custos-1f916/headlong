#!/usr/bin/env node
// AI Lab Watcher — polls AI-lab feeds, HF org APIs, and pages; queues each new item for Custos review.
// Zero-dependency Node ESM (Node >= 18). Config: sources.json next to this file.
// State: /var/lib/ai-lab-watcher/state.json (override with AI_LAB_WATCHER_STATE).

import { readFileSync, writeFileSync, renameSync, existsSync, mkdirSync, readdirSync, unlinkSync, openSync, fsyncSync, closeSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const CONFIG = JSON.parse(readFileSync(process.env.AI_LAB_WATCHER_CONFIG || new URL('sources.json', import.meta.url), 'utf8'));
const STATE_FILE = process.env.AI_LAB_WATCHER_STATE || '/var/lib/ai-lab-watcher/state.json';
const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
const OUTBOX = process.env.AI_LAB_WATCHER_OUTBOX || join(dirname(STATE_FILE), 'custos-outbox');
// Must exceed the largest feed's item count or evicted tail items re-announce every
// run (OpenAI's feed ships its full 1,123-item history; HF blog ships 838).
const SEEN_CAP = 2500;
const ERROR_STREAK_ALERT = 5; // consecutive failures before alerting (once, until it recovers)

const state = existsSync(STATE_FILE) ? JSON.parse(readFileSync(STATE_FILE, 'utf8')) : {};
const hash = (s) => createHash('sha256').update(s).digest('hex');
const log = (...a) => console.log(new Date().toISOString(), ...a);

async function get(url, accept = '*/*') {
  const res = await fetch(url, {
    headers: { 'user-agent': UA, accept },
    redirect: 'follow',
    signal: AbortSignal.timeout(20000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.text();
}

function atomicWrite(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  const fd = openSync(path + '.tmp', 'w', 0o600);
  try { writeFileSync(fd, JSON.stringify(value)); fsyncSync(fd); } finally { closeSync(fd); }
  renameSync(path + '.tmp', path);
  const directory = openSync(dirname(path), 'r');
  try { fsyncSync(directory); } finally { closeSync(directory); }
}

export function eventFor(src, item, kind = ({ rss: 'article', hf: 'model', page: 'page-change' })[src.type]) {
  const sourceUrl = src.homepage || src.url || `https://huggingface.co/${src.org}`;
  const url = new URL(item.link || sourceUrl, sourceUrl);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || item.id.length > 2048) throw new Error('invalid public item identity or URL');
  return { version: 1, id: hash(src.id + '\n' + kind + '\n' + item.id), kind,
    source_id: src.id, source_name: src.name.slice(0, 180), source_url: sourceUrl,
    item_id: item.id, title: item.title.slice(0, 500), url: url.href,
    summary: (item.summary || '').slice(0, 3000), published_at: (item.published_at || '').slice(0, 100),
    detected_at: new Date().toISOString() };
}

export function enqueue(event) {
  const path = join(OUTBOX, event.id + '.json');
  if (existsSync(path)) return; // keep the exact snapshot through retries
  mkdirSync(OUTBOX, { recursive: true });
  if (readdirSync(OUTBOX).filter(n => n.endsWith('.json')).length >= 10000) throw new Error('Custos outbox full');
  atomicWrite(path, event);
}

export async function deliverPending() {
  if (!existsSync(OUTBOX)) return { delivered: 0, pending: 0 };
  const files = readdirSync(OUTBOX).filter(n => /^[a-f0-9]{64}\.json$/.test(n)).sort();
  let delivered = 0;
  if (!files.length) return { delivered, pending: 0 };
  const token = readFileSync(CONFIG.custos.tokenFile, 'utf8').trim();
  if (!/^[a-f0-9]{64}$/.test(token)) throw new Error('invalid Custos intake credential');
  for (const name of files.slice(0, 40)) {
    const path = join(OUTBOX, name);
    const body = readFileSync(path, 'utf8');
    try {
      const res = await fetch(CONFIG.custos.endpoint, { method: 'POST',
        headers: { 'content-type': 'application/json', authorization: 'Bearer ' + token },
        body, signal: AbortSignal.timeout(15000) });
      if (!res.ok) throw new Error(`Custos intake HTTP ${res.status}`);
      const receipt = await res.json();
      if (!receipt.ok || receipt.id !== name.slice(0, -5) || receipt.state !== 'retained') throw new Error('invalid Custos retention receipt');
      unlinkSync(path); delivered++;
    } catch (error) {
      log(`Custos delivery deferred: ${error.message}; durable item retained`);
      break;
    }
  }
  return { delivered, pending: files.length - delivered };
}

// --- parsing helpers -------------------------------------------------------
const first = (re, s) => {
  const m = s.match(re);
  return m ? m[1] : undefined;
};
const unCdata = (s) => s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1');
const decode = (s) =>
  unCdata(s)
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .trim();
const stripTags = (s) =>
  s
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]*>/g, ' ');

// Handles both RSS 2.0 <item> and Atom <entry>
export function parseFeed(xml) {
  const items = [];
  for (const block of xml.split(/<(?:item|entry)[\s>]/i).slice(1)) {
    const title = decode(first(/<title[^>]*>([\s\S]*?)<\/title>/i, block) ?? '(untitled)');
    const link =
      first(/<link[^>]*rel="alternate"[^>]*href="([^"]+)"/i, block) ??
      first(/<link[^>]*href="([^"]+)"/i, block) ??
      decode(first(/<link[^>]*>([\s\S]*?)<\/link>/i, block) ?? '');
    const id =
      decode(
        first(/<guid[^>]*>([\s\S]*?)<\/guid>/i, block) ??
          first(/<id[^>]*>([\s\S]*?)<\/id>/i, block) ??
          '',
      ) ||
      link ||
      title;
    const summary = decode(stripTags(unCdata(first(/<(?:description|summary|content:encoded)[^>]*>([\s\S]*?)<\/(?:description|summary|content:encoded)>/i, block) || ''))).replace(/\s+/g, ' ').slice(0, 3000);
    const published_at = decode(first(/<(?:pubDate|published|updated)[^>]*>([\s\S]*?)<\/(?:pubDate|published|updated)>/i, block) || '');
    if (id) items.push({ id, title, link: decode(link), summary, published_at });
  }
  return items;
}

// --- source adapters -------------------------------------------------------
async function pollRss(src) {
  const xml = await get(src.url, 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*');
  const lb = first(/<lastBuildDate>([\s\S]*?)<\/lastBuildDate>/i, xml);
  const t = lb ? Date.parse(lb.trim()) : NaN;
  return { items: parseFeed(xml), lastBuildMs: Number.isNaN(t) ? undefined : t };
}

async function pollHf(src) {
  const url = `https://huggingface.co/api/models?author=${encodeURIComponent(src.org)}&sort=createdAt&direction=-1&limit=20`;
  const arr = JSON.parse(await get(url, 'application/json'));
  if (!Array.isArray(arr)) throw new Error('HF API returned non-array');
  return {
    items: arr.map((m) => {
      const mid = m.modelId || m.id;
      return { id: mid, title: mid, link: `https://huggingface.co/${mid}` };
    }),
  };
}

async function pollPage(src) {
  const html = await get(src.url);
  const text = stripTags(html).replace(/\s+/g, ' ').trim();
  const hash = createHash('sha256').update(text).digest('hex').slice(0, 16);
  return { items: [{ id: `page:${hash}`, title: `${src.name}: page changed`, link: src.url }] };
}

const POLLERS = { rss: pollRss, hf: pollHf, page: pollPage };

// --- announce + per-source driver ------------------------------------------
async function announce(src, fresh) {
  for (const item of [...fresh].reverse()) enqueue(eventFor(src, item));
}

async function pollSource(src) {
  const st = (state[src.id] ??= { seen: [] });
  try {
    const { items, lastBuildMs } = await POLLERS[src.type](src);
    if (!items.length && !src.allowEmpty) throw new Error('parsed 0 items');
    const seen = new Set(st.seen);
    const fresh = items.filter((i) => !seen.has(i.id));
    if (!st.initialized) {
      log(`[${src.id}] seeded ${items.length} items`);
    } else if (fresh.length) {
      await announce(src, fresh);
      log(`[${src.id}] ${fresh.length} new: ${fresh.map((f) => f.title).join(' | ').slice(0, 300)}`);
    } else {
      log(`[${src.id}] ok, nothing new (${items.length} items)`);
    }
    st.seen = [...new Set([...items.map((i) => i.id), ...st.seen])].slice(0, SEEN_CAP);
    st.initialized = true;
    st.errorStreak = 0;
    st.errorNotified = false;
    st.errorEpisode = null;
    st.lastOkAt = Date.now();
    // staleness alarm for third-party regenerated feeds (e.g. Turing Institute substitutes)
    if (src.staleDays && lastBuildMs && Date.now() - lastBuildMs > src.staleDays * 86400000) {
      if (!st.staleWarnedAt || Date.now() - st.staleWarnedAt > 7 * 86400000) {
        enqueue(eventFor(src, { id: 'stale:' + new Date().toISOString().slice(0, 10),
          title: `${src.name}: stale feed`, link: src.url,
          summary: `Feed last regenerated ${new Date(lastBuildMs).toISOString()}; substitute coverage may be stale.` }, 'source-health'));
        st.staleWarnedAt = Date.now();
      }
    }
  } catch (e) {
    st.errorStreak = (st.errorStreak || 0) + 1;
    log(`[${src.id}] ERROR (streak ${st.errorStreak}): ${e.message ?? e}`);
    if (st.errorStreak >= ERROR_STREAK_ALERT && !st.errorNotified) {
      try {
        st.errorEpisode = st.errorEpisode || Date.now();
        enqueue(eventFor(src, { id: 'failure:' + st.errorEpisode,
          title: `${src.name}: source failing`, link: src.url || `https://huggingface.co/${src.org}`,
          summary: `Failed ${st.errorStreak} consecutive polls. Last error: ${e.message ?? e}` }, 'source-health'));
        st.errorNotified = true;
      } catch (error) { log(`Health event retained for retry: ${error.message}`); }

    }
  }
}

// --- main ------------------------------------------------------------------
export async function main() {
  if (!CONFIG.custos?.endpoint || !CONFIG.custos?.tokenFile) throw new Error('Custos destination required; ntfy publishing is disabled');
  const sources = CONFIG.sources.filter((s) => s.enabled !== false);
  const queue = [...sources];
  async function worker() { while (queue.length) await pollSource(queue.shift()); }
  await Promise.all(Array.from({ length: 6 }, worker));
  atomicWrite(STATE_FILE, state); // commit seen IDs before removing accepted outbox entries
  const delivered = await deliverPending();
  log(`done: ${sources.length} sources polled; Custos ${delivered.delivered} delivered, ${delivered.pending} pending`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
