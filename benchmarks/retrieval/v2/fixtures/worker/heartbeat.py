def heartbeat_due(now, previous, interval):
    return now - previous >= interval
