"""Read an actual GitHub PR review receipt; derive the publication wording from it."""
import argparse
import datetime as dt
import json
import re
import subprocess


def capture(repo, pr, head, review):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or pr < 1 or review < 1:
        raise ValueError('repository, positive PR and review IDs required')
    if not re.fullmatch(r'[a-f0-9]{40}', head): raise ValueError('full reviewed head required')
    result = subprocess.run(['gh', 'api', f'repos/{repo}/pulls/{pr}/reviews/{review}'],
                            capture_output=True, text=True, timeout=30, check=True)
    row = json.loads(result.stdout)
    expected_url = f'https://github.com/{repo}/pull/{pr}#pullrequestreview-{review}'
    if row.get('id') != review or row.get('commit_id') != head or row.get('html_url') != expected_url:
        raise ValueError('review receipt does not match the requested PR/head/id')
    if row.get('user', {}).get('login') != 'custos-1f916': raise ValueError('review belongs to another author')
    state = row.get('state')
    wording = {'COMMENTED': 'Posted a COMMENT review', 'APPROVED': 'Published an APPROVED review',
               'CHANGES_REQUESTED': 'Published a CHANGES_REQUESTED review', 'DISMISSED': 'Review is DISMISSED'}
    if state not in wording or not row.get('submitted_at'): raise ValueError('review has no completed publication receipt')
    return {'repo': repo, 'pr': pr, 'head': head, 'review_id': review, 'state': state,
            'submitted_at': row['submitted_at'], 'url': row['html_url'],
            'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'rendered': f"{wording[state]} on {head}: {row['html_url']} (submitted {row['submitted_at']})."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True); parser.add_argument('--pr', required=True, type=int)
    parser.add_argument('--head', required=True); parser.add_argument('--review', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(capture(args.repo, args.pr, args.head, args.review))); return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as e:
        # gh stderr can contain account diagnostics; keep it out of the publication text.
        print(json.dumps({'ready': False, 'error': str(e) if isinstance(e, ValueError) else type(e).__name__})); return 1
