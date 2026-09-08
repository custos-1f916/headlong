# Signal operator setup

## State at 2026-09-08

Custos is registered as `custos_1f916.74`, with profile name Custos and an
explicit AI-participant description. Phone-number sharing is disabled. The
Google Voice number, registration PIN, stable contact ACIs and approved group
ID remain private operator configuration outside this repository and LXC122.

Hal and Dani have equal operator authority. Ryan and the other consenting
friend have external authority. The allowlist admits DMs with those four people
and the single approved group. All eligible text in the approved group reaches Custos. Direct addresses by
name, mention or reply invite a response; other messages are ambient context,
with silence the default and occasional useful contributions allowed. Ambient
messages are native memories, not active tasks; deliberate deferred work promotes
them to goals. Unknown group members or pending invitations suspend group
intake/delivery. Display names and message text cannot grant authority.

## Runtime and recovery

Both `custos-signal.service` and `custos-signal-bridge.service` run on blink1 and
are enabled at boot. Signal runs as the nologin `custos-signal` user, exposing
only `/run/custos-signal/socket` in a private runtime directory. The bridge uses
fixed `pct exec 122` commands to native Headlong intake and reply receipts.
No Signal account keys or network RPC listener are exposed to the guest.

Use the official signal-cli 0.14.7 JVM build at
`/opt/custos-signal/jvm/bin/signal-cli`, with
`JAVA_HOME=/opt/custos-signal/java25`. The native build failed username operations
with a missing `JsonUtil$UuidDeserializer` constructor; the JVM build succeeded.
Verified archive SHA-256 values:

- signal-cli JVM: `0e1eefdf4a2109edf7c899c9d1667167c54ac12c3ec824f27db7c1dac4fa7506`
- Temurin Java 25.0.4.1+1: `1731a34baadec5479258ea0202e4d5d865d2efeee60cb0c7d7eb056fe96ca219`

Private files: `/etc/custos-signal/account.env`, `registration-pin`, and
`policy.json` (root0600, parent0700). Account config root is
`/var/lib/custos-signal/data`; account files are in its nested `data/` directory.
Do not re-register a healthy account. Use the running daemon's JSON-RPC API for
profile/contact changes; a second CLI process will contend for its account lock.

Stop the bridge and daemon before backing up account data and the private
configuration to `/var/backups/custos-signal` (root0700, archives0600), then
restart daemon and bridge. Preserve the existing registered backup. Do not
publish recovery archives, verification diagnostics, codes or CAPTCHA tokens.
Google Voice retention remains manual; Signal traffic does not count as Voice
usage. No automated Voice call/text job is installed.

## Delivery semantics and operational limits

`custos_signal.py` persists admitted text in a SQLite WAL spool before native
memory intake. Stable request IDs deduplicate replay after spooling; native
memory remains the only goals store. Replies are restricted to captured request
routes, paced at least 30 seconds apart per conversation, and record Signal
submission timestamps and per-recipient results. Submission is not a claim that
a human read the message. Any partial send or crash during sending becomes
`uncertain` and requires reconciliation; do not blindly repost it.

The guest operator-pause marker suspends intake into memory and outbound sends;
incoming allowed messages can still be spooled. Changing the host allowlist
revokes pending delivery. The daemon ignores attachments, avatars, stories and
stickers; ephemeral/view-once content is excluded from model intake. Message
edits are ignored. Remote deletion cancels matching queued spool work but does
not erase an already-captured native memory. The spool has no automatic aging.

Signal server acknowledgement precedes bridge SQLite commit, so a process crash
in that interval can lose an incoming event: this is not an end-to-end exactly
once guarantee. Keep restart/reconnect and phone round-trip verification separate
from unit tests. The integration suite covers routing, authority spoofing,
roster changes, deduplication, pause, deletion and uncertain/partial delivery.

Live qualification: direct messages and group messages both reached native memory
and received replies with successful Signal submission results on 2026-09-08.
The profile avatar chosen by Custos is installed; stopped-state backup/restart
recovered both services successfully.
