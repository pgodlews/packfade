# Adapting Packfade to a Different E-Bike

Packfade was built and tuned on one bike: a Raymon Trailray 160e with a Yamaha PW-X3 motor and a Simplo 720 Wh pack, recorded on a Garmin Edge 1040/840. This guide explains how to adapt it to another e-bike. It assumes the data still comes from a **Garmin head unit that records the e-bike over ANT+ LEV**.

Some settings can be changed in the UI. Most bike-specific behaviour is still **hard-coded in several places**, in both the browser engine (`web/index.html`) and the Python prototype (`scripts/`). This guide lists all of those places. Section 13 describes a refactor that would turn most of this guide into editing a single config object.

> Locations are given as **function or constant names**, not line numbers, because `web/index.html` changes often. Use your editor's search.

---

## 1. What stays the same (Garmin side)

These parts don't depend on the bike and don't need changes:

| Component | Why it is bike-independent |
|---|---|
| FIT decoding (`parseFitActivity`, `packfade/fit.py`) | Uses the FIT format and the Garmin FIT profile field numbers |
| USB/MTP import (`scripts/pull_edge.c`) | Selects any Garmin device (vendor `0x091e`) and reads `Garmin/Activities` |
| Regression machinery (`fitModelClient`, `choleskyDecompose`, Huber IRLS, date bootstrap) | Generic statistics on `log(pp/km)` |
| Charge-gap classification (`classify_gap`) | Works on SOC deltas only |

Packfade relies on these FIT **record** fields, which Garmin writes when an ANT+ LEV e-bike is paired:

| Field # | Name | Required? | Used for |
|---|---|---|---|
| 118 | `ebike_battery_level` (%, scale 1) | **Required** | SOC, consumption, glitch mask |
| 5 | `distance` | **Required** | km per ride |
| 253 | `timestamp` | **Required** | time weights, dates |
| 119 | `ebike_assist_mode` | Strongly recommended | assist-mode covariates (rides without it are excluded from the fit) |
| 13 | `temperature` | Strongly recommended | temperature covariate (this is the head unit's temperature, not the cells') |
| 73 / 6 | `enhanced_speed` / `speed` | Strongly recommended | moving-speed covariate |
| session 22 | `total_ascent` | Optional | climbing covariate (Python sensitivity models only) |

**Don't use** record field 81 (`battery_soc`, scale 2). It isn't the traction battery level.

---

## 2. Step 0: check that your FIT files are compatible

