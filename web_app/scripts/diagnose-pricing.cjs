const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
(async () => {
  const tabs = await (await fetch('http://127.0.0.1:9333/json/list')).json();
  const socket = new WebSocket(tabs.find((item) => item.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') console.error('EXCEPTION', JSON.stringify(message.params.exceptionDetails));
    if (!message.id || !pending.has(message.id)) return;
    const pair = pending.get(message.id); pending.delete(message.id); pair.resolve(message.result);
  });
  const call = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, { resolve }); socket.send(JSON.stringify({ id, method, params })); });
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Page.navigate', { url: 'http://127.0.0.1:5173/app/pricing' });
  await sleep(1800);
  const result = await call('Runtime.evaluate', { expression: `JSON.stringify({text:document.body.innerText,root:document.querySelector('#root')?.innerHTML.slice(0,500),bg:getComputedStyle(document.body).backgroundColor})`, returnByValue: true });
  console.log(result.result.value);
  socket.close();
})().catch((error) => { console.error(error); process.exitCode = 1; });
