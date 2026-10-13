"""Validated strategy settings without importing credentials or database clients."""
import math
import os


def env_float(name, default, minimum=0.0, maximum=None):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}: expected finite value in [{minimum}, {maximum}]")
    return value


def env_int(name, default, minimum=0):
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"Invalid {name}: minimum {minimum}")
    return value


def env_bool(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}:
        raise ValueError(f"Invalid boolean {name}")
    return value in {'true', '1', 'yes', 'on'}