Before changing anything, check what your bike actually sends. Save the script below as a scratch file (don't commit it together with your FIT files) and run it on a few of your rides:

```python
"""Summarise e-bike fields in one or more Garmin FIT files."""
import sys
from collections import Counter
from garmin_fit_sdk import Decoder, Stream

FIELDS = ["ebike_battery_level", "ebike_assist_mode", "ebike_assist_level_percent",
          "ebike_travel_range", "distance", "temperature", "enhanced_speed", "speed", "battery_soc"]

modes = Counter()
for path in sys.argv[1:]:
    messages, errors = Decoder(Stream.from_file(path)).read()
    records = messages.get("record_mesgs", [])
    present = {f: sum(r.get(f) is not None for r in records) for f in FIELDS}
    soc = [r["ebike_battery_level"] for r in records if r.get("ebike_battery_level") is not None]
    exact_50 = sum(v == 50 for v in soc)
    steps = [b - a for a, b in zip(soc, soc[1:]) if b != a]
    rises = [s for s in steps if s > 0]
    modes.update(r["ebike_assist_mode"] for r in records if r.get("ebike_assist_mode") is not None)
    print(f"{path}: {len(records)} records, decode errors={len(errors)}")
    for f, n in present.items():
        print(f"  {f:26s} {n:6d} records")
    if soc:
        print(f"  SOC {soc[0]} -> {soc[-1]} (min {min(soc)}, max {max(soc)}), exact-50 samples: {exact_50}")
        print(f"  SOC changes: {len(steps)} ({len(rises)} upward, {sum(rises)} pp total rise)")

total = sum(modes.values())
print("\nAssist-mode codes (share of records, all files):")
for code, n in modes.most_common():
    print(f"  code {code}: {100 * n / total:5.1f}%")
```

```bash
.venv/bin/python check_fit.py ~/Downloads/rides/*.fit
```

Here is the output for one of the reference bike's rides:

```
  ebike_battery_level         12428 records
  ebike_assist_mode           12425 records
  ebike_assist_level_percent      0 records
  ...
  SOC 99 -> 18 (min 18, max 99), exact-50 samples: 38
  SOC changes: 83 (1 upward, 48 pp total rise)
Assist-mode codes (share of records, all files):
  code 7:  96.3%
  code 2:   2.1%
  code 3:   1.6%
```

Read the output like this:

- **`ebike_battery_level` = 0 records** means Packfade can't work with this bike. Check that the Edge pairs with the bike as an *eBike* sensor and that the bike broadcasts ANT+ LEV.
- **The assist-mode codes** are specific to each drive system. The FIT profile gives their unit as "depends on sensor". Write down which codes appear and how much riding time each one gets; section 5 uses them.
- **Samples at exactly 50%** are not glitches by themselves. A real discharge also passes through 50%, which explains most of the 38 above. What matters is short, isolated runs at exactly 50% that are **bracketed by readings on the same side of 50%**. See section 6 and `docs/ANT_PLUS_BATTERY_ANOMALIES.md`.
- **No assist-mode data, or only one code:** see section 5.3. If `ebike_assist_level_percent` has records, section 5.2 describes a better covariate.
- **SOC changes:** a well-behaved battery reading steps **down** one point at a time. In the reference ride above, the only upward step is the recovery from the 50% glitch (50 → 98). Many upward steps on a ride without charging mean either a voltage-estimated SOC or regenerative braking. See section 8 before going further.
- **SOC resolution:** check that the values are whole numbers. If your system reports in steps other than 1%, see section 6.2.

---

## 3. Step 1: bike profile (UI, no code change)

Open **Bike Profile** in the web app and set:

| Setting | Effect |
|---|---|
| Bike Model / Drive Unit | Display only |
| Nominal Pack Energy (Wh) | Converts the capacity index to "Usable Capacity (Wh)" and pp/km to Wh/km. It **does not** change the estimated fade %. |
| Baseline In-Service Date | Time origin: the capacity index is anchored at 100% on this date. Use the pack's first-use date, or your first recorded ride. |
| Baseline Capacity Assumption (%) | **Not wired up yet.** It is saved but has no effect. |

To make your bike the default for new visitors to your fork, change the `bikeSettings` default object and the matching `value="…"` attributes of the `settingBikeModel`, `settingMotor`, `settingNominalWh` and `settingBaselineDate` inputs in `web/index.html`.

---

## 4. Step 2: use your own data, not the demo

`web/seed_data.js` holds the **author's 96 rides** as a demo dataset. When you import FIT files while the demo is shown, the app asks whether to start a clean **Personal Pack**. Choose **OK**: merging into the demo would fit one model across two different bikes.

For a fork dedicated to another bike, you have two options:

- Keep the demo as it is, as a clearly labelled sample.
- Remove the `<script src="seed_data.js">` tag so the app starts empty.

**Don't** replace `seed_data.js` with your own rides unless you have removed the private details first: exact ride timestamps and FIT file hashes (AGENTS.md Rule 1). To build a demo from your own rides, run the Python analysis, wrap `reports/ebike_battery/analysis.json` and `charging_profile.json` as `window.SEED_EBIKE_DATA = {"analysis": …, "charging": …};`, then run `PYTHONPATH=. .venv/bin/python scripts/make_demo_seed.py`. It moves every ride to 00:00 UTC on a date shifted ±3 days (keeping order, one ride per day), recomputes the models and charging gaps, and refuses to write if anything still looks like a real ride time.

---

## 5. Step 3: assist-mode mapping (the main code change)

The model treats assist mode as a **categorical covariate**. Each ride gets the fraction of its moving time spent in each mode code. One code is left out as the **reference category**, and the rest become regression columns. The reference bike uses:

- codes `7, 5, 3, 2`
- code 7 as the reference, because it accounts for about 96% of riding time
- columns `mode5_fraction`, `mode3_fraction` and `mode2_fraction`

To choose the codes for your bike:

1. Take the codes from step 0, with their time shares across **all** your rides.
2. Make the most-used code the reference.
3. Add a column for another code only if a meaningful number of rides use it for a meaningful share of time. As a rule of thumb, at least about 5% of total riding time. Codes that almost never appear add noise and eat up degrees of freedom: the Python fit needs at least `max(15, 3·(p+2))` rides, where `p` is the number of controls.
4. Codes you don't list (for example "off" or walk-assist) fall into the denominator (Python) or are ignored (JS; see the note below).

Code names such as "Eco" or "Turbo" belong to the manufacturer. Don't assume them. The model never needs names, and the reference project's report deliberately avoids treating codes as numeric multipliers.

Places to change:

| File / function | What to change |
|---|---|
| `web/index.html` → `processFitToRide` | `modeWeights = { 7: 0, 5: 0, 3: 0, 2: 0 }` and the `mode7/5/3/2` fraction variables and returned fields |
| `web/index.html` → `fitModelClient` | the `r.mode7_fraction != null` filter (use your reference code) and the `mode5/3/2_fraction` columns in `cols` |
| `web/index.html` → ride table (`renderRideTable`), detail modal (`showRideDetails`), CSV export | the `% M7` / "Assist Mode 7 Share" / `mode7_fraction` column |
| `web/index.html` → simulator (`simulateRange`, `updateSimulatorUI`, `drawSimulatorChart`, `#simModeBtnGroup` buttons) | `modeMult`, `modeNames`, `modes`, and the button `data-mode` values and labels (see section 9) |
| `scripts/analyze_ebike_battery.py` → `extract()` | `for code in [7,5,3,2]` and the `mode7_fraction` completeness check |
| `scripts/analyze_ebike_battery.py` → `main()` | `core=[…mode5_fraction, mode3_fraction, mode2_fraction]` and the `stable` subset (`mode7_fraction>=.95`) |

> **Keep JS and Python consistent** (AGENTS.md Rule 6). Both need the same codes, the same reference code and the same denominator definition. At the time of writing, JS divides by time in the listed modes only, while Python divides by time in *any* mode. Pick one definition and apply it in both.

### 5.1 More (or different) assist levels

The number of levels and their codes don't matter to the model; only the mapping in the table above changes. What limits you is **sample size**:

- Each non-reference level adds one regression column.
- The Python fit needs at least `max(15, 3·(p+2))` clean rides, where `p` is the number of controls.
- Example: 3 other controls (temperature, speed, mean SOC) plus 4 non-reference levels gives `p = 7` and needs 27 clean rides.

If you don't have that many rides, **group the levels** into a few bands, for example low / mid / high, and add up the time shares within each band. Choose the bands from the time shares in step 0, not from the manufacturer's level names. Code the grouping the same way in `processFitToRide` and `extract()`.

### 5.2 Continuous assist (`ebike_assist_level_percent`, field 120)

Some systems report assist strength as a percentage instead of, or as well as, a mode code. If step 0 shows records for this field, a single **time-weighted mean assist %** per ride (moving time only, like the mode fractions) is a better control than several mode columns: it uses one degree of freedom and keeps the ordering.

This is **not implemented yet**. You would need to:

1. read record field 120 in `parseFitActivity` (uint8, scale 1) and with the SDK in `extract()`
2. compute the weighted mean in `processFitToRide` / `extract()`, the same way as `speed_kmh`
3. use it in place of the `mode*_fraction` columns in `fitModelClient` and in `core`

### 5.3 No assist modes, or a single mode

**With the code as it is, the model fits nothing for such a bike:**

- A ride with no assist-mode data gets the reason `missing_adjustment_covariate` (`processFitToRide` checks `mode7 === null`; Python lists `"mode7_fraction"` in its completeness check in `extract()`), so every ride is excluded.
- `fitModelClient` also filters on `r.mode7_fraction != null`.
- With a single mode, every mode column is zero. Python's rank check refuses the fit; JS runs only because of its `1e-6` stabiliser, and the column adds nothing.

To support these bikes, make the mode columns optional:

| File / function | Change when the bike has no modes |
|---|---|
| `processFitToRide` | drop `mode7 === null` from the `missing_adjustment_covariate` condition |
| `fitModelClient` | drop the `r.mode7_fraction != null` filter and the three `mode*_fraction` columns from `cols` |
| `extract()` (Python) | drop `"mode7_fraction"` from the completeness check |
| `main()` (Python) | drop the mode columns from `core`, and the `mode7_fraction>=.95` condition from the `stable` subset |
| UI | hide the simulator's mode buttons and the ride table's assist column |

What this means for the results depends on why there are no modes:

- **The bike genuinely has one assist level** (or the rider never changes it): nothing is lost, because there is no assist choice to control for.
- **The bike has levels but doesn't broadcast them:** assist choice becomes a **hidden confounder**. If you drift towards higher assist over the months, the model will read that as capacity fade. Expect a less reliable trend, and state this next to the result.

---

## 6. Step 4: SOC sensor behaviour

### 6.1 The reconnection sentinel (the "50% glitch")

With the Yamaha system and a Garmin Edge, a short `50%` reading appears after the drive unit wakes up. Other systems may send a different placeholder value, or none at all.

- **No sentinel on your bike:** leave the mask alone. It only fires on short bracketed excursions, so it is harmless.
- **A different sentinel value `S`:** change the literal `50` in **both** `maskBracketed50Glitches` (`web/index.html`) and `mask_bracketed_50_glitches` (`packfade/battery_history.py`). Keep the invariants from AGENTS.md Rule 4, with 50 replaced by `S`:
  - it is a glitch only if `(SoC_left − S)·(SoC_right − S) > 0`
  - a real crossing, `(SoC_left − S)·(SoC_right − S) ≤ 0`, must never be masked
- **Other parameters:** maximum run length (5 s), minimum excursion distance (≥ 2 pp) and bracket consistency (≤ 2 pp). Change them only with evidence from your own files.
- **Tests:** add a case to `tests/test_battery_history.py` for your sentinel, covering both the glitch and a real crossing.

### 6.2 SOC resolution

The WLS weights assume **integer (1%) SOC steps**. A difference of two rounded readings then has variance `1/6`, so the weight is:

```
w = 1 / (0.05² + 1 / (6 · ΔSoC²))
```

If your system reports in steps of `q` percentage points, the quantisation term becomes `q² / (6 · ΔSoC²)`. Update it in:

- `fitModelClient` (`wBase`)
- `processFitToRide` (`quantization_weight`)
- `scripts/analyze_ebike_battery.py` (`extract()` and `fit_trend()`)

Background is in `docs/MATHEMATICAL_MODEL.md` §4. The `0.05` term is an assumed 5% floor for environmental noise, not a property of the bike.

---

## 7. Step 5: ride-quality filters

These thresholds decide which rides count as "clean" and enter the fit. They were chosen for a 720 Wh eMTB ridden 10–50 km.

| Rule | Value | JS (`processFitToRide`) | Python (`discharge_metrics` / `extract`) |
|---|---|---|---|
| Minimum distance | 8 km | `km < 8` | `km < 8` |
| Minimum SOC drop | 15 pp | `drop < 15` | `drop < 15` |
| Upward jump (charge, swap or reconnect) | ≥ 3 pp | `dSoc >= 3` | `delta >= 3` |
| Total rebounds | > 2 pp | `deltaRebounds > 2` | `delta[delta>0].sum() > 2` |
| Battery time coverage | < 90% | `coverage < 0.90` | `coverage < .9` |
| Max sample gap counted in time weights | 10 s | `dt > 10` | `dt <= 10` |
| Temperature warm-up excluded | 600 s | `t0 + 600` | `t[first] + 600` |
| "Moving" speed threshold | 1 m/s | `cur.speed > 1.0` | `speed > 1` |

How pack size affects these:

- **Smaller pack** (for example 400–500 Wh): each km uses more SOC, so rides reach 15 pp sooner. The defaults usually still work.
- **Larger pack** (for example 900 Wh+) or **light-assist bikes**: many rides may never reach 15 pp. You can lower the minimum drop (for example to 10 pp), because the WLS weights already give shallow rides less influence. Expect wider confidence intervals.
- **Commuter bikes with short trips:** lowering the 8 km minimum increases the sample size but adds more noise from stop-and-go riding.

Change each value in **both** columns of the table, and update the matching preset tooltips in the ride table header (`presetShortBtn`, `presetCleanBtn`).

---

## 8. Hub motors and other drive types

The method doesn't depend on where the motor sits. It only uses the SOC change per km from the Garmin file, so a hub-motor bike can work. Whether it does depends on four things.

### 8.1 The bike must broadcast ANT+ LEV

Many budget hub systems have no wireless connection at all. Without one, Garmin never records `ebike_battery_level`, and Packfade has nothing to work with. Step 0 answers this at once: 0 records for field 118 means the bike can't be used.

### 8.2 How the bike estimates SOC (the deciding factor)

Packfade assumes the % comes from a **battery management system that tracks charge** (coulomb counting), so that 100 → 0% covers the pack's *current* capacity. That is what makes pp/km rise as the pack fades. Many inexpensive hub controllers estimate SOC **from pack voltage** instead. That reading:

- drops under load and recovers when you stop
- depends on temperature
- isn't proportional to the energy used

**Signs of a voltage-based SOC** in step 0: many upward steps on rides without charging; readings that jump back up at every stop; fast drops near full and near empty with a long flat stretch in between.

If your bike shows these signs, **Packfade won't give a meaningful capacity estimate**. Most rides would also fail the jump and rebound filters. Don't try to fix this by loosening the filters: a voltage-based reading doesn't contain the capacity information the model needs.

### 8.3 Regenerative braking

Some direct-drive hub motors recover energy on descents and when braking, so SOC goes *up* while riding. The "upward jump ≥ 3 pp" and "total rebounds > 2 pp" filters (section 7) exist to catch charging, battery swaps and reconnects. On a regen bike they would exclude most hilly rides.

A regen bike would need a new `regen` setting (**not implemented yet**) that:

- **ignores rises that happen while moving** (distance increasing and speed above the moving threshold) when judging jumps and rebounds
- **still flags rises while stationary** (distance not increasing), because those are still charging or reconnect events
- keeps consumption as the **net** start-to-end drop, as it is now. This measures net energy, so the share recovered by regen varies with route and makes climbing a more important control (section 8.4).

Both engines need the change: `processFitToRide` and `discharge_metrics()` / `extract()`. Add synthetic tests: a regen rise while descending must pass, and a stationary jump must still be flagged.

### 8.4 Model controls for hub motors

- **Climbing matters more.** Hub motors lose a lot of efficiency at low speed and high torque, i.e. on steep climbs. `ascent_m_per_km` is already computed (`processFitToRide`, `extract()`), but only Python's sensitivity models use it. For a hub bike, consider adding it to the main controls: `fitModelClient` `cols` and `core`. Rides without elevation data are then excluded, so check that your Edge records `total_ascent`.
- **Speed can behave differently.** Many hub systems use a cadence sensor and deliver a roughly fixed power per assist level. Consumption per km then falls as speed rises (the same power spread over more km per hour). The linear speed term can capture either direction, but `log(speed)` may fit better. Try it only with a before/after comparison on your own data.
- **Placeholder values** are system-specific. Check step 0 for a sentinel value, as in section 6.1. There is no reason to expect the Yamaha 50% behaviour.

### 8.5 Summary

| Bike | Works? |
|---|---|
| Hub motor, ANT+ LEV, BMS-tracked SOC, no regen | Yes, with the same changes as any other bike (sections 3–7) |
| Hub motor with regen | Yes, once the `regen` setting (8.3) is implemented |
| Voltage-estimated SOC (any motor) | No. SOC doesn't reflect capacity (8.2) |
| No ANT+ LEV broadcast | No. Garmin has no SOC data (8.1) |

---

## 9. Step 6: charts, display ranges and the simulator

Several chart ranges were fitted by eye to the reference bike's typical rate of about 2 pp/km. For a different pack, estimate your typical rate first:

```
pp/km ≈ (Wh per km) / (nominal Wh) × 100
e.g. 12 Wh/km on 500 Wh ≈ 2.4 pp/km; 12 Wh/km on 900 Wh ≈ 1.3 pp/km
```

Then adjust:

| Function | Hard-coded value |
|---|---|
| `drawTrendChart` / `handleChartHover` | y-axis `yMin = 1.0, yMax = 3.2`; curve intercept `1.95`; x-axis label with fixed dates |
| `drawTempChart` | x-axis `8–28 °C`, y-axis `1.0–3.2` |
| `drawQuarterlyChart` | bar scale `(range − 35) / 30` (assumes 35–65 km equivalent range) |
| `drawSimulatorChart` | `rangeMin = 20, rangeMax = 95` km |

**The range simulator (`simulateRange`) is not fitted to your data.** `betaTemp`, `betaSpeed`, `baseRate = 1.95` and the per-mode multipliers `modeMult` are hand-tuned constants for the reference bike, and its "95% CI" is a fixed ±9%. For another bike, do one of the following:

- Take the coefficients from your own `fitModelClient` result (it already returns `betaTemp`), or
- Hide the Simulator tab until it is driven by the fitted model.

---

## 10. What still shows the reference bike's data

At the time of writing, some views ignore your imported data and always show the reference bike's numbers. Hide them or rebuild them before presenting results for another bike:

- **Charging Habits chart** (`drawChargingChart`): fixed histogram bins
- **Storage Idle Sag chart and tooltip** (`drawIdleChart`, the idle branch of `handleChartHover`): fixed medians and maxima
- **Storage Idle Sag KPI** (`kpiIdleDrop`): always `0.0`
- **Insights panel** (`.insights-card` in the HTML): static text with reference-bike figures
- **Static text in the HTML**: "Garmin Edge 1040", "Nominal 720 Wh Simplo", "Yamaha PW-X3 assist level", and the placeholder KPI values

The browser has no charging or idle-gap analysis yet; only the Python prototype (`scripts/analyze_ebike_charging.py`) computes it. Porting `classify_gap` and `intervals()` to JS would make these views real for any bike.

### Python prototype (optional)

If you use the headless scripts, also change:

- `NOMINAL_WH = 720` and the `bike` / `motor` / `battery` strings in `analyze_ebike_battery.py` (`main`, `plot`, `report`)
- the `bike` string in `analyze_ebike_charging.py`
- text in the generated reports that describes the reference bike's own history (for example the "latest ride" and "old 9.4%" sentences)

---

## 11. Step 7: tests

- `tests/test_browser_engine.py` checks decoder output against one of the author's FIT files (which is git-ignored) and checks solver output against the demo seed. These expected values describe the **reference bike**. Leave them alone if you keep the demo seed.
- Add tests for your own changes with **synthetic data**, not your real FIT files:
  - the new mode codes going into `processFitToRide` / `extract`
  - your sentinel value (section 6.1)
  - any threshold you changed
  - optional mode columns (5.3) and the regen rules (8.3), if you implement them
- Run `PYTHONPATH=. .venv/bin/pytest -q` before you consider the change done.

---

## 12. Checklist

- [ ] Step 0 script shows `ebike_battery_level`, `distance` and `timestamp` in your files
- [ ] SOC steps down smoothly (not voltage-estimated); regen noted if present (section 8)
- [ ] Bike profile set: nominal Wh and baseline date
- [ ] Personal Pack started (not merged into the demo)
- [ ] Assist modes mapped, grouped, replaced by assist % or disabled (sections 5–5.3), in JS **and** Python
- [ ] Sentinel value and SOC resolution checked, and changed in both engines if needed
- [ ] Filter thresholds reviewed for your pack size, identical in JS and Python
- [ ] Chart ranges fit your pp/km
- [ ] Simulator re-derived or hidden
- [ ] Views that still show reference-bike data (section 10) hidden or rebuilt
- [ ] Tests added with synthetic data; full suite passes
- [ ] No personal FIT, CSV or JSON data, timestamps or hashes committed

---

## 13. Suggested refactor: one bike profile object

Most of this guide exists because bike-specific values are spread across the code. Moving them into a single `BIKE_PROFILE` object in `web/index.html` would reduce customisation to editing that one object. The same object could be exported as JSON for the Python prototype. For example:

```js
const BIKE_PROFILE = {
  name: 'Raymon Trailray 160e', motor: 'Yamaha PW-X3', nominalWh: 720,
  assistModes: { reference: 7, controls: [5, 3, 2] },   // sensor-specific codes; null = no modes (5.3)
  assistLevelPercent: false,                             // use field 120 instead of modes (5.2)
  regen: false,                                          // allow SOC rises while moving (8.3)
  extraControls: [],                                     // e.g. ['ascent_m_per_km'] for hub motors (8.4)
  socSentinel: 50, socResolutionPp: 1,
  filters: { minKm: 8, minDropPp: 15, jumpPp: 3, reboundPp: 2, minCoverage: 0.9 },
  chart: { ppPerKmRange: [1.0, 3.2], tempRangeC: [8, 28], simRangeKm: [20, 95] },
};
```

With this in place, "adapt to a new bike" means: run step 0, fill in the object, and run the tests.
