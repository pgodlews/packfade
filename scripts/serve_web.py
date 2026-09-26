#!/usr/bin/env python3
"""Local server for Packfade - E-Bike Battery Health Studio.

Serves the zero-install web UI and provides optional local USB bridge
endpoints for the connected Garmin Edge device.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
REPORTS_DIR = ROOT / "reports/ebike_battery"
STEP_TIMEOUT_S = 180
# One device sync at a time: the threaded server keeps serving the UI while a sync runs.
SYNC_LOCK = threading.Lock()


def get_device_status():
    pull_tool = ROOT / "build/pull_edge"
    if not pull_tool.exists():
        return {"connected": False, "error": "build/pull_edge not compiled"}

    try:
        p = subprocess.run([str(pull_tool), "--list"], capture_output=True, text=True, timeout=6)
        if p.returncode != 0:
            err = p.stderr.strip() or p.stdout.strip() or "Device check returned non-zero code"
            return {"connected": False, "error": err}

        m_dev = re.search(r"Device \d+ \(VID=([0-9a-fA-F]+) and PID=([0-9a-fA-F]+)\) is a ([^.]+)\.", p.stdout)
        m_act = re.search(r"Activities: (\d+); bytes: (\d+)", p.stdout)
        device_name = m_dev.group(3).strip() if m_dev else "Garmin Edge Device"
        vid = m_dev.group(1) if m_dev else "091e"
        pid = m_dev.group(2) if m_dev else "4f03"
        act_count = int(m_act.group(1)) if m_act else 0
        bytes_count = int(m_act.group(2)) if m_act else 0

        return {
            "connected": True,
            "device": device_name,
            "vid": vid,
            "pid": pid,
            "activities_on_device": act_count,
            "total_bytes": bytes_count,
        }
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "Device probe timed out (device may be busy)"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def get_latest_data():
    analysis_path = REPORTS_DIR / "analysis.json"
    charging_path = REPORTS_DIR / "charging_profile.json"
    analysis = json.loads(analysis_path.read_text()) if analysis_path.exists() else None
    charging = json.loads(charging_path.read_text()) if charging_path.exists() else None
    return {"analysis": analysis, "charging": charging}


def stop_process(proc):
    """Kill a sync step and everything it started (e.g. pull_edge under the importer)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)  # the step runs in its own session/process group
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def run_streamed(cmd, on_line, timeout=STEP_TIMEOUT_S):
    """Run cmd, calling on_line for each non-empty output line; return (returncode, last five lines).

    The deadline covers the whole step, including a child that stalls with stdout still open:
    lines are read on a helper thread, so waiting for the next line is itself bounded. On timeout
    (or any error) the child's process group is killed, and TimeoutExpired is raised.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            start_new_session=hasattr(os, "killpg"))
    lines = queue.Queue()

    def pump():
        for raw in iter(proc.stdout.readline, ""):
            lines.put(raw)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout
    last_lines = []
    finished = False
    try:
        while True:
            try:
                raw = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise subprocess.TimeoutExpired(cmd, timeout) from None
            if raw is None:
                break
            line = raw.strip()
            if line:
                last_lines = (last_lines + [line])[-5:]
                on_line(line)
        code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        finished = True
        return code, last_lines
    finally:
        if not finished:
            stop_process(proc)


def sync_device():
    py = ROOT / ".venv/bin/python"
    if not py.exists():
        py = "python3"
    try:
        # 1. Run import ebike history
        p1 = subprocess.run([str(py), str(ROOT / "scripts/import_ebike_history.py")],
                            capture_output=True, text=True, check=True, timeout=STEP_TIMEOUT_S)
        # 2. Run analyze battery
        p2 = subprocess.run([str(py), str(ROOT / "scripts/analyze_ebike_battery.py")],
                            capture_output=True, text=True, check=True, timeout=STEP_TIMEOUT_S)
        # 3. Run analyze charging
        p3 = subprocess.run([str(py), str(ROOT / "scripts/analyze_ebike_charging.py")],
                            capture_output=True, text=True, check=True, timeout=STEP_TIMEOUT_S)

        return {
            "success": True,
            "data": get_latest_data(),
            "import_log": p1.stdout.strip(),
        }
    except subprocess.TimeoutExpired as e:
        return {"success": False, "error": f"Device sync timed out: {e}"}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": e.stderr or e.stdout or str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


class PackfadeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def is_trusted_host(self):
        host = self.headers.get("Host", "").split(":")[0].strip().lower()
        return host in ("localhost", "127.0.0.1", "::1", "[::1]")

    def get_allowed_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return None
        parsed = re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", origin)
        return origin if parsed else None

    def send_cors_headers(self):
        allowed = self.get_allowed_origin()
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")

    def do_GET(self):
        if not self.is_trusted_host():
            self.send_error(403, "Forbidden: Invalid Host header")
            return

        if self.path == "/api/device":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            info = get_device_status()
            self.wfile.write(json.dumps(info).encode("utf-8"))
            return

        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            data = get_latest_data()
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        if self.path == "/api/sync-stream":
            if self.headers.get("Origin") and not self.get_allowed_origin():
                self.send_error(403, "Forbidden: Untrusted Origin")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_cors_headers()
            self.end_headers()
            self.stream_sync()
            return

        super().do_GET()

    def stream_sync(self):
        py = ROOT / ".venv/bin/python"
        if not py.exists():
            py = "python3"

        def send(step, pct, msg, detail="", done=False, error=None, data=None):
            payload = {
                "step": step,
                "percent": pct,
                "message": msg,
                "detail": detail,
                "done": done,
                "error": error,
                "data": data
            }
            try:
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        dev_info = get_device_status()
        device_label = dev_info.get("device", "Garmin Device") if dev_info.get("connected") else "Garmin Edge"
        send("connect", 3, f"Connecting to {device_label} over USB MTP...", "Checking device connection")

        if not SYNC_LOCK.acquire(blocking=False):
            send("error", 0, "A device sync is already running", done=True, error="Sync already in progress")
            return
        try:
            # Step 1: Import activities & filter battery info
            send("transfer", 8, f"Reading activity files from {device_label}...", "Running pull_edge")

            def on_import_line(line):
                if "Copied" in line:
                    m = re.search(r"Copied (\d+) activities", line)
                    if m:
                        cnt = int(m.group(1))
                        pct = min(40, 8 + int((cnt / 200.0) * 32))
                        send("transfer", pct, f"Transferring from {device_label}: Copied {cnt} activities...", line)
                elif "Activities: " in line:
                    send("transfer", 42, "USB file transfer complete. Scanning for battery telemetry...", line)
                elif "Filtering activity files:" in line or "Filtering for battery" in line:
                    m = re.search(r"(\d+)/(\d+) inspected", line)
                    if m:
                        cur, tot = int(m.group(1)), int(m.group(2))
                        pct = min(68, 42 + int((cur / max(1, tot)) * 26))
                        send("filter", pct, f"Filtering files: {cur}/{tot} inspected for battery info...", line)
                    else:
                        send("filter", 45, line, line)
                else:
                    send("transfer", 20, line, line)

            code, last_lines = run_streamed([str(py), "-u", str(ROOT / "scripts/import_ebike_history.py")], on_import_line)
            if code != 0:
                err_detail = " | ".join(last_lines) or "Import failed"
                send("error", 0, "Garmin USB import encountered an error", detail=err_detail, done=True, error=err_detail)
                return

            # Step 2: Battery Degradation Modeling
            send("modeling", 70, "Fitting battery degradation trend models (Huber-WLS + bootstrap)...", "Huber IRLS regression")
            code, last_lines = run_streamed(
                [str(py), "-u", str(ROOT / "scripts/analyze_ebike_battery.py")],
                lambda line: send("modeling", 78, "Fitting bootstrap iterations for 95% confidence bands...", line))
            if code != 0:
                err_detail = " | ".join(last_lines) or "Battery analysis failed"
                send("error", 0, "Error fitting battery models", detail=err_detail, done=True, error=err_detail)
                return

            # Step 3: Charging & Idle Storage Sag Analysis
            send("charging", 88, "Reconstructing charging intervals and storage idle sag...", "Classifying gaps and self-discharge")
            code, last_lines = run_streamed(
                [str(py), "-u", str(ROOT / "scripts/analyze_ebike_charging.py")],
                lambda line: send("charging", 94, "Analyzing charging habits and parasitic loss...", line))
            if code != 0:
                err_detail = " | ".join(last_lines) or "Charging analysis failed"
                send("error", 0, "Error analyzing charging history", detail=err_detail, done=True, error=err_detail)
                return

            # Step 4: Done!
            fresh_data = get_latest_data()
            rides_count = len(fresh_data.get("analysis", {}).get("per_ride", [])) if fresh_data else 0
            send("complete", 100, f"Sync complete! {rides_count} battery rides processed.", "All degradation and charging models updated", done=True, data=fresh_data)
        except subprocess.TimeoutExpired:
            send("error", 0, f"Sync step timed out after {STEP_TIMEOUT_S // 60} minutes; it was stopped",
                 done=True, error="Subprocess timeout")
        except Exception as e:
            send("error", 0, str(e), done=True, error=str(e))
        finally:
            SYNC_LOCK.release()

    def do_POST(self):
        if not self.is_trusted_host():
            self.send_error(403, "Forbidden: Invalid Host header")
            return

        if self.path == "/api/sync":
            if self.headers.get("Origin") and not self.get_allowed_origin():
                self.send_error(403, "Forbidden: Untrusted Origin")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            if SYNC_LOCK.acquire(blocking=False):
                try:
                    res = sync_device()
                finally:
                    SYNC_LOCK.release()
            else:
                res = {"success": False, "error": "A device sync is already running"}
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        self.send_error(404, "Unknown endpoint")

    def do_OPTIONS(self):
        if not self.is_trusted_host():
            self.send_error(403, "Forbidden: Invalid Host header")
            return
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def run_server(port=8080, host="127.0.0.1"):
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, p), PackfadeHandler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print(f"Failed to bind on ports {port}-{port+9}", file=sys.stderr)
        sys.exit(1)

    print(f"Packfade server running at http://localhost:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


def main():
    run_server(port=8080)


if __name__ == "__main__":
    main()
