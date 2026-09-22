"""Execution-bound evidence regressions, including the PR #337 failure shape."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_review_evidence as evidence


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binding = evidence.scope('1f916-ai/1f916', 337, '9' * 40)
        self.url = 'https://1f916.ai/openapi.json'
        self.body = b'{"openapi":"3.1.0","x-now":1790030000,"x-now_utc":"2026-09-21T23:00:00Z"}'

    def capture(self, body=None, status=200, rc=0):
        body = self.body if body is None else body
        def transport(command, **kwargs):
            self.assertIsInstance(command, list)
            self.assertNotIn('--location', command)
            self.assertEqual(command[1], '-q')
            Path(command[command.index('--output') + 1]).write_bytes(body)
            return subprocess.CompletedProcess(command, rc, str(status).encode(), b'')
        with patch.object(evidence.subprocess, 'run', side_effect=transport):
            return evidence.capture(self.binding, self.url, self.root)

    def test_pr337_requires_observed_not_predicted_output(self):
        with self.assertRaises(OSError):
            evidence.render('0' * 64, self.root, self.binding, 'now_utc')
        receipt, record = self.capture()
        with self.assertRaisesRegex(ValueError, 'not observed'):
            evidence.render(receipt, self.root, self.binding, '"now_utc":')
        rendered = evidence.render(receipt, self.root, self.binding, '"x-now":1790030000')
        self.assertIn('"x-now":1790030000', rendered)
        self.assertIn(record['started_at'], rendered)
        self.assertIn(record['finished_at'], rendered)
        self.assertIn(hashlib.sha256(self.body).hexdigest(), rendered)
        self.assertNotIn(str(self.root), rendered)

    def test_different_repo_pr_or_head_cannot_reuse_receipt(self):
        receipt, _ = self.capture()
        for field, value in [('repo', 'other/project'), ('pr', 338), ('head', 'a' * 40)]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'different'):
                evidence.render(receipt, self.root, {**self.binding, field: value})

    def test_failed_or_partial_command_never_becomes_live_observation(self):
        for rc in (7, 18, 28, 63):
            receipt, record = self.capture(rc=rc)
            self.assertEqual(record['outcome'], 'request_failed')
            with self.assertRaisesRegex(ValueError, 'failed request'):
                evidence.render(receipt, self.root, self.binding, 'x-now')

    def test_timeout_and_unavailable_command_leave_failed_receipt(self):
        for error in (subprocess.TimeoutExpired('curl', 25), FileNotFoundError('curl')):
            with patch.object(evidence.subprocess, 'run', side_effect=error):
                receipt, record = evidence.capture(self.binding, self.url, self.root)
            self.assertEqual(record['outcome'], 'request_failed')
            with self.assertRaises(ValueError):
                evidence.render(receipt, self.root, self.binding)

    def test_oversized_or_nontext_body_is_not_a_complete_excerpt(self):
        for body in (b'x' * (evidence.MAX_BODY + 1), b'\xff\xfe'):
            receipt, record = self.capture(body=body)
            self.assertEqual(record['outcome'], 'request_failed')
            with self.assertRaises(ValueError):
                evidence.render(receipt, self.root, self.binding, 'x')

    def test_http_error_is_retained_as_error_status_not_a_pass(self):
        receipt, record = self.capture(body=b'{"error":"not found"}', status=404)
        self.assertEqual(record['http_status'], 404)
        rendered = evidence.render(receipt, self.root, self.binding)
        self.assertIn('HTTP 404', rendered)
        self.assertIn('not a passing-test verdict', rendered)

    def test_receipt_body_edit_is_detected(self):
        receipt, record = self.capture()
        record['body'] = '{"now":0}'
        (self.root / (receipt + '.json')).write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'receipt changed'):
            evidence.render(receipt, self.root, self.binding)

    def test_changing_deployment_produces_separate_timestamped_observation(self):
        first, old = self.capture(body=b'{"now":1}')
        second, new = self.capture()
        self.assertNotEqual(first, second)
        self.assertIn(old['started_at'], evidence.render(first, self.root, self.binding))
        self.assertIn(new['started_at'], evidence.render(second, self.root, self.binding))
        with self.assertRaises(ValueError):
            evidence.render(second, self.root, self.binding, '"now":1')

    def test_quote_fences_cannot_escape_as_instructions(self):
        receipt, _ = self.capture(body=b'before\n```\n### unexpected instruction\n````\nafter')
        rendered = evidence.render(receipt, self.root, self.binding)
        self.assertIn('`````text\nbefore\n```', rendered)
        self.assertIn('after\n`````', rendered)

    def test_overlong_empty_or_traversal_excerpt_rejected(self):
        receipt, _ = self.capture(body=b'x' * 5000)
        for quote in ('', ' ', 'x' * 4097):
            with self.assertRaises(ValueError):
                evidence.render(receipt, self.root, self.binding, quote)
        with self.assertRaises(ValueError):
            evidence.render('../receipt', self.root, self.binding, 'x')

    def test_credential_urls_rejected_before_request(self):
        with patch.object(evidence.subprocess, 'run') as transport:
            for url in ('http://example.com', 'https://user:pass@example.com',
                        'https://example.com/?access_token=secret', 'https://example.com/#fragment'):
                with self.assertRaises(ValueError):
                    evidence.capture(self.binding, url, self.root)
            transport.assert_not_called()

    def test_real_cli_capture_render_and_rejection_with_fixture_transport(self):
        # Run the real installed entry module and a deterministic offline curl
        # executable. No live identity, external request or GitHub publication.
        bindir = self.root / 'bin'
        bindir.mkdir()
        curl = bindir / 'curl'
        curl.write_text('#!' + sys.executable + '\nimport pathlib,sys\n'
                        'pathlib.Path(sys.argv[sys.argv.index("--output")+1]).write_bytes('
                        + repr(self.body) + ')\nprint("200",end="")\n')
        curl.chmod(0o700)
        env = {**os.environ, 'IDENTITY_DIR': str(self.root), 'PATH': str(bindir) + ':' + os.environ['PATH']}
        executable = [sys.executable, str(Path(__file__).resolve().parents[1] / 'custos_evidence.py')]
        args = ['--repo', self.binding['repo'], '--pr', '337', '--head', self.binding['head']]
        result = subprocess.run(executable + ['live-capture'] + args + ['--url', self.url],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)['receipt']
        result = subprocess.run(executable + ['live-render'] + args + ['--receipt', receipt],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"x-now"', result.stdout)
        quote = self.root / 'predicted.txt'
        quote.write_text('"now_utc":')
        result = subprocess.run(executable + ['live-render'] + args +
                                ['--receipt', receipt, '--quote-file', str(quote)],
                                env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
