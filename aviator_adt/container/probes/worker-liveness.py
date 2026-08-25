#!/aviator_adt/.venv/bin/python3

"""Liveness probe for Celery Worker"""

import sys
import tempfile
import time
from pathlib import Path

LIVENESS_FILE = Path(tempfile.gettempdir()) / "worker_heartbeat"
if not LIVENESS_FILE.is_file():
    print("Celery liveness file NOT found.")  # noqa: T201
    sys.exit(1)

stats = LIVENESS_FILE.stat()
heartbeat_timestamp = stats.st_mtime
current_timestamp = time.time()
time_diff = current_timestamp - heartbeat_timestamp
if time_diff > 60:
    print("Celery Worker liveness file timestamp DOES NOT matches the given constraint.")  # noqa: T201
    sys.exit(1)

print("Celery Worker liveness file found and timestamp matches the given constraint.")  # noqa: T201
sys.exit(0)
