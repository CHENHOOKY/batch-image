const state = {
  options: {
    models: [
      { id: "gpt-image-2", name: "gpt-image-2", aspect_ratios: ["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"], image_sizes: [], supports_quality: true },
      { id: "gpt-image-2-G", name: "gpt-image-2-G", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"], image_sizes: ["1K"], supports_quality: true, poll_timeout_sec: 960 },
      { id: "nano-banana-2", name: "nano-banana-2", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"], image_sizes: ["1K","2K","4K"], supports_quality: false },
      { id: "nano-banana-pro", name: "nano-banana-pro", aspect_ratios: ["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"], image_sizes: ["1K","2K","4K"], supports_quality: false },
    ],
    qualities: ["auto", "low", "medium", "high"],
  },
  prompts: [],
  openPromptIndexes: [0],
  currentJobId: null,
  pollTimer: null,
  pollInFlight: false,
  lastTaskRenderSignature: "",
  lastFocusedElement: null,
  apiKeySet: false,
  lastTasks: [],
  resultFilter: "all",
  lastJobSig: "",
  autoRetryFailed: false,
  maxRetryCount: 0,
  currentScheduleId: null,
  scheduleTimer: null,
  schedulePollInFlight: false,
};

const THEME_STORAGE_KEY = "batch-image-theme";
const DEFAULT_BASE_URL = "https://noova.cn";
const DEFAULT_IMAGE_PROXY_URL = "https://imageproxy.zhongzhuan.chat/api/upload";

const RESULT_FILTERS = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "success", label: "成功" },
  { key: "failed", label: "失败" },
  { key: "cancelled", label: "取消" },
];

const ACTIVE_STATUSES = ["pending", "uploading", "submitting", "processing", "downloading"];

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
  resultsGrid: document.getElementById("results-grid"),
  resultsCount: document.getElementById("results-count"),
  filterChips: document.getElementById("filter-chips"),
  taskTbody: document.getElementById("task-tbody"),
  btnStart: document.getElementById("btn-start"),
  btnCancel: document.getElementById("btn-cancel"),
  btnRetry: document.getElementById("btn-retry-failed"),
  btnDelete: document.getElementById("btn-delete-job"),
  scheduleEnabled: document.getElementById("schedule-enabled"),
  scheduleRunAt: document.getElementById("schedule-run-at"),
  btnSaveSchedule: document.getElementById("btn-save-schedule"),
  btnCancelSchedule: document.getElementById("btn-cancel-schedule"),
  scheduleMessage: document.getElementById("schedule-message"),
  btnTheme: document.getElementById("btn-theme"),
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
  settingsAutoRetryFailed: document.getElementById("settings-auto-retry-failed"),
  settingsMaxRetryCount: document.getElementById("settings-max-retry-count"),
  settingsSourceDir: document.getElementById("settings-source-dir"),
  settingsOutputDir: document.getElementById("settings-output-dir"),
  settingsMessage: document.getElementById("settings-message"),
  creditBadge: document.getElementById("credit-badge"),
  creditValue: document.getElementById("credit-value"),
  btnRefreshCredit: document.getElementById("btn-refresh-credit"),
  lightbox: document.getElementById("lightbox"),
  lightboxImage: document.getElementById("lightbox-image"),
  lightboxCaption: document.getElementById("lightbox-caption"),
  btnCloseLightbox: document.getElementById("btn-close-lightbox"),
};

/* ---------- Theme ---------- */

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (_) {
    // storage unavailable; theme still applies for this session
  }
}

function toggleTheme() {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
}

/* ---------- Helpers ---------- */

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

function scheduleStatusLabel(status) {
  const map = {
    pending: "待执行",
    waiting: "等待空闲",
    submitting: "提交中",
    submitted: "已提交",
    cancelled: "已取消",
    failed: "失败",
  };
  return map[status] || status || "未设置";
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function toDateTimeLocalValue(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function scheduleTimeToInputValue(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return text.includes("T") ? text.slice(0, 19) : text.replace(" ", "T").slice(0, 19);
}

function ensureDefaultScheduleTime() {
  if (!el.scheduleRunAt || el.scheduleRunAt.value) return;
  el.scheduleRunAt.value = toDateTimeLocalValue(new Date(Date.now() + 60 * 1000));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Auth-Token": window.__APP_TOKEN__ || "",
      ...(options.headers || {}),
    },
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("访问令牌已失效，请刷新页面（服务重启后令牌会更新）");
    }
    const detail =
      (payload ? (payload.detail || payload.message) : null) ||
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
    opt.textContent = "不适用";
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

function safeStatusClass(status) {
  const value = String(status || "").trim();
  return /^[a-z0-9_-]+$/i.test(value) ? value : "unknown";
}

function isHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "http:" || url.protocol === "https:";
  } catch (_) {
    return false;
  }
}

