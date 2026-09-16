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
    if (previousText) $('action').value = previousText;
  } catch(e) { error(e); state = await api('world').catch(() => state); }
  finally { sending = false; render(true); }
}
function render(force = false) {
  const signature = JSON.stringify(state);
  if (!force && signature === lastView) return;
  lastView = signature;
  const g = state?.game, p = g?.pending;
  $('table').hidden = !g; $('new').textContent = g ? 'Начать заново' : 'Начать короткую игру';
  $('new').disabled = sending || !!p || state?.busy;
  $('undo').hidden = !g; $('undo').disabled = sending || !!p || !g?.events.length;
  if (!g) return;
  $('send').disabled = sending || !!p || state.busy || !connected;
  $('next').textContent = p ? 'Сначала завершите или отмените текущий ход.' : !connected ? 'Сначала подключите Codex.' : '';
  $('usage').textContent = `Codex: ${g.calls.length} вызовов · API: не используется`;
  $('history').replaceChildren();
  text($('history'), 'div', g.intro, 'entry');
  for (const e of g.events) {
    const row = text($('history'), 'div', '', 'entry');
    text(row, 'div', 'Мира: ' + e.action.text, 'actor'); text(row, 'p', e.text);
    if(e.roll) text(row, 'p', `d20: ${e.roll.value} + ${e.roll.bonus} против ${e.roll.dc} · ${e.roll.success ? 'успех' : 'неудача'}`, 'roll');
  }
  $('pending').hidden = !p; $('pending').replaceChildren();
  if(p) {
    const labels = {planning:'Готовлю предложение…', rendering:'Готовлю реплику…', check:'Нужна проверка', ready:'Исход готов к принятию', error:'Нужно ваше решение', question:'Нужно уточнение'};
    text($('pending'),'h2',labels[p.phase]);
    text($('pending'),'p','Мира: ' + p.action.text);
    if(p.proposal) text($('pending'),'p',p.proposal.summary);
    if(p.phase === 'question') text($('pending'),'p', p.proposal.question + ' Отмените заявку и отправьте уточнённое действие.');
    if(p.error) text($('pending'),'p',p.error);
    if(p.phase === 'ready') {
      text($('pending'),'p',p.text);
      text($('pending'),'p','Последствия: ' + p.outcome.summary, 'muted');
      const details = text($('pending'),'details',''); text(details,'summary','Проверить изменения мира');
      const draft = p.outcome.operations;
      const names = {...g.entities};
      for (const op of draft) if (op.op === 'create') names[op.entity.id] = op.entity;
      for (const op of draft) text(details,'p',describeOperation(op, names));
      if (!draft.length) text(details,'p','Предметы, здоровье и память мира не меняются.');
    }
    if(p.roll) text($('pending'),'p', `Бросок ${p.roll.value} + ${p.roll.bonus} против ${p.roll.dc}: ${p.roll.success ? 'успех' : 'неудача'}`);
    const buttons = text($('pending'),'div','','buttons');
    if(p.phase === 'check') {
      const c=p.proposal.check, dc={easy:10,standard:13,hard:16}[c.difficulty];
      text($('pending'),'p',`d20 + ${g.entities.mira.stats[c.stat]} против ${dc}`);
      text($('pending'),'p','Успех: ' + p.proposal.success.summary);
      text($('pending'),'p','Неудача: ' + p.proposal.failure.summary);
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
  const hero = g.entities.mira;
  text($('world'),'p',`Мира · ${hero.hp}/${hero.max_hp} HP`);
  text($('world'),'p',g.entities[hero.location].name);
  text($('world'),'p','При себе: ' + Object.values(g.entities).filter(e=>e.kind==='item'&&e.location==='mira').map(e=>e.name).join(', '));
  const exits = g.connections.filter(edge=>edge.includes(hero.location)).map(edge=>edge.find(id=>id!==hero.location));
  text($('world'),'p','Пути: ' + exits.map(id=>g.entities[id].name).join(', '));
  text($('world'),'p','Рядом: ' + Object.values(g.entities).filter(e=>e.kind==='npc'&&e.location===hero.location).map(e=>e.name).join(', '));
  $('facts').replaceChildren();
  const names={fact:'Факт',claim:'Заявление',rumor:'Слух',promise:'Обещание'};
  for(const f of Object.values(g.facts)) {
    const row=text($('facts'),'div','','fact');
    text(row,'span',names[f.status]+(f.visibility==='gm'?' · только ведущему':'')+(f.resolved?' · выполнено':''),'tag');
    text(row,'p',f.text);
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
$('new').onclick=()=> { if(!state?.game || confirm('Начать заново? Текущая короткая игра будет заменена, демопартия сохранится.')) command('new'); };
$('undo').onclick=()=>command('undo');
$('action-form').onsubmit=e=>{e.preventDefault();command('submit',{actor:'mira',text:$('action').value});};
async function refresh(){try{state=await api('world');render();}catch(e){error(e);}finally{setTimeout(refresh,900);}}
refresh(); connection();
