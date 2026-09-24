const http = require('node:http');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Ensure docs/images directory exists
const outDir = path.resolve(__dirname, '../docs/images');
fs.mkdirSync(outDir, { recursive: true });

async function run() {
  console.log('Starting static server on port 8089...');
  const serverProcess = spawn('python3', ['-m', 'http.server', '8089', '-d', 'web']);
  await new Promise(r => setTimeout(r, 1200));

  console.log('Launching headless Chrome with remote debugging...');
  // Fresh profile: empty IndexedDB, so the page always shows the anonymised demo dataset.
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), 'packfade-shots-'));
  const chromeProcess = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
    '--headless=new',
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--disable-gpu',
    '--remote-debugging-port=9222',
    '--window-size=1440,960',
    'about:blank'
  ]);

  await new Promise(r => setTimeout(r, 1500));

  try {
    const listRes = await fetch('http://127.0.0.1:9222/json/list');
    const tabs = await listRes.json();
    const pageTab = tabs.find(t => t.type === 'page') || tabs[0];
    if (!pageTab) throw new Error('No page tab found');

    console.log('Connecting to page WebSocket:', pageTab.webSocketDebuggerUrl);
    const ws = new WebSocket(pageTab.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
    });

    let id = 1;
    function send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const msgId = id++;
        const handler = (evt) => {
          try {
            const data = JSON.parse(evt.data);
            if (data.id === msgId) {
              ws.removeEventListener('message', handler);
              if (data.error) reject(data.error);
              else resolve(data.result);
            }
          } catch (e) {
            // ignore
          }
        };
        ws.addEventListener('message', handler);
        ws.send(JSON.stringify({ id: msgId, method, params }));
      });
    }

    console.log('Setting device metrics (1440x920, 2x Retina)...');
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 920,
      deviceScaleFactor: 2,
      mobile: false
    });

    console.log('Navigating to http://127.0.0.1:8089/ ...');
    await send('Page.navigate', { url: 'http://127.0.0.1:8089/' });

    // Wait for DOMContentLoaded and async database / calculation
    console.log('Waiting for model fitting and chart rendering...');
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 500));
      const res = await send('Runtime.evaluate', {
        expression: `Boolean(window.currentData && window.currentData.analysis && window.currentData.analysis.models && Object.keys(window.currentData.analysis.models).length > 0 && document.querySelector('#mainChart'))`
      });
      if (res.result && res.result.value === true) {
        console.log('Dashboard fully initialized in browser!');
        break;
      }
    }

    // Additional settling time for canvas rendering
    await new Promise(r => setTimeout(r, 2000));

    async function capture(file, expression) {
      if (expression) {
        await send('Runtime.evaluate', { expression });
        await new Promise(r => setTimeout(r, 1200));
      }
      console.log(`Capturing ${file} ...`);
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(outDir, file), Buffer.from(shot.data, 'base64'));
    }
    // Show one chart tab with the chart panel at the top of the viewport.
    const showTab = tab => `
      document.querySelector('button[data-tab="${tab}"]')?.click();
      document.querySelector('.workspace-card')?.scrollIntoView({ behavior: 'instant', block: 'start' });
    `;

    await capture('dashboard_hero.png');
    await capture('capacity_trend.png', showTab('capacity'));
    await capture('range_simulator.png', showTab('simulator'));
    await capture('charging_habits.png', showTab('charging'));
    await capture('storage_idle_sag.png', showTab('idle'));
    await capture('activity_audit_table.png', `
      document.querySelector('button[data-tab="capacity"]')?.click();
      document.querySelector('.detail-grid')?.scrollIntoView({ behavior: 'instant' });
    `);

    console.log('All 6 screenshots captured successfully in docs/images/ !');
    ws.close();
  } finally {
    await new Promise(resolve => { chromeProcess.once('exit', resolve); chromeProcess.kill(); });
    serverProcess.kill();
    fs.rmSync(profileDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

run().catch(err => {
  console.error('Error during screenshot capture:', err);
  process.exit(1);
});
