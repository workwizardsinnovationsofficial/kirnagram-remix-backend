import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# Load environment variables from the project .env file when present.
load_dotenv(dotenv_path=ENV_FILE, override=True)


BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", BROKER_URL)


# Create the Celery application instance.
# The name is kept explicit so the app is easy to identify in worker logs.
celery = Celery(
    "remix_backend",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[],
)


# Production-friendly task serialization and timezone settings.
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
)


__all__ = ["celery"]
