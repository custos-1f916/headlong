"""Bounded outbound files. Only the guest opens paths; the host accepts bytes.

signal-cli 0.14.7 supports RFC2397 data URIs with a filename parameter. Files
are opaque here: no image/document decoder or archive extraction runs as root.
"""
import base64
import binascii
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote

MAX_FILES = 4
MAX_BYTES = 8 * 1024 * 1024  # aggregate raw bytes per message
MAX_WIRE = ((MAX_BYTES + 2) // 3) * 4 + 65536
MAX_QUEUED_BYTES = 64 * 1024 * 1024


def validate(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('attachments must contain 1..4 files')
    result, total = [], 0
    for f in files:
        if not isinstance(f, dict) or set(f) != {'filename', 'content_type', 'data'}:
            raise ValueError('attachment needs filename, content_type and base64 data; no paths or URLs')
        name, mime, data = f['filename'], f['content_type'], f['data']
        if (not isinstance(name, str) or not 1 <= len(name.encode()) <= 180 or
                name in {'.', '..'} or any(c in name for c in '/\\') or
                any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError('attachment filename must be a plain name (max 180 UTF-8 bytes)')
        if not isinstance(mime, str) or not re.fullmatch(r'[a-z0-9][a-z0-9!#$&^_.+-]{0,99}/[a-z0-9][a-z0-9!#$&^_.+-]{0,99}', mime):
            raise ValueError('invalid attachment content_type')
        if not isinstance(data, str) or len(data) > ((MAX_BYTES + 2) // 3) * 4:
            raise ValueError('attachment exceeds 8 MiB')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('attachment data must be base64') from None
        total += len(raw)
        if not raw or total > MAX_BYTES:
            raise ValueError('attachments must be nonempty and total at most 8 MiB')
        result.append({'filename': name, 'content_type': mime,
                       'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                       'data': base64.b64encode(raw).decode('ascii')})
    return result


def metadata(files):
    return [{k: v for k, v in f.items() if k != 'data'} for f in files]


def read_files(paths):
    if not 1 <= len(paths) <= MAX_FILES:
        raise ValueError('attach 1..4 files')
    files, total = [], 0
    for path in paths:
        path = Path(path).expanduser()
        # O_NONBLOCK avoids hanging on FIFOs; fstat checks the actual opened file.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('attachments must be regular files')
            if info.st_size > MAX_BYTES - total:
                raise ValueError('attachments total at most 8 MiB')
            raw = source.read(MAX_BYTES - total + 1)
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('attachments total at most 8 MiB')
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        files.append({'filename': path.name, 'content_type': mime,
                      'data': base64.b64encode(raw).decode('ascii')})
    return [{k: f[k] for k in ('filename', 'content_type', 'data')} for f in validate(files)]


def data_uris(files):
    return ['data:%s;filename=%s;base64,%s' %
            (f['content_type'], quote(f['filename'], safe=''), f['data']) for f in files]
