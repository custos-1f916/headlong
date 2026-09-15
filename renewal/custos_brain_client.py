"""Ask the brain router which model actually answers, so every step carries an honest label.

The guest's environment names the cloud models (THINK_MODEL=gpt-6-astra, MONOLITH_REPLY_MODEL=
gpt-5.6-terra). In local mode the router sends every name to johan's Qwen, but the trajectory
recorded whatever name the client asked for — Hal, 2026-09-13: "The monolith steps and other things
still say gpt-6-astra ... make the names dynamic so that when the toggle from cloud to johan
happens, the label stays honest." `resolve_model(name)` returns the router's `effective_model`
while it is in local mode. That is normally Johan's Qwen and becomes the configured OpenRouter
model only while the local observer is unavailable. If the router cannot be reached in two seconds
the name is returned unchanged (the call would fail anyway, and a wrong label is worse than none).
"""
import json
import os
import sys
import urllib.request

DEFAULT_URL = "http://192.168.86.69:18080"
_cache = {}


def brain_url():
    return (os.environ.get("CUSTOS_INFERENCE_URL") or os.environ.get("LLM_BASE_URL") or DEFAULT_URL).rstrip("/").removesuffix("/v1")


def brain_status(url=None, timeout=2.0, opener=urllib.request.urlopen):
    """The router's /brain document, or None when it cannot be read."""
    target = (url or brain_url()) + "/brain"
    try:
        with opener(target, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return payload if isinstance(payload, dict) and payload.get("mode") in ("cloud", "local") else None
    except Exception:  # noqa: BLE001 - unreachable, refused, malformed: all mean "no information"
        return None


def resolve_model(name, status=None, url=None, timeout=2.0, opener=urllib.request.urlopen):
    """The model that will really answer a request for `name`."""
    if not name:
        return name
    if status is None:
        key = url or brain_url()
        status = _cache.get(key) if _cache.get(key) is not None else brain_status(url, timeout, opener)
        _cache[key] = status
    if status and status.get("mode") == "local":
        if status.get("effective_model"):
            return status["effective_model"]
        if status.get("local_model"):
            return status["local_model"]
    return name


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--status":
        status = brain_status()
        print(json.dumps(status) if status else "{}")
        return 0
    if not argv:
        print("usage: brain-model NAME [NAME...]   (prints each name as the brain router will serve it)\n"
              "       brain-model --status         (the router's /brain document, {} if unreachable)", file=sys.stderr)
        return 2
    status = brain_status()
    for name in argv:
        print(resolve_model(name, status=status or {"mode": "unknown"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
