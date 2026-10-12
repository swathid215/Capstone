"""
Central path configuration for the UIDT backend.

All phases read/write through backend/data/ so outputs are not scattered
across phase folders.
"""
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
DATA_DIR = BACKEND_ROOT / "data"
SHARED_DIR = DATA_DIR / "shared"
CACHE_DIR = DATA_DIR / "cache"
RAW_DIR = DATA_DIR / "raw"

for directory in (SHARED_DIR, CACHE_DIR, RAW_DIR):
    directory.mkdir(parents=True, exist_ok=True)