function applyModelTimeoutHint(modelId, timeoutInput) {
  if (!timeoutInput) return;
  const modelTimeout = Number(getModelInfo(modelId).poll_timeout_sec || 0);
  timeoutInput.min = String(modelTimeout || 30);
  if (!modelTimeout) return;
  const currentTimeout = Number(timeoutInput.value || 0);
  if (currentTimeout < modelTimeout) {
    timeoutInput.value = modelTimeout;
  }
}

function appendTextNode(parent, tag, className, text, title = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  if (title) node.title = title;
  parent.appendChild(node);
  return node;
}

/* ---------- Lightbox ---------- */

function openLightbox(src, caption) {
  if (!el.lightbox || !el.lightboxImage) return;
  state.lastFocusedElement = document.activeElement;
  el.lightboxImage.src = src;
  if (el.lightboxCaption) el.lightboxCaption.textContent = caption || "";
  el.lightbox.classList.remove("hidden");
  el.lightbox.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => el.btnCloseLightbox?.focus());
}

function closeLightbox() {
  if (!el.lightbox) return;
  el.lightbox.classList.add("hidden");
  el.lightbox.setAttribute("aria-hidden", "true");
  if (el.lightboxImage) el.lightboxImage.src = "";
  if (state.lastFocusedElement instanceof HTMLElement) {
    state.lastFocusedElement.focus();
  }
  state.lastFocusedElement = null;
}

/* ---------- Result filter ---------- */

function taskMatchesFilter(task, filterKey) {
  if (filterKey === "all") return true;
  if (filterKey === "active") return ACTIVE_STATUSES.includes(task.status);
  return task.status === filterKey;
}

function filterCount(tasks, filterKey) {
  return tasks.filter((task) => taskMatchesFilter(task, filterKey)).length;
}

