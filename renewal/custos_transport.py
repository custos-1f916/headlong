#!/usr/bin/env python3
"""Durable operator/phone transport into native Headlong messages and mem.

This is a transport receipt, not a goal store. Goals live only in native mem.
Call from an activated identity. Operator authority is assigned by the trusted
CLI/paired-phone bridge, never by quoted message text.
"""
import argparse
from custos_signal_targeting import validate as validate_signal_routing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from custos_images import MAX_UPLOAD, import_images, validate_refs
from custos_pdfs import extract_pdfs


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.transport-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def native(args, payload=None):
    proc = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=30)
    if proc.returncode:
        # Child diagnostics can contain model material; don't reflect it to peers.
        raise RuntimeError(args[0] + ' failed; original request remains queued')
    return proc.stdout


def paths(request_id):
    identity = Path(os.environ['IDENTITY_DIR'])
    state = identity / '.state' / 'transport'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(request_id.encode()).hexdigest()
    root = os.environ.get('ROOT_TRAJ_ID') or os.environ['TRAJ_ID']
    trajectory = Path(native(['traj', 'path', root]).strip())
    return state, state / (key + '.json'), trajectory, root


def contains_step(trajectory, step_id):
    with trajectory.open() as source:
        for line in source:
            if step_id not in line:
                continue
            try:
                if json.loads(line).get('step_id') == step_id:
                    return True
            except json.JSONDecodeError:
                continue
    return False


def send(args, content, images=None):
    if not content.strip() or len(content.encode()) > 32768:
        raise ValueError('message must contain 1..32768 UTF-8 bytes')
    if not args.request_id or len(args.request_id) > 512 or len(args.sender) > 160:
        raise ValueError('invalid transport identity')
    state, receipt, trajectory, root = paths(args.request_id)
    with (state / 'ingress.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = {'request_id': args.request_id, 'sender': args.sender,
                    'authority': args.authority, 'source_url': args.source_url, 'content': content}
        if getattr(args, 'ambient', False):
            original['ambient'] = True
        if getattr(args, 'allow_reaction', False):
            original['allow_reaction'] = True
        if getattr(args, 'signal_routing', None) is not None:
            original['signal_routing'] = validate_signal_routing(args.signal_routing)
        if images:
            original['images'] = validate_refs(images)
        if receipt.exists():
            record = json.loads(receipt.read_text())
            if record['original'] != original:
                raise ValueError('request ID already belongs to different content')
        else:
            record = {'original': original, 'step_id': str(uuid.uuid4()), 'phase': 'received',
                      'reply_offset': trajectory.stat().st_size}
            atomic_json(receipt, record)
        if record['phase'] != 'queued':
            captured = json.loads(native(['custos-memory', 'capture'], json.dumps(original)))
            record['goal_id'] = captured['goal_id']
            was_appending = record['phase'] == 'appending'
            record['phase'] = 'appending'
            atomic_json(receipt, record)
            if not was_appending or not contains_step(trajectory, record['step_id']):
                event = {'type': 'message', 'source': 'operator-transport',
                         'step_id': record['step_id'], 'from': args.sender,
                         'to': os.environ.get('IDENTITY_NAME', 'custos'), 'content': content,
                         'request_id': args.request_id, 'authority': args.authority,
                         'source_url': args.source_url}
                if original.get('ambient'):
                    event['ambient'] = True
                if original.get('allow_reaction'):
                    event['allow_reaction'] = True
                if 'signal_routing' in original:
                    event['signal_routing'] = original['signal_routing']
                if original.get('images'):
                    event['images'] = original['images']
                native(['traj', 'append', root], json.dumps(event))
                with trajectory.open('rb') as source:
                    os.fsync(source.fileno())
            record['phase'] = 'queued'
            atomic_json(receipt, record)
        return {'request_id': args.request_id, 'step_id': record['step_id'],
                'goal_id': record['goal_id'], 'queued': True}


def poll(args):
    _, receipt, trajectory, _ = paths(args.request_id)
    if not receipt.exists():
        return {'queued': False, 'replies': []}
    record = json.loads(receipt.read_text())
    replies = []
    with trajectory.open('rb') as source:
        source.seek(record['reply_offset'])
        for raw in source:
            if not raw.endswith(b'\n'):
                break
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if (event.get('type') == 'message' and event.get('from') == os.environ.get('IDENTITY_NAME', 'custos')
                    and event.get('reply_to') == record['step_id']):
                replies.append({'step_id': event['step_id'], 'content': event.get('content', '')})
    from custos_memory import Store
    store = Store()
    with store.lock():
        memory = store.find(record['goal_id'])[4]
    decision = (memory.get('response') or {}).get('state') if memory else None
    return {'queued': record['phase'] == 'queued', 'goal_id': record.get('goal_id'), 'replies': replies,
            'decision': decision, 'disposition': memory.get('status') if memory else 'unknown'}


def receipt_lookup(receipts, reply_to):
    """The inbound request a reply answers: its exact step id, else a unique prefix.

    The mind reads eight-character step ids in its stream and hands them to
    `chat reply --reply-to`; the bridge carries a reply only when this lookup
    names a delivered request. Two deliveries were lost on 2026-09-10/11 (the
    napa confirmation to Kim, the recall correction to Jack) because an exact
    match on a prefix returned nothing and the row was never created."""
    if not reply_to:
        return None
    if reply_to in receipts:
        return receipts[reply_to]
    if len(reply_to) < 4:
        return None
    matches = [request for step_id, request in receipts.items() if step_id.startswith(reply_to)]
    return matches[0] if len(matches) == 1 else None


