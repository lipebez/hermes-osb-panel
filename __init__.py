"""Hermes OSB Panel plugin.

Runtime plugin is intentionally tiny: dashboard assets and read-only API live under
``dashboard/``. Keeping this companion separate from ``open-second-brain`` avoids
upstream update conflicts.
"""
from __future__ import annotations


def register(ctx):  # pragma: no cover - exercised by Hermes plugin loader.
    """Register no runtime hooks; dashboard extension is discovered by manifest."""
    return None
