"""Custos inference delegation stays bounded, synchronous, and single-route."""
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(
    os.environ.get(
        "HEADLONG_ROOT",
        pathlib.Path(__file__).resolve().parents[2] / "runtime" / "headlong",
    )
)


class SubrunPolicy(unittest.TestCase):
    def test_policy_text_requires_one_bounded_synchronous_helper(self):
        command = (
            'SHELLM_RUN_SUMMARY=0 subrun --wait --cwd DIR --max-iterations 12 '
            '--effort "${SHELLM_EFFORT:?}"'
        )
        prompt = (ROOT / "thinkers/monolith/prompt.md").read_text()
        skill = (
            pathlib.Path(__file__).resolve().parents[1]
            / "identity/skills/custos-harness/SKILL.md"
        ).read_text()

        self.assertIn(command, prompt)
        self.assertIn("one bounded synchronous helper at a time", prompt)
        self.assertIn("Do not start background model workers or use a fallback route", prompt)
        self.assertIn("A non-model long job", prompt)

        self.assertIn(command, skill)
        self.assertIn("above 12 automatically **detaches**", skill)
        self.assertIn("not permitted for Custos's inference", skill)
        self.assertIn("do not make\n  reading a detached model-worker report later", skill)

    def _environment(self, base, fake_bin, fake_rc):
        env = dict(
            os.environ,
            PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
            HOME=str(base / "home"),
            HEADLONG_HOME=str(base / "home/.headlong"),
            TMPDIR=str(base / "tmp"),
            SHELLM_EFFORT="configured-effort",
            SHELLM_RUN_SUMMARY="0",
            SUBRUN_SYNC_MAX_ITER="1",
            FAKE_SHELLM_RC=str(fake_rc),
        )
        for name in ["IDENTITY_DIR", "TRAJ_DIR", "TRAJ_ID", "MEM_DIR", "THINKERS_DIR"]:
            env.pop(name, None)
        return env

    def test_wait_overrides_auto_detach_and_propagates_child_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            (base / "home").mkdir()
            (base / "tmp").mkdir()
            workdir = base / "workdir"
            workdir.mkdir()
            fake_shellm = fake_bin / "shellm"
            fake_shellm.write_text(
                """#!/usr/bin/env bash
set -eu
effort=
while (($#)); do
    if [[ "$1" == "--effort" ]]; then
        effort="${2:?}"
        shift 2
    else
        shift
    fi
done
printf 'FINAL: fake child complete\\n'
printf 'forwarded-effort=%s\\n' "$effort"
exit "${FAKE_SHELLM_RC:-0}"
"""
            )
            fake_shellm.chmod(0o755)
            subrun = ROOT / "bin/subrun"

            report = base / "success.report"
            success = subprocess.run(
                [
                    str(subrun),
                    "--wait",
                    "--max-iterations",
                    "8",
                    "--effort",
                    "configured-effort",
                    "--report",
                    str(report),
                    "fixture task",
                ],
                cwd=workdir,
                env=self._environment(base, fake_bin, 0),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertNotIn("subrun: detached", success.stdout)
            self.assertIn("FINAL: fake child complete", success.stdout)
            contents = report.read_text()
            self.assertIn("FINAL: fake child complete", contents)
            self.assertIn("forwarded-effort=configured-effort", contents)
            self.assertIn("# state: exited rc=0", contents)

            failed_report = base / "failed.report"
            failed = subprocess.run(
                [
                    str(subrun),
                    "--wait",
                    "--max-iterations",
                    "8",
                    "--effort",
                    "configured-effort",
                    "--report",
                    str(failed_report),
                    "fixture failure",
                ],
                cwd=workdir,
                env=self._environment(base, fake_bin, 23),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(failed.returncode, 23, failed.stderr)
            self.assertNotIn("subrun: detached", failed.stdout)
            self.assertIn("# state: exited rc=23", failed_report.read_text())


if __name__ == "__main__":
    unittest.main()
