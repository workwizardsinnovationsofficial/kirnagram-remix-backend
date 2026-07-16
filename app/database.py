import importlib
import sys
from pathlib import Path

BACKEND_APP_DIR = Path(__file__).resolve().parents[1].parent / "kirnagram-backend" / "app"
if str(BACKEND_APP_DIR) not in sys.path:
    sys.path.append(str(BACKEND_APP_DIR))

_module = importlib.import_module("database")
db = _module.db
client = _module.client
