const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const byId = (id) => document.getElementById(id);
const campaignField = byId("campaign-id");
const initialId = new URLSearchParams(location.search).get("campaign_id");
if (initialId && /^[1-9]\d*$/.test(initialId)) campaignField.value = initialId;
let streams = [];
let currentCampaign = initialId || null;
let busy = false;

function message(text, success = false) {
  byId("editor-notice").hidden = true;
  byId("editor-success").hidden = true;
  const target = byId(success ? "editor-success" : "editor-notice");
  target.textContent = text;
  target.hidden = !text;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join("; ")
      : data.detail?.message || data.detail;
    throw new Error(detail || "Не удалось выполнить запрос. Проверьте сохранённое состояние.");
  }
  return data;
}

function button(text, callback, className = "secondary", disabled = false) {
  const node = element("button", text, className);
  node.type = "button";
  node.disabled = disabled || busy;
  node.addEventListener("click", callback);
  return node;
}

async function savedState() {
  if (!currentCampaign) return;
  const data = await api(`/api/editor/campaigns/${currentCampaign}`);
  streams = data.streams;
  render();
}

async function perform(action) {
  if (busy) return;
  busy = true;
  message("");
  document.querySelectorAll("button").forEach((node) => { node.disabled = true; });
  try { await action(); }
  catch (error) {
    message(error.message);
    try { await savedState(); } catch { /* черновик остаётся в БД */ }
  } finally {
    busy = false;
    byId("fetch").disabled = false;
    byId("refresh").disabled = false;
    render();
  }
}

async function change(stream, action, values = {}) {
  return perform(async () => {
    const updated = await api(`/api/editor/streams/${stream.id}/${action}`, {
      revision: stream.revision, ...values,
    });
    streams = streams.map((item) => item.id === updated.id ? updated : item);
    if (action === "publish") message("Публикация подтверждена чтением из Keitaro.", true);
    else if (action === "cancel") message("Черновик отменён. Данные Keitaro не менялись.", true);
    else if (action === "reconcile") {
      message(updated.status === "ready" ? "Результат сверен с Keitaro." : "Keitaro изменился. Отмените черновик и загрузите потоки заново.", updated.status === "ready");
    }
  });
}

function offerRow(stream, offer, locked) {
  const row = element("div", undefined, `offer-row${offer.removed ? " removed" : ""}`);
  const label = element("div");
  label.append(element("div", `${offer.offer_id} · ${offer.name}`, "offer-name"));
  label.append(element("div", offer.removed ? "В архиве" : offer.draft_new ? "Добавлен в черновик" : offer.pinned ? "Доля закреплена" : "Автоматическая доля", "offer-label"));
  row.append(label, element("div", `${offer.share}%`, "share"));
  const pin = element("div", undefined, "pin-controls");
  if (!offer.removed) {
    const input = element("input", undefined, "pin-input");
    input.type = "number"; input.min = "0"; input.max = "100"; input.step = "1";
    input.value = offer.share; input.disabled = locked || busy;
    input.setAttribute("aria-label", `Доля оффера ${offer.offer_id} в потоке ${stream.id}`);
    pin.append(input, button(offer.pinned ? "Обновить долю" : "Закрепить долю", () => {
      if (!input.checkValidity() || input.value === "") { input.reportValidity(); return; }
      change(stream, "pin", {offer_id: offer.offer_id, share: Number(input.value)});
    }, "secondary", locked));
    if (offer.pinned) pin.append(button("Открепить", () => change(stream, "unpin", {offer_id: offer.offer_id}), "secondary", locked));
  }
  const actions = element("div", undefined, "offer-actions");
  actions.append(button(offer.removed ? "Вернуть · Bring back" : "Удалить · Remove", () => {
    change(stream, offer.removed ? "restore" : "remove", {offer_id: offer.offer_id});
  }, offer.removed ? "secondary" : "danger", locked));
  row.append(pin, actions);
  return row;
}

