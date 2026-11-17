/* Route and session adapter for the existing wooden roundtable presentation. */
(() => {
  const saved = Council.read('perspectives.session');
  const sid = new URLSearchParams(location.search).get('session') || saved?.id;
  const back = node('a', '← 调整问题与阵容', 'meeting-back');
  back.href = '/choose/';
  document.querySelector('header').append(back);
  const heading = node('section', null, 'meeting-heading');
  heading.setAttribute('aria-label', '圆桌会议');
  const title = node('div');
  title.append(node('span', 'MEET YOUR COUNCIL / 03', 'meeting-eyebrow'), node('h1', '让不同的视角，围坐一起。'));
  const phase = node('span', $('phase').textContent, 'meeting-phase');
  // Mirror the live status without moving the original element out of its panel.
  new MutationObserver(() => { phase.textContent = $('phase').textContent; }).observe($('phase'), {childList:true, subtree:true, characterData:true});
  heading.append(title, phase);
  document.querySelector('header').after(heading);
  const status = node('p', '', 'meeting-status');
  status.id = 'meeting-status'; status.setAttribute('role', 'status');
  heading.after(status);
  new MutationObserver(() => { status.textContent = $('notice').textContent; }).observe($('notice'), {childList:true, subtree:true, characterData:true});
  $('dilemma-form').onsubmit = event => event.preventDefault();
  $('dilemma-form').querySelector('.actions').hidden = true;
  for (const id of ['dilemma','leaning','reasons']) $(id).readOnly = true;
  $('mbti').hidden = true;
  document.querySelector('label[for="mbti"]').hidden = true;
  const edit = node('a', '修改问题与阵容 ↗', 'meeting-edit');
  edit.href = '/choose/'; $('dilemma-form').append(edit);
  const participants = node('p', '', 'meeting-participants');
  $('dilemma-form').append(participants);
  const emptyCard = '辩论结束后将生成决策卡。';
  // Anchor the conversation over the unused space below the tabletop. The
  // console can grow downwards for small screens or an expanded reply form.
  stageActions.append(paperAction);
  const scene = node('div', null, 'meeting-scene');
  const theater = document.querySelector('.theater');
  const consolePanel = node('div', null, 'meeting-console');
  theater.before(scene);
  scene.append(theater, consolePanel);
  consolePanel.append(document.querySelector('.stage-caption'), stageActions, document.querySelector('.table-controls'));
  $('reply').placeholder = '补充一个事实，或说说你的不同看法';
  const applyTeam = roles => {
    team = roles; cast();
    participants.textContent = '本场嘉宾：' + roles.map(role => person(role.id).name + (role.mbti ? ' · ' + role.mbti : '')).join('、');
  };
  let loading = false;
  async function restore() {
    if (loading) return;
    loading = true;
    enterDiscussion(false);
    try {
      if (!sid) {
        const draft = Council.draft();
        applyTeam(draft.selected.map(id => DESIGN_DATA.people.find(p => p.id === id)));
        $('dilemma').value = draft.question;
        $('leaning').value = draft.leaning;
        $('reasons').value = draft.reasons;
        $('phase').textContent = '等待入座';
        document.querySelector('.stage-caption').replaceChildren(node('strong', '等待入座'), node('p', '先填写你的问题，再邀请 2–4 位嘉宾加入讨论。'));
        $('notice').textContent = '还没有开始讨论，请先到选择页填写问题并邀请嘉宾。';
        return;
      }
      $('notice').textContent = '正在载入本场讨论…';
      const snapshot = await Council.api('/sessions/' + encodeURIComponent(sid));
      // The snapshot omits the chosen team; the public event history contains it.
      const history = await Council.api('/sessions/' + encodeURIComponent(sid) + '/events?format=json');
      reset(); enterDiscussion(false); session = snapshot;
      const selectedEvent = history.events.find(event => event.type === 'team.selected');
      const ids = saved?.id === sid ? saved.selected : Council.draft().selected;
      applyTeam(selectedEvent?.data.team || (ids || []).map(id => DESIGN_DATA.people.find(p => p.id === id)).filter(Boolean));
      $('dilemma').value = snapshot.dilemma;
      $('leaning').value = snapshot.leaning || '';
      $('reasons').value = (snapshot.reasons || []).map(r => r.text).join('\n');
      lastSeq = snapshot.last_seq || 0;
      renderReasons();
      for (const entry of snapshot.utterances || []) addSpeech(entry);
      if (snapshot.card) renderCard(snapshot.card);
      else $('card').textContent = emptyCard;
      if (snapshot.status === 'running') { setBusy(true); connect(); }
      else if (['ready','clarifying'].includes(snapshot.status)) {
        $('notice').textContent = '这场讨论尚未开始，请点击下方按钮继续。';
        const start = button('开始这场讨论', async () => {
          start.disabled = true;
          try { await Council.api('/sessions/' + sid + '/start', {}); start.remove(); setBusy(true); connect(); }
          catch (error) { $('notice').textContent = error.message; start.disabled = false; }
        });
        start.className = 'meeting-retry';
        status.after(start);
      } else {
        postCardMode = snapshot.status === 'completed';
        $('phase').textContent = postCardMode ? '决策卡已生成' : '讨论已结束';
        $('interject').querySelector('button[type="submit"]').textContent = postCardMode ? '对决策卡提异议' : '插一句';
        $('notice').textContent = snapshot.status === 'failed' ? '这场讨论已中断。可返回选择页重新开始。' : snapshot.status === 'safety' ? '这个问题需要先得到真人的支持，暂不展开辩论。' : '';
        setBusy(false); syncStage();
      }
    } catch (error) {
      $('notice').textContent = error.message;
      const retry = button('重新连接', () => { retry.remove(); restore(); });
      retry.className = 'meeting-retry'; status.after(retry);
    } finally { loading = false; }
  }
  // Keep keyboard focus inside the centered information card while it is open.
  sidebarSections.forEach(({section, button, id}) => {
    section.setAttribute('role','dialog');
    section.setAttribute('aria-modal','true');
    section.setAttribute('aria-label', button.textContent.replace(/[0-9›]/g,'').trim());
    button.addEventListener('click', () => {
      if (activePanel === id) section.querySelector('.panel-close').focus({preventScroll:true});
    });
    section.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...section.querySelectorAll('a,button,input,textarea,select,[tabindex="0"]')].filter(e => !e.disabled && e.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
  });
  window.addEventListener('pagehide', () => stream?.close());
  window.addEventListener('pageshow', event => { if (event.persisted) restore(); });
  restore();
})();
