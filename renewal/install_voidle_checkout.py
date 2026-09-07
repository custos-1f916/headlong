#!/usr/bin/env python3
"""Provision a fresh canonical Voidle checkout, before editor/build use.

Subsequent worktrees use the repository's existing prep_worktree.sh. This
restores the same ignored inputs; it never substitutes a prebuilt APK.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET

EXPECTED = 'd7d1fb8f705fe7de208ffdae3ccfb6a5a990b36b3e653b13bacb1ebf23214d6c'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--project', type=Path, default=Path('/opt/custos/work/voidle'))
    args = parser.parse_args()
    if not (args.project / '.git').exists():
        raise RuntimeError('Provision the real source checkout first')
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != EXPECTED:
        raise RuntimeError('Build-input archive digest mismatch')
    with tarfile.open(args.archive) as archive:
        for entry in archive:
            parts = Path(entry.name).parts
            allowed = (entry.name.startswith(('icons/', 'addons/admob/android/bin/', 'android/build/res/'))
                       or entry.name in {'icons', 'addons/admob/android/bin', 'android/build/res', 'android/.build_version'}
                       or entry.name.endswith('.uid'))
            if not allowed or '..' in parts or Path(entry.name).is_absolute() or not (entry.isfile() or entry.isdir()):
                raise RuntimeError('Unapproved build-input member')
    cache = args.project / '.godot'
    if cache.exists():
        shutil.rmtree(cache)  # Regenerate against the preserved UID sidecars.
    subprocess.run(['tar', '-xzf', str(args.archive), '--no-same-owner', '-C', str(args.project)], check=True)
    resource = Path(__file__).with_name('voidle-build-resources') / 'play_games_strings.xml'
    project_id = ET.parse(resource).find(".//string[@name='game_services_project_id']").text
    if not project_id or not project_id.isdigit():
        raise RuntimeError('Invalid real Game Services project identifier')
    target = args.project / 'android/build/res/values/play_games_strings.xml'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resource, target)
    settings = Path.home() / '.local/share/godot/app_userdata/Voidle/game_services.cfg'
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('[GAME_SERVICES]\nGAME_ID="' + project_id + '"\n')
    print('CANONICAL_BUILD_INPUTS_RESTORED: UID sidecars, generated icons, AdMob bridge and real public Games resource; source build still required')


if __name__ == '__main__':
    main()
