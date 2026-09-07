#!/usr/bin/env node
// custos relay-bridge — standalone Remote Pi relay client for the headlong agent.
// Holds the LXC's Pi-key, authenticates to the relay (hello/challenge/auth),
// prints the pairing QR, and bridges the phone chat to the headlong agent.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { execFile } = require("node:child_process");
const WebSocket = require("ws");
const ed = require("@noble/ed25519");
const qrcode = require("qrcode-terminal");

// --- config ------------------------------------------------------------
const RELAY_URL = process.env.CUSTOS_RELAY_URL || "ws://192.168.86.46:3000";
const STATE_DIR = process.env.CUSTOS_RELAY_STATE || "/root/.pi/remote";
const IDENTITY_FILE = path.join(STATE_DIR, "identity.json");
const PEERS_FILE = path.join(STATE_DIR, "peers.json");
const SESSION_NAME = "custos";
const CWD = "/opt/custos/repo";
const SENDER = "phone"; // the name the agent sees for phone messages
const QR_TTL_MS = 600_000;

// --- ed25519 (noble v3 + node sha512, per the reference) ---------------
ed.hashes.sha512 = (...msgs) => {
  const h = crypto.createHash("sha512");
  for (const m of msgs) h.update(m);
  return Uint8Array.from(h.digest());
};

function loadOrCreateKeypair() {
  try {
    const raw = JSON.parse(fs.readFileSync(IDENTITY_FILE, "utf8"));
    return {
      publicKey: Buffer.from(raw.publicKey, "base64"),
      secretKey: Buffer.from(raw.secretKey, "base64"),
    };
  } catch {
    const secretKey = crypto.randomBytes(32);
    const publicKey = Buffer.from(ed.getPublicKey(secretKey));
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(IDENTITY_FILE, JSON.stringify(
      { publicKey: publicKey.toString("base64"), secretKey: secretKey.toString("base64") },
      null, 2), { mode: 0o600 });
    console.log("[bridge] new Pi-key at", IDENTITY_FILE);
    return { publicKey, secretKey };
  }
}
const keypair = loadOrCreateKeypair();

// roomIdFor(cwd, name) — first 12 chars of base64url(sha256(realpath + \0 + name))
function roomId() {
  let target = CWD;
  try { target = fs.realpathSync(CWD); } catch {}
  const sep = String.fromCharCode(0);
  return crypto.createHash("sha256").update(target + sep + SESSION_NAME).digest("base64url").slice(0, 12);
}

// --- QR / pairing ------------------------------------------------------
let active = null;
function issueToken(ttlMs = QR_TTL_MS) {
  const token = crypto.randomBytes(16).toString("base64url");
  active = { token, expiresAt: Date.now() + ttlMs, consumed: false };
  return token;
}
function consumeToken(token) {
  if (!active || active.token !== token) return "unknown";
  if (active.consumed) return "consumed";
  if (Date.now() > active.expiresAt) return "expired";
  active.consumed = true;
  return "ok";
}
function printQR() {
  const token = issueToken();
  const epkB64 = Buffer.from(keypair.publicKey).toString("base64url");
  const params = new URLSearchParams({ t: token, epk: epkB64, n: SESSION_NAME.slice(0, 80) });
  params.set("rm", roomId());
  const uri = `remotepi://pair?${params.toString()}`;
  console.log("=== scan with the Remote Pi app (token valid 10 minutes) ===");
  console.log(uri);
  qrcode.generate(uri, { small: true });
  console.log("=== relay:", RELAY_URL, "| room:", roomId(), "===");
}

// --- peers -------------------------------------------------------------
function savePeer(peer) {
  let peers = [];
  try { peers = JSON.parse(fs.readFileSync(PEERS_FILE, "utf8")); } catch {}
  peers = peers.filter((p) => p.remote_epk !== peer.remote_epk);
  peers.push(peer);
  fs.writeFileSync(PEERS_FILE, JSON.stringify(peers, null, 2), { mode: 0o600 });
}

