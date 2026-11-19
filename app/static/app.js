const $ = (id) => document.getElementById(id);
const form = $("campaign-form");
let referencesReady = false;
let attempt = null;
try { attempt = JSON.parse(sessionStorage.getItem("campaign-attempt")); } catch { /* новая сессия */ }

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function lock() { $("fields").disabled = !referencesReady || Boolean(attempt); }

async function loadReferences() {
  $("reload").hidden = true;
  $("connection").textContent = "Подключение…";
  try {
    const response = await fetch("/api/reference-data");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || "Не удалось получить справочники");
    for (const [id, key] of [["offer", "offers"], ["domain", "domains"], ["group", "groups"], ["source", "traffic_sources"]]) {
      $(id).replaceChildren(new Option("Выберите…", ""));
      for (const item of data[key]) $(id).add(new Option(`${item.id} · ${item.name}`, item.id));
      if (!data[key].length) throw new Error(`Справочник ${key} пуст. Создание пока недоступно.`);
    }
    if (attempt?.payload) {
      for (const [key, value] of Object.entries(attempt.payload)) {
        const field = form.elements.namedItem(key);
        if (field) field.value = value;
      }
    }
    referencesReady = true;
    $("connection").textContent = "● API подключён";
    notice("");
  } catch (error) {
    referencesReady = false;
    $("connection").textContent = "Нет подключения";
    $("reload").hidden = false;
    notice(error.message);
  }
  lock();
}

function setLink(id, value) {
  const link = $(id);
  link.hidden = true;
  if (!value) return;
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol)) return;
    link.href = url.href;
    link.hidden = false;
  } catch { /* некорректная ссылка не отображается */ }
}

function showResult(data) {
  $("result").hidden = false;
  $("result").dataset.state = data.status;
  $("result-status").textContent = ({succeeded: "СОЗДАНО", failed: "ОШИБКА", partial: "ТРЕБУЕТ ПРОВЕРКИ", pending: "В ПРОЦЕССЕ"})[data.status] || "СТАТУС НЕИЗВЕСТЕН";
  $("result-title").textContent = data.campaign_name || attempt?.payload.name || "Проверка операции";
  $("result-message").textContent = data.message;
  const details = $("result-details");
  details.replaceChildren();
  for (const [label, value] of [["ID операции", data.request_id], ["ID кампании", data.campaign_id], ["ID потоков", data.stream_ids?.join(", ")]]) {
    if (!value) continue;
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = value;
    details.append(dt, dd);
  }
  setLink("admin-link", data.admin_url);
  setLink("campaign-link", data.status === "succeeded" ? data.campaign_url : null);
  $("editor-link").hidden = data.status !== "succeeded";
  if (data.status === "succeeded") $("editor-link").href = `/editor?campaign_id=${data.campaign_id}`;
  $("new").hidden = !["succeeded", "failed"].includes(data.status);
}

async function checkStatus() {
  if (!attempt) return;
  $("check").disabled = true;
  try {
    const response = await fetch(`/api/operations/${attempt.request_id}`);
    if (response.status === 404) {
      // Тот же UUID: сервер либо начнёт операцию, либо вернёт существующую.
      await submitAttempt();
      return;
    }
    let data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || "Не удалось проверить статус");
    if (["partial", "pending"].includes(data.status) && data.campaign_id) {
      const verified = await fetch(`/api/operations/${attempt.request_id}/verify`, {method: "POST"});
      data = await verified.json();
      if (!verified.ok) throw new Error(data.detail?.message || "Не удалось сверить настройки");
    }
    showResult(data);
    notice("");
  } catch (error) { notice(`${error.message}. Сохраните ID операции и проверьте её позже.`); }
  finally { $("check").disabled = false; }
}

async function submitAttempt() {
  showResult({request_id: attempt.request_id, status: "pending", message: "Создаём кампанию и проверяем потоки…"});
  try {
    const response = await fetch("/api/campaigns", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...attempt.payload, request_id: attempt.request_id}),
    });
    const data = await response.json();
    if (data.status) { showResult(data); return; }
    if (response.status === 422) {
      const errors = Array.isArray(data.detail) ? data.detail.map((e) => `${e.loc.at(-1)}: ${e.msg}`).join("; ") : "Проверьте введённые данные";
      showResult({request_id: attempt.request_id, status: "failed", message: errors});
      return;
    }
    throw new Error(data.detail?.message || "Не удалось получить результат");
  } catch (error) {
    showResult({request_id: attempt.request_id, status: "partial", message: `${error.message}. Не создавайте новую кампанию — сначала проверьте статус этой операции.`});
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (attempt) return;
  const payload = Object.fromEntries(new FormData(form));
  for (const key of ["offer_id", "domain_id", "group_id", "traffic_source_id"]) payload[key] = Number(payload[key]);
  payload.name = payload.name.trim();
  payload.country_code = payload.country_code.trim().toUpperCase();
  attempt = {request_id: crypto.randomUUID(), payload};
  sessionStorage.setItem("campaign-attempt", JSON.stringify(attempt));
  notice(""); lock();
  await submitAttempt();
});

$("check").addEventListener("click", checkStatus);
$("reload").addEventListener("click", loadReferences);
$("new").addEventListener("click", () => {
  attempt = null; sessionStorage.removeItem("campaign-attempt");
  form.reset(); $("result").hidden = true; notice(""); lock();
});
loadReferences();
if (attempt) checkStatus();
