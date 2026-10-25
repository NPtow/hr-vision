from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
for path in (ROOT/'interface-lab/server',ROOT/'services/data-core/src',ROOT/'services/data-core/tests',ROOT/'services/transcription-source/src'):
    sys.path.insert(0,str(path))
from test_core import env as core_env
