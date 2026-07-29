#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} URL", file=sys.stderr)
        return 2

    url = sys.argv[1]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    print(f"ready: {url}")
                    return 0
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)

    print(f"timed out waiting for {url}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

