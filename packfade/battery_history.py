"""Offline e-bike SOC quality checks and between-recording classifications."""
import numpy as np


def mask_bracketed_50_glitches(time, soc):
    """Mask brief 50% islands bracketed by consistent, distant readings.

    This detects transient ANT+ LEV sensor disconnect/reboot fallbacks (where Garmin
    defaults to 50% for 1-5s upon reconnecting after a pause). Genuine passages
    through 50% (where the battery discharges through 50% from above to below) are
    preserved because left and right readings are on opposite sides of 50%.
    """
    time, soc = np.asarray(time), np.asarray(soc)
    mask = np.zeros(len(soc), dtype=bool)
    hits = np.flatnonzero(soc == 50)
    for run in np.split(hits, np.flatnonzero(np.diff(hits) > 1) + 1):
        if not len(run):
            continue
        a, b = int(run[0]), int(run[-1])
        if a == 0 or b == len(soc) - 1 or time[b] - time[a] > 5:
            continue
        left, right = soc[a - 1], soc[b + 1]
        if np.isfinite(left) and np.isfinite(right):
            # Same side of 50% (transient excursion to 50%, not crossing through 50%)
            is_same_side = (left - 50) * (right - 50) > 0
            is_excursion = abs(left - 50) >= 2 and abs(right - 50) >= 2
            is_consistent = abs(left - right) <= 2
            if is_same_side and is_excursion and is_consistent:
                mask[run] = True
    return mask


def classify_gap(previous, following):
    """Adjacent recordings only. A recording missing SOC is a hard barrier."""
    if previous.get("end_soc") is None or following.get("start_soc") is None:
        return "unknown_missing_endpoint"
    if previous.get("end_quality") != "usable" or following.get("start_quality") != "usable":
        return "unknown_endpoint_quality"
    delta=following["start_soc"]-previous["end_soc"]
    if delta >= 10: return "likely_charge_large_gain"
    if delta >= 5: return "possible_top_up"
    if delta > 1: return "ambiguous_small_gain"
    if delta >= -1: return "within_one_point"
    return "apparent_idle_drop"
