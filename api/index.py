
import os
import sys

# Add the project root (parent of the api folder) to Python's path.
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app

app = create_app()


if __name__ == "__main__":
    app.run()
