from storage import is_fresh, cache_key

def test_expiry_boundary():
    assert not is_fresh(120, 0, 120)
    assert is_fresh(119, 0, 120)

def test_tenant_isolation():
    assert cache_key("a", "x") != cache_key("b", "x")
