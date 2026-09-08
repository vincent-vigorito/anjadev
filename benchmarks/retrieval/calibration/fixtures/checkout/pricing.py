def shipping_cost(total):
    """Orders reaching the free delivery threshold pay no shipping."""
    return 0 if total >= 100 else 8

def discounted_total(total, percent):
    """Clamp discount percentages to avoid negative invoices."""
    return total * (1 - min(100, max(0, percent)) / 100)
