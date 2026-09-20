const $ = id => document.getElementById(id);
let state = null, sending = false, lastView = '', connected = false;
let requestMode = 'action', selectedHero = '', viewedHero = '', gameId = null, editId = null, validatedText = null;
let selectedParticipants = new Set();
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body ? 'POST' : 'GET',
    headers: {'Content-Type': 'application/json', 'X-Dnd-Token': document.querySelector('meta[name=dnd-token]').content},
    body: body ? JSON.stringify(body) : undefined});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Не удалось выполнить запрос.');
  return result;
}
function text(parent, tag, value, cls) {
  const node = document.createElement(tag); node.textContent = value;
  if (cls) node.className = cls; parent.append(node); return node;
}
function button(parent, label, fn, cls) {
  const node = text(parent, 'button', label, cls); node.type = 'button'; node.onclick = fn; node.disabled = sending; return node;
}
function error(e) {
  const dialog = document.querySelector('dialog[open]');
  if (dialog) {
    let node = dialog.querySelector('.dialog-error');
    if (!node) {node = text(dialog, 'p', '', 'dialog-error'); node.setAttribute('role','alert');}
    node.textContent = e.message; node.scrollIntoView({block:'nearest'});
  } else { $('error').textContent = e.message; $('error').hidden = false; }
}
function openDialog(id) { if (!$(id).open) $(id).showModal(); }
function describeOperation(op, entities) {
  const name = id => entities[id]?.name || id;
  switch (op.op) {
    case 'create': return 'Появится: ' + op.entity.name + '. ' + op.entity.description;
    case 'move': return `${name(op.entity)}: ${name(op.before)} → ${name(op.destination)}`;
    case 'move_path': return `${name(op.entity)}: ${op.route.map(name).join(' → ')}`;
    case 'transfer': return `${name(op.item)}: ${name(op.before)} → ${name(op.destination)}`;
    case 'consume': return 'Потратить: ' + name(op.item);
    case 'damage': return name(op.entity) + ': −2 здоровья; при 0 герой погибает';
    case 'heal': return name(op.entity) + ': +3 здоровья (до максимума)';
    case 'remember': return (op.visibility === 'gm' ? 'Тайна: ' : 'Запомнить: ') + op.text;
    case 'discover': return 'Открытие: ' + (state.game.facts[op.fact]?.text || op.fact);
    case 'share_fact': return `${name(op.source)} сообщает: ${op.recipients.map(name).join(', ')}`;
    case 'observe': return 'Осмотрено: ' + name(op.subject);
    case 'present': return 'Выведено в сцену: ' + name(op.subject);
    case 'set_state': return `${name(op.entity)}: ${op.before} → ${op.after}`;
    case 'react_state': return `Обязательное последствие: ${name(op.entity)} — ${op.before} → ${op.after}`;
    case 'resolve_promise': return 'Обещание или задача выполнены: ' + (state.game.facts[op.id]?.text || op.id);
    case 'advance_thread': return `Давление «${state.game.threads[op.thread]?.title||op.thread}»: ${op.before} → ${op.after}`;
    case 'resolve_thread': return `Развязка «${state.game.threads[op.thread]?.title||op.thread}»: ${op.outcome}`;
    case 'finish_session': return `Финал (${op.ending}): ${op.summary}`;
  }
}
async function command(kind, data = {}) {
  if (sending) return false;
  const previousText = kind === 'cancel' ? state?.game?.pending?.action?.text : null;
  const previousActor = state?.game?.pending?.action?.actor;
  sending = true; $('error').hidden = true;
  document.querySelectorAll('.dialog-error').forEach(n=>n.remove());
  let ok = false;
  try {
    state = await api('world/command', {kind, data, command_id: crypto.randomUUID(), revision: state?.game?.revision ?? null});
    if (kind === 'submit' || kind === 'direct') { $('action').value = ''; requestMode = 'action'; }
    if (kind === 'import' || kind === 'new') { $('settings-dialog').close(); requestMode = 'action'; selectedParticipants.clear(); $('action').value = ''; }
    if (kind === 'accept' && viewedHero) selectedHero = viewedHero;
    if (kind === 'cancel' && previousActor) selectedHero = viewedHero = previousActor;
    if (previousText) $('action').value = previousText;
    ok = true;
  } catch(e) { error(e); state = await api('world').catch(() => state); }
  finally { sending = false; render(true); }
  return ok;
}
function reading(label, value, caption, advice=false) {
  $('reading').replaceChildren();
  text($('reading'),'p',label,'eyebrow');
  text($('reading'),'div',value,advice ? 'advice-text' : 'read-aloud');
  if (caption) text($('reading'),'p',caption,'caption');
}
function renderCard(g, p) {
  const previousReview = $('review-effects')?.open;
  const previousId = $('pending').dataset.id;
  $('pending').dataset.id = p?.id || '';
  $('pending').hidden = !p; $('pending').replaceChildren();
  if (!p) {
    if (g.session?.status === 'finished') {
      reading('История завершена',g.session.summary || 'Сессия получила окончательный исход.',`Финал: ${g.session.ending}. Для следующего приключения начните новую сессию.`);
      return;
    }
    const last = [...g.events].reverse().find(e=>e.action.mode !== 'hint');
    const narratorHero = g.entities[last?.action.actor || selectedHero];
    const place = g.entities[narratorHero.location];
    reading(last ? `Последняя реплика · ${narratorHero.name} · записано` : 'Начните с этих слов',
      last ? (last.text || place.description) : g.intro,
      last ? 'Выслушайте, что игроки делают дальше.' : 'Прочитайте вслух и спросите игроков: «Что вы делаете?»');
    return;
  }
  const hint = p.action.mode === 'hint';
  if (p.phase === 'ready') {
    if (hint) {
      reading('Совет только для вас · игра не меняется',p.proposal.gm_hint || p.proposal.summary,'Можно воспользоваться советом и продолжить вести сцену.',true);
      if(p.text) {text($('pending'),'p','Можно сказать игрокам:','muted'); text($('pending'),'p',p.text,'suggested-speech');}
    } else {
      const fallback = p.pipeline === 'local' && state.projection
        ? Object.values(g.entities).find(e=>e.kind==='location' && e.name===state.projection.location)?.description : '';
      reading('Прочитайте игрокам · предложенный исход',p.text || fallback || 'Для этого шага отдельная реплика не нужна.',
        'Прочитайте своими словами. Подтвердите результат, когда он подходит вашей сцене.');
      if(p.proposal?.gm_hint) {const note=text($('pending'),'details','','gm-note'); text(note,'summary','Подсказка ведущему'); text(note,'p',p.proposal.gm_hint);}
      if(p.proposal?.pressure?.level && p.proposal.pressure.level!=='ordinary') {
        const pressure=text($('pending'),'div','','pressure '+p.proposal.pressure.level);
        text(pressure,'strong',p.proposal.pressure.level==='verdict'?'Необратимый приговор':p.proposal.pressure.level==='complication'?'Осложнение':'Знак Хранителя');
        text(pressure,'p',p.proposal.pressure.reason||'Ставки сцены растут.');
      }
    }
    if(p.roll) text($('pending'),'p',`Кубик: ${p.roll.value} + ${p.roll.bonus} = ${p.roll.value+p.roll.bonus}; нужно ${p.roll.dc}. ${p.roll.success?'Успех':'Неудача'}.`,'roll-result');
    const ops=p.outcome.operations, names={...g.entities};
    for(const op of ops) if(op.op==='create') names[op.entity.id]=op.entity;
    if(!hint) {
      const review=text($('pending'),'details','','review'); review.id='review-effects';
      review.open=previousId===p.id && !!previousReview;
      text(review,'summary',ops.length ? `Проверить последствия · изменений: ${ops.length}` : 'Проверить последствия · здоровье и вещи не меняются');
      const inner=text(review,'div','','details-inner');
      if(state.projection) {const a=state.projection; text(inner,'p',`После принятия: ${a.name} — ${a.location}; здоровье ${a.hp}; при себе: ${a.items.join(', ')||'ничего'}.`);}
      for(const op of ops) text(inner,'p',describeOperation(op,names));
      if(!ops.length) text(inner,'p','Позиции и факты также не меняются; сохраняется только запись в журнале.');
      text(inner,'p','Итог: '+p.outcome.summary,'muted');
      if(p.proposal?.evidence.length) {const refs=text(inner,'details',''); text(refs,'summary','Основания в памяти'); for(const id of p.proposal.evidence) text(refs,'p',g.facts[id]?.text||id);}
    }
    const controls=text($('pending'),'div','','pending-controls');
    $('pending').prepend(controls);
    button(controls,hint?'Понятно · закрыть совет':'Применить и продолжить',()=>command('accept'),'primary');
    if(!hint)button(controls,'Поправить помощника',()=>{ $('redirect-text').value=''; openDialog('redirect-dialog'); });
    button(controls,'Исправить текст',openEditor);
    button(controls,hint?'Убрать совет':'Отклонить',()=>command('cancel'),'quiet');
    if(p.edited) text($('pending'),'p','Правки ведущего сохранены.','muted');
  } else if(p.phase==='check') {
    const check=p.proposal.check, hero=g.entities[check.actor||p.action.actor];
    const stat={strength:'силы',agility:'ловкости',mind:'смекалки'}[check.stat], dc={easy:10,standard:13,hard:16}[check.difficulty];
    const helpers=(check.helpers||[]).map(id=>g.entities[id]?.name||id), assist=Math.min(2,helpers.length), total=hero.stats[check.stat]+assist;
    reading('Сейчас нужен один бросок',`Попросите игрока за ${hero.name} бросить двадцатигранный кубик. Проверка ${stat}: нужно ${dc}; бонус ${total} (${hero.stats[check.stat]} героя${assist?` + ${assist} за помощь`:''}).`+(helpers.length?` Помогают: ${helpers.join(', ')}.`:''),'Обе ветки уже подготовлены — после кубика ждать помощника не нужно.',true);
    const box=text($('pending'),'div','','roll-box');
    text(box,'p',p.proposal.gm_hint || p.proposal.summary);
    if(p.proposal?.pressure?.level && p.proposal.pressure.level!=='ordinary')text(box,'p',(p.proposal.pressure.level==='verdict'?'Необратимый риск: ':'Давление сцены: ')+(p.proposal.pressure.reason||'ставки растут'),'pressure '+p.proposal.pressure.level);
    const controls=text(box,'div','','buttons');
    const input=document.createElement('input');input.type='number';input.min=1;input.max=20;input.placeholder='1–20';input.setAttribute('aria-label','Результат физического кубика');controls.append(input);
    button(controls,'Ввести бросок',()=>{if(input.value)command('roll',{value:Number(input.value)});},'primary');
    button(controls,'Бросить в приложении',()=>command('roll'));
    const branches=text(box,'details','','review');text(branches,'summary','Что произойдёт при успехе и неудаче');
    text(branches,'p','Успех: '+p.proposal.success.summary);text(branches,'p','Неудача: '+p.proposal.failure.summary);
    button($('pending'),'Отменить заявку',()=>command('cancel'),'quiet');
    button($('pending'),'Поправить помощника',()=>{ $('redirect-text').value=''; openDialog('redirect-dialog'); });
  } else if(p.phase==='question' || p.phase==='error') {
    reading(p.phase==='question'?'Нужно уточнение':'Не получилось подготовить карточку',p.phase==='question'?p.proposal.question:p.error,'Состояние игры не изменилось.',true);
    if(p.phase==='error'&&!$('action').value)$('action').value=p.action.text;
    if(p.proposal?.gm_hint) text($('pending'),'p',p.proposal.gm_hint,'gm-note');
    const controls=text($('pending'),'div','','pending-controls');
    button(controls,'Вернуться к заявке',()=>command('cancel'),'primary');
    button(controls,'Поправить помощника',()=>{ $('redirect-text').value=''; openDialog('redirect-dialog'); });
    if(p.phase==='error')button(controls,'Повторить запрос',()=>command('retry'));
  } else {
    reading('Готовим продолжение',p.action.text,'Можно обсудить намерение с игроком. Результат появится здесь.',true);
    button($('pending'),'Отменить ожидание',()=>command('cancel'),'quiet');
  }
}
function renderHeroes(g, p) {
  const heroes=Object.values(g.entities).filter(e=>e.kind==='hero');
  if(!heroes.some(e=>e.id===viewedHero))viewedHero=selectedHero;
  $('hero-tabs').replaceChildren();
  for(const hero of heroes) {
    const tab=button($('hero-tabs'),(hero.status==='dead'?'† ':'')+hero.name,()=>{
      viewedHero=hero.id;
      if(!state.game.pending && hero.status!=='dead')selectedHero=hero.id;
      render(true);
    });
    tab.id='tab-'+hero.id;tab.setAttribute('role','tab');tab.setAttribute('aria-controls','world');
    tab.setAttribute('aria-selected',String(hero.id===viewedHero));tab.tabIndex=hero.id===viewedHero?0:-1;
    tab.onkeydown=e=>{if(['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();const i=heroes.findIndex(h=>h.id===hero.id);const next=heroes[(i+(e.key==='ArrowRight'?1:heroes.length-1))%heroes.length];$('tab-'+next.id).click();$('tab-'+next.id).focus();}};
  }
  const hero=g.entities[viewedHero], place=g.entities[hero.location];
  $('world').replaceChildren();$('world').setAttribute('aria-labelledby','tab-'+hero.id);
  const heading=text($('world'),'div','','hero-name');text(heading,'strong',hero.name);text(heading,'span',hero.status==='dead'?'погиб':`${hero.hp}/${hero.max_hp} здоровья`,'hp');
  text($('world'),'p',hero.description,'hero-description');text($('world'),'p','Сейчас: '+place.name);
  const items=Object.values(g.entities).filter(e=>e.kind==='item'&&e.location===hero.id).map(e=>e.name);
  text($('world'),'p','При себе: '+(items.join(', ')||'ничего'));
  const npcs=Object.values(g.entities).filter(e=>e.kind==='npc'&&e.location===hero.location).map(e=>e.name);
  if(npcs.length)text($('world'),'p','Рядом: '+npcs.join(', '));
  const surroundings=Object.values(g.entities).filter(e=>['item','feature'].includes(e.kind)&&e.location===hero.location).map(e=>e.name);
  if(surroundings.length)text($('world'),'p','Здесь: '+surroundings.join(', '));
  const available=state.routes?.[hero.id];
  let exits;
  if(available) exits=Object.entries(available).sort((a,b)=>a[1].length-b[1].length).map(([id])=>id);
  else {
    const distances=new Map([[hero.location,0]]), queue=[hero.location];
    for(const current of queue) for(const edge of g.connections.filter(e=>e.includes(current))) {
      const next=edge.find(id=>id!==current);if(!distances.has(next)){distances.set(next,distances.get(current)+1);queue.push(next);}
    }
    exits=[...distances].filter(([id])=>id!==hero.location).sort((a,b)=>a[1]-b[1]).map(([id])=>id);
  }
  if(exits.length && (!p||p.phase==='error')) {
    text($('world'),'h3','Куда может пойти '+hero.name);
    const routes=text($('world'),'div','','routes');
    for(const id of exits) {
      const b=button(routes,g.entities[id].name,()=>{selectedHero=hero.id;selectedParticipants.clear();command('travel',{actor:hero.id,destination:id});},'route');
      b.setAttribute('aria-label',`${hero.name}: перейти — ${g.entities[id].name}`);text(b,'small','→');b.disabled=sending||(!!p&&p.phase!=='error')||state.busy||hero.status==='dead'||g.session?.status==='finished';
    }
    text($('world'),'p','Выберите конечное место: весь известный безопасный маршрут попадёт в одну карточку. Идёт только этот герой.','route-note');
  }
}
function renderFacts(g) {
  $('facts').replaceChildren();$('secret-facts').replaceChildren();$('threads').replaceChildren();
  const pressureLabels=['обычные последствия','появился знак','осложнение','приговор'];
  for(const thread of Object.values(g.threads||{})){
    const row=text($('threads'),'div','','fact');text(row,'span',thread.resolved?'разрешено':pressureLabels[thread.pressure]||'открыто','tag');text(row,'p',thread.title);text(row,'p',thread.outcome||thread.stakes,'muted');
  }
  const labels={fact:'',claim:'Заявление',rumor:'Слух',promise:'Обещание',task:'Задача'};
  let privateCount=0,publicCount=0;
  for(const f of Object.values(g.facts)) {
    const privateFact=f.visibility==='gm';if(privateFact)privateCount++;else publicCount++;
    const row=text($(privateFact?'secret-facts':'facts'),'div','','fact');
    if(labels[f.status]||f.resolved)text(row,'span',labels[f.status]+(f.resolved?' · выполнено':''),'tag');
    text(row,'p',f.text);
  }
  if(!publicCount)text($('facts'),'p','Здесь появятся открытия и обещания игроков.','muted');
  $('secrets').hidden=!privateCount;$('secret-count').textContent=`(${privateCount})`;
}
function renderLore(g) {
  $('lore-content').replaceChildren();
  if(!g)return;
  const d=g.document;
  text($('lore-content'),'h3','Мир, который увидят игроки');text($('lore-content'),'p',d?.lore||g.intro);
  const gm=text($('lore-content'),'div','','gm-prose');
  if(d) {
    text(gm,'h3','Вам как ведущему · замысел и тайна');text(gm,'p',d.gm_notes);
    const rules=text(gm,'details','');text(rules,'summary','Границы истории и простые правила');
    for(const line of d.boundaries)text(rules,'p',line);
    text(rules,'p','Если действие рискованное, бросьте d20 и прибавьте характеристику. Нужно 10 для простого, 13 для обычного, 16 для трудного испытания. Помощник подскажет, когда это необходимо.');
    for(const line of d.rules.manual_rules)text(rules,'p',line);
  }
}
function render(force=false) {
  if(!state)return;
  const signature=JSON.stringify(state);if(!force&&signature===lastView)return;lastView=signature;
  const g=state.game,p=g?.pending,blocked=!!p&&p.phase!=='error';
  if(g?.id!==gameId){gameId=g?.id;selectedHero='';viewedHero='';requestMode='action';selectedParticipants.clear();renderLore(g);}
  $('welcome').hidden=!!g;$('table').hidden=!g;$('open-lore').hidden=!g;
  $('new').disabled=$('start-example').disabled=sending||!!p||state.busy;
  $('undo').disabled=sending||!!p||!g?.events.length;
  $('export-log').disabled=!g;$('import-document').disabled=sending||!!p||state.busy;
  if($('world-model').options.length!==Object.keys(state.models||{}).length) {
    $('world-model').replaceChildren();for(const [id,label] of Object.entries(state.models||{})){const o=text($('world-model'),'option',label);o.value=id;}
  }
  $('world-model').value=state.model;$('world-model').disabled=sending||!!p||state.busy;
  $('model-note').textContent=p?'Закончите текущую карточку перед сменой модели.':'Выбор сохраняется только для игры.';
  $('usage').textContent=`Запросов: ${g?.calls.length||0} · платный API не используется`;
  $('calls').replaceChildren();for(const c of (g?.calls||[]).slice(-15).reverse())text($('calls'),'div',`${c.stage}: ${c.status} · ${c.seconds??'?'} с · ${c.usage?.input_tokens??'?'} / ${c.usage?.output_tokens??'?'} токенов`,'call');
  if(!g)return;
  const allHeroes=Object.values(g.entities).filter(e=>e.kind==='hero');
  const heroes=allHeroes.filter(e=>e.status!=='dead');
  if(!allHeroes.some(h=>h.id===selectedHero)||(!p&&heroes.length&&!heroes.some(h=>h.id===selectedHero)))selectedHero=(heroes[0]||allHeroes[0]).id;
  if(p&&p.phase!=='error')selectedHero=p.action.actor;
  $('session-title').textContent=g.document?.title||'Вода для деревни';
  $('actor').replaceChildren();for(const hero of heroes){const o=text($('actor'),'option',hero.name);o.value=hero.id;}
  $('actor').value=selectedHero;$('actor').disabled=sending||blocked;
  const finished=g.session?.status==='finished';
  $('action-form').hidden=blocked||finished;
  $('manual-note').closest('details').hidden=blocked||finished;
  $('save-note').disabled=sending||blocked||state.busy;
  $('send').disabled=sending||blocked||state.busy||!connected;
  $('ask-hint').disabled=$('direct-model').disabled=$('back-action').disabled=sending||blocked;
  $('ask-hint').hidden=requestMode==='hint';$('direct-model').hidden=requestMode==='direction';$('back-action').hidden=requestMode==='action';
  $('action-title').textContent=requestMode==='hint'?'О чём подсказать вам?':requestMode==='direction'?'Что помощник должен учесть?':'Что делают игроки?';
  $('send').textContent=requestMode==='hint'?'Спросить помощника':requestMode==='direction'?'Сохранить указание':'Разобрать действие';
  $('action-form').classList.toggle('hint-mode',requestMode==='hint');
  const nearby=heroes.filter(h=>h.id!==selectedHero&&h.location===g.entities[selectedHero].location);
  selectedParticipants=new Set([...selectedParticipants].filter(id=>nearby.some(h=>h.id===id)));
  $('participants').replaceChildren();
  for(const hero of nearby){const label=text($('participants'),'label','','participant');const input=document.createElement('input');input.type='checkbox';input.value=hero.id;input.checked=selectedParticipants.has(hero.id);input.disabled=sending||blocked;input.onchange=()=>input.checked?selectedParticipants.add(hero.id):selectedParticipants.delete(hero.id);label.prepend(input);text(label,'span',hero.name);}
  if(!nearby.length)text($('participants'),'span','Рядом нет другого активного героя.','muted');
  $('participants-control').hidden=requestMode!=='action';
  $('mode-note').textContent=requestMode==='hint'?'Совет только для вас. Герои, вещи и здоровье останутся на месте.':requestMode==='direction'?'Это не действие героя. Указание будет обязательным для следующей карточки.':`Заявка за ${g.entities[selectedHero].name}. Для другого героя выберите его имя.`;
  $('action').placeholder=requestMode==='hint'?'Например: игроки растерялись. Какие два подхода им предложить?':requestMode==='direction'?'Например: покажи существующую зацепку, но не раскрывай её содержание.':`Например: ${g.entities[selectedHero].name} внимательно осматривает рабочий стол Ады.`;
  $('director-note').replaceChildren();$('director-note').hidden=!g.director_note;
  if(g.director_note){text($('director-note'),'strong','Указание для следующей карточки');text($('director-note'),'p',g.director_note);button($('director-note'),'Убрать',()=>command('direct',{text:''}),'quiet');}
  $('next').textContent=finished?'История завершена.':blocked?'Сначала завершите карточку выше.':p?.phase==='error'?'Можно исправить заявку ниже или выбрать локальный переход.':!connected?'Подключите помощника в настройках.':'';
  const hero=g.entities[selectedHero],place=g.entities[hero.location];
  $('scene').replaceChildren();text($('scene'),'span',place.name);text($('scene'),'span',hero.name);
  renderCard(g,p);renderHeroes(g,p);renderFacts(g);
  $('event-count').textContent=`(${g.events.length})`;$('history').replaceChildren();
  for(const e of [...g.events].reverse()){
    const actors=(e.action.participants?.length?e.action.participants:[e.action.actor]).map(id=>g.entities[id]?.name||id);
    const row=text($('history'),'div','','entry');text(row,'p',(e.action.mode==='hint'?'Вопрос ведущего':actors.join(' + '))+': '+e.action.text,'actor');
    text(row,'p',e.text||e.summary);if(e.text)text(row,'p',e.summary,'muted');
    if(e.roll)text(row,'p',`Кубик ${e.roll.value} + ${e.roll.bonus}; нужно ${e.roll.dc}.`,'roll');
  }
}
function openEditor() {
  const p=state.game.pending;editId=p.id;
  $('edit-text').value=p.text;$('edit-summary').value=p.outcome.summary;$('edit-effects').replaceChildren();$('edit-error').hidden=true;
  const names={...state.game.entities};for(const op of p.outcome.operations)if(op.op==='create')names[op.entity.id]=op.entity;
  const protectedEffects=new Set(p.protected_operations||[]);
  p.outcome.operations.forEach((op,i)=>{const row=text($('edit-effects'),'label','','effect');const input=document.createElement('input');input.type='checkbox';input.checked=true;input.value=i;input.disabled=protectedEffects.size>0;row.append(input);text(row,'span',describeOperation(op,names)+(protectedEffects.has(i)?' · обязательно':protectedEffects.size?' · связано с обязательным последствием':''));});
  if(!p.outcome.operations.length)text($('edit-effects'),'p','Изменений состояния нет.');
  openDialog('edit-dialog');
}
$('save-edit').onclick=async()=>{
  if(state.game.pending?.id!==editId){$('edit-error').textContent='Карточка уже изменилась. Закройте редактор и откройте её заново.';$('edit-error').hidden=false;return;}
  if(await command('edit',{text:$('edit-text').value,summary:$('edit-summary').value,keep:[...$('edit-effects').querySelectorAll('input:checked')].map(n=>Number(n.value))}))$('edit-dialog').close();
};
async function connection() {
  $('check-login').disabled=true;
  try{const r=await api('codex/status');connected=r.ready;$('connection-status').textContent=r.message;$('welcome-connection').textContent=connected?'Помощник подключён. Можно начинать.':'Для подсказок подключите помощника в настройках. Начало истории доступно и без него.';render(true);}
  catch(e){connected=false;error(e);}finally{$('check-login').disabled=false;}
}
$('open-settings').onclick=()=>openDialog('settings-dialog');
$('open-guide').onclick=()=>openDialog('guide-dialog');
$('open-lore').onclick=()=>{renderLore(state.game);openDialog('lore-dialog');};
$('start-custom').onclick=()=>{openDialog('settings-dialog');$('documents').open=true;};
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
$('check-login').onclick=connection;
$('login').onclick=async()=>{$('login').disabled=true;try{const r=await api('codex/login',{});$('connection-status').textContent=r.message;}catch(e){error(e);}finally{$('login').disabled=false;}};
function start(){if(!state?.game||confirm('Начать историю с самого начала? Текущая партия будет заменена.'))command('new');}
$('start-example').onclick=$('new').onclick=start;
$('undo').onclick=()=>command('undo');
$('actor').onchange=()=>{selectedHero=$('actor').value;viewedHero=selectedHero;selectedParticipants.clear();render(true);};
$('ask-hint').onclick=()=>{requestMode='hint';render(true);$('action').focus();};
$('direct-model').onclick=()=>{requestMode='direction';render(true);$('action').focus();};
$('back-action').onclick=()=>{requestMode='action';render(true);$('action').focus();};
$('action-form').onsubmit=e=>{e.preventDefault();if(requestMode==='direction')command('direct',{text:$('action').value});else command('submit',{actor:selectedHero,participants:[selectedHero,...selectedParticipants],mode:requestMode,text:$('action').value});};
$('save-redirect').onclick=async()=>{if(await command('redirect',{text:$('redirect-text').value}))$('redirect-dialog').close();};
$('world-model').onchange=async()=>{if(sending)return;sending=true;try{state=await api('world/model',{model:$('world-model').value});}catch(e){error(e);}finally{sending=false;render(true);}};
function download(name,content,type='application/json'){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('document-text').oninput=()=>{validatedText=null;$('import-document').hidden=true;};
$('document-file').onchange=async()=>{const file=$('document-file').files[0];if(!file)return;if(file.size>500000){error(Error('Документ должен быть меньше 500 КБ.'));return;}$('document-text').value=await file.text();$('document-text').oninput();};
$('validate-document').onclick=async()=>{const value=$('document-text').value;try{const r=await api('world/validate',{text:value});validatedText=r.ok&&value===$('document-text').value?value:null;$('import-document').hidden=!validatedText;$('document-report').textContent=r.ok?`${r.preview.title}\nГерои: ${r.preview.heroes.join(', ')}\nЗаписей журнала: ${r.preview.events}; фактов: ${r.preview.facts}\nГотово к загрузке.`:r.repair_prompt;}catch(e){error(e);}};
$('import-document').onclick=()=>{if(validatedText!==$('document-text').value||!validatedText)return;if(state?.game&&!confirm('Заменить текущую партию проверенным документом?'))return;command('import',{text:validatedText});};
$('author-kit').onclick=async()=>{try{const k=await api('world/author-kit');download('dnd-gm-author-kit.txt',k.prompt+'\n\nСХЕМА МИРА\n'+JSON.stringify(k.schema,null,2)+'\n\nПРИМЕР\n'+JSON.stringify(k.example,null,2)+'\n\nСХЕМА ЖУРНАЛА\n'+JSON.stringify(k.journal_schema,null,2),'text/plain');}catch(e){error(e);}};
$('example-document').onclick=async()=>{try{const k=await api('world/author-kit');$('document-text').value=JSON.stringify(k.example,null,2);$('document-text').oninput();}catch(e){error(e);}};
$('export-log').onclick=async()=>{try{download('dnd-gm-journal.json',JSON.stringify(await api('world/export'),null,2));}catch(e){error(e);}};
$('save-note').onclick=async()=>{if(await command('note',{actor:selectedHero,text:$('manual-note').value,visibility:$('note-visibility').value}))$('manual-note').value='';};
async function refresh(){try{state=await api('world');render();}catch(e){error(e);}finally{setTimeout(refresh,900);}}
refresh();connection();
