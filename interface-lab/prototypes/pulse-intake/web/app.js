"use strict";
const $ = (id) => document.getElementById(id);
const labels = { empty: "Не обсудили", captured: "Записано", needs_detail: "Уточнить", unknown: "Пока не определено" };
let stateKey = "pulse-intake:2026-10-20.1";
let config, state, busy = false, editing = null, canSave = true;
let voiceState = "idle";
const fieldElements = new Map();
const turnElements = new Map();
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function textOf(answer) { return answer.manual ?? (answer.summary || answer.quotes.map((quote) => quote.quote).join("\n")); }
function sourceOf(answer) { return answer.quotes.map((quote) => quote.quote).join("\n"); }
function freshState() {
  return { session: crypto.randomUUID(), version: config.version, revision: 0,
    brief: Object.fromEntries(config.fields.map((field) => [field.id, { status: "empty", quotes: [], manual: null, summary: "" }])),
    messages: [{ id: "welcome", role: "assistant", content: config.welcome }], pending: null,
    activeField: config.fields[0].id, activeQuestion: config.fields[0].question, drafts: {} };
}
function saveWarning(text) { $("save-note").textContent = text; $("save-note").hidden = false; }
function save() {
  if (!canSave || !state) return;
  try { localStorage.setItem(stateKey, JSON.stringify(state)); }
  catch { saveWarning("Не удалось сохранить ответ в браузере. Не закрывайте страницу до отправки."); }
}
function selectTab(tab) {
  $("tab-chat").setAttribute("aria-pressed", String(tab === "chat"));
  $("tab-brief").setAttribute("aria-pressed", String(tab === "brief"));
  $("chat-pane").classList.toggle("mobile-active", tab === "chat");
  $("brief-pane").classList.toggle("mobile-active", tab === "brief");
  updateJump();
}
function nearLatest() {
  const pane = $("messages-scroll");
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
}
function updateJump() { $("jump-latest").hidden = nearLatest(); }
function scrollLatest() {
  $("messages-scroll").scrollTop = $("messages-scroll").scrollHeight;
  updateJump();
}
function blocked() { return busy || voiceState !== "idle" || !!state?.pending; }
function controls() {
  $("answer").disabled = busy || voiceState !== "idle" || !config || !state;
  $("send").disabled = blocked() || !config || !state || !$("answer").value.trim();
  $("voice").disabled = busy || !config || !state || !!state?.pending || ["requesting", "transcribing"].includes(voiceState);
  $("thinking").hidden = !busy;
  $("review-open").disabled = busy || !state || !Object.values(state.brief).some((answer) => textOf(answer).trim());
  $("edit-current").disabled = blocked();
  for (const item of fieldElements.values()) item.button.disabled = blocked();
}
function selectField(id) {
  if (blocked()) return;
  state.drafts[state.activeField || "final"] = $("answer").value;
  state.activeField = id;
  state.activeQuestion = config.fields.find((field) => field.id === id).question;
  $("answer").value = state.drafts[id] || "";
  $("error").hidden = true;
  save(); drawFields(); drawQuestion(); selectTab("chat");
  scrollLatest();
  $("current-question").focus({ preventScroll: true });
}
function buildFields() {
  for (const group of [...new Set(config.fields.map((field) => field.group))]) {
    const section = element("section", "field-group");
    section.append(element("h2", "", group));
    for (const field of config.fields.filter((item) => item.group === group)) {
      const article = element("article", "field"); article.id = "field-" + field.id;
      const button = element("button", "field-select"); button.type = "button";
      button.setAttribute("aria-label", "Ответить: " + field.label);
      const caption = element("span", "field-label"), dot = element("span", "status-dot");
      dot.setAttribute("aria-hidden", "true");
      caption.append(dot, element("span", "field-title", field.label));
      const value = element("span", "field-value"), status = element("span", "field-state");
      button.append(caption, value, status);
      button.addEventListener("click", () => selectField(field.id));
      article.append(button); section.append(article);
      fieldElements.set(field.id, { article, button, dot, value, status });
    }
    $("fields").append(section);
  }
}
function drawFields(updated = []) {
  let filled = 0, captured = 0;
  for (const field of config.fields) {
    const answer = state.brief[field.id], parts = fieldElements.get(field.id), value = textOf(answer);
    parts.article.classList.toggle("field-updated", updated.includes(field.id));
    parts.article.classList.toggle("field-selected", field.id === state.activeField);
    parts.button.setAttribute("aria-current", field.id === state.activeField ? "step" : "false");
    parts.dot.className = "status-dot " + answer.status;
    parts.value.textContent = value || "—";
    parts.value.classList.toggle("has-value", !!value);
    parts.status.textContent = answer.status === "needs_detail" ? "Уточнить" : "";
    parts.status.className = "field-state " + answer.status;
    if (answer.status !== "empty") filled++;
    if (field.required && answer.status === "captured" && value.trim()) captured++;
  }
  const total = config.fields.filter((field) => field.required).length;
  $("progress-text").textContent = `${captured} / ${total}`;
  $("progress").max = total; $("progress").value = captured;
  $("field-count").textContent = filled;
  controls();
}
function drawQuestion() {
  $("question-stage").hidden = !!state.pending;
  $("current-question").textContent = state.activeQuestion;
  const answer = state.brief[state.activeField];
  $("current-answer").hidden = !answer || !textOf(answer);
}
function drawMessages() {
  const messages = $("messages"), visible = new Set();
  let question = null;
  let position = 0;
  for (const message of state.messages) {
    // An unanswered suggestion is replaced when the user chooses another topic.
    // The active question is rendered once, at the end of the conversation.
    if (message.role === "assistant") { question = message; continue; }
    visible.add(message.id);
    const previous = turnElements.get(message.id);
    if (previous?.question === question?.content && previous?.answer === message.content) {
      if (messages.children[position] !== previous.node) messages.insertBefore(previous.node, messages.children[position] || null);
      question = null; position++; continue;
    }
    const turn = element("article", "conversation-turn");
    if (question) {
      const prompt = element("div", "message message-assistant");
      prompt.setAttribute("aria-label", "Вопрос");
      prompt.append(element("p", "", question.content)); turn.append(prompt);
    }
    const reply = element("div", "message message-user");
    reply.setAttribute("aria-label", "Ваш ответ");
    const body = element("div", "message-body"); body.append(element("p", "", message.content));
    reply.append(body); turn.append(reply);
    previous?.node.remove();
    messages.insertBefore(turn, messages.children[position] || null);
    turnElements.set(message.id, { node: turn, question: question?.content, answer: message.content });
    position++;
    question = null;
  }
  for (const [id, entry] of turnElements) {
    if (!visible.has(id)) { entry.node.remove(); turnElements.delete(id); }
  }
}
function render(updated = []) {
  const position = $("messages-scroll").scrollTop;
  drawFields(updated); drawQuestion(); drawMessages();
  $("messages-scroll").scrollTop = position;
  updateJump();
}
function showError(text, conflict = false) {
  $("error-text").textContent = text; $("error").hidden = false;
  $("retry").hidden = !state?.pending || conflict;
  $("edit-answer").hidden = !state?.pending || conflict;
  $("refresh-state").hidden = !conflict;
}
function validateSaved(saved) {
  if (![config.version, "2026-10-20.1", "2026-10-20.2"].includes(saved.version) || typeof saved.session !== "string" || !Array.isArray(saved.messages) || saved.messages.length > 82) throw new Error();
  for (const field of config.fields) {
    const answer = saved.brief[field.id];
    if (!answer || !Object.hasOwn(labels, answer.status) || !Array.isArray(answer.quotes) || !(answer.manual === null || typeof answer.manual === "string")) throw new Error();
    for (const quote of answer.quotes) if (typeof quote.quote !== "string" || typeof quote.message_id !== "string") throw new Error();
    answer.summary ??= "";
  }
  for (const message of saved.messages) if (!["user", "assistant"].includes(message.role) || typeof message.content !== "string" || typeof message.id !== "string") throw new Error();
  saved.version = config.version; saved.revision ??= 0; saved.drafts ??= {};
  if (!Object.hasOwn(saved, "activeField")) saved.activeField = config.fields.find((field) => saved.brief[field.id].status === "empty")?.id || null;
  saved.activeQuestion ||= config.fields.find((field) => field.id === saved.activeField)?.question || "Что хотите дополнить в заявке?";
  if (saved.pending) {
    saved.pending.focus ??= saved.activeField;
    saved.pending.question ??= saved.activeQuestion;
    for (const answer of Object.values(saved.pending.brief)) answer.summary ??= "";
  }
  return saved;
}
async function jsonRequest(path, body, headers = {}) {
  const response = await fetch(path, { ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) }), signal: AbortSignal.timeout(285000) });
  const result = await response.json();
  if (!response.ok) {
    const messages = { REVISION_CONFLICT: "Заявка изменилась в другом окне. Обновите её перед продолжением.", INVITE_REQUIRED: "Откройте анкету по полной ссылке-приглашению.", SESSION_BUDGET: "Лимит беседы достигнут. Можно исправить поля и скачать заявку.", CONVERSATION_LIMIT: "Основная беседа завершена. Можно исправить поля и скачать заявку." };
    const error = new Error(messages[result.error] || "Не удалось получить ответ. Ваш текст сохранён, попробуйте ещё раз.");
    error.code = result.error; throw error;
  }
  return result;
}
async function send(retry = false) {
  if (busy || voiceState !== "idle" || (!retry && (state.pending || !$("answer").value.trim()))) return;
  $("error").hidden = true;
  if (!retry) {
    const message = { id: crypto.randomUUID(), role: "user", content: $("answer").value.trim() };
    state.pending = { session: state.session, message_id: message.id, message: message.content, history: structuredClone(state.messages), brief: structuredClone(state.brief), focus: state.activeField, question: state.activeQuestion };
    state.pendingRevision = state.revision;
    if (state.messages.at(-1)?.content !== state.activeQuestion) state.messages.push({ id: "question-" + message.id, role: "assistant", content: state.activeQuestion });
    state.messages.push(message);
    state.drafts[state.activeField || "final"] = "";
    $("answer").value = ""; save();
  }
  busy = true; controls(); drawQuestion(); drawMessages(); scrollLatest();
  let follow = true;
  try {
    const result = await jsonRequest("/api/turn", state.pending, { "X-Revision": String(state.pendingRevision ?? state.revision) });
    follow = nearLatest();
    const drafts = state.drafts;
    if (result.state) state = { ...validateSaved(result.state), drafts };
    else {
      state.brief = result.brief;
      state.messages.push({ id: crypto.randomUUID(), role: "assistant", content: result.message });
      state.pending = null; state.activeField = result.focus; state.activeQuestion = result.message;
    }
    $("answer").value = drafts[state.activeField || "final"] || "";
    save(); render(result.updated);
  } catch (error) {
    follow = nearLatest();
    showError(error.name === "TimeoutError" ? "Ответ занимает больше времени. Можно повторить запрос: текст сохранён." : error.message, error.code === "REVISION_CONFLICT");
  } finally { busy = false; controls(); if (follow) scrollLatest(); else updateJump(); }
}
async function refreshState() {
  if (busy) return;
  busy = true; controls();
  try {
    const current = validateSaved(await jsonRequest("/api/state"));
    const pending = state.pending, drafts = state.drafts;
    if (pending && !current.messages.some((message) => message.id === pending.message_id)) {
      drafts[pending.focus || "final"] = pending.message;
      current.activeField = pending.focus; current.activeQuestion = pending.question;
    }
    state = { ...current, drafts };
    $("answer").value = drafts[state.activeField || "final"] || "";
    $("error").hidden = true; save(); render(); scrollLatest();
  } catch (error) { showError(error.message); }
  finally { busy = false; controls(); }
}
function review() {
  const gaps = config.fields.filter((field) => field.required && state.brief[field.id].status !== "captured");
  const box = $("review-gaps"); box.replaceChildren();
  if (gaps.length) {
    box.append(element("strong", "", "Можно уточнить позже"));
    const list = element("ul"); gaps.forEach((field) => list.append(element("li", "", field.label))); box.append(list);
  } else box.textContent = "Основные требования записаны. Проверьте формулировки перед скачиванием.";
  $("download").textContent = gaps.length ? "Скачать черновик ↓" : "Скачать заявку ↓";
  $("review-dialog").showModal();
}
function exportBrief() {
  const lines = ["# Пульс Логистика · Заявка на подбор", "", "Направление: лидогенерация", "Версия: " + config.version, ""];
  for (const field of config.fields) {
    const answer = state.brief[field.id];
    lines.push("## " + field.label, "Статус: " + labels[answer.status], textOf(answer) || "Не обсуждалось.", "");
    if (answer.summary && answer.manual === null && sourceOf(answer) !== answer.summary) lines.push("Ответы заказчика:", sourceOf(answer), "");
  }
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = "Пульс-Логистика-заявка-на-подбор.md";
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000); $("review-dialog").close();
}
async function edit(value) {
  if (busy) return;
  busy = true; controls(); $("edit-form").querySelector("button[type=submit]").disabled = true;
  try {
    if (config.persistent) {
      const result = await jsonRequest("/api/edit", { revision: state.revision, field: editing, value });
      state.brief = result.brief; state.revision = result.revision;
    } else {
      const answer = state.brief[editing]; answer.manual = value;
      answer.status = value === null ? (answer.quotes.length ? "needs_detail" : "empty") : (value ? "captured" : "empty");
    }
    save(); render([editing]); $("edit-dialog").close();
  } catch (error) { $("edit-dialog").close(); showError(error.message, error.code === "REVISION_CONFLICT"); }
  finally { busy = false; controls(); $("edit-form").querySelector("button[type=submit]").disabled = false; }
}
const voice = new VoiceInput({
  upload: async (blob, signal) => {
    const response = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": blob.type, "X-Intake-Session": state.session }, body: blob, signal });
    const result = await response.json();
    if (!response.ok) {
      const messages = { NO_SPEECH: "Не получилось разобрать речь. Попробуйте ещё раз.", VOICE_NOT_CONFIGURED: "Голосовой ввод ещё подключается. Пока можно ответить текстом.", AUDIO_TOO_LONG: "Голосовой ответ должен быть короче двух минут.", VOICE_BUDGET: "Лимит голосовых ответов достигнут. Продолжите текстом." };
      throw new Error(messages[result.error] || "Не получилось распознать запись. Попробуйте ещё раз.");
    }
    return result.text;
  },
  onState: (value, seconds) => {
    voiceState = value;
    $("voice").classList.toggle("is-recording", value === "recording");
    $("voice").setAttribute("aria-label", value === "recording" ? "Закончить запись" : "Ответить голосом");
    $("voice-cancel").hidden = value === "idle";
    $("voice-status").hidden = value === "idle";
    $("voice-status").textContent = value === "recording" ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : value === "requesting" ? "Микрофон…" : "Распознаю…";
    controls();
  },
  onResult: (text) => {
    const before = $("answer").value.trim();
    const joined = before ? before + "\n" + text : text;
    if (joined.length > 6000) { showError("Ответ слишком длинный. Сократите текст в поле и повторите запись."); return; }
    $("answer").value = joined; state.drafts[state.activeField || "final"] = joined; save();
  },
  onError: (text) => showError(text),
});
async function initialize() {
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get("access");
    if (token) { await jsonRequest("/api/unlock", { token }); history.replaceState(null, "", location.pathname); }
    config = await jsonRequest("/api/config");
    if (config.persistent) stateKey = "pulse-intake:" + config.session;
    state = freshState();
    let saved = null;
    try {
      const raw = localStorage.getItem(stateKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed.version === "2026-10-20.1") localStorage.setItem(stateKey + ":original", raw);
        saved = validateSaved(parsed);
      }
    } catch { canSave = false; saveWarning("Старый черновик сохранён отдельно. Не удалось восстановить его автоматически."); }
    if (config.persistent) {
      state = validateSaved(await jsonRequest("/api/state"));
      state.drafts = saved?.drafts || {};
      if (saved?.revision === state.revision) {
        state.activeField = saved.activeField; state.activeQuestion = saved.activeQuestion;
        if (saved.pending && !state.messages.some((message) => message.id === saved.pending.message_id)) {
          state.pending = saved.pending; state.pendingRevision = saved.pendingRevision;
          if (state.messages.at(-1)?.content !== saved.pending.question) state.messages.push({ id: "question-" + saved.pending.message_id, role: "assistant", content: saved.pending.question });
          state.messages.push({ id: saved.pending.message_id, role: "user", content: saved.pending.message });
        }
      }
    } else if (saved) state = saved;
    $("answer").value = state.drafts[state.activeField || "final"] || "";
    buildFields(); render();
    if (state.pending) showError("Предыдущий ответ ещё не обработан. Можно повторить запрос или изменить ответ.");
    if (!config.configured) showError("Помощник пока недоступен. Попробуйте открыть анкету позже.");
    controls(); scrollLatest();
  } catch (error) { $("current-question").textContent = "Анкета для «Пульс Логистика»"; showError(error.message); }
}
$("tab-chat").addEventListener("click", () => selectTab("chat"));
$("tab-brief").addEventListener("click", () => selectTab("brief"));
$("messages-scroll").addEventListener("scroll", updateJump, { passive: true });
window.addEventListener("resize", updateJump);
$("jump-latest").addEventListener("click", scrollLatest);
$("answer").addEventListener("input", () => { if (state) { state.drafts[state.activeField || "final"] = $("answer").value; save(); } controls(); });
$("answer").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(); } });
$("composer").addEventListener("submit", (event) => { event.preventDefault(); void send(); });
$("retry").addEventListener("click", () => void send(true));
$("refresh-state").addEventListener("click", () => void refreshState());
$("edit-answer").addEventListener("click", () => {
  if (!state.pending || busy) return;
  $("answer").value = state.pending.message; state.activeField = state.pending.focus; state.activeQuestion = state.pending.question;
  state.messages = state.messages.filter((message) => message.id !== state.pending.message_id);
  state.drafts[state.activeField || "final"] = state.pending.message; state.pending = null;
  save(); render(); $("error").hidden = true; scrollLatest(); $("answer").focus();
});
$("edit-current").addEventListener("click", () => {
  editing = state.activeField;
  $("edit-title").textContent = config.fields.find((field) => field.id === editing).label;
  $("edit-value").value = textOf(state.brief[editing]);
  $("edit-revert").hidden = state.brief[editing].manual === null;
  $("edit-dialog").showModal();
});
$("edit-close").addEventListener("click", () => $("edit-dialog").close());
$("edit-form").addEventListener("submit", (event) => { event.preventDefault(); void edit($("edit-value").value.trim()); });
$("edit-revert").addEventListener("click", () => void edit(null));
$("review-open").addEventListener("click", review);
$("review-close").addEventListener("click", () => $("review-dialog").close());
$("review-back").addEventListener("click", () => { $("review-dialog").close(); selectTab("brief"); });
$("download").addEventListener("click", exportBrief);
$("voice").addEventListener("click", () => { $("error").hidden = true; if (voiceState === "recording") voice.stop(); else if (!blocked()) void voice.start(); });
$("voice-cancel").addEventListener("click", () => voice.cancel());
document.addEventListener("visibilitychange", () => { if (document.hidden && voiceState === "recording") voice.stop(); });
window.addEventListener("pagehide", () => voice.cancel());
void initialize();
