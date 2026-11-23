from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    unit_price_cents: int
    quantity: int


def receipt_total(items: list[LineItem]) -> int:
    """Return the total price of all units on a receipt, in cents."""
    return sum(item.unit_price_cents for item in items)
