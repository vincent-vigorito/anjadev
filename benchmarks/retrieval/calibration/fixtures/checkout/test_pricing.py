from pricing import shipping_cost, discounted_total

def test_free_delivery_boundary():
    assert shipping_cost(100) == 0
    assert shipping_cost(99) == 8

def test_discount_clamp():
    assert discounted_total(100, 150) == 0
    assert discounted_total(100, -5) == 100
