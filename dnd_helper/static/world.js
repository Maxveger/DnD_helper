const $ = id => document.getElementById(id);
let state = null, sending = false, lastView = '', connected = false;
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body ? 'POST' : 'GET',
    headers: {'Content-Type': 'application/json', 'X-Dnd-Token': document.querySelector('meta[name=dnd-token]').content},
    body: body ? JSON.stringify(body) : undefined});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Не удалось выполнить запрос.');
  return result;
}
function error(e) { $('error').textContent = e.message; $('error').hidden = false; }
function text(parent, tag, value, cls) {
  const node = document.createElement(tag); node.textContent = value;
  if (cls) node.className = cls; parent.append(node); return node;
}
function button(parent, label, fn, cls) {
  const node = text(parent, 'button', label, cls); node.type = 'button';
  node.onclick = fn; node.disabled = sending; return node;
}
function describeOperation(op, entities) {
  const name = id => entities[id]?.name || id;
  switch (op.op) {
    case 'create': return 'Добавить: ' + op.entity.name + '. ' + op.entity.description;
    case 'move': return `${name(op.entity)}: ${name(op.before)} → ${name(op.destination)}`;
    case 'transfer': return `${name(op.item)}: ${name(op.before)} → ${name(op.destination)}`;
    case 'consume': return 'Потратить: ' + name(op.item);
    case 'damage': return name(op.entity) + ': −2 HP (не ниже 1)';
    case 'heal': return name(op.entity) + ': +3 HP (до максимума)';
    case 'remember': return (op.visibility === 'gm' ? 'Только ведущему: ' : 'Запомнить: ') + op.text;
    case 'resolve_promise': return 'Отметить обещание выполненным: ' + (state.game.facts[op.id]?.text || op.id);
  }
}
async function command(kind, data = {}) {
  if (sending) return;
  const previousText = kind === 'cancel' ? state?.game?.pending?.action?.text : null;
  sending = true; $('error').hidden = true;
  try {
    state = await api('world/command', {kind, data, command_id: crypto.randomUUID(), revision: state?.game?.revision ?? null});
    if (kind === 'submit') $('action').value = '';
    if (kind === 'import') $('documents').open = false;
    if (previousText) $('action').value = previousText;
  } catch(e) { error(e); state = await api('world').catch(() => state); }
  finally { sending = false; render(true); }
}
function render(force = false) {
  const signature = JSON.stringify(state);
  if (!force && signature === lastView) return;
  lastView = signature;
  const g = state?.game, p = g?.pending;
  $('table').hidden = !g; $('new').textContent = g ? 'Открыть пример заново' : 'Открыть пример';
  $('new').disabled = sending || !!p || state?.busy;
  $('undo').hidden = !g; $('undo').disabled = sending || !!p || !g?.events.length;
  if ($('world-model').options.length !== Object.keys(state.models || {}).length) {
    $('world-model').replaceChildren();
    for (const [id, label] of Object.entries(state.models || {})) {
      const option = document.createElement('option'); option.value = id; option.textContent = label;
      $('world-model').append(option);
    }
  }
  $('world-model').value = state.model;
  $('world-model').disabled = sending || !!p || state.busy;
  const activeModel = p?.pipeline === 'local' ? 'без модели' : p?.model || [...(g?.calls || [])].reverse().find(c => c.action === p?.action?.id)?.model;
  $('model-note').textContent = p ? `Текущий ход: ${activeModel || 'ранее выбранная модель'}. Выбор доступен после завершения хода.` : 'Выбор сохраняется только в приложении. Настройки Codex для разработки не меняются.';
  $('export-log').disabled = !g;
  $('import-document').disabled = sending || !!p || state.busy;
  if (!g) return;
  const heroes = Object.values(g.entities).filter(e => e.kind === 'hero');
  const actor = p?.action.actor || $('actor').value || heroes[0].id;
  $('actor').replaceChildren();
  for(const hero of heroes) { const option=text($('actor'),'option',hero.name); option.value=hero.id; }
  $('actor').value = heroes.some(h=>h.id===actor) ? actor : heroes[0].id;
  $('actor').disabled = sending || !!p;
  $('save-note').disabled = sending || !!p || state.busy;
  $('request-mode').disabled = sending || !!p;
  const actorName = id => g.entities[id]?.name || id;
  $('send').disabled = sending || !!p || state.busy || !connected;
  $('next').textContent = p ? 'Сначала завершите или отмените текущий ход.' : !connected ? 'Сначала подключите Codex.' : '';
  $('usage').textContent = `Codex: ${g.calls.length} вызовов · API: не используется`;
  $('scene').replaceChildren();
  const place=g.entities[g.entities[$('actor').value].location];
  text($('scene'),'strong',place.name); text($('scene'),'p',place.description);
  $('event-count').textContent = `(${g.events.length})`;
  $('history').replaceChildren();
  text($('history'), 'div', g.intro, 'entry');
  for (const e of g.events) {
    const row = text($('history'), 'div', '', 'entry');
    text(row, 'div', (e.action.mode === 'hint' ? 'Вопрос ведущего' : actorName(e.action.actor)) + ': ' + e.action.text, 'actor'); text(row, 'p', e.text || e.summary);
    if(e.text && e.summary !== e.text) text(row,'p',e.summary,'muted');
    if(e.roll) text(row, 'p', `d20: ${e.roll.value} + ${e.roll.bonus} против ${e.roll.dc} · ${e.roll.success ? 'успех' : 'неудача'}`, 'roll');
  }
  $('pending').hidden = !p; $('pending').replaceChildren();
  if(p) {
    const labels = {planning:'Готовлю подсказку ведущему · один запрос…', rendering:'Готовлю реплику…', check:'Нужна проверка', ready:'Карточка ведущего готова', error:'Нужно ваше решение', question:'Нужно уточнение'};
    text($('pending'),'h2',labels[p.phase]);
    text($('pending'),'p',actorName(p.action.actor) + ': ' + p.action.text);
    if(p.proposal) {
      text($('pending'),'p',p.proposal.summary);
      if(p.proposal.gm_hint) { text($('pending'),'h3','Ведущему'); text($('pending'),'p',p.proposal.gm_hint,'gm-hint'); }
      if(p.proposal.evidence?.length) {
        const refs=text($('pending'),'details',''); text(refs,'summary','На какие факты опирается совет');
        for(const id of p.proposal.evidence) text(refs,'p',g.facts[id]?.text || id);
      }
    }
    if(p.phase === 'question') text($('pending'),'p', p.proposal.question + ' Отмените заявку и отправьте уточнённое действие.');
    if(p.error) text($('pending'),'p',p.error);
    if(p.phase === 'ready') {
      text($('pending'),'h3','Черновик реплики — проверьте перед чтением');
      text($('pending'),'p',p.text || 'Реплика не нужна: используйте совет ведущему.');
      text($('pending'),'p','Последствия: ' + p.outcome.summary, 'muted');
      if(state.projection) {
        const a=state.projection;
        text($('pending'),'p',`После принятия по расчёту программы: ${a.name} · ${a.location} · ${a.hp} HP · вещи: ${a.items.join(', ') || '—'}`,'mechanics');
      }
      const changes=text($('pending'),'div','','changes');
      const opNames={...g.entities};
      for(const op of p.outcome.operations) if(op.op==='create') opNames[op.entity.id]=op.entity;
      for(const op of p.outcome.operations) text(changes,'p',describeOperation(op,opNames));
      const details = text($('pending'),'details',''); text(details,'summary','Проверить и исправить карточку без запроса');
      const draft = p.outcome.operations;
      const names = {...g.entities};
      for (const op of draft) if (op.op === 'create') names[op.entity.id] = op.entity;
      const keep = [];
      for (const [i,op] of draft.entries()) {
        const row=text(details,'label','','effect');
        const input=document.createElement('input'); input.type='checkbox'; input.checked=true;
        row.append(input); text(row,'span',describeOperation(op,names)); keep.push([i,input]);
      }
      if (!draft.length) text(details,'p','HP, предметы и факты не меняются.');
      const label=text(details,'label','Текст для чтения'); const editor=document.createElement('textarea');
      editor.value=p.text; editor.maxLength=1800; editor.setAttribute('aria-label','Исправить реплику'); label.append(editor);
      const summaryLabel=text(details,'label','Итог в журнале'); const summary=document.createElement('textarea');
      summary.value=p.outcome.summary; summary.maxLength=800; summary.setAttribute('aria-label','Исправить итог'); summaryLabel.append(summary);
      text(details,'p','Можно убрать лишние изменения. Добавлять новые механические эффекты через текст нельзя.','muted');
      button(details,'Сохранить правки',()=>command('edit',{text:editor.value,summary:summary.value,keep:keep.filter(([,input])=>input.checked).map(([i])=>i)}));
      if(p.edited) text($('pending'),'p','Правки ведущего сохранены.','muted');
    }
    if(p.roll) text($('pending'),'p', `Бросок ${p.roll.value} + ${p.roll.bonus} против ${p.roll.dc}: ${p.roll.success ? 'успех' : 'неудача'}`);
    const buttons = text($('pending'),'div','','buttons');
    if(p.phase === 'check') {
      const c=p.proposal.check, dc={easy:10,standard:13,hard:16}[c.difficulty];
      text($('pending'),'p',`d20 + ${g.entities[p.action.actor].stats[c.stat]} против ${dc}`);
      text($('pending'),'p','Успех: ' + p.proposal.success.summary);
      if(p.proposal.success.read_aloud) text($('pending'),'p','Черновик при успехе: ' + p.proposal.success.read_aloud,'muted');
      text($('pending'),'p','Неудача: ' + p.proposal.failure.summary);
      if(p.proposal.failure.read_aloud) text($('pending'),'p','Черновик при неудаче: ' + p.proposal.failure.read_aloud,'muted');
      button(buttons,'Бросить d20',()=>command('roll'),'primary');
      const input=document.createElement('input'); input.type='number'; input.min=1; input.max=20;
      input.setAttribute('aria-label','Результат физического кубика'); buttons.append(input);
      button(buttons,'Ввести бросок',()=> { if(input.value) command('roll',{value:Number(input.value)}); });
    }
    if(p.phase === 'ready') button(buttons,'Принять исход',()=>command('accept'),'primary');
    if(p.phase === 'error') button(buttons,'Повторить запрос',()=>command('retry'));
    button(buttons,p.phase === 'ready' ? 'Отклонить' : 'Отменить заявку',()=>command('cancel'));
  }
  $('world').replaceChildren();
  for(const hero of heroes) {
    text($('world'),'p',`${hero.name} · ${hero.hp}/${hero.max_hp} HP`,'actor');
    text($('world'),'p',g.entities[hero.location].name);
    text($('world'),'p','При себе: ' + (Object.values(g.entities).filter(e=>e.kind==='item'&&e.location===hero.id).map(e=>e.name).join(', ') || '—'));
    const exits = g.connections.filter(edge=>edge.includes(hero.location)).map(edge=>edge.find(id=>id!==hero.location));
    text($('world'),'p','Пути: ' + (exits.map(id=>g.entities[id].name).join(', ') || 'не описаны'));
    if(exits.length) {
      const routes=text($('world'),'div','','buttons');
      for(const id of exits) {const b=button(routes,'Перейти: '+g.entities[id].name,()=>command('travel',{actor:hero.id,destination:id})); b.disabled=sending||!!p||state.busy;}
    }
    text($('world'),'p','Рядом: ' + (Object.values(g.entities).filter(e=>e.kind==='npc'&&e.location===hero.location).map(e=>e.name).join(', ') || 'никого'));
  }
  if(g.document) {
    const doc=text($('world'),'details',''); text(doc,'summary',g.document.title+' · памятка ведущему');
    text(doc,'p',g.document.gm_notes);
    for(const boundary of g.document.boundaries) text(doc,'p',boundary);
    text(doc,'p','d20 + характеристика против 10 / 13 / 16. Урон 2 (минимум 1 HP), лечение 3 с перевязью.');
    for(const rule of g.document.rules.manual_rules) text(doc,'p','Ручное правило: '+rule);
  }
  $('facts').replaceChildren();
  const names={fact:'Факт',claim:'Заявление',rumor:'Слух',promise:'Обещание'};
  for(const f of Object.values(g.facts)) {
    const row=text($('facts'),'div','','fact');
    text(row,'span',names[f.status]+(f.visibility==='gm'?' · только ведущему':'')+(f.resolved?' · выполнено':''),'tag');
    text(row,'p',f.text);
    text(row,'small','Знают: '+(f.known_by.map(id=>g.entities[id]?.name || id).join(', ') || 'никто из персонажей'));
  }
  $('calls').replaceChildren();
  for(const c of g.calls.slice(-15).reverse()) text($('calls'),'div',`${c.stage}: ${c.status} · ${c.seconds ?? '?'} с · tokens: ${c.usage?.input_tokens ?? '?'} / ${c.usage?.output_tokens ?? '?'}`,'call');
}
async function connection() {
  $('check-login').disabled = true;
  try { const result=await api('codex/status'); connected=result.ready; $('connection-status').textContent=result.message; render(true); }
  catch(e){error(e);} finally{$('check-login').disabled=false;}
}
$('check-login').onclick=connection;
$('login').onclick=async()=>{ $('login').disabled=true; try { const r=await api('codex/login',{}); $('connection-status').textContent=r.message; } catch(e){error(e);} finally{$('login').disabled=false;} };
$('new').onclick=()=> { if(!state?.game || confirm('Начать заново? Текущая сессия будет заменена. Сначала скачайте журнал, если хотите продолжить её позже.')) command('new'); };
$('undo').onclick=()=>command('undo');
$('action-form').onsubmit=e=>{e.preventDefault();command('submit',{actor:$('actor').value,mode:$('request-mode').value,text:$('action').value});};
async function refresh(){try{state=await api('world');render();}catch(e){error(e);}finally{setTimeout(refresh,900);}}
refresh(); connection();

