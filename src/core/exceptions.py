class CalendarError(Exception):
        """Base exception for all calendar errors."""
        pass



class CalendarAuthError(CalendarError):
    """Raised when OAuth credentials or tokens are invalid/expired."""
    pass

class CalendarResourceNotFoundError(CalendarError):
    """Raised when an event ID cannot be found."""
    pass 