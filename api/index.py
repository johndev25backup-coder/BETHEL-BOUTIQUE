import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "/var/task" not in sys.path:
    sys.path.insert(0, "/var/task")

from app import create_app

app = create_app()
