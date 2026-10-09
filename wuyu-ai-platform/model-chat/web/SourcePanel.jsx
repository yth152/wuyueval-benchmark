import React, { useEffect, useRef, useState } from 'react';
import { BookOpen, Check, ChevronRight, ExternalLink, Globe, X } from 'lucide-react';
import { sourceCatalog, sourceHost, sourceKind, sourceTitle } from './sources.mjs';
import './sources.css';

const kinds = [{ id: 'webpage', label: '网页', unit: '个', Icon: Globe },
               { id: 'scholar', label: '文献', unit: '篇', Icon: BookOpen }];

function SourceBadge({ source }) {
  const host = sourceHost(source);
  const tone = [...host].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4;
  return <span aria-hidden="true" className={`source-badge tone-${tone}`}>{host[0].toUpperCase()}</span>;
}

export function SourceButtons({ turn, view, onOpen }) {
  if (!turn.request?.network_search || turn.status === 'streaming') return null;
  const sources = sourceCatalog(turn);
  const citedCount = sources.filter(source => source.cited).length;
  return <div className="source-summary">
    {!!sources.length && <div className="source-buttons" aria-label="查看搜索来源">{kinds.map(({ id, label, unit, Icon }) => {
      const group = sources.filter(source => sourceKind(source) === id);
      const icons = [...new Map(group.map(source => [sourceHost(source), source])).values()].slice(0, 3);
      const active = view?.turnId === turn.id && view.kind === id;
      return <button key={id} type="button" className={`source-pill ${active ? 'active' : ''}`} disabled={!group.length}
        aria-label={`查看${group.length}${unit}${label}来源`} aria-expanded={active} aria-controls="source-panel"
        title={`${group.length}${unit}${label}，其中${group.filter(source => source.cited).length}${unit}被正文引用`}
        onClick={event => onOpen(turn, id, null, event.currentTarget)}>
        {icons.length ? <span className="source-avatar-stack">{icons.map(source => <SourceBadge key={sourceHost(source)} source={source}/>)}</span> : <Icon size={17}/>}
        <span>{group.length} {unit}{label}</span><ChevronRight size={14}/>
      </button>;
    })}</div>}
    {sources.length ? <p className="source-summary-note">正文引用 {citedCount} 个来源 · 点击查看详情</p>
      : <p className="source-summary-note">{turn.toolCalls?.length ? '本次搜索未取得可用来源。' : '本次未调用搜索工具。'}</p>}
    {!!sources.length && !citedCount && <p className="source-summary-note">检索结果已保留，回答尚未标明可匹配的引用。</p>}
    {!!turn.searchMeta?.unmatched_ids?.length && <p className="citation-warning">未匹配的引用：{turn.searchMeta.unmatched_ids.join('、')}</p>}
  </div>;
}

