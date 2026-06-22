from kernel import clock


def test_to_units_and_from_units_roundtrip():
    assert clock.to_units(1, 0) == 4
    assert clock.to_units(3, 2) == 14
    assert clock.from_units(4) == (1, 0)
    assert clock.from_units(14) == (3, 2)


def test_advance_within_day():
    # 晨(0) + 2 bands -> 下午(2), same day
    assert clock.advance(1, 0, 0, 2) == (1, 2)


def test_advance_band_carries_into_next_day():
    # 夜晚(3) + 1 band -> next day 晨(0)
    assert clock.advance(1, 3, 0, 1) == (2, 0)


def test_advance_full_days_keeps_band():
    assert clock.advance(2, 1, 3, 0) == (5, 1)


def test_advance_overflow_bands_carry_days():
    # 晨(0) + 6 bands -> +1 day, 下午(2)
    assert clock.advance(1, 0, 0, 6) == (2, 2)


def test_advance_zero_is_identity():
    assert clock.advance(4, 2, 0, 0) == (4, 2)


def test_elapsed_is_unit_difference():
    a = clock.to_units(1, 0)
    b = clock.to_units(3, 2)
    assert clock.elapsed(a, b) == 10


def test_compare_orders_clocks():
    a = clock.to_units(1, 3)
    b = clock.to_units(2, 0)
    assert clock.compare(a, b) == -1
    assert clock.compare(b, a) == 1
    assert clock.compare(a, a) == 0


def test_expired_boundary():
    born = clock.to_units(1, 0)        # 4
    now_at = clock.to_units(2, 0)      # 8  -> elapsed 4
    assert clock.expired(born, 4, now_at) is True       # exactly at lifespan
    assert clock.expired(born, 5, now_at) is False      # one unit short
    assert clock.expired(born, 3, now_at) is True       # past lifespan


def test_band_name():
    assert clock.band_name(0) == "晨"
    assert clock.band_name(3) == "夜晚"
    assert clock.band_name(4) == "晨"   # wraps defensively
