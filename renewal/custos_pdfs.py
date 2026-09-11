"""Bounded local Signal PDF text extraction.

Untrusted PDF attachments are decoded and run through the guest-side
pdftotext binary in an isolated, rlimit-bounded subprocess. Nothing is
imported or executed from the attachment; only extracted text (plus a
durable full-text pointer) reaches the message stream.
"""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from custos_images import MAX_RAW, MAX_TOTAL_RAW

MAX_PDFS = 2
MAX_PDF_TEXT = 20000
_PDF_CPU_SECONDS = 20
_PDF_AS_BYTES = 512 * 1024 * 1024
_PDF_TIMEOUT = 20


def pdf_dir():
    root = Path(os.environ['IDENTITY_DIR']) / '.state/signal-pdfs'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _extract(raw):
    binary = shutil.which('pdftotext')
    if not binary:
        raise RuntimeError('pdftotext is not installed')
    root = pdf_dir()
    fd, name = tempfile.mkstemp(prefix='.pdf-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                       'LANG': os.environ.get('LANG', 'C.UTF-8'),
                       'LC_ALL': os.environ.get('LC_ALL', 'C.UTF-8')}
        if os.environ.get('PYTHONPATH'):
            environment['PYTHONPATH'] = os.environ['PYTHONPATH']

        def bounded():
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (_PDF_CPU_SECONDS, _PDF_CPU_SECONDS))
            if sys.platform.startswith('linux'):
                resource.setrlimit(resource.RLIMIT_AS, (_PDF_AS_BYTES, _PDF_AS_BYTES))

        result = subprocess.run([binary, name, '-'], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=_PDF_TIMEOUT,
                                cwd=root, env=environment, preexec_fn=bounded)
        if result.returncode:
            raise RuntimeError('pdftotext rejected the attachment')
        return result.stdout.decode('utf-8', 'replace')
    finally:
        Path(name).unlink(missing_ok=True)


def extract_one(encoded, name):
    if not isinstance(encoded, str) or len(encoded) > MAX_RAW * 4 // 3 + 8:
        raise ValueError('PDF exceeds transfer limit')
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_RAW:
        raise ValueError('PDF exceeds transfer limit')
    full = _extract(raw)
    text = full[:MAX_PDF_TEXT]
    digest = hashlib.sha256(raw).hexdigest()
    root = pdf_dir()
    target = root / (digest + '.txt')
    fd, tmp = tempfile.mkstemp(prefix='.pdf-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(full)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if Path(tmp).exists():
            os.unlink(tmp)
    return {'text': text, 'truncated': len(full) > MAX_PDF_TEXT,
            'sha256': digest, 'chars': len(full), 'bytes': len(full.encode('utf-8')),
            'name': name}


def extract_pdfs(pdfs):
    """Extract bounded text for bridge-admitted PDFs.

    Returns (text_blocks, notes): one '[PDF <name>']-labeled block per
    readable PDF plus honest pointer/failure lines. Full extracted text is
    persisted under pdf_dir()/<sha256>.txt so later wakes can read past the
    inline cap.
    """
    if not isinstance(pdfs, list) or len(pdfs) > MAX_PDFS + 100:
        raise ValueError('invalid PDF count')
    blocks, notes = [], []
    pdfs = list(pdfs)
    if len(pdfs) > MAX_PDFS:
        notes.append('[%d more PDF attachment(s) not read.]' % (len(pdfs) - MAX_PDFS))
        pdfs = pdfs[:MAX_PDFS]
    total = 0
    for attachment in pdfs:
        name = attachment.get('id') if isinstance(attachment.get('id'), str) else 'attachment'
        declared = attachment.get('size')
        if type(declared) is int and total + declared > MAX_TOTAL_RAW:
            notes.append("[PDF '%s'] could not be read: attachment transfer limit." % name)
            continue
        try:
            raw = base64.b64decode(attachment['encoded'], validate=True)
        except (KeyError, TypeError, ValueError):
            notes.append("[PDF '%s'] could not be read." % name)
            continue
        if type(declared) is int and total + len(raw) > MAX_TOTAL_RAW:
            notes.append("[PDF '%s'] could not be read: attachment transfer limit." % name)
            continue
        total += len(raw)
        try:
            info = extract_one(attachment['encoded'], name)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
            notes.append("[PDF '%s'] could not be read." % name)
            continue
        block = "[PDF '%s']\n" % name
        if info['text'].strip():
            block += info['text'].rstrip()
            if info['truncated']:
                block += '\n[... PDF text truncated at %d chars]' % MAX_PDF_TEXT
        else:
            block += '[no extractable text: image-only or non-text PDF]'
        blocks.append(block)
        notes.append("[PDF '%s'] full text: %s.txt, %d bytes."
                     % (name, info['sha256'], info['bytes']))
    return blocks, notes
