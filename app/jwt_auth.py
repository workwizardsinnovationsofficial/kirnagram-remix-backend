import importlib
import sys
from pathlib import Path

# Allow the remix backend to reuse the shared JWT helper from the main backend.
BACKEND_APP_DIR = Path(__file__).resolve().parents[1].parent / "kirnagram-backend" / "app"
if str(BACKEND_APP_DIR) not in sys.path:
    sys.path.append(str(BACKEND_APP_DIR))

_module = importlib.import_module("jwt_auth")

create_access_token = _module.create_access_token
create_refresh_token = _module.create_refresh_token
create_session_tokens = _module.create_session_tokens
get_user_id_from_authorization_header = _module.get_user_id_from_authorization_header
get_user_id_from_token = _module.get_user_id_from_token
verify_access_token = _module.verify_access_token
verify_token = _module.verify_token
