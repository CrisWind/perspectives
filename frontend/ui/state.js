/* Shared draft survives navigation and refresh; storage failure stays non-fatal. */
window.Council = (() => {
  const key = 'perspectives.draft.v1';
  const roster = DESIGN_DATA.people;
  let memory = {};
  function read(name) {
    try { return JSON.parse(localStorage.getItem(name)); } catch { return memory[name] ?? null; }
  }
  function write(name, value) {
    memory[name] = value;
    try { localStorage.setItem(name, JSON.stringify(value)); } catch { /* Private storage may be unavailable. */ }
  }
  function draft() {
    const saved = read(key) || {};
    const text = value => typeof value === 'string' ? value : '';
    return {
      question: text(saved.question), leaning: text(saved.leaning), reasons: text(saved.reasons),
      selected: Array.isArray(saved.selected) ? [...new Set(saved.selected)].filter(id => roster.some(p => p.id === id)).slice(0, 4) : roster.slice(0, 4).map(p => p.id),
      sage: text(saved.sage) || (saved.thinker === true ? 'wangyangming' : ''), customized: saved.customized === true,
      focus: Number.isInteger(saved.focus) && saved.focus >= 0 && saved.focus < roster.length ? saved.focus : 0,
    };
  }
  const base = location.port === '8000' ? '' : `${location.protocol}//${location.hostname}:${['5174', '5175'].includes(location.port) ? '8001' : '8000'}`;
  async function api(path, data) {
    let response;
    try {
      response = await fetch(base + '/api' + path, {
        ...(data === undefined ? {} : { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
        signal: AbortSignal.timeout(60000),
      });
    } catch { throw new Error('暂时无法连接辩论服务。已保留填写内容，请稍后重试。'); }
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '提交未成功，请稍后重试。');
    return payload;
  }
  return { draft, save: value => write(key, value), read, write, api };
})();
