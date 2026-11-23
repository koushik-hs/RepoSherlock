from timing import minutes_from_hours


def delivery_window_minutes(hours):
    """Convert a route duration for the delivery window display."""
    return minutes_from_hours(hours)


def route_title(route):
    return route["name"].strip()


def window_caption(minutes):
    return f"Delivery window: {minutes} minutes"
