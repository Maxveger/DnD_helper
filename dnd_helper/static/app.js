"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="dnd-token"]').content;
let state = null,
  busy = false,
  tab = "table",
  formSession = null,
  lastTurn = null,
  toastTimer,
  stopped = false;
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const show = (id, visible) => {
  $(id).hidden = !visible;
};
function toast(text, error = false) {
  $("toast").textContent = text;
  $("toast").className = error ? "error" : "";
  show("toast", true);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => show("toast", false), 6000);
}
async function api(path, data) {
  const response = await fetch("/api/" + path, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      "X-Dnd-Token": token,
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      body.error || "Не удалось выполнить запрос. Обновите страницу.",
    );
  return body;
}
async function run(fn) {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  try {
    await fn();
  } catch (e) {
    toast(e.message || "Нет связи с приложением", true);
  } finally {
    busy = false;
    document.body.classList.remove("busy");
    if (state) render();
  }
}
async function command(kind, data = {}) {
  state = await api("command", {
    kind,
    data,
    command_id: crypto.randomUUID(),
    revision: state?.game?.revision ?? null,
  });
  render();
}
function closeMenu() {
  document.body.classList.remove("nav-open");
  $("mobile-menu").setAttribute("aria-expanded", "false");
}
function switchTab(next) {
  tab = next;
  closeMenu();
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  render();
}
let authorKit = null,
  importReport = null,
  validatedText = null;
function openNew(key) {
  $("adventure-select").innerHTML = state.adventures
    .map(
      (a) =>
        `<option value="${esc(a.key)}">${esc(a.title)} · ${esc(a.version)}</option>`,
    )
    .join("");
  if (typeof key === "string") $("adventure-select").value = key;
  renderNewCharacters();
  $("new-dialog").showModal();
}
function renderNewCharacters() {
  const adventure = state.adventures.find(
    (a) => a.key === $("adventure-select").value,
  );
  if (!adventure) return;
  $("new-adventure-summary").textContent =
    `${adventure.summary} · ${adventure.min_players}–${adventure.max_players} игроков`;
  $("character-options").innerHTML = Object.values(adventure.characters)
    .map(
      (c, i) =>
        `<label class="character-choice"><span class="avatar">${esc(c.initial)}</span><span><strong>${esc(c.name)}</strong><small>${esc(c.role)}</small></span><input type="checkbox" name="character" value="${esc(c.id)}" ${i < adventure.min_players ? "checked" : ""}></label>`,
    )
    .join("");
}
$("adventure-select").onchange = renderNewCharacters;
function download(text, filename, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function getAuthorKit() {
  authorKit ??= await api("author-kit");
  return authorKit;
}
async function copyText(text, filename) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано. Вставьте в чат GPT.");
  } catch {
    download(text, filename);
    toast("Текст сохранён в файл. Откройте его и скопируйте в чат.");
  }
}
$("copy-author-prompt").onclick = () =>
  run(async () =>
    copyText((await getAuthorKit()).prompt, "adventure-prompt.md"),
  );
$("download-author-kit").onclick = () =>
  run(async () =>
    download((await getAuthorKit()).prompt, "adventure-prompt.md"),
  );
function invalidateDocument() {
  validatedText = null;
  importReport = null;
  $("import-adventure").disabled = true;
  $("import-report").textContent = "";
  show("copy-repair", false);
}
$("adventure-document").oninput = invalidateDocument;
$("load-example").onclick = () =>
  run(async () => {
    $("adventure-document").value = JSON.stringify(
      (await getAuthorKit()).example,
      null,
      2,
    );
    invalidateDocument();
  });
$("adventure-file").onchange = () =>
  run(async () => {
    const file = $("adventure-file").files[0];
    if (!file) return;
    if (file.size > 500000)
      throw new Error("Документ должен быть не больше 500 КБ.");
    $("adventure-document").value = await file.text();
    invalidateDocument();
  });
