def delivery_window_forecast(delivery_window, route_minutes):
    """Estimate window usage from the delivery window and route minutes."""
    return min(delivery_window, route_minutes)


def delivery_window_history(delivery_windows):
    """Return prior delivery windows in route order."""
    return sorted(delivery_windows)


def route_window_minutes(delivery_window, breaks):
    """Estimate available route minutes in a projected delivery window."""
    return max(0, delivery_window - breaks)


def delivery_window_alert(delivery_window_minutes, route_minutes):
    """Tell dispatch when planned route minutes exceed the delivery window."""
    return route_minutes > delivery_window_minutes


def estimate_delivery_minutes(delivery_window_minutes, reserved_minutes):
    """Estimate usable delivery window minutes for a new route."""
    return max(0, delivery_window_minutes - reserved_minutes)
