importScripts("response-json.js", "tunnel-config.js");

const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const BACKEND_CANDIDATES = [
  DEFAULT_BACKEND,
  "http://localhost:8000",
  globalThis.EXDIVO_TUNNEL_BACKEND
];
const POLL_ALARM = "exdivo-task-poll";

chrome.runtime.onInstalled.addListener(async () => {
  const state = await chrome.storage.local.get(["backendUrl", "instanceId", "polling"]);
  await chrome.storage.local.set({
    backendUrl: state.backendUrl || DEFAULT_BACKEND,
    instanceId: state.instanceId || crypto.randomUUID(),
    polling: state.polling ?? false
  });
  await syncAlarm();
});

chrome.runtime.onStartup.addListener(syncAlarm);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) pollForTask().catch(recordBackgroundError);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message).then(sendResponse).catch((error) => {
    sendResponse({ ok: false, error: error.message });
  });
  return true;
});

async function handleMessage(message) {
  if (message.type === "pair") return pair(message.pairingCode);
  if (message.type === "status") return status();
  if (message.type === "setPolling") {
    await chrome.storage.local.set({ polling: Boolean(message.enabled) });
    await syncAlarm();
    if (message.enabled) await pollForTask();
    return status();
  }
  if (message.type === "reportStage") {
    return reportStage(message.jobId, message.sequenceNo, message.stage, message.details || {});
  }
  throw new Error("Unsupported extension message");
}

async function pair(pairingCode) {
  const state = await chrome.storage.local.get(["backendUrl", "instanceId"]);
  const backendUrl = await discoverBackend(state.backendUrl);
  const payload = await requestJson({ backendUrl }, "/api/v1/social/extension/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pairing_code: pairingCode,
      extension_instance_id: state.instanceId
    })
  });
  await chrome.storage.local.set({
    backendUrl,
    accessToken: payload.access_token,
    device: {
      id: payload.id,
      business_id: payload.business_id,
      container_code: payload.container_code,
      display_name: payload.display_name
    },
    polling: true
  });
  await syncAlarm();
  return status();
}

async function status() {
  const state = await chrome.storage.local.get([
    "backendUrl", "device", "accessToken", "polling", "activeTask", "lastError"
  ]);
  if (state.accessToken) {
    try {
      await authorizedFetch(state, "/api/v1/social/extension/heartbeat", { method: "POST" });
      state.lastError = null;
      await chrome.storage.local.remove("lastError");
    } catch (error) {
      state.lastError = error.message;
    }
  }
  return {
    ok: true,
    paired: Boolean(state.accessToken && state.device),
    device: state.device || null,
    polling: Boolean(state.polling),
    activeTask: state.activeTask || null,
    lastError: state.lastError || null
  };
}

async function syncAlarm() {
  const { polling, accessToken } = await chrome.storage.local.get(["polling", "accessToken"]);
  await chrome.alarms.clear(POLL_ALARM);
  if (polling && accessToken) {
    chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.25 });
  }
}

async function pollForTask() {
  const state = await chrome.storage.local.get([
    "backendUrl", "accessToken", "polling", "activeTask"
  ]);
  if (!state.polling || !state.accessToken || state.activeTask) return;
  const payload = await authorizedFetch(state, "/api/v1/social/extension/tasks/next");
  if (!payload.task) return;
  await chrome.storage.local.set({ activeTask: payload.task });
  await reportStage(payload.task.id, 1, "opening_page", { platform: payload.task.platform });
  const tab = await chrome.tabs.create({ url: payload.task.target_url, active: true });
  const listener = (tabId, changeInfo) => {
    if (tabId !== tab.id || changeInfo.status !== "complete") return;
    chrome.tabs.onUpdated.removeListener(listener);
    chrome.tabs.sendMessage(tab.id, { type: "taskReady", task: payload.task }).catch(
      recordBackgroundError
    );
  };
  chrome.tabs.onUpdated.addListener(listener);
}

async function reportStage(jobId, sequenceNo, stage, details) {
  const state = await chrome.storage.local.get(["backendUrl", "accessToken", "activeTask"]);
  const result = await authorizedFetch(
    state,
    `/api/v1/social/extension/tasks/${encodeURIComponent(jobId)}/stage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sequence_no: sequenceNo,
        stage,
        details: sanitizeDetails(details)
      })
    }
  );
  if (["published", "manual_required", "failed"].includes(stage)) {
    await chrome.storage.local.remove("activeTask");
  }
  return result;
}

async function authorizedFetch(state, path, options = {}) {
  return requestJson(state, path, options, true);
}

async function requestJson(state, path, options = {}, authorized = false) {
  const headers = new Headers(options.headers || {});
  if (authorized) headers.set("Authorization", `Bearer ${state.accessToken}`);
  const response = await fetch(`${state.backendUrl || DEFAULT_BACKEND}${path}`, {
    ...options,
    headers
  });
  return globalThis.parseExdivoJsonResponse(
    response,
    `${state.backendUrl || DEFAULT_BACKEND}${path}`
  );
}

async function discoverBackend(preferred) {
  const candidates = [...new Set([preferred, ...BACKEND_CANDIDATES].filter(Boolean))];
  const failures = [];
  for (const candidate of candidates) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);
      const response = await fetch(`${candidate}/api/health`, { signal: controller.signal });
      clearTimeout(timeout);
      if (response.ok) return candidate;
      failures.push(`${candidate}: HTTP ${response.status}`);
    } catch (error) {
      failures.push(`${candidate}: ${error.name === "AbortError" ? "连接超时" : error.message}`);
    }
  }
  throw new Error(`无法连接本机后端（${failures.join("；")}）。请确认发布器已在当前 Mac 上启动并重新加载扩展。`);
}

function sanitizeDetails(details) {
  const allowed = ["platform", "url", "reason_code", "message"];
  return Object.fromEntries(
    Object.entries(details).filter(([key]) => allowed.includes(key)).map(([key, value]) => [
      key,
      String(value).slice(0, 1000)
    ])
  );
}

async function recordBackgroundError(error) {
  await chrome.storage.local.set({ lastError: error.message || String(error) });
}
