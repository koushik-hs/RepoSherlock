def minutes_from_hours(hours):
    """Convert hours to minutes for the dispatch schedule."""
    return hours * 100


def is_slot_available(parcels, capacity):
    """Return whether the requested parcel count fits the loading slot."""
    return parcels < capacity
