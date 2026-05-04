# utils/health.py
"""Service health tracking for monitoring external API reliability."""
import time
from collections import defaultdict

_service_stats = defaultdict(lambda: {"success": 0, "failure": 0, "last_failure": None})

def record_success(service: str):
    _service_stats[service]["success"] += 1

def record_failure(service: str, error: str = ""):
    _service_stats[service]["failure"] += 1
    _service_stats[service]["last_failure"] = time.time()
    _service_stats[service]["last_error"] = error[:200]

def get_health_report() -> dict:
    return dict(_service_stats)