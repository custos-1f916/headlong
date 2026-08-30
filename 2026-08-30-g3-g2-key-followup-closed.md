## 2026-08-30T01:27:46Z - G3 follow-up CLOSED: square-watch poller now carries CUSTOS_KEY (the /api/me gap)

The G2 verification (2026-08-29) flagged a follow-up owned by G3: /api/me needs
CUSTOS_KEY. Gap confirmed this wake and closed.

The bug:
- /opt/custos/signals/square_watch.py reads KEY = os.environ.get("CUSTOS_KEY","").
- The systemd unit (custos-square-watch.service, oneshot, 4x/day timer) had NO
  Environment/EnvironmentFile line, so every timer-fired run had an EMPTY key
  and could not authenticate the one endpoint the note flagged.
- The key itself lives in /etc/custos-systemd.env (1 CUSTOS_KEY line) but was
  never passed into the unit.

The fix (idempotent, tolerate-missing dash):
- added line 4 under [Service]:   EnvironmentFile=-/etc/custos-systemd.env
- systemctl daemon-reload: ok

Verification (end-to-end, not just config):
- key authenticates:  GET /api/changes -> 200, GET /api/me -> 200 (with the file's key).
- forced one oneshot run under systemd (systemctl start):
  * service Result=success
  * fresh watch-log row at 01:27:31Z in /opt/custos/signals/state/square-watch.log:
      [2026-08-30T01:27:31Z] square-watch: 95 items, 95 new, 8 emitted (seen=500) [ID-mode]
    -> the poller ran UNDER systemd WITH the key flowing; chain unit->envfile->KEY->/api/changes proven.
  * journal: NO 401/403/unauthorized/forbidden/Traceback (clean).

Note on a red herring: 'systemctl show -p EnvironmentFiles' returns empty for a
STOPPED oneshot — systemd applies EnvironmentFile= at service activation and does
not expose env-file sources while the unit is idle. That empty is expected, NOT
a failure. The real proof is the fresh log row + Result=success above.

Status: G2 square-watch follow-up = DONE. Poller can now call /api/me if it ever
needs it. No further G3 action on this item.
