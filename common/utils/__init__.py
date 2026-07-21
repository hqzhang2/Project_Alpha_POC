#!/usr/bin/env python3
"""
Common Utilities
Shared utilities for logging, health checks, encoding, etc.
"""
import json
import logging
import math
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional


class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types, NaN, inf, datetime."""

    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return None if math.isnan(obj) or math.isinf(obj) else float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        if isinstance(obj, (pd.Timestamp, pd.Timedelta)):
            return str(obj)
        return super().default(obj)


def clean_dict(d: dict) -> dict:
    """Recursively clean dict of NaN/inf values."""
    if not isinstance(d, dict):
        return d

    result = {}
    for k, v in d.items():
        if isinstance(v, float):
            result[k] = None if math.isnan(v) or math.isinf(v) else v
        elif isinstance(v, list):
            result[k] = [None if isinstance(x, float) and (math.isnan(x) or math.isinf(x)) else x for x in v]
        elif isinstance(v, dict):
            result[k] = clean_dict(v)
        else:
            result[k] = v
    return result


def setup_logger(name: str, log_dir: str = "logs", level: str = "INFO",
                 max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5) -> logging.Logger:
    """
    Set up structured logging with rotation.
    Returns a configured logger instance.
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation
    log_file = os.path.join(log_dir, f"{name}.log")
    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get logger by name, creating if needed."""
    return logging.getLogger(name)


class TTLCache:
    """Thread-safe TTL cache."""

    def __init__(self, ttl_seconds: int = 300):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, expiry = self._cache[key]
            if time.time() < expiry:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: Any):
        self._cache[key] = (value, time.time() + self._ttl)

    def clear(self):
        self._cache.clear()


class HealthCheck:
    """Simple health check tracker."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.start_time = time.time()
        self.checks = {}

    def add_check(self, name: str, status: bool, details: str = ""):
        self.checks[name] = {"status": "ok" if status else "fail", "details": details}

    def is_healthy(self) -> bool:
        return all(c["status"] == "ok" for c in self.checks.values())

    def to_dict(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "service": self.service_name,
            "status": "healthy" if self.is_healthy() else "unhealthy",
            "uptime_seconds": round(uptime, 2),
            "checks": self.checks
        }


# Import time here to avoid circular imports
import time
import numpy as np
import pandas as pd