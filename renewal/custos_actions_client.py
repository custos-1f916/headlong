#!/usr/bin/env python3
"""Custos's proactive Signal and Automata commands. Writes read JSON on stdin.

custos-actions signal-contacts
custos-actions signal-send < {request_id,target,message}.json
custos-actions automata-status
custos-actions automata-deploy < {request_id,goal_id,commit}.json
custos-actions automata-rollback < {request_id,goal_id}.json
custos-actions status REQUEST_ID
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
import subprocess
import sys

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


def record(route, message, request_id, phase):
    """Append the accepted send to the root trajectory as a message step."""
    root = os.environ.get('ROOT_TRAJ_ID') or os.environ.get('TRAJ_ID')
    if not root:
        return
    step = {'type': 'message', 'from': os.environ.get('IDENTITY_NAME', 'custos'), 'to': route, 'content': message,
            'source': 'custos-actions', 'delivered_by': 'custos-actions', 'request_id': request_id, 'phase': phase}
    subprocess.run(['traj', 'append', root], input=json.dumps(step, ensure_ascii=False), text=True, capture_output=True, timeout=30)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=['signal-contacts', 'signal-send', 'automata-status', 'automata-deploy', 'automata-rollback', 'status'])
    p.add_argument('request_id', nargs='?'); args = p.parse_args()
    try:
        if args.action in ('signal-send', 'automata-deploy', 'automata-rollback'):
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768: raise ValueError('request too large')
            payload = json.loads(raw)
            if not isinstance(payload, dict) or 'action' in payload: raise ValueError('object without action required')
        else:
            payload = {}
        if args.action == 'status':
            if not args.request_id: raise ValueError('request ID required')
            payload['request_id'] = args.request_id
        elif args.request_id:
            raise ValueError('unexpected request ID argument')
        route = None
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
        payload['action'] = args.action
        status, result = call(payload)
        print(json.dumps(result, ensure_ascii=False))
        ok = status == 200 and result.get('ok')
        if ok and route is not None:
            record(route, payload['message'], payload['request_id'], result.get('phase', 'queued'))
        return 0 if ok else 1
    except (ValueError, OSError, http.client.HTTPException, RecursionError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'error': 'Unavailable or invalid request; a write may be queued. Check status and reuse its exact request ID.'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
