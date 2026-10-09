export function sourceHost(source) {
  try { return new URL(source.url).hostname.replace(/^www\./, ''); }
  catch { return '来源网站'; }
}

export function sourceTitle(source) {
  const title = String(source.title || '').trim();
  return !title || /^(?:var|let|const)\s+\w+\s*=|document\.write\(|<script|^您访问的链接即将离开/i.test(title)
    ? `来源页面 · ${sourceHost(source)}` : title;
}

export const sourceKind = source => source.type === 'scholar' ? 'scholar' : 'webpage';

export function sourceMap(turn) {
  const map = new Map();
  function add(source, scholarly = false) {
    if (!/^S\d+$/.test(source?.source_id)) return;
    try {
      const url = new URL(source.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return;
    } catch { return; }
    const old = map.get(source.source_id);
    map.set(source.source_id, { ...old, ...source,
      type: scholarly || old?.type === 'scholar' || source.type === 'scholar' ? 'scholar' : 'webpage' });
  }
  for (const call of turn.toolCalls || []) {
    for (const source of call.sources || []) add(source, /scholar/.test(call.name || ''));
  }
  // Older conversations may only retain the citation list. Preserve their IDs.
  for (const source of turn.citations || []) add(source);
  return map;
}

export function sourceCatalog(turn) {
  const cited = new Set((turn.citations || []).map(source => source.source_id));
  return [...sourceMap(turn).values()].sort((a, b) => Number(a.source_id.slice(1)) - Number(b.source_id.slice(1)))
    .map(source => ({ ...source, cited: cited.has(source.source_id) }));
}
