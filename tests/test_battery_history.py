import numpy as np
from packfade.battery_history import classify_gap, mask_bracketed_50_glitches


def test_brief_50_glitch_not_genuine_crossing():
    b=np.array([99,98,50,98,97,51,50,49,48.])
    np.testing.assert_array_equal(np.flatnonzero(mask_bracketed_50_glitches(np.arange(len(b)),b)),[2])


def test_unbracketed_or_prolonged_50_is_not_removed():
    b=np.array([50,60,50,50,60.])
    assert not mask_bracketed_50_glitches(np.array([0,1,2,20,21]),b).any()


def test_ant_plus_reconnection_dip_at_54_and_oscillations():
    # 54% -> 50% (2s) -> 54% (empirical case from 2025-06-10)
    t = np.array([1520, 1521, 1541, 1542, 1543, 1544])
    b = np.array([54, 54, 50, 50, 54, 54], dtype=float)
    mask = mask_bracketed_50_glitches(t, b)
    np.testing.assert_array_equal(np.flatnonzero(mask), [2, 3])

    # Oscillations around 60%: 60 -> 50 (1s) -> 60 (empirical case from 2026-09-15)
    t2 = np.array([0, 1, 2, 3, 4, 5])
    b2 = np.array([60, 50, 60, 60, 50, 60], dtype=float)
    mask2 = mask_bracketed_50_glitches(t2, b2)
    np.testing.assert_array_equal(np.flatnonzero(mask2), [1, 4])


def test_gap_classes_and_missing_barrier():
    a={"end_soc":30,"end_quality":"usable"}
    def next_soc(n):return {"start_soc":n,"start_quality":"usable"}
    assert classify_gap(a,next_soc(99))=="likely_charge_large_gain"
    assert classify_gap(a,next_soc(35))=="possible_top_up"
    assert classify_gap(a,next_soc(33))=="ambiguous_small_gain"
    assert classify_gap(a,next_soc(29))=="within_one_point"
    assert classify_gap(a,next_soc(25))=="apparent_idle_drop"
    assert classify_gap({},next_soc(99))=="unknown_missing_endpoint"
