"""Re-export calendar helper methods from root method.py."""
from method import delete_event, delete_events, get_client

__all__ = ["delete_event", "delete_events", "get_client"]
