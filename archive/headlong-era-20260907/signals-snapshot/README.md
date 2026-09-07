# signals-snapshot — durability copies of live signal scripts

These are reference copies of the live scripts under /opt/custos/signals/
(which is NOT under version control). The live path is always
/opt/custos/signals/*.py — do not edit these copies. They exist so a
machine reset or signals-dir wipe does not lose bug fixes.

Refresh after any live change:
  cp -p /opt/custos/signals/beads_watch.py /opt/custos/signals/watcher_common.py /opt/custos/repo/signals-snapshot/
  git -C /opt/custos/repo add signals-snapshot && git -C /opt/custos/repo commit -m "refresh signals-snapshot"

Snapshotted 2026-08-30 19:00Z after the RE_SKIP word-boundary fix +
drop-logging observability (see 2026-08-30-beads-watch-playtest-skip-bug.md).
