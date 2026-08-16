const fs = require('fs');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function connect() {
  let tabs;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      tabs = await (await fetch('http://127.0.0.1:9333/json/list')).json();
      break;
    } catch {
      await sleep(250);
    }
  }
  if (!tabs) throw new Error('CDP unavailable');
  const tab = tabs.find((item) => item.type === 'page');
  const socket = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  let id = 0;
  const pending = new Map();
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    id += 1;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { socket, call };
}

(async () => {
  const { socket, call } = await connect();
  await call('Page.enable');
  await call('Runtime.enable');
  const targets = [
    ['pricing', '/app/pricing'],
    ['profile', '/app/profile'],
    ['stats', '/app/stats?state=data'],
    ['wizard-fx', '/app/generate?project=project_1&qaStage=3'],
    ['tiktok', '/app/projects/project_1/post?qaPost=valid']
  ];
  fs.mkdirSync('tmp/qa', { recursive: true });
  for (const [name, path] of targets) {
    await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1024, deviceScaleFactor: 1, mobile: false });
    await call('Page.navigate', { url: `http://127.0.0.1:5173${path}` });
    await sleep(1800);
    const audit = await call('Runtime.evaluate', {
      expression: `JSON.stringify({url:location.href,title:document.title,bodyScroll:[document.documentElement.scrollWidth,document.documentElement.clientWidth,document.documentElement.scrollHeight,document.documentElement.clientHeight],overflows:[...document.querySelectorAll('*')].filter(e=>getComputedStyle(e).overflowX==='visible'&&e.scrollWidth>e.clientWidth+1).slice(0,20).map(e=>({tag:e.tagName,cls:e.className,sw:e.scrollWidth,cw:e.clientWidth}))})`,
      returnByValue: true
    });
    fs.writeFileSync(`tmp/qa/${name}.json`, audit.result.value);
    const shot = await call('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
    fs.writeFileSync(`tmp/qa/${name}.png`, Buffer.from(shot.data, 'base64'));
  }
  socket.close();
})().catch((error) => { console.error(error); process.exitCode = 1; });
