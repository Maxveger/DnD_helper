const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name=dnd-token]').content;
let view=null,sending=false,connected=false,lastSignature='',lastGameSignature='';
const labels={hero:'Иво',inventory:'При себе',known:'Известно',commitments:'Договорённости'};
const situationLabels={npcs:'Сейчас в сцене',threats:'Непосредственные угрозы'};
const handoffLabels={decision:'новый выбор',direct_question:'нужен ответ',imminent_threat:'немедленная угроза',scene_end:'сцена завершена'};

function node(tag,content='',className=''){const el=document.createElement(tag);if(content!==undefined)el.textContent=content;if(className)el.className=className;return el;}
function button(parent,label,handler,className=''){const el=node('button',label,className);el.type='button';el.onclick=handler;parent.append(el);return el;}
function showError(error){$('error').textContent=error.message||String(error);$('error').hidden=false;window.scrollTo({top:0,behavior:'smooth'});}
function clearError(){$('error').hidden=true;}
function commandId(){return crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`;}
async function api(path,body){const options={headers:{'x-dnd-token':token}};if(body!==undefined){options.method='POST';options.headers['content-type']='application/json';options.body=JSON.stringify(body);}const response=await fetch('/api/'+path,options);let data={};try{data=await response.json();}catch{}if(!response.ok)throw Error(data.error||'Запрос не выполнен.');return data;}
async function command(kind,data={}){if(sending)return false;sending=true;clearError();try{view=await api('studio/command',{kind,data,command_id:commandId(),revision:view?.studio?.revision??null});render(true);return true;}catch(error){showError(error);return false;}finally{sending=false;render(true);}}

function renderTranscript(state){const root=$('transcript');root.replaceChildren();for(const entry of state.transcript){const turn=node('article','',`turn ${entry.role}`);turn.append(node('div',entry.role==='gm'?'Ведущий':'Иво','role'));turn.append(node('div',entry.text,'body'));root.append(turn);} }

function renderCapsule(capsule){const root=$('capsule');root.replaceChildren();const situation=capsule.situation;const scene=node('section','','memory-section');scene.append(node('h3','Ситуация сейчас'));scene.append(node('p',situation.scene));root.append(scene);for(const [key,label] of Object.entries(situationLabels)){const section=node('section','','memory-section');section.append(node('h3',label));const values=situation[key]||[];if(values.length){const ul=node('ul');for(const value of values)ul.append(node('li',value));section.append(ul);}else section.append(node('p','—','muted'));root.append(section);}if(situation.active_intent){const intent=node('section','','memory-section');intent.append(node('h3','Продолжающееся намерение'));intent.append(node('p',situation.active_intent));root.append(intent);}const handoff=node('section','','memory-section');handoff.append(node('h3','Почему ход у игрока'));handoff.append(node('p',`${handoffLabels[situation.handoff.kind]||situation.handoff.kind}: ${situation.handoff.reason}`));root.append(handoff);for(const [key,label] of Object.entries(labels)){const section=node('section','','memory-section');section.append(node('h3',label));const values=capsule[key]||[];if(values.length){const ul=node('ul');for(const value of values)ul.append(node('li',value));section.append(ul);}else section.append(node('p','—','muted'));root.append(section);} }

function capsuleDiff(before,after){const root=node('div','','memory-diff');let changed=false;const oldSituation=before.situation,nextSituation=after.situation;if(JSON.stringify(oldSituation)!==JSON.stringify(nextSituation)){changed=true;if(oldSituation.scene!==nextSituation.scene)root.append(node('p','Ситуация: '+nextSituation.scene,'added'));for(const [key,label] of Object.entries(situationLabels)){const old=new Set(oldSituation[key]||[]),next=new Set(nextSituation[key]||[]);for(const value of next)if(!old.has(value))root.append(node('p',`+ ${label}: ${value}`,'added'));for(const value of old)if(!next.has(value))root.append(node('p',`− ${label}: ${value}`,'removed'));}if(oldSituation.active_intent!==nextSituation.active_intent)root.append(node('p','Намерение: '+(nextSituation.active_intent||'завершено'),'added'));root.append(node('p',`Передача хода: ${handoffLabels[nextSituation.handoff.kind]||nextSituation.handoff.kind} — ${nextSituation.handoff.reason}`,'added'));}for(const key of Object.keys(labels)){const old=new Set(before[key]||[]),next=new Set(after[key]||[]);for(const value of next)if(!old.has(value)){changed=true;root.append(node('p',`+ ${labels[key]}: ${value}`,'added'));}for(const value of old)if(!next.has(value)){changed=true;root.append(node('p',`− ${labels[key]}: ${value}`,'removed'));}}if(!changed)root.append(node('p','Модель не предлагает менять принятую память.','muted'));return root;}

function detailsBlock(card,outcome){const details=node('details','','review');details.append(node('summary','Проверить основания и новые детали'));const inner=node('div');inner.append(node('h3','Как понято действие'));inner.append(node('p',card.interpretation));inner.append(node('h3','Канон, на который опирается модель'));if(card.canon_used.length){const ul=node('ul');for(const item of card.canon_used)ul.append(node('li',item));inner.append(ul);}else inner.append(node('p','Модель не указала опору.','muted'));inner.append(node('h3','Существенные новые детали'));if(outcome.introduced_details.length){const ul=node('ul');for(const item of outcome.introduced_details)ul.append(node('li',item));inner.append(ul);}else inner.append(node('p','Новых существенных деталей не заявлено.','muted'));details.append(inner);return details;}

function renderPending(state){const root=$('pending'),pending=state.pending;root.replaceChildren();root.hidden=!pending;if(!pending)return;if(pending.phase==='planning'){root.append(node('div','Живая модель продолжает сцену…','loader'));button(root,'Отменить',()=>command('cancel',{target:'game'}),'quiet');return;}if(pending.phase==='error'){root.append(node('h2','Карточка не получена'));root.append(node('p',pending.error));const controls=node('div','','buttons');button(controls,'Повторить',()=>command('retry',{target:'game'}),'primary');button(controls,'Отклонить',()=>command('reject'),'quiet');root.append(controls);return;}
  const revision=pending.revision,editing=revision?.phase==='planning';
  if(revision){const notice=node('div','','revision-notice');if(editing){notice.append(node('div','Игровая модель пересобирает эту карточку…','loader'));button(notice,'Отменить редактуру',()=>command('revision_cancel'),'quiet');}else{notice.append(node('strong','Редактура не удалась — исходный черновик сохранён.'));notice.append(node('p',revision.error||'Карточка не изменена.','muted'));const actions=node('div','','buttons');button(actions,'Повторить редактуру',()=>command('revision_retry'),'primary');button(actions,'Оставить исходную',()=>command('revision_cancel'),'quiet');notice.append(actions);}root.append(notice);}
  const card=pending.card;root.append(node('p','Черновик живой модели','eyebrow'));root.append(node('h2',pending.phase==='check'?'Сначала определите ставки':'Ведущий решает, что принять'));
  const gm=node('div','','gm-note');gm.append(node('strong','Только ведущему'));gm.append(node('p',card.gm_note));root.append(gm);
  if(pending.phase==='check'){
    const check=card.check,box=node('div','','check-box');box.append(node('h3',`Проверка: ${labelsForStat(check.stat)} · ${labelsForDifficulty(check.difficulty)}`));box.append(node('p',check.reason));const stakes=node('div','','stakes');const success=node('div','','stake');success.append(node('strong','При успехе'));success.append(node('p',check.success_stake));const failure=node('div','','stake');failure.append(node('strong','При неудаче'));failure.append(node('p',check.failure_stake));stakes.append(success,failure);box.append(stakes);const controls=node('div','','buttons');const roll=button(controls,'Бросить d20',()=>command('roll'),'primary');roll.disabled=editing;const input=node('input');input.type='number';input.min=1;input.max=20;input.placeholder='1–20';input.disabled=editing;input.setAttribute('aria-label','Результат своего d20');controls.append(input);const ownRoll=button(controls,'Использовать бросок',()=>{const value=Number(input.value);if(value>=1&&value<=20)command('roll',{value});else showError(Error('Введите результат от 1 до 20.'));});ownRoll.disabled=editing;box.append(controls);root.append(box);const generic={introduced_details:[]};root.append(detailsBlock(card,generic));return;
  }
  const outcome=pending.selected_outcome;if(pending.roll){const roll=pending.roll;root.append(node('p',`Кубик ${roll.value} + ${roll.bonus} = ${roll.value+roll.bonus}; нужно ${roll.dc}. ${roll.success?'Успех.':'Неудача.'}`,'roll-result'));}
  const draft=node('div','','draft');const textarea=node('textarea');textarea.id='draft-text';textarea.value=outcome.read_aloud;textarea.maxLength=3000;textarea.disabled=editing;textarea.setAttribute('aria-label','Редактируемая реплика игрокам');draft.append(textarea);root.append(draft);root.append(node('p',outcome.summary,'muted'));root.append(capsuleDiff(state.capsule,outcome.capsule_after));root.append(detailsBlock(card,outcome));const controls=node('div','','buttons');const accept=button(controls,'Принять текст и память',()=>command('accept',{text:$('draft-text').value,apply_capsule:true,capsule:outcome.capsule_after}),'primary');const textOnly=button(controls,'Принять только текст',()=>command('accept',{text:$('draft-text').value,apply_capsule:false}));const reject=button(controls,'Отклонить',()=>command('reject'),'quiet');accept.disabled=textOnly.disabled=reject.disabled=editing;root.append(controls);
}
function labelsForStat(value){return {strength:'сила',agility:'ловкость',mind:'ум'}[value]||value;}
function labelsForDifficulty(value){return {easy:'10 · простая',standard:'13 · обычная',hard:'16 · трудная'}[value]||value;}

function renderPrivate(state){const root=$('private-history');root.replaceChildren();for(const item of state.side_chat.slice(-8)){const entry=node('div','','private-entry');entry.append(node('span',item.mode==='meta'?'Мета:':'Совет:','tag'));entry.append(node('p',item.input));entry.append(node('p',item.answer));if(item.observations?.length){const ul=node('ul');for(const value of item.observations)ul.append(node('li',value));entry.append(ul);}root.append(entry);}const pending=state.assistant_pending,box=$('private-pending');box.replaceChildren();box.hidden=!pending;if(pending){if(pending.phase==='planning'){box.append(node('div','Модель думает…','loader'));button(box,'Отменить',()=>command('cancel',{target:'chat'}),'quiet');}else{box.append(node('p',pending.error));button(box,'Повторить',()=>command('retry',{target:'chat'}),'primary');button(box,'Убрать',()=>command('cancel',{target:'chat'}),'quiet');}}}

function renderDirector(state){
  const root=$('pulse-content'),badge=$('pulse-status'),director=state.director||{phase:'idle'};
  root.replaceChildren();badge.className='pulse-status';
  const controls=node('div','','buttons pulse-controls');
  if(director.phase==='idle'){
    badge.textContent='тихо';
    root.append(node('p',state.pending?.card?'Можно оценить открытую карточку до броска или принятия.':'Редактор появится рядом с текущим черновиком, а не будет советовать следующий ход.','muted'));
    const ask=button(controls,'Оценить черновик',()=>command('director_request'));
    ask.disabled=sending||view.busy||!state.pending?.card||!!state.pending?.revision||!!state.assistant_pending||!connected;
    root.append(controls);return;
  }
  if(director.phase==='planning'){
    badge.textContent='наблюдает';badge.classList.add('working');
    root.append(node('div','Проверяет текущий ответ мира и обе ветки проверки…','loader'));
    root.append(node('p','Карточка уже доступна: можно не ждать и оставить её как есть.','muted'));
    button(controls,'Отменить',()=>command('director_cancel'),'quiet');root.append(controls);return;
  }
  if(director.phase==='applying'){
    badge.textContent='редактура';badge.classList.add('working');
    root.append(node('strong',director.choice?.label||'Поправка ведущего'));
    root.append(node('p','Игровая модель пересобирает открытую карточку. Канон пока не изменён.','muted'));
    button(controls,'Отменить редактуру',()=>command('revision_cancel'),'quiet');root.append(controls);return;
  }
  if(director.phase==='error'){
    badge.textContent='ошибка';badge.classList.add('warning');
    root.append(node('p',director.error||'Оценка не получена. Игра не изменена.'));
    if(state.pending?.revision?.phase==='error'){root.append(node('p','Повтор или отказ от редакции доступны над исходной карточкой.','muted'));return;}
    button(controls,'Повторить',()=>command('director_request'));
    button(controls,'Закрыть',()=>command('director_dismiss'),'quiet');root.append(controls);return;
  }
  if(director.phase==='revised'){
    badge.textContent='исправлено';badge.classList.add('decision');
    root.append(node('strong',director.choice?.label||'Редактура ведущего'));
    root.append(node('p','Открытая карточка заменена. Сравните результат и только затем бросайте или принимайте.','muted'));
    button(controls,'Скрыть отметку',()=>command('director_dismiss'),'quiet');root.append(controls);return;
  }
  const pulse=director.pulse||{};
  const statusLabel={steady:'ровно',watch:'наблюдать',decision:'решение'}[pulse.status]||'оценка';
  badge.textContent=statusLabel;badge.classList.add(pulse.status||'');
  root.append(node('p',pulse.observation||'','pulse-observation'));
  if(pulse.evidence?.length){const evidence=node('ul','','pulse-evidence');for(const item of pulse.evidence)evidence.append(node('li',item));root.append(evidence);}
  if(pulse.risk)root.append(node('p','Риск: '+pulse.risk,'pulse-risk'));
  root.append(node('p',`Срочность: ${{low:'низкая',medium:'средняя',high:'высокая'}[pulse.urgency]||pulse.urgency} · уверенность: ${{low:'низкая',medium:'средняя',high:'высокая'}[pulse.confidence]||pulse.confidence}`,'muted'));
  for(const move of pulse.moves||[]){
    const card=node('article','','pulse-move');card.append(node('strong',move.label));card.append(node('p',move.purpose));card.append(node('p','Ограничение: '+move.tradeoff,'muted'));
    if(move.changes_canon)card.append(node('span','может изменить канон','canon-flag'));
    const use=button(card,'Пересобрать карточку',()=>command('director_choose',{kind:move.kind}),'primary');use.disabled=sending||view.busy||!state.pending?.card;root.append(card);
  }
  button(controls,pulse.status==='decision'?'Оставить строго':'Закрыть оценку',()=>command('director_dismiss'),'quiet');
  if(pulse.status!=='steady')button(controls,'Своя редактура',()=>{$('director-note').focus();$('director-note').scrollIntoView({block:'center'});});
  root.append(controls);
}

function renderCalls(state){const root=$('calls');root.replaceChildren();for(const call of state.calls.slice(-12).reverse()){const usage=call.turn_usage||call.usage||{};const timing=call.first_event_seconds===undefined?'':` · событие ${call.first_event_seconds??'?'} с${call.first_output_seconds!=null?` · текст ${call.first_output_seconds} с`:''}`;root.append(node('div',`${call.mode} · ${call.status||'отклонён'}${timing} · полностью ${call.seconds??'?'} с · ${usage.input_tokens??'?'} / ${usage.output_tokens??'?'} токенов хода`,'call'));}}

function renderDossier(d){const root=$('dossier-content');root.replaceChildren();const intro=node('section');intro.append(node('p',d.scope));root.append(intro);for(const [title,values] of [['Неизменные истины',d.immutable_truths],['Свобода модели',d.freedom],['Сторожевые напоминания',d.watchpoints]]){const section=node('section');section.append(node('h2',title));const ul=node('ul');for(const value of values)ul.append(node('li',value));section.append(ul);root.append(section);}const cast=node('section');cast.append(node('h2','Персонажи'));for(const person of d.cast){cast.append(node('h3',person.name));cast.append(node('p',person.public));cast.append(node('p','Цель: '+person.goal));cast.append(node('p','Публичная позиция: '+person.public_position,'muted'));}root.append(cast);const situations=node('section');situations.append(node('h2','Ситуации'));for(const item of d.situations){situations.append(node('h3',item.title));situations.append(node('p',item.physical));}root.append(situations);}

function fillCapsuleDialog(capsule){const situation=capsule.situation;$('cap-scene').value=situation.scene;$('cap-npcs').value=(situation.npcs||[]).join('\n');$('cap-threats').value=(situation.threats||[]).join('\n');$('cap-active-intent').value=situation.active_intent||'';$('cap-handoff-kind').value=situation.handoff.kind;$('cap-handoff-reason').value=situation.handoff.reason;const root=$('capsule-fields');root.replaceChildren();for(const [key,label] of Object.entries(labels)){const lab=node('label',label);lab.htmlFor='cap-'+key;const textarea=node('textarea');textarea.id='cap-'+key;textarea.rows=3;textarea.value=(capsule[key]||[]).join('\n');root.append(lab,textarea);}}
function capsuleFromDialog(){const lines=id=>$(id).value.split('\n').map(x=>x.trim()).filter(Boolean);const value={situation:{scene:$('cap-scene').value.trim(),npcs:lines('cap-npcs'),threats:lines('cap-threats'),active_intent:$('cap-active-intent').value.trim()||null,handoff:{kind:$('cap-handoff-kind').value,reason:$('cap-handoff-reason').value.trim()}}};for(const key of Object.keys(labels))value[key]=lines('cap-'+key);return value;}

function render(force=false){if(!view)return;const signature=JSON.stringify(view);if(!force&&signature===lastSignature)return;lastSignature=signature;const state=view.studio;document.body.classList.toggle('session-open',!!state);$('welcome').hidden=!!state;$('workspace').hidden=!state;if($('model').options.length!==Object.keys(view.models||{}).length){$('model').replaceChildren();for(const [id,label] of Object.entries(view.models||{})){const option=node('option',label);option.value=id;$('model').append(option);}}$('model').value=view.model;if(!state){renderDossier(view.dossier);return;}const gameSignature=JSON.stringify([state.transcript,state.pending]);$('title').textContent=view.dossier.title;renderTranscript(state);renderCapsule(state.capsule);renderPending(state);renderDirector(state);renderPrivate(state);renderCalls(state);renderDossier(view.dossier);const blocked=!!state.pending||view.busy;$('send-action').disabled=sending||blocked||!connected;$('action').disabled=sending||!!state.pending;$('action-status').textContent=!connected?'Подключите Codex в настройках.':view.busy?(state.compaction?.phase==='planning'?'Сжимаем историю между сценами; действие уже можно набрать.':'Ждём ответ модели.'):state.pending?'Сначала завершите карточку.':'';$('undo').disabled=sending||!!state.pending||view.busy||!state.events.length;$('edit-capsule').disabled=!!state.pending||view.busy;$('send-private').disabled=sending||!!state.assistant_pending||view.busy||!connected;$('private-input').disabled=sending||!!state.assistant_pending||!connected;$('save-direction').disabled=sending||view.busy||!state.pending?.card||!!state.pending?.revision;$('remind-dossier').disabled=sending||view.busy;$('reset-model').disabled=sending||view.busy||!!state.pending||!!state.assistant_pending;let memory=state.model_session_id?`Одна живая беседа · ${state.model_turns||0} ответов модели.`:'Беседа модели начнётся со следующей реплики.';if(state.compaction?.phase==='planning')memory+=' История сжимается в фоне между сценами.';else if(state.compaction?.phase==='completed')memory+=` История сжата после ${state.compaction.after_turn} ответа.`;else if(state.compaction?.phase==='error')memory+=' Сжатие не удалось; игра сохранена и может продолжаться.';$('model-memory-status').textContent=memory+(state.remind_dossier?' Досье будет приложено к следующей реплике.':'');if(gameSignature!==lastGameSignature&&matchMedia('(min-width: 761px)').matches){requestAnimationFrame(()=>{if(state.pending&&state.pending.phase!=='planning')$('pending').scrollIntoView({block:'start'});else $('game-stream').scrollTop=$('game-stream').scrollHeight;});}lastGameSignature=gameSignature;}

async function connection(){$('check-login').disabled=true;try{const status=await api('codex/status');connected=status.ready;$('connection-status').textContent=status.message;$('welcome-connection').textContent=status.ready?'Живая модель подключена.':'Для игры подключите Codex в настройках.';}catch(error){connected=false;showError(error);}finally{$('check-login').disabled=false;render(true);}}

$('start').onclick=()=>command('new');$('restart').onclick=()=>{if(confirm('Начать лабораторный срез заново? Текущая история останется только в локальной резервной записи.'))command('new');};$('undo').onclick=()=>command('undo');
$('action-form').onsubmit=async event=>{event.preventDefault();const value=$('action').value;if(await command('submit',{mode:'action',text:value}))$('action').value='';};
$('private-form').onsubmit=async event=>{event.preventDefault();const value=$('private-input').value;if(await command('submit',{mode:$('private-mode').value,text:value}))$('private-input').value='';};
$('save-direction').onclick=async()=>{if(await command('direct',{text:$('director-note').value}))$('director-note').value='';};
$('remind-dossier').onclick=()=>command('remind');
$('reset-model').onclick=()=>{if(confirm('Начать для модели новую беседу? Игра и принятая память останутся; досье будет загружено заново.'))command('reset_model');};
$('edit-capsule').onclick=()=>{fillCapsuleDialog(view.studio.capsule);$('capsule-dialog').showModal();};
$('save-capsule').onclick=async()=>{if(await command('capsule',{capsule:capsuleFromDialog()}))$('capsule-dialog').close();};
$('open-dossier').onclick=()=>$('dossier-dialog').showModal();$('open-settings').onclick=()=>$('settings-dialog').showModal();for(const close of document.querySelectorAll('[data-close]'))close.onclick=()=>$(close.dataset.close).close();
$('check-login').onclick=connection;$('login').onclick=async()=>{try{const result=await api('codex/login',{});$('connection-status').textContent=result.message;}catch(error){showError(error);}};$('model').onchange=async()=>{if(sending)return;sending=true;try{view=await api('studio/model',{model:$('model').value});}catch(error){showError(error);}finally{sending=false;render(true);}};
async function refresh(){try{view=await api('studio');render();}catch(error){showError(error);}finally{setTimeout(refresh,900);}}refresh();connection();
