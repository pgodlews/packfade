"""Run the browser engine from web/index.html in Node, with a minimal DOM stub.

The whole application script is loaded (nothing runs until DOMContentLoaded, which never fires),
plus the FIT import functions that live inside setupEventListeners(), so tests call the real code.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web/index.html").read_text("utf8")
SEED = ROOT / "web/seed_data.js"

ENGINE = HTML[HTML.index("const DB_NAME = 'PackfadeDB';"):HTML.rindex("</script>")]
IMPORT_FNS = HTML[HTML.index("  function parseFitActivity("):HTML.index("  // File Input change")]

PRELUDE = """
const fs = require('fs');
const elements = {};
function makeEl() {
  return {
    style: {}, dataset: {}, value: '', textContent: '', innerHTML: '', checked: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, setAttribute() {}, getAttribute() { return null; }, addEventListener() {},
    querySelector() { return makeEl(); }, querySelectorAll() { return []; }, getContext() { return null; }
  };
}
global.document = {
  getElementById: id => (elements[id] = elements[id] || makeEl()),
  createElement: () => makeEl(),
  querySelector: () => makeEl(),
  querySelectorAll: () => []
};
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.localStorage = { getItem() { return null; }, setItem() {} };
const alerts = [];
global.alert = msg => alerts.push(String(msg));
let confirmAnswer = true;
global.confirm = () => confirmAnswer;
const consoleLines = [];
"""

# Replace IndexedDB, rendering and the sync modal with recorders once the engine is loaded.
STUBS = """
const idb = {};
idbSet = async (k, v) => { idb[k] = JSON.parse(JSON.stringify(v)); };
idbGet = async k => (k in idb ? idb[k] : null);
drawChart = () => {};
renderDashboard = () => {};
openSyncModal = () => {};
appendSyncConsole = (line) => consoleLines.push(line);
const progress = [];
updateSyncProgress = (step, pct, message, detail) => progress.push({ step, message, detail });
const fitFile = (name, path) => ({ name, arrayBuffer: async () => { const b = fs.readFileSync(path); return b.buffer.slice(b.byteOffset, b.byteOffset + b.length); } });
function loadSeed() {
  eval(fs.readFileSync(SEED_PATH, 'utf8'));
  return JSON.parse(JSON.stringify(window.SEED_EBIKE_DATA));
}
"""


def run_engine(body):
    """Run `body` inside an async function after loading the engine; it must return a JSON-able value."""
    script = "\n".join([
        PRELUDE,
        f"const SEED_PATH = {json.dumps(str(SEED))};",
        ENGINE,
        IMPORT_FNS,
        STUBS,
        "(async () => {",
        body,
        "})().then(out => process.stdout.write(JSON.stringify(out === undefined ? null : out)),",
        "          err => { console.error(err && err.stack || err); process.exit(1); });",
    ])
    res = subprocess.run(["node", "-"], input=script, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    return json.loads(res.stdout)
