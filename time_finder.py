"""Standalone time finder facade module (time_finder.py).

Provides time_finder and find_time_window helper functions.
"""
from src.tools.time_finder import (
    time_finder,
    find_time_window,
    serialize_notes_input,
)

__all__ = ["time_finder", "find_time_window", "serialize_notes_input"]
