# AGENTS.md

Instructions, architecture notes, and non-negotiable rules for coding agents and contributors working in `packfade`.

---

## 1. Project Overview & Layout

**Packfade** is an open-source, local-first State of Health (SOH) estimation system for e-bikes communicating over ANT+ LEV (e.g. Yamaha PW-X3, Simplo 720 Wh).

### Directory Structure
```
packfade/
├── packfade/                 # Core Python package
│   ├── __init__.py          # Package metadata & version
│   ├── cli.py               # packfade CLI entry point (serve, analyze, charging)
│   ├── battery_history.py   # SOC quality review, 50% glitch detection, gap classifications
│   └── fit.py               # Garmin FIT SDK helpers, field parsers, battery check
├── scripts/                  # Standalone execution tools & report generators
│   ├── analyze_ebike_battery.py   # Multi-covariate Huber-WLS degradation analysis
│   ├── analyze_ebike_charging.py  # Inter-ride charging & storage sag analysis
│   ├── serve_web.py               # Local HTTP API & USB bridge for the web app
│   ├── make_demo_seed.py          # Anonymises web/seed_data.js (00:00 times, ±3-day date jitter)
│   ├── pull_activities.py         # MTP USB download wrapper for Garmin devices
│   └── build_usb.sh               # Native libmtp C helper build script
├── web/                      # Standalone client-side application
│   ├── index.html           # Zero-build UI (PackfadeDB, pure Canvas charts, JS linear algebra)
│   └── seed_data.js         # Offline seed dataset (82 clean rides, 96 total)
├── docs/                     # Engineering documentation
│   ├── CUSTOMISATION.md           # Adapting Packfade to a different e-bike (modes, sentinel, filters)
│   ├── MATHEMATICAL_MODEL.md      # Mathematical derivation (WLS, Huber, Bootstrap, Arrhenius)
│   └── ANT_PLUS_BATTERY_ANOMALIES.md # Telemetry glitch analysis & rectification
├── data/                     # Local data directory (GIT-IGNORED)
├── reports/                  # Generated analysis reports & plots (GIT-IGNORED)
└── tests/                    # Pytest test suite
```

---

## 2. Non-Negotiable Rules

### Rule 1: Strict Data Privacy (Zero Cloud / No Leakage)
- **Never commit `.fit` files, `.csv` exports, `.json` dumps containing private GPS coordinates, serial numbers, hostnames, or personal paths.**
- `data/` and `reports/` are git-ignored.
- `web/seed_data.js` is public demo data. Regenerate it only with `scripts/make_demo_seed.py`, which moves every ride to 00:00 UTC on a jittered, distinct date. `tests/test_demo_seed.py` fails if real ride times get back in.
- Web application telemetry stays strictly inside the user's browser via **IndexedDB (`PackfadeDB`)**. No analytics or metrics may be sent to central servers.

### Rule 2: Web UI Dependency-Free Constraint
- `web/index.html` is a single-file, zero-dependency application.
- **Do not introduce build steps (Webpack, Vite, Rollup) or external CDN scripts (React, Chart.js, Tailwind).**
- Visualizations use native HTML5 Canvas (`setupCanvasChart()`), and numerical optimizations use client-side vanilla JavaScript linear algebra (`choleskyDecompose`, Huber IRLS).

### Rule 3: Fail Loudly
- If a FIT file lacks battery records, or if an activity has missing distance or corrupted timestamps, it must fail or be explicitly flagged in `reasons` (e.g. `missing_battery_or_distance`).
- Never silently impute artificial battery values or return fake positive drops.

### Rule 4: Preserve Mathematical Invariants
- When modifying `discharge_metrics()` or `mask_bracketed_50_glitches()`:
  - **Same-side excursion condition:** A glitch excursion to $50\%$ satisfies $(\text{SoC}_{\text{left}} - 50) \cdot (\text{SoC}_{\text{right}} - 50) > 0$.
  - **True crossing preservation:** Any real battery discharge through 50% satisfies $(\text{SoC}_{\text{left}} - 50) \cdot (\text{SoC}_{\text{right}} - 50) \le 0$ and must NEVER be masked.
  - Review `docs/MATHEMATICAL_MODEL.md` before changing weights or loss functions.

### Rule 5: USB & Device Safety
- `scripts/pull_activities.py` and `build/pull_edge` must **only execute read-only operations** (`LIBMTP_Get_File_To_File_Descriptor` / `LIBMTP_Get_File_To_File`).
- Never issue delete, rename, or write commands to a connected Garmin device.
- The browser WebUSB importer (`MtpReader` in `web/index.html`) sends only the read operations in `MTP_READ_ONLY_OPS` and refuses anything else; `tests/test_webusb_mtp.py` enforces this.

### Rule 6: Browser-First Canonical Source of Truth
- **The web application (`web/index.html`) is the primary product and sole canonical source of truth.**
- Python scripts in `scripts/` served as the early exploratory research prototype (POC) and remain available for headless batch replication.
- All live data ingestion, binary FIT decoding, 50% glitch scrubbing, and Huber-WLS solving run natively in client-side JavaScript.
- Avoid maintaining duplicate diverging formulas: the client-side implementation is the production engine.

### Rule 7: Bike-Specific Changes Follow `docs/CUSTOMISATION.md`
- Assist-mode codes, the SOC sentinel, SOC resolution, filter thresholds and chart ranges are bike-specific. Every one of them is listed in `docs/CUSTOMISATION.md`.
- Change each value in **both** engines (`web/index.html` and `scripts/`) in the same change, and update the guide if you move or add a location.
- Never invent assist-mode names or numeric multipliers. Derive mode codes from the user's FIT files (step 0 of the guide).
- Test new bike parameters with synthetic data. Never commit a user's FIT files or a personal seed dataset.

---

## 3. Running Tests

Always run tests from the repository root using the virtual environment:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

All tests must pass cleanly before any code change is considered complete.
