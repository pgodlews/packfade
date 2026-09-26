import json
from pathlib import Path
import struct
import subprocess
import tempfile

from synthetic_fit import fit_file


def make_synthetic_fit(lead_without_battery=0):
    """Generate a valid binary FIT activity containing e-bike telemetry and a bracketed 50% glitch.

    lead_without_battery prepends records (1 s apart) recorded before the e-bike battery reading
    arrives, as happens while the Edge pairs with the bike.
    """
    fields = [
        (253, 4, 134),  # timestamp (u32, 4B)
        (5, 4, 134),    # distance in cm (u32, 4B)
        (118, 1, 2),    # ebike_battery_level (u8, 1B)
        (13, 1, 1),     # temperature in C (s8, 1B)
        (73, 4, 134),   # enhanced_speed in mm/s (u32, 4B)
        (119, 1, 2),    # assist mode (u8, 1B)
    ]
    records_data = bytearray()
    t0 = 1000000000
    for j in range(lead_without_battery):
        records_data.append(0x00)
        # 0xFF is the FIT invalid value for the uint8 battery field
        records_data.extend(struct.pack('<IIBbIB', t0 - lead_without_battery + j, 0, 0xFF, 20, 0, 0xFF))
    # 25 records spaced 1 second apart (24 km total distance)
    for i in range(25):
        t = t0 + i
        dist_cm = int(i * 1000 * 100)  # 1 km/sec => 24 km total
        # Discharge from 90% down to 67% with a 50% glitch at record 12
        if i == 12:
            soc = 50  # 50% glitch flanked by 78% (i=11) and 77% (i=13)
        elif i < 12:
            soc = 90 - i
        else:
            soc = 90 - (i - 1)
        temp = 20
        spd_mms = 5000  # 5 m/s = 18 km/h (> 1 m/s moving threshold)
        mode = 7        # Mode 7 assistance

        records_data.append(0x00)  # Data record, local msg 0
        records_data.extend(struct.pack('<IIBbIB', t, dist_cm, soc, temp, spd_mms, mode))

    return fit_file(records_data, fields)


def test_browser_js_fit_parser_and_glitch_mask():
    root = Path(__file__).resolve().parent.parent
    index_html = root / "web/index.html"
    assert index_html.exists(), "web/index.html must exist"

    html = index_html.read_text("utf8")
    fit_start = html.index("function parseFitActivity(")
    fit_end = html.index("async function handleFitFileList(")
    fit_fns = html[fit_start:fit_end]

    with tempfile.NamedTemporaryFile(suffix=".fit", delete=True) as tmp:
        tmp.write(make_synthetic_fit())
        tmp.flush()

        node_script = f"""
        const fs = require('fs');
        {fit_fns}

        const buf = fs.readFileSync({json.dumps(tmp.name)});
        const ride = processFitToRide(buf);
        if (!ride) throw new Error('processFitToRide returned null for synthetic fit');

        console.log(JSON.stringify({{
          km: ride.km,
          drop_pp: ride.drop_pp,
          start_soc: ride.start_soc,
          end_soc: ride.end_soc,
          masked_50_glitches: ride.masked_50_glitches,
          reasons: ride.reasons,
          mode7_fraction: ride.mode7_fraction,
          temp: ride.temperature_c,
          speed: ride.speed_kmh
        }}));
        """

        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        out = json.loads(res.stdout.strip())

        assert out["km"] == 24.0
        assert out["drop_pp"] == 23.0
        assert out["start_soc"] == 90.0
        assert out["end_soc"] == 67.0
        assert out["masked_50_glitches"] == 1, "Must scrub the bracketed 50% glitch"
        assert out["reasons"] == [], "Synthetic ride meets all clean discharge thresholds"
        assert out["mode7_fraction"] == 1.0
        assert out["temp"] == 20.0
        assert out["speed"] == 18.0

    # Optional local test on real device recording if present
    sample_fit = root / "data/ebike_history/raw/0194d4c1819995d9bd8ecb1bdea928986e27872ddb2eda9dd0e557829fbc784c.fit"
    if sample_fit.exists():
        node_script_real = f"""
        const fs = require('fs');
        {fit_fns}

        const buf = fs.readFileSync({json.dumps(str(sample_fit))});
        const ride = processFitToRide(buf);
        if (!ride) throw new Error('processFitToRide returned null');

        console.log(JSON.stringify({{
          km: ride.km,
          drop_pp: ride.drop_pp,
          start_soc: ride.start_soc,
          end_soc: ride.end_soc,
          masked_50_glitches: ride.masked_50_glitches,
          reasons: ride.reasons,
          mode7_fraction: ride.mode7_fraction
        }}));
        """
        res_real = subprocess.run(["node", "-e", node_script_real], capture_output=True, text=True, check=True)
        out_real = json.loads(res_real.stdout.strip())
        assert abs(out_real["km"] - 47.25) < 0.1
        assert out_real["drop_pp"] == 81.0
        assert out_real["masked_50_glitches"] == 1


