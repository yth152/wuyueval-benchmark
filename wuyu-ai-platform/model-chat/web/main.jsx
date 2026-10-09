import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowUp, Brain, Building2, Check, ChevronDown, Copy, Download, FlaskConical,
  LoaderCircle, PanelLeftClose, PanelLeftOpen, Cpu, MessageSquare, Plus, Settings2, ShieldCheck, Square, Trash2, X, Landmark, Globe, ExternalLink, Search, Paperclip, FileText, ArrowDown } from 'lucide-react';
import logo from './logo.png';
import { SourceButtons, SourcePanel } from './SourcePanel.jsx';
import { sourceMap, sourceTitle, sourceKind } from './sources.mjs';
import './style.css';
import './readability.css';

const STORE = 'wuyu-model-chat-v1';
const defaults = { thinking: true, network_search: false, temperature: 0.2, max_tokens: 8192, history: 10,
  system: '你是一个严谨、清晰的中文助手。请根据用户问题作答；信息不足时明确说明，不编造事实、数据、来源或法律条文。' };
const uid = () => crypto.randomUUID();
const fresh = (provider = 'team') => ({ id: uid(), title: '新的对话', provider, updated: Date.now(), turns: [] });
function load() {
  try {
    const data = JSON.parse(localStorage.getItem(STORE));
    if (!Array.isArray(data?.chats) || !data.chats.length) throw new Error();
    return { ...data, chats: data.chats.slice(0, 30).map(chat => ({ ...chat,
      turns: (chat.turns || []).map(turn => turn.status === 'streaming'
        ? { ...turn, status: 'incomplete', error: '上次生成已中断，可重新提问。' } : turn) })) };
  } catch { return { chats: [fresh()], settings: defaults }; }
}
const initial = load();
const formatTime = value => new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(value);
function Logo() { return <span className="brand-mark"><img src={logo} alt="无隅循环 Logo" /></span>; }
const fileSize = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
const toolLabel = name => /scholar/.test(name) ? '学术搜索' : '联网搜索';
function FileChips({files, onRemove}) {
  if (!files?.length) return null;
  return <div className="file-chips">{files.map(file => <div className={`file-chip ${file.kind === 'image' ? 'image-chip' : ''}`} key={file.id}>
    {file.kind === 'image' ? <a href={file.preview_url} target="_blank" rel="noopener noreferrer" aria-label={`查看图片 ${file.name}`}><img src={file.preview_url} alt={file.name} /></a> : <FileText size={25} />}
    <span><b title={file.name}>{file.name}</b><small>{fileSize(file.size)} · {file.kind === 'image' ? '图片' : '已解析'}</small>{file.warning && <small className="file-warning" title={file.warning}>{file.warning}</small>}</span>
    {onRemove && <button type="button" className="icon-button" aria-label={`移除附件 ${file.name}`} onClick={() => onRemove(file.id)}><X size={14}/></button>}
  </div>)}</div>;
}
function Reasoning({turn}) {
  const [open,setOpen] = useState(turn.status === 'streaming');
  return <details className="reasoning" open={open}><summary onClick={event => {event.preventDefault();setOpen(value => !value);}}><Brain size={16}/><strong>{turn.status === 'streaming' && !turn.answer ? '正在思考' : '思考过程'}</strong><span>{turn.reasoning.length.toLocaleString()} 字</span><ChevronDown size={15}/></summary><div className="reasoning-text">{turn.reasoning}</div></details>;
}
function Answer({turn, onSource}) {
  const sources = sourceMap(turn);
  return <div className="answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{a: ({node,children,href,...props}) => {
    const id=String(children).match(/^S(\d+)$/)?.[0];
    const source=id ? sources.get(id) : [...sources.values()].find(source => source.url === href);
    return source ? <a className="inline-citation" data-source-trigger={`cite-${turn.id}-${source.source_id}`} href={source.url} onClick={event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();onSource(turn, sourceKind(source), source.source_id, event.currentTarget);
    }} target="_blank" rel="noopener noreferrer" title={sourceTitle(source)} aria-label={`引用 ${source.source_id.slice(1)}：${sourceTitle(source)}`}>{source.source_id.slice(1)}</a> : <a {...props} href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
  }, img:()=> <span className="image-omitted">[外部图片已省略]</span>}}>{citedMarkdown(turn.answer,turn)}</Markdown></div>;
}

