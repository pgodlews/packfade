import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def webusb_reader_js():
    html = (ROOT / "web/index.html").read_text("utf8")
    start = html.index("/* ────────────── WebUSB: read-only MTP import")
    end = html.index("/* ────────────── end WebUSB MTP reader")
    return html[start:end]


# A fake Garmin Edge that answers MTP over a WebUSB-shaped API, with replies split
# into uneven chunks and zero-length packets, like real bulk transfers.
FAKE_EDGE = r"""
const ROOT = 0xFFFFFFFF, STORAGE = 0x00010001, FOLDER = 0x3001, UNDEFINED = 0x3000;
const fitBytes = Uint8Array.from({ length: 70000 }, (_, i) => (i * 31) % 251);
const objects = {
  1: { parent: ROOT, format: FOLDER, name: 'GARMIN' },
  2: { parent: 1, format: FOLDER, name: 'Activities' },
  3: { parent: 2, format: UNDEFINED, name: '2026-09-15-16-54-27.FIT', data: fitBytes },
  4: { parent: 2, format: UNDEFINED, name: 'notes.txt', data: new Uint8Array([1, 2, 3]) },
  5: { parent: ROOT, format: FOLDER, name: 'Music' },
};
const sentOps = [];
let queue = new Uint8Array(0), released = false, closed = false, chunkNo = 0;

function container(type, code, tid, payload) {
  const out = new Uint8Array(12 + payload.length);
  const v = new DataView(out.buffer);
  v.setUint32(0, out.length, true); v.setUint16(4, type, true); v.setUint16(6, code, true); v.setUint32(8, tid, true);
  out.set(payload, 12);
  return out;
}
function u32s(values) {
  const out = new Uint8Array(4 * values.length); const v = new DataView(out.buffer);
  values.forEach((x, i) => v.setUint32(4 * i, x, true)); return out;
}
function objectInfo(o) {
  const name = o.name + '\0';
  const out = new Uint8Array(53 + 2 * name.length + 3); const v = new DataView(out.buffer);
  v.setUint32(0, STORAGE, true); v.setUint16(4, o.format, true); v.setUint32(8, o.data ? o.data.length : 0, true);
  v.setUint32(38, o.parent, true); v.setUint8(52, name.length);
  for (let i = 0; i < name.length; i++) v.setUint16(53 + 2 * i, name.charCodeAt(i), true);
  return out;
}
function enqueue(...parts) {
  const merged = new Uint8Array(queue.length + parts.reduce((a, p) => a + p.length, 0));
  merged.set(queue); let o = queue.length; for (const p of parts) { merged.set(p, o); o += p.length; }
  queue = merged;
}
const device = {
  productName: 'Edge 1040', productId: 0x4f03, configuration: null,
  async open() {}, async close() { closed = true; },
  async selectConfiguration() {
    this.configuration = { interfaces: [
      { interfaceNumber: 0, alternate: { interfaceClass: 3, endpoints: [] } },
      { interfaceNumber: 1, alternate: { interfaceClass: 6, endpoints: [
        { endpointNumber: 1, direction: 'in', type: 'bulk' },
        { endpointNumber: 2, direction: 'out', type: 'bulk' },
        { endpointNumber: 3, direction: 'in', type: 'interrupt' } ] } } ] };
  },
  async claimInterface(n) { if (n !== 1) throw new Error('wrong interface'); },
  async releaseInterface() { released = true; },
  async transferOut(ep, buf) {
    const v = new DataView(buf); const code = v.getUint16(6, true), tid = v.getUint32(8, true);
    const params = Array.from({ length: (v.getUint32(0, true) - 12) / 4 }, (_, i) => v.getUint32(12 + 4 * i, true));
    sentOps.push(code);
    const ok = () => container(3, 0x2001, tid, new Uint8Array(0));
    if (code === 0x1004) enqueue(container(2, code, tid, u32s([1, STORAGE])), ok());
    else if (code === 0x1007) {
      const kids = Object.keys(objects).map(Number).filter(h => objects[h].parent === params[2]);
      enqueue(container(2, code, tid, u32s([kids.length, ...kids])), ok());
    } else if (code === 0x1008) enqueue(container(2, code, tid, objectInfo(objects[params[0]])), ok());
    else if (code === 0x1009) enqueue(container(2, code, tid, objects[params[0]].data), ok());
    else enqueue(ok());
    return { status: 'ok', bytesWritten: buf.byteLength };
  },
  async transferIn(ep, length) {
    chunkNo++;
    if (chunkNo % 7 === 0) return { status: 'ok', data: new DataView(new ArrayBuffer(0)) };  // zero-length packet
    const n = Math.min(length, queue.length, [5, 512, 3000, 64][chunkNo % 4]);
    const out = queue.slice(0, n); queue = queue.subarray(n);
    return { status: 'ok', data: new DataView(out.buffer) };
  },
};
"""


def test_webusb_mtp_reader_imports_only_fit_files_read_only():
    script = FAKE_EDGE + webusb_reader_js() + r"""
    (async () => {
      const files = await readGarminActivitiesOverWebUsb(device);
      const bytes = new Uint8Array(await files[0].arrayBuffer());
      console.log(JSON.stringify({
        names: files.map(f => f.name),
        exact: bytes.length === fitBytes.length && bytes.every((b, i) => b === fitBytes[i]),
        ops: [...new Set(sentOps)].sort(),
        readOnly: sentOps.every(op => MTP_READ_ONLY_OPS.has(op)),
        released, closed,
      }));
    })().catch(e => { console.error(e); process.exit(1); });
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())
    assert out["names"] == ["2026-09-15-16-54-27.FIT"]
    assert out["exact"] is True
    assert out["readOnly"] is True
    assert out["ops"] == sorted([0x1002, 0x1003, 0x1004, 0x1007, 0x1008, 0x1009])
    assert out["released"] and out["closed"]


def test_webusb_mtp_reader_refuses_write_operations():
    script = FAKE_EDGE + webusb_reader_js() + r"""
    (async () => {
      const mtp = new MtpReader(device);
      await mtp.open();
      try { await mtp.transaction(0x100B, [3]); console.log('sent'); }  // DeleteObject
      catch (e) { console.log(sentOps.includes(0x100B) ? 'sent' : 'refused'); }
    })();
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "refused"
