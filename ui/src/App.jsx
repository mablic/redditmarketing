import React, {useCallback, useEffect, useRef, useState} from 'react';

const DEFAULT_PROMPT = '清新明亮的水彩旅行插画，温暖的阳光，绿色山林与土黄色建筑。英文文案简短自然，加入有来源的本地美食。';
const labels = {idle:'尚未开始', running:'正在生成，请保持电脑唤醒', cancelling:'正在停止任务…', done:'生成完成', failed:'生成暂停，请查看日志', finished:'批量已结束', interrupted:'批量已中断，可继续补跑', cancelled:'任务已取消，等待手动启动', needs_attention:'需要处理浏览器登录或验证'};
const cityOf = (folder, cities) => {
  const leaf = folder.split('/').at(-1);
  return cities.find(c => leaf === c.name_zh || leaf.startsWith(c.name_zh + '_'))?.name_zh || '';
};

export function Platform({type, state = {}, current, busy, pending, act, subreddit, setSubreddit}) {
  const reddit = type === 'reddit', name = reddit ? 'Reddit' : 'TikTok';
  const [manual, setManual] = useState('');
  const matched = state.status === 'prepared' && state.folder === current?.folder && (!reddit || state.subreddit === subreddit.replace(/^r\//, ''));
  return <section className="card"><h2>发布到 {name}</h2>
    {reddit && <><label htmlFor="subreddit">目标社区</label><input id="subreddit" value={subreddit} onChange={e => setSubreddit(e.target.value)}/></>}
    <button className="secondary wide" disabled={pending || state.status === 'busy'} onClick={() => act(`/api/${type}/login`, {})}>打开 Chrome 登录 {name}</button>
    <p className="muted">请在项目专用窗口登录，登录状态保存在本地。</p>
    <button className="secondary wide" disabled={!current || busy || pending || state.status === 'busy'} onClick={() => act(`/api/${type}/prepare`, {folder:current.folder, ...(reddit ? {subreddit} : {})})}>{reddit ? '本地 AI 审核并准备帖子' : '合成竖屏视频并上传准备'}</button>
    <button className="wide" disabled={!matched || busy || pending} onClick={() => act(`/api/${type}/publish`, {prepared_id:state.prepared_id})}>发布到 {name}</button>
    <div className="note">{reddit ? '有许可的照片保留作者和许可署名。准备不会提交；点击发布才提交当前审核版本。' : '视频包含 AI 插画声明，开启 AI 标签后才允许提交。TikTok 网页上传仍需实际登录后验证。'}</div>
    <div className="status">{state.message || '尚未连接'}</div>
    {state.url && <a href={state.url} target="_blank" rel="noopener noreferrer">查看 {name} 帖子</a>}
    {reddit && <details><summary>已手动发布？记录链接</summary><input aria-label="手动发布链接" value={manual} onChange={e => setManual(e.target.value)} placeholder="https://www.reddit.com/r/.../comments/..."/><button className="secondary wide" disabled={!current || pending || !manual.trim()} onClick={() => act('/api/reddit/mark', {folder:current.folder, url:manual})}>保存发布记录</button></details>}
  </section>;
}

export default function App() {
  const [state, setState] = useState(null), [error, setError] = useState(''), [pending, setPending] = useState(false);
  const [city, setCity] = useState(''), [folder, setFolder] = useState(''), [current, setCurrent] = useState(null);
  const [prompt, setPrompt] = useState(() => localStorage.getItem('travelPrompt') || DEFAULT_PROMPT);
  const [food, setFood] = useState(true), [subreddit, setSubreddit] = useState('chinatravelhelp');
  const [pendingAction, setPendingAction] = useState(''), [lastRefresh, setLastRefresh] = useState('');
  const token = useRef(''), refreshing = useRef(false), lastOutput = useRef('');
  const api = useCallback(async (path, data) => {
    if (data && !token.current) {
      const config = await fetch('/api/config');
      if (!config.ok) throw Error('后端未就绪，请运行 npm run dev。');
      token.current = (await config.json()).token;
    }
    const response = await fetch(path, data ? {method:'POST', headers:{'Content-Type':'application/json', 'X-Local-Token':token.current}, body:JSON.stringify(data)} : {});
    const value = await response.json();
    if (!response.ok) throw Error(value.error || '操作失败');
    return value;
  }, []);
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const next = await api('/api/state'); setState(next); setLastRefresh(new Date().toLocaleTimeString());
      if (next.job.output && next.job.output !== lastOutput.current) {
        lastOutput.current = next.job.output;
        setCity(cityOf(next.job.output, next.cities)); setFolder(next.job.output);
      }
    } catch (e) { setError('无法连接本地服务：' + e.message); }
    finally { refreshing.current = false; }
  }, [api]);
  useEffect(() => {refresh(); const timer = setInterval(refresh, 2500); return () => clearInterval(timer);}, [refresh]);
  useEffect(() => {localStorage.setItem('travelPrompt', prompt);}, [prompt]);
  const cities = state?.cities || [], posts = state?.posts || [];
  const generated = cities.filter(c => posts.some(p => cityOf(p.folder, cities) === c.name_zh));
  const versions = posts.filter(p => cityOf(p.folder, cities) === city);
  const versionsKey = versions.map(p => p.folder).join('|');
  useEffect(() => {
    if (!city && cities.length) setCity(generated[0]?.name_zh || cities[0].name_zh);
  }, [state, city]);
  useEffect(() => {
    if (!versions.some(p => p.folder === folder)) setFolder(versions[0]?.folder || '');
  }, [city, versionsKey, folder]);
  useEffect(() => {
    let cancelled = false; setCurrent(null);
    if (folder) api('/api/post?folder=' + encodeURIComponent(folder)).then(post => {if (!cancelled) setCurrent(post);}).catch(e => {if (!cancelled) setError(e.message);});
    return () => {cancelled = true;};
  }, [folder, api]);
  async function act(path, data) {
    if (pending) return;
    setPending(true); setPendingAction(path); setError('');
    try {await api(path, data); await refresh();}
    catch (e) {setError(e.message);}
    finally {setPending(false); setPendingAction('');}
  }
  async function copy(value) {
    try {await navigator.clipboard.writeText(value);} catch {setError('复制失败，请手动选择并复制文字。');}
  }
  const batch = state?.batch, busy = ['running', 'cancelling'].includes(state?.job.status) || batch?.status === 'running';
  const remaining = cities.filter(c => !c.run_time).length;
  const generation = {city, prompt, include_food:food};
  const job = state?.job || {status:'idle'};
  const starting = pending && ['/api/start', '/api/batch/start'].includes(pendingAction);
  const activeLog = batch?.status === 'running' ? batch.log || '' : job.log || '';
  const latestLine = activeLog.trim().split(/\r?\n/).filter(Boolean).at(-1) || '';
  const step = latestLine.startsWith('ComfyUI') ? '生成或检查插画' : latestLine.includes('收集景点') || latestLine.includes('搜索：') ? '采集与筛选景点' : latestLine.includes('最终文件夹') ? '保存结果' : '正在处理，请查看日志';
  return <main>
    <header><div><div className="eyebrow">CHINA · SMALL TOWN STORIES</div><h1>中国小城 · 创作台</h1><p className="muted">选择一座城，把风景与味道整理成一篇旅行帖子。</p></div><span className="badge">本地 Ollama + ComfyUI</span></header>
    {error && <div className="error" role="alert">{error}</div>}
    <section className={'run-banner ' + (busy || starting ? 'active' : '')} role="status" aria-live="polite">
      <strong>{starting ? '正在启动任务，请稍候…' : busy ? `任务已开始${batch?.status === 'running' && batch.current ? ' · ' + batch.current : ''}` : batch?.status === 'cancelled' ? '任务已取消，可重新启动' : '网页已就绪'}</strong>
      <div>{busy ? `当前步骤：${step}` : starting ? '正在向 Python 后端发送启动请求。' : '点击“开始生成”处理一座城市，或点击“运行剩余城市”逐城生成。'}</div>
      {busy && latestLine && <div className="latest-log">最近日志：{latestLine}</div>}
      {busy && <div className="muted">本地模型处理时可能暂时没有新日志；运行日志已在下方展开。{lastRefresh && ' 状态更新时间：' + lastRefresh}</div>}
    </section>
    <div className="layout"><aside>
      <section className="card"><h2>开始创作</h2>
        <label htmlFor="city">城市</label><select id="city" value={city} onChange={e => setCity(e.target.value)}>
          <optgroup label="已生成 · 可查看或重新生成">{generated.map(c => <option key={c.name_zh} value={c.name_zh}>{c.name_zh} · {c.province}</option>)}</optgroup>
          <optgroup label="尚未生成">{cities.filter(c => !generated.includes(c)).map(c => <option key={c.name_zh} value={c.name_zh}>{c.name_zh} · {c.province}</option>)}</optgroup>
        </select>
        <label htmlFor="prompt">你的创作 Prompt</label><textarea id="prompt" maxLength={2000} value={prompt} onChange={e => setPrompt(e.target.value)}/>
        <div className="muted">同时影响文案语气和插画风格，不添加未经核实的事实。</div>
        <label className="check"><input type="checkbox" checked={food} onChange={e => setFood(e.target.checked)}/>加入有来源的本地美食推荐</label>
        <button className="wide" disabled={busy || pending || !city} onClick={() => act('/api/start', generation)}>{versions.length ? '重新生成 ' + city : '开始生成'}</button>
        <button className="wide batch-button" disabled={busy || pending || !state || !remaining} onClick={() => act('/api/batch/start', {prompt, include_food:food})}>{starting ? '正在启动…' : busy ? '生成任务正在运行' : `运行剩余城市（${remaining} 个）`}</button>
        <button className="wide cancel-button" disabled={pending || !state} onClick={() => act('/api/tasks/cancel', {})}>取消所有生成任务</button>
        <p className="muted">停止本项目生成、自动补跑和相关子进程。保留网页与已有文件，不关闭其他程序。</p>
        <p className="muted">批量逐城运行，跳过已完成城市。失败城市保留进度，可再次补跑。新任务使用上方 Prompt；已有中间进度沿用原设置。只保存本地草稿。</p>
        <div className="status" aria-live="polite">{batch?.status === 'running' ? `正在批量生成：${batch.current}\n成功 ${batch.completed} / ${batch.total}；失败 ${batch.failed}` : `${labels[job.status] || job.status}${job.error ? '\n' + job.error : ''}`}</div>
        {batch && <div className="batch-summary"><span>{labels[batch.status] || batch.status}</span><span>成功 {batch.completed} · 失败 {batch.failed}</span><progress value={batch.completed + batch.failed} max={Math.max(1, batch.total)} aria-label="批量处理进度"/></div>}
        {batch?.failures?.length > 0 && <details><summary>未完成城市（{batch.failed}）</summary>{batch.failures.map(f => <details key={f.city}><summary>{f.city}</summary><pre>{f.error}</pre></details>)}</details>}
        <details open={busy || undefined}><summary>查看运行日志</summary><pre>{batch?.status === 'running' ? batch.log || '任务运行中，等待日志…' : job.log || batch?.log || '尚无日志'}</pre></details>
        <p className="muted">关闭网页不影响批量任务；请保持电脑开机、不进入睡眠。</p>
      </section>
      <Platform type="reddit" state={state?.reddit} current={current} busy={busy} pending={pending} act={act} subreddit={subreddit} setSubreddit={setSubreddit}/>
      <Platform type="tiktok" state={state?.tiktok} current={current} busy={busy} pending={pending} act={act}/>
    </aside><section className="card preview-card"><h2>已生成的城市</h2>
      <label htmlFor="saved-city">选择城市，直接查看已有结果</label><select id="saved-city" value={generated.some(c => c.name_zh === city) ? city : ''} onChange={e => e.target.value && setCity(e.target.value)}><option value="">选择已生成城市</option>{generated.map(c => <option key={c.name_zh} value={c.name_zh}>{c.name_zh} · {c.province} · {posts.filter(p => cityOf(p.folder, cities) === c.name_zh).length} 个版本</option>)}</select>
      <div className="row"><button className="secondary" disabled={!current || busy || pending} onClick={() => act('/api/start', {...generation, city:cityOf(current.folder, cities)})}>重新生成这个城市</button></div>
      <label htmlFor="versions">选择这个城市的版本</label><select id="versions" value={folder} onChange={e => setFolder(e.target.value)}>{!versions.length && <option value="">这个城市暂无版本</option>}{versions.map((p, i) => <option key={p.folder} value={p.folder}>{p.folder.includes('_美化') ? '美化版' : `版本 ${i + 1}`} — {p.title}</option>)}</select>
      {!current ? <div className="empty">{folder ? '正在读取作品…' : '这个城市尚未生成。填写 Prompt 后点击开始生成。'}</div> : <>
        <div className="note">图片为 AI 艺术插画，不是真实照片；建筑、颜色和细节可能与实景不同。</div><h2 className="post-title">{current.title}</h2>
        <div className="row"><button className="secondary" onClick={() => copy('[AI illustrations] ' + current.title)}>复制标题</button><button className="secondary" onClick={() => copy('These images are AI-generated artistic illustrations, not real photographs or accurate depictions of the locations.\n\n' + current.body)}>复制正文</button></div>
        <pre className="post-body">{current.body}</pre><div className="gallery">{current.images.map((image, i) => {const url = '/media/' + image.split('/').map(encodeURIComponent).join('/'); return <a key={image} href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={`AI 旅行插画 ${i + 1}`}/>查看插画 {i + 1}</a>;})}</div>
        <details open><summary>本地美食</summary><pre>{current.foods || '这个版本未附可核实的美食推荐。'}</pre></details><p className="muted">文件夹：city_posts/{current.folder}</p>
      </>}
    </section></div>
  </main>;
}
