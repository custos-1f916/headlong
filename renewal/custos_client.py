#!/usr/bin/env python3
"""One operator message: durable native goal first, then wait for its reply."""
import argparse
import json
import sys
import time
import uuid
from custos_transport import send, poll


def main():
    text = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read(32769)
    request_id = 'operator:' + str(uuid.uuid4())
    args = argparse.Namespace(request_id=request_id, sender='hal', authority='operator',
                              source_url='operator-cli:' + request_id)
    try:
        receipt = send(args, text)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = poll(args)
            if response['replies']:
                print(response['replies'][0]['content'])
                return 0
            if response.get('decision') == 'no-reply':
                return 0
            time.sleep(2)
        print('Saved as goal ' + receipt['goal_id'] + '. Reply pending; inference may be busy, paused, or still reasoning.')
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        print('custos: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
