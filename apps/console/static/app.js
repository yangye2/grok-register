(function () {
  const state = {
    tasks: [],
    selectedTaskId: null,
    accounts: [],
    selectedAccountIds: new Set(),
    accountSearch: "",
    accountTaskFilter: "all",
    accountPage: 1,
    accountPageSize: 20,
    accountTotal: 0,
    accountTotalPages: 1,
    taskStatusFilter: "all",
    taskPage: 1,
    taskPageSize: 20,
    taskTotal: 0,
    taskTotalPages: 1,
  };

  const taskListEl = document.getElementById("taskList");
  const detailTitleEl = document.getElementById("detailTitle");
  const detailMetaEl = document.getElementById("detailMeta");
  const detailSummaryEl = document.getElementById("detailSummary");
  const consoleOutputEl = document.getElementById("consoleOutput");
  const formEl = document.getElementById("taskForm");
  const settingsFormEl = document.getElementById("settingsForm");
  const formMessageEl = document.getElementById("formMessage");
  const settingsMessageEl = document.getElementById("settingsMessage");
  const stopBtnEl = document.getElementById("stopBtn");
  const refreshBtnEl = document.getElementById("refreshBtn");
  const healthRefreshBtnEl = document.getElementById("healthRefreshBtn");
  const toggleAdvancedBtnEl = document.getElementById("toggleAdvancedBtn");
  const toggleMailBtnEl = document.getElementById("toggleMailBtn");
  const themeToggleEl = document.getElementById("themeToggle");
  const advancedFieldsEl = document.getElementById("advancedFields");
  const tabButtons = Array.from(document.querySelectorAll("[data-tab-target]"));
  const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
  const healthGridEl = document.getElementById("healthGrid");
  const healthMetaEl = document.getElementById("healthMeta");
  const accountsRefreshBtnEl = document.getElementById("accountsRefreshBtn");
  const accountsDownloadBtnEl = document.getElementById("accountsDownloadBtn");
  const accountsDeleteBtnEl = document.getElementById("accountsDeleteBtn");
  const accountsMetaEl = document.getElementById("accountsMeta");
  const accountsSelectAllEl = document.getElementById("accountsSelectAll");
  const accountsTableBodyEl = document.getElementById("accountsTableBody");
  const accountsEmptyEl = document.getElementById("accountsEmpty");
  const accountsSearchInputEl = document.getElementById("accountsSearchInput");
  const accountsTaskFilterEl = document.getElementById("accountsTaskFilter");
  const accountsPageSizeEl = document.getElementById("accountsPageSize");
  const accountsPageMetaEl = document.getElementById("accountsPageMeta");
  const accountsPrevPageBtnEl = document.getElementById("accountsPrevPageBtn");
  const accountsNextPageBtnEl = document.getElementById("accountsNextPageBtn");
  const metricTaskTotalEl = document.getElementById("metricTaskTotal");
  const metricTaskRunningEl = document.getElementById("metricTaskRunning");
  const metricAccountTotalEl = document.getElementById("metricAccountTotal");
  const metricAccountSelectedEl = document.getElementById("metricAccountSelected");
  const taskListMetaCountEl = document.getElementById("taskListMetaCount");
  const taskStatusFilterEl = document.getElementById("taskStatusFilter");
  const taskPageSizeEl = document.getElementById("taskPageSize");
  const taskPageMetaEl = document.getElementById("taskPageMeta");
  const taskPrevPageBtnEl = document.getElementById("taskPrevPageBtn");
  const taskNextPageBtnEl = document.getElementById("taskNextPageBtn");

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function setDefaults() {
    const defaults = window.__DEFAULTS__ || {};
    formEl.elements.name.value = `grok-task-${Date.now()}`;
    formEl.elements.count.value = defaults.run?.count || 50;
    settingsFormEl.elements.proxy.value = defaults.proxy || "";
    settingsFormEl.elements.browser_proxy.value = defaults.browser_proxy || "";
    settingsFormEl.elements.temp_mail_api_base.value = defaults.temp_mail_api_base || "";
    settingsFormEl.elements.temp_mail_admin_password.value = defaults.temp_mail_admin_password || "";
    settingsFormEl.elements.temp_mail_domain.value = defaults.temp_mail_domain || "";
    settingsFormEl.elements.temp_mail_site_password.value = defaults.temp_mail_site_password || "";
    settingsFormEl.elements.cpa_auth_dir.value = defaults.cpa_auth_dir || "./cpa_auths";
    settingsFormEl.elements.cpa_proxy.value = defaults.cpa_proxy || "";
    settingsFormEl.elements.cpa_hotload_dir.value = defaults.cpa_hotload_dir || "";
    settingsFormEl.elements.cpa_mint_timeout_sec.value = defaults.cpa_mint_timeout_sec || 300;
    settingsFormEl.elements.cpa_export_enabled.checked = Boolean(defaults.cpa_export_enabled);
    settingsFormEl.elements.cpa_copy_to_hotload.checked = Boolean(defaults.cpa_copy_to_hotload);
    settingsFormEl.elements.cpa_headless.checked = Boolean(defaults.cpa_headless);
    settingsFormEl.elements.cpa_cloud_upload_enabled.checked = Boolean(defaults.cpa_cloud_upload_enabled);
    settingsFormEl.elements.cpa_cloud_api_base.value = defaults.cpa_cloud_api_base || "";
    settingsFormEl.elements.cpa_cloud_management_key.value = defaults.cpa_cloud_management_key || "";
    settingsFormEl.elements.cpa_cloud_upload_timeout.value = defaults.cpa_cloud_upload_timeout || 30;
    settingsFormEl.elements.cpa_cloud_upload_retries.value = defaults.cpa_cloud_upload_retries || 3;
  }

  function statusClass(status) {
    return `status-pill status-${status || "unknown"}`;
  }

  function healthClass(ok) {
    return ok ? "health-pill health-ok" : "health-pill health-bad";
  }

  function getTaskProgress(task) {
    const target = Math.max(Number(task.target_count) || 0, 1);
    const completed = Number(task.completed_count) || 0;
    const failed = Number(task.failed_count) || 0;
    const handled = Math.min(target, completed + failed);
    return Math.max(0, Math.min(100, Math.round((handled / target) * 100)));
  }


  async function updateMetrics() {
    try {
      const allTasksData = await fetchJson("/api/tasks?page_size=1000");
      const allTasks = allTasksData.tasks || [];
      const runningStatuses = new Set(["queued", "running", "stopping"]);
      metricTaskTotalEl.textContent = String(allTasks.length);
      metricTaskRunningEl.textContent = String(allTasks.filter((task) => runningStatuses.has(task.status)).length);
      metricAccountTotalEl.textContent = String(state.accountTotal);
      metricAccountSelectedEl.textContent = String(state.selectedAccountIds.size);
    } catch (error) {
      console.error("Failed to update metrics:", error);
    }
  }

  async function renderAccountFilters() {
    try {
      const allTasksData = await fetchJson("/api/tasks?page_size=1000");
      const allTasks = allTasksData.tasks || [];
      const options = [
        '<option value="all">全部任务</option>',
        ...allTasks.map((task) => (
          `<option value="${task.id}" ${state.accountTaskFilter === String(task.id) ? "selected" : ""}>#${task.id} ${escapeHtml(task.name)}</option>`
        )),
      ];
      accountsTaskFilterEl.innerHTML = options.join("");
      accountsSearchInputEl.value = state.accountSearch;
      accountsPageSizeEl.value = String(state.accountPageSize);
    } catch (error) {
      console.error("Failed to render account filters:", error);
    }
  }

  function applyTheme(theme) {
    const normalized = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = normalized;
    themeToggleEl.setAttribute("aria-pressed", normalized === "dark" ? "true" : "false");
    themeToggleEl.dataset.theme = normalized;
    localStorage.setItem("grok-register-theme", normalized);
  }

  function initTheme() {
    const saved = localStorage.getItem("grok-register-theme");
    if (saved) {
      applyTheme(saved);
      return;
    }
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(prefersDark ? "dark" : "light");
  }

  function activateTab(tabName) {
    tabButtons.forEach((button) => {
      const active = button.dataset.tabTarget === tabName;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    tabPanels.forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.tabPanel !== tabName);
    });
    if (tabName === "accounts") {
      refreshAccounts();
    }
    if (tabName === "config") {
      refreshHealth();
    }
  }

  function renderHealth(data) {
    const items = data.items || [];
    healthMetaEl.textContent = `最近检测时间 ${data.checked_at || "-"}`;
    if (!items.length) {
      healthGridEl.innerHTML = '<div class="empty">暂无健康检查结果</div>';
      return;
    }
    healthGridEl.innerHTML = items.map((item) => `
      <div class="health-card">
        <div class="task-row">
          <strong>${escapeHtml(item.label)}</strong>
          <span class="${healthClass(item.ok)}">${item.ok ? "正常" : "异常"}</span>
        </div>
        <div class="health-summary">${escapeHtml(item.summary || "-")}</div>
        <div class="health-target">${escapeHtml(item.target || "-")}</div>
        <div class="health-detail">${escapeHtml(item.detail || "-")}</div>
      </div>
    `).join("");
  }

  function renderTaskList() {
    taskStatusFilterEl.value = state.taskStatusFilter;
    taskPageSizeEl.value = String(state.taskPageSize);
    const start = state.taskTotal ? ((state.taskPage - 1) * state.taskPageSize) + 1 : 0;
    const end = state.taskTotal ? Math.min(state.taskPage * state.taskPageSize, state.taskTotal) : 0;
    taskListMetaCountEl.textContent = `筛选结果 ${state.taskTotal} 个，当前页 ${state.tasks.length} 个`;
    taskPrevPageBtnEl.disabled = state.taskPage <= 1;
    taskNextPageBtnEl.disabled = state.taskPage >= state.taskTotalPages;
    taskPageMetaEl.textContent = state.taskTotal
      ? `第 ${state.taskPage} / ${state.taskTotalPages} 页，显示 ${start}-${end} / ${state.taskTotal}`
      : "第 1 / 1 页";

    if (!state.tasks.length) {
      taskListEl.innerHTML = '<div class="empty">暂无任务</div>';
      return;
    }

    taskListEl.innerHTML = state.tasks.map((task) => `
      <button class="task-card ${task.id === state.selectedTaskId ? "selected" : ""}" data-task-id="${task.id}">
        <div class="task-row">
          <strong title="${escapeHtml(task.name)}">#${task.id} ${escapeHtml(task.name)}</strong>
          <span class="${statusClass(task.status)}">${escapeHtml(task.status)}</span>
        </div>
        <div class="task-meta-group">
          <div class="task-subrow">执行次数 ${task.target_count}</div>
          <div class="task-subrow">本地账号 ${task.account_count || 0}</div>
        </div>
        <div class="task-card-progress">
          <div class="task-row">
            <span class="task-action-hint">进度 ${getTaskProgress(task)}%</span>
            <span class="task-action-hint">${task.completed_count}/${task.target_count}</span>
          </div>
          <div class="task-progress-bar">
            <div class="task-progress-fill" style="width:${getTaskProgress(task)}%"></div>
          </div>
        </div>
        <div class="task-actions">
          <span class="task-action-hint">点击查看日志</span>
          <button class="button button-danger button-small" type="button" data-delete-task-id="${task.id}">删除</button>
        </div>
      </button>
    `).join("");

    taskListEl.querySelectorAll("[data-task-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedTaskId = Number(button.dataset.taskId);
        renderTaskList();
        refreshDetail();
      });
    });

    taskListEl.querySelectorAll("[data-delete-task-id]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        const taskId = Number(button.dataset.deleteTaskId);
        const confirmed = window.confirm(`确认删除任务 #${taskId} 吗？`);
        if (!confirmed) return;
        try {
          await fetchJson(`/api/tasks/${taskId}`, { method: "DELETE" });
          if (state.selectedTaskId === taskId) {
            state.selectedTaskId = null;
            detailTitleEl.textContent = "实时控制台";
            detailSummaryEl.innerHTML = "";
            detailMetaEl.innerHTML = "";
            consoleOutputEl.textContent = "选择任务后显示输出";
          }
          await refreshTasks();
          await refreshDetail();
        } catch (error) {
          formMessageEl.textContent = error.message;
          formMessageEl.className = "form-message error";
        }
      });
    });
  }

  function renderTaskDetail(task) {
    detailTitleEl.textContent = `任务 #${task.id} · ${task.name}`;
    stopBtnEl.disabled = !["queued", "running", "stopping"].includes(task.status);
    detailSummaryEl.innerHTML = [
      ["状态", task.status],
      ["目标次数", task.target_count],
      ["成功数", task.completed_count],
      ["失败数", task.failed_count],
      ["账号数", task.account_count || 0],
      ["当前轮次", task.current_round],
      ["当前阶段", task.current_phase || "-"],
    ].map(([label, value]) => `
      <div class="summary-item">
        <div class="meta-item-label">${escapeHtml(label)}</div>
        <div class="meta-item-value">${escapeHtml(value)}</div>
      </div>
    `).join("");

    const cfg = task.config || {};
    detailMetaEl.innerHTML = [
      ["邮箱 API Base", cfg.temp_mail_api_base || "-"],
      ["邮箱域名", cfg.temp_mail_domain || "-"],
      ["邮箱管理密码", cfg.temp_mail_admin_password || "-"],
      ["站点密码", cfg.temp_mail_site_password || "-"],
      ["请求代理", cfg.proxy || "-"],
      ["浏览器代理", cfg.browser_proxy || "-"],
      ["最近邮箱", task.last_email || "-"],
      ["最近错误", task.last_error || "-"],
      ["创建时间", task.created_at || "-"],
      ["开始时间", task.started_at || "-"],
      ["结束时间", task.finished_at || "-"],
      ["PID", task.pid || "-"],
    ].map(([label, value]) => `
      <div class="meta-item">
        <div class="meta-item-label">${escapeHtml(label)}</div>
        <div class="meta-item-value">${escapeHtml(value)}</div>
      </div>
    `).join("");
  }

  function renderAccounts() {
    const validIds = new Set(state.accounts.map((account) => account.id));
    state.selectedAccountIds = new Set(
      Array.from(state.selectedAccountIds).filter((id) => validIds.has(id))
    );

    const start = state.accountTotal ? ((state.accountPage - 1) * state.accountPageSize) + 1 : 0;
    const end = state.accountTotal ? Math.min(state.accountPage * state.accountPageSize, state.accountTotal) : 0;
    accountsMetaEl.textContent = `筛选结果 ${state.accountTotal} 个，当前页 ${state.accounts.length} 个，已选择 ${state.selectedAccountIds.size} 个`;
    accountsDownloadBtnEl.disabled = state.selectedAccountIds.size === 0;
    accountsDeleteBtnEl.disabled = state.selectedAccountIds.size === 0;
    accountsSelectAllEl.checked = state.accounts.length > 0 && state.selectedAccountIds.size === state.accounts.length;
    accountsSelectAllEl.indeterminate = state.selectedAccountIds.size > 0 && state.selectedAccountIds.size < state.accounts.length;
    accountsPrevPageBtnEl.disabled = state.accountPage <= 1;
    accountsNextPageBtnEl.disabled = state.accountPage >= state.accountTotalPages;
    accountsPageMetaEl.textContent = state.accountTotal
      ? `第 ${state.accountPage} / ${state.accountTotalPages} 页，显示 ${start}-${end} / ${state.accountTotal}`
      : "第 1 / 1 页";
    renderAccountFilters();

    if (!state.accounts.length) {
      accountsTableBodyEl.innerHTML = "";
      accountsEmptyEl.textContent = state.accountTotal ? "当前页没有账号" : "当前筛选条件下没有账号";
      accountsEmptyEl.classList.remove("hidden");
      return;
    }
    accountsEmptyEl.classList.add("hidden");

    const cpaStatusLabel = (status) => ({
      not_started: "未授权",
      running: "授权中",
      generated: "已生成",
      uploaded: "已推送",
      failed: "失败",
    }[status] || status || "未授权");

    accountsTableBodyEl.innerHTML = state.accounts.map((account) => `
      <tr>
        <td class="select-col">
          <input type="checkbox" data-account-select="${account.id}" ${state.selectedAccountIds.has(account.id) ? "checked" : ""} aria-label="选择 ${escapeHtml(account.email)}">
        </td>
        <td class="account-email" title="${escapeHtml(account.email)}">${escapeHtml(account.email)}</td>
        <td class="account-password" title="${escapeHtml(account.password || "")}">${escapeHtml(account.password || "-")}</td>
        <td class="account-sso" title="${escapeHtml(account.sso || "")}">${escapeHtml(account.sso || "-")}</td>
        <td title="#${account.task_id} ${escapeHtml(account.task_name || "")}">#${account.task_id} ${escapeHtml(account.task_name || "")}</td>
        <td>${escapeHtml(account.created_at || "-")}</td>
        <td title="${escapeHtml(account.cpa_error || account.cpa_path || "")}">${escapeHtml(cpaStatusLabel(account.cpa_status))}</td>
        <td class="account-actions">
          <button class="button button-small" type="button" data-download-account-id="${account.id}">下载</button>
          <button class="button button-secondary button-small" type="button" data-cpa-account-id="${account.id}" ${account.cpa_status === "running" ? "disabled" : ""}>授权并推送</button>
          <button class="button button-danger button-small" type="button" data-delete-account-id="${account.id}">删除</button>
        </td>
      </tr>
    `).join("");

    accountsTableBodyEl.querySelectorAll("[data-account-select]").forEach((input) => {
      input.addEventListener("change", () => {
        const accountId = Number(input.dataset.accountSelect);
        if (input.checked) {
          state.selectedAccountIds.add(accountId);
        } else {
          state.selectedAccountIds.delete(accountId);
        }
        renderAccounts();
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-download-account-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.downloadAccountId));
        if (account) {
          downloadSsoFile([account], `sso_${account.email || account.id}.txt`);
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-delete-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.deleteAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认删除账号 ${account.email} 吗？`);
        if (!confirmed) return;
        await fetchJson(`/api/accounts/${account.id}`, { method: "DELETE" });
        state.selectedAccountIds.delete(account.id);
        await refreshAccounts();
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-cpa-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.cpaAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认对账号 ${account.email} 生成授权并推送 CPA 吗？`);
        if (!confirmed) return;
        button.disabled = true;
        try {
          await fetchJson(`/api/accounts/${account.id}/cpa`, { method: "POST" });
          accountsMetaEl.textContent = `账号 ${account.email} 的 CPA 授权任务已开始`;
          await refreshAccounts();
        } catch (error) {
          accountsMetaEl.textContent = `CPA 授权启动失败: ${error.message}`;
          button.disabled = false;
        }
      });
    });
  }

  function downloadSsoFile(accounts, filename) {
    const lines = accounts
      .map((account) => String(account.sso || "").trim())
      .filter(Boolean);
    if (!lines.length) {
      accountsMetaEl.textContent = "没有可下载的 SSO";
      return;
    }
    const blob = new Blob([`${lines.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Request failed");
    }
    return data;
  }

  async function refreshTasks() {
    try {
      const params = new URLSearchParams();
      if (state.taskStatusFilter !== "all") {
        params.set("status", state.taskStatusFilter);
      }
      params.set("page", String(state.taskPage));
      params.set("page_size", String(state.taskPageSize));
      const data = await fetchJson(`/api/tasks?${params.toString()}`);
      state.tasks = data.tasks || [];
      state.taskTotal = Number(data.pagination?.total || 0);
      state.taskTotalPages = Number(data.pagination?.total_pages || 1);
      state.taskPage = Number(data.pagination?.page || 1);
      state.taskPageSize = Number(data.pagination?.page_size || state.taskPageSize || 20);
      if (!state.selectedTaskId && state.tasks.length) {
        state.selectedTaskId = state.tasks[0].id;
      }
      renderTaskList();
    } catch (error) {
      taskListEl.innerHTML = '<div class="empty">任务加载失败</div>';
      taskListMetaCountEl.textContent = `任务加载失败: ${error.message}`;
      taskPrevPageBtnEl.disabled = true;
      taskNextPageBtnEl.disabled = true;
      taskPageMetaEl.textContent = "第 1 / 1 页";
      state.tasks = [];
      state.taskTotal = 0;
      state.taskTotalPages = 1;
    }
  }

  async function refreshDetail() {
    if (!state.selectedTaskId) {
      return;
    }
    const taskData = await fetchJson(`/api/tasks/${state.selectedTaskId}`);
    renderTaskDetail(taskData.task);
    const logData = await fetchJson(`/api/tasks/${state.selectedTaskId}/logs?limit=250`);
    consoleOutputEl.innerHTML = escapeHtml((logData.lines || []).join("\n"));
    consoleOutputEl.scrollTop = consoleOutputEl.scrollHeight;
  }

  async function refreshAll() {
    try {
      await refreshTasks();
      await refreshDetail();
    } catch (error) {
      formMessageEl.textContent = error.message;
      formMessageEl.className = "form-message error";
    }
  }

  async function refreshHealth() {
    try {
      healthMetaEl.textContent = "检测中...";
      const data = await fetchJson("/api/health");
      renderHealth(data);
    } catch (error) {
      healthMetaEl.textContent = `检测失败: ${error.message}`;
      healthGridEl.innerHTML = '<div class="empty">健康检查失败</div>';
    }
  }

  async function refreshAccounts() {
    try {
      const params = new URLSearchParams();
      if (state.accountTaskFilter !== "all") {
        params.set("task_id", state.accountTaskFilter);
      }
      if (state.accountSearch.trim()) {
        params.set("search", state.accountSearch.trim());
      }
      params.set("page", String(state.accountPage));
      params.set("page_size", String(state.accountPageSize));
      const data = await fetchJson(`/api/accounts?${params.toString()}`);
      state.accounts = data.accounts || [];
      state.accountTotal = Number(data.pagination?.total || 0);
      state.accountTotalPages = Number(data.pagination?.total_pages || 1);
      state.accountPage = Number(data.pagination?.page || 1);
      state.accountPageSize = Number(data.pagination?.page_size || state.accountPageSize || 20);
      renderAccounts();
    } catch (error) {
      accountsMetaEl.textContent = `账号加载失败: ${error.message}`;
      accountsTableBodyEl.innerHTML = "";
      accountsEmptyEl.textContent = "账号加载失败";
      accountsEmptyEl.classList.remove("hidden");
      accountsPrevPageBtnEl.disabled = true;
      accountsNextPageBtnEl.disabled = true;
      accountsPageMetaEl.textContent = "第 1 / 1 页";
      state.accounts = [];
      state.accountTotal = 0;
      state.accountTotalPages = 1;
    }
  }

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: formEl.elements.name.value.trim(),
      count: Number(formEl.elements.count.value),
      proxy: formEl.elements.proxy.value.trim() || null,
      browser_proxy: formEl.elements.browser_proxy.value.trim() || null,
      temp_mail_api_base: formEl.elements.temp_mail_api_base.value.trim() || null,
      temp_mail_admin_password: formEl.elements.temp_mail_admin_password.value.trim() || null,
      temp_mail_domain: formEl.elements.temp_mail_domain.value.trim() || null,
      temp_mail_site_password: formEl.elements.temp_mail_site_password.value.trim() || null,
    };
    try {
      const data = await fetchJson("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.selectedTaskId = data.task.id;
      formMessageEl.textContent = `任务 #${data.task.id} 已创建`;
      formMessageEl.className = "form-message success";
      await refreshAll();
    } catch (error) {
      formMessageEl.textContent = error.message;
      formMessageEl.className = "form-message error";
    }
  });

  stopBtnEl.addEventListener("click", async () => {
    if (!state.selectedTaskId) {
      return;
    }
    try {
      await fetchJson(`/api/tasks/${state.selectedTaskId}/stop`, { method: "POST" });
      await refreshAll();
    } catch (error) {
      formMessageEl.textContent = error.message;
      formMessageEl.className = "form-message error";
    }
  });

  refreshBtnEl.addEventListener("click", refreshAll);
  healthRefreshBtnEl.addEventListener("click", refreshHealth);
  accountsRefreshBtnEl.addEventListener("click", refreshAccounts);
  accountsDownloadBtnEl.addEventListener("click", () => {
    const selected = state.accounts.filter((account) => state.selectedAccountIds.has(account.id));
    downloadSsoFile(selected, `sso_selected_${Date.now()}.txt`);
  });
  accountsSelectAllEl.addEventListener("change", () => {
    if (accountsSelectAllEl.checked) {
      state.selectedAccountIds = new Set(state.accounts.map((account) => account.id));
    } else {
      state.selectedAccountIds.clear();
    }
    renderAccounts();
  });
  accountsDeleteBtnEl.addEventListener("click", async () => {
    const selected = state.accounts.filter((account) => state.selectedAccountIds.has(account.id));
    if (!selected.length) {
      return;
    }
    const confirmed = window.confirm(`确认批量删除 ${selected.length} 个账号吗？`);
    if (!confirmed) return;
    for (const account of selected) {
      await fetchJson(`/api/accounts/${account.id}`, { method: "DELETE" });
    }
    state.selectedAccountIds.clear();
    await refreshAccounts();
  });
  accountsSearchInputEl.addEventListener("input", () => {
    state.accountSearch = accountsSearchInputEl.value;
    state.accountPage = 1;
    renderAccounts();
  });
  accountsTaskFilterEl.addEventListener("change", () => {
    state.accountTaskFilter = accountsTaskFilterEl.value;
    state.accountPage = 1;
    state.selectedAccountIds.clear();
    renderAccounts();
  });
  accountsPageSizeEl.addEventListener("change", () => {
    state.accountPageSize = Number(accountsPageSizeEl.value) || 20;
    state.accountPage = 1;
    state.selectedAccountIds.clear();
    renderAccounts();
  });
  accountsPrevPageBtnEl.addEventListener("click", () => {
    state.accountPage = Math.max(1, state.accountPage - 1);
    state.selectedAccountIds.clear();
    renderAccounts();
  });
  accountsNextPageBtnEl.addEventListener("click", () => {
    state.accountPage += 1;
    state.selectedAccountIds.clear();
    renderAccounts();
  });
  taskStatusFilterEl.addEventListener("change", () => {
    state.taskStatusFilter = taskStatusFilterEl.value;
    state.taskPage = 1;
    refreshTasks();
  });
  taskPageSizeEl.addEventListener("change", () => {
    state.taskPageSize = Number(taskPageSizeEl.value) || 20;
    state.taskPage = 1;
    refreshTasks();
  });
  taskPrevPageBtnEl.addEventListener("click", () => {
    state.taskPage = Math.max(1, state.taskPage - 1);
    refreshTasks();
  });
  taskNextPageBtnEl.addEventListener("click", () => {
    state.taskPage += 1;
    refreshTasks();
  });
  themeToggleEl.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });

  settingsFormEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      proxy: settingsFormEl.elements.proxy.value.trim(),
      browser_proxy: settingsFormEl.elements.browser_proxy.value.trim(),
      temp_mail_api_base: settingsFormEl.elements.temp_mail_api_base.value.trim(),
      temp_mail_admin_password: settingsFormEl.elements.temp_mail_admin_password.value.trim(),
      temp_mail_domain: settingsFormEl.elements.temp_mail_domain.value.trim(),
      temp_mail_site_password: settingsFormEl.elements.temp_mail_site_password.value.trim(),
      cpa_auth_dir: settingsFormEl.elements.cpa_auth_dir.value.trim(),
      cpa_proxy: settingsFormEl.elements.cpa_proxy.value.trim(),
      cpa_hotload_dir: settingsFormEl.elements.cpa_hotload_dir.value.trim(),
      cpa_mint_timeout_sec: Number(settingsFormEl.elements.cpa_mint_timeout_sec.value) || 300,
      cpa_export_enabled: settingsFormEl.elements.cpa_export_enabled.checked,
      cpa_copy_to_hotload: settingsFormEl.elements.cpa_copy_to_hotload.checked,
      cpa_headless: settingsFormEl.elements.cpa_headless.checked,
      cpa_cloud_upload_enabled: settingsFormEl.elements.cpa_cloud_upload_enabled.checked,
      cpa_cloud_api_base: settingsFormEl.elements.cpa_cloud_api_base.value.trim(),
      cpa_cloud_management_key: settingsFormEl.elements.cpa_cloud_management_key.value.trim(),
      cpa_cloud_upload_timeout: Number(settingsFormEl.elements.cpa_cloud_upload_timeout.value) || 30,
      cpa_cloud_upload_retries: Number(settingsFormEl.elements.cpa_cloud_upload_retries.value) || 3,
    };
    try {
      const data = await fetchJson("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      window.__DEFAULTS__ = data.defaults || window.__DEFAULTS__;
      settingsMessageEl.textContent = "默认配置已保存";
      settingsMessageEl.className = "form-message success";
      setDefaults();
      await refreshHealth();
    } catch (error) {
      settingsMessageEl.textContent = error.message;
      settingsMessageEl.className = "form-message error";
    }
  });

  toggleAdvancedBtnEl.addEventListener("click", () => {
    advancedFieldsEl.classList.toggle("hidden");
    toggleAdvancedBtnEl.textContent = advancedFieldsEl.classList.contains("hidden") ? "高级设置" : "收起高级设置";
  });

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activateTab(button.dataset.tabTarget);
    });
  });

  toggleMailBtnEl.addEventListener("click", () => {
    detailMetaEl.classList.toggle("hidden");
    toggleMailBtnEl.textContent = detailMetaEl.classList.contains("hidden") ? "展开临时邮箱参数" : "收起临时邮箱参数";
  });

  initTheme();
  setDefaults();
  renderAccountFilters();
  updateMetrics();
  activateTab("register");
  refreshHealth();
  refreshAccounts();
  refreshAll();
  window.setInterval(refreshAll, 2000);
  window.setInterval(refreshHealth, 15000);
  window.setInterval(refreshAccounts, 5000);
  window.setInterval(updateMetrics, 3000);
})();