$('world-model').onchange = async () => {
  if(sending) return;
  sending = true; $('error').hidden = true;
  try { state = await api('world/model', {model: $('world-model').value}); }
  catch(e) { error(e); }
  finally { sending = false; render(true); }
};

function download(name, content, type='application/json') {
  const url=URL.createObjectURL(new Blob([content],{type}));
  const a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
let validatedText = null;
$('document-text').oninput=()=>{ validatedText=null; $('import-document').hidden=true; };
$('document-file').onchange=async()=>{
  const file=$('document-file').files[0]; if(!file) return;
  if(file.size>500000) {error(Error('Документ должен быть меньше 500 КБ.')); return;}
  $('document-text').value=await file.text(); $('document-text').oninput();
};
$('validate-document').onclick=async()=>{
  const value=$('document-text').value;
  try {
    const r=await api('world/validate',{text:value});
    validatedText=r.ok && value===$('document-text').value ? value : null;
    $('import-document').hidden=!validatedText;
    $('document-report').textContent=r.ok ? `${r.preview.title}
Герои: ${r.preview.heroes.join(', ')}
Записей журнала: ${r.preview.events}; фактов: ${r.preview.facts}
Правила: ${r.preview.rules}. Готово к загрузке.` : r.repair_prompt;
  } catch(e){error(e);}
};
$('import-document').onclick=()=>{
  if(validatedText!==$('document-text').value || !validatedText) return;
  if(state?.game && !confirm('Заменить сессию проверенным документом? Скачайте текущий журнал для продолжения позже.')) return;
  command('import',{text:validatedText});
};
$('author-kit').onclick=async()=>{
  try {const kit=await api('world/author-kit'); download('dnd-gm-author-kit.txt',kit.prompt+'\n\nСХЕМА МИРА\n'+JSON.stringify(kit.schema,null,2)+'\n\nПРИМЕР\n'+JSON.stringify(kit.example,null,2)+'\n\nСХЕМА ЖУРНАЛА\n'+JSON.stringify(kit.journal_schema,null,2),'text/plain');} catch(e){error(e);}
};
$('example-document').onclick=async()=>{
  try {const kit=await api('world/author-kit'); $('document-text').value=JSON.stringify(kit.example,null,2); $('document-text').oninput();} catch(e){error(e);}
};
$('export-log').onclick=async()=>{
  try {download('dnd-gm-journal.json',JSON.stringify(await api('world/export'),null,2));} catch(e){error(e);}
};
$('save-note').onclick=async()=>{
  await command('note',{actor:$('actor').value,text:$('manual-note').value,visibility:$('note-visibility').value});
  if($('error').hidden) $('manual-note').value='';
};

$('actor').onchange=()=>render(true);