def outbox(args):
    """Read complete outbound events incrementally from the current root."""
    state, _, trajectory, _ = paths('outbox')
    offset = args.offset if args.trajectory == str(trajectory) else 0
    if offset < 0 or offset > trajectory.stat().st_size:
        offset = 0
    events = []
    consumed = 0
    cursor = offset
    receipts = None
    with trajectory.open('rb') as source:
        source.seek(offset)
        while consumed < 4194304 and len(events) < 100:
            raw = source.readline()
            if not raw or not raw.endswith(b'\n'):
                break
            consumed += len(raw)
            cursor = source.tell()
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not (event.get('type') == 'message' and event.get('from') == 'custos' and event.get('to') == args.sender):
                continue
            if event.get('delivered_by') == 'custos-actions':
                # Recorded by custos-actions for the conversation ledger; that
                # service delivered (or is delivering) it. Sending it again here
                # would double-post.
                continue
            if receipts is None:
                receipts = {}
                for receipt in state.glob('*.json'):
                    record = json.loads(receipt.read_text())
                    receipts[record['step_id']] = record['original']['request_id']
            events.append({'step_id': event['step_id'], 'content': event.get('content', ''),
                           'request_id': receipt_lookup(receipts, event.get('reply_to')), 'ts': event.get('ts'),
                           **({'reaction': event['reaction']} if 'reaction' in event else {})})
    return {'trajectory': str(trajectory), 'offset': cursor, 'events': events}


def reconcile():
    """Retry interrupted transport commits, without any model invocation."""
    state, _, _, _ = paths('reconcile')
    retried, failed = [], []
    for receipt in sorted(state.glob('*.json')):
        record = json.loads(receipt.read_text())
        if record['phase'] == 'queued':
            continue
        original = record['original']
        args = argparse.Namespace(**{key: original[key] for key in
                                  ('request_id', 'sender', 'authority', 'source_url')})
        args.signal_routing = original.get('signal_routing')
        args.ambient = original.get('ambient', False)
        args.allow_reaction = original.get('allow_reaction', False)
        try:
            send(args, original['content'], original.get('images'))
            retried.append(original['request_id'])
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
            failed.append(original['request_id'])
        if len(retried) + len(failed) >= 16:
            break
    return {'retried': retried, 'still_pending': failed}


def unfold_media(media):
    """Validate a --media envelope; fold bounded extracted PDF text after content."""
    if (not isinstance(media,dict) or not isinstance(media.get('content'),str) or
            set(media) not in ({'content','images'},{'content','images','pdfs'})):
        raise ValueError('invalid image transfer')
    content=media['content']
    if media.get('pdfs'):
        blocks,notes=extract_pdfs(media['pdfs'])
        if blocks or notes:
            content+='\n'+'\n'.join(blocks+notes)
    images=media['images']
    refs,errors=import_images(images) if images else ([],[])
    return content,refs,errors


def fit_content(content,limit=32768):
    """Keep the captured message inside the transport byte budget, honestly."""
    marker='\n[... PDF text truncated]'
    if len(content.encode())<=limit:
        return content
    data=content.encode()
    cut=limit-len(marker.encode())
    while cut>0 and (data[cut]&0xC0)==0x80:
        cut-=1
    return data[:cut].decode('utf-8')+marker


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    put = commands.add_parser('send')
    put.add_argument('--sender', default='hal')
    put.add_argument('--authority', choices=['operator', 'agent', 'external'], default='operator')
    put.add_argument('--request-id', required=True)
    put.add_argument('--source-url', default='')
    put.add_argument('--ambient', action='store_true', help='Observed conversation, not a directed task')
    put.add_argument('--allow-reaction', action='store_true', help='Host can deliver an emoji reaction to this message')
    put.add_argument('--signal-routing', type=json.loads, help='Verified per-message addressing from the host bridge')
    put.add_argument('--media', action='store_true', help='Read text and bounded base64 images from stdin JSON')
    get = commands.add_parser('poll')
    get.add_argument('--request-id', required=True)
    outgoing = commands.add_parser('outbox')
    outgoing.add_argument('--sender', default='phone')
    outgoing.add_argument('--offset', type=int, default=0)
    outgoing.add_argument('--trajectory', default='')
    commands.add_parser('reconcile')
    args = parser.parse_args()
    try:
        if args.command == 'send':
            if args.media:
                raw=sys.stdin.read(MAX_UPLOAD+1)
                if len(raw)>MAX_UPLOAD: raise ValueError('image transfer too large')
                media=json.loads(raw)
                content,refs,errors=unfold_media(media)
                result=send(args, fit_content(content + ('\n'+'\n'.join(errors) if errors else '')), refs)
            else:
                result = send(args, sys.stdin.read(32769))
        elif args.command == 'outbox':
            result = outbox(args)
        elif args.command == 'reconcile':
            result = reconcile()
        else:
            result = poll(args)
        print(json.dumps(result, ensure_ascii=False))
    except (KeyError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('custos-transport: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
