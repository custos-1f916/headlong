#!/usr/bin/env python3
"""Custos's proactive Signal and Automata commands. Writes read JSON on stdin.

custos-actions signal-contacts
custos-actions signal-send < {request_id,target,message}.json
custos-actions automata-status
custos-actions automata-deploy < {request_id,goal_id,commit}.json
custos-actions automata-rollback < {request_id,goal_id}.json
custos-actions status REQUEST_ID
(inline asks: see bin/ask-agent — signal-ask / signal-await / signal-release)
Queue acceptance is not delivery. Reuse the exact request ID/payload on timeout.

signal-send goes through the same conversation guard as `chat` (when the caller
sets CHAT_DOUBLE_TEXT_GUARD_HOURS / CHAT_SOCIAL_BLOCKED, as the social thinker
does) and records the accepted message as a `message` step in the root
trajectory, marked delivered_by=custos-actions so the bridge does not send it
twice. On 2026-09-09 a send that `chat` had refused went out through here
unrecorded.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import custos_attachments as attachments

HOST, PORT = '192.168.86.44', 18082


def call(payload):
    connection = http.client.HTTPConnection(HOST, PORT, timeout=40)
    try:
        connection.request('POST', '/v1/actions', body=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        response = connection.getresponse(); raw = response.read(1048577)
        if len(raw) > 1048576: raise ValueError('response limit')
        return response.status, json.loads(raw)
    finally:
        connection.close()


def explain_status(result):
    """Add scheduling semantics without upgrading the host's delivery evidence."""
    if not isinstance(result, dict) or 'phase' not in result:
        return result
    phase = result['phase']
    states = {
        'queued': (True, False, 'Host retained the action; queue acceptance is not delivery.',
                   'Wait for a receipt or relevant transport event; do useful other work.'),
        'running': (False, False, 'Action is in progress; acceptance is not established.',
                    'Reconcile this request ID; do not duplicate an in-flight action.'),
        'blocked': (False, True, 'Terminal blocked action. It is not queued and will not automatically retry.',
                    'The reason in receipt must be resolved and eligibility independently re-established. '
                    'A policy change or new eligible inbound event may justify reconsideration; this row stays blocked. '
                    'Do not poll unchanged state, invent a new ID to bypass the guard, or promise automatic delivery.'),
        'uncertain': (False, True, 'Acceptance is unknown; automatic retry risks a duplicate.',
                      'Reconcile the original request and transport receipt before any new send.'),
        'submitted': (False, True, 'Transport submission recorded; inspect receipt for timestamp and per-recipient SUCCESS. Not a read receipt.',
                      'No retry; retain the actual receipt as evidence.'),
        'succeeded': (False, True, 'Action reports success; inspect its receipt for the actual result.', 'No retry.'),
        'failed': (False, True, 'Action failed and is not queued.', 'Resolve the receipt error before reconsideration.'),
    }
    automatic, terminal, meaning, resume = states.get(phase, (False, False, 'Unknown phase; no delivery or retry claim is justified.', 'Inspect the original receipt.'))
    return {**result, 'delivery_state': {'automatic_retry': automatic, 'terminal': terminal,
                                       'meaning': meaning, 'resume_condition': resume}}


def resolve_target(target, contacts):
    """A `dm:`/`group:` target as is; otherwise a unique label from signal-contacts."""
    if not isinstance(target, str):
        raise ValueError('target must be a string')
    if target.startswith(('dm:', 'group:')):
        return target
    matches = [c for c in contacts if isinstance(c, dict) and str(c.get('label', '')).casefold() == target.casefold()]
    if len(matches) != 1:
        raise ValueError('target is not uniquely allowlisted; use signal-contacts')
    return matches[0]['target']


def route_for(target):
    """The trajectory routing key the bridge uses for this conversation."""
    return 'signal-' + hashlib.sha256(target.encode()).hexdigest()[:24]


def guard(route):
    """Refuse what `chat` would refuse: the day's allowance, or a double text."""
    blocked = os.environ.get('CHAT_SOCIAL_BLOCKED')
    if blocked:
        raise ValueError(blocked)
    hours = os.environ.get('CHAT_DOUBLE_TEXT_GUARD_HOURS', '')
    if hours.isdigit() and int(hours) > 0:
        result = subprocess.run(['chat', 'guard', route], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout).strip().replace('chat: error: ', '') or 'refused by the conversation guard')


