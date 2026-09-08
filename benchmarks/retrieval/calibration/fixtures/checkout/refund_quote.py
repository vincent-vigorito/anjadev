def refundable_amount(paid, refunded):
    """Quote the remaining refundable amount without issuing payments."""
    return max(0, paid - refunded)
