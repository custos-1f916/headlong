import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import custos_images as ci
import custos_gateway as cg


def png(size=(80,60)):
    out=io.BytesIO(); Image.new('RGB',size,'red').save(out,format='PNG'); return out.getvalue()


class ImageTests(unittest.TestCase):
    def test_decode_resize_strip_metadata_and_attach(self):
        with tempfile.TemporaryDirectory() as root:
            refs,errors=ci.import_images([base64.b64encode(png((2000,1000))).decode()],root)
            self.assertEqual(errors,[])
            self.assertEqual((refs[0]['width'],refs[0]['height']),(1600,800))
            msgs=ci.attach_images([{'role':'user','content':'What is shown?'}],refs,root)
            url=msgs[0]['content'][1]['image_url']['url']
            jpeg=ci.inline_jpeg(url)
            self.assertEqual(len(jpeg),refs[0]['size'])
            self.assertNotIn('base64',json.dumps(refs))
            with Image.open(io.BytesIO(jpeg)) as image:
                self.assertEqual(dict(image.getexif()),{})
            forwarded=json.loads(cg.payload(json.dumps({'model':cg.MODEL,'messages':msgs}).encode(),65536))
            self.assertEqual(forwarded['messages'],msgs)

    def test_corrupt_and_unsupported_images_are_honest_failures(self):
        with tempfile.TemporaryDirectory() as root:
            for raw in (b'not an image',b'<svg><script/></svg>'):
                refs,errors=ci.import_images([base64.b64encode(raw).decode()],root)
                self.assertEqual(refs,[]); self.assertEqual(len(errors),1)

    def test_refs_cannot_escape_store_or_substitute_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            refs,_=ci.import_images([base64.b64encode(png()).decode()],root)
            with self.assertRaises(ValueError): ci.validate_refs([{**refs[0],'id':'../../etc/passwd'}])
            path=Path(root)/(refs[0]['id']+'.jpg'); path.write_bytes(b'changed')
            with self.assertRaises(ValueError): ci.attach_images([{'role':'user','content':'look'}],refs,root)
            path.unlink(); path.symlink_to('/etc/passwd')
            with self.assertRaises(OSError): ci.attach_images([{'role':'user','content':'look'}],refs,root)

    def test_remote_url_nonimage_and_oversize_inputs_rejected(self):
        for url in ('https://example.com/photo.jpg','file:///etc/passwd',
                    'data:image/svg+xml;base64,PHN2Zy8+', 'data:image/jpeg;base64,invalid'):
            with self.assertRaises(ValueError): ci.inline_jpeg(url)
        with tempfile.TemporaryDirectory() as root:
            refs,_=ci.import_images([base64.b64encode(png()).decode()],root)
            msg=ci.attach_images([{'role':'user','content':'look'}],refs,root)[0]
            with self.assertRaises(cg.Denied):
                cg.payload(json.dumps({'model':cg.MODEL,'messages':[msg]*5}).encode(),65536)
            with self.assertRaises(cg.Denied):
                cg.payload(json.dumps({'model':cg.MODEL,'messages':[{**msg,'role':'system'}]}).encode(),65536)
            with self.assertRaises(ValueError): ci.import_images(['AA==']*5,root)

    def test_text_budget_remains_bounded_despite_larger_media_envelope(self):
        with self.assertRaises(cg.Denied):
            cg.payload(json.dumps({'model':cg.MODEL,'messages':[{'role':'user','content':'x'*262145}]}).encode(),65536)
        # 256 KiB of text is admitted (the mind's context cap is 192 KiB since 2026-09-09).
        cg.payload(json.dumps({'model':cg.MODEL,'messages':[{'role':'user','content':'x'*200000}]}).encode(),65536)


if __name__=='__main__': unittest.main()
