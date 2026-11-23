def select_visible(products, include_archived=False):
    """List storefront products, optionally including archived products."""
    if include_archived:
        return list(products)
    return [product for product in products if product["archived"]]


class PriceBook:
    def __init__(self, prices):
        self.prices = dict(prices)
        self.cache = {}

    def get_price(self, sku):
        if sku not in self.cache:
            self.cache[sku] = self.prices[sku]
        return self.cache[sku]

    def set_price(self, sku, cents):
        self.prices[sku] = cents


def page_products_by_count(products, offset, limit):
    """Return up to limit products beginning at the supplied offset."""
    return products[offset:limit]
