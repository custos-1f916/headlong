"""Shared square pacing, draft provenance and decaying attention. No network/model calls.

Admission is checked inside the outbound reservation transaction. Rolling windows
do not refill at UTC midnight. State additions are compatible with code rollback.
"""
import hashlib
import json
import math
import time

WINDOW = 6 * 3600
BURST_WINDOW = 30 * 60
COMMENT_LIMIT = 5
DISCRETIONARY_LIMIT = 3
RESERVE = 8
SIGNAL_LIMIT = 2
READ_LIMIT = 12
THREAD_COOLDOWN = 24 * 3600
SHORTLIST = 3


def body_key(body):
    # Moving files, changing parents/request IDs or whitespace does not reset age.
    return 'square:candidate:' + hashlib.sha256(' '.join(body.split()).encode()).hexdigest()


def remember_candidate(store, body, composed_at=None, now=None):
    now = time.time() if now is None else now
    proposed = now if composed_at is None else float(composed_at)
    if not math.isfinite(proposed) or proposed <= 0 or proposed > now + 60:
        raise ValueError('invalid composition timestamp')
    key = body_key(body)
    # Atomic MIN; a copied old candidate can only become older, never younger.
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        old = store.get(key)
        first = min(proposed, now, old['first_seen'] if old else now)
        store.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                         (key, json.dumps({'first_seen': first})))
    return first


def directed(store, payload, now):
    target = '%s:%s' % (payload['post_id'], payload.get('parent_id', 0))
    record = store.get('square:directed:' + target)
    return bool(record and now - record['at'] < WINDOW)


def recent_writes(store, now):
    result = []
    for row in store.db.execute("SELECT id,payload,started FROM outbound WHERE verb='comment' AND status!='rejected' AND started>?", (now - WINDOW,)):
        # Unknown/uncertain reservations consume local capacity conservatively.
        priority = store.get('square:priority:' + row[0], False)
        result.append((row[2], priority))
    return result


def admission(store, payload, remaining, now, daily_comments=20):
    """Return eligibility and exact next check time; caller holds reservation lock."""
    priority = directed(store, payload, now)
    # Concurrent callers can both have fetched the same live remaining count.
    # Include reservations already committed by the other caller before spending
    # the final discretionary slot. Uncertain writes remain reservations.
    reserved_today = store.db.execute("SELECT count(*) FROM outbound WHERE verb='comment' AND status!='rejected' AND started>=?",
                                     ((int(now) // 86400) * 86400,)).fetchone()[0]
    remaining = min(remaining, max(0, daily_comments - reserved_today))
    writes = recent_writes(store, now)
    waits = []
    if remaining < 1:
        waits.append((int(now) // 86400 + 1) * 86400)
    for rows, limit, window in [([t for t, _ in writes if t > now - BURST_WINDOW], 2, BURST_WINDOW),
                                ([t for t, _ in writes], COMMENT_LIMIT, WINDOW)]:
        if len(rows) >= limit:
            waits.append(sorted(rows)[-limit] + window)
    if not priority:
        discretionary = sorted(t for t, p in writes if not p)
        if len(discretionary) >= DISCRETIONARY_LIMIT:
            waits.append(discretionary[-DISCRETIONARY_LIMIT] + WINDOW)
        if remaining <= RESERVE:
            waits.append((int(now) // 86400 + 1) * 86400)
    return {'allowed': not waits, 'directed': priority,
            'next_at': max(waits, default=now), 'recent_comments': len(writes)}


def attention(store, now=None):
    now = time.time() if now is None else now
    state = store.get('square:attention', {})
    signals = [t for t in state.get('signals', []) if t > now - WINDOW]
    reads = [t for t in state.get('reads', []) if t > now - WINDOW]
    writes = recent_writes(store, now)
    discretionary = [t for t, priority in writes if not priority]
    # A continuous signal tells the chooser why this source is losing salience.
    pressure = min(1.0, max(len(signals) / SIGNAL_LIMIT, len(reads) / READ_LIMIT,
                            len(discretionary) / DISCRETIONARY_LIMIT))
    cached = store.get('square:allowance', {})
    today = cached.get('today', {})
    reset = (today.get('interval') or {}).get('until', 0) / 1000
    exhausted = today.get('comments_remaining', 20) == 0 and (not reset or reset > now)
    if exhausted:
        pressure = 1.0
    waits = []
    for rows, limit in [(signals, SIGNAL_LIMIT), (reads, READ_LIMIT),
                         (discretionary, DISCRETIONARY_LIMIT)]:
        if len(rows) >= limit:
            waits.append(sorted(rows)[-limit] + WINDOW)
    if exhausted:
        waits.append(reset or (int(now) // 86400 + 1) * 86400)
    return {'pressure': round(pressure, 2), 'satiated': pressure >= 1,
            'signals_6h': len(signals), 'reads_6h': len(reads),
            'comments_6h': len(writes), 'discretionary_comments_6h': len(discretionary),
            'revisit_after': max(waits, default=now),
            'signal_limit': SIGNAL_LIMIT, 'read_attention_limit': READ_LIMIT,
            'comment_limit_6h': COMMENT_LIMIT, 'discretionary_limit_6h': DISCRETIONARY_LIMIT,
            'burst_limit_30m': 2, 'reserved_comments': RESERVE}


def record_attention(store, kind, now=None, thread=None):
    now = time.time() if now is None else now
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        state = store.get('square:attention', {})
        for key in ('signals', 'reads'):
            state[key] = [t for t in state.get(key, []) if t > now - WINDOW]
        # Cap telemetry storage even if someone repeatedly reads manually.
        state[kind] = (state[kind] + [now])[-100:]
        if thread is not None:
            store.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                             ('square:thread:' + str(thread), json.dumps(now)))
        store.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                         ('square:attention', json.dumps(state)))


def select_discovery(store, thread, now):
    if attention(store, now)['satiated']:
        return False
    last = store.get('square:thread:' + str(thread))
    return last is None or now - last >= THREAD_COOLDOWN


def policy_summary(store, now=None):
    now = time.time() if now is None else now
    state = attention(store, now)
    row = store.db.execute("SELECT MAX(started) FROM outbound WHERE verb='post' AND status='delivered'").fetchone()
    state['last_confirmed_post'] = row[0] if row else None
    state['post_nudge_due'] = state['last_confirmed_post'] is None or now - state['last_confirmed_post'] >= 86400
    return state


def hint(state):
    tone = 'Satiated: let the square recede.' if state['satiated'] else 'Square attention is optional and diminishing.'
    return ('%s In the rolling six hours: %d selected discoveries, %d explicit reads, %d comments. '
            'Repetitions and another thread on the same distinction are weak reasons to continue. '
            'Choose a different interest or idle when nothing is compelling; no make/explore quota. '
            'Fresh directed asks remain separate. Unused capacity is not work owed. '
            'Do not stage tomorrow\'s comments or wait/poll for reset. '
            'A substantial new artifact can merit an original post, whose allowance is separate.'
            % (tone, state['signals_6h'], state['reads_6h'], state['comments_6h']))
