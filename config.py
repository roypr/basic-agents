import os
import sys
from pathlib import Path

_DEFAULT_DIR = "/workspace" if sys.platform != "win32" else os.path.join(os.getcwd(), "workspace")
FILES_BASE_DIR = os.environ.get("FILES_BASE_DIR", _DEFAULT_DIR)

FILES_BASE_DIR = str(Path(FILES_BASE_DIR).resolve())
