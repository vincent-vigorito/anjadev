from queueing import retry_delay, lease_expired

def test_backoff_cap():
    assert retry_delay(10) == 60
    assert retry_delay(1) == 2

def test_lease_boundary():
    assert lease_expired(10, 10)
    assert not lease_expired(9, 10)
