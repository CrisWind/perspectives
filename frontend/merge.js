(() => {
  const people=window.DESIGN_DATA.people;
  let focus=0;
  const gallery=document.createElement('section');gallery.className='merge-gallery';gallery.id='role-gallery';
  gallery.innerHTML=`<div class="gallery-heading"><div><span class="eyebrow">MEET YOUR COUNCIL</span><h2>让不一样的想法，坐在一起。</h2><p>16张原版性格卡沿圆环轮转。点击卡片转到面前，再邀请四位嘉宾入座。</p></div><span id="lineup-count"></span></div><div class="gallery-layout"><div class="card-stack ring-picker" role="region" aria-label="16位角色环形卡牌选择" tabindex="0"><div class="ring-track" aria-hidden="true"></div><div id="orbit-cards"></div></div><div class="gallery-detail"><span class="eyebrow" id="card-index"></span><h3 id="card-name"></h3><p id="card-description"></p><div class="gallery-controls"><button type="button" id="role-prev" aria-label="上一位">←</button><button type="button" id="role-next" aria-label="下一位">→</button><button type="button" id="orbit-toggle">暂停轮转</button><button type="button" id="card-main">查看卡面</button></div><button type="button" id="select-role" class="invite-button"></button><div id="selected-roster" class="selected-roster"></div><details class="thinker-box" open><summary>✦ 邀请一位思想家 <small>额外嘉宾</small></summary><p>借一位思想家的思考方式，看看同一个问题。</p><div id="sage-buttons" class="sage-buttons" role="group" aria-label="选择思想家"></div><label for="merge-sage">当前邀请</label><select id="merge-sage"><option value="">暂不邀请</option></select><p id="sage-state">连接后端后，读取实际开放的嘉宾名单。</p><small>按其思想推演，不是本人对现代事件的原话。</small></details><p class="gallery-note">示例播放使用原来的固定四人阵容；自选阵容用于实时会话。</p></div></div>`;
  document.querySelector('.arena').after(gallery);
  const homeEnter=document.getElementById('enter');
  document.querySelector('.stage-copy').append(homeEnter);
  const enterButton=document.createElement('button');enterButton.type='button';enterButton.id='confirm-lineup';enterButton.className='enter';enterButton.textContent='确认阵容，进入圆桌 ↗';
  const selectionBack=document.createElement('button');selectionBack.type='button';selectionBack.className='selection-back';selectionBack.textContent='← 返回首页';gallery.prepend(selectionBack);
  selectionBack.onclick=()=>{document.body.classList.remove('step-select','step-debate');document.body.classList.add('step-intro');window.scrollTo(0,0)};
  enterButton.onclick=()=>{if(selectedRoles.length!==4)return;document.body.classList.remove('step-intro','step-select');document.body.classList.add('step-debate');document.getElementById('back-intro').hidden=false;window.scrollTo(0,0)};
  const enterBar=document.createElement('div');enterBar.className='selection-enter-bar';
  const enterHint=document.createElement('p');enterHint.id='selection-enter-hint';
  enterBar.append(enterHint,enterButton);gallery.append(enterBar);
  enterButton.setAttribute('aria-describedby','selection-enter-hint');

  const dialog=document.createElement('dialog');dialog.className='personality-dialog';dialog.innerHTML='<button type="button" class="dialog-close" aria-label="关闭">×</button><div id="enlarged-card"></div>';document.body.append(dialog);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function card(p,i){return `<img class="original-personality-card" src="ui/assets/${esc(p.personalityCard.split('/').pop())}" alt="${esc(p.name)} · ${esc(p.mbti)} 完整性格卡" draggable="false">`}

  function render(){const p=people[focus];document.getElementById('card-name').textContent=p.name+' · '+p.mbti;document.getElementById('card-description').textContent=p.tagline;document.getElementById('card-index').textContent=String(focus+1).padStart(2,'0')+' / '+people.length;document.getElementById('lineup-count').textContent='已邀请 '+selectedRoles.length+' / 4';enterButton.disabled=selectedRoles.length!==4;enterHint.textContent=selectedRoles.length===4?'四位嘉宾已就座，准备开始你的圆桌讨论。':'请选择四位嘉宾，还差 '+(4-selectedRoles.length)+' 位。';const b=document.getElementById('select-role');const has=selectedRoles.includes(p.id);b.textContent=has?'✓ 已邀请 · 点击移出':'+ 邀请 '+p.name;b.disabled=!has&&selectedRoles.length>=4;b.setAttribute('aria-pressed',String(has));document.getElementById('selected-roster').replaceChildren(...selectedRoles.map(id=>{const p=people.find(x=>x.id===id);const chip=document.createElement('button');chip.type='button';chip.textContent=p.name+' '+p.mbti+' ×';chip.onclick=()=>{selectedRoles=selectedRoles.filter(x=>x!==id);render();paint()};return chip}));if(!session){team=people.filter(p=>selectedRoles.includes(p.id));if(selectedSageRole())team.push(selectedSageRole());cast()}try{localStorage.setItem('perspectives.merge.lineup',JSON.stringify(selectedRoles))}catch{}}
  const orbitHost=document.getElementById('orbit-cards');
  orbitHost.innerHTML=people.map((p,i)=>`<button type="button" class="ring-card" aria-label="将${esc(p.name)} ${esc(p.mbti)}转到前方" data-index="${i}">${card(p,i)}<span class="ring-selected">✓ 已邀请</span></button>`).join('');
  const orbitCards=[...orbitHost.children],step=2*Math.PI/people.length;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let phase=Math.PI/2,paused=reduced.matches,hovered=false,tween=null,lastTime=null,orbitVisible=true;
  const stage=gallery.querySelector('.ring-picker');
  function paint(){
    const width=stage.clientWidth,cardWidth=Math.min(190,Math.max(110,width*.23)),rx=Math.max(0,(width-cardWidth)/2-8),ry=width<500?85:110;
    let nearest=0,max=-1;
    orbitCards.forEach((el,i)=>{const angle=phase-i*step,depth=(Math.sin(angle)+1)/2,scale=.42+.58*depth;if(depth>max){max=depth;nearest=i}el.style.width=cardWidth+'px';el.style.transform=`translate(-50%,-50%) translate(${Math.cos(angle)*rx}px,${Math.sin(angle)*ry}px) scale(${scale})`;el.style.zIndex=String(2+Math.round(depth*100));el.style.opacity=String(.5+.5*depth);el.classList.toggle('chosen',selectedRoles.includes(people[i].id));el.classList.toggle('front',i===nearest);});
    if(focus!==nearest){focus=nearest;render()}
  }
  function pauseLabel(){document.getElementById('orbit-toggle').textContent=paused?'继续轮转':'暂停轮转';document.getElementById('orbit-toggle').setAttribute('aria-pressed',String(paused))}
  function rotateTo(index){paused=true;pauseLabel();const base=Math.PI/2+index*step;const target=base+Math.round((phase-base)/(2*Math.PI))*2*Math.PI;tween={from:phase,to:target,start:performance.now()};}
  function move(n){rotateTo((focus+n+people.length)%people.length)}
  document.getElementById('role-prev').onclick=()=>move(-1);document.getElementById('role-next').onclick=()=>move(1);
  document.getElementById('orbit-toggle').onclick=()=>{paused=!paused;tween=null;pauseLabel()};
  document.getElementById('card-main').onclick=()=>{paused=true;pauseLabel();document.getElementById('enlarged-card').innerHTML=card(people[focus],focus);dialog.showModal()};dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclick=e=>{if(e.target===dialog)dialog.close()};
  orbitCards.forEach((el,i)=>el.onclick=()=>{rotateTo(i)});
  stage.addEventListener('pointerenter',e=>{if(e.pointerType!=='touch')hovered=true});stage.addEventListener('pointerleave',()=>{hovered=false});
  new ResizeObserver(paint).observe(stage);new IntersectionObserver(entries=>{orbitVisible=entries[0].isIntersecting}).observe(stage);
  function tick(time){const dt=lastTime===null?0:Math.min(time-lastTime,50);lastTime=time;if(tween){const t=Math.min(1,(time-tween.start)/650),ease=1-Math.pow(1-t,3);phase=tween.from+(tween.to-tween.from)*ease;paint();if(t===1)tween=null;}else if(!paused&&!hovered&&orbitVisible&&!document.hidden&&!dialog.open&&!document.body.classList.contains('step-debate')){phase+=dt*2*Math.PI/90000;paint()}requestAnimationFrame(tick)}
  pauseLabel();paint();requestAnimationFrame(tick);
  document.getElementById('select-role').onclick=()=>{paused=true;pauseLabel();const id=people[focus].id;if(selectedRoles.includes(id))selectedRoles=selectedRoles.filter(x=>x!==id);else if(selectedRoles.length<4)selectedRoles.push(id);render();paint()};
  gallery.addEventListener('keydown',e=>{if(e.target.matches('select,input'))return;if(e.key==='ArrowLeft'){e.preventDefault();move(-1)}if(e.key==='ArrowRight'){e.preventDefault();move(1)}});
  let touchX;const stack=gallery.querySelector('.card-stack');stack.addEventListener('touchstart',e=>{touchX=e.touches[0].clientX},{passive:true});stack.addEventListener('touchend',e=>{const delta=e.changedTouches[0].clientX-touchX;if(Math.abs(delta)>45)move(delta<0?1:-1)},{passive:true});
  try{const saved=JSON.parse(localStorage.getItem('perspectives.merge.lineup'));if(Array.isArray(saved)&&saved.length<=4&&saved.every(id=>people.some(p=>p.id===id)))selectedRoles=[...new Set(saved)]}catch{}
  render();
  const sageSelect=document.getElementById('merge-sage');
  const sageChoices=[{id:'',name:'不邀请',note:'保持四人讨论'},{id:'wangyangming',name:'王阳明',note:'知行合一'},{id:'sushi',name:'苏轼',note:'换个角度看处境'},{id:'sartre',name:'萨特',note:'自由与责任'}];
  sageSelect.replaceChildren(...sageChoices.map(p=>new Option(p.name,p.id)));
  function selectSage(id){selectedSage=id;sageSelect.value=id;if(!session){team=people.filter(p=>selectedRoles.includes(p.id));if(selectedSageRole())team.push(selectedSageRole());cast()}document.querySelectorAll('#sage-buttons button').forEach(b=>{const chosen=b.dataset.id===id;b.classList.toggle('selected',chosen);b.setAttribute('aria-pressed',String(chosen))});try{localStorage.setItem('perspectives.merge.sage',id)}catch{}}
  document.getElementById('sage-buttons').replaceChildren(...sageChoices.map(p=>{const b=document.createElement('button');b.type='button';b.dataset.id=p.id;if(p.id){const portrait=document.createElement('img');portrait.src='assets/'+p.id+'.png';portrait.alt=p.name;portrait.className='sage-choice-portrait';b.append(portrait)}const title=document.createElement('strong'),note=document.createElement('small');title.textContent=p.name;note.textContent=p.note;b.append(title,note);b.onclick=()=>selectSage(p.id);return b}));
  sageSelect.onchange=()=>selectSage(sageSelect.value);
  try{const id=localStorage.getItem('perspectives.merge.sage')||'';selectSage(sageChoices.some(p=>p.id===id)?id:'')}catch{selectSage('')}
  document.getElementById('sage-state').textContent='可先选择嘉宾；实时讨论需后端接口支持，离线示例不生成思想家发言。';
  nativeFetch(apiBase+'/api/personas',{signal:AbortSignal.timeout(7000)}).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(catalog=>{if(Array.isArray(catalog.frameworks)){const field=document.getElementById('custom-framework'),value=field.value;field.replaceChildren(new Option('请选择',''),...catalog.frameworks.map(p=>new Option(p.name,p.id)));field.value=value;}const available=new Set((catalog.sages||[]).map(p=>p.id));document.getElementById('sage-state').textContent='后端已连接 · 当前开放：'+(sageChoices.filter(p=>p.id&&available.has(p.id)).map(p=>p.name).join('、')||'暂无')+'。';}).catch(()=>{document.getElementById('sage-state').textContent='当前未连接后端，选择会保留；接口接通后以 options.sage 提交。';});
  const stopButton=document.createElement('button');stopButton.type='button';stopButton.id='stop-discussion';stopButton.textContent='停止讨论';stopButton.title='停止当前页面播放；后端生成取消接口待接入';document.querySelector('.caption-top').append(stopButton);
  stopButton.onclick=()=>{playbackStopped=true;stream?.close();clearTimeout(stageTimer);clearTimeout(bubbleDelayTimer);clearTimeout(captionInterval);clearInterval(motionTimer);clearInterval(reactionTimer);clearInterval(poseTimer);stageTimer=null;stageQueue=[];if(animatedImage?.dataset.rest)animatedImage.src=animatedImage.dataset.rest;animatedImage=null;document.querySelectorAll('.actor').forEach(a=>{a.classList.remove('is-talking','is-listening','speaking','listener-reaction');const bubble=a.querySelector('.actor-bubble');if(bubble)bubble.hidden=true});document.getElementById('stage-speech').hidden=true;document.getElementById('phase').textContent='已停止播放';document.getElementById('notice').textContent=demoPlayback?'示例已停止，可重新播放。':'已停止本页播放并断开事件连接；后端可能仍在生成，取消生成接口待接入。';document.getElementById('interject').hidden=true;document.getElementById('join-wrap').classList.remove('open');setBusy(false)};
  // Keep real interjection on the stage rather than forcing people to the transcript.
  document.getElementById('join-send').onclick=async()=>{const input=document.getElementById('join-input'),hint=document.getElementById('join-hint'),send=document.getElementById('join-send');if(!input.value.trim()){hint.textContent='先写下你想说的话。';return}if(demoPlayback||!session){hint.textContent='示例回放不会生成回复；连接后端发起真实讨论后，即可插话。';return}send.disabled=true;try{const result=await api('/sessions/'+session.id+'/interject',{text:input.value.trim()});if(result.safety){showSafety(result.safety);return}input.value='';hint.textContent=result.queued?'已加入，嘉宾会在后续讨论中回应。':'已提交。';if(result.followup)connect()}catch(error){hint.textContent=error.message}finally{send.disabled=false}};
})();