// --- the headlong bridge ----------------------------------------------
function transport(args, input = "") {
  return new Promise((resolve) => {
    // Message bytes go through stdin. No interpolation into executable Bash.
    const child = execFile("bash", ["-c",
      "source /root/.headlong/app/.identities/custos/activate >/dev/null 2>&1; " +
      'exec /usr/bin/python3 /opt/custos/repo/renewal/custos_transport.py "$@"',
      "custos-relay", ...args], { cwd: CWD, timeout: 15000, maxBuffer: 1024 * 1024 },
      (err, stdout) => {
        if (err) { resolve({ ok: false }); return; }
        try { resolve({ ok: true, ...JSON.parse(stdout) }); }
        catch { resolve({ ok: false }); }
      });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

function chatSend(text, requestId) {
  return transport(["send", "--sender", SENDER, "--authority", "operator",
    "--request-id", requestId, "--source-url", `remote-pi:${requestId}`], text);
}

const OUTBOX_FILE = path.join(STATE_DIR, "headlong-outbox.json");
let outbox = { trajectory: "", offset: 0, requests: {}, events: [] };
try { outbox = JSON.parse(fs.readFileSync(OUTBOX_FILE, "utf8")); }
catch (error) { if (error.code !== "ENOENT") throw error; }
function saveOutbox() {
  const temporary = OUTBOX_FILE + ".tmp";
  const fd = fs.openSync(temporary, "w", 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(outbox)); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temporary, OUTBOX_FILE);
  const directory = fs.openSync(STATE_DIR, "r");
  try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
}
const activePeers = new Set();
let pumping = false;
async function pumpOutbox() {
  if (pumping) return;
  pumping = true;
  try {
    const batch = await transport(["outbox", "--sender", SENDER,
      "--offset", String(outbox.offset), "--trajectory", outbox.trajectory]);
    if (!batch.ok) return;
    for (const event of batch.events) {
      const id = "custos:" + event.step_id;
      if (outbox.events.some((old) => old.message.id === id)) continue;
      const request = event.request_id && outbox.requests[event.request_id];
      const targets = request ? [request.peer] : [...authorizedPeers];
      outbox.events.push({ targets, delivered: [], message: {
        type: "agent_message", id,
        ...(request ? { in_reply_to: request.msgId } : {}),
        text: event.content,
      }});
    }
    outbox.offset = batch.offset;
    outbox.trajectory = batch.trajectory;
    // Persist events BEFORE advancing delivery. The source remains the native
    // trajectory; this spool survives late replies, disconnects and restarts.
    saveOutbox();
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    for (const event of outbox.events) {
      for (const peer of event.targets) {
        if (!activePeers.has(peer) || event.delivered.includes(peer)) continue;
        await new Promise((resolve, reject) => ws.send(JSON.stringify({
          peer, ct: Buffer.from(JSON.stringify(event.message)).toString("base64"),
        }), (error) => error ? reject(error) : resolve()));
        // This is a relay enqueue receipt, not proof that a phone read it.
        // session_sync replays retained history with the same stable IDs.
        event.delivered.push(peer);
        saveOutbox();
      }
    }
  } catch (error) {
    console.error("[bridge] durable outbox pending:", error.code || error.name);
  } finally { pumping = false; }
}

// --- the relay ---------------------------------------------------------
let ws = null;
const authorizedPeers = new Set();
try {
  for (const peer of JSON.parse(fs.readFileSync(PEERS_FILE, "utf8"))) {
    if (typeof peer.remote_epk === "string") authorizedPeers.add(peer.remote_epk);
  }
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
let qrTimer = null;

function sendTo(peer, msg) {
  const ct = Buffer.from(JSON.stringify(msg)).toString("base64");
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ peer, ct }));
}

function startQRRotation() {
  clearInterval(qrTimer);
  printQR();
  qrTimer = setInterval(printQR, QR_TTL_MS);
}
// SIGHUP — force a fresh pairing QR on demand (the `custos pair` command).
process.on("SIGHUP", () => {
  console.log("[bridge] pairing requested — fresh QR");
  startQRRotation();
});


