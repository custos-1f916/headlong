#!/usr/bin/env python3
"""One explicit fresh-persona cutover, never an implicit updater or installer.

Required private archive and identity hashes must predate this invocation.
Does not start any mind, observer, dashboard or public-writing process.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def unpack(archive, destination):
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise RuntimeError('archive contains a special device or unsupported entry')
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError('archive path escapes stage')
            if member.issym() or member.islnk():
                link = ((destination if member.islnk() else target.parent) / member.linkname).resolve()
                if not link.is_relative_to(destination.resolve()):
                    raise RuntimeError('archive link escapes stage')
        source.extractall(destination)
        # Archives built on the Mac carry uid501. Root owns its complete guest
        # home; fix ownership rather than disabling Git's safety checks.
        for root, directories, files in os.walk(destination):
            os.chown(root, 0, 0)
            for name in directories + files:
                os.chown(Path(root) / name, 0, 0, follow_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headlong', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--preserved', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('run inside LXC122 as root')
    if subprocess.check_output(['hostname'], text=True).strip() != 'custos':
        raise RuntimeError('wrong guest identity')
    marker = Path('/var/lib/custos/renewal-installed.json')
    if marker.exists():
        raise RuntimeError('fresh reset already installed; update source without resetting identity')
    manifest = json.loads((args.preserved / 'identity-manifest.json').read_text())
    for name, expected in manifest.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected['sha256']:
            raise RuntimeError('identity material changed since preservation: ' + name)
    for name in ['headlong-thinkers@custos.service', 'custos-relay-bridge.service', 'headlong-web.service']:
        state = subprocess.run(['systemctl', 'is-active', name], text=True, capture_output=True).stdout.strip()
        if state in ['active', 'activating', 'deactivating']:
            raise RuntimeError('old actor is not quiescent: ' + name)
    stage = Path(tempfile.mkdtemp(prefix='custos-cutover-', dir='/var/tmp'))
    try:
        unpack(args.headlong, stage)
        unpack(args.source, stage)
        fresh_app, fresh_repo = stage / 'headlong', stage / 'custos'
        if not (fresh_app / 'bin/thinkers').is_file() or not (fresh_repo / 'renewal/custos_memory.py').is_file():
            raise RuntimeError('incomplete deployment bundle')
        # Preserve ONLY the retained citizen's own keys and scoped source key.
        keep = {'ed25519.key', 'ed25519-openssh.key', 'git-deploy.key', 'git-deploy.key.pub'}
        home = Path('/opt/custos')
        for path in home.iterdir():
            if path.name in keep:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        shutil.rmtree('/root/.headlong')
        app_home = Path('/root/.headlong')
        app_home.mkdir(mode=0o700)
        shutil.move(fresh_app, app_home / 'app')
        app = app_home / 'app'
        shutil.move(fresh_repo, home / 'repo')
        renewal = home / 'repo/renewal'
        work = home / 'work'
        work.mkdir(mode=0o755)
        (app_home / 'app_dir').write_text(str(app) + '\n')
        local_bin = Path('/root/.local/bin')
        local_bin.mkdir(parents=True, exist_ok=True)
        for folder in ['bin', 'tools']:
            for source in (app / folder).iterdir():
                if source.is_file() and os.access(source, os.X_OK):
                    link = local_bin / source.name
                    if link.exists() or link.is_symlink(): link.unlink()
                    link.symlink_to(source)
        for link in local_bin.iterdir():
            if link.is_symlink() and str(app) in os.readlink(link) and not link.exists():
                link.unlink()
        for source in (renewal / 'bin').iterdir():
            source.chmod(0o755)
            link = Path('/usr/local/bin') / source.name
            if link.exists() or link.is_symlink(): link.unlink()
            link.symlink_to(source)
        operator_link = local_bin / 'custos'
        if operator_link.exists() or operator_link.is_symlink(): operator_link.unlink()
        operator_link.symlink_to('/usr/local/bin/custos')
        # Fresh built-in skill sources, never the retired global copy.
        core = Path('/root/.skills/core-skills')
        if core.is_dir() and not core.is_symlink(): shutil.rmtree(core)
        elif core.exists() or core.is_symlink(): core.unlink()
        core.parent.mkdir(parents=True, exist_ok=True)
        core.symlink_to(app / 'skills')
        defaults = (renewal / 'identity.env.example').read_text()
        defaults += '\nSHELLM_ENV=local\nSHELLM_WORKDIR=/opt/custos/work\n'
        (app_home / '.env').write_text(defaults)
        environment = os.environ.copy()
        environment.update({'HOME': '/root', 'PATH': '/root/.local/bin:/root/.bun/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin',
                            'IDENTITY_DIR': str(app / '.identities')})
        for key in ['IDENTITY_NAME', 'TRAJ_ID', 'ROOT_TRAJ_ID', 'MEM_DIR']:
            environment.pop(key, None)
        run([str(app / 'tools/identity'), 'new', '--default', 'custos'], cwd=app, env=environment)
        identity = app / '.identities/custos'
        # Upstream's canned thoughts assert a human family/personhood. They are
        # bootstrap scaffolding, not experience or this persona's charter.
        root_id = dict(line.split('=', 1) for line in (identity / 'info.txt').read_text().splitlines())['root_trajectory']
        root_env = environment | {'TRAJ_DIR': str(identity / 'trajectories')}
        root_path = Path(subprocess.check_output(['traj', 'path', root_id], env=root_env, text=True).strip())
        initial = [json.loads(line) for line in root_path.read_text().splitlines()]
        if any(item.get('type') != 'trajectory' and item.get('source') != 'seed' for item in initial):
            raise RuntimeError('refusing to erase non-bootstrap experience')
        root_path.write_text('\n'.join(json.dumps(item) for item in initial if item.get('type') == 'trajectory') + '\n')
        run(['traj', 'append', root_id], env=root_env, input=json.dumps({
            'type': 'observation', 'source': 'operator-bootstrap',
            'content': 'Hal authorized a fresh Custos working persona and history. The existing citizen identity is retained. Use the installed charter and native goals; no canned biography or inherited watch duties apply.'}),
            stdout=subprocess.DEVNULL)
        shutil.copy2(renewal / 'identity/core_identity_prompt.md', identity / 'core_identity_prompt.md')
        (identity / '.env').write_text(defaults)
        info = identity / 'info.txt'
        info.write_text(info.read_text() + 'think_model=qwen3.8-27b\n')
        for name in ['googleworkspace', 'obsidian', 'slack', 'telegram']:
            target = identity / 'skills' / name
            if target.is_symlink(): target.unlink()
            elif target.exists(): shutil.rmtree(target)
        shutil.copytree(renewal / 'identity/skills', identity / 'skills', dirs_exist_ok=True)
        # Native share defaults to Custos; operator transports set sender explicitly.
        (identity / 'chat').mkdir(exist_ok=True)
        (identity / 'chat/.chatrc').write_text('default_send_from=custos\n')
        # Web UI reads the serve-root default, not the activated agent CHATRC.
        (app / '.chatrc').write_text('default_send_from=hal\n')
        shutil.copy2(renewal / 'identity/WORKSPACE.md', work / 'WORKSPACE.md')
        seed = json.loads((renewal / 'identity/seed.json').read_text())
        native_env = environment | {'MEM_DIR': str(identity / 'memories')}
        for item in seed['memories']:
            run(['mem', 'add', '--type', item['type'], item['content']], env=native_env, stdout=subprocess.DEVNULL)
        env_text = 'HOME=/root\nPATH=' + environment['PATH'] + '\n'
        env_text += '\n'.join(line for line in defaults.splitlines() if line and not line.startswith('#')) + '\n'
        Path('/etc/custos-headlong.env').write_text(env_text)
        Path('/etc/custos-headlong.env').chmod(0o600)
        # Git writes are scoped to Custos's own repository deploy key.
        run(['git', '-C', str(home / 'repo'), 'config', 'core.sshCommand',
             'ssh -i /opt/custos/git-deploy.key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'])
        units = ['headlong-thinkers@custos.service', 'headlong-web.service', 'custos-relay-bridge.service',
                 'custos-observe.service', 'custos-observe.timer']
        for name in units:
            target = Path('/etc/systemd/system') / name
            if target.exists() or target.is_symlink(): target.unlink()
            shutil.copy2(renewal / 'systemd' / name, target)
            target.chmod(0o644)
        run(['systemctl', 'daemon-reload'])
        run(['npm', 'ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund'], cwd=renewal, env=environment)
        # Verify retained keys again before any real actor can run.
        for name, expected in manifest.items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected['sha256']:
                raise RuntimeError('identity preservation failed: ' + name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'headlong': '5458fee8e722c9eb45a75739e9fa1f92419ebafc',
                                     'source_archive_sha256': hashlib.file_digest(args.source.open('rb'), 'sha256').hexdigest(),
                                     'identity': str(identity), 'root': info.read_text(),
                                     'preserved': str(args.preserved)}, indent=2) + '\n')
        marker.chmod(0o600)
        print('FRESH_CUSTOS_INSTALLED; no actors or public writers started', flush=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == '__main__':
    main()
