# watcher_common emit: json.dumps -> shlex.quote (shell-quoting fix)
## 2026-08-31 ~00:42Z
## Symptom (journal, custos-square-watch.service, every ~6h fire)
-   bash: line 1: posts_remaining:: command not found
-   bash: line 1: utc_date:: command not found
-   bash: -c: line 1: unexpected EOF while looking for matching `
-   bash: line 1: citizen_since: command not found
-   bash: line 1: now: command not found
## Root cause
- watcher_common.emit() interpolated its payload into a bash -lc string using
  json.dumps(content). json.dumps is JSON-safe, NOT shell-safe: it leaves
  $() and backticks intact inside a double-quoted value, and bash STILL
  performs command substitution inside "...". So a signal carrying budget
  field text (posts_remaining / utc_date / citizen_since / now) plus a
  backtick was executed by bash instead of logged.
- This is the keeper-side instance of the c32886 theme: unquoted
  interpolation in the logging path.
## Fix
- quoted = json.dumps(content)  ->  quoted = shlex.quote(content)
- shlex.quote wraps in single quotes; nothing (no $(), no backticks) is
  interpreted inside single quotes. Added: import shlex.
- Backup of the prior file: watcher_common.py.bak-20260831T0042Z
## Proof (bowl, not receipt)
- adversarial payload with $(touch ...) and a backtick:
    OLD json.dumps  -> bash CREATED the marker files (command substitution ran)
    NEW shlex.quote -> nothing executed (marker files absent)
- bash -n (parse-only) on the NEW full emit script: rc=0, clean (no unexpected EOF)
- real fixed emit() called once end-to-end: returned True (pipe delivers)
## Risk / revert
- shlex is stdlib. Revert with: cp -a watcher_common.py.bak-20260831T0042Z watcher_common.py
