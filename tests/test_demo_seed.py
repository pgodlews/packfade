from datetime import date

from scripts.make_demo_seed import jitter_days, load, problems


def test_published_seed_has_no_ride_times():
    # The demo seed is public: rides at 00:00 UTC, one per date, never overlapping.
    assert problems(load()) == []


def test_jitter_keeps_order_and_bounds_even_for_same_day_rides():
    days = [date(2025, 6, 13)] * 2 + [date(2025, 6, 14)] * 3 + [date(2025, 6, 20)]
    for _ in range(200):
        out = jitter_days(days)
        assert out[0] == days[0]
        assert all(b > a for a, b in zip(out, out[1:]))
        assert all(abs((o - d).days) <= 3 for o, d in zip(out, days))
