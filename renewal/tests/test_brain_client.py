"""The honest model label: names resolve through the brain router's mode."""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_brain_client as bc


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def opener_for(doc):
    def opener(url, timeout=0):
        if doc is None: raise OSError("connection refused")
        return FakeResponse(json.dumps(doc).encode())
    return opener


class BrainClientTests(unittest.TestCase):
    def setUp(self): bc._cache.clear()

    def test_local_mode_names_qwen_for_every_cloud_name(self):
        st = bc.brain_status("http://x", opener=opener_for({"mode": "local", "local_model": "qwen3.8-27b"}))
        self.assertEqual(bc.resolve_model("gpt-6-astra", status=st), "qwen3.8-27b")
        self.assertEqual(bc.resolve_model("gpt-5.6-terra", status=st), "qwen3.8-27b")

    def test_cloud_mode_keeps_the_name(self):
        st = bc.brain_status("http://x", opener=opener_for({"mode": "cloud", "local_model": "qwen3.8-27b"}))
        self.assertEqual(bc.resolve_model("gpt-6-astra", status=st), "gpt-6-astra")

    def test_unreachable_or_malformed_router_keeps_the_name(self):
        self.assertIsNone(bc.brain_status("http://x", opener=opener_for(None)))
        self.assertIsNone(bc.brain_status("http://x", opener=opener_for({"mode": "weird"})))
        self.assertEqual(bc.resolve_model("gpt-6-astra", url="http://x", opener=opener_for(None)), "gpt-6-astra")
        self.assertEqual(bc.resolve_model("", status={"mode": "local", "local_model": "q"}), "")

    def test_url_derivation(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"CUSTOS_INFERENCE_URL": "http://192.168.86.69:18080/v1/"}):
            self.assertEqual(bc.brain_url(), "http://192.168.86.69:18080")
        with mock.patch.dict(os.environ, {"CUSTOS_INFERENCE_URL": "", "LLM_BASE_URL": ""}):
            self.assertEqual(bc.brain_url(), bc.DEFAULT_URL)

    def test_cli_prints_one_resolved_name_per_argument(self):
        from unittest import mock
        out = io.StringIO()
        with mock.patch.object(bc, "brain_status", return_value={"mode": "local", "local_model": "qwen3.8-27b"}), mock.patch("sys.stdout", out):
            self.assertEqual(bc.main(["gpt-6-astra", "gpt-5.6-terra"]), 0)
        self.assertEqual(out.getvalue().split(), ["qwen3.8-27b", "qwen3.8-27b"])


if __name__ == "__main__":
    unittest.main()
