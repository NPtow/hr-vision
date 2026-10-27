#!/usr/bin/env python3
"""Reuse the tested board audit with this batch's scope and node namespace."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('board_audit', ROOT / 'scripts/audit-school-boards.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
audit.PREFIX = 'segment-review-20261014-'
audit.DEFAULT_RUN = ROOT / 'output/segments-2026-10-14'
if __name__ == '__main__':
    audit.main()
