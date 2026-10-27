"""Run the local prototype with credentials supplied through its environment."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
os.execve(sys.executable, [sys.executable, str(root / 'server.py')], os.environ.copy())
