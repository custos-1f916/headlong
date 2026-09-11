import base64
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_pdfs as cp
import custos_transport as ct

MARKER = 'CUSTOS_PDF_PROOF_20260911'


def minimal_pdf(marker):
    content = ('BT /F1 24 Tf 72 720 Td (%s) Tj ET' % marker).encode() + b'\n'
    objects = [
        b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n',
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
        b'/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n',
        b'4 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n' % (len(content), content),
        b'5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n',
    ]
    out = b'%PDF-1.4\n'
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    xref = len(out)
    out += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    return out + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % xref


class PdfExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {'IDENTITY_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = cp.pdf_dir()
        self.real_run = cp.subprocess.run
        self.run_patcher = mock.patch.object(cp.subprocess, 'run')
        self.run_mock = self.run_patcher.start()
        self.addCleanup(self.run_patcher.stop)
        self.which_patcher = mock.patch.object(cp.shutil, 'which',
                                               return_value='/usr/bin/pdftotext')
        self.which_mock = self.which_patcher.start()
        self.addCleanup(self.which_patcher.stop)

    def test_extract_one_bounded_and_persisted(self):
        raw = minimal_pdf(MARKER)
        self.run_mock.return_value = mock.Mock(returncode=0,
                                               stdout=b'MARKER line\n', stderr=b'')
        info = cp.extract_one(base64.b64encode(raw).decode(), 'paper.pdf')
        self.assertEqual(info['text'], 'MARKER line\n')
        self.assertFalse(info['truncated'])
        self.assertEqual(info['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.root / (info['sha256'] + '.txt')).read_text(),
                         'MARKER line\n')
        self.assertEqual([p for p in self.root.iterdir()
                           if p.name.startswith('.pdf-')], [])
        args = self.run_mock.call_args[0][0]
        self.assertEqual(args[0], '/usr/bin/pdftotext')
        self.assertEqual(args[-1], '-')

    def test_base64_is_validated(self):
        with self.assertRaises(ValueError):
            cp.extract_one('!!!not-base64!!!', 'x.pdf')

    def test_oversize_rejected(self):
        big = base64.b64encode(b'x' * (cp.MAX_RAW + 1)).decode()
        with self.assertRaises(ValueError):
            cp.extract_one(big, 'x.pdf')

    def test_nonzero_rc_is_honest_error(self):
        raw = minimal_pdf(MARKER)
        self.run_mock.return_value = mock.Mock(returncode=1, stdout=b'',
                                               stderr=b'boom')
        with self.assertRaises(RuntimeError):
            cp.extract_one(base64.b64encode(raw).decode(), 'x.pdf')

    def test_text_capped_but_full_text_persisted(self):
        raw = minimal_pdf(MARKER)
        full = 'a' * (cp.MAX_PDF_TEXT + 5)
        self.run_mock.return_value = mock.Mock(returncode=0,
                                               stdout=full.encode(), stderr=b'')
        info = cp.extract_one(base64.b64encode(raw).decode(), 'x.pdf')
        self.assertEqual(len(info['text']), cp.MAX_PDF_TEXT)
        self.assertTrue(info['truncated'])
        self.assertEqual((self.root / (info['sha256'] + '.txt')).read_text(), full)


class PdfRealBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {'IDENTITY_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_real_pdftotext_extracts_the_marker(self):
        if not shutil.which('pdftotext'):
            self.skipTest('pdftotext not installed')
        raw = minimal_pdf(MARKER)
        info = cp.extract_one(base64.b64encode(raw).decode(), 'real.pdf')
        self.assertIn(MARKER, info['text'])
        self.assertFalse(info['truncated'])


class PdfBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {'IDENTITY_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = cp.pdf_dir()
        self.real_run = cp.subprocess.run
        self.run_patcher = mock.patch.object(cp.subprocess, 'run')
        self.run_mock = self.run_patcher.start()
        self.addCleanup(self.run_patcher.stop)
        self.which_patcher = mock.patch.object(cp.shutil, 'which',
                                               return_value='/usr/bin/pdftotext')
        self.which_mock = self.which_patcher.start()
        self.addCleanup(self.which_patcher.stop)

    def test_empty_is_a_noop(self):
        self.assertEqual(cp.extract_pdfs([]), ([], []))

    def test_over_the_cap_is_bounded_with_note(self):
        raw = minimal_pdf(MARKER)
        encoded = base64.b64encode(raw).decode()
        self.run_mock.return_value = mock.Mock(returncode=0, stdout=b'M',
                                               stderr=b'')
        entries = [{'id': 'p%d.pdf' % i, 'size': len(raw), 'encoded': encoded}
                   for i in range(4)]
        blocks, notes = cp.extract_pdfs(entries)
        self.assertEqual(len(blocks), cp.MAX_PDFS)
        self.assertEqual(notes[0], '[2 more PDF attachment(s) not read.]')
        self.assertEqual(len(notes), 3)

    def test_mixed_ok_and_fail(self):
        raw = minimal_pdf(MARKER)
        self.run_mock.return_value = mock.Mock(returncode=0, stdout=b'M',
                                               stderr=b'')
        blocks, notes = cp.extract_pdfs([
            {'id': 'good.pdf', 'size': len(raw),
             'encoded': base64.b64encode(raw).decode()},
            {'id': 'bad.pdf', 'size': 10, 'encoded': '!!'},
        ])
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].startswith("[PDF 'good.pdf']"))
        self.assertEqual(notes[0],
                         "[PDF 'good.pdf'] full text: %s.txt, 1 bytes."
                         % hashlib.sha256(raw).hexdigest())
        self.assertEqual(notes[1], "[PDF 'bad.pdf'] could not be read.")


class TransportMediaTests(unittest.TestCase):
    def test_pdfs_envelope_folds_text_after_original(self):
        raw = minimal_pdf(MARKER)
        media = {'content': 'original', 'images': [],
                 'pdfs': [{'id': 'p.pdf', 'size': len(raw),
                           'encoded': base64.b64encode(raw).decode()}]}
        with mock.patch.object(ct, 'extract_pdfs',
                               return_value=(["[PDF 'p.pdf']\nM"], ['pointer'])), \
             mock.patch.object(ct, 'import_images', return_value=([], [])):
            content, refs, errors = ct.unfold_media(media)
        self.assertEqual(content, "original\n[PDF 'p.pdf']\nM\npointer")
        self.assertEqual((refs, errors), ([], []))

    def test_old_envelope_is_unchanged(self):
        b64 = base64.b64encode(b'x').decode()
        with mock.patch.object(ct, 'import_images',
                               return_value=([{'id': 'ref'}], [])):
            content, refs, errors = ct.unfold_media(
                {'content': 'c', 'images': [b64]})
        self.assertEqual(content, 'c')
        self.assertEqual(refs, [{'id': 'ref'}])

    def test_invalid_envelopes_rejected(self):
        for media in ({'content': 'c'},
                      {'content': 1, 'images': [], 'pdfs': []},
                      {'content': 'c', 'images': [], 'pdfs': [], 'extra': 1}):
            with self.assertRaises(ValueError):
                ct.unfold_media(media)

    def test_fit_content_truncates_with_marker(self):
        fitted = ct.fit_content('a' * 40000)
        self.assertLessEqual(len(fitted.encode()), 32768)
        self.assertTrue(fitted.endswith('[... PDF text truncated]'))
        self.assertEqual(ct.fit_content('short'), 'short')

    def test_multibyte_boundary_is_not_split(self):
        fitted = ct.fit_content('\u00e9' * 20000)
        self.assertLessEqual(len(fitted.encode()), 32768)
        self.assertTrue(fitted.startswith('\u00e9'))
        self.assertNotIn('\ufffd', fitted)