function showImportReport(report) {
  importReport = report;
  $("import-report").innerHTML =
    `<h3>${report.ok ? "Документ готов к импорту" : "Нужно исправить документ"}</h3>` +
    (report.preview
      ? `<p><strong>${esc(report.preview.title)}</strong> · ${esc(report.preview.rules)} · ${report.preview.scenes} локаций · ${report.preview.actions} действий</p><p>${esc(report.preview.summary)}</p>`
      : "") +
    [...report.errors, ...report.warnings]
      .map((e) => `<p><strong>${esc(e.path)}</strong>: ${esc(e.message)}</p>`)
      .join("");
  show("copy-repair", !report.ok);
  $("import-adventure").disabled = !report.ok;
}
$("validate-adventure").onclick = () =>
  run(async () => {
    const text = $("adventure-document").value;
    const report = await api("adventures/validate", { text });
    if (text !== $("adventure-document").value) return;
    validatedText = report.ok ? text : null;
    showImportReport(report);
  });
$("copy-repair").onclick = () =>
  run(() => copyText(importReport.repair_prompt, "adventure-fixes.txt"));
$("import-adventure").onclick = () =>
  run(async () => {
    const text = $("adventure-document").value;
    if (text !== validatedText)
      throw new Error("Документ изменился. Проверьте его снова.");
    const report = await api("adventures/import", { text });
    showImportReport(report);
    if (report.ok) {
      state = await api("state");
      $("import-adventure").disabled = true;
      toast("Приключение добавлено. Выберите «Играть» в библиотеке.");
    }
  });