export function SourcePanel({ turn, view, onKindChange, onClose }) {
  const [citedOnly, setCitedOnly] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width: 1080px)').matches);
  const panel = useRef(null), closeButton = useRef(null), list = useRef(null), cards = useRef(new Map());
  const sources = sourceCatalog(turn);
  const group = sources.filter(source => sourceKind(source) === view.kind);
  const shown = group.filter(source => !citedOnly || source.cited);
  const citedCount = group.filter(source => source.cited).length;

  useEffect(() => {
    const media = window.matchMedia('(max-width: 1080px)');
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { closeButton.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => { setCitedOnly(false); }, [view.sourceId, view.kind, view.openKey]);
  useEffect(() => {
    if (view.sourceId) {
      const element = cards.current.get(view.sourceId);
      if (element && list.current) list.current.scrollTo({ top: list.current.scrollTop + element.getBoundingClientRect().top - list.current.getBoundingClientRect().top - 12 });
    } else if (list.current) list.current.scrollTop = 0;
  }, [view.sourceId, view.kind, citedOnly, view.openKey]);

  function keyboard(event) {
    if (event.key === 'Escape') { event.stopPropagation(); onClose(); return; }
    if (!compact || event.key !== 'Tab') return;
    const focusable = [...panel.current.querySelectorAll('button:not(:disabled),a[href],input,summary,[tabindex]')]
      .filter(element => element.tabIndex >= 0 && element.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }

  return <>
    {compact && <button className="source-panel-scrim" aria-label="关闭来源面板遮罩" tabIndex={-1} onClick={onClose}/>}
    <aside className="source-panel" id="source-panel" ref={panel} role={compact ? 'dialog' : 'complementary'}
      aria-modal={compact || undefined} aria-labelledby="source-panel-title" onKeyDown={keyboard}>
      <header className="source-panel-header"><h2 id="source-panel-title">搜索来源</h2>
        <button className="icon-button" ref={closeButton} aria-label="关闭来源面板" title="关闭" onClick={onClose}><X size={20}/></button></header>
      <div className="source-panel-tabs" role="tablist" aria-label="来源类型">{kinds.map(({ id, label, Icon }) =>
        <button key={id} id={`source-tab-${id}`} role="tab" aria-selected={view.kind === id} aria-controls="source-results"
          tabIndex={view.kind === id ? 0 : -1} onClick={() => onKindChange(id)}
          onKeyDown={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
            event.preventDefault(); const next = event.key === 'Home' ? 'webpage' : event.key === 'End' ? 'scholar' : id === 'scholar' ? 'webpage' : 'scholar';
            onKindChange(next); document.getElementById(`source-tab-${next}`)?.focus();
          } }}><Icon size={16}/><span>{label}</span><span className="source-tab-count">{sources.filter(source => sourceKind(source) === id).length}</span></button>
      )}</div>
      <div className="source-panel-filter"><span>{group.length} 个来源 · 已引用 {citedCount} 个</span>
        <label><input type="checkbox" checked={citedOnly} onChange={event => setCitedOnly(event.target.checked)}/>仅看已引用</label></div>
      <div className="source-panel-results" ref={list} id="source-results" role="tabpanel" aria-labelledby={`source-tab-${view.kind}`} tabIndex={0}>
        {shown.length ? <ol>{shown.map(source => <li key={source.source_id} ref={element => { if (element) cards.current.set(source.source_id, element); else cards.current.delete(source.source_id); }}
          className={`source-card ${view.sourceId === source.source_id ? 'selected' : ''}`}>
          <div className="source-card-meta"><SourceBadge source={source}/><span title={sourceHost(source)}>{sourceHost(source)}</span>
            {source.date && <time>{source.date}</time>}<span className="source-id">{source.source_id.slice(1)}</span></div>
          <a className="source-card-title" href={source.url} target="_blank" rel="noopener noreferrer">{sourceTitle(source)}<ExternalLink size={13}/></a>
          {source.type === 'scholar' && !!source.authors?.length && <p className="source-card-authors" title={source.authors.join('、')}>{source.authors.join('、')}</p>}
          {source.snippet && <details className="source-excerpt"><summary><span>{source.snippet}</span><small>展开摘要</small></summary><p>{source.snippet}</p></details>}
          <div className="source-card-footer">{source.cited ? <span className="source-cited"><Check size={12}/>正文已引用</span> : <span>检索结果</span>}
            {source.year_status === 'unknown' && <span>发表年份未标明</span>}</div>
        </li>)}</ol> : <div className="source-panel-empty"><BookOpen size={27}/><p>{citedOnly ? '这类来源尚未被正文引用' : `本次没有${view.kind === 'scholar' ? '文献' : '网页'}来源`}</p>
          {citedOnly && <button onClick={() => setCitedOnly(false)}>查看全部来源</button>}</div>}
      </div>
      <footer className="source-panel-note">来源编号对应正文引用；检索摘要不代表已阅读全文。</footer>
    </aside>
  </>;
}
