"""Browser engine: FIT import validation, derived analytics, backup/restore and ride selection."""
import json
import math
import tempfile
from pathlib import Path

from browser_harness import run_engine
from synthetic_fit import INVALID_S8, INVALID_U8, INVALID_U32, T0, fit_file, record, ride_fit

DAY = 86400
RECORD_BYTES = 16          # 1 header byte + '<IIBbIB'
DATA_START = 14 + 6 + 3 * 6  # file header + record definition


def write_fits(tmp, **files):
    paths = {}
    for name, data in files.items():
        path = Path(tmp) / f"{name}.fit"
        path.write_bytes(data)
        paths[name] = str(path)
    return paths


def discharge(start=90, n=25):
    return [start - i for i in range(n)]


def process(data):
    """processFitToRide on one file: the ride, or {'error': message} if it was rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_fits(tmp, ride=data)["ride"]
        return run_engine(f"""
          try {{ return processFitToRide(new Uint8Array(fs.readFileSync({json.dumps(path)}))); }}
          catch (err) {{ return {{ error: err.message }}; }}
        """)


def assert_close(actual, expected, path="$"):
    """Recursive equality, with a relative tolerance for floats (JS vs numpy summation order)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) >= set(expected), f"{path}: keys {sorted(expected)} vs {actual}"
        for k in expected:
            assert_close(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: length differs"
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) or isinstance(actual, float):
        assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), f"{path}: {actual} != {expected}"
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


# ── H3: truncated and corrupt FIT files ──────────────────────────────────────────────────────────

def test_truncated_or_corrupt_fit_is_rejected_not_imported_as_a_shorter_ride():
    good = ride_fit(discharge())
    assert process(good)["reasons"] == []

    after_20_records = good[:DATA_START + 20 * RECORD_BYTES]  # declared length unchanged
    inside_last_record = good[:-2 - 5]
    flipped = bytearray(good)
    flipped[DATA_START + 5 * RECORD_BYTES + 9] ^= 0x01        # one SOC byte
    for bad, expected in [(after_20_records, "truncated"), (inside_last_record, "truncated"),
                          (bytes(flipped), "CRC mismatch")]:
        out = process(bad)
        assert "error" in out and expected in out["error"], out


# ── M2 / M3 / L2: ride metrics ───────────────────────────────────────────────────────────────────

def test_missing_temperature_is_missing_not_zero():
    socs = discharge()
    ride = process(ride_fit(socs, temps=[INVALID_S8] * len(socs)))
    assert ride["temperature_c"] is None
    assert "missing_adjustment_covariate" in ride["reasons"]

    # Partial coverage: the mean is over recorded values only, not pulled towards 0
    half = len(socs) // 2
    ride = process(ride_fit(socs, temps=[10] * half + [INVALID_S8] * (len(socs) - half)))
    assert ride["temperature_c"] == 10