function render() {
  if (!state) return;
  const g = state.game,
    cat = state.catalog;
  show("library-view", tab === "library");
  $("adventure-title").textContent = cat.title;
  $("adventure-library").innerHTML = state.adventures
    .map(
      (a) =>
        `<article class="side-card"><h3>${esc(a.title)} · ${esc(a.version)}</h3><p>${esc(a.summary)}</p><button class="button primary" data-play-adventure="${esc(a.key)}">Играть</button></article>`,
    )
    .join("");
  show("builtin-rules", !cat.rules_summary);
  show("custom-rules", !!cat.rules_summary);
  if (cat.rules_summary)
    $("custom-rules").innerHTML =
      `<h2>Правила приключения</h2><p>${esc(cat.rules_summary)}</p>${(cat.manual_rules || []).map((r) => `<p><strong>Решает ведущий:</strong> ${esc(r)}</p>`).join("")}<h3>Мир</h3><p>${esc(cat.lore)}</p><h3>Только для ведущего</h3><p>${esc(cat.gm_notes)}</p>${Object.values(
        cat.npcs || {},
      )
        .map(
          (n) =>
            `<h3>${esc(n.name)}</h3><p>${esc(n.public)}</p><p><strong>Секрет:</strong> ${esc(n.secret)}</p>`,
        )
        .join("")}`;
  show("table-view", tab === "table");
  show("journal-view", tab === "journal");
  show("rules-view", tab === "rules");
  show("welcome", !g);
  show("game-layout", !!g);
  show("undo", !!g && g.status !== "lobby");
  show("pause", !!g && ["active", "paused"].includes(g.status));
  $("mode-label").textContent =
    state.settings.ai_enabled && state.settings.has_openai_key
      ? "GPT включён"
      : "Деморежим · без API";
  $("journal-count").textContent = g?.journal.length || 0;
  $("page-title").textContent =
    tab === "library"
      ? "Мои сценарии"
      : tab === "journal"
        ? "У каждой истории есть память"
        : tab === "rules"
          ? "Всё нужное — под рукой"
          : g
            ? g.status === "finished"
              ? "История, которую вы создали"
              : "Ваша история продолжается"
            : "Поведём приключение?";
  $("page-subtitle").textContent =
    g?.status === "finished"
      ? "Самое время обсудить, как прошла игра."
      : "Вы рассказываете историю. Мы помогаем с остальным.";
  $("journal-list").innerHTML = g?.journal.length
    ? [...g.journal]
        .reverse()
        .map(
          (e) =>
            `<article class="journal-entry"><small>${new Date(e.time * 1000).toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" })} · ${e.kind === "system" ? "СИСТЕМА" : "ИГРОВОЙ ШАГ"}</small><h3>${esc(e.title)}</h3><p>${esc(e.text)}</p></article>`,
        )
        .join("")
    : '<p class="empty-line">Здесь появятся принятые события вашей игры.</p>';
  if (!g) return;
  const presentation = state.presentation;
  if (formSession !== g.id) {
    formSession = g.id;
    const options = Object.values(g.characters)
      .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`)
      .join("");
    $("actor").innerHTML = options;
    $("target").innerHTML =
      '<option value="">Цель: сам / не нужна</option>' + options;
    $("edit-intent").innerHTML = Object.entries(cat.intents)
      .map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`)
      .join("");
    $("manual-hp").innerHTML = Object.values(g.characters)
      .map(
        (c) =>
          `<label>${esc(c.name)}: новые HP<input type="number" min="0" max="${c.max_hp}" step="1" data-hp="${esc(c.id)}" placeholder="Без изменений"></label>`,
      )
      .join("");
  }
  $("pause").textContent =
    g.status === "paused" ? "▷ Продолжить игру" : "Ⅱ Пауза";
  if (g.world.turn !== lastTurn) {
    lastTurn = g.world.turn;
    if (lastTurn) $("actor").value = lastTurn;
  }
  if (g.status === "paused") {
    $("connection-banner").textContent =
      "Игра на паузе. Входящие действия сохраняются в очереди.";
    show("connection-banner", true);
  } else {
    show("connection-banner", false);
  }
  const scene = cat.scenes[g.scene],
    p = g.pending,
    last = g.journal.filter((e) => e.kind === "event").at(-1);
  $("scene-number").textContent = scene.eyebrow;
  $("step-title").textContent = p
    ? p.title
    : g.status === "finished"
      ? presentation.ending_title
      : last?.title || scene.name;
  $("narration").textContent = p ? p.text : last?.text || scene.text;
  $("next-prompt").textContent = p
    ? p.prompt
    : presentation.can_finish
      ? "Чем эта история закончилась для вашего персонажа?"
      : scene.prompt;
  $("gm-secret").textContent = scene.secret;
  $("gm-rule-note").textContent =
    p?.note ||
    "Всё необходимое уже подготовлено. Можно прочитать текст или пересказать своими словами.";
  $("read-label").textContent =
    p?.phase === "clarify"
      ? "Решение для ведущего"
      : p?.phase === "waiting"
        ? "Ждём игрока"
        : "Прочитайте вслух";
  $("step-tag").textContent = p
    ? {
        check: "ПРОВЕРКА",
        waiting: "ОЖИДАНИЕ БРОСКА",
        ready: "ГОТОВЫЙ ИСХОД",
        clarify: "НУЖЕН ВАШ ВЫБОР",
      }[p.phase]
    : g.status === "finished"
      ? "ПРИКЛЮЧЕНИЕ ЗАВЕРШЕНО"
      : "ТЕКУЩАЯ СЦЕНА";
  show("action-author", !!p);
  if (p)
    $("action-author").textContent =
      `${g.characters[p.action.actor].name}: ${p.action.text}`;
  show("changes", !!p?.changes?.length);
  $("changes").innerHTML = (p?.changes || [])
    .map((s) => `<span>✓ ${esc(s)}</span>`)
    .join("");
  show("roll-panel", !!p?.check);
  if (p?.check) {
    const check = p.check,
      r = p.roll;
    $("roll-panel").innerHTML = r
      ? `<span class="roll-value">${r.total}</span><div class="roll-details"><strong>${r.success ? "Проверка успешна" : "Проверка не пройдена"}</strong><small>d${cat.dice} ${r.die} ${check.bonus >= 0 ? "+" : ""}${check.bonus} = ${r.total} · сложность ${check.dc} · ${r.source === "physical" ? "введён вручную" : "бросок приложения"}</small></div>`
      : `<span class="roll-value">d${cat.dice}</span><div class="roll-details"><strong>${esc(check.label)} ${check.bonus >= 0 ? "+" : ""}${check.bonus}</strong><small>Сложность ${check.dc} · ${esc(g.characters[p.action.actor].name)}</small></div>${p.phase === "waiting" ? '<button class="button quiet small" id="open-roll">Ввести бросок</button>' : ""}`;
  }
  const primary = $("primary-action");
  primary.disabled = false;
  primary.dataset.command = "";
  if (g.status === "lobby") {
    primary.textContent = "Начать приключение →";
    primary.dataset.command = "start";
  } else if (g.status === "paused") {
    primary.textContent = "Возобновить игру →";
    primary.dataset.command = "pause";
  } else if (g.status === "finished") {
    primary.textContent = "Приключение завершено ✓";
    primary.disabled = true;
  } else if (p?.phase === "check") {
    primary.textContent = `Попросить бросок: ${g.characters[p.action.actor].name} →`;
    primary.dataset.command = "request_roll";
  } else if (p?.phase === "ready") {
    primary.textContent = "Продолжить →";
    primary.dataset.command = "accept";
  } else if (p?.phase === "waiting") {
    primary.textContent = "Ожидаем бросок игрока";
    primary.disabled = true;
  } else if (p) {
    primary.textContent = "Отменить это действие →";
    primary.dataset.command = "discard";
  } else if (presentation.can_finish) {
    primary.textContent = "Завершить приключение →";
    primary.dataset.command = "finish";
  } else {
    primary.textContent = "Ждём действие игроков";
    primary.disabled = true;
  }
  show("discard", !!p && p.phase !== "clarify");
  show("clear-actions", g.queue.length > 0 && (!p || p.phase === "clarify"));
  $("clear-actions").textContent =
    `Отменить все заявки (${g.queue.length + (p ? 1 : 0)})`;
  $("gm-guidance").textContent =
    p?.phase === "clarify"
      ? "Это действие сейчас выполнить нельзя. Выберите доступную замену ниже или отмените заявку, чтобы сменить локацию. Если накопились лишние действия, нажмите «Отменить все заявки»."
      : presentation.guidance ||
        "Выберите действие игрока. Если нужного варианта нет, уточните намерение или откройте «Решение ведущего».";
  show(
    "rewrite",
    p?.phase === "ready" &&
      state.settings.ai_enabled &&
      state.settings.has_openai_key,
  );
  const availability =
    presentation.availability?.[$("actor").value]?.[$("target").value] || {};
  const choosing =
    g.status === "active" &&
    !presentation.can_finish &&
    (!p || p.phase === "clarify");
  const quick = choosing
    ? presentation.quick_actions.filter((k) => availability[k] === "")
    : [];
  const exits = choosing && !p && !g.world.combat ? presentation.exits : [];
  $("quick-help").textContent =
    g.status === "lobby"
      ? "Начните приключение кнопкой выше."
      : g.status === "paused"
        ? "Возобновите игру, чтобы выбрать действие."
        : g.status === "finished"
          ? "Приключение завершено. История сохранена в журнале."
          : presentation.can_finish && !p
            ? "Цель достигнута. Завершите приключение кнопкой выше."
            : p?.phase === "clarify"
              ? "Кнопка заменит текущее ошибочное действие. Остальные заявки останутся в очереди."
              : p
                ? "Сначала завершите текущее действие кнопкой выше."
                : quick.length || exits.length
                  ? "Выберите действие или место, куда направится группа."
                  : "Для выбранного героя и цели нет готовых действий. Выберите другого героя или предложите своё действие.";
  $("quick-actions").innerHTML = [
    ...quick.map(
      (k) =>
        `<button type="button" data-intent="${esc(k)}">${esc(cat.intents[k])}</button>`,
    ),
    ...exits.map(
      (id) =>
        `<button type="button" data-scene="${esc(id)}">Перейти: ${esc(cat.scenes[id].name)} ↗</button>`,
    ),
  ].join("");
  $("input-source").textContent = g.demo
    ? "Локальный ввод / демо"
    : "Telegram и ввод ведущего";
  $("party-count").textContent =
    `${Object.keys(g.characters).length} ${Object.keys(g.characters).length === 1 ? "герой" : "героя"}`;
  const expandedCards = new Set(
    [...document.querySelectorAll("#party details[open]")].map(
      (el) => el.dataset.character,
    ),
  );
  $("party").innerHTML = Object.values(g.characters)
    .map((c) => {
      const connected = Object.values(g.participants).some(
        (p) => p.actor === c.id,
      );
      return `<article class="character-card"><div class="character-heading"><span class="avatar ${esc(c.id)}">${esc(c.initial)}</span><div><h3>${esc(c.name)}</h3><p>${esc(c.role)} · ${g.demo ? "демо" : connected ? "в игре" : "ожидаем"}</p></div>${connected || g.demo ? '<span class="connected-dot" title="В игре"></span>' : ""}</div><div class="hp-line"><span>${c.hp === 0 ? "Выбыл из боя" : "Здоровье"}</span><strong>${c.hp} / ${c.max_hp} HP</strong></div><progress value="${c.hp}" max="${c.max_hp}"></progress><div class="stats-row">${Object.entries(
        c.stats,
      )
        .map(
          ([k, v]) =>
            `<span>${esc(cat.attributes[k])}<strong>${v >= 0 ? "+" : ""}${v}</strong></span>`,
        )
        .join(
          "",
        )}</div><details data-character="${esc(c.id)}" ${expandedCards.has(c.id) ? "open" : ""}><summary>Снаряжение и описание</summary><p>${esc(c.items.join(" · "))}</p><p>${esc(c.description)}</p></details></article>`;
    })
    .join("");
  $("objectives").innerHTML = presentation.objectives
    .map(
      ({ text, done }) =>
        `<div class="objective-row ${done ? "done" : ""}"><span class="check">${done ? "✓" : ""}</span>${esc(text)}</div>`,
    )
    .join("");
  $("clues").innerHTML = g.clues.length
    ? g.clues
        .map((k) => `<div class="clue">${esc(cat.clues[k])}</div>`)
        .join("")
    : '<div class="empty-line">Пока здесь чистый лист.<br>Любопытство приведёт к первой улике.</div>';
  show("combat-card", g.world.combat);
  $("combat-card").innerHTML =
    `<h3>Страж · ${g.world.guard_hp}/${g.world.guard_max_hp} HP</h3><p>Раунд ${g.world.round} · ход: ${esc(g.characters[g.world.turn]?.name || "—")}</p><p>Можно договориться с Адой.</p>`;
  $("queue-count").textContent = g.queue.length;
  $("queue-list").innerHTML = g.queue.length
    ? g.queue
        .map(
          (a) =>
            `<div class="queue-item"><span class="avatar ${esc(a.actor)}">${esc(g.characters[a.actor].initial)}</span><p><strong>${esc(g.characters[a.actor].name)} · ${a.source === "telegram" ? "Telegram" : "ввод ведущего"}</strong>${esc(a.text)}</p></div>`,
        )
        .join("")
    : '<div class="empty-line"><span>◌</span>Все действия разобраны. Пусть история идёт своим чередом.</div>';
  $("bot-status").textContent = state.telegram.status;
  $("ai-mode").textContent =
    state.settings.ai_enabled && state.settings.has_openai_key
      ? "GPT + правила"
      : "Готовые подсказки";
  $("budget").textContent =
    `$${state.spending.total.toFixed(3)} / $${state.settings.budget.toFixed(2)}`;
  $("budget-progress").max = state.settings.budget;
  $("budget-progress").value = state.spending.total;
  $("ai-status").textContent = state.ai_status;
}
document.addEventListener("click", (e) => {
  const play = e.target.closest("[data-play-adventure]");
  if (play) openNew(play.dataset.playAdventure);
  const destination = e.target.closest("[data-scene]");
  if (destination && !destination.disabled)
    run(() => command("scene", { scene: destination.dataset.scene }));
  const close = e.target.closest("[data-close]");
  if (close) $(close.dataset.close).close();
  const nav = e.target.closest("[data-tab]");
  if (nav) switchTab(nav.dataset.tab);
  const quick = e.target.closest("[data-intent]");
  if (quick && !quick.disabled)
    run(() =>
      command("choose", {
        actor: $("actor").value,
        text: state.catalog.intents[quick.dataset.intent],
        intent: quick.dataset.intent,
        target: $("target").value || null,
      }),
    );
  if (e.target.closest("#open-roll")) {
    const p = state.game.pending;
    $("roll-description").textContent =
      `${state.game.characters[p.action.actor].name} · ${p.check.label} ${p.check.bonus >= 0 ? "+" : ""}${p.check.bonus}`;
    $("physical-die").value = "";
    $("physical-die").max = state.catalog.dice;
    $("digital-roll").textContent = `Бросить d${state.catalog.dice}`;
    $("roll-dialog").showModal();
  }
});
$("new-game").onclick = openNew;
$("welcome-start").onclick = openNew;
$("mobile-menu").onclick = () => {
  $("mobile-menu").setAttribute(
    "aria-expanded",
    String(document.body.classList.toggle("nav-open")),
  );
};
$("new-form").onsubmit = (e) => {
  e.preventDefault();
  run(async () => {
    const ids = [
      ...document.querySelectorAll("input[name=character]:checked"),
    ].map((i) => i.value);
    await command("new", {
      characters: ids,
      demo: $("demo").checked,
      adventure_key: $("adventure-select").value,
    });
    $("new-dialog").close();
    switchTab("table");
  });
};
$("primary-action").onclick = () => {
  const cmd = $("primary-action").dataset.command;
  if (cmd) run(() => command(cmd));
};
$("pause").onclick = () => run(() => command("pause"));
$("discard").onclick = () => run(() => command("discard"));
$("clear-actions").onclick = () => run(() => command("clear_actions"));
$("actor").onchange = render;
$("target").onchange = render;
$("undo").onclick = () => run(() => command("undo"));
$("hint").onclick = () =>
  toast(
    state.presentation.guidance || state.catalog.scenes[state.game.scene].hint,
  );
