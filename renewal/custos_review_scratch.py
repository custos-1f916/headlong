"""Explicit ownership for disposable review work; never adopt arbitrary directories."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def fingerprint(path):
    entries = []
    for p in sorted(path.rglob('*')):
        if p.is_symlink(): value = ['link', os.readlink(p)]
        elif p.is_file(): value = ['file', hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777]
        elif p.is_dir(): continue
        else: raise ValueError('special file in scratch; preserve it')
        entries.append([str(p.relative_to(path)), value])
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


class Registry:
    def __init__(self, root=None, budget=2 << 30):
        self.root = Path(root or Path(os.environ['IDENTITY_DIR']) / '.state/review-scratch')
        self.budget = budget
        self.managed = root is None

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def rows(self):
        return [(p, json.loads(p.read_text())) for p in self.root.glob('*.json')]

    def save(self, path, row):
        staging = path.with_suffix('.new')
        staging.write_text(json.dumps(row, sort_keys=True))
        staging.replace(path)

    def create(self, repo, head, goal, kind, review=None):
        import re
        if (bool(goal) == bool(review) or kind not in {'worktree', 'copy'}
                or (goal and not re.fullmatch('[0-9a-f]{8}', goal))
                or (review and not re.fullmatch(r'github-pr:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*:[1-9][0-9]*', review))):
            raise ValueError('one native goal or observation batch owner, and worktree/copy kind required')
        repo = Path(repo).resolve(strict=True)
        head = run('git', '-C', str(repo), 'rev-parse', '--verify', head + '^{commit}')
        with self.locked():
            # Budget applies only to our owned scratch; dirty work is never evicted.
            used = sum(p.stat().st_size for _, r in self.rows() for p in Path(r['path']).rglob('*') if p.is_file() and not p.is_symlink())
            sizes = run('git', '-C', str(repo), 'ls-tree', '-rl', head)
            estimate = sum(int(line.split()[3]) for line in sizes.splitlines() if line.split()[3].isdigit())
            if used + estimate > self.budget:
                raise ValueError('source snapshot exceeds scratch budget; use a smaller review scope')
            path = Path(tempfile.mkdtemp(prefix='custos-review-'))
            token = uuid.uuid4().hex
            row = {'path': str(path), 'repo': str(repo), 'head': head, 'goal': goal or '', 'kind': kind,
                   'device': path.stat().st_dev, 'inode': path.stat().st_ino, 'completed': False}
            if review: row['review'] = review
            # Register before population: a crash leaves a preserved, diagnosable entry.
            record = self.root / (token + '.json'); self.save(record, row)
            if kind == 'worktree':
                run('git', '-C', str(repo), 'worktree', 'add', '--detach', str(path), head)
            else:
                import tarfile
                with tempfile.TemporaryFile() as archive:
                    subprocess.run(['git', '-C', str(repo), 'archive', head], stdout=archive, stderr=subprocess.PIPE, check=True, timeout=60)
                    if used + archive.tell() > self.budget: raise ValueError('review archive exceeds scratch budget')
                    archive.seek(0)
                    with tarfile.open(fileobj=archive) as tar:
                        members = tar.getmembers()
                        links = {m.name.rstrip('/') for m in members if m.issym()}
                        for member in members:
                            from pathlib import PurePosixPath
                            relative = PurePosixPath(member.name)
                            if relative.is_absolute() or '..' in relative.parts or any(str(parent) in links for parent in relative.parents):
                                raise ValueError('unsafe archive path')
                            if not (member.isdir() or member.isfile() or member.issym()):
                                raise ValueError('unsupported archive entry')
                            if member.issym():
                                target = (path / member.name).parent / member.linkname
                                if not target.resolve().is_relative_to(path): raise ValueError('external archive symlink')
                        for member in members:
                            target = path / member.name
                            if member.isdir(): target.mkdir(parents=True, exist_ok=True)
                            elif member.isfile():
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with tar.extractfile(member) as source, target.open('wb') as dest: shutil.copyfileobj(source, dest)
                                target.chmod(member.mode & 0o777)
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.symlink_to(member.linkname)
                row['fingerprint'] = fingerprint(path)
            row['device'], row['inode'] = path.stat().st_dev, path.stat().st_ino
            self.save(record, row)
            return row

    def finish_review(self, review, head, report):
        path = Path(report).resolve(strict=True)
        raw = path.read_bytes()
        if not raw or len(raw) > 1024*1024 or head not in raw.decode():
            raise ValueError('retain a report containing the full reviewed head')
        with self.locked():
            rows = [item for item in self.rows() if item[1].get('review') == review and item[1]['head'] == head]
            if any(path.is_relative_to(Path(row['path']).resolve()) for _, row in rows):
                raise ValueError('report must be retained outside disposable scratch')
            for record, row in rows:
                row['completed'] = True
                row['completion_evidence'] = {'report': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
                self.save(record, row)
        return self.clean()

    def clean(self, goal=None, evidence=''):
        result = {'removed': [], 'preserved': []}
        with self.locked():
            for record, row in self.rows():
                if goal is None and self.managed and row.get('goal') and not row['completed']:
                    from custos_memory import Store, MemoryError
                    try:
                        store = Store()
                        with store.lock(): native = store.find(row['goal'])[4]
                        resolution = (native or {}).get('resolution', {})
                        if native and native['status'] == 'completed' and row['head'] in resolution.get('evidence', ''):
                            row['completed'] = True; self.save(record, row)
                    except (OSError, ValueError, MemoryError): pass
                if goal == row['goal'] and row['head'] in evidence:
                    row['completed'] = True; self.save(record, row)
                if not row['completed']: continue
                path = Path(row['path'])
                try:
                    if not path.exists(): record.unlink(); continue
                    if (path.is_symlink() or path.parent != Path(tempfile.gettempdir())
                        or not path.name.startswith('custos-review-')
                        or (path.stat().st_dev, path.stat().st_ino) != (row['device'], row['inode'])):
                        raise ValueError('scratch identity changed')
                    if row['kind'] == 'worktree':
                        if run('git', '-C', str(path), 'rev-parse', 'HEAD') != row['head']:
                            raise ValueError('head changed')
                        if run('git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all', '--ignored'):
                            raise ValueError('dirty or generated work retained')
                        run('git', '-C', row['repo'], 'worktree', 'remove', str(path))
                    else:
                        if fingerprint(path) != row.get('fingerprint'): raise ValueError('copy changed')
                        shutil.rmtree(path)
                    record.unlink(); result['removed'].append(str(path))
                except (ValueError, OSError, subprocess.SubprocessError):
                    result['preserved'].append(str(path))
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create')
    for arg in ('repo', 'head'): create.add_argument('--' + arg, required=True)
    owner = create.add_mutually_exclusive_group(required=True)
    owner.add_argument('--goal')
    owner.add_argument('--review')
    create.add_argument('--kind', choices=['worktree', 'copy'], default='worktree')
    finish = sub.add_parser('finish')
    for arg in ('review', 'head', 'report'): finish.add_argument('--' + arg, required=True)
    sub.add_parser('clean')
    args = p.parse_args()
    try:
        registry = Registry()
        if args.action == 'create': result = registry.create(args.repo, args.head, args.goal, args.kind, args.review)
        elif args.action == 'finish': result = registry.finish_review(args.review, args.head, args.report)
        else: result = registry.clean()
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)})); return 1

if __name__ == '__main__': sys.exit(main())
