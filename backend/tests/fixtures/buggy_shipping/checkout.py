from rates import standard_fee


def checkout_shipping_cents(subtotal_cents: int) -> int:
    """The checkout delegates shipping prices to the standard tariff."""
    return standard_fee(subtotal_cents)
