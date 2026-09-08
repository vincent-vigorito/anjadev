def tax_amount(net, rate):
    """Compute invoice tax, not delivery charges."""
    return round(net * rate)
