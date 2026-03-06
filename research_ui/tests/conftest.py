from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"

for candidate in (_SRC_DIR,):
    path_str = str(candidate)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
