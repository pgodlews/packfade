from pathlib import Path
from scripts.serve_web import get_device_status, get_latest_data


def test_web_static_files():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    seed = root / "web/seed_data.js"
    assert index.exists(), "web/index.html must exist"
    assert seed.exists(), "web/seed_data.js must exist"

    html = index.read_text(encoding="utf-8")
    assert "Packfade" in html
    assert "PackfadeDB" in html
    assert "sohCircle" in html
    assert "seed_data.js" in html
    assert "syncModalOverlay" in html
    assert "syncProgressFill" in html
    assert "syncConsole" in html
    assert "simPanel" in html
    assert "simTempSlider" in html
    assert "choleskyDecompose" in html
    assert "solveWeightedLinearSystem" in html
    assert "simulateRange" in html
    assert "fitModelClient" in html


def test_serve_web_data():
    data = get_latest_data()
    assert "analysis" in data
    assert "charging" in data
    if data["analysis"]:
        assert "models" in data["analysis"]
        assert "per_ride" in data["analysis"]
    if data["charging"]:
        assert "likely_charges" in data["charging"]


def test_device_status_schema():
    status = get_device_status()
    assert isinstance(status, dict)
    assert "connected" in status


def test_ride_inclusion_and_checkbox_table():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    html = index.read_text(encoding="utf-8")
    assert "selectAllCheckbox" in html
    assert "presetCleanBtn" in html
    assert "presetShortBtn" in html
    assert "presetJumpsBtn" in html
    assert "presetAllBtn" in html
    assert "toggleRideInclusion" in html
    assert "applyInclusionPreset" in html
    assert "isRideIncluded" in html
    assert "recomputeModelFromInclusions" in html
    assert "rideTotalHeader" in html
    assert "rideIncludedHeader" in html


def test_server_security_cors_and_host_validation():
    from scripts.serve_web import PackfadeHandler

    class DummyHandler:
        is_trusted_host = PackfadeHandler.is_trusted_host
        get_allowed_origin = PackfadeHandler.get_allowed_origin

    h = DummyHandler()
    h.headers = {"Host": "localhost:8080"}
    assert h.is_trusted_host() is True

    h.headers = {"Host": "127.0.0.1:8080"}
    assert h.is_trusted_host() is True

    h.headers = {"Host": "attacker.com"}
    assert h.is_trusted_host() is False

    h.headers = {"Origin": "http://localhost:8080"}
    assert h.get_allowed_origin() == "http://localhost:8080"

    h.headers = {"Origin": "https://evil-site.com"}
    assert h.get_allowed_origin() is None


def test_dataset_isolation_ui_and_parity_guards():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    html = index.read_text(encoding="utf-8")
    assert "datasetModeChip" in html
    assert "datasetModeLabel" in html
    assert "btnLoadDemo" in html
    assert "btnClearData" in html
    assert "battery_time_coverage_under_90pct" in html
    assert "missing_adjustment_covariate" in html
    assert "rideKey" in html
    # Ensure field 118 (ebike_battery_level) is parsed, and field 81 is not mistakenly mapped to battery
    assert "f.fieldNum === 118" in html
    assert "f.fieldNum === 81" not in html


def test_honest_statistics_and_dynamic_chart_guards():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    html = index.read_text(encoding="utf-8")

    # Hardcoded synthetic distribution bins must not be present
    assert "bins[0] = 2; bins[1] = 5" not in html

    # Hardcoded fake confidence interval fallbacks must not be present
    assert "apparentSoh * 0.92" not in html
    assert "beta * 0.5" not in html

    # Canvas context should not use unparsed CSS variables
    assert "ctx.fillStyle = 'var(--" not in html

    # SOH badge color reset
    assert "badge.style.color = '';" in html


def test_security_xss_sanitization_and_dynamic_ui_states():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    html = index.read_text(encoding="utf-8")

    # XSS sanitization helper must exist and be used in table & modal
    assert "function escapeHtml(" in html
    assert "onchange=\"toggleRideInclusion('${r.date}'" not in html
    assert "onchange=\"toggleRideInclusion('${ride.date}'" not in html

    # Initial pill must not falsely claim Edge 1040 USB Ready before probing
    assert "Garmin Edge 1040 (USB Ready)" not in html
    assert "Local Mode (Zero-Cloud)" in html

    # USB sync must preserve browser data via mergeSyncedData rather than clobbering
    assert "mergeSyncedData" in html
    assert "insightChargingText" in html
    assert "insightStorageText" in html
    assert "insightAnomalyText" in html


def test_km_miles_unit_toggle():
    root = Path(__file__).resolve().parent.parent
    index = root / "web/index.html"
    html = index.read_text(encoding="utf-8")

    # Unit toggle elements in navbar and settings
    assert 'id="unitToggle"' in html
    assert 'id="unitBtnKm"' in html
    assert 'id="unitBtnMi"' in html
    assert 'id="settingDistanceUnit"' in html

    # KPI unit labels
    assert 'id="kpiConsumptionUnit"' in html
    assert 'id="kpiRangeUnit"' in html
    assert 'id="kpiDistanceUnit"' in html

    # Table headers
    assert 'id="thDistHeader"' in html
    assert 'id="thRateHeader"' in html
    assert 'id="thRangeHeader"' in html
    assert 'id="thSpeedHeader"' in html

    # Conversion functions & state
    assert "function setUnit(" in html
    assert "function toUserDist(" in html
    assert "function toUserRate(" in html
    assert "function toUserSpeed(" in html
    assert "packfade_unit" in html
