#!/usr/bin/env python3
"""Host-owned proactive Signal queue and fixed Automata deployment channel.

Only LXC122 is admitted. No caller-controlled command, container, path or socket.
Signal sending stays in its single existing worker; deployments run serially.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import selectors
from custos_signal import load_policy, encoded, MAX_TEXT

STATE = '/var/lib/custos-actions/actions.sqlite'
POLICY = '/etc/custos-signal/policy.json'
CHECKOUT = '/opt/custos/work/repos/collettiquette/automata'
MAX_BODY = 32768


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path=STATE):
    db = sqlite3.connect(path, timeout=15, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
               "phase TEXT NOT NULL DEFAULT 'queued', receipt TEXT, created REAL NOT NULL)")
    return db


def destinations(policy):
    return ([{'target': 'dm:' + who, 'label': p['label'], 'kind': 'dm'}
             for who, p in policy['people'].items()] +
            [{'target': 'group:' + g, 'label': policy.get('group_labels', {}).get(g, 'Group'), 'kind': 'group'}
             for g in policy['groups']])


def resolve(target, policy):
    if not isinstance(target, str):
        raise ValueError('target must be a current contact label or target from signal-contacts')
    matches = [d for d in destinations(policy)
               if target == d['target'] or target.casefold() == d['label'].casefold()]
    if len(matches) != 1:
        raise ValueError('target is not uniquely allowlisted; use signal-contacts')
    return matches[0]['target']


def validate(payload, policy):
    if not isinstance(payload, dict):
        raise ValueError('object required')
    action = payload.get('action')
    fields = {'signal-send': {'target', 'message'}, 'automata-deploy': {'commit', 'goal_id'},
              'automata-rollback': {'goal_id'}}
    if action not in fields or set(payload) != fields[action] | {'action', 'request_id'}:
        raise ValueError('invalid action fields')
    if not isinstance(payload['request_id'], str) or not re.fullmatch(r'[A-Za-z0-9:._-]{1,128}', payload['request_id']):
        raise ValueError('stable request_id required')
    p = dict(payload)
    if action == 'signal-send':
        p['target'] = resolve(p['target'], policy)
        if not isinstance(p['message'], str) or not p['message'].strip() or len(p['message'].encode()) > MAX_TEXT:
            raise ValueError('message must contain 1..12000 UTF-8 bytes')
    else:
        if not isinstance(p['goal_id'], str) or not re.fullmatch(r'[0-9a-f]{8}', p['goal_id']):
            raise ValueError('native goal_id required')
        if action == 'automata-deploy' and (not isinstance(p['commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', p['commit'])):
            raise ValueError('full published Git commit required')
    return p


def receipt(row):
    return {'ok': True, 'request_id': row['id'], 'phase': row['phase'],
            'receipt': json.loads(row['receipt']) if row['receipt'] else None}


class Channel:
    def __init__(self, state=STATE, policy=POLICY):
        self.state, self.policy = state, policy
        connect(state).close()

    def handle(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('object required')
        if payload == {'action': 'signal-contacts'}:
            return {'ok': True, 'contacts': destinations(load_policy(self.policy))}
        if payload.get('action') == 'status' and set(payload) == {'action', 'request_id'}:
            if not isinstance(payload['request_id'], str): raise ValueError('request_id required')
            with connect(self.state) as db:
                row = db.execute('SELECT * FROM actions WHERE id=?', (payload['request_id'],)).fetchone()
            return receipt(row) if row else {'ok': False, 'error': 'unknown_request'}
        if payload == {'action': 'automata-status'}:
            return {'ok': True, 'automata': json.loads(run_bounded(
                ['/usr/sbin/pct','exec','126','--','/usr/bin/python3',
                 '/usr/local/libexec/custos-automata-deploy.py','status'], timeout=25).decode())}
        # Resolve labels once; store the fixed routing target, not a mutable alias.
        p = validate(payload, load_policy(self.policy))
        with connect(self.state) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM actions WHERE id=?', (p['request_id'],)).fetchone()
            if old:
                if old['payload'] != encoded(p): raise ValueError('request_id belongs to different content')
                return receipt(old)
            db.execute('INSERT INTO actions(id,payload,created) VALUES(?,?,?)',
                       (p['request_id'], encoded(p), time.time()))
            row = db.execute('SELECT * FROM actions WHERE id=?', (p['request_id'],)).fetchone()
        return receipt(row)


def run_bounded(args, timeout=90, maximum=1048576):
    """Bound host subprocess output/time; never execute shell or guest code here."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    parts, size = [], 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline: raise TimeoutError('command timeout')
                for key, _ in selector.select(min(1, max(0, deadline-time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    size += len(chunk)
                    if size > maximum: raise ValueError('command output limit')
                    parts.append(chunk)
        if proc.wait(timeout=max(0.1, deadline-time.monotonic())):
            raise RuntimeError('fixed command failed')
        return b''.join(parts)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait()
        proc.stdout.close()


def deploy(payload):
    prefix = ['/usr/sbin/pct','exec','126','--','/usr/bin/python3','/usr/local/libexec/custos-automata-deploy.py']
    if payload['action'] == 'automata-rollback':
        return json.loads(run_bounded(prefix+['rollback'], timeout=120))
    commit = payload['commit']
    git = ['/usr/sbin/pct','exec','122','--','/usr/bin/git','-C',CHECKOUT]
    # Fixed GitHub source. A published branch must contain the requested commit.
    run_bounded(git+['fetch','--prune','https://github.com/collettiquette/automata.git',
                     '+refs/heads/*:refs/remotes/custos-deploy/*'])
    branches = run_bounded(git+['for-each-ref','--contains',commit,'--format=%(refname)',
                               'refs/remotes/custos-deploy/'])
    if not branches.strip(): raise ValueError('commit is not on a published Automata branch')
    archive = run_bounded(git+['archive','--format=tar',commit+':particle-life'], maximum=32*1024*1024)
    # Archive bytes are opaque on the host; extraction/code execution is in 126.
    with tempfile.TemporaryDirectory(prefix='automata-', dir='/var/lib/custos-actions') as tmp:
        path = Path(tmp)/'source.tar'; path.write_bytes(archive)
        run_bounded(['/usr/sbin/pct','push','126',str(path),'/var/lib/custos-automata-incoming/source.tar'])
        return json.loads(run_bounded(prefix+['deploy',commit,hashlib.sha256(archive).hexdigest()], timeout=240))


def worker(state=STATE):
    from custos_signal import paused
    with connect(state) as db:
        db.execute("UPDATE actions SET phase='uncertain' WHERE phase='running'")
    while True:
        try:
            if paused(): time.sleep(2); continue
            with connect(state) as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM actions WHERE phase='queued' AND "
                                 "json_extract(payload,'$.action') IN ('automata-deploy','automata-rollback') "
                                 'ORDER BY created LIMIT 1').fetchone()
                if row: db.execute("UPDATE actions SET phase='running' WHERE id=?", (row['id'],))
            if row:
                try:
                    result = deploy(json.loads(row['payload']))
                    phase = 'succeeded' if result.get('ok') else 'failed'
                except Exception as error:
                    # Could have switched before losing the response; never retry automatically.
                    result, phase = {'error': type(error).__name__, 'reconcile': 'Use automata-status before a new deployment'}, 'uncertain'
                with connect(state) as db:
                    db.execute('UPDATE actions SET phase=?,receipt=? WHERE id=?', (phase,encoded(result),row['id']))
        except Exception:
            print('custos-actions: deployment worker unavailable', flush=True)
        time.sleep(2)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(10)
    def log_message(self, *args): pass
    def do_POST(self):
        code = 200
        try:
            if self.client_address[0] != '192.168.86.52':
                code=403; raise ValueError('client not allowed')
            if self.path not in {'/v1/actions','/v1/harness'}:
                code=404; raise ValueError('unknown path')
            lengths=self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths)!=1 or
                not re.fullmatch(r'[0-9]{1,6}',lengths[0]) or not 0<int(lengths[0])<=MAX_BODY or
                self.headers.get_content_type()!='application/json'):
                raise ValueError('bounded JSON body required')
            raw=self.rfile.read(int(lengths[0]))
            if len(raw)!=int(lengths[0]): raise ValueError('incomplete body')
            if self.path=='/v1/harness':
                import socket
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as upstream:
                    upstream.settimeout(12);upstream.connect('/run/custos-harness.sock')
                    upstream.sendall(raw+b'\n')
                    with upstream.makefile('rb') as response:
                        line=response.readline(1024*1024+1)
                    if len(line)>1024*1024 or not line.endswith(b'\n'):raise ValueError('invalid supervisor response')
                    result=json.loads(line)
            else:
                result=self.server.channel.handle(json.loads(raw))
        except (ValueError, UnicodeError, RecursionError) as error:
            code=400 if code==200 else code; result={'ok':False,'error':str(error)[:200]}
        except Exception:
            code=503; result={'ok':False,'error':'unavailable; reuse original request_id'}
        body=encoded(result).encode()
        try:
            self.send_response(code); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body))); self.send_header('Connection','close')
            self.end_headers(); self.wfile.write(body)
        except OSError: pass
        self.close_connection=True


def main():
    os.umask(0o077)
    channel=Channel()
    threading.Thread(target=worker,daemon=True).start()
    with ThreadingHTTPServer(('192.168.86.44',18082),Handler) as server:
        server.channel=channel; server.serve_forever()

if __name__=='__main__': main()
