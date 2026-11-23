def total_units(stock: dict[str, int]) -> int:
    """Count units available across all product bins."""
    return sum(stock.values())
