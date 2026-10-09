import React,{useState} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {Brain,ChevronDown,ExternalLink,FileText,FlaskConical,Globe,Search,X} from 'lucide-react';
import logo from './logo.png';
import {sourceMap,sourceTitle,sourceKind} from './sources.mjs';
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
    {turn.toolCalls.map(call => <div className="tool-record" key={call.call_id}><div className="tool-record-title">{/scholar/.test(call.name) ? <FlaskConical size={15} /> : <Search size={15} />}<strong>{call.label||toolLabel(call.name)}{call.language ? ` · ${call.language === 'zh' ? '中文' : '英文'}` : ''}</strong><span className={call.status === 'error' ? 'tool-failed' : ''}>{call.status === 'running' ? (turn.status === 'streaming' ? '正在调用…' : '已中断') : call.status === 'error' ? '调用失败' : `${call.sources?.length||0} 个来源${call.data?' · 已返回数据':''}${call.cached?' · 复用结果':''}`}</span></div>
      <p className="tool-query">{call.query || '工具查询'}</p>{call.error && <p className="tool-failed">{call.error}</p>}
      {call.data&&<details className="tool-sources"><summary>查看工具返回数据</summary><pre className="tool-test-result">{call.data}</pre>{call.truncated&&<small>结果过长，已截取部分内容。</small>}</details>}{call.date_window && <p className="tool-date">近十年优先 · {call.date_window.from} 至 {call.date_window.to}{call.excluded_by_date ? ` · 已排除 ${call.excluded_by_date} 个范围外结果` : ''}</p>}
      {!!call.sources?.length && <details className="tool-sources"><summary>查看本次检索结果{call.seconds != null ? ` · ${call.seconds}s` : ''}</summary><SourceList sources={call.sources} compact /></details>}
    </div>)}
  </details>;
}


export {Logo,FileChips,Reasoning,Answer,ToolHistory,citedMarkdown};