function citedMarkdown(text, turn, history = false) {
  const sources = sourceMap(turn);
  return text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g).map(part => part.startsWith('`') ? part : part.replace(
    /\[(S\d+(?:\s*[,，;；]\s*S\d+)*)\](?:\([^\n)]*\))?/g,
    (whole, ids) => [...ids.matchAll(/S\d+/g)].map(([id]) => {
      const source = sources.get(id);
      if (!source) return history ? '（历史引用未匹配）' : `[${id}]`;
      const url = source.url.replace(/\(/g, '%28').replace(/\)/g, '%29');
      return history ? `（来源：${sourceTitle(source)}；${url}）` : `[${id}](${url})`;
    }).join(' ')
  )).join('');
}
function SourceList({ sources, compact = false }) {
  return <ol className={`source-list ${compact ? 'compact' : ''}`}>{sources.map(source => <li key={source.source_id}>
    <span className="source-id">{source.source_id.replace(/^S/,'')}</span><div><a href={source.url} target="_blank" rel="noopener noreferrer">{sourceTitle(source)}<ExternalLink size={12} /></a>
    <small>{source.type === 'scholar' ? '学术' : '网页'}{source.date ? ` · ${source.date}` : ''} · {new URL(source.url).hostname}</small>
    {!!source.authors?.length && !compact && <small>{source.authors.join('、')}</small>}
    {source.year_status === 'unknown' && <small>发表年份未标明，不能确认属于近十年</small>}
    {compact && source.snippet && <p>{source.snippet}</p>}</div>
  </li>)}</ol>;
}
function ToolHistory({ turn }) {
  if (!turn.toolCalls?.length) return null;
  const running = turn.status === 'streaming' && turn.toolCalls.some(call => call.status === 'running');
  return <details className="tool-history" open={running || undefined}><summary><Globe size={16} /><strong>工具调用记录</strong><span>{turn.toolCalls.length} 次</span><ChevronDown size={15} /></summary>
    {turn.toolCalls.map(call => <div className="tool-record" key={call.call_id}><div className="tool-record-title">{/scholar/.test(call.name) ? <FlaskConical size={15} /> : <Search size={15} />}<strong>{toolLabel(call.name)}{call.language ? ` · ${call.language === 'zh' ? '中文' : '英文'}` : ''}</strong><span className={call.status === 'error' ? 'tool-failed' : ''}>{call.status === 'running' ? (turn.status === 'streaming' ? '正在搜索…' : '已中断') : call.status === 'error' ? '调用失败' : `${call.sources?.length || 0} / 10 个来源${call.cached ? ' · 复用结果' : ''}`}</span></div>
      <p className="tool-query">{call.query || '无有效搜索词'}</p>{call.error && <p className="tool-failed">{call.error}</p>}
      {call.date_window && <p className="tool-date">近十年优先 · {call.date_window.from} 至 {call.date_window.to}{call.excluded_by_date ? ` · 已排除 ${call.excluded_by_date} 个范围外结果` : ''}</p>}
      {!!call.sources?.length && <details className="tool-sources"><summary>查看本次检索结果{call.seconds != null ? ` · ${call.seconds}s` : ''}</summary><SourceList sources={call.sources} compact /></details>}
    </div>)}
  </details>;
}

async function consumeSSE(response, onEvent) {
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  function dispatch(block) {
    const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
    if (data) onEvent(JSON.parse(data));
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
      let index;
      while ((index = buffer.indexOf('\n\n')) !== -1) {
        dispatch(buffer.slice(0, index)); buffer = buffer.slice(index + 2);
      }
      if (done) { if (buffer.trim()) dispatch(buffer); break; }
    }
  } finally { reader.releaseLock(); }
}

