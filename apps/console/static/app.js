(function () {
  const state = {
    tasks: [],
    selectedTaskId: null,
    accounts: [],
    selectedAccountId: null,
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
  const toggleSettingsBtnEl = document.getElementById("toggleSettingsBtn");
  const toggleAdvancedBtnEl = document.getElementById("toggleAdvancedBtn");
  const toggleMailBtnEl = document.getElementById("toggleMailBtn");
  const advancedFieldsEl = document.getElementById("advancedFields");
  const healthGridEl = document.getElementById("healthGrid");
  const healthMetaEl = document.getElementById("healthMeta");
  const accountsRefreshBtnEl = document.getElementById("accountsRefreshBtn");
  const accountsMetaEl = document.getElementById("accountsMeta");
  const accountsListEl = document.getElementById("accountsList");
  const accountDetailEl = document.getElementById("accountDetail");

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
  }

  function statusClass(status) {
    return `status-pill status-${status || "unknown"}`;
  }

  function healthClass(ok) {
    return ok ? "health-pill health-ok" : "health-pill health-bad";
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
        <div class="task-subrow">执行次数 ${task.target_count}</div>
        <div class="task-subrow">本地账号 ${task.account_count || 0}</div>
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

  function renderAccountDetail(account) {
    if (!account) {
      accountDetailEl.className = "account-detail empty";
      accountDetailEl.textContent = "选择账号后显示详情";
      return;
    }

    accountDetailEl.className = "account-detail";
    accountDetailEl.innerHTML = `
      <div class="account-detail-head">
        <div>
          <div class="meta-item-label">当前账号</div>
          <h3>${escapeHtml(account.email)}</h3>
        </div>
        <button class="button button-danger button-small" type="button" data-delete-account-id="${account.id}">删除账号</button>
      </div>
      <div class="detail-grid account-fields">
        ${[
          ["任务", `#${account.task_id} ${account.task_name || ""}`],
          ["姓名", `${account.given_name || "-"} ${account.family_name || ""}`.trim() || "-"],
          ["密码", account.password || "-"],
          ["创建时间", account.created_at || "-"],
          ["导入时间", account.imported_at || "-"],
          ["来源文件", account.source_file || "-"],
        ].map(([label, value]) => `
          <div class="meta-item">
            <div class="meta-item-label">${escapeHtml(label)}</div>
            <div class="meta-item-value">${escapeHtml(value)}</div>
          </div>
        `).join("")}
      </div>
      <div class="account-token-block">
        <div class="meta-item-label">SSO</div>
        <pre>${escapeHtml(account.sso || "-")}</pre>
      </div>
    `;

    accountDetailEl.querySelector("[data-delete-account-id]").addEventListener("click", async () => {
      const confirmed = window.confirm(`确认删除账号 ${account.email} 吗？`);
      if (!confirmed) return;
      await fetchJson(`/api/accounts/${account.id}`, { method: "DELETE" });
      state.selectedAccountId = null;
      await refreshAccounts();
    });
  }

  function renderAccounts() {
    accountsMetaEl.textContent = `本地账号 ${state.accounts.length} 个`;
    if (!state.accounts.length) {
      accountsListEl.innerHTML = '<div class="empty">暂无账号</div>';
      renderAccountDetail(null);
      return;
    }

    if (!state.selectedAccountId || !state.accounts.some((item) => item.id === state.selectedAccountId)) {
      state.selectedAccountId = state.accounts[0].id;
    }

    accountsListEl.innerHTML = state.accounts.map((account) => `
      <button class="account-row ${account.id === state.selectedAccountId ? "selected" : ""}" type="button" data-account-id="${account.id}">
        <span>${escapeHtml(account.email)}</span>
        <span>#${account.task_id} ${escapeHtml(account.task_name || "")}</span>
        <span>${escapeHtml(account.created_at || "-")}</span>
      </button>
    `).join("");

    accountsListEl.querySelectorAll("[data-account-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedAccountId = Number(button.dataset.accountId);
        renderAccounts();
      });
    });

    renderAccountDetail(state.accounts.find((item) => item.id === state.selectedAccountId));
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
    const data = await fetchJson("/api/tasks");
    state.tasks = data.tasks || [];
    if (!state.selectedTaskId && state.tasks.length) {
      state.selectedTaskId = state.tasks[0].id;
    }
    renderTaskList();
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
      const data = await fetchJson("/api/accounts");
      state.accounts = data.accounts || [];
      renderAccounts();
    } catch (error) {
      accountsMetaEl.textContent = `账号加载失败: ${error.message}`;
      accountsListEl.innerHTML = '<div class="empty">账号加载失败</div>';
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

  settingsFormEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      proxy: settingsFormEl.elements.proxy.value.trim(),
      browser_proxy: settingsFormEl.elements.browser_proxy.value.trim(),
      temp_mail_api_base: settingsFormEl.elements.temp_mail_api_base.value.trim(),
      temp_mail_admin_password: settingsFormEl.elements.temp_mail_admin_password.value.trim(),
      temp_mail_domain: settingsFormEl.elements.temp_mail_domain.value.trim(),
      temp_mail_site_password: settingsFormEl.elements.temp_mail_site_password.value.trim(),
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

  toggleSettingsBtnEl.addEventListener("click", () => {
    settingsFormEl.classList.toggle("hidden");
    toggleSettingsBtnEl.textContent = settingsFormEl.classList.contains("hidden") ? "展开系统默认配置" : "收起系统默认配置";
  });

  toggleMailBtnEl.addEventListener("click", () => {
    detailMetaEl.classList.toggle("hidden");
    toggleMailBtnEl.textContent = detailMetaEl.classList.contains("hidden") ? "展开临时邮箱参数" : "收起临时邮箱参数";
  });

  setDefaults();
  refreshHealth();
  refreshAccounts();
  refreshAll();
  window.setInterval(refreshAll, 2000);
  window.setInterval(refreshHealth, 15000);
  window.setInterval(refreshAccounts, 5000);
})();