def record(route, message, request_id, phase, files=None, reply_to=None):
    """Append the accepted send to the root trajectory as a message step."""
    root = os.environ.get('ROOT_TRAJ_ID') or os.environ.get('TRAJ_ID')
    if not root:
        return
    step = {'type': 'message', 'from': os.environ.get('IDENTITY_NAME', 'custos'), 'to': route, 'content': message,
            'source': 'custos-actions', 'delivered_by': 'custos-actions', 'request_id': request_id, 'phase': phase}
    if files:
        step['attachments'] = attachments.metadata(attachments.validate(files))
    if reply_to:
        step['reply_to'] = reply_to
    subprocess.run(['traj', 'append', root], input=json.dumps(step, ensure_ascii=False), text=True, capture_output=True, timeout=30)


def resolve_reply(step, route):
    if len(step) < 4:
        raise ValueError('reply-to needs a unique native step ID or prefix')
    matches = []
    for path in (Path(os.environ['IDENTITY_DIR']) / '.state' / 'transport').glob('*.json'):
        record = json.loads(path.read_text())
        if record['step_id'].startswith(step):
            matches.append(record)
    if len(matches) != 1 or matches[0]['phase'] != 'queued' or matches[0]['original']['sender'] != route:
        raise ValueError('reply-to must uniquely name a delivered message in this conversation')
    return matches[0]['original']['request_id'], matches[0]['step_id']


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=['signal-contacts', 'signal-send', 'signal-ask', 'signal-await', 'signal-release', 'automata-status', 'automata-deploy', 'automata-rollback', 'status'])
    p.add_argument('request_id', nargs='?')
    p.add_argument('--attach', action='append', default=[], metavar='FILE', help='attach a guest file (repeat up to four; 8 MiB total)')
    p.add_argument('--reply-to', metavar='STEP', help='quote a delivered native Signal message (unique step ID/prefix)')
    args = p.parse_args()
    try:
        if (args.attach or args.reply_to) and args.action != 'signal-send':
            raise ValueError('--attach and --reply-to are for signal-send')
        if args.action in ('signal-send', 'signal-ask', 'automata-deploy', 'automata-rollback'):
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768: raise ValueError('request too large')
            payload = json.loads(raw)
            if not isinstance(payload, dict) or 'action' in payload: raise ValueError('object without action required')
        else:
            payload = {}
        if args.action in ('status', 'signal-await', 'signal-release'):
            if not args.request_id: raise ValueError('request ID required')
            payload['request_id'] = args.request_id
        elif args.request_id:
            raise ValueError('unexpected request ID argument')
        route = None
        reply_step = None
        if args.action == 'signal-send':
            if not isinstance(payload.get('message'), str) or not isinstance(payload.get('request_id'), str):
                raise ValueError('signal-send needs request_id, target and message')
            target = payload.get('target')
            if not (isinstance(target, str) and target.startswith(('dm:', 'group:'))):
                status, contacts = call({'action': 'signal-contacts'})
                target = resolve_target(target, contacts.get('destinations') or contacts.get('contacts') or (contacts if isinstance(contacts, list) else []))
            route = route_for(target)
            try:
                guard(route)
            except ValueError as refusal:
                print(json.dumps({'ok': False, 'error': 'not sent: ' + str(refusal), 'request_id': payload['request_id']}))
                return 1
            if args.attach:
                if 'attachments' in payload:
                    raise ValueError('use --attach or JSON attachments, not both')
                payload['attachments'] = attachments.read_files(args.attach)
            if 'attachments' in payload:
                attachments.validate(payload['attachments'])
            if args.reply_to:
                if 'reply_to' in payload:
                    raise ValueError('use --reply-to or JSON reply_to, not both')
                payload['reply_to'], reply_step = resolve_reply(args.reply_to, route)
        payload['action'] = args.action
        status, result = call(payload)
        result = explain_status(result)
        print(json.dumps(result, ensure_ascii=False))
        ok = status == 200 and result.get('ok')
        if ok and route is not None:
            if payload.get('attachments') or reply_step:
                record(route, payload['message'], payload['request_id'], result.get('phase', 'queued'),
                       payload.get('attachments'), reply_step)
            else:
                record(route, payload['message'], payload['request_id'], result.get('phase', 'queued'))
        return 0 if ok else 1
    except ValueError as error:
        print(json.dumps({'ok': False, 'error': str(error)[:200]}))
        return 2
    except (OSError, http.client.HTTPException, RecursionError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'error': 'Unavailable or invalid request; a write may be queued. Check status and reuse its exact request ID.'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
