def stock_available(on_hand, reserved):
    return max(0, on_hand - reserved)
