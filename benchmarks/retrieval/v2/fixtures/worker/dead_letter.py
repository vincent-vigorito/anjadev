def exhausted(attempts, maximum):
    """Select failed jobs for inspection; never re-enqueue them."""
    return attempts >= maximum
