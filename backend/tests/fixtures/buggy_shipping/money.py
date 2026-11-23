def format_cents(value: int) -> str:
    """Format an integer amount for the storefront receipt without rounding."""
    dollars, cents = divmod(value, 100)
    return f"{dollars}.{cents:02d}"