def test_battery_coverage_counts_intervals_without_a_reading():
    # 1 Hz discharge over 20 km and 20 pp, with every other battery sample missing
    socs = [90 - i // 2 if i % 2 == 0 else INVALID_U8 for i in range(41)]
    ride = process(ride_fit(socs, km_per_step=0.5))
    assert ride["km"] == 20 and ride["drop_pp"] == 20
    assert abs(ride["battery_time_coverage"] - 0.5) < 1e-9
    assert "battery_time_coverage_under_90pct" in ride["reasons"]


def test_upward_battery_jumps_are_counted_like_python():
    socs = [90, 89, 88, 92, 91, 90, 89, 93] + discharge(92, 20)
    ride = process(ride_fit(socs))
    assert ride["upward_jumps"] == 2
    assert ride["largest_upward_jump_pp"] == 4
    assert "upward_battery_jump_at_least_3pp" in ride["reasons"]


# ── M1 / L1 / M4: the import batch ───────────────────────────────────────────────────────────────

FRESH_PERSONAL = """
  datasetMode = 'personal';
  currentData = { analysis: { per_ride: [], models: {} }, charging: { gaps: [] } };
"""


def import_files(setup, batches, extra=""):
    """Run handleFitFileList once per batch of named files; report messages and stored data."""
    with tempfile.TemporaryDirectory() as tmp:
        names = {n for batch in batches for n in batch}
        paths = write_fits(tmp, **{n: FILES[n] for n in names})
        calls = "\n".join(
            "await handleFitFileList([" + ", ".join(f"fitFile('{n}.fit', {json.dumps(paths[n])})" for n in batch) + "]);"
            "messages.push(progress[progress.length - 1].message);"
            for batch in batches)
        return run_engine(f"""
          {setup}
          const messages = [];
          {calls}
          {extra}
          return {{ messages, console: consoleLines, data: currentData, idb, datasetMode }};
        """)


def steady_ride(start_soc, t0):
    """24 km and 24 pp at 1 Hz, one point every 20 s, so the 30 s endpoint windows are steady."""
    socs = [start_soc - i // 20 for i in range(24 * 20 + 1)]
    return ride_fit(socs, t0=t0, km_per_step=0.05)


FILES = {
    "ride_a": steady_ride(90, T0),                             # 90 -> 66
    "ride_b": steady_ride(100, T0 + DAY),                      # charged overnight: 100 -> 76
    "ride_c": steady_ride(75, T0 + 2 * DAY),                   # parked: 76 -> 75
    "no_distance": fit_file(b"".join(record(T0 - DAY + i, INVALID_U32, 95 - i) for i in range(25))),
    "corrupt": ride_fit(discharge(80), t0=T0 + 3 * DAY)[:-40],
}


def test_missing_distance_and_corrupt_files_do_not_abort_the_batch():
    out = import_files(FRESH_PERSONAL, [["no_distance", "corrupt", "ride_a", "ride_b"]])
    rides = out["data"]["analysis"]["per_ride"]
    assert len(rides) == 3
    assert rides[0]["reasons"] == ["missing_battery_or_distance"] and rides[0]["km"] is None
    assert any("[FILTERED]" in line and "no distance" in line for line in out["console"])
    assert any("[REJECTED] corrupt.fit" in line for line in out["console"])
    assert out["messages"][0].startswith("Import Complete: 2 new clean rides added")
    assert "1 rejected as corrupt" in out["messages"][0]
    assert len(out["idb"]["ebike_data"]["analysis"]["per_ride"]) == 3


def test_reimporting_files_does_not_report_them_as_new():
    out = import_files(FRESH_PERSONAL, [["ride_a", "ride_b"], ["ride_a", "ride_b"]])
    assert out["messages"][0].startswith("Import Complete: 2 new clean rides added")
    assert out["messages"][1].startswith("Import Complete: 0 new clean rides added, 2 already in database")
    assert len(out["data"]["analysis"]["per_ride"]) == 2


def test_import_builds_charging_gaps_and_quarterly_summary():
    out = import_files(FRESH_PERSONAL, [["ride_a", "ride_b"], ["ride_c"]])
    data = out["data"]
    gaps = data["charging"]["gaps"]
    assert [g["classification"] for g in gaps] == ["likely_charge_large_gain", "within_one_point"]
    assert (gaps[0]["before_soc"], gaps[0]["after_soc"]) == (66, 100)
    assert abs(gaps[0]["gap_hours"] - (DAY - 480) / 3600) < 1e-9  # end of ride A to start of ride B
    assert data["charging"]["likely_charges"]["count"] == 1
    assert data["analysis"]["quarterly"] == [{
        "period": "2021 Q3", "rides": 3, "median_pp_per_km": 1.0, "median_equivalent_range_km": 100.0,
        "median_temperature_c": 20, "median_mode7_fraction": 1}]
    assert "charging_endpoint" not in data["analysis"]["per_ride"][0]
    assert len(data["charging"]["endpoints"]) == 3


def test_usb_bridge_merge_rebuilds_charging_over_all_rides():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_fits(tmp, ride_c=FILES["ride_c"])["ride_c"]
        bridge = f"""
          const rideC = processFitToRide(new Uint8Array(fs.readFileSync({json.dumps(path)})));
          const epC = rideC.charging_endpoint; delete rideC.charging_endpoint;
          await mergeSyncedData({{ analysis: {{ per_ride: [rideC] }},
                                   charging: {{ endpoints: [epC], gaps: [], likely_charges: {{ count: 99 }} }} }});
        """
        out = import_files(FRESH_PERSONAL, [["ride_a", "ride_b"]], extra=bridge)
    charging = out["data"]["charging"]
    assert [g["classification"] for g in charging["gaps"]] == ["likely_charge_large_gain", "within_one_point"]
    assert charging["likely_charges"]["count"] == 1


def test_browser_charging_and_quarterly_match_python_on_the_seed():
    out = run_engine("""
      const seed = loadSeed();
      return {
        charging: buildChargingSummary(seed.analysis.per_ride, seed.charging.endpoints),
        quarterly: buildQuarterly(seed.analysis.per_ride),
        seed: { charging: seed.charging, quarterly: seed.analysis.quarterly }
      };
    """)
    js, py = out["charging"], out["seed"]["charging"]
    for key in ["recordings", "date_range", "gap_counts", "masked_50_samples", "rides_with_masked_50_samples",
                "residual_rise_rides", "likely_charges", "possible_topups", "gaps", "endpoints"]:
        assert_close(js[key], py[key], key)
    assert_close(js["idle"], {k: v for k, v in py["idle"].items() if k != "duration_drop_correlation"}, "idle")
    assert_close(out["quarterly"], out["seed"]["quarterly"], "quarterly")


# ── H1 / H2 / M5: backup and restore ─────────────────────────────────────────────────────────────

DEMO_LOADED = """
  currentData = loadSeed();
  datasetMode = 'demo';
  await idbSet('dataset_mode', 'demo');
"""


def test_restore_in_demo_mode_keeps_the_backup_with_its_settings_and_selections():
    out = run_engine(DEMO_LOADED + """
      const backup = loadSeed();
      const key = rideKey(backup.analysis.per_ride.find(r => !r.reasons.length));
      backup.bike_settings = { bikeModel: 'Test bike', motor: 'Test motor', nominalWh: 500,
                               baselineDate: '2025-04-01', baselinePct: 98, unit: 'mi' };
      backup.user_ride_inclusions = { [key]: false };
      const result = await restoreBackup(JSON.parse(JSON.stringify(backup)));
      return { result, key, idb, datasetMode, exported: buildBackup() };
    """)
    idb = out["idb"]
    # Startup only keeps saved data when the mode is not 'demo'
    assert out["datasetMode"] == "personal" and idb["dataset_mode"] == "personal"
    assert len(idb["ebike_data"]["analysis"]["per_ride"]) == 96
    assert out["result"] == {"restoredSettings": True}
    assert idb["bike_settings"]["nominalWh"] == 500 and idb["bike_settings"]["baselineDate"] == "2025-04-01"
    assert idb["bike_settings"]["baselinePct"] == 98 and idb["bike_settings"]["unit"] == "mi"
    assert idb["user_ride_inclusions"] == {out["key"]: False}
    # The export carries them too, so the backup round-trips
    assert out["exported"]["bike_settings"]["nominalWh"] == 500
    assert out["exported"]["user_ride_inclusions"] == {out["key"]: False}
    assert out["exported"]["analysis"]["per_ride"][0]["date"] == idb["ebike_data"]["analysis"]["per_ride"][0]["date"]


def test_restore_without_settings_resets_selections_to_default():
    out = run_engine(DEMO_LOADED + """
      userInclusions = { '2025-03-26T00:00:00': true };
      const result = await restoreBackup({ analysis: loadSeed().analysis });
      return { result, idb };
    """)
    assert out["result"] == {"restoredSettings": False}
    assert out["idb"]["user_ride_inclusions"] == {}
    assert out["idb"]["dataset_mode"] == "personal"


PAYLOAD = '<img src=x onerror="alert(document.domain)">'


def test_restore_rejects_markup_in_numeric_fields():
    out = run_engine(DEMO_LOADED + f"""
      const results = [];
      for (const field of ['start_soc', 'end_soc', 'drop_pp', 'date']) {{
        const backup = loadSeed();
        backup.analysis.per_ride[3][field] = {json.dumps(PAYLOAD)};
        try {{ await restoreBackup(backup); results.push('accepted'); }}
        catch (err) {{ results.push(err.message); }}
      }}
      const reasons = loadSeed();
      reasons.analysis.per_ride[3].reasons = [{json.dumps(PAYLOAD)}];
      try {{ await restoreBackup(reasons); results.push('accepted'); }} catch (err) {{ results.push(err.message); }}
      return {{ results, datasetMode, rides: currentData.analysis.per_ride.length, idb }};
    """)
    assert all(r.startswith("ride 4.") and "invalid value" in r for r in out["results"]), out["results"]
    # Nothing was changed by the rejected files
    assert out["datasetMode"] == "demo" and out["idb"]["dataset_mode"] == "demo" and out["rides"] == 96


def test_ride_details_escape_stored_values():
    out = run_engine(f"""
      currentData = {{ analysis: {{ per_ride: [], models: {{}} }} }};
      showRideDetails({{ date: '2025-01-01T00:00:00', start_soc: {json.dumps(PAYLOAD)}, end_soc: 40,
                         drop_pp: {json.dumps(PAYLOAD)}, masked_50_glitches: 0, reasons: [] }});
      return document.getElementById('rideDetailContent').innerHTML;
    """)
    assert "<img" not in out and "&lt;img" in out


# ── M6: capacity at the last ride ────────────────────────────────────────────────────────────────

def test_short_history_reports_capacity_at_the_last_ride():
    out = run_engine("""
      const rides = Array.from({ length: 15 }, (_, i) => ({
        date: new Date(Date.UTC(2025, 0, 1 + i)).toISOString(),
        pp_per_km: 1.0 * Math.exp(0.01 * i), drop_pp: 30, reasons: []
      }));
      const fit = fitModelClient(rides, { controls: [], baselineDate: '2025-01-01', baselinePct: 100,
                                          useWls: false, useHuber: false, skipCleanCheck: true });
      return { durationYears: fit.durationYears, beta: fit.beta, soh: fit.apparentSoh };
    """)
    assert abs(out["durationYears"] - 14 / 365.25) < 1e-12
    assert abs(out["soh"] - 100 * math.exp(-out["beta"] * 14 / 365.25)) < 1e-9
    assert abs(out["soh"] - 100 * math.exp(-0.14)) < 1e-6  # exactly the fitted rise over 14 days


# ── M8: select all follows the table search ──────────────────────────────────────────────────────

def test_select_all_acts_on_the_rows_the_search_shows():
    out = run_engine("""
      currentData = { analysis: { models: {}, per_ride: [
        { date: '2025-01-01T00:00:00Z', reasons: [] },
        { date: '2025-01-02T00:00:00Z', reasons: ['distance_under_8km'] }
      ] } };
      searchFilter = 'excluded';
      await toggleSelectAll(true);
      const afterExcluded = { ...userInclusions };
      searchFilter = 'included';
      await toggleSelectAll(false);
      return { afterExcluded, afterIncluded: userInclusions };
    """)
    assert out["afterExcluded"] == {"2025-01-02T00:00:00": True}
    assert out["afterIncluded"] == {"2025-01-01T00:00:00": False, "2025-01-02T00:00:00": False}