$("action-form").onsubmit = (e) => {
  e.preventDefault();
  run(async () => {
    await command("submit", {
      actor: $("actor").value,
      text: $("action-text").value,
      target: $("target").value || null,
    });
    $("action-text").value = "";
  });
};
$("edit-action").onclick = () =>
  run(() =>
    command("edit", {
      intent: $("edit-intent").value,
      target: $("target").value || null,
      ...($("action-text").value.trim()
        ? { text: $("action-text").value.trim() }
        : {}),
    }),
  );
$("manual-form").onsubmit = (e) => {
  e.preventDefault();
  run(async () => {
    const hp = {};
    document.querySelectorAll("[data-hp]").forEach((i) => {
      if (i.value !== "") hp[i.dataset.hp] = Number(i.value);
    });
    await command("manual", {
      text: $("manual-text").value,
      actor: $("actor").value,
      hp,
    });
    $("manual-text").value = "";
    document.querySelectorAll("[data-hp]").forEach((i) => (i.value = ""));
  });
};
async function roll(die) {
  await command("roll", {
    request_id: state.game.pending.request_id,
    ...(die === undefined ? {} : { die }),
  });
  $("roll-dialog").close();
}
$("digital-roll").onclick = () => run(() => roll());
$("roll-form").onsubmit = (e) => {
  e.preventDefault();
  run(() => roll(Number($("physical-die").value)));
};
$("rewrite").onclick = () =>
  run(async () => {
    toast("Готовлю другую формулировку…");
    state = await api("rewrite", {
      kind: "rewrite",
      data: {},
      command_id: crypto.randomUUID(),
      revision: state.game.revision,
    });
  });
