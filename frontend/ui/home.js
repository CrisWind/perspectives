(() => {
  const sageDescription = document.getElementById('sage-description');
  const sagePeople = document.getElementById('sage-people');
  const sageBook = document.getElementById('sage-book');
  function renderSages(sages) {
    if (!sageDescription || !sagePeople) return;
    if (!sages.length) {
      sageDescription.textContent = '思想家视角正在准备中，之后会和不同的嘉宾一起加入你的圆桌。';
      return;
    }
    const names = sages.map(s => s.name).join('、');
    const lead = sages[0];
    sageBook.textContent = lead.name.length > 3 ? '思想' : lead.name;
    sageDescription.textContent = `邀请${names}的思想视角加入讨论，从各自的时代与处境出发，提供另一种看法。`;
    sagePeople.replaceChildren(...sages.map(sage => {
      const item = document.createElement('span');
      item.className = 'sage-person';
      item.textContent = sage.name;
      item.title = [sage.era, sage.tagline].filter(Boolean).join(' · ');
      return item;
    }));
  }
  if (sageDescription && window.Council) {
    Council.api('/personas').then(catalog => renderSages(catalog.sages || [])).catch(() => {
      sageDescription.textContent = '思想家视角暂时无法连接，进入选择页即可重试。';
    });
  }
  const dialog = document.getElementById('sample-dialog');
  const frame = document.getElementById('sample-frame');
  document.querySelectorAll('[data-open-sample]').forEach(button => {
    button.onclick = () => {
      frame.src = 'sample.html';
      dialog.showModal();
    };
  });
  document.getElementById('close-sample').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { frame.src = 'about:blank'; });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  window.addEventListener('message', event => {
    if (event.source === frame.contentWindow && event.data?.type === 'close-sample') dialog.close();
  });
})();
