// The presentation advances only when the reader explicitly finishes a complete speech.
let presentationState = 'idle';
let sendingInterjection = false;
const stageActions = node('div', null, 'stage-actions');
const finishSpeech = button('读完了', () => {
  if (presentationState !== 'speech') return;
  presentationState = 'gap';
  interjectGap = true;
  clearInterval(motionTimer);
  if (animatedImage?.dataset.rest) animatedImage.src = animatedImage.dataset.rest;
  document.querySelectorAll('.actor').forEach(a => a.classList.remove('active', 'is-talking', 'speaking'));
  syncStage();
});
const nextSpeech = button('继续下一位', () => {
  if (presentationState !== 'gap' || sendingInterjection) return;
  $('interject').hidden = true;
  interjectGap = false;
  presentNext();
});
stageActions.append(finishSpeech, nextSpeech);
document.querySelector('.stage-caption').after(stageActions);
const lights = node('div', null, 'stage-lights');
for (let i = 0; i < 5; i++) lights.append(node('span', null, 'lamp-glow'));
lights.setAttribute('aria-hidden', 'true');
document.querySelector('.theater').append(lights);
const paperAction = button('', () => {
  if (paperAction.disabled) return;
  $('interject').hidden = !$('interject').hidden;
  if (!$('interject').hidden) $('reply').focus();
});
paperAction.className = 'paper-action';
paperAction.setAttribute('aria-label', '插一句，打开文字输入');
paperAction.append(node('span', '插一句'));
document.querySelector('.theater').append(paperAction);
function syncStage() {
  const isSpeech = presentationState === 'speech';
  finishSpeech.hidden = !isSpeech;
  nextSpeech.hidden = presentationState !== 'gap';
  nextSpeech.disabled = !stageQueue.length || sendingInterjection;
  paperAction.disabled = presentationState !== 'gap' || sendingInterjection;
  if (paperAction.disabled) $('interject').hidden = true;
  const actors = [...$('cast').children];
  const index = isSpeech ? actors.findIndex(a => a.dataset.role === currentStageEntry?.persona_id) : -1;
  [...lights.children].forEach((light, i) => light.classList.toggle('on', i === index));
  updateInterjectAvailability();
}
function presentNext() {
  if (!stageQueue.length) { presentationState = 'gap'; interjectGap = hasGuestSpeech; syncStage(); maybeOpenIntermission(); return; }
  presentationState = 'speech';
  interjectGap = false;
  animateSpeech(stageQueue.shift());
  syncStage();
}
function queueSpeech(entry) {
  if (entry.persona_id === 'user') return;
  stageQueue.push(entry);
  if (presentationState === 'idle') presentNext();
  else syncStage();
}
function resetPresentation() {
  presentationState = 'idle';
  sendingInterjection = false;
  syncStage();
}
syncStage();
