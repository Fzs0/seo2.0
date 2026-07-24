chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "taskReady") return;
  showTaskPanel(message.task);
  sendResponse({ ok: true });
});

function showTaskPanel(task) {
  document.getElementById("exdivo-extension-task-panel")?.remove();
  const panel = document.createElement("aside");
  panel.id = "exdivo-extension-task-panel";
  panel.style.cssText = [
    "position:fixed", "right:16px", "bottom:16px", "z-index:2147483647",
    "width:340px", "max-height:70vh", "overflow:auto", "padding:16px",
    "border-radius:12px", "background:#171717", "color:#fff",
    "font:14px/1.45 system-ui,sans-serif", "box-shadow:0 12px 40px #0008"
  ].join(";");

  const title = document.createElement("strong");
  title.textContent = `Exdivo · ${task.platform}`;
  const description = document.createElement("p");
  description.textContent = task.title || task.body.slice(0, 240);
  description.style.whiteSpace = "pre-wrap";
  const note = document.createElement("p");
  note.textContent = "任务已安全送达当前页面。平台回填适配器将在下一阶段启用；此版本不会自动点击发布。";
  note.style.color = "#f4c95d";
  const acknowledge = document.createElement("button");
  acknowledge.textContent = "确认收到";
  acknowledge.style.cssText = "padding:8px 12px;border:0;border-radius:8px;cursor:pointer";
  acknowledge.addEventListener("click", async () => {
    const response = await chrome.runtime.sendMessage({
      type: "reportStage",
      jobId: task.id,
      sequenceNo: 2,
      stage: "received",
      details: { platform: task.platform, url: location.href }
    });
    note.textContent = response.ok ? "已向后端确认任务送达。" : response.error;
  });
  panel.append(title, description, note, acknowledge);
  document.documentElement.append(panel);
}