function renderFilterChips(tasks) {
  if (!el.filterChips) return;
  el.filterChips.replaceChildren();
  if (!tasks.length) return;

  RESULT_FILTERS.forEach((filter) => {
    const count = filterCount(tasks, filter.key);
    if (filter.key !== "all" && count === 0 && state.resultFilter !== filter.key) return;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip${state.resultFilter === filter.key ? " active" : ""}`;
    chip.setAttribute("aria-pressed", state.resultFilter === filter.key ? "true" : "false");

    const label = document.createElement("span");
    label.textContent = filter.label;
    chip.appendChild(label);

    const countNode = document.createElement("span");
    countNode.className = "chip-count";
    countNode.textContent = String(count);
    chip.appendChild(countNode);

    chip.addEventListener("click", () => {
      state.resultFilter = filter.key;
      renderTaskViews(state.lastTasks, true);
    });
    el.filterChips.appendChild(chip);
  });
}

/* ---------- Result rendering ---------- */

function renderResultCards(tasks) {
  if (!el.resultsGrid) return;
  el.resultsGrid.replaceChildren();

  if (!tasks.length) {
    const isFiltered = state.resultFilter !== "all" && state.lastTasks.length > 0;
    appendTextNode(
      el.resultsGrid,
      "div",
      "results-empty",
      isFiltered
        ? "当前筛选条件下没有结果，点击“全部”查看所有任务。"
        : "创建任务后，这里会按图片展示处理结果。"
    );
    return;
  }

  tasks.forEach((task) => {
    const statusClass = safeStatusClass(task.status);
    const output = task.output_path || task.result_url || "尚未生成";

    const card = document.createElement("article");
    card.className = `result-card ${statusClass}`;

    const head = document.createElement("div");
    head.className = "result-card-head";
    appendTextNode(head, "span", "result-name", task.image_name || "未命名图片", task.image_name || "");

    const status = document.createElement("span");
    status.className = `status ${statusClass}`;
    status.textContent = statusLabel(task.status);
    head.appendChild(status);
    card.appendChild(head);

    appendTextNode(
      card,
      "p",
      "result-prompt",
      task.prompt || task.prompt_name || "未填写提示词",
      task.prompt || ""
    );
    appendTextNode(card, "div", "result-message", task.message || "等待处理");

    if (task.result_url && isHttpUrl(task.result_url)) {
      const preview = document.createElement("img");
      preview.className = "result-preview";
      preview.src = task.result_url;
      preview.alt = `${task.image_name || "生成结果"} 预览`;
      preview.loading = "lazy";
      preview.referrerPolicy = "no-referrer";
      preview.title = "点击放大预览";
      preview.addEventListener("error", () => preview.remove());
      preview.addEventListener("click", () => {
        openLightbox(task.result_url, `${task.image_name || ""}${task.prompt ? " · " + task.prompt : ""}`);
      });
      card.appendChild(preview);
    }

    const outputWrap = document.createElement("div");
    outputWrap.className = "result-output";
    appendTextNode(outputWrap, "span", "result-output-label", "输出");
    if (task.result_url && isHttpUrl(task.result_url)) {
      const link = document.createElement("a");
      link.className = "result-link";
      link.href = task.result_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "打开图片";
      outputWrap.appendChild(link);
    }
    appendTextNode(outputWrap, "span", "result-path", output, output);
    card.appendChild(outputWrap);
    el.resultsGrid.appendChild(card);
  });
}

function renderTaskTable(tasks) {
  if (!tasks.length) {
    const isFiltered = state.resultFilter !== "all" && state.lastTasks.length > 0;
    el.taskTbody.innerHTML = `<tr><td colspan="5" class="empty">${
      isFiltered ? "当前筛选条件下没有子任务" : "暂无子任务"
    }</td></tr>`;
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
          <td><span class="task-image-name" title="${escapeHtml(task.image_name || "")}">${escapeHtml(task.image_name || "")}</span></td>
          <td><span class="task-prompt" title="${escapeHtml(task.prompt || "")}">${escapeHtml(task.prompt_name || "")}</span></td>
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
}

function renderTaskViews(tasks, force = false) {
  state.lastTasks = tasks;
  const filtered = tasks.filter((task) => taskMatchesFilter(task, state.resultFilter));
  if (el.resultsCount) {
    el.resultsCount.textContent =
      state.resultFilter === "all"
        ? `${tasks.length} 项`
        : `${filtered.length} / ${tasks.length} 项`;
  }
  renderFilterChips(tasks);
  renderResultCards(filtered);
  renderTaskTable(filtered);
  if (force) {
    state.lastTaskRenderSignature = "";
  }
}

function setActionMessage(message, isError = false) {
  el.actionMessage.textContent = message || "";
  el.actionMessage.classList.toggle("error", !!isError);
}

function setSettingsMessage(message, isError = false) {
  el.settingsMessage.textContent = message || "";
  el.settingsMessage.classList.toggle("error", !!isError);
}

function setScheduleMessage(message, isError = false) {
  if (!el.scheduleMessage) return;
  el.scheduleMessage.textContent = message || "";
  el.scheduleMessage.classList.toggle("error", !!isError);
}

function openSettings() {
  state.lastFocusedElement = document.activeElement;
  el.settingsModal.classList.remove("hidden");
  el.settingsModal.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => document.getElementById("btn-close-settings")?.focus());
}

function closeSettings() {
  el.settingsModal.classList.add("hidden");
  el.settingsModal.setAttribute("aria-hidden", "true");
  if (state.lastFocusedElement instanceof HTMLElement) {
    state.lastFocusedElement.focus();
  }
  state.lastFocusedElement = null;
}

async function loadOptions() {
  try {
    const res = await api("/api/options");
    if (res.data && Array.isArray(res.data.models) && res.data.models.length) {
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

function syncRetrySettingsControls() {
  if (!el.settingsAutoRetryFailed || !el.settingsMaxRetryCount) return;
  const enabled = !!el.settingsAutoRetryFailed.checked;
  el.settingsMaxRetryCount.disabled = !enabled;
  if (enabled && Number(el.settingsMaxRetryCount.value || 0) < 1) {
    el.settingsMaxRetryCount.value = 1;
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
      applyModelTimeoutHint(el.settingsModel.value, el.settingsPollTimeout);
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
  el.baseUrl.value = s.base_url || DEFAULT_BASE_URL;
  if (el.imageProxyUrl) el.imageProxyUrl.value = s.image_proxy_url || DEFAULT_IMAGE_PROXY_URL;
  applyModelValue(el.settingsModel, el.settingsModelCustom, s.model || "gpt-image-2");
  el.settingsConcurrency.value = s.concurrency || 10;
  el.settingsPollInterval.value = s.poll_interval_sec || 20;
  el.settingsPollTimeout.value = s.poll_timeout_sec || 300;
  state.autoRetryFailed = !!s.auto_retry_failed;
  state.maxRetryCount = Math.max(0, Math.min(Number(s.max_retry_count || 0), 10));
  if (el.settingsAutoRetryFailed) el.settingsAutoRetryFailed.checked = state.autoRetryFailed;
  if (el.settingsMaxRetryCount) el.settingsMaxRetryCount.value = state.maxRetryCount;
  syncRetrySettingsControls();
  applyModelTimeoutHint(selectedSettingsModel(), el.settingsPollTimeout);
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
  const model = selectedSettingsModel();
  const modelTimeout = Number(getModelInfo(model).poll_timeout_sec || 0);
  const pollTimeout = Math.max(Number(el.settingsPollTimeout.value || 300), modelTimeout);
  const body = {
    base_url: el.baseUrl.value.trim() || DEFAULT_BASE_URL,
    image_proxy_url: el.imageProxyUrl ? (el.imageProxyUrl.value.trim() || DEFAULT_IMAGE_PROXY_URL) : DEFAULT_IMAGE_PROXY_URL,
    model,
    aspect_ratio: el.settingsAspectRatio.value,
    quality: el.settingsQuality.value,
    image_size: el.settingsImageSize ? el.settingsImageSize.value : "",
    concurrency: Number(el.settingsConcurrency.value || 10),
    poll_interval_sec: Number(el.settingsPollInterval.value || 20),
    poll_timeout_sec: pollTimeout,
    auto_retry_failed: !!el.settingsAutoRetryFailed?.checked,
    max_retry_count: Math.max(0, Math.min(Number(el.settingsMaxRetryCount?.value || 0), 10)),
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

async function restoreLatestJob() {
  const list = await api("/api/jobs");
  const jobs = list.data || [];
  if (!jobs.length) return;

  state.currentJobId = jobs[0].id;
  const res = await api(`/api/jobs/${state.currentJobId}`);
  renderJob(res.data);
  if (["queued", "running"].includes(res.data.status)) {
    startPolling();
  }
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

function validateJobPrompts(prompts) {
  if (!prompts.length) {
    throw new Error("请至少填写 1 个有效提示词内容");
  }
  if (prompts.length > 10) {
    throw new Error("最多支持 10 个提示词");
  }
}

function buildJobPayload(prompts = collectPrompts()) {
  const maxRetryCount = Math.max(0, Math.min(Number(state.maxRetryCount || 0), 10));
  return {
    source_dir: el.sourceDir.value.trim(),
    output_dir: el.outputDir.value.trim(),
    model: selectedModel(),
    aspect_ratio: el.aspectRatio.value,
    quality: el.quality.value,
    image_size: el.imageSize ? el.imageSize.value : "",
    concurrency: Number(el.concurrency.value || 10),
    poll_interval_sec: Number(el.pollInterval?.value || 20),
    auto_retry_failed: !!state.autoRetryFailed && maxRetryCount > 0,
    max_retry_count: maxRetryCount,
    prompts,
  };
}

async function startJob() {
  const prompts = collectPrompts();

  el.btnStart.disabled = true;
  try {
    validateJobPrompts(prompts);
    const body = buildJobPayload(prompts);
    const res = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const job = res.data;
    state.currentJobId = job.id;
    state.resultFilter = "all";
    state.lastJobSig = "";
    setActionMessage(`任务已创建：${job.id}`);
    renderJob(job);
    startPolling();
  } catch (err) {
    setActionMessage(err.message || String(err), true);
  } finally {
    el.btnStart.disabled = false;
  }
}

function renderSchedule(schedule) {
  if (!el.scheduleMessage) return;
  if (!schedule) {
    state.currentScheduleId = null;
    if (el.btnCancelSchedule) el.btnCancelSchedule.disabled = true;
    setScheduleMessage("");
    return;
  }

  state.currentScheduleId = schedule.id || null;
  if (el.scheduleEnabled && ["pending", "waiting", "submitting"].includes(schedule.status)) {
    el.scheduleEnabled.checked = true;
  }
  if (el.scheduleRunAt && schedule.run_at) {
    el.scheduleRunAt.value = scheduleTimeToInputValue(schedule.run_at);
  }
  if (el.btnCancelSchedule) {
    el.btnCancelSchedule.disabled = !["pending", "waiting"].includes(schedule.status);
  }

  const statusText = scheduleStatusLabel(schedule.status);
  const timeText = schedule.run_at ? ` · ${schedule.run_at}` : "";
  const jobText = schedule.job_id ? ` · 任务 ${String(schedule.job_id).slice(0, 8)}` : "";
  setScheduleMessage(`定时${statusText}${timeText}${jobText}${schedule.message ? ` · ${schedule.message}` : ""}`, schedule.status === "failed");
}

function pickVisibleSchedule(schedules) {
  return (
    schedules.find((item) => ["pending", "waiting", "submitting"].includes(item.status)) ||
    schedules[0] ||
    null
  );
}

async function refreshSchedules() {
  if (state.schedulePollInFlight) return null;
  let visibleSchedule = null;
  state.schedulePollInFlight = true;
  try {
    const res = await api("/api/schedules");
    const schedules = res.data || [];
    const schedule = pickVisibleSchedule(schedules);
    visibleSchedule = schedule;
    renderSchedule(schedule);

    if (schedule?.status === "submitted" && schedule.job_id) {
      if (state.currentJobId !== schedule.job_id) {
        state.currentJobId = schedule.job_id;
        state.lastJobSig = "";
        state.resultFilter = "all";
      }
      startPolling();
      stopSchedulePolling();
    } else if (!schedule || ["cancelled", "failed"].includes(schedule.status)) {
      stopSchedulePolling();
    }
    return visibleSchedule;
  } finally {
    state.schedulePollInFlight = false;
  }
}

async function loadSchedules() {
  const schedule = await refreshSchedules();
  if (schedule && ["pending", "waiting", "submitting"].includes(schedule.status)) {
    startSchedulePolling();
  }
}

function startSchedulePolling() {
  if (state.scheduleTimer) return;
  state.scheduleTimer = setInterval(() => {
    refreshSchedules().catch((err) => {
      setScheduleMessage(err.message || String(err), true);
    });
  }, 5000);
}

function stopSchedulePolling() {
  if (state.scheduleTimer) {
    clearInterval(state.scheduleTimer);
    state.scheduleTimer = null;
  }
}

async function saveSchedule() {
  if (!el.scheduleEnabled?.checked) {
    setScheduleMessage("请先启用定时运行", true);
    return;
  }
  const runAt = (el.scheduleRunAt?.value || "").trim();
  if (!runAt) {
    setScheduleMessage("请选择定时运行时间", true);
    return;
  }
  const runAtDate = new Date(runAt);
  if (Number.isNaN(runAtDate.getTime()) || runAtDate.getTime() <= Date.now()) {
    setScheduleMessage("定时运行时间必须晚于当前时间", true);
    return;
  }

  const prompts = collectPrompts();
  el.btnSaveSchedule.disabled = true;
  try {
    validateJobPrompts(prompts);
    const res = await api("/api/schedules", {
      method: "POST",
      body: JSON.stringify({
        run_at: runAt,
        payload: buildJobPayload(prompts),
      }),
    });
    renderSchedule(res.data);
    setScheduleMessage(res.message || "定时任务已保存");
    startSchedulePolling();
  } catch (err) {
    setScheduleMessage(err.message || String(err), true);
  } finally {
    el.btnSaveSchedule.disabled = false;
  }
}

async function cancelSchedule() {
  if (!state.currentScheduleId) return;
  const res = await api(`/api/schedules/${state.currentScheduleId}`, { method: "DELETE" });
  renderSchedule(res.data);
  setScheduleMessage(res.message || "定时任务已取消");
  stopSchedulePolling();
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

async function retryFailed() {
  if (!state.currentJobId) return;
  el.btnRetry.disabled = true;
  try {
    const res = await api(`/api/jobs/${state.currentJobId}/retry`, {
      method: "POST",
      body: "{}",
    });
    setActionMessage(res.message || "已开始重试失败任务");
    if (res.data) renderJob(res.data);
    startPolling();
  } catch (err) {
    setActionMessage(err.message || String(err), true);
    el.btnRetry.disabled = false;
  }
}

async function deleteJob() {
  if (!state.currentJobId) return;
  const res = await api(`/api/jobs/${state.currentJobId}`, { method: "DELETE" });
  setActionMessage(res.message || "任务已删除");
  state.currentJobId = null;
  state.lastTaskRenderSignature = "";
  state.lastJobSig = "";
  state.lastTasks = [];
  state.resultFilter = "all";
  stopPolling();
  el.jobSummary.textContent = "";
  el.statTotal.textContent = "0";
  el.statSuccess.textContent = "0";
  el.statFailed.textContent = "0";
  if (el.statCancelled) el.statCancelled.textContent = "0";
  el.statPending.textContent = "0";
  el.progressFill.style.transform = "scaleX(0)";
  el.progressText.textContent = "0%";
  renderTaskViews([]);
  el.taskTbody.innerHTML = '<tr><td colspan="5" class="empty">创建任务后显示每张图的处理状态</td></tr>';
  if (el.btnRetry) el.btnRetry.disabled = true;
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    refreshJob().catch((err) => {
      setActionMessage(err.message || String(err), true);
    });
  }, 2000);
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

    let res;
    try {
      // 带上上次的状态指纹：没变化时后端只回一个极小的响应，
      // 大批量任务下能省掉每 2 秒几 MB 的序列化与传输
      const sig = encodeURIComponent(state.lastJobSig || "");
      res = await api(`/api/jobs/${state.currentJobId}?sig=${sig}`);
    } catch (err) {
      // 历史记录有条数上限，旧任务可能已被清理
      if (String(err.message || "").includes("任务不存在")) {
        state.currentJobId = null;
        state.lastJobSig = "";
        stopPolling();
        setActionMessage("该任务记录已被清理（仅保留最近若干条）");
        return;
      }
      throw err;
    }
    if (res.data && res.data.unchanged) {
      // 状态未变化，无需重绘；注意必须在这里返回，
      // 否则下面会拿 undefined 的 status 去判断按钮可用性
      state.lastJobSig = res.data.sig || state.lastJobSig;
      return;
    }
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
  if (job.sig) state.lastJobSig = job.sig;

  const statusBreakdown = [
    job.success > 0 ? `成功${job.success}` : "",
    job.pending > 0 ? `等待${job.pending}` : "",
    job.failed > 0 ? `失败${job.failed}` : "",
    job.cancelled > 0 ? `取消${job.cancelled}` : "",
  ].filter(Boolean).join("/");
  const retryText = job.max_retry_count > 0
    ? ` · 重试 ${job.retry_attempt_count || 0}/${job.max_retry_count}`
    : "";
  el.jobSummary.textContent = `任务 ${job.id.slice(0, 8)} · ${statusLabel(job.status)}${statusBreakdown ? " [" + statusBreakdown + "]" : ""}${retryText} · ${job.message || ""}`;
  el.statTotal.textContent = job.total || 0;
  el.statSuccess.textContent = job.success || 0;
  el.statFailed.textContent = job.failed || 0;
  if (el.statCancelled) el.statCancelled.textContent = job.cancelled || 0;
  el.statPending.textContent = job.pending || 0;

  const done = (job.success || 0) + (job.failed || 0) + (job.cancelled || 0);
  const percent = job.total ? Math.round((done / job.total) * 100) : 0;
  el.progressFill.style.transform = `scaleX(${percent / 100})`;
  el.progressText.textContent = `${percent}%`;

  const tasks = job.tasks || [];
  const taskRenderSignature = [
    job.id,
    state.resultFilter,
    ...tasks.map((task) => [
      task.id,
      task.status,
      task.message,
      task.output_path,
      task.result_url,
    ].join("")),
  ].join("");

  if (state.lastTaskRenderSignature !== taskRenderSignature) {
    state.lastTaskRenderSignature = taskRenderSignature;
    renderTaskViews(tasks);
  }

  const isRunning = ["queued", "running"].includes(job.status);
  el.btnCancel.disabled = !isRunning;
  if (el.btnDelete) el.btnDelete.disabled = isRunning;
  if (el.btnRetry) {
    const retryable = (job.failed || 0) + (job.cancelled || 0);
    el.btnRetry.disabled = isRunning || retryable === 0;
  }
}

async function fetchCredit() {
  const badge = el.creditBadge;
  const valueEl = el.creditValue;
  badge.classList.add("loading");
  try {
    const resp = await api("/api/credit");
    if (resp && resp.data) {
      valueEl.textContent = Number(resp.data.remaining_total).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } else {
      valueEl.textContent = "--";
    }
  } catch (err) {
    valueEl.textContent = "--";
  } finally {
    badge.classList.remove("loading");
  }
}

function bindEvents() {
  if (el.btnTheme) el.btnTheme.addEventListener("click", toggleTheme);
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
  if (el.settingsAutoRetryFailed) {
    el.settingsAutoRetryFailed.addEventListener("change", syncRetrySettingsControls);
  }
  document.getElementById("btn-pick-source").addEventListener("click", async () => {
    try {
      const path = await pickFolder("选择源图片文件夹", el.sourceDir.value);
      if (!path) return;
      el.sourceDir.value = path;
      await scanFolder();
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
  if (el.btnSaveSchedule) {
    el.btnSaveSchedule.addEventListener("click", saveSchedule);
  }
  if (el.btnCancelSchedule) {
    el.btnCancelSchedule.addEventListener("click", async () => {
      try {
        await cancelSchedule();
      } catch (err) {
        setScheduleMessage(err.message || String(err), true);
      }
    });
  }
  if (el.scheduleEnabled) {
    el.scheduleEnabled.addEventListener("change", () => {
      if (el.scheduleEnabled.checked) {
        ensureDefaultScheduleTime();
        setScheduleMessage("");
      } else {
        setScheduleMessage("");
      }
    });
  }
  el.btnCancel.addEventListener("click", async () => {
    try {
      await cancelJob();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  if (el.btnRetry) {
    el.btnRetry.addEventListener("click", async () => {
      try {
        await retryFailed();
      } catch (err) {
        setActionMessage(err.message || String(err), true);
      }
    });
  }
  document.getElementById("btn-delete-job").addEventListener("click", async () => {
    try {
      await deleteJob();
    } catch (err) {
      setActionMessage(err.message || String(err), true);
    }
  });
  document.getElementById("btn-refresh-credit").addEventListener("click", async () => { try { await fetchCredit(); } catch (_) {} });
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

  if (el.lightbox) {
    el.lightbox.addEventListener("click", (e) => {
      if (e.target === el.lightbox) closeLightbox();
    });
  }
  if (el.btnCloseLightbox) {
    el.btnCloseLightbox.addEventListener("click", closeLightbox);
  }

  document.addEventListener("keydown", (event) => {
    if (el.lightbox && !el.lightbox.classList.contains("hidden")) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeLightbox();
      }
      return;
    }
    if (el.settingsModal.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeSettings();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = Array.from(el.settingsModal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  window.addEventListener("beforeunload", () => {
    stopPolling();
    stopSchedulePolling();
  });
}

async function init() {
  bindEvents();
  bindModelSelectors();
  state.prompts = defaultPrompts();
  renderPrompts();
  renderTaskViews([]);
  if (typeof window.refreshIcons === "function") {
    window.refreshIcons();
  }
  await loadOptions();
  await fetchCredit();
  try {
    await loadSettings();
  } catch (err) {
    setActionMessage(`加载设置失败: ${err.message || err}`, true);
  }
  try {
    await restoreLatestJob();
  } catch (err) {
    setActionMessage(`恢复最近任务失败: ${err.message || err}`, true);
  }
  ensureDefaultScheduleTime();
  try {
    await loadSchedules();
  } catch (err) {
    setScheduleMessage(`加载定时任务失败: ${err.message || err}`, true);
  }
  if (typeof window.refreshIcons === "function") {
    window.refreshIcons();
  }
}

init();
