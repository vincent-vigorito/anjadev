def refresh_due(age, interval):
    """Background refresh schedule, not the entry expiration policy."""
    return age >= interval
