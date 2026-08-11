(() => {
  const safeHttpUrl = value => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  };

  function normalizeStoredModel(value) {
    if (typeof value !== "string" || !value || value.length > 1024) return "";
    try {
      const model = JSON.parse(value);
      if (!model || typeof model !== "object" || Array.isArray(model) || Object.keys(model).sort().join(",") !== "modelId,providerId") return "";
      if (typeof model.providerId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(model.providerId)) return "";
      if (typeof model.modelId !== "string" || !model.modelId || model.modelId !== model.modelId.trim() || model.modelId.length > 256 || /[\x00-\x1f\x7f]/.test(model.modelId)) return "";
      return JSON.stringify({ providerId: model.providerId, modelId: model.modelId });
    } catch { return ""; }
  }

  function formatDuration(milliseconds) {
    const seconds = Math.max(0, milliseconds) / 1000;
    return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
  }

  function aiContextFingerprint(parts) {
    let hash = 1469598103934665603n;
    for (const character of JSON.stringify(parts)) {
      hash ^= BigInt(character.codePointAt(0));
      hash = BigInt.asUintN(64, hash * 1099511628211n);
    }
    return hash.toString(16).padStart(16, "0");
  }

  function boundedUtf8Text(value, maximum) {
    const encoder = new TextEncoder();
    let result = "";
    for (const character of String(value ?? "")) {
      if (encoder.encode(result + character).length > maximum) break;
      result += character;
    }
    return result.trim();
  }

  function boundedAiQueryResult(result, { maxRows = 50, maxColumns = 50, maxBytes = 24 * 1024, envelope = {} } = {}) {
    const columns = (result?.columns ?? []).slice(0, maxColumns).map(column => ({ name: String(column?.name ?? column) }));
    const rows = (result?.rows ?? []).slice(0, maxRows).map(row => Array.isArray(row) ? row.slice(0, columns.length) : []);
    const bounded = {
      ...envelope,
      columns,
      rows,
      rowCount: rows.length,
      truncated: Boolean(result?.truncated || (result?.rows?.length ?? 0) > rows.length || (result?.columns?.length ?? 0) > columns.length),
    };
    const encoder = new TextEncoder();
    while (bounded.rows.length && encoder.encode(JSON.stringify(bounded)).length > maxBytes) {
      bounded.rows.pop();
      bounded.rowCount = bounded.rows.length;
      bounded.truncated = true;
    }
    if (encoder.encode(JSON.stringify(bounded)).length > maxBytes) throw new Error("Query result metadata exceeds the AI disclosure limit");
    return bounded;
  }

  function createAiAssistant(options) {
    const {
      sessionClient, root, trigger, settingsDialog, historyDialog, storageKey, getContext,
      buildMessagePayload, createSessionTitle, contextKey = () => null, parseSession = session => ({ title: session.title || "Untitled chat", key: null }),
      renderAction, validateAction, applyAction, toolLabels = {}, skillLabels = {}, labels = {},
      onOpenChange = () => {}, onAccessChange = () => {}, onNewChat = () => {}, state: suppliedState,
      canViewSession = () => true, extraBusyControls = [],
    } = options;
    if (!sessionClient || !root || !trigger || !settingsDialog || !historyDialog || typeof getContext !== "function") throw new TypeError("AI assistant dependencies are required");
    const find = name => root.querySelector(`[data-ai="${name}"]`);
    const elements = {
      model: find("model"), access: find("access"), status: find("status"), railStatus: trigger.querySelector("[data-ai-trigger-status]"),
      messages: find("messages"), prompt: find("prompt"), form: find("form"), send: find("send"), close: find("close"),
      newChat: find("new-chat"), history: find("history"), settings: find("settings"), disclosure: find("disclosure"),
      settingsStatus: settingsDialog.querySelector("[data-ai-settings-status]"), providers: settingsDialog.querySelector("[data-ai-settings-body]"),
      historyList: historyDialog.querySelector("[data-ai-history-body]"),
    };
    if (Object.entries(elements).some(([key, value]) => !value && !["railStatus", "disclosure"].includes(key))) throw new TypeError("AI assistant markup is incomplete");
    const state = suppliedState || {};
    Object.assign(state, {
      loaded: false, available: false, version: "", providers: [], default: {}, authMethods: {}, skills: [],
      sessionId: null, contextKey: null, busy: false, requestGeneration: 0, oauth: null,
    });
    const allowPath = path => typeof path === "string" && path.startsWith("/api/ai/");
    const request = (path, requestOptions = {}) => sessionClient.json(path, requestOptions, { allowPath, defaultMessage: "The AI service request failed" });
    const fetchActivity = (path, requestOptions = {}) => sessionClient.fetch(path, requestOptions, { allowPath, defaultMessage: "Agent activity is unavailable" });
    const shared = window.SchemiiShared;
    shared.decorateIconControl(trigger, { icon: "assistant", label: labels.trigger || "AI assistant", placement: "bottom", className: trigger.className });
    if (root.id) trigger.setAttribute("aria-controls", root.id);
    if (elements.railStatus) trigger.append(elements.railStatus);
    shared.decorateIconControl(elements.history, { icon: "history", label: "Chat history" });
    shared.decorateIconControl(elements.newChat, { icon: "newChat", label: "New chat" });
    shared.decorateIconControl(elements.settings, { icon: "settings", label: "Provider settings" });
    shared.decorateIconControl(elements.close, { icon: "close", label: "Close AI assistant" });
    const settingsClose = settingsDialog.querySelector("[data-ai-settings-close]");
    const historyClose = historyDialog.querySelector("[data-ai-history-close]");
    shared.decorateIconControl(settingsClose, { icon: "close", label: "Close provider settings" });
    shared.decorateIconControl(historyClose, { icon: "close", label: "Close chat history" });

    function storedModel() {
      try { return normalizeStoredModel(localStorage.getItem(storageKey)); } catch { return ""; }
    }

    function rememberModel(value = elements.model.value) {
      const normalized = normalizeStoredModel(value);
      if (!normalized) return;
      try { localStorage.setItem(storageKey, normalized); } catch { /* Model preference persistence is optional. */ }
    }

    function setOpen(open) {
      root.classList.toggle("open", open);
      root.setAttribute("aria-hidden", String(!open));
      root.inert = !open;
      trigger.classList.toggle("active", open);
      trigger.setAttribute("aria-expanded", String(open));
      onOpenChange(open);
      if (open) {
        loadStatus();
        requestAnimationFrame(() => elements.prompt.focus());
      } else if (root.contains(document.activeElement)) trigger.focus();
    }

    function setBusy(busy) {
      state.busy = busy;
      elements.send.disabled = busy || !state.available || !elements.model.value;
      elements.prompt.disabled = busy || !state.available || !elements.model.value;
      for (const control of [elements.newChat, elements.history, elements.settings, elements.access, elements.model]) control.disabled = busy || (control === elements.model && !elements.model.value);
      for (const control of extraBusyControls) control.disabled = busy;
      elements.send.textContent = busy ? "Working..." : "Send";
    }

    function renderModels() {
      const previous = normalizeStoredModel(elements.model.value) || storedModel();
      elements.model.replaceChildren();
      for (const provider of state.providers.filter(item => item.connected && item.models?.length)) {
        const group = document.createElement("optgroup");
        group.label = provider.name;
        for (const model of provider.models) {
          const option = document.createElement("option");
          option.value = JSON.stringify({ providerId: provider.id, modelId: model.id });
          const active = !model.status || model.status === "active";
          option.disabled = !active;
          option.textContent = active ? model.name : `${model.name} (${model.status})`;
          group.append(option);
        }
        elements.model.append(group);
      }
      const selectable = option => option.value && !option.disabled;
      if (previous && [...elements.model.options].some(option => option.value === previous && selectable(option))) elements.model.value = previous;
      else {
        const provider = state.providers.find(item => item.connected && item.models?.some(model => model.id === state.default?.[item.id]));
        const preferred = provider && JSON.stringify({ providerId: provider.id, modelId: state.default[provider.id] });
        const defaultOption = preferred && [...elements.model.options].find(option => option.value === preferred && selectable(option));
        const fallback = [...elements.model.options].find(selectable);
        elements.model.value = (defaultOption || fallback)?.value || "";
      }
      if (!elements.model.options.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Connect a provider in settings";
        elements.model.append(option);
      }
      const hasModel = Boolean(elements.model.value);
      elements.prompt.placeholder = hasModel ? (labels.prompt || "Ask the assistant...") : "Connect a provider in settings to start chatting";
      const emptyCopy = elements.messages.querySelector("[data-ai-empty-copy]");
      if (emptyCopy && !hasModel) emptyCopy.textContent = "Connect a provider in settings to start chatting.";
      if (hasModel) rememberModel();
      setBusy(state.busy);
    }

    function authMethods(providerId) {
      const methods = state.authMethods?.[providerId] ?? [];
      if (Array.isArray(methods)) return methods.map(method => typeof method === "string" ? { id: method, name: method } : method);
      return Object.entries(methods).map(([id, method]) => typeof method === "string" ? { id, name: method } : { id, ...method });
    }

    function renderOauthCompletion(authorization) {
      const form = document.createElement("form");
      form.className = "ai-oauth-completion";
      const instructions = document.createElement("p");
      instructions.textContent = authorization.instructions || "Complete authorization in the opened page, then enter the returned code if requested.";
      const url = safeHttpUrl(authorization.url);
      if (url) {
        const link = document.createElement("a");
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "Open authorization page";
        form.append(instructions, link);
      } else form.append(instructions);
      const code = document.createElement("input");
      code.name = "code"; code.autocomplete = "off"; code.placeholder = "Callback code (if provided)";
      const finish = document.createElement("button");
      finish.className = "button button-primary"; finish.type = "submit"; finish.textContent = "Complete connection";
      form.append(code, finish);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!state.oauth) return;
        try {
          await request("/api/ai/auth/oauth/callback", { method: "POST", body: JSON.stringify({ providerId: state.oauth.providerId, method: state.oauth.method, ...(code.value.trim() ? { code: code.value.trim() } : {}) }) });
          state.oauth = null;
          await loadStatus(true);
        } catch (error) { elements.settingsStatus.textContent = error.message; }
      });
      elements.settingsStatus.replaceChildren(form);
    }

    function buildAuthForm(provider, method) {
      const form = document.createElement("form");
      form.className = "ai-auth-form";
      const methodId = Number(method.id);
      const apiKeyMethod = method.type === "api" || /api.?key/i.test(method.name ?? method.label ?? "");
      const label = document.createElement("strong");
      label.textContent = method.name ?? (apiKeyMethod ? "API key" : "OAuth");
      form.append(label);
      const helpUrl = safeHttpUrl(method.helpUrl);
      if (helpUrl) {
        const help = document.createElement("a");
        help.className = "ai-auth-help"; help.href = helpUrl; help.target = "_blank"; help.rel = "noopener noreferrer"; help.textContent = method.helpLabel || "Create provider key";
        form.append(help);
      }
      const addInputs = () => {
        for (const definition of method.inputs ?? method.prompts ?? []) {
          const name = definition.id ?? definition.key ?? definition.name;
          const input = definition.type === "select" ? document.createElement("select") : document.createElement("input");
          if (definition.type === "select") for (const item of definition.options ?? []) {
            const option = document.createElement("option"); option.value = item.value; option.textContent = item.label || item.value; input.append(option);
          }
          input.name = name; input.required = definition.required !== false; input.autocomplete = "off"; input.placeholder = definition.label ?? definition.message ?? name;
          form.append(input);
        }
      };
      if (apiKeyMethod) {
        const key = document.createElement("input");
        key.type = "password"; key.name = "key"; key.autocomplete = "off"; key.placeholder = "API key"; key.required = true;
        form.append(key);
      }
      addInputs();
      const submit = document.createElement("button");
      submit.type = "submit"; submit.className = "button button-primary"; submit.textContent = apiKeyMethod ? "Connect" : "Start authorization";
      form.append(submit);
      form.addEventListener("submit", async event => {
        event.preventDefault(); submit.disabled = true;
        try {
          if (apiKeyMethod) {
            const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name && name !== "key"));
            await request("/api/ai/auth/api", { method: "POST", body: JSON.stringify({ providerId: provider.id, key: form.elements.key.value, inputs }) });
            form.elements.key.value = "";
            await loadStatus(true);
          } else {
            const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name));
            const authorization = await request("/api/ai/auth/oauth/authorize", { method: "POST", body: JSON.stringify({ providerId: provider.id, method: methodId, inputs }) });
            state.oauth = { providerId: provider.id, method: methodId };
            renderOauthCompletion(authorization);
            const url = safeHttpUrl(authorization.url);
            if (url) window.open(url, "_blank", "noopener,noreferrer");
          }
        } catch (error) { elements.settingsStatus.textContent = error.message; }
        finally { submit.disabled = false; }
      });
      return form;
    }

    function renderProviders() {
      elements.providers.replaceChildren();
      for (const provider of state.providers) {
        const card = document.createElement("article"); card.className = "ai-provider-card";
        const heading = document.createElement("div"); heading.className = "ai-provider-heading";
        const name = document.createElement("strong"); name.textContent = provider.name;
        const indicator = document.createElement("span");
        const free = provider.connected && provider.authenticated === false;
        indicator.className = provider.connected ? "connected" : ""; indicator.textContent = free ? "Free access" : provider.connected ? "Connected" : "Not connected";
        heading.append(name, indicator); card.append(heading);
        if (provider.connected && !free) {
          const disconnect = document.createElement("button"); disconnect.type = "button"; disconnect.className = "button button-ghost"; disconnect.textContent = "Disconnect";
          disconnect.addEventListener("click", async () => {
            if (!confirm(`Disconnect ${provider.name}? This affects both Schemii and Schemer.`)) return;
            await request(`/api/ai/auth/${encodeURIComponent(provider.id)}`, { method: "DELETE" }); await loadStatus(true);
          });
          card.append(disconnect);
        } else {
          const methods = authMethods(provider.id);
          for (const method of methods) card.append(buildAuthForm(provider, method));
          if (!methods.length) { const note = document.createElement("p"); note.textContent = "This provider did not advertise a supported authentication method."; card.append(note); }
        }
        elements.providers.append(card);
      }
    }

    async function loadStatus(renderSettings = false) {
      elements.status.textContent = "Checking";
      try {
        const payload = await request("/api/ai/status", { method: "GET" });
        Object.assign(state, { loaded: true, available: payload.available === true || payload.healthy === true, version: payload.version ?? "", providers: payload.providers ?? [], default: payload.default ?? {}, authMethods: payload.authMethods ?? {}, skills: payload.skills ?? [] });
        const connected = state.providers.filter(provider => provider.connected).length;
        elements.status.textContent = state.available ? `${connected} connected` : "Unavailable";
        elements.status.classList.toggle("available", state.available);
        elements.railStatus?.classList.toggle("available", state.available);
        elements.settingsStatus.textContent = state.available ? `OpenCode ${state.version || "available"}` : "OpenCode is unavailable. The application remains usable without AI.";
      } catch (error) {
        Object.assign(state, { loaded: true, available: false, providers: [], default: {} });
        elements.status.textContent = "Offline"; elements.settingsStatus.textContent = `AI unavailable: ${error.message}`;
      }
      renderModels();
      if (renderSettings || settingsDialog.open) renderProviders();
    }

    function removeEmptyState() { elements.messages.querySelector(".ai-empty-state")?.remove(); }

    function appendMessage(role, text) {
      removeEmptyState();
      const message = document.createElement("article"); message.className = `ai-message ${role}`;
      const label = document.createElement("span"); label.textContent = role === "assistant" ? "Assistant" : role === "tool" ? "Query result" : "You";
      const body = document.createElement("p"); body.textContent = String(text ?? "");
      message.append(label, body); elements.messages.append(message); scrollToEnd();
      return message;
    }

    function appendQueryResult(result) {
      removeEmptyState();
      const columns = (result?.columns ?? []).map(column => column?.name ?? String(column));
      const rows = Array.isArray(result?.rows) ? result.rows : [];
      const message = document.createElement("article"); message.className = "ai-message tool ai-query-result";
      const label = document.createElement("span"); label.textContent = "Query result";
      const card = document.createElement("div"); card.className = "ai-query-result-card";
      const meta = document.createElement("div"); meta.className = "ai-query-result-meta";
      const count = document.createElement("strong"); count.textContent = `${rows.length} row${rows.length === 1 ? "" : "s"}`;
      const status = document.createElement("span"); status.textContent = result?.truncated ? `Truncated / ${rows.length} displayed` : "Complete result";
      meta.append(count, status); card.append(meta);
      if (columns.length) {
        const scroll = document.createElement("div"); scroll.className = "ai-query-result-scroll"; scroll.tabIndex = 0;
        scroll.setAttribute("aria-label", `Query result with ${rows.length} row${rows.length === 1 ? "" : "s"}`);
        const table = document.createElement("table"); table.className = "ai-query-result-table";
        const head = document.createElement("thead"); const headingRow = document.createElement("tr");
        for (const column of columns) { const heading = document.createElement("th"); heading.scope = "col"; heading.textContent = column; headingRow.append(heading); }
        head.append(headingRow);
        const body = document.createElement("tbody");
        for (const row of rows) {
          const tableRow = document.createElement("tr");
          columns.forEach((_column, index) => {
            const cell = document.createElement("td"); const value = Array.isArray(row) ? row[index] : null;
            if (value === null) { cell.textContent = "NULL"; cell.className = "null"; }
            else if (value === "") { cell.textContent = "empty"; cell.className = "empty"; }
            else { try { cell.textContent = typeof value === "object" ? JSON.stringify(value) : String(value); } catch { cell.textContent = "[unsupported value]"; } }
            cell.title = cell.textContent; tableRow.append(cell);
          });
          body.append(tableRow);
        }
        table.append(head, body); scroll.append(table); card.append(scroll);
      }
      if (!rows.length) { const empty = document.createElement("p"); empty.className = "ai-query-result-empty"; empty.textContent = "Query returned no rows."; card.append(empty); }
      message.append(label, card); elements.messages.append(message); scrollToEnd(); return message;
    }

    function scrollToEnd() { elements.messages.scrollTop = elements.messages.scrollHeight; }

    function beginActivity(modelName) {
      removeEmptyState();
      const startedAt = performance.now();
      const details = document.createElement("details"); details.className = "ai-run active"; details.open = true; details.setAttribute("role", "status");
      const summary = document.createElement("summary");
      const indicator = document.createElement("span"); indicator.className = "ai-progress-grid"; indicator.setAttribute("aria-hidden", "true");
      for (let index = 0; index < 25; index += 1) { const dot = document.createElement("i"); dot.style.setProperty("--dot-index", index); indicator.append(dot); }
      const title = document.createElement("span"); title.className = "ai-run-title shimmer"; title.textContent = "Starting assistant";
      const elapsed = document.createElement("time"); elapsed.className = "ai-run-time"; elapsed.textContent = "0.0s";
      const steps = document.createElement("div"); steps.className = "ai-run-steps";
      summary.append(indicator, title, elapsed); details.append(summary, steps); elements.messages.append(details); scrollToEnd();
      const stageElements = new Map(); let retryAt = null; let finished = false;
      const setStage = (key, label, status = "running") => {
        const safeStatus = ["running", "completed", "error"].includes(status) ? status : "running";
        let row = stageElements.get(key);
        if (!row) {
          row = document.createElement("div"); row.className = "ai-run-step";
          const marker = document.createElement("span"); marker.className = "ai-run-step-marker"; marker.setAttribute("aria-hidden", "true");
          const copy = document.createElement("span"); copy.className = "ai-run-step-copy"; row.append(marker, copy); steps.append(row); stageElements.set(key, row);
        }
        row.className = `ai-run-step ${safeStatus}`; row.querySelector(".ai-run-step-copy").textContent = label;
      };
      setStage("request", `Opening ${modelName || "selected model"}`);
      const tick = () => { elapsed.textContent = formatDuration(performance.now() - startedAt); if (retryAt) title.textContent = `Retrying in ${Math.max(0, Math.ceil((retryAt - Date.now()) / 1000))}s`; };
      const timer = setInterval(tick, 100);
      return {
        update(event) {
          if (finished || !event || typeof event !== "object") return;
          if (event.type === "part") setStage("model", "Model started", "completed");
          if (event.type === "connection") {
            if (event.state === "connected") { title.textContent = "Waiting for model"; setStage("request", `Connected to ${modelName || "selected model"}`, "completed"); }
            else { title.textContent = "Working without live updates"; setStage("stream", "Live activity disconnected", "error"); }
          } else if (event.type === "session" && event.state === "busy") { retryAt = null; title.textContent = "Agent is working"; setStage("model", "Model started"); }
          else if (event.type === "session" && event.state === "retry") { retryAt = Number.isFinite(event.retryAt) ? event.retryAt : null; title.textContent = "Retrying provider"; setStage("retry", `Provider retry ${Number.isInteger(event.attempt) ? event.attempt : ""}`.trim()); }
          else if (event.type === "session" && event.state === "error") { title.textContent = "Provider reported an issue"; setStage("provider-error", "Provider issue detected", "error"); }
          else if (event.type === "session" && event.state === "idle") { retryAt = null; title.textContent = "Finalizing response"; setStage("model", "Model finished", "completed"); }
          else if (event.type === "compaction") { title.textContent = "Compacting context"; setStage("compaction", "Context compacted", event.state === "completed" ? "completed" : "running"); }
          else if (event.type === "part" && event.kind === "reasoning") { title.textContent = event.state === "completed" ? "Preparing response" : "Reasoning"; setStage(event.key, "Reasoning", event.state); }
          else if (event.type === "part" && event.kind === "text") { title.textContent = "Writing response"; setStage(event.key, "Writing response", event.state); }
          else if (event.type === "part" && event.kind === "tool" && toolLabels[event.tool]) { title.textContent = toolLabels[event.tool]; setStage(event.key, toolLabels[event.tool], event.state); }
          else if (event.type === "part" && event.kind === "skill" && skillLabels[event.skill]) { title.textContent = `Loading ${skillLabels[event.skill]}`; setStage(event.key, skillLabels[event.skill], event.state); }
          scrollToEnd();
        },
        finish(outcome) {
          if (finished) return; clearInterval(timer); retryAt = null; tick(); finished = true;
          const failed = outcome === "error"; details.classList.remove("active"); details.classList.add(failed ? "failed" : "completed"); title.classList.remove("shimmer"); title.textContent = failed ? "Agent stopped" : "Completed";
          setStage("model", failed ? "Response failed" : "Model finished", failed ? "error" : "completed"); if (!failed) setStage("delivered", "Response delivered", "completed");
          if (!failed) setTimeout(() => { details.open = false; }, 650);
        }
      };
    }

    async function readActivity(sessionId, onEvent, signal) {
      const response = await fetchActivity(`/api/ai/sessions/${encodeURIComponent(sessionId)}/activity`, { method: "GET", signal });
      if (!response.body) throw new Error("Agent activity stream is unavailable");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n"); buffer = lines.pop() || "";
        for (const line of lines) if (line.trim()) try { onEvent(JSON.parse(line)); } catch { /* Ignore malformed records. */ }
        if (done) break;
      }
    }

    function startActivityStream(sessionId, activity) {
      const controller = new AbortController(); let resolveReady; let readyResolved = false;
      const ready = new Promise(resolve => { resolveReady = resolve; });
      const markReady = () => { if (!readyResolved) { readyResolved = true; resolveReady(); } };
      const done = readActivity(sessionId, event => { if (event?.type === "connection") markReady(); activity.update(event); }, controller.signal)
        .catch(error => { markReady(); if (error.name !== "AbortError") activity.update({ type: "connection", state: "disconnected" }); }).finally(markReady);
      return { ready, done, abort: () => controller.abort() };
    }

    function renderReasoning(part) {
      if (!part.text) return;
      const details = document.createElement("details"); details.className = "ai-reasoning";
      const summary = document.createElement("summary"); summary.textContent = `Thought${Number.isFinite(part.durationMs) ? ` / ${formatDuration(part.durationMs)}` : ""}`;
      const body = document.createElement("p"); body.textContent = part.text; details.append(summary, body); elements.messages.append(details);
    }

    function renderToolPart(part) {
      const label = part.type === "skill" ? skillLabels[part.skill] : toolLabels[part.tool];
      if (!label) return;
      const status = ["pending", "running", "completed", "error"].includes(part.status) ? part.status : "completed";
      const card = document.createElement("div"); card.className = `ai-tool-part ${status}`;
      const marker = document.createElement("span"); marker.className = "ai-tool-marker"; marker.setAttribute("aria-hidden", "true");
      const name = document.createElement("strong"); name.textContent = label;
      const statusNode = document.createElement("span"); statusNode.textContent = status;
      card.append(marker, name, statusNode); elements.messages.append(card);
    }

    function renderGenericAction(action, capture) {
      let normalized;
      try { normalized = validateAction?.(action, capture); } catch { return; }
      if (!normalized) return;
      const card = document.createElement("section"); card.className = `ai-action-card${normalized.destructive ? " destructive" : ""}`;
      const title = document.createElement("strong"); title.textContent = normalized.title;
      const summary = document.createElement("p"); summary.textContent = normalized.summary;
      card.append(title, summary);
      if (normalized.review) { const review = document.createElement("pre"); review.textContent = normalized.review; card.append(review); }
      const button = document.createElement("button"); button.type = "button"; button.className = "button button-primary"; button.textContent = normalized.buttonLabel || (normalized.destructive ? "Review deletion" : "Review & confirm");
      button.addEventListener("click", async () => {
        card.querySelectorAll(".ai-action-error").forEach(error => error.remove()); button.disabled = true;
        try { const appliedLabel = await applyAction(normalized, capture); button.textContent = appliedLabel || normalized.appliedLabel || "Applied"; card.classList.add("applied"); }
        catch (error) { button.disabled = false; const detail = document.createElement("p"); detail.className = "ai-action-error"; detail.textContent = error.message; card.append(detail); }
      });
      card.append(button); elements.messages.append(card);
    }

    function renderResponse(response, capture) {
      let renderedText = false;
      for (const part of response.parts ?? []) {
        if (part?.type === "text" && part.text) { appendMessage("assistant", part.text); renderedText = true; }
        else if (part?.type === "reasoning") renderReasoning(part);
        else if (part?.type === "tool" || part?.type === "skill") renderToolPart(part);
      }
      if (!renderedText && response.text) appendMessage("assistant", response.text);
      for (const action of response.actions ?? []) {
        if (renderAction) renderAction(action, capture, api);
        else renderGenericAction(action, capture);
      }
      scrollToEnd();
    }

    async function ensureSession(model, capture, key) {
      if (state.sessionId && state.contextKey === key) return state.sessionId;
      state.sessionId = null;
      const title = boundedUtf8Text(createSessionTitle ? createSessionTitle(capture, elements.access.value) : labels.sessionTitle || "Assistant chat", 256);
      const session = await request("/api/ai/sessions", { method: "POST", body: JSON.stringify({ title, model }) });
      state.sessionId = session.id; state.contextKey = key;
      return session.id;
    }

    async function sendMessage(text, renderedRole = "user", messageOptions = {}) {
      if (!text.trim() || state.busy) return;
      let model;
      try { model = JSON.parse(elements.model.value); } catch { return; }
      const accessLevel = elements.access.value;
      const capture = messageOptions.capture ?? getContext(accessLevel);
      if (!capture) return;
      const key = contextKey(capture, accessLevel);
      const requestGeneration = ++state.requestGeneration;
      if (renderedRole === "user") appendMessage("user", text);
      const activity = beginActivity(elements.model.selectedOptions[0]?.textContent || model.modelId);
      let stream = null; setBusy(true);
      try {
        const sessionId = await ensureSession(model, capture, key);
        if (requestGeneration !== state.requestGeneration) return;
        stream = startActivityStream(sessionId, activity);
        await Promise.race([stream.ready, new Promise(resolve => setTimeout(resolve, 1500))]);
        const extras = messageOptions.extras ?? {};
        const payload = buildMessagePayload ? buildMessagePayload({ text, model, capture, accessLevel, extras }) : { text, model, accessLevel, ...extras };
        const response = await request(`/api/ai/sessions/${encodeURIComponent(sessionId)}/messages`, { method: "POST", body: JSON.stringify(payload) });
        if (requestGeneration !== state.requestGeneration) return;
        await Promise.race([stream.done, new Promise(resolve => setTimeout(resolve, 750))]);
        renderResponse(response, capture); activity.finish("completed");
      } catch (error) {
        if (requestGeneration === state.requestGeneration) {
          activity.finish("error"); appendMessage("assistant", `AI unavailable: ${error.message}`);
          if (["provider_timeout", "provider_empty_response", "opencode_error"].includes(error.code)) await loadStatus();
        }
      } finally { stream?.abort(); if (requestGeneration === state.requestGeneration) setBusy(false); }
    }

    function resetConversation(copy = labels.newChatCopy || "Proposals will use the current application context.") {
      if (state.busy) return;
      state.requestGeneration += 1; state.sessionId = null; state.contextKey = null; elements.messages.replaceChildren(); onNewChat();
      const empty = document.createElement("div"); empty.className = "ai-empty-state";
      const title = document.createElement("strong"); title.textContent = "New conversation";
      const paragraph = document.createElement("p"); paragraph.dataset.aiEmptyCopy = ""; paragraph.textContent = copy;
      empty.append(title, paragraph); elements.messages.append(empty);
    }

    function formatHistoryDate(value) {
      if (!Number.isFinite(value)) return "Saved conversation";
      const date = new Date(value); return Number.isNaN(date.getTime()) ? "Saved conversation" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    }

    async function restoreSession(session, binding) {
      try {
        const currentKey = contextKey(getContext(elements.access.value), elements.access.value);
        if (!canViewSession(binding, currentKey, elements.access.value)) throw new Error("Select the original data disclosure and PostgreSQL target before opening this conversation");
        const history = await request(`/api/ai/sessions/${encodeURIComponent(session.id)}/messages`, { method: "GET" });
        state.requestGeneration += 1; elements.messages.replaceChildren();
        for (const message of history.messages ?? []) {
          if (message.role === "user") appendMessage("user", message.text);
          if (message.role === "assistant") renderResponse({ parts: message.parts ?? [], text: message.text ?? "", actions: [] }, null);
        }
        if (!elements.messages.children.length) appendMessage("assistant", "This saved conversation has no displayable messages.");
        const resumable = binding.key == null || binding.key === currentKey;
        state.sessionId = resumable ? session.id : null; state.contextKey = resumable ? currentKey : null;
        const modelValue = normalizeStoredModel(JSON.stringify(history.model ?? {}));
        if (modelValue && [...elements.model.options].some(option => option.value === modelValue)) { elements.model.value = modelValue; rememberModel(); }
        setBusy(false); historyDialog.close(); setOpen(true); scrollToEnd(); elements.prompt.focus();
        if (!resumable) appendMessage("assistant", "This history is read-only in the current context. Sending starts a new isolated chat.");
      } catch (error) { elements.historyList.textContent = `Could not open chat: ${error.message}`; }
    }

    async function renderHistory() {
      elements.historyList.replaceChildren();
      const loading = document.createElement("p"); loading.className = "ai-history-empty"; loading.textContent = "Loading conversations..."; elements.historyList.append(loading);
      try {
        const history = await request("/api/ai/sessions", { method: "GET" }); elements.historyList.replaceChildren();
        if (!(history.sessions ?? []).length) { loading.textContent = "No saved conversations yet."; elements.historyList.append(loading); return; }
        for (const session of history.sessions) {
          const binding = parseSession(session);
          const item = document.createElement("article"); item.className = `ai-history-item${session.id === state.sessionId ? " current" : ""}`;
          const copy = document.createElement("div"); copy.className = "ai-history-copy";
          const title = document.createElement("strong"); title.textContent = binding.title;
          const date = document.createElement("span"); date.textContent = `${formatHistoryDate(session.updatedAt ?? session.createdAt)}${session.id === state.sessionId ? " / Current" : ""}`;
          const open = document.createElement("button"); open.type = "button"; open.className = "button button-ghost"; open.textContent = session.id === state.sessionId ? "Reopen" : "Open"; open.addEventListener("click", () => restoreSession(session, binding));
          const remove = document.createElement("button"); remove.type = "button"; remove.className = "button button-ghost ai-history-delete"; remove.textContent = "Delete";
          remove.addEventListener("click", async () => {
            if (!confirm(`Permanently delete chat “${binding.title}”?`)) return;
            await request(`/api/ai/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
            if (state.sessionId === session.id) resetConversation(); await renderHistory();
          });
          copy.append(title, date); item.append(copy, open, remove); elements.historyList.append(item);
        }
      } catch (error) { loading.textContent = `Could not load chat history: ${error.message}`; }
    }

    const api = Object.freeze({ appendMessage, appendQueryResult, renderResponse, sendMessage, scrollToEnd, get accessLevel() { return elements.access.value; }, get state() { return state; } });
    trigger.addEventListener("click", () => setOpen(!root.classList.contains("open")));
    elements.close.addEventListener("click", () => setOpen(false));
    elements.newChat.addEventListener("click", () => resetConversation());
    elements.history.addEventListener("click", async () => { if (state.busy) return; historyDialog.showModal(); await renderHistory(); });
    elements.settings.addEventListener("click", async () => { settingsDialog.showModal(); await loadStatus(true); });
    settingsClose.addEventListener("click", () => settingsDialog.close());
    historyClose.addEventListener("click", () => historyDialog.close());
    elements.model.addEventListener("change", () => { rememberModel(); setBusy(state.busy); });
    elements.access.addEventListener("change", () => onAccessChange(elements.access.value, api));
    elements.form.addEventListener("submit", event => { event.preventDefault(); const text = elements.prompt.value.trim(); if (!text) return; elements.prompt.value = ""; sendMessage(text); });
    elements.prompt.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.form.requestSubmit(); } });
    document.addEventListener("keydown", event => { if (event.key === "Escape" && root.classList.contains("open") && !settingsDialog.open && !historyDialog.open) setOpen(false); });
    root.inert = true;
    return Object.freeze({ ...api, open: () => setOpen(true), close: () => setOpen(false), refresh: loadStatus, reset: resetConversation, normalizeStoredModel });
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createAiAssistant, normalizeStoredAiModel: normalizeStoredModel, aiContextFingerprint, boundedAiQueryResult });
})();
