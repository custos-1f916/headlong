"""Bounded local Signal image storage and inline-only vision inputs.

Pillow is imported only in the guest's isolated decoder subprocess. Gateway
validation is stdlib-only and never opens a URL or guest-specified file path.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

MAX_IMAGES = 4
MAX_RAW = 8 * 1024 * 1024
MAX_TOTAL_RAW = 16 * 1024 * 1024
MAX_JPEG = 512 * 1024
MAX_EDGE = 1600
MAX_UPLOAD = 24 * 1024 * 1024


def jpeg_size(data):
    if not 4 <= len(data) <= MAX_JPEG or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('invalid bounded JPEG')
    at = 2
    while at + 4 <= len(data):
        if data[at] != 255:
            break
        while at < len(data) and data[at] == 255:
            at += 1
        if at + 3 > len(data):
            break
        marker = data[at]; at += 1
        length = int.from_bytes(data[at:at+2], 'big')
        if length < 2 or at + length > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2):
            if length < 8 or data[at+2] != 8:
                break
            height = int.from_bytes(data[at+3:at+5], 'big')
            width = int.from_bytes(data[at+5:at+7], 'big')
            if not 1 <= width <= MAX_EDGE or not 1 <= height <= MAX_EDGE:
                break
            return width, height
        if marker in (0xda, 0xd9):
            break
        at += length
    raise ValueError('unsupported JPEG dimensions')


def inline_jpeg(url):
    prefix = 'data:image/jpeg;base64,'
    if not isinstance(url, str) or not url.startswith(prefix) or len(url) > MAX_JPEG * 4 // 3 + 64:
        raise ValueError('only bounded embedded JPEG images are admitted')
    data = base64.b64decode(url[len(prefix):], validate=True)
    jpeg_size(data)
    return data


def validate_refs(refs):
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_IMAGES:
        raise ValueError('invalid image count')
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'id','mime','size','width','height'} or
                not isinstance(ref['id'],str) or not re.fullmatch('[0-9a-f]{64}',ref['id']) or
                ref['mime'] != 'image/jpeg' or type(ref['size']) is not int or not 1 <= ref['size'] <= MAX_JPEG or
                any(type(ref[k]) is not int or not 1 <= ref[k] <= MAX_EDGE for k in ('width','height'))):
            raise ValueError('invalid image reference')
    return refs


def image_dir():
    return Path(os.environ['IDENTITY_DIR']) / '.state/signal-images'


def decode_image(raw):
    # A separate bounded process decodes untrusted images inside Custos's home.
    result = subprocess.run([sys.executable, __file__, '--decode'], input=raw,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    if result.returncode:
        raise ValueError('image could not be decoded within limits')
    jpeg_size(result.stdout)
    return result.stdout


def import_images(images, directory=None):
    if not isinstance(images,list) or not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError('invalid image count')
    root = Path(directory) if directory else image_dir()
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    refs, errors, total = [], [], 0
    for encoded in images:
        try:
            if not isinstance(encoded,str) or len(encoded) > MAX_RAW * 4 // 3 + 8:
                raise ValueError('image exceeds transfer limit')
            raw = base64.b64decode(encoded,validate=True)
            total += len(raw)
            if len(raw) > MAX_RAW or total > MAX_TOTAL_RAW:
                raise ValueError('image exceeds transfer limit')
            data = decode_image(raw)
            digest = hashlib.sha256(data).hexdigest()
            target = root / (digest + '.jpg')
            fd, name = tempfile.mkstemp(prefix='.image-',dir=root)
            try:
                with os.fdopen(fd,'wb') as f:
                    f.write(data); f.flush(); os.fsync(f.fileno())
                os.replace(name,target)
            finally:
                if os.path.exists(name): os.unlink(name)
            width,height = jpeg_size(data)
            refs.append({'id':digest,'mime':'image/jpeg','size':len(data),'width':width,'height':height})
        except (ValueError, OSError, subprocess.TimeoutExpired):
            errors.append('An attached image could not be read or exceeded the image limits.')
    fd=os.open(root,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return refs,errors


def attach_images(messages, refs, directory=None):
    validate_refs(refs)
    root = Path(directory) if directory else image_dir()
    parts=[]
    for ref in refs:
        fd=os.open(root/(ref['id']+'.jpg'),os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as f: data=f.read(MAX_JPEG+1)
        if len(data)!=ref['size'] or hashlib.sha256(data).hexdigest()!=ref['id'] or jpeg_size(data)!=(ref['width'],ref['height']):
            raise ValueError('stored image does not match its durable reference')
        parts.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(data).decode()}})
    result=[dict(m) for m in messages]
    if not result or result[-1]['role']!='user' or not isinstance(result[-1]['content'],str):
        raise ValueError('images require the current user message')
    result[-1]['content']=[{'type':'text','text':result[-1]['content']},*parts]
    return result


def _decoder():
    import resource
    import warnings
    resource.setrlimit(resource.RLIMIT_CPU,(15,15))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS=25_000_000
    warnings.simplefilter('error',Image.DecompressionBombWarning)
    raw=sys.stdin.buffer.read(MAX_RAW+1)
    if len(raw)>MAX_RAW: raise ValueError('oversize image')
    with Image.open(io.BytesIO(raw)) as original:
        if original.format not in {'JPEG','PNG','WEBP','GIF'}:
            raise ValueError('unsupported image format')
        original.seek(0)
        frame=ImageOps.exif_transpose(original)
        frame.thumbnail((MAX_EDGE,MAX_EDGE))
        if frame.mode in ('RGBA','LA') or 'transparency' in frame.info:
            rgba=frame.convert('RGBA'); background=Image.new('RGBA',rgba.size,'white')
            background.alpha_composite(rgba); frame=background.convert('RGB')
        else: frame=frame.convert('RGB')
        # Metadata is deliberately omitted, including EXIF/GPS. Animated images
        # contribute their first frame; no video/audio processing is enabled.
        for quality in (85,70,55):
            out=io.BytesIO(); frame.save(out,format='JPEG',quality=quality,optimize=True)
            if len(out.getvalue())<=MAX_JPEG:
                sys.stdout.buffer.write(out.getvalue()); return
        raise ValueError('image cannot fit normalized limit')


if __name__=='__main__':
    try: _decoder()
    except Exception: raise SystemExit(1)