function addForm(stream, locked) {
  const area = element("div", undefined, "add-offer");
  const row = element("div", undefined, "search-row");
  const searchBox = element("div");
  const label = element("label", "Поиск оффера по ID или названию");
  label.htmlFor = `search-${stream.id}`;
  const input = element("input");
  input.id = label.htmlFor; input.placeholder = "Введите ID или часть названия";
  input.maxLength = 100; input.disabled = locked || busy;
  searchBox.append(label, input);
  const selection = element("div");
  const selectLabel = element("label", "Результаты поиска");
  const select = element("select");
  select.id = `offers-${stream.id}`; selectLabel.htmlFor = select.id;
  select.append(new Option("Начните поиск…", ""));
  select.disabled = locked || busy;
  selection.append(selectLabel, select);
  const status = element("p", "", "search-status");
  let timer;
  let sequence = 0;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const version = ++sequence;
    timer = setTimeout(async () => {
      status.textContent = "Поиск…";
      try {
        const result = await api(`/api/editor/offers?q=${encodeURIComponent(input.value)}`);
        if (version !== sequence) return;
        select.replaceChildren(new Option("Выберите оффер…", ""));
        for (const offer of result) select.add(new Option(`${offer.id} · ${offer.name}`, offer.id));
        status.textContent = result.length ? `Найдено: ${result.length}` : "Нет доступных офферов";
      } catch (error) { if (version === sequence) status.textContent = error.message; }
    }, 300);
  });
  row.append(searchBox, selection, button("Добавить · Add", () => {
    if (!select.value) { status.textContent = "Сначала выберите оффер из результатов поиска"; return; }
    change(stream, "add", {offer_id: Number(select.value)});
  }, "secondary", locked));
  area.append(row, status);
  return area;
}

function render() {
  const container = byId("streams");
  container.replaceChildren();
  if (!streams.length) {
    container.append(element("p", "Сохранённых потоков нет. Нажмите Fetch, чтобы загрузить их из Keitaro.", "empty-state"));
    return;
  }
  for (const stream of streams) {
    const uncertain = ["publishing", "uncertain", "conflict"].includes(stream.status);
    const locked = uncertain || !stream.editable;
    const card = element("article", undefined, `stream${stream.dirty ? " dirty" : ""}${uncertain ? " locked" : ""}`);
    const head = element("div", undefined, "stream-head");
    const title = element("div");
    title.append(element("h2", stream.name), element("p", `Поток #${stream.id} · позиция ${stream.position} · версия ${stream.revision}`, "hint"));
    const status = !stream.remote_exists ? "Удалён в Keitaro" : ({publishing: "Публикация…", uncertain: "Исход неизвестен", conflict: "Внешние изменения"})[stream.status] || (stream.dirty ? "Черновик · не опубликован" : "Синхронизирован");
    head.append(title, element("span", status, "badge"));
    card.append(head);
    if (!stream.editable) {
      card.append(element("p", "Поток без редактируемого списка офферов. Его действие и фильтры сохраняются без изменений.", "stream-note"));
      container.append(card); continue;
    }
    const list = element("div", undefined, "offer-list");
    for (const offer of stream.offers) list.append(offerRow(stream, offer, locked));
    if (!stream.offers.length) list.append(element("p", "В потоке пока нет офферов.", "stream-note"));
    card.append(list, addForm(stream, locked));
    const footer = element("div", undefined, "stream-footer");
    footer.append(element("p", `Активных: ${stream.offers.filter((o) => !o.removed).length} · Сумма долей: ${stream.total_share}%`));
    const actions = element("div", undefined, "actions");
    if (["publishing", "uncertain"].includes(stream.status)) {
      actions.append(button("Проверить публикацию", () => change(stream, "reconcile")));
    } else {
      actions.append(button("Отменить · Cancel", () => change(stream, "cancel"), "secondary", !stream.dirty && stream.status !== "conflict"));
      actions.append(button("Опубликовать · Push to KT", () => change(stream, "publish"), "publish", !stream.can_publish));
    }
    footer.append(actions); card.append(footer);
    if (stream.dirty && stream.total_share !== 100) card.append(element("p", "Перед публикацией добавьте или восстановите активный оффер. Сумма долей должна быть 100%.", "stream-note"));
    container.append(card);
  }
}

byId("load-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    const id = Number(campaignField.value);
    if (id !== Number(currentCampaign) && streams.some((s) => s.dirty || ["publishing", "uncertain"].includes(s.status))) {
      throw new Error("Сначала завершите работу с черновиком текущей кампании.");
    }
    const data = await api(`/api/editor/campaigns/${id}/fetch`, {});
    currentCampaign = String(id);
    streams = data.streams;
    history.replaceState(null, "", `/editor?campaign_id=${id}`);
    message(`Загружена ${data.campaign_name}. Потоков: ${streams.length}.`, true);
  });
});
byId("refresh").addEventListener("click", () => perform(savedState));
if (currentCampaign) perform(savedState);
