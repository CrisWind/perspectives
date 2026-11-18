/* Review-only, front-facing personality cards moving along an ellipse. */
(() => {
  const people = DESIGN_DATA.people;
  const stage = document.querySelector('.council-orbit');
  const host = document.getElementById('orbit-cards');
  const pause = document.getElementById('orbit-pause');
  const dialog = document.getElementById('personality-dialog');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const step = Math.PI * 2 / people.length;
  let phase = Math.PI / 2, userPaused = reduced.matches, hovered = false;
  let focused = false, visible = true, lastTime = null, swipeStart, swiped = false;
  let width = stage.clientWidth;
  host.innerHTML = people.map(p => `<button class="orbit-personality" aria-label="放大${p.name} ${p.mbti}的性格卡"><img src="${p.personalityCard}" alt="${p.name} · ${p.mbti} 性格卡" draggable="false"></button>`).join('');
  const cards = [...host.children];
  let featured = -1;
  function paint() {
    const cardWidth = Math.min(204, Math.max(126, width * .19));
    const rx = Math.max(0, (width - cardWidth) / 2 - 6);
    const ry = width < 600 ? 50 : 68;
    let nearest = 0, nearestDepth = -1;
    cards.forEach((card, i) => {
      const angle = phase - i * step;
      const depth = (Math.sin(angle) + 1) / 2;
      if (depth > nearestDepth) { nearest = i; nearestDepth = depth; }
      const scale = .46 + depth * .54;
      const x = Math.cos(angle) * rx;
      const y = Math.sin(angle) * ry;
      card.style.width = cardWidth + 'px';
      card.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${scale})`;
      card.style.zIndex = Math.round(depth * 100) + 2;
      card.style.opacity = .66 + depth * .34;
    });
    if (nearest !== featured) {
      featured = nearest;
      document.getElementById('orbit-mbti').textContent = people[nearest].mbti;
      document.getElementById('orbit-name').textContent = people[nearest].name;
    }
  }
  function updatePause() {
    pause.textContent = userPaused ? '继续轮转' : '暂停轮转';
    pause.setAttribute('aria-pressed', String(userPaused));
  }
  function move(direction) {
    userPaused = true;
    phase = Math.round((phase - Math.PI / 2) / step) * step + Math.PI / 2 + direction * step;
    updatePause(); paint();
  }
  pause.onclick = () => { userPaused = !userPaused; updatePause(); };
  document.getElementById('orbit-prev').onclick = () => move(-1);
  document.getElementById('orbit-next').onclick = () => move(1);
  stage.addEventListener('pointerover', e => { if (e.pointerType !== 'touch' && e.target.closest('.orbit-personality')) hovered = true; });
  stage.addEventListener('pointerout', e => { if (e.pointerType !== 'touch' && !(e.relatedTarget && e.relatedTarget.closest('.orbit-personality'))) hovered = false; });
  stage.addEventListener('focusin', () => { focused = true; });
  stage.addEventListener('focusout', e => { focused = stage.contains(e.relatedTarget); });
  stage.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault(); move(e.key === 'ArrowLeft' ? -1 : 1);
    }
  });
  stage.addEventListener('touchstart', e => { swipeStart = e.changedTouches[0].clientX; swiped = false; }, {passive:true});
  stage.addEventListener('touchend', e => {
    const delta = e.changedTouches[0].clientX - swipeStart;
    if (Math.abs(delta) > 40) { swiped = true; move(delta > 0 ? -1 : 1); }
  }, {passive:true});
  cards.forEach((card, i) => card.onclick = () => {
    if (swiped) { swiped = false; return; }
    const p = people[i];
    document.getElementById('personality-title').textContent = p.name + ' · ' + p.mbti;
    const image = document.getElementById('personality-image');
    image.src = p.personalityCard;
    image.alt = p.name + '的完整性格卡';
    dialog.showModal();
  });
  document.getElementById('close-personality').onclick = () => dialog.close();
  dialog.addEventListener('click', e => { if (e.target === dialog) {
    const rect = dialog.getBoundingClientRect();
    if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) dialog.close();
  }});
  new ResizeObserver(() => { width = stage.clientWidth; paint(); }).observe(stage);
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; }).observe(stage);
  reduced.addEventListener('change', () => { userPaused = reduced.matches; updatePause(); });
  function tick(time) {
    const dt = lastTime === null ? 0 : Math.min(time - lastTime, 50);
    lastTime = time;
    if (!userPaused && !hovered && !focused && !dialog.open && !document.getElementById('sample-dialog').open && visible && !document.hidden) {
      phase += dt * Math.PI * 2 / 100000;
      paint();
    }
    requestAnimationFrame(tick);
  }
  updatePause(); paint(); requestAnimationFrame(tick);
})();
