import os
from pathlib import Path

FILES_BASE_DIR = os.environ.get("FILES_BASE_DIR", "/workspace")
FILES_BASE_DIR = str(Path(FILES_BASE_DIR).resolve())
