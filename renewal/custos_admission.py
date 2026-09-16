"""Cheap, shared admission preflight. Never performs inference or grants access."""
import fcntl
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from custos_brain_client import brain_url


def probe(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + '/health', timeout=5) as response:
            value = json.loads(response.read(8192))
        return ('admitted', 5) if value.get('status') == 'ok' else ('invalid_health_response', 300)
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read(8192))['error']
            code = value['code']
            if not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_]{1,100}', code):
                raise ValueError('invalid code')
            delay = 30 if code in {'custos_request_in_flight', 'backend_busy_or_unavailable'} else 300
            return code, delay
        except (ValueError, KeyError, TypeError):
            return 'invalid_gateway_denial', 300
    except (OSError, ValueError, urllib.error.URLError):
        return 'gateway_unreachable', 300


def check(identity, url=None):
    root = Path(identity) / 'run'
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'inference-admission.json'
    url = url or brain_url()
    with (root / 'inference-admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = {}
        now = time.time()
        if old.get('url') == url and old.get('next_probe', 0) > now:
            return old
        state, delay = probe(url)
        transitions = old.get('transitions', [])
        if old.get('state') != state:
            transitions = (transitions + [{'at': now, 'from': old.get('state'), 'to': state}])[-32:]
        result = {'state': state, 'url': url, 'checked_at': now,
                  'next_probe': now + delay, 'transitions': transitions}
        tmp = path.with_suffix('.tmp')
        with tmp.open('w') as out:
            json.dump(result, out)
            out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
        return result


def main():
    state = check(os.environ['IDENTITY_DIR'])
    if state['state'] == 'admitted':
        return 0
    print('Inference deferred: %s; shared health retry after %s UTC. No model call made.' %
          (state['state'], time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(state['next_probe']))))
    return 75
