import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
const root = fileURLToPath(new URL('../', import.meta.url));
process.chdir(root);
let backend, frontend, keepAlive, stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true; clearInterval(keepAlive); frontend?.kill(); backend?.kill(); process.exitCode = code;
}
process.on('SIGINT', () => stop()); process.on('SIGTERM', () => stop());
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function config() {
  try { const response = await fetch('http://127.0.0.1:8765/api/config', {signal: AbortSignal.timeout(1500)}); return response.ok ? await response.json() : null; }
  catch { return null; }
}
async function devPortAvailable() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once('error', error => error.code === 'EADDRINUSE' ? resolve(false) : reject(error));
    probe.listen(5173, '127.0.0.1', () => probe.close(() => resolve(true)));
  });
}
async function isProjectFrontend(ready) {
  try {
    const options = {signal: AbortSignal.timeout(2000)};
    const response = await fetch('http://127.0.0.1:5173/', options);
    const html = await response.text();
    const api = await fetch('http://127.0.0.1:5173/api/config', {signal: AbortSignal.timeout(2000)});
    const remote = await api.json();
    return response.ok && api.ok && remote.token === ready.token &&
      html.includes('中国小城 · 创作台') && html.includes('/src/main.jsx');
  } catch { return false; }
}
try {
  let ready = await config();
  if (!ready) {
    const candidates = [process.env.CHINA_TRAVEL_PYTHON,
      join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
      join(homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')];
    const python = candidates.find(path => path && existsSync(path)) || (process.platform === 'win32' ? 'python' : 'python3');
    backend = spawn(python, ['-u', 'local_app.py', '--no-browser'], {cwd: root, stdio: 'inherit', windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}});
    backend.on('error', error => { console.error('Python 启动失败：', error.message); stop(1); });
    backend.on('exit', code => { if (!stopping) { console.error('Python 后端退出。'); stop(code || 1); } });
    for (let i = 0; i < 40 && !ready && !stopping; i++) { await delay(250); ready = await config(); }
    if (!ready) throw Error('Python 后端未就绪，请检查上面的日志。');
  }
  if (ready.version !== 2) throw Error('8765 端口正在运行旧版后端，请先关闭旧主页后重新运行 npm run dev。');
  if (process.argv.includes('--production')) {
    if (!existsSync(resolve('dist/index.html'))) throw Error('请先运行 npm run build。');
    console.log('本地主页：http://127.0.0.1:8765'); keepAlive = setInterval(() => {}, 1000);
  } else {
    if (!(await devPortAvailable())) {
      if (!(await isProjectFrontend(ready))) throw Error('5173 端口被其他程序占用，请关闭占用程序后重试。');
      console.log('创作台已在运行：http://127.0.0.1:5173');
      console.log('直接打开上面的地址即可；不会重复启动或中断已有任务。');
      if (backend) keepAlive = setInterval(() => {}, 1000);
    } else {
      frontend = spawn(process.execPath, [resolve('node_modules/vite/bin/vite.js')], {stdio: 'inherit', windowsHide: true});
      frontend.on('error', error => { console.error(error.message); stop(1); });
      frontend.on('exit', code => stop(code || 0));
    }
  }
} catch (error) { console.error(error.message); stop(1); }