function connect() {
  ws = new WebSocket(RELAY_URL);
  ws.on("open", () => {
    ws.send(JSON.stringify({
      type: "hello",
      pubkey: Buffer.from(keypair.publicKey).toString("base64"),
      room_id: roomId(),
      room_meta: { name: SESSION_NAME, cwd: CWD, model: "qwen3.8-27b" },
    }));
  });
  ws.on("message", (raw) => {
    const text = Buffer.isBuffer(raw) ? raw.toString() : String(raw);
    for (const l of text.split("\n")) {
      const t = l.trim();
      if (!t) continue;
      let frame;
      try { frame = JSON.parse(t); } catch { continue; }
      handleFrame(frame);
    }
  });
  ws.on("close", () => { activePeers.clear(); console.error("[relay] closed — reconnecting in 5s"); setTimeout(connect, 5000); });
  ws.on("error", (e) => { console.error("[relay] ws error:", e.message || e); });
}

function handleFrame(frame) {
  if (frame.type === "challenge") {
    const nonce = Buffer.from(frame.nonce, "base64");
    const sig = ed.sign(nonce, keypair.secretKey);
    ws.send(JSON.stringify({ type: "auth", sig: Buffer.from(sig).toString("base64") }));
    console.log("[relay] authenticated — pairing QR active");
    startQRRotation();
    return;
  }
  if (frame.type === "error") {
    console.error("[relay] error:", frame.code || frame.message || "unknown");
    return;
  }
  if (frame.ct) {
    let inner;
    try { inner = JSON.parse(Buffer.from(frame.ct, "base64").toString()); } catch { return; }
    handleInner(frame.peer, inner);
  }
}

function handleInner(peer, msg) {
  if (authorizedPeers.has(peer)) activePeers.add(peer);
  console.log("[inner]", msg.type);
  if (msg.type === "pair_request") {
    const res = consumeToken(msg.token);
    if (res !== "ok") {
      const code = res === "expired" ? "token_expired" : res === "consumed" ? "token_consumed" : "token_unknown";
      sendTo(peer, { type: "pair_error", in_reply_to: msg.id, code, message: res });
      return;
    }
    activePeers.add(peer);
    authorizedPeers.add(peer);
    savePeer({ name: msg.device_name || "device", remote_epk: peer, paired_at: new Date().toISOString() });
    sendTo(peer, {
      type: "pair_ok", in_reply_to: msg.id, session_name: SESSION_NAME,
      session_started_at: Date.now(), room_id: roomId(),
      harness: "headlong", hostname: os.hostname(),
    });
    console.log("[pair] paired with", msg.device_name, "(", peer.slice(0, 12), "…)");
    void pumpOutbox();
    return;
  }
  if (msg.type === "user_message") {
    if (!authorizedPeers.has(peer)) return;
    handleUserMessage(peer, msg);
    return;
  }
  if (msg.type === "ping") { sendTo(peer, { type: "pong", in_reply_to: msg.id }); return; }
  if (msg.type === "session_sync") {
    if (!authorizedPeers.has(peer)) return;
    const events = outbox.events.filter((event) => event.targets.includes(peer))
      .slice(-50).map((event) => event.message);
    sendTo(peer, { type: "session_history", in_reply_to: msg.id, events });
    void pumpOutbox();
    return;
  }
  if (["session_new", "session_compact", "model_set", "thinking_set", "list_models"].includes(msg.type)) {
    sendTo(peer, { type: "action_error", in_reply_to: msg.id, action: msg.type, error: "unsupported in the headlong bridge" });
    return;
  }
  // pair_request when already paired: ignore (idempotent per the reference).
}

async function handleUserMessage(peer, msg) {
  if (typeof msg.text !== "string" || typeof msg.id !== "string") return;
  const requestId = `relay:${peer}:${msg.id}`;
  if (!outbox.requests[requestId]) {
    outbox.requests[requestId] = { peer, msgId: msg.id };
    saveOutbox();
  }
  const sent = await chatSend(msg.text, requestId);
  if (!sent.ok) {
    sendTo(peer, { type: "error", in_reply_to: msg.id, code: "internal_error", message: "chat send failed" });
    return;
  }
  void pumpOutbox();
}

console.log("[bridge] custos relay-bridge starting | relay:", RELAY_URL, "| room:", roomId());
connect();
setInterval(() => { void pumpOutbox(); }, 2000);