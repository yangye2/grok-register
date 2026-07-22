(function () {
  const ACTIVE_TASK_STATUSES = new Set(["queued", "running", "stopping"]);

  const state = {
    tasks: [],
    selectedTaskId: null,
    logsLoadedTaskId: null,
    accounts: [],
    selectedAccountIds: new Set(),
    accountSearch: "",
    accountTaskFilter: "all",
    accountCpaFilter: "all",
    accountTokenFilter: "all",
    accountSub2Filter: "all",
    accountGrok2Filter: "all",
    accountSsoFilter: "all",
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
  const logoutBtnEl = document.getElementById("logoutBtn");
  const sidebarUserEl = document.getElementById("sidebarUser");
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
  const accountsProbeBatchBtnEl = document.getElementById("accountsProbeBatchBtn");
  const accountsRefreshTokenBatchBtnEl = document.getElementById("accountsRefreshTokenBatchBtn");
  const accountsOauthBatchBtnEl = document.getElementById("accountsOauthBatchBtn");
  const accountsCpaPushBatchBtnEl = document.getElementById("accountsCpaPushBatchBtn");
  const accountsSub2apiPushBatchBtnEl = document.getElementById("accountsSub2apiPushBatchBtn");
  const accountsGrok2MarkBtnEl = document.getElementById("accountsGrok2MarkBtn");
  const accountsGrok2UnmarkBtnEl = document.getElementById("accountsGrok2UnmarkBtn");
  const accountsCpaCancelBtnEl = document.getElementById("accountsCpaCancelBtn");
  const accountsCpaExportBtnEl = document.getElementById("accountsCpaExportBtn");
  const accountsSelectFilteredBtnEl = document.getElementById("accountsSelectFilteredBtn");
  const accountsClearFilteredBtnEl = document.getElementById("accountsClearFilteredBtn");
  const accountsLogBtnEl = document.getElementById("accountsLogBtn");
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
  const accountsCpaFilterEl = document.getElementById("accountsCpaFilter");
  const accountsTokenFilterEl = document.getElementById("accountsTokenFilter");
  const accountsSub2FilterEl = document.getElementById("accountsSub2Filter");
  const accountsGrok2FilterEl = document.getElementById("accountsGrok2Filter");
  const accountsSsoFilterEl = document.getElementById("accountsSsoFilter");
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


  /** Normalize mixed log tags into a short level token. */
  function normalizeLogLevel(raw) {
    const t = String(raw || "").trim().toLowerCase();
    if (t === "ok" || t === "success") return "OK";
    if (!t || t === "*" || t === "info") return "INFO";
    if (t === "error" || t === "fail" || t === "failed" || t === "err") return "ERROR";
    if (t === "warn" || t === "warning") return "WARN";
    if (t === "debug" || t === "dbg" || t === "trace") return "DEBUG";
    return "INFO";
  }

  function shortLogTime(value) {
    const t = String(value || "").trim();
    if (!t) return "";
    const m = t.match(/(\d{2}:\d{2}:\d{2})/);
    if (m) return m[1];
    if (t.length >= 19 && t[10] === "T") return t.slice(11, 19);
    return t.length > 19 ? t.slice(0, 19) : t;
  }

  /**
   * Parse one raw log line from task console / account cpa_log.
   * Supports:
   *   [2026-07-20T12:00:00+08:00] message
   *   2026-07-20 12:00:00 | message
   *   [Debug]/Error]/Warn]/Info]|[*]|[OK] message
   */
  function parseLogLine(raw) {
    let line = String(raw ?? "").replace(/\r/g, "");
    if (!line.trim()) {
      return { time: "", level: "META", msg: "", empty: true, raw: line };
    }
    const trimmed = line.trim();
    if (/^-{3,}/.test(trimmed) || /^={3,}/.test(trimmed) || /^[\u2014-]{3,}/.test(trimmed)) {
      return { time: "", level: "META", msg: trimmed, empty: false, raw: line, section: true };
    }
    // Chinese section titles: \u8be6\u7ec6\u65e5\u5fd7 / \u64cd\u4f5c\u65e5\u5fd7
    if (trimmed.indexOf("\u8be6\u7ec6\u65e5\u5fd7") >= 0 || trimmed.indexOf("\u64cd\u4f5c\u65e5\u5fd7") >= 0) {
      return { time: "", level: "META", msg: trimmed, empty: false, raw: line, section: true };
    }

    let time = "";
    let msg = line;

    let m = msg.match(/^\[(\d{4}-\d{2}-\d{2}[T ][^\]]+)\]\s*(.*)$/);
    if (m) {
      time = m[1].replace("T", " ").replace(/\.\d+/, "").replace(/\+[\d:]+$/, "").replace(/Z$/, "");
      msg = m[2];
    } else {
      m = msg.match(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s*[|]\s*(.*)$/);
      if (m) {
        time = m[1].replace("T", " ").replace(/[.,]\d+$/, "");
        msg = m[2];
      } else {
        m = msg.match(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s+(.*)$/);
        if (m) {
          time = m[1].replace("T", " ");
          msg = m[2];
        }
      }
    }

    let level = "INFO";
    m = msg.match(/^\[(\*|Debug|Dbg|Error|Err|Warn(?:ing)?|Info|OK|Success|Fail(?:ed)?|Trace)\]\s*/i);
    if (m) {
      level = normalizeLogLevel(m[1]);
      msg = msg.slice(m[0].length);
    } else {
      const low = msg.toLowerCase();
      if (/(error|exception|traceback|failed)/i.test(msg)
          && !/(no error|without error|fail_count\s*[:=]\s*0)/i.test(msg)
          && (msg.indexOf("\u5931\u8d25") >= 0 || msg.indexOf("\u5f02\u5e38") >= 0 || msg.indexOf("\u9519\u8bef") >= 0
              || /(error|exception|traceback|failed)/i.test(msg))) {
        // severity by keywords (EN + CN)
        if (/(error|exception|traceback|failed)/i.test(msg)
            || msg.indexOf("\u5931\u8d25") >= 0
            || msg.indexOf("\u5f02\u5e38") >= 0
            || msg.indexOf("\u9519\u8bef") >= 0) {
          level = "ERROR";
        }
      }
      if (level === "INFO" && (/\bwarn(ing)?\b/i.test(msg) || msg.indexOf("\u8b66\u544a") >= 0)) {
        level = "WARN";
      }
      if (level === "INFO" && (/^\[?debug\b/i.test(msg) || /\[debug\]/i.test(line))) {
        level = "DEBUG";
      }
    }

    m = msg.match(/^(\[[^\]]+\]\s*)\[(Debug|Error|Warn(?:ing)?|Info|OK|Success|Fail(?:ed)?)\]\s*/i);
    if (m) {
      level = normalizeLogLevel(m[2]);
      msg = msg.replace(/^(\[[^\]]+\]\s*)\[(Debug|Error|Warn(?:ing)?|Info|OK|Success|Fail(?:ed)?)\]\s*/i, "$1");
    }

    msg = String(msg || "").trimEnd();
    if (!msg) msg = line.trim();
    return { time, level, msg, empty: false, raw: line, section: false };
  }

  function renderLogHtml(lines, options = {}) {
    const list = Array.isArray(lines) ? lines : String(lines || "").split(/\r?\n/);
    const showTime = options.showTime !== false;
    const emptyText = options.emptyText || "\u6682\u65e0\u65e5\u5fd7";
    if (!list.length || (list.length === 1 && !String(list[0] || "").trim())) {
      return `<div class="log-line log-level-meta"><span class="log-msg">${escapeHtml(emptyText)}</span></div>`;
    }
    const parts = [];
    for (const raw of list) {
      const item = parseLogLine(raw);
      if (item.empty) {
        parts.push(`<div class="log-line log-empty"></div>`);
        continue;
      }
      if (item.section) {
        parts.push(
          `<div class="log-line log-level-meta log-section"><span class="log-msg">${escapeHtml(item.msg)}</span></div>`
        );
        continue;
      }
      const lv = item.level || "INFO";
      const timeHtml = showTime && item.time
        ? `<span class="log-time" title="${escapeHtml(item.time)}">${escapeHtml(shortLogTime(item.time))}</span>`
        : (showTime ? `<span class="log-time log-time-empty"></span>` : "");
      parts.push(
        `<div class="log-line log-level-${lv.toLowerCase()}">` +
          timeHtml +
          `<span class="log-level">${escapeHtml(lv)}</span>` +
          `<span class="log-msg">${escapeHtml(item.msg)}</span>` +
        `</div>`
      );
    }
    return parts.join("");
  }

  function setLogContent(el, lines, options = {}) {
    if (!el) return;
    el.innerHTML = renderLogHtml(lines, options);
    if (options.scrollToBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }

  function setDefaults() {
    const defaults = window.__DEFAULTS__ || {};
    formEl.elements.name.value = `grok-task-${Date.now()}`;
    formEl.elements.count.value = defaults.run?.count || 50;
    settingsFormEl.elements.proxy.value = defaults.proxy || "";
    settingsFormEl.elements.browser_proxy.value = defaults.browser_proxy || "";
    if (settingsFormEl.elements.max_concurrent_tasks) {
      settingsFormEl.elements.max_concurrent_tasks.value = defaults.max_concurrent_tasks ?? 1;
    }
    const maxConcEl = document.getElementById("maxConcurrentTasksDisplay");
    if (maxConcEl) maxConcEl.textContent = String(defaults.max_concurrent_tasks ?? 1);
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
    if (settingsFormEl.elements.email_provider) {
      settingsFormEl.elements.email_provider.value = defaults.email_provider || "duckmail";
    }
    const _omSet = (name, value, isCheck=false) => {
      const el = settingsFormEl.elements[name];
      if (!el) return;
      if (isCheck) el.checked = Boolean(value);
      else el.value = value;
    };
    _omSet("outmail_api_base", defaults.outmail_api_base || "");
    _omSet("outmail_api_key", defaults.outmail_api_key || "");
    _omSet("outmail_session_cookie", defaults.outmail_session_cookie || "");
    _omSet("outmail_proxy", defaults.outmail_proxy || "");
    _omSet("outmail_from_filter", defaults.outmail_from_filter || "x.ai");
    _omSet("outmail_subject_filter", defaults.outmail_subject_filter || "xAI");
    _omSet("outmail_anonymous_provider", defaults.outmail_anonymous_provider || "cloudflare");
    _omSet("outmail_anonymous_domain", defaults.outmail_anonymous_domain || "");
    _omSet("outmail_anonymous_username_prefix", defaults.outmail_anonymous_username_prefix || "");
    _omSet("outmail_anonymous_password", defaults.outmail_anonymous_password || "");
    _omSet("outmail_poll_timeout_sec", defaults.outmail_poll_timeout_sec ?? 180);
    _omSet("outmail_poll_interval_sec", defaults.outmail_poll_interval_sec ?? 5);
    _omSet("outmail_plus_alias", defaults.outmail_plus_alias !== false, true);
    _omSet("outmail_plus_alias_count", defaults.outmail_plus_alias_count ?? 1);
    _omSet("outmail_alias_suffix_len", defaults.outmail_alias_suffix_len ?? 6);
    _omSet("outmail_fetch_top", defaults.outmail_fetch_top ?? 10);
    _omSet("outmail_since_padding_sec", defaults.outmail_since_padding_sec ?? 30);
    _omSet("outmail_group_id", defaults.outmail_group_id || "");
    _omSet("outmail_used_file", defaults.outmail_used_file || "outmail_used_mailboxes.txt");

    _omSet("outmail_anonymous_enabled", Boolean(defaults.outmail_anonymous_enabled), true);
    _omSet("outmail_anonymous_delete_after", Boolean(defaults.outmail_anonymous_delete_after), true);
    _omSet("outmail_exclude_used", defaults.outmail_exclude_used !== false, true);
    settingsFormEl.elements.cpa_auth_dir.value = defaults.cpa_auth_dir || "./cpa_auths";
    settingsFormEl.elements.cpa_proxy.value = defaults.cpa_proxy || "";
    settingsFormEl.elements.cpa_hotload_dir.value = defaults.cpa_hotload_dir || "";
    settingsFormEl.elements.cpa_mint_timeout_sec.value = defaults.cpa_mint_timeout_sec || 300;
    if (settingsFormEl.elements.cpa_prefer_sso_oauth) {
      settingsFormEl.elements.cpa_prefer_sso_oauth.checked = defaults.cpa_prefer_sso_oauth !== false;
    }
    if (settingsFormEl.elements.cpa_probe_after_write) {
      settingsFormEl.elements.cpa_probe_after_write.checked = defaults.cpa_probe_after_write !== false;
    }
    if (settingsFormEl.elements.cpa_probe_delay_sec) {
      settingsFormEl.elements.cpa_probe_delay_sec.value = defaults.cpa_probe_delay_sec ?? 5;
    }
    if (settingsFormEl.elements.cpa_probe_required) {
      settingsFormEl.elements.cpa_probe_required.checked = Boolean(defaults.cpa_probe_required);
    }
    if (settingsFormEl.elements.cpa_post_task_oauth_enabled) {
      settingsFormEl.elements.cpa_post_task_oauth_enabled.checked = Boolean(defaults.cpa_post_task_oauth_enabled);
    }
    if (settingsFormEl.elements.cpa_post_task_refresh_enabled) {
      settingsFormEl.elements.cpa_post_task_refresh_enabled.checked = defaults.cpa_post_task_refresh_enabled !== false;
    }
    settingsFormEl.elements.cpa_export_enabled.checked = Boolean(defaults.cpa_export_enabled);
    settingsFormEl.elements.cpa_copy_to_hotload.checked = Boolean(defaults.cpa_copy_to_hotload);
    settingsFormEl.elements.cpa_headless.checked = Boolean(defaults.cpa_headless);
    settingsFormEl.elements.cpa_cloud_upload_enabled.checked = defaults.cpa_cloud_upload_enabled !== false;
    if (settingsFormEl.elements.cpa_register_push_enabled) {
      settingsFormEl.elements.cpa_register_push_enabled.checked = Boolean(defaults.cpa_register_push_enabled);
    }
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
      settingsFormEl.elements.sub2api_upload_enabled.checked = defaults.sub2api_upload_enabled !== false;
    }
    if (settingsFormEl.elements.sub2api_register_push_enabled) {
      settingsFormEl.elements.sub2api_register_push_enabled.checked = Boolean(defaults.sub2api_register_push_enabled);
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
      {
      let p = String(defaults.sub2api_platform || "grok").trim().toLowerCase() || "grok";
      if (p === "openai" || p === "chatgpt" || p === "codex") p = "grok";
      settingsFormEl.elements.sub2api_platform.value = p;
    }
    }
    if (settingsFormEl.elements.sub2api_account_type) {
      settingsFormEl.elements.sub2api_account_type.value = defaults.sub2api_account_type || "oauth";
    }
    if (settingsFormEl.elements.sub2api_account_concurrency) {
      settingsFormEl.elements.sub2api_account_concurrency.value = defaults.sub2api_account_concurrency ?? 1;
    }
    if (settingsFormEl.elements.sub2api_account_priority) {
      settingsFormEl.elements.sub2api_account_priority.value = defaults.sub2api_account_priority ?? 1;
    if (settingsFormEl.elements.sub2api_account_load_factor) {
      settingsFormEl.elements.sub2api_account_load_factor.value = defaults.sub2api_account_load_factor ?? 10;
    }
    if (settingsFormEl.elements.sub2api_account_rate_multiplier) {
      settingsFormEl.elements.sub2api_account_rate_multiplier.value = defaults.sub2api_account_rate_multiplier ?? 1;
    }

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

  function parseTaskTimestamp(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    // Backend stores local wall time as "YYYY-MM-DD HH:MM:SS".
    const normalized = text.includes("T") ? text : text.replace(" ", "T");
    const ms = Date.parse(normalized);
    return Number.isFinite(ms) ? ms : null;
  }

  function formatElapsedSeconds(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "-";
    const total = Math.max(0, Math.floor(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours > 0) {
      return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(secs).padStart(2, "0")}s`;
    }
    if (minutes > 0) {
      return `${minutes}m ${String(secs).padStart(2, "0")}s`;
    }
    return `${secs}s`;
  }

  function getTaskTimingStats(task) {
    // Prefer backend-derived fields; recompute as fallback for older API responses.
    const startedAt = task?.started_at || "";
    const finishedAt = task?.finished_at || "";
    const status = String(task?.status || "");
    const completed = Math.max(Number(task?.completed_count) || 0, Number(task?.account_count) || 0);

    // For active tasks, recompute live so elapsed ticks between polls.
    if (["running", "stopping"].includes(status)) {
      const startMs = parseTaskTimestamp(startedAt);
      if (startMs == null) {
        return {
          startedAt: "-",
          elapsedSeconds: null,
          elapsedDisplay: "-",
          accountsPerMinute: null,
          accountsPerMinuteDisplay: "-",
        };
      }
      const liveElapsed = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      const liveRate = Number(((completed * 60) / Math.max(liveElapsed, 1)).toFixed(2));
      return {
        startedAt: startedAt || "-",
        elapsedSeconds: liveElapsed,
        elapsedDisplay: formatElapsedSeconds(liveElapsed),
        accountsPerMinute: liveRate,
        accountsPerMinuteDisplay: `${liveRate.toFixed(2)}/min`,
      };
    }

    let elapsedSeconds = task?.elapsed_seconds;
    if (elapsedSeconds == null || !Number.isFinite(Number(elapsedSeconds))) {
      const startMs = parseTaskTimestamp(startedAt);
      if (startMs == null) {
        elapsedSeconds = null;
      } else {
        const endMs = parseTaskTimestamp(finishedAt) || Date.now();
        elapsedSeconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
      }
    } else {
      elapsedSeconds = Math.max(0, Math.floor(Number(elapsedSeconds)));
    }

    let rate = task?.accounts_per_minute;
    if (rate == null || !Number.isFinite(Number(rate))) {
      rate = elapsedSeconds == null
        ? null
        : Number(((completed * 60) / Math.max(elapsedSeconds, 1)).toFixed(2));
    } else {
      rate = Number(Number(rate).toFixed(2));
    }

    const elapsedDisplay = (task?.elapsed_display && task.elapsed_display !== "-")
      ? String(task.elapsed_display)
      : formatElapsedSeconds(elapsedSeconds);
    const rateDisplay = (task?.accounts_per_minute_display && task.accounts_per_minute_display !== "-")
      ? String(task.accounts_per_minute_display)
      : (rate == null ? "-" : `${rate.toFixed(2)}/min`);

    return {
      startedAt: startedAt || "-",
      elapsedSeconds,
      elapsedDisplay: elapsedSeconds == null ? "-" : elapsedDisplay,
      accountsPerMinute: rate,
      accountsPerMinuteDisplay: elapsedSeconds == null ? "-" : rateDisplay,
    };
  }

  async function fetchAllTasksForUi() {
    // 后端 page_size 上限 200，用分页拉全量（筛选下拉用）
    const pageSize = 200;
    let page = 1;
    let totalPages = 1;
    const tasks = [];
    while (page <= totalPages) {
      const data = await fetchJson(`/api/tasks?page=${page}&page_size=${pageSize}`);
      tasks.push(...(data.tasks || []));
      totalPages = Number(data.pagination?.total_pages || 1);
      page += 1;
      if (page > 50) break; // 安全上限
    }
    return tasks;
  }

  async function updateMetrics() {
    try {
      // 用 pagination.total，避免 page_size 超限导致 422 后指标一直为 0
      const tasksMeta = await fetchJson("/api/tasks?page_size=1");
      const taskTotal = Number(tasksMeta.pagination?.total || 0);
      let running = 0;
      for (const st of ["queued", "running", "stopping"]) {
        const d = await fetchJson(`/api/tasks?status=${encodeURIComponent(st)}&page_size=1`);
        running += Number(d.pagination?.total || 0);
      }
      const accountsMeta = await fetchJson("/api/accounts?page_size=1");
      const accountTotal = Number(accountsMeta.pagination?.total || 0);

      metricTaskTotalEl.textContent = String(taskTotal);
      metricTaskRunningEl.textContent = String(running);
      metricAccountTotalEl.textContent = String(accountTotal);
      metricAccountSelectedEl.textContent = String(state.selectedAccountIds.size);
    } catch (error) {
      console.error("Failed to update metrics:", error);
    }
  }

  async function renderAccountFilters() {
    try {
      let optionsData = [];
      try {
        const optRes = await fetchJson("/api/accounts/task-options");
        optionsData = Array.isArray(optRes.items) ? optRes.items : [];
      } catch (_e) {
        const allTasks = await fetchAllTasksForUi();
        optionsData = allTasks.map((task) => ({
          id: task.id,
          name: task.name,
          account_count: task.account_count || 0,
          deleted: false,
        }));
      }
      const options = [
        '<option value="all">全部任务</option>',
        ...optionsData.map((task) => {
          const deleted = !!task.deleted;
          const label = deleted
            ? `#${task.id} ${task.name}`.replace(/\(已删除\)$/, "") + " (任务已删除)"
            : `#${task.id} ${task.name}`;
          return `<option value="${task.id}" ${state.accountTaskFilter === String(task.id) ? "selected" : ""}>${escapeHtml(label)}</option>`;
        }),
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

    taskListEl.innerHTML = state.tasks.map((task) => {
      const timing = getTaskTimingStats(task);
      const progress = getTaskProgress(task);
      return `
      <div class="task-card ${task.id === state.selectedTaskId ? "selected" : ""}" data-task-id="${task.id}" role="button" tabindex="0">
        <div class="task-row">
          <strong title="${escapeHtml(task.name)}">#${task.id} ${escapeHtml(task.name)}</strong>
          <span class="${statusClass(task.status)}">${escapeHtml(task.status)}</span>
        </div>
        <div class="task-meta-group">
          <div class="task-subrow"><span>执行次数 ${task.target_count}</span><span>本地账号 ${task.account_count || 0}</span></div>
          <div class="task-subrow"><span>开始 ${escapeHtml(timing.startedAt)}</span><span>用时 ${escapeHtml(timing.elapsedDisplay)} · ${escapeHtml(timing.accountsPerMinuteDisplay)}</span></div>
        </div>
        <div class="task-card-progress">
          <div class="task-row">
            <span class="task-action-hint">进度 ${progress}%</span>
            <span class="task-action-hint">${task.completed_count}/${task.target_count}</span>
          </div>
          <div class="task-progress-bar">
            <div class="task-progress-fill" style="width:${progress}%"></div>
          </div>
        </div>
        <div class="task-actions">
          <span class="task-action-hint">点击查看日志</span>
          <button class="button button-danger button-small" type="button" data-delete-task-id="${task.id}" data-account-count="${task.account_count || 0}">删除</button>
        </div>
      </div>
    `;
    }).join("");

    taskListEl.querySelectorAll("[data-task-id]").forEach((card) => {
      const selectTask = () => {
        state.selectedTaskId = Number(card.dataset.taskId);
        state.logsLoadedTaskId = null;
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
        const accountHint = button.dataset.accountCount
          ? `已注册 ${button.dataset.accountCount} 个账号会保留在账号管理中。\n`
          : "";
        const confirmed = window.confirm(
          `确认删除任务 #${taskId} 吗？\n` +
          accountHint +
          "将删除任务运行文件与日志，不会删除已入库账号。"
        );
        if (!confirmed) return;
        try {
          const delRes = await fetchJson(`/api/tasks/${taskId}`, { method: "DELETE" });
          if (delRes && typeof delRes.kept_accounts === "number") {
            formMessageEl.textContent = `任务 #${taskId} 已删除，保留账号 ${delRes.kept_accounts} 个`;
            formMessageEl.className = "form-message success";
          }
          if (state.selectedTaskId === taskId) {
            state.selectedTaskId = null;
            detailTitleEl.textContent = "实时控制台";
            detailSummaryEl.innerHTML = "";
            detailMetaEl.innerHTML = "";
            state.logsLoadedTaskId = null;
            consoleOutputEl.textContent = "选择任务后显示输出";
          }
          await refreshTasks();
          await refreshDetail();
          if (typeof refreshAccounts === "function") {
            await refreshAccounts();
            await renderAccountFilters();
          }
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
    const timing = getTaskTimingStats(task);
    detailSummaryEl.innerHTML = [
      ["状态", task.status],
      ["目标次数", task.target_count],
      ["成功数", task.completed_count],
      ["失败数", task.failed_count],
      ["账号数", task.account_count || 0],
      ["开始时间", timing.startedAt],
      ["用时", timing.elapsedDisplay],
      ["账号/分", timing.accountsPerMinuteDisplay],
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
    // uploaded 显示 "CPA"：表示已推送到远程 CPA
    return ({
      not_started: "未授权",
      queued: "排队中",
      running: "授权中",
      uploading: "推送中",
      generated: "已生成",
      uploaded: "CPA",
      invalid: "测活失败",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status || "未授权");
  }

  function sub2StatusLabel(status) {
    // uploaded 显示 "Sub2"：表示已推送到 Sub2API
    return ({
      not_started: "-",
      queued: "排队中",
      running: "推送中",
      uploading: "推送中",
      uploaded: "Sub2",
      failed: "失败",
      invalid: "失败",
      cancelled: "已取消",
    }[status] || status || "-");
  }

  function sub2StatusTone(status) {
    const s = String(status || "not_started");
    if (s === "uploaded") return "tone-ok";
    if (s === "queued" || s === "running" || s === "uploading") return "tone-info";
    if (s === "failed" || s === "invalid") return "tone-bad";
    return "tone-mute";
  }

  function flagLabel(value) {
    return value ? "已推送" : "未推送";
  }

  function flagTone(value) {
    return value ? "tone-ok" : "tone-mute";
  }

  

  

  function appendAccountFilterParams(params) {
    if (state.accountTaskFilter && state.accountTaskFilter !== "all") {
      params.set("task_id", state.accountTaskFilter);
    }
    if (state.accountSearch && state.accountSearch.trim()) {
      params.set("search", state.accountSearch.trim());
    }
    if (state.accountCpaFilter && state.accountCpaFilter !== "all") {
      params.set("cpa_status", state.accountCpaFilter);
    }
    if (state.accountTokenFilter && state.accountTokenFilter !== "all") {
      params.set("token_status", state.accountTokenFilter);
    }
    if (state.accountSub2Filter && state.accountSub2Filter !== "all") {
      params.set("sub2_status", state.accountSub2Filter);
    }
    if (state.accountGrok2Filter && state.accountGrok2Filter !== "all") {
      params.set("grok2", state.accountGrok2Filter);
    }
if (state.accountSsoFilter && state.accountSsoFilter !== "all") {
      params.set("sso_alive", state.accountSsoFilter);
    }
    return params;
  }

  async function copyTextToClipboard(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (e) {}
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch (e) {
      return false;
    }
  }

  function shortSso(value, head = 10, tail = 6) {
    const s = String(value || "").trim();
    if (!s) return "-";
    if (s.length <= head + tail + 3) return s;
    return s.slice(0, head) + "..." + s.slice(-tail);
  }

  function tokenStatusLabel(status) {
    const map = {
      unknown: "未知",
      alive: "有效",
      dead: "失效",
      sso_dead: "SSO失效",
      api_dead: "API失效",
      refresh_failed: "续期失败",
      refresh_invalid: "RT失效",
      oauth_failed: "授权失败",
      error: "异常",
      refreshed: "已续期",
    };
    return map[status] || status || "未知";
  }

  function ssoAliveLabel(value) {
    if (value === 1 || value === true || value === "1") return "存活";
    if (value === 0 || value === false || value === "0") return "失效";
    return "-";
  }

  function cpaStatusTone(status) {
    const s = String(status || "not_started");
    if (s === "uploaded" || s === "generated") return "tone-ok";
    if (s === "queued" || s === "running" || s === "uploading") return "tone-info";
    if (s === "invalid" || s === "failed") return "tone-bad";
    if (s === "cancelled") return "tone-mute";
    return "tone-mute"; // not_started / unknown
  }

  function tokenStatusTone(status) {
    const s = String(status || "unknown");
    if (s === "alive" || s === "refreshed") return "tone-ok";
    if (s === "dead" || s === "sso_dead" || s === "api_dead" || s === "refresh_failed" || s === "refresh_invalid" || s === "oauth_failed" || s === "error") return "tone-bad";
    return "tone-mute";
  }

  function ssoAliveTone(value) {
    if (value === 1 || value === true || value === "1") return "tone-ok";
    if (value === 0 || value === false || value === "0") return "tone-bad";
    return "tone-mute";
  }

  function statusPill(text, tone, title) {
    const t = escapeHtml(text || "-");
    const c = escapeHtml(tone || "tone-mute");
    const tip = title ? ` title="${escapeHtml(title)}"` : "";
    return `<span class="status-pill ${c}"${tip}>${t}</span>`;
  }

function isCpaBusy(account) {
    const busy = ["running", "uploading", "queued"];
    return busy.includes(account.cpa_status) || busy.includes(account.sub2_status);
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
    if (metricAccountSelectedEl) metricAccountSelectedEl.textContent = String(state.selectedAccountIds.size);
    accountsDownloadBtnEl.disabled = state.selectedAccountIds.size === 0;
    accountsDeleteBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsLogBtnEl) accountsLogBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsClearFilteredBtnEl) accountsClearFilteredBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsProbeBatchBtnEl) accountsProbeBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsRefreshTokenBatchBtnEl) accountsRefreshTokenBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsOauthBatchBtnEl) accountsOauthBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsCpaBatchBtnEl) accountsCpaBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsCpaPushBatchBtnEl) accountsCpaPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsSub2apiPushBatchBtnEl) accountsSub2apiPushBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsGrok2MarkBtnEl) accountsGrok2MarkBtnEl.disabled = state.selectedAccountIds.size === 0;
    if (accountsGrok2UnmarkBtnEl) accountsGrok2UnmarkBtnEl.disabled = state.selectedAccountIds.size === 0;
    const pageIds = state.accounts.map((account) => account.id);
    const pageSelectedCount = pageIds.filter((id) => state.selectedAccountIds.has(id)).length;
    accountsSelectAllEl.checked = pageIds.length > 0 && pageSelectedCount === pageIds.length;
    accountsSelectAllEl.indeterminate = pageSelectedCount > 0 && pageSelectedCount < pageIds.length;
    if (accountsCpaCancelBtnEl) accountsCpaCancelBtnEl.disabled = !(state.cpaQueue && state.cpaQueue.active);
    if (accountsCpaExportBtnEl) {
      accountsCpaExportBtnEl.disabled = state.selectedAccountIds.size === 0;
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
        <td class="account-email account-copyable" data-copy="${escapeHtml(account.email)}" title="点击复制邮箱: ${escapeHtml(account.email)}">${escapeHtml(account.email)}</td>
        <td class="account-password account-copyable" data-copy="${escapeHtml(account.password || "")}" title="点击复制密码">${escapeHtml(account.password || "-")}</td>
        <td class="account-sso account-copyable" data-copy="${escapeHtml(account.sso || "")}" title="点击复制 SSO: ${escapeHtml(account.sso || "")}">${escapeHtml(shortSso(account.sso))}</td>
        <td>${escapeHtml(account.created_at || "-")}</td>
        <td class="account-sso-alive">${statusPill(ssoAliveLabel(account.sso_alive), ssoAliveTone(account.sso_alive), "")}</td>
        <td class="account-token-status">${statusPill(tokenStatusLabel(account.token_status), tokenStatusTone(account.token_status), account.token_error || account.last_renew_source || "")}</td>
        <td class="account-cpa-status">${statusPill(cpaStatusLabel(account.cpa_status), cpaStatusTone(account.cpa_status), account.cpa_error || account.cpa_path || "")}</td>
        <td class="account-sub2-status">${statusPill(sub2StatusLabel(account.sub2_status), sub2StatusTone(account.sub2_status), account.sub2_error || account.sub2_uploaded_at || "")}</td>
        <td class="account-grok2-status">${statusPill(flagLabel(account.grok2), flagTone(account.grok2), account.grok2_updated_at || "")}</td>
        <td title="${escapeHtml(account.token_checked_at || "")}">${escapeHtml(account.token_expires_at || "-")}</td>
        <td class="account-actions">
          <button class="button button-small button-warn" type="button" data-refresh-account-id="${account.id}" ${isCpaBusy(account) ? "disabled" : ""} title="优先 RT 续期">续期</button>
          <button class="button button-small button-success" type="button" data-probe-account-id="${account.id}" ${isCpaBusy(account) ? "disabled" : ""} title="检测是否可用">测活</button>
          <button class="button button-small button-info" type="button" data-oauth-account-id="${account.id}" ${isCpaBusy(account) ? "disabled" : ""} title="完整 SSO OAuth 重建">OAuth</button>
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

    accountsTableBodyEl.querySelectorAll("[data-copy]").forEach((cell) => {
      cell.addEventListener("click", async () => {
        const value = cell.getAttribute("data-copy") || "";
        const ok = await copyTextToClipboard(value);
        if (!ok) {
          accountsMetaEl.textContent = "复制失败，请手动选择文本";
          return;
        }
        cell.classList.add("copied");
        const prevTitle = cell.getAttribute("title") || "";
        cell.setAttribute("title", "已复制");
        accountsMetaEl.textContent = `已复制: ${String(value).slice(0, 48)}${String(value).length > 48 ? "..." : ""}`;
        setTimeout(() => {
          cell.classList.remove("copied");
          if (prevTitle) cell.setAttribute("title", prevTitle);
        }, 1200);
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

    

    accountsTableBodyEl.querySelectorAll("[data-probe-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.probeAccountId));
        if (!account) return;
        button.disabled = true;
        try {
          accountsMetaEl.textContent = `正在测活 ${account.email}...`;
          const result = await fetchJson(`/api/accounts/${account.id}/probe`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          accountsMetaEl.textContent = `测活 ${account.email}: ${result.token_status || (result.alive ? "alive" : "dead")}`;
          await refreshAccounts();
        } catch (error) {
          accountsMetaEl.textContent = `测活失败: ${error.message}`;
        } finally {
          button.disabled = false;
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-refresh-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.refreshAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认对账号 ${account.email} 执行 Token 续期吗？`);
        if (!confirmed) return;
        button.disabled = true;
        try {
          accountsMetaEl.textContent = `正在续期 ${account.email}...`;
          const result = await fetchJson(`/api/accounts/${account.id}/refresh?force=true`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          accountsMetaEl.textContent = result.ok
            ? `续期 ${account.email}: ${result.renewed ? ("已刷新/" + (result.source || "token")) : "无需刷新"}`
            : `续期失败: ${result.error || "unknown"}`;
          await refreshAccounts();
        } catch (error) {
          accountsMetaEl.textContent = `续期失败: ${error.message}`;
        } finally {
          button.disabled = false;
        }
      });
    });

    accountsTableBodyEl.querySelectorAll("[data-oauth-account-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const account = state.accounts.find((item) => item.id === Number(button.dataset.oauthAccountId));
        if (!account) return;
        const confirmed = window.confirm(`确认对账号 ${account.email} 执行 SSO OAuth 授权获取吗？`);
        if (!confirmed) return;
        button.disabled = true;
        try {
          const oauthStart = await fetchJson(`/api/accounts/${account.id}/oauth`, { method: "POST" });
          state.cpaLogAccountId = account.id;
          if (oauthStart.queue) {
            state.cpaQueue = oauthStart.queue;
            renderCpaQueuePanel();
          }
          accountsMetaEl.textContent = `账号 ${account.email} OAuth 已加入全局队列`;
          await refreshAccounts();
          await refreshCpaQueue();
        } catch (error) {
          accountsMetaEl.textContent = `OAuth 启动失败: ${error.message}`;
          button.disabled = false;
        }
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
  }

  function fillAccountCpaLogPanel(account, scrollToBottom = false) {
    if (!accountCpaLogPanelEl || !accountCpaLogEl || !accountCpaLogTitleEl || !account) {
      return;
    }
    accountCpaLogPanelEl.classList.remove("hidden");
    accountCpaLogTitleEl.textContent = "账号日志 · " + (account.email || ("#" + account.id)) + (account.task_deleted ? " · 任务已删除" : "");

    const metaRows = [
      ["\u90ae\u7bb1", account.email || "-"],
      ["SSO\u6d4b\u6d3b", ssoAliveLabel(account.sso_alive)],
      ["Token", tokenStatusLabel(account.token_status)],
      ["CPA", cpaStatusLabel(account.cpa_status)],
      ["Sub2", sub2StatusLabel(account.sub2_status)],
    ];
    if (account.cpa_path) metaRows.push(["\u6587\u4ef6", account.cpa_path]);
    if (account.token_expires_at) metaRows.push(["\u8fc7\u671f", account.token_expires_at]);
    if (account.token_checked_at) metaRows.push(["\u68c0\u6d4b\u65f6\u95f4", account.token_checked_at]);
    if (account.last_renew_source) metaRows.push(["\u7eed\u671f\u6765\u6e90", account.last_renew_source]);
    if (account.cpa_uploaded_at) metaRows.push(["CPA\u63a8\u9001", account.cpa_uploaded_at]);
    if (account.sub2_uploaded_at) metaRows.push(["Sub2\u63a8\u9001", account.sub2_uploaded_at]);
    if (account.token_error) metaRows.push(["Token\u9519\u8bef", account.token_error]);
    if (account.cpa_error) metaRows.push(["CPA\u9519\u8bef", account.cpa_error]);
    if (account.sub2_error) metaRows.push(["Sub2\u9519\u8bef", account.sub2_error]);

    const metaHtml = metaRows.map(([k, v]) => (
      `<div class="log-meta-row"><span class="log-meta-k">${escapeHtml(k)}</span><span class="log-meta-v">${escapeHtml(String(v ?? "-"))}</span></div>`
    )).join("");

    let detailLines;
    if (account.cpa_log) {
      detailLines = String(account.cpa_log).split(/\r?\n/);
    } else {
      detailLines = [
        "[Info] \u6682\u65e0\u8be6\u7ec6\u65e5\u5fd7\u3002",
        "[Info] \u5bf9\u672c\u8d26\u53f7\u6267\u884c \u7eed\u671f / \u6d4b\u6d3b / OAuth / \u63a8\u9001 \u540e\uff0c\u8fc7\u7a0b\u4f1a\u5199\u5230\u8fd9\u91cc\u3002",
        "[Info] \u6ce8\u518c\u6d4f\u89c8\u5668\u6b65\u9aa4\u5728\u300c\u6ce8\u518c\u4efb\u52a1\u300d\u63a7\u5236\u53f0\uff1b\u6ce8\u518c\u540e OAuth/\u6d4b\u6d3b\u4f1a\u540c\u6b65\u5230\u672c\u65e5\u5fd7\u3002",
      ];
    }

    accountCpaLogEl.innerHTML =
      `<div class="log-meta-block">${metaHtml}</div>` +
      `<div class="log-section-title">\u64cd\u4f5c\u65e5\u5fd7</div>` +
      renderLogHtml(detailLines, { emptyText: "\u6682\u65e0\u8be6\u7ec6\u65e5\u5fd7" });

    if (scrollToBottom) {
      accountCpaLogEl.scrollTop = accountCpaLogEl.scrollHeight;
    }
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
      if (!accountCpaLogPanelEl.dataset.stickyLog) {
        accountCpaLogPanelEl.classList.add("hidden");
      }
      return;
    }
    accountCpaLogPanelEl.dataset.stickyLog = "";
    fillAccountCpaLogPanel(account, scrollToBottom);
  }

  async function openSelectedAccountLog() {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      accountsMetaEl.textContent = "请先勾选要查看日志的账号";
      return;
    }
    const accountId = ids[0];
    if (ids.length > 1) {
      accountsMetaEl.textContent = `已选 ${ids.length} 个，仅展示第一个账号的日志 (#${accountId})`;
    }
    state.cpaLogAccountId = accountId;
    let account = state.accounts.find((item) => item.id === accountId);
    if (!account) {
      try {
        const data = await fetchJson("/api/accounts/by-ids", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_ids: [accountId] }),
        });
        account = (data.accounts || [])[0];
      } catch (error) {
        accountsMetaEl.textContent = `加载日志失败: ${error.message}`;
        return;
      }
    }
    if (!account) {
      accountsMetaEl.textContent = "未找到选中账号";
      return;
    }
    if (accountCpaLogPanelEl) accountCpaLogPanelEl.dataset.stickyLog = "1";
    fillAccountCpaLogPanel(account, true);
    if (accountCpaLogPanelEl && accountCpaLogPanelEl.scrollIntoView) {
      accountCpaLogPanelEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function downloadSsoFile(accounts, filename) {
    const lines = (accounts || [])
      .map((account) => String(account.sso || "").trim())
      .filter(Boolean);
    if (!lines.length) {
      if (accountsMetaEl) accountsMetaEl.textContent = "没有可下载的 SSO";
      return;
    }
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || ("sso_" + Date.now() + ".txt");
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function downloadAccountLines(accounts, filename) {
    const lines = (accounts || [])
      .map((account) => {
        const email = String(account.email || "").trim();
        const password = String(account.password || "").trim();
        const sso = String(account.sso || "").trim();
        if (!email && !sso) return "";
        return email + "----" + password + "----" + sso;
      })
      .filter(Boolean);
    if (!lines.length) {
      if (accountsMetaEl) accountsMetaEl.textContent = "没有可导出的账号行";
      return;
    }
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || ("accounts_" + Date.now() + ".txt");
    document.body.appendChild(a);
    a.click();
    a.remove();
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
    const opts = Object.assign({ credentials: "same-origin" }, options || {});
    if (opts.headers == null && opts.body && !(opts.body instanceof FormData)) {
      opts.headers = { "Content-Type": "application/json" };
    }
    const response = await fetch(url, opts);
    if (response.status === 401 && !String(url).includes("/api/auth/")) {
      window.location.href = "/login";
      throw new Error("未登录或登录已过期");
    }
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

    async function loadTaskLogs(taskId) {
    const logData = await fetchJson(`/api/tasks/${taskId}/logs?limit=250`);
    setLogContent(consoleOutputEl, logData.lines || [], {
      emptyText: "暂无输出",
      scrollToBottom: true,
    });
    state.logsLoadedTaskId = taskId;
  }


  async function refreshDetail(options = {}) {
    const forceLogs = options.forceLogs === true;
    if (!state.selectedTaskId) {
      renderDefaultMailDetail();
      return null;
    }
    const taskData = await fetchJson(`/api/tasks/${state.selectedTaskId}`);
    const task = taskData.task;
    renderTaskDetail(task);
    const needLogs = (
      forceLogs
      || ACTIVE_TASK_STATUSES.has(task.status)
      || state.logsLoadedTaskId !== task.id
    );
    if (needLogs) {
      await loadTaskLogs(task.id);
    }
    return task;
  }

  async function refreshAll(options = {}) {
    const force = options.force === true;
    try {
      await refreshTasks();
      if (!state.selectedTaskId) {
        state.logsLoadedTaskId = null;
        renderDefaultMailDetail();
        return;
      }
      const listed = (state.tasks || []).find((t) => t.id === state.selectedTaskId);
      const isActive = listed ? ACTIVE_TASK_STATUSES.has(listed.status) : true;
      const logsMissing = state.logsLoadedTaskId !== state.selectedTaskId;
      if (force || isActive || logsMissing) {
        // 运行中 / 手动刷新 / 首次点选：拉详情 + 日志
        await refreshDetail({ forceLogs: true });
      } else if (listed) {
        // 非运行且已加载过日志：只刷新摘要，停止轮询日志接口
        renderTaskDetail(listed);
      } else {
        await refreshDetail({ forceLogs: true });
      }
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
      const params = appendAccountFilterParams(new URLSearchParams());
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

  refreshBtnEl.addEventListener("click", () => refreshAll({ force: true }));
  healthRefreshBtnEl.addEventListener("click", refreshHealth);
  accountsRefreshBtnEl.addEventListener("click", refreshAccounts);
  if (accountCpaLogCloseBtnEl) {
    accountCpaLogCloseBtnEl.addEventListener("click", () => {
      state.cpaLogAccountId = null;
      if (accountCpaLogPanelEl) accountCpaLogPanelEl.dataset.stickyLog = "";
      renderAccountCpaLog();
    });
  }
  if (accountsLogBtnEl) {
    accountsLogBtnEl.addEventListener("click", () => {
      openSelectedAccountLog();
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
    if (accountsCpaExportBtnEl) accountsCpaExportBtnEl.disabled = state.selectedAccountIds.size === 0;
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

  

  async function startBatchMaintain(mode) {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      accountsMetaEl.textContent = "请先选择账号";
      return;
    }
    const modeLabel = mode === "probe_only"
      ? "批量测活"
      : mode === "refresh_only"
        ? "批量 Token 续期"
        : mode === "oauth_only"
          ? "批量 OAuth 授权"
          : mode;
    const confirmed = window.confirm(
      `确认对选中的 ${ids.length} 个账号执行「${modeLabel}」吗？
将进入全局队列串行处理。`
    );
    if (!confirmed) return;
    if (accountsProbeBatchBtnEl) accountsProbeBatchBtnEl.disabled = true;
    if (accountsRefreshTokenBatchBtnEl) accountsRefreshTokenBatchBtnEl.disabled = true;
    if (accountsOauthBatchBtnEl) accountsOauthBatchBtnEl.disabled = true;
    try {
      const result = await fetchJson("/api/accounts/maintain/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: ids, mode }),
      });
      const accepted = Number(result.accepted_count || 0);
      const skipped = Number(result.skipped_count || 0);
      const rejected = Number(result.rejected_count || 0);
      accountsMetaEl.textContent = `${modeLabel}：已入队 ${accepted}，跳过 ${skipped}，拒绝 ${rejected}`;
      if (result.queue) {
        state.cpaQueue = result.queue;
        renderCpaQueuePanel();
      }
      await refreshAccounts();
      await refreshCpaQueue();
    } catch (error) {
      accountsMetaEl.textContent = `${modeLabel}启动失败: ${error.message}`;
    } finally {
      if (accountsProbeBatchBtnEl) accountsProbeBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
      if (accountsRefreshTokenBatchBtnEl) accountsRefreshTokenBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
      if (accountsOauthBatchBtnEl) accountsOauthBatchBtnEl.disabled = state.selectedAccountIds.size === 0;
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

  if (accountsProbeBatchBtnEl) {
    accountsProbeBatchBtnEl.addEventListener("click", () => startBatchMaintain("probe_only"));
  }
  if (accountsRefreshTokenBatchBtnEl) {
    accountsRefreshTokenBatchBtnEl.addEventListener("click", () => startBatchMaintain("refresh_only"));
  }
  if (accountsOauthBatchBtnEl) {
    accountsOauthBatchBtnEl.addEventListener("click", () => startBatchMaintain("oauth_only"));
  }
  if (accountsCpaBatchBtnEl) {
    accountsCpaBatchBtnEl.addEventListener("click", () => startBatchCpa("authorize_and_push"));
  }
  if (accountsCpaPushBatchBtnEl) {
    accountsCpaPushBatchBtnEl.addEventListener("click", () => startBatchCpa("push_only"));
  }
  async function markSelectedFlag(flagName, value) {
    const ids = Array.from(state.selectedAccountIds);
    if (!ids.length) {
      accountsMetaEl.textContent = "请先选择账号";
      return;
    }
    const label = "Grok2";
    try {
      const data = await fetchJson(`/api/accounts/${flagName}/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: ids, [flagName]: value }),
      });
      accountsMetaEl.textContent = `${label} ${value ? "已标记支持" : "已取消支持"}：${data.updated_count || 0} 个账号`;
      await refreshAccounts();
    } catch (error) {
      accountsMetaEl.textContent = `${label} 标记失败: ${error.message}`;
    }
  }

  if (accountsSub2apiPushBatchBtnEl) {
    accountsSub2apiPushBatchBtnEl.addEventListener("click", () => startBatchCpa("push_sub2api"));
  }
  if (accountsGrok2MarkBtnEl) {
    accountsGrok2MarkBtnEl.addEventListener("click", () => markSelectedFlag("grok2", true));
  }
  if (accountsGrok2UnmarkBtnEl) {
    accountsGrok2UnmarkBtnEl.addEventListener("click", () => markSelectedFlag("grok2", false));
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
    accountsCpaExportBtnEl.title =
      "导出选中账号为 txt：email----password----sso（每行一条）";
    accountsCpaExportBtnEl.addEventListener("click", async () => {
      const ids = Array.from(state.selectedAccountIds);
      if (!ids.length) {
        accountsMetaEl.textContent = "请先选择要导出的账号";
        return;
      }
      try {
        const response = await fetch("/api/accounts/export", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_ids: ids }),
        });
        if (!response.ok) {
          let detail = `HTTP ${response.status}`;
          try {
            const data = await response.json();
            detail = data.detail || data.message || detail;
          } catch (e) {}
          throw new Error(detail);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const cd = response.headers.get("content-disposition") || "";
        const m = /filename="?([^";]+)"?/i.exec(cd);
        a.href = url;
        a.download = (m && m[1]) || ("accounts_" + Date.now() + ".txt");
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        accountsMetaEl.textContent = `已导出账号 txt（email----password----sso）共 ${ids.length} 条`;
      } catch (error) {
        try {
          const data = await fetchJson("/api/accounts/by-ids", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ account_ids: ids }),
          });
          downloadAccountLines(data.accounts || [], "accounts_" + Date.now() + ".txt");
          accountsMetaEl.textContent = `已导出账号 txt（本地回退）共 ${ids.length} 条`;
        } catch (e2) {
          accountsMetaEl.textContent = `导出失败: ${error.message || error}`;
        }
      }
    });
  }
  if (accountsSelectFilteredBtnEl) {
    accountsSelectFilteredBtnEl.addEventListener("click", async () => {
      try {
        const params = appendAccountFilterParams(new URLSearchParams());
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
    if (accountsClearFilteredBtnEl) {
    accountsClearFilteredBtnEl.addEventListener("click", () => {
      const n = state.selectedAccountIds.size;
      state.selectedAccountIds = new Set();
      accountsMetaEl.textContent = n
        ? `已取消全选（清除 ${n} 个已选账号）`
        : "当前没有选中的账号";
      renderAccounts();
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
  if (accountsCpaFilterEl) {
    accountsCpaFilterEl.addEventListener("change", () => {
      state.accountCpaFilter = accountsCpaFilterEl.value;
      state.accountPage = 1;
      refreshAccounts();
    });
  }
  if (accountsTokenFilterEl) {
    accountsTokenFilterEl.addEventListener("change", () => {
      state.accountTokenFilter = accountsTokenFilterEl.value;
      state.accountPage = 1;
      refreshAccounts();
    });
  }
  if (accountsSub2FilterEl) {
    accountsSub2FilterEl.addEventListener("change", () => {
      state.accountSub2Filter = accountsSub2FilterEl.value;
      state.accountPage = 1;
      refreshAccounts();
    });
  }
  if (accountsGrok2FilterEl) {
    accountsGrok2FilterEl.addEventListener("change", () => {
      state.accountGrok2Filter = accountsGrok2FilterEl.value;
      state.accountPage = 1;
      refreshAccounts();
    });
  }
if (accountsSsoFilterEl) {
    accountsSsoFilterEl.addEventListener("change", () => {
      state.accountSsoFilter = accountsSsoFilterEl.value;
      state.accountPage = 1;
      refreshAccounts();
    });
  }
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

  const maxConcurrentInputEl = settingsFormEl.elements.max_concurrent_tasks;
  if (maxConcurrentInputEl) {
    maxConcurrentInputEl.addEventListener("input", () => {
      const maxConcEl = document.getElementById("maxConcurrentTasksDisplay");
      const n = Math.max(1, Math.min(20, Number(maxConcurrentInputEl.value) || 1));
      if (maxConcEl) maxConcEl.textContent = String(n);
    });
  }

  settingsFormEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      proxy: settingsFormEl.elements.proxy.value.trim(),
      browser_proxy: settingsFormEl.elements.browser_proxy.value.trim(),
      max_concurrent_tasks: Number(settingsFormEl.elements.max_concurrent_tasks?.value ?? 1) || 1,
      temp_mail_api_base: settingsFormEl.elements.temp_mail_api_base.value.trim(),
      temp_mail_admin_password: settingsFormEl.elements.temp_mail_admin_password.value.trim(),
      temp_mail_domain: settingsFormEl.elements.temp_mail_domain.value.trim(),
      temp_mail_domains_removed: String(settingsFormEl.elements.temp_mail_domains_removed?.value || "").trim(),
      domain_auth_fail_threshold: Number(settingsFormEl.elements.domain_auth_fail_threshold?.value ?? 3) || 3,
      domain_auth_fail_auto_remove: Boolean(settingsFormEl.elements.domain_auth_fail_auto_remove?.checked),
      temp_mail_site_password: settingsFormEl.elements.temp_mail_site_password.value.trim(),
      email_provider: String(settingsFormEl.elements.email_provider?.value || "duckmail").trim() || "duckmail",
      outmail_api_base: String(settingsFormEl.elements.outmail_api_base?.value || "").trim(),
      outmail_api_key: String(settingsFormEl.elements.outmail_api_key?.value || "").trim(),
      outmail_session_cookie: String(settingsFormEl.elements.outmail_session_cookie?.value || "").trim(),
      outmail_proxy: String(settingsFormEl.elements.outmail_proxy?.value || "").trim(),
      outmail_plus_alias: Boolean(settingsFormEl.elements.outmail_plus_alias?.checked),
      outmail_plus_alias_count: Number(settingsFormEl.elements.outmail_plus_alias_count?.value ?? 1) || 1,
      outmail_alias_suffix_len: Number(settingsFormEl.elements.outmail_alias_suffix_len?.value ?? 6) || 6,
      outmail_fetch_top: Number(settingsFormEl.elements.outmail_fetch_top?.value ?? 10) || 10,
      outmail_poll_interval_sec: Number(settingsFormEl.elements.outmail_poll_interval_sec?.value ?? 5) || 5,
      outmail_poll_timeout_sec: Number(settingsFormEl.elements.outmail_poll_timeout_sec?.value ?? 180) || 180,
      outmail_since_padding_sec: Number(settingsFormEl.elements.outmail_since_padding_sec?.value ?? 30) || 30,
      outmail_from_filter: String(settingsFormEl.elements.outmail_from_filter?.value || "x.ai").trim(),
      outmail_subject_filter: String(settingsFormEl.elements.outmail_subject_filter?.value || "xAI").trim(),
      outmail_group_id: String(settingsFormEl.elements.outmail_group_id?.value || "").trim(),
      outmail_anonymous_enabled: Boolean(settingsFormEl.elements.outmail_anonymous_enabled?.checked),
      outmail_anonymous_provider: String(settingsFormEl.elements.outmail_anonymous_provider?.value || "cloudflare").trim() || "cloudflare",
      outmail_anonymous_domain: String(settingsFormEl.elements.outmail_anonymous_domain?.value || "").trim(),
      outmail_anonymous_username_prefix: String(settingsFormEl.elements.outmail_anonymous_username_prefix?.value || "").trim(),
      outmail_anonymous_password: String(settingsFormEl.elements.outmail_anonymous_password?.value || "").trim(),
      outmail_anonymous_delete_after: Boolean(settingsFormEl.elements.outmail_anonymous_delete_after?.checked),
      outmail_exclude_used: Boolean(settingsFormEl.elements.outmail_exclude_used?.checked),
      outmail_used_file: String(settingsFormEl.elements.outmail_used_file?.value || "outmail_used_mailboxes.txt").trim() || "outmail_used_mailboxes.txt",
      cpa_auth_dir: settingsFormEl.elements.cpa_auth_dir.value.trim(),
      cpa_proxy: settingsFormEl.elements.cpa_proxy.value.trim(),
      cpa_hotload_dir: settingsFormEl.elements.cpa_hotload_dir.value.trim(),
      cpa_mint_timeout_sec: Number(settingsFormEl.elements.cpa_mint_timeout_sec.value) || 300,
      cpa_prefer_sso_oauth: settingsFormEl.elements.cpa_prefer_sso_oauth ? Boolean(settingsFormEl.elements.cpa_prefer_sso_oauth.checked) : true,
      cpa_probe_after_write: settingsFormEl.elements.cpa_probe_after_write ? Boolean(settingsFormEl.elements.cpa_probe_after_write.checked) : true,
      cpa_probe_delay_sec: Number(settingsFormEl.elements.cpa_probe_delay_sec?.value ?? 5),
      cpa_probe_required: settingsFormEl.elements.cpa_probe_required ? Boolean(settingsFormEl.elements.cpa_probe_required.checked) : false,
      cpa_post_task_oauth_enabled: Boolean(settingsFormEl.elements.cpa_post_task_oauth_enabled?.checked),
      cpa_post_task_refresh_enabled: Boolean(settingsFormEl.elements.cpa_post_task_refresh_enabled?.checked),
      cpa_export_enabled: settingsFormEl.elements.cpa_export_enabled.checked,
      cpa_copy_to_hotload: settingsFormEl.elements.cpa_copy_to_hotload.checked,
      cpa_headless: settingsFormEl.elements.cpa_headless.checked,
      cpa_cloud_upload_enabled: settingsFormEl.elements.cpa_cloud_upload_enabled.checked,
      cpa_register_push_enabled: Boolean(settingsFormEl.elements.cpa_register_push_enabled?.checked),
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
      sub2api_register_push_enabled: Boolean(settingsFormEl.elements.sub2api_register_push_enabled?.checked),
      sub2api_export_enabled: Boolean(settingsFormEl.elements.sub2api_export_enabled?.checked),
      sub2api_api_base: String(settingsFormEl.elements.sub2api_api_base?.value || "").trim(),
      sub2api_upload_timeout: Number(settingsFormEl.elements.sub2api_upload_timeout?.value) || 30,
      sub2api_upload_retries: Number(settingsFormEl.elements.sub2api_upload_retries?.value) || 3,
      sub2api_platform: String(settingsFormEl.elements.sub2api_platform?.value || "grok").trim() || "grok",
      sub2api_account_type: String(settingsFormEl.elements.sub2api_account_type?.value || "oauth").trim() || "oauth",
      sub2api_account_concurrency: Number(settingsFormEl.elements.sub2api_account_concurrency?.value ?? 1) || 1,
      sub2api_account_priority: Number(settingsFormEl.elements.sub2api_account_priority?.value ?? 1),
      sub2api_account_load_factor: Number(settingsFormEl.elements.sub2api_account_load_factor?.value ?? 10) || 10,
      sub2api_account_rate_multiplier: Number(settingsFormEl.elements.sub2api_account_rate_multiplier?.value ?? 1) || 1,

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

  async function initAuthUi() {
    try {
      const data = await fetchJson("/api/auth/status");
      if (data.auth_enabled) {
        if (sidebarUserEl) {
          sidebarUserEl.textContent = data.username ? `已登录：${data.username}` : "已登录";
          sidebarUserEl.classList.remove("hidden");
        }
        if (logoutBtnEl) logoutBtnEl.classList.remove("hidden");
      }
    } catch (_error) {
      // ignore
    }
  }

  if (logoutBtnEl) {
    logoutBtnEl.addEventListener("click", async () => {
      try {
        await fetchJson("/api/auth/logout", { method: "POST", body: "{}" });
      } catch (_e) {}
      window.location.href = "/login";
    });
  }
  initAuthUi();
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

