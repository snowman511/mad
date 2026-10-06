"""Launch the mad web board. Adds src/ to sys.path automatically.

  python serve.py --port 8765 --open
  python serve.py --report examples/demo_report.json --port 8765 --open
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from mad.web import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
