def is_fresh(now, saved_at, ttl):
    """Cached entries expire when their age reaches the time to live."""
    return now - saved_at < ttl

def cache_key(tenant, name):
    """Include the tenant in keys to isolate customer data."""
    return tenant + ":" + name
