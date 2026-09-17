"""Host-owned group bot streaks across native and proactive Signal sends.

Sender classes come from verified host policy, never message text. Read-only
preflight is advisory; a durable send-time reservation is authoritative.
"""
import json
from pathlib import Path
import sqlite3
import time

WRAP, EMOJI, DIGEST = 4, 5, 6
GAP = 7200
SCHEMA = '''CREATE TABLE IF NOT EXISTS group_bot_claims (
    send_id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
    created REAL NOT NULL, decision TEXT NOT NULL)'''


def human(policy, sender):
    person = policy['people'].get(sender)
    return person is not None and not person.get('bot', False)


def human_request(db, policy, conversation, request_id):
    if not request_id:
        return False
    row = db.execute('SELECT payload FROM inbox WHERE id=?', (request_id,)).fetchone()
    if not row:
        return False
    item = json.loads(row['payload'])
    return (item['conversation'] == conversation and not item.get('reaction')
            and human(policy, item['sender_aci']))


def mode(turns):
    return 'digest' if turns >= DIGEST else 'emoji_only' if turns >= EMOJI else 'wrap' if turns >= WRAP else None


def state(db, policy, conversation, actions_state=None, actions_db=None, now=None):
    """Count Custos turns in the current consecutive bot-only group segment.

    A turn is one outgoing Custos message/reaction. Bot message
    bursts do not spend extra Custos turns. Pure human/bot reactions do not
    reopen a conversation. Queued, blocked, deleted and failed sends do not count;
    sending/uncertain reservations remain spent across restarts.
    """
    if not conversation.startswith('group:'):
        return {'turns': 0, 'mode': None, 'active': False}
    at = time.time() if now is None else now
    events, sends = [], {}
    for row in db.execute("SELECT id,payload FROM inbox WHERE json_extract(payload,'$.conversation')=?", (conversation,)):
        item = json.loads(row['payload'])
        if item.get('reaction') or item['sender_aci'] not in policy['people']:
            continue
        events.append((item['timestamp'] / 1000, 'human' if human(policy,item['sender_aci']) else 'bot', row['id']))
    for row in db.execute("SELECT o.id,o.created,o.receipt FROM outbox o JOIN inbox i ON i.id=o.request_id "
                          "WHERE json_extract(i.payload,'$.conversation')=? "
                          "AND o.phase IN ('sending','submitted','uncertain')", (conversation,)):
        receipt = json.loads(row['receipt'] or '{}')
        stamp = receipt.get('timestamp')
        sends['outbox:'+row['id']] = stamp / 1000 if isinstance(stamp,(int,float)) else row['created']
    owned = None
    if actions_db is None and actions_state and Path(actions_state).exists():
        owned = sqlite3.connect('file:'+str(actions_state)+'?mode=ro',uri=True)
        owned.row_factory = sqlite3.Row
        actions_db = owned
    try:
        if actions_db is not None:
            for row in actions_db.execute("SELECT id,created,receipt FROM actions WHERE json_extract(payload,'$.target')=? "
                                          "AND json_extract(payload,'$.action') IN ('signal-send','signal-ask') "
                                          "AND phase IN ('sending','submitted','uncertain')", (conversation,)):
                receipt=json.loads(row['receipt'] or '{}');stamp=receipt.get('timestamp')
                sends['action:'+row['id']] = stamp / 1000 if isinstance(stamp,(int,float)) else row['created']
    finally:
        if owned is not None:
            owned.close()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_bot_claims'").fetchone():
        # A claim's send-time clock overrides enqueue time; uncertain calls and
        # a crash before phase update cannot refill the streak's budget.
        for row in db.execute('SELECT send_id,created FROM group_bot_claims WHERE conversation=?',(conversation,)):
            sends[row['send_id']] = row['created']
    events.extend((stamp,'custos',sid) for sid,stamp in sends.items())
    events.sort(key=lambda event:(event[0],event[1],event[2]), reverse=True)
    turns, saw_bot, newer = 0, False, None
    for stamp,kind,_ in events:
        if newer is None and at-stamp > GAP:
            break
        if newer is not None and newer-stamp > GAP:
            break
        newer=stamp
        if kind=='human':
            break
        if kind=='bot':
            saw_bot=True
        elif kind=='custos':
            turns+=1
    return {'turns': turns if saw_bot else 0, 'mode':mode(turns) if saw_bot else None, 'active':saw_bot}


def claim(db, policy, conversation, send_id, request_id=None, actions_state=None, actions_db=None,
          can_react=False, now=None):
    """Reserve at most five texts plus one closing reaction; never send here.

    This is shared across every thinker/path. A proactive write has no safe
    incoming reaction target, so it is blocked once text is closed. Correlated
    work requested by a verified human remains deliverable through normal claims.
    """
    if not conversation.startswith('group:'):
        return None, None
    with db:
        db.execute('BEGIN IMMEDIATE')
        if human_request(db,policy,conversation,request_id):
            return None,None
        if db.execute('SELECT 1 FROM group_bot_claims WHERE send_id=?',(send_id,)).fetchone():
            return None,'bot-only group send already claimed; reconcile the original request'
        current=state(db,policy,conversation,actions_state,actions_db,now)
        if not current['active']:
            return None,None
        if current['mode']=='digest':
            return None,'bot-only group conversation closed; wait for a human message or a quiet interval'
        if current['mode']=='emoji_only' and not can_react:
            return None,'bot-only group text closed; no proactive continuation'
        decision='reaction' if current['mode']=='emoji_only' else 'text'
        db.execute('INSERT INTO group_bot_claims VALUES(?,?,?,?)',
                   (send_id,conversation,time.time() if now is None else now,decision))
        return decision,None
