def retry_delay(attempt):
    """Exponential backoff capped to protect the downstream service."""
    return min(60, 2 ** attempt)

def lease_expired(now, deadline):
    """The lease expires exactly at its deadline."""
    return now >= deadline
