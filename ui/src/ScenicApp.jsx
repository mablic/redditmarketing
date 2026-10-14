import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Platform} from './App.jsx';

const SOURCE_LABEL={unsplash:'Unsplash',commons:'Wikimedia Commons',ai_local:'本地 AI 绘制'};
const STATUS_LABEL={draft_ready:'已有草稿',draft_partial_images:'部分缺图',draft_without_images:'缺图',failed:'失败'};

export default function ScenicApp() {
  const [state,setState]=useState(null), [province,setProvince]=useState('北京'), [city,setCity]=useState('');
  const [folder,setFolder]=useState(''), [post,setPost]=useState(null), [error,setError]=useState(''), [pending,setPending]=useState('');
  const [prompt,setPrompt]=useState(()=>localStorage.getItem('scenicPrompt')||'Warm, knowledgeable English, like a local guide who loves history. Explain the story of each place, why it earned 5A, and what not to miss.');
  const [subreddit,setSubreddit]=useState('chinatravelhelp'), [reviewed,setReviewed]=useState(false);
  const token=useRef(''), refreshing=useRef(false);
  const api=useCallback(async(path,data)=>{
    if(data&&!token.current){const r=await fetch('/api/config');token.current=(await r.json()).token;}
    const r=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token.current},body:JSON.stringify(data)}:{});
    const result=await r.json();if(!r.ok)throw Error(result.error||'操作失败');return result;
  },[]);
  const refresh=useCallback(async()=>{
    if(refreshing.current)return;refreshing.current=true;
    try{setState(await api('/api/state'));}catch(e){setError('无法连接后端：'+e.message);}finally{refreshing.current=false;}
  },[api]);
  useEffect(()=>{refresh();const timer=setInterval(refresh,2500);return()=>clearInterval(timer);},[refresh]);
  useEffect(()=>{localStorage.setItem('scenicPrompt',prompt);},[prompt]);
  const regions=state?.provinces||[], region=regions.find(p=>p.province===province), cities=region?.cities||[];
  useEffect(()=>{if(cities.length&&!cities.some(c=>c.city===city))setCity(cities[0].city);},[region,city]);
  const current=cities.find(c=>c.city===city), entries=(region?.attractions||[]).filter(x=>x.city===city);
  const versions=(state?.posts||[]).filter(p=>p.folder.startsWith(`5a/${province}/${city}_`));
  const versionKey=versions.map(p=>p.folder).join('|');
  useEffect(()=>{if(!versions.some(x=>x.folder===folder))setFolder(versions[0]?.folder||'');},[versionKey,folder]);
  useEffect(()=>{let cancelled=false;setPost(null);setReviewed(false);if(folder)api('/api/post?folder='+encodeURIComponent(folder)).then(p=>{if(!cancelled)setPost(p);}).catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};},[folder,api]);
  async function act(path,data){if(pending)return;setPending(path);setError('');try{await api(path,data);await refresh();}catch(e){setError(e.message);}finally{setPending('');}}
  async function copy(text){try{await navigator.clipboard.writeText(text);}catch{setError('复制失败，请手动复制。');}}
  const batch=state?.batch, busy=['running','cancelling'].includes(state?.job.status)||batch?.status==='running';
  const remaining=cities.filter(c=>c.status!=='draft_ready').length;
  const remainingAll=regions.reduce((count,region)=>count+region.cities.filter(c=>c.status!=='draft_ready').length,0);
  const log=batch?.log||state?.job.log||'', lastLine=log.trim().split(/\r?\n/).filter(Boolean).at(-1)||'';
  return <main><header><div><div className="eyebrow">CHINA · 5A SCENIC AREAS</div><h1>中国 5A 景区 · 创作台</h1><p className="muted">按省份、城市生成帖子：每个帖子介绍一座城市的全部 5A 景区——历史、为何评为 5A、主要看点。</p></div><span className="badge">Unsplash · Commons · 本地 AI</span></header>
    {error&&<div className="error" role="alert">{error}</div>}
    <section className="card status" aria-live="polite"><strong>{pending==='/api/tasks/cancel'?'正在取消…':pending.includes('start')?'正在启动任务…':busy?'任务已开始 · '+(batch?.province||province)+' · '+(batch?.current||'准备中'):'等待手动启动'}</strong>
      {batch&&<div>本批次 {batch.total} 个城市 · 已生成草稿 {batch.completed} · 失败 {batch.failed} · {batch.status}</div>}
      {lastLine&&<div>最近日志：{lastLine}</div>}
    </section>
    <div className="layout"><aside><section className="card"><h2>省份与城市</h2>
      <label htmlFor="province">省份 / 地区</label><select id="province" value={province} onChange={e=>{setProvince(e.target.value);setCity('');}}>{regions.map(p=><option key={p.province}>{p.province}</option>)}</select>
      <p className="muted">{region?.source_as_of&&`官方目录截止日：${region.source_as_of}`} · {cities.length} 个城市 · {region?.attractions.length||0} 个 5A 景区</p>
      <label htmlFor="city">城市</label><select id="city" value={city} onChange={e=>setCity(e.target.value)}>{cities.map(c=><option key={c.city} value={c.city}>{c.city}（{c.attraction_ids.length}）{STATUS_LABEL[c.status]?' · '+STATUS_LABEL[c.status]:''}</option>)}</select>
      <ul className="muted">{entries.map(x=><li key={x.id}>{x.name_zh}{x.rating_year&&!x.rating_year_disputed?` · ${x.rating_year}`:''}</li>)}</ul>
      {current?.last_error&&<div className="note">上次失败：{current.last_error}</div>}
      <label htmlFor="prompt">文案 Prompt</label><textarea id="prompt" value={prompt} maxLength={2000} onChange={e=>setPrompt(e.target.value)}/>
      <p className="muted">只影响英文文案的语气；事实只来自查到的公开资料。</p>
      <button className="wide" disabled={!city||busy||!!pending} onClick={()=>act('/api/start',{province,city,prompt})}>生成 / 补跑 {city||'这个城市'} 的帖子</button>
      <button className="wide batch-button" disabled={!region||busy||!!pending||!remaining} onClick={()=>act('/api/batch/start',{province,prompt})}>运行{province}全部待处理城市（{remaining}）</button>
      <button className="wide batch-button" disabled={busy||!!pending||!remainingAll} onClick={()=>act('/api/batch/start',{all:true,prompt})}>运行全部待处理城市（{remainingAll}）</button>
      <button className="wide cancel-button" disabled={!!pending||!state} onClick={()=>act('/api/tasks/cancel',{})}>取消所有生成任务</button>
      <div className="note">图片顺序：先找 Unsplash，再找 Wikimedia Commons 的可复用照片；都没有时，从百度图片取参考图，由本地 ComfyUI 重新绘制。发布前请核对每张图是否对应景区。</div>
      <details open={busy||undefined}><summary>运行日志</summary><pre>{log||'尚无日志'}</pre></details>
      {!!batch?.failures?.length&&<details><summary>未完成城市</summary>{batch.failures.map((f,i)=><pre key={i}>{f.city}: {f.error}</pre>)}</details>}
    </section>
    <Platform type="reddit" state={state?.reddit} current={reviewed&&post?.images.length?post:null} busy={busy} pending={!!pending} act={act} subreddit={subreddit} setSubreddit={setSubreddit}/>
    </aside><section className="card"><h2>城市帖子草稿</h2><p>{city?`${province} · ${city}`:'请选择城市'}</p>
      <label htmlFor="versions">已有版本</label><select id="versions" value={folder} onChange={e=>setFolder(e.target.value)}>{!versions.length&&<option value="">尚无草稿</option>}{versions.map((p,i)=><option key={p.folder} value={p.folder}>版本 {i+1} — {p.title}</option>)}</select>
      {!post?<div className="empty">{folder?'正在读取…':'运行城市后，在这里查看文案与图片。'}</div>:<>
        <h2 className="post-title">{post.title}</h2><div className="row"><button className="secondary" onClick={()=>copy(post.title)}>复制标题</button><button className="secondary" onClick={()=>copy(post.body)}>复制正文</button></div><pre className="post-body">{post.body}</pre>
        {post.images.length?<div className="gallery">{post.images.map((image,i)=>{const url='/media/'+image.split('/').map(encodeURIComponent).join('/');const meta=(post.metadata?.images||[]).find(m=>image.endsWith('/'+m.file));return <div key={image}><a href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={(meta?.name_zh||city)+' 图片 '+(i+1)}/></a><div className="muted">{meta?.name_zh} · {SOURCE_LABEL[meta?.source]||'未知来源'}{meta?.author?' · '+meta.author:''}</div>{meta?.page&&<a href={meta.page} target="_blank" rel="noopener noreferrer">原图页面及许可</a>}{meta?.reference?.result_page&&<a href={meta.reference.result_page} target="_blank" rel="noopener noreferrer">AI 参考图来源</a>}</div>;})}</div>:<div className="note">本版本没有图片。可点击“补跑”重试。</div>}
        {!!post.images.length&&<label className="check"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>我已核对每张图对应的景区、署名和许可，可准备 Reddit 帖子</label>}
        <p className="muted">文件夹：city_posts/{post.folder}</p><p className="muted">生成草稿不会自动发布。</p>
      </>}
    </section></div></main>;
}
