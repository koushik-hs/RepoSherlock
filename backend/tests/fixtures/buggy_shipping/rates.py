FREE_SHIPPING_AT_CENTS = 5000


def standard_fee(subtotal_cents: int) -> int:
    """Shipping is free starting at the published subtotal threshold."""
    if subtotal_cents > FREE_SHIPPING_AT_CENTS:
        return 0
    return 499
