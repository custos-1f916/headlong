"""Construct literal text from retained JSON receipt fields; never evaluate text.

custos-evidence receipt-text --receipt receipt.json --template-file report.txt
    --field total=/estimated_total --usd total --output final.txt

The template uses {{total}} placeholders. USD fields are decimal dollars (not
cents). The sidecar records the source hash/pointers. This proves field copying,
not the truth of a supplied receipt or surrounding prose.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def render(document, template, fields, usd=()):
    values = {}
    for name, pointer in fields.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) or not pointer.startswith("/"):
            raise ValueError("fields require a name and JSON pointer")
        value = document
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        if isinstance(value, (dict, list)) or value is None or isinstance(value, bool):
            raise ValueError("receipt field must be a scalar text or number")
        if name in usd:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("USD receipt field must contain exact decimal dollars")
            value = "$" + format(amount, ".2f")
        values[name] = str(value)
    placeholders = set(re.findall(r"\{\{([A-Za-z_][A-Za-z_0-9]*)\}\}", template))
    if placeholders != set(values) or not set(usd) <= set(values):
        raise ValueError("template placeholders and receipt fields must match")
    return re.sub(r"\{\{([A-Za-z_][A-Za-z_0-9]*)\}\}", lambda m: values[m[1]], template)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--template-file", required=True)
    parser.add_argument("--field", action="append", default=[])
    parser.add_argument("--usd", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.receipt).read_bytes()
        if len(raw) > 1048576:
            raise ValueError("receipt exceeds 1 MiB")
        fields = dict(x.split("=", 1) for x in args.field)
        if len(fields) != len(args.field):
            raise ValueError("duplicate field")
        text = render(json.loads(raw, parse_float=str), Path(args.template_file).read_text(), fields, args.usd)
        if args.output:
            output = Path(args.output)
            # Refuse to overwrite the source receipt or template.
            if output.resolve() in {Path(args.receipt).resolve(), Path(args.template_file).resolve()}:
                raise ValueError("output must differ from inputs")
            provenance = {"receipt": str(Path(args.receipt).resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                          "fields": fields, "usd": args.usd, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
            for path, content in [(Path(str(output)+".receipt.json"), json.dumps(provenance)), (output, text)]:
                temp = path.with_name(path.name + "." + str(os.getpid()))
                with temp.open("x") as stream:
                    os.chmod(temp, 0o600)
                    stream.write(content)
                os.replace(temp, path)
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError, InvalidOperation) as error:
        print("receipt-text: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