function App() {
  const [chats, setChats] = useState(initial.chats);
  const [selected, setSelected] = useState(initial.selected && initial.chats.some(c => c.id === initial.selected) ? initial.selected : initial.chats[0].id);
  const [settings, setSettings] = useState({ ...defaults, ...initial.settings });
  const [providers, setProviders] = useState([]);
  const [toolsReady, setToolsReady] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sourceView, setSourceView] = useState(null);
  const sourceOpener = useRef(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [historyFilter, setHistoryFilter] = useState('');
  const [modelMenu, setModelMenu] = useState(false);
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [fileConfig, setFileConfig] = useState({max_files:5,file_mb:20,image_mb:10,total_mb:50,supported:['.docx','.pptx','.xlsx','.xls','.pdf','.txt','.md','.csv','.tsv','.json','.png','.jpg','.jpeg','.webp','.gif','.bmp']});
  const [showLatest,setShowLatest] = useState(false);
  const [notice, setNotice] = useState('');
  const [copied, setCopied] = useState('');
  const [deleteTarget, setDeleteTarget] = useState(null);
  const controller = useRef(null), textarea = useRef(null), scrollbox = useRef(null), stick = useRef(true);
  const fileInput = useRef(null), uploadLock = useRef(false), pendingPatch = useRef(null), patchTimer = useRef(null);
  const scrollIntent = useRef(false), scrollPointer = useRef(false);
  const chatsRef = useRef(chats), settingsRef = useRef(settings), selectedRef = useRef(selected);
  chatsRef.current = chats; settingsRef.current = settings; selectedRef.current = selected;
  const chat = chats.find(c => c.id === selected) || chats[0];
  const provider = providers.find(p => p.id === chat.provider);
  const sourceTurn = sourceView?.chatId === chat.id ? chat.turns.find(turn => turn.id === sourceView.turnId) : null;

  function openSources(turn, kind, sourceId, opener) {
    sourceOpener.current = { element: opener, trigger: opener?.dataset.sourceTrigger };
    stick.current = false;
    setSourceView({ chatId: chat.id, turnId: turn.id, kind, sourceId, openKey: uid() });
    setSettingsOpen(false); setSidebarOpen(false); setModelMenu(false);
  }
  function closeSources() {
    const wasOpen = Boolean(document.getElementById('source-panel'));
    setSourceView(null);
    requestAnimationFrame(() => {
      if (!wasOpen) return;
      const previous = sourceOpener.current;
      const target = previous?.element?.isConnected ? previous.element : previous?.trigger ? document.querySelector(`[data-source-trigger="${CSS.escape(previous.trigger)}"]`) : null;
      target?.focus({ preventScroll: true });
    });
  }
  useEffect(() => { setSourceView(null); }, [selected]);

  useEffect(() => {
    fetch('/api/providers').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(setProviders)
      .catch(() => setNotice('本机服务暂时不可用，请重新运行启动脚本。'));
    fetch('/api/tools').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(data => setToolsReady(data.configured))
      .catch(() => setToolsReady(false));
    fetch('/api/files/config').then(r => {if(!r.ok) throw new Error();return r.json();}).then(setFileConfig).catch(() => {});
  }, []);
  useEffect(() => {
    const save = () => {
      try { localStorage.setItem(STORE, JSON.stringify({ chats: chatsRef.current, settings: settingsRef.current, selected: selectedRef.current })); }
      catch { setNotice('浏览器存储空间不足，当前对话尚未保存。请导出记录或删除旧对话。'); }
    };
    const timer = setTimeout(save, 500);
    window.addEventListener('pagehide', save);
    return () => { clearTimeout(timer); window.removeEventListener('pagehide', save); };
  }, [chats, settings, selected]);
  useEffect(() => {
    if (!busy) return;
    const start = Date.now(); setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    const beforeUnload = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload);
    return () => { clearInterval(timer); window.removeEventListener('beforeunload', beforeUnload); };
  }, [busy]);
  useEffect(() => {
    if (!scrollbox.current) return;
    if (!stick.current) {const el=scrollbox.current;setShowLatest(el.scrollHeight-el.scrollTop-el.clientHeight>90);return;}
    const frame = requestAnimationFrame(() => {if(stick.current && scrollbox.current) scrollbox.current.scrollTop = chat.turns.length ? scrollbox.current.scrollHeight : 0;});
    return () => cancelAnimationFrame(frame);
  }, [chats, selected]);
  useEffect(() => {
    const release = () => {scrollPointer.current=false;};
    window.addEventListener('pointerup',release);
    return () => {window.removeEventListener('pointerup',release);clearTimeout(patchTimer.current);};
  },[]);
  useEffect(() => {
    if (textarea.current) { textarea.current.style.height = 'auto'; textarea.current.style.height = `${Math.min(textarea.current.scrollHeight, 180)}px`; }
  }, [draft, selected, chat.turns.length]);
  useEffect(() => {
    const escape = event => { if (event.key === 'Escape') { setSettingsOpen(false); setSidebarOpen(false); setDeleteTarget(null); setModelMenu(false); closeSources(); } };
    window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape);
  }, []);
  useEffect(() => {
    if (!modelMenu) return;
    const close = () => setModelMenu(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [modelMenu]);

  function newChat(model = chat.provider) {
    if (busy || uploading) return;
    if (chats.length >= 30) { setNotice('最多保存 30 个对话，请先导出并删除不需要的记录。'); return; }
    const next = fresh(model); setChats([next, ...chats]); setSelected(next.id); setDraft(''); setFiles([]); setNotice(''); setSidebarOpen(false); setModelMenu(false); stick.current = true;setShowLatest(false);
  }
  function chooseProvider(value) {
    setModelMenu(false);
    if (value === chat.provider) return;
    if (chat.turns.length) newChat(value);
    else setChats(chats.map(c => c.id === selected ? { ...c, provider: value } : c));
  }
  function updateTurn(chatId, turnId, patch) {
    pendingPatch.current={chatId,turnId,patch:{...pendingPatch.current?.patch,...patch}};
    const flush=()=>{
      const pending=pendingPatch.current;pendingPatch.current=null;patchTimer.current=null;
      if(pending)setChats(list=>list.map(c=>c.id!==pending.chatId?c:{...c,updated:Date.now(),turns:c.turns.map(turn=>turn.id===pending.turnId?{...turn,...pending.patch}:turn)}));
    };
    if(patch.status || patch.toolCalls || patch.citations || patch.warning){clearTimeout(patchTimer.current);flush();}
    else if(!patchTimer.current)patchTimer.current=setTimeout(flush,80);
  }
  function pauseFollow(event) {
    scrollIntent.current=true;
    if(event.type==='pointerdown')scrollPointer.current=true;
    if(event.type!=='wheel' || event.deltaY<0)stick.current=false;
  }
  function onConversationScroll() {
    const el=scrollbox.current;if(!el)return;
    const distance=el.scrollHeight-el.scrollTop-el.clientHeight;
    setShowLatest(distance>90);
    if(scrollIntent.current || scrollPointer.current){stick.current=distance<24;scrollIntent.current=scrollPointer.current;}
  }
  function jumpLatest() {stick.current=true;scrollIntent.current=false;setShowLatest(false);scrollbox.current?.scrollTo({top:scrollbox.current.scrollHeight,behavior:'instant'});}
  async function uploadFiles(list) {
    if(!list.length)return;
    if(busy || uploadLock.current){setNotice(busy?'请先停止当前生成，再添加附件。':'附件正在解析，请稍候再添加。');return;}
    if(files.length+list.length>fileConfig.max_files){setNotice(`每轮最多 ${fileConfig.max_files} 个文件。`);return;}
    if([...files,...list].reduce((sum,f)=>sum+f.size,0)>fileConfig.total_mb*1048576){setNotice(`每轮附件合计不能超过 ${fileConfig.total_mb} MB。`);return;}
    for(const file of list){
      const ext='.'+file.name.split('.').pop().toLowerCase();
      if(!fileConfig.supported.includes(ext)){setNotice(`${file.name} 格式暂不支持；旧版 DOC/PPT 请另存为 DOCX/PPTX。`);return;}
      const max=/\.(png|jpe?g|webp|gif|bmp)$/i.test(file.name)?fileConfig.image_mb:fileConfig.file_mb;
      if(file.size>max*1048576){setNotice(`${file.name} 超过 ${max} MB。`);return;}
    }
    uploadLock.current=true;setUploading(true);setNotice('');
    try{for(const file of list){
      const form=new FormData();form.append('file',file);
      const response=await fetch('/api/files',{method:'POST',body:form});
      const saved=await response.json();if(!response.ok)throw new Error(typeof saved.detail==='string'?saved.detail:'文件上传或解析失败。');
      setFiles(previous=>[...previous,saved]);
    }}catch(error){setNotice(error.message);}finally{uploadLock.current=false;setUploading(false);textarea.current?.focus();}
  }
  function pasteFiles(event) {
    const items=Array.from(event.clipboardData.files);
    if(items.length){event.preventDefault();void uploadFiles(items);return;}
    const text=event.clipboardData.getData('text/plain');
    if(text.length>4000){event.preventDefault();void uploadFiles([new File([text],`粘贴文本-${Date.now()}.txt`,{type:'text/plain'})]);}
  }
  async function send(event) {
    event?.preventDefault();
    const question = draft.trim() || (files.length ? '请分析这些附件，概括主要内容，并指出需要进一步核实的问题。' : '');
    if (!question || controller.current || uploadLock.current) return;
    if (settings.network_search && toolsReady !== true) { setNotice('搜索服务暂不可用，请检查配置或关闭联网搜索。'); return; }
    if (question.length > 20000) { setNotice('单次问题最多 20,000 字，请缩短后发送。'); return; }
    const chatId = chat.id, turnId = uid();
    const completed = chat.turns.filter(t => t.status === 'complete' && t.answer).slice(-Number(settings.history || 1)).map(t => ({ ...t, contextAnswer: citedMarkdown(t.answer, t, true) }));
    const context = Number(settings.history) === 0 ? [] : completed;
    // Keep complete turns only; reserve room for the current question and system prompt.
    while (context.length && context.reduce((sum, t) => sum + t.user.length + t.contextAnswer.length, 0) + question.length + settings.system.length > 58000) context.shift();
    if (context.some(t => t.user.length > 20000 || t.contextAnswer.length > 20000)) {
      setNotice('历史中有超长回答，请将“携带历史轮数”设为 0，或新建对话。'); return;
    }
    const sentFiles=[...files];
    const turn = { id: turnId, user: question, answer: '', reasoning: '', files:sentFiles, status: 'streaming', created: Date.now(),
      toolCalls: [], citations: [], request: { thinking: settings.thinking, network_search: settings.network_search, temperature: settings.temperature, max_tokens: settings.max_tokens, context_turns: context.length } };
    setChats(list => list.map(c => c.id === chatId ? { ...c, title: c.turns.length ? c.title : question.slice(0, 28), updated: Date.now(), turns: [...c.turns, turn] } : c));
    setDraft(''); setFiles([]); setNotice(''); setBusy(true); stick.current = true;setShowLatest(false);
    const abort = new AbortController(); controller.current = abort;
    let answer = '', reasoning = '', finalEvent = false, toolCalls = [], reasonRound = 0, warnings = [];
    try {
      const response = await fetch('/api/chat', { method: 'POST', signal: abort.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: chat.provider,
          messages: [...context.flatMap(t => [{ role: 'user', content: t.user, file_ids:(t.files||[]).map(f=>f.id) }, { role: 'assistant', content: t.contextAnswer }]), { role: 'user', content: question, file_ids:sentFiles.map(f=>f.id) }],
          system: settings.system, thinking: settings.thinking, network_search: settings.network_search, temperature: Number(settings.temperature), max_tokens: Number(settings.max_tokens) }) });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}），请检查输入长度和参数。`);
      }
      await consumeSSE(response, data => {
        if (data.type === 'content') { answer += data.text; updateTurn(chatId, turnId, { answer, phase: '正在回答' }); }
        if (data.type === 'reasoning') {
          if (reasonRound && reasonRound !== data.round) reasoning += '\n\n';
          reasonRound = data.round; reasoning += data.text; updateTurn(chatId, turnId, { reasoning, phase: '正在思考' });
        }
        if (data.type === 'model_round') updateTurn(chatId, turnId, { phase: data.label || (data.round > 1 ? '模型读取工具结果' : '模型自主选择工具') });
        if (data.type === 'tool_plan') { answer = ''; updateTurn(chatId, turnId, { answer, phase: '准备调用搜索工具' }); }
        if (data.type === 'tool_start') {
          toolCalls = [...toolCalls, { ...data, status: 'running' }];
          updateTurn(chatId, turnId, { toolCalls, phase: `调用${data.label}` });
        }
        if (data.type === 'tool_result') {
          toolCalls = toolCalls.map(call => call.call_id === data.call_id ? { ...call, ...data, status: data.ok ? 'complete' : 'error' } : call);
          updateTurn(chatId, turnId, { toolCalls, phase: '搜索结果已返回模型' });
        }
        if (data.type === 'citations') updateTurn(chatId, turnId, { citations: data.sources, searchMeta: data });
        if (data.type === 'warning') {
          if (!warnings.includes(data.message)) warnings.push(data.message);
          updateTurn(chatId, turnId, { warning: warnings.join(' ') });
        }
        if (data.type === 'error') {
          finalEvent = true; updateTurn(chatId, turnId, { status: 'error', error: data.message, meta: data });
        }
        if (data.type === 'done') {
          finalEvent = true;
          const error = data.complete ? '' : data.finish_reason === 'length'
            ? '已达到输出上限，回答未完成。可提高输出上限后重试。'
            : !answer.trim() ? '接口未提供完整回答。可提高输出上限，或关闭思考后重试。' : '接口未正常完成回答，请检查内容后重试。';
          updateTurn(chatId, turnId, { status: data.complete ? 'complete' : 'incomplete', error, meta: data });
        }
      });
      if (!finalEvent) throw new Error('连接已结束，但未收到完成标记。当前内容不完整。');
    } catch (error) {
      updateTurn(chatId, turnId, { answer, reasoning, status: abort.signal.aborted ? 'stopped' : 'error',
        error: abort.signal.aborted ? '已停止生成。未完成回答不会带入下一轮。' : error.message });
    } finally { controller.current = null; setBusy(false); textarea.current?.focus(); }
  }
  async function copy(turn) {
    const references = turn.citations?.length ? '\n\n引用来源：\n' + turn.citations.map(s => `[${s.source_id}] ${sourceTitle(s)} — ${s.url}`).join('\n') : '';
    try { await navigator.clipboard.writeText(citedMarkdown(turn.answer, turn) + references); setCopied(turn.id); setTimeout(() => setCopied(''), 1800); }
    catch { setNotice('复制失败，请手动选择回答文本复制。'); }
  }
  async function download() {
    const text = '# ' + chat.title + '\n\n模型：' + (provider?.label || chat.provider) + '\n导出时间：' + new Date().toLocaleString('zh-CN') + '\n\n' + chat.turns.map((turn, i) =>
      `## 第 ${i + 1} 轮\n\n### 问题\n\n${turn.user}\n\n### 模型返回的思考内容\n\n${turn.reasoning || '接口未返回独立思考内容。'}\n\n### 回答\n\n${citedMarkdown(turn.answer, turn) || '未收到回答。'}\n\n### 本次回答引用的来源\n\n${turn.citations?.length ? turn.citations.map(s => `- [${s.source_id}] ${sourceTitle(s)} — ${s.url}${s.date ? '；' + s.date : ''}`).join('\n') : '本轮未标明可匹配的联网引用。'}\n\n状态：${turn.status}${turn.error ? '；' + turn.error : ''}\n\n参数、工具调用与统计：\n\n\`\`\`json\n${JSON.stringify({ request: turn.request, response: turn.meta, tool_calls: turn.toolCalls || [], citation_metadata: turn.searchMeta, attachments: turn.files || [] }, null, 2)}\n\`\`\`\n`).join('\n---\n\n');
    try {
      const response = await fetch('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
      if (!response.ok) throw new Error();
      const saved = await response.json();
      setNotice(<span>记录已保存到本机：<span className="export-path">{saved.path}</span><a href={saved.url} download>下载副本</a></span>);
    } catch { setNotice('导出失败，请检查本机服务和磁盘空间后重试。'); }
  }
  function removeChat(id) {
    const rest = chats.filter(c => c.id !== id); if (!rest.length) rest.push(fresh());
    setChats(rest); if (id === selected) { setSelected(rest[0].id); setDraft(''); } setDeleteTarget(null);
  }

  const samples = [
    { icon: Landmark, category: '政府监管', title: '制定现场核查清单', text: '遥感发现某河道附近新增一处疑似固废堆场，仅有影像和位置。请制定现场核查清单，区分已知事实、待核实事项，以及需要补充的证据。' },
    { icon: Building2, category: '企业管理', title: '梳理固废管理台账', text: '一家制造企业产生废包装、含油抹布和生产污泥。请给出固废分类与台账管理的初步方案，明确还需要哪些工艺和检测资料，不要直接假定其危险废物属性。' },
    { icon: FlaskConical, category: '科研分析', title: '设计可靠的评估实验', text: '我想评估遥感固废识别模型的泛化能力。请设计一个包含空间独立测试、类别不平衡处理、误报漏报分析和不确定性评估的实验方案。' },
  ];
  const composer = <div className={`composer-wrap ${!chat.turns.length ? 'welcome-composer' : ''}`}>
    {notice && <div className="notice" role="alert"><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X size={15} /></button></div>}
    <form className={`composer ${busy ? 'is-busy' : ''}`} onSubmit={send} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();void uploadFiles(Array.from(e.dataTransfer.files));}}>
      <FileChips files={files} onRemove={uploading?undefined:id=>setFiles(previous=>previous.filter(file=>file.id!==id))}/>
      <textarea ref={textarea} aria-label="输入问题" placeholder={chat.turns.length ? '继续追问，或粘贴图片、拖入文件…' : '给无隅 AI 发送消息，或粘贴图片、拖入文件…'} value={draft} maxLength={20000} onPaste={pasteFiles} onChange={e => setDraft(e.target.value)} rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (!busy) send(); } }} />
      <input ref={fileInput} type="file" multiple hidden accept={fileConfig.supported.join(',')} onChange={e=>{void uploadFiles(Array.from(e.target.files||[]));e.target.value='';}}/>
      <div className="composer-tools"><div className="mode-toggles">
        <button type="button" className={`tool pill ${settings.thinking ? 'selected' : ''}`} aria-label="深度思考" aria-pressed={settings.thinking} disabled={busy} title="请求模型返回思考内容，是否提供取决于服务支持" onClick={() => setSettings({ ...settings, thinking: !settings.thinking })}><Brain size={17} /><span>深度思考</span></button>
        <button type="button" className={`tool pill ${settings.network_search ? 'selected' : ''}`} aria-label="联网搜索" aria-pressed={settings.network_search} disabled={busy || (toolsReady !== true && !settings.network_search)} title={toolsReady === false ? '搜索服务尚未配置' : '模型按需选择联网或学术搜索，中英文分别检索，每次10个来源'} onClick={() => setSettings({ ...settings, network_search: !settings.network_search })}><Globe size={17} /><span>联网搜索</span></button>
        <div className="model-picker" onClick={e => e.stopPropagation()}>
          <button type="button" className={`tool pill model-trigger ${modelMenu ? 'selected' : ''}`} aria-label="选择模型" aria-expanded={modelMenu} aria-haspopup="listbox" disabled={busy || uploading || !providers.length} title={provider?.label || '连接本机服务…'} onClick={() => setModelMenu(!modelMenu)}><Cpu size={17} /><span>模型</span><ChevronDown size={13} /></button>
          {modelMenu && <div className="model-menu" role="listbox" aria-label="可用模型"><div className="model-menu-title">选择对话模型</div>{providers.map(p => <button type="button" role="option" aria-selected={chat.provider === p.id} key={p.id} disabled={!p.configured} onClick={() => chooseProvider(p.id)}><Cpu size={18} /><span><b>{p.label}{!p.configured ? '（未配置）' : ''}</b><small>{p.id === 'team' ? '固废与资源循环领域专家问答' : p.model}</small></span>{chat.provider === p.id && <Check size={16} />}</button>)}<small className="model-menu-note">切换模型会开启一段新对话</small></div>}
        </div>
      </div><div className="send-area"><button type="button" className="icon-button attachment-button" aria-label="上传文件或图片" disabled={busy||uploading} title={`最多 ${fileConfig.max_files} 个文件 · 文档 ${fileConfig.file_mb} MB / 图片 ${fileConfig.image_mb} MB · 合计 ${fileConfig.total_mb} MB`} onClick={()=>fileInput.current?.click()}>{uploading?<LoaderCircle size={20} className="spin"/>:<Paperclip size={20}/>}</button>{busy ? <button className="send-button stop" type="button" onClick={() => controller.current?.abort()} aria-label="停止生成" title="停止生成"><Square size={16} fill="currentColor" /></button> : <button className="send-button" type="submit" aria-label="发送问题" disabled={(!draft.trim()&&!files.length) || !provider?.configured || uploading}><ArrowUp size={22} /></button>}</div></div>
      {uploading && <div className="upload-status" role="status">正在上传并解析附件…</div>}
    </form><div className="composer-caption"><span title={provider?.label}>{provider?.label || '正在连接…'}{settings.network_search?' · 中英检索 · 学术近十年优先':''}</span><span>最多5个 · 文档20MB / 图片10MB · 合计50MB</span></div>
  </div>;
  return <div className="app-shell">
    <header className="site-header"><button className="site-brand" disabled={busy} onClick={() => newChat()} aria-label="无隅 AI 首页"><Logo /><strong>无隅AI平台</strong></button><div className="header-actions"><span className="connection"><span className={`status-dot ${!provider?.configured ? 'muted' : ''}`} />{provider?.configured ? '模型已就绪' : '正在连接'}</span><span className="edition-badge">模型测试版</span></div></header>
    <div className={`workspace-shell ${sourceTurn ? 'sources-open' : ''} ${sidebarOpen ? 'history-open' : ''}`}>
    <nav className="icon-rail" aria-label="工作台导航">
      <button className={`icon-button ${sidebarOpen ? 'active' : ''}`} aria-label={sidebarOpen ? '收起历史对话' : '展开历史对话'} title="历史对话" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>{sidebarOpen ? <PanelLeftClose size={21} /> : <PanelLeftOpen size={21} />}</button>
      <button className="icon-button" aria-label="新建对话" title="新建对话" disabled={busy} onClick={() => newChat()}><Plus size={22} /></button>
      <button className="icon-button active" aria-label="对话工作台" title="对话工作台" onClick={() => { setSidebarOpen(false); textarea.current?.focus(); }}><MessageSquare size={21} /></button>
      <button className="icon-button" aria-label="导出当前对话" title="导出当前对话（含思考内容与来源）" disabled={busy || !chat.turns.length} onClick={download}><Download size={20} /></button>
      <button className={`icon-button rail-bottom ${settingsOpen ? 'active' : ''}`} aria-label="对话设置" title="对话设置" onClick={() => setSettingsOpen(!settingsOpen)}><Settings2 size={21} /></button>
    </nav>
    {sidebarOpen && <><button className="mobile-scrim" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />
    <aside className="sidebar">
      <div className="history-heading"><span>历史对话</span><button className="icon-button" onClick={() => setSidebarOpen(false)} aria-label="关闭历史对话"><X size={18} /></button></div>
      <button className="new-chat" disabled={busy} onClick={() => newChat()}><Plus size={18} /> 新建对话 <span>＋</span></button>
      <input className="history-search" aria-label="搜索对话" placeholder="搜索对话…" value={historyFilter} onChange={e => setHistoryFilter(e.target.value)} />
      <div className="section-label">最近对话 <span>{chats.length} / 30</span></div>
      <nav className="history" aria-label="对话历史">{[...chats].filter(item => item.title.includes(historyFilter.trim())).sort((a, b) => b.updated - a.updated).map(item => <div className={`history-row ${item.id === chat.id ? 'selected' : ''}`} key={item.id}>
        <button className="history-item" disabled={busy || uploading} onClick={() => { setSelected(item.id); setDraft(''); setFiles([]); setNotice(''); setSidebarOpen(false); stick.current = true;setShowLatest(false); }} title={item.title}>
          <MessageSquare size={16} /><div><span>{item.title}</span><small>{item.turns.length ? formatTime(item.updated) : '等待你的第一个问题'}</small></div>
        </button><button className="delete-chat icon-button" aria-label={`删除对话：${item.title}`} title="删除对话" disabled={busy} onClick={() => setDeleteTarget(item.id)}><Trash2 size={14} /></button>
      </div>)}{!chats.some(item => item.title.includes(historyFilter.trim())) && <p className="history-empty">没有找到匹配的对话</p>}</nav>
      <div className="sidebar-foot"><div><ShieldCheck size={16} /><strong>本机工作台</strong><span className="status-dot" /></div><p>会话保存在当前浏览器<br />API 密钥保存在本机后端</p><small>问题将发送到所选模型服务</small></div>
    </aside></>}
    <main className="main">
      <div className={`conversation ${!chat.turns.length ? 'empty-chat' : ''}`} ref={scrollbox} tabIndex={0} aria-label="对话内容" onScroll={onConversationScroll} onWheel={pauseFollow} onPointerDown={pauseFollow} onTouchStart={()=>{scrollIntent.current=true;stick.current=false;}} onKeyDown={e=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(e.key))pauseFollow(e);}}>
        {!chat.turns.length ? <section className="welcome"><h1>欢迎体验 <span>无隅 AI</span>，<br className="mobile-break" />今天想了解什么？</h1><p className="welcome-subtitle">固废与资源循环领域的专家问答</p>{composer}<div className="suggestions">{samples.map(sample => <button key={sample.category} title={sample.title} onClick={() => { setDraft(sample.text); textarea.current?.focus(); }}><sample.icon size={19} /><strong>{sample.category}</strong></button>)}</div></section> : <div className="messages"><div className="thread-title">{chat.title}</div>{chat.turns.map((turn, index) => <article className="turn" key={turn.id}>
          <div className="user-message"><div>{turn.user}</div><FileChips files={turn.files}/></div>
          <div className="assistant-message"><div className="assistant-label"><Logo /><strong>{provider?.label || '模型'}</strong>{turn.status === 'streaming' && <span className="generating"><LoaderCircle size={13} className="spin" />{turn.phase || (turn.reasoning && !turn.answer ? '正在思考' : turn.answer ? '正在回答' : '等待模型')} · {elapsed}s</span>}</div>
            {turn.reasoning ? <Reasoning turn={turn}/> : turn.status === 'streaming' && !turn.answer ? <div className="waiting"><span /><span /><span /><small>{turn.request.thinking ? '正在等待模型…' : '正在回答…'}</small></div> : null}
            <ToolHistory turn={turn} />
            {turn.answer && <Answer turn={turn} onSource={openSources}/>}
            {turn.error && <div className="turn-error" role="status">{turn.error}<button disabled={busy || uploading} onClick={() => { setDraft(turn.user);setFiles(turn.files||[]); textarea.current?.focus(); }}>重新编辑</button></div>}
            {turn.warning && <div className="turn-error" role="status">{turn.warning}</div>}
            <SourceButtons turn={turn} view={sourceView} onOpen={openSources}/>
            {turn.status !== 'streaming' && <div className="answer-footer"><div>{turn.meta?.seconds !== undefined && <span>{turn.meta.seconds}s</span>}{turn.meta?.usage?.completion_tokens != null && <span>输出 {turn.meta.usage.completion_tokens.toLocaleString()} tokens</span>}<span title="对话模型">{provider?.label}</span>{turn.meta?.first_token_seconds != null && <span title="从本机请求开始到收到首个内容片段">首段 {turn.meta.first_token_seconds}s</span>}</div><button className="icon-button" disabled={!turn.answer} title="复制回答" aria-label={`复制第 ${index + 1} 轮回答`} onClick={() => copy(turn)}>{copied === turn.id ? <Check size={15} /> : <Copy size={15} />}</button></div>}
          </div>
        </article>)}</div>}
      </div>
      {!!chat.turns.length && <div className="composer-dock">{showLatest && <button className="jump-latest" onClick={jumpLatest} aria-label="回到最新"><ArrowDown size={18}/><span>回到最新</span></button>}{composer}</div>}
      <footer className="platform-footer">无隅智循团队 · 固废与资源循环领域垂直大模型应用</footer>
    </main>
    {sourceTurn && <SourcePanel key={sourceTurn.id} turn={sourceTurn} view={sourceView} onKindChange={kind => setSourceView(view => ({ ...view, kind, sourceId: null }))} onClose={closeSources}/>}
    </div>
    {settingsOpen && <><button className="settings-scrim" aria-label="关闭对话设置" onClick={() => setSettingsOpen(false)} /><aside className="settings-panel" role="dialog" aria-modal="true" aria-label="对话设置"><div className="panel-heading"><div><span className="eyebrow">PREFERENCES</span><h2>对话设置</h2></div><button className="icon-button" aria-label="关闭设置面板" onClick={() => setSettingsOpen(false)}><X size={21} /></button></div><p className="panel-intro">设置从下一次提问开始生效。切换模型会开启新对话。</p><fieldset disabled={busy}><label className="setting"><span>温度 <b>{Number(settings.temperature).toFixed(1)}</b></span><input aria-label="温度" type="range" min="0" max="1.5" step="0.1" value={settings.temperature} onChange={e => setSettings({ ...settings, temperature: Number(e.target.value) })} /><small>较低更稳定，较高更多样。</small></label><label className="setting"><span>最大输出 tokens</span><select aria-label="最大输出 tokens" value={settings.max_tokens} onChange={e => setSettings({ ...settings, max_tokens: Number(e.target.value) })}>{[1024, 2048, 4096, 8192, 16384].map(n => <option key={n} value={n}>{n.toLocaleString()}</option>)}</select><small>思考内容和最终回答共用输出预算。</small></label><label className="setting"><span>携带历史轮数</span><select aria-label="携带历史轮数" value={settings.history} onChange={e => setSettings({ ...settings, history: Number(e.target.value) })}>{[0, 3, 5, 10, 20].map(n => <option key={n} value={n}>{n === 0 ? '不携带历史' : `最近 ${n} 轮完整问答`}</option>)}</select><small>仅携带完整问答；过长时从最早一轮缩减。思考内容不进入后续上下文。</small></label><label className="setting"><span>系统提示词</span><textarea aria-label="系统提示词" rows={6} maxLength={8000} value={settings.system} onChange={e => setSettings({ ...settings, system: e.target.value })} /><small>为所有模型设置相同提示词，便于比较回答。</small></label><button className="reset-settings" onClick={() => setSettings({ ...defaults })}>恢复默认设置</button></fieldset><div className="settings-note"><Brain size={19} /><p>思考面板只展示接口实际返回的推理字段或显式思考片段。没有返回时，页面会明确标注。</p></div></aside></>}
    {deleteTarget && <div className="modal-scrim"><div className="delete-dialog" role="dialog" aria-modal="true" aria-label="删除对话确认"><Trash2 size={24} /><h2>删除这段对话？</h2><p>将从当前浏览器移除该对话。需要保留内容时，请先导出记录。</p><div><button onClick={() => setDeleteTarget(null)}>取消</button><button className="danger" onClick={() => removeChat(deleteTarget)}>删除对话</button></div></div></div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App />);
