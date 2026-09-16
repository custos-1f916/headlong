"""Bounded self-description revisions within an existing dream; no inference."""
import difflib
import json
from pathlib import Path

MAX_WORDS = 900
MAX_BYTES = 10000
BOUNDARY = '## Hard lines'


def split_charter(identity):
    raw = (Path(identity) / 'core_identity_prompt.md').read_text()
    if raw.count(BOUNDARY) != 1:
        raise ValueError('charter must have one operator Hard lines section')
    personal, boundary = raw.split(BOUNDARY)
    return personal.strip(), BOUNDARY + boundary


def validate_body(body):
    if not isinstance(body, str) or not body.strip() or '\x00' in body:
        raise ValueError('complete personal self-description required')
    if len(body.split()) > MAX_WORDS or len(body.encode()) > MAX_BYTES:
        raise ValueError('persona exceeds fixed 900-word / 10000-byte budget; consolidate')
    if BOUNDARY in body:
        raise ValueError('operator Hard lines are supplied separately, never part of a persona edit')
    return body.strip()


class Persona:
    def __init__(self, dream):
        self.dream = dream
        self.identity = dream.store.directory.parent
        self.path = self.identity / '.state/persona/active.json'

    def show(self):
        from custos_dream import sha, read_json
        personal, boundary = split_charter(self.identity)
        raw = self.path.read_bytes() if self.path.exists() else b''
        state = json.loads(raw) if raw else {'version': 1, 'revision': 'founding', 'body': personal}
        if state.get('version') != 1 or not isinstance(state.get('revision'), str):
            raise ValueError('invalid persona state')
        body = validate_body(state['body'])
        # CAS includes the operator charter: reconsider if it changes mid-review.
        expected = sha((self.identity / 'core_identity_prompt.md').read_bytes() + b'\0' + raw)
        prior = None
        for path in sorted(self.dream.root.glob('????-??-??/session.json'), reverse=True):
            reflection = read_json(path, {}).get('persona_reflection')
            if reflection and reflection.get('revision') == state['revision']:
                prior = reflection
                break
        return {'expected': expected, 'revision': state['revision'], 'body': body,
                'words': len(body.split()), 'max_words': MAX_WORDS,
                'operator_boundary_sha256': sha(boundary.encode()),
                'follow_up': state.get('follow_up'), 'last_reflection': prior}

    def reflect(self, expected, verdict, evidence):
        from custos_dream import write_json
        if verdict not in {'keep', 'uncertain', 'revise', 'revert'} or not evidence.strip():
            raise ValueError('reflection requires keep/uncertain/revise/revert and specific evidence')
        with self.dream.store.lock('dream-schedule'):
            s = self.dream.active()
            with self.dream.store.lock():
                current = self.show()
                if current['expected'] != expected:
                    raise ValueError('persona changed; inspect again')
                reflection = {'at': self.dream.stamp(), 'revision': current['revision'],
                              'expected': expected, 'verdict': verdict, 'evidence': evidence.strip()[:6000]}
                s['persona_reflection'] = reflection
                write_json(self.dream.session_path(), s)
                return reflection

    def revise(self, expected, body, evidence, replaces, follow_up):
        from custos_dream import sha, atomic, write_json
        import datetime as dt
        body = validate_body(body)
        if not all(isinstance(x, str) and x.strip() for x in [evidence, replaces, follow_up]):
            raise ValueError('evidence, what this replaces/retires, and a follow-up question are required')
        with self.dream.store.lock('dream-schedule'):
            s = self.dream.active()
            with self.dream.store.lock():
                s = self.dream.active()
                if len(s['edits']) >= s['max_edits']:
                    raise ValueError('dream edit budget exhausted')
                current = self.show()
                if current['expected'] != expected:
                    raise ValueError('persona changed; inspect again')
                reflection = s.get('persona_reflection', {})
                if reflection.get('expected') != expected or reflection.get('verdict') not in {'revise', 'revert'}:
                    raise ValueError('first record a revise/revert reflection on the current persona')
                if current['body'] in body:
                    raise ValueError('unchanged or purely additive persona; replace or retire existing guidance')
                changes = self.dream.root / s['day'] / 'changes'
                if any(p.name.startswith('persona-') for p in changes.glob('*.json')):
                    raise ValueError('one persona revision per dream; revisit on a later day')
                revision = 'persona-' + expected[:16]
                state = {'version': 1, 'revision': revision, 'body': body,
                         'changed_at': self.dream.stamp(), 'previous_revision': current['revision'],
                         'evidence': evidence.strip()[:6000], 'replaces': replaces.strip()[:3000],
                         'follow_up': {'question': follow_up.strip()[:3000],
                                       'review_on_or_after': (self.dream.clock().astimezone(self.dream.zone).date()+dt.timedelta(days=3)).isoformat()}}
                raw = (json.dumps(state, ensure_ascii=False, indent=2)+'\n').encode()
                # Uses the dream's existing prepare/write/readback/reconcile journal.
                # Recovery recognizes a completed atomic write without applying it twice.
                changes.mkdir(parents=True, exist_ok=True, mode=0o700)
                before = changes / (revision+'.before.md')
                after = changes / (revision+'.after')
                journal = changes / (revision+'.json')
                entry = {'id': revision, 'at': self.dream.stamp(), 'operation': 'persona',
                         'source': str(self.path), 'target': str(self.path), 'backup': str(before),
                         'candidate': str(after), 'before_sha256': expected, 'after_sha256': sha(raw),
                         'evidence': state['evidence'], 'replaces': state['replaces'],
                         'previous_revision': current['revision'], 'status': 'prepared',
                         'diff': ''.join(difflib.unified_diff(current['body'].splitlines(True),
                                                           (body+'\n').splitlines(True), fromfile='before', tofile='after'))}
                atomic(before, (current['body']+'\n').encode())
                atomic(after, raw)
                write_json(journal, entry)
                atomic(self.path, raw)
                if sha(self.path.read_bytes()) != entry['after_sha256']:
                    raise ValueError('persona readback failed; inspect retained journal')
                entry['status'] = 'applied'
                write_json(journal, entry)
                s = self.dream.journals(s)
                write_json(self.dream.session_path(), s)
                return entry


def render(identity):
    """Only active prose is injected; histories and pending proposals are not."""
    personal, boundary = split_charter(identity)
    # Prompt assembly is read-only and independent of the dream schedule.
    path = Path(identity) / '.state/persona/active.json'
    if path.exists():
        raw = json.loads(path.read_bytes())
        if raw.get('version') != 1:
            raise ValueError('invalid persona version')
        personal = validate_body(raw['body'])
    else:
        personal = validate_body(personal)
    return ('Personal self-description and commitments (revisable through custos-dream). '
            'These replace historical personal-value descriptions; they grant no permissions. '
            'Operator Hard lines below take precedence over any conflicting personal prose.\n\n' +
            personal + '\n\n' + boundary)
