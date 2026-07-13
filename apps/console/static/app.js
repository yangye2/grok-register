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
    cpaLogAccountId: null,
    cpaQueue: null,
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
  const accountsCpaBatchBtnEl = document.getElementById("accountsCpaBatchBtn");
  const accountsCpaPushBatchBtnEl = document.getElementById("accountsCpaPushBatchBtn");
  const accountsSub2apiPushBatchBtnEl = document.getElementById("accountsSub2apiPushBatchBtn");
  const accountsCpaCancelBtnEl = document.getElementById("accountsCpaCancelBtn");
  const accountsCpaExportBtnEl = document.getElementById("accountsCpaExportBtn");
  const accountsSelectFilteredBtnEl = document.getElementById("accountsSelectFilteredBtn");
  const accountsCpaQueuePanelEl = document.getElementById("accountsCpaQueuePanel");
  const accountsCpaQueueTitleEl = document.getElementById("accountsCpaQueueTitle");
  const accountsCpaQueueMetaEl = document.getElementById("accountsCpaQueueMeta");
  const accountsCpaQueueBarEl = document.getElementById("accountsCpaQueueBar");
  const accountsCpaQueueMessageEl = document.getElementById("accountsCpaQueueMessage");
  const accountsMetaEl = document.getElementById("accountsMeta");
  const accountsSelectAllEl = document.getElementById("accountsSelectAll");
  const accountsTableBodyEl = document.getElementById("accountsTableBody");
  const accountsEmptyEl = document.getElementById("accountsEmpty");
  const accountCpaLogPanelEl = document.getElementById("accountCpaLogPanel");
  const accountCpaLogTitleEl = document.getElementById("accountCpaLogTitle");
  const accountCpaLogEl = document.getElementById("accountCpaLog");
  const accountCpaLogCloseBtnEl = document.getElementById("accountCpaLogCloseBtn");
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
    if (settingsFormEl.elements.temp_mail_domains_removed) {
      settingsFormEl.elements.temp_mail_domains_removed.value = defaults.temp_mail_domains_removed || "";
    }
    if (settingsFormEl.elements.domain_auth_fail_threshold) {
      settingsFormEl.elements.domain_auth_fail_threshold.value = defaults.domain_auth_fail_threshold ?? 3;
    }
    if (settingsFormEl.elements.domain_auth_fail_auto_remove) {
      settingsFormEl.elements.domain_auth_fail_auto_remove.checked = defaults.domain_auth_fail_auto_remove !== false;
    }
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
    settingsFormEl.elements.cpa_cloud_management_key.value = "";
    settingsFormEl.elements.cpa_cloud_upload_timeout.value = defaults.cpa_cloud_upload_timeout || 30;
    settingsFormEl.elements.cpa_cloud_upload_retries.value = defaults.cpa_cloud_upload_retries || 3;
    if (settingsFormEl.elements.cpa_batch_retry_count) {
      settingsFormEl.elements.cpa_batch_retry_count.value = defaults.cpa_batch_retry_count ?? 1;
    }
    if (settingsFormEl.elements.cpa_mint_browser_recycle_every) {
      settingsFormEl.elements.cpa_mint_browser_recycle_every.value = defaults.cpa_mint_browser_recycle_every ?? 15;
    }
    if (settingsFormEl.elements.cpa_health_check_before_upload) {
      settingsFormEl.elements.cpa_health_check_before_upload.checked = defaults.cpa_health_check_before_upload !== false;
    }
    if (settingsFormEl.elements.cpa_health_check_timeout) {
      settingsFormEl.elements.cpa_health_check_timeout.value = defaults.cpa_health_check_timeout ?? 15;
    }
    if (settingsFormEl.elements.cpa_health_check_model) {
      settingsFormEl.elements.cpa_health_check_model.value = defaults.cpa_health_check_model || "grok-4.5";
    }
    if (settingsFormEl.elements.cpa_health_check_headers) {
      settingsFormEl.elements.cpa_health_check_headers.value = defaults.cpa_health_check_headers || "";
    }
    if (settingsFormEl.elements.cpa_health_check_use_file_headers) {
      settingsFormEl.elements.cpa_health_check_use_file_headers.checked = defaults.cpa_health_check_use_file_headers !== false;
    }
    if (settingsFormEl.elements.sub2api_upload_enabled) {
      settingsFormEl.elements.sub2api_upload_enabled.checked = Boolean(defaults.sub2api_upload_enabled);
    }
    if (settingsFormEl.elements.sub2api_export_enabled) {
      settingsFormEl.elements.sub2api_export_enabled.checked = Boolean(defaults.sub2api_export_enabled);
    }
    if (settingsFormEl.elements.sub2api_api_base) {
      settingsFormEl.elements.sub2api_api_base.value = defaults.sub2api_api_base || "";
    }
    if (settingsFormEl.elements.sub2api_api_key) {
      settingsFormEl.elements.sub2api_api_key.value = "";
    }
    if (settingsFormEl.elements.sub2api_upload_timeout) {
      settingsFormEl.elements.sub2api_upload_timeout.value = defaults.sub2api_upload_timeout || 30;
    }
    if (settingsFormEl.elements.sub2api_upload_retries) {
      settingsFormEl.elements.sub2api_upload_retries.value = defaults.sub2api_upload_retries || 3;
    }
    if (settingsFormEl.elements.sub2api_platform) {
      settingsFormEl.elements.sub2api_platform.value = defaults.sub2api_platform || "openai";
    }
    if (settingsFormEl.elements.sub2api_account_type) {
      settingsFormEl.elements.sub2api_account_type.value = defaults.sub2api_account_type || "oauth";
    }
    if (settingsFormEl.elements.sub2api_account_concurrency) {
      settingsFormEl.elements.sub2api_account_concurrency.value = defaults.sub2api_account_concurrency ?? 10;
    }
    if (settingsFormEl.elements.sub2api_account_priority) {
      settingsFormEl.elements.sub2api_account_priority.value = defaults.sub2api_account_priority ?? 1;
    }
    if (settingsFormEl.elements.sub2api_account_group_ids) {
      settingsFormEl.elements.sub2api_account_group_ids.value = defaults.sub2api_account_group_ids || "";
    }
    if (settingsFormEl.elements.sub2api_default_proxy) {
      settingsFormEl.elements.sub2api_default_proxy.value = defaults.sub2api_default_proxy || "";
    }
    if (settingsFormEl.elements.sub2api_local_export_dir) {
      settingsFormEl.elements.sub2api_local_export_dir.value = defaults.sub2api_local_export_dir || "./sub2api_exports";
    }
    if (settingsFormEl.elements.sub2api_local_export) {
      settingsFormEl.elements.sub2api_local_export.checked = defaults.sub2api_local_export !== false;
    }
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
      <div class="task-card ${task.id === state.selectedTaskId ? "selected" : ""}" data-task-id="${task.id}" role="button" tabindex="0">
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
      </div>
    `).join("");

    taskListEl.querySelectorAll("[data-task-id]").forEach((card) => {
      const selectTask = () => {
        state.selectedTaskId = Number(card.dataset.taskId);
        renderTaskList();
        refreshDetail();
      };
      card.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete-task-id]")) {
          return;
        }
        selectTask();
      });
      card.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        selectTask();
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
      ["邮箱域名池", cfg.temp_mail_domain || cfg.temp_mail_domains || "-"],
      ["已移除域名", cfg.temp_mail_domains_removed || "-"],
      ["域名失败阈值", cfg.domain_auth_fail_threshold ?? 3],
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

  function renderDefaultMailDetail() {
    const cfg = window.__DEFAULTS__ || {};
    detailMetaEl.innerHTML = [
      ["邮箱 API Base", cfg.temp_mail_api_base || "-"],
      ["邮箱域名池", cfg.temp_mail_domain || cfg.temp_mail_domains || "-"],
      ["已移除域名", cfg.temp_mail_domains_removed || "-"],
      ["域名失败阈值", cfg.domain_auth_fail_threshold ?? 3],
      ["邮箱管理密码", cfg.temp_mail_admin_password || "-"],
      ["站点密码", cfg.temp_mail_site_password || "-"],
      ["请求代理", cfg.proxy || "-"],
      ["浏览器代理", cfg.browser_proxy || "-"],
    ].map(([label, value]) => `
      <div class="meta-item">
        <div class="meta-item-label">${escapeHtml(label)}</div>
        <div class="meta-item-value">${escapeHtml(value)}</div>
      </div>
    `).join("");
  }

  function cpaStatusLabel(status) {
    return ({
      not_started: "未授权",
      queued: "排队中",
      running: "授权中",
      uploading: "推送中",
      generated: "已生成",
      uploaded: "已推送",
      invalid: "测活失败",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status || "未授权");
  }

  function isCpaBusy(account) {
    return ["running", "uploading", "queued"].includes(account.cpa_status);
  }

  function canPushExistingCpa(account) {
    // Allow re-push when auth file path exists (including already uploaded)
    return Boolean(account.cpa_path) && !["running", "uploading", "queued"].includes(account.cpa_status);
  }

  function renderAccounts() {
    // Keep cross-page multi-select; only render the current page rows.
    renderAccountCpaLog();
    renderCpaQueuePanel();

    const start = state.accountTotal ? ((state.accountPage - 1) * state.accountPageSize) + 1 : 0;
    const end = state.accountTotal ? Math.min(state.accountPage * state.accountPageSize, state.accountTotal) : 0;
    accountsMetaEl.textContent = `筛选结果 ${state.accountTotal} 个，当前页 ${state.accounts.length} 个，已选择 ${state.selectedAccountIds.size} 个`;
    accountsDownloadBtnEl.disabled = state.selectedAccountIds.size === 0;
    accountsDeleteBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsCpaBatchBtnEl) accountsCpaBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsCpaPushBatchBtnEl) accountsCpaPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsSub2apiPushBatchBtnEl) accountsSub2apiPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    const pageIds = state.accounts.map((account) => account.id);
    const pageSelectedCount = pageIds.filter((id) => state.selectedAccountIds.has(id)).length;
    accountsSelectAllEl.checked = pageIds.length > 0 && pageSelectedCount === pageIds.length;
    accountsSelectAllEl.indeterminate = pageSelectedCount > 0 && pageSelectedCount < pageIds.length;
    if (accountsCpaCancelBtnEl) accountsCpaCancelBtnEl.disabled = !(state.cpaQueue && state.cpaQueue.active);
    if (accountsCpaExportBtnEl) {
      accountsCpaExportBtnEl.disabled = !(state.cpaQueue && (state.cpaQueue.results || []).length);
    }
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
          <button class="button button-secondary button-small" type="button" data-cpa-account-id="${account.id}" ${isCpaBusy(account) ? "disabled" : ""}>授权并推送</button>
          ${canPushExistingCpa(account) ? `<button class="button button-secondary button-small" type="button" data-cpa-upload-account-id="${account.id}">推送CPA</button>` : ""}
          ${canPushExistingCpa(account) ? `<button class="button button-secondary button-small" type="button" data-sub2api-upload-account-id="${account.id}">推Sub2API</button>` : ""}
          <button class="button button-secondary button-small" type="button" data-cpa-log-account-id="${account.id}">日志</button>
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
          const cpaStart = await fetchJson(`/api/accounts/${account.id}/cpa`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          if (cpaStart.queue) {
            state.cpaQueue = cpaStart.queue;
            renderCpaQueuePanel();
          }
          accountsMetaEl.textContent = `账号 ${account.email} 已加入全局 CPA 队列`;
          await refreshAccounts();
          await refreshCpaQueue();
        } catch (error) {
          accountsMetaEl.textContent = `CPA 授权启动失败: ${error.message}`;
          button.disabled = false;
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-cpa-upload-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.cpaUploadAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认将账号 ${account.email} 已生成的 CPA 授权文件推送到远程 CPA 吗？`);
        if (!confirmed) return;
        button.disabled = true;
        try {
          const cpaUpload = await fetchJson(`/api/accounts/${account.id}/cpa/upload`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          if (cpaUpload.queue) {
            state.cpaQueue = cpaUpload.queue;
            renderCpaQueuePanel();
          }
          accountsMetaEl.textContent = `账号 ${account.email} 推送已加入全局 CPA 队列`;
          await refreshAccounts();
          await refreshCpaQueue();
        } catch (error) {
          accountsMetaEl.textContent = `CPA 推送启动失败: ${error.message}`;
          button.disabled = false;
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-sub2api-upload-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.sub2apiUploadAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认将账号 ${account.email} 已生成的 CPA 授权推送到 Sub2API 吗？`);
        if (!confirmed) return;
        button.disabled = true;
        try {
          const upload = await fetchJson(`/api/accounts/${account.id}/cpa/sub2api`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          if (upload.queue) {
            state.cpaQueue = upload.queue;
            renderCpaQueuePanel();
          }
          accountsMetaEl.textContent = `账号 ${account.email} Sub2API 推送已加入全局队列`;
          await refreshAccounts();
          await refreshCpaQueue();
        } catch (error) {
          accountsMetaEl.textContent = `Sub2API 推送启动失败: ${error.message}`;
          button.disabled = false;
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-cpa-log-account-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.cpaLogAccountId = Number(button.dataset.cpaLogAccountId);
        renderAccountCpaLog(true);
      });
    });
  }

  function renderAccountCpaLog(scrollToBottom = false) {
    if (!accountCpaLogPanelEl || !accountCpaLogEl || !accountCpaLogTitleEl) {
      return;
    }
    if (!state.cpaLogAccountId) {
      accountCpaLogPanelEl.classList.add("hidden");
      return;
    }
    const account = state.accounts.find((item) => item.id === state.cpaLogAccountId);
    if (!account) {
      accountCpaLogPanelEl.classList.add("hidden");
      return;
    }
    accountCpaLogPanelEl.classList.remove("hidden");
    accountCpaLogTitleEl.textContent = `CPA 日志 · ${account.email || `#${account.id}`}`;
    const lines = [];
    lines.push(`状态: ${cpaStatusLabel(account.cpa_status)}`);
    if (account.cpa_path) lines.push(`文件: ${account.cpa_path}`);
    if (account.cpa_uploaded_at) lines.push(`推送时间: ${account.cpa_uploaded_at}`);
    if (account.cpa_error) lines.push(`错误: ${account.cpa_error}`);
    if (account.cpa_log) {
      lines.push("");
      lines.push(account.cpa_log);
    }
    accountCpaLogEl.textContent = lines.join("\n") || "暂无日志";
    if (scrollToBottom || isCpaBusy(account)) {
      accountCpaLogEl.scrollTop = accountCpaLogEl.scrollHeight;
    }
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

  function formatApiErrorDetail(detail, status, rawText) {
    if (detail == null || detail === "") {
      const body = String(rawText || "").trim();
      if (!body) return status ? `请求失败 (HTTP ${status})` : "请求失败";
      const lower = body.slice(0, 200).toLowerCase();
      if (
        lower.includes("<!doctype") ||
        lower.startsWith("<html") ||
        lower.includes("<html") ||
        body.trimStart().startsWith("<")
      ) {
        return `服务返回了 HTML/XML 而非 JSON（HTTP ${status || "?"}）。请检查 Console 地址、反代配置，或远程 CPA 管理地址是否写错。`;
      }
      return body.slice(0, 240);
    }
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
            const msg = item.msg || item.message || JSON.stringify(item);
            return loc ? `${loc}: ${msg}` : String(msg);
          }
          return String(item);
        })
        .filter(Boolean)
        .join("; ") || `请求失败 (HTTP ${status || "?"})`;
    }
    if (typeof detail === "object") {
      return detail.message || detail.error || detail.msg || JSON.stringify(detail);
    }
    return String(detail);
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const rawText = await response.text();
    const contentType = String(response.headers.get("content-type") || "").toLowerCase();
    let data = null;
    const trimmed = String(rawText || "").trim();
    if (trimmed) {
      const looksJson =
        contentType.includes("application/json") ||
        contentType.includes("+json") ||
        trimmed.startsWith("{") ||
        trimmed.startsWith("[");
      if (looksJson) {
        try {
          data = JSON.parse(trimmed);
        } catch (parseError) {
          if (!response.ok) {
            throw new Error(formatApiErrorDetail(null, response.status, trimmed));
          }
          throw new Error(
            `响应不是合法 JSON（HTTP ${response.status}）：${String(parseError.message || parseError).slice(0, 160)}`
          );
        }
      } else if (!response.ok) {
        throw new Error(formatApiErrorDetail(null, response.status, trimmed));
      } else {
        throw new Error(
          `期望 JSON 响应，但收到 ${contentType || "未知类型"}（HTTP ${response.status}）`
        );
      }
    } else if (!response.ok) {
      throw new Error(formatApiErrorDetail(null, response.status, ""));
    } else {
      data = {};
    }
    if (!response.ok) {
      throw new Error(formatApiErrorDetail(data && data.detail, response.status, rawText));
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
      renderDefaultMailDetail();
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
  if (accountCpaLogCloseBtnEl) {
    accountCpaLogCloseBtnEl.addEventListener("click", () => {
      state.cpaLogAccountId = null;
      renderAccountCpaLog();
    });
  }


  function renderCpaQueuePanel() {
    if (!accountsCpaQueuePanelEl) return;
    const q = state.cpaQueue;
    if (!q || (!q.active && !(q.results || []).length && !q.message)) {
      accountsCpaQueuePanelEl.classList.add("hidden");
      return;
    }
    accountsCpaQueuePanelEl.classList.remove("hidden");
    const total = Number(q.total || 0);
    const done = Number(q.done || 0);
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : (q.active ? 0 : 100);
    if (accountsCpaQueueBarEl) accountsCpaQueueBarEl.style.width = `${pct}%`;
    if (accountsCpaQueueTitleEl) {
      accountsCpaQueueTitleEl.textContent = q.active ? "CPA 全局队列（运行中）" : "CPA 全局队列（已结束）";
    }
    if (accountsCpaQueueMetaEl) {
      accountsCpaQueueMetaEl.textContent =
        `完成 ${done}/${total} · 成功 ${q.success || 0} · 失败 ${q.failed || 0} · 取消 ${q.cancelled || 0}`;
    }
    if (accountsCpaQueueMessageEl) {
      const cur = q.current_email || (q.current_id ? `#${q.current_id}` : "");
      accountsCpaQueueMessageEl.textContent = q.message || (cur ? `当前: ${cur}` : "空闲");
    }
    if (accountsCpaCancelBtnEl) accountsCpaCancelBtnEl.disabled = !q.active;
    if (accountsCpaExportBtnEl) accountsCpaExportBtnEl.disabled = !(q.results || []).length;
  }

  async function refreshCpaQueue() {
    try {
      const data = await fetchJson("/api/accounts/cpa/queue");
      state.cpaQueue = data.queue || null;
      renderCpaQueuePanel();
    } catch (_error) {
      // ignore poll errors
    }
  }

  async function startBatchCpa(mode) {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      accountsMetaEl.textContent = "请先选择账号";
      return;
    }
    const modeLabel = mode === "push_only"
      ? "批量推送已生成的 CPA 授权"
      : mode === "push_sub2api"
        ? "批量推送已生成的授权到 Sub2API"
        : "批量授权并推送 CPA";
    const confirmed = window.confirm(
      `确认对选中的 ${ids.length} 个账号执行「${modeLabel}」吗？\n全局单队列：一个线程 + 一个浏览器串行处理。`
    );
    if (!confirmed) return;

    if (accountsCpaBatchBtnEl) accountsCpaBatchBtnEl.disabled = true;
    if (accountsCpaPushBatchBtnEl) accountsCpaPushBatchBtnEl.disabled = true;
    if (accountsSub2apiPushBatchBtnEl) accountsSub2apiPushBatchBtnEl.disabled = true;
    try {
      const result = await fetchJson("/api/accounts/cpa/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_ids: ids,
          mode,
        }),
      });
      const accepted = Number(result.accepted_count || 0);
      const skipped = Number(result.skipped_count || 0);
      const rejected = Number(result.rejected_count || 0);
      accountsMetaEl.textContent =
        `${modeLabel}：已入队 ${accepted}（单线程单浏览器串行），跳过 ${skipped}，拒绝 ${rejected}`;
      if (result.accepted && result.accepted.length) {
        state.cpaLogAccountId = result.accepted[0].id;
      }
      if (result.queue) {
        state.cpaQueue = result.queue;
        renderCpaQueuePanel();
      }
      if (result.rejected && result.rejected.length) {
        const reasons = result.rejected
          .slice(0, 5)
          .map((item) => `${item.email || ("#" + item.id)}: ${item.reason}`)
          .join("；");
        accountsMetaEl.textContent += reasons ? `。拒绝详情: ${reasons}` : "";
      }
      await refreshAccounts();
      await refreshCpaQueue();
    } catch (error) {
      accountsMetaEl.textContent = `${modeLabel}启动失败: ${error.message}`;
    } finally {
      if (accountsCpaBatchBtnEl) accountsCpaBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
      if (accountsCpaPushBatchBtnEl) accountsCpaPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
      if (accountsSub2apiPushBatchBtnEl) accountsSub2apiPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    }
  }

  if (accountsCpaBatchBtnEl) {
    accountsCpaBatchBtnEl.addEventListener("click", () => startBatchCpa("authorize_and_push"));
  }
  if (accountsCpaPushBatchBtnEl) {
    accountsCpaPushBatchBtnEl.addEventListener("click", () => startBatchCpa("push_only"));
  }
  if (accountsSub2apiPushBatchBtnEl) {
    accountsSub2apiPushBatchBtnEl.addEventListener("click", () => startBatchCpa("push_sub2api"));
  }
  if (accountsCpaCancelBtnEl) {
    accountsCpaCancelBtnEl.addEventListener("click", async () => {
      if (!window.confirm("确认停止 CPA 队列？当前账号会跑完，剩余排队将取消。")) return;
      try {
        const data = await fetchJson("/api/accounts/cpa/queue/cancel", { method: "POST" });
        state.cpaQueue = data.queue || state.cpaQueue;
        accountsMetaEl.textContent = data.message || "已请求停止队列";
        renderCpaQueuePanel();
        await refreshAccounts();
      } catch (error) {
        accountsMetaEl.textContent = `停止队列失败: ${error.message}`;
      }
    });
  }
  if (accountsCpaExportBtnEl) {
    accountsCpaExportBtnEl.addEventListener("click", () => {
      window.open("/api/accounts/cpa/queue/export", "_blank");
    });
  }
  if (accountsSelectFilteredBtnEl) {
    accountsSelectFilteredBtnEl.addEventListener("click", async () => {
      try {
        const params = new URLSearchParams();
        if (state.accountTaskFilter && state.accountTaskFilter !== "all") {
          params.set("task_id", state.accountTaskFilter);
        }
        if (state.accountSearch && state.accountSearch.trim()) {
          params.set("search", state.accountSearch.trim());
        }
        const qs = params.toString();
        const data = await fetchJson(`/api/accounts/ids${qs ? `?${qs}` : ""}`);
        const ids = data.ids || [];
        state.selectedAccountIds = new Set(ids);
        accountsMetaEl.textContent = `已跨页选中筛选结果 ${ids.length} 个账号`;
        renderAccounts();
      } catch (error) {
        accountsMetaEl.textContent = `全选筛选结果失败: ${error.message}`;
      }
    });
  }
  accountsDownloadBtnEl.addEventListener("click", async () => {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      accountsMetaEl.textContent = "请先选择账号";
      return;
    }
    try {
      const data = await fetchJson("/api/accounts/by-ids", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: ids }),
      });
      const selected = data.accounts || [];
      downloadSsoFile(selected, `sso_selected_${Date.now()}.txt`);
    } catch (error) {
      accountsMetaEl.textContent = `下载失败: ${error.message}`;
    }
  });
  accountsSelectAllEl.addEventListener("change", () => {
    // Cross-page multi-select: only toggle current page rows
    const pageIds = state.accounts.map((account) => account.id);
    if (accountsSelectAllEl.checked) {
      pageIds.forEach((id) => state.selectedAccountIds.add(id));
    } else {
      pageIds.forEach((id) => state.selectedAccountIds.delete(id));
    }
    renderAccounts();
  });
  accountsDeleteBtnEl.addEventListener("click", async () => {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      return;
    }
    const confirmed = window.confirm(`确认批量删除 ${ids.length} 个账号吗？`);
    if (!confirmed) return;
    for (const id of ids) {
      await fetchJson(`/api/accounts/${id}`, { method: "DELETE" });
      state.selectedAccountIds.delete(id);
    }
    await refreshAccounts();
  });
  accountsSearchInputEl.addEventListener("input", () => {
    state.accountSearch = accountsSearchInputEl.value;
    state.accountPage = 1;
    refreshAccounts();
  });
  accountsTaskFilterEl.addEventListener("change", () => {
    state.accountTaskFilter = accountsTaskFilterEl.value;
    state.accountPage = 1;
    refreshAccounts();
  });
  accountsPageSizeEl.addEventListener("change", () => {
    state.accountPageSize = Number(accountsPageSizeEl.value) || 20;
    state.accountPage = 1;
    refreshAccounts();
  });
  accountsPrevPageBtnEl.addEventListener("click", () => {
    state.accountPage = Math.max(1, state.accountPage - 1);
    refreshAccounts();
  });
  accountsNextPageBtnEl.addEventListener("click", () => {
    state.accountPage += 1;
    refreshAccounts();
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
      temp_mail_domains_removed: String(settingsFormEl.elements.temp_mail_domains_removed?.value || "").trim(),
      domain_auth_fail_threshold: Number(settingsFormEl.elements.domain_auth_fail_threshold?.value ?? 3) || 3,
      domain_auth_fail_auto_remove: Boolean(settingsFormEl.elements.domain_auth_fail_auto_remove?.checked),
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
      cpa_cloud_upload_timeout: Number(settingsFormEl.elements.cpa_cloud_upload_timeout.value) || 30,
      cpa_cloud_upload_retries: Number(settingsFormEl.elements.cpa_cloud_upload_retries.value) || 3,
      cpa_batch_retry_count: Number(settingsFormEl.elements.cpa_batch_retry_count?.value ?? 1),
      cpa_mint_browser_recycle_every: Number(settingsFormEl.elements.cpa_mint_browser_recycle_every?.value ?? 15),
      cpa_health_check_before_upload: Boolean(settingsFormEl.elements.cpa_health_check_before_upload?.checked),
      cpa_health_check_timeout: Number(settingsFormEl.elements.cpa_health_check_timeout?.value ?? 15),
      cpa_health_check_model: String(settingsFormEl.elements.cpa_health_check_model?.value || "grok-4.5").trim() || "grok-4.5",
      cpa_health_check_headers: String(settingsFormEl.elements.cpa_health_check_headers?.value || "").trim(),
      cpa_health_check_use_file_headers: Boolean(settingsFormEl.elements.cpa_health_check_use_file_headers?.checked),
      sub2api_upload_enabled: Boolean(settingsFormEl.elements.sub2api_upload_enabled?.checked),
      sub2api_export_enabled: Boolean(settingsFormEl.elements.sub2api_export_enabled?.checked),
      sub2api_api_base: String(settingsFormEl.elements.sub2api_api_base?.value || "").trim(),
      sub2api_upload_timeout: Number(settingsFormEl.elements.sub2api_upload_timeout?.value) || 30,
      sub2api_upload_retries: Number(settingsFormEl.elements.sub2api_upload_retries?.value) || 3,
      sub2api_platform: String(settingsFormEl.elements.sub2api_platform?.value || "openai").trim() || "openai",
      sub2api_account_type: String(settingsFormEl.elements.sub2api_account_type?.value || "oauth").trim() || "oauth",
      sub2api_account_concurrency: Number(settingsFormEl.elements.sub2api_account_concurrency?.value ?? 10) || 10,
      sub2api_account_priority: Number(settingsFormEl.elements.sub2api_account_priority?.value ?? 1),
      sub2api_account_group_ids: String(settingsFormEl.elements.sub2api_account_group_ids?.value || "").trim(),
      sub2api_default_proxy: String(settingsFormEl.elements.sub2api_default_proxy?.value || "").trim(),
      sub2api_local_export: Boolean(settingsFormEl.elements.sub2api_local_export?.checked),
      sub2api_local_export_dir: String(settingsFormEl.elements.sub2api_local_export_dir?.value || "./sub2api_exports").trim() || "./sub2api_exports",
    };
    const managementKey = settingsFormEl.elements.cpa_cloud_management_key.value.trim();
    if (managementKey) payload.cpa_cloud_management_key = managementKey;
    const sub2Key = String(settingsFormEl.elements.sub2api_api_key?.value || "").trim();
    if (sub2Key) payload.sub2api_api_key = sub2Key;
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
  renderDefaultMailDetail();
  renderAccountFilters();
  updateMetrics();
  activateTab("register");
  refreshHealth();
  refreshAccounts();
  refreshAll();
  window.setInterval(refreshAll, 2000);
  window.setInterval(refreshHealth, 15000);
  window.setInterval(refreshAccounts, 5000);
  window.setInterval(refreshCpaQueue, 2000);
  window.setInterval(updateMetrics, 3000);
})();
