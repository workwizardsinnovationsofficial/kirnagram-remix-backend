import time
from typing import Any

from celery_app import celery


@celery.task(name="tasks.test_task", bind=True)
def test_task(self) -> str:
    """Sample Celery task used for smoke testing worker setup.

    This is intentionally simple and production-friendly so it can be reused
    as a base pattern for future AI image-generation or background jobs.
    """
    print("Task Started...")
    time.sleep(5)
    print("Task Finished...")
    return "Hello from Celery!"


__all__ = ["test_task"]
