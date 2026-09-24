import numpy as np
from scripts.analyze_ebike_battery import discharge_metrics


def test_zero_soc_and_aligned_endpoints():
    t=np.arange(5.)
    d=np.array([0,1000,5000,11000,12000.])
    b=np.array([np.nan,20,10,0,np.nan])
    result,ends=discharge_metrics(t,d,b)
    assert ends==(1,3)
    assert result['drop_pp']==20
    assert result['km']==10
    assert result['pp_per_km']==2
    assert result['reasons']==[]


def test_charge_jump_is_not_degradation():
    result,_=discharge_metrics(np.arange(4.),np.array([0,5000,10000,15000.]),np.array([90,60,80,50.]))
    assert 'upward_battery_jump_at_least_3pp' in result['reasons']


def test_discharge_metrics_scrubs_bracketed_50_glitch():
    # 98% -> 50% (1s) -> 98% -> discharges to 55% over 15 km
    t = np.array([0, 100, 101, 102, 2000], dtype=float)
    d = np.array([0, 1000, 1010, 1020, 15000], dtype=float)
    b = np.array([98, 98, 50, 98, 55], dtype=float)
    result, ends = discharge_metrics(t, d, b)
    assert result['masked_50_glitches'] == 1
    assert result['upward_jumps'] == 0
    assert result['drop_pp'] == 43.0
    assert result['km'] == 15.0
    assert result['reasons'] == []


def test_short_discharge_is_insufficient():
    result,_=discharge_metrics(np.arange(3.),np.array([0,1000,3000.]),np.array([100,98,95.]))
    assert 'distance_under_8km' in result['reasons']
    assert 'battery_drop_under_15pp' in result['reasons']


def test_has_battery_info_independent_of_ebike_sport():
    from packfade.fit import has_battery_info

    # 1. Non-ebike sport tag (road bike) but has ebike_battery_level in records -> should be ACCEPTED
    road_with_battery = {
        "session_mesgs": [{"sport": "cycling", "sub_sport": "road"}],
        "record_mesgs": [{"timestamp": 1, "ebike_battery_level": 85}]
    }
    assert has_battery_info(road_with_battery) is True

    # 2. Tagged as e-bike, but NO battery field in record messages -> should be REJECTED
    ebike_tagged_no_battery = {
        "session_mesgs": [{"sport": "e_biking", "sport_profile_name": "E-Bike"}],
        "record_mesgs": [{"timestamp": 1, "speed": 5.0, "power": 120}]
    }
    assert has_battery_info(ebike_tagged_no_battery) is False

    # 3. Non-record battery info (e.g. only device_info_mesgs for head unit) -> should be REJECTED
    head_unit_battery_only = {
        "session_mesgs": [{"sport": "cycling"}],
        "device_info_mesgs": [{"battery_voltage": 3.7, "battery_status": "ok"}],
        "record_mesgs": [{"timestamp": 1, "speed": 4.5}]
    }
    assert has_battery_info(head_unit_battery_only) is False


def test_robust_wls_and_efc_models():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    analysis_file = root / "reports/ebike_battery/analysis.json"
    if analysis_file.exists():
        data = json.loads(analysis_file.read_text())
    else:
        seed_js = (root / "web/seed_data.js").read_text()
        s = seed_js.find("{")
        e = seed_js.rfind("}") + 1
        data = json.loads(seed_js[s:e])

    models = data.get("analysis", data).get("models", {})
    assert "Robust WLS (Quantization + Huber + Controls)" in models
    assert "Cycle Throughput (EFC + Controls)" in models

    wls = models["Robust WLS (Quantization + Huber + Controls)"]
    assert wls["use_wls"] is True
    assert wls["use_huber"] is True
    assert 85 <= wls["apparent_capacity_index_end"] <= 95
    assert wls["in_sample_r_squared"] > 0.35

    efc = models["Cycle Throughput (EFC + Controls)"]
    assert efc["time_variable"] == "efc"
    assert efc["in_sample_r_squared"] > 0.45


def test_fit_trend_bootstrap_none_when_insufficient():
    from datetime import datetime
    from scripts.analyze_ebike_battery import fit_trend

    rows = []
    base = datetime(2025, 1, 1)
    for i in range(20):
        rows.append({
            "date": datetime(2025, 1, 1 + i).isoformat(),
            "pp_per_km": 2.0 + 0.01 * i,
            "drop_pp": 20.0,
            "reasons": [],
            "temperature_c": 20.0 + (i % 3),
            "speed_kmh": 20.0,
            "mean_soc": 50.0,
        })

    res = fit_trend(rows, ["temperature_c"], base, datetime(2025, 1, 20), bootstrap=10)
    assert res is not None
    assert res["slope_bootstrap_95"] is None
    assert res["apparent_index_95"] is None
    assert res["successful_bootstraps"] <= 10


def test_charging_report_and_cycles_robustness():
    import tempfile
    from pathlib import Path
    from scripts.analyze_ebike_charging import cycles, report

    # Verify cycles() skips mismatched accounting without throwing AssertionError
    rows = [
        {"record_start": "2025-01-01T10:00:00", "record_end": "2025-01-01T11:00:00", "recorded_drop_pp": 10, "start_soc": 100, "end_soc": 90, "residual_soc_rises_at_least_3pp": 0, "km": 10, "start_quality": "usable", "end_quality": "usable"},
        {"record_start": "2025-01-02T10:00:00", "record_end": "2025-01-02T11:00:00", "recorded_drop_pp": 10, "start_soc": 80, "end_soc": 70, "residual_soc_rises_at_least_3pp": 0, "km": 10, "start_quality": "usable", "end_quality": "usable"}
    ]
    gaps = [
        {"classification": "likely_charge_large_gain"},
        {"classification": "apparent_idle_drop", "apparent_drop_pp": 5},
        {"classification": "likely_charge_large_gain"}
    ]
    # riding (10) + idle (5) = 15 != total (100 - 70 = 30) -> should be skipped gracefully
    assert cycles(rows, gaps) == []

    # Verify report() does not crash on empty/zero-charge datasets
    with tempfile.TemporaryDirectory() as td:
        s = {
            "recordings": 0,
            "date_range": ["2025-01-01", "2025-01-02"],
            "gap_counts": {},
            "masked_50_samples": 0,
            "rides_with_masked_50_samples": 0,
            "residual_rise_rides": 0,
            "likely_charges": {"count": 0},
            "idle": {"count": 0, "duration_bands": [], "largest_drops": []},
            "cycles": {"count": 0, "multi_ride_count": 0}
        }
        report(s, Path(td))
        txt = (Path(td) / "charging_profile.md").read_text()
        assert "0 likely charging intervals detected" in txt
        assert "usable gaps have no detected charge" in txt



