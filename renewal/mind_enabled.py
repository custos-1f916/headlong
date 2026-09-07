#!/usr/bin/env python3
"""Guest operator pause only; outside-guest admission bounds remain independent."""
from pathlib import Path

raise SystemExit(1 if Path('/var/lib/custos/operator-paused').exists() else 0)
