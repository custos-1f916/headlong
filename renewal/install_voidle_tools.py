#!/usr/bin/env python3
"""Restore only approved SDK/debug-emulator capabilities inside Custos's home."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

EXPECTED = '8787109e65a4b2b75de64d1fda108dd46f594c95a8d2f4aae53ebbf8f328e321'
ROOTS = ['opt/android-sdk', 'opt/godot', 'root/.android',
         'root/.local/share/godot/export_templates', 'root/android/debug.keystore', 'root/.gradle/caches']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or subprocess.check_output(['hostname'], text=True).strip() != 'custos':
        raise RuntimeError('Run only inside Custos LXC as root')
    marker = Path('/var/lib/custos/voidle-tools-installed')
    if marker.exists():
        raise RuntimeError('Toolchain already installed; do not overwrite an active emulator')
    with args.archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != EXPECTED:
            raise RuntimeError('Toolchain archive digest mismatch')
    names = subprocess.check_output(['tar', '--zstd', '-tf', str(args.archive)], text=True).splitlines()
    for name in names:
        if name.startswith('/') or '..' in Path(name).parts or not any(name.rstrip('/') == root or name.startswith(root + '/') for root in ROOTS):
            raise RuntimeError('Unapproved archive member')
    existing = [root for root in ROOTS if (Path('/') / root).exists()]
    if existing:
        backup = Path('/var/lib/custos-qualification/voidle-tools-before.tar.zst')
        if backup.exists():
            raise RuntimeError('Previous-tool backup already exists; inspect before proceeding')
        backup.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['tar', '--zstd', '-cf', str(backup), '-C', '/', *existing], check=True)
        backup.chmod(0o600)
    keys = [Path('/etc/custos.env'), Path('/opt/custos/ed25519.key'), Path('/opt/custos/ed25519-openssh.key')]
    digests = {key: hashlib.sha256(key.read_bytes()).digest() for key in keys}
    subprocess.run(['tar', '--zstd', '-xf', str(args.archive), '-C', '/'], check=True)
    for key, digest in digests.items():
        if hashlib.sha256(key.read_bytes()).digest() != digest:
            raise RuntimeError('Citizen identity changed during tool restoration')
    binary = Path('/opt/godot/Godot_v4.6.3-stable_linux.x86_64')
    if not binary.is_file():
        raise RuntimeError('Expected verified Godot binary missing')
    link = Path('/usr/local/bin/godot')
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            shutil.copy2(link, '/var/lib/custos-qualification/godot.before')
        link.unlink()
    link.symlink_to(binary)
    # Locks belong to the stopped former emulator; saved images remain in backup.
    for lock in Path('/root/.android/avd').glob('**/*.lock'):
        if lock.is_dir() and not lock.is_symlink():
            shutil.rmtree(lock)
        else:
            lock.unlink()
    settings = Path('/root/.config/godot/editor_settings-4.6.tres')
    settings.parent.mkdir(parents=True, exist_ok=True)
    if not settings.exists():
        settings.write_text('[gd_resource type="EditorSettings" format=3]\n\n[resource]\nexport/android/android_sdk_path = "/opt/android-sdk"\nexport/android/java_sdk_path = "/usr/lib/jvm/java-17-openjdk-amd64"\n')
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(EXPECTED + '\n')
    print(subprocess.check_output([str(link), '--version'], text=True).strip())
    print('VOIDLE_TOOLCHAIN_INSTALLED; no model, emulator or fixed pipeline started')


if __name__ == '__main__':
    main()
