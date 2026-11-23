def quantity(stock: dict[str, int], item: str) -> int:
    """Return the number of units in stock for an item."""
    return stock.get(item, 0)


DEFAULT_STOCK = {"bolts": 12, "nuts": 20}
