(() => {
  const people = DESIGN_DATA.people, el = id => document.getElementById(id);
  const draft = Council.draft(), selected = new Set(draft.selected);
  let focus = draft.focus, customized = draft.customized, busy = false, sages = [];
  const sageOption = el('thinker').closest('.sage-option');
  sageOption.outerHTML = '<section class="sage-option" aria-labelledby="sage-title"><div class="sage-heading"><span class="sage-glyph" aria-hidden="true">知</span><div class="sage-copy"><strong id="sage-title">邀请一位思想家</strong><p id="sage-status" role="status">正在确认可用状态…</p></div></div><div class="sage-select-wrap"><label for="thinker">思想家</label><select id="thinker" disabled aria-describedby="sage-detail"><option value="">不邀请思想家</option></select></div><p class="sage-detail" id="sage-detail">为圆桌增添一个跨越时代的视角。</p></section>';
  el('question').value = draft.question;
  el('leaning').value = draft.leaning;
  el('reasons').value = draft.reasons;
  function save() {
    Council.save({question:el('question').value, leaning:el('leaning').value, reasons:el('reasons').value,
      selected:[...selected], sage:el('thinker').value, customized, focus});
  }
  function validation() {
    const question = el('question').value.trim();
    const reasons = el('reasons').value.split('\n').map(s => s.trim()).filter(Boolean);
    if (question.length < 4) return '请写下至少 4 个字的问题。';
    if (question.length > 1000) return '问题请控制在 1000 字以内。';
    if (reasons.length > 6 || reasons.some(s => s.length > 200)) return '理由最多 6 条，每条不超过 200 字。';
    if (selected.size < 2 || selected.size > 4) return '邀请 2–4 位嘉宾即可继续。';
    if (el('thinker').value && !sages.some(s => s.id === el('thinker').value)) return '所选思想家暂不可用。';
    return '';
  }
  function updateSummary() {
    const sage = sages.find(s => s.id === el('thinker').value);
    el('sage-detail').textContent = sage ? [sage.era, sage.tagline].filter(Boolean).join(' · ') : '为圆桌增添一个跨越时代的视角。';
    const error = validation();
    el('summary').textContent = (el('question').value.trim() ? '问题已填写' : '等待填写问题') + ' · ' + selected.size + ' 位嘉宾' + (el('thinker').value ? ' + 1 位思想家' : '');
    el('enter').disabled = busy || !!error;
    el('enter').textContent = busy ? '正在准备圆桌…' : '进入圆桌会议 ↗';
    el('status').textContent = error || '阵容已就绪，带着你的问题入座。';
    el('leanings').querySelectorAll('button').forEach(button => {
      const active = button.textContent === el('leaning').value;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    save();
  }
  function card(person, main) {
    return `<img src="${person.personalityCard}" alt="${main ? person.name + ' · ' + person.mbti + ' 性格卡' : ''}" draggable="false">${main && selected.has(person.id) ? '<span class="selected-mark">✓ 已选</span>' : ''}`;
  }
  function render() {
    const p = people[focus];
    el('role-mbti').textContent = p.mbti;
    el('role-name').textContent = p.name;
    el('main-card').innerHTML = card(p, true);
    el('main-card').setAttribute('aria-label', '放大' + p.name + ' ' + p.mbti + '的性格卡');
    el('prev-card').innerHTML = card(people[(focus + people.length - 1) % people.length]);
    el('next-card').innerHTML = card(people[(focus + 1) % people.length]);
    el('counter').textContent = String(focus + 1).padStart(2, '0') + ' / 16';
    const joined = selected.has(p.id);
    el('select-role').textContent = joined ? '✓ 已加入 · 点击移出' : '+ 邀请 ' + p.name + ' 加入';
    el('select-role').disabled = busy || (!joined && selected.size === 4);
    el('select-role').setAttribute('aria-pressed', String(joined));
    el('selection-hint').textContent = !joined && selected.size === 4 ? '已选满 4 位，可先从下方阵容移出一位' : '浏览卡片不会改变已选阵容';
    el('roster-count').textContent = '你的阵容 · ' + selected.size + ' / 4';
    el('roster-label').textContent = customized ? '自定义阵容 · 至少 2 位' : '默认推荐，可自由替换';
    el('seats').innerHTML = [...selected].map(id => people.find(p => p.id === id)).map(p => `<div class="seat"><img src="${p.portrait}" alt=""><b>${p.name}<small>${p.mbti}</small></b><button type="button" data-remove="${p.id}" aria-label="移出${p.name}" ${busy?'disabled':''}>×</button></div>`).join('');
    updateSummary();
  }
  function move(delta) { focus = (focus + delta + people.length) % people.length; render(); }
  el('previous').onclick = () => move(-1);
  el('next').onclick = () => move(1);
  el('select-role').onclick = () => {
    if (busy) return;
    const id = people[focus].id;
    if (selected.has(id)) selected.delete(id); else if (selected.size < 4) selected.add(id);
    customized = true; render();
  };
  el('seats').onclick = event => {
    const button = event.target.closest('[data-remove]');
    if (!button || busy) return;
    selected.delete(button.dataset.remove); customized = true; render();
    el('select-role').focus();
  };
  for (const id of ['question','leaning','reasons']) el(id).oninput = updateSummary;
  el('thinker').onchange = updateSummary;
  el('leanings').onclick = event => {
    if (!event.target.matches('button') || busy) return;
    el('leaning').value = event.target.textContent; updateSummary();
  };
  const stack = document.querySelector('.stack');
  stack.onkeydown = event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1);
    }
  };
  let touchX, swiped = false;
  stack.addEventListener('touchstart', event => { touchX = event.changedTouches[0].clientX; swiped = false; }, {passive:true});
  stack.addEventListener('touchend', event => {
    const dx = event.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 45) { swiped = true; move(dx > 0 ? -1 : 1); }
  }, {passive:true});
  el('main-card').onclick = () => {
    if (swiped) { swiped = false; return; }
    const p = people[focus];
    el('personality-title').textContent = p.name + ' · ' + p.mbti;
    el('personality-image').src = p.personalityCard;
    el('personality-image').alt = p.name + '的完整性格卡';
    el('personality-dialog').showModal();
  };
  el('close-personality').onclick = () => el('personality-dialog').close();
  el('personality-dialog').onclick = event => {
    if (event.target !== event.currentTarget) return;
    const r = event.currentTarget.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.currentTarget.close();
  };
  function setBusy(value) {
    busy = value;
    document.querySelectorAll('.form-panel input,.form-panel textarea,.form-panel button').forEach(control => control.disabled = value);
    el('thinker').disabled = value || !sages.length;
    render();
  }
  el('enter').onclick = async () => {
    if (busy || validation()) return;
    const payload = {dilemma:el('question').value.trim(), leaning:el('leaning').value.trim(),
      reasons:el('reasons').value.split('\n').map(s => s.trim()).filter(Boolean),
      mode:'deep', options:{team:[...selected], skip_questions:true, ...(el('thinker').value ? {sage:el('thinker').value} : {})}};
    save(); setBusy(true); el('submit-error').textContent = '';
    try {
      // Retry a failed start against the same session instead of creating duplicates.
      const signature = JSON.stringify(payload), pending = Council.read('perspectives.pending');
      let result = pending?.signature === signature ? await Council.api('/sessions/' + encodeURIComponent(pending.id)) : await Council.api('/sessions', payload);
      if (result.status === 'safety') throw new Error('这个问题需要先得到真人的支持，暂时不适合展开辩论。');
      Council.write('perspectives.pending', {id:result.id, signature});
      if (['ready','clarifying'].includes(result.status)) await Council.api('/sessions/' + result.id + '/start', {});
      else if (result.status !== 'running' && result.status !== 'completed') {
        Council.write('perspectives.pending', null);
        throw new Error('这场讨论已中断，请重新开始。');
      }
      Council.write('perspectives.session', {id:result.id, selected:[...selected]});
      Council.write('perspectives.pending', null);
      location.assign('/roundtable/?session=' + encodeURIComponent(result.id));
    } catch (error) {
      el('submit-error').textContent = error.message;
      el('submit-error').focus(); setBusy(false);
    }
  };
  Promise.all([Council.api('/health'), Council.api('/personas')]).then(([health, catalog]) => {
    sages = catalog.sages || [];
    for (const sage of sages) el('thinker').add(new Option(sage.name, sage.id));
    el('thinker').value = sages.some(s => s.id === draft.sage) ? draft.sage : '';
    el('sage-status').textContent = sages.length ? '额外 +1 席，不占嘉宾名额' : '暂未开放 · 资料准备中';
    el('thinker').disabled = busy || !sages.length;
    el('service-status').textContent = health.mock ? '演示模式 · 使用模拟辩论内容' : '辩论服务已连接';
    updateSummary();
  }).catch(() => {
    el('service-status').textContent = '辩论服务暂未连接，填写内容会保留。';
    el('sage-status').textContent = '暂时无法确认可用状态';
  });
  window.addEventListener('pageshow', event => { if (event.persisted) setBusy(false); });
  render();
})();
