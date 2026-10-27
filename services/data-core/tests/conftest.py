from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "recruiting-api"))
sys.path.insert(0, str(ROOT / "services" / "data-core" / "src"))
