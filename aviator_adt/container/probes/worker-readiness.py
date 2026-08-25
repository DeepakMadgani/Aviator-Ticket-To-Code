#!/aviator_adt/.venv/bin/python3

"""Readiness probe for Celery Worker"""

import sys
import tempfile
from pathlib import Path

READINESS_FILE = Path(tempfile.gettempdir()) / "worker_ready"
if not READINESS_FILE.is_file():
    print("Celery readiness file NOT found.")  # noqa: T201
    sys.exit(1)
sys.exit(0)