$("settings-open").onclick = () => {
  closeMenu();
  const s = state.settings;
  $("api-key").value = "";
  $("bot-token").value = "";
  $("api-key").placeholder = s.has_openai_key
    ? "Ключ сохранён; оставьте пустым"
    : "Не настроен";
  $("bot-token").placeholder = s.has_telegram_token
    ? "Токен сохранён; оставьте пустым"
    : "Не настроен";
  $("ai-enabled").checked = s.ai_enabled;
  $("tg-enabled").checked = s.telegram_enabled;
  $("limit").value = s.budget;
  $("model").innerHTML = state.catalog.models
    .map((m) => `<option ${m === s.model ? "selected" : ""}>${esc(m)}</option>`)
    .join("");
  $("clear-api").checked = false;
  $("clear-bot").checked = false;
  $("settings-dialog").showModal();
};
$("settings-form").onsubmit = (e) => {
  e.preventDefault();
  run(async () => {
    await api("settings", {
      openai_key: $("api-key").value.trim(),
      telegram_token: $("bot-token").value.trim(),
      ai_enabled: $("ai-enabled").checked,
      telegram_enabled: $("tg-enabled").checked,
      model: $("model").value,
      budget: Number($("limit").value),
      clear_openai_key: $("clear-api").checked,
      clear_telegram_token: $("clear-bot").checked,
    });
    $("api-key").value = "";
    $("bot-token").value = "";
    $("settings-dialog").close();
    state = await api("state");
    toast("Настройки сохранены. Статус Telegram обновится после подключения.");
  });
};
$("invite-open").onclick = () => {
  const g = state.game,
    name = state.telegram.username;
  let html;
  if (name) {
    html = `<p class="muted">Приглашения действуют 24 часа. Новые участники подключаются до начала игры.</p><p class="invite-title">Ссылка для игроков</p><a class="invite-link" href="https://t.me/${encodeURIComponent(name)}?start=${encodeURIComponent(g.invite)}" target="_blank" rel="noopener">https://t.me/${esc(name)}?start=${esc(g.invite)}</a><p class="invite-title">Личная ссылка ведущего</p><p class="fineprint">Откройте сами, чтобы отправлять голосовые указания. Не передавайте игрокам.</p><a class="invite-link" href="https://t.me/${encodeURIComponent(name)}?start=${encodeURIComponent(g.gm_invite)}" target="_blank" rel="noopener">https://t.me/${esc(name)}?start=${esc(g.gm_invite)}</a>`;
  } else {
    html =
      '<p class="muted">Сначала добавьте токен бота в «Подключения» и включите Telegram. После подключения здесь появятся ссылки.</p><p>Без Telegram можно играть в деморежиме: действия и броски вводятся на ноутбуке.</p>';
  }
  $("invite-content").innerHTML = html;
  $("invite-dialog").showModal();
};
$("export").onclick = () =>
  run(async () => {
    const data = await api("export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "dnd-save.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
$("shutdown").onclick = () => {
  if (confirm("Завершить приложение? Игра сохранена. Бот остановится."))
    run(async () => {
      await api("shutdown", {});
      stopped = true;
      toast("Приложение завершает работу. Вкладку можно закрыть.");
    });
};
async function refresh() {
  if (busy || stopped) return;
  try {
    state = await api("state");
    render();
  } catch {
    if (state) {
      $("connection-banner").textContent =
        "Нет связи с приложением. Проверьте, что оно запущено; игра сохранена.";
      show("connection-banner", true);
    } else toast("Не удалось подключиться к локальному приложению.", true);
  }
}
refresh();
setInterval(refresh, 2500);
