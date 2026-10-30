"""Isolated, loopback-only demo host; never imports app.main or server settings."""
from pathlib import Path
import sys

_API_DIR = Path(__file__).resolve().parent.parent / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