def test_browser_js_huber_wls_solver_matches_baseline():
    root = Path(__file__).resolve().parent.parent
    index_html = root / "web/index.html"
    seed_js = root / "web/seed_data.js"

    html = index_html.read_text("utf8")
    solver_start = html.index("function choleskyDecompose(")
    solver_end = html.index("function simulateRange(")
    solver_fns = html[solver_start:solver_end]

    node_script = f"""
    global.window = {{}};
    const fs = require('fs');
    eval(fs.readFileSync({json.dumps(str(seed_js))}, 'utf8'));

    function median(arr) {{
      if (!arr.length) return 0;
      const s = [...arr].sort((a,b) => a - b);
      const mid = Math.floor(s.length / 2);
      return s.length % 2 !== 0 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    }}

    {solver_fns}

    const rides = window.SEED_EBIKE_DATA.analysis.per_ride.filter(r => !r.reasons || r.reasons.length === 0);
    const fitWls = fitModelClient(rides, {{
      baselineDate: '2025-03-26',
      useWls: true,
      useHuber: true,
      skipCleanCheck: true,
      bootstrap: 200,
      seed: 7
    }});

    console.log(JSON.stringify({{
      apparentSoh: fitWls.apparentSoh,
      beta: fitWls.beta,
      sohCi: fitWls.sohCi,
      rSquared: fitWls.rSquared
    }}));
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())

    assert 90.0 <= out["apparentSoh"] <= 93.0
    assert abs(out["apparentSoh"] - 91.72) < 0.2
    assert 0.050 <= out["beta"] <= 0.065
    assert out["sohCi"] is not None
    assert len(out["sohCi"]) == 2
    assert out["sohCi"][0] < out["apparentSoh"] < out["sohCi"][1]
    assert out["rSquared"] > 0.35


def test_browser_ride_key_is_recording_start_like_python():
    # The Python importer keys rides by the first record; the browser must match, or the same
    # ride imported via the USB bridge and via WebUSB/drag-and-drop becomes two rides.
    html = (Path(__file__).resolve().parent.parent / "web/index.html").read_text("utf8")
    fit_fns = html[html.index("function parseFitActivity("):html.index("async function handleFitFileList(")]
    with tempfile.NamedTemporaryFile(suffix=".fit") as tmp:
        tmp.write(make_synthetic_fit(lead_without_battery=5))
        tmp.flush()
        script = f"""
        {fit_fns}
        const ride = processFitToRide(require('fs').readFileSync({json.dumps(tmp.name)}));
        console.log(JSON.stringify({{ date: ride.date, km: ride.km, start_soc: ride.start_soc }}));
        """
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    # FIT epoch 1989-12-31; t0 - 5 s is the first (battery-less) record
    assert out["date"] == "2021-09-08T01:46:35.000Z"
    assert out["km"] == 24.0 and out["start_soc"] == 90
