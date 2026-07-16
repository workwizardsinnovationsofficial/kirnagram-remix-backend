import os
from dotenv import load_dotenv
import razorpay

BASE_DIR = os.path.dirname(__file__)
ENV_PATH = os.path.join(BASE_DIR, ".env")
# Force-load backend .env and override stale shell vars.
load_dotenv(dotenv_path=ENV_PATH, override=True)


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "kirnagram")
APP_ENV = os.getenv("APP_ENV", "dev")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_FALLBACK_MODE = os.getenv("GEMINI_FALLBACK_MODE")

def _get_key_preview(key: str, show_chars: int = 4) -> str:
    """Return key preview (last N chars) without exposing full key."""
    if not key:
        return "NOT SET"
    if len(key) <= show_chars:
        return f"(length={len(key)})"
    return f"***{key[-show_chars:]}"


def validate_gemini_api_key() -> dict:
    """Validate Gemini API key configuration at startup."""
    import google.generativeai as genai
    from google.api_core.exceptions import InvalidArgument, Unauthenticated
    
    result = {
        "valid": False,
        "key_loaded": False,
        "key_preview": "NOT SET",
        "error": None
    }
    
    # Check if key exists
    if not GEMINI_API_KEY:
        result["error"] = "GEMINI_API_KEY not set in environment"
        return result
    
    result["key_loaded"] = True
    result["key_preview"] = _get_key_preview(GEMINI_API_KEY)
    
    # Try to configure and validate
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Try to list available models as a validation test
        models = genai.list_models()
        # If we got here, API key is valid
        result["valid"] = True
        return result
    except (InvalidArgument, Unauthenticated) as e:
        result["error"] = f"API key is invalid or unauthorized: {str(e)}"
        return result
    except Exception as e:
        result["error"] = f"Failed to validate API key: {str(e)}"
        return result


def print_startup_info():
    """Print startup configuration information."""
    print(f"🔐 [CONFIG] GEMINI_API_KEY: {_get_key_preview(GEMINI_API_KEY)}")
    print(f"🔐 [CONFIG] GEMINI_IMAGE_MODEL: {GEMINI_IMAGE_MODEL}")
    print(f"🔐 [CONFIG] GEMINI_FALLBACK_MODE: {GEMINI_FALLBACK_MODE or 'error'}")
    print(f"🔐 [CONFIG] OPENAI_API_KEY: {_get_key_preview(OPENAI_API_KEY)}")
    print()
    # Validate Gemini key
    validation = validate_gemini_api_key()
    if validation["valid"]:
        print(f"✅ [VALIDATION] Gemini API key is VALID (last chars: {validation['key_preview']})")
    elif validation["key_loaded"]:
        print(f"⚠️  [VALIDATION] Gemini API key loaded but INVALID")
        print(f"    Error: {validation['error']}")
    else:
        print(f"❌ [VALIDATION] Gemini API key NOT LOADED")
        print(f"    Error: {validation['error']}")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY")
CHATGPT_API_KEY = OPENAI_API_KEY
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")

# OTP + SMS configuration
FAST2SMS_API_KEY = (os.getenv("FAST2SMS_API_KEY") or "").strip()
FAST2SMS_ROUTE = (os.getenv("FAST2SMS_ROUTE", "q") or "q").strip()
FAST2SMS_SENDER_ID = (os.getenv("FAST2SMS_SENDER_ID") or "").strip()
OTP_HASH_SECRET = (os.getenv("OTP_HASH_SECRET", "change-me-in-env") or "change-me-in-env").strip()
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "3"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "30"))
MOBILE_VERIFICATION_WINDOW_MINUTES = int(os.getenv("MOBILE_VERIFICATION_WINDOW_MINUTES", "15"))
OTP_DEV_FALLBACK_ENABLED = _as_bool(os.getenv("OTP_DEV_FALLBACK_ENABLED"), default=True)

# Email OTP configuration
SMTP_EMAIL = (os.getenv("SMTP_EMAIL") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
EMAIL_OTP_EXPIRY_MINUTES = int(os.getenv("EMAIL_OTP_EXPIRY_MINUTES", "5"))
EMAIL_OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("EMAIL_OTP_RESEND_COOLDOWN_SECONDS", "30"))

# ✅ Razorpay
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

razorpay_client = razorpay.Client(auth=(
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET
))