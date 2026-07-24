const identity = document.getElementById("identity");
const pairForm = document.getElementById("pair-form");
const pairingCode = document.getElementById("pairing-code");
const polling = document.getElementById("polling");
const task = document.getElementById("task");
const message = document.getElementById("message");

pairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "正在配对…";
  const result = await chrome.runtime.sendMessage({
    type: "pair",
    pairingCode: pairingCode.value
  });
  render(result);
});

polling.addEventListener("change", async () => {
  const result = await chrome.runtime.sendMessage({
    type: "setPolling",
    enabled: polling.checked
  });
  render(result);
});

async function refresh() {
  render(await chrome.runtime.sendMessage({ type: "status" }));
}

function render(state) {
  if (!state.ok) {
    message.textContent = state.error || "扩展发生错误";
    return;
  }
  pairForm.hidden = state.paired;
  polling.disabled = !state.paired;
  polling.checked = Boolean(state.polling);
  identity.textContent = state.paired
    ? `${state.device.display_name} · Hubstudio ${state.device.container_code}`
    : "尚未配对";
  task.textContent = state.activeTask
    ? `当前任务：${state.activeTask.platform} · ${state.activeTask.id}`
    : "当前没有任务";
  message.textContent = state.lastError || "";
}

refresh();
