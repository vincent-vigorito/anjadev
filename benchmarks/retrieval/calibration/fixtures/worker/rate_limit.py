def rate_allowed(used, quota):
    """Account for request quota, not exponential retry backoff."""
    return used < quota
