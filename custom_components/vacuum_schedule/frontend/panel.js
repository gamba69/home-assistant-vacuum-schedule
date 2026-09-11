const VACUUM_SCHEDULER_LOCALIZATION_CACHE = new Map();

class VacuumSchedulePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._hassConnection = null;
    this._translations = {};
    this._fallbackTranslations = {};
    this._translationLanguage = null;
    this._data = null;
    this._schedulerData = null;
    this._settingsData = null;
    this._notificationData = null;
    this._statisticsData = null;
    this._forecastData = null;
    this._forecastDataEntryId = null;
    this._forecastLoading = false;
    this._forecastError = null;
    this._maintenanceData = null;
    this._maintenanceDraft = null;
    this._statisticsFilters = {date_from:"", date_to:"", schedule_id:"all", zone_id:"all", execution_mode:"all", execution_source:"all"};
    this._statisticsTab = "summary";
    this._notificationRoutingTestResult = null;
    this._notificationHistorySelectedId = null; // legacy delivery deep links
    this._notificationMessageSelectedId = null;
    this._notificationMessageDetail = null;
    this._historyJobDetails = new Map();
    this._historyJobOpen = new Set();
    this._historyJobLoading = new Set();
    this._historyJobTab = new Map();
    this._historyFilters = {execution_mode:"all", result:"all", source:"all", schedule_id:"all"};
    this._historyFiltersOpen = false;
    this._activeJobOpen = new Set();
    this._activeJobTab = new Map();
    this._activeJobDetails = new Map();
    this._activeJobLoading = new Set();
    this._deepLinkJobId = null;
    this._notificationRecipientDraft = null;
    this._notificationRecipientIsNew = false;
    this._notificationRecipientDirty = false;
    this._notificationGlobalDraft = null;
    this._notificationGlobalDirty = false;
    this._policyDraft = null;
    this._policyDirty = false;
    this._interfaceLanguageDraft = null;
    this._interfaceLanguageDirty = false;
    this._roomDraft = null;
    this._roomTestResult = null;
    this._settingsEditor = null;
    this._view = "status";
    this._debugTarget = "all";
    this._schedulerEntryId = null;
    this._entryStorageKey = "vacuum_schedule.last_config_entry";
    this._unsubscribePush = null;
    this._midnightTimer = null;
    this._editor = null;
    this._form = null;
    this._loading = false;
    this._saving = false;
    this._executionSettingsDraft = null;
    this._executionSettingsDirty = false;
    this._executionOptionsSaving = false;
    this._executionAdvancedOpen = false;
    this._systemNoticesOpen = false;
    this._globalNoticesReadyEntryId = null;
    this._settingsDataEntryId = null;
    this._error = null;
    this._notice = null;
    this._noticeTimer = null;
    this._modalPending = null;
    this._zoneCountdownTimer = null;
    this._zoneCountdownReloading = false;
    this._activeLiveTimer = null;
    this._activeClockTimer = null;
    this._queryHandled = false;
    this._connected = false;
    this._lastLoadAt = 0;
    this._onWindowFocus = () => { if (!this._editingAny) this._load(); };
    this._onVisibilityChange = () => {
      if (document.visibilityState === "visible" && !this._editingAny) this._load();
    };
  }

  connectedCallback() {
    if (this._connected) return;
    this._connected = true;
    window.addEventListener("focus", this._onWindowFocus);
    document.addEventListener("visibilitychange", this._onVisibilityChange);
    this._scheduleMidnightRefresh();
    this._startActiveLiveTimer();
    this._startActiveClockTimer();
    if (this._hass) {
      this._load(true);
      this._subscribePush();
    }
  }

  disconnectedCallback() {
    this._connected = false;
    window.removeEventListener("focus", this._onWindowFocus);
    document.removeEventListener("visibilitychange", this._onVisibilityChange);
    this._clearMidnightRefresh();
    this._clearZoneCountdownTimer();
    this._clearActiveLiveTimer();
    this._clearActiveClockTimer();
    if (this._unsubscribePush) {
      this._unsubscribePush();
      this._unsubscribePush = null;
    }
  }

  set hass(value) {
    const first = !this._hass;
    const previousLanguage = this._language;
    const previousConnection = this._hassConnection;
    this._hass = value;
    this._hassConnection = value?.connection || null;
    const languageChanged = !first && previousLanguage !== this._language;
    const connectionChanged = !first && previousConnection !== this._hassConnection;
    if ((first || languageChanged || connectionChanged) && this._connected) {
      if (languageChanged) this._translationLanguage = null;
      if (connectionChanged && this._unsubscribePush) {
        this._unsubscribePush();
        this._unsubscribePush = null;
      }
      this._load(true);
      if (first || connectionChanged) this._subscribePush();
    }
  }

  set narrow(value) {
    const narrow = !!value;
    this._narrow = narrow;
    this.toggleAttribute("narrow", narrow);
  }
  set route(value) {
    const changed = this._route !== value;
    this._route = value;
    if (changed && this._hass && this._connected && !this._editingAny) this._load();
  }
  set panel(value) { this._panel = value; }

  get _haLanguage() {
    const raw = String(this._hass?.language || navigator.language || "en").toLowerCase().replaceAll("_", "-");
    if (raw.startsWith("ru")) return "ru";
    if (raw.startsWith("uk") || raw.startsWith("ua")) return "uk";
    return "en";
  }

  get _language() {
    const sameEntry = this._settingsDataEntryId && this._settingsDataEntryId === this._schedulerEntryId;
    const configured = sameEntry ? String(this._settingsData?.interface_language || "auto").toLowerCase() : "auto";
    return ["ru", "uk", "en"].includes(configured) ? configured : this._haLanguage;
  }

  get _locale() { return this._language === "ru" ? "ru-RU" : this._language === "uk" ? "uk-UA" : "en-US"; }
  get _ru() { return this._language === "ru"; }

  async _loadTranslationCatalog(language) {
    const lang = ["ru", "uk", "en"].includes(String(language)) ? String(language) : "en";
    if (VACUUM_SCHEDULER_LOCALIZATION_CACHE.has(lang)) return VACUUM_SCHEDULER_LOCALIZATION_CACHE.get(lang);
    const response = await fetch(`/vacuum_schedule_frontend/localization/${lang}.json?v=0.13.3`, { cache: "no-cache" });
    if (!response.ok) throw new Error(`localization_${lang}_${response.status}`);
    const catalog = await response.json();
    VACUUM_SCHEDULER_LOCALIZATION_CACHE.set(lang, catalog);
    return catalog;
  }

  async _ensureTranslations() {
    const language = this._language;
    if (this._translationLanguage === language && Object.keys(this._fallbackTranslations).length) return;
    const fallback = await this._loadTranslationCatalog("en");
    const current = language === "en" ? fallback : await this._loadTranslationCatalog(language);
    this._fallbackTranslations = fallback;
    this._translations = current;
    this._translationLanguage = language;
  }

  _tr(key, params = {}) {
    let value = this._translations?.[key] ?? this._fallbackTranslations?.[key] ?? key;
    value = String(value);
    return value.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
    );
  }
  get _editingAny() { return !!this._editor || !!this._roomDraft || !!this._settingsEditor || !!this._notificationRecipientDraft || !!this._maintenanceDraft || !!this._modalPending || this._notificationGlobalDirty || this._policyDirty || this._interfaceLanguageDirty || this._executionSettingsDirty || this._executionOptionsSaving || this._saving; }

  _cloneData(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  _syncNotificationGlobalDraft(force = false) {
    const settings=this._notificationData?.settings;
    if(!settings) { if(force) this._notificationGlobalDraft=null; return; }
    if(!force && this._notificationGlobalDirty) return;
    this._notificationGlobalDraft={
      enabled: settings.enabled !== false,
      dry_run_delivery: String(settings.dry_run_delivery || "send"),
      preset: String(settings.preset || "balanced"),
      policy: this._cloneData(settings.policy || this._notificationPresetPolicy(settings.preset || "balanced")),
    };
  }

  _syncPolicyDraft(force = false) {
    const policy=this._settingsData?.policy;
    if(!policy) { if(force) this._policyDraft=null; return; }
    if(!force && this._policyDirty) return;
    this._policyDraft=this._cloneData(policy);
  }

  _syncInterfaceLanguageDraft(force = false) {
    if (!this._settingsData) { if (force) this._interfaceLanguageDraft = null; return; }
    if (!force && this._interfaceLanguageDirty) return;
    this._interfaceLanguageDraft = String(this._settingsData.interface_language || "auto");
  }

  _syncExecutionSettingsDraft(force = false) {
    const execution = this._settingsData?.execution;
    if (!execution) {
      if (force) this._executionSettingsDraft = null;
      return;
    }
    if (!force && (this._executionSettingsDirty || this._executionOptionsSaving || this._saving)) return;
    this._executionSettingsDraft = {
      mode: String(execution.mode || "DRY_RUN") === "REAL" ? "REAL" : "DRY_RUN",
      dry_run_duration: Math.max(1, Math.min(300, Number(execution.dry_run?.execution_duration_seconds ?? 10))),
      restore_previous_settings: execution.real?.restore_previous_settings !== false,
      runtime_error_recovery_minutes: Math.max(1, Math.min(1440, Number(execution.real?.runtime_error_recovery_minutes ?? 30))),
    };
  }

  _setExecutionFormSaving(saving, includeModeControls = false) {
    const root=this.shadowRoot;
    if(!root) return;
    const card=root.querySelector(".execution-settings-card");
    if(card) {
      const advancedBody=card.querySelector(".execution-advanced-body");
      if(advancedBody) {
        advancedBody.toggleAttribute("inert", !!saving);
        advancedBody.setAttribute("aria-busy", saving ? "true" : "false");
      }
      if(includeModeControls) {
        card.querySelectorAll("button.execution-mode-choice").forEach((control) => { control.disabled = !!saving; });
      }
      card.toggleAttribute("data-saving", !!saving);
    }
    if(includeModeControls) {
      root.querySelectorAll("button.quick-execution-mode").forEach((control)=>{ control.disabled=!!saving; });
    }
  }

  _syncExecutionOptionsDom() {
    const root=this.shadowRoot;
    if(!root || !this._executionSettingsDraft) return;
    const duration=root.querySelector("#dry-run-duration");
    if(duration) duration.value=String(this._executionSettingsDraft.dry_run_duration ?? 10);
    const restore=root.querySelector("#real-restore-settings");
    if(restore) restore.checked=!!this._executionSettingsDraft.restore_previous_settings;
    const recovery=root.querySelector("#runtime-error-recovery-minutes");
    if(recovery) recovery.value=String(this._executionSettingsDraft.runtime_error_recovery_minutes ?? 30);
  }

  async _callWs(message) {
    // hass.callWS() is the public Home Assistant frontend API and resolves
    // directly to the command result payload.
    return await this._hass.callWS(message);
  }

  async _subscribePush() {
    if (!this._hass?.connection || this._unsubscribePush) return;
    try {
      this._unsubscribePush = await this._hass.connection.subscribeMessage(
        async () => {
          await this._loadScheduler();
          if (this._activeJobOpen.size) await Promise.all([...this._activeJobOpen].map((id)=>this._loadActiveJobDetails(id,true,false)));
          await this._loadSettings(false);
          if (this._view === "notifications" || this._view === "testing") await this._loadNotifications(false);
          if (this._view === "statistics") {
            await this._loadStatistics(false);
            if (this._statisticsTab === "forecast") await this._loadForecast(false);
          }
          if (this._view === "maintenance") await this._loadMaintenance(false);
          if (!this._editingAny) this._render();
        },
        { type: "vacuum_schedule/scheduler/subscribe" }
      );
    } catch (_) {
      // Push is an optimization. Exact backend timers and manual refresh remain authoritative.
      this._unsubscribePush = null;
    }
  }

  _markGlobalNoticesReady(entryId = this._schedulerEntryId) {
    const selected=entryId || this._schedulerEntryId;
    if (!selected) return;
    if (!this._data || !this._schedulerData || !this._settingsData) return;
    if (this._settingsDataEntryId !== selected) return;
    this._globalNoticesReadyEntryId = selected;
  }

  async _loadScheduler() {
    if (!this._hass) return;
    try {
      this._schedulerData = await this._callWs({ type: "vacuum_schedule/scheduler/status" });
      this._markGlobalNoticesReady();
      this._scheduleMidnightRefresh();
      if (!this._editingAny) this._render();
    } catch (err) {
      this._error = this._errorText(err);
      this._render();
    }
  }

  async _loadSettings(render = true) {
    if (!this._hass) return;
    const entryId = this._selectEntry(this._schedulerEntryId, false);
    if (!entryId) { this._settingsData = null; this._settingsDataEntryId = null; return; }
    try {
      const languageBefore = this._language;
      this._settingsData = await this._callWs({ type:"vacuum_schedule/settings/get", entry_id:entryId });
      this._settingsDataEntryId = entryId;
      this._markGlobalNoticesReady(entryId);
      this._syncExecutionSettingsDraft();
      this._syncPolicyDraft();
      this._syncInterfaceLanguageDraft();
      if (this._language !== languageBefore || this._translationLanguage !== this._language) {
        await this._ensureTranslations();
      }
      this._settingsLoadedAtMs = Date.now();
      if (render && !this._editingAny) this._render();
    } catch (err) {
      this._error = this._errorText(err);
      if (render) this._render();
    }
  }

  async _loadNotifications(render = true) {
    if (!this._hass) return;
    const entryId = this._selectEntry(this._schedulerEntryId, false);
    if (!entryId) { this._notificationData = null; this._notificationMessageDetail=null; return; }
    try {
      const params = new URLSearchParams(window.location.search);
      const urlDelivery = params.get("delivery");
      const urlMessage = params.get("message");
      const deliveryId = this._notificationHistorySelectedId || urlDelivery || null;
      const eventId = this._notificationMessageSelectedId || urlMessage || null;
      this._notificationData = await this._callWs({
        type:"vacuum_schedule/notifications/get", entry_id:entryId, language:this._language,
        ...(deliveryId?{delivery_id:deliveryId}:{}), ...(eventId?{event_id:eventId}:{})
      });
      const selected = this._notificationData?.selected_message_id || eventId || null;
      this._notificationMessageSelectedId = selected;
      if (selected) {
        this._notificationMessageDetail = await this._callWs({
          type:"vacuum_schedule/notifications/detail", entry_id:entryId, event_id:selected, language:this._language
        });
      } else {
        this._notificationMessageDetail = null;
      }
      this._syncNotificationGlobalDraft();
      if (render && !this._editingAny) this._render();
    } catch (err) {
      this._error = this._errorText(err);
      if (render) this._render();
    }
  }

  async _loadMaintenance(render = true) {
    if (!this._hass) return;
    const entryId = this._selectEntry(this._schedulerEntryId, false);
    if (!entryId) { this._maintenanceData = null; return; }
    try {
      this._maintenanceData = await this._callWs({type:"vacuum_schedule/maintenance/get",entry_id:entryId,limit:200});
      if(render && !this._editingAny)this._render();
    } catch(err) {
      this._error=this._errorText(err);
      if(render)this._render();
    }
  }

  _statisticsDateValue(date) {
    const value = date instanceof Date ? date : new Date(date);
    if (Number.isNaN(value.getTime())) return "";
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  _statisticsParseDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12, 0, 0, 0);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  _statisticsEnsureDateRange() {
    const filters = this._statisticsFilters || (this._statisticsFilters = {});
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    if (!filters.date_to) filters.date_to = this._statisticsDateValue(today);
    if (!filters.date_from) {
      // Pick a real quick-range by default so Statistics never opens with an
      // unlabeled ad-hoc interval. Current month is useful while still bounded.
      const start = new Date(today.getFullYear(), today.getMonth(), 1, 12, 0, 0, 0);
      filters.date_from = this._statisticsDateValue(start);
    }
    return filters;
  }

  _statisticsRangeIso(value, endOfDay = false) {
    const date = this._statisticsParseDate(value);
    if (!date) return null;
    if (endOfDay) date.setHours(23, 59, 59, 999);
    else date.setHours(0, 0, 0, 0);
    return date.toISOString();
  }

  _statisticsQuickRanges() {
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const addDays = (date, amount) => { const value = new Date(date); value.setDate(value.getDate() + amount); return value; };
    const monday = addDays(today, -((today.getDay() + 6) % 7));
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1, 12, 0, 0, 0);
    const previousMonthStart = new Date(today.getFullYear(), today.getMonth() - 1, 1, 12, 0, 0, 0);
    const previousMonthEnd = new Date(today.getFullYear(), today.getMonth(), 0, 12, 0, 0, 0);
    const range = (key, label, from, to) => ({key, label, from:this._statisticsDateValue(from), to:this._statisticsDateValue(to)});
    return [
      range("today", this._tr("panel.statistics_today"), today, today),
      range("yesterday", this._tr("panel.statistics_yesterday"), addDays(today, -1), addDays(today, -1)),
      range("day_before_yesterday", this._tr("panel.statistics_day_before_yesterday"), addDays(today, -2), addDays(today, -2)),
      range("current_week", this._tr("panel.statistics_current_week"), monday, today),
      range("previous_week", this._tr("panel.statistics_previous_week"), addDays(monday, -7), addDays(monday, -1)),
      range("current_month", this._tr("panel.statistics_current_month"), monthStart, today),
      range("previous_month", this._tr("panel.statistics_previous_month"), previousMonthStart, previousMonthEnd),
    ];
  }

  async _loadStatistics(render = true) {
    if (!this._hass) return;
    const entryId = this._selectEntry(this._schedulerEntryId, false);
    if (!entryId) { this._statisticsData = null; return; }
    const f=this._statisticsEnsureDateRange();
    const payload={type:"vacuum_schedule/statistics/get",entry_id:entryId,limit:500,
      schedule_id:f.schedule_id||"all",zone_id:f.zone_id||"all",execution_mode:f.execution_mode||"all",execution_source:f.execution_source||"all"};
    const start=this._statisticsRangeIso(f.date_from,false);
    const end=this._statisticsRangeIso(f.date_to,true);
    if(start) payload.start=start;
    if(end) payload.end=end;
    try {
      this._statisticsData=await this._callWs(payload);
      if(render && !this._editingAny)this._render();
    } catch(err) {
      this._error=this._errorText(err);
      if(render)this._render();
    }
  }

  async _loadForecast(render = true) {
    if (!this._hass || this._forecastLoading) return;
    const entryId = this._selectEntry(this._schedulerEntryId, false);
    if (!entryId) { this._forecastData = null; this._forecastDataEntryId = null; this._forecastError = null; return; }
    this._forecastLoading = true;
    this._forecastError = null;
    if(render && !this._editingAny)this._render();
    let timeoutId = null;
    try {
      const request = this._callWs({type:"vacuum_schedule/statistics/forecast/get",entry_id:entryId});
      const timeout = new Promise((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(this._tr("panel.forecast_load_timeout"))), 20000);
      });
      this._forecastData = await Promise.race([request, timeout]);
      this._forecastDataEntryId = entryId;
    } catch(err) {
      this._forecastData = null;
      this._forecastDataEntryId = null;
      this._forecastError = this._errorText(err);
      this._error = this._forecastError;
    } finally {
      if (timeoutId !== null) clearTimeout(timeoutId);
      this._forecastLoading = false;
      if(render && !this._editingAny)this._render();
    }
  }

  async _load(force = false) {
    if (!this._hass || this._loading) return;
    try {
      await this._ensureTranslations();
    } catch (err) {
      this._error = this._errorText(err);
      this._render();
      return;
    }
    const now = Date.now();
    if (!force && now - this._lastLoadAt < 750) return;
    this._lastLoadAt = now;
    const hadContent = !!this._data;
    this._loading = true;
    this._error = null;
    if (hadContent) this._syncLoadingIndicator();
    else this._render();
    try {
      const [scheduleData, schedulerData] = await Promise.all([
        this._callWs({ type: "vacuum_schedule/schedules/list" }),
        this._callWs({ type: "vacuum_schedule/scheduler/status" }),
      ]);
      this._data = scheduleData;
      this._schedulerData = schedulerData;
      this._selectEntry(this._schedulerEntryId, false);
      this._markGlobalNoticesReady();
      // Resolve the URL view before loading view-specific data. On a direct
      // reload of ?view=statistics the constructor still has _view="status";
      // loading Statistics before applying the URL would therefore be skipped
      // forever and leave the page on the loading placeholder.
      if (await this._applyLocationContext()) return;
      await this._loadSettings(false);
      await this._loadNotifications(false);
      if (this._view === "statistics") {
        await this._loadStatistics(false);
        if (this._statisticsTab === "forecast") await this._loadForecast(false);
      }
      if (this._view === "maintenance") await this._loadMaintenance(false);
      this._scheduleMidnightRefresh();
    } catch (err) {
      this._error = this._errorText(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _syncLoadingIndicator() {
    const indicator = this.shadowRoot?.querySelector(".loading-indicator");
    if (indicator) indicator.classList.toggle("visible", this._loading);
    const refresh = this.shadowRoot?.querySelector("button.refresh");
    if (refresh) refresh.disabled = this._loading;
  }

  _errorText(err) {
    const code = err?.code || err?.error?.code;
    if (code) {
      const key = `error.${code}`;
      const localized = this._translations?.[key] ?? this._fallbackTranslations?.[key];
      if (localized) return localized;
    }
    return err?.message || err?.error?.message || this._tr("error.unknown");
  }

  _mdi(name) {
    return `<ha-icon class="button-icon" icon="mdi:${this._escape(name)}"></ha-icon>`;
  }

  _toggleHomeAssistantMenu() {
    // Home Assistant listens for this event at the application shell and
    // toggles the native navigation drawer on narrow layouts.
    this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
  }

  _toastHtml() {
    if (!this._notice) return "";
    const icon = this._notice.kind === "error" ? "alert-circle-outline" : "check-circle-outline";
    return `<div class="ui-toast ${this._notice.kind === "error" ? "error-toast" : "success-toast"}" role="status">${this._mdi(icon)}<span>${this._escape(this._notice.message)}</span></div>`;
  }

  _notify(message, kind = "success", timeout = 2800) {
    if (!message) return;
    this._notice = { message, kind };
    if (this._noticeTimer) clearTimeout(this._noticeTimer);
    const host = this.shadowRoot?.querySelector(".toast-host");
    if (host) host.innerHTML = this._toastHtml();
    this._noticeTimer = setTimeout(() => {
      this._notice = null;
      const current = this.shadowRoot?.querySelector(".toast-host");
      if (current) current.innerHTML = "";
    }, timeout);
  }


  _closeModal(value = null) {
    const pending = this._modalPending;
    if (!pending) return;
    this._modalPending = null;
    const host = this.shadowRoot?.querySelector(".dialog-host");
    const dialog = host?.querySelector("dialog.vs-dialog");
    try { if (dialog?.open) dialog.close(); } catch (_) {}
    if (host) host.innerHTML = "";
    pending.resolve(value);
  }

  _openModal({ title, message = "", confirmLabel = null, cancelLabel = null, danger = false, input = null, checkbox = null }) {
    if (this._modalPending) this._closeModal(null);
    return new Promise((resolve) => {
      this._modalPending = { resolve };
      const host = this.shadowRoot?.querySelector(".dialog-host");
      if (!host) { this._modalPending = null; resolve(null); return; }
      const confirmText = confirmLabel || this._tr("panel.confirm");
      const cancelText = cancelLabel || this._tr("panel.cancel");
      const inputHtml = input ? `<label class="dialog-field"><span>${this._escape(input.label || "")}</span><input class="dialog-input" type="${this._escape(input.type || "text")}" value="${this._escape(input.value || "")}" ${input.min ? `min="${this._escape(input.min)}"` : ""} ${input.max ? `max="${this._escape(input.max)}"` : ""}></label>` : "";
      const checkboxHtml = checkbox ? `<label class="dialog-option"><input class="dialog-checkbox" type="checkbox" ${checkbox.checked ? "checked" : ""}><span>${this._escape(checkbox.label || "")}</span></label>` : "";
      host.innerHTML = `<dialog class="vs-dialog" aria-labelledby="vs-dialog-title">
        <form method="dialog" class="vs-dialog-card">
          <div class="vs-dialog-header"><div class="vs-dialog-icon ${danger ? "danger-icon" : ""}">${this._mdi(danger ? "alert-outline" : "help-circle-outline")}</div><div><h2 id="vs-dialog-title">${this._escape(title)}</h2>${message ? `<div class="vs-dialog-message">${this._escape(message)}</div>` : ""}</div></div>
          ${inputHtml}
          ${checkboxHtml}
          <div class="vs-dialog-actions"><button type="button" class="ghost dialog-cancel">${this._escape(cancelText)}</button><button type="submit" class="${danger ? "danger" : "primary"} dialog-confirm">${this._escape(confirmText)}</button></div>
        </form>
      </dialog>`;
      const dialog = host.querySelector("dialog.vs-dialog");
      const finish = (confirmed) => {
        if (!this._modalPending) return;
        if (!confirmed) { this._closeModal(null); return; }
        const field = dialog.querySelector(".dialog-input");
        if (field && !field.reportValidity()) return;
        const option = dialog.querySelector(".dialog-checkbox");
        if (option) { this._closeModal({confirmed:true, checked:!!option.checked}); return; }
        this._closeModal(field ? field.value : true);
      };
      dialog.querySelector(".dialog-cancel")?.addEventListener("click", () => finish(false));
      dialog.querySelector("form")?.addEventListener("submit", (event) => { event.preventDefault(); finish(true); });
      dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        const r = dialog.getBoundingClientRect();
        const inside = event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
        if (!inside) finish(false);
      });
      try { dialog.showModal(); } catch (_) { dialog.setAttribute("open", ""); }
      const field = dialog.querySelector(".dialog-input");
      if (field) { field.focus(); field.select?.(); }
      else dialog.querySelector(".dialog-confirm")?.focus();
    });
  }

  _confirmAction(title, message, confirmLabel = null, danger = true) {
    return this._openModal({ title, message, confirmLabel, danger });
  }

  _confirmActionWithCheckbox(title, message, checkboxLabel, confirmLabel = null, danger = false) {
    return this._openModal({ title, message, confirmLabel, danger, checkbox: {label:checkboxLabel, checked:false} });
  }

  _promptAction(title, message, { label, value = "", type = "text", min = null, max = null, confirmLabel = null, danger = false } = {}) {
    return this._openModal({ title, message, confirmLabel, danger, input: { label, value, type, min, max } });
  }



  _remainingSeconds(until) {
    if (!until) return 0;
    const ms = Date.parse(String(until));
    if (!Number.isFinite(ms)) return 0;
    const serverNow = Date.parse(String(this._settingsData?.scheduler_now || ""));
    const referenceNow = Number.isFinite(serverNow) ? Date.now() + (serverNow - (this._settingsLoadedAtMs || Date.now())) : Date.now();
    return Math.max(0, Math.ceil((ms - referenceNow) / 1000));
  }

  _clearZoneCountdownTimer() {
    if (this._zoneCountdownTimer) {
      clearInterval(this._zoneCountdownTimer);
      this._zoneCountdownTimer = null;
    }
  }

  _updateZoneCountdownCells() {
    if (!this.shadowRoot || this._view !== "settings" || this._editingAny) return;
    let hasActive = false;
    let expired = false;
    this.shadowRoot.querySelectorAll("[data-zone-countdown-kind]").forEach((el) => {
      const until = el.dataset.stabilizingUntil || "";
      const remaining = this._remainingSeconds(until);
      if (remaining > 0) hasActive = true;
      else expired = true;
      const kind = el.dataset.zoneCountdownKind;
      const label=el.querySelector(".quiet-status-label");
      const value=kind === "busy"
        ? (remaining > 0 ? this._tr("panel.busy_value_s", {p1: remaining}) : this._tr("panel.configured_currently_not_busy"))
        : (remaining > 0 ? this._tr("panel.inaccessible_value_s", {p1: remaining}) : this._tr("panel.configured_currently_accessible"));
      if(label) label.textContent=value; else el.textContent=value;
      el.classList.toggle("zone-live-warning", remaining > 0);
      el.classList.toggle("zone-live-good", remaining <= 0);
      el.classList.toggle("quiet-status-warning", remaining > 0);
      el.classList.toggle("quiet-status-good", remaining <= 0);
      el.classList.remove("zone-live-neutral", "zone-live-bad", "quiet-status-neutral", "quiet-status-bad");
    });
    if (!hasActive) this._clearZoneCountdownTimer();
    if (expired && !this._zoneCountdownReloading) {
      this._zoneCountdownReloading = true;
      Promise.resolve(this._loadSettings(false)).finally(() => {
        this._zoneCountdownReloading = false;
        if (!this._editingAny && this._view === "settings") this._render();
      });
    }
  }

  _syncZoneCountdownTimer() {
    this._clearZoneCountdownTimer();
    if (this._view !== "settings" || this._editingAny) return;
    const nodes = [...this.shadowRoot.querySelectorAll("[data-zone-countdown-kind]")];
    if (!nodes.some((el) => this._remainingSeconds(el.dataset.stabilizingUntil) > 0)) return;
    this._zoneCountdownTimer = setInterval(() => this._updateZoneCountdownCells(), 1000);
  }

  _configuredEntries() {
    const entries = this._data?.entries || [];
    if (entries.length) return entries;
    return (this._schedulerData?.entries || []).map((entry) => ({
      entry_id: entry.entry_id,
      title: entry.title,
      vacuum_entity_id: entry.vacuum_entity_id || "",
    }));
  }

  _rememberEntry(entryId) {
    if (!entryId) return;
    try { window.localStorage.setItem(this._entryStorageKey, entryId); } catch (_) {}
  }

  _rememberedEntry() {
    try { return window.localStorage.getItem(this._entryStorageKey); } catch (_) { return null; }
  }

  _selectEntry(entryId, updateUrl = false) {
    const entries = this._configuredEntries();
    if (!entries.length) {
      this._schedulerEntryId = null;
      return null;
    }
    const ids = new Set(entries.map((entry) => entry.entry_id));
    let selected = entryId && ids.has(entryId) ? entryId : null;
    if (!selected && this._schedulerEntryId && ids.has(this._schedulerEntryId)) selected = this._schedulerEntryId;
    if (!selected) {
      const remembered = this._rememberedEntry();
      if (remembered && ids.has(remembered)) selected = remembered;
    }
    if (!selected) selected = entries[0].entry_id;
    this._schedulerEntryId = selected;
    this._rememberEntry(selected);
    if (updateUrl) this._replaceContextUrl(selected);
    return selected;
  }

  _replaceContextUrl(entryId = this._schedulerEntryId) {
    const query = new URLSearchParams();
    if (entryId) query.set("config_entry", entryId);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    history.replaceState(null, "", `/vacuum-schedule${suffix}`);
  }

  async _applyLocationContext() {
    const params = new URLSearchParams(window.location.search);
    const configEntryId = params.get("config_entry");
    const legacyEditorEntryId = params.get("entry");
    const requestedEntryId = configEntryId || legacyEditorEntryId;
    this._selectEntry(requestedEntryId, false);

    const requestedView = params.get("view");
    const messageId = params.get("message");
    const deliveryId = params.get("delivery");
    const jobId = params.get("job");
    if (["status","maintenance","schedules","statistics","notifications","settings","testing"].includes(requestedView)) this._view = requestedView;
    if (messageId || deliveryId) {
      // Message identity wins over legacy view=status&job=... links. Old 0.8.11
      // pushes carried delivery+job and must now open the compact archive message.
      if (messageId) this._notificationMessageSelectedId = messageId;
      if (deliveryId) this._notificationHistorySelectedId = deliveryId;
      this._view = "notifications";
      this._deepLinkJobId = null;
    } else if (jobId) {
      this._deepLinkJobId = jobId;
      if (!requestedView) this._view = "status";
    }

    const scheduleId = params.get("schedule");
    const editorRequested = params.get("editor") === "1" || !!legacyEditorEntryId;
    if (editorRequested && requestedEntryId && !this._editor) {
      await this._openEditor(requestedEntryId, scheduleId || null, false);
      return true;
    }
    return false;
  }

  _currentEntryMeta() {
    const entries = this._configuredEntries();
    const selectedId = this._selectEntry(this._schedulerEntryId, false);
    return entries.find((entry) => entry.entry_id === selectedId) || null;
  }

  _currentEntryTitle() {
    if (this._editor?.entry_title) return this._editor.entry_title;
    return this._currentEntryMeta()?.title || "";
  }

  async _openEditor(entryId, scheduleId = null, updateUrl = true) {
    this._view = "schedules";
    this._loading = true;
    this._error = null;
    this._render();
    try {
      this._editor = await this._callWs({
        type: "vacuum_schedule/schedules/editor",
        entry_id: entryId,
        ...(scheduleId ? { schedule_id: scheduleId } : {}),
      });
      this._form = JSON.parse(JSON.stringify(this._editor.form));
      this._schedulerEntryId = entryId;
      this._rememberEntry(entryId);
      if (updateUrl) {
        const query = new URLSearchParams({ config_entry: entryId, editor: "1" });
        if (scheduleId) query.set("schedule", scheduleId);
        history.replaceState(null, "", `/vacuum-schedule?${query.toString()}`);
      }
    } catch (err) {
      this._error = this._errorText(err);
      this._editor = null;
      this._form = null;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _closeEditor() {
    this._editor = null;
    this._form = null;
    this._error = null;
    this._replaceContextUrl(this._schedulerEntryId);
    this._render();
  }

  async _save() {
    if (!this._editor || !this._form || this._saving) return;
    const durationInputs=[...(this.shadowRoot?.querySelectorAll("[data-duration-minutes]")||[])];
    for(const el of durationInputs){
      const key=el.dataset.durationMinutes;
      const optional=el.dataset.durationOptional==="1";
      if(optional && !String(el.value||"").trim()){ this._form[key]=""; el.setCustomValidity(""); continue; }
      const parsed=this._parseDurationHm(el.value,{min:key==="execution_window_minutes"?1:0,max:10080});
      if(parsed===null){
        el.setCustomValidity(this._tr("panel.invalid_hours_minutes"));
        el.reportValidity();
        return;
      }
      el.setCustomValidity("");
      this._form[key]=parsed;
    }
    this._saving = true;
    this._error = null;
    this._render();
    try {
      const schedulePayload={...this._form};
      delete schedulePayload._weekday_draft_times;
      delete schedulePayload._weekday_bulk_time;
      const result = await this._callWs({
        type: "vacuum_schedule/schedules/save",
        entry_id: this._editor.entry_id,
        ...(this._editor.schedule_id ? { schedule_id: this._editor.schedule_id } : {}),
        schedule: schedulePayload,
      });
      this._editor = null;
      this._form = null;
      this._replaceContextUrl(this._schedulerEntryId);
      await this._load();
      this._notify(this._tr("panel.saved"));
      return result;
    } catch (err) {
      this._error = this._errorText(err);
    } finally {
      this._saving = false;
      this._render();
    }
  }

  async _delete(entryId, scheduleId, name) {
    const confirmed = await this._confirmAction(
      this._tr("panel.delete_schedule"),
      this._tr("panel.schedule_value_will_be_deleted_this_action_cannot_be_undone", {p1: name}),
      this._tr("panel.delete"),
      true,
    );
    if (!confirmed) return;
    this._loading = true;
    this._error = null;
    this._render();
    try {
      await this._callWs({
        type: "vacuum_schedule/schedules/delete",
        entry_id: entryId,
        schedule_id: scheduleId,
      });
      this._loading = false;
      await this._load();
      this._notify(this._tr("panel.deleted"));
    } catch (err) {
      this._error = this._errorText(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _formatNumber(value, digits = 1) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    const maximumFractionDigits = Math.max(0, Math.min(6, Number.isFinite(Number(digits)) ? Math.trunc(Number(digits)) : 1));
    return numeric.toLocaleString(this._language || undefined, { maximumFractionDigits });
  }

  _timeZone() {
    return this._hass?.config?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  }

  _schedulerOffsetSeconds() {
    const entries = this._schedulerData?.entries || [];
    const selected = entries.find((x) => x.entry_id === this._schedulerEntryId) || entries[0];
    return Number(selected?.clock?.offset_seconds || 0);
  }

  _displayNow() {
    return new Date(Date.now() + this._schedulerOffsetSeconds() * 1000);
  }

  _dateParts(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: this._timeZone(), year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(value);
    const get = (type) => Number(parts.find((x) => x.type === type)?.value);
    return { year: get("year"), month: get("month"), day: get("day") };
  }

  _dateOrdinal(value) {
    const p = this._dateParts(value);
    return Math.floor(Date.UTC(p.year, p.month - 1, p.day) / 86400000);
  }

  _zonedMidnightEpochMs(year, month, day) {
    const targetAsUtc = Date.UTC(year, month - 1, day, 0, 0, 0, 0);
    let guess = targetAsUtc;
    for (let i = 0; i < 3; i += 1) {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: this._timeZone(), year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
      }).formatToParts(new Date(guess));
      const get = (type) => Number(parts.find((x) => x.type === type)?.value);
      const representedAsUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
      guess += targetAsUtc - representedAsUtc;
    }
    return guess;
  }

  _clearMidnightRefresh() {
    if (this._midnightTimer) {
      clearTimeout(this._midnightTimer);
      this._midnightTimer = null;
    }
  }

  _scheduleMidnightRefresh() {
    this._clearMidnightRefresh();
    if (!this._connected) return;
    try {
      const virtualNow = this._displayNow();
      const p = this._dateParts(virtualNow);
      const next = new Date(Date.UTC(p.year, p.month - 1, p.day + 1));
      const nextVirtualMidnight = this._zonedMidnightEpochMs(
        next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate()
      );
      const delay = Math.max(250, nextVirtualMidnight - virtualNow.getTime() + 100);
      this._midnightTimer = setTimeout(() => {
        this._midnightTimer = null;
        if (!this._connected) return;
        if (!this._editingAny) this._render();
        this._scheduleMidnightRefresh();
      }, Math.min(delay, 2147483000));
    } catch (_) {
      // Relative labels are a presentation enhancement; failed scheduling must not affect the scheduler UI.
    }
  }

  _formatDateTime(value) {
    if (!value) return "—";
    try {
      const d = new Date(value);
      const dayDelta = this._dateOrdinal(d) - this._dateOrdinal(this._displayNow());
      const relative = {
        "-2": this._tr("common.relative.day_before_yesterday"),
        "-1": this._tr("common.relative.yesterday"),
        "0": this._tr("common.relative.today"),
        "1": this._tr("common.relative.tomorrow"),
        "2": this._tr("common.relative.day_after_tomorrow"),
      };
      const time = new Intl.DateTimeFormat(this._locale, {
        timeZone: this._timeZone(), hour: "2-digit", minute: "2-digit"
      }).format(d);
      if (Object.prototype.hasOwnProperty.call(relative, String(dayDelta))) {
        return `${relative[String(dayDelta)]}, ${time}`;
      }
      return new Intl.DateTimeFormat(this._locale, {
        timeZone: this._timeZone(), weekday: "short", day: "2-digit", month: "2-digit",
        hour: "2-digit", minute: "2-digit"
      }).format(d);
    } catch (_) { return value; }
  }

  _formatTimeOnly(value) {
    if (!value) return "—";
    try {
      return new Intl.DateTimeFormat(this._locale, {
        timeZone: this._timeZone(), hour: "2-digit", minute: "2-digit"
      }).format(new Date(value));
    } catch (_) { return String(value); }
  }

  _formatPlannedDateTime(value) {
    if (!value) return "—";
    try {
      const d = new Date(value);
      if (this._dateOrdinal(d) === this._dateOrdinal(this._displayNow())) return this._formatTimeOnly(value);
    } catch (_) {}
    return this._formatDateTime(value);
  }

  _formatHoursMinutes(minutes, prefix = "") {
    const numeric = Number(minutes);
    if (!Number.isFinite(numeric) || numeric < 0) return "—";
    const total = Math.max(0, Math.round(numeric));
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    return `${prefix}${String(hours).padStart(2,"0")}:${String(mins).padStart(2,"0")}`;
  }

  _minutesBetween(later, earlier) {
    const a = Date.parse(String(later || ""));
    const b = Date.parse(String(earlier || ""));
    if (!Number.isFinite(a) || !Number.isFinite(b) || a < b) return null;
    return Math.round((a - b) / 60000);
  }

  _activeLatestOffset(job) {
    const minutes = this._minutesBetween(job?.deadline_at, job?.planned_start);
    return minutes === null ? "—" : this._formatHoursMinutes(minutes, "+");
  }

  _activeEarlyOffset(job) {
    let minutes = Number(job?.force?.max_advance_minutes);
    if (!Number.isFinite(minutes) || minutes <= 0) {
      minutes = this._minutesBetween(job?.planned_start, job?.force?.window_start);
    }
    return Number.isFinite(minutes) && minutes > 0 ? this._formatHoursMinutes(minutes, "-") : "—";
  }

  _activeOffsetCell(relative, absolute) {
    const secondary=relative!=="—"&&absolute?this._formatTimeOnly(absolute):"";
    return `<span class="job-offset-cell"><span>${this._escape(relative)}</span>${secondary?`<small>${this._escape(secondary)}</small>`:""}</span>`;
  }

  _activeLatestCell(job) {
    return this._activeOffsetCell(this._activeLatestOffset(job),job?.deadline_at);
  }

  _activeEarlyCell(job) {
    return this._activeOffsetCell(this._activeEarlyOffset(job),job?.force?.window_start);
  }

  _durationHmEditorValue(minutes, fallback = 0) {
    const numeric = Number(minutes);
    return this._formatHoursMinutes(Number.isFinite(numeric) ? numeric : fallback);
  }

  _parseDurationHm(value, { min = 0, max = 10080 } = {}) {
    const match = String(value || "").trim().match(/^(\d{1,3}):([0-5]\d)$/);
    if (!match) return null;
    const total = Number(match[1]) * 60 + Number(match[2]);
    if (!Number.isFinite(total) || total < min || total > max) return null;
    return total;
  }

  _clearActiveLiveTimer() {
    if (this._activeLiveTimer) {
      clearInterval(this._activeLiveTimer);
      this._activeLiveTimer = null;
    }
  }


  _clearActiveClockTimer() {
    if (this._activeClockTimer) { clearInterval(this._activeClockTimer); this._activeClockTimer=null; }
  }

  _startActiveClockTimer() {
    this._clearActiveClockTimer();
    if (!this._connected) return;
    this._activeClockTimer=setInterval(()=>this._updateActiveNowCells(),1000);
  }

  _startActiveLiveTimer() {
    this._clearActiveLiveTimer();
    if (!this._connected) return;
    this._activeLiveTimer = setInterval(() => this._refreshActiveLiveStatus(), 10000);
  }

  async _refreshActiveLiveStatus() {
    if (!this._hass || this._view !== "status" || this._editingAny || this._activeLiveRefreshing) return;
    this._activeLiveRefreshing=true;
    try {
      this._schedulerData=await this._callWs({type:"vacuum_schedule/scheduler/status"});
      this._updateActiveNowCells();
      this._updateRobotStatusStrip();
    } catch (_) {
      // Live status is best-effort; normal scheduler push/reload remains authoritative.
    } finally {
      this._activeLiveRefreshing=false;
    }
  }

  _updateActiveNowCells() {
    if (!this.shadowRoot || this._view !== "status" || this._editingAny) return;
    const entry=(this._schedulerData?.entries||[]).find((x)=>x.entry_id===this._schedulerEntryId)||(this._schedulerData?.entries||[])[0];
    const jobs=new Map((entry?.active_jobs||[]).map((job)=>[String(job.job_id||""),job]));
    this.shadowRoot.querySelectorAll("[data-live-job]").forEach((cell)=>{
      const job=jobs.get(String(cell.dataset.liveJob||""));
      if(job) cell.innerHTML=this._nowHtml(job);
    });
  }

  _updateRobotStatusStrip() {
    if (!this.shadowRoot || this._view !== "status") return;
    const host=this.shadowRoot.querySelector("[data-live-robot-status]");
    const entry=this._statusEntry();
    if(host&&entry) host.outerHTML=this._robotStatusHtml(entry);
  }

  _weekdayLabel(code) {
    const translated = this._tr(`common.weekday.${code}`);
    return translated.startsWith("common.weekday.") ? code : translated;
  }

  _targetTypeLabel(value) {
    const translated = this._tr(`common.target_type.${value}`);
    return translated.startsWith("common.target_type.") ? value : translated;
  }

  _normalizePreset(value) {
    return String(value ?? "").trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  }

  _isOpaquePreset(value) {
    return this._normalizePreset(value) === "custom";
  }

  _presetLabel(kind, value) {
    const key = this._normalizePreset(value);
    const translationKey = `common.preset.${kind}.${key}`;
    const translated = this._tr(translationKey);
    if (translated !== translationKey) return translated;
    const raw = String(value ?? "").trim();
    return raw ? raw.replaceAll("_", " ") : this._noOverrideLabel();
  }

  _compactPresetLabel(kind, value) {
    const key = this._normalizePreset(value);
    const translationKey = `common.preset_short.${kind}.${key}`;
    const translated = this._tr(translationKey);
    return translated !== translationKey ? translated : this._presetLabel(kind, value);
  }

  _cleaningModeScope(params) {
    const key = this._normalizePreset(params?.cleaning_mode);
    if (!key || key === "custom" || key === "standard") return { vacuum: true, mop: true };
    const hasMop = key.includes("mop") || key.includes("wash");
    const hasVacuum = key.includes("vac") || key.includes("sweep");
    if (hasMop && hasVacuum) return { vacuum: true, mop: true };
    if (hasMop) return { vacuum: false, mop: true };
    if (hasVacuum) return { vacuum: true, mop: false };
    return { vacuum: true, mop: true };
  }

  _cleaningProfileFieldVisible(params, key) {
    const scope = this._cleaningModeScope(params);
    const field = String(key || "").endsWith("_entity_id") ? String(key).slice(0, -10) : String(key || "");
    if (field === "fan_mode" || field === "cleaning_route") return scope.vacuum;
    if (field === "mop_mode" || field === "water_mode") return scope.mop;
    return true;
  }

  _compactCleaningProfileParts(params, { includeDefaultPasses = true } = {}) {
    const p = params || {};
    const parts = [];
    if (p.cleaning_mode) parts.push(this._compactPresetLabel("cleaning_mode", p.cleaning_mode));
    if (includeDefaultPasses || (p.passes !== undefined && p.passes !== null)) parts.push(`${p.passes ?? 1}×`);
    if (p.fan_mode && this._cleaningProfileFieldVisible(p, "fan_mode")) parts.push(this._compactPresetLabel("fan_mode", p.fan_mode));
    if (p.cleaning_route && this._cleaningProfileFieldVisible(p, "cleaning_route")) parts.push(`${this._tr("panel.route_short")}: ${this._compactPresetLabel("cleaning_route", p.cleaning_route)}`);
    if (p.mop_mode && this._cleaningProfileFieldVisible(p, "mop_mode")) parts.push(`${this._tr("panel.mop_short")}: ${this._compactPresetLabel("mop_mode", p.mop_mode)}`);
    if (p.water_mode && this._cleaningProfileFieldVisible(p, "water_mode")) parts.push(`${this._tr("panel.water_short")}: ${this._compactPresetLabel("water_mode", p.water_mode)}`);
    return parts;
  }

  _compactCleaningProfileText(params, options = {}) {
    return this._compactCleaningProfileParts(params, options).join(" · ");
  }

  _noOverrideLabel() {
    return this._tr("panel.do_not_override_use_the_vacuum_setting");
  }

  _fieldHelp(kind) {
    const key = `panel.help.${kind}`;
    const translated = this._tr(key);
    return translated === key ? "" : translated;
  }

  _controlRawValue(option) {
    try {
      const decoded = JSON.parse(String(option?.value || ""));
      if (Array.isArray(decoded) && decoded.length === 2) return String(decoded[1]);
    } catch (_) {}
    return String(option?.label || option?.value || "");
  }

  _controlSourcePrefix(option) {
    const label = String(option?.label || "");
    const pos = label.lastIndexOf(": ");
    return pos > 0 ? label.slice(0, pos) : "";
  }

  _cleaningChips(row) {
    const profile = this._compactCleaningProfileText(row.cleaning_summary || {});
    let policyHtml = "";
    if ((row.targets || []).length > 1) {
      const policy = String(row.zone_execution_policy || "progressive");
      const policyLabel = policy === "combined" ? this._tr("panel.zone_execution_combined") : this._tr("panel.zone_execution_progressive");
      policyHtml = `<div class="schedule-execution-policy">${this._escape(policyLabel)}</div>`;
    }
    return `<div class="schedule-cleaning-profile">${this._escape(profile || "—")}</div>${policyHtml}`;
  }

  _scheduleOverrideSummary(row) {
    const source=row.weekday_overrides_codes||{};
    const base=row.cleaning_summary||{};
    const order=["mon","tue","wed","thu","fri","sat","sun"];
    const groups=new Map();
    const describe=(override)=>this._compactCleaningProfileText({...base,...(override||{})});
    for(const code of order){
      const text=source[code]?describe(source[code]):"";
      if(!text)continue;
      if(!groups.has(text))groups.set(text,[]);
      groups.get(text).push(this._weekdayLabel(code));
    }
    if(!groups.size)return "";
    const body=[...groups.entries()].map(([text,days])=>`${days.join(" · ")} — ${text}`).join("; ");
    const title=`${this._tr("panel.correction")}: ${body}`;
    return `<div class="schedule-corrections-summary" title="${this._escape(title)}"><b>${this._tr("panel.correction")}:</b> ${[...groups.entries()].map(([text,days])=>`${this._escape(days.join(" · "))} — ${this._escape(text)}`).join("; ")}</div>`;
  }

  _forceScheduleSummary(row) {
    if (!row?.force_enabled) return "";
    const minutes=Math.max(0,Number(row.force_max_advance_minutes||0));
    const duration=this._formatHoursMinutes(minutes,"-");
    const groups=(row.force_condition_groups||[]).length;
    const preemptKey=row.force_preempts_scheduled?"panel.force_preempt_short_yes":"panel.force_preempt_short_no";
    const preemptTitle=row.force_preempts_scheduled?this._tr("panel.force_preempts_scheduled_help"):this._tr("panel.force_respects_scheduled_help");
    return `<div class="schedule-force-summary"><b>${this._tr("panel.source_force")}</b> <span class="force-preempt-short" title="${this._escape(preemptTitle)}">${this._escape(this._tr(preemptKey))}</span>: ${this._tr("panel.force_summary",{window:duration,priority:row.force_priority??0,groups})}</div>`;
  }

  _scheduleRule(row) {
    const times = row.weekday_times_codes || {};
    const order = ["mon","tue","wed","thu","fri","sat","sun"];
    const grouped = new Map();
    for (const code of order) {
      const value = times[code];
      if (!value) continue;
      const key = String(value).slice(0,5);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(this._weekdayLabel(code));
    }
    const weekly = [...grouped.entries()].map(([time, days]) => `${days.join(" · ")} — ${time}`);
    const dates = (row.dates || []).map((value) => `${value} — ${String(row.local_time || "").slice(0,5)}`);
    return [...weekly, ...dates].join("; ") || "—";
  }

  _scheduleJob(entryId, scheduleId) {
    const entry = (this._schedulerData?.entries || []).find((x) => x.entry_id === entryId);
    return (entry?.active_jobs || []).find((x) => x.schedule_id === scheduleId) || null;
  }

  _listHtml() {
    const entries = this._data?.entries || [];
    if (!entries.length) {
      return `<div class="empty">${this._tr("panel.no_vacuum_schedule_entries_configured")}</div>`;
    }
    const selectedId = this._selectEntry(this._schedulerEntryId, false);
    const visibleEntries = entries.filter((entry) => entry.entry_id === selectedId);
    return `${this._entrySelectorHtml()}${visibleEntries.map((entry) => {
      const rows = entry.schedules || [];
      const tableRows = rows.length
        ? rows.map((row) => `
          <tr class="${row.enabled ? "" : "disabled-row"}">
            <td class="name-cell" data-label="${this._tr("panel.job")}"><div class="schedule-name-line"><span class="status-dot ${row.enabled ? "on" : "off"}"></span><strong>${this._escape(row.name)}</strong><small class="schedule-revision">(${this._tr("panel.revision_short")} ${row.revision})</small></div><div class="schedule-id-line"><span>ID:</span> <code title="${this._escape(row.schedule_id)}">${this._escape(row.schedule_id)}</code></div>${this._scheduleJob(entry.entry_id,row.schedule_id) ? `<div class="schedule-row-state"><span class="schedule-row-state-text ${this._escape(this._stateChipClass(this._scheduleJob(entry.entry_id,row.schedule_id)))}">${this._escape(this._stateLabel(this._scheduleJob(entry.entry_id,row.schedule_id).state))}</span></div>` : ""}</td>
            <td data-label="${this._tr("panel.when")}">${this._escape(this._scheduleRule(row))}</td>
            <td data-label="${this._tr("panel.targets")}"><div class="targets schedule-targets">${(row.targets_display || []).map((x) => `<span class="schedule-target-text">${this._escape(x)}</span>`).join("")}</div></td>
            <td data-label="${this._tr("panel.cleaning")}"><div class="chips">${this._cleaningChips(row)}</div>${this._scheduleOverrideSummary(row)}${this._forceScheduleSummary(row)}</td>
            <td data-label="${this._tr("panel.next_run")}">${this._escape(this._formatDateTime(row.next_run))}</td>
            <td class="actions schedule-actions-cell mobile-actions-cell" data-label="${this._tr("panel.actions")}"><div class="schedule-actions">
              <button class="ghost icon-only compact-icon-action schedule-toggle" data-entry="${this._escape(entry.entry_id)}" data-schedule="${this._escape(row.schedule_id)}" data-enabled="${row.enabled?"false":"true"}" title="${row.enabled?this._tr("panel.disable_schedule"):this._tr("panel.enable_schedule")}" aria-label="${row.enabled?this._tr("panel.disable_schedule"):this._tr("panel.enable_schedule")}">${this._mdi(row.enabled?"pause-circle-outline":"play-circle-outline")}</button>
              <button class="primary icon-only compact-icon-action edit" data-entry="${this._escape(entry.entry_id)}" data-schedule="${this._escape(row.schedule_id)}" title="${this._tr("panel.edit_schedule")}" aria-label="${this._tr("panel.edit_schedule")}">${this._mdi("pencil-outline")}</button>
              <button class="danger icon-only compact-icon-action delete" data-entry="${this._escape(entry.entry_id)}" data-schedule="${this._escape(row.schedule_id)}" data-name="${this._escape(row.name)}" title="${this._tr("panel.delete_schedule")}" aria-label="${this._tr("panel.delete_schedule")}">${this._mdi("delete-outline")}</button>
            </div></td>
          </tr>`).join("")
        : `<tr><td colspan="6" class="empty-cell">${this._tr("panel.no_schedules_yet")}</td></tr>`;
      return `
        <section class="entry-card">
          <div class="entry-header">
            <div><h2>${this._escape(entry.title)}</h2><div class="muted">${this._escape(entry.vacuum_entity_id)}</div></div>
            <button class="primary add" data-entry="${this._escape(entry.entry_id)}">${this._mdi("calendar-plus-outline")}<span>${this._tr("panel.add")}</span></button>
          </div>
          <div class="table-wrap">
            <table class="mobile-card-table schedule-table">
              <thead><tr>
                <th>${this._tr("panel.job")}</th>
                <th>${this._tr("panel.when")}</th>
                <th>${this._tr("panel.targets")}</th>
                <th>${this._tr("panel.cleaning")}</th>
                <th>${this._tr("panel.next_run")}</th>
                <th>${this._tr("panel.actions")}</th>
              </tr></thead>
              <tbody>${tableRows}</tbody>
            </table>
          </div>
        </section>`;
    }).join("")}`;
  }

  _stateLabel(value) {
    if (!value) return "—";
    const key = `common.job_state.${value}`;
    const translated = this._tr(key);
    return translated === key ? value : translated;
  }

  _stateChipClass(job) {
    if (!job) return "state-UNKNOWN";
    if (job.state === "FINISHED") {
      if (job.result === "SUCCESS" || job.result === "PARTIAL_SUCCESS") return "state-FINISHED-SUCCESS";
      if (job.result === "SUPPRESSED") return "state-FINISHED-SUPPRESSED";
      if (job.result === "FAILED") return "state-FINISHED-FAILED";
    }
    return `state-${job.state || "UNKNOWN"}`;
  }

  _stateChipHtml(job) {
    if (!job) return "";
    return `<span class="ui-chip status-badge state-chip ${this._escape(this._stateChipClass(job))}">${this._escape(this._stateLabel(job.state))}</span>`;
  }

  _resultLabel(value) {
    if (!value) return "—";
    const key = `common.job_result.${value}`;
    const translated = this._tr(key);
    return translated === key ? value : translated;
  }

  _reasonLabel(value) {
    if (!value) return "—";
    const key = `common.reason.${value}`;
    const translated = this._tr(key);
    return translated === key ? this._blockerLabel(value) : translated;
  }

  _preflightDecisionLabel(value) {
    if (!value) return "—";
    const key = `common.preflight_decision.${value}`;
    const translated = this._tr(key);
    return translated === key ? value : translated;
  }

  _blockerLabel(value) {
    const rawValue = String(value || "");
    if (rawValue.startsWith("synthetic_")) {
      const raw = this._presentationBlockerCode(rawValue.slice("synthetic_".length));
      const blockerKey = `common.blocker.${raw}`;
      const blocker = this._tr(blockerKey);
      return this._tr("common.blocker.synthetic", { blocker: blocker === blockerKey ? raw.replaceAll("_", " ") : blocker });
    }
    const presentationValue=this._presentationBlockerCode(rawValue);
    const key = `common.blocker.${presentationValue}`;
    const translated = this._tr(key);
    return translated === key ? presentationValue.replaceAll("_", " ") : translated;
  }

  _presentationBlockerCode(value) {
    const code=String(value||"");
    return code==="execution_lease_busy"?"vacuum_busy":code;
  }

  _blockerCodes(values) {
    const result=[];const seen=new Set();
    for(const value of (values||[])){
      const raw=String(value||"");
      const synthetic=raw.startsWith("synthetic_");
      const body=synthetic?raw.slice("synthetic_".length):raw;
      const canonical=this._presentationBlockerCode(body);
      const code=synthetic?`synthetic_${canonical}`:canonical;
      if(!code||seen.has(code))continue;
      seen.add(code);result.push(code);
    }
    return result;
  }

  _blockerLabels(values) {
    return this._blockerCodes(values).map((code)=>this._blockerLabel(code));
  }

  _mergedBlockerDurations(values) {
    const totals=new Map();
    for(const [raw,seconds] of Object.entries(values||{})){
      const code=this._presentationBlockerCode(raw);
      totals.set(code,(totals.get(code)||0)+Math.max(0,Number(seconds)||0));
    }
    return [...totals.entries()];
  }

  _schedulerBusyDetail(job) {
    const lease=job?.execution_lease||{};
    const schedule=String(lease.owner_schedule_name||"").trim();
    if(!schedule)return this._tr("panel.robot_busy_scheduler_generic");
    return String(lease.owner_attempt_state||"")==="COMPLETION_PENDING"
      ?this._tr("panel.robot_busy_scheduler_completion",{schedule})
      :this._tr("panel.robot_busy_scheduler_running",{schedule});
  }

  _activeExecutionAttempt(job) {
    const rows=Object.values(job?.execution_attempts || {});
    const terminal=new Set(["COMPLETED","FAILED","CANCELLED"]);
    return rows.slice().reverse().find((x)=>!terminal.has(String(x?.state||""))) || null;
  }

  _executionSubstatus(job) {
    const attempt=this._activeExecutionAttempt(job);
    if(!attempt) return null;
    const state=String(attempt.state||"");
    const key=`panel.execution_substate.${state}`;
    const translated=this._tr(key);
    return translated===key?null:translated;
  }

  _forceReadinessHtml(job) {
    const force=job?.force;
    if(!force || job.state!=="PLANNED") return "";
    const window=String(force.window_state||"");
    if(window==="BEFORE_WINDOW") return `<small class="force-readiness force-readiness-neutral">${this._tr("panel.force_available_from",{time:this._formatDateTime(force.window_start)})}</small>`;
    if(window!=="IN_WINDOW") return "";
    if(force.execution_committed) return `<small class="force-readiness force-readiness-pass">${this._tr("panel.force_executed")}</small>`;
    if(force.deferred_by_arbiter_class==="manual") return `<small class="force-readiness force-readiness-neutral">${this._tr("panel.force_deferred_manual")}</small>`;
    if(force.deferred_by_arbiter_class==="scheduled") return `<small class="force-readiness force-readiness-neutral">${this._tr("panel.force_deferred_scheduled")}</small>`;
    if(force.plan_protection && force.plan_protection.allowed===false) return `<small class="force-readiness force-readiness-wait">${this._forceProtectionText(force)}</small>`;
    if(force.selected && force.eligible) return `<small class="force-readiness force-readiness-pass">${this._tr("panel.force_selected_candidate")}</small>`;
    if(!force.conditions_matched) return `<small class="force-readiness force-readiness-neutral">${this._tr("panel.force_conditions_not_met")}</small>`;
    const blockers=this._blockerLabels(force.preflight_blockers||[]);
    if(blockers.length) return `<small class="force-readiness force-readiness-wait">${this._tr("panel.force_conditions_met_but_blocked",{blockers:blockers.join(", ")})}</small>`;
    if(force.eligible) return `<small class="force-readiness force-readiness-pass">${this._tr("panel.force_eligible_lower_priority")}</small>`;
    return "";
  }

  _forceProtectionText(force) {
    const protection=force?.plan_protection;
    if(!protection || protection.allowed!==false) return "";
    const next=this._formatDateTime(protection.next_planned_start);
    const gap=this._historyDurationValue(protection.available_gap_seconds,true);
    const required=this._historyDurationValue(protection.required_gap_seconds,true);
    const estimate=protection.estimate||{};
    if(protection.estimate_available){
      const raw=this._historyDurationValue(estimate.raw_percentile_value,true);
      const percentile=`P${Number(estimate.percentile||90)}`;
      const delta=`${this._formatNumber(Number(estimate.delta_percent||0),1)}%`;
      return this._tr("panel.force_plan_protection_blocked_estimated",{time:next,gap,required,percentile,raw,delta});
    }
    return "";
  }

  _diagnosticActivityActive(value) {
    const text=String(value??"").trim().toLowerCase().replaceAll("-","_").replaceAll(" ","_");
    if(!text) return false;
    return !new Set(["0","off","false","no","idle","standby","ready","none","inactive","disabled","unknown","unavailable","not_available","not_supported","not_drying","not_washing","complete","completed","done","ok","charging_complete"]).has(text);
  }

  _observationActivityCode(obs = {}, resources = {}) {
    const status=String(obs.vendor_status||"").toLowerCase();
    const statusMap={
      going_to_wash_the_mop:"going_to_wash_mop", washing_the_mop:"washing_mop",
      emptying_the_bin:"emptying_bin", attaching_the_mop:"attaching_mop", detaching_the_mop:"detaching_mop",
      waiting_to_charge:"waiting_to_charge", going_to_target:"going_to_target", relocating:"relocating",
      transitioning:"transitioning", mapping:"mapping", sweeping:"vacuuming", mopping:"mopping",
      sweep_and_mop:"vacuuming_and_mopping", segment_cleaning:"cleaning_zone", zoned_cleaning:"cleaning_zone",
      spot_cleaning:"spot_cleaning", cleaning:"cleaning", returning:"returning_to_dock",
      returning_home:"returning_to_dock", manual_mode:"manual_control", remote_control_active:"manual_control",
    };
    if(statusMap[status]) return statusMap[status];
    if(this._diagnosticActivityActive(obs.dust_collection_status)) return "emptying_bin";
    if(this._diagnosticActivityActive(obs.wash_status)||this._diagnosticActivityActive(obs.wash_phase)) return "washing_mop";
    if(this._diagnosticActivityActive(obs.dry_status)) return "drying_mop";
    if(this._diagnosticActivityActive(obs.charge_status)) return "charging";
    if(status==="charging") return "charging";
    if(status==="charging_complete"||status==="docked") return "on_dock";
    if(status==="idle") return "idle";
    const phase=String(obs.phase||"").toUpperCase();
    if(phase==="RETURNING") return "returning_to_dock";
    if(phase==="PAUSED") return "paused";
    if(phase==="SERVICE_BLOCKED") return "service_blocked";
    if(phase==="SERVICE") return "dock_service";
    if(phase==="CLEANING") return "cleaning";
    if(phase==="ERROR") return "robot_error";
    if(phase==="UNAVAILABLE") return "unavailable";
    const normalized=String(obs.normalized_state||"").toLowerCase();
    const charging=resources?.["vacuum.charging"]?.effective;
    if(normalized==="docked") return charging===true?"charging":"on_dock";
    if(normalized==="returning") return "returning_to_dock";
    if(normalized==="paused") return "paused";
    if(normalized==="cleaning") return "cleaning";
    if(normalized==="error") return "robot_error";
    if(normalized==="unavailable") return "unavailable";
    if(normalized==="idle") return "idle";
    return "unknown";
  }

  _robotActivityCode(job) {
    const attempt=this._activeExecutionAttempt(job);
    const live=job?.live||{};
    const obs=live.observation||attempt?.metadata?.last_observation||attempt?.metadata?.start_observation||{};
    const attemptState=String(live.attempt_state||attempt?.state||"");
    if(attemptState==="ROBOT_ERROR_BLOCKED") return "robot_error";
    if(attemptState==="ROBOT_SERVICE_BLOCKED") return "service_blocked";
    if(attemptState==="PAUSED") return "paused";
    if(attemptState==="COMPLETION_PENDING") return "completion_check";
    if(attemptState==="CANCEL_REQUESTED") return "stopping";
    if(String(job?.state||"")==="STARTING") {
      if(attemptState==="PREPARING") return "preparing";
      if(attemptState==="RECONCILING") return "reconciling";
      return "starting";
    }
    const code=this._observationActivityCode(obs);
    return code==="unknown"?"running":code;
  }

  _activityLabel(code) {
    const key=`panel.live_activity.${code}`;
    const translated=this._tr(key);
    return translated===key?this._tr("panel.unknown"):translated;
  }

  _liveZoneNames(job) {
    const live=job?.live||{};
    const names=[];
    const add=(value)=>{const name=String(value||"").trim();if(name&&!names.includes(name))names.push(name);};
    if(Array.isArray(live.current_zone_names)) live.current_zone_names.forEach(add);
    add(live.current_zone_name);
    if(names.length) return names;

    let zoneIds=[];
    if(Array.isArray(live.current_zone_ids)) zoneIds=live.current_zone_ids.map(v=>String(v||"").trim()).filter(Boolean);
    else if(live.current_zone_id) zoneIds=[String(live.current_zone_id).trim()];
    if(!zoneIds.length){
      const attempt=this._activeExecutionAttempt(job);
      if(Array.isArray(attempt?.zone_ids)) zoneIds=attempt.zone_ids.map(v=>String(v||"").trim()).filter(Boolean);
    }
    zoneIds.forEach((zoneId)=>add(job?.zone_runs?.[zoneId]?.zone_name||zoneId));
    if(names.length) return names;

    const targets=Array.isArray(job?.targets_display)?job.targets_display:[];
    if(targets.length===1) add(targets[0]);
    return names;
  }

  _liveZoneActivityLabel(job,code,label) {
    const names=this._liveZoneNames(job);
    if(!names.length) return label;
    const cleaningCodes=new Set(["cleaning","cleaning_zone","vacuuming","mopping","vacuuming_and_mopping","spot_cleaning"]);
    if(cleaningCodes.has(code)){
      if(names.length===1) return this._tr("panel.live_activity.cleaning_zone_named",{zone:names[0]});
      return this._tr("panel.live_activity.cleaning_zones_named",{zones:names.join(" · ")});
    }
    return `${label} · ${names.join(" · ")}`;
  }

  _liveActivityLabel(job) {
    const code=this._robotActivityCode(job);
    let label=this._activityLabel(code);
    if(code==="completion_check"){
      const dueMs=new Date(job?.live?.completion_check_due_at||"").getTime();
      if(Number.isFinite(dueMs)){
        const seconds=Math.max(0,Math.ceil((dueMs-this._displayNow().getTime())/1000));
        label=this._tr("panel.live_activity.completion_check_remaining",{seconds});
      }
    }
    if(label===this._tr("panel.unknown")) label=this._tr("panel.cleaning_in_progress");
    return this._liveZoneActivityLabel(job,code,label);
  }

  _liveMetrics(job) {
    const live=job?.live||{};
    const obs=live.observation||{};
    const started=live.started_at||job.actual_start||job.starting_at||null;
    const now=this._displayNow();
    const parts=[];
    let elapsedSeconds=null;
    if(started){
      const startMs=new Date(started).getTime();
      if(Number.isFinite(startMs)) elapsedSeconds=Math.max(0,(now.getTime()-startMs)/1000);
    }
    if(elapsedSeconds!==null) parts.push(this._formatDurationSeconds(elapsedSeconds));

    const activity=this._robotActivityCode(job);
    const floorCodes=new Set(["cleaning","cleaning_zone","vacuuming","mopping","vacuuming_and_mopping","spot_cleaning"]);
    const interpolation=live.interpolation||{};
    const maxAge=Number(interpolation.max_age_seconds??12);
    const anchorAge=(value)=>{
      const anchorMs=new Date(value||"").getTime();
      return Number.isFinite(anchorMs)?Math.max(0,(now.getTime()-anchorMs)/1000):Number.NaN;
    };
    const mayInterpolate=(age)=>floorCodes.has(activity)&&Number.isFinite(age)&&age>0.5&&age<=Math.max(1,maxAge);

    const rawArea=live.cleaning_area_m2??obs.cleaning_area_m2;
    let area=(rawArea===null||rawArea===undefined||rawArea==="")?Number.NaN:Number(rawArea);
    let areaEstimated=false;
    const areaRate=Number(interpolation.area_m2_per_second);
    const areaAge=anchorAge(interpolation.area_anchor_at||interpolation.anchor_at||obs.observed_at);
    const maxAreaIncrement=Number(interpolation.max_area_increment_m2??0.2);
    if(mayInterpolate(areaAge)&&Number.isFinite(area)&&area>=0&&Number.isFinite(areaRate)&&areaRate>0&&Number.isFinite(maxAreaIncrement)&&maxAreaIncrement>0){
      const increment=Math.min(areaRate*areaAge,maxAreaIncrement);
      if(increment>0){ area+=increment; areaEstimated=true; }
    }
    if(Number.isFinite(area)&&area>0) parts.push(`${areaEstimated?"≈":""}${this._formatNumber(area,1)} ${this._tr("panel.m2_unit")}`);

    const battery=Number(live.battery_percent??obs.battery_percent);
    if(new Set(["charging","waiting_to_charge"]).has(activity)&&Number.isFinite(battery)) parts.push(`${Math.round(battery)}%`);
    const robotPercentRaw=live.robot_clean_percent;
    const robotPercent=(robotPercentRaw===null||robotPercentRaw===undefined||robotPercentRaw==="")?Number.NaN:Number(robotPercentRaw);
    const expected=Number(live.forecast?.expected_total_seconds);
    let progress=null, estimated=false;
    const interpolatedPercentRate=Number(interpolation.percent_per_second);
    if(Number.isFinite(robotPercent)&&robotPercent>=0&&robotPercent<=100){
      let value=robotPercent;
      const percentAge=anchorAge(interpolation.percent_anchor_at||interpolation.anchor_at||obs.observed_at);
      const maxPercentIncrement=Number(interpolation.max_percent_increment??2);
      if(mayInterpolate(percentAge)&&Number.isFinite(interpolatedPercentRate)&&interpolatedPercentRate>0&&Number.isFinite(maxPercentIncrement)&&maxPercentIncrement>0&&value<100){
        value=Math.min(99,value+Math.min(interpolatedPercentRate*percentAge,maxPercentIncrement));
        estimated=true;
      }
      progress=Math.round(value);
    } else {
      const forecastProgressRaw=live.forecast?.estimated_progress_percent;
      const forecastProgress=(forecastProgressRaw===null||forecastProgressRaw===undefined||forecastProgressRaw==="")?Number.NaN:Number(forecastProgressRaw);
      if(Number.isFinite(forecastProgress)&&forecastProgress>=0){
        progress=Math.min(95,Math.max(0,Math.round(forecastProgress)));
        estimated=true;
      } else if(elapsedSeconds!==null&&Number.isFinite(expected)&&expected>0){
        progress=Math.min(95,Math.max(0,Math.round(elapsedSeconds/expected*100)));
        estimated=true;
      }
    }
    const serviceCodes=new Set(["going_to_wash_mop","washing_mop","emptying_bin","attaching_mop","detaching_mop","drying_mop","charging","waiting_to_charge","dock_service","completion_check"]);
    if(progress!==null){
      if(progress>=100&&serviceCodes.has(activity)) parts.push(this._tr("panel.floor_cleaning_100"));
      else parts.push(`${estimated?"≈":""}${progress}%`);
    }

    const forecastEtaMs=new Date(live.forecast?.eta_at||"").getTime();
    let etaMs=Number.isFinite(forecastEtaMs)?forecastEtaMs:null;
    if(!Number.isFinite(etaMs)&&started&&Number.isFinite(expected)&&expected>0){
      etaMs=new Date(started).getTime()+expected*1000;
    }
    if(Number.isFinite(etaMs)){
      if(etaMs>=now.getTime()-30000) parts.push(this._tr("panel.live_eta",{time:this._formatTimeOnly(new Date(etaMs))}));
      else parts.push(this._tr("panel.live_forecast_overrun"));
    }
    return parts;
  }

  _currentExecutionActionsHtml(job) {
    const button=(action,icon,label,style="ghost")=>`<button type="button" class="${style} icon-only compact-icon-action job-action job-now-action" data-job="${this._escape(job.job_id)}" data-action="${this._escape(action)}" data-current-execution="1" title="${this._escape(label)}" aria-label="${this._escape(label)}">${this._mdi(icon)}</button>`;
    if(job.state==="STARTING") return `<span class="job-now-actions">${button("cancel","stop-circle-outline",this._tr("panel.stop_current_execution"),"danger")}</span>`;
    if(job.state!=="RUNNING") return "";
    const attempt=this._activeExecutionAttempt(job);
    const real=String(job.execution_mode||"")==="REAL";
    let pauseResume="";
    if(real&&attempt){
      if(String(attempt.state)==="PAUSED") pauseResume=button("resume","play-circle-outline",this._tr("panel.resume_current_execution"),"primary");
      else if(String(attempt.state)==="ROBOT_ERROR_BLOCKED") pauseResume="";
      else pauseResume=button("pause","pause-circle-outline",this._tr("panel.pause_current_execution"));
    }
    return `<span class="job-now-actions">${pauseResume}${button("cancel","stop-circle-outline",this._tr("panel.stop_current_execution"),"danger")}</span>`;
  }

  _nowHtml(job) {
    const force=this._forceReadinessHtml(job);
    const stack=(main)=>force?`<span class="job-now-stack">${main}${force}</span>`:main;
    const state=String(job?.state||"");
    const withControls=(main)=>`<span class="job-now-layout">${main}${this._currentExecutionActionsHtml(job)}</span>`;
    if(state==="STARTING") return withControls(`<span class="job-now-main readiness readiness-starting"><b>${this._escape(this._liveActivityLabel(job))}</b></span>`);
    if(state==="RUNNING"){
      const attempt=this._activeExecutionAttempt(job);
      const blocked=String(job?.live?.attempt_state||attempt?.state||"")==="ROBOT_ERROR_BLOCKED";
      const tone=blocked?"wait":"pass";
      const metrics=this._liveMetrics(job);
      let label=this._liveActivityLabel(job);
      if(blocked){
        const obs=job?.live?.observation||attempt?.metadata?.last_observation||{};
        const physical=attempt?.metadata?.physical_error||{};
        const reason=obs.vacuum_error||obs.dock_error||physical.vacuum_error||physical.dock_error||physical.raw_vacuum_error||physical.raw_dock_error||this._tr("panel.unknown");
        label=this._tr("panel.now_error",{reason});
      }
      return withControls(`<span class="job-now-main readiness readiness-${tone}"><b>${this._escape(label)}</b>${metrics.length?`<small>${metrics.map((x)=>this._escape(x)).join(" · ")}</small>`:""}</span>`);
    }
    if(job?.schedule_paused) return `<span class="job-now-main job-now-paused"><b>${this._tr("panel.schedule_paused")}</b></span>`;
    const blockers=this._blockerCodes(job.current_blockers||[]);
    const labels=this._blockerLabels(blockers);
    const forecast=job.start_forecast;
    if(forecast && ["attention","unknown","risk"].includes(forecast.status)) {
      const text=this._tr(`panel.forecast_${forecast.status}`);
      return stack(`<span class="job-now-main readiness readiness-wait"><b>${this._escape(text)}</b>${labels.length?`<small>${this._escape(labels.join(", "))}</small>`:""}</span>`);
    }
    if(state==="WAIT"){
      const text=labels.length?labels.join(" · "):this._tr("panel.unknown");
      return `<span class="job-now-main readiness readiness-wait"><b>${this._escape(text)}</b></span>`;
    }
    if(!job.advisory_checked_at&&!blockers.length) return stack(`<span class="job-now-main readiness readiness-unknown"><b>${this._tr("panel.now_check_at",{time:this._formatDateTime(job.warning_at)})}</b></span>`);
    const decision=job.current_preflight_decision||(blockers.length?"WAIT":"PASS");
    if(decision==="FAIL") return stack(`<span class="job-now-main readiness readiness-fail"><b>${this._escape(this._tr("panel.now_blocked",{reason:labels.join(", ")||this._tr("panel.start_blocked")}))}</b></span>`);
    if(labels.length){
      return stack(`<span class="job-now-main readiness readiness-wait"><b>${this._escape(labels.join(" · "))}</b></span>`);
    }
    return stack(`<span class="job-now-main readiness readiness-pass"><b>${this._tr("panel.ready_to_start")}</b></span>`);
  }

  _resourceStatusText(resource) {
    if(!resource || resource.effective_available!==true) return this._tr("panel.unknown");
    if(resource.effective===true) return this._tr("panel.ok");
    if(resource.effective===false) return this._tr("panel.service_required_short");
    const raw=resource.effective;
    return raw===null||raw===undefined||String(raw)===""?this._tr("panel.unknown"):String(raw);
  }

  _robotStatusHtml(entry) {
    const status=entry?.robot_status||{};
    const obs=status.observation||{};
    const resources=status.resources||{};
    const water=status.water||{};
    const activity=this._observationActivityCode(obs,resources);
    let activityLabel=this._activityLabel(activity);
    const liveJob=(entry?.active_jobs||[]).find((job)=>["STARTING","RUNNING"].includes(String(job?.state||"")) && this._liveZoneNames(job).length);
    const liveZoneNames=liveJob?this._liveZoneNames(liveJob):[];
    const zoneCleaningActivity=new Set(["cleaning","cleaning_zone","vacuuming","mopping","vacuuming_and_mopping","spot_cleaning"]).has(activity);
    if(liveZoneNames.length && zoneCleaningActivity){
      activityLabel=liveZoneNames.length===1
        ?this._tr("panel.live_activity.cleaning_zone_named",{zone:liveZoneNames[0]})
        :this._tr("panel.live_activity.cleaning_zones_named",{zones:liveZoneNames.join(" · ")});
    }
    const observedBattery=obs.battery_percent===null||obs.battery_percent===undefined?NaN:Number(obs.battery_percent);
    const resourceBattery=resources?.["vacuum.battery_percent"]?.effective===null||resources?.["vacuum.battery_percent"]?.effective===undefined?NaN:Number(resources?.["vacuum.battery_percent"]?.effective);
    const battery=Number.isFinite(observedBattery)?observedBattery:(Number.isFinite(resourceBattery)?resourceBattery:null);
    if(activity==="charging" && battery!==null && Math.round(battery)>=100) activityLabel=this._activityLabel("on_dock");
    const batteryText=battery===null?this._tr("panel.unknown"):`${Math.round(battery)}%`;
    const batteryIcon=battery===null?"battery-unknown":battery<=20?"battery-low":battery<=60?"battery-medium":"battery-high";
    const cleanPct=water?.clean?.remaining_percent===null||water?.clean?.remaining_percent===undefined?NaN:Number(water.clean.remaining_percent);
    const dirtyPct=water?.dirty?.filled_percent===null||water?.dirty?.filled_percent===undefined?NaN:Number(water.dirty.filled_percent);
    const cleanResource=resources?.["dock.clean_water"];
    const dirtyResource=resources?.["dock.dirty_water"];
    const cleanText=Number.isFinite(cleanPct)?`≈${this._formatNumber(cleanPct,0)}%`:this._resourceStatusText(cleanResource);
    const dirtyText=Number.isFinite(dirtyPct)?`≈${this._formatNumber(dirtyPct,0)}%`:this._resourceStatusText(dirtyResource);
    const cleanMl=water?.clean?.estimate_ml_eq===null||water?.clean?.estimate_ml_eq===undefined?NaN:Number(water.clean.estimate_ml_eq);
    const dirtyMl=water?.dirty?.estimate_ml_eq===null||water?.dirty?.estimate_ml_eq===undefined?NaN:Number(water.dirty.estimate_ml_eq);
    const cleanDetail=Number.isFinite(cleanMl)?`≈${this._formatNumber(cleanMl,0)} ${this._tr("panel.ml_unit")}`:this._resourceStatusText(cleanResource);
    const dirtyDetail=Number.isFinite(dirtyMl)?`≈${this._formatNumber(dirtyMl,0)} ${this._tr("panel.ml_unit")}`:this._resourceStatusText(dirtyResource);
    const detergent=this._resourceStatusText(resources?.["dock.detergent"]);
    const mop=this._resourceStatusText(resources?.["mop.attached"]);
    const rawState=obs.vendor_status||obs.raw_state||obs.normalized_state||this._tr("panel.unknown");
    const observedAt=obs.observed_at?this._formatDateTime(obs.observed_at):"—";
    return `<details class="robot-status-strip" data-live-robot-status>
      <summary>
        <span class="robot-status-primary">${this._mdi("robot-vacuum")}<span><small>${this._tr("panel.robot_short")}</small><b>${this._escape(activityLabel)}</b></span></span>
        <span class="robot-status-metric">${this._mdi(batteryIcon)}<span><small>${this._tr("panel.battery")}</small><b>${this._escape(batteryText)}</b></span></span>
        <span class="robot-status-metric">${this._mdi("water")}<span><small>${this._tr("panel.clean_water_short")}</small><b>${this._escape(cleanText)}</b></span></span>
        <span class="robot-status-metric">${this._mdi("water-alert")}<span><small>${this._tr("panel.dirty_water_short")}</small><b>${this._escape(dirtyText)}</b></span></span>
        <span class="robot-status-expand">${this._mdi("chevron-down")}</span>
      </summary>
      <div class="robot-status-details">
        <span><b>${this._tr("panel.robot_status")}:</b> ${this._escape(rawState)}</span>
        <span><b>${this._tr("panel.battery")}:</b> ${this._escape(batteryText)}</span>
        <span><b>${this._tr("panel.clean_water")}:</b> ${this._escape(cleanDetail)}</span>
        <span><b>${this._tr("panel.dirty_water")}:</b> ${this._escape(dirtyDetail)}</span>
        <span><b>${this._tr("panel.detergent")}:</b> ${this._escape(detergent)}</span>
        <span><b>${this._tr("panel.mop")}:</b> ${this._escape(mop)}</span>
        <span><b>${this._tr("panel.observed_at")}:</b> ${this._escape(observedAt)}</span>
      </div>
    </details>`;
  }

  _activeJobModeHtml(job) {
    const text=this._compactCleaningProfileText(job?.cleaning_params||{});
    return `<span class="active-job-mode" title="${this._escape(text||this._tr("panel.unknown"))}">${this._escape(text||"—")}</span>`;
  }

  _readinessHtml(job) {
    const force=this._forceReadinessHtml(job);
    const stack=(main)=>force?`<span class="job-readiness-stack">${main}${force}</span>`:main;
    if (job.state === "STARTING") {
      const sub=this._executionSubstatus(job);
      return `<span class="readiness readiness-starting">${this._escape(sub||this._tr("panel.start_allowed"))}</span>`;
    }
    if (job.state === "RUNNING") {
      const attempt=this._activeExecutionAttempt(job);
      const sub=this._executionSubstatus(job);
      if(String(attempt?.state||"")==="ROBOT_ERROR_BLOCKED") return `<span class="readiness readiness-wait">${this._escape(sub||this._tr("panel.active_robot_error_blocked"))}</span>`;
      return `<span class="readiness readiness-pass">${this._escape(sub||this._tr("panel.cleaning_in_progress"))}</span>`;
    }
    const blockers = this._blockerCodes(job.current_blockers || []);
    if (!job.advisory_checked_at && !blockers.length) {
      return stack(`<span class="readiness readiness-unknown">${this._tr("panel.check")} ${this._escape(this._formatDateTime(job.warning_at))}</span>`);
    }
    const labels = this._blockerLabels(blockers);
    const decision = job.current_preflight_decision || (blockers.length ? "WAIT" : "PASS");
    if (decision === "FAIL") {
      const text = labels.join(", ") || this._tr("panel.start_blocked");
      return stack(`<span class="readiness readiness-fail">${this._escape(text)}</span>`);
    }
    if (labels.length) {
      return stack(`<span class="readiness readiness-wait">${this._escape(labels.join(", "))}</span>`);
    }
    return stack(`<span class="readiness readiness-pass">${this._tr("panel.ready_to_start")}</span>`);
  }

  _currentReadinessDetailsHtml(job) {
    const blockers = this._blockerCodes(job.current_blockers || []);
    if (!job.advisory_checked_at && !blockers.length) {
      return `<span class="plain-status">${this._tr("panel.the_check_has_not_run_yet_scheduled_for")} ${this._escape(this._formatDateTime(job.warning_at))}</span>`;
    }
    if (blockers.length) {
      const css = job.current_preflight_decision === "FAIL" ? "blocker-chip blocker-fail" : "blocker-chip";
      return this._blockerLabels(blockers).map((label) => `<span class="ui-chip ${css}">${this._escape(label)}</span>`).join("");
    }
    return `<span class="plain-status ok">${this._tr("panel.there_are_currently_no_blockers")}</span>`;
  }

  _advisorySnapshotHtml(job) {
    if (!job.advisory_checked_at) {
      return `<span class="plain-status">${this._tr("panel.the_preliminary_check_has_not_run_yet")}</span>`;
    }
    const blockers = this._blockerCodes(job.advisory_blockers || []);
    if (blockers.length) {
      const css = job.advisory_preflight_decision === "FAIL" ? "blocker-chip blocker-fail" : "blocker-chip";
      return this._blockerLabels(blockers).map((label) => `<span class="ui-chip ${css}">${this._escape(label)}</span>`).join("");
    }
    return `<span class="plain-status ok">${this._tr("panel.no_blockers_were_found_during_the_preliminary_check")}</span>`;
  }

  _statusEntry() {
    const entries = this._schedulerData?.entries || [];
    if (!entries.length) return null;
    const selectedId = this._selectEntry(this._schedulerEntryId, false);
    return entries.find((entry) => entry.entry_id === selectedId) || null;
  }

  _entrySelectorHtml() {
    const entries = this._configuredEntries();
    if (entries.length <= 1) return "";
    const currentId = this._selectEntry(this._schedulerEntryId, false);
    return `<label class="field entry-selector"><span>${this._tr("panel.robot_vacuum")}</span><select id="scheduler-entry">${entries.map((entry)=>`<option value="${this._escape(entry.entry_id)}" ${currentId===entry.entry_id?"selected":""}>${this._escape(entry.title || entry.vacuum_entity_id || entry.entry_id)}</option>`).join("")}</select></label>`;
  }

  _jobTargetLabels(job) {
    const raw = job.targets || [];
    const display = job.targets_display || [];
    return raw.map((rawValue, index) => {
      const resolved = display[index];
      if (resolved !== null && resolved !== undefined && String(resolved).trim()) return String(resolved);
      return this._tr("panel.cleaning_zone_value", {p1: index + 1});
    });
  }


  _jobCleaningParamsHtml(job) {
    const p = job.cleaning_params || {};
    const items = [];
    const addPreset = (key, label) => {
      const value = p[key];
      if (value === null || value === undefined || String(value).trim() === "") return;
      items.push(`<span class="ui-chip parameter-chip"><b>${this._escape(label)}:</b> ${this._escape(this._presetLabel(key, value))}</span>`);
    };
    addPreset("cleaning_mode", this._tr("panel.cleaning_type"));
    if (this._cleaningProfileFieldVisible(p, "fan_mode")) addPreset("fan_mode", this._tr("panel.suction_power"));
    if (this._cleaningProfileFieldVisible(p, "cleaning_route")) addPreset("cleaning_route", this._tr("panel.cleaning_route"));
    if (this._cleaningProfileFieldVisible(p, "mop_mode")) addPreset("mop_mode", this._tr("panel.mopping_mode"));
    if (this._cleaningProfileFieldVisible(p, "water_mode")) addPreset("water_mode", this._tr("panel.water_flow_intensity"));
    if (p.passes !== null && p.passes !== undefined && String(p.passes).trim() !== "") {
      items.push(`<span class="ui-chip parameter-chip"><b>${this._tr("panel.cleaning_passes")}:</b> ${this._escape(p.passes)}</span>`);
    }
    if (p.minimum_battery_percent !== null && p.minimum_battery_percent !== undefined && String(p.minimum_battery_percent).trim() !== "") {
      items.push(`<span class="ui-chip parameter-chip"><b>${this._tr("panel.min_battery")}:</b> ${this._escape(p.minimum_battery_percent)}%</span>`);
    }
    if (p.minimum_start_window_minutes !== null && p.minimum_start_window_minutes !== undefined && String(p.minimum_start_window_minutes).trim() !== "") {
      items.push(`<span class="ui-chip parameter-chip"><b>${this._tr("panel.min_start_window")}:</b> ${this._escape(this._formatDurationSeconds(Number(p.minimum_start_window_minutes)*60))}</span>`);
    }
    return items.join("") || `<span class="plain-status">${this._tr("panel.vacuum_settings_are_not_overridden")}</span>`;
  }

  _formatValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    if (value === true) return this._tr("panel.yes");
    if (value === false) return this._tr("panel.no");
    return String(value);
  }

  _formatSettingValue(key, value) {
    if (key === "dnd.active") {
      if (value === true) return this._tr("panel.enabled");
      if (value === false) return this._tr("panel.disabled_no_time_restriction");
    }
    if (key === "dnd.starts_at" || key === "dnd.ends_at") {
      const text = String(value ?? "");
      const m = text.match(/^(\d{1,2}):(\d{2})/);
      if (m) return `${m[1].padStart(2,"0")}:${m[2]}`;
    }
    if (["dock.clean_water","dock.dirty_water","dock.detergent","mop.attached"].includes(key)) {
      if (value === true) return this._tr("panel.ok");
      if (value === false) return this._tr("panel.blocks_cleaning");
    }
    return this._formatValue(value);
  }

  _preflightSourcesHtml(job, advisory = false) {
    const report = advisory ? job.metadata?.advisory_preflight_report : job.metadata?.current_preflight_report;
    if (!report) return `<span class="plain-status">${this._tr("panel.pre_flight_source_data_is_not_available_yet")}</span>`;
    const blockers = report.blockers || [];
    let rows = blockers.map((b) => {
      const room = (b.details?.zone_name || b.details?.room_name) ? `<div class="muted">${this._escape(b.details?.zone_name || b.details?.room_name)}</div>` : "";
      const entity = b.entity_id ? `<code>${this._escape(b.entity_id)}</code>` : `<span class="muted">${this._escape(b.source_key || "—")}</span>`;
      return `<tr><td data-label="${this._tr("panel.check")}">${this._escape(this._blockerLabel(b.code))}${room}</td><td data-label="${this._tr("panel.source")}">${entity}</td><td data-label="${this._tr("panel.raw")}">${this._escape(this._formatValue(b.raw_value))}</td><td data-label="${this._tr("panel.normalized")}">${this._escape(this._formatValue(b.normalized_value))}</td><td data-label="${this._tr("panel.decision")}"><span class="readiness readiness-${String(b.decision_class || "WAIT").toLowerCase()}">${this._escape(this._preflightDecisionLabel(b.decision_class || "WAIT"))}</span></td></tr>`;
    });
    if (!rows.length) {
      const values = report.input_snapshot?.values || {};
      const useful = ["vacuum.available","vacuum.activity","vacuum.battery_percent","vacuum.charging","dock.clean_water","dock.dirty_water","dock.detergent","mop.attached","dnd.active","dnd.starts_at","dnd.ends_at"];
      rows = useful.filter((key) => values[key]).map((key) => {
        const item = values[key];
        const source = item.source_entity_id ? `<code>${this._escape(item.source_entity_id)}</code>` : `<span class="muted">${this._escape(key)}</span>`;
        return `<tr><td data-label="${this._tr("panel.check")}">${this._escape(key)}</td><td data-label="${this._tr("panel.source")}">${source}</td><td data-label="${this._tr("panel.raw")}">${this._escape(this._formatValue(item.raw_value ?? item.live))}</td><td data-label="${this._tr("panel.normalized")}">${this._escape(this._formatValue(item.effective))}</td><td data-label="${this._tr("panel.decision")}"><span class="readiness readiness-pass">${this._escape(this._preflightDecisionLabel("PASS"))}</span></td></tr>`;
      });
    }
    return `<div class="table-wrap"><table class="source-table mobile-card-table"><thead><tr><th>${this._tr("panel.check")}</th><th>${this._tr("panel.source")}</th><th>${this._tr("panel.raw")}</th><th>${this._tr("panel.normalized")}</th><th>${this._tr("panel.decision")}</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  _zoneRunsHtml(job) {
    const runs = Object.values(job.zone_runs || {});
    if (!runs.length) return `<span class="plain-status">${this._tr("panel.no_zone_level_data")}</span>`;
    const zoneResult=(r)=> r.result ? this._resultLabel(r.result === "SKIPPED" ? "SKIPPED" : r.result) : this._stateLabel(r.state);
    const label=(r)=>r.zone_name || r.zone_id;
    return `<div class="table-wrap"><table class="source-table mobile-card-table"><thead><tr><th>${this._tr("panel.cleaning_zone")}</th><th>${this._tr("panel.robot_target")}</th><th>${this._tr("panel.state")}</th><th>${this._tr("panel.result_reason")}</th></tr></thead><tbody>${runs.map(r=>`<tr><td data-label="${this._tr("panel.cleaning_zone")}"><b>${this._escape(label(r))}</b></td><td data-label="${this._tr("panel.robot_target")}">${this._escape(`${r.robot_target_type ? this._targetTypeLabel(r.robot_target_type) : "—"}: ${r.robot_target_id || "—"}`)}</td><td data-label="${this._tr("panel.state")}">${this._escape(this._stateLabel(r.state))}</td><td data-label="${this._tr("panel.result_reason")}">${this._escape(r.result === "SKIPPED" ? this._tr("panel.skipped") : (r.result ? this._resultLabel(r.result) : "—"))}${r.reason_code?`<div class="muted">${this._escape(this._reasonLabel(r.reason_code))}</div>`:""}${(r.blockers||[]).length?`<div class="muted">${this._blockerLabels(r.blockers||[]).map(x=>this._escape(x)).join(" · ")}</div>`:""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  _jobNotificationHistoryHtml(job) {
    const sourceRows=(job?.notification_history||this._notificationData?.history||[]);
    const rows=sourceRows.filter(x=>x.job_id===job.job_id);
    if(!rows.length)return `<span class="plain-status">${this._tr("panel.no_notification_deliveries_for_this_job_yet")}</span>`;
    return `<div class="table-wrap"><table class="source-table mobile-card-table"><thead><tr><th>${this._tr("panel.time")}</th><th>${this._tr("panel.event")}</th><th>${this._tr("panel.channel")}</th><th>${this._tr("panel.result")}</th></tr></thead><tbody>${rows.map(x=>`<tr><td data-label="${this._tr("panel.time")}">${this._formatDateTime(x.sent_at||x.created_at)}</td><td data-label="${this._tr("panel.event")}">${this._escape(this._notificationEventLabel(x.event_type))}</td><td data-label="${this._tr("panel.channel")}">${this._escape(`${x.recipient_name||""} · ${x.channel_name||x.target||""}`)}</td><td data-label="${this._tr("panel.result")}">${this._escape(this._notificationDeliveryStatusLabel(x.status))}${x.suppression_reason?`<div class="muted">${this._escape(this._notificationSuppressionLabel(x.suppression_reason))}</div>`:""}${x.error?`<div class="muted">${this._escape(x.error)}</div>`:""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  _jobAuditHtml(job) {
    const rows = job.user_action_history || [];
    if (!rows.length) return `<span class="plain-status">${this._tr("panel.no_manual_actions_yet")}</span>`;
    const actionLabel = (value) => ({
      run_schedule_now:this._tr("panel.run_schedule_now"),
      start_job_now:this._tr("panel.start_now"),
      start_job_now_ignore_busy:this._tr("panel.manual_occupancy_override"),
      skip:this._tr("panel.skip"),
      recheck:this._tr("panel.recheck"),
      cancel:this._tr("panel.cancel_f8a3423"),
      pause:this._tr("panel.pause"),
      resume:this._tr("panel.resume"),
    }[value] || String(value || "—"));
    const sourceLabel = (value) => ({
      frontend:this._tr("panel.panel"),
      ha_action:this._tr("panel.home_assistant_action"),
      mobile_notification:this._tr("panel.mobile_app_button"),
      telegram_notification:this._tr("panel.telegram_button"),
    }[value] || String(value || "—"));
    return `<div class="table-wrap"><table class="source-table mobile-card-table"><thead><tr><th>${this._tr("panel.time")}</th><th>${this._tr("panel.user")}</th><th>${this._tr("panel.action")}</th><th>${this._tr("panel.source")}</th></tr></thead><tbody>${rows.slice().reverse().map(x=>`<tr><td data-label="${this._tr("panel.time")}">${this._formatDateTime(x.at)}</td><td data-label="${this._tr("panel.user")}"><b>${this._escape(x.actor_display || x.actor_name || x.user_id || x.recipient_id || this._tr("panel.unknown"))}</b></td><td data-label="${this._tr("panel.action")}">${this._escape(actionLabel(x.action))}</td><td data-label="${this._tr("panel.source")}">${this._escape(sourceLabel(x.source))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  _attemptRepetitionLabel(a) {
    const requested=Math.max(1,Number(a?.requested_passes ?? a?.cleaning_params?.passes ?? a?.pass_total ?? 1) || 1);
    const physicalTotal=Math.max(1,Number(a?.pass_total||1) || 1);
    const physicalIndex=Math.max(1,Number(a?.pass_index||1) || 1);
    let mode=String(a?.repetition_mode||"").toUpperCase();
    if(!mode) mode=physicalTotal>1?"EMULATED":(requested>1?"NATIVE":"SINGLE");
    if(mode==="EMULATED" && physicalTotal>1) {
      return `${this._tr("panel.emulated_start")} ${physicalIndex} ${this._tr("panel.of")} ${physicalTotal} · ${this._tr("panel.passes")}: ${requested} · ${this._tr("panel.repetition_emulated")}`;
    }
    if(mode==="NATIVE" && requested>1) {
      return `${this._tr("panel.passes")}: ${requested} · ${this._tr("panel.repetition_native")}`;
    }
    return `${this._tr("panel.passes")}: ${requested}`;
  }

  _attemptDiagnosticHtml(a, obs, detailed=false) {
    const blockerCodes=this._blockerCodes([...(obs?.resource_blockers||[]),...(a?.metadata?.resource_blockers||[]),...(a?.metadata?.post_clean_resource_blockers||[])]);
    const physicalError=a?.metadata?.physical_error||{};
    const historicalError=detailed || ["ROBOT_ERROR_BLOCKED","FAILED"].includes(String(a?.state||""));
    const vacuumError=obs?.vacuum_error||obs?.raw_vacuum_error||(historicalError?(physicalError.vacuum_error||physicalError.raw_vacuum_error):null)||null;
    const dockError=obs?.dock_error||obs?.raw_dock_error||(historicalError?(physicalError.dock_error||physicalError.raw_dock_error):null)||null;
    const rows=[];
    if(blockerCodes.length) rows.push(`<div class="readiness readiness-fail"><b>${this._tr("panel.resource_blockers")}:</b> ${this._blockerLabels(blockerCodes).map(v=>this._escape(v)).join(" · ")}</div>`);
    if(vacuumError) rows.push(`<div class="readiness readiness-fail"><b>${this._tr("panel.robot_error_detail")}:</b> ${this._escape(vacuumError)}</div>`);
    if(dockError) rows.push(`<div class="readiness readiness-fail"><b>${this._tr("panel.dock_error_detail")}:</b> ${this._escape(dockError)}</div>`);
    if(String(a?.state||"")==="ROBOT_ERROR_BLOCKED") rows.push(`<div class="readiness readiness-wait"><b>${this._tr("panel.active_robot_error_blocked")}</b> · ${this._tr("panel.error_recovery_deadline",{time:this._formatDateTime(a?.error_recovery_deadline_at)})}</div>`);
    const runtimeIncidents=(a?.metadata?.runtime_incidents||[]).filter((x)=>x&&x.kind==="robot_error");
    if(runtimeIncidents.length) {
      for(const incident of runtimeIncidents) {
        const status=String(incident.status||"active");
        const err=incident.last_error||incident.initial_error||{};
        const reason=err.vacuum_error||err.dock_error||err.raw_vacuum_error||err.raw_dock_error||"—";
        const duration=incident.duration_seconds!==undefined?this._durationBetween(incident.started_at,incident.ended_at):"";
        rows.push(`<div class="muted"><b>${this._tr("panel.runtime_incident")}:</b> ${this._escape(this._tr(`panel.runtime_incident_status.${status}`))} · ${this._escape(reason)} · ${this._formatDateTime(incident.started_at)}${duration?` · ${this._escape(duration)}`:""}</div>`);
      }
    }
    if(detailed && a?.metadata?.suppressed_service_error?.raw_dock_error && !dockError) rows.push(`<div class="muted"><b>${this._tr("panel.dock_error_detail")}:</b> ${this._escape(a.metadata.suppressed_service_error.raw_dock_error)}</div>`);
    return rows.join("");
  }

  _physicalExecutionHtml(job) {
    const attempts=Object.values(job?.execution_attempts || {});
    if(!attempts.length) return `<span class="plain-status">${this._tr("panel.no_physical_execution_attempts")}</span>`;
    return `<div class="physical-attempt-list">${attempts.map((a)=>{
      const stateKey=`panel.execution_substate.${String(a.state||"")}`;
      const translated=this._tr(stateKey);
      const stateLabel=translated===stateKey?String(a.state||"—"):translated;
      const obs=a.metadata?.last_observation||a.metadata?.start_observation||{};
      const targets=(a.targets||[]).map(x=>`<code>${this._escape(x)}</code>`).join(", ")||"—";
      const vendor=obs.vendor||a.metadata?.adapter_vendor||"—";
      const robotStatus=obs.vendor_status||obs.raw_state||"—";
      return `<div class="physical-attempt-card"><div><b>${this._escape(stateLabel)}</b> · ${this._escape(this._attemptRepetitionLabel(a))}</div><div class="muted">${this._tr("panel.targets")}: ${targets}</div>${this._attemptDiagnosticHtml(a,obs)}<div class="muted">${this._tr("panel.adapter")}: ${this._escape(vendor)} · ${this._tr("panel.robot_status")}: ${this._escape(robotStatus)}</div><div class="muted">${this._tr("panel.command_intent")}: ${this._escape(this._formatDateTime(a.command_intent_at))} · ${this._tr("panel.start_confirmed")}: ${this._escape(this._formatDateTime(a.start_confirmed_at))} · ${this._tr("panel.completed")}: ${this._escape(this._formatDateTime(a.completed_at))}</div></div>`;
    }).join("")}</div>`;
  }

  _formatDurationSeconds(value) {
    if(value===null||value===undefined||!Number.isFinite(Number(value))) return "—";
    const raw=Number(value);
    const sign=raw<0?"−":"";
    const total=Math.abs(Math.round(raw));
    const hours=Math.floor(total/3600);
    const minutes=Math.floor((total%3600)/60);
    const seconds=total%60;
    if(hours>0){
      return `${sign}${String(hours).padStart(2,"0")}:${String(minutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}`;
    }
    const totalMinutes=Math.floor(total/60);
    return `${sign}${String(totalMinutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}`;
  }

  _durationBetween(start, end) {
    if (!start || !end) return "—";
    const a = new Date(start).getTime();
    const b = new Date(end).getTime();
    if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return "—";
    return this._formatDurationSeconds((b-a)/1000);
  }

  _lifecycleEventLabel(value) {
    const labels={
      created:this._tr("panel.lifecycle_created"),
      updated:this._tr("panel.lifecycle_updated"),
      finished:this._tr("panel.lifecycle_finished"),
      user_action:this._tr("panel.lifecycle_user_action"),
      reconciled:this._tr("panel.lifecycle_reconciled"),
      recovered:this._tr("panel.lifecycle_recovered"),
      external_execution_detected:this._tr("panel.lifecycle_external_execution"),
    };
    return labels[String(value||"")] || String(value||"—");
  }

  _lifecycleTraceHtml(job) {
    const rows=(job?.lifecycle_events||[]).slice().sort((a,b)=>String(a.scheduler_at||a.at||"").localeCompare(String(b.scheduler_at||b.at||"")));
    if(!rows.length)return `<span class="plain-status">${this._tr("panel.no_lifecycle_trace")}</span>`;
    return `<div class="table-wrap"><table class="source-table lifecycle-table mobile-card-table"><thead><tr><th>${this._tr("panel.time")}</th><th>${this._tr("panel.event")}</th><th>${this._tr("panel.state")}</th><th>${this._tr("panel.details")}</th></tr></thead><tbody>${rows.map((x)=>{
      const blockers=this._blockerCodes([...(x.current_blockers||[]),...(x.blockers||[])]);
      const zoneBits=Object.values(x.zones||{}).map(z=>`${this._stateLabel(z.state)}${z.result?` / ${this._resultLabel(z.result)}`:""}${z.reason_code?` · ${this._reasonLabel(z.reason_code)}`:""}`);
      const detailBits=[];
      if(x.current_preflight_decision)detailBits.push(`${this._tr("panel.pre_flight")}: ${this._preflightDecisionLabel(x.current_preflight_decision)}`);
      if(blockers.length)detailBits.push(`${this._tr("panel.blockers")}: ${this._blockerLabels(blockers).join(" · ")}`);
      if(zoneBits.length)detailBits.push(`${this._tr("panel.zones")}: ${zoneBits.join(" | ")}`);
      if(x.reason_code)detailBits.push(`${this._tr("panel.reason")}: ${this._reasonLabel(x.reason_code)}`);
      if(x.details && Object.keys(x.details).length)detailBits.push(`${this._tr("panel.technical_details")}: ${JSON.stringify(x.details)}`);
      const state=x.result?`${this._stateLabel(x.state)} / ${this._resultLabel(x.result)}`:this._stateLabel(x.state);
      return `<tr><td data-label="${this._tr("panel.time")}">${this._formatDateTime(x.scheduler_at||x.at)}</td><td data-label="${this._tr("panel.event")}">${this._escape(this._lifecycleEventLabel(x.event_type))}</td><td data-label="${this._tr("panel.state")}">${this._escape(state)}</td><td data-label="${this._tr("panel.details")}">${detailBits.length?detailBits.map(v=>`<div class="muted">${this._escape(v)}</div>`).join(""):"—"}${x.recorded_at&&x.recorded_at!==x.scheduler_at?`<div class="muted">${this._tr("panel.recorded_at")}: ${this._formatDateTime(x.recorded_at)}</div>`:""}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  _physicalExecutionDetailedHtml(job) {
    const attempts=Object.values(job?.execution_attempts||{}).sort((a,b)=>String(a.created_at||"").localeCompare(String(b.created_at||"")));
    if(!attempts.length)return `<span class="plain-status">${this._tr("panel.no_physical_execution_attempts")}</span>`;
    const stamp=(label,value)=>value?`<div><b>${this._escape(label)}:</b> ${this._formatDateTime(value)}</div>`:"";
    return `<div class="physical-attempt-list">${attempts.map((a)=>{
      const key=`panel.execution_substate.${String(a.state||"")}`; const translated=this._tr(key); const state=translated===key?String(a.state||"—"):translated;
      const obs=a.metadata?.last_observation||a.metadata?.start_observation||{};
      const params=Object.entries(a.cleaning_params||{}).filter(([k])=>this._cleaningProfileFieldVisible(a.cleaning_params||{},k)).map(([k,v])=>`<span class="ui-chip parameter-chip"><b>${this._escape(k)}:</b> ${this._escape(this._formatValue(v))}</span>`).join("");
      return `<div class="physical-attempt-card history-attempt-card"><div class="attempt-title"><b>${this._escape(state)}</b><span>${this._escape(this._attemptRepetitionLabel(a))}</span></div><div class="muted">${this._tr("panel.target_type")}: ${this._escape(this._targetTypeLabel(a.target_type))} · ${this._tr("panel.targets")}: ${(a.targets||[]).map(x=>`<code>${this._escape(x)}</code>`).join(", ")||"—"}</div><div class="muted">${this._tr("panel.cleaning_zones")}: ${(a.zone_ids||[]).map(x=>`<code>${this._escape(x)}</code>`).join(", ")||"—"}</div><div class="attempt-time-grid">${stamp(this._tr("panel.created"),a.created_at)}${stamp(this._tr("panel.preparing"),a.preparing_at)}${stamp(this._tr("panel.command_intent"),a.command_intent_at)}${stamp(this._tr("panel.requested"),a.requested_at)}${stamp(this._tr("panel.start_confirmed"),a.start_confirmed_at)}${stamp(this._tr("panel.paused"),a.paused_at)}${stamp(this._tr("panel.completion_candidate"),a.completion_candidate_at)}${stamp(this._tr("panel.cancel_requested"),a.cancel_requested_at)}${stamp(this._tr("panel.completed"),a.completed_at)}${stamp(this._tr("panel.last_observed"),a.last_observed_at)}</div>${params?`<div class="attempt-params">${params}</div>`:""}${a.failure_reason?`<div class="readiness readiness-fail">${this._tr("panel.failure")}: ${this._escape(this._reasonLabel(a.failure_reason))}</div>`:""}${this._attemptDiagnosticHtml(a,obs,true)}${a.fault_injection?`<div class="muted">${this._tr("panel.fault_injection")}: ${this._escape(a.fault_injection)}</div>`:""}<div class="muted">${this._tr("panel.adapter")}: ${this._escape(obs.vendor||a.metadata?.adapter_vendor||"—")} · ${this._tr("panel.robot_status")}: ${this._escape(obs.vendor_status||obs.raw_state||"—")}</div></div>`;
    }).join("")}</div>`;
  }

  _historyDurationValue(value, precise=false) {
    void precise;
    return this._formatDurationSeconds(value);
  }

  _historyNumberValue(value,digits=1,suffix="") {
    if(value===null||value===undefined||Number.isNaN(Number(value)))return "—";
    const text=Number(value).toLocaleString(this._language||undefined,{maximumFractionDigits:digits});
    return `${text}${suffix}`;
  }

  _historyComparisonDelta(current,median,kind="relative") {
    if(current===null||current===undefined||median===null||median===undefined||!Number.isFinite(Number(current))||!Number.isFinite(Number(median)))return "—";
    const c=Number(current),m=Number(median),delta=c-m;
    if(kind==="points") return `${delta>0?"+":delta<0?"−":""}${Math.abs(delta).toLocaleString(this._language||undefined,{maximumFractionDigits:1})} ${this._tr("panel.percentage_points_short")}`;
    if(Math.abs(m)<1e-9)return "—";
    const pct=delta/m*100;
    return `${pct>0?"+":pct<0?"−":""}${Math.abs(pct).toLocaleString(this._language||undefined,{maximumFractionDigits:0})}%`;
  }

  _historyComparisonBasisLabel(value) {
    const labels={zones_and_parameters:this._tr("panel.comparison_basis_zones_parameters"),zones:this._tr("panel.comparison_basis_zones"),schedule:this._tr("panel.comparison_basis_schedule")};
    return labels[String(value||"")]||this._tr("panel.comparison_basis_history");
  }

  _historyComparisonHtml(job,context) {
    const record=context?.record||null; const comparison=context?.comparison||{}; const aggregate=comparison.aggregate||{};
    if(!record)return `<div class="history-report-empty">${this._tr("panel.job_statistics_record_unavailable")}</div>`;
    if(!comparison.available){
      const n=Number(comparison.sample_count||0),minimum=Number(comparison.minimum_samples||3);
      return `<div class="history-comparison-empty"><b>${this._tr("panel.historical_comparison")}</b><span>${this._tr("panel.not_enough_comparable_runs",{count:n,minimum})}</span></div>`;
    }
    const area=Number(record.area?.physical_cleaned_m2||0)||null;
    const duration=record.time?.physical_execution_seconds;
    const battery=record.battery?.consumed_percent;
    const secPerM2=area&&duration!==null&&duration!==undefined?Number(duration)/area:null;
    const batteryPerM2=area&&battery!==null&&battery!==undefined?Number(battery)/area:null;
    const rows=[];
    const add=(label,current,dist,formatter,kind="relative")=>{
      if(!dist||Number(dist.count||0)<3||current===null||current===undefined)return;
      const percentileLabel=this._formatPercentileLabel(dist.percentile_number??90);
      rows.push(`<tr><td>${this._escape(label)}</td><td class="metric-number">${formatter(current)}</td><td class="metric-number">${formatter(dist.median)}</td><td class="metric-number">${this._escape(this._historyComparisonDelta(current,dist.median,kind))}</td><td class="metric-number" data-label="${this._escape(percentileLabel)}">${formatter(dist.percentile)}</td></tr>`);
    };
    add(this._tr("panel.physical_execution_time"),duration,aggregate.time?.execution_seconds,(v)=>this._historyDurationValue(v,true));
    add(this._tr("panel.time_per_m2"),secPerM2,aggregate.time?.seconds_per_m2,(v)=>this._formatDurationSeconds(v));
    add(this._tr("panel.battery_used"),battery,aggregate.battery?.consumed_percent,(v)=>`${this._historyNumberValue(v,1)}%`,"points");
    add(this._tr("panel.battery_per_m2"),batteryPerM2,aggregate.battery?.consumed_percent_per_m2,(v)=>`${this._historyNumberValue(v,2)} %/m²`);
    if(!rows.length)return `<div class="history-comparison-empty"><b>${this._tr("panel.historical_comparison")}</b><span>${this._tr("panel.not_enough_metric_samples")}</span></div>`;
    return `<div class="history-comparison"><div class="history-report-section-head"><div><h4>${this._tr("panel.historical_comparison")}</h4><span>${this._escape(this._historyComparisonBasisLabel(comparison.basis))} · ${this._tr("panel.previous_runs_count",{count:comparison.sample_count})}</span></div></div><div class="table-wrap"><table class="mobile-card-table history-comparison-table"><thead><tr><th>${this._tr("panel.metric")}</th><th class="metric-header">${this._tr("panel.this_run")}</th><th class="metric-header">${this._tr("panel.typical_value")}</th><th class="metric-header">${this._tr("panel.deviation")}</th><th class="metric-header">${this._tr("panel.percentile")}</th></tr></thead><tbody>${rows.join("")}</tbody></table></div></div>`;
  }

  _historySpecificResourceReason(job) {
    const accepted=new Set(["clean_water_insufficient","dirty_water_full","detergent_unavailable"]);
    const candidates=[];
    const add=(values)=>{for(const value of (Array.isArray(values)?values:[])){const code=String(value||"");if(accepted.has(code))candidates.push(code);}};
    add(job?.current_blockers); add(job?.blockers);
    for(const zone of Object.values(job?.zone_runs||{})){add(zone?.blockers);if(accepted.has(String(zone?.reason_code||"")))candidates.push(String(zone.reason_code));}
    for(const attempt of Object.values(job?.execution_attempts||{})){
      const meta=attempt?.metadata||{};
      add(meta.resource_blockers);
      for(const key of ["runtime_error_timeout_observation","resource_blocked_observation","last_observation","start_observation"])add(meta?.[key]?.resource_blockers);
      const observations=Array.isArray(meta.statistics_observations)?meta.statistics_observations:[];
      for(let i=observations.length-1;i>=0;i--)add(observations[i]?.resource_blockers);
    }
    const events=Array.isArray(job?.lifecycle_events)?job.lifecycle_events:[];
    for(let i=events.length-1;i>=0;i--){add(events[i]?.current_blockers);add(events[i]?.blockers);}
    return candidates[0]||null;
  }

  _historyDisplayReason(job) {
    const original=String(job?.reason_code||"");
    const specific=this._historySpecificResourceReason(job);
    const generic=new Set(["vacuum_error","execution_failed","robot_error_recovery_timeout","resource_blocked_until_deadline","no_zone_succeeded","preflight_failed"]);
    return specific && (!original || generic.has(original)) ? specific : (original||specific||"");
  }

  _historyReasonLabel(reason) {
    const code=String(reason||"");
    const key={clean_water_insufficient:"panel.clean_water_empty",dirty_water_full:"panel.dirty_water_full",detergent_unavailable:"panel.detergent_unavailable"}[code];
    return key?this._tr(key):this._reasonLabel(code);
  }

  _historyFailureExplanation(reason) {
    const code=String(reason||"");
    const key=`panel.failure_explanation.${code}`;
    const translated=this._tr(key);
    if(translated!==key)return translated;
    if(["vacuum_error","execution_failed","robot_error_recovery_timeout","no_zone_succeeded","preflight_failed"].includes(code))return this._tr("panel.failure_explanation.unknown");
    return "";
  }

  _historySummaryReportHtml(job,context) {
    const record=context?.record||{}; const water=context?.water_usage||{};
    const external=this._historySource(job)==="EXTERNAL";
    const physical=record.time?.physical_execution_seconds ?? (job.actual_start&&job.finished_at?Math.max(0,(new Date(job.finished_at)-new Date(job.actual_start))/1000):null);
    const delay=record.time?.start_delay_seconds ?? (job.actual_start&&job.planned_start?(new Date(job.actual_start)-new Date(job.planned_start))/1000:null);
    const area=record.area?.physical_cleaned_m2;
    const battery=record.battery?.consumed_percent;
    const cleanWater=water.clean_used_ml_eq;
    const waterComplete=water.complete!==false;
    const timeline=(external?[job.actual_start?`${this._tr("panel.started_short")}: ${this._formatDateTime(job.actual_start)}`:"",job.finished_at?`${this._tr("panel.finished_short")}: ${this._formatDateTime(job.finished_at)}`:""]:[`${this._tr("panel.plan_short")}: ${this._formatDateTime(job.planned_start)}`,job.actual_start?`${this._tr("panel.started_short")}: ${this._formatDateTime(job.actual_start)}`:"",job.finished_at?`${this._tr("panel.finished_short")}: ${this._formatDateTime(job.finished_at)}`:""]).filter(Boolean).join(" → ");
    const displayReason=this._historyDisplayReason(job);
    const failureExplanation=["SUCCESS","SUPPRESSED"].includes(String(job?.result||"").toUpperCase())?"":this._historyFailureExplanation(displayReason);
    return `<div class="history-report-pane history-report-summary">
      <div class="history-primary-metrics">
        <div><span>${this._tr("panel.result")}</span><b>${this._historyResultHtml(job.result)}</b><small>${this._escape(this._historyReasonLabel(displayReason))}</small>${failureExplanation?`<small>${this._escape(failureExplanation)}</small>`:""}</div>
        <div><span>${this._tr("panel.physical_execution_time")}</span><b>${this._historyDurationValue(physical,true)}</b><small>${job.execution_mode==="REAL"?this._tr("panel.execution_real"):this._tr("panel.execution_dry_run")}</small></div>
        ${external?`<div><span>${this._tr("panel.source")}</span><b>${this._tr("panel.source_external")}</b><small>${this._tr("panel.external_observed_run")}</small></div>`:`<div><span>${this._tr("panel.start_delay")}</span><b>${this._historyDurationValue(delay,true)}</b><small>${this._tr("panel.relative_to_plan")}</small></div>`}
        <div><span>${this._tr("panel.physical_area")}</span><b>${this._historyNumberValue(area,1," m²")}</b><small>${this._escape(record.area?.source==="robot_observation"?this._tr("panel.measured_by_robot"):this._tr("panel.data_unavailable"))}</small></div>
        <div><span>${this._tr("panel.battery_used")}</span><b>${battery===null||battery===undefined?"—":`−${this._historyNumberValue(battery,1)}%`}</b><small>${record.battery?.start_percent!==null&&record.battery?.start_percent!==undefined&&record.battery?.end_percent!==null&&record.battery?.end_percent!==undefined?`${this._historyNumberValue(record.battery.start_percent,0)}% → ${this._historyNumberValue(record.battery.end_percent,0)}%`:this._tr("panel.data_unavailable")}</small></div>
        <div><span>${this._tr("panel.water")}</span><b>${cleanWater===null||cleanWater===undefined?"—":`≈ ${this._historyNumberValue(cleanWater,0)} ${this._tr("panel.ml_unit")}`}</b><small>${!waterComplete?this._tr("panel.external_water_partial"):cleanWater===null||cleanWater===undefined?this._tr("panel.data_unavailable"):this._tr("panel.synthetic_clean_water_usage")}</small></div>
      </div>
      <div class="history-run-timeline-line">${this._escape(timeline)}</div>
      ${this._historyComparisonHtml(job,context)}
    </div>`;
  }

  _historyZoneReportHtml(job,context) {
    const record=context?.record; const zones=record?.zones||[];
    if(!zones.length)return `<div class="history-report-empty">${this._tr("panel.no_zone_level_data")}</div>`;
    const batches=record.execution_batches||[];
    const passesFor=(zoneId)=>{const values=batches.filter(b=>(b.zone_ids||[]).map(String).includes(String(zoneId))).map(b=>Number(b.requested_passes||b.pass_total||1)||1);return values.length?Math.max(...values):null;};
    const attribution=(value)=>value==="measured"?this._tr("panel.measured"):value==="estimated"?this._tr("panel.estimated"):this._tr("panel.unavailable");
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.zones")}</h4><span>${this._tr("panel.zone_report_help")}</span></div></div><div class="table-wrap"><table class="mobile-card-table history-zone-table"><thead><tr><th>${this._tr("panel.cleaning_zone")}</th><th>${this._tr("panel.result")}</th><th>${this._tr("panel.area")}</th><th>${this._tr("panel.passes")}</th><th>${this._tr("panel.time")}</th><th>${this._tr("panel.battery")}</th><th>${this._tr("panel.data_quality")}</th></tr></thead><tbody>${zones.map(z=>`<tr><td><b>${this._escape(z.zone_name||z.zone_id)}</b><small>${z.nominal_area_m2?`${this._tr("panel.nominal")}: ${this._historyNumberValue(z.nominal_area_m2,1)} m²`:""}</small></td><td>${this._escape(this._resultLabel(z.result||"—"))}${z.reason_code?`<small>${this._escape(this._reasonLabel(z.reason_code))}</small>`:""}</td><td>${this._historyNumberValue(z.attributed_area_m2,1," m²")}</td><td>${passesFor(z.zone_id)??"—"}</td><td>${this._historyDurationValue(z.attributed_cleaning_seconds,true)}</td><td>${z.attributed_battery_percent===null||z.attributed_battery_percent===undefined?"—":`${this._historyNumberValue(z.attributed_battery_percent,1)}%`}</td><td>${this._escape(attribution(z.attribution))}</td></tr>`).join("")}</tbody></table></div></div>`;
  }

  _historyTimeReportHtml(job,context) {
    const record=context?.record||{}; const wait=record.time?.wait||{}; const byReason=this._mergedBlockerDurations(wait.by_reason_seconds||{}).sort((a,b)=>Number(b[1]||0)-Number(a[1]||0));
    const points=[{label:this._tr("panel.plan_short"),at:job.planned_start},{label:"WAIT",at:(wait.intervals||[])[0]?.start},{label:this._tr("panel.started_short"),at:job.actual_start},{label:this._tr("panel.finished_short"),at:job.finished_at}].filter(x=>x.at);
    return `<div class="history-report-pane">
      <div class="history-timeline">${points.map((x,i)=>`<div class="history-timeline-step"><span class="history-timeline-dot"></span><div><b>${this._escape(x.label)}</b><small>${this._formatDateTime(x.at)}</small></div></div>${i<points.length-1?`<span class="history-timeline-connector"></span>`:""}`).join("")}</div>
      <div class="history-secondary-metrics"><div><span>${this._tr("panel.total_duration")}</span><b>${this._historyDurationValue(record.time?.total_duration_seconds,true)}</b></div><div><span>${this._tr("panel.physical_execution_time")}</span><b>${this._historyDurationValue(record.time?.physical_execution_seconds,true)}</b></div><div><span>WAIT</span><b>${this._historyDurationValue(wait.total_seconds,true)}</b><small>${this._tr("panel.intervals_count",{count:wait.count||0})}</small></div><div><span>PAUSE</span><b>${this._historyDurationValue(record.time?.pause_seconds,true)}</b><small>${this._tr("panel.intervals_count",{count:record.time?.pause_count||0})}</small></div></div>
      ${byReason.length?`<div class="history-report-section-head"><div><h4>${this._tr("panel.wait_reasons")}</h4><span>${this._tr("panel.aggregated_wait_help")}</span></div></div><div class="table-wrap"><table class="mobile-card-table"><thead><tr><th>${this._tr("panel.reason")}</th><th>${this._tr("panel.total_time")}</th></tr></thead><tbody>${byReason.map(([reason,seconds])=>`<tr><td>${this._escape(this._blockerLabel(reason))}</td><td>${this._historyDurationValue(seconds,true)}</td></tr>`).join("")}</tbody></table></div>`:`<div class="history-report-empty compact">${this._tr("panel.no_wait_during_run")}</div>`}
    </div>`;
  }

  _historyResourcesReportHtml(job,context) {
    const record=context?.record||{}; const battery=record.battery||{}; const water=context?.water_usage||{}; const area=Number(record.area?.physical_cleaned_m2||0)||null;
    const batteryPer=area&&battery.consumed_percent!==null&&battery.consumed_percent!==undefined?Number(battery.consumed_percent)/area:null;
    const cleanPer=area&&water.clean_used_ml_eq?Number(water.clean_used_ml_eq)/area:null;
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.battery_statistics")}</h4></div></div><div class="history-secondary-metrics"><div><span>${this._tr("panel.battery_before")}</span><b>${this._historyNumberValue(battery.start_percent,0,"%")}</b></div><div><span>${this._tr("panel.battery_after")}</span><b>${this._historyNumberValue(battery.end_percent,0,"%")}</b></div><div><span>${this._tr("panel.battery_used")}</span><b>${battery.consumed_percent===null||battery.consumed_percent===undefined?"—":`${this._historyNumberValue(battery.consumed_percent,1)}%`}</b><small>${batteryPer===null?"":`${this._historyNumberValue(batteryPer,2)} %/m²`}</small></div><div><span>${this._tr("panel.charging_during_job")}</span><b>${battery.charge_gain_percent?`+${this._historyNumberValue(battery.charge_gain_percent,1)}%`:this._tr("panel.none")}</b><small>${battery.charging_seconds?this._historyDurationValue(battery.charging_seconds,true):""}</small></div></div>
      <div class="history-report-section-head"><div><h4>${this._tr("panel.water")}</h4><span>${this._tr("panel.synthetic_water_job_help")}</span></div></div><div class="history-secondary-metrics"><div><span>${this._tr("panel.clean_water_used")}</span><b>${water.clean_used_ml_eq!==null&&water.clean_used_ml_eq!==undefined?`≈ ${this._historyNumberValue(water.clean_used_ml_eq,0)} ${this._tr("panel.ml_unit")}`:"—"}</b><small>${cleanPer===null?"":`≈ ${this._historyNumberValue(cleanPer,1)} ${this._tr("panel.ml_per_m2_unit")}`}</small></div><div><span>${this._tr("panel.dirty_water_gain")}</span><b>${water.dirty_gained_ml_eq!==null&&water.dirty_gained_ml_eq!==undefined?`≈ ${this._historyNumberValue(water.dirty_gained_ml_eq,0)} ${this._tr("panel.ml_unit")}`:"—"}</b></div><div><span>${this._tr("panel.mop_washes")}</span><b>${water.wash_count??record.water_facts?.wash_count??0}</b></div><div><span>${this._tr("panel.physical_area")}</span><b>${this._historyNumberValue(area,1," m²")}</b></div></div>
    </div>`;
  }

  _historyExecutionReportHtml(job,context) {
    const targetLabels=this._jobTargetLabels(job); const targetType=this._targetTypeLabel(job.target_type);
    const targets=targetLabels.length?targetLabels.map(x=>`<span class="ui-chip target-chip">${this._escape(x)}</span>`).join(""):`<span class="plain-status">${this._tr("panel.not_specified")}</span>`;
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.execution")}</h4><span>${this._tr("panel.execution_report_help")}</span></div></div><div class="history-execution-overview"><div><b>${this._tr("panel.cleaning_targets")}</b><div><span class="detail-prefix">${this._escape(targetType)}:</span> ${targets}</div></div><div><b>${this._tr("panel.cleaning_settings")}</b>${this._jobCleaningParamsHtml(job)}</div></div><div class="history-report-section-head"><div><h4>${this._tr("panel.physical_execution")}</h4></div></div>${this._physicalExecutionHtml(job)}</div>`;
  }

  _historyJournalReportHtml(job) {
    const rows=(job?.lifecycle_events||[]).slice().sort((a,b)=>String(a.scheduler_at||a.at||"").localeCompare(String(b.scheduler_at||b.at||"")));
    const events=[]; let previousState=null, previousBlockers="";
    for(const row of rows){
      const state=String(row.state||""); const blockers=this._blockerCodes([...(row.current_blockers||[]),...(row.blockers||[])]); const blockerKey=blockers.join("|");
      let text="";
      if(!events.length && String(row.event_type||"")==="created") text=this._tr("panel.journal_job_created");
      if(state==="WAIT" && previousState!=="WAIT") text=this._tr("panel.journal_start_delayed",{reason:blockers.length?this._blockerLabels(blockers).join(" · "):this._tr("panel.unknown")});
      else if(state==="WAIT" && previousState==="WAIT" && blockerKey!==previousBlockers) text=this._tr("panel.journal_wait_changed",{reason:blockers.length?this._blockerLabels(blockers).join(" · "):this._tr("panel.unknown")});
      else if(state==="STARTING" && previousState==="WAIT") text=this._tr("panel.journal_conditions_met");
      else if(state==="RUNNING" && previousState!=="RUNNING") text=this._tr("panel.journal_cleaning_started");
      if(row.result && String(row.event_type||"")==="finished") text=this._tr("panel.journal_cleaning_finished",{result:this._resultLabel(row.result),reason:this._reasonLabel(row.reason_code)});
      if(text && (!events.length||events[events.length-1].text!==text)) events.push({at:row.scheduler_at||row.at,text});
      previousState=state||previousState; previousBlockers=blockerKey;
    }
    if(!events.some(e=>String(e.text).includes(this._resultLabel(job.result))) && job.finished_at)events.push({at:job.finished_at,text:this._tr("panel.journal_cleaning_finished",{result:this._resultLabel(job.result),reason:this._reasonLabel(job.reason_code)})});
    if(!events.length)return `<div class="history-report-empty">${this._tr("panel.no_meaningful_events")}</div>`;
    return `<div class="history-report-pane"><div class="history-readable-log">${events.map(e=>`<div class="history-readable-event"><time>${this._formatDateTime(e.at)}</time><span class="history-readable-dot"></span><div>${this._escape(e.text)}</div></div>`).join("")}</div></div>`;
  }

  _historyTechnicalReportHtml(job) {
    return `<div class="history-report-pane history-technical-pane">
      <details><summary>${this._tr("panel.final_pre_flight")}</summary>${this._preflightSourcesHtml(job,false)}</details>
      ${job.advisory_checked_at?`<details><summary>${this._tr("panel.advisory_pre_flight_sources")}</summary>${this._preflightSourcesHtml(job,true)}</details>`:""}
      <details><summary>${this._tr("panel.execution_trace")}</summary>${this._lifecycleTraceHtml(job)}</details>
      <details><summary>${this._tr("panel.user_actions")}</summary>${this._jobAuditHtml(job)}</details>
      <details><summary>${this._tr("panel.notifications")}</summary>${this._jobNotificationHistoryHtml(job)}</details>
      <details><summary>${this._tr("panel.raw_job_data")}</summary><pre>${this._escape(JSON.stringify(job,null,2))}</pre></details>
    </div>`;
  }

  _historyJobDetailsHtml(job) {
    if(!job)return `<div class="job-details history-job-details"><div class="wide-detail"><span class="plain-status">${this._tr("panel.loading_job_trace")}</span></div></div>`;
    const jobId=String(job.job_id||""); const active=this._historyJobTab.get(jobId)||"summary"; const context=job.statistics_context||{};
    const tabs=[
      ["summary","view-dashboard-outline",this._tr("panel.history_tab_summary")],
      ["zones","floor-plan",this._tr("panel.history_tab_zones")],
      ["time","timeline-clock-outline",this._tr("panel.history_tab_time")],
      ["resources","battery-50",this._tr("panel.history_tab_resources")],
      ["execution","robot-vacuum",this._tr("panel.history_tab_execution")],
      ["journal","format-list-bulleted",this._tr("panel.history_tab_journal")],
      ["technical","code-json",this._tr("panel.history_tab_technical")],
    ];
    const nav=`<div class="history-report-tabs" role="tablist">${tabs.map(([key,icon,label])=>`<button type="button" class="history-report-tab ${active===key?"selected":""}" data-history-tab="${key}" data-history-tab-job="${this._escape(jobId)}" role="tab" aria-selected="${active===key?"true":"false"}">${this._mdi(icon)}<span>${this._escape(label)}</span></button>`).join("")}</div>`;
    const bodies={summary:()=>this._historySummaryReportHtml(job,context),zones:()=>this._historyZoneReportHtml(job,context),time:()=>this._historyTimeReportHtml(job,context),resources:()=>this._historyResourcesReportHtml(job,context),execution:()=>this._historyExecutionReportHtml(job,context),journal:()=>this._historyJournalReportHtml(job),technical:()=>this._historyTechnicalReportHtml(job)};
    return `<div class="history-job-report">${nav}<div class="history-report-body">${(bodies[active]||bodies.summary)()}</div></div>`;
  }

  async _loadHistoryJobDetails(jobId) {
    const id=String(jobId||"");
    if(!id||this._historyJobDetails.has(id)||this._historyJobLoading.has(id))return;
    this._historyJobLoading.add(id);
    try{
      const details=await this._callWs({type:"vacuum_schedule/scheduler/job_details",entry_id:this._schedulerEntryId,job_id:id});
      this._historyJobDetails.set(id,details);
    }catch(err){this._notify(this._errorText(err),"error",4200);}
    finally{this._historyJobLoading.delete(id);if(this._view==="status"&&!this._editingAny)this._render();}
  }

  _activeBlockerRequiresAction(code) {
    return new Set([
      "clean_water_insufficient", "dirty_water_full", "detergent_unavailable",
      "mop_not_attached", "invalid_target", "invalid_configuration", "vacuum_error",
    ]).has(String(code || ""));
  }

  _activeStatusDescriptor(job) {
    const blockers=this._blockerCodes(job.current_blockers||[]);
    const actionBlockers=blockers.filter((code)=>this._activeBlockerRequiresAction(code));
    const attempt=this._activeExecutionAttempt(job);
    if(actionBlockers.length) {
      const critical=actionBlockers.some((code)=>["invalid_configuration","vacuum_error"].includes(String(code)));
      return {
      tone:critical?"fail":"warning",
      title:this._tr("panel.active_action_required"),
      text:this._tr("panel.active_action_required_detail",{reason:actionBlockers.map((x)=>this._blockerLabel(x)).join(" · ")}),
      };
    }
    if(String(job.state||"")==="RUNNING" && String(attempt?.state||"")==="ROBOT_ERROR_BLOCKED") {
      const obs=attempt?.metadata?.last_observation||{};
      const physical=attempt?.metadata?.physical_error||{};
      const reason=obs.vacuum_error||obs.dock_error||physical.vacuum_error||physical.dock_error||physical.raw_vacuum_error||physical.raw_dock_error||this._tr("panel.unknown");
      return {
        tone:"warning", title:this._tr("panel.active_robot_error_blocked"),
        text:this._tr("panel.active_robot_error_blocked_detail",{reason,deadline:this._formatDateTime(attempt?.error_recovery_deadline_at)}),
      };
    }
    if(String(job.state||"")==="RUNNING" && String(attempt?.state||"")==="PAUSED") return {
      tone:"warning", title:this._tr("panel.active_paused"), text:this._tr("panel.active_paused_detail"),
    };
    if(String(job.state||"")==="RUNNING") {
      const metrics=this._liveMetrics(job);
      return {tone:"success",title:this._liveActivityLabel(job),text:metrics.join(" · ")||this._tr("panel.active_running_detail",{duration:"—"})};
    }
    if(String(job.state||"")==="STARTING") return {
      tone:"info",title:this._tr("panel.active_starting"),text:this._executionSubstatus(job)||this._tr("panel.active_starting_detail"),
    };
    if(String(job.state||"")==="WAIT") return {
      tone:"warning",title:this._tr("panel.active_waiting"),
      text:blockers.length
        ? this._tr("panel.active_waiting_detail",{reason:this._blockerLabels(blockers).join(" · ")})
        : this._tr("panel.active_waiting_recheck"),
    };
    if(String(job.state||"")==="PLANNED" && !job.advisory_checked_at) return {
      tone:"neutral",title:this._tr("panel.active_waiting_for_preflight"),
      text:this._tr("panel.active_preflight_scheduled_detail",{time:this._formatDateTime(job.warning_at)}),
    };
    if(String(job.state||"")==="PLANNED") return {
      tone:"success",title:this._tr("panel.active_ready_for_schedule"),
      text:this._tr("panel.active_ready_for_schedule_detail",{time:this._formatDateTime(job.planned_start)}),
    };
    return {tone:"neutral",title:this._stateLabel(job.state),text:this._tr("panel.active_no_action_needed")};
  }

  _activeNextActionLabel(job) {
    const blockers=(job.current_blockers||[]).filter(Boolean);
    if(blockers.some((code)=>this._activeBlockerRequiresAction(code))) return this._tr("panel.active_next_user_action");
    const attempt=this._activeExecutionAttempt(job);
    if(String(job.state||"")==="RUNNING" && String(attempt?.state||"")==="ROBOT_ERROR_BLOCKED") return this._tr("panel.active_next_fix_robot_error");
    if(String(job.state||"")==="RUNNING" && String(attempt?.state||"")==="PAUSED") return this._tr("panel.active_next_resume");
    if(String(job.state||"")==="RUNNING") return this._tr("panel.active_next_finish");
    if(String(job.state||"")==="STARTING") return this._tr("panel.active_next_confirm_start");
    if(String(job.state||"")==="WAIT") return this._tr("panel.active_next_auto_recheck");
    if(!job.advisory_checked_at) return this._tr("panel.active_next_preflight",{time:this._formatDateTime(job.warning_at)});
    return this._tr("panel.active_next_planned_start",{time:this._formatDateTime(job.planned_start)});
  }

  _activeZoneTargetLabel(run) {
    const type=String(run?.robot_target_type||"");
    const raw=String(run?.robot_target_id||"").trim();
    if(type==="segment") {
      const match=raw.match(/(?:^|_)(\d+)$/);
      return `${this._tr("panel.segment_0b904ec")} ${match?match[1]:(raw||"—")}`;
    }
    if(type==="zone") return this._tr("panel.robot_zone");
    return raw || "—";
  }

  _activeZoneReadiness(run) {
    const blockers=this._blockerCodes(run?.blockers||[]);
    const labels=[];
    if(blockers.includes("zone_busy")) labels.push(this._tr("panel.active_zone_busy"));
    if(blockers.includes("zone_access_blocked")) labels.push(this._tr("panel.active_zone_inaccessible"));
    if(blockers.includes("zone_state_unknown")) labels.push(this._tr("panel.active_zone_unknown"));
    for(const code of blockers){if(!["zone_busy","zone_access_blocked","zone_state_unknown"].includes(code))labels.push(this._blockerLabel(code));}
    if(labels.length) return {tone:"warning",text:labels.join(" · ")};
    if(String(run?.state||"")==="FINISHED") return {tone:"neutral",text:"—"};
    return {tone:"success",text:this._tr("panel.active_zone_ready")};
  }

  _activeZoneExecutionLabel(run) {
    const state=String(run?.state||"");
    if(state==="RUNNING") return this._tr("panel.active_zone_running");
    if(state==="STARTING") return this._tr("panel.active_zone_starting");
    if(state==="FINISHED") return this._tr("panel.active_zone_finished");
    if(state==="WAIT") return this._tr("panel.active_zone_waiting");
    return this._tr("panel.active_zone_not_started");
  }

  _activeNowReportHtml(job) {
    const status=this._activeStatusDescriptor(job);
    const zones=Object.values(job.zone_runs||{});
    const started=job.actual_start||job.starting_at||null;
    const currentDuration=started?this._durationBetween(started,this._displayNow().toISOString()):null;
    return `<div class="history-report-pane active-now-pane">
      <div class="history-primary-metrics active-primary-metrics">
        <div><span>${this._tr("panel.state")}</span><b>${this._escape(this._stateLabel(job.state))}</b><small>${this._escape(this._executionModeLabel(job.execution_mode))}</small></div>
        <div><span>${this._tr("panel.active_next_action")}</span><b>${this._escape(this._activeNextActionLabel(job))}</b></div>
        <div><span>${this._tr("panel.scheduled_start")}</span><b>${this._formatDateTime(job.planned_start)}</b></div>
        <div><span>${this._tr("panel.latest_allowed_start")}</span><b>${this._formatDateTime(job.deadline_at)}</b></div>
        <div><span>${this._tr("panel.zones")}</span><b>${zones.length||this._jobTargetLabels(job).length||0}</b>${currentDuration?`<small>${this._tr("panel.current_duration")}: ${this._escape(currentDuration)}</small>`:""}</div>
      </div>
      <div class="active-now-message active-now-${this._escape(status.tone)}"><div>${this._mdi(status.tone==="success"?"check-circle-outline":status.tone==="fail"?"alert-octagon-outline":status.tone==="warning"?"alert-circle-outline":status.tone==="info"?"progress-clock":"information-outline")}</div><div><b>${this._escape(status.title)}</b><span>${this._escape(status.text)}</span></div></div>
      <div class="history-run-timeline-line">${this._tr("panel.active_plan_line",{planned:this._formatDateTime(job.planned_start),deadline:this._formatDateTime(job.deadline_at),next:this._formatDateTime(job.next_planned_start)})}</div>
    </div>`;
  }

  _activeZonesReportHtml(job) {
    const runs=Object.values(job.zone_runs||{});
    if(!runs.length) return `<div class="history-report-empty">${this._tr("panel.no_zone_level_data")}</div>`;
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.history_tab_zones")}</h4><span>${this._tr("panel.active_zones_help")}</span></div></div><div class="table-wrap"><table class="mobile-card-table active-zone-table"><thead><tr><th>${this._tr("panel.cleaning_zone")}</th><th>${this._tr("panel.robot_target")}</th><th>${this._tr("panel.active_zone_readiness")}</th><th>${this._tr("panel.execution")}</th><th>${this._tr("panel.result")}</th></tr></thead><tbody>${runs.map((run)=>{
      const readiness=this._activeZoneReadiness(run);
      const result=run.result?this._resultLabel(run.result):"—";
      const reason=run.reason_code?`<small>${this._escape(this._reasonLabel(run.reason_code))}</small>`:"";
      return `<tr><td data-label="${this._tr("panel.cleaning_zone")}"><b>${this._escape(run.zone_name||run.zone_id||"—")}</b></td><td data-label="${this._tr("panel.robot_target")}">${this._escape(this._activeZoneTargetLabel(run))}</td><td data-label="${this._tr("panel.active_zone_readiness")}"><span class="readiness readiness-${readiness.tone==='success'?'pass':readiness.tone==='warning'?'wait':'unknown'}">${this._escape(readiness.text)}</span></td><td data-label="${this._tr("panel.execution")}">${this._escape(this._activeZoneExecutionLabel(run))}</td><td data-label="${this._tr("panel.result")}">${this._escape(result)}${reason}</td></tr>`;
    }).join("")}</tbody></table></div></div>`;
  }

  _activePlanReportHtml(job) {
    const points=[
      {label:this._tr("panel.created"),at:job.created_at},
      {label:this._tr("panel.pre_start_warning"),at:job.warning_at},
      {label:this._tr("panel.plan_short"),at:job.planned_start},
      {label:this._tr("panel.latest_allowed_start"),at:job.deadline_at},
    ].filter((x)=>x.at);
    const force=job.force;let forceState="";if(force?.execution_committed){const advance=force.actual_advance_seconds!==undefined?this._historyDurationValue(force.actual_advance_seconds,true):"";forceState=`${this._tr("panel.force_executed")}${advance?` · ${this._tr("panel.force_executed_advance",{duration:advance})}`:""}`;}else if(force?.deferred_by_arbiter_class==="manual")forceState=this._tr("panel.force_deferred_manual");else if(force?.deferred_by_arbiter_class==="scheduled")forceState=this._tr("panel.force_deferred_scheduled");else if(force?.plan_protection&&force.plan_protection.allowed===false)forceState=this._forceProtectionText(force);else if(force)forceState=force.selected&&force.eligible?this._tr("panel.force_selected_candidate"):force.conditions_matched?this._tr("panel.force_conditions_met"):this._tr("panel.force_conditions_not_met");const forceBlock=force?`<div class="active-force-summary ${force.execution_committed||force.selected&&force.eligible?"success":force.conditions_matched?"warning":"neutral"}"><b>${this._tr("panel.early_start")}</b><span>${this._tr("panel.force_plan_summary",{window:this._formatDateTime(force.window_start),priority:force.priority??0,state:forceState})}</span></div>`:"";return `<div class="history-report-pane">${forceBlock}<div class="history-timeline active-plan-timeline">${points.map((x,i)=>`<div class="history-timeline-step"><span class="history-timeline-dot"></span><div><b>${this._escape(x.label)}</b><small>${this._formatDateTime(x.at)}</small></div></div>${i<points.length-1?`<span class="history-timeline-connector"></span>`:""}`).join("")}</div><div class="history-secondary-metrics"><div><span>${this._tr("panel.pre_start_warning")}</span><b>${this._formatDateTime(job.warning_at)}</b></div><div><span>${this._tr("panel.scheduled_start")}</span><b>${this._formatDateTime(job.planned_start)}</b></div><div><span>${this._tr("panel.latest_allowed_start")}</span><b>${this._formatDateTime(job.deadline_at)}</b></div><div><span>${this._tr("panel.next_scheduled_start")}</span><b>${this._formatDateTime(job.next_planned_start)}</b></div></div></div>`;
  }

  _activeReadinessReportHtml(job) {
    const currentBlockerCodes=(job.current_blockers||[]).filter(Boolean).map(String);
    if(!job.advisory_checked_at && !job.metadata?.current_preflight_report && !currentBlockerCodes.length) return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.current_readiness_to_start")}</h4></div></div><div class="active-readiness-summary neutral"><b>${this._tr("panel.active_readiness_not_checked")}</b><span>${this._tr("panel.active_preflight_scheduled_detail",{time:this._formatDateTime(job.warning_at)})}</span></div></div>`;
    const report=job.metadata?.current_preflight_report||job.metadata?.advisory_preflight_report||{};
    const snapshot=report.input_snapshot||{};
    const values=snapshot.values||{};
    const zoneSnapshots=snapshot.zones||{};
    const blockers=[...(report.blockers||[])];
    for(const code of currentBlockerCodes){
      if(!blockers.some((item)=>String(item?.code||"")===code)) blockers.push({code,decision_class:code==="execution_lease_busy"?"WAIT":job.current_preflight_decision||"WAIT"});
    }
    const blockerFor=(codes,roomId=null)=>blockers.find((b)=>codes.includes(String(b.code||"")) && (!roomId || String(b.room_id||"")===String(roomId)))||null;
    const rows=[];
    const push=(condition,tone,state,detail="")=>rows.push({condition,tone,state,detail});
    const available=values["vacuum.available"];
    const activity=values["vacuum.activity"];
    const unavailableBlocker=blockerFor(["vacuum_unavailable"]);
    const errorBlocker=blockerFor(["vacuum_error"]);
    const physicalBusyBlocker=blockerFor(["vacuum_busy"]);
    const leaseBlocker=blockerFor(["execution_lease_busy"]);
    const robotBlocker=errorBlocker||unavailableBlocker||leaseBlocker||physicalBusyBlocker;
    if(available||activity||robotBlocker){
      const raw=String(activity?.effective??activity?.live??"");
      let detail=raw;
      if(robotBlocker===leaseBlocker)detail=this._schedulerBusyDetail(job);
      else if(robotBlocker===physicalBusyBlocker)detail=this._tr("panel.robot_busy_external");
      else if(!detail&&available)detail=available.effective===true?this._tr("panel.active_robot_available"):"";
      push(
        this._tr("panel.robot_vacuum"),
        robotBlocker?(robotBlocker.decision_class==="FAIL"?"fail":"warning"):"success",
        robotBlocker?this._blockerLabel(robotBlocker.code):this._tr("panel.active_condition_ready"),
        detail,
      );
    }
    const battery=values["vacuum.battery_percent"];
    if(battery){const bad=blockerFor(["battery_insufficient","battery_unavailable"]);const pct=Number(battery.effective);const minimum=bad?.details?.minimum_percent??job.cleaning_params?.minimum_battery_percent;const detail=[Number.isFinite(pct)?`${pct}%`:this._tr("panel.data_unavailable"),minimum!==null&&minimum!==undefined&&String(minimum)!==""?`${this._tr("panel.min_battery")}: ${minimum}%`:""].filter(Boolean).join(" · ");push(this._tr("panel.battery"),bad?(bad.decision_class==="FAIL"?"fail":"warning"):"success",bad?this._blockerLabel(bad.code):this._tr("panel.active_condition_ready"),detail);}
    const dnd=values["dnd.active"];
    if(dnd && (dnd.source_entity_id || blockerFor(["dnd_active","dnd_window","dnd_unavailable","invalid_configuration"]))){const bad=blockerFor(["dnd_active","dnd_window","dnd_unavailable","invalid_configuration"]);push("DND",bad?(bad.decision_class==="FAIL"?"fail":"warning"):"success",bad?this._blockerLabel(bad.code):this._tr("panel.active_condition_ready"),dnd.effective===true?this._tr("panel.enabled"):this._tr("panel.disabled_no_time_restriction"));}
    for(const [key,label,codes] of [
      ["dock.clean_water",this._tr("panel.clean_water"),["clean_water_insufficient","dock_clean_water_unavailable"]],
      ["dock.dirty_water",this._tr("panel.dirty_water"),["dirty_water_full","dock_dirty_water_unavailable"]],
      ["dock.detergent",this._tr("common.binding.dock.detergent"),["detergent_unavailable","dock_detergent_unavailable"]],
      ["mop.attached",this._tr("common.binding.mop.attached"),["mop_not_attached","mop_attached_unavailable","mop_unavailable"]],
    ]) {const item=values[key];const bad=blockerFor(codes);if(!item || (!item.source_entity_id && !bad))continue;push(label,bad?(bad.decision_class==="FAIL"?"fail":"warning"):"success",bad?this._blockerLabel(bad.code):this._tr("panel.active_condition_ready"),item.effective_available===false?this._tr("panel.unavailable"):this._formatSettingValue(key,item.effective));}
    for(const [zoneId,z] of Object.entries(zoneSnapshots)){
      const zoneName=z.name||job.zone_runs?.[zoneId]?.zone_name||zoneId;
      const busyBad=blockerFor(["zone_busy","zone_state_unknown"],zoneId);
      const busyConfigured=(z.source_details||[]).length>0;
      push(`${zoneName} · ${this._tr("panel.occupancy")}`,busyBad?"warning":busyConfigured?"success":"neutral",busyBad?this._blockerLabel(busyBad.code):(busyConfigured?this._tr("panel.active_condition_ready"):this._tr("panel.active_not_checked")),busyConfigured?(z.busy?.stabilizing?this._tr("panel.active_stabilizing_until",{time:this._formatDateTime(z.busy?.stabilizing_until)}):""):this._tr("panel.active_check_disabled"));
      const accessBad=blockerFor(["zone_access_blocked"],zoneId);
      const accessConfigured=(z.path_details||[]).length>0;
      push(`${zoneName} · ${this._tr("panel.accessibility")}`,accessBad?"warning":accessConfigured?"success":"neutral",accessBad?this._blockerLabel(accessBad.code):(accessConfigured?this._tr("panel.active_condition_ready"):this._tr("panel.active_not_checked")),accessConfigured?(z.accessible?.stabilizing?this._tr("panel.active_stabilizing_until",{time:this._formatDateTime(z.accessible?.stabilizing_until)}):""):this._tr("panel.active_check_disabled"));
    }
    const decision=String(job.current_preflight_decision||report.decision||(blockers.length?"WAIT":"PASS"));
    const presentationBlockers=this._blockerCodes(blockers.map((b)=>b.code));
    const actionCount=presentationBlockers.filter((code)=>this._activeBlockerRequiresAction(code)).length;
    const summaryTitle=decision==="PASS"?this._tr("panel.active_readiness_ready"):actionCount?this._tr("panel.active_action_required"):this._tr("panel.active_readiness_waiting");
    const summaryText=decision==="PASS"?this._tr("panel.active_readiness_ready_detail"):this._tr("panel.active_readiness_issue_count",{count:presentationBlockers.length});
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.current_readiness_to_start")}</h4></div></div><div class="active-readiness-summary ${decision==="PASS"?"success":decision==="FAIL"?"fail":"warning"}"><b>${this._escape(summaryTitle)}</b><span>${this._escape(summaryText)}</span></div>${rows.length?`<div class="table-wrap"><table class="mobile-card-table active-readiness-table"><thead><tr><th>${this._tr("panel.check")}</th><th>${this._tr("panel.state")}</th><th>${this._tr("panel.details")}</th></tr></thead><tbody>${rows.map((r)=>`<tr><td data-label="${this._tr("panel.check")}"><b>${this._escape(r.condition)}</b></td><td data-label="${this._tr("panel.state")}"><span class="readiness readiness-${r.tone==='success'?'pass':r.tone==='fail'?'fail':r.tone==='warning'?'wait':'unknown'}">${this._escape(r.state)}</span></td><td data-label="${this._tr("panel.details")}" class="muted">${this._escape(r.detail||"—")}</td></tr>`).join("")}</tbody></table></div>`:""}</div>`;
  }

  _activeParametersReportHtml(job) {
    const p=job.cleaning_params||{};
    const rows=[];
    const add=(label,value)=>{if(value===null||value===undefined||String(value).trim()==="")return;rows.push([label,value]);};
    const addPreset=(key,label)=>{const value=p[key];if(value===null||value===undefined||String(value).trim()===""||!this._cleaningProfileFieldVisible(p,key))return;add(label,this._presetLabel(key,value));};
    addPreset("cleaning_mode",this._tr("panel.cleaning_type"));
    addPreset("fan_mode",this._tr("panel.suction_power"));
    addPreset("cleaning_route",this._tr("panel.cleaning_route"));
    addPreset("mop_mode",this._tr("panel.mopping_mode"));
    addPreset("water_mode",this._tr("panel.water_flow_intensity"));
    add(this._tr("panel.cleaning_passes"),p.passes);
    if(p.minimum_battery_percent!==null&&p.minimum_battery_percent!==undefined)add(this._tr("panel.min_battery"),`${p.minimum_battery_percent}%`);
    if(p.minimum_start_window_minutes!==null&&p.minimum_start_window_minutes!==undefined)add(this._tr("panel.min_start_window"),`${this._formatDurationSeconds(Number(p.minimum_start_window_minutes)*60)}`);
    if(!rows.length)return `<div class="history-report-empty">${this._tr("panel.vacuum_settings_are_not_overridden")}</div>`;
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.active_tab_parameters")}</h4><span>${this._tr("panel.active_parameters_help")}</span></div></div><div class="table-wrap"><table class="mobile-card-table active-parameters-table"><thead><tr><th>${this._tr("panel.parameter")}</th><th>${this._tr("panel.value")}</th></tr></thead><tbody>${rows.map(([label,value])=>`<tr><td><b>${this._escape(label)}</b></td><td>${this._escape(value)}</td></tr>`).join("")}</tbody></table></div></div>`;
  }

  _activeExecutionReportHtml(job) {
    const attempts=Object.values(job.execution_attempts||{}).sort((a,b)=>String(a.created_at||"").localeCompare(String(b.created_at||"")));
    if(!attempts.length)return `<div class="history-report-empty">${this._tr("panel.active_execution_not_started")}</div>`;
    const zoneName=(id)=>job.zone_runs?.[id]?.zone_name||id;
    return `<div class="history-report-pane"><div class="history-report-section-head"><div><h4>${this._tr("panel.history_tab_execution")}</h4><span>${this._tr("panel.active_execution_help")}</span></div></div><div class="physical-attempt-list">${attempts.map((attempt,index)=>{
      const stateKey=`panel.execution_substate.${String(attempt.state||"")}`;const translated=this._tr(stateKey);const stateLabel=translated===stateKey?String(attempt.state||"—"):translated;
      const started=attempt.start_confirmed_at||attempt.command_intent_at||attempt.created_at;
      const ended=attempt.completed_at||this._displayNow().toISOString();
      const zones=(attempt.zone_ids||[]).map(zoneName).filter(Boolean);
      const obs=attempt.metadata?.last_observation||attempt.metadata?.start_observation||{};
      return `<div class="physical-attempt-card active-attempt-card"><div class="attempt-title"><b>${this._tr("panel.execution_attempt_number",{number:index+1})} · ${this._escape(stateLabel)}</b><span>${this._escape(this._attemptRepetitionLabel(attempt))}</span></div><div class="active-attempt-summary"><span><b>${this._tr("panel.cleaning_zones")}:</b> ${this._escape(zones.join(", ")||"—")}</span><span><b>${this._tr("panel.started_short")}:</b> ${this._formatDateTime(started)}</span><span><b>${this._tr("panel.current_duration")}:</b> ${this._escape(this._durationBetween(started,ended))}</span></div>${this._attemptDiagnosticHtml(attempt,obs)}${obs.vendor_status||obs.raw_state?`<div class="muted">${this._tr("panel.robot_status")}: ${this._escape(obs.vendor_status||obs.raw_state)}</div>`:""}</div>`;
    }).join("")}</div></div>`;
  }

  _activeTechnicalReportHtml(job) {
    return `<div class="history-report-pane history-technical-pane">
      <details><summary>${this._tr("panel.current_pre_flight_sources")}</summary>${this._preflightSourcesHtml(job,false)}</details>
      ${job.advisory_checked_at?`<details><summary>${this._tr("panel.advisory_pre_flight_sources")}</summary>${this._preflightSourcesHtml(job,true)}</details>`:""}
      <details><summary>${this._tr("panel.execution_trace")}</summary>${this._lifecycleTraceHtml(job)}</details>
      <details><summary>${this._tr("panel.user_actions")}</summary>${this._jobAuditHtml(job)}</details>
      <details><summary>${this._tr("panel.notifications")}</summary>${this._jobNotificationHistoryHtml(job)}</details>
      <details><summary>${this._tr("panel.raw_job_data")}</summary><pre>${this._escape(JSON.stringify({...job,execution_mode:job.execution_mode,execution_attempts:job.execution_attempts},null,2))}</pre></details>
    </div>`;
  }

  async _loadActiveJobDetails(jobId, force=false, render=true) {
    const id=String(jobId||"");
    if(!id || (!force && this._activeJobDetails.has(id)) || this._activeJobLoading.has(id)) return;
    this._activeJobLoading.add(id);
    try {
      const details=await this._callWs({type:"vacuum_schedule/scheduler/job_details",entry_id:this._schedulerEntryId,job_id:id});
      this._activeJobDetails.set(id,details);
    } catch(err) {
      if(render)this._notify(this._errorText(err),"error",4200);
    } finally {
      this._activeJobLoading.delete(id);
      if(render && this._view==="status" && !this._editingAny)this._render();
    }
  }

  _jobDetailsHtml(job) {
    const jobId=String(job.job_id||"");
    const active=this._activeJobTab.get(jobId)||"now";
    const attempts=Object.values(job.execution_attempts||{});
    const tabs=[
      ["now","view-dashboard-outline",this._tr("panel.active_tab_now")],
      ["zones","floor-plan",this._tr("panel.history_tab_zones")],
      ["plan","timeline-clock-outline",this._tr("panel.active_tab_plan")],
      ["readiness","check-decagram-outline",this._tr("panel.active_tab_readiness")],
      ["parameters","tune-variant",this._tr("panel.active_tab_parameters")],
      ...(attempts.length?[["execution","robot-vacuum",this._tr("panel.history_tab_execution")]]:[]),
      ["journal","format-list-bulleted",this._tr("panel.history_tab_journal")],
      ["technical","code-json",this._tr("panel.history_tab_technical")],
    ];
    const selected=tabs.some(([key])=>key===active)?active:"now";
    const nav=`<div class="history-report-tabs active-job-tabs" role="tablist">${tabs.map(([key,icon,label])=>`<button type="button" class="history-report-tab ${selected===key?"selected":""}" data-active-job-tab="${key}" data-active-job-tab-job="${this._escape(jobId)}" role="tab" aria-selected="${selected===key?"true":"false"}">${this._mdi(icon)}<span>${this._escape(label)}</span></button>`).join("")}</div>`;
    const bodies={
      now:()=>this._activeNowReportHtml(job),
      zones:()=>this._activeZonesReportHtml(job),
      plan:()=>this._activePlanReportHtml(job),
      readiness:()=>this._activeReadinessReportHtml(job),
      parameters:()=>this._activeParametersReportHtml(job),
      execution:()=>this._activeExecutionReportHtml(job),
      journal:()=>this._historyJournalReportHtml(job),
      technical:()=>this._activeTechnicalReportHtml(job),
    };
    return `<div class="history-job-report active-job-report">${nav}<div class="history-report-body">${(bodies[selected]||bodies.now)()}</div></div>`;
  }

  _jobRowActionsHtml(job) {
    const button = (action, icon, label, style = "ghost") => `<button type="button" class="${style} icon-only compact-icon-action job-action job-row-action" data-job="${this._escape(job.job_id)}" data-schedule="${this._escape(job.schedule_id||"")}" data-action="${this._escape(action)}" title="${this._escape(label)}" aria-label="${this._escape(label)}">${this._mdi(icon)}</button>`;
    const future=job?.planned_start && new Date(job.planned_start).getTime()>this._displayNow().getTime();
    const scheduleAction=String(job?.origin||"")==="SCHEDULED"
      ? (job?.schedule_paused
        ? button("schedule_resume","play-circle-outline",this._tr("panel.resume_schedule"),"primary")
        : button("schedule_pause","pause-circle-outline",this._tr("panel.pause_schedule")))
      : "";
    const runnable=!job?.schedule_paused;
    const additional=runnable?button("additional_run", "play-box-multiple-outline", this._tr("panel.additional_run"), "primary"):"";
    const early=runnable&&future?button("start_now", "clock-fast", this._tr("panel.execute_early")):"";
    if (job.state === "PLANNED") return `${scheduleAction}${additional}${early}${button("skip", "skip-next-outline", this._tr("panel.skip"), "danger")}`;
    if (job.state === "WAIT") return `${scheduleAction}${runnable?button("recheck", "refresh", this._tr("panel.recheck"), "primary"):""}${additional}${early}${button("skip", "skip-next-outline", this._tr("panel.skip"), "danger")}`;
    if (job.state === "STARTING" || job.state === "RUNNING") return scheduleAction;
    return "";
  }

  _formatForecastDuration(value) {
    const seconds=Number(value);
    if(!Number.isFinite(seconds)||seconds<0)return "—";
    const minutes=Math.max(0,Math.round(seconds/60));
    const hours=Math.floor(minutes/60);
    return `${String(hours).padStart(2,"0")}:${String(minutes%60).padStart(2,"0")}`;
  }

  _visibleActiveJobs(jobs) {
    const source=Array.isArray(jobs)?jobs:[];
    const rank=(job)=>{
      const state=String(job?.state||"").toUpperCase();
      if(state==="RUNNING")return 0;
      if(state==="STARTING")return 1;
      if(state==="WAIT")return 2;
      if(state==="PLANNED")return 3;
      return 4;
    };
    const selected=new Map();
    const passthrough=[];
    for(const job of source){
      if(String(job?.origin||"").toUpperCase()!=="SCHEDULED"||!job?.schedule_id){
        passthrough.push(job);
        continue;
      }
      const key=String(job.schedule_id);
      const current=selected.get(key);
      if(!current){selected.set(key,job);continue;}
      const jobRank=rank(job), currentRank=rank(current);
      const jobTime=new Date(job?.planned_start||0).getTime();
      const currentTime=new Date(current?.planned_start||0).getTime();
      if(jobRank<currentRank||(jobRank===currentRank&&jobTime<currentTime))selected.set(key,job);
    }
    return [...selected.values(),...passthrough].sort((a,b)=>{
      const ar=rank(a),br=rank(b);
      if(ar!==br)return ar-br;
      return new Date(a?.planned_start||0).getTime()-new Date(b?.planned_start||0).getTime();
    });
  }

  _activeJobsHtml(entry) {
    const jobs = this._visibleActiveJobs(entry?.active_jobs || []);
    if (!jobs.length) return `<div class="empty">${this._tr("panel.no_active_jobs")}</div>`;
    return `<div class="job-list">
      <div class="job-list-header">
        <span>${this._tr("panel.job")}</span>
        <span>${this._tr("panel.now_short")}</span>
        <span>${this._tr("panel.cleaning")}</span>
        <span>${this._tr("panel.planned")}</span>
        <span>${this._tr("panel.latest_start")}</span>
        <span>${this._tr("panel.early_from")}</span>
        <span>${this._tr("panel.forecast_value")}</span>
        <span class="job-actions-header">${this._tr("panel.actions")}</span>
      </div>
      ${jobs.map((job) => {
        const id=String(job.job_id||"");
        const cached=this._activeJobDetails.get(id);
        const displayJob=cached?{...cached,...job,lifecycle_events:cached.lifecycle_events||[],notification_history:cached.notification_history||[],statistics_context:cached.statistics_context||null}:job;
        return `<details class="job-card active-job-card" data-job-id="${this._escape(job.job_id)}" ${this._activeJobOpen.has(id)?"open":""}>
      <summary class="job-summary">
        <span class="job-name" data-label="${this._tr("panel.job")}">${this._escape(job.schedule_name)}</span>
        <span class="job-now-cell" data-live-job="${this._escape(job.job_id)}" data-label="${this._tr("panel.now_short")}">${this._nowHtml(job)}</span>
        <span class="job-mode-cell" data-label="${this._tr("panel.cleaning")}">${this._activeJobModeHtml(job)}</span>
        <span data-label="${this._tr("panel.planned")}">${this._escape(this._formatPlannedDateTime(job.planned_start))}</span>
        <span data-label="${this._tr("panel.latest_start")}">${this._activeLatestCell(job)}</span>
        <span data-label="${this._tr("panel.early_from")}">${this._activeEarlyCell(job)}</span>
        <span class="job-forecast-cell" data-label="${this._tr("panel.forecast_value")}">${this._escape(this._formatForecastDuration(job.forecast_duration_seconds))}</span>
        <span class="job-row-actions" data-label="${this._tr("panel.actions")}">${this._jobRowActionsHtml(job)}</span>
      </summary>${this._jobDetailsHtml(displayJob)}</details>`;
      }).join("")}</div>`;
  }

  _executionModeLabel(mode) {
    return String(mode || "DRY_RUN").toUpperCase() === "REAL" ? this._tr("panel.execution_real") : this._tr("panel.execution_dry_run");
  }

  _executionModeIcon(mode) {
    return String(mode || "DRY_RUN").toUpperCase() === "REAL" ? "robot-vacuum" : "flask-outline";
  }

  _executionModeHtml(mode) {
    const normalized=String(mode || "DRY_RUN").toUpperCase() === "REAL" ? "REAL" : "DRY_RUN";
    const tone=normalized === "REAL" ? "real" : "dry-run";
    return `<span class="execution-mode-inline execution-mode-inline-${tone}">${this._mdi(this._executionModeIcon(normalized))}<span>${this._escape(this._executionModeLabel(normalized))}</span></span>`;
  }

  _historyExecutionModeHtml(mode) {
    const normalized=this._normalizedExecutionMode(mode);
    const tone=normalized === "REAL" ? "good" : "warning";
    return this._quietStatusHtml(this._executionModeLabel(normalized),tone,"history-execution-mode");
  }

  _historyResultHtml(result) {
    const normalized=String(result || "").toUpperCase();
    const tone=normalized === "SUCCESS" || normalized === "PARTIAL_SUCCESS"
      ? "good"
      : normalized === "SUPPRESSED"
        ? "warning"
        : normalized === "FAILED"
          ? "bad"
          : "neutral";
    return this._quietStatusHtml(this._resultLabel(result),tone,"history-result");
  }

  _quietStatusHtml(value,tone="neutral",extraClass="") {
    const safeTone=["good","warning","bad","neutral","info"].includes(String(tone))?String(tone):"neutral";
    const classes=["quiet-status",`quiet-status-${safeTone}`,String(extraClass||"").trim()].filter(Boolean).join(" ");
    return `<span class="${classes}"><span class="quiet-status-dot" aria-hidden="true"></span><span class="quiet-status-label">${this._escape(value)}</span></span>`;
  }

  _normalizedExecutionMode(mode) {
    const value=String(mode || "DRY_RUN").trim().toUpperCase();
    return value === "REAL" || value === "LIVE" || value === "REAL_EXECUTION" ? "REAL" : "DRY_RUN";
  }

  _currentExecutionMode() {
    const entry=this._statusEntry();
    return this._normalizedExecutionMode(this._settingsData?.execution?.mode || entry?.execution_mode || entry?.mode || "DRY_RUN");
  }

  _runtimeBlockerLabels(runtimeStatus) {
    const seen=new Set();
    const result=[];
    for(const blocker of (runtimeStatus?.blockers||[])) {
      const code=this._presentationBlockerCode(blocker?.code);
      if(!code || seen.has(code)) continue;
      seen.add(code);
      result.push(this._blockerLabel(code));
    }
    return result;
  }

  _runtimeAttentionBlockers(runtimeStatus) {
    const fallbackActionCodes=new Set(["clean_water_insufficient","dirty_water_full","detergent_unavailable","mop_not_attached","invalid_target","invalid_configuration","vacuum_error"]);
    return (runtimeStatus?.blockers||[]).filter((blocker)=>{
      const attention=String(blocker?.attention||"").toUpperCase();
      const code=String(blocker?.code||"");
      return attention==="ACTION_REQUIRED" || attention==="CRITICAL" || fallbackActionCodes.has(code);
    });
  }

  _bindingActive(key) {
    const binding=(this._settingsData?.bindings||[]).find((item)=>String(item?.key||"")===String(key));
    return String(binding?.binding_mode||"auto").toLowerCase()!=="disabled";
  }

  _resourceNeedsUserService(key) {
    const item=this._settingsData?.inputs?.values?.[key];
    if(!item || !this._bindingActive(key)) return false;
    const hasSource=!!item.source_entity_id || String(item.binding_mode||"").toLowerCase()==="manual";
    return hasSource && item.live_available===true && item.status==="ready" && item.live===false;
  }

  _runtimeBlockerSummary(runtimeStatus) {
    if(!runtimeStatus?.available) return this._tr("panel.current_start_unknown");
    if(runtimeStatus.can_start_now===true) return this._tr("panel.current_start_clear");
    const labels=this._runtimeBlockerLabels(runtimeStatus);
    if(!labels.length) return this._tr("panel.current_start_unknown");
    return labels.length===1 ? labels[0] : `${labels[0]} +${labels.length-1}`;
  }

  async _requestExecutionModeSwitch(targetMode) {
    const entryId=this._selectEntry(this._schedulerEntryId,false);
    if(!entryId || this._saving || this._executionOptionsSaving) return false;
    const selected=this._normalizedExecutionMode(targetMode);
    const current=this._currentExecutionMode();
    if(selected===current) return false;
    let preview;
    try {
      preview=await this._callWs({type:"vacuum_schedule/settings/preview_execution_mode",entry_id:entryId,mode:selected});
    } catch(err) {
      this._notify(this._errorText(err),"error",4200);
      return false;
    }
    let message=selected==="REAL"?this._tr("panel.switch_to_real_execution_warning"):this._tr("panel.switch_to_dry_run_warning");
    message+=`\n\n${this._tr("panel.execution_mode_regeneration_warning",{regenerated:preview?.regenerable_jobs??0,preserved:preview?.preserved_jobs??0})}`;
    if(selected==="REAL"&&preview?.may_start_immediately) message+=`\n\n${this._tr("panel.execution_mode_may_start_immediately")}`;
    if(selected==="REAL"&&preview?.runtime_status?.available&&preview.runtime_status.can_start_now===false) {
      const blockers=this._runtimeBlockerLabels(preview.runtime_status).join(", ") || this._tr("panel.current_start_unknown");
      message+=`\n\n${this._tr("panel.real_mode_runtime_blocked_warning",{blockers})}`;
    }
    const label=selected==="REAL"?this._tr("panel.enable_real_execution"):this._tr("panel.enable_dry_run");
    const confirmed=await this._confirmAction(label,message,label,selected==="REAL");
    if(!confirmed) return false;
    this._saving=true;
    this._setExecutionFormSaving(true, true);
    this._error=null;
    try {
      const result=await this._callWs({type:"vacuum_schedule/settings/update_execution_mode",entry_id:entryId,mode:selected});
      await Promise.all([this._loadScheduler(),this._loadSettings(false)]);
      if(this._executionSettingsDraft) this._executionSettingsDraft.mode=selected;
      const change=result?.mode_change||{};
      this._notify(this._tr("panel.execution_mode_changed",{regenerated:change.regenerated_jobs??0,preserved:change.preserved_jobs??0}));
      return true;
    } catch(err) {
      this._error=this._errorText(err);
      this._notify(this._error,"error",5000);
      return false;
    } finally {
      this._saving=false;
      this._render();
    }
  }

  _executionBannerHtml() {
    return this._globalBannersHtml();
  }

  _globalNoticeItems() {
    const entry=this._statusEntry();
    const mode=String(entry?.execution_mode || entry?.mode || this._settingsData?.execution?.mode || "DRY_RUN").toUpperCase();
    const notices=[];
    const seen=new Set();
    const claimedCodes=new Set();
    const add=(key,notice)=>{if(!seen.has(key)){seen.add(key);notices.push(notice);}};
    const persistent=Object.values(this._settingsData?.overrides?.active || this._settingsData?.overrides || {}).filter(x=>x?.persistent).length;
    const runtimeStatus=this._settingsData?.execution?.runtime_status||{};

    // Serviceable station resources deserve global attention even when the
    // nearest Job is not currently asking for them. They cannot self-heal.
    const stationLines=[];
    if(this._resourceNeedsUserService("dock.clean_water")) {
      stationLines.push(this._tr("panel.clean_water_service_required"));
      claimedCodes.add("clean_water_insufficient");
    }
    if(this._resourceNeedsUserService("dock.dirty_water")) {
      stationLines.push(this._tr("panel.dirty_water_service_required"));
      claimedCodes.add("dirty_water_full");
    }
    if(this._resourceNeedsUserService("dock.detergent")) {
      stationLines.push(this._tr("panel.detergent_service_required"));
      claimedCodes.add("detergent_unavailable");
    }
    if(stationLines.length) {
      add("station-service",{priority:5,icon:"water-alert",title:this._tr("panel.station_service_required"),lines:stationLines});
    }

    const pendingMaintenance=this._settingsData?.attention?.maintenance?.pending_sessions||[];
    if(pendingMaintenance.length) {
      const lines=pendingMaintenance.map((session)=>{const tanks=this._maintenanceTanks(session);const label=tanks.length>1?this._tr("panel.tanks_clean_dirty"):tanks[0]==="clean"?this._tr("panel.tank_clean_only"):this._tr("panel.tank_dirty_only");return `${label} · ${this._formatDateTime(session?.occurred_at||session?.detected_at)}`;});
      add("tank-service-confirmation",{priority:6,icon:"clipboard-alert-outline",title:this._tr("panel.tank_service_requires_confirmation"),lines,action:"maintenance"});
    }

    const validationErrors=(this._settingsData?.validation||[]).filter((item)=>String(item?.severity||"").toLowerCase()==="error");

    // Runtime WAIT by itself is not a global alert. Only blockers explicitly
    // classified as requiring action/critical attention are promoted here.
    // Configuration problems have their own consolidated notice below.
    const runtimeAttention=this._runtimeAttentionBlockers(runtimeStatus).filter((item)=>{
      const code=String(item?.code||"");
      if(claimedCodes.has(code)) return false;
      if(validationErrors.length && (code==="invalid_configuration" || code==="invalid_target")) return false;
      return true;
    });
    if(runtimeAttention.length) {
      const critical=runtimeAttention.some((item)=>String(item?.attention||"").toUpperCase()==="CRITICAL" || String(item?.code||"")==="vacuum_error" || String(item?.code||"")==="invalid_configuration");
      const labels=[];
      const codes=new Set();
      for(const item of runtimeAttention) {
        const code=String(item?.code||"");
        if(!code || codes.has(code)) continue;
        codes.add(code);
        labels.push(this._blockerLabel(code));
      }
      if(labels.length) add("runtime-attention",{priority:critical?0:8,icon:critical?"alert-octagon-outline":"account-alert-outline",title:this._tr(critical?"panel.critical_attention":"panel.action_required"),lines:labels});
    }

    const gate=String(this._settingsData?.policy?.execution_gate||"enabled");
    if(gate==="disabled") {
      add("execution-gate",{priority:10,icon:"pause-circle-outline",title:this._tr("panel.schedule_execution_disabled"),lines:[this._tr("panel.execution_disabled_banner_text")]});
    } else if(gate==="disabled_until") {
      const until=this._settingsData?.policy?.disabled_until;
      add("execution-gate",{priority:10,icon:"timer-pause-outline",title:this._tr("panel.execution_temporarily_disabled"),lines:[until?this._tr("panel.execution_disabled_until_banner_text",{time:this._formatDateTime(until)}):this._tr("panel.execution_disabled_banner_text")]});
    }

    if(validationErrors.length) {
      add("configuration-attention",{priority:1,icon:"cog-alert-outline",title:this._tr("panel.configuration_requires_attention"),lines:[this._tr("panel.configuration_error_count",{count:validationErrors.length})]});
    }

    if(this._view==="testing" && mode!=="DRY_RUN") {
      add("testing-real",{priority:20,icon:"flask-off-outline",title:this._tr("panel.dry_run_tab"),lines:[this._tr("panel.dry_run_tools_disabled_in_real")]});
    }
    if(persistent) {
      add("persistent-overrides",{priority:30,icon:"flask-outline",title:this._tr("panel.test_overrides"),lines:[this._tr("panel.persistent_test_overrides_active_value_they_affect_only_vacuum_schedule", {p1:persistent})]});
    }
    if(mode==="DRY_RUN") {
      add("dry-run",{priority:40,icon:"flask-outline",title:this._tr("panel.execution_dry_run"),lines:[this._tr("panel.dry_run_banner_text")]});
    }
    return notices.sort((a,b)=>a.priority-b.priority);
  }

  _systemBannerRowHtml(notice, lines=null, extraClass="") {
    const content=(lines || notice.lines || []).filter(Boolean);
    const action=notice.action?`<button type="button" class="ghost system-banner-action" data-global-notice-action="${this._escape(notice.action)}">${this._tr("panel.maintenance_open_from_alert")}</button>`:"";
    return `<div class="system-banner ${extraClass}">${this._mdi(notice.icon||"alert-outline")}<div class="system-banner-body"><b>${this._escape(notice.title||"")}</b>${content.map(line=>`<span>${this._escape(line)}</span>`).join("")}${action}</div></div>`;
  }

  _globalBannersHtml() {
    if(!this._schedulerEntryId || this._globalNoticesReadyEntryId!==this._schedulerEntryId) return "";
    const notices=this._globalNoticeItems();
    if(!notices.length) return "";
    const first=notices[0];
    const firstLines=(first.lines||[]).filter(Boolean);
    const hiddenCount=Math.max(0,firstLines.length-1)+notices.slice(1).reduce((total,item)=>total+Math.max(1,(item.lines||[]).filter(Boolean).length),0);
    if(!hiddenCount) return this._systemBannerRowHtml(first);
    const summaryLine=firstLines.length?firstLines[0]:"";
    const extra=[];
    if(firstLines.length>1) extra.push(this._systemBannerRowHtml(first,firstLines.slice(1),"system-banner-continuation"));
    for(const notice of notices.slice(1)) extra.push(this._systemBannerRowHtml(notice));
    const firstAction=first.action?`<button type="button" class="ghost system-banner-action" data-global-notice-action="${this._escape(first.action)}">${this._tr("panel.maintenance_open_from_alert")}</button>`:"";
    return `<details class="system-notice-stack" ${this._systemNoticesOpen?"open":""}><summary class="system-banner system-banner-summary">${this._mdi(first.icon||"alert-outline")}<div class="system-banner-body"><b>${this._escape(first.title||"")}</b>${summaryLine?`<span>${this._escape(summaryLine)}</span>`:""}${firstAction}</div><span class="system-banner-more">${this._tr("panel.more_notices",{count:hiddenCount})}${this._mdi(this._systemNoticesOpen?"chevron-up":"chevron-down")}</span></summary><div class="system-banner-extra">${extra.join("")}</div></details>`;
  }

  _historySource(job) {
    const explicit=String(job?.execution_source || "").toUpperCase();
    if (["SCHEDULED","MANUAL","FORCE","EXTERNAL"].includes(explicit)) return explicit;
    const origin=String(job?.origin || "").toUpperCase();
    if (origin === "EXTERNAL") return "EXTERNAL";
    if (origin === "MANUAL" || job?.manual_triggered_at || job?.manual_release_at) return "MANUAL";
    const force=job?.metadata?.force_execution;
    if (force && force.committed !== false && (force.selected_at || force.start_requested_at || force.attempt_ids?.length)) return "FORCE";
    return "SCHEDULED";
  }

  _historySourceLabel(value) {
    if (value === "EXTERNAL") return this._tr("panel.source_external");
    if (value === "MANUAL") return this._tr("panel.source_manual");
    if (value === "FORCE") return this._tr("panel.source_force");
    return this._tr("panel.source_scheduled");
  }

  _historyJobName(job) {
    return this._historySource(job)==="EXTERNAL" ? this._tr("panel.external_cleaning") : String(job?.schedule_name || "—");
  }

  _historyResultCategory(job) {
    const result=String(job?.result || "").toUpperCase();
    if (result === "SUCCESS") return "success";
    if (result === "PARTIAL_SUCCESS") return "partial";
    if (result === "SUPPRESSED") return "not_executed";
    if (result === "FAILED") return "error";
    return "other";
  }

  _historyFilterValues(group) {
    const raw=this._historyFilters?.[group];
    if(Array.isArray(raw)) return raw.map((value)=>String(value)).filter((value)=>value && value!=="all");
    if(raw===null || raw===undefined || raw==="" || raw==="all") return [];
    return [String(raw)];
  }

  _historyFilterSelected(group,value) {
    const values=this._historyFilterValues(group);
    return value==="all" ? values.length===0 : values.includes(String(value));
  }

  _toggleHistoryFilterValue(group,value) {
    if(!this._historyFilters || !Object.prototype.hasOwnProperty.call(this._historyFilters,group)) return;
    const normalized=String(value||"all");
    if(normalized==="all") {
      this._historyFilters[group]="all";
      return;
    }
    const values=this._historyFilterValues(group);
    const index=values.indexOf(normalized);
    if(index>=0) values.splice(index,1); else values.push(normalized);
    this._historyFilters[group]=values.length?values:"all";
  }

  _clearHistoryFilterValue(group,value=null) {
    if(!this._historyFilters || !Object.prototype.hasOwnProperty.call(this._historyFilters,group)) return;
    if(value===null || value===undefined || value==="") {
      this._historyFilters[group]="all";
      return;
    }
    const remaining=this._historyFilterValues(group).filter((item)=>item!==String(value));
    this._historyFilters[group]=remaining.length?remaining:"all";
  }

  _historyFilterMatches(group,value) {
    const selected=this._historyFilterValues(group);
    return selected.length===0 || selected.includes(String(value));
  }

  _historyFilterCompactLabel(group,value,fallback) {
    if(group==="execution_mode") return value==="REAL"?this._tr("panel.filter_chip_real"):this._tr("panel.execution_dry_run");
    if(group==="source") {
      const keys={SCHEDULED:"panel.filter_chip_scheduled",FORCE:"panel.filter_chip_force",MANUAL:"panel.filter_chip_manual",EXTERNAL:"panel.filter_chip_external"};
      if(keys[value]) return this._tr(keys[value]);
    }
    return fallback;
  }

  _historyFilterHtml(entry) {
    const rows=entry?.history || [];
    const option=(group,value,label)=>{const selected=this._historyFilterSelected(group,value);return `<button type="button" class="history-filter-option ${selected?"selected":""}" data-history-filter-group="${this._escape(group)}" data-history-filter-value="${this._escape(value)}" aria-pressed="${selected?"true":"false"}">${this._escape(label)}</button>`;};
    const schedules=[...new Map(rows.filter(row=>row?.schedule_id).map(row=>[String(row.schedule_id),String(row.schedule_name||row.schedule_id)])).entries()].sort((a,b)=>a[1].localeCompare(b[1],this._language));
    const resultLabels={success:this._tr("panel.success"),partial:this._tr("panel.partial"),not_executed:this._tr("panel.not_executed"),error:this._tr("panel.errors")};
    const active=[];
    for(const value of this._historyFilterValues("execution_mode")) active.push(["execution_mode",value,value==="REAL"?this._tr("panel.execution_real"):this._tr("panel.execution_dry_run")]);
    for(const value of this._historyFilterValues("result")) active.push(["result",value,resultLabels[value]||value]);
    for(const value of this._historyFilterValues("source")) active.push(["source",value,this._historySourceLabel(value)]);
    for(const value of this._historyFilterValues("schedule_id")) active.push(["schedule_id",value,schedules.find(([id])=>id===value)?.[1]||value]);
    const activeHtml=active.map(([group,value,label])=>{const compact=this._historyFilterCompactLabel(group,value,label);return `<span class="history-active-filter" title="${this._escape(label)}"><span class="history-active-filter-label">${this._escape(compact)}</span><button type="button" class="history-active-filter-remove icon-only" data-history-filter-clear="${this._escape(group)}" data-history-filter-clear-value="${this._escape(value)}" title="${this._tr("panel.remove_filter")}" aria-label="${this._tr("panel.remove_filter")}">${this._mdi("close")}</button></span>`;}).join("");
    const scheduleOptions=schedules.length?`${option("schedule_id","all",this._tr("panel.all"))}${schedules.map(([id,name])=>option("schedule_id",id,name)).join("")}`:`<span class="muted">${this._tr("panel.no_data")}</span>`;
    return `<div class="history-filter-shell">
      <details class="history-filter-popover" ${this._historyFiltersOpen?"open":""}>
        <summary class="history-filter-trigger">${this._mdi("filter-variant")}<span>${this._tr("panel.filters")}</span>${active.length?`<b>${active.length}</b>`:""}</summary>
        <div class="history-filter-panel">
          <div class="history-filter-group"><b>${this._tr("panel.execution_mode")}</b><div class="history-filter-options">${option("execution_mode","all",this._tr("panel.all"))}${option("execution_mode","REAL",this._tr("panel.execution_real"))}${option("execution_mode","DRY_RUN",this._tr("panel.execution_dry_run"))}</div></div>
          <div class="history-filter-group"><b>${this._tr("panel.result")}</b><div class="history-filter-options">${option("result","all",this._tr("panel.all"))}${option("result","success",this._tr("panel.success"))}${option("result","partial",this._tr("panel.partial"))}${option("result","not_executed",this._tr("panel.not_executed"))}${option("result","error",this._tr("panel.errors"))}</div></div>
          <div class="history-filter-group"><b>${this._tr("panel.source")}</b><div class="history-filter-options">${option("source","all",this._tr("panel.all"))}${option("source","SCHEDULED",this._tr("panel.source_scheduled"))}${option("source","MANUAL",this._tr("panel.source_manual"))}${option("source","EXTERNAL",this._tr("panel.source_external"))}${option("source","FORCE",this._tr("panel.source_force"))}</div></div>
          <div class="history-filter-group history-filter-schedule"><b>${this._tr("panel.schedule")}</b><div class="history-filter-options history-filter-schedule-options">${scheduleOptions}</div></div>
          ${active.length?`<button type="button" class="ghost history-filter-reset">${this._mdi("filter-remove-outline")}<span>${this._tr("panel.reset_filters")}</span></button>`:""}
        </div>
      </details>
      ${active.length?`<div class="history-active-filters">${activeHtml}</div>`:""}
    </div>`;
  }

  _historyAttempts(job) {
    return Object.values(job?.execution_attempts||{}).filter((attempt)=>attempt&&typeof attempt==="object");
  }

  _historyCleaningProfileText(job) {
    const attempts=this._historyAttempts(job);
    const started=attempts.filter((attempt)=>attempt.start_confirmed_at);
    const sources=(started.length?started:attempts)
      .map((attempt)=>attempt.cleaning_params)
      .filter((params)=>params&&typeof params==="object"&&Object.keys(params).length);
    if(!sources.length&&job?.cleaning_params) sources.push(job.cleaning_params);
    const profiles=[];
    for(const params of sources){
      const text=this._compactCleaningProfileText(params);
      if(text&&!profiles.includes(text)) profiles.push(text);
    }
    return profiles.join(" / ");
  }

  _historyPhysicalDurationSeconds(job) {
    if(this._normalizedExecutionMode(job?.execution_mode)!=="REAL") return null;
    const confirmedStarts=this._historyAttempts(job)
      .filter((attempt)=>this._normalizedExecutionMode(attempt.execution_mode||job?.execution_mode)==="REAL")
      .map((attempt)=>Date.parse(String(attempt.start_confirmed_at||"")))
      .filter(Number.isFinite);
    const start=confirmedStarts.length
      ?Math.min(...confirmedStarts)
      :Date.parse(String(job?.actual_start||""));
    const end=Date.parse(String(job?.finished_at||""));
    return Number.isFinite(start)&&Number.isFinite(end)&&end>=start?(end-start)/1000:null;
  }

  _historyTime(job) {
    return this._formatPlannedDateTime(job?.finished_at||job?.planned_start);
  }

  _historyHtml(entry) {
    const allRows = entry?.history || [];
    if (!allRows.length) return `<div class="empty">${this._tr("panel.history_is_empty")}</div>`;
    const rows=allRows.filter(job=>{
      if(!this._historyFilterMatches("execution_mode",this._normalizedExecutionMode(job.execution_mode))) return false;
      if(!this._historyFilterMatches("result",this._historyResultCategory(job))) return false;
      if(!this._historyFilterMatches("source",this._historySource(job))) return false;
      if(!this._historyFilterMatches("schedule_id",String(job.schedule_id||""))) return false;
      return true;
    });
    const header=`<div class="history-job-list-header"><span>${this._tr("panel.completed")}</span><span>${this._tr("panel.schedule")}</span><span>${this._tr("panel.source")}</span><span>${this._tr("panel.execution_mode")}</span><span>${this._tr("panel.cleaning_profile_used")}</span><span>${this._tr("panel.duration")}</span><span>${this._tr("panel.result")}</span><span>${this._tr("panel.reason")}</span></div>`;
    const cards=rows.map((job)=>{
      const id=String(job.job_id||""); const cached=this._historyJobDetails.get(id); const open=this._historyJobOpen.has(id);
      const body=cached?this._historyJobDetailsHtml(cached):`<div class="job-details history-job-details"><div class="wide-detail"><span class="plain-status">${this._historyJobLoading.has(id)?this._tr("panel.loading_job_trace"):this._tr("panel.open_to_load_full_trace")}</span></div></div>`;
      const completed=this._historyTime(job);
      const name=this._historyJobName(job);
      const sourceLabel=this._historySourceLabel(this._historySource(job));
      const source=job.waited_before_start?this._tr("panel.with_wait"):sourceLabel;
      const execution=this._executionModeLabel(this._normalizedExecutionMode(job.execution_mode));
      const profile=this._historyCleaningProfileText(job)||"—";
      const duration=this._formatDurationSeconds(this._historyPhysicalDurationSeconds(job));
      const result=this._resultLabel(job.result);
      const reason=this._historyResultCategory(job)==="success"?"—":this._historyReasonLabel(this._historyDisplayReason(job));
      return `<details class="job-card history-job-card" data-history-job-id="${this._escape(id)}" ${open?"open":""}><summary class="history-job-summary"><span data-label="${this._tr("panel.completed")}"><span class="history-cell-value" title="${this._escape(completed)}">${this._escape(completed)}</span></span><span class="job-name" data-label="${this._tr("panel.schedule")}"><span class="history-cell-value" title="${this._escape(name)}">${this._escape(name)}</span></span><span class="history-source-cell" data-label="${this._tr("panel.source")}"><span class="history-cell-value" title="${this._escape(source)}">${this._escape(source)}</span></span><span data-label="${this._tr("panel.execution_mode")}"><span class="history-cell-value" title="${this._escape(execution)}">${this._historyExecutionModeHtml(job.execution_mode)}</span></span><span data-label="${this._tr("panel.cleaning_profile_used")}"><span class="history-cell-value history-cleaning-profile" title="${this._escape(profile)}">${this._escape(profile)}</span></span><span class="history-physical-duration" data-label="${this._tr("panel.duration")}"><span class="history-cell-value" title="${this._escape(duration)}">${this._escape(duration)}</span></span><span data-label="${this._tr("panel.result")}"><span class="history-cell-value" title="${this._escape(result)}">${this._historyResultHtml(job.result)}</span></span><span data-label="${this._tr("panel.reason")}"><span class="history-cell-value" title="${this._escape(reason)}">${this._escape(reason)}</span></span></summary>${body}</details>`;
    }).join("");
    return rows.length?`<div class="history-job-list">${header}${cards}</div>`:`<div class="empty">${this._tr("panel.no_jobs_match_filters")}</div>`;
  }

  _statusHtml() {
    const entry = this._statusEntry();
    if (!entry) return `<div class="empty">${this._tr("panel.scheduler_is_not_loaded_yet")}</div>`;
    const c = entry.counts || {};
    const policy=this._settingsData?.policy || entry.preflight?.policy || {};
    const gate=policy.execution_gate || "enabled";
    const gateText=gate==="enabled"?this._tr("panel.enabled_dde9969"):gate==="disabled_until"?`${this._tr("panel.disabled_until")} ${this._formatDateTime(policy.disabled_until)}`:this._tr("panel.disabled");
    const rawExecutionMode=String(entry.mode || "").trim().toUpperCase();
    const dryRunMode=rawExecutionMode==="SIMULATION" || rawExecutionMode==="DRY_RUN" || rawExecutionMode==="DRY-RUN";
    const realExecutionMode=rawExecutionMode==="LIVE" || rawExecutionMode==="REAL" || rawExecutionMode==="REAL_EXECUTION";
    const executionModeText=dryRunMode?this._tr("panel.execution_dry_run"):realExecutionMode?this._tr("panel.execution_real"):this._tr("panel.unknown_12049ad");
    const executionModeHint=dryRunMode?this._tr("panel.jobs_run_fully_in_simulation_no_physical_commands_are_sent_to_the_vacuum"):realExecutionMode?this._tr("panel.jobs_are_executed_on_the_real_vacuum"):this._tr("panel.the_backend_did_not_report_a_known_execution_mode");
    const executionModeClass=dryRunMode?"execution-mode-dry-run":realExecutionMode?"execution-mode-real":"execution-mode-unknown";
    return `${this._entrySelectorHtml()}
      <div class="summary-grid">
        <div class="summary-card summary-card-gate"><span>${this._tr("panel.execution")}</span><b class="gate-${this._escape(gate)}" title="${this._escape(gateText)}">${this._escape(gateText)}</b><div class="mini-actions summary-actions">${gate==="enabled"?`<button class="ghost icon-only compact-icon-action quick-gate summary-action" data-mode="disabled" title="${this._tr("panel.disable_schedule_execution")}" aria-label="${this._tr("panel.disable_schedule_execution")}">${this._mdi("power")}</button><button class="ghost icon-only compact-icon-action quick-gate-until summary-action" title="${this._tr("panel.disable_execution_until_a_specified_time")}" aria-label="${this._tr("panel.disable_execution_until_a_specified_time")}">${this._mdi("clock-outline")}</button>`:`<button class="primary icon-only compact-icon-action quick-gate summary-action" data-mode="enabled" title="${this._tr("panel.enable_schedule_execution")}" aria-label="${this._tr("panel.enable_schedule_execution")}">${this._mdi("power")}</button>`}</div></div>
        <div class="summary-card summary-card-mode"><span>${this._tr("panel.execution_mode")}</span><span class="summary-mode-value ${executionModeClass}" title="${this._escape(executionModeHint)}"><b>${this._escape(executionModeText)}</b></span><div class="mini-actions summary-actions summary-mode-actions" role="group" aria-label="${this._escape(this._tr("panel.execution_mode"))}"><button type="button" class="ghost icon-only compact-icon-action quick-execution-mode summary-action quick-execution-mode-dry ${dryRunMode?"active":""}" data-execution-mode="DRY_RUN" aria-pressed="${dryRunMode?"true":"false"}" title="${this._escape(this._tr("panel.enable_dry_run"))}" aria-label="${this._escape(this._tr("panel.enable_dry_run"))}">${this._mdi("flask-outline")}</button><button type="button" class="ghost icon-only compact-icon-action quick-execution-mode summary-action quick-execution-mode-real ${realExecutionMode?"active":""}" data-execution-mode="REAL" aria-pressed="${realExecutionMode?"true":"false"}" title="${this._escape(this._tr("panel.enable_real_execution"))}" aria-label="${this._escape(this._tr("panel.enable_real_execution"))}">${this._mdi("robot-vacuum")}</button></div></div>
        <div class="summary-card"><span>${this._tr("panel.active_jobs")}</span><b>${c.active ?? 0}</b><small>${this._tr("panel.waiting")} ${c.waiting ?? 0} · ${this._tr("panel.running")} ${c.running ?? 0}</small></div>
        <div class="summary-card"><span>${this._tr("panel.next_transition")}</span><b>${this._formatDateTime(entry.next_transition)}</b></div>
      </div>
      ${this._robotStatusHtml(entry)}
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.active_jobs")}</h2><div class="muted">${this._tr("panel.click_a_row_to_view_job_details")}</div></div><button class="ghost rebuild-schedule">${this._mdi("calendar-refresh-outline")}<span>${this._tr("panel.refresh_schedule")}</span></button></div>${this._activeJobsHtml(entry)}</section>
      <section class="entry-card"><div class="entry-header history-entry-header"><div><h2>${this._tr("panel.recent_jobs")}</h2><div class="muted">${this._tr("panel.recently_completed_jobs")}</div></div>${this._historyFilterHtml(entry)}</div>${this._historyHtml(entry)}</section>
      ${this._maintenanceStatusCardHtml()}`;
  }


  _notificationPresetPolicy(preset) {
    if (preset === "minimal") return {prewarning_mode:"blockers_only",wait_enter_mode:"delayed",wait_delay_seconds:300,wait_reminder_enabled:false,wait_reminder_interval_seconds:1800,wait_reminder_max_count:0,start_mode:"off",finish_mode:"attention_only"};
    if (preset === "detailed") return {prewarning_mode:"always",wait_enter_mode:"immediate",wait_delay_seconds:0,wait_reminder_enabled:true,wait_reminder_interval_seconds:1800,wait_reminder_max_count:3,start_mode:"always",finish_mode:"always"};
    return {prewarning_mode:"blockers_only",wait_enter_mode:"delayed",wait_delay_seconds:120,wait_reminder_enabled:true,wait_reminder_interval_seconds:1800,wait_reminder_max_count:3,start_mode:"deviation_only",finish_mode:"incomplete_only"};
  }

  _notificationEventLabel(value) {
    const labels = {
      prewarning: this._tr("panel.prewarning"),
      wait_enter: this._tr("panel.wait_entry"),
      wait_reminder: this._tr("panel.wait_reminder"),
      started: this._tr("panel.start_43805bb"),
      finished: this._tr("panel.cleaning_result"),
      start_forecast: this._tr("panel.start_forecast"),
      test: this._tr("panel.test"),
    };
    return labels[String(value || "")] || String(value || "");
  }

  _notificationModeOptions(kind, selected) {
    const tables = {
      start_forecast_mode:[["off",this._tr("panel.do_not_send")],["important",this._tr("panel.forecast_important")],["all",this._tr("panel.forecast_all")]],
      prewarning_mode:[["off",this._tr("panel.do_not_send")],["always",this._tr("panel.always")],["blockers_only",this._tr("panel.only_when_blockers_exist")]],
      wait_enter_mode:[["off",this._tr("panel.do_not_send")],["immediate",this._tr("panel.immediately")],["delayed",this._tr("panel.if_waiting_continues")]],
      start_mode:[["off",this._tr("panel.do_not_send")],["always",this._tr("panel.always")],["deviation_only",this._tr("panel.only_when_execution_deviates_from_plan")]],
      finish_mode:[["off",this._tr("panel.do_not_send")],["always",this._tr("panel.always")],["incomplete_only",this._tr("panel.only_when_not_fully_completed")],["failed_only",this._tr("panel.only_when_cleaning_failed")],["attention_only",this._tr("panel.only_when_attention_is_required")]],
    };
    return (tables[kind]||[]).map(([value,label])=>`<option value="${value}" ${selected===value?"selected":""}>${this._escape(label)}</option>`).join("");
  }

  _presenceOptions(selected) {
    const rows=[["always",this._tr("panel.always")],["home_only",this._tr("panel.only_when_home")],["away_only",this._tr("panel.only_when_away")],["disabled",this._tr("panel.disabled_ba65a5a")]];
    return rows.map(([v,l])=>`<option value="${v}" ${selected===v?"selected":""}>${this._escape(l)}</option>`).join("");
  }

  _channelFilterOptions(selected) {
    const rows=[["all",this._tr("panel.all_eligible")],["attention_only",this._tr("panel.attention_only")],["error_only",this._tr("panel.errors_only")],["custom",this._tr("panel.custom")]];
    return rows.map(([v,l])=>`<option value="${v}" ${selected===v?"selected":""}>${this._escape(l)}</option>`).join("");
  }

  _notificationSuppressionLabel(reason) {
    const labels={
      forecast_coalesced:this._tr("panel.forecast_coalesced"),
      forecast_no_prior_warning:this._tr("panel.forecast_no_prior_warning"),
      recipient_disabled:this._tr("panel.recipient_disabled"),
      channel_disabled:this._tr("panel.channel_disabled"),
      event_filtered:this._tr("panel.event_filtered_by_channel_rules"),
      presence_unknown:this._tr("panel.presence_state_is_unknown"),
      not_home:this._tr("panel.recipient_is_not_home"),
      not_away:this._tr("panel.recipient_is_home"),
    };
    return labels[String(reason||"")]||String(reason||"—");
  }

  _notificationRoutingTestResultHtml() {
    const result=this._notificationRoutingTestResult;
    if(!result)return "";
    const rows=result.deliveries||[];
    const sent=Number(result.sent||0),suppressed=Number(result.suppressed||0),failed=Number(result.failed||0);
    const summary=`<div class="routing-test-summary"><span class="ui-chip status-badge support support-${sent?"detected":"unverified"}">${this._tr("panel.sent")}: ${sent}</span><span class="ui-chip status-badge support support-${suppressed?"unverified":"detected"}">${this._tr("panel.suppressed")}: ${suppressed}</span><span class="ui-chip status-badge support support-${failed?"conflict":"detected"}">${this._tr("panel.failed")}: ${failed}</span></div>`;
    const master=result.master_bypassed?`<div class="muted routing-test-note">${this._tr("panel.the_global_notification_master_switch_is_currently_off_it_was_bypassed_f")}</div>`:"";
    if(!rows.length)return `${summary}${master}<div class="empty compact">${this._tr("panel.no_delivery_routes_exist_check_recipients_and_attached_channels")}</div>`;
    const body=rows.map(x=>{const channelName=this._notificationChannelDisplayName(x.channel_name,x.target,x.target_kind);const reason=x.status==="suppressed"?this._notificationSuppressionLabel(x.reason):(x.reason||this._notificationDeliveryOperationLabel(x.operation)||"—");return `<tr><td class="recipient-channel-cell" data-label="${this._tr("panel.recipient_channel")}"><div class="recipient-channel-stack"><b>${this._escape(x.recipient_name||"")}</b><small>${this._escape(channelName)}</small></div></td><td class="routing-target" data-label="${this._tr("panel.target")}"><code>${this._escape(x.target||"")}</code></td><td data-label="${this._tr("panel.result")}"><span class="ui-chip status-badge support support-${x.status==="sent"?"detected":x.status==="failed"?"conflict":"unverified"}">${this._escape(this._notificationDeliveryStatusLabel(x.status))}</span></td><td data-label="${this._tr("panel.reason")}">${this._escape(reason)}</td></tr>`;}).join("");
    return `${summary}${master}<div class="table-wrap routing-test-result"><table class="source-table mobile-card-table"><thead><tr><th>${this._tr("panel.recipient_channel")}</th><th>${this._tr("panel.target")}</th><th>${this._tr("panel.result")}</th><th>${this._tr("panel.reason")}</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }

  _jobById(jobId) {
    const id=String(jobId||"");
    if(!id)return null;
    for(const entry of (this._schedulerData?.entries||[])) {
      const active=(entry.active_jobs||[]).find((job)=>String(job.job_id)===id);
      if(active)return {job:active,entry,active:true};
      const historical=(entry.history||[]).find((job)=>String(job.job_id)===id);
      if(historical)return {job:historical,entry,active:false};
    }
    return null;
  }

  _notificationHistoryJobActionsHtml(x) {
    if(!x.job_id)return "";
    const found=this._jobById(x.job_id);
    if(!found)return `<div class="wide-detail delivery-job-context"><b>${this._tr("panel.related_job")}</b><div class="muted">${this._tr("panel.the_current_state_of_the_related_job_is_unavailable")}</div></div>`;
    const job=found.job;
    const actionButton=(action,icon,label,style="ghost")=>`<button type="button" class="${style} job-action delivery-job-action" data-job="${this._escape(job.job_id)}" data-action="${this._escape(action)}">${this._mdi(icon)}<span>${this._escape(label)}</span></button>`;
    let actions="";
    if(job.state==="PLANNED")actions=`${actionButton("start_now","play",this._tr("panel.start_now"),"primary")}${actionButton("skip","skip-next-outline",this._tr("panel.skip"),"danger")}`;
    else if(job.state==="WAIT")actions=`${actionButton("recheck","refresh",this._tr("panel.recheck"),"primary")}${actionButton("start_now","play",this._tr("panel.start_now"))}${actionButton("skip","skip-next-outline",this._tr("panel.skip"),"danger")}`;
    else if(job.state==="STARTING"||job.state==="RUNNING")actions=actionButton("cancel","stop-circle-outline",this._tr("panel.cancel_execution"),"danger");
    const noActions=found.active?this._tr("panel.no_user_actions_are_available_in_the_current_state"):this._tr("panel.the_job_is_already_finished_so_user_actions_are_unavailable");
    return `<div class="wide-detail delivery-job-context">
      <div class="delivery-job-head"><div><b>${this._tr("panel.user_actions")}</b><div class="muted">${this._escape(job.schedule_name||"")} · ${this._stateChipHtml(job)}</div></div><button type="button" class="ghost delivery-open-job" data-job="${this._escape(job.job_id)}">${this._mdi("open-in-new")}<span>${this._tr("panel.open_job")}</span></button></div>
      ${actions?`<div class="delivery-job-actions">${actions}</div>`:`<div class="muted delivery-job-no-actions">${noActions}</div>`}
    </div>`;
  }

  _notificationTechnicalValueHtml(label,value) {
    const text=String(value||"—");
    return `<div class="delivery-tech-item"><b>${this._escape(label)}</b><div class="delivery-tech-value"><code>${this._escape(text)}</code>${text!=="—"?`<button type="button" class="ghost icon-only compact-icon-action delivery-copy" data-copy="${this._escape(text)}" title="${this._tr("panel.copy")}" aria-label="${this._tr("panel.copy")}">${this._mdi("content-copy")}</button>`:""}</div></div>`;
  }

  _notificationHistoryDetailHtml(x) {
    const provider=[x.provider_entity_id?`${this._tr("panel.entity")}: ${x.provider_entity_id}`:"",x.provider_chat_id?`chat: ${x.provider_chat_id}`:"",x.provider_message_id?`message: ${x.provider_message_id}`:""].filter(Boolean).join(" · ");
    const technical=[
      this._notificationTechnicalValueHtml(this._tr("panel.semantic_event"),x.semantic_type||x.event_type||"—"),
      this._notificationTechnicalValueHtml("event_id",x.event_id||"—"),
      this._notificationTechnicalValueHtml("delivery_id",x.delivery_id||"—"),
      x.job_id?this._notificationTechnicalValueHtml("job_id",x.job_id):"",
      provider?this._notificationTechnicalValueHtml(this._tr("panel.provider_data"),provider):"",
    ].filter(Boolean).join("");
    return `<div class="notification-delivery-detail-grid">
      <div class="delivery-field"><b>${this._tr("panel.title")}</b><div class="delivery-value">${this._escape(x.title||"—")}</div></div>
      <div class="delivery-field"><b>${this._tr("panel.transport")}</b><div class="delivery-value">${this._escape(this._notificationTransportLabel(x.transport_type))} · ${this._escape(x.target||"—")}</div></div>
      <div class="delivery-field"><b>${this._tr("panel.delivery_result")}</b><div class="delivery-value"><span class="ui-chip status-badge support support-${x.status==="sent"?"detected":x.status==="failed"?"conflict":"unverified"}">${this._escape(this._notificationDeliveryStatusLabel(x.status))}</span></div></div>
      <div class="delivery-field"><b>${this._tr("panel.delivery_operation")}</b><div class="delivery-value">${this._escape(this._notificationDeliveryOperationLabel(x.delivery_operation))}</div></div>
      <div class="wide-detail delivery-field"><b>${this._tr("panel.message")}</b><pre class="notification-message">${this._escape(x.message||"—")}</pre></div>
      ${this._notificationHistoryJobActionsHtml(x)}
      ${(x.suppression_reason||x.error)?`<div class="wide-detail delivery-field"><b>${this._tr("panel.reason_error")}</b><div class="delivery-value">${this._escape(x.suppression_reason?this._notificationSuppressionLabel(x.suppression_reason):x.error)}</div></div>`:""}
      <details class="wide-detail delivery-technical"><summary>${this._tr("panel.technical_details")}</summary><div class="delivery-tech-grid">${technical}</div></details>
    </div>`;
  }

  _notificationMessageDuration(seconds) {
    return this._formatDurationSeconds(Math.max(0,Number(seconds||0)));
  }

  _notificationMessageFactsHtml(message) {
    if(!message)return "";
    const facts=[];
    const add=(label,value)=>{if(value!==null&&value!==undefined&&String(value)!=="")facts.push(`<div class="notification-message-fact"><span>${this._escape(label)}</span><b>${value}</b></div>`);};
    if(message.schedule_name)add(this._tr("panel.schedule"),this._escape(message.schedule_name));
    const snapshots=message.zone_snapshots||[];
    const waiting=snapshots.filter(z=>String(z.state)==="WAIT");
    const running=snapshots.filter(z=>String(z.state)==="RUNNING"||String(z.state)==="STARTING");
    const finished=snapshots.filter(z=>String(z.state)==="FINISHED");
    if(waiting.length){
      const value=waiting.map(z=>{const blockers=this._blockerLabels(z.blockers||[]);return `${z.name||z.zone_id}${blockers.length?` — ${blockers.join(", ")}`:""}`;}).join("; ");
      add(this._tr("panel.waiting_zones"),this._escape(value));
    } else if(running.length) {
      add(this._tr("panel.cleaning_zones"),this._escape(running.map(z=>z.name||z.zone_id).join(", ")));
    } else if(finished.length && message.event_type==="finished") {
      const value=finished.map(z=>`${z.name||z.zone_id}${z.result?` — ${this._resultLabel(z.result)}`:""}${z.reason_code?`: ${this._reasonLabel(z.reason_code)}`:""}`).join("; ");
      add(this._tr("panel.cleaning_zones"),this._escape(value));
    } else if((message.zones||[]).length) {
      add(this._tr("panel.cleaning_zones"),this._escape((message.zones||[]).join(", ")));
    }
    if(message.planned_at)add(this._tr("panel.scheduled_start"),this._escape(this._formatDateTime(message.planned_at)));
    if(message.wait_elapsed_seconds!==null&&message.wait_elapsed_seconds!==undefined)add(this._tr("panel.waiting"),this._escape(this._notificationMessageDuration(message.wait_elapsed_seconds)));
    if(message.deadline_at && ["wait_enter","wait_reminder"].includes(String(message.event_type)))add(this._tr("panel.latest_allowed_start"),this._escape(this._formatDateTime(message.deadline_at)));
    if(message.result)add(this._tr("panel.result"),this._escape(this._resultLabel(message.result)));
    if(message.reason_code)add(this._tr("panel.reason"),this._escape(this._reasonLabel(message.reason_code)));
    const blockers=this._blockerLabels(message.blockers||[]);
    if(!waiting.length&&blockers.length)add(this._tr("panel.blockers"),this._escape(blockers.join(" · ")));
    return `<div class="notification-message-facts">${facts.join("")}</div>`;
  }

  _notificationCurrentJobHtml(job) {
    if(!job)return "";
    if(job.available===false)return `<section class="notification-current-card"><h3>${this._tr("panel.current_state")}</h3><div class="muted">${this._tr("panel.the_current_state_of_the_related_job_is_unavailable")}</div></section>`;
    const stateJob={state:job.state,result:job.result};
    const details=[];
    const waiting=(job.zone_runs||[]).filter(z=>String(z.state)==="WAIT");
    const running=(job.zone_runs||[]).filter(z=>["STARTING","RUNNING"].includes(String(z.state)));
    if(waiting.length)details.push(waiting.map(z=>{const blockers=this._blockerLabels(z.blockers||[]);return `${z.zone_name||z.zone_id}${blockers.length?` — ${blockers.join(", ")}`:""}`;}).join("; "));
    else if(running.length)details.push(running.map(z=>z.zone_name||z.zone_id).join(", "));
    else if(job.reason_code)details.push(this._reasonLabel(job.reason_code));
    const actionMeta={
      start_now:["play",this._tr("panel.start_now"),"primary"],
      start_now_ignore_busy:["account-alert-outline",this._tr("panel.start_ignoring_occupancy"),"primary"],
      recheck:["refresh",this._tr("panel.recheck"),"ghost"],
      skip:["skip-next-outline",this._tr("panel.skip"),"danger"],
      cancel:["stop-circle-outline",this._tr("panel.cancel_execution"),"danger"],
      pause:["pause-circle-outline",this._tr("panel.pause"),"ghost"],
      resume:["play-circle-outline",this._tr("panel.resume"),"primary"],
    };
    const actions=(job.available_actions||[]).map(item=>{
      const meta=actionMeta[item.action];if(!meta)return "";
      const busy=(item.busy_zones||[]).join(", ");
      return `<button type="button" class="${meta[2]} job-action notification-job-action" data-job="${this._escape(job.job_id)}" data-action="${this._escape(item.action)}" data-busy-zones="${this._escape(busy)}">${this._mdi(meta[0])}<span>${this._escape(meta[1])}</span></button>`;
    }).join("");
    const noActions=job.active?this._tr("panel.no_user_actions_are_available_in_the_current_state"):this._tr("panel.the_job_is_already_finished_so_user_actions_are_unavailable");
    return `<section class="notification-current-card">
      <div class="notification-current-head"><div><h3>${this._tr("panel.current_state")}</h3><div class="notification-current-state">${this._stateChipHtml(stateJob)}${job.execution_state?`<span class="muted">${this._escape(job.execution_state)}</span>`:""}</div></div></div>
      ${details.length?`<div class="notification-current-detail">${this._escape(details.join(" · "))}</div>`:""}
      ${actions?`<div class="notification-current-actions">${actions}</div>`:`<div class="muted notification-current-no-actions">${noActions}</div>`}
      <button type="button" class="text-link notification-open-job" data-job="${this._escape(job.job_id)}">${this._tr("panel.job_details_link")} ${this._mdi("chevron-right")}</button>
    </section>`;
  }

  _notificationMessageDetailHtml() {
    const detail=this._notificationMessageDetail;
    const message=detail?.message;
    if(!message)return `<section class="entry-card"><div class="empty">${this._tr("panel.loading_notification_message")}</div></section>`;
    const delivery=message.delivery_summary||{};
    return `<section class="entry-card notification-message-page">
      <div class="notification-message-nav"><button type="button" class="ghost notification-message-back">${this._mdi("arrow-left")}<span>${this._tr("panel.back_to_notifications")}</span></button></div>
      <div class="notification-message-head"><div><div class="notification-message-kicker">${this._escape(this._notificationEventLabel(message.event_type))} · ${this._escape(this._formatDateTime(message.created_at))}</div><h2>${this._escape(message.title||this._notificationEventLabel(message.event_type))}</h2></div>${this._executionModeHtml(message.execution_mode||"REAL")}</div>
      ${this._notificationMessageFactsHtml(message)}
      ${this._notificationCurrentJobHtml(detail.job)}
      <details class="notification-message-technical"><summary>${this._tr("panel.technical_details")}</summary><div class="delivery-tech-grid">${this._notificationTechnicalValueHtml("event_id",message.event_id)}${message.job_id?this._notificationTechnicalValueHtml("job_id",message.job_id):""}<div class="delivery-tech-item"><b>${this._tr("panel.delivery_result")}</b><div>${this._tr("panel.deliveries_summary",{total:delivery.total||0,sent:delivery.sent||0,failed:delivery.failed||0,suppressed:delivery.suppressed||0})}</div></div></div></details>
    </section>`;
  }

  _notificationMessageRoutesHtml(message) {
    const routes=message?.delivery_routes||[];
    if(!routes.length)return `<span class="muted">—</span>`;
    return `<div class="notification-message-routes">${routes.map(route=>{
      const recipient=route.recipient_name||route.recipient_id||this._tr("panel.unknown_8a51c33");
      const channel=this._notificationChannelDisplayName(route.channel_name,route.target,route.target_kind);
      const transport=this._notificationTransportLabel(route.transport_type);
      return `<div class="notification-message-route"><span class="notification-message-route-recipient">${this._escape(recipient)}</span><small>${this._escape(`${transport} · ${channel}`)}</small>${route.target?`<code title="${this._escape(route.target)}">${this._escape(route.target)}</code>`:""}</div>`;
    }).join("")}</div>`;
  }

  _notificationMessageDeliverySummaryHtml(message) {
    const summary=message?.delivery_summary||{};
    const total=Number(summary.total||0),sent=Number(summary.sent||0),failed=Number(summary.failed||0),suppressed=Number(summary.suppressed||0),pending=Number(summary.pending||0);
    if(!total)return `<span class="muted">—</span>`;
    let tone="neutral",headline="";
    if(failed && !sent && !suppressed && !pending){tone="failed";headline=`${this._tr("panel.failed")} ${failed}/${total}`;}
    else if(suppressed===total){tone="suppressed";headline=`${this._tr("panel.suppressed")} ${suppressed}/${total}`;}
    else if(pending===total){tone="pending";headline=`${this._tr("panel.pending")} ${pending}/${total}`;}
    else if(sent===total){tone="sent";headline=`${this._tr("panel.sent")} ${sent}/${total}`;}
    else {tone=failed?"failed":sent?"sent":suppressed?"suppressed":"pending";headline=`${this._tr("panel.sent")} ${sent}/${total}`;}
    const detail=[];
    if(failed && !(failed===total && !sent && !suppressed && !pending))detail.push(`${this._tr("panel.failed")}: ${failed}`);
    if(suppressed && suppressed!==total)detail.push(`${this._tr("panel.suppressed")}: ${suppressed}`);
    if(pending && pending!==total)detail.push(`${this._tr("panel.pending")}: ${pending}`);
    const routeReasons=(message?.delivery_routes||[]).map(route=>route.suppression_reason?this._notificationSuppressionLabel(route.suppression_reason):(route.error||"")).filter(Boolean);
    const uniqueReasons=[...new Set(routeReasons)];
    return `<div class="notification-message-delivery"><span class="notification-message-delivery-state notification-message-delivery-${tone}"><span class="notification-status-dot" aria-hidden="true"></span><span>${this._escape(headline)}</span></span>${detail.length?`<small>${this._escape(detail.join(" · "))}</small>`:""}${uniqueReasons.length?`<small class="notification-message-delivery-reason">${this._escape(uniqueReasons.join(" · "))}</small>`:""}</div>`;
  }

  _notificationExecutionModeHtml(mode) {
    const normalized=String(mode || "DRY_RUN").toUpperCase() === "REAL" ? "REAL" : "DRY_RUN";
    const tone=normalized === "REAL" ? "real" : "dry-run";
    return `<span class="notification-execution-mode notification-execution-mode-${tone}"><span class="notification-mode-dot" aria-hidden="true"></span><span>${this._escape(this._executionModeLabel(normalized))}</span></span>`;
  }

  _notificationMessagesHtml() {
    const rows=this._notificationData?.messages||[];
    if(!rows.length)return `<div class="empty compact">${this._tr("panel.notification_log_is_empty")}</div>`;
    const body=rows.map(x=>{
      const id=String(x.event_id||"");
      const pushBody=String(x.message||"");
      return `<tr class="notification-message-row" data-event-id="${this._escape(id)}" tabindex="0"><td data-label="${this._tr("panel.time")}">${this._formatDateTime(x.created_at)}</td><td data-label="${this._tr("panel.message")}"><b>${this._escape(x.title||this._notificationEventLabel(x.event_type))}</b>${pushBody?`<div class="notification-message-push-body">${this._escape(pushBody)}</div>`:""}</td><td class="notification-message-route-cell" data-label="${this._tr("panel.recipient_channel")}">${this._notificationMessageRoutesHtml(x)}</td><td class="notification-message-delivery-cell" data-label="${this._tr("panel.delivery")}">${this._notificationMessageDeliverySummaryHtml(x)}</td><td class="notification-message-mode-cell" data-label="${this._tr("panel.execution_mode")}">${this._notificationExecutionModeHtml(x.execution_mode||"REAL")}</td><td data-label="${this._tr("panel.event")}">${this._escape(this._notificationEventLabel(x.event_type))}</td></tr>`;
    }).join("");
    return `<div class="table-wrap notification-message-list-wrap"><table class="source-table mobile-card-table notification-message-list"><thead><tr><th>${this._tr("panel.time")}</th><th>${this._tr("panel.message")}</th><th>${this._tr("panel.recipient_channel")}</th><th>${this._tr("panel.delivery")}</th><th>${this._tr("panel.execution_mode")}</th><th>${this._tr("panel.event")}</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }

  _notificationHistoryHtml() {
    const rows=this._notificationData?.history||[];
    if(!rows.length) return `<div class="empty compact">${this._tr("panel.notification_log_is_empty")}</div>`;
    const body=rows.map(x=>{
      const id=String(x.delivery_id||"");
      const selected=id&&id===this._notificationHistorySelectedId;
      const main=`<tr class="notification-history-row ${selected?"selected":""}" data-delivery-id="${this._escape(id)}" tabindex="0"><td class="history-time" data-label="${this._tr("panel.time")}">${this._formatDateTime(x.sent_at||x.created_at)}</td><td class="history-event" data-label="${this._tr("panel.event")}">${this._escape(this._notificationEventLabel(x.event_type))}</td><td data-label="${this._tr("panel.execution_mode")}">${this._executionModeHtml(x.execution_mode||"DRY_RUN")}</td><td class="history-recipient" data-label="${this._tr("panel.recipient_channel")}"><div class="recipient-channel-stack"><b>${this._escape(x.recipient_name||"")}</b><small>${this._escape(this._notificationChannelDisplayName(x.channel_name,x.target,x.target_kind))}</small></div></td><td class="history-status" data-label="${this._tr("panel.result")}"><span class="ui-chip status-badge support support-${x.status==="sent"?"detected":x.status==="failed"?"conflict":"unverified"}">${this._escape(this._notificationDeliveryStatusLabel(x.status))}</span></td><td class="history-reason" data-label="${this._tr("panel.reason_error")}">${this._escape(x.suppression_reason?this._notificationSuppressionLabel(x.suppression_reason):(x.error||this._notificationDeliveryOperationLabel(x.delivery_operation)))}</td></tr>`;
      const detail=selected?`<tr class="notification-history-detail" data-delivery-detail="${this._escape(id)}"><td colspan="6">${this._notificationHistoryDetailHtml(x)}</td></tr>`:"";
      return main+detail;
    }).join("");
    return `<div class="table-wrap notification-history-wrap"><table class="source-table notification-history"><thead><tr><th>${this._tr("panel.time")}</th><th>${this._tr("panel.event")}</th><th>${this._tr("panel.execution_mode")}</th><th>${this._tr("panel.recipient_channel")}</th><th>${this._tr("panel.result")}</th><th>${this._tr("panel.reason_error")}</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }

  _notificationPresenceSearchHtml(selected) {
    const presence=this._notificationData?.candidates?.presence_entities||[];
    const selectedText=String(selected||"");
    const known=new Set(presence.map(x=>String(x.entity_id)));
    const items=presence.map((x)=>({
      value:String(x.entity_id),
      label:`${x.name||x.entity_id} · ${x.state||"—"}`,
    }));
    if(selectedText&&!known.has(selectedText)) items.unshift({value:selectedText,label:`⚠ ${selectedText}`});
    items.unshift({value:"",label:this._tr("panel.not_set")});
    return this._searchSelectHtml(items,selectedText,'data-notification-recipient-field="presence_entity_id"',this._tr("panel.search_person_or_device_tracker"));
  }

  _notificationPresenceMeta(rawState) {
    const state=String(rawState||"").trim().toLowerCase();
    if(state==="home") return {label:this._tr("panel.home"),tone:"home"};
    if(state==="unavailable") return {label:this._tr("panel.unavailable"),tone:"unavailable"};
    if(!state || state==="unknown") return {label:this._tr("panel.unknown_12049ad"),tone:"unknown"};
    // Home Assistant person/device_tracker custom zone states are operationally
    // "away" for Vacuum Schedule routing, just like not_home.
    return {label:this._tr("panel.away"),tone:"away"};
  }

  _notificationPresenceBadgeHtml(rawState) {
    const meta=this._notificationPresenceMeta(rawState);
    return `<span class="ui-chip status-badge presence-badge presence-${meta.tone}">${this._escape(meta.label)}</span>`;
  }

  _notificationRecipientLanguageLabel(value) {
    const language=String(value||"default").toLowerCase();
    if(language==="ru") return this._tr("common.language.ru");
    if(language==="uk") return this._tr("common.language.uk");
    if(language==="en") return this._tr("common.language.en");
    return this._tr("panel.language_default");
  }

  _notificationPresencePolicyLabel(value) {
    const labels={
      always:this._tr("panel.always"),
      home_only:this._tr("panel.only_when_home"),
      away_only:this._tr("panel.only_when_away"),
      disabled:this._tr("panel.disabled_ba65a5a"),
    };
    return labels[String(value||"always")]||String(value||"—");
  }

  _notificationChannelFilterLabel(value) {
    const labels={
      all:this._tr("panel.all_eligible"),
      attention_only:this._tr("panel.attention_only"),
      error_only:this._tr("panel.errors_only"),
      custom:this._tr("panel.custom"),
    };
    return labels[String(value||"all")]||String(value||"—");
  }

  _notificationRecipientTableHtml(settings) {
    const recipients=settings?.recipients||[];
    const presence=this._notificationData?.candidates?.presence_entities||[];
    const presenceById=new Map(presence.map(x=>[String(x.entity_id),x]));
    const channelCandidates=this._notificationData?.candidates?.channels||[];
    const candidateByTarget=new Map(channelCandidates.map(x=>[`${x.target_kind||"service"}|${x.target}`,x]));
    if(!recipients.length) return `<div class="empty">${this._tr("panel.no_recipients_configured")}</div>`;
    const rows=recipients.map((r)=>{
      const presenceInfo=r.presence_entity_id?presenceById.get(String(r.presence_entity_id)):null;
      const presenceName=r.presence_entity_id?(presenceInfo?.name||r.presence_entity_id):this._tr("panel.not_set_7c11b6a");
      const presenceState=r.presence_entity_id?(r.presence_state||presenceInfo?.state||null):null;
      const channels=r.channels||[];
      const channelRows=channels.map(c=>{
        const candidate=candidateByTarget.get(`${c.target_kind||"service"}|${c.target}`);
        const transport=this._notificationTransportLabel(candidate?.transport_type||c.transport_type);
        const name=candidate?(candidate.name||transport):(c.name||transport);
        const target=String(c.target||"");
        const disabled=c.enabled===false;
        return `<div class="notification-recipient-channel-line ${disabled?"disabled":""}"><b>${this._escape(name)}</b><small>${this._escape(transport)}${target?` · ${this._escape(target)}`:""}</small></div>`;
      }).join("");
      const ruleRows=channels.map(c=>{
        const candidate=candidateByTarget.get(`${c.target_kind||"service"}|${c.target}`);
        const capability=candidate?.supports_actionable||c.actionable?this._tr("panel.action_buttons"):this._tr("panel.text_delivery");
        const parts=c.enabled===false
          ? [this._tr("panel.disabled_ba65a5a")]
          : [this._notificationPresencePolicyLabel(c.presence_policy),this._notificationChannelFilterLabel(c.event_filter),capability];
        return `<div class="notification-recipient-rule-line"><span>${parts.map(x=>this._escape(x)).join(" · ")}</span></div>`;
      }).join("");
      const enabledChannels=channels.filter(c=>c.enabled!==false).length;
      return `<tr>
        <td data-label="${this._tr("panel.recipient")}"><b>${this._escape(r.name||this._tr("panel.recipient"))}</b></td>
        <td data-label="${this._tr("panel.notification_language")}"><b>${this._escape(this._notificationRecipientLanguageLabel(r.language))}</b></td>
        <td data-label="${this._tr("panel.presence")}"><span>${this._escape(presenceName)}</span>${r.presence_entity_id?`<small class="notification-presence-detail"><span>${this._escape(r.presence_entity_id)}</span>${this._notificationPresenceBadgeHtml(presenceState)}</small>`:""}</td>
        <td data-label="${this._tr("panel.channels")}"><b>${channels.length}</b>${channelRows||`<small>${this._tr("panel.no_channels_attached")}</small>`}</td>
        <td data-label="${this._tr("panel.delivery_rules")}">${ruleRows||"—"}</td>
        <td data-label="${this._tr("panel.status")}"><span class="ui-chip status-badge support support-${r.enabled!==false?"detected":"disabled"}">${r.enabled!==false?this._tr("panel.enabled"):this._tr("panel.disabled_ba65a5a")}</span>${channels.length?`<small>${this._tr("panel.channels")}: ${enabledChannels}/${channels.length}</small>`:""}</td>
        <td class="notification-recipient-actions mobile-actions-cell" data-label="${this._tr("panel.actions")}"><div class="table-actions-inner"><button class="ghost icon-only compact-icon-action notification-edit-recipient" data-recipient-id="${this._escape(r.recipient_id)}" title="${this._tr("panel.edit_recipient")}" aria-label="${this._tr("panel.edit_recipient")}">${this._mdi("pencil-outline")}</button><button class="danger icon-only compact-icon-action notification-delete-recipient" data-recipient-id="${this._escape(r.recipient_id)}" data-recipient-name="${this._escape(r.name||"")}" title="${this._tr("panel.delete_recipient")}" aria-label="${this._tr("panel.delete_recipient")}">${this._mdi("delete-outline")}</button></div></td>
      </tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="source-table notification-recipient-table mobile-card-table"><thead><tr><th>${this._tr("panel.recipient")}</th><th>${this._tr("panel.notification_language")}</th><th>${this._tr("panel.presence")}</th><th>${this._tr("panel.channels")}</th><th>${this._tr("panel.delivery_rules")}</th><th>${this._tr("panel.status")}</th><th>${this._tr("panel.actions")}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }


  _notificationTransportLabel(value) {
    const key = `common.transport.${value}`;
    const translated = this._tr(key);
    return translated === key ? (value || this._tr("panel.unknown_8a51c33")) : translated;
  }

  _notificationChannelDisplayName(channelName, target, targetKind = null) {
    const candidates=this._notificationData?.candidates?.channels||[];
    const exact=candidates.find(x=>String(x.target||"")===String(target||"") && (!targetKind || String(x.target_kind||"service")===String(targetKind)));
    const byTarget=exact||candidates.find(x=>String(x.target||"")===String(target||""));
    return byTarget?.name || channelName || target || this._tr("panel.unknown_8a51c33");
  }

  _notificationDeliveryStatusLabel(value) {
    if (!value) return "—";
    const key = `common.delivery_status.${value}`;
    const translated = this._tr(key);
    return translated === key ? value : translated;
  }

  _notificationSuppressionLabel(value) {
    if (!value) return "";
    const key = `common.suppression.${value}`;
    const translated = this._tr(key);
    return translated === key ? value.replaceAll("_", " ") : translated;
  }

  _notificationDeliveryOperationLabel(value) {
    if (!value) return "—";
    const key = `common.delivery_operation.${value}`;
    const translated = this._tr(key);
    return translated === key ? value.replaceAll("_", " ") : translated;
  }

  _notificationChannelSearchHtml(channel, candidates) {
    const match=(candidates||[]).find(x=>String(x.target_kind)===String(channel.target_kind||"service")&&String(x.target)===String(channel.target||""));
    const current=channel.target
      ? (match
        ? `${match.target_kind}|${match.target}|${match.transport_type}`
        : `${channel.target_kind||"service"}|${channel.target}|${channel.transport_type||"generic_notify"}`)
      : "";
    const items=(candidates||[]).map((x)=>{
      const transport=this._notificationTransportLabel(x.transport_type);
      const friendly=String(x.name||"").trim();
      return {
        value:`${x.target_kind}|${x.target}|${x.transport_type}`,
        label:friendly||transport,
        detail:x.target,
        badge:transport,
        display:friendly?`${friendly} · ${transport}`:`${transport} · ${x.target}`,
      };
    });
    const known=(candidates||[]).some(x=>String(x.target_kind)===String(channel.target_kind||"service")&&String(x.target)===String(channel.target||""));
    if(channel.target&&!known) items.unshift({value:current,label:this._tr("panel.unknown_ha_notify_channel"),detail:channel.target,badge:"⚠",display:`⚠ ${channel.target}`});
    items.unshift({value:"",label:this._tr("panel.select_a_ha_notify_channel")});
    return this._searchSelectHtml(items,current,'data-notification-channel-field="target_combo"',this._tr("panel.search_notify_channel_by_name_or_entity_id"));
  }

  _notificationRecipientEditorHtml() {
    const r=this._notificationRecipientDraft;
    const data=this._notificationData;
    if(!r||!data) return "";
    const candidates=data.candidates?.channels||[];
    const channels=(r.channels||[]).map((c,ci)=>{
      const candidate=candidates.find(x=>String(x.target_kind)===String(c.target_kind||"service")&&String(x.target)===String(c.target||""));
      const effectiveTransport=candidate?.transport_type||c.transport_type||"generic_notify";
      const opts=c.options||{};
      const providerRichPayload=c.target_kind!=="entity";
      const transportHelp=effectiveTransport==="mobile_app"
        ? (providerRichPayload
          ? this._tr("panel.every_allowed_event_is_sent_as_a_separate_push_duplicate_business_delive")
          : this._tr("panel.this_notify_entity_accepts_basic_text_only_without_companion_app_action_"))
        : effectiveTransport==="pushover"
          ? (providerRichPayload
            ? this._tr("panel.pushover_receives_text_a_vacuum_schedule_link_and_the_priority_sound_tt")
            : this._tr("panel.a_generic_notify_entity_sends_only_title_and_text_pushover_specific_opti"))
          : effectiveTransport==="generic_notify"
            ? this._tr("panel.safe_fallback_title_and_text_only_without_transport_specific_fields_or_i")
            : "";
      const pushoverAdvanced=effectiveTransport==="pushover"&&providerRichPayload?`<details class="transport-options"><summary>${this._tr("panel.pushover_options")}</summary><div class="form-grid">
        <label class="field wide"><span>${this._tr("panel.pushover_devices_comma_separated")}</span><input data-pushover-option="targets" value="${this._escape(Array.isArray(opts.targets)?opts.targets.join(", "):(opts.targets||""))}" placeholder="iphone, tablet"></label>
        <label class="field"><span>${this._tr("panel.priority")}</span><select data-pushover-option="priority"><option value="">${this._tr("panel.default")}</option>${[-2,-1,0,1,2].map(v=>`<option value="${v}" ${String(opts.priority??"")===String(v)?"selected":""}>${v}</option>`).join("")}</select></label>
        <label class="field"><span>${this._tr("panel.sound")}</span><input data-pushover-option="sound" value="${this._escape(opts.sound||"")}" placeholder="default"></label>
        <label class="field"><span>TTL, ${this._tr("panel.sec")}</span><input data-pushover-option="ttl" type="number" min="0" value="${this._escape(opts.ttl??"")}"></label>
        <label class="field"><span>${this._tr("panel.emergency_retry_sec")}</span><input data-pushover-option="retry" type="number" min="30" value="${this._escape(opts.retry??"")}"></label>
        <label class="field"><span>${this._tr("panel.emergency_expire_sec")}</span><input data-pushover-option="expire" type="number" min="0" max="10800" value="${this._escape(opts.expire??"")}"></label>
      </div><div class="muted">${this._tr("panel.priority_2_requires_retry_and_expire")}</div></details>`:"";
      const testDisabled=this._notificationRecipientIsNew||this._notificationRecipientDirty;
      return `<div class="channel-card" data-channel-index="${ci}">
        <div class="channel-head"><div><b>${this._escape(candidate?.name||(candidate?this._notificationTransportLabel(effectiveTransport):c.name)||this._notificationTransportLabel(effectiveTransport)||this._tr("panel.ha_notify_channel"))}</b><small>${this._escape(this._notificationTransportLabel(effectiveTransport))}${c.target?` · ${this._escape(c.target)}`:""}</small></div><div class="room-actions"><button class="ghost small notification-test-channel" ${testDisabled?"disabled":""} title="${testDisabled?this._tr("panel.save_recipient_changes_first"):this._tr("panel.send_test_notification")}">${this._mdi("send-check-outline")}<span>${this._tr("panel.test")}</span></button><button class="danger small icon-only compact-icon-action notification-delete-channel" title="${this._tr("panel.detach_channel")}" aria-label="${this._tr("panel.detach_channel")}">${this._mdi("link-off")}</button></div></div>
        <div class="form-grid notification-channel-grid">
          <div class="field wide"><span>${this._tr("panel.ha_notify_channel")}</span>${this._notificationChannelSearchHtml(c,candidates)}<small class="help">${this._tr("panel.the_channel_is_created_and_configured_in_home_assistant_vacuum_schedule")}</small></div>
          <label class="field"><span>${this._tr("panel.use_when")}</span><select data-notification-channel-field="presence_policy">${this._presenceOptions(c.presence_policy||"always")}</select></label>
          <label class="field"><span>${this._tr("panel.messages")}</span><select data-notification-channel-field="event_filter">${this._channelFilterOptions(c.event_filter||"all")}</select></label>
          <label class="switch-row"><input data-notification-channel-field="enabled" type="checkbox" ${c.enabled!==false?"checked":""}><span>${this._tr("panel.binding_enabled")}</span></label>
          <div class="field"><span>${this._tr("panel.channel_capabilities")}</span><b>${this._escape(candidate?.supports_actionable?this._tr("panel.action_buttons"):this._tr("panel.text_delivery"))}</b>${candidate?.supports_updates?`<small class="help">${this._tr("panel.the_job_notification_is_updated_replaced_without_stacking_duplicates")}</small>`:""}${transportHelp?`<small class="help">${this._escape(transportHelp)}</small>`:""}${effectiveTransport==="telegram"&&candidate?.telegram_mode==="broadcast"?`<small class="help warning">${this._tr("panel.telegram_broadcast_is_send_only_so_action_buttons_are_unavailable_use_po")}</small>`:""}</div>
        </div>
        ${c.event_filter==="custom"?`<div class="notification-event-checks">${["prewarning","wait_enter","wait_reminder","started","finished","start_forecast"].map(v=>`<label class="inline-check"><input type="checkbox" data-notification-custom-event="${v}" ${(c.custom_events||[]).includes(v)?"checked":""}> ${this._escape(this._notificationEventLabel(v))}</label>`).join("")}</div>`:""}
        ${pushoverAdvanced}
      </div>`;
    }).join("");
    return `<section class="entry-card notification-recipient-editor"><div class="entry-header"><div><h2>${this._notificationRecipientIsNew?this._tr("panel.new_recipient"):this._tr("panel.edit_recipient_6ab8221")}</h2><div class="muted">${this._tr("panel.a_recipient_represents_one_person_configure_presence_and_bindings_to_exi")}</div></div><button class="ghost icon-only notification-recipient-cancel" title="${this._tr("panel.close")}" aria-label="${this._tr("panel.close")}">${this._mdi("close")}</button></div>
      <div class="form-grid"><label class="field"><span>${this._tr("panel.recipient_name")}</span><input data-notification-recipient-field="name" value="${this._escape(r.name||"")}"></label><div class="field"><span>${this._tr("panel.user_presence")}</span>${this._notificationPresenceSearchHtml(r.presence_entity_id||"")}</div><label class="field"><span>${this._tr("panel.notification_language")}</span><select data-notification-recipient-field="language"><option value="default" ${(r.language||"default")==="default"||(r.language||"")==="auto"?"selected":""}>${this._tr("panel.language_default")}</option><option value="ru" ${(r.language||"")==="ru"?"selected":""}>${this._tr("common.language.ru")}</option><option value="uk" ${(r.language||"")==="uk"?"selected":""}>${this._tr("common.language.uk")}</option><option value="en" ${(r.language||"")==="en"?"selected":""}>${this._tr("common.language.en")}</option></select><small>${this._tr("panel.notification_language_help")}</small></label><label class="switch-row"><input data-notification-recipient-field="enabled" type="checkbox" ${r.enabled!==false?"checked":""}><span>${this._tr("panel.recipient_enabled")}</span></label></div>
      <div class="subsection-header"><div><h4>${this._tr("panel.delivery_channels")}</h4><div class="muted">${this._tr("panel.select_existing_home_assistant_notify_channels_the_same_ha_channel_may_b")}</div></div><button class="ghost small notification-add-channel">${this._mdi("bell-plus-outline")}<span>${this._tr("panel.attach")}</span></button></div>
      <div class="channel-list">${channels||`<div class="empty compact">${this._tr("panel.no_channels_attached")}</div>`}</div>
      <div class="editor-actions"><button class="ghost notification-recipient-cancel">${this._mdi("close")}<span>${this._tr("panel.cancel")}</span></button><button class="primary notification-recipient-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div>
    </section>`;
  }

  _notificationsHtml() {
    const entryId=this._selectEntry(this._schedulerEntryId,false);
    const data=this._notificationData;
    if(!entryId || !data) return `${this._entrySelectorHtml()}<div class="empty">${this._tr("panel.loading_notification_settings")}</div>`;
    if(this._notificationMessageSelectedId) return `${this._entrySelectorHtml()}${this._notificationMessageDetailHtml()}`;
    const settings=data.settings||{};
    if(!this._notificationGlobalDraft)this._syncNotificationGlobalDraft(true);
    const globalDraft=this._notificationGlobalDraft||{enabled:settings.enabled!==false,dry_run_delivery:String(settings.dry_run_delivery||"send"),preset:String(settings.preset||"balanced"),policy:this._cloneData(settings.policy||this._notificationPresetPolicy(settings.preset||"balanced"))};
    const policy=globalDraft.policy||this._notificationPresetPolicy(globalDraft.preset||"balanced");
    return `${this._entrySelectorHtml()}
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.global_notification_policy")}</h2><div class="muted">${this._tr("panel.the_policy_first_decides_whether_an_event_is_noteworthy_each_channel_the")}</div></div><label class="switch-row"><input id="notification-enabled" type="checkbox" ${globalDraft.enabled!==false?"checked":""}><span>${this._tr("panel.notifications_enabled")}</span></label></div>
      <div class="form-grid">
        <label class="field"><span>${this._tr("panel.preset")}</span><select id="notification-preset">${[["minimal",this._tr("panel.minimal")],["balanced",this._tr("panel.balanced")],["detailed",this._tr("panel.detailed")],["custom",this._tr("panel.custom_52b35b9")]].map(([v,l])=>`<option value="${v}" ${globalDraft.preset===v?"selected":""}>${this._escape(l)}</option>`).join("")}</select></label>
        <label class="field"><span>${this._tr("panel.dry_run_notifications")}</span><select id="notification-dry-run-delivery"><option value="send" ${String(globalDraft.dry_run_delivery||"send")==="send"?"selected":""}>${this._tr("panel.send")}</option><option value="log_only" ${String(globalDraft.dry_run_delivery||"")==="log_only"?"selected":""}>${this._tr("panel.log_only")}</option></select></label>
        <div class="field wide notification-policy-message-type"><b>${this._tr("panel.prewarning_834cdf5")}</b><small class="help">${this._tr("panel.prewarning_policy_help")}</small></div>
        <label class="field"><span>${this._tr("panel.mode")}</span><select data-notification-policy="prewarning_mode">${this._notificationModeOptions("prewarning_mode",policy.prewarning_mode)}</select></label>
        <div class="field wide notification-policy-message-type"><b>${this._tr("panel.wait_entry")}</b><small class="help">${this._tr("panel.wait_entry_policy_help")}</small></div>
        <label class="field"><span>${this._tr("panel.mode")}</span><select data-notification-policy="wait_enter_mode">${this._notificationModeOptions("wait_enter_mode",policy.wait_enter_mode)}</select></label>
        <label class="field"><span>${this._tr("panel.wait_delay_seconds")}</span><input data-notification-policy="wait_delay_seconds" type="number" min="0" value="${this._escape(policy.wait_delay_seconds??120)}"></label>
        <label class="switch-row"><input data-notification-policy="wait_reminder_enabled" type="checkbox" ${policy.wait_reminder_enabled!==false?"checked":""}><span>${this._tr("panel.send_wait_reminders")}</span></label>
        <label class="field"><span>${this._tr("panel.reminder_interval_minutes")}</span><input data-notification-policy-minutes="wait_reminder_interval_seconds" type="number" min="1" value="${this._escape(Math.round((policy.wait_reminder_interval_seconds||1800)/60))}"></label>
        <label class="field"><span>${this._tr("panel.maximum_reminders")}</span><input data-notification-policy="wait_reminder_max_count" type="number" min="0" value="${this._escape(policy.wait_reminder_max_count??3)}"></label>
        <div class="field wide notification-policy-message-type"><b>${this._tr("panel.cleaning_start")}</b><small class="help">${this._tr("panel.cleaning_start_policy_help")}</small></div>
        <label class="field"><span>${this._tr("panel.mode")}</span><select data-notification-policy="start_mode">${this._notificationModeOptions("start_mode",policy.start_mode)}</select></label>
        <div class="field wide notification-policy-message-type"><b>${this._tr("panel.cleaning_result")}</b><small class="help">${this._tr("panel.cleaning_result_policy_help")}</small></div>
        <label class="field"><span>${this._tr("panel.mode")}</span><select data-notification-policy="finish_mode">${this._notificationModeOptions("finish_mode",policy.finish_mode)}</select></label>
        <div class="field wide notification-policy-message-type"><b>${this._tr("panel.start_forecast")}</b><small class="help">${this._tr("panel.forecast_help")}</small></div>
        <label class="field"><span>${this._tr("panel.mode")}</span><select data-notification-policy="start_forecast_mode">${this._notificationModeOptions("start_forecast_mode",policy.start_forecast_mode||"off")}</select></label>
        <label class="field"><span>${this._tr("panel.forecast_interval")}</span><input data-notification-policy-minutes="start_forecast_interval_seconds" type="number" min="1" value="${this._escape(Math.round((policy.start_forecast_interval_seconds??600)/60))}"></label>
        <label class="field"><span>${this._tr("panel.forecast_deadline")}</span><input data-notification-policy="start_forecast_deadline_minutes" type="number" min="0" value="${this._escape(policy.start_forecast_deadline_minutes??10)}"><small class="help">${this._tr("panel.forecast_zero_off")}</small></label>
      </div><div class="editor-actions"><button class="primary notification-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></section>
      <section class="entry-card notification-users-card"><div class="entry-header notification-users-header"><div><h2>${this._tr("panel.recipients")}</h2><div class="muted">${this._tr("panel.a_recipient_is_a_person_attach_existing_ha_notify_channels_and_configure")}</div></div><button class="primary notification-add-recipient">${this._mdi("account-plus-outline")}<span>${this._tr("panel.add")}</span></button></div>
      ${this._notificationRecipientTableHtml(settings)}</section>
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.notification_routing_test")}</h2><div class="muted">${this._tr("panel.creates_a_synthetic_notification_event_without_changing_a_job_recipient_")}</div></div></div><div class="button-row notification-test-events">${["prewarning","wait_enter","wait_reminder","started","finished","start_forecast"].map(v=>`<button class="ghost notification-test-event" data-event="${v}">${this._mdi("bell-ring-outline")}<span>${this._escape(this._notificationEventLabel(v))}</span></button>`).join("")}</div>${this._notificationRoutingTestResultHtml()}</section>
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.notification_archive")}</h2><div class="muted">${this._tr("panel.notification_archive_help")}</div></div><button class="ghost notification-clear-history">${this._mdi("delete-sweep-outline")}<span>${this._tr("panel.clear")}</span></button></div>${this._notificationMessagesHtml()}</section>`;
  }

  _settingLabel(key) {
    if (String(key || "").startsWith("capability.")) {
      const name = String(key).slice(11).replaceAll("_", " ");
      return `${this._tr("panel.capability")}: ${name}`;
    }
    const translationKey = `common.binding.${key}`;
    const translated = this._tr(translationKey);
    return translated === translationKey ? key : translated;
  }

  _supportLabel(status) {
    if (!status) return "—";
    const key = `common.support.${status}`;
    const translated = this._tr(key);
    return translated === key ? status : translated;
  }

  _modeLabel(mode) {
    const key = `common.binding_mode.${mode}`;
    const translated = this._tr(key);
    return translated === key ? mode : translated;
  }

  _searchSelectHtml(options, selected = "", hiddenAttrs = "", placeholder = "") {
    const value = String(selected || "");
    const selectedItem = options.find((item) => String(item.value) === value);
    const display = selectedItem?.display || selectedItem?.label || (value ? `⚠ ${value}` : "");
    const rows = options.map((item) => {
      const label = String(item.label || item.value || "");
      const detail = String(item.detail || (String(item.value||"") !== label ? item.value : "") || "");
      const badge = String(item.badge || "");
      const rowDisplay = String(item.display || label);
      const search = `${label} ${detail} ${badge} ${item.value || ""}`.toLowerCase();
      return `<button type="button" class="search-select-option" data-value="${this._escape(item.value)}" data-label="${this._escape(rowDisplay)}" data-search="${this._escape(search)}"><span class="search-select-option-main"><span>${this._escape(label)}</span>${badge?`<span class="ui-chip status-badge search-select-badge">${this._escape(badge)}</span>`:""}</span>${detail ? `<small>${this._escape(detail)}</small>` : ""}</button>`;
    }).join("");
    return `<div class="search-select"><input class="search-select-input" type="text" autocomplete="off" placeholder="${this._escape(placeholder || this._tr("panel.type_to_search"))}" value="${this._escape(display)}" data-selected-label="${this._escape(display)}"><input class="search-select-value" type="hidden" value="${this._escape(value)}" ${hiddenAttrs}><div class="search-select-menu">${rows || `<div class="search-select-empty">${this._tr("panel.no_options")}</div>`}</div></div>`;
  }


  _entitySearchHtml(selected = "", hiddenAttrs = "") {
    const entities = this._settingsData?.entities || [];
    const selectedText = String(selected || "");
    const known = new Set(entities.map((x) => x.entity_id));
    const items = entities.map((item) => ({
      value: item.entity_id,
      label: item.name && item.name !== item.entity_id ? `${item.name} · ${item.entity_id}` : item.entity_id,
    }));
    if (selectedText && !known.has(selectedText)) items.unshift({value:selectedText,label:`⚠ ${selectedText}`});
    items.unshift({value:"",label:this._tr("panel.not_selected")});
    return this._searchSelectHtml(items, selectedText, hiddenAttrs, this._tr("panel.search_by_name_or_entity_id"));
  }


  _zoneTargetEditor(r) {
    if ((r.robot_target_type || "segment") === "zone") {
      return `<label class="field wide"><span>${this._tr("panel.robot_zone_coordinates")}</span><textarea id="room-zone-target" rows="3" placeholder="[x1,y1,x2,y2]">${this._escape(r.robot_target_id || "")}</textarea></label>`;
    }
    const segments=this._settingsData?.segments || [];
    const selected=String(r.robot_target_id || "");
    const known=new Set(segments.map(x=>String(x.value)));
    const opts=segments.map((a)=>({value:String(a.value),label:`${a.label} · ${a.value}`}));
    if(selected && !known.has(selected)) opts.unshift({value:selected,label:`⚠ ${selected}`});
    opts.unshift({value:"",label:this._tr("panel.select")});
    return `<div class="field wide"><span>${this._tr("panel.robot_segment")}</span>${this._searchSelectHtml(opts,selected,'id="room-target-single"',this._tr("panel.search_segment_by_name_or_id"))}${!segments.length?`<small class="help">${this._tr("panel.the_vacuum_did_not_return_a_segment_list_a_saved_id_is_still_shown_for_d")}</small>`:""}</div>`;
  }


  _conditionEditor(cond, kind, index, pathIndex=null, conditionIndex=null) {
    const attrs=pathIndex===null ? `data-room-source-kind="${kind}" data-index="${index}"` : `data-path-index="${pathIndex}" data-condition-index="${conditionIndex}"`;
    const prefix=pathIndex===null ? "data-source-field" : "data-condition-field";
    const op=cond.operator || "equals";
    return `<div class="condition-row" ${attrs}>
      <div class="field"><span>${this._tr("panel.entity_0e084dd")}</span>${this._entitySearchHtml(cond.entity_id, `${prefix}="entity_id"`)}</div>
      <label class="field"><span>${this._tr("panel.attribute_optional")}</span><input ${prefix}="attribute" type="text" value="${this._escape(cond.attribute||"")}"></label>
      <label class="field"><span>${this._tr("panel.blocking_condition")}</span><select ${prefix}="operator"><option value="equals" ${op==="equals"?"selected":""}>=</option><option value="not_equals" ${op==="not_equals"?"selected":""}>≠</option><option value="above" ${op==="above"?"selected":""}>&gt;</option><option value="below" ${op==="below"?"selected":""}>&lt;</option><option value="in" ${op==="in"?"selected":""}>${this._tr("panel.in_list")}</option></select></label>
      <label class="field"><span>${this._tr("panel.value")}</span><input ${prefix}="value" type="text" value="${this._escape(cond.value ?? "on")}"></label>
      <button class="danger small icon-only compact-icon-action ${pathIndex===null?"remove-source":"remove-condition"}" ${attrs} title="${this._tr("panel.remove")}">${this._mdi("delete-outline")}</button>
    </div>`;
  }

  _sourceGroupHtml(title, kind, rows) {
    return `<div class="settings-subsection"><div class="subsection-header"><div><h4>${this._escape(title)}</h4><div class="muted">${this._tr("panel.any_matching_sensor_blocks_this_cleaning_zone_unknown_unavailable_is_blo")}</div></div><button class="ghost icon-only compact-icon-action add-source" data-room-source-kind="${kind}" title="${this._tr("panel.add_sensor")}">${this._mdi("plus")}</button></div>${(rows||[]).map((c,i)=>this._conditionEditor(c,kind,i)).join("") || `<div class="empty compact">${this._tr("panel.no_sensors_configured")}</div>`}</div>`;
  }


  _pathsHtml(paths) {
    const items=paths||[];
    return `<div class="settings-subsection"><div class="subsection-header"><div><h4>${this._tr("panel.access_paths")}</h4><div class="muted">${this._tr("panel.conditions_inside_a_path_use_and_alternative_paths_use_or_with_no_paths_")}</div></div><button class="ghost add-path">${this._mdi("routes")}<span>${this._tr("panel.add_path")}</span></button></div>
      ${items.map((path,pi)=>`<div class="path-card"><div class="path-header"><input data-path-name="${pi}" type="text" value="${this._escape(path.name||`${this._tr("panel.path")} ${pi+1}`)}"><button class="danger small icon-only compact-icon-action remove-path" data-path-index="${pi}" title="${this._tr("panel.remove_path")}">${this._mdi("delete-outline")}</button></div>${(path.conditions||[]).map((c,ci)=>this._conditionEditor(c,"",0,pi,ci)).join("") || `<div class="empty compact">${this._tr("panel.an_empty_path_is_not_considered_open_add_conditions")}</div>`}<button class="ghost add-condition" data-path-index="${pi}">${this._mdi("plus")}<span>${this._tr("panel.condition")}</span></button></div>`).join("") || `<div class="empty compact">${this._tr("panel.no_access_paths_configured_route_accessibility_check_is_disabled")}</div>`}
    </div>`;
  }

  _roomEditorHtml() {
    const r=this._roomDraft;
    if (!r) return "";
    const until=r.disabled_until ? String(r.disabled_until).slice(0,16) : "";
    return `<section class="entry-card room-editor"><div class="entry-header"><div><h2>${r.zone_id?this._tr("panel.edit_cleaning_zone"):this._tr("panel.new_cleaning_zone")}</h2><div class="muted">${this._tr("panel.one_vacuum_schedule_cleaning_zone_maps_to_exactly_one_physical_vacuum_t")}</div></div><button class="ghost icon-only room-cancel" title="${this._tr("panel.close")}">${this._mdi("close")}</button></div>
      <div class="form-grid"><label class="field"><span>${this._tr("panel.name")}</span><input id="room-name" type="text" value="${this._escape(r.name||"")}"></label><label class="field"><span>${this._tr("panel.robot_target_type")}</span><select id="room-target-type"><option value="segment" ${(r.robot_target_type||"segment")==="segment"?"selected":""}>${this._tr("panel.segment")}</option><option value="zone" ${r.robot_target_type==="zone"?"selected":""}>${this._tr("panel.robot_zone")}</option></select></label></div>
      <div class="form-grid">${this._zoneTargetEditor(r)}<label class="field"><span>${this._tr("panel.cleaning_permission")}</span><select id="zone-control"><option value="enabled" ${(r.control||"enabled")==="enabled"?"selected":""}>${this._tr("panel.enabled_5b6e62d")}</option><option value="disabled" ${r.control==="disabled"?"selected":""}>${this._tr("panel.disabled_a94d2cb")}</option><option value="disabled_until" ${r.control==="disabled_until"?"selected":""}>${this._tr("panel.disabled_until_6e4cb8a")}</option></select></label><label class="field"><span>${this._tr("panel.disabled_until_db71475")}</span><input id="zone-disabled-until" type="datetime-local" value="${this._escape(until)}" ${r.control!=="disabled_until"?"disabled":""}></label></div>
      <div class="form-grid"><label class="field"><span>${this._tr("panel.nominal_area_m2")}</span><input id="zone-nominal-area" type="number" min="0.1" step="0.1" value="${this._escape(r.nominal_area_m2 ?? "")}" placeholder="—"><small class="help">${this._tr("panel.nominal_area_help")}</small></label><label class="field"><span>${this._tr("panel.zone_must_remain_free_sec")}</span><input id="zone-occupancy-delay" type="number" min="0" max="86400" step="1" value="${this._escape(r.occupancy_clear_delay_seconds ?? "")}" placeholder="${this._tr("panel.global")}"><small class="help">${this._tr("panel.blank_global_value_s_after_occupancy_clears_the_zone_remains_busy_until_", {p1: this._settingsData?.policy?.occupancy_clear_delay_seconds ?? 0})}</small></label><label class="field"><span>${this._tr("panel.route_must_remain_accessible_sec")}</span><input id="zone-access-delay" type="number" min="0" max="86400" step="1" value="${this._escape(r.access_stable_delay_seconds ?? "")}" placeholder="${this._tr("panel.global")}"><small class="help">${this._tr("panel.blank_global_value_s_at_least_one_complete_route_must_remain_continuousl", {p1: this._settingsData?.policy?.access_stable_delay_seconds ?? 0})}</small></label></div>
      ${this._sourceGroupHtml(this._tr("panel.presence_occupancy_sensors"),"busy_sources",r.busy_sources)}
      ${this._pathsHtml(r.access_paths)}
      ${this._roomTestResult?`<div class="test-result"><b>${this._tr("panel.current_test")}</b><pre>${this._escape(JSON.stringify(this._roomTestResult,null,2))}</pre></div>`:""}
      <div class="editor-actions">${r.zone_id?`<button class="ghost room-test">${this._mdi("flask-outline")}<span>${this._tr("panel.test_now")}</span></button>`:""}<button class="ghost room-cancel">${this._mdi("close")}<span>${this._tr("panel.cancel")}</span></button><button class="primary room-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save_zone")}</span></button></div>
    </section>`;
  }


  _forecastPolicyHtml() {
    const forecasts=(this._policyDraft||this._settingsData?.policy||{}).forecasts||{};
    const configs=[
      ["time",this._tr("panel.forecast_time")],
      ["battery",this._tr("panel.forecast_battery")],
      ["clean_water",this._tr("panel.forecast_clean_water")],
      ["dirty_water",this._tr("panel.forecast_dirty_water")],
    ];
    const card=([key,label])=>{const c=forecasts[key]||{};return `<div class="forecast-policy-card" data-forecast-policy="${key}"><div class="forecast-policy-head"><b>${this._escape(label)}</b><label class="inline-check"><input id="forecast-${key}-enabled" type="checkbox" ${c.enabled?"checked":""}> ${this._tr("panel.use_forecast")}</label></div><div class="forecast-policy-grid"><label class="field"><span>${this._tr("panel.percentile")}</span><div class="input-prefix"><span>P</span><input id="forecast-${key}-percentile" type="number" min="50" max="99" step="1" value="${this._escape(c.percentile??90)}"></div></label><label class="field"><span>Δ, %</span><input id="forecast-${key}-delta" type="number" min="0" max="500" step="0.1" value="${this._escape(c.delta_percent??10)}"></label><label class="field"><span>${this._tr("panel.forecast_period_days")}</span><input id="forecast-${key}-days" type="number" min="1" max="3650" step="1" value="${this._escape(c.lookback_days??90)}"></label></div></div>`;};
    return `<section class="entry-card forecast-policy-section"><div class="entry-header"><div><h2>${this._tr("panel.forecasting")}</h2><div class="muted">${this._tr("panel.forecasting_settings_help")}</div></div></div><div class="forecast-policy-cards">${configs.map(card).join("")}</div><div class="editor-actions"><button class="primary policy-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></section>`;
  }

  _policyHtml() {
    const p=this._policyDraft || this._settingsData?.policy || {};
    const gate=p.execution_gate || "enabled";
    const until=p.disabled_until ? String(p.disabled_until).slice(0,16) : "";
    return `<section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.schedule_execution")}</h2><div class="muted">${this._tr("panel.this_section_contains_the_global_execution_gate_start_thresholds_and_glo")}</div></div></div>
      <div class="form-grid"><label class="field"><span>${this._tr("panel.mode")}</span><select id="policy-gate"><option value="enabled" ${gate==="enabled"?"selected":""}>${this._tr("panel.enabled_10414ee")}</option><option value="disabled" ${gate==="disabled"?"selected":""}>${this._tr("panel.disabled_a787225")}</option><option value="disabled_until" ${gate==="disabled_until"?"selected":""}>${this._tr("panel.disabled_until_f0a72fd")}</option></select></label><label class="field"><span>${this._tr("panel.disabled_until_4131808")}</span><input id="policy-disabled-until" type="datetime-local" value="${this._escape(until)}" ${gate!=="disabled_until"?"disabled":""}></label>
      <label class="field"><span>${this._tr("panel.minimum_remaining_battery")}</span><input id="policy-battery" type="number" min="0" max="100" step="1" value="${this._escape(p.default_min_battery_percent??20)}"><small class="help">${this._tr("panel.minimum_remaining_battery_forecast_help")}</small></label>
      <label class="field"><span>${this._tr("panel.minimum_remaining_start_window_min")}</span><input id="policy-window" type="number" min="0" max="10080" step="1" value="${this._escape(p.minimum_start_window_minutes??0)}"><small class="help">${this._tr("panel.remaining_time_is_measured_to_the_nearest_boundary_after_which_a_new_sta")}</small></label>
      <label class="field"><span>${this._tr("panel.zone_must_remain_free_sec")}</span><input id="policy-occupancy-delay" type="number" min="0" max="86400" step="1" value="${this._escape(p.occupancy_clear_delay_seconds??0)}"><small class="help">${this._tr("panel.global_default_after_all_occupancy_sensors_clear_the_zone_remains_busy_f")}</small></label>
      <label class="field"><span>${this._tr("panel.route_must_remain_accessible_sec")}</span><input id="policy-access-delay" type="number" min="0" max="86400" step="1" value="${this._escape(p.access_stable_delay_seconds??0)}"><small class="help">${this._tr("panel.global_default_at_least_one_complete_route_must_remain_continuously_acce")}</small></label></div>
      <div class="editor-actions"><button class="primary policy-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></section>${this._forecastPolicyHtml()}`;
  }

  _bindingSingleEditorHtml(key) {
    const row=(this._settingsData?.bindings || []).find((item)=>item.key===key);
    if(!row) return `${this._entrySelectorHtml()}<section class="editor-card settings-editor-page"><div class="editor-header"><div><h2>${this._tr("panel.setting_not_found")}</h2></div><button class="ghost icon-only settings-editor-cancel" title="${this._tr("panel.close")}">${this._mdi("close")}</button></div><div class="editor-actions"><button class="ghost settings-editor-cancel">${this._mdi("arrow-left")}<span>${this._tr("panel.back")}</span></button></div></section>`;
    const mode=row.binding_mode || "auto";
    const input=row.effective_input || {};
    const auto=row.auto_candidate || {};
    const binaryResource=["dock.clean_water","dock.dirty_water","dock.detergent","mop.attached"].includes(row.key);
    const mappingText=Object.keys(row.value_mapping||{}).length ? JSON.stringify(row.value_mapping) : "";
    const selectedEntity=row.resolved_entity_id || row.entity_id || "";
    const title=`${this._tr("panel.edit")}: ${this._escape(this._settingLabel(row.key))}`;
    const help=String(row.key).startsWith("capability.")
      ? this._tr("panel.only_this_robot_capability_is_edited_other_rows_are_not_changed")
      : this._tr("panel.only_this_pre_flight_source_is_edited_do_not_use_completely_disables_thi");
    const normalState=row.normal_state || row.inferred_normal_state || auto.normal_state || "";
    const policy=this._settingsData?.policy || {};
    const simpleUse = binaryResource || row.key==="vacuum.battery_percent" || row.key==="dnd.active";
    const autoModeLabel = binaryResource ? this._tr("panel.use_if_sensor_is_found") : simpleUse ? this._tr("panel.use_automatic") : this._tr("panel.automatic");
    const manualModeLabel = simpleUse ? this._tr("panel.use_manual") : this._tr("panel.manual");
    const modeOptions=`<option value="auto" ${mode==="auto"?"selected":""}>${autoModeLabel}</option><option value="manual" ${mode==="manual"?"selected":""}>${manualModeLabel}</option><option value="disabled" ${mode==="disabled"?"selected":""}>${this._tr("panel.do_not_use")}</option>`;
    const special = "";
    const binary = binaryResource
      ? `<label class="field"><span>${this._tr("panel.normal_sensor_state")}</span><select data-binding-field="normal_state"><option value="" ${!normalState?"selected":""}>${this._tr("panel.unknown_select_manually")}</option><option value="on" ${normalState==="on"?"selected":""}>on — ${this._tr("panel.ok_f0d944c")}</option><option value="off" ${normalState==="off"?"selected":""}>off — ${this._tr("panel.ok_f0d944c")}</option></select><small class="help">${this._tr("panel.scheduler_converts_the_state_to_simple_ok_blocks_percentages_and_resourc")}</small></label>`
      : "";
    return `${this._entrySelectorHtml()}<section class="editor-card settings-editor-page"><div class="editor-header"><div><h2>${title}</h2><div class="muted">${help}</div><small class="settings-editor-key">${this._escape(row.key)}</small></div><button class="ghost icon-only settings-editor-cancel" title="${this._tr("panel.close")}">${this._mdi("close")}</button></div>
      <div class="binding-body binding-single-form" data-binding-key="${this._escape(row.key)}">
        <div class="binding-preview"><div><span>${this._tr("panel.auto_source")}</span><b>${this._escape(auto.candidate || auto.entity_id || auto.note || "—")}</b>${auto.entity_id?`<code>${this._escape(auto.entity_id)}</code>`:""}</div><div><span>${binaryResource?this._tr("panel.raw_state"):this._tr("panel.live")}</span><b>${this._escape(binaryResource?this._formatValue(input.raw_value ?? input.live):this._formatSettingValue(row.key, input.live))}</b></div><div><span>${this._tr("panel.effective")}</span><b>${this._escape(binaryResource&&!normalState?this._tr("panel.select_normal_state"):this._formatSettingValue(row.key, input.effective))}</b></div></div>
        <div class="form-grid"><label class="field"><span>${this._tr("panel.binding_mode")}</span><select data-binding-field="binding_mode">${modeOptions}</select></label><div class="field"><span>${this._tr("panel.entity_0e084dd")}</span>${this._entitySearchHtml(selectedEntity, 'data-binding-field="entity_id"')}<small class="help">${row.entity_registry_id?`${this._tr("panel.stable_registry_binding")}: ${this._escape(row.entity_registry_id)}`:this._tr("panel.saving_manual_stores_a_registry_id_so_entity_renames_remain_valid")}</small></div><label class="field"><span>${this._tr("panel.attribute")}</span><input data-binding-field="attribute" type="text" value="${this._escape(row.attribute||"")}"></label>${binary}${special}<label class="field field-wide"><span>${this._tr("panel.vendor_value_mapping_json")}</span><input data-binding-field="value_mapping" type="text" value="${this._escape(mappingText)}" placeholder='{"yes":true,"no":false}'><small class="help">${this._tr("panel.optional_applied_before_normalization")}</small></label></div>
      </div>
      <div class="editor-actions"><button class="ghost settings-editor-cancel">${this._mdi("close")}<span>${this._tr("panel.cancel")}</span></button><button class="ghost binding-single-refresh" data-key="${this._escape(row.key)}">${this._mdi("refresh")}<span>${this._tr("panel.refresh_live")}</span></button><button class="primary binding-single-save" data-key="${this._escape(row.key)}">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></section>`;
  }

  _bindingSummaryValue(row) {
    const input = row.effective_input || {};
    const value = input.effective;
    if (row.binding_mode === "disabled") return this._tr("panel.not_used");
    const binaryResource=["dock.clean_water","dock.dirty_water","dock.detergent","mop.attached"].includes(row.key);
    if (binaryResource && !(row.normal_state || row.inferred_normal_state || row.auto_candidate?.normal_state)) {
      const raw=input.raw_value ?? input.live;
      return raw===null || raw===undefined ? this._tr("panel.sensor_not_found") : `${this._tr("panel.raw")}: ${this._formatValue(raw)} · ${this._tr("panel.configure_normal_state")}`;
    }
    if (!String(row.key || "").startsWith("capability.")) {
      return this._formatSettingValue(row.key, value);
    }
    if (value !== null && value !== undefined && value !== "") {
      return this._formatSettingValue(row.key, value);
    }
    const status = String(row.support_status || "");
    if (status === "detected" || status === "manual") return this._tr("panel.supported");
    if (status === "unsupported") return this._tr("panel.unsupported");
    if (status === "disabled") return this._tr("panel.not_used");
    return this._tr("panel.needs_attention");
  }

  _bindingRuntimeTone(row) {
    if(row.binding_mode === "disabled") return "neutral";
    const input=row.effective_input||{};
    const binaryResource=["dock.clean_water","dock.dirty_water","dock.detergent","mop.attached"].includes(row.key);
    if(!binaryResource) return "neutral";
    if(input.effective_available===false || input.status==="requires_attention") return "warning";
    if(input.effective===true) return "good";
    if(input.effective===false) return "bad";
    return "neutral";
  }

  _bindingSummaryValueHtml(row) {
    const value=this._bindingSummaryValue(row);
    const tone=this._bindingRuntimeTone(row);
    if(tone==="neutral") return this._escape(value);
    return this._quietStatusHtml(value,tone,"resource-state");
  }

  _bindingSupportTone(status) {
    const value=String(status||"");
    if(value==="detected" || value==="manual") return "good";
    if(value==="unverified" || value==="conflict") return "warning";
    return "neutral";
  }

  _bindingSummaryHtml(rows, capability = false) {
    if (!rows.length) return `<div class="empty">${this._tr("panel.no_data")}</div>`;
    const firstHeader = capability ? this._tr("panel.capability") : this._tr("panel.parameter");
    const valueHeader = capability ? this._tr("panel.value_support") : this._tr("panel.current_value");
    return `<div class="table-wrap"><table class="settings-summary-table mobile-card-table"><thead><tr><th>${firstHeader}</th><th>${this._tr("panel.configuration_state")}</th><th>${valueHeader}</th><th>${this._tr("panel.action")}</th></tr></thead><tbody>${rows.map((row)=>{
      const runtimeTone=this._bindingRuntimeTone(row);
      return `<tr class="settings-summary-row resource-runtime-${runtimeTone}"><td data-label="${firstHeader}"><b>${this._escape(this._settingLabel(row.key))}</b><small>${this._escape(row.key)}</small></td><td data-label="${this._tr("panel.configuration_state")}">${this._quietStatusHtml(this._supportLabel(row.support_status),this._bindingSupportTone(row.support_status),"binding-support-state")}</td><td class="settings-summary-value" data-label="${valueHeader}">${this._bindingSummaryValueHtml(row)}</td><td class="settings-summary-action mobile-actions-cell" data-label="${this._tr("panel.action")}"><button class="ghost icon-only compact-icon-action settings-edit-binding" data-key="${this._escape(row.key)}" title="${this._tr("panel.edit_0fd5c3c")}" aria-label="${this._tr("panel.edit_0fd5c3c")}">${this._mdi("pencil-outline")}</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  _zonePermissionText(zone) {
    const mode=zone.control || "enabled";
    if (mode === "disabled") return this._tr("panel.disabled_a94d2cb");
    if (mode === "disabled_until") return `${this._tr("panel.disabled_until_db71475")} ${this._formatDateTime(zone.disabled_until)}`;
    return this._tr("panel.allowed");
  }

  _zoneBusyText(zone) {
    if (!(zone.busy_sources || []).length) return this._tr("panel.not_configured");
    const busy = zone.live?.busy || {};
    const remaining = busy.stabilizing ? this._remainingSeconds(busy.stabilizing_until) : 0;
    if (remaining > 0) return this._tr("panel.busy_value_s", {p1: remaining});
    return busy.effective === true
      ? this._tr("panel.configured_currently_busy")
      : this._tr("panel.configured_currently_not_busy");
  }

  _zoneAccessText(zone) {
    if (!(zone.access_paths || []).length) return this._tr("panel.not_configured");
    const access = zone.live?.accessible || {};
    const remaining = access.stabilizing ? this._remainingSeconds(access.stabilizing_until) : 0;
    if (remaining > 0) return this._tr("panel.inaccessible_value_s", {p1: remaining});
    return access.effective === true
      ? this._tr("panel.configured_currently_accessible")
      : this._tr("panel.configured_currently_inaccessible");
  }

  _zoneStatusTone(zone, kind) {
    const configured = kind === "busy" ? (zone.busy_sources || []).length : (zone.access_paths || []).length;
    if (!configured) return "neutral";
    const item = kind === "busy" ? (zone.live?.busy || {}) : (zone.live?.accessible || {});
    const remaining = item.stabilizing ? this._remainingSeconds(item.stabilizing_until) : 0;
    if (remaining > 0) return "warning";
    if (kind === "busy") return item.effective === true ? "warning" : "good";
    return item.effective === true ? "good" : "warning";
  }

  _zoneStatusCell(zone, kind) {
    const item = kind === "busy" ? zone.live?.busy : zone.live?.accessible;
    const text = kind === "busy" ? this._zoneBusyText(zone) : this._zoneAccessText(zone);
    const tone = this._zoneStatusTone(zone, kind);
    const attrs = item?.stabilizing && item?.stabilizing_until
      ? ` data-zone-countdown-kind="${kind}" data-stabilizing-until="${this._escape(item.stabilizing_until)}"`
      : "";
    return `<span class="quiet-status quiet-status-${tone} zone-live-state zone-live-${tone}"${attrs}><span class="quiet-status-dot" aria-hidden="true"></span><span class="quiet-status-label">${this._escape(text)}</span></span>`;
  }


  _validationProblemLabel(problem) {
    if (!problem) return "—";
    const key = `common.validation.${problem}`;
    const translated = this._tr(key);
    return translated === key ? problem : translated;
  }

  _validationHtml() {
    const issues=this._settingsData?.validation || [];
    if (!issues.length) return `<div class="validation-ok">${this._mdi("check-circle-outline")}<span>${this._tr("panel.pre_flight_configuration_is_valid")}</span></div>`;
    return `<div class="validation-list">${issues.map(i=>`<div class="validation-${this._escape(i.severity||"warning")}"><b>${this._escape(i.object||i.code||i.severity)}</b><span>${this._escape(this._validationProblemLabel(i.problem||i.message||i.detail))}${i.preflight_blocker?` · ${this._escape(this._blockerLabel(i.preflight_blocker))}`:""}${i.entity_id?` · ${this._escape(i.entity_id)}`:""}</span></div>`).join("")}</div>`;
  }

  _realReadinessHtml(readiness) {
    if(!readiness) return "";
    const issues=readiness.issues||[];
    const status=readiness.ready?this._tr("panel.real_execution_ready"):this._tr("panel.real_execution_not_ready");
    const cls=readiness.ready?"validation-ok":"validation-error";
    const rows=issues.map((item)=>{
      const key=`panel.real_readiness.${item.code}`;
      const translated=this._tr(key);
      const label=translated===key?String(item.code||"unknown").replaceAll("_"," "):translated;
      const detail=item.detail?` · ${this._escape(String(item.detail))}`:"";
      return `<div class="validation-${this._escape(item.severity||"warning")}"><b>${this._escape(label)}</b><span>${detail}</span></div>`;
    }).join("");
    return `<div class="real-readiness"><div class="${cls}">${this._mdi(readiness.ready?"check-circle-outline":"alert-circle-outline")}<span><b>${this._escape(status)}</b> · ${this._tr("panel.adapter")}: ${this._escape(readiness.vendor||"generic")}</span></div>${rows?`<div class="validation-list">${rows}</div>`:""}</div>`;
  }

  _dataLayerLabel(key) {
    return this._tr(`panel.data_layer_${String(key||"")}`);
  }

  _dataLayerSelectionModal({title,message="",available=null,defaults=null,confirmLabel=null,danger=false}={}) {
    if (this._modalPending) this._closeModal(null);
    const all=["execution_history","statistics","models","maintenance"];
    const allowed=new Set((available&&available.length?available:all).map(String));
    const initial=new Set((defaults||[]).map(String));
    return new Promise((resolve)=>{
      this._modalPending={resolve};
      const host=this.shadowRoot?.querySelector(".dialog-host");
      if(!host){this._modalPending=null;resolve(null);return;}
      const rows=all.filter(key=>allowed.has(key)).map(key=>`<label class="data-layer-choice"><input type="checkbox" data-data-layer="${key}" ${initial.has(key)?"checked":""}><span><b>${this._escape(this._dataLayerLabel(key))}</b><small>${this._escape(this._tr(`panel.data_layer_${key}_help`))}</small></span></label>`).join("");
      host.innerHTML=`<dialog class="vs-dialog data-layer-dialog" aria-labelledby="vs-data-dialog-title"><form method="dialog" class="vs-dialog-card"><div class="vs-dialog-header"><div class="vs-dialog-icon ${danger?"danger-icon":""}">${this._mdi(danger?"delete-alert-outline":"backup-restore")}</div><div><h2 id="vs-data-dialog-title">${this._escape(title||"")}</h2>${message?`<div class="vs-dialog-message">${this._escape(message)}</div>`:""}</div></div><div class="data-layer-choices">${rows}</div><div class="vs-dialog-actions"><button type="button" class="ghost dialog-cancel">${this._tr("panel.cancel")}</button><button type="submit" class="${danger?"danger":"primary"} dialog-confirm">${this._escape(confirmLabel||this._tr("panel.confirm"))}</button></div></form></dialog>`;
      const dialog=host.querySelector("dialog.vs-dialog");
      const finish=(confirmed)=>{
        if(!this._modalPending)return;
        if(!confirmed){this._closeModal(null);return;}
        const selected=[...dialog.querySelectorAll("input[data-data-layer]:checked")].map(el=>el.dataset.dataLayer).filter(Boolean);
        if(!selected.length){this._notify(this._tr("panel.select_at_least_one_data_layer"),"error",3200);return;}
        this._closeModal(selected);
      };
      dialog.querySelector(".dialog-cancel")?.addEventListener("click",()=>finish(false));
      dialog.querySelector("form")?.addEventListener("submit",event=>{event.preventDefault();finish(true);});
      dialog.addEventListener("cancel",event=>{event.preventDefault();finish(false);});
      try{dialog.showModal();}catch(_){dialog.setAttribute("open","");}
    });
  }

  _dataManagementHtml(data) {
    const d=data||{}; const counts=d.counts||{}; const backups=d.backups||[];
    const count=(key)=>Number(counts[key]||0).toLocaleString(this._language||undefined);
    const layerNames=(layers)=>(layers||[]).map(key=>this._dataLayerLabel(key)).join(" · ");
    const backupRows=backups.map(item=>`<div class="data-backup-row"><div class="data-backup-main"><b>${this._escape(this._formatDateTime(item.created_at))}</b><span>${this._escape(item.version||"—")} · ${this._escape(layerNames(item.layers))}</span><small>${this._escape(this._tr("panel.backup_counts",{history:item.counts?.execution_history||0,statistics:item.counts?.statistics||0,models:item.counts?.models||0,maintenance:item.counts?.maintenance||0}))}</small></div><div class="button-row"><button type="button" class="ghost data-backup-restore" data-backup-id="${this._escape(item.backup_id)}" data-backup-layers="${this._escape((item.layers||[]).join(","))}">${this._mdi("backup-restore")}<span>${this._tr("panel.restore")}</span></button><button type="button" class="danger icon-only compact-icon-action data-backup-delete" data-backup-id="${this._escape(item.backup_id)}" title="${this._tr("panel.delete_backup")}" aria-label="${this._tr("panel.delete_backup")}">${this._mdi("delete-outline")}</button></div></div>`).join("");
    return `<section class="entry-card data-management-card"><div class="entry-header"><div><h2>${this._tr("panel.data_and_backups")}</h2><div class="muted">${this._tr("panel.data_and_backups_help")}</div></div><div class="button-row"><button type="button" class="ghost data-backup-create">${this._mdi("archive-arrow-down-outline")}<span>${this._tr("panel.create_backup")}</span></button><button type="button" class="danger data-clear-open">${this._mdi("delete-sweep-outline")}<span>${this._tr("panel.clear_data")}</span></button></div></div><div class="data-layer-summary"><div><span>${this._tr("panel.data_layer_execution_history")}</span><b>${count("execution_history")}</b></div><div><span>${this._tr("panel.data_layer_statistics")}</span><b>${count("statistics")}</b></div><div><span>${this._tr("panel.data_layer_models")}</span><b>${count("models")}</b></div><div><span>${this._tr("panel.data_layer_maintenance")}</span><b>${count("maintenance")}</b></div></div>${d.statistics_reset_at?`<div class="muted data-reset-note">${this._tr("panel.statistics_epoch_since",{date:this._formatDateTime(d.statistics_reset_at)})}</div>`:""}<details class="data-backups" ${backups.length?"":"open"}><summary>${this._mdi("archive-outline")}<span>${this._tr("panel.backups")}</span><b>${backups.length}</b></summary><div class="data-backup-list">${backupRows||`<div class="empty compact">${this._tr("panel.no_backups")}</div>`}</div></details><div class="muted data-backup-path">${this._tr("panel.backup_path_help")}</div></section>`;
  }

  _settingsHtml() {
    const d=this._settingsData;
    if (!d) return `${this._entrySelectorHtml()}<div class="empty">${this._tr("panel.loading_settings")}</div>`;
    const caps=(d.bindings||[]).filter(x=>String(x.key).startsWith("capability."));
    const resourceOrder=["vacuum.battery_percent","vacuum.charging","dock.available","dock.robot_docked","dock.clean_water","dock.dirty_water","dock.detergent","mop.attached","dnd.active","dnd.starts_at","dnd.ends_at"];
    const resources=(d.bindings||[]).filter(x=>!String(x.key).startsWith("capability.")).sort((a,b)=>{const ai=resourceOrder.indexOf(a.key),bi=resourceOrder.indexOf(b.key);return (ai<0?999:ai)-(bi<0?999:bi)||String(a.key).localeCompare(String(b.key));});
    const rooms=(d.cleaning_zones||[]);
    const executionDraft=this._executionSettingsDraft || {
      mode:String(d.execution?.mode||"DRY_RUN")==="REAL"?"REAL":"DRY_RUN",
      dry_run_duration:Math.max(1,Math.min(300,Number(d.execution?.dry_run?.execution_duration_seconds??10))),
      restore_previous_settings:d.execution?.real?.restore_previous_settings!==false,
      runtime_error_recovery_minutes:Math.max(1,Math.min(1440,Number(d.execution?.real?.runtime_error_recovery_minutes??30))),
    };
    const currentMode=String(d.execution?.mode||"DRY_RUN")==="REAL"?"REAL":"DRY_RUN";
    const validationErrors=Number(d.summary?.validation_errors||0);
    const readiness=d.execution?.readiness||{};
    const vendorRaw=String(readiness.vendor||"generic");
    const vendor=vendorRaw?vendorRaw.charAt(0).toUpperCase()+vendorRaw.slice(1):"Generic";
    const configuredDevices=(d.related_devices||[]).filter(x=>!x.is_primary);
    const deviceItems=[
      `<span class="robot-device-item robot-device-primary">${this._mdi("robot-vacuum")}<span>${this._escape(d.vacuum?.name||"—")}</span></span>`,
      ...configuredDevices.map(x=>`<span class="robot-device-item">${this._mdi("devices")}<span>${this._escape(x.name)}</span></span>`),
    ].join("");
    const physicalSummary=readiness.ready
      ? this._tr("panel.adapter_ready_summary",{adapter:vendor})
      : this._tr("panel.adapter_not_ready_summary",{adapter:vendor});
    const preflightSummary=validationErrors===0
      ? this._tr("panel.configuration_correct_summary")
      : this._tr("panel.configuration_errors_summary",{count:validationErrors});
    const interfaceLanguageDraft=String(this._interfaceLanguageDraft ?? d.interface_language ?? "auto");
    return `${this._entrySelectorHtml()}
      <section class="entry-card robot-settings-card compact-robot-settings"><div class="entry-header compact-robot-header"><div><h2>${this._tr("panel.vacuum_and_related_devices")}</h2></div><details class="overflow-menu"><summary title="${this._tr("panel.actions")}" aria-label="${this._tr("panel.actions")}">${this._mdi("dots-vertical")}</summary><div class="overflow-menu-popup"><button class="ghost settings-rescan">${this._mdi("radar")}<span>${this._tr("panel.rediscover")}</span></button><button class="ghost settings-validate">${this._mdi("check-circle-outline")}<span>${this._tr("panel.validate")}</span></button></div></details></div><div class="robot-device-list">${deviceItems}</div><div class="robot-compact-status"><div class="robot-status-row ${readiness.ready?"ok":"bad"}"><span>${this._tr("panel.physical_execution")}:</span><b>${this._escape(physicalSummary)}</b></div><div class="robot-status-row ${validationErrors===0?"ok":"bad"}"><span>${this._tr("panel.preliminary_check")}:</span><b>${this._escape(preflightSummary)}</b></div></div></section>
      <section class="entry-card execution-settings-card"><div class="entry-header"><div><h2>${this._tr("panel.execution")}</h2><div class="muted">${this._tr("panel.execution_mode_action_help")}</div></div></div>
        <div class="execution-mode-picker execution-mode-picker-primary" role="radiogroup" aria-label="${this._escape(this._tr("panel.execution_mode"))}"><button type="button" class="execution-mode-choice execution-mode-choice-dry ${currentMode!=="REAL"?"selected":""}" data-execution-mode="DRY_RUN" aria-pressed="${currentMode!=="REAL"?"true":"false"}">${this._mdi("flask-outline")}<span>${this._tr("panel.execution_dry_run")}</span></button><button type="button" class="execution-mode-choice execution-mode-choice-real ${currentMode==="REAL"?"selected":""}" data-execution-mode="REAL" aria-pressed="${currentMode==="REAL"?"true":"false"}">${this._mdi("robot-vacuum")}<span>${this._tr("panel.execution_real")}</span></button></div>
        <details class="execution-advanced" ${this._executionAdvancedOpen?"open":""}><summary>${this._mdi("tune-variant")}<span>${this._tr("panel.additional_execution_settings")}</span></summary><div class="execution-advanced-body"><div class="form-grid"><label class="field"><span>${this._tr("panel.dry_run_duration")}</span><input id="dry-run-duration" type="number" min="1" max="300" value="${this._escape(executionDraft.dry_run_duration)}"><small class="help">${this._tr("panel.dry_run_duration_help")}</small></label><label class="field"><span>${this._tr("panel.runtime_error_recovery_minutes")}</span><input id="runtime-error-recovery-minutes" type="number" min="1" max="1440" step="1" value="${this._escape(executionDraft.runtime_error_recovery_minutes??30)}"><small class="help">${this._tr("panel.runtime_error_recovery_minutes_help")}</small></label><label class="field field-wide"><span>${this._tr("panel.real_execution_options")}</span><label class="inline-check"><input id="real-restore-settings" type="checkbox" ${executionDraft.restore_previous_settings?"checked":""}> ${this._tr("panel.restore_previous_robot_settings")}</label><small class="help">${this._tr("panel.restore_previous_robot_settings_help")}</small></label></div><div class="editor-actions"><button class="primary execution-additional-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></div></details>
      </section>
      ${this._policyHtml()}
      <div class="settings-bindings-grid">
        <section class="entry-card settings-binding-column"><div class="entry-header"><div><h2>${this._tr("panel.robot_capabilities")}</h2><div class="muted">${this._tr("panel.each_capability_is_edited_separately_with_its_edit_icon")}</div></div></div>${this._bindingSummaryHtml(caps, true)}</section>
        <section class="entry-card settings-binding-column"><div class="entry-header"><div><h2>${this._tr("panel.resources_dock_and_dnd")}</h2><div class="muted">${this._tr("panel.each_resource_dock_setting_and_dnd_input_is_edited_separately_with_its_e")}</div></div></div>${this._bindingSummaryHtml(resources, false)}</section>
      </div>
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.cleaning_zones")}</h2><div class="muted">${this._tr("panel.each_cleaning_zone_maps_to_one_robot_target_and_owns_its_presence_sensor")}</div></div><button class="primary room-new" title="${this._tr("panel.add_cleaning_zone")}" aria-label="${this._tr("panel.add_cleaning_zone")}">${this._mdi("vector-square-plus")}<span>${this._tr("panel.add")}</span></button></div>
        <div class="table-wrap"><table class="zone-summary-table mobile-card-table"><thead><tr><th>${this._tr("panel.cleaning_zone")}</th><th>${this._tr("panel.robot_target")}</th><th>${this._tr("panel.nominal_area")}</th><th>${this._tr("panel.cleaning_a7a7549")}</th><th>${this._tr("panel.occupancy")}</th><th>${this._tr("panel.accessibility")}</th><th>${this._tr("panel.actions")}</th></tr></thead><tbody>${rooms.map(r=>`<tr class="room-row"><td class="room-main" data-label="${this._tr("panel.cleaning_zone")}"><b>${this._escape(r.name)}</b></td><td data-label="${this._tr("panel.robot_target")}"><span>${this._escape(r.robot_target_type==="segment"?this._tr("panel.segment_0b904ec"):this._tr("panel.robot_zone"))}: ${this._escape(r.target_name||r.robot_target_id||"—")}</span></td><td data-label="${this._tr("panel.nominal_area")}">${r.nominal_area_m2?`${this._escape(r.nominal_area_m2)} m²`:"—"}</td><td data-label="${this._tr("panel.cleaning_a7a7549")}"><b>${this._escape(this._zonePermissionText(r))}</b></td><td data-label="${this._tr("panel.occupancy")}">${this._zoneStatusCell(r,"busy")}</td><td data-label="${this._tr("panel.accessibility")}">${this._zoneStatusCell(r,"access")}</td><td class="room-actions mobile-actions-cell" data-label="${this._tr("panel.actions")}"><div class="room-actions-inner"><button class="ghost icon-only compact-icon-action room-edit" data-room-id="${this._escape(r.zone_id)}" title="${this._tr("panel.configure")}" aria-label="${this._tr("panel.configure")}">${this._mdi("cog-outline")}</button><button class="danger icon-only compact-icon-action room-delete" data-room-id="${this._escape(r.zone_id)}" data-room-name="${this._escape(r.name)}" title="${this._tr("panel.delete")}" aria-label="${this._tr("panel.delete")}">${this._mdi("delete-outline")}</button></div></td></tr>`).join("") || `<tr><td colspan="7" class="empty-cell">${this._tr("panel.no_cleaning_zones_yet")}</td></tr>`}</tbody></table></div>
      </section>
      ${this._dataManagementHtml(d.data_management)}
      <section class="entry-card interface-language-card"><div class="entry-header"><div><h2>${this._tr("panel.interface_language")}</h2><div class="muted">${this._tr("panel.interface_language_help")}</div></div></div><div class="form-grid"><label class="field"><span>${this._tr("panel.language")}</span><select id="interface-language"><option value="auto" ${interfaceLanguageDraft==="auto"?"selected":""}>${this._tr("panel.language_auto")}</option><option value="ru" ${interfaceLanguageDraft==="ru"?"selected":""}>${this._tr("common.language.ru")}</option><option value="uk" ${interfaceLanguageDraft==="uk"?"selected":""}>${this._tr("common.language.uk")}</option><option value="en" ${interfaceLanguageDraft==="en"?"selected":""}>${this._tr("common.language.en")}</option></select><small class="help">${this._tr("panel.interface_language_auto_help")}</small></label></div><div class="editor-actions"><button class="primary interface-language-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save")}</span></button></div></section>`;
  }

  _overrideInputLabel(key) {
    const text=String(key || "");
    const match=text.match(/^zone\.([^.]+)\.(busy|accessible)$/);
    if (!match) return this._settingLabel(text);
    const [, zoneId, kind]=match;
    const zone=(this._settingsData?.cleaning_zones || []).find(item=>String(item.zone_id)===zoneId);
    const zoneName=zone?.name || `${this._tr("panel.zone")} ${zoneId.slice(0,8)}`;
    const suffix=kind==="busy" ? this._tr("panel.occupancy_5d45f00") : this._tr("panel.accessibility_ac4b900");
    return `${zoneName} — ${suffix}`;
  }

  _overrideRowsHtml(editable = true) {
    const d=this._settingsData;
    if (!d) return "";
    const flat=[];
    Object.values(d.inputs?.values||{}).forEach(x=>flat.push(x));
    Object.values(d.inputs?.zones||{}).forEach(r=>{ if(r.busy)flat.push(r.busy);if(r.accessible)flat.push(r.accessible); });
    const overrides=d.overrides?.active || d.overrides || {};
    const disabled=editable ? "" : " disabled";
    const disabledRow=editable ? "" : " disabled-row";
    return `<div class="table-wrap"><table class="override-table mobile-card-table"><thead><tr><th>${this._tr("panel.input")}</th><th>${this._tr("panel.live")}</th><th>${this._tr("panel.effective")}</th><th>${this._tr("panel.test_mode")}</th><th>${this._tr("panel.value")}</th><th>${this._tr("panel.after_restart")}</th><th>${this._tr("panel.action")}</th></tr></thead><tbody>${flat.map(item=>{const ov=item.override || overrides[item.key] || null; const mode=String(ov?.mode||"live").toLowerCase();return `<tr class="${disabledRow.trim()}" data-override-target="${this._escape(item.key)}" aria-disabled="${editable?"false":"true"}"><td data-label="${this._tr("panel.input")}"><b>${this._escape(this._overrideInputLabel(item.key))}</b><small><code>${this._escape(item.key)}</code>${item.source_entity_id?` · ${this._escape(item.source_entity_id)}`:""}</small></td><td data-label="${this._tr("panel.live")}">${this._escape(this._formatValue(item.live))}</td><td data-label="${this._tr("panel.effective")}">${this._escape(this._formatValue(item.effective))}</td><td data-label="${this._tr("panel.test_mode")}"><select data-override-field="mode"${disabled}><option value="live" ${mode==="live"?"selected":""}>${this._tr("common.override_mode.live")}</option><option value="freeze" ${mode==="freeze"?"selected":""}>${this._tr("common.override_mode.freeze")}</option><option value="force" ${mode==="force"?"selected":""}>${this._tr("common.override_mode.force")}</option><option value="unavailable" ${mode==="unavailable"?"selected":""}>${this._tr("common.override_mode.unavailable")}</option></select></td><td data-label="${this._tr("panel.value")}"><input data-override-field="value" type="text" value="${this._escape(ov?.value??"")}" placeholder="${this._tr("panel.force_only")}"${disabled}></td><td data-label="${this._tr("panel.after_restart")}"><label class="inline-check"><input data-override-field="persistent" type="checkbox" ${ov?.persistent?"checked":""}${disabled}> ${this._tr("panel.persist")}</label></td><td class="mobile-actions-cell" data-label="${this._tr("panel.action")}"><div class="override-actions"><button class="primary icon-only compact-icon-action override-apply" title="${this._tr("panel.apply")}" aria-label="${this._tr("panel.apply")}"${disabled}>${this._mdi("check")}</button>${ov?`<button class="danger small icon-only compact-icon-action override-clear" title="${this._tr("panel.clear_override")}"${disabled}>${this._mdi("delete-outline")}</button>`:""}</div></td></tr>`}).join("")}</tbody></table></div>`;
  }


  _testingHtml() {
    const entry = this._statusEntry();
    if (!entry) return `<div class="empty">${this._tr("panel.scheduler_is_not_loaded_yet")}</div>`;
    const clock = entry.clock || {};
    const dryRun=String(entry.execution_mode || entry.mode || "DRY_RUN").toUpperCase()==="DRY_RUN";
    const fault=entry.dry_run?.pending_fault || "";
    return `${this._entrySelectorHtml()}
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.real_input_overrides")}</h2><div class="muted">${this._tr("panel.live_uses_the_real_source_freeze_locks_the_current_value_force_injects_a")}</div></div><button class="ghost override-clear-all" ${dryRun?"":"disabled"}>${this._mdi("delete-sweep")}<span>${this._tr("panel.clear_all_overrides")}</span></button></div>${this._overrideRowsHtml(dryRun)}<div class="preset-row"><b>${this._tr("panel.presets")}</b>${[["battery_low",this._tr("panel.low_battery")],["vacuum_busy",this._tr("panel.vacuum_busy")],["vacuum_unavailable",this._tr("panel.vacuum_unavailable")],["dnd_active","DND"],["clean_water_empty",this._tr("panel.clean_water_empty")],["dirty_water_full",this._tr("panel.dirty_water_full")],["detergent_empty",this._tr("panel.detergent_unavailable")],["zone_busy",this._tr("panel.zone_busy")],["path_blocked",this._tr("panel.path_blocked")]].map(([v,l])=>`<button class="ghost override-preset" data-preset="${v}" ${dryRun?"":"disabled"}>${this._mdi("flask-outline")}<span>${this._escape(l)}</span></button>`).join("")}</div></section>
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.test_clock")}</h2><div class="muted">${this._tr("panel.dry_run_clock_help")}</div></div></div><div class="clock-line"><div><span>${this._tr("panel.scheduler_time")}</span><b>${this._formatDateTime(clock.now)}</b></div><div><span>${this._tr("panel.offset")}</span><b>${this._formatDurationSeconds(clock.offset_seconds||0)}</b></div><div><span>${this._tr("panel.next_transition")}</span><b>${this._formatDateTime(entry.next_transition)}</b></div></div><div class="button-row"><button class="ghost advance" data-seconds="60" ${dryRun?"":"disabled"}>${this._mdi("clock-plus-outline")}<span>+1 ${this._tr("panel.min")}</span></button><button class="ghost advance" data-seconds="300" ${dryRun?"":"disabled"}>${this._mdi("clock-plus-outline")}<span>+5 ${this._tr("panel.min")}</span></button><button class="ghost advance" data-seconds="1800" ${dryRun?"":"disabled"}>${this._mdi("clock-plus-outline")}<span>+30 ${this._tr("panel.min")}</span></button><button class="primary next-transition" ${dryRun?"":"disabled"}>${this._mdi("skip-next")}<span>${this._tr("panel.next_transition")}</span></button><button class="ghost reset-time" ${dryRun?"":"disabled"}>${this._mdi("restore")}<span>${this._tr("panel.reset_time")}</span></button></div></section>
      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.execution_fault_injection")}</h2><div class="muted">${this._tr("panel.execution_fault_help")}</div></div></div><div class="form-grid"><label class="field"><span>${this._tr("panel.next_execution_attempt")}</span><select id="execution-fault" ${dryRun?"":"disabled"}><option value="" ${!fault?"selected":""}>${this._tr("panel.none")}</option>${[["start_rejected",this._tr("panel.fault_start_rejected")],["start_timeout",this._tr("panel.fault_start_timeout")],["execution_failed",this._tr("panel.fault_execution_failed")],["execution_lost",this._tr("panel.fault_execution_lost")]].map(([v,l])=>`<option value="${v}" ${fault===v?"selected":""}>${l}</option>`).join("")}</select></label></div><div class="editor-actions"><button class="primary execution-fault-save" ${dryRun?"":"disabled"}>${this._mdi("content-save-outline")}<span>${this._tr("panel.apply")}</span></button></div></section>
      <section class="entry-card reset-simulation-card"><div class="entry-header"><div><h2>${this._tr("panel.reset_dry_run")}</h2><div class="muted">${this._tr("panel.reset_dry_run_help")}</div></div><button class="danger reset-simulation" ${dryRun?"":"disabled"}>${this._mdi("restart")}<span>${this._tr("panel.reset")}</span></button></div></section>`;
  }

  _looksInternalIdentifier(value) {
    const text=String(value ?? "").trim();
    if (!text) return false;
    return /^[0-9a-f]{16,}$/i.test(text)
      || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text);
  }

  _forecastZoneDisplayName(zoneId, storedName = "") {
    const id=String(zoneId ?? "").trim();
    const live=(this._settingsData?.cleaning_zones || []).find(item=>String(item?.zone_id ?? "")===id);
    const liveName=String(live?.name ?? "").trim();
    if (liveName) return liveName;
    const stored=String(storedName ?? "").trim();
    if (stored && stored !== id && !this._looksInternalIdentifier(stored)) return stored;
    return "";
  }

  _forecastScheduleDisplayName(scheduleId, storedName = "") {
    const id=String(scheduleId ?? "").trim();
    const entry=(this._data?.entries || []).find(item=>String(item?.entry_id ?? "")===String(this._schedulerEntryId ?? "")) || {};
    const live=(entry.schedules || []).find(item=>String(item?.schedule_id ?? "")===id);
    const liveName=String(live?.name ?? "").trim();
    if (liveName) return liveName;
    const stored=String(storedName ?? "").trim();
    if (stored && stored !== id && !this._looksInternalIdentifier(stored)) return stored;
    return "";
  }

  _forecastProfileText(row, kind) {
    const profileText=this._compactCleaningProfileText(row?.profile_params || {});
    if (kind !== "schedule") return profileText;
    const ids=Array.isArray(row?.zone_ids) ? row.zone_ids : [];
    const stored=Array.isArray(row?.zone_names) ? row.zone_names : [];
    const zoneNames=ids.map((id,index)=>this._forecastZoneDisplayName(id,stored[index])).filter(Boolean);
    const scheduleName=this._forecastScheduleDisplayName(row?.schedule_id,row?.schedule_name);
    const distinctZones=zoneNames.filter((name,index)=>zoneNames.indexOf(name)===index);
    const showZones=distinctZones.length>1 || (distinctZones.length===1 && distinctZones[0]!==scheduleName);
    return [showZones ? distinctZones.join(" · ") : "", profileText].filter(Boolean).join(" · ");
  }

  _formatPercentileLabel(value) {
    const numeric=Number(value);
    const n=Number.isFinite(numeric)?Math.max(0,Math.min(100,Math.round(numeric))):90;
    const subs={"0":"₀","1":"₁","2":"₂","3":"₃","4":"₄","5":"₅","6":"₆","7":"₇","8":"₈","9":"₉"};
    return `P${String(n).split("").map((digit)=>subs[digit]||digit).join("")}`;
  }

  _forecastCleaningScope(params) {
    const source=params||{};
    const token=(value)=>String(value??"").trim().toLowerCase();
    const mode=token(source.cleaning_mode);
    const dry=new Set(["vacuum","vacuum_only","vacuum-only","vacuum only","sweep","sweeping"]);
    const wet=new Set(["mop","mop_only","mop-only","mop only","wash","washing","vac_and_mop","vacuum_and_mop","vacuum-and-mop","vacuum+mop","vacuum_mop","sweep_and_mop","sweep-and-mop"]);
    const ambiguous=new Set(["","default","standard","custom","auto","automatic","__none__","none"]);
    const disabled=new Set(["","none","off","__none__","disabled","false","0"]);
    if(dry.has(mode)) return "dry";
    if(wet.has(mode)) return "wet";
    if(mode&&!ambiguous.has(mode)) return "unknown";
    if(!disabled.has(token(source.mop_mode))||!disabled.has(token(source.water_mode))) return "wet";
    return "unknown";
  }

  _forecastParameterSignature(params,metric="") {
    const source=params||{};
    const waterMetric=metric==="clean_water"||metric==="dirty_water";
    const keys=waterMetric?["cleaning_mode","mop_mode","water_mode","passes"]:["cleaning_mode","cleaning_route","mop_mode","fan_mode","water_mode","passes"];
    return keys.map((key)=>{
      // Forecast identity follows the same physical-field visibility as the
      // cleaning profile itself. Historical dry snapshots can retain stale
      // mop/water values and mop-only snapshots can retain stale suction fields;
      // neither is allowed to split one real physical profile.
      if(key!=="cleaning_mode"&&key!=="passes"&&!this._cleaningProfileFieldVisible(source,key)) return null;
      let value=source[key];
      if(key==="passes"&&(value===null||value===undefined||value===""||value==="__none__")) value=1;
      if(value===null||value===undefined||value===""||value==="__none__") return null;
      return `${key}=${String(value)}`;
    }).filter(Boolean).join("|");
  }

  _forecastZoneSignature(zoneIds) {
    return Array.from(new Set((zoneIds||[]).map((value)=>String(value||"")).filter(Boolean))).sort().join("|");
  }

  _forecastCurrentDemands(metric="") {
    const entry=(this._data?.entries||[]).find((item)=>String(item?.entry_id||"")===String(this._schedulerEntryId||""))||{};
    const demands=[];
    const waterMetric=metric==="clean_water"||metric==="dirty_water";
    for(const schedule of entry.schedules||[]) {
      if(schedule?.enabled===false||schedule?.paused===true) continue;
      const zones=(schedule?.targets||[]).map((value)=>String(value||"")).filter(Boolean);
      if(!zones.length) continue;
      const base={...(schedule?.cleaning_params||{})};
      const overrides=schedule?.weekday_overrides||{};
      const weekdays=Array.isArray(schedule?.weekdays)?schedule.weekdays:[];
      const variants=[];
      if(weekdays.length) {
        for(const day of weekdays) variants.push({...base,...(overrides[String(day)]||overrides[day]||{})});
      }
      if((schedule?.dates||[]).length||!variants.length) variants.push(base);
      const uniqueProfiles=[];
      const seenProfiles=new Set();
      for(const params of variants) {
        // Water Estimate rows exist only for wet work. Explicitly dry weekday
        // variants are deterministic zero and must not make historical wet rows
        // appear current; unresolved legacy modes stay out as well.
        if(waterMetric&&this._forecastCleaningScope(params)!=="wet") continue;
        const signature=this._forecastParameterSignature(params,metric);
        if(seenProfiles.has(signature)) continue;
        seenProfiles.add(signature);
        uniqueProfiles.push({...params});
      }
      if(!uniqueProfiles.length&&waterMetric) continue;
      demands.push({schedule_id:String(schedule?.schedule_id||""),zone_signature:this._forecastZoneSignature(zones),zones:new Set(zones),profiles:uniqueProfiles,profile_signatures:seenProfiles});
    }
    return demands;
  }

  _forecastProfileDistance(left,right,metric="") {
    const a=left||{};const b=right||{};
    const waterMetric=metric==="clean_water"||metric==="dirty_water";
    const keys=waterMetric?["cleaning_mode","mop_mode","water_mode","passes"]:["cleaning_mode","cleaning_route","mop_mode","fan_mode","water_mode","passes"];
    let distance=0;
    for(const key of keys) {
      if(key!=="cleaning_mode"&&key!=="passes") {
        const visibleA=this._cleaningProfileFieldVisible(a,key);
        const visibleB=this._cleaningProfileFieldVisible(b,key);
        if(!visibleA&&!visibleB) continue;
      }
      let av=a[key],bv=b[key];
      if(key==="passes") { if(av===null||av===undefined||av===""||av==="__none__") av=1;if(bv===null||bv===undefined||bv===""||bv==="__none__") bv=1; }
      const as=av===null||av===undefined||av==="__none__"?"":String(av);
      const bs=bv===null||bv===undefined||bv==="__none__"?"":String(bv);
      if(as!==bs) distance+=1;
    }
    return distance;
  }

  _forecastRowMatch(row,kind,metric="") {
    const demands=this._forecastCurrentDemands(metric);
    const profile=row?.profile_params||{};
    const profileSignature=this._forecastParameterSignature(profile,metric);
    let candidates=[];
    if(kind==="schedule") {
      const zones=this._forecastZoneSignature(row?.zone_ids||[]);
      candidates=demands.filter((item)=>item.zone_signature===zones);
    } else {
      const zoneId=String(row?.zone_id||"");
      candidates=demands.filter((item)=>item.zones.has(zoneId));
    }
    if(!candidates.length) return {applicable:false,exact:false,related:false,correction_count:Number.POSITIVE_INFINITY};
    let exact=false;let correctionCount=Number.POSITIVE_INFINITY;
    for(const item of candidates) {
      if(item.profile_signatures.has(profileSignature)) exact=true;
      for(const demandProfile of item.profiles||[]) correctionCount=Math.min(correctionCount,this._forecastProfileDistance(profile,demandProfile,metric));
    }
    if(exact) correctionCount=0;
    // "Applicable now" is a presentation concept: the row must represent an
    // effective profile that exists in the current Schedule configuration,
    // including weekday overrides.  Parameter-agnostic Forecast fallbacks may
    // aggregate old profiles for calculation, but that does not make every
    // historical profile a current profile in the Estimate table.
    return {applicable:exact,exact,related:true,correction_count:Number.isFinite(correctionCount)?correctionCount:99};
  }

  _forecastRowApplicability(row,kind,metric="") {
    const match=this._forecastRowMatch(row,kind,metric);
    return !match.applicable?0:(match.exact?2:1);
  }

  _forecastRowSortName(row,kind) {
    return kind==="schedule"?(this._forecastScheduleDisplayName(row?.schedule_id,row?.schedule_name)||""):(this._forecastZoneDisplayName(row?.zone_id,row?.zone_name)||"");
  }

  _forecastRowStableId(row,kind,metric="") {
    const owner=kind==="schedule"?String(row?.schedule_id||""):String(row?.zone_id||"");
    const zones=kind==="schedule"?this._forecastZoneSignature(row?.zone_ids||[]):"";
    return `${owner}|${zones}|${this._forecastParameterSignature(row?.profile_params||{},metric)}`;
  }

  _forecastSortRows(rows,kind,policy,metric="") {
    const enabled=policy?.enabled!==false;
    const decorated=(rows||[]).map((row)=>({row,match:enabled?this._forecastRowMatch(row,kind,metric):{applicable:false,exact:false,correction_count:Number.POSITIVE_INFINITY}}));
    const nameCompare=(left,right)=>this._forecastRowSortName(left.row,kind).localeCompare(this._forecastRowSortName(right.row,kind),this._language||undefined,{sensitivity:"base",numeric:true});
    const commonCompare=(left,right)=>{
      const byName=nameCompare(left,right);if(byName) return byName;
      const byExact=Number(Boolean(right.match.exact))-Number(Boolean(left.match.exact));if(byExact) return byExact;
      const byCorrections=Number(left.match.correction_count)-Number(right.match.correction_count);if(byCorrections) return byCorrections;
      const byTrained=Number(Boolean(right.row?.trained))-Number(Boolean(left.row?.trained));if(byTrained) return byTrained;
      const bySamples=Number(right.row?.sample_count||0)-Number(left.row?.sample_count||0);if(bySamples) return bySamples;
      const leftFresh=Date.parse(left.row?.last_sample_at||"")||0;const rightFresh=Date.parse(right.row?.last_sample_at||"")||0;
      if(rightFresh!==leftFresh) return rightFresh-leftFresh;
      return this._forecastRowStableId(left.row,kind,metric).localeCompare(this._forecastRowStableId(right.row,kind,metric),undefined,{numeric:true});
    };
    const active=decorated.filter((item)=>item.match.applicable).sort(commonCompare).map((item)=>item.row);
    const other=decorated.filter((item)=>!item.match.applicable).sort(commonCompare).map((item)=>item.row);
    return {active,other};
  }

  _statisticsForecastHtml(forecast) {
    const models=forecast?.models||{};
    const configs=[["time",this._tr("panel.forecast_time")],["battery",this._tr("panel.forecast_battery")],["clean_water",this._tr("panel.forecast_clean_water")],["dirty_water",this._tr("panel.forecast_dirty_water")]];
    const value=(metric,v)=>{const n=Number(v);if(!Number.isFinite(n))return "—";if(metric==="time")return this._formatDurationSeconds(n);if(metric==="battery")return `${this._formatNumber(n,1)}%`;return `${this._formatNumber(n,1)} ml`;};
    const basisLabel=(basis)=>{const key={zones_and_parameters:"panel.forecast_basis_zones_and_parameters",zone_composite_and_parameters:"panel.forecast_basis_zone_composite_and_parameters",zones:"panel.forecast_basis_zones",zone_composite:"panel.forecast_basis_zone_composite",insufficient_matching_profile:"panel.forecast_basis_matching_profile_insufficient",insufficient_matching_water_profile:"panel.forecast_basis_matching_profile_insufficient",insufficient_real_history:"panel.forecast_basis_insufficient"}[String(basis||"")];return key?this._tr(key):"";};
    const table=(metric,rows,kind,policy)=>{
      const objectLabel=kind==="schedule"?this._tr("panel.job"):this._tr("panel.cleaning_zone");
      const percentile=policy?.percentile??90;
      const percentileLabel=this._formatPercentileLabel(percentile);
      const rowName=(row)=>this._forecastRowSortName(row,kind)||objectLabel;
      const sorted=this._forecastSortRows(rows,kind,policy,metric);
      const active=sorted.active;
      const other=sorted.other;
      const rowHtml=(row)=>{
        const name=rowName(row);
        const profileText=this._forecastProfileText(row,kind);
        const supportCount=Number(row.sample_count||0);
        const directCount=Number(row.direct_sample_count);
        const basis=kind==="schedule"?basisLabel(row.basis):"";
        const directNote=kind==="schedule"&&Number.isFinite(directCount)&&directCount!==supportCount?this._tr("panel.forecast_direct_data_count",{count:directCount}):"";
        const dataNote=[basis,directNote].filter(Boolean).join(" · ");
        const med=supportCount>0&&row.median_value!==null&&row.median_value!==undefined?value(metric,row.median_value):"—";
        return `<tr><td data-label="${objectLabel}"><b>${this._escape(name)}</b></td><td class="forecast-profile-cell" data-label="${this._tr("panel.profile")}">${this._escape(profileText||"—")}</td><td class="forecast-data-count metric-number" data-label="${this._tr("panel.data_count")}"><b>${this._escape(supportCount)}</b>${dataNote?`<small>${this._escape(dataNote)}</small>`:""}</td><td class="metric-number" data-label="${this._tr("panel.median")}">${med}</td><td class="metric-number" data-label="${percentileLabel}">${row.trained?value(metric,row.raw_percentile_value):this._tr("panel.not_trained")}</td><td class="metric-number" data-label="Δ">${this._formatNumber(Number(row.delta_percent||0),1)}%</td><td class="metric-number" data-label="${this._tr("panel.estimate_value")}"><b>${row.trained?value(metric,row.forecast_value):"—"}</b></td></tr>`;
      };
      const header=`<thead><tr><th>${objectLabel}</th><th>${this._tr("panel.profile")}</th><th class="metric-header">${this._tr("panel.data_count")}</th><th class="metric-header">${this._tr("panel.median")}</th><th class="metric-header">${percentileLabel}</th><th class="metric-header">Δ</th><th class="metric-header">${this._tr("panel.estimate_value")}</th></tr></thead>`;
      const tableHtml=(selectedRows,emptyText)=>`<div class="table-wrap forecast-table-wrap"><table class="mobile-card-table forecast-model-table">${header}<tbody>${selectedRows.map(rowHtml).join("")||`<tr><td colspan="7" class="empty-cell">${emptyText}</td></tr>`}</tbody></table></div>`;
      const secondary=other.length?`<details class="forecast-other-data"><summary>${this._tr("panel.forecast_other_data",{count:other.length})}</summary>${tableHtml(other,this._tr("panel.no_forecast_data"))}</details>`:"";
      return `<div class="history-report-section-head forecast-table-head"><div><h4>${objectLabel}</h4></div></div>${tableHtml(active,this._tr("panel.no_current_forecast_data"))}${secondary}`;
    };
    const card=([metric,label])=>{
      const m=models[metric]||{};const p=m.policy||{};const archives=m.archives||[];const status=m.trained?this._tr("panel.model_trained"):this._tr("panel.not_trained");
      const percentileLabel=this._formatPercentileLabel(p.percentile??90);
      return `<section class="forecast-model-card" data-forecast-model="${metric}"><div class="forecast-model-head"><div><h3>${this._escape(label)}</h3><div class="forecast-model-meta"><span class="ui-chip">${p.enabled?this._tr("panel.forecast_used"):this._tr("panel.forecast_not_used")}</span><span class="ui-chip">${percentileLabel}</span><span class="ui-chip">Δ ${this._formatNumber(Number(p.delta_percent||0),1)}%</span><span class="ui-chip">${this._escape(p.lookback_days??90)} ${this._tr("panel.days_short")}</span><span class="ui-chip">${this._tr("panel.generation_short")} ${this._escape(m.generation??1)}</span><span class="ui-chip">${status}</span><span class="ui-chip">${this._tr("panel.data_count")}: ${this._escape(m.active_sample_count??0)}</span></div><div class="muted">${this._tr("panel.forecast_generation_since",{date:this._formatDateTime(m.started_at)})}</div></div><div class="forecast-model-actions"><label class="field"><span>${this._tr("panel.retain_last_days")}</span><input class="forecast-retain-days" data-metric="${metric}" type="number" min="0" max="3650" step="1" value="5"></label><button class="ghost forecast-archive" data-metric="${metric}">${this._mdi("archive-arrow-down-outline")}<span>${this._tr("panel.archive_model")}</span></button></div></div>${table(metric,m.by_schedule,"schedule",p)}${table(metric,m.by_zone,"zone",p)}${archives.length?`<div class="history-report-section-head"><div><h4>${this._tr("panel.model_archives")}</h4></div></div><div class="forecast-archive-list">${archives.map(a=>`<div class="forecast-archive-row"><div><b>${this._tr("panel.generation_short")} ${this._escape(a.source_generation??"—")}</b><div class="muted">${this._formatDateTime(a.archived_at)} · ${this._tr("panel.data_count")}: ${this._escape(a.sample_count??0)} · ${this._tr("panel.archived_older_than_days",{days:a.retain_days??0})}</div></div><div class="button-row"><button class="ghost small forecast-restore-archive" data-metric="${metric}" data-archive-id="${this._escape(a.archive_id)}">${this._mdi("restore")}<span>${this._tr("panel.restore")}</span></button><button class="danger small icon-only compact-icon-action forecast-delete-archive" data-metric="${metric}" data-archive-id="${this._escape(a.archive_id)}" title="${this._tr("panel.delete")}">${this._mdi("delete-outline")}</button></div></div>`).join("")}</div>`:""}</section>`;
    };
    return `<section class="statistics-tab-section"><div class="muted statistics-tab-caption">${this._tr("panel.forecast_tab_help")}</div>${configs.map(card).join("")}</section>`;
  }

  _statisticsHtml() {
    const d=this._statisticsData;
    if(!d) return `${this._entrySelectorHtml()}<section class="entry-card"><div class="empty">${this._tr("panel.statistics_loading")}</div></section>`;
    const a=d.aggregate||{}; const f=this._statisticsFilters||{};
    const configuredPercentiles=d.percentiles||{};
    const configuredPercentile=(metricName,fallback=90)=>{const n=Number(configuredPercentiles?.[metricName]);return Number.isFinite(n)?n:fallback;};
    const distributionPercentile=(obj,fallback=90)=>{const n=Number(obj?.percentile_number);return Number.isFinite(n)?n:fallback;};
    const fmt=(v,digits=1)=>v===null||v===undefined||Number.isNaN(Number(v))?"—":Number(v).toLocaleString(this._language,{maximumFractionDigits:digits});
    const duration=(v)=>this._formatDurationSeconds(v);
    const durationPerM2=(v)=>this._formatDurationSeconds(v);
    const tableDuration=(v)=>this._formatDurationSeconds(v);
    const metric=(obj,key="median",unit="")=>{if(!obj||!obj.count)return "—";const value=obj[key];if(unit==="time")return duration(value);if(unit==="time_per_m2")return durationPerM2(value);return `${fmt(value,1)}${unit?` ${unit}`:""}`;};
    const tableMetric=(obj,key="median")=>!obj||!obj.count?"—":fmt(obj[key],1);
    const tableTime=(obj,key="median")=>!obj||!obj.count?"—":tableDuration(obj[key]);
    const dist=(obj,unit="")=>obj&&obj.count?`${metric(obj,"median",unit)} · ${this._formatPercentileLabel(distributionPercentile(obj))}: ${metric(obj,"percentile",unit)}`:"—";
    const metricCard=(label,obj,unit="",note="")=>`<div class="summary-card statistics-metric-card"><span>${label}</span><b>${metric(obj,"median",unit)}</b><small class="statistics-percentile">${this._formatPercentileLabel(distributionPercentile(obj))}: ${metric(obj,"percentile",unit)}</small>${note?`<small class="statistics-card-note">${note}</small>`:""}</div>`;
    const weightedCard=(label,obj,unit="")=>{const value=obj?.value;const text=value===null||value===undefined?"—":unit==="time_per_m2"?durationPerM2(value):`${fmt(value,1)} ${unit}`;return `<div class="summary-card statistics-metric-card"><span>${label}</span><b>${text}</b></div>`;};
    const profileLabel=(params)=>this._compactCleaningProfileText(params);
    const entry=(this._data?.entries||[]).find(x=>x.entry_id===this._schedulerEntryId)||{};
    const schedules=entry.schedules||[]; const zones=this._settingsData?.cleaning_zones||[];
    this._statisticsEnsureDateRange();
    const quickRanges=this._statisticsQuickRanges();
    const filters=`<section class="entry-card statistics-filter-card"><div class="entry-header"><div><h2>${this._tr("panel.statistics")}</h2><div class="muted">${this._tr("panel.statistics_long_term_help")}</div></div><button class="ghost icon-only compact-icon-action statistics-refresh" title="${this._tr("panel.refresh")}" aria-label="${this._tr("panel.refresh")}">${this._mdi("refresh")}</button></div><div class="form-grid statistics-filters">
      <label class="field"><span>${this._tr("panel.date_from")}</span><input id="statistics-from" type="date" value="${this._escape(f.date_from||"")}"></label>
      <label class="field"><span>${this._tr("panel.date_to")}</span><input id="statistics-to" type="date" value="${this._escape(f.date_to||"")}"></label>
      <label class="field"><span>${this._tr("panel.schedule")}</span><select id="statistics-schedule"><option value="all">${this._tr("panel.all")}</option>${schedules.map(x=>`<option value="${this._escape(x.schedule_id)}" ${f.schedule_id===x.schedule_id?"selected":""}>${this._escape(x.name)}</option>`).join("")}</select></label>
      <label class="field"><span>${this._tr("panel.cleaning_zone")}</span><select id="statistics-zone"><option value="all">${this._tr("panel.all")}</option>${zones.map(x=>`<option value="${this._escape(x.zone_id)}" ${f.zone_id===x.zone_id?"selected":""}>${this._escape(x.name)}</option>`).join("")}</select></label>
      <label class="field"><span>${this._tr("panel.execution_mode")}</span><select id="statistics-execution"><option value="all">${this._tr("panel.all")}</option><option value="REAL" ${f.execution_mode==="REAL"?"selected":""}>${this._tr("panel.execution_real")}</option><option value="DRY_RUN" ${f.execution_mode==="DRY_RUN"?"selected":""}>Dry-Run</option></select></label>
      <label class="field"><span>${this._tr("panel.source")}</span><select id="statistics-source"><option value="all">${this._tr("panel.all")}</option><option value="SCHEDULED" ${f.execution_source==="SCHEDULED"?"selected":""}>${this._tr("panel.source_scheduled")}</option><option value="MANUAL" ${f.execution_source==="MANUAL"?"selected":""}>${this._tr("panel.source_manual")}</option><option value="EXTERNAL" ${f.execution_source==="EXTERNAL"?"selected":""}>${this._tr("panel.source_external")}</option><option value="FORCE" ${f.execution_source==="FORCE"?"selected":""}>${this._tr("panel.source_force")}</option></select></label>
    </div></section>`;
    const execution=a.execution||{};
    const summary=`<section class="statistics-tab-section statistics-summary-section"><div class="summary-grid statistics-summary-grid">
      <div class="summary-card"><span>${this._tr("panel.total")}</span><b>${fmt(a.records,0)} · ${a.records?"100%":"—"}</b></div><div class="summary-card"><span>${this._tr("panel.success")}</span><b>${fmt(execution.success,0)} · ${fmt(execution.success_rate)}%</b></div><div class="summary-card"><span>${this._tr("panel.partial")}</span><b>${fmt(execution.partial,0)} · ${fmt(execution.partial_rate)}%</b></div><div class="summary-card"><span>${this._tr("panel.not_executed")}</span><b>${fmt(execution.suppressed,0)} · ${fmt(execution.suppressed_rate)}%</b></div><div class="summary-card"><span>${this._tr("panel.errors")}</span><b>${fmt(execution.failed,0)} · ${fmt(execution.failure_rate)}%</b></div><div class="summary-card"><span>${this._tr("panel.scheduled_uncompleted")}</span><b>${fmt(execution.scheduled_uncompleted,0)} · ${execution.scheduled_uncompleted_rate===null||execution.scheduled_uncompleted_rate===undefined?"—":`${fmt(execution.scheduled_uncompleted_rate)}%`}</b></div><div class="summary-card statistics-metric-card"><span>${this._tr("panel.floor_area")}</span><b>${fmt(a.area?.floor_cleaned_m2??a.area?.physical_cleaned_m2)} m²</b><small class="statistics-card-note">${this._tr("panel.processed_area")}: ${fmt(a.area?.processed_m2)} m²</small></div>
    </div></section>`;
    const waitReasonEntries=this._mergedBlockerDurations(a.time?.wait_by_reason_seconds||{});
    const waitReasonTotal=waitReasonEntries.reduce((sum,[,seconds])=>sum+Math.max(0,Number(seconds)||0),0);
    const time=`<section class="statistics-tab-section"><div class="summary-grid statistics-summary-grid statistics-time-grid">${metricCard(this._tr("panel.physical_execution_time_short"),a.time?.execution_seconds,"time")}${weightedCard(this._tr("panel.time_per_m2"),a.time?.seconds_per_m2_weighted,"time_per_m2")}${metricCard(this._tr("panel.start_delay"),a.time?.start_delay_seconds,"time")}${metricCard(this._tr("panel.wait_time"),a.time?.wait_seconds,"time",`${this._tr("panel.wait_cycles")}: ${fmt(a.time?.wait_count,0)}`)}${metricCard(this._tr("panel.pause"),a.time?.pause_seconds,"time")}</div>${waitReasonEntries.length?`<div class="table-wrap"><table class="mobile-card-table statistics-wait-reasons-table"><thead><tr><th>${this._tr("panel.reason")}</th><th class="metric-header">${this._tr("panel.total_time")}</th><th class="metric-header">${this._tr("panel.share")}</th></tr></thead><tbody>${waitReasonEntries.map(([reason,seconds])=>`<tr><td data-label="${this._tr("panel.reason")}">${this._escape(this._blockerLabel(reason))}</td><td class="metric-number" data-label="${this._tr("panel.total_time")}">${duration(seconds)}</td><td class="metric-number" data-label="${this._tr("panel.share")}">${waitReasonTotal?`${fmt(Math.max(0,Number(seconds)||0)/waitReasonTotal*100,1)}%`:"—"}</td></tr>`).join("")}</tbody></table></div>`:""}</section>`;
    const charging=d.charging||{}; const activeCharge=charging.active||null;
    const chargeDetail=activeCharge?this._tr("panel.charge_observing_now",{start:fmt(activeCharge.start_percent,0),current:fmt(activeCharge.last_percent,0)}):this._tr("panel.charge_sessions",{count:fmt(charging.session_count||0,0)});
    const chargeSourceHelp=a.battery?.charge_rate_source==="job_observation_fallback"&&a.battery?.charge_rate_percent_per_minute?.count?this._tr("panel.charge_rate_job_fallback"):this._tr("panel.charge_rate_continuous_help");
    const chargeRate=a.battery?.charge_rate_percent_per_minute;
    const chargeCard=`<div class="summary-card statistics-metric-card statistics-charge-card"><span>${this._tr("panel.charge_rate_short")}</span><b>${metric(chargeRate,"median","%/min")}</b><small class="statistics-percentile">${this._formatPercentileLabel(distributionPercentile(chargeRate,configuredPercentile("battery")))}: ${metric(chargeRate,"percentile","%/min")}</small><small class="statistics-card-note statistics-charge-detail">${this._escape(chargeDetail)}</small><small class="statistics-card-note statistics-charge-explanation">${chargeSourceHelp}</small></div>`;
    const battery=`<section class="statistics-tab-section"><div class="summary-grid statistics-summary-grid statistics-battery-grid">${metricCard(this._tr("panel.battery_per_job"),a.battery?.consumed_percent,"%")}${weightedCard(this._tr("panel.battery_per_m2"),a.battery?.consumed_percent_per_m2_weighted,"%/m²")}${chargeCard}</div></section>`;
    const reasonTotal=(a.reasons||[]).reduce((sum,item)=>sum+Number(item.count||0),0);
    const reasons=`<section class="statistics-tab-section statistics-summary-reasons"><div class="history-report-section-head"><div><h4>${this._tr("panel.reasons")}</h4><span>${this._tr("panel.failure_reasons_share_help")}</span></div></div>${(a.reasons||[]).length?`<div class="table-wrap"><table class="mobile-card-table statistics-reasons-table"><thead><tr><th>${this._tr("panel.reason")}</th><th class="metric-header">${this._tr("panel.count")}</th><th class="metric-header">${this._tr("panel.share")}</th></tr></thead><tbody>${a.reasons.map(x=>`<tr><td data-label="${this._tr("panel.reason")}">${this._escape(this._reasonLabel(x.reason_code))}</td><td class="metric-number" data-label="${this._tr("panel.count")}">${fmt(x.count,0)}</td><td class="metric-number" data-label="${this._tr("panel.share")}">${reasonTotal?`${fmt(Number(x.count||0)/reasonTotal*100,1)}%`:"—"}</td></tr>`).join("")}</tbody></table></div>`:`<div class="empty compact">${this._tr("panel.no_failure_reasons")}</div>`}</section>`;
    const profileTable=(rows,{kind,withAttribution=false,help=""})=>{
      const objectLabel=kind==="schedule"?this._tr("panel.job"):this._tr("panel.cleaning_zone");
      const cols=withAttribution?14:13;
      const timeLabel=this._formatPercentileLabel(configuredPercentile("time"));
      const batteryLabel=this._formatPercentileLabel(configuredPercentile("battery"));
      const areaLabel=this._formatPercentileLabel(configuredPercentile("time"));
      const cleanLabel=this._formatPercentileLabel(configuredPercentile("clean_water"));
      const dirtyLabel=this._formatPercentileLabel(configuredPercentile("dirty_water"));
      const subheads=[[this._tr("panel.median"),timeLabel],[this._tr("panel.median"),batteryLabel],[this._tr("panel.median"),areaLabel],[this._tr("panel.median"),cleanLabel],[this._tr("panel.median"),dirtyLabel]];
      const header=`<thead><tr><th rowspan="2">${objectLabel}</th><th rowspan="2">${this._tr("panel.profile")}</th><th rowspan="2" class="metric-header">${this._tr("panel.result")}</th>${withAttribution?`<th rowspan="2" class="metric-header">${this._tr("panel.attribution")}</th>`:""}<th colspan="2" class="metric-group-head">${this._tr("panel.time")}</th><th colspan="2" class="metric-group-head">${this._tr("panel.battery_percent_header")}</th><th colspan="2" class="metric-group-head">${this._tr("panel.floor_area_m2_header")}</th><th colspan="2" class="metric-group-head">${this._tr("panel.clean_water_ml_per_m2_header")}</th><th colspan="2" class="metric-group-head">${this._tr("panel.dirty_water_ml_per_m2_header")}</th></tr><tr class="statistics-zone-subhead">${subheads.map(([med,p])=>`<th class="metric-header">${med}</th><th class="metric-header">${p}</th>`).join("")}</tr></thead>`;
      const body=rows.map(x=>{
        const name=kind==="schedule"?x.schedule_name:x.zone_name;
        return `<tr><td data-label="${objectLabel}"><b>${this._escape(name||"—")}</b></td><td class="statistics-profile-cell" data-label="${this._tr("panel.profile")}">${this._escape(profileLabel(x.profile_params))}</td><td class="metric-number" data-label="${this._tr("panel.result")}">${fmt(x.success,0)}/${fmt(x.total,0)} ${this._tr("panel.success_short")}</td>${withAttribution?`<td class="metric-number" data-label="${this._tr("panel.attribution")}">${this._tr("panel.measured")}: ${fmt(x.measured,0)} · ${this._tr("panel.estimated")}: ${fmt(x.estimated,0)}</td>`:""}<td class="metric-number" data-label="${this._tr("panel.time")} · ${this._tr("panel.median")}">${tableTime(x.cleaning_seconds,"median")}</td><td class="metric-number" data-label="${this._tr("panel.time")} · ${timeLabel}">${tableTime(x.cleaning_seconds,"percentile")}</td><td class="metric-number" data-label="${this._tr("panel.battery_percent_header")} · ${this._tr("panel.median")}">${tableMetric(x.battery_consumed_percent,"median")}</td><td class="metric-number" data-label="${this._tr("panel.battery_percent_header")} · ${batteryLabel}">${tableMetric(x.battery_consumed_percent,"percentile")}</td><td class="metric-number" data-label="${this._tr("panel.floor_area_m2_header")} · ${this._tr("panel.median")}">${tableMetric(x.floor_area_m2||x.area_m2,"median")}</td><td class="metric-number" data-label="${this._tr("panel.floor_area_m2_header")} · ${areaLabel}">${tableMetric(x.floor_area_m2||x.area_m2,"percentile")}</td><td class="metric-number" data-label="${this._tr("panel.clean_water_ml_per_m2_header")} · ${this._tr("panel.median")}">${tableMetric(x.clean_water_ml_eq_per_m2,"median")}</td><td class="metric-number" data-label="${this._tr("panel.clean_water_ml_per_m2_header")} · ${cleanLabel}">${tableMetric(x.clean_water_ml_eq_per_m2,"percentile")}</td><td class="metric-number" data-label="${this._tr("panel.dirty_water_ml_per_m2_header")} · ${this._tr("panel.median")}">${tableMetric(x.dirty_water_ml_eq_per_m2,"median")}</td><td class="metric-number" data-label="${this._tr("panel.dirty_water_ml_per_m2_header")} · ${dirtyLabel}">${tableMetric(x.dirty_water_ml_eq_per_m2,"percentile")}</td></tr>`;
      }).join("")||`<tr><td colspan="${cols}" class="empty-cell">${this._tr("panel.no_statistics_yet")}</td></tr>`;
      return `<section class="statistics-tab-section">${help?`<div class="muted statistics-tab-caption">${help}</div>`:""}<div class="table-wrap statistics-profile-table-wrap"><table class="mobile-card-table statistics-profile-table">${header}<tbody>${body}</tbody></table></div></section>`;
    };
    const scheduleTable=profileTable(a.by_schedule||[],{kind:"schedule",help:this._tr("panel.schedule_profile_statistics_help")});
    const zoneTable=profileTable(a.zones?.rows||[],{kind:"zone",withAttribution:true,help:`${this._tr("panel.profile_statistics_help")} ${this._tr("panel.attribution_help")}`});
    const water=this._statisticsWaterHtml(d.water||{},fmt,a);
    const forecastReady=this._forecastDataEntryId===this._schedulerEntryId && !!this._forecastData;
    const forecastError=this._forecastError;
    let forecast;
    if (forecastReady) {
      try {
        forecast=this._statisticsForecastHtml(this._forecastData);
      } catch (err) {
        const renderError=this._errorText(err);
        forecast=`<section class="statistics-tab-section"><div class="empty"><b>${this._tr("panel.forecast_load_failed")}</b><div class="muted">${this._escape(renderError)}</div><div class="button-row center-actions"><button class="ghost icon-only compact-icon-action forecast-retry" title="${this._tr("panel.refresh")}" aria-label="${this._tr("panel.refresh")}">${this._mdi("refresh")}</button></div></div></section>`;
      }
    } else if (forecastError) {
      forecast=`<section class="statistics-tab-section"><div class="empty"><b>${this._tr("panel.forecast_load_failed")}</b><div class="muted">${this._escape(forecastError)}</div><div class="button-row center-actions"><button class="ghost icon-only compact-icon-action forecast-retry" title="${this._tr("panel.refresh")}" aria-label="${this._tr("panel.refresh")}">${this._mdi("refresh")}</button></div></div></section>`;
    } else {
      forecast=`<section class="statistics-tab-section"><div class="empty">${this._tr("panel.forecast_loading")}</div></section>`;
    }
    const tabs=[["summary","view-dashboard-outline",this._tr("panel.history_tab_summary")],["time","timeline-clock-outline",this._tr("panel.history_tab_time")],["jobs","clipboard-text-clock-outline",this._tr("panel.history_tab_jobs")],["zones","floor-plan",this._tr("panel.history_tab_zones")],["battery","battery-50",this._tr("panel.battery_statistics")],["water","water-outline",this._tr("panel.water")],["forecast","chart-bell-curve-cumulative",this._tr("panel.forecast_tab")]];
    const requestedTab=this._statisticsTab||"summary";
    const active=tabs.some(([key])=>key===requestedTab)?requestedTab:"summary";
    const periodNav=`<div class="statistics-period-tabs" role="group" aria-label="${this._tr("panel.quick_periods")}">${quickRanges.map(range=>{const selected=f.date_from===range.from&&f.date_to===range.to;return `<button type="button" class="statistics-period-toggle ${selected?"selected":""}" data-statistics-range="${range.key}" data-statistics-from="${range.from}" data-statistics-to="${range.to}" aria-pressed="${selected?"true":"false"}">${this._escape(range.label)}</button>`;}).join("")}</div>`;
    const tabNav=`<div class="history-report-tabs statistics-report-tabs" role="tablist">${tabs.map(([key,icon,label])=>`<button type="button" class="history-report-tab ${active===key?"selected":""}" data-statistics-tab="${key}" role="tab" aria-selected="${active===key?"true":"false"}">${this._mdi(icon)}<span>${this._escape(label)}</span></button>`).join("")}</div>`;
    const recordsCaption=active==="forecast"?`<div class="muted statistics-global-caption">${this._tr("panel.forecast_filters_independent")}</div>`:`<div class="muted statistics-global-caption">${this._tr("panel.statistics_records_found",{count:d.total_records??0})}</div>`;
    const sections={summary:`${summary}${reasons}`,time,jobs:scheduleTable,zones:zoneTable,battery,water,forecast};
    return `${this._entrySelectorHtml()}${active==="forecast"?"":filters}<section class="entry-card statistics-report-card">${active==="forecast"?"":periodNav}${tabNav}${recordsCaption}<div class="statistics-tab-body">${sections[active]||summary}</div></section>`;
  }


  _maintenanceActionLabel(tank, interpretation) {
    const action=String(interpretation?.action||"");
    const value=interpretation?.value;
    const key=action==="level"
      ? (tank==="clean"?"panel.service_clean_level":"panel.service_dirty_level")
      : {full:"panel.service_full",empty:"panel.service_empty",add_ml:"panel.service_add_ml",remove_ml:"panel.service_remove_ml",noop:"panel.service_noop",unknown:"panel.service_unknown"}[action];
    if(!key)return "—";
    return this._tr(key,{value:value??"—"});
  }

  _maintenanceTankLabel(tank) {
    return tank==="clean"?this._tr("panel.clean_tank"):this._tr("panel.dirty_tank");
  }

  _maintenanceTanks(session) {
    const detected=((session?.detection||{}).items||[]).map(x=>String(x.tank||"")).filter(x=>x==="clean"||x==="dirty");
    const interpreted=Object.keys(session?.interpretations||{}).filter(x=>x==="clean"||x==="dirty");
    return [...new Set([...detected,...interpreted])];
  }

  _maintenanceActionsText(session) {
    const values=Object.entries(session?.interpretations||{}).map(([tank,value])=>`${this._maintenanceTankLabel(tank)} — ${this._maintenanceActionLabel(tank,value)}`);
    return values.length?values.join("; "):"—";
  }

  _maintenanceDetectedHtml(session) {
    const items=((session?.detection||{}).items||[]);
    if(!items.length)return `<div class="muted">${this._tr("panel.manual_maintenance")}</div>`;
    return `<div class="maintenance-detected-list">${items.map(item=>{const text=item.removed_at?this._tr("panel.tank_removed_returned",{removed:this._formatDateTime(item.removed_at),returned:this._formatDateTime(item.returned_at)}):this._tr("panel.resource_recovery_detected",{returned:this._formatDateTime(item.returned_at)});return `<div><b>${this._escape(this._maintenanceTankLabel(item.tank))}</b><span>${this._escape(text)}</span></div>`;}).join("")}</div>`;
  }

  _maintenanceStatusCardHtml() {
    const m=this._settingsData?.attention?.maintenance||{};
    const clean=m.clean||{}; const dirty=m.dirty||{}; const summary=m.summary||{};
    const percent=(v)=>Number.isFinite(Number(v))?`${Math.round(Number(v))}%`:"—";
    const pendingSessions=m.pending_sessions||[]; const pendingTanks=new Set(pendingSessions.flatMap(session=>this._maintenanceTanks(session)));
    const pending=Number(summary.pending_count||pendingSessions.length||0);
    const cleanValue=pendingTanks.has("clean")?this._tr("panel.pending"):percent(clean.remaining_percent);
    const dirtyValue=pendingTanks.has("dirty")?this._tr("panel.pending"):percent(dirty.filled_percent);
    return `<section class="entry-card maintenance-status-card"><div class="entry-header"><div><h2>${this._tr("panel.maintenance")}</h2><div class="muted">${this._tr("panel.maintenance_status_help")}</div></div><div class="button-row"><button type="button" class="ghost maintenance-add">${this._mdi("plus")}<span>${this._tr("panel.add_maintenance")}</span></button><button type="button" class="primary maintenance-open">${this._mdi("tools")}<span>${this._tr("panel.open_maintenance")}</span></button></div></div>
      <div class="maintenance-status-grid"><div><span>${this._tr("panel.clean_water_remaining_short")}</span><b>${cleanValue}</b></div><div><span>${this._tr("panel.dirty_water_filled_short")}</span><b>${dirtyValue}</b></div><div class="${pending?"maintenance-attention":""}"><span>${this._tr("panel.maintenance_requires_confirmation")}</span><b>${pending}</b></div><div><span>${this._tr("panel.last_maintenance")}</span><b>${summary.last_service_at?this._formatDateTime(summary.last_service_at):this._tr("panel.no_maintenance_yet")}</b></div></div>
    </section>`;
  }

  _maintenanceDraftFor(session=null) {
    const source=session?.source||"manual";
    const detected=this._maintenanceTanks(session);
    const current=session?.interpretations||{};
    const values={};
    for(const tank of ["clean","dirty"]) {
      const existing=current[tank];
      if(existing) values[tank]={action:String(existing.action||""),value:existing.value??""};
      else if(source==="detected" && detected.includes(tank)) values[tank]={action:tank==="clean"?"full":"empty",value:""};
      else values[tank]={action:"__none__",value:""};
    }
    return {session_id:session?.session_id||null,source,session:session||null,values,note:session?.note||""};
  }

  _maintenanceActionOptions(tank, selected, allowNone=true) {
    const rows=[];
    if(allowNone)rows.push(["__none__",this._tr("panel.no_action_for_tank")]);
    if(tank==="clean") rows.push(["full",this._tr("panel.filled_completely")],["add_ml",this._tr("panel.added_partial_water")]);
    else rows.push(["empty",this._tr("panel.emptied_completely")],["remove_ml",this._tr("panel.drained_partial_water")]);
    rows.push(["level",this._tr(tank==="clean"?"panel.set_clean_remaining":"panel.set_dirty_filled")],["noop",this._tr("panel.just_removed_reinserted")],["unknown",this._tr("panel.unknown_service_action")]);
    return rows.map(([value,label])=>`<option value="${value}" ${selected===value?"selected":""}>${this._escape(label)}</option>`).join("");
  }

  _maintenanceEditorHtml() {
    const draft=this._maintenanceDraft;
    if(!draft)return "";
    const detected=new Set(this._maintenanceTanks(draft.session));
    const row=(tank)=>{
      const item=draft.values[tank]||{action:"__none__",value:""};
      const required=draft.source==="detected"&&detected.has(tank);
      const needsValue=["level","add_ml","remove_ml"].includes(item.action);
      const label=item.action==="level"?this._tr(tank==="clean"?"panel.clean_water_remaining_percent":"panel.dirty_water_filled_percent"):this._tr("panel.volume_ml");
      const levelHelp=item.action==="level"?this._tr(tank==="clean"?"panel.clean_level_percent_help":"panel.dirty_level_percent_help"):"";
      return `<div class="maintenance-tank-editor"><div class="maintenance-tank-head"><b>${this._escape(this._maintenanceTankLabel(tank))}</b>${required?`<span class="plain-status warning-text">${this._tr("panel.maintenance_requires_confirmation")}</span>`:""}</div><label><span>${this._tr("panel.maintenance_interpretation")}</span><select data-maintenance-action="${tank}">${this._maintenanceActionOptions(tank,item.action,!required)}</select></label>${needsValue?`<label><span>${label}</span><input type="number" min="${item.action==="level"?"0":"1"}" max="${item.action==="level"?"100":""}" value="${this._escape(item.value)}" data-maintenance-value="${tank}">${levelHelp?`<small class="muted">${this._escape(levelHelp)}</small>`:""}</label>`:""}</div>`;
    };
    const revising=draft.session?.status==="confirmed";
    const title=draft.session_id?(revising?this._tr("panel.edit_maintenance"):this._tr("panel.confirm_maintenance")):this._tr("panel.add_maintenance");
    return `<section class="entry-card maintenance-editor"><div class="entry-header"><div><h2>${title}</h2>${draft.session?`<div class="muted">${this._formatDateTime(draft.session.occurred_at||draft.session.detected_at)}</div>`:""}</div></div>${draft.session?this._maintenanceDetectedHtml(draft.session):""}<div class="maintenance-editor-grid">${row("clean")}${row("dirty")}</div><label class="maintenance-note"><span>${this._tr("panel.maintenance_note")}</span><textarea data-maintenance-note rows="2">${this._escape(draft.note||"")}</textarea></label>${revising?`<div class="readiness readiness-wait maintenance-recalc-note">${this._mdi("calculator-variant-outline")}<span>${this._tr("panel.maintenance_recalculation_notice")}</span></div>`:""}<div class="button-row"><button type="button" class="primary maintenance-save">${this._mdi("content-save-outline")}<span>${this._tr("panel.save_maintenance")}</span></button><button type="button" class="ghost maintenance-cancel">${this._tr("panel.cancel_edit")}</button></div></section>`;
  }

  _maintenanceSessionCardHtml(session, pending=false) {
    const source=session.source==="detected"?this._tr("panel.detected_maintenance"):this._tr("panel.manual_maintenance");
    const revisions=session.revisions||[];
    return `<div class="maintenance-session ${pending?"pending":""}"><div class="maintenance-session-head"><div><b>${this._formatDateTime(session.occurred_at||session.detected_at)}</b><div class="muted">${source}</div></div><div class="button-row">${pending?`<button type="button" class="primary maintenance-confirm" data-maintenance-session="${this._escape(session.session_id)}">${this._tr("panel.confirm_maintenance")}</button>`:`<button type="button" class="ghost maintenance-edit" data-maintenance-session="${this._escape(session.session_id)}">${this._mdi("pencil-outline")}<span>${this._tr("panel.edit_maintenance")}</span></button>`}</div></div>${this._maintenanceDetectedHtml(session)}${pending?`<div class="muted maintenance-pending-help">${this._tr("panel.maintenance_pending_help")}</div>`:`<div class="maintenance-actions-summary"><b>${this._tr("panel.maintenance_actions")}</b><span>${this._escape(this._maintenanceActionsText(session))}</span></div>`}${session.note?`<div class="maintenance-note-view">${this._escape(session.note)}</div>`:""}${!pending?`<details class="maintenance-revisions"><summary>${this._tr("panel.revision_history")} · ${revisions.length}</summary>${revisions.length?`<div class="maintenance-revision-list">${revisions.slice().reverse().map(r=>`<div><b>${this._formatDateTime(r.changed_at)}</b><span>${this._escape(this._maintenanceActionsText({interpretations:r.previous?.interpretations||{}}))}</span></div>`).join("")}</div>`:`<div class="muted">${this._tr("panel.no_revisions")}</div>`}</details>`:""}</div>`;
  }

  _maintenanceHtml() {
    const data=this._maintenanceData||this._settingsData?.attention?.maintenance||{};
    const pending=data.pending_sessions||[]; const sessions=data.sessions||[];
    const confirmed=sessions.filter(s=>s.status==="confirmed");
    const clean=data.clean||{}; const dirty=data.dirty||{}; const fmt=v=>Number.isFinite(Number(v))?`${Math.round(Number(v))}%`:"—";
    const pendingTanks=new Set(pending.flatMap(session=>this._maintenanceTanks(session)));
    const cleanValue=pendingTanks.has("clean")?this._tr("panel.pending"):fmt(clean.remaining_percent);
    const dirtyValue=pendingTanks.has("dirty")?this._tr("panel.pending"):fmt(dirty.filled_percent);
    return `${this._entrySelectorHtml()}<div class="maintenance-page-head"><button type="button" class="ghost maintenance-back">${this._mdi("arrow-left")}<span>${this._tr("panel.back_to_status")}</span></button><button type="button" class="primary maintenance-add">${this._mdi("plus")}<span>${this._tr("panel.add_maintenance")}</span></button></div>${this._maintenanceEditorHtml()}<section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.current_water_state")}</h2></div></div><div class="summary-grid"><div class="summary-card"><span>${this._tr("panel.clean_water_remaining_short")}</span><b>${cleanValue}</b></div><div class="summary-card"><span>${this._tr("panel.dirty_water_filled_short")}</span><b>${dirtyValue}</b></div><div class="summary-card"><span>${this._tr("panel.maintenance_requires_confirmation")}</span><b>${pending.length}</b></div><div class="summary-card"><span>${this._tr("panel.last_maintenance")}</span><b>${data.summary?.last_service_at?this._formatDateTime(data.summary.last_service_at):"—"}</b></div></div></section><section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.pending_maintenance")}</h2><div class="muted">${this._tr("panel.maintenance_pending_help")}</div></div></div>${pending.length?`<div class="maintenance-list">${pending.map(s=>this._maintenanceSessionCardHtml(s,true)).join("")}</div>`:`<div class="empty compact">${this._tr("panel.no_pending_maintenance")}</div>`}</section><section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.maintenance_history")}</h2><div class="muted">${this._tr("panel.maintenance_history_help")}</div></div></div>${confirmed.length?`<div class="maintenance-list">${confirmed.map(s=>this._maintenanceSessionCardHtml(s,false)).join("")}</div>`:`<div class="empty compact">${this._tr("panel.no_maintenance_history")}</div>`}</section>`;
  }

  _statisticsWaterHtml(water,fmt,aggregate={}) {
    const profile=water.profile; const clean=water.clean||{}; const dirty=water.dirty||{}; const cal=water.calibration||{};
    if(!profile) return `<section class="statistics-tab-section"><div class="muted statistics-tab-caption">${this._tr("panel.water_profile_unavailable_help")}</div></section>`;
    const liters=(v)=>v===null||v===undefined?"—":`${fmt(Number(v)/1000,2)} ${this._tr("panel.l_unit")}`;
    const confidence=(v)=>this._tr(`panel.confidence_${String(v||"unknown")}`);
    const model=(v)=>this._tr(`panel.model_${String(v||"unavailable")}`);
    return `<section class="statistics-tab-section water-statistics-card"><div class="muted statistics-tab-caption">${this._escape(profile.model||profile.label||profile.profile_id||"")}</div>
      <div class="summary-grid statistics-summary-grid statistics-water-grid"><div class="summary-card statistics-metric-card statistics-water-metric"><span>${this._tr("panel.clean_water_remaining_short")}</span><b>${fmt(clean.remaining_percent)}% · ${liters(clean.estimate_ml_eq)}</b><small class="statistics-card-note">${this._tr("panel.state_confidence")}: ${confidence(clean.state_confidence)} · ${this._tr("panel.model_quality")}: ${model(cal.clean_model_quality)}</small></div><div class="summary-card statistics-metric-card statistics-water-metric"><span>${this._tr("panel.dirty_water_filled_short")}</span><b>${fmt(dirty.filled_percent)}% · ${liters(dirty.estimate_ml_eq)}</b><small class="statistics-card-note statistics-water-free">${this._tr("panel.free")}: ${fmt(dirty.free_percent)}% · ${liters(dirty.free_ml_eq)}</small><small class="statistics-card-note statistics-water-confidence">${this._tr("panel.state_confidence")}: ${confidence(dirty.state_confidence)} · ${this._tr("panel.model_quality")}: ${model(cal.dirty_model_quality)}</small></div><div class="summary-card statistics-metric-card"><span>${this._tr("panel.clean_water_per_m2")}</span><b>${aggregate.water_usage?.clean_ml_eq_per_m2_weighted?.value===null||aggregate.water_usage?.clean_ml_eq_per_m2_weighted?.value===undefined?"—":`${fmt(aggregate.water_usage.clean_ml_eq_per_m2_weighted.value,1)} ${this._tr("panel.ml_per_m2_unit")}`}</b></div><div class="summary-card statistics-metric-card"><span>${this._tr("panel.dirty_water_per_m2")}</span><b>${aggregate.water_usage?.dirty_ml_eq_per_m2_weighted?.value===null||aggregate.water_usage?.dirty_ml_eq_per_m2_weighted?.value===undefined?"—":`${fmt(aggregate.water_usage.dirty_ml_eq_per_m2_weighted.value,1)} ${this._tr("panel.ml_per_m2_unit")}`}</b></div></div>
      <details><summary>${this._tr("panel.water_model_details")}</summary><div class="technical-json"><pre>${this._escape(JSON.stringify({profile,calibration:cal,recent_cycles:water.recent_cycles||[]},null,2))}</pre></div></details>
    </section>`;
  }

  _tabsHtml() {
    const tabs = [["status", "view-dashboard-outline", this._tr("panel.status")], ["schedules", "calendar-clock", this._tr("panel.schedules")], ["statistics", "chart-box-outline", this._tr("panel.statistics")], ["notifications", "bell-outline", this._tr("panel.notifications")], ["settings", "cog-outline", this._tr("panel.settings")], ["testing", "flask-outline", this._tr("panel.dry_run_tab")]];
    return `<div class="tabs">${tabs.map(([key,icon,label])=>`<button class="tab ${(this._view===key||(key==="status"&&this._view==="maintenance"))?"active":""}" data-view="${key}">${this._mdi(icon)}<span>${label}</span></button>`).join("")}</div>`;
  }

  _optionsHtml(options, selected, includeNone = false, kind = null) {
    const selectedText = String(selected ?? "");
    const base = includeNone
      ? [{ value: "vacuum_schedule_no_override", label: this._noOverrideLabel(), raw_value: null }, ...options]
      : [...options];
    const visible = base.filter((item) => {
      const raw = item.raw_value ?? (kind ? this._controlRawValue(item) : item.value);
      return !this._isOpaquePreset(raw) || String(item.value) === selectedText;
    });
    return visible.map((item) => {
      let label = item.label;
      if (String(item.value) === "vacuum_schedule_no_override") {
        label = this._noOverrideLabel();
      } else if (kind) {
        const raw = item.raw_value ?? this._controlRawValue(item);
        const prefix = item.source_label ?? this._controlSourcePrefix(item);
        const localized = this._presetLabel(kind, raw);
        label = prefix ? `${prefix}: ${localized}` : localized;
      }
      return `<option value="${this._escape(item.value)}" ${String(item.value) === selectedText ? "selected" : ""}>${this._escape(label)}</option>`;
    }).join("");
  }

  _simpleOptions(values, selected, includeBlank = false, kind = null) {
    const selectedText = String(selected ?? "");
    const visible = values.filter((value) => !this._isOpaquePreset(value) || String(value) === selectedText);
    const items = includeBlank ? ["", ...visible] : visible;
    return items.map((value) => {
      const label = value === "" ? this._noOverrideLabel() : (kind ? this._presetLabel(kind, value) : value);
      return `<option value="${this._escape(value)}" ${String(value) === selectedText ? "selected" : ""}>${this._escape(label)}</option>`;
    }).join("");
  }

  _weekdayDraftTimes() {
    const codes=["mon","tue","wed","thu","fri","sat","sun"];
    const values=this._form.weekday_times||{};
    const draft=this._form._weekday_draft_times||{};
    const firstEnabled=codes.find((code)=>Object.prototype.hasOwnProperty.call(values,code));
    const fallback=String((firstEnabled&&values[firstEnabled])||this._form.local_time||"09:00").slice(0,5)||"09:00";
    for(const code of codes){
      if(!draft[code]) draft[code]=String(values[code]||fallback).slice(0,5);
      if(Object.prototype.hasOwnProperty.call(values,code)) draft[code]=String(values[code]||draft[code]||fallback).slice(0,5);
    }
    this._form._weekday_draft_times=draft;
    return draft;
  }

  _weekdayScheduleRows() {
    const values=this._form.weekday_times||{};
    const draft=this._weekdayDraftTimes();
    return ["mon","tue","wed","thu","fri","sat","sun"].map((code) => {
      const enabled=Object.prototype.hasOwnProperty.call(values,code);
      const value=String(enabled?values[code]:draft[code]||"09:00").slice(0,5);
      return `<div class="weekday-schedule-row ${enabled?"enabled":""}">
        <label class="weekday-enable"><input type="checkbox" data-weekday-enabled="${code}" ${enabled?"checked":""}><b>${this._escape(this._weekdayLabel(code))}</b></label>
        <input type="time" data-weekday-time="${code}" value="${this._escape(value)}" ${enabled?"":"disabled"}>
      </div>`;
    }).join("");
  }

  _weekdayBulkTimeValue() {
    if(this._form._weekday_bulk_time) return String(this._form._weekday_bulk_time).slice(0,5);
    const values=this._form.weekday_times||{};
    const enabled=["mon","tue","wed","thu","fri","sat","sun"].find((code)=>Object.prototype.hasOwnProperty.call(values,code));
    return String((enabled&&values[enabled])||this._weekdayDraftTimes().mon||"09:00").slice(0,5);
  }

  _applyWeekdayBulkTime(value) {
    const normalized=String(value||"").slice(0,5);
    if(!/^\d{2}:\d{2}$/.test(normalized)) return false;
    const draft=this._weekdayDraftTimes();
    this._form.weekday_times=this._form.weekday_times||{};
    for(const code of ["mon","tue","wed","thu","fri","sat","sun"]){
      draft[code]=normalized;
      if(Object.prototype.hasOwnProperty.call(this._form.weekday_times,code)) this._form.weekday_times[code]=normalized;
    }
    this._form._weekday_bulk_time=normalized;
    return true;
  }

  _weekdayBulkTimeHtml() {
    return `<div class="weekday-bulk-time"><label class="field"><span>${this._tr("panel.weekday_bulk_time")}</span><input type="time" data-weekday-bulk-time value="${this._escape(this._weekdayBulkTimeValue())}"></label><button type="button" class="ghost weekday-bulk-apply">${this._mdi("clock-check-outline")}<span>${this._tr("panel.apply_to_enabled_days")}</span></button></div>`;
  }

  _dailyOverrideSelect(kind, code, current) {
    const options = this._editor.controls?.[kind] || [];
    const selected = String(current || "");
    const known = new Set(options.map((item) => String(item.value)));
    const rendered = [...options];
    if (selected && !known.has(selected)) rendered.push({value:selected,label:`⚠ ${selected}`});
    return `<select data-day-override="${code}" data-day-override-field="${kind}"><option value="">${this._tr("panel.use_base_profile")}</option>${rendered.map((item)=>`<option value="${this._escape(item.value)}" ${String(item.value)===selected?"selected":""}>${this._escape(this._presetLabel(kind,this._controlRawValue(item)))}</option>`).join("")}</select>`;
  }

  _dailyOverrideRows() {
    const enabled = Object.keys(this._form.weekday_times || {});
    if (!enabled.length) return `<div class="muted">${this._tr("panel.enable_weekly_days_first")}</div>`;
    const overrides = this._form.weekday_overrides || {};
    const fanModes = this._editor.fan_modes || [];
    return `<div class="day-overrides">${enabled.map((code)=>{
      const row = overrides[code] || {};
      const fan = String(row.fan_mode || "");
      const passes = row.passes ?? "";
      return `<div class="day-override-row">
        <div class="day-override-name"><b>${this._escape(this._weekdayLabel(code))}</b><small>${this._escape(String((this._form.weekday_times||{})[code]||"").slice(0,5))}</small></div>
        <label><span>${this._tr("panel.cleaning_type")}</span>${this._dailyOverrideSelect("cleaning_mode",code,row.cleaning_mode)}</label>
        <label><span>${this._tr("panel.suction_power")}</span><select data-day-override="${code}" data-day-override-field="fan_mode"><option value="">${this._tr("panel.use_base_profile")}</option>${fanModes.filter((v)=>!this._isOpaquePreset(v)||String(v)===fan).map((v)=>`<option value="${this._escape(v)}" ${String(v)===fan?"selected":""}>${this._escape(this._presetLabel("fan_mode",v))}</option>`).join("")}</select></label>
        <label><span>${this._tr("panel.mopping_mode")}</span>${this._dailyOverrideSelect("mop_mode",code,row.mop_mode)}</label>
        <label><span>${this._tr("panel.water_flow_intensity")}</span>${this._dailyOverrideSelect("water_mode",code,row.water_mode)}</label>
        <label><span>${this._tr("panel.passes")}</span><select data-day-override="${code}" data-day-override-field="passes"><option value="">${this._tr("panel.use_base_profile")}</option>${Array.from({length:10},(_,i)=>i+1).map((v)=>`<option value="${v}" ${Number(passes)===v?"selected":""}>${v}</option>`).join("")}</select></label>
      </div>`;
    }).join("")}</div>`;
  }

  _newForceCondition(type="binary") {
    const condition_id=`fc_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,8)}`;
    const base={condition_id,type,for_minutes:0};
    if(type==="numeric") return {...base,entity_id:"",operator:"gte",value:0};
    if(type==="entity") return {...base,entity_id:"",operator:"eq",value:"on"};
    if(type==="person") return {...base,entity_id:"",state:"not_home"};
    if(type==="people_absent") return {...base,entity_ids:[],minimum_absent:1};
    return {...base,entity_id:"",state:"on"};
  }

  _forcePeopleSelector(condition, groupIndex, conditionIndex) {
    const people=(this._settingsData?.entities||[]).filter((item)=>String(item.entity_id||"").startsWith("person."));
    const selected=new Set((condition.entity_ids||[]).map(String));
    if(!people.length) return `<div class="muted">${this._tr("panel.no_person_entities")}</div>`;
    return `<div class="force-people-grid">${people.map((item)=>{
      const id=String(item.entity_id||"");
      const label=item.name&&item.name!==id?item.name:id;
      return `<label class="force-person-choice"><input type="checkbox" data-force-person data-force-group="${groupIndex}" data-force-condition="${conditionIndex}" data-entity-id="${this._escape(id)}" ${selected.has(id)?"checked":""}><span><b>${this._escape(label)}</b><small>${this._escape(id)}</small></span></label>`;
    }).join("")}</div>`;
  }

  _forceConditionRow(condition, groupIndex, conditionIndex) {
    const type=String(condition.type||"binary");
    const attrs=`data-force-group="${groupIndex}" data-force-condition="${conditionIndex}"`;
    const entityField=(domains="")=>`<div class="field"><span>${this._tr("panel.entity_0e084dd")}</span>${this._entitySearchHtml(condition.entity_id||"",`${attrs} data-force-condition-field="entity_id"`)}</div>`;
    let body="";
    if(type==="entity") {
      body=`${entityField()}<label class="field"><span>${this._tr("panel.operator")}</span><select ${attrs} data-force-condition-field="operator"><option value="eq" ${condition.operator==="eq"?"selected":""}>=</option><option value="ne" ${condition.operator==="ne"?"selected":""}>≠</option></select></label><label class="field"><span>${this._tr("panel.value")}</span><input ${attrs} data-force-condition-field="value" type="text" value="${this._escape(condition.value??"")}"></label>`;
    } else if(type==="numeric") {
      const op=String(condition.operator||"gte");
      body=`${entityField()}<label class="field"><span>${this._tr("panel.operator")}</span><select ${attrs} data-force-condition-field="operator"><option value="gt" ${op==="gt"?"selected":""}>&gt;</option><option value="gte" ${op==="gte"?"selected":""}>≥</option><option value="lt" ${op==="lt"?"selected":""}>&lt;</option><option value="lte" ${op==="lte"?"selected":""}>≤</option></select></label><label class="field"><span>${this._tr("panel.value")}</span><input ${attrs} data-force-condition-field="value" type="number" step="any" value="${this._escape(condition.value??0)}"></label>`;
    } else if(type==="person") {
      body=`${entityField("person")}<label class="field"><span>${this._tr("panel.required_state")}</span><select ${attrs} data-force-condition-field="state"><option value="home" ${condition.state==="home"?"selected":""}>${this._tr("panel.at_home")}</option><option value="not_home" ${condition.state!=="home"?"selected":""}>${this._tr("panel.away_from_home")}</option></select></label>`;
    } else if(type==="people_absent") {
      body=`<div class="field wide"><span>${this._tr("panel.selected_people")}</span>${this._forcePeopleSelector(condition,groupIndex,conditionIndex)}</div><label class="field"><span>${this._tr("panel.minimum_absent_people")}</span><input ${attrs} data-force-condition-field="minimum_absent" type="number" min="1" max="${Math.max(1,(condition.entity_ids||[]).length)}" value="${this._escape(condition.minimum_absent??1)}"></label>`;
    } else {
      body=`${entityField("binary_sensor")}<label class="field"><span>${this._tr("panel.required_state")}</span><select ${attrs} data-force-condition-field="state"><option value="on" ${condition.state!=="off"?"selected":""}>ON</option><option value="off" ${condition.state==="off"?"selected":""}>OFF</option></select></label>`;
    }
    return `<div class="force-condition-row" ${attrs}><div class="force-condition-head"><label class="field"><span>${this._tr("panel.condition_type")}</span><select ${attrs} data-force-condition-field="type"><option value="entity" ${type==="entity"?"selected":""}>${this._tr("panel.force_type_entity")}</option><option value="numeric" ${type==="numeric"?"selected":""}>${this._tr("panel.force_type_numeric")}</option><option value="person" ${type==="person"?"selected":""}>${this._tr("panel.force_type_person")}</option><option value="people_absent" ${type==="people_absent"?"selected":""}>${this._tr("panel.force_type_people_absent")}</option><option value="binary" ${type==="binary"?"selected":""}>${this._tr("panel.force_type_binary")}</option></select></label><label class="field"><span>${this._tr("panel.for_minutes")}</span><input ${attrs} data-force-condition-field="for_minutes" type="number" min="0" max="10080" step="1" value="${this._escape(condition.for_minutes??0)}"></label><button type="button" class="danger icon-only compact-icon-action force-remove-condition" ${attrs} title="${this._tr("panel.remove_condition")}">${this._mdi("delete-outline")}</button></div><div class="force-condition-fields">${body}</div></div>`;
  }

  _forceEditorHtml() {
    const enabled=!!this._form.force_enabled;
    if(!enabled) return "";
    const groups=this._form.force_condition_groups||[];
    const names=this._form.force_condition_group_names||[];
    return `<div class="field wide force-editor"><div class="force-editor-head"><div><span class="editor-section-title">${this._tr("panel.early_conditions")}</span><small class="help">${this._tr("panel.force_engine_help")}</small></div></div><div class="form-grid force-main-fields"><label class="field"><span class="field-label-row"><span>${this._tr("panel.force_priority")}</span><small>${this._tr("panel.force_priority_help")}</small></span><input type="number" min="0" max="1000000" step="1" data-force-field="priority" value="${this._escape(this._form.force_priority??100)}"></label><div class="field force-preempt-field"><label class="inline-check"><input type="checkbox" data-force-field="preempts_scheduled" ${this._form.force_preempts_scheduled?"checked":""}> ${this._tr("panel.force_preempts_scheduled")}</label><small class="help">${this._tr("panel.force_preempts_scheduled_help")}</small></div></div><div class="force-logic-help">${this._tr("panel.force_logic_help")}</div><div class="force-groups">${groups.map((group,gi)=>{const fallback=this._tr("panel.force_group_number",{number:gi+1});const groupName=String(names[gi]||fallback);return `${gi?`<div class="force-or-divider">${this._tr("panel.or")}</div>`:""}<div class="force-group"><div class="force-group-head"><input class="force-group-name-input" type="text" maxlength="120" data-force-group-name="${gi}" value="${this._escape(groupName)}" placeholder="${this._escape(fallback)}" aria-label="${this._escape(this._tr("panel.condition_group_name"))}" title="${this._escape(this._tr("panel.condition_group_name_help"))}"><div><button type="button" class="ghost small force-add-condition" data-force-group="${gi}">${this._mdi("plus")}<span>${this._tr("panel.condition")}</span></button><button type="button" class="danger icon-only compact-icon-action force-remove-group" data-force-group="${gi}" title="${this._tr("panel.remove_group")}">${this._mdi("delete-outline")}</button></div></div>${group.map((condition,ci)=>`${ci?`<div class="force-and-divider">AND</div>`:""}${this._forceConditionRow(condition,gi,ci)}`).join("")||`<div class="empty compact">${this._tr("panel.no_conditions_in_group")}</div>`}</div>`;}).join("")||`<div class="empty compact">${this._tr("panel.no_force_groups")}</div>`}</div><button type="button" class="ghost force-add-group">${this._mdi("plus")}<span>${this._tr("panel.add_or_group")}</span></button></div>`;
  }

  _scheduleTimingHtml() {
    const forceEnabled=!!this._form.force_enabled;
    const windowValue=this._durationHmEditorValue(this._form.execution_window_minutes,120);
    const earlyValue=this._durationHmEditorValue(this._form.force_max_advance_minutes,360);
    const minimumWindowValue=this._form.minimum_start_window_minutes===""||this._form.minimum_start_window_minutes===null||this._form.minimum_start_window_minutes===undefined?"":this._durationHmEditorValue(this._form.minimum_start_window_minutes,0);
    return `<div class="field wide schedule-timing-editor"><span class="editor-section-title">${this._tr("panel.time_parameters")}</span><small class="help">${this._tr("panel.time_parameters_help")}</small><div class="form-grid schedule-timing-grid"><label class="field"><span>${this._tr("panel.execution_window_hm")}</span><input data-duration-minutes="execution_window_minutes" type="text" inputmode="numeric" pattern="\\d{1,3}:[0-5]\\d" value="${this._escape(windowValue)}" placeholder="02:00"><small class="help">${this._tr("panel.execution_window_hm_help")}</small></label><label class="field"><span>${this._tr("panel.minimum_remaining_start_window_hm")}</span><input data-duration-minutes="minimum_start_window_minutes" data-duration-optional="1" type="text" inputmode="numeric" pattern="\\d{1,3}:[0-5]\\d" value="${this._escape(minimumWindowValue)}" placeholder="${this._tr("panel.global")}"><small class="help">${this._tr("panel.minimum_remaining_start_window_hm_help")}</small></label><div class="field"><span>${this._tr("panel.early_start")}</span><label class="switch-row"><input type="checkbox" data-force-field="enabled" ${forceEnabled?"checked":""}><span>${forceEnabled?this._tr("panel.enabled_e5a7db1"):this._tr("panel.disabled_2d3f5b5")}</span></label></div>${forceEnabled?`<label class="field"><span>${this._tr("panel.maximum_advance_hm")}</span><input data-duration-minutes="force_max_advance_minutes" type="text" inputmode="numeric" pattern="\\d{1,3}:[0-5]\\d" value="${this._escape(earlyValue)}" placeholder="06:00"><small class="help">${this._tr("panel.maximum_advance_hm_help")}</small></label>`:""}</div>${forceEnabled?this._forceEditorHtml():""}</div>`;
  }

  _targetEditorHtml() {
    const selected = new Set((this._form.targets || []).map(String));
    const source = this._editor.cleaning_zones || [];
    if (!source.length) return `<div class="warning">${this._tr("panel.create_at_least_one_cleaning_zone_in_settings_first")}</div>`;
    return `<div class="target-grid">${source.map((item) => `<label class="target-choice"><input type="checkbox" data-target="${this._escape(item.value)}" ${selected.has(String(item.value)) ? "checked" : ""}><span><b>${this._escape(item.label)}</b><small>${this._escape(this._targetTypeLabel(item.robot_target_type))} · ${this._escape(item.robot_target_id)}</small></span></label>`).join("")}</div>`;
  }


  _controlField(key, label) {
    const options = this._editor.controls?.[key] || [];
    const current = this._form[key] || "vacuum_schedule_no_override";
    if (!options.length && current === "vacuum_schedule_no_override") return "";
    const known = new Set(options.map((x) => String(x.value)));
    const rendered = [...options];
    if (current !== "vacuum_schedule_no_override" && !known.has(String(current))) {
      rendered.push({ value: current, label: `⚠ ${current}`, raw_value: this._controlRawValue({ value: current, label: current }) });
    }
    return `<label class="field"><span>${label}</span><small class="help">${this._escape(this._fieldHelp(key))}</small><select data-field="${key}">${this._optionsHtml(rendered, current, true, key)}</select></label>`;
  }

  _editorHtml() {
    const isEdit = !!this._editor.schedule_id;
    const title = isEdit
      ? this._tr("panel.edit_value", {p1: this._form.name})
      : this._tr("panel.new_schedule");
    const fans = this._editor.fan_modes || [];
    const fanCurrent = this._form.fan_mode || "";
    const fanField = fans.length || fanCurrent
      ? `<label class="field"><span>${this._tr("panel.suction_power")}</span><small class="help">${this._escape(this._fieldHelp("fan_mode"))}</small><select data-field="fan_mode">${this._simpleOptions(fans.includes(fanCurrent) ? fans : [...fans, fanCurrent].filter(Boolean), fanCurrent, true, "fan_mode")}</select></label>`
      : "";
    const notificationPolicy = this._form.notification_policy || {};
    const notificationMode = notificationPolicy.mode || "inherit";
    const notificationOverrides = notificationPolicy.overrides || {};
    const inheritOption = `<option value="">${this._tr("panel.use_global")}</option>`;
    const multiZone = (this._form.targets || []).length > 1;
    const zoneExecutionPolicy = String(this._form.zone_execution_policy || "combined");
    return `
      <section class="editor-card">
        <div class="editor-header">
          <div><h2>${this._escape(title)}</h2><div class="muted">${this._escape(this._editor.entry_title)}${isEdit ? ` · revision ${this._editor.revision}` : ""}</div></div>
          <button class="ghost icon-only close" title="${this._tr("panel.close")}">${this._mdi("close")}</button>
        </div>
        <div class="form-grid">
          <label class="field wide"><span>${this._tr("panel.name")}</span><input data-field="name" type="text" value="${this._escape(this._form.name || "")}"></label>
          <label class="switch-row"><input data-field="enabled" type="checkbox" ${this._form.enabled ? "checked" : ""}><span>${this._tr("panel.schedule_enabled")}</span></label>
          <div class="field wide schedule-weekly-editor"><span class="editor-section-title">${this._tr("panel.weekly_schedule")}</span><small class="help">${this._tr("panel.weekly_schedule_help")}</small>${this._weekdayBulkTimeHtml()}<div class="weekday-schedule-grid">${this._weekdayScheduleRows()}</div></div>
          <label class="field"><span class="specific-dates-label">${this._tr("panel.specific_dates_yyyy_mm_dd")}</span><small class="help">${this._tr("panel.specific_dates_help")}</small><input data-field="dates" type="text" value="${this._escape(this._form.dates || "")}" placeholder="2026-08-15, 2026-08-20"></label>
          <label class="field"><span>${this._tr("panel.specific_dates_time")}</span><small class="help">${this._tr("panel.specific_dates_time_help")}</small><input data-field="local_time" type="time" value="${this._escape(String(this._form.local_time || "09:00").slice(0,5))}"></label>
          ${this._scheduleTimingHtml()}
          <div class="field wide editor-subsection-heading"><span class="editor-section-title">${this._tr("panel.base_cleaning_profile")}</span><small class="help">${this._tr("panel.base_cleaning_profile_help")}</small></div>
          <div class="field wide"><span>${this._tr("panel.cleaning_zones")}</span><small class="help">${this._tr("panel.one_schedule_may_contain_multiple_independent_cleaning_zones")}</small>${this._targetEditorHtml()}</div>
          ${multiZone ? `<label class="field wide"><span>${this._tr("panel.zone_execution_policy")}</span><small class="help">${this._tr("panel.help.zone_execution_policy")}</small><select data-field="zone_execution_policy"><option value="combined" ${zoneExecutionPolicy==="combined"?"selected":""}>${this._tr("panel.zone_execution_combined")}</option><option value="progressive" ${zoneExecutionPolicy==="progressive"?"selected":""}>${this._tr("panel.zone_execution_progressive")}</option></select></label>` : ""}
          ${fanField}
          ${this._controlField("cleaning_mode", this._tr("panel.cleaning_type"))}
          ${this._controlField("cleaning_route", this._tr("panel.cleaning_route"))}
          ${this._controlField("mop_mode", this._tr("panel.mopping_mode"))}
          ${this._controlField("water_mode", this._tr("panel.water_flow_intensity"))}
          <label class="field"><span>${this._tr("panel.passes_d88b442")}</span><small class="help">${this._escape(this._fieldHelp("passes"))}</small><select data-field="passes">${Array.from({length:10},(_,i)=>i+1).map((v)=>`<option value="${v}" ${Number(this._form.passes)===v?"selected":""}>${v}</option>`).join("")}</select></label>
          <div class="field wide schedule-day-overrides"><span class="editor-section-title">${this._tr("panel.daily_corrections")}</span><small class="help">${this._tr("panel.daily_corrections_help")}</small>${this._dailyOverrideRows()}</div>
        </div>
        <div class="settings-subsection schedule-start-restrictions"><h4>${this._tr("panel.start_restrictions")}</h4><div class="muted">${this._tr("panel.start_restrictions_help")}</div>
          <div class="form-grid">
            <label class="field"><span>${this._tr("panel.minimum_remaining_battery")}</span><small class="help">${this._tr("panel.blank_use_the_global_value_from_schedule_execution")}</small><input data-field="minimum_battery_percent" type="number" min="0" max="100" step="1" value="${this._escape(this._form.minimum_battery_percent ?? "")}" placeholder="${this._tr("panel.global")}"></label>
          </div>
        </div>
        <div class="settings-subsection schedule-notifications"><h4>${this._tr("panel.notifications")}</h4><div class="muted">${this._tr("panel.notification_changes_do_not_change_the_execution_revision_or_invalidate_")}</div>
          <div class="form-grid">
            <label class="field"><span>${this._tr("panel.prewarning_minutes")}</span><input data-field="prewarning_minutes" type="number" min="0" max="1440" step="1" value="${this._escape(this._form.prewarning_minutes ?? 15)}"></label>
            <label class="field"><span>${this._tr("panel.mode")}</span><select id="schedule-notification-mode"><option value="inherit" ${notificationMode==="inherit"?"selected":""}>${this._tr("panel.use_global_settings")}</option><option value="disabled" ${notificationMode==="disabled"?"selected":""}>${this._tr("panel.disable_for_this_schedule")}</option><option value="custom" ${notificationMode==="custom"?"selected":""}>${this._tr("panel.custom_settings")}</option></select></label>
            ${notificationMode==="custom"?`
              <label class="field"><span>${this._tr("panel.prewarning")}</span><select data-schedule-notification="prewarning_mode">${inheritOption}${this._notificationModeOptions("prewarning_mode",notificationOverrides.prewarning_mode)}</select></label>
              <label class="field"><span>${this._tr("panel.wait_entry")}</span><select data-schedule-notification="wait_enter_mode">${inheritOption}${this._notificationModeOptions("wait_enter_mode",notificationOverrides.wait_enter_mode)}</select></label>
              <label class="field"><span>${this._tr("panel.wait_delay_seconds")}</span><input data-schedule-notification-number="wait_delay_seconds" type="number" min="0" value="${this._escape(notificationOverrides.wait_delay_seconds??"")}" placeholder="${this._tr("panel.global_67206dd")}"></label>
              <label class="field"><span>${this._tr("panel.wait_reminders")}</span><select data-schedule-notification-bool="wait_reminder_enabled">${inheritOption}<option value="true" ${notificationOverrides.wait_reminder_enabled===true?"selected":""}>${this._tr("panel.enabled_e5a7db1")}</option><option value="false" ${notificationOverrides.wait_reminder_enabled===false?"selected":""}>${this._tr("panel.disabled_2d3f5b5")}</option></select></label>
              <label class="field"><span>${this._tr("panel.reminder_interval_minutes")}</span><input data-schedule-notification-minutes="wait_reminder_interval_seconds" type="number" min="1" value="${notificationOverrides.wait_reminder_interval_seconds?Math.round(notificationOverrides.wait_reminder_interval_seconds/60):""}" placeholder="${this._tr("panel.global_67206dd")}"></label>
              <label class="field"><span>${this._tr("panel.maximum_reminders")}</span><input data-schedule-notification-number="wait_reminder_max_count" type="number" min="0" value="${this._escape(notificationOverrides.wait_reminder_max_count??"")}" placeholder="${this._tr("panel.global_67206dd")}"></label>
              <label class="field"><span>${this._tr("panel.start_43805bb")}</span><select data-schedule-notification="start_mode">${inheritOption}${this._notificationModeOptions("start_mode",notificationOverrides.start_mode)}</select></label>
              <label class="field"><span>${this._tr("panel.result")}</span><select data-schedule-notification="finish_mode">${inheritOption}${this._notificationModeOptions("finish_mode",notificationOverrides.finish_mode)}</select></label>
              <label class="field"><span>${this._tr("panel.start_forecast")}</span><select data-schedule-notification="start_forecast_mode">${inheritOption}${this._notificationModeOptions("start_forecast_mode",notificationOverrides.start_forecast_mode)}</select></label>
              <label class="field"><span>${this._tr("panel.forecast_interval")}</span><input data-schedule-notification-minutes="start_forecast_interval_seconds" type="number" min="1" value="${notificationOverrides.start_forecast_interval_seconds?Math.round(notificationOverrides.start_forecast_interval_seconds/60):""}" placeholder="${this._tr("panel.global_67206dd")}"></label>
              <label class="field"><span>${this._tr("panel.forecast_deadline")}</span><input data-schedule-notification-number="start_forecast_deadline_minutes" type="number" min="0" value="${this._escape(notificationOverrides.start_forecast_deadline_minutes??"")}" placeholder="${this._tr("panel.global_67206dd")}"><small class="help">${this._tr("panel.forecast_zero_off")}</small></label>`:""}
            <div class="field wide"><span>${this._tr("panel.recipients_for_this_schedule")}</span><small class="help">${this._tr("panel.default_all_globally_enabled_recipients")}</small>
              <label class="switch-row"><input id="schedule-notification-all-recipients" type="checkbox" ${Object.prototype.hasOwnProperty.call(notificationPolicy,"recipient_ids")?"":"checked"}><span>${this._tr("panel.all_recipients")}</span></label>
              ${Object.prototype.hasOwnProperty.call(notificationPolicy,"recipient_ids")?`<div class="notification-event-checks">${(this._notificationData?.settings?.recipients||[]).map(r=>`<label class="inline-check"><input type="checkbox" data-schedule-recipient="${this._escape(r.recipient_id)}" ${(notificationPolicy.recipient_ids||[]).includes(r.recipient_id)?"checked":""}> ${this._escape(r.name||r.recipient_id)}</label>`).join("")||this._tr("panel.no_recipients_configured_yet")}</div>`:""}
            </div>
          </div>
        </div>
        <div class="editor-actions">
          <button class="ghost close">${this._mdi("close")}<span>${this._tr("panel.cancel")}</span></button>
          <button class="primary save" ${this._saving ? "disabled" : ""}>${this._mdi(this._saving ? "loading" : "content-save-outline")}<span>${this._saving ? this._tr("panel.saving") : this._tr("panel.save")}</span></button>
        </div>
      </section>`;
  }

  async _debugAction(type, extra = {}, successMessage = null) {
    const entry = this._statusEntry();
    if (!entry || this._loading) return;
    this._loading = true; this._error = null; this._syncLoadingIndicator();
    try {
      await this._callWs({ type, entry_id: entry.entry_id, ...extra });
      await this._loadScheduler();
      if (successMessage) this._notify(successMessage);
    } catch (err) { this._error = this._errorText(err); this._notify(this._error, "error", 4200); }
    finally { this._loading = false; this._render(); }
  }

  _focusDeepLinkTarget() {
    if (!this.shadowRoot) return;
    const deliveryId=this._notificationHistorySelectedId;
    if (this._view==="notifications" && deliveryId) {
      const row=[...this.shadowRoot.querySelectorAll(".notification-history-row")].find(el=>el.dataset.deliveryId===deliveryId);
      if(row){setTimeout(()=>row.scrollIntoView({block:"center",behavior:"smooth"}),0);}
    }
    if (this._view==="status" && this._deepLinkJobId) {
      const job=[...this.shadowRoot.querySelectorAll("[data-job-id]")].find(el=>el.dataset.jobId===this._deepLinkJobId);
      if(job){if(job.tagName==="DETAILS")job.open=true;job.classList.add("deep-link-target");setTimeout(()=>job.scrollIntoView({block:"center",behavior:"smooth"}),0);}
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const sectionTitle = this._view === "status" ? this._tr("panel.page_scheduler_status") : this._view === "maintenance" ? this._tr("panel.page_maintenance") : this._view === "statistics" ? this._tr("panel.page_statistics") : this._view === "notifications" ? this._tr("panel.page_user_notifications") : this._view === "settings" ? this._tr("panel.page_scheduler_settings") : this._view === "testing" ? this._tr("panel.page_dry_run_parameters") : this._tr("panel.page_cleaning_schedule");
    const entryTitle = this._currentEntryTitle();
    const appBarTitle = `${this._tr("panel.vacuum_schedule")}${entryTitle ? ` — ${entryTitle}` : ""}`;
    let content = "";
    const settingsEditing = this._view === "settings" && (!!this._roomDraft || !!this._settingsEditor);
    const notificationEditing = this._view === "notifications" && !!this._notificationRecipientDraft;
    const editingPage = !!this._editor || settingsEditing || notificationEditing;
    if (this._editor) content = this._editorHtml();
    else if (this._view === "notifications" && this._notificationRecipientDraft) content = `${this._entrySelectorHtml()}${this._notificationRecipientEditorHtml()}`;
    else if (this._view === "settings" && this._roomDraft) content = `${this._entrySelectorHtml()}${this._roomEditorHtml()}`;
    else if (this._view === "settings" && this._settingsEditor) content = this._bindingSingleEditorHtml(this._settingsEditor);
    else if (this._view === "status") content = this._statusHtml();
    else if (this._view === "maintenance") content = this._maintenanceHtml();
    else if (this._view === "statistics") content = this._statisticsHtml();
    else if (this._view === "notifications") content = this._notificationsHtml();
    else if (this._view === "settings") content = this._settingsHtml();
    else if (this._view === "testing") content = this._testingHtml();
    else content = this._data ? this._listHtml() : "";
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--ha-card-header-font-family, inherit); color-scheme:inherit;
          --vs-card-surface:var(--ha-card-background,var(--card-background-color));
          --vs-raised-surface:var(--wa-color-surface-raised,var(--ha-dialog-surface-background,var(--mdc-theme-surface,var(--vs-card-surface))));
          --vs-border:var(--ha-card-border-color,var(--ha-color-border-neutral-quiet,var(--divider-color)));
          --vs-control-surface:var(--wa-form-control-background-color,var(--input-fill-color,var(--vs-raised-surface)));
          --vs-control-border:var(--wa-form-control-border-color,var(--input-outlined-idle-border-color,var(--divider-color)));
          --vs-control-text:var(--wa-form-control-value-color,var(--input-ink-color,var(--primary-text-color)));
          --vs-brand-fill:var(--wa-color-brand-fill-normal,var(--ha-color-fill-primary-normal-resting,var(--primary-color)));
          --vs-brand-fill-hover:var(--ha-color-fill-primary-normal-hover,var(--vs-brand-fill));
          --vs-brand-fill-active:var(--ha-color-fill-primary-normal-active,var(--vs-brand-fill-hover));
          --vs-brand-on:var(--wa-color-brand-on-normal,var(--ha-color-on-primary-normal,var(--text-primary-color)));
          --vs-brand-quiet-fill:var(--ha-color-fill-primary-quiet-resting,var(--wa-color-brand-fill-quiet,var(--secondary-background-color)));
          --vs-brand-quiet-hover:var(--ha-color-fill-primary-quiet-hover,var(--vs-brand-quiet-fill));
          --vs-brand-quiet-on:var(--wa-color-brand-on-quiet,var(--ha-color-on-primary-quiet,var(--primary-color)));
          --vs-neutral-fill:var(--wa-color-neutral-fill-normal,var(--ha-color-fill-neutral-normal-resting,var(--secondary-background-color)));
          --vs-neutral-fill-hover:var(--ha-color-fill-neutral-normal-hover,var(--vs-neutral-fill));
          --vs-neutral-fill-active:var(--ha-color-fill-neutral-normal-active,var(--vs-neutral-fill-hover));
          --vs-neutral-on:var(--wa-color-neutral-on-normal,var(--ha-color-on-neutral-normal,var(--primary-text-color)));
          --vs-neutral-quiet-fill:var(--ha-color-fill-neutral-quiet-resting,var(--wa-color-neutral-fill-quiet,var(--secondary-background-color)));
          --vs-neutral-quiet-hover:var(--ha-color-fill-neutral-quiet-hover,var(--vs-neutral-quiet-fill));
          --vs-neutral-quiet-on:var(--wa-color-neutral-on-quiet,var(--ha-color-on-neutral-quiet,var(--primary-text-color)));
          --vs-success-quiet-fill:var(--ha-color-fill-success-quiet-resting,var(--wa-color-success-fill-quiet,var(--secondary-background-color)));
          --vs-success-quiet-on:var(--wa-color-success-on-quiet,var(--ha-color-on-success-quiet,var(--success-color)));
          --vs-warning-quiet-fill:var(--ha-color-fill-warning-quiet-resting,var(--wa-color-warning-fill-quiet,var(--secondary-background-color)));
          --vs-warning-quiet-on:var(--wa-color-warning-on-quiet,var(--ha-color-on-warning-quiet,var(--warning-color)));
          --vs-danger-fill:var(--wa-color-danger-fill-normal,var(--ha-color-fill-danger-normal-resting,var(--error-color)));
          --vs-danger-fill-hover:var(--ha-color-fill-danger-normal-hover,var(--vs-danger-fill));
          --vs-danger-fill-active:var(--ha-color-fill-danger-normal-active,var(--vs-danger-fill-hover));
          --vs-danger-on:var(--wa-color-danger-on-normal,var(--ha-color-on-danger-normal,var(--text-primary-color)));
          --vs-danger-quiet-fill:var(--ha-color-fill-danger-quiet-resting,var(--wa-color-danger-fill-quiet,var(--secondary-background-color)));
          --vs-danger-quiet-on:var(--wa-color-danger-on-quiet,var(--ha-color-on-danger-quiet,var(--error-color)));
          --vs-disabled-fill:var(--ha-color-fill-disabled-normal-resting,var(--input-disabled-fill-color,var(--secondary-background-color)));
          --vs-disabled-on:var(--ha-color-on-disabled-normal,var(--disabled-text-color));
          --vs-table-header-bg:var(--table-header-background-color,var(--vs-neutral-quiet-fill));
          --vs-table-header-padding:10px 12px;
          --vs-table-row-padding:13px 12px;
          --vs-table-font-size:13px;
          --vs-table-header-font-size:12px;
          --vs-compact-action-size:28px;
          --vs-compact-action-icon-size:16px;
          --vs-compact-action-radius:7px;
        }
        .app-bar { position:sticky; top:0; z-index:5; height:var(--header-height, 56px); min-height:var(--header-height, 56px); display:flex; align-items:center; box-sizing:border-box; padding:0 24px; background:var(--app-header-background-color, var(--card-background-color)); color:var(--app-header-text-color, var(--primary-text-color)); border-bottom:var(--app-header-border-bottom,1px solid var(--divider-color)); }
        .app-bar-menu { width:48px; min-width:48px; height:48px; min-height:48px!important; padding:0!important; margin-inline-start:-12px; margin-inline-end:4px; border:0; border-radius:50%; background:transparent; color:inherit; box-shadow:none; }
        :host(:not([narrow])) .app-bar-menu { display:none!important; }
        .app-bar-menu:hover { background:var(--vs-neutral-quiet-hover); }
        .app-bar-menu:active { background:var(--vs-neutral-quiet-fill); }
        .app-bar-menu .button-icon { --mdc-icon-size:24px; width:24px; height:24px; flex-basis:24px; }
        .app-bar-title { min-width:0; flex:1 1 auto; font-size:20px; line-height:1.2; font-weight:500; letter-spacing:.01em; }
        .page { max-width:1500px; margin:0 auto; padding:20px; box-sizing:border-box; }
        .top { display:flex; gap:16px; align-items:center; justify-content:space-between; margin-bottom:12px; }
        h2 { margin:0 0 4px; font-size:20px; }
        .section-title { font-size:22px; line-height:1.25; font-weight:600; }
        .version { color:var(--secondary-text-color); font-size:13px; margin-top:3px; }
        .tabs { display:flex; gap:4px; border-bottom:1px solid var(--divider-color); margin-bottom:18px; }
        button.tab { border-radius:0; background:transparent; color:var(--secondary-text-color); padding:11px 16px; border-bottom:3px solid transparent; display:inline-flex; align-items:center; gap:7px; }
        button.tab.active { color:var(--primary-color); border-bottom-color:var(--primary-color); }
        button.tab:hover { background:var(--vs-neutral-quiet-hover); color:var(--primary-text-color); }
        .entry-card,.editor-card { background:var(--vs-card-surface); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow); border:var(--ha-card-border-width,1px) solid var(--vs-border); -webkit-backdrop-filter:var(--ha-card-backdrop-filter,none); backdrop-filter:var(--ha-card-backdrop-filter,none); padding:18px; margin-bottom:18px; }
        .channel-list { display:grid; gap:12px; margin-top:12px; }
        .channel-card { border:1px solid var(--vs-border); border-radius:10px; padding:14px; background:var(--vs-neutral-quiet-fill); }
        .channel-head,.subsection-header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
        .channel-head small,.notification-history small { display:block; color:var(--secondary-text-color); margin-top:3px; }
        .notification-history-row { cursor:pointer; }
        .notification-history-row:hover { background:var(--vs-brand-quiet-hover); }
        .notification-history-row.selected,.deep-link-target { outline:2px solid var(--wa-color-brand-border-normal,var(--primary-color)); outline-offset:-2px; background:var(--vs-brand-quiet-fill); }
        .notification-history-detail td { background:var(--vs-neutral-quiet-fill); padding:14px 16px!important; min-width:0; }
        .notification-delivery-detail-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 18px; min-width:0; }
        .notification-delivery-detail-grid .wide-detail { grid-column:1 / -1; }
        .delivery-field,.delivery-value,.delivery-job-context,.delivery-technical,.delivery-tech-item { min-width:0; }
        .delivery-value { margin-top:4px; overflow-wrap:anywhere; word-break:break-word; }
        .notification-message { margin:5px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; font:inherit; color:var(--primary-text-color); max-width:100%; }
        .delivery-job-context { padding:12px; border:1px solid var(--wa-color-brand-border-quiet,var(--vs-border)); border-radius:9px; background:var(--vs-brand-quiet-fill); }
        .delivery-job-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
        .physical-attempt-list { display:grid; gap:8px; margin-top:6px; }
        .physical-attempt-card { padding:9px 10px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .physical-attempt-card .muted { margin-top:3px; overflow-wrap:anywhere; }
        .delivery-job-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
        .delivery-job-action { min-height:38px; }
        .delivery-job-no-actions { margin-top:8px; }
        .delivery-technical { border-top:1px solid var(--divider-color); padding-top:10px; }
        .delivery-technical>summary { cursor:pointer; color:var(--secondary-text-color); font-weight:600; }
        .delivery-tech-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 16px; margin-top:10px; }
        .delivery-tech-value { display:flex; align-items:flex-start; gap:6px; min-width:0; margin-top:4px; }
        .delivery-tech-value code { display:block; min-width:0; max-width:100%; white-space:normal; overflow-wrap:anywhere; word-break:break-all; font-size:11px; }
        .delivery-copy { flex:0 0 var(--vs-compact-action-size); }
        .notification-message-row { cursor:pointer; }
        .notification-message-row:hover { background:var(--vs-neutral-quiet-hover); }
        .notification-message-list td { vertical-align:top; }
        .notification-message-list td:nth-child(2) { min-width:250px; }
        .notification-message-route-cell { min-width:220px; }
        .notification-message-delivery-cell { min-width:145px; }
        .notification-message-mode-cell { min-width:150px; }
        .notification-message-push-body { margin-top:3px; color:var(--secondary-text-color); white-space:pre-line; line-height:1.35; }
        .notification-message-routes{display:grid;gap:7px}.notification-message-route{display:grid;gap:1px;min-width:0}.notification-message-route-recipient{font-size:12px;font-weight:600;color:var(--primary-text-color)}.notification-message-route small{color:var(--secondary-text-color);font-size:10px;line-height:1.25}.notification-message-route code{font-family:var(--code-font-family,monospace);font-size:9px;color:var(--secondary-text-color);opacity:.82;overflow-wrap:anywhere;user-select:all}.notification-message-delivery{display:grid;gap:2px;justify-items:start}.notification-message-delivery-state,.notification-execution-mode{display:inline-flex;align-items:center;gap:7px;color:var(--primary-text-color);font-size:12px;font-weight:500;line-height:1.3}.notification-message-delivery small{color:var(--secondary-text-color);font-size:10px;line-height:1.25}.notification-message-delivery-reason{max-width:220px}.notification-status-dot,.notification-mode-dot{width:7px;height:7px;border-radius:50%;flex:0 0 7px;background:var(--secondary-text-color);opacity:.8}.notification-message-delivery-sent .notification-status-dot,.notification-execution-mode-real .notification-mode-dot{background:var(--success-color,var(--vs-success-quiet-on))}.notification-message-delivery-suppressed .notification-status-dot,.notification-message-delivery-pending .notification-status-dot,.notification-execution-mode-dry-run .notification-mode-dot{background:var(--warning-color,var(--vs-warning-quiet-on))}.notification-message-delivery-failed .notification-status-dot{background:var(--error-color,var(--vs-danger-quiet-on))}.notification-execution-mode{color:var(--primary-text-color);font-weight:500}.notification-message-list>tbody>tr>td:last-child{font-weight:500}
        .notification-message-page { max-width:920px; margin-left:auto; margin-right:auto; }
        .notification-message-nav { margin-bottom:14px; }
        .notification-message-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding-bottom:14px; border-bottom:1px solid var(--divider-color); }
        .notification-message-head h2 { margin:3px 0 0; font-size:22px; }
        .notification-message-kicker { color:var(--secondary-text-color); font-size:12px; }
        .notification-message-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0; margin-top:6px; }
        .notification-message-fact { display:grid; grid-template-columns:minmax(120px,38%) minmax(0,1fr); gap:12px; padding:10px 0; border-bottom:1px solid var(--divider-color); }
        .notification-message-fact:nth-child(odd) { padding-right:18px; }
        .notification-message-fact:nth-child(even) { padding-left:18px; border-left:1px solid var(--divider-color); }
        .notification-message-fact span { color:var(--secondary-text-color); font-size:12px; }
        .notification-message-fact b { font-weight:500; overflow-wrap:anywhere; }
        .notification-current-card { margin-top:18px; padding:14px; border:1px solid var(--wa-color-brand-border-quiet,var(--vs-border)); border-radius:10px; background:var(--vs-brand-quiet-fill); }
        .notification-current-card h3 { margin:0 0 7px; font-size:16px; }
        .notification-current-state { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
        .notification-current-detail { margin-top:9px; line-height:1.45; }
        .notification-current-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
        .notification-job-action { min-height:40px; }
        .notification-current-no-actions { margin-top:12px; }
        button.text-link { margin-top:12px; padding:4px 0; min-height:28px; background:transparent; border:0; box-shadow:none; color:var(--primary-color); display:inline-flex; align-items:center; gap:4px; }
        button.text-link:hover { text-decoration:underline; background:transparent; }
        .notification-message-technical { margin-top:16px; padding-top:12px; border-top:1px solid var(--divider-color); }
        .notification-message-technical>summary { cursor:pointer; color:var(--secondary-text-color); font-weight:500; }
        .notification-event-checks,.job-actions { display:flex; gap:8px 12px; align-items:center; flex-wrap:wrap; margin-top:10px; }
        .transport-options { margin-top:12px; border-top:1px solid var(--divider-color); padding-top:10px; }
        .transport-options summary { cursor:pointer; font-weight:500; }
        .notification-users-card .notification-users-header { margin:0 0 16px; }
        .notification-recipient-table { min-width:1180px; }
        .notification-recipient-table td { vertical-align:middle; }
        .notification-recipient-table td small { display:block; color:var(--secondary-text-color); font-size:10px; margin-top:3px; }
        .notification-recipient-table .notification-presence-detail { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .notification-recipient-channel-line,.notification-recipient-rule-line { display:block; line-height:1.35; }
        .notification-recipient-channel-line + .notification-recipient-channel-line,.notification-recipient-rule-line + .notification-recipient-rule-line { margin-top:6px; }
        .notification-recipient-channel-line.disabled { opacity:.58; }
        .presence-home { color:var(--vs-success-quiet-on); background:var(--vs-success-quiet-fill); }
        .presence-away { color:var(--vs-neutral-quiet-on); background:var(--vs-neutral-quiet-fill); }
        .presence-unknown { color:var(--vs-warning-quiet-on); background:var(--vs-warning-quiet-fill); }
        .presence-unavailable { color:var(--vs-danger-quiet-on); background:var(--vs-danger-quiet-fill); }
        .notification-recipient-table td:nth-child(1){min-width:170px}.notification-recipient-table td:nth-child(2){min-width:130px}.notification-recipient-table td:nth-child(3){min-width:235px}.notification-recipient-table td:nth-child(4){min-width:270px}.notification-recipient-table td:nth-child(5){min-width:320px}
        .notification-recipient-actions { width:1%; white-space:nowrap; text-align:right; }
        .table-actions-inner { display:flex; gap:4px; align-items:center; justify-content:flex-end; white-space:nowrap; }
        .notification-recipient-editor { max-width:1200px; margin-left:auto; margin-right:auto; border:2px solid var(--wa-color-brand-border-normal,var(--primary-color)); }
        .entry-header,.editor-header { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px; }
        .muted { color:var(--secondary-text-color); font-size:12px; margin-top:3px; overflow-wrap:anywhere; word-break:break-word; }
        .table-wrap { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; min-width:1050px; font-size:var(--vs-table-font-size); }
        thead { border-top:1px solid var(--divider-color); }
        th { text-align:left; color:var(--secondary-text-color); font-size:var(--vs-table-header-font-size); font-weight:600; padding:var(--vs-table-header-padding); border-bottom:1px solid var(--divider-color); background:var(--table-header-background-color,var(--vs-neutral-quiet-fill)); background:var(--vs-table-header-bg); white-space:nowrap; box-sizing:border-box; }
        td { padding:var(--vs-table-row-padding); border-bottom:1px solid var(--divider-color); vertical-align:top; box-sizing:border-box; }
        th.metric-header,td.metric-number { text-align:right; font-variant-numeric:tabular-nums; }
        tbody tr:last-child td { border-bottom:0; }
        .disabled-row { opacity:.58; }
        .name-cell { min-width:150px; }
        .status-dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:7px; }
        .status-dot.on { background:var(--success-color,var(--ha-color-fill-success-loud-resting)); } .status-dot.off { background:var(--disabled-text-color,var(--ha-color-fill-neutral-normal-resting)); }
        .ui-chip { display:inline-flex; align-items:center; gap:5px; width:max-content; max-width:100%; padding:4px 7px; border-radius:10px; font-size:11px; line-height:1.2; font-weight:700; box-sizing:border-box; vertical-align:middle; white-space:nowrap; }
        .parameter-chip,.reference-chip { color:var(--vs-neutral-quiet-on); background:var(--vs-neutral-quiet-fill); }
        .parameter-chip,.reference-chip,.target-chip,.blocker-chip { margin:2px 4px 2px 0; }
        .target-chip { color:var(--vs-brand-quiet-on); background:var(--vs-brand-quiet-fill); }
        .schedule-targets { display:flex; align-items:center; gap:5px 10px; flex-wrap:wrap; }
        .schedule-target-text { color:var(--vs-brand-quiet-on); font-weight:600; line-height:1.3; }
        .blocker-chip { color:var(--vs-warning-quiet-on); background:var(--vs-warning-quiet-fill); }
        .blocker-chip.blocker-fail { color:var(--vs-danger-quiet-on); background:var(--vs-danger-quiet-fill); }
        .actions { white-space:nowrap; }
        .schedule-actions-cell { width:1%; vertical-align:top; }
        .schedule-actions { display:flex; gap:4px; align-items:flex-start; flex-wrap:nowrap; white-space:nowrap; }
        button { border:1px solid transparent; border-radius:var(--ha-button-border-radius,var(--ha-border-radius-pill,20px)); padding:var(--ha-space-2,8px) var(--ha-space-3,12px); cursor:pointer; font:inherit; font-size:var(--ha-font-size-m,14px); font-weight:var(--ha-font-weight-medium,600); box-sizing:border-box; box-shadow:var(--ha-button-box-shadow,none); transition:background-color var(--ha-animation-duration-fast,.12s) ease,border-color var(--ha-animation-duration-fast,.12s) ease,color var(--ha-animation-duration-fast,.12s) ease,box-shadow var(--ha-animation-duration-fast,.12s) ease; }
        button:not(.tab):not(.search-select-option) { display:inline-flex; align-items:center; justify-content:center; gap:var(--ha-space-2,7px); min-height:var(--ha-button-height,36px); }
        button.primary { color:var(--vs-brand-on); background:var(--vs-brand-fill); border-color:transparent; }
        button.primary:hover { background:var(--vs-brand-fill-hover); }
        button.primary:active { background:var(--vs-brand-fill-active); }
        button.ghost { color:var(--vs-neutral-on); background:var(--vs-neutral-fill); border-color:transparent; }
        button.ghost:hover { background:var(--vs-neutral-fill-hover); }
        button.ghost:active { background:var(--vs-neutral-fill-active); }
        button.danger { color:var(--vs-danger-on); background:var(--vs-danger-fill); border-color:transparent; margin-left:4px; }
        button.danger:hover { background:var(--vs-danger-fill-hover); }
        button.danger:active { background:var(--vs-danger-fill-active); }
        button.icon-only { position:relative; display:inline-grid!important; place-items:center; width:var(--ha-button-height,36px); min-width:var(--ha-button-height,36px); height:var(--ha-button-height,36px); min-height:var(--ha-button-height,36px)!important; padding:0!important; gap:0!important; line-height:0; }
        .button-icon { --mdc-icon-size:18px; width:18px; height:18px; flex:0 0 18px; display:block; margin:0; }
        button.icon-only>.button-icon { position:absolute; left:50%; top:50%; margin:0!important; transform:translate(-50%,-50%); }
        /* Compact action geometry MUST come after the generic icon-only rule.
           Both rules use !important min-height; ordering here prevents the generic 36px
           control height from stretching compact actions into 28x36 rectangles. */
        button.icon-only.compact-icon-action {
          position:relative; display:inline-grid!important; place-items:center!important;
          inline-size:var(--vs-compact-action-size)!important; min-inline-size:var(--vs-compact-action-size)!important; max-inline-size:var(--vs-compact-action-size)!important;
          block-size:var(--vs-compact-action-size)!important; min-block-size:var(--vs-compact-action-size)!important; max-block-size:var(--vs-compact-action-size)!important;
          width:var(--vs-compact-action-size)!important; min-width:var(--vs-compact-action-size)!important; max-width:var(--vs-compact-action-size)!important;
          height:var(--vs-compact-action-size)!important; min-height:var(--vs-compact-action-size)!important; max-height:var(--vs-compact-action-size)!important;
          flex:0 0 var(--vs-compact-action-size)!important; aspect-ratio:1 / 1!important; box-sizing:border-box!important;
          padding:0!important; margin-left:0!important; border-radius:var(--vs-compact-action-radius)!important; gap:0!important; line-height:0!important;
          vertical-align:middle;
        }
        button.icon-only.compact-icon-action>.button-icon {
          --mdc-icon-size:var(--vs-compact-action-icon-size);
          position:absolute!important; left:50%!important; top:50%!important; transform:translate(-50%,-50%)!important;
          display:block!important; box-sizing:border-box!important;
          width:var(--vs-compact-action-icon-size)!important; min-width:var(--vs-compact-action-icon-size)!important; max-width:var(--vs-compact-action-icon-size)!important;
          height:var(--vs-compact-action-icon-size)!important; min-height:var(--vs-compact-action-icon-size)!important; max-height:var(--vs-compact-action-icon-size)!important;
          flex:0 0 var(--vs-compact-action-icon-size)!important; margin:0!important; padding:0!important; line-height:0!important;
        }
        button.save .button-icon[icon="mdi:loading"] { animation:spin .8s linear infinite; }
        @keyframes spin { to { transform:rotate(360deg); } }
        button:disabled { opacity:1; cursor:default; color:var(--vs-disabled-on)!important; background:var(--vs-disabled-fill)!important; border-color:transparent!important; }
        button:focus-visible { outline:2px solid var(--ha-color-focus,var(--primary-color)); outline-offset:2px; }
        .empty,.empty-cell { color:var(--secondary-text-color); padding:28px; text-align:center; }
        .error { padding:12px 14px; margin-bottom:16px; border-radius:8px; background:var(--vs-danger-quiet-fill); color:var(--vs-danger-quiet-on); }
        .loading-indicator { position:absolute; right:20px; top:50%; transform:translateY(-50%); color:var(--secondary-text-color); font-size:12px; pointer-events:none; visibility:hidden; opacity:0; transition:opacity .12s linear; }
        .loading-indicator.visible { visibility:visible; opacity:1; }
        .content-shell { min-height:1px; min-width:0; max-width:100%; }
        .form-grid { display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:15px 18px; }
        .field { display:flex; flex-direction:column; gap:7px; min-width:0; }
        .field > span { font-size:13px; color:var(--secondary-text-color); }
        .help { display:block; color:var(--secondary-text-color); font-size:11px; line-height:1.35; margin-top:-2px; }
        .field.wide { grid-column:1 / -1; }
        .notification-policy-message-type { margin-top:8px; }
        input[type=text], input[type=time], input[type=date], input[type=number], input[type=datetime-local], select, textarea { box-sizing:border-box; width:100%; color:var(--vs-control-text); background:var(--vs-control-surface); border:1px solid var(--vs-control-border); border-radius:var(--ha-border-radius-md,8px); padding:10px; font:inherit; }
        textarea { resize:vertical; }
        input:focus-visible,select:focus-visible,textarea:focus-visible { outline:2px solid var(--ha-color-focus,var(--primary-color)); outline-offset:1px; border-color:var(--primary-color); }
        .search-select { position:relative; width:100%; }
        .search-select-input { width:100%; }
        .search-select-menu { display:none; position:absolute; z-index:20; left:0; right:0; top:calc(100% + 4px); max-height:min(240px,42vh); overflow-y:auto; overscroll-behavior:contain; background:var(--vs-raised-surface); border:1px solid var(--vs-control-border); border-radius:var(--ha-border-radius-md,8px); box-shadow:var(--ha-card-box-shadow,none); padding:4px; }
        .search-select.open .search-select-menu { display:block; }
        button.search-select-option { display:flex; flex-direction:column; align-items:flex-start; width:100%; padding:8px 10px; text-align:left; background:transparent; color:var(--primary-text-color); font-weight:500; }
        button.search-select-option:hover { background:var(--vs-neutral-quiet-hover); }
        .search-select-option-main { display:flex; align-items:center; gap:8px; width:100%; min-width:0; }
        .search-select-option-main > span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .status-badge { background:var(--vs-neutral-quiet-fill); color:var(--vs-neutral-quiet-on); }
        .search-select-badge { margin-left:auto; flex:0 0 auto; background:var(--vs-neutral-quiet-fill); color:var(--vs-neutral-quiet-on); }
        button.search-select-option[hidden] { display:none !important; }
        button.search-select-option:hover, button.search-select-option:focus { background:var(--vs-neutral-quiet-hover); outline:none; }
        button.search-select-option small { margin-top:2px; color:var(--secondary-text-color); font-size:10px; font-weight:400; }
        .search-select-empty { padding:10px; color:var(--secondary-text-color); font-size:12px; }
        .switch-row { display:flex; align-items:center; gap:9px; align-self:end; padding-bottom:8px; }
        .weekday-row { display:flex; gap:8px; flex-wrap:wrap; }
        .editor-section-title { font-size:14px!important; font-weight:700; color:var(--primary-text-color)!important; }
        .editor-subsection-heading { padding-top:6px; border-top:1px solid var(--divider-color); }
        .weekday-bulk-time { display:grid; grid-template-columns:minmax(190px,280px) auto; align-items:end; gap:10px; margin:8px 0 2px; }
        .weekday-bulk-time .field { gap:5px; }
        .weekday-bulk-time button { min-width:max-content; margin-bottom:0; }
        .weekday-schedule-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:8px; margin-top:4px; }
        .weekday-schedule-row { display:grid; grid-template-columns:84px minmax(110px,1fr); align-items:center; gap:10px; padding:9px 10px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-neutral-quiet-fill); opacity:.68; }
        .weekday-schedule-row.enabled { opacity:1; border-color:var(--vs-control-border); }
        .weekday-schedule-row input[type=time] { min-width:0; padding:7px 8px; }
        .weekday-enable { display:flex; align-items:center; gap:8px; min-width:0; }
        .weekday-enable b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        @media (max-width:720px) { .weekday-bulk-time { grid-template-columns:1fr; } .weekday-bulk-time button { width:100%; } }
        .day-overrides { display:grid; gap:9px; margin-top:4px; }
        .day-override-row { display:grid; grid-template-columns:minmax(90px,.7fr) repeat(5,minmax(135px,1fr)); gap:9px; align-items:end; padding:10px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .day-override-row label { display:flex; flex-direction:column; gap:5px; min-width:0; }
        .day-override-row label>span { color:var(--secondary-text-color); font-size:11px; }
        .day-override-row select { padding:8px; min-width:0; }
        .day-override-name { display:flex; flex-direction:column; gap:2px; align-self:center; }
        .day-override-name small { color:var(--secondary-text-color); }
        .schedule-cleaning-profile { line-height:1.4; max-width:520px; }
        .schedule-execution-policy { margin-top:4px; color:var(--secondary-text-color); font-size:11px; line-height:1.35; }
        .schedule-corrections-summary { margin-top:6px; color:var(--secondary-text-color); font-size:11px; line-height:1.35; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .schedule-force-summary { margin-top:6px; color:var(--secondary-text-color); font-size:11px; line-height:1.35; max-width:460px; }
        .force-editor { margin-top:4px; padding:12px; border:1px solid var(--vs-border); border-radius:10px; background:var(--vs-neutral-quiet-fill); }
        .schedule-timing-editor { margin-top:4px; padding:12px; border:1px solid var(--vs-border); border-radius:10px; background:var(--vs-neutral-quiet-fill); }
        .schedule-timing-grid { margin-top:8px; align-items:end; }
        .schedule-timing-grid .switch-row { min-height:42px; box-sizing:border-box; }
        .specific-dates-label { font-weight:700!important; color:var(--primary-text-color)!important; }
        .force-group-name-input { flex:1 1 260px; min-width:140px; max-width:520px; font:inherit; font-weight:700; padding:7px 9px; }
        .schedule-timing-editor > .force-editor { margin-top:12px; padding:12px 0 0; border:0; border-top:1px solid var(--vs-border); border-radius:0; background:transparent; }
        .force-editor-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }
        .force-editor-head>div:first-child { display:flex; flex-direction:column; gap:4px; }
        .force-main-fields { margin-top:12px; align-items:end; }
        .field-label-row { display:flex; align-items:baseline; justify-content:space-between; gap:10px; min-width:0; }
        .field-label-row>small { color:var(--secondary-text-color); font-size:11px; font-weight:400; white-space:nowrap; }
        .force-logic-help { margin:8px 0; color:var(--secondary-text-color); font-size:12px; }
        .force-groups { display:grid; gap:8px; margin:10px 0; }
        .force-group { padding:10px; border:1px solid var(--vs-control-border); border-radius:9px; background:var(--vs-card-surface); }
        .force-group-head { display:flex; align-items:center; justify-content:space-between; gap:9px; min-height:40px; margin-bottom:10px; }
        .force-group-title { display:flex; align-items:center; min-height:36px; line-height:1.2; }
        .force-group-head>div { display:flex; align-items:center; gap:6px; }
        .force-condition-row { --force-condition-gap:9px; --force-action-width:36px; display:grid; gap:9px; padding:9px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .force-condition-head { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr) var(--force-action-width); align-items:end; gap:var(--force-condition-gap); }
        .force-condition-head .field { min-width:0; }
        .force-condition-head .force-remove-condition { width:var(--force-action-width); min-width:var(--force-action-width); margin-left:0; }
        .force-condition-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:var(--force-condition-gap); align-items:end; margin-right:calc(var(--force-action-width) + var(--force-condition-gap)); }
        .force-condition-fields .wide { grid-column:1/-1; }
        .force-or-divider,.force-and-divider { text-align:center; font-weight:700; color:var(--secondary-text-color); font-size:11px; letter-spacing:.04em; }
        .force-people-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:6px; max-height:180px; overflow:auto; padding:7px; border:1px solid var(--vs-border); border-radius:7px; }
        .force-person-choice { display:flex; align-items:flex-start; gap:7px; padding:5px; border-radius:6px; }
        .force-person-choice span { display:flex; flex-direction:column; min-width:0; }
        .force-person-choice small { color:var(--secondary-text-color); overflow-wrap:anywhere; }
        .selectable-chip { background:var(--vs-neutral-quiet-fill); color:var(--vs-neutral-quiet-on); cursor:pointer; }
        .selectable-chip input { margin:0; }
        .target-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:8px; border:1px solid var(--vs-border); padding:10px; border-radius:8px; max-height:260px; overflow:auto; }
        .target-choice { display:flex; align-items:center; gap:8px; padding:7px; border-radius:7px; background:var(--vs-neutral-quiet-fill); }
        .warning { color:var(--vs-warning-quiet-on); padding:10px; background:var(--vs-warning-quiet-fill); border-radius:8px; }
        .editor-actions { display:flex; justify-content:flex-end; gap:10px; margin-top:18px; }
        .system-notice-stack { margin:0 0 18px; border:0; }
        .system-notice-stack>summary { list-style:none; cursor:pointer; }
        .system-notice-stack>summary::-webkit-details-marker { display:none; }
        .system-banner { display:flex; align-items:center; gap:12px; box-sizing:border-box; width:100%; padding:12px 14px; margin:0 0 18px; border:1px solid var(--wa-color-warning-border-normal,var(--warning-color)); background:var(--vs-warning-quiet-fill); border-radius:10px; color:var(--primary-text-color); }
        .system-notice-stack>.system-banner { margin-bottom:0; }
        .system-banner-body { display:flex; align-items:baseline; gap:12px; min-width:0; flex:1 1 auto; }
        .system-banner-body b { flex:0 0 auto; letter-spacing:.02em; color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .system-banner-body span { min-width:0; overflow-wrap:anywhere; }
        .system-banner>.button-icon { --mdc-icon-size:20px; width:20px; height:20px; flex:0 0 20px; color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .system-banner-more { margin-left:auto; display:inline-flex; align-items:center; gap:5px; flex:0 0 auto; white-space:nowrap; color:var(--warning-color,var(--vs-warning-quiet-on)); font-weight:600; }
        .system-banner-more>.button-icon { --mdc-icon-size:18px; width:18px; height:18px; flex:0 0 18px; }
        .system-banner-extra { display:grid; gap:8px; padding-top:8px; }
        .system-banner-extra .system-banner { margin:0; }
        .system-banner-continuation .system-banner-body b { display:none; }
        .execution-mode-inline { display:inline-flex; align-items:center; gap:6px; font-weight:600; white-space:nowrap; }
        .execution-mode-inline .button-icon,.summary-mode-value .button-icon { --mdc-icon-size:18px; width:18px; height:18px; flex-basis:18px; }
        .execution-mode-inline-dry-run { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .execution-mode-inline-real { color:var(--success-color,var(--vs-success-quiet-on)); }
        .summary-mode-value { margin-left:auto; min-width:0; display:inline-flex; align-items:center; justify-content:flex-end; gap:6px; }
        .summary-mode-value b { margin-left:0; }
        .execution-mode-picker { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
        button.execution-mode-choice { min-height:48px!important; justify-content:flex-start!important; padding:10px 12px; border:1px solid var(--vs-control-border)!important; border-radius:var(--ha-border-radius-md,8px); background:var(--vs-control-surface); font-weight:600; }
        button.execution-mode-choice:hover { background:var(--vs-neutral-quiet-hover); }
        button.execution-mode-choice.selected { box-shadow:inset 0 0 0 1px currentColor; }
        button.execution-mode-choice-dry { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        button.execution-mode-choice-real { color:var(--success-color,var(--vs-success-quiet-on)); }
        button.execution-mode-choice-dry.selected { border-color:var(--warning-color,var(--vs-warning-quiet-on))!important; }
        button.execution-mode-choice-real.selected { border-color:var(--success-color,var(--vs-success-quiet-on))!important; }
        button.execution-mode-choice-dry .button-icon,button.execution-mode-choice-real .button-icon { color:currentColor; }
        .execution-mode-picker-primary{max-width:720px;margin-top:12px}.execution-advanced{margin-top:12px;border-top:1px solid var(--divider-color)}.execution-advanced>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;padding:12px 2px 4px;color:var(--secondary-text-color);font-weight:600}.execution-advanced>summary::-webkit-details-marker{display:none}.execution-advanced-body{padding:10px 0 0}.compact-robot-settings{padding-top:12px;padding-bottom:12px}.compact-robot-header{margin-bottom:8px}.robot-device-list{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin:2px 0 10px}.robot-device-item{display:inline-flex;align-items:center;gap:6px;min-width:0;padding:5px 8px;border:1px solid var(--vs-border);border-radius:8px;background:var(--vs-neutral-quiet-fill);font-size:12px;font-weight:600}.robot-device-item .button-icon{--mdc-icon-size:17px;width:17px;height:17px;flex-basis:17px;color:var(--secondary-text-color)}.robot-device-primary .button-icon{color:var(--primary-text-color)}.robot-compact-status{display:grid;gap:4px;border-top:1px solid var(--divider-color);padding-top:8px}.robot-status-row{display:flex;align-items:baseline;gap:6px;font-size:12px;min-width:0}.robot-status-row>span{color:var(--secondary-text-color);white-space:nowrap}.robot-status-row>b{font-size:12px;min-width:0;overflow-wrap:anywhere}.robot-status-row.ok>b{color:var(--success-color,var(--vs-success-quiet-on))}.robot-status-row.bad>b{color:var(--error-color,var(--vs-danger-quiet-on))}.robot-status-row.neutral>b{color:var(--secondary-text-color)}.settings-bindings-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start;margin-bottom:18px}.settings-binding-column{min-width:0;max-width:100%;margin:0;box-sizing:border-box}.settings-bindings-grid .table-wrap{width:100%;max-width:100%;min-width:0;overflow-x:hidden}.settings-bindings-grid .settings-summary-table{min-width:0!important;max-width:100%;width:100%;table-layout:fixed}.settings-bindings-grid .settings-summary-table th,.settings-bindings-grid .settings-summary-table td{min-width:0!important;white-space:normal;overflow-wrap:anywhere}.settings-bindings-grid .settings-summary-table th:nth-child(1){width:39%}.settings-bindings-grid .settings-summary-table th:nth-child(2){width:22%}.settings-bindings-grid .settings-summary-table th:nth-child(3){width:31%}.settings-bindings-grid .settings-summary-table th:nth-child(4){width:8%}.settings-bindings-grid .settings-summary-table td:first-child{min-width:0!important}.settings-bindings-grid .settings-summary-table td:first-child small{overflow-wrap:anywhere;word-break:break-word}.settings-bindings-grid .settings-summary-table td.settings-summary-action{width:8%;min-width:0!important;white-space:normal;text-align:right}.overflow-menu{position:relative}.overflow-menu>summary{cursor:pointer;list-style:none;width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center}.overflow-menu>summary::-webkit-details-marker{display:none}.overflow-menu>summary:hover{background:var(--vs-neutral-quiet-hover)}.overflow-menu-popup{position:absolute;right:0;top:42px;z-index:15;min-width:210px;padding:6px;border:1px solid var(--vs-border);border-radius:9px;background:var(--card-background-color);box-shadow:var(--ha-card-box-shadow);display:grid;gap:4px}.overflow-menu-popup button{justify-content:flex-start!important;width:100%}
        .system-banner-action{margin-left:auto!important;min-height:30px!important;height:30px;padding:4px 10px!important;white-space:nowrap}
        .maintenance-status-card{padding-bottom:14px}.maintenance-status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.maintenance-status-grid>div{display:flex;flex-direction:column;gap:4px;padding:9px 11px;border:1px solid var(--vs-border);border-radius:8px;background:var(--vs-neutral-quiet-fill)}.maintenance-status-grid span{font-size:12px;color:var(--secondary-text-color)}.maintenance-status-grid b{font-size:14px}.maintenance-status-grid .maintenance-attention{border-color:var(--warning-color,var(--vs-warning-quiet-on));background:var(--vs-warning-quiet-fill)}.maintenance-status-grid .maintenance-attention b{color:var(--warning-color,var(--vs-warning-quiet-on))}
        .maintenance-page-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:0 0 14px}.maintenance-list{display:grid;gap:10px}.maintenance-session{border:1px solid var(--vs-border);border-radius:10px;padding:13px 14px;background:var(--vs-card-surface)}.maintenance-session.pending{border-left:4px solid var(--warning-color,var(--vs-warning-quiet-on));background:var(--vs-warning-quiet-fill)}.maintenance-session-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.maintenance-detected-list{display:grid;gap:6px;margin-top:10px}.maintenance-detected-list>div{display:flex;gap:8px 12px;align-items:baseline;flex-wrap:wrap}.maintenance-detected-list span{color:var(--secondary-text-color);font-size:12px}.maintenance-actions-summary{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px 12px;margin-top:10px}.maintenance-actions-summary>span{overflow-wrap:anywhere}.maintenance-pending-help{margin-top:10px}.maintenance-note-view{margin-top:10px;padding:8px 10px;border-left:3px solid var(--divider-color);background:var(--vs-neutral-quiet-fill)}.maintenance-revisions{margin-top:10px;border-top:1px solid var(--divider-color);padding-top:8px}.maintenance-revisions>summary{cursor:pointer;color:var(--secondary-text-color);font-weight:600}.maintenance-revision-list{display:grid;gap:8px;margin-top:8px}.maintenance-revision-list>div{padding:8px;border:1px solid var(--vs-border);border-radius:8px}.maintenance-revision-list pre{margin:5px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}
        .maintenance-editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:12px 0}.maintenance-tank-editor{display:grid;gap:10px;padding:12px;border:1px solid var(--vs-border);border-radius:10px;background:var(--vs-neutral-quiet-fill)}.maintenance-tank-editor label,.maintenance-note{display:grid;gap:5px}.maintenance-tank-editor label>span,.maintenance-note>span{font-size:12px;color:var(--secondary-text-color);font-weight:600}.maintenance-tank-head{display:flex;justify-content:space-between;gap:8px;align-items:center}.warning-text{color:var(--warning-color,var(--vs-warning-quiet-on))}.maintenance-note{margin:0 0 12px}.maintenance-note textarea{width:100%;box-sizing:border-box;resize:vertical}.maintenance-recalc-note{display:flex;align-items:flex-start;gap:9px;margin:0 0 12px}.maintenance-recalc-note .button-icon{flex:0 0 20px}
        .summary-grid { display:grid; grid-template-columns:.95fr 1.35fr 1.15fr 1fr; gap:8px; margin-bottom:12px; }
        /* Operational summary cards stay a single compact row on desktop. */
        .summary-card { background:var(--vs-card-surface); border:var(--ha-card-border-width,1px) solid var(--vs-border); border-radius:var(--ha-card-border-radius,9px); box-shadow:var(--ha-card-box-shadow); padding:7px 10px; height:44px; min-height:44px; box-sizing:border-box; display:flex; flex-direction:row; align-items:center; gap:8px; white-space:nowrap; overflow:hidden; }
        .summary-card span,.summary-card small { color:var(--secondary-text-color); font-size:12px; white-space:nowrap; }
        .summary-card b { margin-left:auto; min-width:0; font-size:14px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .summary-card small { flex:0 0 auto; }
        /* Statistics cards can carry a percentile and explanatory note without clipping. */
        .statistics-summary-grid .summary-card { height:auto; min-height:60px; display:grid; grid-template-columns:minmax(0,1fr) auto; grid-auto-rows:auto; align-content:start; align-items:start; column-gap:10px; row-gap:2px; white-space:normal; overflow:visible; }.statistics-summary-grid .summary-card>span:first-child,.statistics-summary-grid .summary-card>b{align-self:start}.statistics-summary-grid .summary-card>b{margin-top:0}
        .statistics-metric-card>span { min-width:0; white-space:normal; overflow-wrap:anywhere; font-weight:600; color:var(--primary-text-color); }
        .statistics-summary-section .summary-card>span:first-child { font-weight:600; color:var(--primary-text-color); }
        .statistics-metric-card>b { margin-left:0; text-align:right; overflow:visible; text-overflow:clip; }
        .statistics-metric-card>small { grid-column:1/-1; min-width:0; white-space:normal; line-height:1.25; overflow-wrap:anywhere; text-align:right; }
        .statistics-metric-card>.statistics-percentile { color:var(--secondary-text-color); }
        .statistics-metric-card>.statistics-card-note { margin-top:1px; }
        .statistics-time-grid { grid-template-columns:repeat(5,minmax(0,1fr)); }
        .statistics-battery-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
        .water-statistics-card .statistics-water-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .forecast-policy-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.forecast-policy-card{border:1px solid var(--divider-color);border-radius:var(--ha-border-radius-md,10px);padding:14px;background:var(--card-background-color)}.forecast-policy-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.forecast-policy-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.input-prefix{display:flex;align-items:center;gap:5px}.input-prefix>span{font-weight:700}.input-prefix>input{min-width:0}.forecast-model-card{border:1px solid var(--divider-color);border-radius:var(--ha-border-radius-md,10px);padding:14px;margin-bottom:18px}.forecast-model-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:16px}.forecast-model-head h3{margin:0}.forecast-model-head .muted{margin-top:8px}.forecast-model-meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}.forecast-model-actions{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}.forecast-model-actions .field{min-width:150px}.forecast-model-card .forecast-table-head{margin-top:18px;margin-bottom:8px}.forecast-model-card .forecast-model-head + .forecast-table-head{margin-top:0}.forecast-model-card .forecast-table-wrap + .forecast-table-head{margin-top:20px}.forecast-model-table th,.forecast-model-table td{overflow-wrap:anywhere}.forecast-model-table td.metric-number{white-space:nowrap}.forecast-model-table .forecast-profile-cell{line-height:1.35}.forecast-model-table .forecast-data-count small{display:block;margin-top:2px;color:var(--secondary-text-color);font-size:10px;line-height:1.25;white-space:normal}.forecast-other-data{margin-top:8px;border-top:1px solid var(--divider-color);padding-top:8px}.forecast-other-data>summary{cursor:pointer;color:var(--secondary-text-color);font-weight:600;padding:5px 0}.forecast-other-data>.forecast-table-wrap{margin-top:7px}.forecast-archive-list{display:grid;gap:7px;margin-top:10px}.forecast-archive-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px;border-radius:8px;background:var(--vs-neutral-fill-hover)}
        .statistics-profile-table .metric-group-head,.statistics-profile-table .statistics-zone-subhead th { text-align:right; white-space:nowrap; }
        .statistics-profile-table td.metric-number { white-space:nowrap; }
        .statistics-profile-table .statistics-profile-cell { min-width:190px; max-width:260px; line-height:1.3; }
        .statistics-reasons-table { min-width:620px; }
        .statistics-summary-reasons .history-report-section-head + .table-wrap { margin-top:10px; }
        .statistics-reasons-table th:nth-child(2),.statistics-reasons-table th:nth-child(3),.statistics-reasons-table td.metric-number,.statistics-wait-reasons-table th:nth-child(2),.statistics-wait-reasons-table th:nth-child(3),.statistics-wait-reasons-table td.metric-number { text-align:right; font-variant-numeric:tabular-nums; }
        .statistics-charge-card,.statistics-water-metric { align-content:start; }
        .statistics-charge-card>.statistics-charge-explanation { margin-top:3px; }
        .statistics-water-metric>.statistics-card-note { text-align:right; }
        .summary-card-gate > span { flex:0 0 auto; }
        .summary-card-gate > b { flex:1 1 auto; text-align:right; }
        .summary-actions { display:flex; flex:0 0 auto; align-items:center; gap:4px; flex-wrap:nowrap; margin:0; }
        button.summary-action { flex:0 0 var(--vs-compact-action-size); }
        .summary-card-mode > .summary-mode-value { flex:1 1 auto; }
        .summary-mode-actions { margin-left:0; }
        .summary-card-gate button.ghost.summary-action,
        .summary-card-mode button.quick-execution-mode { color:var(--vs-neutral-on)!important; background:var(--vs-neutral-fill)!important; border-color:var(--vs-control-border)!important; box-shadow:none; }
        .summary-card-gate button.ghost.summary-action:hover,
        .summary-card-mode button.quick-execution-mode:hover { background:var(--vs-neutral-fill-hover)!important; }
        .summary-card-gate button.ghost.summary-action:active,
        .summary-card-mode button.quick-execution-mode:active { background:var(--vs-neutral-fill-active)!important; }
        .summary-card-mode button.quick-execution-mode.active { color:var(--primary-text-color)!important; background:var(--vs-neutral-fill-active)!important; border-color:var(--secondary-text-color)!important; }
        button.quick-execution-mode .button-icon { color:currentColor; }
        .robot-status-strip { margin:0 0 12px; border:1px solid var(--vs-border); border-radius:10px; background:var(--vs-card-surface); overflow:hidden; }
        .robot-status-strip>summary { position:relative; display:grid; grid-template-columns:minmax(220px,1.5fr) repeat(3,minmax(150px,.75fr)) 28px; gap:12px; align-items:center; min-height:58px; padding:9px 14px; cursor:pointer; list-style:none; }
        .robot-status-strip>summary::-webkit-details-marker { display:none; }
        .robot-status-primary,.robot-status-metric { display:flex; align-items:center; gap:9px; min-width:0; }
        .robot-status-primary>.button-icon { --mdc-icon-size:25px; width:25px; height:25px; color:var(--primary-color); }
        .robot-status-metric>.button-icon { --mdc-icon-size:20px; width:20px; height:20px; color:var(--secondary-text-color); }
        .robot-status-primary>span,.robot-status-metric>span { display:flex; flex-direction:column; min-width:0; gap:2px; }
        .robot-status-primary small,.robot-status-metric small { color:var(--secondary-text-color); font-size:10px; line-height:1.1; }
        .robot-status-primary b,.robot-status-metric b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .robot-status-expand { display:flex; justify-content:flex-end; color:var(--secondary-text-color); }
        .robot-status-expand>.button-icon { transition:transform .16s ease; }
        .robot-status-strip[open] .robot-status-expand>.button-icon { transform:rotate(180deg); }
        .robot-status-details { display:flex; flex-wrap:wrap; gap:7px 18px; padding:9px 14px 12px; border-top:1px solid var(--divider-color); color:var(--secondary-text-color); font-size:11px; }
        .robot-status-details b { color:var(--primary-text-color); }
        .active-job-mode { display:block; line-height:1.35; overflow-wrap:anywhere; }
        .job-list { --job-actions-column:124px; border-top:1px solid var(--divider-color); font-size:var(--vs-table-font-size); }
        /* Keep every active-job row on the exact same column tracks. The action count
           varies by state (for example WAIT has three buttons while PLANNED has two),
           so an auto-sized action column would shift all preceding columns per row. */
        .schedule-name-line{display:flex;align-items:baseline;gap:6px;min-width:0;flex-wrap:wrap}.schedule-name-line strong{min-width:0}.schedule-revision{font-size:10px;font-weight:400;color:var(--secondary-text-color);white-space:nowrap}.schedule-id-line{margin-top:3px;color:var(--secondary-text-color);font-size:10px;line-height:1.3;min-width:0}.schedule-id-line code{font-family:var(--code-font-family,monospace);font-size:10px;overflow-wrap:anywhere;user-select:all;color:inherit}.schedule-row-state{margin-top:3px;font-size:10px;line-height:1.3;font-weight:500}.schedule-row-state-text{display:inline;background:transparent!important;padding:0!important;border:0!important;border-radius:0!important;box-shadow:none!important}.schedule-row-state-text.state-PLANNED{color:var(--secondary-text-color)}.schedule-row-state-text.state-WAIT{color:var(--warning-color,var(--vs-warning-quiet-on))}.schedule-row-state-text.state-STARTING{color:var(--info-color,var(--vs-brand-quiet-on))}.schedule-row-state-text.state-RUNNING,.schedule-row-state-text.state-FINISHED-SUCCESS{color:var(--success-color,var(--vs-success-quiet-on))}.schedule-row-state-text.state-FINISHED-SUPPRESSED{color:var(--warning-color,var(--vs-warning-quiet-on))}.schedule-row-state-text.state-FINISHED-FAILED{color:var(--error-color,var(--vs-danger-quiet-on))}.force-preempt-short{white-space:nowrap;font-weight:400;color:var(--secondary-text-color)}
        .statistics-report-card{--statistics-card-pad:18px;padding-top:0}.statistics-period-tabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 calc(-1 * var(--statistics-card-pad));padding:12px var(--statistics-card-pad);border-bottom:1px solid var(--divider-color)}button.statistics-period-toggle{min-height:34px!important;height:34px;padding:4px 10px!important;border-radius:var(--ha-border-radius-md,8px);background:transparent;color:var(--secondary-text-color);border:1px solid transparent;font-weight:600}button.statistics-period-toggle:hover{background:var(--vs-neutral-quiet-hover);color:var(--primary-text-color)}button.statistics-period-toggle.selected{background:var(--vs-control-surface);color:var(--primary-text-color);border-color:var(--vs-control-border)}.statistics-report-card>.statistics-report-tabs{margin:0 calc(-1 * var(--statistics-card-pad));padding:12px var(--statistics-card-pad);border-bottom:1px solid var(--divider-color)}.statistics-global-caption{margin-top:12px}.statistics-tab-body{padding-top:12px}.statistics-tab-section{min-width:0}.statistics-tab-caption{margin-bottom:12px}.statistics-tab-section>.entry-header:first-child{margin-top:0}
        .job-list-header,.job-summary { display:grid; grid-template-columns:minmax(0,1.05fr) minmax(250px,1.65fr) minmax(220px,1.45fr) minmax(0,1fr) minmax(0,1fr) minmax(0,.85fr) minmax(78px,.65fr) var(--job-actions-column); gap:12px; align-items:center; }
        .job-summary > span { min-width:0; }
        .job-now-cell { min-width:0; font-family:inherit; font-size:var(--vs-table-font-size); font-weight:400; line-height:1.35; }
        .job-now-layout { display:flex; align-items:center; gap:8px; min-width:0; width:100%; }
        .job-now-stack,.job-now-main { display:flex; flex-direction:column; align-items:flex-start; gap:3px; min-width:0; font-family:inherit; }
        .job-now-main { max-width:100%; white-space:normal; line-height:inherit; font-size:inherit; font-weight:inherit; }
        .job-now-main.readiness { font-family:inherit; font-size:inherit; line-height:inherit; font-weight:inherit; }
        .job-now-main b { font:inherit; font-weight:600; overflow-wrap:anywhere; }
        .job-now-main small { color:var(--secondary-text-color); font-family:inherit; font-size:11px; line-height:1.3; font-weight:400; white-space:normal; }
        .job-offset-cell { display:flex; flex-direction:column; align-items:flex-start; gap:3px; min-width:0; font:inherit; line-height:1.35; }
        .job-offset-cell > span { font:inherit; }
        .job-offset-cell small { color:var(--secondary-text-color); font-family:inherit; font-size:11px; line-height:1.3; font-weight:400; white-space:normal; }
        .job-forecast-cell { font-variant-numeric:tabular-nums; white-space:nowrap; }
        .history-source-cell { min-width:0; }
        .job-now-paused { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .job-now-actions { display:flex; align-items:center; gap:4px; margin-left:auto; flex:0 0 auto; }
        .job-now-action { flex:0 0 var(--vs-compact-action-size)!important; }
        .job-list-header { padding:var(--vs-table-header-padding); color:var(--secondary-text-color); font-size:var(--vs-table-header-font-size); font-weight:600; border-bottom:1px solid var(--divider-color); background:var(--vs-table-header-bg); min-height:36px; box-sizing:border-box; }
        .job-list-header span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        details.job-card { border-bottom:1px solid var(--divider-color); }
        .job-summary { cursor:pointer; list-style:none; padding:var(--vs-table-row-padding); }
        .job-summary::-webkit-details-marker { display:none; }
        .history-job-list { border-top:1px solid var(--divider-color); font-size:var(--vs-table-font-size); }
        .history-job-list-header,.history-job-summary { display:grid; grid-template-columns:.75fr 1fr .95fr 1.15fr 2fr .78fr .75fr 1.2fr; gap:10px; align-items:center; }
        .history-job-list-header { padding:var(--vs-table-header-padding); background:var(--vs-table-header-bg); color:var(--secondary-text-color); font-size:var(--vs-table-header-font-size); font-weight:600; border-bottom:1px solid var(--divider-color); min-height:36px; box-sizing:border-box; }
        .history-job-list-header>span { min-width:0; white-space:normal; overflow-wrap:anywhere; }
        .history-job-card { border-bottom:1px solid var(--divider-color); }
        .history-job-summary { cursor:pointer; list-style:none; padding:var(--vs-table-row-padding); }
        .history-job-summary::-webkit-details-marker { display:none; }
        .history-job-summary>span { min-width:0; white-space:normal; }
        .history-cell-value { display:block; min-width:0; max-width:100%; white-space:normal; overflow-wrap:anywhere; }
        .history-job-summary>span:nth-child(1) .history-cell-value,.history-job-summary>span:nth-child(6) .history-cell-value,.history-job-summary>span:nth-child(7) .history-cell-value { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; overflow-wrap:normal; }
        .history-cleaning-profile { display:block; }
        .history-physical-duration { font-variant-numeric:tabular-nums; white-space:nowrap; }
        .job-summary:hover,.history-job-summary:hover { background:var(--vs-neutral-quiet-fill); }
        .job-readiness-stack { display:flex; flex-direction:column; align-items:flex-start; gap:4px; min-width:0; }
        .force-readiness { display:block; font-size:10px; line-height:1.25; overflow-wrap:anywhere; }
        .force-readiness-neutral { color:var(--secondary-text-color); }
        .force-readiness-pass { color:var(--success-color,var(--vs-success-quiet-on)); font-weight:700; }
        .force-readiness-wait { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .active-force-summary { display:flex; flex-direction:column; gap:4px; padding:10px 12px; margin-bottom:10px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .active-force-summary.success b { color:var(--success-color,var(--vs-success-quiet-on)); }
        .active-force-summary.warning b { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .job-list details.job-card:last-child,.history-job-list details.history-job-card:last-child { border-bottom:0; }
        .history-entry-header { align-items:center; }
        .history-filter-shell { margin-left:auto; display:flex; align-items:center; justify-content:flex-end; gap:8px; flex-wrap:wrap; min-width:0; }
        .history-filter-popover { position:relative; }
        .history-filter-trigger { list-style:none; cursor:pointer; min-height:34px; padding:5px 10px; border:1px solid var(--vs-control-border); border-radius:var(--ha-border-radius-md,8px); background:var(--vs-control-surface); color:var(--primary-text-color); display:inline-flex; align-items:center; gap:7px; font-weight:600; user-select:none; }
        .history-filter-trigger::-webkit-details-marker { display:none; }
        .history-filter-trigger .button-icon { --mdc-icon-size:18px; width:18px; height:18px; }
        .history-filter-trigger b { min-width:20px; height:20px; padding:0 5px; border-radius:10px; display:inline-flex; align-items:center; justify-content:center; background:var(--vs-neutral-fill); font-size:12px; }
        .history-filter-panel { position:absolute; right:0; top:calc(100% + 7px); z-index:30; width:min(560px,calc(100vw - 40px)); padding:14px; border:1px solid var(--vs-control-border); border-radius:var(--ha-border-radius-lg,12px); background:var(--card-background-color); box-shadow:0 8px 28px rgba(0,0,0,.28); display:grid; gap:13px; }
        .history-filter-group { display:grid; gap:7px; min-width:0; }
        .history-filter-group>b { color:var(--secondary-text-color); font-size:13px; }
        .history-filter-options { display:flex; gap:6px; flex-wrap:wrap; }
        button.history-filter-option { width:auto!important; min-width:0; min-height:30px!important; height:30px; padding:3px 9px!important; border:1px solid var(--vs-control-border)!important; border-radius:var(--ha-border-radius-md,8px); background:var(--vs-control-surface); color:var(--secondary-text-color); font-size:13px; font-weight:600; }
        button.history-filter-option.selected { color:var(--primary-text-color); border-color:var(--primary-text-color)!important; background:var(--vs-neutral-fill); }
        .history-filter-schedule-options { max-height:150px; overflow:auto; align-content:flex-start; padding-right:2px; }
        .history-filter-reset { justify-self:start; width:auto!important; }
        .history-active-filters { display:flex; align-items:center; justify-content:flex-end; gap:6px; flex-wrap:wrap; min-width:0; }
        .history-active-filter { min-width:0; max-width:220px; height:30px; padding:0 4px 0 10px; border-radius:15px; border:1px solid var(--vs-control-border); background:var(--vs-neutral-fill); color:var(--primary-text-color); display:inline-flex; flex:0 0 auto; align-items:center; gap:4px; box-sizing:border-box; font-size:13px; font-weight:600; white-space:nowrap; }
        .history-active-filter-label { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; line-height:28px; }
        button.history-active-filter-remove { width:22px!important; min-width:22px!important; max-width:22px!important; min-height:22px!important; height:22px!important; padding:0!important; border:0!important; border-radius:50%; background:transparent!important; color:var(--secondary-text-color); display:inline-flex; align-items:center; justify-content:center; flex:0 0 22px; }
        button.history-active-filter-remove:hover { background:var(--secondary-background-color)!important; color:var(--primary-text-color); }
        button.history-active-filter-remove .button-icon { --mdc-icon-size:15px; width:15px; height:15px; }
        .quiet-status { display:inline-flex; align-items:center; gap:7px; color:var(--primary-text-color); font:inherit; font-weight:500; line-height:1.3; min-width:0; }
        .quiet-status-dot { width:7px; height:7px; border-radius:50%; flex:0 0 7px; background:var(--secondary-text-color); opacity:.82; }
        .quiet-status-good .quiet-status-dot { background:var(--success-color,var(--vs-success-quiet-on)); }
        .quiet-status-warning .quiet-status-dot { background:var(--warning-color,var(--vs-warning-quiet-on)); }
        .quiet-status-bad .quiet-status-dot { background:var(--error-color,var(--vs-danger-quiet-on)); }
        .quiet-status-info .quiet-status-dot { background:var(--info-color,var(--vs-brand-quiet-on)); }
        .quiet-status-neutral { color:var(--secondary-text-color); }
        .history-execution-mode,.history-result { color:var(--primary-text-color); font-weight:500; }
        .history-job-report { background:var(--vs-neutral-quiet-fill); padding:14px; }
        .history-report-tabs { display:flex; gap:4px; flex-wrap:wrap; border-bottom:1px solid var(--divider-color); margin:-4px -2px 14px; padding:0 2px 8px; }
        button.history-report-tab { min-height:34px!important; height:34px; padding:5px 10px!important; border-radius:var(--ha-border-radius-md,8px); background:transparent; color:var(--secondary-text-color); border:1px solid transparent; font-weight:600; }
        button.history-report-tab:hover { background:var(--vs-neutral-quiet-hover); color:var(--primary-text-color); }
        button.history-report-tab.selected { background:var(--vs-control-surface); color:var(--primary-text-color); border-color:var(--vs-control-border); }
        .history-report-pane { display:flex; flex-direction:column; gap:16px; min-width:0; }
        .active-primary-metrics { grid-template-columns:repeat(5,minmax(0,1fr)); }
        .active-now-message { display:grid; grid-template-columns:24px minmax(0,1fr); gap:10px; align-items:start; padding:13px 14px; border-left:3px solid var(--secondary-text-color); border-radius:0 8px 8px 0; background:var(--vs-card-surface); }
        .active-now-message>div:first-child { color:var(--secondary-text-color); padding-top:1px; }
        .active-now-message>div:last-child { display:flex; flex-direction:column; gap:3px; }
        .active-now-message span { color:var(--secondary-text-color); }
        .active-now-success { border-left-color:var(--success-color,var(--vs-success-quiet-on)); } .active-now-success>div:first-child { color:var(--success-color,var(--vs-success-quiet-on)); }
        .active-now-warning { border-left-color:var(--warning-color,var(--vs-warning-quiet-on)); } .active-now-warning>div:first-child { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .active-now-fail { border-left-color:var(--error-color,var(--vs-danger-quiet-on)); } .active-now-fail>div:first-child { color:var(--error-color,var(--vs-danger-quiet-on)); }
        .active-now-info { border-left-color:var(--primary-color); } .active-now-info>div:first-child { color:var(--primary-color); }
        .active-readiness-summary { display:flex; flex-direction:column; gap:4px; padding:12px 14px; border-left:3px solid var(--secondary-text-color); background:var(--vs-card-surface); border-radius:0 8px 8px 0; }
        .active-readiness-summary span { color:var(--secondary-text-color); } .active-readiness-summary.success { border-left-color:var(--success-color,var(--vs-success-quiet-on)); } .active-readiness-summary.warning { border-left-color:var(--warning-color,var(--vs-warning-quiet-on)); } .active-readiness-summary.fail { border-left-color:var(--error-color,var(--vs-danger-quiet-on)); }
        .active-zone-table { min-width:800px; } .active-zone-table td small { display:block; color:var(--secondary-text-color); margin-top:3px; }
        .active-readiness-table { min-width:760px; } .active-parameters-table { min-width:520px; max-width:860px; }
        .active-attempt-summary { display:flex; gap:8px 18px; flex-wrap:wrap; margin:7px 0; } .active-attempt-summary span { color:var(--secondary-text-color); } .active-attempt-summary b { color:var(--primary-text-color); }
        .history-primary-metrics,.history-secondary-metrics { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:9px; }
        .history-secondary-metrics { grid-template-columns:repeat(4,minmax(0,1fr)); }
        .history-primary-metrics>div,.history-secondary-metrics>div { display:flex; flex-direction:column; gap:4px; min-width:0; padding:11px 12px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-card-surface); }
        .history-primary-metrics span:first-child,.history-secondary-metrics span:first-child { color:var(--secondary-text-color); font-size:11px; }
        .history-primary-metrics b,.history-secondary-metrics b { font-size:15px; overflow-wrap:anywhere; }
        .history-primary-metrics small,.history-secondary-metrics small { color:var(--secondary-text-color); overflow-wrap:anywhere; }
        .history-run-timeline-line { padding:10px 12px; border-left:3px solid var(--primary-color); background:var(--vs-card-surface); border-radius:0 8px 8px 0; font-weight:600; }
        .history-report-section-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-end; }
        .history-report-section-head h4 { margin:0; font-size:15px; }
        .history-report-section-head span { display:block; margin-top:3px; color:var(--secondary-text-color); font-size:12px; }
        .history-comparison,.history-comparison-empty { border-top:1px solid var(--divider-color); padding-top:14px; }
        .history-comparison-empty { display:flex; gap:8px 14px; align-items:baseline; flex-wrap:wrap; color:var(--secondary-text-color); }
        .history-comparison-empty b { color:var(--primary-text-color); }
        .history-comparison-table { margin-top:8px; min-width:720px; }
        .history-zone-table { min-width:900px; }
        .history-zone-table td small { display:block; color:var(--secondary-text-color); margin-top:3px; }
        .history-timeline { display:flex; align-items:center; gap:8px; overflow-x:auto; padding:4px 2px 10px; }
        .history-timeline-step { display:flex; align-items:center; gap:8px; min-width:max-content; }
        .history-timeline-step>div { display:flex; flex-direction:column; gap:2px; }
        .history-timeline-step small { color:var(--secondary-text-color); }
        .history-timeline-dot,.history-readable-dot { width:10px; height:10px; border-radius:50%; background:var(--primary-color); flex:0 0 10px; }
        .history-timeline-connector { width:34px; height:1px; background:var(--divider-color); flex:0 0 34px; }
        .history-execution-overview { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
        .history-execution-overview>div { padding:12px; border:1px solid var(--vs-border); border-radius:8px; background:var(--vs-card-surface); }
        .history-readable-log { display:flex; flex-direction:column; }
        .history-readable-event { display:grid; grid-template-columns:145px 12px minmax(0,1fr); gap:10px; align-items:start; padding:9px 0; border-bottom:1px solid var(--divider-color); }
        .history-readable-event:last-child { border-bottom:0; }
        .history-readable-event time { color:var(--secondary-text-color); font-size:12px; }
        .history-readable-dot { margin-top:4px; width:8px; height:8px; flex-basis:8px; }
        .history-technical-pane>details { border-bottom:1px solid var(--divider-color); padding:8px 0; }
        .history-technical-pane>details:last-child { border-bottom:0; }
        .history-technical-pane summary { cursor:pointer; font-weight:600; padding:5px 0; }
        .history-report-empty { color:var(--secondary-text-color); padding:24px; text-align:center; }
        .history-report-empty.compact { padding:10px 0; text-align:left; }
        .attempt-title { display:flex; justify-content:space-between; gap:10px; align-items:center; }
        .attempt-time-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px 12px; margin-top:7px; font-size:11px; }
        .attempt-params { display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }
        .history-job-details code { overflow-wrap:anywhere; word-break:break-word; }
        .job-actions-header { text-align:right; }
        .job-row-actions { display:flex; align-items:center; justify-content:flex-end; gap:4px; white-space:nowrap; min-width:0; }
        .job-row-action { flex:0 0 var(--vs-compact-action-size)!important; }
        .job-name { font-weight:600; }
        .state-chip { background:var(--vs-neutral-quiet-fill); }
        .state-PLANNED { background:var(--vs-neutral-quiet-fill); color:var(--vs-neutral-quiet-on); }
        .state-WAIT { background:var(--vs-warning-quiet-fill); color:var(--vs-warning-quiet-on); }
        .state-STARTING { background:var(--vs-brand-quiet-fill); color:var(--info-color,var(--vs-brand-quiet-on)); }
        .state-RUNNING { background:var(--vs-success-quiet-fill); color:var(--vs-success-quiet-on); }
        .state-FINISHED-SUCCESS { background:var(--vs-success-quiet-fill); color:var(--vs-success-quiet-on); }
        .state-FINISHED-SUPPRESSED { background:var(--vs-warning-quiet-fill); color:var(--vs-warning-quiet-on); }
        .state-FINISHED-FAILED { background:var(--vs-danger-quiet-fill); color:var(--vs-danger-quiet-on); }
        .job-details { background:var(--vs-neutral-quiet-fill); padding:14px; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; font-size:13px; }
        .wide-detail { grid-column:1/-1; }
        .detail-prefix { color:var(--secondary-text-color); font-size:12px; }
        .plain-status { color:var(--secondary-text-color); }
        .plain-status.ok { color:var(--success-color,var(--vs-success-quiet-on)); }
        .readiness { font-size:12px; font-weight:600; line-height:1.3; }
        .readiness-unknown { color:var(--secondary-text-color); }
        .readiness-pass { color:var(--success-color,var(--vs-success-quiet-on)); }
        .readiness-wait { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .readiness-fail { color:var(--error-color,var(--vs-danger-quiet-on)); }
        .readiness-starting { color:var(--info-color,var(--vs-brand-quiet-on)); }
        .technical { grid-column:1/-1; }
        pre { white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; max-width:100%; overflow:auto; font-size:11px; }
        .result-SUCCESS { background:var(--vs-success-quiet-fill); color:var(--vs-success-quiet-on); }
        .result-PARTIAL_SUCCESS { background:var(--vs-success-quiet-fill); color:var(--vs-success-quiet-on); }
        .result-FAILED { background:var(--vs-danger-quiet-fill); color:var(--vs-danger-quiet-on); }
        .debug-target,.entry-selector { max-width:520px; margin-bottom:14px; }
        .blocker-grid { display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:8px; }
        .blocker-toggle { display:flex; gap:9px; align-items:center; padding:10px; border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .clock-line { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:14px; }
        .clock-line > div { display:flex; flex-direction:column; gap:5px; }.clock-line span{font-size:12px;color:var(--secondary-text-color)}
        .button-row { display:flex; gap:8px; flex-wrap:wrap; }
        .routing-test-summary { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
        .routing-test-note { margin-top:9px; }
        .routing-test-result { margin-top:10px; }
        .execution-row { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid var(--divider-color); }
        .execution-row:last-child { border-bottom:0; }
        .persistent-warning,.synthetic-warning { margin:0 0 14px; padding:10px 12px; border-radius:8px; background:var(--vs-warning-quiet-fill); color:var(--vs-warning-quiet-on); }
        .gate-disabled,.gate-disabled_until { color:var(--error-color,var(--vs-danger-quiet-on)); }
        .gate-enabled { color:var(--success-color,var(--vs-success-quiet-on)); }
        .execution-mode-dry-run { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .execution-mode-real { color:var(--success-color,var(--vs-success-quiet-on)); }
        .summary-card .summary-mode-value.execution-mode-dry-run { color:var(--warning-color,var(--vs-warning-quiet-on)); }
        .summary-card .summary-mode-value.execution-mode-real { color:var(--success-color,var(--vs-success-quiet-on)); }
        .summary-card .summary-mode-value .button-icon,.summary-card .summary-mode-value b { color:currentColor; }
        .execution-mode-unknown { color:var(--secondary-text-color); }
        .related-devices,.preset-row { display:flex; gap:7px; flex-wrap:wrap; align-items:center; margin:8px 0 14px; }
        .validation-list { display:grid; gap:6px; margin-top:12px; }.validation-list>div { display:flex; gap:10px; padding:8px 10px; border-radius:7px; background:var(--vs-neutral-quiet-fill); }.validation-error b { color:var(--error-color,var(--vs-danger-quiet-on)); }.validation-warning b { color:var(--warning-color,var(--vs-warning-quiet-on)); }.validation-ok { margin-top:12px; color:var(--success-color,var(--vs-success-quiet-on)); }
        .binding-list { display:grid; gap:8px; }.binding-card { border:1px solid var(--vs-border); border-radius:9px; overflow:hidden; }.binding-card>summary { cursor:pointer; display:grid; grid-template-columns:minmax(220px,1fr) 150px 140px; gap:12px; align-items:center; padding:11px 12px; list-style:none; }.binding-card>summary::-webkit-details-marker{display:none}.binding-card>summary span:first-child { display:flex; flex-direction:column; gap:2px; }.binding-card>summary small { color:var(--secondary-text-color); font-weight:400; }.binding-body { border-top:1px solid var(--divider-color); padding:13px; background:var(--vs-neutral-quiet-fill); }.binding-preview { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; margin-bottom:13px; }.binding-preview>div { display:flex; flex-direction:column; gap:3px; padding:9px; background:var(--vs-card-surface); border-radius:8px; }.binding-preview span { color:var(--secondary-text-color); font-size:11px; }.binding-preview code { font-size:10px; opacity:.8; overflow-wrap:anywhere; word-break:break-word; }.support-detected,.support-manual { color:var(--vs-success-quiet-on); background:var(--vs-success-quiet-fill); }.support-unverified,.support-conflict { color:var(--vs-warning-quiet-on); background:var(--vs-warning-quiet-fill); }.support-disabled,.support-unsupported { color:var(--vs-neutral-quiet-on); background:var(--vs-neutral-quiet-fill); }
        .settings-summary-table { min-width:760px; }.settings-summary-table td { vertical-align:middle; }.settings-summary-table td:first-child { min-width:250px; }.settings-summary-table td:first-child small { display:block; color:var(--secondary-text-color); font-size:10px; margin-top:2px; }.settings-summary-value { font-weight:600; }.binding-support-state,.resource-state{font-weight:500;color:var(--primary-text-color)}.settings-summary-action { width:1%; white-space:nowrap; text-align:right; }.settings-editor-key{display:block;margin-top:5px;color:var(--secondary-text-color);font-size:11px;overflow-wrap:anywhere;word-break:break-word}.binding-single-form{border-top:1px solid var(--divider-color);border-bottom:1px solid var(--divider-color);margin-top:10px}.settings-editor-page { max-width:1200px; margin-left:auto; margin-right:auto; }.binding-editor-list { margin-top:8px; }
        .zone-summary-table { min-width:1120px; }.zone-summary-table td { vertical-align:middle; }.zone-summary-table td:nth-child(1){min-width:180px}.zone-summary-table td:nth-child(2){min-width:190px}.zone-summary-table td:nth-child(3){min-width:170px}.zone-summary-table td:nth-child(4),.zone-summary-table td:nth-child(5){min-width:220px}.zone-live-state { font:inherit; font-weight:400; color:var(--primary-text-color); }.zone-live-neutral { color:var(--secondary-text-color); }.room-actions { display:flex; gap:4px; align-items:center; white-space:nowrap; }.zone-summary-table td.room-actions { display:table-cell; width:1%; white-space:nowrap; vertical-align:middle!important; }.room-actions-inner { display:flex; gap:4px; align-items:center; justify-content:flex-end; white-space:nowrap; }.room-editor { max-width:1200px; margin-left:auto; margin-right:auto; border:2px solid var(--wa-color-brand-border-normal,var(--primary-color)); }.settings-subsection { margin-top:15px; padding:12px; border:1px solid var(--vs-border); border-radius:9px; }.settings-subsection h4 { margin:0 0 8px; } .field-wide { grid-column:1 / -1; }.subsection-header,.path-header { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:9px; }.choice-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:7px; max-height:220px; overflow:auto; }.target-choice small { display:block; color:var(--secondary-text-color); }.condition-row { display:grid; grid-template-columns:1.4fr 1fr .8fr 1fr auto; gap:8px; align-items:end; margin:8px 0; }.path-card { margin:9px 0; padding:10px; border-radius:8px; background:var(--vs-neutral-quiet-fill); }.path-header input { max-width:420px; }.small { padding:6px 8px!important; }.empty.compact { padding:12px; }.test-result { margin-top:12px; padding:10px; border-radius:8px; background:var(--vs-neutral-quiet-fill); }
        .data-management-card{display:grid;gap:14px}.data-layer-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.data-layer-summary>div{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid var(--vs-border);border-radius:10px;background:var(--vs-neutral-quiet-fill)}.data-layer-summary span{color:var(--secondary-text-color);font-size:12px}.data-layer-summary b{font-size:18px}.data-backups>summary{display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600}.data-backups>summary b{margin-left:auto}.data-backup-list{display:grid;gap:8px;margin-top:10px}.data-backup-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border:1px solid var(--vs-border);border-radius:10px}.data-backup-main{display:grid;gap:2px;min-width:0}.data-backup-main span,.data-backup-main small{color:var(--secondary-text-color)}.data-backup-path,.data-reset-note{font-size:12px}.data-layer-choices{display:grid;gap:8px;padding:4px 20px 18px 72px}.data-layer-choice{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start;padding:9px 10px;border:1px solid var(--vs-border);border-radius:10px;cursor:pointer}.data-layer-choice input{margin-top:3px}.data-layer-choice span{display:grid;gap:2px}.data-layer-choice small{color:var(--secondary-text-color);line-height:1.35}.data-layer-dialog{max-width:600px}
        .dialog-host { position:relative; z-index:1200; }
        .vs-dialog { width:min(520px,calc(100vw - 32px)); max-width:520px; padding:0; border:1px solid var(--vs-border); border-radius:var(--ha-dialog-border-radius,var(--ha-border-radius-3xl,14px)); background:var(--vs-raised-surface); color:var(--primary-text-color); box-shadow:var(--dialog-box-shadow,var(--ha-dialog-box-shadow,var(--ha-card-box-shadow,none))); -webkit-backdrop-filter:var(--ha-dialog-surface-backdrop-filter,none); backdrop-filter:var(--ha-dialog-surface-backdrop-filter,none); overflow:hidden; }
        .vs-dialog::backdrop { background:var(--mdc-dialog-scrim-color,rgba(0,0,0,.58)); -webkit-backdrop-filter:var(--ha-dialog-scrim-backdrop-filter,none); backdrop-filter:var(--ha-dialog-scrim-backdrop-filter,none); }
        .vs-dialog-card { padding:0; margin:0; }
        .vs-dialog-header { display:grid; grid-template-columns:40px 1fr; gap:12px; align-items:start; padding:20px 20px 12px; }
        .vs-dialog-icon { width:36px; height:36px; display:flex; align-items:center; justify-content:center; border-radius:50%; background:var(--vs-brand-quiet-fill); color:var(--vs-brand-quiet-on); }
        .vs-dialog-icon.danger-icon { background:var(--vs-danger-quiet-fill); color:var(--vs-danger-quiet-on); }
        .vs-dialog-icon .button-icon { width:22px; height:22px; }
        .vs-dialog h2 { margin:1px 0 5px; font-size:18px; line-height:1.25; }
        .vs-dialog-message { color:var(--secondary-text-color); line-height:1.45; white-space:pre-line; }
        .dialog-field { display:grid; gap:6px; padding:8px 20px 16px 72px; }
        .dialog-field span { font-size:12px; color:var(--secondary-text-color); }
        .dialog-option { display:flex; align-items:flex-start; gap:10px; padding:6px 20px 16px 72px; line-height:1.35; cursor:pointer; }
        .dialog-option input { margin:2px 0 0; flex:0 0 auto; }
        .dialog-input { width:100%; box-sizing:border-box; min-height:42px; padding:8px 10px; border:1px solid var(--vs-control-border); border-radius:var(--ha-border-radius-md,8px); background:var(--vs-control-surface); color:var(--vs-control-text); font:inherit; color-scheme:inherit; }
        .vs-dialog-actions { display:flex; justify-content:flex-end; gap:8px; padding:14px 20px 18px; border-top:1px solid var(--vs-border); }
        .vs-dialog-actions button { min-width:96px; }
        .toast-host { position:fixed; left:50%; bottom:28px; transform:translateX(-50%); z-index:1000; pointer-events:none; width:min(520px,calc(100vw - 32px)); }
        .ui-toast { display:flex; align-items:center; justify-content:center; gap:9px; padding:12px 16px; border-radius:var(--ha-card-border-radius,10px); box-shadow:var(--ha-card-box-shadow,none); background:var(--vs-raised-surface); border:1px solid var(--vs-border); font-weight:600; }
        .ui-toast.success-toast { color:var(--vs-success-quiet-on); border-color:var(--wa-color-success-border-normal,var(--success-color)); }
        .ui-toast.error-toast { color:var(--vs-danger-quiet-on); border-color:var(--wa-color-danger-border-normal,var(--error-color)); }
        .validation-ok,.synthetic-warning,.persistent-warning { display:flex; align-items:center; gap:8px; }
        .advanced-testing { padding:0; }.advanced-testing>summary { cursor:pointer; list-style:none; display:flex; flex-direction:column; gap:4px; padding:14px 16px; }.advanced-testing>summary::-webkit-details-marker{display:none}.advanced-body{padding:0 16px 16px}.override-table small { display:block; color:var(--secondary-text-color); margin-top:3px; }.override-table td { vertical-align:middle; }.override-table select,.override-table input[type=text] { min-width:105px; }.override-table select:disabled,.override-table input:disabled { cursor:not-allowed; color:var(--vs-disabled-on); background:var(--vs-disabled-fill); border-color:transparent; opacity:1; }.override-table .inline-check:has(input:disabled) { color:var(--disabled-text-color,var(--secondary-text-color)); cursor:not-allowed; }.inline-check { white-space:nowrap; }.source-table th { font-size:var(--vs-table-header-font-size); }.source-table td { font-size:var(--vs-table-font-size); }
        /* 0.6.35 mobile contract: wide desktop tables become native card lists, never horizontal scrollers. */
        .mobile-card-table td code,.mobile-card-table td small { overflow-wrap:anywhere; word-break:break-word; }
        .recipient-channel-stack { display:flex; flex-direction:column; align-items:flex-start; gap:3px; min-width:0; }
        .recipient-channel-stack b { display:block; line-height:1.3; }
        .recipient-channel-stack small { display:block; margin:0; color:var(--secondary-text-color); font-size:11px; line-height:1.35; }
        .routing-test-result { margin-top:12px; }
        .routing-test-result td { vertical-align:middle; }
        .routing-test-result .routing-target code { font-size:11px; }
        .override-actions { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
        @media (max-width:1100px) { .zone-summary-table{min-width:1040px}.settings-bindings-grid{grid-template-columns:1fr} }
        @media (max-width:900px) { .robot-status-strip>summary{grid-template-columns:1.4fr repeat(3,1fr) 24px;gap:8px}.summary-grid{grid-template-columns:repeat(2,1fr)}.statistics-time-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.statistics-battery-grid,.water-statistics-card .statistics-water-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.maintenance-status-grid{grid-template-columns:repeat(2,1fr)}.maintenance-editor-grid{grid-template-columns:1fr}.job-list-header,.job-summary{grid-template-columns:minmax(0,1fr) minmax(0,1.45fr) minmax(0,1.35fr) minmax(0,1fr) minmax(78px,.7fr) var(--job-actions-column)}.job-list-header span:nth-child(5),.job-list-header span:nth-child(6),.job-summary span:nth-child(5),.job-summary span:nth-child(6){display:none}.job-details{grid-template-columns:repeat(2,1fr)}.blocker-grid{grid-template-columns:repeat(2,1fr)}.history-job-list-header{display:none}.history-job-summary{grid-template-columns:.75fr 1fr 1.6fr .72fr .72fr}.history-job-summary>span:nth-child(3),.history-job-summary>span:nth-child(4),.history-job-summary>span:nth-child(8){display:none}.history-primary-metrics{grid-template-columns:repeat(3,1fr)}.history-secondary-metrics{grid-template-columns:repeat(2,1fr)} }
        @media (max-width:760px) {
          .app-bar{height:var(--header-height,56px);min-height:var(--header-height,56px);padding:0 max(12px,env(safe-area-inset-right)) 0 max(12px,env(safe-area-inset-left))}.app-bar-menu{margin-inline-start:-8px;margin-inline-end:2px}.app-bar-title{font-size:18px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:72px}.loading-indicator{right:max(12px,env(safe-area-inset-right))}.page{padding:10px}.entry-card,.editor-card{padding:13px;margin-bottom:12px;min-width:0;max-width:100%;box-sizing:border-box}.statistics-report-card{--statistics-card-pad:13px}
          .top{align-items:flex-start;flex-direction:column;gap:10px}.top>.refresh{align-self:flex-end;margin-top:-42px}.section-title{font-size:20px}.entry-header,.editor-header,.channel-head,.subsection-header,.path-header{align-items:stretch;flex-direction:column;gap:10px}.entry-header button:not(.icon-only),.editor-header button:not(.icon-only),.channel-head button:not(.icon-only),.subsection-header button:not(.icon-only),.path-header button:not(.icon-only){width:100%}.history-entry-header{align-items:stretch}.history-filter-shell{width:100%;margin-left:0;justify-content:flex-start;align-items:stretch;flex-direction:column}.history-filter-popover{width:100%}.history-filter-trigger{width:fit-content}.history-filter-panel{position:static;width:100%;box-sizing:border-box;margin-top:7px}.history-active-filters{width:100%;justify-content:flex-start}
          .tabs{overflow-x:visible;flex-wrap:wrap;gap:0 4px}.tabs button.tab{flex:1 1 auto;min-width:max-content;padding:9px 10px}
          .robot-status-strip>summary{grid-template-columns:1fr 1fr;gap:10px}.robot-status-primary{grid-column:1/-1}.robot-status-expand{position:absolute;right:18px}.robot-status-details{display:grid;grid-template-columns:1fr}.form-grid{grid-template-columns:1fr}.field.wide{grid-column:auto}.statistics-battery-grid,.water-statistics-card .statistics-water-grid{grid-template-columns:1fr}.weekday-schedule-grid{grid-template-columns:1fr 1fr}.day-override-row{grid-template-columns:1fr 1fr}.day-override-name{grid-column:1/-1}.summary-grid{grid-template-columns:1fr}.execution-mode-picker-primary{grid-template-columns:1fr}.overflow-menu-popup{right:0}.summary-card{height:auto;min-height:44px;white-space:normal;overflow:visible;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center}.summary-card>*{min-width:0}.summary-card span,.summary-card small,.summary-card b{white-space:normal;overflow-wrap:anywhere;word-break:break-word}.summary-card b{margin-left:0;text-align:right}.summary-card-gate,.summary-card-mode{grid-template-columns:minmax(0,1fr) auto}.summary-actions{grid-column:1/-1;justify-content:flex-end;flex-wrap:wrap}.summary-card-mode>.summary-mode-value{margin-left:0}
          .system-banner{align-items:flex-start}.system-banner-body{align-items:flex-start;flex-direction:column;gap:4px}.system-banner-more{align-self:center}.editor-actions{flex-wrap:wrap}.editor-actions button{flex:1 1 140px}.button-row{width:100%}.button-row button:not(.icon-only){flex:1 1 160px}
          .job-list-header{display:none}.job-summary{grid-template-columns:1fr 1fr;gap:10px 14px}.history-job-summary{grid-template-columns:1fr 1fr;gap:10px 14px}.history-job-summary>span{display:flex!important;flex-direction:column;align-items:flex-start;gap:3px;min-width:0;overflow:hidden;white-space:nowrap}.history-job-summary>span::before{content:attr(data-label);font-size:10px;font-weight:500;color:var(--secondary-text-color)}.history-primary-metrics,.history-secondary-metrics,.active-primary-metrics{grid-template-columns:1fr}.history-execution-overview{grid-template-columns:1fr}.history-report-tabs{flex-wrap:nowrap;overflow-x:auto}.history-report-tab{flex:0 0 auto}.statistics-period-tabs{flex-wrap:nowrap;overflow-x:auto}.statistics-period-toggle{flex:0 0 auto}.history-readable-event{grid-template-columns:1fr;gap:4px}.history-readable-dot{display:none}.attempt-time-grid{grid-template-columns:1fr}.job-summary>span{display:flex!important;flex-direction:column;align-items:flex-start;gap:3px;min-width:0;overflow-wrap:anywhere}.job-summary>span::before{content:attr(data-label);font-size:10px;font-weight:500;color:var(--secondary-text-color)}.job-summary>.job-row-actions{flex-direction:row!important;align-items:center!important;justify-content:flex-start;flex-wrap:wrap;white-space:normal}.job-summary>.job-row-actions::before{margin-right:4px}.job-details{grid-template-columns:1fr}.blocker-grid{grid-template-columns:1fr}.clock-line{grid-template-columns:1fr}.execution-row{align-items:flex-start;flex-direction:column}.binding-card>summary{grid-template-columns:1fr}.binding-preview{grid-template-columns:1fr}.condition-row{grid-template-columns:1fr}
          .validation-list>div{display:grid;grid-template-columns:1fr;gap:4px;min-width:0}.validation-ok,.synthetic-warning,.persistent-warning{align-items:flex-start;flex-wrap:wrap}.target-grid,.choice-grid{grid-template-columns:1fr;max-height:none}.target-choice{min-width:0}.robot-device-list,.preset-row,.weekday-row{align-items:flex-start}.ui-chip{white-space:normal;overflow-wrap:anywhere;word-break:break-word}.content-shell button:not(.icon-only){max-width:100%;white-space:normal}.search-select-option-main{flex-wrap:wrap}.search-select-option-main>span:first-child{white-space:normal;overflow-wrap:anywhere;flex:1 1 180px}.search-select-menu{max-height:min(320px,55vh)}
          .table-wrap{overflow:visible}.mobile-card-table{display:block!important;width:100%!important;min-width:0!important;border-collapse:separate}.mobile-card-table thead{display:none}.mobile-card-table tbody{display:grid;gap:10px;width:100%}.mobile-card-table tbody>tr{display:grid!important;grid-template-columns:1fr;gap:8px;width:100%;min-width:0;padding:11px 12px;border:1px solid var(--vs-border);border-radius:var(--ha-card-border-radius,10px);background:var(--vs-card-surface);box-sizing:border-box}.mobile-card-table tbody>tr.disabled-row{opacity:.58}.mobile-card-table td{display:grid!important;grid-template-columns:minmax(92px,34%) minmax(0,1fr);gap:8px;padding:0!important;border:0!important;min-width:0!important;width:auto!important;max-width:100%;white-space:normal!important;overflow-wrap:anywhere;word-break:break-word;align-items:start;text-align:left!important}.mobile-card-table td::before{content:attr(data-label);display:block;color:var(--secondary-text-color);font-size:10px;font-weight:500;line-height:1.3}.mobile-card-table td:not([data-label])::before{display:none}.mobile-card-table td.empty-cell{display:block!important;padding:8px!important;text-align:center!important}.mobile-card-table .mobile-actions-cell{display:flex!important;align-items:center;justify-content:space-between;gap:10px}.mobile-card-table .mobile-actions-cell::before{flex:0 0 auto}.mobile-card-table .schedule-actions,.mobile-card-table .table-actions-inner,.mobile-card-table .room-actions-inner,.mobile-card-table .override-actions{flex-wrap:wrap;white-space:normal;justify-content:flex-end}.mobile-card-table .name-cell,.mobile-card-table td:first-child,.mobile-card-table td:nth-child(2),.mobile-card-table td:nth-child(3),.mobile-card-table td:nth-child(4),.mobile-card-table td:nth-child(5){min-width:0!important}.mobile-card-table select,.mobile-card-table input[type=text],.mobile-card-table input[type=number]{min-width:0!important;width:100%}.inline-check{white-space:normal}.schedule-actions-cell,.notification-recipient-actions,.settings-summary-action,.zone-summary-table td.room-actions{width:auto!important;white-space:normal!important}
          .notification-history-wrap{overflow:visible}.notification-history{display:block;min-width:0!important;width:100%}.notification-history thead{display:none}.notification-history tbody{display:grid;gap:10px;width:100%}.notification-history-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 10px;padding:11px 12px;border:1px solid var(--vs-border);border-radius:var(--ha-card-border-radius,10px);background:var(--vs-card-surface);min-width:0}.notification-history-row td{display:block;padding:0!important;border:0;min-width:0;overflow-wrap:anywhere}.notification-history-row td::before{display:block;content:attr(data-label);font-size:10px;color:var(--secondary-text-color);margin-bottom:2px}.notification-history-row .history-time{grid-column:1}.notification-history-row .history-event{grid-column:1}.notification-history-row .history-recipient{grid-column:1/-1}.notification-history-row .history-status{grid-column:2;grid-row:1/3;justify-self:end}.notification-history-row .history-status::before{display:none}.notification-history-row .history-reason{grid-column:1/-1;color:var(--secondary-text-color);font-size:11px}.notification-history-detail{display:block;width:100%;min-width:0}.notification-history-detail td{display:block;width:100%;box-sizing:border-box;padding:12px!important;border:0;border-radius:10px}.notification-delivery-detail-grid{grid-template-columns:1fr;gap:12px;max-width:100%;overflow:hidden}.notification-delivery-detail-grid .wide-detail{grid-column:auto}.delivery-tech-grid{grid-template-columns:1fr}.delivery-job-head{flex-direction:column;align-items:stretch}.delivery-job-head .delivery-open-job{width:100%;justify-content:center}.delivery-job-actions{gap:8px}.delivery-job-action{min-height:42px;flex:1 1 auto;justify-content:center}.notification-message{max-height:none;overflow:visible}.delivery-tech-value{align-items:flex-start}.delivery-tech-value code{font-size:10px}
          .dialog-field{padding:8px 16px 14px}.dialog-option{padding:6px 16px 14px}.vs-dialog-header{grid-template-columns:32px minmax(0,1fr);gap:10px;padding:16px 16px 10px}.vs-dialog-icon{width:32px;height:32px}.vs-dialog-actions{padding:12px 16px 16px;flex-wrap:wrap}.vs-dialog-actions button{min-width:0;flex:1 1 120px}.advanced-body{padding:0 12px 12px}.advanced-testing>summary{padding:12px}
        }
        @media (max-width:460px) {
          .notification-message-page{max-width:none}.notification-message-head{gap:10px}.notification-message-head h2{font-size:19px}.notification-message-facts{grid-template-columns:1fr}.notification-message-fact,.notification-message-fact:nth-child(odd),.notification-message-fact:nth-child(even){grid-template-columns:1fr;gap:3px;padding:9px 0;border-left:0}.notification-current-actions{flex-direction:column}.notification-job-action{width:100%;justify-content:center}
          .page{padding:8px}.entry-card,.editor-card{padding:11px}.forecast-policy-cards,.forecast-policy-grid{grid-template-columns:1fr}.forecast-model-head,.forecast-archive-row{align-items:stretch;flex-direction:column}.forecast-model-actions{align-items:stretch}.forecast-model-actions .field{min-width:0}
.statistics-report-card{--statistics-card-pad:11px}.weekday-schedule-grid,.day-override-row{grid-template-columns:1fr}.day-override-name{grid-column:auto}.maintenance-status-grid{grid-template-columns:1fr}.data-layer-summary{grid-template-columns:1fr}.data-backup-row{align-items:stretch;flex-direction:column}.data-backup-row .button-row{justify-content:flex-start}.data-layer-choices{padding:4px 16px 14px}.maintenance-session-head,.maintenance-page-head{align-items:stretch;flex-direction:column}.maintenance-session-head .button-row,.maintenance-page-head button{width:100%}.maintenance-actions-summary{grid-template-columns:1fr}.tabs button.tab{flex:1 1 calc(50% - 4px);min-width:0}.job-summary{grid-template-columns:1fr}.mobile-card-table td{grid-template-columns:1fr;gap:3px}.mobile-card-table .mobile-actions-cell{align-items:flex-start;flex-direction:column}.mobile-card-table .schedule-actions,.mobile-card-table .table-actions-inner,.mobile-card-table .room-actions-inner,.mobile-card-table .override-actions{justify-content:flex-start}.summary-card{grid-template-columns:1fr}.summary-card b{text-align:left}.summary-card-gate,.summary-card-mode{grid-template-columns:1fr}.summary-actions{justify-content:flex-start}.summary-card-mode>.summary-mode-value{justify-content:flex-start}.delivery-job-actions{flex-direction:column}.delivery-job-action{width:100%}.vs-dialog{width:calc(100vw - 16px)}.vs-dialog-actions{flex-direction:column}.vs-dialog-actions button{width:100%;flex-basis:auto}.ui-toast{align-items:flex-start;text-align:left}.toast-host{width:calc(100vw - 16px)}
        }
      </style>
      <div class="app-bar"><button type="button" class="app-bar-menu" title="${this._escape(this._tr("panel.open_home_assistant_menu"))}" aria-label="${this._escape(this._tr("panel.open_home_assistant_menu"))}">${this._mdi("menu")}</button><div class="app-bar-title">${this._escape(appBarTitle)}</div><div class="loading-indicator ${this._loading ? "visible" : ""}">${this._tr("panel.loading")}</div></div>
      <div class="page">
        <div class="top"><div><div class="section-title">${sectionTitle}</div><div class="version">${this._escape(this._data?.version || this._panel?.config?.version || "")}</div></div>${editingPage ? "" : `<button class="ghost icon-only refresh" title="${this._tr("panel.refresh")}" aria-label="${this._tr("panel.refresh")}">${this._mdi("refresh")}</button>`}</div>
        ${editingPage ? "" : this._tabsHtml()}
        ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}
        <div class="content-shell">${this._globalBannersHtml()}${content}</div>
      </div>
      <div class="toast-host">${this._toastHtml()}</div>
      <div class="dialog-host"></div>`;
    this._wire();
    this._syncZoneCountdownTimer();
  }

  _wire() {
    this.shadowRoot.querySelectorAll(".search-select").forEach((wrap) => {
      const input=wrap.querySelector(".search-select-input");
      const hidden=wrap.querySelector(".search-select-value");
      const options=[...wrap.querySelectorAll("button.search-select-option")];
      if(!input || !hidden) return;
      const filter=()=>{
        const query=String(input.value||"").trim().toLowerCase();
        let visible=0;
        options.forEach((option)=>{ const match=!query || String(option.dataset.search||"").includes(query); option.hidden=!match; if(match)visible+=1; });
        wrap.classList.add("open");
      };
      input.addEventListener("focus",()=>{options.forEach((option)=>option.hidden=false);wrap.classList.add("open");input.select();});
      input.addEventListener("input",()=>{
        if(input.value !== (input.dataset.selectedLabel||"")) {
          const changed=hidden.value!=="";
          hidden.value="";
          if(changed) hidden.dispatchEvent(new Event("change",{bubbles:true}));
        }
        filter();
      });
      input.addEventListener("keydown",(event)=>{ if(event.key==="Escape"){wrap.classList.remove("open");input.blur();} });
      input.addEventListener("blur",()=>setTimeout(()=>wrap.classList.remove("open"),120));
      options.forEach((option)=>option.addEventListener("mousedown",(event)=>event.preventDefault()));
      options.forEach((option)=>option.addEventListener("click",()=>{
        hidden.value=option.dataset.value||"";
        input.value=option.dataset.label||"";
        input.dataset.selectedLabel=input.value;
        hidden.dispatchEvent(new Event("change",{bubbles:true}));
        wrap.classList.remove("open");
      }));
    });
    const noticeStack=this.shadowRoot.querySelector("details.system-notice-stack");
    if(noticeStack) noticeStack.addEventListener("toggle",()=>{this._systemNoticesOpen=noticeStack.open;const icon=noticeStack.querySelector(".system-banner-more .button-icon");if(icon)icon.outerHTML=this._mdi(noticeStack.open?"chevron-up":"chevron-down");});

    const openMaintenance=async(editSession=null,manual=false)=>{
      this._view="maintenance"; this._error=null;
      const q=new URLSearchParams({config_entry:this._schedulerEntryId,view:"maintenance"});history.replaceState(null,"",`/vacuum-schedule?${q.toString()}`);
      if(!this._maintenanceData) await this._loadMaintenance(false);
      if(editSession||manual){
        const session=editSession?(this._maintenanceData?.sessions||[]).find(x=>String(x.session_id)===String(editSession)):null;
        this._maintenanceDraft=this._maintenanceDraftFor(session||null);
      } else this._maintenanceDraft=null;
      this._render();
    };
    this.shadowRoot.querySelectorAll("button.system-banner-action").forEach(btn=>btn.addEventListener("click",(event)=>{event.preventDefault();event.stopPropagation();if(btn.dataset.globalNoticeAction==="maintenance")void openMaintenance();}));
    this.shadowRoot.querySelectorAll("button.maintenance-open").forEach(btn=>btn.addEventListener("click",()=>void openMaintenance()));
    this.shadowRoot.querySelectorAll("button.maintenance-add").forEach(btn=>btn.addEventListener("click",()=>void openMaintenance(null,true)));
    this.shadowRoot.querySelectorAll("button.maintenance-back").forEach(btn=>btn.addEventListener("click",()=>{this._maintenanceDraft=null;this._view="status";const q=new URLSearchParams({config_entry:this._schedulerEntryId,view:"status"});history.replaceState(null,"",`/vacuum-schedule?${q.toString()}`);this._render();}));
    this.shadowRoot.querySelectorAll("button.maintenance-confirm,button.maintenance-edit").forEach(btn=>btn.addEventListener("click",()=>void openMaintenance(btn.dataset.maintenanceSession,false)));
    this.shadowRoot.querySelectorAll("select[data-maintenance-action]").forEach(el=>el.addEventListener("change",()=>{if(!this._maintenanceDraft)return;const tank=el.dataset.maintenanceAction;this._maintenanceDraft.values[tank]={action:el.value,value:this._maintenanceDraft.values[tank]?.value||""};this._render();}));
    this.shadowRoot.querySelectorAll("input[data-maintenance-value]").forEach(el=>el.addEventListener("input",()=>{if(!this._maintenanceDraft)return;const tank=el.dataset.maintenanceValue;this._maintenanceDraft.values[tank].value=el.value;}));
    this.shadowRoot.querySelector("textarea[data-maintenance-note]")?.addEventListener("input",(event)=>{if(this._maintenanceDraft)this._maintenanceDraft.note=event.target.value;});
    this.shadowRoot.querySelectorAll("button.maintenance-cancel").forEach(btn=>btn.addEventListener("click",()=>{this._maintenanceDraft=null;this._render();}));
    this.shadowRoot.querySelectorAll("button.maintenance-save").forEach(btn=>btn.addEventListener("click",async()=>{
      const draft=this._maintenanceDraft;if(!draft)return;
      const interpretations={}; let invalid=false;
      for(const tank of ["clean","dirty"]){const item=draft.values[tank]||{};const action=String(item.action||"");if(!action||action==="__none__")continue;const row={action};if(["level","add_ml","remove_ml"].includes(action)){const value=Number(item.value);if(!Number.isFinite(value)||(action==="level"?(value<0||value>100):value<=0)){invalid=true;break;}row.value=value;}interpretations[tank]=row;}
      if(invalid){this._notify(this._tr("panel.invalid_maintenance_value"),"error",4200);return;}
      if(!Object.keys(interpretations).length){this._notify(this._tr("panel.maintenance_interpretation_required"),"error",4200);return;}
      try{
        await this._callWs({type:"vacuum_schedule/maintenance/save",entry_id:this._schedulerEntryId,interpretations,...(draft.session_id?{session_id:draft.session_id}:{}),...(draft.note?{note:draft.note}:{})});
        this._maintenanceDraft=null;
        await Promise.all([this._loadMaintenance(false),this._loadSettings(false)]);
        this._render();this._notify(this._tr("panel.maintenance_saved"));
      }catch(err){this._notify(this._errorText(err),"error",5000);}
    }));

    this.shadowRoot.querySelectorAll("button.tab").forEach((el) => el.addEventListener("click", () => {
      const requestedView = el.dataset.view;
      this._view = requestedView;
      this._maintenanceDraft = null;
      this._error = null;
      this._notificationHistorySelectedId=null; this._notificationMessageSelectedId=null; this._notificationMessageDetail=null; this._deepLinkJobId=null;
      this._settingsEditor=null; this._roomDraft=null; this._roomTestResult=null; this._notificationRecipientDraft=null; this._notificationRecipientIsNew=false; this._notificationRecipientDirty=false;
      this._notificationGlobalDraft=null; this._notificationGlobalDirty=false; this._policyDraft=null; this._policyDirty=false;
      const tabQuery=new URLSearchParams({config_entry:this._schedulerEntryId,view:requestedView});history.replaceState(null,"",`/vacuum-schedule?${tabQuery.toString()}`);
      // Navigation must never wait for a WebSocket round-trip. Render the selected
      // tab immediately, then refresh Settings/Testing data in the background.
      this._render();
      if (requestedView === "settings" || requestedView === "testing") {
        void this._loadSettings(false).then(() => {
          if (this._view === requestedView && !this._editingAny) this._render();
        });
      }
      if (requestedView === "notifications" || requestedView === "testing") {
        void this._loadNotifications(false).then(() => {
          if (this._view === requestedView && !this._editingAny) this._render();
        });
      }
      if (requestedView === "statistics") {
        void this._loadStatistics(false).then(async () => {
          if (this._statisticsTab === "forecast") await this._loadForecast(false);
          if (this._view === requestedView && !this._editingAny) this._render();
        });
      }
    }));
    const appBarMenu = this.shadowRoot.querySelector("button.app-bar-menu");
    if (appBarMenu) appBarMenu.addEventListener("click", () => this._toggleHomeAssistantMenu());
    const refresh = this.shadowRoot.querySelector("button.refresh");
    if (refresh) refresh.addEventListener("click", () => this._load(true));
    this.shadowRoot.querySelectorAll("button.add").forEach((el) => el.addEventListener("click", () => this._openEditor(el.dataset.entry)));
    this.shadowRoot.querySelectorAll("button.edit").forEach((el) => el.addEventListener("click", () => this._openEditor(el.dataset.entry, el.dataset.schedule)));
    this.shadowRoot.querySelectorAll("button.delete").forEach((el) => el.addEventListener("click", () => this._delete(el.dataset.entry, el.dataset.schedule, el.dataset.name)));
    this.shadowRoot.querySelectorAll("button.schedule-toggle").forEach((el)=>el.addEventListener("click",async()=>{
      const enabling=el.dataset.enabled==="true";
      if(!enabling){
        const confirmed=await this._confirmAction(
          this._tr("panel.disable_schedule"),
          this._tr("panel.new_occurrences_for_this_schedule_will_not_be_created_until_you_enable_i"),
          this._tr("panel.disable"),
          true,
        );
        if(!confirmed)return;
      }
      try{await this._callWs({type:"vacuum_schedule/schedules/set_enabled",entry_id:el.dataset.entry,schedule_id:el.dataset.schedule,enabled:enabling});await this._load(true);this._notify(enabling?this._tr("panel.schedule_enabled"):this._tr("panel.schedule_disabled"));}catch(err){this._notify(this._errorText(err),"error",4200);}
    }));
    this.shadowRoot.querySelectorAll("button.job-action").forEach((el)=>el.addEventListener("click",async(event)=>{
      event.preventDefault();
      event.stopPropagation();
      let action=el.dataset.action;
      if(action==="schedule_pause"||action==="schedule_resume"){
        const pausing=action==="schedule_pause";
        if(pausing){
          const confirmed=await this._confirmAction(this._tr("panel.pause_schedule"),this._tr("panel.pause_schedule_help"),this._tr("panel.pause_schedule"),false);
          if(!confirmed)return;
        }
        try{
          await this._callWs({type:"vacuum_schedule/schedules/set_paused",entry_id:this._schedulerEntryId,schedule_id:el.dataset.schedule,paused:pausing});
          await this._load(true);
          this._notify(pausing?this._tr("panel.schedule_paused_notice"):this._tr("panel.schedule_resumed_notice"));
        }catch(err){this._notify(this._errorText(err),"error",4200);}
        return;
      }
      if(action==="additional_run"){
        const confirmed=await this._confirmAction(this._tr("panel.additional_run"),this._tr("panel.additional_run_help"),this._tr("panel.run"),false);
        if(!confirmed)return;
        try{await this._callWs({type:"vacuum_schedule/jobs/action",entry_id:this._schedulerEntryId,job_id:el.dataset.job,action:"additional_run"});await this._load(true);this._notify(this._tr("panel.manual_job_created"));}catch(err){this._notify(this._errorText(err),"error",4200);}
        return;
      }
      if(action==="start_now"||action==="start_now_ignore_busy"||action==="skip"||action==="cancel"){
        const isStart=action==="start_now"||action==="start_now_ignore_busy";
        const isSkip=action==="skip";
        const currentExecution=el.dataset.currentExecution==="1";
        let title=isStart?this._tr("panel.execute_early"):isSkip?this._tr("panel.skip_job"):currentExecution?this._tr("panel.stop_current_execution"):this._tr("panel.cancel_execution");
        let message=isStart?this._tr("panel.execute_early_help"):isSkip?this._tr("panel.this_job_will_finish_as_an_uncompleted_scheduled_cleaning"):this._tr("panel.the_current_job_execution_will_be_cancelled");
        let confirmLabel=isStart?this._tr("panel.execute_early"):isSkip?this._tr("panel.skip"):currentExecution?this._tr("panel.stop_current_execution"):this._tr("panel.cancel_execution");
        if(action==="start_now_ignore_busy"){
          const supplied=String(el.dataset.busyZones||"").split(",").map(x=>x.trim()).filter(Boolean);
          const found=this._jobById(el.dataset.job);
          const currentBusy=Object.values(found?.job?.zone_runs||{}).filter((zone)=>String(zone?.state||"")!=="FINISHED" && (zone?.blockers||[]).includes("zone_busy")).map((zone)=>String(zone?.zone_name||zone?.zone_id||"")).filter(Boolean);
          const busyZones=supplied.length?supplied:currentBusy;
          title=this._tr("panel.occupied_zones");
          message=this._tr("panel.start_ignoring_occupancy_confirm", {p1: busyZones.join(", ")||this._tr("panel.occupied_zones")});
          confirmLabel=this._tr("panel.start_ignoring_occupancy");
        } else if(action==="start_now"){
          const found=this._jobById(el.dataset.job);
          const busyZones=Object.values(found?.job?.zone_runs||{})
            .filter((zone)=>String(zone?.state||"")!=="FINISHED" && (zone?.blockers||[]).includes("zone_busy"))
            .map((zone)=>String(zone?.zone_name||zone?.zone_id||""))
            .filter(Boolean);
          if(busyZones.length){
            title=this._tr("panel.occupied_zones");
            message=this._tr("panel.start_ignoring_occupancy_confirm", {p1: busyZones.join(", ")});
            confirmLabel=this._tr("panel.start_ignoring_occupancy");
            action="start_now_ignore_busy";
          }
        }
        const confirmed=await this._confirmAction(title,message,confirmLabel,!isStart);
        if(!confirmed)return;
      }
      try{await this._callWs({type:"vacuum_schedule/jobs/action",entry_id:this._schedulerEntryId,job_id:el.dataset.job,action});await this._loadScheduler();if(this._view==="notifications"&&this._notificationMessageSelectedId)await this._loadNotifications(false);this._render();this._notify(this._tr("panel.action_completed"));}catch(err){if(this._view==="notifications"&&this._notificationMessageSelectedId){await this._loadScheduler();await this._loadNotifications(false);this._render();}this._notify(this._errorText(err),"error",4200);}
    }));
    this.shadowRoot.querySelectorAll("button.close").forEach((el) => el.addEventListener("click", () => this._closeEditor()));
    const save = this.shadowRoot.querySelector("button.save");
    if (save) save.addEventListener("click", () => this._save());
    const schedulerEntry = this.shadowRoot.querySelector("#scheduler-entry");
    if (schedulerEntry) schedulerEntry.addEventListener("change", async () => {
      this._schedulerEntryId = schedulerEntry.value;
      this._rememberEntry(this._schedulerEntryId);
      this._replaceContextUrl(this._schedulerEntryId);
      this._debugTarget = "all";
      this._statisticsTab = "summary";
      this._forecastData = null; this._forecastDataEntryId = null; this._forecastError = null; this._forecastLoading = false;
      this._maintenanceData = null; this._maintenanceDraft = null;
      this._historyJobDetails.clear(); this._historyJobOpen.clear(); this._historyJobLoading.clear(); this._historyJobTab.clear(); this._activeJobOpen.clear(); this._activeJobTab.clear(); this._activeJobDetails.clear(); this._activeJobLoading.clear();
      this._roomDraft = null; this._roomTestResult = null; this._settingsEditor = null; this._notificationRecipientDraft=null; this._notificationRecipientIsNew=false; this._notificationRecipientDirty=false;
      this._scheduleMidnightRefresh();
      await this._loadSettings(false);
      await this._loadNotifications(false);
      if(this._view==="statistics") await this._loadStatistics(false);
      if(this._view==="maintenance") await this._loadMaintenance(false);
      this._render();
    });
    const statisticsReload=()=>void this._loadStatistics(true);
    const statisticsRefresh=this.shadowRoot.querySelector("button.statistics-refresh");if(statisticsRefresh)statisticsRefresh.addEventListener("click",statisticsReload);
    this.shadowRoot.querySelectorAll("button[data-statistics-tab]").forEach((button)=>button.addEventListener("click",()=>{
      this._statisticsTab=button.dataset.statisticsTab||"summary";
      this._render();
      if(this._statisticsTab==="forecast" && (this._forecastDataEntryId!==this._schedulerEntryId || !this._forecastData)) void this._loadForecast(true);
    }));
    const forecastRetry=this.shadowRoot.querySelector("button.forecast-retry");if(forecastRetry)forecastRetry.addEventListener("click",()=>void this._loadForecast(true));
    [["#statistics-schedule","schedule_id"],["#statistics-zone","zone_id"],["#statistics-execution","execution_mode"],["#statistics-source","execution_source"]].forEach(([selector,key])=>{const el=this.shadowRoot.querySelector(selector);if(el)el.addEventListener("change",()=>{this._statisticsFilters[key]=el.value;statisticsReload();});});
    [["#statistics-from","date_from"],["#statistics-to","date_to"]].forEach(([selector,key])=>{const el=this.shadowRoot.querySelector(selector);if(el)el.addEventListener("change",()=>{this._statisticsFilters[key]=el.value;if(key==="date_from"&&this._statisticsFilters.date_to&&el.value>this._statisticsFilters.date_to)this._statisticsFilters.date_to=el.value;if(key==="date_to"&&this._statisticsFilters.date_from&&el.value<this._statisticsFilters.date_from)this._statisticsFilters.date_from=el.value;statisticsReload();});});
    this.shadowRoot.querySelectorAll("button[data-statistics-range]").forEach((button)=>button.addEventListener("click",()=>{this._statisticsFilters.date_from=button.dataset.statisticsFrom||"";this._statisticsFilters.date_to=button.dataset.statisticsTo||"";statisticsReload();}));
    this.shadowRoot.querySelectorAll("button.forecast-archive").forEach(btn=>btn.addEventListener("click",async()=>{const metric=btn.dataset.metric||"";const input=this.shadowRoot.querySelector(`.forecast-retain-days[data-metric="${metric}"]`);const retainDays=Math.max(0,Math.min(3650,Number(input?.value||0)));try{const preview=await this._callWs({type:"vacuum_schedule/statistics/forecast/archive_preview",entry_id:this._schedulerEntryId,metric,retain_days:retainDays});if(!preview?.will_rotate_generation){this._notify(this._tr("panel.nothing_to_archive"));return;}const message=this._tr("panel.archive_model_preview",{days:retainDays,archive:preview.archive_sample_count??0,retain:preview.retained_sample_count??0,active:preview.active_after_count??0});const confirmed=await this._confirmAction(this._tr("panel.archive_model"),message,this._tr("panel.archive"),false);if(!confirmed)return;await this._callWs({type:"vacuum_schedule/statistics/forecast/archive",entry_id:this._schedulerEntryId,metric,retain_days:retainDays});await this._loadForecast(false);this._render();this._notify(this._tr("panel.model_archived"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button.forecast-restore-archive").forEach(btn=>btn.addEventListener("click",async()=>{const confirmed=await this._confirmAction(this._tr("panel.restore_model_archive"),this._tr("panel.restore_model_archive_confirm"),this._tr("panel.restore"),false);if(!confirmed)return;try{await this._callWs({type:"vacuum_schedule/statistics/forecast/restore_archive",entry_id:this._schedulerEntryId,metric:btn.dataset.metric,archive_id:btn.dataset.archiveId});await this._loadForecast(false);this._render();this._notify(this._tr("panel.model_archive_restored"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button.forecast-delete-archive").forEach(btn=>btn.addEventListener("click",async()=>{const confirmed=await this._confirmAction(this._tr("panel.delete_model_archive"),this._tr("panel.delete_model_archive_confirm"),this._tr("panel.delete"),true);if(!confirmed)return;try{await this._callWs({type:"vacuum_schedule/statistics/forecast/delete_archive",entry_id:this._schedulerEntryId,metric:btn.dataset.metric,archive_id:btn.dataset.archiveId});await this._loadForecast(false);this._render();this._notify(this._tr("panel.deleted"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button.water-service").forEach(btn=>btn.addEventListener("click",async()=>{try{await this._callWs({type:"vacuum_schedule/statistics/water_service",entry_id:this._schedulerEntryId,tank:btn.dataset.tank,action:btn.dataset.action,...(btn.dataset.pending?{pending_id:btn.dataset.pending}:{})});await Promise.all([this._loadStatistics(false),this._loadSettings(false)]);this._render();this._notify(this._tr("panel.water_service_saved"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button.water-service-level").forEach(btn=>btn.addEventListener("click",async()=>{const raw=await this._promptAction(this._tr(btn.dataset.tank==="dirty"?"panel.dirty_water_filled_percent":"panel.clean_water_remaining_percent"),this._tr(btn.dataset.tank==="dirty"?"panel.dirty_level_percent_help":"panel.clean_level_percent_help"),{label:"%",value:"50",type:"number",min:"0",max:"100",confirmLabel:this._tr("panel.save")});if(raw===null)return;const value=Number(raw);if(!Number.isFinite(value)||value<0||value>100){this._notify(this._tr("panel.invalid_percent"),"error",4200);return;}try{await this._callWs({type:"vacuum_schedule/statistics/water_service",entry_id:this._schedulerEntryId,tank:btn.dataset.tank,action:"level",value,...(btn.dataset.pending?{pending_id:btn.dataset.pending}:{})});await Promise.all([this._loadStatistics(false),this._loadSettings(false)]);this._render();this._notify(this._tr("panel.water_service_saved"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button.water-service-volume").forEach(btn=>btn.addEventListener("click",async()=>{const raw=await this._promptAction(this._tr("panel.enter_volume_ml"),"",{label:"ml",value:"500",type:"number",min:"1",confirmLabel:this._tr("panel.save")});if(raw===null)return;const value=Number(raw);if(!Number.isFinite(value)||value<=0){this._notify(this._tr("panel.invalid_volume"),"error",4200);return;}try{await this._callWs({type:"vacuum_schedule/statistics/water_service",entry_id:this._schedulerEntryId,tank:btn.dataset.tank,action:btn.dataset.action,value,...(btn.dataset.pending?{pending_id:btn.dataset.pending}:{})});await Promise.all([this._loadStatistics(false),this._loadSettings(false)]);this._render();this._notify(this._tr("panel.water_service_saved"));}catch(err){this._notify(this._errorText(err),"error",4200);}}));
    this.shadowRoot.querySelectorAll("button[data-active-job-tab]").forEach((button)=>button.addEventListener("click",()=>{const id=String(button.dataset.activeJobTabJob||"");if(id){this._activeJobTab.set(id,button.dataset.activeJobTab||"now");this._activeJobOpen.add(id);}this._render();}));
    this.shadowRoot.querySelectorAll("details.active-job-card").forEach((el)=>el.addEventListener("toggle",()=>{const id=String(el.dataset.jobId||"");if(!id)return;if(el.open){this._activeJobOpen.add(id);void this._loadActiveJobDetails(id,false,true);}else this._activeJobOpen.delete(id);}));
    const historyFilterDetails=this.shadowRoot.querySelector("details.history-filter-popover");
    if(historyFilterDetails) historyFilterDetails.addEventListener("toggle",()=>{this._historyFiltersOpen=historyFilterDetails.open;});
    this.shadowRoot.querySelectorAll("button[data-history-filter-group]").forEach((button)=>button.addEventListener("click",()=>{
      const group=button.dataset.historyFilterGroup; const value=button.dataset.historyFilterValue||"all";
      if(group){this._toggleHistoryFilterValue(group,value);this._historyFiltersOpen=true;this._render();}
    }));
    this.shadowRoot.querySelectorAll("button[data-history-filter-clear]").forEach((button)=>button.addEventListener("click",()=>{
      const group=button.dataset.historyFilterClear; const value=button.dataset.historyFilterClearValue||null;
      if(group){this._clearHistoryFilterValue(group,value);this._render();}
    }));
    this.shadowRoot.querySelectorAll("button.history-filter-reset").forEach((button)=>button.addEventListener("click",()=>{
      this._historyFilters={execution_mode:"all",result:"all",source:"all",schedule_id:"all"}; this._historyFiltersOpen=true; this._render();
    }));
    this.shadowRoot.querySelectorAll("button[data-history-tab]").forEach((button)=>button.addEventListener("click",()=>{const id=String(button.dataset.historyTabJob||"");if(id)this._historyJobTab.set(id,button.dataset.historyTab||"summary");this._render();}));
    this.shadowRoot.querySelectorAll("details.history-job-card").forEach((el)=>el.addEventListener("toggle",()=>{
      const id=String(el.dataset.historyJobId||"");
      if(el.open){this._historyJobOpen.add(id);void this._loadHistoryJobDetails(id);}
      else{this._historyJobOpen.delete(id);}
    }));
    this.shadowRoot.querySelectorAll("button.advance").forEach((el)=>el.addEventListener("click", async()=>this._debugAction("vacuum_schedule/debug/advance_time", {seconds:Number(el.dataset.seconds)})));
    const nextTransition = this.shadowRoot.querySelector("button.next-transition"); if (nextTransition) nextTransition.addEventListener("click", ()=>this._debugAction("vacuum_schedule/debug/next_transition"));
    const rebuildSchedule=this.shadowRoot.querySelector("button.rebuild-schedule"); if(rebuildSchedule)rebuildSchedule.addEventListener("click",async()=>{
      const choice=await this._confirmActionWithCheckbox(
        this._tr("panel.refresh_future_jobs"),
        this._tr("panel.refresh_future_jobs_confirm"),
        this._tr("panel.restore_early_executed_jobs"),
        this._tr("panel.refresh"),
        false,
      );
      if(!choice?.confirmed)return;
      try{
        const result=await this._callWs({
          type:"vacuum_schedule/scheduler/rebuild_schedule",
          entry_id:this._schedulerEntryId,
          restore_early_executed:!!choice.checked,
        });
        await this._load(true);
        this._notify(this._tr("panel.schedule_refreshed",{created:result?.created_jobs??0}));
      }catch(err){this._notify(this._errorText(err),"error",4200);}
    });
    const resetTime = this.shadowRoot.querySelector("button.reset-time"); if (resetTime) resetTime.addEventListener("click", async()=>{
      const confirmed=await this._confirmAction(
        this._tr("panel.reset_test_time"),
        this._tr("panel.reset_test_time_confirm"),
        this._tr("panel.reset_and_restore"),
        false,
      );
      if(!confirmed)return;
      await this._debugAction("vacuum_schedule/debug/reset_time",{},this._tr("panel.test_time_reset_and_jobs_restored"));
    });
    const resetSimulation = this.shadowRoot.querySelector("button.reset-simulation"); if (resetSimulation) resetSimulation.addEventListener("click", async()=>{
      const message = this._tr("panel.reset_simulation_active_simulated_jobs_and_history_will_be_deleted_the_t");
      const confirmed = await this._confirmAction(this._tr("panel.reset_simulation_01dcd64"), message, this._tr("panel.reset"), true);
      if (!confirmed) return;
      this._debugTarget = "all";
      await this._debugAction("vacuum_schedule/debug/reset_dry_run", {}, this._tr("panel.simulation_reset"));
    });

    const entryId = this._selectEntry(this._schedulerEntryId, false);
    const settingsWrite = async (message, successMessage = null) => {
      if (!entryId || this._loading) return null;
      this._loading = true; this._error = null; this._syncLoadingIndicator();
      try {
        const result = await this._callWs({ ...message, entry_id: entryId });
        await Promise.all([this._loadScheduler(), this._loadSettings(false)]);
        if (successMessage) this._notify(successMessage);
        return result;
      } catch (err) { this._error = this._errorText(err); this._notify(this._error, "error", 4200); return null; }
      finally { this._loading = false; this._render(); }
    };
    const interfaceLanguage=this.shadowRoot.querySelector("#interface-language");
    if(interfaceLanguage)interfaceLanguage.addEventListener("change",()=>{
      this._interfaceLanguageDraft=interfaceLanguage.value||"auto";
      this._interfaceLanguageDirty=true;
    });
    const interfaceLanguageSave=this.shadowRoot.querySelector("button.interface-language-save");
    if(interfaceLanguageSave)interfaceLanguageSave.addEventListener("click",async()=>{
      if(!entryId||this._loading)return;
      const selected=String(this._interfaceLanguageDraft ?? interfaceLanguage?.value ?? "auto");
      this._loading=true;this._error=null;this._syncLoadingIndicator();
      try{
        await this._callWs({type:"vacuum_schedule/settings/update_interface_language",entry_id:entryId,language:selected});
        if(this._settingsData)this._settingsData.interface_language=selected;
        this._interfaceLanguageDirty=false;this._syncInterfaceLanguageDraft(true);
        this._translationLanguage=null;
        await this._ensureTranslations();
        await this._loadNotifications(false);
        this._notify(this._tr("panel.interface_language_saved"));
      }catch(err){this._error=this._errorText(err);this._notify(this._error,"error",4200);}
      finally{this._loading=false;this._render();}
    });
    const executionAdvanced=this.shadowRoot.querySelector("details.execution-advanced");
    if(executionAdvanced)executionAdvanced.addEventListener("toggle",()=>{this._executionAdvancedOpen=executionAdvanced.open;});
    this.shadowRoot.querySelectorAll("button.execution-mode-choice").forEach(btn=>btn.addEventListener("click",async()=>{
      await this._requestExecutionModeSwitch(btn.dataset.executionMode);
    }));
    this.shadowRoot.querySelectorAll("button.quick-execution-mode").forEach(btn=>btn.addEventListener("click",async()=>{
      await this._requestExecutionModeSwitch(btn.dataset.executionMode);
    }));
    const dryRunDurationInput=this.shadowRoot.querySelector("#dry-run-duration");
    if(dryRunDurationInput)dryRunDurationInput.addEventListener("input",()=>{
      this._executionSettingsDraft=this._executionSettingsDraft||{};
      this._executionSettingsDraft.dry_run_duration=dryRunDurationInput.value;
      this._executionSettingsDirty=true;
    });
    const runtimeErrorRecoveryInput=this.shadowRoot.querySelector("#runtime-error-recovery-minutes");
    if(runtimeErrorRecoveryInput)runtimeErrorRecoveryInput.addEventListener("input",()=>{
      this._executionSettingsDraft=this._executionSettingsDraft||{};
      this._executionSettingsDraft.runtime_error_recovery_minutes=runtimeErrorRecoveryInput.value;
      this._executionSettingsDirty=true;
    });
    const restoreSettingsInput=this.shadowRoot.querySelector("#real-restore-settings");
    if(restoreSettingsInput)restoreSettingsInput.addEventListener("change",()=>{
      this._executionSettingsDraft=this._executionSettingsDraft||{};
      this._executionSettingsDraft.restore_previous_settings=!!restoreSettingsInput.checked;
      this._executionSettingsDirty=true;
    });
    const executionSave=this.shadowRoot.querySelector("button.execution-additional-save"); if(executionSave)executionSave.addEventListener("click",async()=>{
      if(!entryId||this._saving||this._executionOptionsSaving)return;
      const mode=String(this._settingsData?.execution?.mode||"DRY_RUN")==="REAL"?"REAL":"DRY_RUN";
      const dryRunDuration=Math.max(1,Math.min(300,Number(this._executionSettingsDraft?.dry_run_duration??this.shadowRoot.querySelector("#dry-run-duration")?.value??10)));
      const restorePreviousSettings=Boolean(this._executionSettingsDraft?.restore_previous_settings??this.shadowRoot.querySelector("#real-restore-settings")?.checked);
      const runtimeErrorRecoveryMinutes=Math.max(1,Math.min(1440,Number(this._executionSettingsDraft?.runtime_error_recovery_minutes??this.shadowRoot.querySelector("#runtime-error-recovery-minutes")?.value??30)));
      const snapshot={mode,dry_run:{execution_duration_seconds:dryRunDuration},real:{restore_previous_settings:restorePreviousSettings,runtime_error_recovery_minutes:runtimeErrorRecoveryMinutes}};
      this._executionOptionsSaving=true;this._error=null;this._setExecutionFormSaving(true);
      try{
        await this._callWs({type:"vacuum_schedule/settings/update_execution",entry_id:entryId,...snapshot});
        await this._loadSettings(false);
        this._executionSettingsDirty=false;this._syncExecutionSettingsDraft(true);this._syncExecutionOptionsDom();
        this._notify(this._tr("panel.execution_settings_saved"));
      }catch(err){this._error=this._errorText(err);this._notify(this._error,"error",4200);}
      finally{this._executionOptionsSaving=false;this._setExecutionFormSaving(false);}
    });
    const faultSave=this.shadowRoot.querySelector("button.execution-fault-save"); if(faultSave)faultSave.addEventListener("click",()=>this._debugAction("vacuum_schedule/debug/set_execution_fault",{fault:this.shadowRoot.querySelector("#execution-fault")?.value||null},this._tr("panel.execution_fault_saved")));
    const parseValue = (text) => {
      const raw=String(text??"").trim();
      if (raw==="") return "";
      if (raw.toLowerCase()==="true" || raw.toLowerCase()==="on") return true;
      if (raw.toLowerCase()==="false" || raw.toLowerCase()==="off") return false;
      if (raw.toLowerCase()==="null") return null;
      if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw);
      if ((raw.startsWith("[")&&raw.endsWith("]"))||(raw.startsWith("{")&&raw.endsWith("}"))) { try { return JSON.parse(raw); } catch (_) {} }
      return raw;
    };

    this.shadowRoot.querySelectorAll("button.quick-gate").forEach(btn=>btn.addEventListener("click",async()=>{
      const enabling=btn.dataset.mode==="enabled";
      const confirmed=await this._confirmAction(
        enabling?this._tr("panel.enable_schedule_execution"):this._tr("panel.disable_schedule_execution"),
        enabling?this._tr("panel.after_enabling_the_scheduler_may_again_start_ready_jobs_automatically_wi"):this._tr("panel.the_scheduler_will_keep_planning_and_showing_jobs_but_new_cleanings_will"),
        enabling?this._tr("panel.enable"):this._tr("panel.disable"),
        !enabling,
      );
      if(!confirmed)return;
      await settingsWrite({type:"vacuum_schedule/settings/update_policy",policy:{execution_gate:btn.dataset.mode,disabled_until:null}},enabling?this._tr("panel.schedule_execution_enabled"):this._tr("panel.schedule_execution_disabled"));
    }));
    const quickUntil=this.shadowRoot.querySelector("button.quick-gate-until"); if(quickUntil)quickUntil.addEventListener("click",async()=>{
      const suggestedDate=new Date(Date.now()+3600000);
      const pad=(value)=>String(value).padStart(2,"0");
      const suggested=`${suggestedDate.getFullYear()}-${pad(suggestedDate.getMonth()+1)}-${pad(suggestedDate.getDate())}T${pad(suggestedDate.getHours())}:${pad(suggestedDate.getMinutes())}`;
      const value=await this._promptAction(
        this._tr("panel.disable_execution_until"),
        this._tr("panel.until_the_selected_time_the_scheduler_will_keep_showing_jobs_but_will_no"),
        {label:this._tr("panel.date_and_time"),value:suggested,type:"datetime-local",confirmLabel:this._tr("panel.disable_until"),danger:true},
      );
      if(!value)return;
      await settingsWrite({type:"vacuum_schedule/settings/update_policy",policy:{execution_gate:"disabled_until",disabled_until:value}},this._tr("panel.execution_temporarily_disabled"));
    });

    const notificationSettings=this._notificationData?.settings;
    const notificationPolicy=notificationSettings?.policy;
    const notificationGlobal=this._notificationGlobalDraft;
    const markNotificationGlobalDirty=()=>{this._notificationGlobalDirty=true;};
    const notificationSave=async()=>{
      if(!entryId||!notificationSettings||!notificationGlobal)return;
      const payload=this._cloneData(notificationSettings)||{};
      payload.enabled=notificationGlobal.enabled!==false;
      payload.dry_run_delivery=notificationGlobal.dry_run_delivery||"send";
      payload.preset=notificationGlobal.preset||"balanced";
      payload.policy=this._cloneData(notificationGlobal.policy||this._notificationPresetPolicy(payload.preset));
      try{
        const result=await this._callWs({type:"vacuum_schedule/notifications/save",entry_id:entryId,settings:payload});
        this._notificationData=result;this._notificationGlobalDirty=false;this._syncNotificationGlobalDraft(true);
        await this._loadScheduler();this._notify(this._tr("panel.notification_settings_saved"));this._render();
      } catch(err){this._notify(this._errorText(err),"error",4200);}
    };
    const notificationEnabled=this.shadowRoot.querySelector("#notification-enabled"); if(notificationEnabled&&notificationGlobal)notificationEnabled.addEventListener("change",()=>{notificationGlobal.enabled=notificationEnabled.checked;markNotificationGlobalDirty();});
    const notificationDryRun=this.shadowRoot.querySelector("#notification-dry-run-delivery"); if(notificationDryRun&&notificationGlobal)notificationDryRun.addEventListener("change",()=>{notificationGlobal.dry_run_delivery=notificationDryRun.value;markNotificationGlobalDirty();});
    const notificationPreset=this.shadowRoot.querySelector("#notification-preset"); if(notificationPreset&&notificationGlobal)notificationPreset.addEventListener("change",()=>{notificationGlobal.preset=notificationPreset.value;if(notificationPreset.value!=="custom")notificationGlobal.policy=this._notificationPresetPolicy(notificationPreset.value);markNotificationGlobalDirty();this._render();});
    this.shadowRoot.querySelectorAll("[data-notification-policy]").forEach(el=>{const update=()=>{if(!notificationGlobal)return;notificationGlobal.preset="custom";notificationGlobal.policy=notificationGlobal.policy||this._notificationPresetPolicy("balanced");const key=el.dataset.notificationPolicy;notificationGlobal.policy[key]=el.type==="checkbox"?el.checked:(el.type==="number"?Number(el.value||0):el.value);markNotificationGlobalDirty();};el.addEventListener("change",update);if(el.tagName==="INPUT"&&el.type!=="checkbox")el.addEventListener("input",update);});
    this.shadowRoot.querySelectorAll("[data-notification-policy-minutes]").forEach(el=>{const update=()=>{if(!notificationGlobal)return;notificationGlobal.preset="custom";notificationGlobal.policy=notificationGlobal.policy||this._notificationPresetPolicy("balanced");notificationGlobal.policy[el.dataset.notificationPolicyMinutes]=Math.max(60,Number(el.value||1)*60);markNotificationGlobalDirty();};el.addEventListener("change",update);el.addEventListener("input",update);});
    const nSave=this.shadowRoot.querySelector("button.notification-save"); if(nSave)nSave.addEventListener("click",notificationSave);
    const newId=()=>{try{return crypto.randomUUID().replaceAll("-","");}catch(_){return `${Date.now()}${Math.random().toString(16).slice(2)}`;}};
    const addRecipient=this.shadowRoot.querySelector("button.notification-add-recipient");
    if(addRecipient&&notificationSettings)addRecipient.addEventListener("click",()=>{
      this._notificationRecipientDraft={recipient_id:newId(),name:"",presence_entity_id:null,language:"default",enabled:true,channels:[]};
      this._notificationRecipientIsNew=true;
      this._notificationRecipientDirty=true;
      this._render();
    });
    this.shadowRoot.querySelectorAll("button.notification-edit-recipient").forEach(btn=>btn.addEventListener("click",()=>{
      const recipient=(notificationSettings?.recipients||[]).find(r=>r.recipient_id===btn.dataset.recipientId);
      if(!recipient)return;
      this._notificationRecipientDraft=JSON.parse(JSON.stringify(recipient));
      delete this._notificationRecipientDraft.presence_state;
      if(!this._notificationRecipientDraft.language||this._notificationRecipientDraft.language==="auto")this._notificationRecipientDraft.language="default";
      this._notificationRecipientIsNew=false;
      this._notificationRecipientDirty=false;
      this._render();
    }));
    this.shadowRoot.querySelectorAll("button.notification-delete-recipient").forEach(btn=>btn.addEventListener("click",async()=>{
      if(!notificationSettings||!entryId)return;
      const recipient=(notificationSettings.recipients||[]).find(r=>r.recipient_id===btn.dataset.recipientId);
      if(!recipient)return;
      const confirmed=await this._confirmAction(this._tr("panel.delete_recipient"),this._tr("panel.recipient_value_and_all_of_its_channels_will_be_deleted", {p1: recipient.name}),this._tr("panel.delete"),true);
      if(!confirmed)return;
      notificationSettings.recipients=(notificationSettings.recipients||[]).filter(r=>r.recipient_id!==recipient.recipient_id);
      try{const result=await this._callWs({type:"vacuum_schedule/notifications/save",entry_id:entryId,settings:notificationSettings});this._notificationData=result;this._syncNotificationGlobalDraft();await this._loadScheduler();this._render();}
      catch(err){this._notify(this._errorText(err),"error",4200);}
    }));
    this.shadowRoot.querySelectorAll("button.notification-recipient-cancel").forEach(btn=>btn.addEventListener("click",()=>{
      this._notificationRecipientDraft=null;
      this._notificationRecipientIsNew=false;
      this._notificationRecipientDirty=false;
      this._error=null;
      this._render();
    }));
    if(this._notificationRecipientDraft){
      const recipient=this._notificationRecipientDraft;
      const markDirty=()=>{this._notificationRecipientDirty=true;this.shadowRoot.querySelectorAll("button.notification-test-channel").forEach(btn=>btn.disabled=true);};
      this.shadowRoot.querySelectorAll("[data-notification-recipient-field]").forEach(el=>{const update=()=>{const key=el.dataset.notificationRecipientField;recipient[key]=el.type==="checkbox"?el.checked:(el.value||null);markDirty();};el.addEventListener("input",update);el.addEventListener("change",update);});
      const add=this.shadowRoot.querySelector("button.notification-add-channel");if(add)add.addEventListener("click",()=>{recipient.channels=recipient.channels||[];recipient.channels.push({channel_id:newId(),name:"",transport_type:"generic_notify",target:"",target_kind:"service",enabled:true,presence_policy:"always",event_filter:"all",custom_events:[],actionable:false,options:{}});markDirty();this._render();});
      this.shadowRoot.querySelectorAll(".channel-card").forEach(card=>{
        const ci=Number(card.dataset.channelIndex),channel=recipient.channels?.[ci];if(!channel)return;
        card.querySelectorAll("[data-notification-channel-field]").forEach(el=>{const update=()=>{const key=el.dataset.notificationChannelField;if(key==="target_combo"){const [kind,target,type]=String(el.value||"").split("|");const candidate=(this._notificationData?.candidates?.channels||[]).find(x=>String(x.target_kind)===String(kind)&&String(x.target)===String(target));channel.target_kind=kind||"service";channel.target=target||"";channel.transport_type=type||candidate?.transport_type||"generic_notify";channel.name=candidate?.name||"";channel.actionable=!!candidate?.supports_actionable;markDirty();this._render();return;}channel[key]=el.type==="checkbox"?el.checked:el.value;markDirty();if(key==="event_filter")this._render();};el.addEventListener("change",update);if(el.tagName==="INPUT"&&el.type!=="checkbox")el.addEventListener("input",update);});
        card.querySelectorAll("[data-notification-custom-event]").forEach(el=>el.addEventListener("change",()=>{const set=new Set(channel.custom_events||[]);if(el.checked)set.add(el.dataset.notificationCustomEvent);else set.delete(el.dataset.notificationCustomEvent);channel.custom_events=[...set];markDirty();}));
        card.querySelectorAll("[data-pushover-option]").forEach(el=>{const update=()=>{channel.options=channel.options||{};const key=el.dataset.pushoverOption;const raw=String(el.value||"").trim();if(!raw){delete channel.options[key];markDirty();return;}if(key==="targets")channel.options[key]=raw.split(",").map(x=>x.trim()).filter(Boolean);else if(["priority","ttl","retry","expire"].includes(key))channel.options[key]=Number(raw);else channel.options[key]=raw;markDirty();};el.addEventListener("input",update);el.addEventListener("change",update);});
        const del=card.querySelector("button.notification-delete-channel");if(del)del.addEventListener("click",async()=>{const confirmed=await this._confirmAction(this._tr("panel.detach_channel"),this._tr("panel.channel_value_will_be_detached_from_the_recipient_the_ha_notify_channel_", {p1: channel.name||channel.target}),this._tr("panel.detach"),true);if(confirmed){recipient.channels.splice(ci,1);markDirty();this._render();}});
        const test=card.querySelector("button.notification-test-channel");if(test&&!test.disabled)test.addEventListener("click",async()=>{try{const result=await this._callWs({type:"vacuum_schedule/notifications/test_channel",entry_id:entryId,recipient_id:recipient.recipient_id,channel_id:channel.channel_id});this._notify(result.sent?this._tr("panel.test_notification_sent"):(result.error||this._tr("panel.send_failed")),result.sent?"success":"error",4200);}catch(err){this._notify(this._errorText(err),"error",4200);}});
      });
      const recipientSave=this.shadowRoot.querySelector("button.notification-recipient-save");if(recipientSave)recipientSave.addEventListener("click",async()=>{
        if(!String(recipient.name||"").trim()){this._error=this._tr("panel.enter_a_recipient_name");this._render();return;}
        const channels=recipient.channels||[];
        const invalid=channels.find(c=>!String(c.target||"").trim());
        if(invalid){this._error=this._tr("panel.select_a_ha_notify_channel_for_every_binding");this._render();return;}
        const bindingKeys=channels.map(c=>`${c.target_kind||"service"}|${c.target||""}`);
        if(new Set(bindingKeys).size!==bindingKeys.length){this._error=this._tr("panel.the_same_ha_notify_channel_cannot_be_attached_twice_to_one_recipient");this._render();return;}
        notificationSettings.recipients=notificationSettings.recipients||[];
        const payload=JSON.parse(JSON.stringify(recipient));
        delete payload.presence_state;
        const index=notificationSettings.recipients.findIndex(r=>r.recipient_id===recipient.recipient_id);
        if(index>=0)notificationSettings.recipients[index]=payload;else notificationSettings.recipients.push(payload);
        try{
          const result=await this._callWs({type:"vacuum_schedule/notifications/save",entry_id:entryId,settings:notificationSettings});
          this._notificationData=result;this._syncNotificationGlobalDraft();
          this._notificationRecipientDraft=null;this._notificationRecipientIsNew=false;this._notificationRecipientDirty=false;this._error=null;
          await this._loadScheduler();this._render();this._notify(this._tr("panel.recipient_saved"));
        }catch(err){this._notify(this._errorText(err),"error",4200);}
      });
    }
    const clearNotificationHistory=this.shadowRoot.querySelector("button.notification-clear-history");if(clearNotificationHistory)clearNotificationHistory.addEventListener("click",async()=>{if(!entryId)return;const confirmed=await this._confirmAction(this._tr("panel.clear_notification_log"),this._tr("panel.notification_delivery_history_will_be_deleted_recipient_and_channel_sett"),this._tr("panel.clear"),true);if(!confirmed)return;await this._callWs({type:"vacuum_schedule/notifications/clear_history",entry_id:entryId});this._notificationMessageSelectedId=null;this._notificationMessageDetail=null;this._notificationHistorySelectedId=null;await this._loadNotifications(false);this._render();this._notify(this._tr("panel.log_cleared"));});

    this.shadowRoot.querySelectorAll(".notification-message-row").forEach(row=>{const open=async()=>{const id=row.dataset.eventId||null;if(!id)return;this._notificationMessageSelectedId=id;this._notificationHistorySelectedId=null;const q=new URLSearchParams({config_entry:entryId,view:"notifications",message:id});history.replaceState(null,"",`/vacuum-schedule?${q.toString()}`);await this._loadNotifications(false);this._render();};row.addEventListener("click",()=>void open());row.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();void open();}});});
    const messageBack=this.shadowRoot.querySelector("button.notification-message-back");if(messageBack)messageBack.addEventListener("click",()=>{this._notificationMessageSelectedId=null;this._notificationMessageDetail=null;this._notificationHistorySelectedId=null;const q=new URLSearchParams({config_entry:entryId,view:"notifications"});history.replaceState(null,"",`/vacuum-schedule?${q.toString()}`);this._render();});
    this.shadowRoot.querySelectorAll("button.notification-open-job,button.delivery-open-job").forEach(btn=>btn.addEventListener("click",()=>{const jobId=btn.dataset.job||"";if(!jobId)return;this._deepLinkJobId=jobId;this._view="status";const q=new URLSearchParams({config_entry:entryId,view:"status",job:jobId});history.replaceState(null,"",`/vacuum-schedule?${q.toString()}`);this._render();}));
    this.shadowRoot.querySelectorAll("button.delivery-copy").forEach(btn=>btn.addEventListener("click",async()=>{const text=btn.dataset.copy||"";if(!text)return;try{await navigator.clipboard.writeText(text);this._notify(this._tr("panel.copied"));}catch(_){this._notify(this._tr("panel.copy_failed"),"error",3200);}}));

    this.shadowRoot.querySelectorAll("button.notification-test-event").forEach(btn=>btn.addEventListener("click",async()=>{if(!entryId)return;try{const result=await this._callWs({type:"vacuum_schedule/notifications/test_event",entry_id:entryId,event_type:btn.dataset.event,notification_class:btn.dataset.event==="finished"?"error":"attention"});this._notificationRoutingTestResult=result;await this._loadNotifications(false);this._render();const sent=Number(result.sent||0),suppressed=Number(result.suppressed||0),failed=Number(result.failed||0);const message=this._tr("panel.sent_value_suppressed_value_failed_value", {p1: sent, p2: suppressed, p3: failed});this._notify(message,failed||!sent?"error":"success",5200);}catch(err){this._notify(this._errorText(err),"error",4200);}}));

    const markPolicyDirty=()=>{this._policyDirty=true;};
    const policyGate=this.shadowRoot.querySelector("#policy-gate");
    if (policyGate) policyGate.addEventListener("change",()=>{ const u=this.shadowRoot.querySelector("#policy-disabled-until"); if(u)u.disabled=policyGate.value!=="disabled_until"; markPolicyDirty(); });
    ["#policy-disabled-until","#policy-battery","#policy-window","#policy-occupancy-delay","#policy-access-delay"].forEach(selector=>{const el=this.shadowRoot.querySelector(selector);if(el){el.addEventListener("input",markPolicyDirty);el.addEventListener("change",markPolicyDirty);}});
    this.shadowRoot.querySelectorAll(".forecast-policy-section input").forEach(el=>{el.addEventListener("input",markPolicyDirty);el.addEventListener("change",markPolicyDirty);});
    const savePolicy=async()=>{
      const gate=this.shadowRoot.querySelector("#policy-gate")?.value||"enabled";
      const currentGate=this._settingsData?.policy?.execution_gate||"enabled";
      if(gate!==currentGate){
        const enabling=gate==="enabled";
        const confirmed=await this._confirmAction(
          enabling?this._tr("panel.enable_schedule_execution"):gate==="disabled_until"?this._tr("panel.temporarily_disable_execution"):this._tr("panel.disable_schedule_execution"),
          enabling?this._tr("panel.after_enabling_the_scheduler_may_again_start_ready_jobs_automatically_wi"):gate==="disabled_until"?this._tr("panel.new_cleanings_will_not_start_until_the_selected_time_the_scheduler_and_j"):this._tr("panel.new_cleanings_will_not_start_until_schedule_execution_is_enabled_again"),
          enabling?this._tr("panel.enable"):gate==="disabled_until"?this._tr("panel.disable_until"):this._tr("panel.disable"),
          !enabling,
        );
        if(!confirmed)return;
      }
      const forecastPolicy={};
      for(const key of ["time","battery","clean_water","dirty_water"]){
        forecastPolicy[key]={
          enabled:!!this.shadowRoot.querySelector(`#forecast-${key}-enabled`)?.checked,
          percentile:Math.max(50,Math.min(99,Number(this.shadowRoot.querySelector(`#forecast-${key}-percentile`)?.value||90))),
          delta_percent:Math.max(0,Math.min(500,Number(this.shadowRoot.querySelector(`#forecast-${key}-delta`)?.value||0))),
          lookback_days:Math.max(1,Math.min(3650,Number(this.shadowRoot.querySelector(`#forecast-${key}-days`)?.value||90))),
        };
      }
      this._policyDraft={...(this._policyDraft||this._cloneData(this._settingsData?.policy||{})),execution_gate:gate,disabled_until:gate==="disabled_until"?(this.shadowRoot.querySelector("#policy-disabled-until")?.value||null):null,default_min_battery_percent:Math.max(0,Math.min(100,Number(this.shadowRoot.querySelector("#policy-battery")?.value||20))),minimum_start_window_minutes:Math.max(0,Number(this.shadowRoot.querySelector("#policy-window")?.value||0)),occupancy_clear_delay_seconds:Math.max(0,Number(this.shadowRoot.querySelector("#policy-occupancy-delay")?.value||0)),access_stable_delay_seconds:Math.max(0,Number(this.shadowRoot.querySelector("#policy-access-delay")?.value||0)),forecasts:forecastPolicy};
      const result=await settingsWrite({type:"vacuum_schedule/settings/update_policy",policy:this._cloneData(this._policyDraft)}, this._tr("panel.global_settings_saved"));
      if(result){this._policyDirty=false;this._syncPolicyDraft(true);this._render();}
    };
    this.shadowRoot.querySelectorAll("button.policy-save").forEach(btn=>btn.addEventListener("click",savePolicy));
    const bindingPayload=(key,body)=>{
      const val=(name)=>body.querySelector(`[data-binding-field="${name}"]`)?.value??"";
      let value_mapping={}; const mappingRaw=String(val("value_mapping")||"").trim();
      if(mappingRaw){
        try{value_mapping=JSON.parse(mappingRaw);if(!value_mapping||Array.isArray(value_mapping)||typeof value_mapping!=="object")throw new Error("mapping_not_object");}
        catch(_){throw new Error(this._tr("panel.vendor_value_mapping_must_be_a_json_object"));}
      }
      return {binding_mode:val("binding_mode")||"auto",entity_id:val("entity_id")||null,attribute:val("attribute")||null,normal_state:val("normal_state")||null,value_mapping,thresholds:{},semantic:null,unit:null};
    };
    this.shadowRoot.querySelectorAll("button.settings-edit-binding").forEach((btn)=>btn.addEventListener("click",()=>{this._settingsEditor=btn.dataset.key;this._error=null;this._render();}));
    this.shadowRoot.querySelectorAll("button.settings-editor-cancel").forEach((btn)=>btn.addEventListener("click",()=>{this._settingsEditor=null;this._error=null;this._render();}));
    const singleRefresh=this.shadowRoot.querySelector("button.binding-single-refresh");
    if(singleRefresh) singleRefresh.addEventListener("click",async()=>{
      if(!entryId || this._loading) return;
      this._loading=true;this._error=null;this._syncLoadingIndicator();
      try{await this._loadSettings(false);}
      catch(err){this._error=this._errorText(err);}
      finally{this._loading=false;this._render();}
    });
    const singleSave=this.shadowRoot.querySelector("button.binding-single-save");
    if(singleSave) singleSave.addEventListener("click",async()=>{
      if(!entryId || this._loading) return;
      const body=this.shadowRoot.querySelector("[data-binding-key]");
      const key=singleSave.dataset.key;
      if(!body || !key) return;
      let binding;
      try{binding=bindingPayload(key,body);}
      catch(err){this._error=this._errorText(err);this._render();return;}
      this._loading=true;this._error=null;this._syncLoadingIndicator();
      try{
        await this._callWs({type:"vacuum_schedule/settings/update_binding",entry_id:entryId,key,binding});
        await Promise.all([this._loadScheduler(),this._loadSettings(false)]);
        this._settingsEditor=null;
        this._notify(this._tr("panel.saved"));
      }catch(err){this._error=this._errorText(err);this._notify(this._error,"error",4200);}
      finally{this._loading=false;this._render();}
    });
    const rescan=this.shadowRoot.querySelector("button.settings-rescan"); if(rescan)rescan.addEventListener("click",()=>settingsWrite({type:"vacuum_schedule/settings/rescan"}, this._tr("panel.rediscovery_completed")));
    const validate=this.shadowRoot.querySelector("button.settings-validate"); if(validate)validate.addEventListener("click",()=>settingsWrite({type:"vacuum_schedule/settings/validate"}, this._tr("panel.configuration_validation_completed")));

    const runDataAction=async(payload,success)=>{
      if(!entryId||this._loading)return;
      this._loading=true;this._error=null;this._syncLoadingIndicator();
      try{
        const result=await this._callWs({...payload,entry_id:entryId});
        await Promise.all([this._loadSettings(false),this._loadStatistics(false),this._loadMaintenance(false)]);
        this._notify(success);
        return result;
      }catch(err){this._error=this._errorText(err);this._notify(this._error,"error",5200);}
      finally{this._loading=false;this._render();}
      return null;
    };
    this.shadowRoot.querySelector("button.data-backup-create")?.addEventListener("click",()=>void runDataAction({type:"vacuum_schedule/data/backup"},this._tr("panel.backup_created")));
    this.shadowRoot.querySelector("button.data-clear-open")?.addEventListener("click",async()=>{
      const layers=await this._dataLayerSelectionModal({title:this._tr("panel.clear_data"),message:this._tr("panel.clear_data_warning"),defaults:["execution_history","statistics"],confirmLabel:this._tr("panel.clear"),danger:true});
      if(!layers)return;
      await runDataAction({type:"vacuum_schedule/data/clear",layers},this._tr("panel.data_cleared_backup_created"));
    });
    this.shadowRoot.querySelectorAll("button.data-backup-restore").forEach(btn=>btn.addEventListener("click",async()=>{
      const available=String(btn.dataset.backupLayers||"").split(",").filter(Boolean);
      const layers=await this._dataLayerSelectionModal({title:this._tr("panel.restore_backup"),message:this._tr("panel.restore_backup_warning"),available,defaults:available,confirmLabel:this._tr("panel.restore"),danger:false});
      if(!layers)return;
      await runDataAction({type:"vacuum_schedule/data/restore",backup_id:btn.dataset.backupId,layers},this._tr("panel.backup_restored"));
    }));
    this.shadowRoot.querySelectorAll("button.data-backup-delete").forEach(btn=>btn.addEventListener("click",async()=>{
      const confirmed=await this._confirmAction(this._tr("panel.delete_backup"),this._tr("panel.delete_backup_warning"),this._tr("panel.delete"),true);
      if(!confirmed)return;
      await runDataAction({type:"vacuum_schedule/data/delete_backup",backup_id:btn.dataset.backupId},this._tr("panel.backup_deleted"));
    }));

    const newRoom=()=>({zone_id:"",name:"",robot_target_type:"segment",robot_target_id:"",control:"enabled",disabled_until:null,busy_sources:[],access_paths:[],occupancy_clear_delay_seconds:null,access_stable_delay_seconds:null,nominal_area_m2:null});
    const roomNew=this.shadowRoot.querySelector("button.room-new"); if(roomNew)roomNew.addEventListener("click",()=>{this._roomDraft=newRoom();this._roomTestResult=null;this._render();});
    this.shadowRoot.querySelectorAll("button.room-edit").forEach(btn=>btn.addEventListener("click",()=>{const r=(this._settingsData?.cleaning_zones||[]).find(x=>x.zone_id===btn.dataset.roomId);if(r){this._roomDraft=JSON.parse(JSON.stringify(r));this._roomTestResult=null;this._render();}}));
    this.shadowRoot.querySelectorAll("button.room-delete").forEach(btn=>btn.addEventListener("click",async()=>{const confirmed=await this._confirmAction(this._tr("panel.delete_cleaning_zone"),this._tr("panel.cleaning_zone_value_will_be_deleted_schedules_that_reference_it_may_requ", {p1: btn.dataset.roomName}),this._tr("panel.delete"),true);if(!confirmed)return;await settingsWrite({type:"vacuum_schedule/settings/delete_zone",zone_id:btn.dataset.roomId}, this._tr("panel.zone_deleted"));}));
    this.shadowRoot.querySelectorAll("button.room-cancel").forEach(btn=>btn.addEventListener("click",()=>{this._roomDraft=null;this._roomTestResult=null;this._render();}));
    if(this._roomDraft){
      const roomName=this.shadowRoot.querySelector("#room-name"); if(roomName)roomName.addEventListener("input",()=>this._roomDraft.name=roomName.value); const nominalArea=this.shadowRoot.querySelector("#zone-nominal-area"); if(nominalArea)nominalArea.addEventListener("input",()=>{const v=Number(nominalArea.value);this._roomDraft.nominal_area_m2=nominalArea.value&&v>0?v:null;});
      const targetType=this.shadowRoot.querySelector("#room-target-type"); if(targetType)targetType.addEventListener("change",()=>{this._roomDraft.robot_target_type=targetType.value;this._roomDraft.robot_target_id="";this._render();});
      const targetSingle=this.shadowRoot.querySelector("#room-target-single"); if(targetSingle)targetSingle.addEventListener("change",()=>this._roomDraft.robot_target_id=targetSingle.value);
      const zoneTarget=this.shadowRoot.querySelector("#room-zone-target"); if(zoneTarget)zoneTarget.addEventListener("input",()=>this._roomDraft.robot_target_id=zoneTarget.value.trim());
      const control=this.shadowRoot.querySelector("#zone-control"); if(control)control.addEventListener("change",()=>{this._roomDraft.control=control.value;if(control.value!=="disabled_until")this._roomDraft.disabled_until=null;this._render();});
      const disabledUntil=this.shadowRoot.querySelector("#zone-disabled-until"); if(disabledUntil)disabledUntil.addEventListener("change",()=>this._roomDraft.disabled_until=disabledUntil.value||null);
      const occupancyDelay=this.shadowRoot.querySelector("#zone-occupancy-delay"); if(occupancyDelay)occupancyDelay.addEventListener("input",()=>this._roomDraft.occupancy_clear_delay_seconds=occupancyDelay.value===""?null:Math.max(0,Number(occupancyDelay.value)));
      const accessDelay=this.shadowRoot.querySelector("#zone-access-delay"); if(accessDelay)accessDelay.addEventListener("input",()=>this._roomDraft.access_stable_delay_seconds=accessDelay.value===""?null:Math.max(0,Number(accessDelay.value)));
      this.shadowRoot.querySelectorAll("button.add-source").forEach(btn=>btn.addEventListener("click",()=>{const k=btn.dataset.roomSourceKind;this._roomDraft[k]=this._roomDraft[k]||[];this._roomDraft[k].push({entity_id:"",attribute:null,operator:"equals",value:"on"});this._render();}));
      this.shadowRoot.querySelectorAll("button.remove-source").forEach(btn=>btn.addEventListener("click",()=>{this._roomDraft[btn.dataset.roomSourceKind].splice(Number(btn.dataset.index),1);this._render();}));
      this.shadowRoot.querySelectorAll("[data-source-field]").forEach(el=>{const update=()=>{const row=el.closest(".condition-row");if(!row)return;const obj=this._roomDraft[row.dataset.roomSourceKind][Number(row.dataset.index)];obj[el.dataset.sourceField]=el.dataset.sourceField==="value"?parseValue(el.value):(el.value||null);};el.addEventListener("change",update);el.addEventListener("input",update);});
      const addPath=this.shadowRoot.querySelector("button.add-path");if(addPath)addPath.addEventListener("click",()=>{this._roomDraft.access_paths=this._roomDraft.access_paths||[];this._roomDraft.access_paths.push({path_id:"",name:`${this._tr("panel.path")} ${this._roomDraft.access_paths.length+1}`,conditions:[]});this._render();});
      this.shadowRoot.querySelectorAll("button.remove-path").forEach(btn=>btn.addEventListener("click",()=>{this._roomDraft.access_paths.splice(Number(btn.dataset.pathIndex),1);this._render();}));
      this.shadowRoot.querySelectorAll("[data-path-name]").forEach(el=>el.addEventListener("input",()=>this._roomDraft.access_paths[Number(el.dataset.pathName)].name=el.value));
      this.shadowRoot.querySelectorAll("button.add-condition").forEach(btn=>btn.addEventListener("click",()=>{this._roomDraft.access_paths[Number(btn.dataset.pathIndex)].conditions.push({entity_id:"",attribute:null,operator:"equals",value:"on"});this._render();}));
      this.shadowRoot.querySelectorAll("button.remove-condition").forEach(btn=>btn.addEventListener("click",()=>{this._roomDraft.access_paths[Number(btn.dataset.pathIndex)].conditions.splice(Number(btn.dataset.conditionIndex),1);this._render();}));
      this.shadowRoot.querySelectorAll("[data-condition-field]").forEach(el=>{const update=()=>{const row=el.closest(".condition-row");if(!row)return;const obj=this._roomDraft.access_paths[Number(row.dataset.pathIndex)].conditions[Number(row.dataset.conditionIndex)];obj[el.dataset.conditionField]=el.dataset.conditionField==="value"?parseValue(el.value):(el.value||null);};el.addEventListener("change",update);el.addEventListener("input",update);});
      const roomSave=this.shadowRoot.querySelector("button.room-save");if(roomSave)roomSave.addEventListener("click",async()=>{
        if(!String(this._roomDraft.name||"").trim()){this._error=this._tr("panel.enter_a_cleaning_zone_name");this._render();return;}
        if(!String(this._roomDraft.robot_target_id||"").trim()){this._error=this._tr("panel.select_one_physical_robot_target");this._render();return;}
        if(this._roomDraft.control==="disabled_until" && !this._roomDraft.disabled_until){this._error=this._tr("panel.set_the_disable_until_date_and_time");this._render();return;}
        const result=await settingsWrite({type:"vacuum_schedule/settings/update_zone",zone:this._roomDraft}, this._tr("panel.zone_saved"));if(result){this._roomDraft=null;this._roomTestResult=null;await this._loadSettings(false);await this._loadSchedules(false);this._render();}
      });
      const roomTest=this.shadowRoot.querySelector("button.room-test");if(roomTest)roomTest.addEventListener("click",async()=>{try{this._roomTestResult=await this._callWs({type:"vacuum_schedule/preflight/test",entry_id:entryId,zone_id:this._roomDraft.zone_id});this._render();this._notify(this._tr("panel.check_completed"));}catch(err){this._error=this._errorText(err);this._render();}});
    }


    this.shadowRoot.querySelectorAll("button.override-apply").forEach(btn=>btn.addEventListener("click",async()=>{const row=btn.closest("tr[data-override-target]");if(!row)return;const mode=row.querySelector('[data-override-field="mode"]')?.value||"live";const value=parseValue(row.querySelector('[data-override-field="value"]')?.value||"");const persistent=!!row.querySelector('[data-override-field="persistent"]')?.checked;await settingsWrite({type:"vacuum_schedule/testing/overrides/set",target:row.dataset.overrideTarget,mode,value,persistent}, this._tr("panel.applied"));}));
    this.shadowRoot.querySelectorAll("button.override-clear").forEach(btn=>btn.addEventListener("click",async()=>{const row=btn.closest("tr[data-override-target]");if(row)await settingsWrite({type:"vacuum_schedule/testing/overrides/clear",target:row.dataset.overrideTarget}, this._tr("panel.override_cleared"));}));
    const clearAll=this.shadowRoot.querySelector("button.override-clear-all");if(clearAll)clearAll.addEventListener("click",()=>settingsWrite({type:"vacuum_schedule/testing/overrides/clear"}, this._tr("panel.all_overrides_cleared")));
    this.shadowRoot.querySelectorAll("button.override-preset").forEach(btn=>btn.addEventListener("click",()=>settingsWrite({type:"vacuum_schedule/testing/preset",preset:btn.dataset.preset}, this._tr("panel.preset_applied"))));

    if (this._editor && this._form) {
      const scheduleNotificationMode=this.shadowRoot.querySelector("#schedule-notification-mode");
      if(scheduleNotificationMode)scheduleNotificationMode.addEventListener("change",()=>{this._form.notification_policy=this._form.notification_policy||{};this._form.notification_policy.mode=scheduleNotificationMode.value;if(scheduleNotificationMode.value==="custom")this._form.notification_policy.overrides=this._form.notification_policy.overrides||{};else delete this._form.notification_policy.overrides;this._render();});
      const allRecipients=this.shadowRoot.querySelector("#schedule-notification-all-recipients");if(allRecipients)allRecipients.addEventListener("change",()=>{this._form.notification_policy=this._form.notification_policy||{};if(allRecipients.checked)delete this._form.notification_policy.recipient_ids;else this._form.notification_policy.recipient_ids=[];this._render();});
      this.shadowRoot.querySelectorAll("[data-schedule-recipient]").forEach(el=>el.addEventListener("change",()=>{this._form.notification_policy=this._form.notification_policy||{};const set=new Set(this._form.notification_policy.recipient_ids||[]);if(el.checked)set.add(el.dataset.scheduleRecipient);else set.delete(el.dataset.scheduleRecipient);this._form.notification_policy.recipient_ids=[...set];}));
      const overrides=()=>{this._form.notification_policy=this._form.notification_policy||{mode:"custom"};this._form.notification_policy.overrides=this._form.notification_policy.overrides||{};return this._form.notification_policy.overrides;};
      this.shadowRoot.querySelectorAll("[data-schedule-notification]").forEach(el=>el.addEventListener("change",()=>{const o=overrides(),key=el.dataset.scheduleNotification;if(el.value==="")delete o[key];else o[key]=el.value;}));
      this.shadowRoot.querySelectorAll("[data-schedule-notification-number]").forEach(el=>el.addEventListener("change",()=>{const o=overrides(),key=el.dataset.scheduleNotificationNumber;if(el.value==="")delete o[key];else o[key]=Math.max(0,Number(el.value));}));
      this.shadowRoot.querySelectorAll("[data-schedule-notification-minutes]").forEach(el=>el.addEventListener("change",()=>{const o=overrides(),key=el.dataset.scheduleNotificationMinutes;if(el.value==="")delete o[key];else o[key]=Math.max(60,Number(el.value)*60);}));
      this.shadowRoot.querySelectorAll("[data-schedule-notification-bool]").forEach(el=>el.addEventListener("change",()=>{const o=overrides(),key=el.dataset.scheduleNotificationBool;if(el.value==="")delete o[key];else o[key]=el.value==="true";}));
    }

    this._focusDeepLinkTarget();

    if (!this._editor || !this._form) return;

    this.shadowRoot.querySelectorAll("[data-field]").forEach((el) => {
      el.addEventListener("change", () => {
        const key = el.dataset.field;
        if (el.type === "checkbox") this._form[key] = el.checked;
        else if ((key === "minimum_battery_percent" || key === "minimum_start_window_minutes") && el.value === "") this._form[key] = "";
        else if (el.type === "number" || key === "passes") this._form[key] = Number(el.value);
        else this._form[key] = el.value;
        
      });
      if (el.tagName === "INPUT" && el.type === "text") {
        el.addEventListener("input", () => { this._form[el.dataset.field] = el.value; });
      }
    });

    this.shadowRoot.querySelectorAll("[data-weekday-enabled]").forEach((el) => el.addEventListener("change", () => {
      const code=el.dataset.weekdayEnabled;
      this._form.weekday_times=this._form.weekday_times||{};
      this._form.weekday_overrides=this._form.weekday_overrides||{};
      const draft=this._weekdayDraftTimes();
      const timeInput=this.shadowRoot.querySelector(`[data-weekday-time="${code}"]`);
      if(el.checked){
        this._form.weekday_times[code]=String(timeInput?.value||draft[code]||"09:00").slice(0,5);
        draft[code]=this._form.weekday_times[code];
      }else{
        if(timeInput?.value) draft[code]=String(timeInput.value).slice(0,5);
        delete this._form.weekday_times[code];
        delete this._form.weekday_overrides[code];
      }
      const order=["mon","tue","wed","thu","fri","sat","sun"];
      this._form.weekdays=order.filter((day)=>Object.prototype.hasOwnProperty.call(this._form.weekday_times,day));
      this._render();
    }));
    this.shadowRoot.querySelectorAll("[data-weekday-time]").forEach((el)=>el.addEventListener("change",()=>{
      const code=el.dataset.weekdayTime;
      this._form.weekday_times=this._form.weekday_times||{};
      if(Object.prototype.hasOwnProperty.call(this._form.weekday_times,code)){
        const value=String(el.value||"09:00").slice(0,5);
        this._form.weekday_times[code]=value;
        this._weekdayDraftTimes()[code]=value;
        this._render();
      }
    }));
    const weekdayBulkApply=this.shadowRoot.querySelector("button.weekday-bulk-apply");
    if(weekdayBulkApply)weekdayBulkApply.addEventListener("click",()=>{
      const timeInput=this.shadowRoot.querySelector("[data-weekday-bulk-time]");
      const value=String(timeInput?.value||"").slice(0,5);
      if(!value)return;
      this._applyWeekdayBulkTime(value);
      this._render();
    });
    this.shadowRoot.querySelectorAll("[data-day-override]").forEach((el)=>el.addEventListener("change",()=>{
      const code=el.dataset.dayOverride, key=el.dataset.dayOverrideField;
      this._form.weekday_overrides=this._form.weekday_overrides||{};
      const row={...(this._form.weekday_overrides[code]||{})};
      const value=el.value;
      if(value==="") delete row[key]; else row[key]=key==="passes"?Number(value):value;
      if(Object.keys(row).length) this._form.weekday_overrides[code]=row; else delete this._form.weekday_overrides[code];
    }));
    this.shadowRoot.querySelectorAll("[data-duration-minutes]").forEach((el)=>el.addEventListener("change",()=>{
      const key=el.dataset.durationMinutes;
      const minimum=key==="execution_window_minutes"?1:0;
      const optional=el.dataset.durationOptional==="1";
      if(optional && !String(el.value||"").trim()){ this._form[key]=""; el.setCustomValidity(""); return; }
      const parsed=this._parseDurationHm(el.value,{min:minimum,max:10080});
      if(parsed===null){
        el.setCustomValidity(this._tr("panel.invalid_hours_minutes"));
        el.reportValidity();
        return;
      }
      el.setCustomValidity("");
      this._form[key]=parsed;
      el.value=this._formatHoursMinutes(parsed);
    }));
    const forceGroups=()=>{this._form.force_condition_groups=this._form.force_condition_groups||[];return this._form.force_condition_groups;};
    const forceGroupNames=()=>{this._form.force_condition_group_names=this._form.force_condition_group_names||[];return this._form.force_condition_group_names;};
    this.shadowRoot.querySelectorAll("[data-force-field]").forEach((el)=>el.addEventListener("change",()=>{
      const key=el.dataset.forceField;
      if(key==="enabled"){
        this._form.force_enabled=!!el.checked;
        if(this._form.force_enabled){
          if(!Number(this._form.force_max_advance_minutes||0))this._form.force_max_advance_minutes=360;
          if(this._form.force_priority===undefined||this._form.force_priority===null||this._form.force_priority==="")this._form.force_priority=100;
          if(!forceGroups().length){this._form.force_condition_groups=[[this._newForceCondition("binary")]];this._form.force_condition_group_names=[""];}
        }
        this._render();
      }else if(key==="priority"){
        this._form.force_priority=Math.max(0,Math.round(Number(el.value||0)));
      }else if(key==="preempts_scheduled"){
        this._form.force_preempts_scheduled=!!el.checked;
      }
    }));
    this.shadowRoot.querySelectorAll("[data-force-group-name]").forEach((el)=>{
      const update=()=>{const gi=Number(el.dataset.forceGroupName);const names=forceGroupNames();while(names.length<forceGroups().length)names.push("");names[gi]=String(el.value||"").trim().slice(0,120);};
      el.addEventListener("input",update);
      el.addEventListener("change",()=>{update();this._render();});
    });
    const forceConditionAt=(gi,ci)=>forceGroups()[gi]?.[ci]||null;
    this.shadowRoot.querySelectorAll("[data-force-condition-field]").forEach((el)=>el.addEventListener("change",()=>{
      const gi=Number(el.dataset.forceGroup),ci=Number(el.dataset.forceCondition);
      const row=forceConditionAt(gi,ci); if(!row)return;
      const key=el.dataset.forceConditionField;
      if(key==="type"){
        const replacement=this._newForceCondition(String(el.value||"binary"));
        replacement.condition_id=row.condition_id||replacement.condition_id;
        replacement.for_minutes=Number(row.for_minutes||0);
        forceGroups()[gi][ci]=replacement;
        this._render(); return;
      }
      if(["for_minutes","minimum_absent"].includes(key))row[key]=Math.max(key==="minimum_absent"?1:0,Math.round(Number(el.value||0)));
      else if(key==="value" && row.type==="numeric")row[key]=Number(el.value||0);
      else row[key]=el.value;
    }));
    this.shadowRoot.querySelectorAll("[data-force-person]").forEach((el)=>el.addEventListener("change",()=>{
      const gi=Number(el.dataset.forceGroup),ci=Number(el.dataset.forceCondition);
      const row=forceConditionAt(gi,ci);if(!row)return;
      const set=new Set((row.entity_ids||[]).map(String));
      if(el.checked)set.add(String(el.dataset.entityId||""));else set.delete(String(el.dataset.entityId||""));
      row.entity_ids=[...set].filter(Boolean);
      row.minimum_absent=Math.min(Math.max(1,Number(row.minimum_absent||1)),Math.max(1,row.entity_ids.length));
      this._render();
    }));
    this.shadowRoot.querySelectorAll("button.force-add-group").forEach((btn)=>btn.addEventListener("click",()=>{forceGroups().push([this._newForceCondition("binary")]);forceGroupNames().push("");this._render();}));
    this.shadowRoot.querySelectorAll("button.force-add-condition").forEach((btn)=>btn.addEventListener("click",()=>{const gi=Number(btn.dataset.forceGroup);if(forceGroups()[gi])forceGroups()[gi].push(this._newForceCondition("binary"));this._render();}));
    this.shadowRoot.querySelectorAll("button.force-remove-condition").forEach((btn)=>btn.addEventListener("click",()=>{const gi=Number(btn.dataset.forceGroup),ci=Number(btn.dataset.forceCondition);const groups=forceGroups();if(!groups[gi])return;groups[gi].splice(ci,1);if(!groups[gi].length){groups.splice(gi,1);forceGroupNames().splice(gi,1);}this._render();}));
    this.shadowRoot.querySelectorAll("button.force-remove-group").forEach((btn)=>btn.addEventListener("click",()=>{const gi=Number(btn.dataset.forceGroup);forceGroups().splice(gi,1);forceGroupNames().splice(gi,1);this._render();}));

    this.shadowRoot.querySelectorAll("[data-target]").forEach((el) => el.addEventListener("change", () => {
      const set = new Set((this._form.targets || []).map(String));
      if (el.checked) set.add(String(el.dataset.target)); else set.delete(String(el.dataset.target));
      this._form.targets = [...set];
      this._render();
    }));
  }
}

const VACUUM_SCHEDULER_PANEL_NAMES = [
  "vacuum-schedule-panel-0133",
  "vacuum-schedule-panel-0132",
  "vacuum-schedule-panel-01260",
  "vacuum-schedule-panel-01259",
  "vacuum-schedule-panel-01258",
  "vacuum-schedule-panel-01257",
  "vacuum-schedule-panel-01256",
  "vacuum-schedule-panel-01255",
  "vacuum-schedule-panel-01254",
  "vacuum-schedule-panel-01245",
  "vacuum-schedule-panel-01244",
  "vacuum-schedule-panel-01243",
  "vacuum-schedule-panel-01242",
  "vacuum-schedule-panel-01239",
  "vacuum-schedule-panel-01238",
  "vacuum-schedule-panel-01236",
  "vacuum-schedule-panel-01235",
  "vacuum-schedule-panel-01233",
  "vacuum-schedule-panel-01232",
  "vacuum-schedule-panel-01231",
  "vacuum-schedule-panel-01230",
  "vacuum-schedule-panel-01229",
  "vacuum-schedule-panel-01228",
  "vacuum-schedule-panel-01227",
  "vacuum-schedule-panel-01226",
  "vacuum-schedule-panel-01225",
  "vacuum-schedule-panel-01224",
  "vacuum-schedule-panel-01223",
  "vacuum-schedule-panel-01222",
  "vacuum-schedule-panel-01221",
  "vacuum-schedule-panel-01220",
  "vacuum-schedule-panel-01219",
  "vacuum-schedule-panel-01217",
  "vacuum-schedule-panel-01216",
  "vacuum-schedule-panel-01215",
  "vacuum-schedule-panel-01214",
  "vacuum-schedule-panel-01213",
  "vacuum-schedule-panel-01212",
  "vacuum-schedule-panel-01211",
  "vacuum-schedule-panel-0129",
  "vacuum-schedule-panel-0128",
  "vacuum-schedule-panel-0127",
  "vacuum-schedule-panel-0126",
  "vacuum-schedule-panel-0125",
  "vacuum-schedule-panel-0124",
  "vacuum-schedule-panel-0122",
  "vacuum-schedule-panel-0121",
  "vacuum-schedule-panel-0120",
  "vacuum-schedule-panel-0119",
  "vacuum-schedule-panel-0116",
  "vacuum-schedule-panel-0115",
  "vacuum-schedule-panel-0114",
  "vacuum-schedule-panel-0113",
  "vacuum-schedule-panel-080",
  "vacuum-schedule-panel-079",
  "vacuum-schedule-panel-077",
  "vacuum-schedule-panel-076",
  "vacuum-schedule-panel-075",
  "vacuum-schedule-panel-074",
  "vacuum-schedule-panel-073",
  "vacuum-schedule-panel-072",
  "vacuum-schedule-panel-071",
  "vacuum-schedule-panel-070",
  "vacuum-schedule-panel-0635"
];

VACUUM_SCHEDULER_PANEL_NAMES.forEach((name, index) => {
  if (customElements.get(name)) return;
  // A CustomElementRegistry may not register one constructor under multiple names.
  // Keep the current identity on the real class and legacy identities on unique subclasses.
  const ElementClass = index === 0 ? VacuumSchedulePanel : class extends VacuumSchedulePanel {};
  customElements.define(name, ElementClass);
});
