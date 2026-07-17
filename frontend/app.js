const state = {
  options: {
    models: [
      { id: "gpt-image-2", name: "gpt-image-2", aspect_ratios: ["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"], image_sizes: [], supports_quality: true },
      { id: "nano-banana-2", name: "nano-banana-2", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"], image_sizes: ["1K","2K","4K"], supports_quality: false },
      { id: "nano-banana-pro", name: "nano-banana-pro", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"], image_sizes: ["1K","2K","4K"], supports_quality: false },
      { id: "nano-banana-fast", name: "nano-banana-fast", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"], image_sizes: ["1K"], supports_quality: false },
      { id: "nano-banana", name: "nano-banana", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"], image_sizes: [], supports_quality: false },
    ],
    qualities: ["auto", "low", "medium", "high"],
  },
  prompts: [],
  openPromptIndexes: [0],
  currentJobId: null,
  pollTimer: null,
  pollInFlight: false,
  apiKeySet: false,
};

const el = {
  sourceDir: document.getElementById("source-dir"),
  outputDir: document.getElementById("output-dir"),
  model: document.getElementById("model"),
  pollInterval: document.getElementById("poll-interval"),
  aspectRatio: document.getElementById("aspect-ratio"),
  quality: document.getElementById("quality"),
  concurrency: document.getElementById("concurrency"),
  promptList: document.getElementById("prompt-list"),
  scanSummary: document.getElementById("scan-summary"),
  actionMessage: document.getElementById("action-message"),
  jobSummary: document.getElementById("job-summary"),
  statTotal: document.getElementById("stat-total"),
  statSuccess: document.getElementById("stat-success"),
  statFailed: document.getElementById("stat-failed"),
  statCancelled: document.getElementById("stat-cancelled"),
  statPending: document.getElementById("stat-pending"),
  progressFill: document.getElementById("progress-fill"),
  progressText: document.getElementById("progress-text"),
  taskTbody: document.getElementById("task-tbody"),
  btnStart: document.getElementById("btn-start"),
  btnCancel: document.getElementById("btn-cancel"),
  btnDelete: document.getElementById("btn-delete-job"),
  imageSize: document.getElementById("image-size"),
  settingsImageSize: document.getElementById("settings-image-size"),
  settingsModal: document.getElementById("settings-modal"),
  apiKey: document.getElementById("api-key"),
  apiKeyHint: document.getElementById("api-key-hint"),
  baseUrl: document.getElementById("base-url"),
  imageProxyUrl: document.getElementById("image-proxy-url"),
  settingsModel: document.getElementById("settings-model"),
  settingsModelCustom: document.getElementById("settings-model-custom"),
  settingsAspectRatio: document.getElementById("settings-aspect-ratio"),
  settingsQuality: document.getElementById("settings-quality"),
  settingsConcurrency: document.getElementById("settings-concurrency"),
  settingsPollInterval: document.getElementById("settings-poll-interval"),
  settingsPollTimeout: document.getElementById("settings-poll-timeout"),
  settingsSourceDir: document.getElementById("settings-source-dir"),
  settingsOutputDir: document.getElementById("settings-output-dir"),
  settingsMessage: document.getElementById("settings-message"),
};

function statusLabel(status) {
  const map = {
    pending: "排队中",
    uploading: "上传中",
    submitting: "提交中",
    processing: "生成中",
    downloading: "下载中",
    success: "成功",
    failed: "失败",
    cancelled: "已取消",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
  };
  return map[status] || status;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) ||
      `请求失败 (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}


function getModelInfo(modelId) {
  const models = state.options.models || [];
  return models.find((m) => m.id === modelId) || models[0] || { id: modelId, aspect_ratios: ["1:1"], image_sizes: [], supports_quality: true };
}

function updateModelDependentOptions(selectModelEl, selectRatioEl, selectSizeEl, currentRatio, currentSize, selectQualityEl) {
  const modelId = selectModelEl.value;
  const info = getModelInfo(modelId);
  const ratios = info.aspect_ratios || ["1:1"];
  const sizes = info.image_sizes || [];
  const supportsQuality = info.supports_quality !== false;

  // Show/hide quality dropdown based on model
  if (selectQualityEl) {
    if (supportsQuality) {
      selectQualityEl.closest(".field").style.display = "";
    } else {
      selectQualityEl.closest(".field").style.display = "none";
    }
  }

  // Fill aspect ratios
  selectRatioEl.innerHTML = "";
  ratios.forEach((r) => {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    selectRatioEl.appendChild(opt);
  });
  if (currentRatio && ratios.includes(currentRatio)) {
    selectRatioEl.value = currentRatio;
  } else {
    selectRatioEl.value = ratios[0] || "1:1";
  }

  // Fill image sizes
  selectSizeEl.innerHTML = "";
  if (sizes.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "\u4e0d\u9002\u7528";
    selectSizeEl.appendChild(opt);
    selectSizeEl.disabled = true;
  } else {
    selectSizeEl.disabled = false;
    sizes.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      selectSizeEl.appendChild(opt);
    });
    if (currentSize && sizes.includes(currentSize)) {
      selectSizeEl.value = currentSize;
    } else {
      selectSizeEl.value = sizes[0] || "";
    }
  }
}

function fillSelect(select, values) {
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    if (typeof value === "string") {
      option.value = value;
      option.textContent = value;
    } else {
      option.value = value.id;
      option.textContent = value.name;
    }
    select.appendChild(option);
  });
}

function defaultPrompts() {
  return [
    {
      prompt: "",
      enabled: true,
      source_dir: "",
      output_dir: "",
      extra_image_1: "",
      extra_image_2: "",
    },
  ];
}

function folderNameFromPrompt(prompt, maxLen = 40) {
  let text = String(prompt || "").replace(/\s+/g, " ").trim();
  const invalid = /[<>:"/\\|?*]/g;
  text = text.replace(invalid, "_").replace(/[. ]+$/g, "");
  if (!text) return "未命名";
  if (text.length > maxLen) {
    text = text.slice(0, maxLen).replace(/[. ]+$/g, "");
  }
  return text || "未命名";
}

function shortPath(path) {
  const text = String(path || "").trim();
  if (!text) return "";
  if (text.length <= 28) return text;
  return `${text.slice(0, 12)}...${text.slice(-12)}`;
}

function promptPreview(text) {
  const value = String(text || "").trim();
  return value || "点击填写提示词";
}

function renderPrompts() {
  el.promptList.innerHTML = "";
  state.prompts.forEach((item, index) => {
    const isOpen = state.openPromptIndexes.includes(index);
    const card = document.createElement("div");
    card.className = `prompt-item${isOpen ? " open active" : ""}${item.enabled ? "" : " disabled-card"}`;

    const folderName = folderNameFromPrompt(item.prompt);
    const title = (item.prompt || "").trim() ? folderName : `提示词${index + 1}`;
    const extraCount = [item.extra_image_1, item.extra_image_2].filter((v) => (v || "").trim()).length;
    const extraHint = extraCount ? `固定图 ${extraCount}` : "无固定图";

    card.innerHTML = `
      <button class="prompt-summary" type="button" data-role="toggle">
        <span class="prompt-index">${index + 1}</span>
        <span class="prompt-summary-main">
          <div class="prompt-title">${escapeHtml(title)}</div>
          <p class="prompt-preview">${escapeHtml(promptPreview(item.prompt))}</p>
          <div class="prompt-extra-hint">${escapeHtml(extraHint)}</div>
        </span>
        <span class="prompt-summary-side">
          <label class="prompt-toggle" data-role="enabled-wrap">
            <input type="checkbox" data-role="enabled" ${item.enabled ? "checked" : ""} />
            启用
          </label>
          <span class="prompt-chevron">${isOpen ? "收起" : "展开"}</span>
        </span>
      </button>
      <div class="prompt-paths" data-role="paths">
        <div class="prompt-path-row">
          <span>源图</span>
          <input data-role="source_dir" type="text" value="${escapeHtml(item.source_dir || "")}" placeholder="全局源图目录" />
          <button class="btn secondary icon-only" data-role="pick-source" type="button" title="选择源图目录">
            <i data-lucide="folder-open"></i>
          </button>
        </div>
        <div class="prompt-path-row">
          <span>输出</span>
          <input data-role="output_dir" type="text" value="${escapeHtml(item.output_dir || "")}" placeholder="全局输出/提示词" />
          <button class="btn secondary icon-only" data-role="pick-output" type="button" title="选择输出目录">
            <i data-lucide="folder-open"></i>
          </button>
        </div>
        <div class="prompt-path-row">
          <span>图一</span>
          <input data-role="extra_image_1" type="text" value="${escapeHtml(item.extra_image_1 || "")}" placeholder="可选固定参考图" />
          <button class="btn secondary icon-only" data-role="pick-extra-1" type="button" title="选择图一">
            <i data-lucide="image"></i>
          </button>
          <button class="btn ghost icon-only" data-role="clear-extra-1" type="button" title="清除图一">
            <i data-lucide="x"></i>
          </button>
        </div>
        <div class="prompt-path-row">
          <span>图二</span>
          <input data-role="extra_image_2" type="text" value="${escapeHtml(item.extra_image_2 || "")}" placeholder="可选固定参考图" />
          <button class="btn secondary icon-only" data-role="pick-extra-2" type="button" title="选择图二">
            <i data-lucide="image"></i>
          </button>
          <button class="btn ghost icon-only" data-role="clear-extra-2" type="button" title="清除图二">
            <i data-lucide="x"></i>
          </button>
        </div>
      </div>
      <div class="prompt-body">
        <textarea data-role="prompt" placeholder="输入提示词内容">${escapeHtml(item.prompt || "")}</textarea>
        <div class="prompt-body-actions">
          <button class="btn ghost icon-btn" data-role="remove" type="button">
            <i data-lucide="trash-2"></i>
            <span>删除</span>
          </button>
          <button class="btn secondary icon-btn" data-role="collapse" type="button">
            <i data-lucide="chevron-up"></i>
            <span>收起</span>
          </button>
        </div>
      </div>
    `;

    const stop = (event) => event.stopPropagation();

    card.querySelector('[data-role="toggle"]').addEventListener("click", (event) => {
      if (event.target.closest('[data-role="enabled-wrap"]')) return;
      if (state.openPromptIndexes.includes(index)) {
        state.openPromptIndexes = state.openPromptIndexes.filter((i) => i !== index);
      } else {
        state.openPromptIndexes = [...state.openPromptIndexes, index];
      }
      renderPrompts();
    });

    card.querySelector('[data-role="paths"]').addEventListener("click", stop);

    card.querySelector('[data-role="enabled"]').addEventListener("click", stop);
    card.querySelector('[data-role="enabled"]').addEventListener("change", (e) => {
      state.prompts[index].enabled = e.target.checked;
      renderPrompts();
    });

    const bindInput = (role, key, onInput) => {
      const node = card.querySelector(`[data-role="${role}"]`);
      if (!node) return;
      node.addEventListener("click", stop);
      node.addEventListener("input", (e) => {
        state.prompts[index][key] = e.target.value;
        if (onInput) onInput(e.target.value);
      });
    };

    const refreshExtraHint = () => {
      const count = [state.prompts[index].extra_image_1, state.prompts[index].extra_image_2]
        .filter((v) => (v || "").trim()).length;
      const hint = card.querySelector(".prompt-extra-hint");
      if (hint) hint.textContent = count ? `固定图 ${count}` : "无固定图";
    };

    bindInput("prompt", "prompt", (value) => {
      const folder = folderNameFromPrompt(value);
      card.querySelector(".prompt-title").textContent =
        (value || "").trim() ? folder : `提示词${index + 1}`;
      card.querySelector(".prompt-preview").textContent = promptPreview(value);
    });
    bindInput("source_dir", "source_dir");
    bindInput("output_dir", "output_dir");
    bindInput("extra_image_1", "extra_image_1", refreshExtraHint);
    bindInput("extra_image_2", "extra_image_2", refreshExtraHint);

    card.querySelector('[data-role="pick-source"]').addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const path = await pickFolder("选择源图片文件夹", state.prompts[index].source_dir || el.sourceDir.value);
        if (!path) return;
        state.prompts[index].source_dir = path;
        card.querySelector('[data-role="source_dir"]').value = path;
      } catch (err) {
        setActionMessage(err.message || String(err), true);
      }
    });

    card.querySelector('[data-role="pick-output"]').addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const path = await pickFolder("选择输出文件夹", state.prompts[index].output_dir || el.outputDir.value);
        if (!path) return;
        state.prompts[index].output_dir = path;
        card.querySelector('[data-role="output_dir"]').value = path;
      } catch (err) {
        setActionMessage(err.message || String(err), true);
      }
    });

    card.querySelector('[data-role="pick-extra-1"]').addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const path = await pickFile("选择图一", state.prompts[index].extra_image_1 || state.prompts[index].source_dir || el.sourceDir.value);
        if (!path) return;
        state.prompts[index].extra_image_1 = path;
        card.querySelector('[data-role="extra_image_1"]').value = path;
        refreshExtraHint();
      } catch (err) {
        setActionMessage(err.message || String(err), true);
      }
    });

    card.querySelector('[data-role="pick-extra-2"]').addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const path = await pickFile("选择图二", state.prompts[index].extra_image_2 || state.prompts[index].source_dir || el.sourceDir.value);
        if (!path) return;
        state.prompts[index].extra_image_2 = path;
        card.querySelector('[data-role="extra_image_2"]').value = path;
        refreshExtraHint();
      } catch (err) {
        setActionMessage(err.message || String(err), true);
      }
    });

    card.querySelector('[data-role="clear-extra-1"]').addEventListener("click", (event) => {
      event.stopPropagation();
      state.prompts[index].extra_image_1 = "";
      card.querySelector('[data-role="extra_image_1"]').value = "";
      refreshExtraHint();
    });

    card.querySelector('[data-role="clear-extra-2"]').addEventListener("click", (event) => {
      event.stopPropagation();
      state.prompts[index].extra_image_2 = "";
      card.querySelector('[data-role="extra_image_2"]').value = "";
      refreshExtraHint();
    });

    card.querySelector('[data-role="remove"]').addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.prompts.length <= 1) {
        setActionMessage("至少保留 1 个提示词", true);
        return;
      }
      state.prompts.splice(index, 1);
      state.openPromptIndexes = state.openPromptIndexes
        .filter((i) => i !== index)
        .map((i) => (i > index ? i - 1 : i));
      renderPrompts();
    });

    card.querySelector('[data-role="collapse"]').addEventListener("click", (event) => {
      event.stopPropagation();
      state.openPromptIndexes = state.openPromptIndexes.filter((i) => i !== index);
      renderPrompts();
    });

    el.promptList.appendChild(card);
  });

  if (typeof window.refreshIcons === "function") {
    window.refreshIcons();
  }
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setActionMessage(message, isError = false) {
  el.actionMessage.textContent = message || "";
  el.actionMessage.style.color = isError ? "#b42318" : "";
}

function setSettingsMessage(message, isError = false) {
  el.settingsMessage.textContent = message || "";
  el.settingsMessage.style.color = isError ? "#b42318" : "";
}

function openSettings() {
  el.settingsModal.classList.remove("hidden");
  el.settingsModal.setAttribute("aria-hidden", "false");
}

function closeSettings() {
  el.settingsModal.classList.add("hidden");
  el.settingsModal.setAttribute("aria-hidden", "true");
}

async function loadOptions() {
  try {
    const res = await api("/api/options");
    if (res.data && res.data.models) {
      state.options.models = res.data.models;
    }
  } catch (_) {
    // keep defaults
  }

  fillSelect(el.model, state.options.models);
  fillSelect(el.settingsModel, state.options.models);
  fillSelect(el.quality, state.options.qualities);
  fillSelect(el.settingsQuality, state.options.qualities);

  // Trigger model-dependent dropdown population
  if (el.model && el.aspectRatio) {
    updateModelDependentOptions(el.model, el.aspectRatio, el.imageSize, null, null, el.quality);
  }
  if (el.settingsModel && el.settingsAspectRatio) {
    updateModelDependentOptions(el.settingsModel, el.settingsAspectRatio, el.settingsImageSize, null, null, el.settingsQuality);
  }
}

function selectedModel() {
  return (el.model?.value || "gpt-image-2").trim();
}

function selectedSettingsModel() {
  const custom = (el.settingsModelCustom?.value || "").trim();
  if (custom) return custom;
  return (el.settingsModel?.value || "gpt-image-2").trim();
}

function applyModelValue(selectEl, customEl, value) {
  const model = (value || "gpt-image-2").trim();
  const options = Array.from(selectEl.options).map((opt) => opt.value);
  if (options.includes(model)) {
    selectEl.value = model;
    if (customEl) customEl.value = "";
  } else if (model) {
    if (options.length) selectEl.value = options[0];
    if (customEl) customEl.value = model;
  } else if (options.length) {
    selectEl.value = options[0];
    if (customEl) customEl.value = "";
  }
}

function bindModelSelectors() {
  if (el.model) {
    el.model.addEventListener("change", () => {
      const ratio = el.aspectRatio.value;
      const size = el.imageSize ? el.imageSize.value : "";
      updateModelDependentOptions(el.model, el.aspectRatio, el.imageSize, ratio, size, el.quality);
    });
  }
  if (el.settingsModel) {
    el.settingsModel.addEventListener("change", () => {
      if (el.settingsModelCustom) el.settingsModelCustom.value = "";
      const ratio = el.settingsAspectRatio.value;
      const size = el.settingsImageSize ? el.settingsImageSize.value : "";
      updateModelDependentOptions(el.settingsModel, el.settingsAspectRatio, el.settingsImageSize, ratio, size, el.settingsQuality);
    });
  }
  if (el.settingsModelCustom) {
    el.settingsModelCustom.addEventListener("input", () => {
      // custom model: keep ratios/sizes as-is or reset to defaults
    });
  }
}

async function loadSettings() {
  const res = await api("/api/settings");
  const s = res.data || {};

  state.apiKeySet = !!s.api_key_set;
  el.apiKey.value = "";
  if (el.apiKeyHint) {
    el.apiKeyHint.textContent = s.api_key_set
      ? `已保存：${s.api_key_masked || "****"}（留空不修改）`
      : "尚未配置 API Key";
  }
  el.baseUrl.value = s.base_url || "https://noova.cn";
  if (el.imageProxyUrl) el.imageProxyUrl.value = s.image_proxy_url || "";
  applyModelValue(el.settingsModel, el.settingsModelCustom, s.model || "gpt-image-2");
  el.settingsConcurrency.value = s.concurrency || 10;
  el.settingsPollInterval.value = s.poll_interval_sec || 20;
  el.settingsPollTimeout.value = s.poll_timeout_sec || 300;
  el.settingsSourceDir.value = s.source_dir || "";
  el.settingsOutputDir.value = s.output_dir || "";

  el.sourceDir.value = s.source_dir || "";
  el.outputDir.value = s.output_dir || "";

  // Set model and trigger dependent option population
  const savedModel = s.model || "gpt-image-2";
  if (el.model) {
    const options = Array.from(el.model.options).map((opt) => opt.value);
    el.model.value = options.includes(savedModel) ? savedModel : (options[0] || "gpt-image-2");
  }
  if (el.aspectRatio && el.imageSize) {
    updateModelDependentOptions(el.model, el.aspectRatio, el.imageSize, s.aspect_ratio || "9:16", s.image_size || "", el.quality);
  }
  if (el.settingsModel && el.settingsAspectRatio && el.settingsImageSize) {
    updateModelDependentOptions(el.settingsModel, el.settingsAspectRatio, el.settingsImageSize, s.aspect_ratio || "9:16", s.image_size || "", el.settingsQuality);
  }
  el.settingsQuality.value = s.quality || "auto";
  el.quality.value = s.quality || "auto";
  el.concurrency.value = s.concurrency || 10;
  if (el.pollInterval) el.pollInterval.value = s.poll_interval_sec || 20;
}

async function saveSettings() {
  const body = {
    base_url: el.baseUrl.value.trim() || "https://noova.cn",
    image_proxy_url: el.imageProxyUrl ? el.imageProxyUrl.value.trim() : "",
    model: selectedSettingsModel(),
    aspect_ratio: el.settingsAspectRatio.value,
    quality: el.settingsQuality.value,
    image_size: el.settingsImageSize ? el.settingsImageSize.value : "",
    concurrency: Number(el.settingsConcurrency.value || 10),
    poll_interval_sec: Number(el.settingsPollInterval.value || 20),
    poll_timeout_sec: Number(el.settingsPollTimeout.value || 300),
    source_dir: el.settingsSourceDir.value.trim(),
    output_dir: el.settingsOutputDir.value.trim(),
  };
  const apiKey = el.apiKey.value.trim();
  if (apiKey) body.api_key = apiKey;

  const res = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });

  setSettingsMessage(res.message || "设置已保存");
  await loadSettings();
}

async function testConnection() {
  await saveSettings();
  const res = await api("/api/test-connection", { method: "POST", body: "{}" });
  setSettingsMessage(res.message || "连接成功");
}


async function pickFolder(title = "选择文件夹", initial = "") {
  const startRes = await api("/api/folder/pick", {
    method: "POST",
    body: JSON.stringify({
      title,
      initial: (initial || "").trim(),
    }),
  });
  const sessionId = startRes.data.session_id;
  for (let i = 0; i < 240; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const res = await api(`/api/pick/${sessionId}`);
    const data = res.data || {};
    if (!data.done) continue;
    if (data.cancelled || !data.path) return "";
    return data.path;
  }
  throw new Error("选择文件夹超时");
}

async function pickFile(title = "选择图片", initial = "") {
  const startRes = await api("/api/file/pick", {
    method: "POST",
    body: JSON.stringify({
      title,
      initial: (initial || "").trim(),
    }),
  });
  const sessionId = startRes.data.session_id;
  for (let i = 0; i < 240; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const res = await api(`/api/pick/${sessionId}`);
    const data = res.data || {};
    if (!data.done) continue;
    if (data.cancelled || !data.path) return "";
    return data.path;
  }
  throw new Error("选择图片超时");
}

async function scanFolder() {
  const path = el.sourceDir.value.trim();
  if (!path) {
    setActionMessage("请先填写源图片文件夹", true);
    return;
  }
  const res = await api("/api/folder/scan", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  const data = res.data || {};
  const names = (data.files || []).slice(0, 8).join("、");
  el.scanSummary.textContent = `找到 ${data.count || 0} 张图片${names ? `：${names}` : ""}${
    data.truncated ? " ..." : ""
  }`;
  setActionMessage(`扫描完成，共 ${data.count || 0} 张图片`);
}

function collectPrompts() {
  return state.prompts
    .map((item) => ({
      prompt: (item.prompt || "").trim(),
      enabled: !!item.enabled,
      source_dir: (item.source_dir || "").trim(),
      output_dir: (item.output_dir || "").trim(),
      extra_image_1: (item.extra_image_1 || "").trim(),
      extra_image_2: (item.extra_image_2 || "").trim(),
    }))
    .filter((item) => item.enabled && item.prompt);
}

async function startJob() {
  const prompts = collectPrompts();
  if (!prompts.length) {
    setActionMessage("请至少填写 1 个有效提示词内容", true);
    return;
  }
  if (prompts.length > 10) {
    setActionMessage("最多支持 10 个提示词", true);
    return;
  }

  const body = {
    source_dir: el.sourceDir.value.trim(),
    output_dir: el.outputDir.value.trim(),
    model: selectedModel(),
    aspect_ratio: el.aspectRatio.value,
    quality: el.quality.value,
    image_size: el.imageSize ? el.imageSize.value : "",
    concurrency: Number(el.concurrency.value || 10),
    poll_interval_sec: Number(el.pollInterval?.value || 20),
    prompts,
  };

  el.btnStart.disabled = true;
  try {
    const res = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const job = res.data;
    state.currentJobId = job.id;
    setActionMessage(`任务已创建：${job.id}`);
    renderJob(job);
    startPolling();
  } catch (err) {
    setActionMessage(err.message || String(err), true);
  } finally {
    el.btnStart.disabled = false;
  }
}

async function cancelJob() {
  if (!state.currentJobId) return;
  const res = await api(`/api/jobs/${state.currentJobId}/cancel`, {
    method: "POST",
    body: "{}",
  });
  setActionMessage(res.message || "已请求取消");
  if (res.data) renderJob(res.data);
}

async function deleteJob() {
  if (!state.currentJobId) return;
  const res = await api(`/api/jobs/${state.currentJobId}`, { method: "DELETE" });
  setActionMessage(res.message || "任务已删除");
  state.currentJobId = null;
  stopPolling();
  el.jobSummary.textContent = "";
  el.statTotal.textContent = "0";
  el.statSuccess.textContent = "0";
  el.statFailed.textContent = "0";
  if (el.statCancelled) el.statCancelled.textContent = "0";
  el.statPending.textContent = "0";
  el.progressFill.style.width = "0%";
  el.progressText.textContent = "0%";
  el.taskTbody.innerHTML = '<tr><td colspan="5" class="empty">创建任务后显示每张图的处理状态</td></tr>';
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    refreshJob().catch((err) => {
      setActionMessage(err.message || String(err), true);
    });
  }, 1000);
  refreshJob().catch((err) => {
    setActionMessage(err.message || String(err), true);
  });
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  state.pollInFlight = false;
}

async function refreshJob() {
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    if (!state.currentJobId) {
      const list = await api("/api/jobs");
      const jobs = list.data || [];
      if (!jobs.length) return;
      state.currentJobId = jobs[0].id;
    }

    const res = await api(`/api/jobs/${state.currentJobId}`);
    renderJob(res.data);

    if (["completed", "failed", "cancelled"].includes(res.data.status)) {
      stopPolling();
      el.btnCancel.disabled = true;
      if (el.btnDelete) el.btnDelete.disabled = false;
    } else {
      el.btnCancel.disabled = false;
    }
  } finally {
    state.pollInFlight = false;
  }
}

function renderJob(job) {
  if (!job) return;
  state.currentJobId = job.id;

  const statusBreakdown = [
    job.success > 0 ? `成功${job.success}` : "",
    job.pending > 0 ? `等待${job.pending}` : "",
    job.failed > 0 ? `失败${job.failed}` : "",
    job.cancelled > 0 ? `取消${job.cancelled}` : "",
  ].filter(Boolean).join("/");
  el.jobSummary.textContent = `任务 ${job.id.slice(0, 8)} · ${statusLabel(job.status)}${statusBreakdown ? " [" + statusBreakdown + "]" : ""} · ${job.message || ""}`;
  el.statTotal.textContent = job.total || 0;
  el.statSuccess.textContent = job.success || 0;
  el.statFailed.textContent = job.failed || 0;
  if (el.statCancelled) el.statCancelled.textContent = job.cancelled || 0;
  el.statPending.textContent = job.pending || 0;

  const done = (job.success || 0) + (job.failed || 0) + (job.cancelled || 0);
  const percent = job.total ? Math.round((done / job.total) * 100) : 0;
  el.progressFill.style.width = `${percent}%`;
  el.progressText.textContent = `${percent}%`;

  const tasks = job.tasks || [];
  if (!tasks.length) {
    el.taskTbody.innerHTML =
      '<tr><td colspan="5" class="empty">暂无子任务</td></tr>';
    return;
  }

  el.taskTbody.innerHTML = tasks
    .map((task) => {
      const errMsg = (task.status === "failed" && task.message) ? `<div class="task-error">${escapeHtml(task.message)}</div>` : "";
      const pathDisplay = task.output_path || task.result_url || "-";
      const isCompleted = ["success", "failed", "cancelled"].includes(task.status);
      const isActive = !isCompleted;
      return `
        <tr class="${isActive ? "task-active" : ""}">
          <td>${escapeHtml(task.image_name || "")}</td>
          <td><span title="${escapeHtml(task.prompt || "")}">${escapeHtml(task.prompt_name || "")}</span></td>
          <td><span class="status ${escapeHtml(task.status)}">${escapeHtml(
            statusLabel(task.status)
          )}</span></td>
          <td>
            <span class="task-msg">${escapeHtml(task.message || "")}</span>
            ${errMsg}
          </td>
          <td class="path-cell">${escapeHtml(pathDisplay)}</td>
        </tr>
      `;
    })
    .join("");

  el.btnCancel.disabled = !["queued", "running"].includes(job.status);
  if (el.btnDelete) el.btnDelete.disabled = ["queued", "running"].includes(job.status);
}

function bindEvents() {
  document.getElementById("btn-open-settings").addEventListener("click", openSettings);
  document.getElementById("btn-close-settings").addEventListener("click", closeSettings);
  document.getElementById("btn-save-settings").addEventListener("click", async () => {
    try {
      await saveSettings();
    } catch (err) {
      setSettingsMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-test-connection").addEventListener("click", async () => {
    try {
      await testConnection();
    } catch (err) {
      setSettingsMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-pick-source").addEventListener("click", async () => {
    try {
      const path = await pickFolder("选择源图片文件夹", el.sourceDir.value);
      if (!path) return;
      el.sourceDir.value = path;
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-pick-output").addEventListener("click", async () => {
    try {
      const path = await pickFolder("选择输出文件夹", el.outputDir.value);
      if (!path) return;
      el.outputDir.value = path;
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-pick-settings-source").addEventListener("click", async () => {
    try {
      const path = await pickFolder("选择默认源文件夹", el.settingsSourceDir.value);
      if (!path) return;
      el.settingsSourceDir.value = path;
    } catch (err) {
      setSettingsMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-pick-settings-output").addEventListener("click", async () => {
    try {
      const path = await pickFolder("选择默认输出文件夹", el.settingsOutputDir.value);
      if (!path) return;
      el.settingsOutputDir.value = path;
    } catch (err) {
      setSettingsMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-scan").addEventListener("click", async () => {
    try {
      await scanFolder();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-expand-prompts").addEventListener("click", () => {
    state.openPromptIndexes = state.prompts.map((_, index) => index);
    renderPrompts();
  });
  document.getElementById("btn-collapse-prompts").addEventListener("click", () => {
    state.openPromptIndexes = [];
    renderPrompts();
  });
  document.getElementById("btn-add-prompt").addEventListener("click", () => {
    if (state.prompts.length >= 10) {
      setActionMessage("最多 10 个提示词", true);
      return;
    }
    state.prompts.push({
      prompt: "",
      enabled: true,
      source_dir: "",
      output_dir: "",
      extra_image_1: "",
      extra_image_2: "",
    });
    state.openPromptIndexes = [state.prompts.length - 1];
    renderPrompts();
  });
  el.btnStart.addEventListener("click", startJob);
  el.btnCancel.addEventListener("click", async () => {
    try {
      await cancelJob();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-delete-job").addEventListener("click", async () => {
    try {
      await deleteJob();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-refresh-job").addEventListener("click", async () => {
    try {
      await refreshJob();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });

  el.settingsModal.addEventListener("click", (e) => {
    if (e.target === el.settingsModal) closeSettings();
  });
  window.addEventListener("beforeunload", () => {
    stopPolling();
  });
}

async function init() {
  bindEvents();
  bindModelSelectors();
  state.prompts = defaultPrompts();
  renderPrompts();
  if (typeof window.refreshIcons === "function") {
    window.refreshIcons();
  }
  await loadOptions();
  try {
    await loadSettings();
  } catch (err) {
    setActionMessage(`加载设置失败: ${err.message || err}`, true);
  }
  if (typeof window.refreshIcons === "function") {
    window.refreshIcons();
  }
}

init();
