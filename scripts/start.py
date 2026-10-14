#!/usr/bin/env python3
"""Start the no-key demo from any working directory; no dependencies needed."""
from pathlib import Path
import os
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "server.py"), run_name="__main__")
