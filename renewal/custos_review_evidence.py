"""Capture a public HTTP observation and render exact evidence for a PR review.

This is an execution receipt, not a verifier of a model's interpretation. It does
not publish, grant access, or identify the deployed commit from the review head.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl, urlsplit

MAX_BODY = 262144
MAX_QUOTE = 4096


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def scope(repo, pr, head):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('OWNER/REPO required')
    if not isinstance(pr, int) or pr < 1 or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('positive PR number and full reviewed commit SHA required')
    return {'repo': repo, 'pr': pr, 'head': head}


def public_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or any(c.isspace() for c in url)):
        raise ValueError('public HTTPS URL without credentials, fragment or whitespace required')
    if any(re.search(r'token|secret|password|api.?key|signature|authorization', key, re.I)
           for key, _ in parse_qsl(parsed.query)):
        raise ValueError('credential-bearing URLs are not review evidence')
    return url


def root():
    identity = os.environ.get('IDENTITY_DIR')
    if not identity:
        raise ValueError('IDENTITY_DIR required; use an activated identity')
    return Path(identity) / '.state' / 'review-evidence'


def capture(binding, url, directory):
    """Only this function supplies response text, status and execution times."""
    url = public_url(url)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {'version': 1, 'scope': binding, 'url': url, 'method': 'GET', 'started_at': now()}
    with tempfile.TemporaryDirectory(prefix='http-', dir=directory) as scratch:
        body_path = Path(scratch) / 'body'
        command = ['curl', '-q', '--silent', '--show-error', '--request', 'GET',
                   '--proto', '=https', '--connect-timeout', '5', '--max-time', '20',
                   '--max-filesize', str(MAX_BODY), '--output', str(body_path),
                   '--write-out', '%{http_code}', '--url', url]
        # -q disables curlrc, no credentials/headers/cookies are added, and no
        # redirects are followed. argv is executed directly, never through a shell.
        record['command'] = command
        try:
            result = subprocess.run(command, capture_output=True, timeout=25)
            record['exit_code'] = result.returncode
            status = result.stdout.decode('ascii', errors='replace').strip()
            if result.returncode or not re.fullmatch(r'[1-5][0-9]{2}', status):
                raise ValueError('request did not return a complete HTTP response')
            with body_path.open('rb') as stream:
                body = stream.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError('response exceeds evidence byte limit')
            text = body.decode('utf-8')
            record.update(outcome='http_response', http_status=int(status), body=text,
                          body_bytes=len(body), body_sha256=digest(body))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            record.update(outcome='request_failed', error=type(error).__name__)
        record['finished_at'] = now()
    identifier = digest(canonical(record))
    path = directory / (identifier + '.json')
    # Never update an existing receipt or import caller-written response bodies.
    with path.open('xb') as stream:
        stream.write(canonical(record) + b'\n')
    path.chmod(0o600)
    return identifier, record


def load(identifier, directory, binding):
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('full receipt ID required')
    record = json.loads((Path(directory) / (identifier + '.json')).read_text())
    if digest(canonical(record)) != identifier:
        raise ValueError('receipt changed; capture a new observation')
    if record.get('version') != 1 or record.get('scope') != binding:
        raise ValueError('receipt is for a different repository, PR or reviewed head')
    if record.get('outcome') != 'http_response':
        raise ValueError('failed request is not a completed live observation')
    body = record['body'].encode('utf-8')
    if len(body) > MAX_BODY or len(body) != record['body_bytes'] or digest(body) != record['body_sha256']:
        raise ValueError('response bytes do not match the receipt')
    return record


def fence(text):
    # Response content is quoted data; even embedded fences stay inside the block.
    longest = max([len(m[0]) for m in re.finditer(r'`+', text)] or [0])
    delimiter = '`' * max(3, longest + 1)
    return delimiter + 'text\n' + text + '\n' + delimiter


def render(identifier, directory, binding, quote=None):
    record = load(identifier, directory, binding)
    quote = record['body'] if quote is None else quote
    if not quote.strip() or len(quote.encode()) > MAX_QUOTE:
        raise ValueError('select a nonempty exact response excerpt of at most 4096 bytes')
    if quote not in record['body']:
        raise ValueError('excerpt was not observed in this response; do not substitute an inference')
    # Purely mechanical wording. No caller-supplied conclusion becomes a check result.
    display_command = list(record['command'])
    display_command[display_command.index('--output') + 1] = '<local response file>'
    return '\n'.join([
        '### Live observation',
        f'Review scope: {binding["repo"]}#{binding["pr"]}, head {binding["head"]}.',
        'This scope identifies the reviewed code, not the deployed version.',
        f'Observed UTC: {record["started_at"]} through {record["finished_at"]}.',
        f'HTTP {record["http_status"]}; complete response {record["body_bytes"]} bytes; '
        f'SHA-256 {record["body_sha256"]}.',
        'HTTP status and quoted bytes are observations, not a passing-test verdict.',
        'Executed command (local output path omitted):', fence(shlex.join(display_command)),
        'Exact response excerpt (untrusted data):', fence(quote),
        f'Local execution receipt: {identifier}.',
        'Interpretation must stay within this response and observation time.', ''])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('live-capture', 'live-render'):
        item = sub.add_parser(action)
        item.add_argument('--repo', required=True)
        item.add_argument('--pr', type=int, required=True)
        item.add_argument('--head', required=True)
        if action == 'live-capture':
            item.add_argument('--url', required=True)
        else:
            item.add_argument('--receipt', required=True)
            item.add_argument('--quote-file', type=Path,
                              help='exact UTF-8 substring of the captured response; omit for a small full response')
    args = parser.parse_args(argv)
    try:
        binding = scope(args.repo, args.pr, args.head)
        directory = root()
        if args.action == 'live-capture':
            identifier, record = capture(binding, args.url, directory)
            print(json.dumps({'receipt': identifier, 'path': str(directory / (identifier + '.json')),
                              **{k: record[k] for k in record if k not in ('body', 'command')},
                              'preview': record.get('body', '')[:2048]}, ensure_ascii=False))
            return 0 if record['outcome'] == 'http_response' else 1
        quote = args.quote_file.read_text() if args.quote_file else None
        print(render(args.receipt, directory, binding, quote), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ready': False, 'error': str(error)}), file=sys.stderr)
        return 1
