const elements = {
  dialog: document.querySelector("#connections-dialog"),
  connectionList: document.querySelector("#connection-list"),
  connectionForm: document.querySelector("#connection-form"),
  connectionStatus: document.querySelector("#connection-status"),
  relationList: document.querySelector("#relation-list"),
  relationStatus: document.querySelector("#relation-browser-status"),
  relationDetail: document.querySelector("#relation-detail"),
  sourceSummary: document.querySelector(".source-summary"),
  sourceName: document.querySelector("#source-name"),
  sourceDetail: document.querySelector("#source-detail"),
  namespaceSelect: document.querySelector("#namespace-select"),
  workspace: document.querySelector(".dashboard-workspace"),
  canvas: document.querySelector("#dashboard-canvas"),
  dashboardList: document.querySelector("#dashboard-list"),
  topDashboardTitle: document.querySelector("#top-dashboard-title"),
  dashboardHeading: document.querySelector("#dashboard-heading"),
  dashboardDescription: document.querySelector("#dashboard-description"),
  saveStatus: document.querySelector("#save-status"),
  editModeButton: document.querySelector("#edit-mode-button"),
  addWidgetButton: document.querySelector("#add-widget-button"),
  conflict: document.querySelector("#dashboard-conflict"),
  formDialog: document.querySelector("#dashboard-form-dialog"),
  dashboardForm: document.querySelector("#dashboard-form"),
  dashboardFormTitle: document.querySelector("#dashboard-form-title"),
  dashboardFormCopy: document.querySelector("#dashboard-form-copy"),
  dashboardFormStatus: document.querySelector("#dashboard-form-status"),
  dashboardName: document.querySelector("#dashboard-name"),
  previewTemplates: document.querySelector("#preview-widget-templates"),
  widgetFocus: document.querySelector("#widget-focus"),
  widgetFocusContent: document.querySelector("#widget-focus-content"),
  widgetInspector: document.querySelector("#widget-inspector"),
  widgetInspectorTitle: document.querySelector("#widget-inspector-title"),
  widgetInspectorBody: document.querySelector("#widget-inspector-body"),
  sqlDialog: document.querySelector("#executed-sql-dialog"),
  sqlContext: document.querySelector("#executed-sql-context"),
  sqlTitle: document.querySelector("#executed-sql-title"),
  sqlStatus: document.querySelector("#executed-sql-status"),
  sqlCode: document.querySelector("#executed-sql-code"),
  sqlParameters: document.querySelector("#executed-sql-parameters"),
  sqlParameterCode: document.querySelector("#executed-sql-parameter-code")
};

let sessionToken = null;
let profiles = [];
let profilesLoading = null;
let selectedProfileId = null;
let selectedRelationIdentity = null;
let relationInspectionGeneration = 0;
let relationCatalogGeneration = 0;
let dashboards = [];
let activeDashboard = null;
let editMode = false;
let showArchived = false;
let saveTimer = null;
let saveQueue = Promise.resolve();
let changeGeneration = 0;
let dashboardConflict = false;
let formAction = "create";
let focusedWidgetId = null;
let focusedSourceRect = null;
let focusAnimation = null;
let draggedWidgetId = null;
let dragCenterOffset = { x: 0, y: 0 };
let dragOrderChanged = false;
let lastSwapTargetId = null;
const executedSqlByResult = new Map();
const postgres = window.SchemiiShared.createPostgresClient({
  getToken: () => sessionToken,
  setToken: token => { sessionToken = token; }
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 600px)").matches;
}

async function ensureSession() {
  if (sessionToken) return sessionToken;
  const response = await fetch("/api/session");
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.token) throw new Error(payload.error?.message || "Could not start a dashboard session");
  sessionToken = payload.token;
  return sessionToken;
}

async function dashboardRequest(path, options = {}, retry = true) {
  if (typeof path !== "string" || !path.startsWith("/api/dashboards")) throw new Error("Dashboard requests must use the local Schemer API");
  const token = await ensureSession();
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Schemii-Token": token, ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (payload.error?.code === "invalid_session" && retry) {
      sessionToken = null;
      return dashboardRequest(path, options, false);
    }
    const error = new Error(payload.error?.message || "Dashboard request failed");
    error.code = payload.error?.code;
    error.currentRevision = payload.error?.currentRevision;
    throw error;
  }
  return payload;
}

function setConnectionStatus(message, error = false) {
  elements.connectionStatus.textContent = message;
  elements.connectionStatus.classList.toggle("error", error);
}

function setSaveStatus(message, state = "") {
  elements.saveStatus.textContent = message;
  elements.saveStatus.dataset.state = state;
}

function profilePayload() {
  return {
    name: document.querySelector("#profile-name").value.trim(),
    host: document.querySelector("#profile-host").value.trim(),
    port: Number(document.querySelector("#profile-port").value),
    dbname: document.querySelector("#profile-database").value.trim(),
    user: document.querySelector("#profile-user").value.trim(),
    password: document.querySelector("#profile-password").value,
    sslmode: document.querySelector("#profile-sslmode").value,
    timeout: Number(document.querySelector("#profile-timeout").value)
  };
}

function fillProfileForm(profile = null) {
  document.querySelector("#profile-id").value = profile?.id ?? "";
  document.querySelector("#profile-name").value = profile?.name ?? "Analytics database";
  document.querySelector("#profile-host").value = profile?.host ?? "127.0.0.1";
  document.querySelector("#profile-port").value = profile?.port ?? 5432;
  document.querySelector("#profile-database").value = profile?.dbname ?? "";
  document.querySelector("#profile-user").value = profile?.user ?? "";
  document.querySelector("#profile-password").value = "";
  document.querySelector("#profile-sslmode").value = profile?.sslmode ?? "prefer";
  document.querySelector("#profile-timeout").value = profile?.timeout ?? 10;
  setConnectionStatus("");
}

function renderProfiles() {
  elements.connectionList.replaceChildren();
  if (!profiles.length) {
    const empty = document.createElement("p");
    empty.className = "empty-connection";
    empty.textContent = "No PostgreSQL connections yet.";
    elements.connectionList.append(empty);
    return;
  }
  for (const profile of profiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `connection-item${profile.id === selectedProfileId ? " active" : ""}`;
    const name = document.createElement("strong");
    name.textContent = profile.name;
    const detail = document.createElement("small");
    detail.textContent = `${profile.user}@${profile.host}:${profile.port} / ${profile.dbname}`;
    button.append(name, detail);
    button.addEventListener("click", async () => {
      selectedProfileId = profile.id;
      fillProfileForm(profile);
      renderProfiles();
      await selectProfile(profile);
    });
    elements.connectionList.append(button);
  }
}

async function loadProfiles() {
  if (profilesLoading) return profilesLoading;
  profilesLoading = (async () => {
    try {
      const result = await postgres.request("/api/postgres/profiles");
      profiles = result.profiles ?? [];
      if (!profiles.some(profile => profile.id === selectedProfileId)) selectedProfileId = profiles[0]?.id ?? null;
      renderProfiles();
      const selected = profiles.find(profile => profile.id === selectedProfileId);
      if (selected) {
        fillProfileForm(selected);
        await selectProfile(selected);
      }
    } catch (error) {
      setConnectionStatus(error.message, true);
    }
  })();
  try {
    return await profilesLoading;
  } finally {
    profilesLoading = null;
  }
}

function renderRelations(catalog) {
  elements.relationList.replaceChildren();
  for (const relation of catalog.relations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "relation-item";
    const name = document.createElement("strong");
    name.textContent = relation.name;
    const kind = document.createElement("span");
    kind.textContent = relation.kind.replaceAll("_", " ");
    button.append(name, kind);
    button.addEventListener("click", () => {
      for (const item of elements.relationList.querySelectorAll(".relation-item")) item.classList.toggle("active", item === button);
      inspectSelectedRelation(catalog, relation);
    });
    elements.relationList.append(button);
  }
  if (!catalog.relations.length) elements.relationStatus.textContent = `No supported relations in ${catalog.database}.${catalog.namespace}.`;
}

function renderRelationDetail(descriptor) {
  const header = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = descriptor.relation;
  const kind = document.createElement("span");
  kind.textContent = descriptor.kind.replaceAll("_", " ");
  header.append(title, kind);
  const fingerprintLabel = document.createElement("span");
  fingerprintLabel.className = "relation-fingerprint-label";
  fingerprintLabel.textContent = "Catalog fingerprint";
  const fingerprint = document.createElement("code");
  fingerprint.className = "relation-fingerprint";
  fingerprint.textContent = descriptor.fingerprint;
  const table = document.createElement("table");
  table.className = "relation-columns";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["#", "Column", "PostgreSQL type", "Nullability"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const column of descriptor.columns) {
    const row = document.createElement("tr");
    for (const value of [column.ordinal, column.name, column.type, column.nullable ? "Nullable" : "Not null"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  const assignment = document.createElement("div");
  assignment.className = "relation-assignment";
  const assignmentLabel = document.createElement("label");
  assignmentLabel.textContent = "Assign verified source to widget";
  const widgetSelect = document.createElement("select");
  widgetSelect.setAttribute("aria-label", "Widget receiving this source");
  for (const widget of activeDashboard?.dashboard.widgets ?? []) {
    const current = widget.configuration?.source;
    const matches = current?.profileId === descriptor.profileId && current.database === descriptor.database && current.namespace === descriptor.namespace && current.relation === descriptor.relation && current.fingerprint === descriptor.fingerprint;
    widgetSelect.append(new Option(`${widget.title}${matches ? " (current)" : ""}`, widget.id));
  }
  assignmentLabel.append(widgetSelect);
  const actions = document.createElement("div");
  const assign = document.createElement("button");
  assign.type = "button";
  assign.className = "button button-primary";
  assign.textContent = "Assign source";
  assign.disabled = !editMode || !widgetSelect.options.length;
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "button button-ghost";
  clear.textContent = "Clear source";
  clear.disabled = !editMode || !widgetSelect.options.length || !activeDashboard?.dashboard.widgets.find(widget => widget.id === widgetSelect.value)?.configuration?.source;
  const assignmentStatus = document.createElement("span");
  assignmentStatus.textContent = editMode ? "" : "Enter Edit mode to change widget sources.";
  const updateClearState = () => {
    clear.disabled = !editMode || !activeDashboard?.dashboard.widgets.find(widget => widget.id === widgetSelect.value)?.configuration?.source;
  };
  widgetSelect.addEventListener("change", updateClearState);
  assign.addEventListener("click", () => {
    const widget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetSelect.value);
    if (!editMode || !widget) return;
    widget.configuration = { source: {
      profileId: descriptor.profileId,
      database: descriptor.database,
      namespace: descriptor.namespace,
      relation: descriptor.relation,
      kind: descriptor.kind,
      fingerprint: descriptor.fingerprint
    }};
    assignmentStatus.textContent = `Assigned to ${widget.title}.`;
    markDashboardChanged(true);
    updateClearState();
  });
  clear.addEventListener("click", () => {
    const widget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetSelect.value);
    if (!editMode || !widget?.configuration?.source) return;
    widget.configuration = {};
    assignmentStatus.textContent = `Cleared source from ${widget.title}.`;
    markDashboardChanged(true);
    updateClearState();
  });
  actions.append(assign, clear, assignmentStatus);
  assignment.append(assignmentLabel, actions);
  elements.relationDetail.replaceChildren(header, fingerprintLabel, fingerprint, table, assignment);
  elements.relationDetail.hidden = false;
}

async function inspectSelectedRelation(catalog, relation) {
  const generation = ++relationInspectionGeneration;
  selectedRelationIdentity = null;
  elements.relationDetail.hidden = true;
  elements.relationStatus.textContent = `Inspecting ${catalog.database}.${catalog.namespace}.${relation.name}...`;
  try {
    const descriptor = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(catalog.profileId)}/relation?database=${encodeURIComponent(catalog.database)}&namespace=${encodeURIComponent(catalog.namespace)}&relation=${encodeURIComponent(relation.name)}`);
    if (generation !== relationInspectionGeneration) return;
    selectedRelationIdentity = {
      profileId: descriptor.profileId,
      database: descriptor.database,
      namespace: descriptor.namespace,
      relation: descriptor.relation,
      kind: descriptor.kind,
      fingerprint: descriptor.fingerprint
    };
    elements.relationStatus.textContent = `${descriptor.columns.length} column${descriptor.columns.length === 1 ? "" : "s"} · ${descriptor.database}.${descriptor.namespace}.${descriptor.relation}`;
    elements.sourceDetail.textContent = `${descriptor.database}.${descriptor.namespace}.${descriptor.relation}`;
    renderRelationDetail(descriptor);
  } catch (error) {
    if (generation !== relationInspectionGeneration) return;
    elements.relationStatus.textContent = error.message;
  }
}

async function loadRelations(profile, namespace) {
  const generation = ++relationCatalogGeneration;
  relationInspectionGeneration += 1;
  selectedRelationIdentity = null;
  elements.relationList.replaceChildren();
  elements.relationDetail.hidden = true;
  if (!profile || !namespace) {
    elements.relationStatus.textContent = "Select a connection and namespace.";
    return;
  }
  elements.relationStatus.textContent = `Loading ${profile.dbname}.${namespace}...`;
  try {
    const catalog = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profile.id)}/relations?database=${encodeURIComponent(profile.dbname)}&namespace=${encodeURIComponent(namespace)}`);
    if (generation !== relationCatalogGeneration) return;
    elements.relationStatus.textContent = `${catalog.relations.length} supported relation${catalog.relations.length === 1 ? "" : "s"} in ${catalog.database}.${catalog.namespace}.`;
    renderRelations(catalog);
  } catch (error) {
    if (generation !== relationCatalogGeneration) return;
    elements.relationStatus.textContent = error.message;
  }
}

async function selectProfile(profile) {
  elements.namespaceSelect.disabled = true;
  elements.namespaceSelect.replaceChildren(new Option("Loading namespaces...", ""));
  try {
    const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profile.id)}/namespaces`);
    const namespaces = result.namespaces ?? [];
    elements.namespaceSelect.replaceChildren(...namespaces.map(namespace => new Option(namespace, namespace)));
    elements.namespaceSelect.disabled = !namespaces.length;
    elements.sourceSummary.classList.add("connected");
    elements.sourceName.textContent = profile.name;
    elements.sourceDetail.textContent = namespaces.length ? `${profile.dbname}.${namespaces[0]}` : `${profile.dbname} has no user namespaces`;
    setConnectionStatus(namespaces.length ? `Connected to ${profile.dbname}.` : "Connected; no user namespaces were found.");
    await loadRelations(profile, namespaces[0]);
  } catch (error) {
    elements.namespaceSelect.replaceChildren(new Option("Connection unavailable", ""));
    elements.sourceSummary.classList.remove("connected");
    elements.sourceName.textContent = profile.name;
    elements.sourceDetail.textContent = error.message;
    setConnectionStatus(error.message, true);
    await loadRelations(null, null);
  }
}

function dashboardWidgetElement(widget) {
  let card = null;
  if (widget.kind === "preview") {
    const template = elements.previewTemplates.content.querySelector(`[data-preview-id="${widget.id}"]`);
    if (template) card = template.cloneNode(true);
  }
  if (!card) {
    card = document.createElement("article");
    card.className = "widget metric-widget placeholder-widget";
    const header = document.createElement("header");
    const title = document.createElement("span");
    title.textContent = widget.title;
    header.append(title);
    const mark = document.createElement("strong");
    mark.textContent = "--";
    const copy = document.createElement("p");
    copy.textContent = "Query configuration arrives in Phase 3";
    card.append(header, mark, copy);
  }
  card.dataset.widgetId = widget.id;
  card.tabIndex = 0;
  card.draggable = editMode;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", editMode ? `Move ${widget.title}` : `Open ${widget.title}`);
  card.removeAttribute("data-preview-id");
  const title = card.querySelector("header span");
  if (title) title.textContent = widget.title;
  const source = widget.configuration?.source;
  if (title && source) {
    let titleGroup = title.parentElement?.tagName === "DIV" ? title.parentElement : null;
    if (!titleGroup) {
      titleGroup = document.createElement("div");
      title.before(titleGroup);
      titleGroup.append(title);
    }
    const sourceLabel = document.createElement("small");
    sourceLabel.className = "widget-source-label";
    sourceLabel.textContent = `${source.database}.${source.namespace}.${source.relation}`;
    titleGroup.append(sourceLabel);
  }
  const oldMenu = card.querySelector("header > button");
  oldMenu?.remove();
  const viewSql = document.createElement("button");
  viewSql.type = "button";
  viewSql.className = "widget-sql-button";
  viewSql.dataset.action = "view-widget-sql";
  viewSql.textContent = "View SQL";
  viewSql.setAttribute("aria-label", `View SQL for ${widget.title}`);
  const controls = document.createElement("div");
  controls.className = "widget-edit-controls";
  const widgetIndex = activeDashboard?.dashboard.widgets.findIndex(item => item.id === widget.id) ?? -1;
  const moveEarlier = document.createElement("button");
  moveEarlier.type = "button";
  moveEarlier.dataset.action = "move-widget-earlier";
  moveEarlier.textContent = "Earlier";
  moveEarlier.disabled = widgetIndex <= 0;
  moveEarlier.setAttribute("aria-label", `Move ${widget.title} earlier`);
  const moveLater = document.createElement("button");
  moveLater.type = "button";
  moveLater.dataset.action = "move-widget-later";
  moveLater.textContent = "Later";
  moveLater.disabled = widgetIndex < 0 || widgetIndex >= (activeDashboard?.dashboard.widgets.length ?? 0) - 1;
  moveLater.setAttribute("aria-label", `Move ${widget.title} later`);
  const duplicate = document.createElement("button");
  duplicate.type = "button";
  duplicate.dataset.action = "duplicate-widget";
  duplicate.textContent = "Duplicate";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.dataset.action = "delete-widget";
  remove.textContent = "Delete";
  controls.append(moveEarlier, moveLater, duplicate, remove);
  card.querySelector("header")?.append(viewSql, controls);
  applyWidgetLayout(card, widget);
  return card;
}

function applyWidgetLayout(card, widget) {
  card.style.setProperty("--mobile-order", widget.layout.mobile.order);
}

function renderDashboard() {
  elements.canvas.replaceChildren();
  if (!activeDashboard) {
    const empty = document.createElement("p");
    empty.className = "empty-dashboard";
    empty.textContent = "Create a dashboard to begin.";
    elements.canvas.append(empty);
    return;
  }
  const dashboard = activeDashboard.dashboard;
  elements.topDashboardTitle.textContent = dashboard.title;
  elements.dashboardHeading.textContent = dashboard.title;
  elements.dashboardDescription.textContent = dashboard.widgets.length ? `${dashboard.widgets.length} saved widget${dashboard.widgets.length === 1 ? "" : "s"}.` : "Empty dashboard. Enter Edit mode to add a widget.";
  document.querySelector("#archive-dashboard").textContent = dashboard.archived ? "Unarchive" : "Archive";
  for (const widget of dashboard.widgets) elements.canvas.append(dashboardWidgetElement(widget));
  if (!dashboard.widgets.length) {
    const empty = document.createElement("p");
    empty.className = "empty-dashboard";
    empty.textContent = editMode ? "Add a widget to this dashboard." : "This dashboard has no widgets.";
    elements.canvas.append(empty);
  }
  elements.canvas.classList.toggle("editing", editMode);
  requestAnimationFrame(() => {
    const viewport = dashboard.viewport[isMobileLayout() ? "mobile" : "desktop"];
    elements.workspace.scrollTo(viewport.x, viewport.y);
  });
}

function renderDashboardList() {
  elements.dashboardList.replaceChildren();
  const activeCount = dashboards.filter(record => !record.dashboard.archived).length;
  const archivedCount = dashboards.length - activeCount;
  document.querySelector("#active-dashboard-count").textContent = activeCount;
  document.querySelector("#archived-dashboard-count").textContent = archivedCount;
  const visible = dashboards.filter(record => record.dashboard.archived === showArchived);
  for (const record of visible) {
    const button = document.createElement("button");
    button.className = `dashboard-link${record.id === activeDashboard?.id ? " active" : ""}`;
    button.type = "button";
    const marker = document.createElement("i");
    const copy = document.createElement("span");
    copy.textContent = record.dashboard.title;
    const count = document.createElement("small");
    count.textContent = `${record.dashboard.widgets.length} widget${record.dashboard.widgets.length === 1 ? "" : "s"}`;
    copy.append(count);
    button.append(marker, copy);
    button.addEventListener("click", async () => {
      await flushPendingSave();
      openDashboard(record.id);
    });
    elements.dashboardList.append(button);
  }
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "empty-sidebar";
    empty.textContent = showArchived ? "No archived dashboards." : "No active dashboards.";
    elements.dashboardList.append(empty);
  }
}

function openDashboard(dashboardId) {
  closeWidgetFocus(true);
  const record = dashboards.find(item => item.id === dashboardId);
  activeDashboard = record ? clone(record) : null;
  dashboardConflict = false;
  elements.conflict.hidden = true;
  setEditMode(false, false);
  renderDashboardList();
  renderDashboard();
  setSaveStatus(activeDashboard ? "Saved" : "No dashboard", activeDashboard ? "saved" : "");
}

async function loadDashboards(preferredId = activeDashboard?.id) {
  try {
    const payload = await dashboardRequest("/api/dashboards");
    dashboards = payload.dashboards ?? [];
    const preferred = dashboards.find(record => record.id === preferredId);
    const fallback = dashboards.find(record => !record.dashboard.archived) ?? dashboards[0];
    openDashboard((preferred ?? fallback)?.id ?? null);
  } catch (error) {
    setSaveStatus(error.message, "error");
  }
}

function markDashboardChanged(render = false) {
  if (!activeDashboard || dashboardConflict) return;
  changeGeneration += 1;
  setSaveStatus("Unsaved changes", "dirty");
  if (render) renderDashboard();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    persistDashboard();
  }, 450);
}

async function persistDashboard() {
  if (!activeDashboard || dashboardConflict) return;
  const dashboardId = activeDashboard.id;
  setSaveStatus("Saving...", "saving");
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    if (activeDashboard?.id !== dashboardId || dashboardConflict) return;
    const generation = changeGeneration;
    const snapshot = clone(activeDashboard);
    try {
      const saved = await dashboardRequest(`/api/dashboards/${encodeURIComponent(dashboardId)}`, { method: "PUT", body: JSON.stringify(snapshot) });
      if (activeDashboard?.id !== dashboardId) return;
      if (generation === changeGeneration) activeDashboard = clone(saved);
      else {
        activeDashboard.revision = saved.revision;
        activeDashboard.updatedAt = saved.updatedAt;
      }
      const index = dashboards.findIndex(record => record.id === dashboardId);
      if (index >= 0) dashboards[index] = clone(activeDashboard);
      renderDashboardList();
      if (generation === changeGeneration) setSaveStatus("Saved", "saved");
      else {
        setSaveStatus("Unsaved changes", "dirty");
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          saveTimer = null;
          persistDashboard();
        }, 450);
      }
    } catch (error) {
      if (error.code === "dashboard_conflict") {
        dashboardConflict = true;
        clearTimeout(saveTimer);
        elements.conflict.hidden = false;
        setSaveStatus("Conflict: reload required", "error");
      } else if (error.code === "invalid_dashboard") {
        const persisted = dashboards.find(record => record.id === dashboardId);
        if (persisted) {
          activeDashboard = clone(persisted);
          renderDashboard();
        }
        setSaveStatus("Invalid layout restored", "error");
      } else {
        setSaveStatus("Save failed", "error");
      }
    }
  });
  return saveQueue;
}

async function flushPendingSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
    await persistDashboard();
  }
  await saveQueue;
}

function setEditMode(enabled, flush = true) {
  editMode = Boolean(enabled && activeDashboard && !dashboardConflict);
  document.body.classList.toggle("dashboard-edit-mode", editMode);
  elements.canvas.classList.toggle("editing", editMode);
  elements.editModeButton.textContent = editMode ? "Finish editing" : "Edit dashboard";
  elements.editModeButton.classList.toggle("button-primary", editMode);
  elements.editModeButton.classList.toggle("button-ghost", !editMode);
  elements.addWidgetButton.hidden = !editMode;
  for (const card of elements.canvas.querySelectorAll(".widget")) {
    card.draggable = editMode;
    const widget = activeDashboard?.dashboard.widgets.find(item => item.id === card.dataset.widgetId);
    if (widget) card.setAttribute("aria-label", editMode ? `Move ${widget.title}` : `Open ${widget.title}`);
  }
  if (!editMode && flush) flushPendingSave();
}

function nextWidgetId() {
  const random = crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : Math.random().toString(16).slice(2);
  return `widget_${random}`;
}

function addWidget() {
  if (!activeDashboard || !editMode) return;
  const widgets = activeDashboard.dashboard.widgets;
  const y = widgets.reduce((maximum, widget) => Math.max(maximum, widget.layout.desktop.y + widget.layout.desktop.h), 0);
  widgets.push({
    id: nextWidgetId(),
    kind: "placeholder",
    title: "Untitled widget",
    layout: { desktop: { x: 0, y, w: 4, h: 3 }, mobile: { order: widgets.length, h: 3 } },
    configuration: {}
  });
  markDashboardChanged(true);
}

function duplicateWidget(widgetId) {
  const source = activeDashboard?.dashboard.widgets.find(widget => widget.id === widgetId);
  if (!source || !editMode) return;
  const duplicate = clone(source);
  duplicate.id = nextWidgetId();
  duplicate.title = `${source.title} copy`;
  duplicate.layout.desktop.y += 1;
  duplicate.layout.mobile.order = activeDashboard.dashboard.widgets.length;
  activeDashboard.dashboard.widgets.push(duplicate);
  markDashboardChanged(true);
}

function persistWidgetOrder(widgets, render = true) {
  widgets.forEach((widget, index) => { widget.layout.mobile.order = index; });
  markDashboardChanged(render);
}

function moveWidget(widgetId, offset) {
  if (!activeDashboard || !editMode) return;
  const widgets = activeDashboard.dashboard.widgets;
  const sourceIndex = widgets.findIndex(widget => widget.id === widgetId);
  const destinationIndex = sourceIndex + offset;
  if (sourceIndex < 0 || destinationIndex < 0 || destinationIndex >= widgets.length) return;
  const [widget] = widgets.splice(sourceIndex, 1);
  widgets.splice(destinationIndex, 0, widget);
  persistWidgetOrder(widgets);
}

function animateWidgetSwap(previousRects) {
  for (const card of elements.canvas.querySelectorAll(".widget")) {
    const previous = previousRects.get(card.dataset.widgetId);
    const current = card.getBoundingClientRect();
    const x = previous?.left - current.left;
    const y = previous?.top - current.top;
    if (!x && !y) continue;
    card.animate([{ transform: `translate(${x}px, ${y}px)` }, { transform: "translate(0, 0)" }], { duration: 260, easing: "cubic-bezier(.22,1,.36,1)" });
  }
}

function swapWidgets(widgetId, targetId) {
  if (!activeDashboard || !editMode || widgetId === targetId) return false;
  const widgets = activeDashboard.dashboard.widgets;
  const sourceIndex = widgets.findIndex(widget => widget.id === widgetId);
  const targetIndex = widgets.findIndex(item => item.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0) return false;
  const cards = Array.from(elements.canvas.querySelectorAll(".widget"));
  const previousRects = new Map(cards.map(card => [card.dataset.widgetId, card.getBoundingClientRect()]));
  [widgets[sourceIndex], widgets[targetIndex]] = [widgets[targetIndex], widgets[sourceIndex]];
  widgets.forEach((widget, index) => { widget.layout.mobile.order = index; });
  const sourceCard = cards.find(card => card.dataset.widgetId === widgetId);
  const targetCard = cards.find(card => card.dataset.widgetId === targetId);
  if (sourceCard.nextSibling === targetCard) targetCard.after(sourceCard);
  else if (targetCard.nextSibling === sourceCard) sourceCard.after(targetCard);
  else {
    const sourceNext = sourceCard.nextSibling;
    const targetNext = targetCard.nextSibling;
    elements.canvas.insertBefore(sourceCard, targetNext);
    elements.canvas.insertBefore(targetCard, sourceNext);
  }
  animateWidgetSwap(previousRects);
  dragOrderChanged = true;
  return true;
}

function deleteWidget(widgetId) {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetId);
  if (!widget || !editMode || !confirm(`Delete widget “${widget.title}”?`)) return;
  activeDashboard.dashboard.widgets = activeDashboard.dashboard.widgets.filter(item => item.id !== widgetId);
  activeDashboard.dashboard.widgets.forEach((item, index) => { item.layout.mobile.order = index; });
  markDashboardChanged(true);
}

function widgetType(widget) {
  if (widget.id === "widget_trend") return "Line chart";
  if (widget.id === "widget_status") return "Donut chart";
  if (widget.id === "widget_recent") return "Data table";
  return "Metric";
}

const DATE_BUCKETS = ["Jun 1–4", "Jun 5–8", "Jun 9–12", "Jun 13–16", "Jun 17–20", "Jun 21–24", "Jun 25–27", "Jun 28–30"];
const KPI_SERIES = {
  widget_revenue: ["$2,940", "$4,380", "$3,720", "$5,460", "$4,810", "$6,220", "$5,570", "$9,760"],
  widget_orders: ["34", "48", "42", "61", "55", "72", "68", "120"],
  widget_average: ["$86.47", "$91.25", "$88.57", "$89.51", "$87.45", "$86.39", "$81.91", "$81.33"]
};
const PREVIEW_POPULATION = [
  ["#500", "Harper Jones", "Pending", "2", "$91.40", "Jun 29, 20:00", 29],
  ["#499", "Casey Khan", "Cancelled", "4", "$148.75", "Jun 25, 05:00", 25],
  ["#498", "Morgan Foster", "Shipped", "3", "$76.20", "Jun 22, 14:00", 22],
  ["#497", "Blair Evans", "Packed", "2", "$63.95", "Jun 18, 23:00", 18],
  ["#496", "Alex Rivera", "Paid", "1", "$42.10", "Jun 15, 17:00", 15],
  ["#495", "Jamie Chen", "Paid", "5", "$185.30", "Jun 10, 11:00", 10],
  ["#494", "Taylor Smith", "Pending", "2", "$58.80", "Jun 6, 09:00", 6],
  ["#493", "Jordan Lee", "Shipped", "3", "$97.45", "Jun 2, 16:00", 2]
];

function markMetricTarget(target, metric, filters) {
  target.dataset.inspectMetric = metric;
  target.dataset.inspectFilters = JSON.stringify(filters);
  target.tabIndex = 0;
  target.setAttribute("role", "button");
  target.setAttribute("aria-label", `Inspect ${metric}`);
}

function markInspectableMetrics(card, widget) {
  const value = card.classList.contains("metric-widget") ? card.querySelector("strong") : null;
  if (value) markMetricTarget(value, widget.title, [{ field: "Order date", value: "Jun 1–30" }, { field: "Displayed value", value: value.textContent.trim() }]);
  card.querySelectorAll(".metric-sparkline i").forEach((bar, index) => {
    const date = DATE_BUCKETS[index];
    const amount = KPI_SERIES[widget.id]?.[index] || "Preview value";
    bar.dataset.barLabel = date;
    bar.dataset.barValue = amount;
    bar.title = `${widget.title}: ${amount} for ${date}`;
    markMetricTarget(bar, widget.title, [{ field: "Order date", value: date }, { field: "Bucket value", value: amount }]);
  });
  const series = card.querySelector(".chart-area svg");
  if (series) markMetricTarget(series, widget.title, [{ field: "Order date", value: "Jun 1–30" }, { field: "Series", value: "Daily gross sales" }]);
  const donut = card.querySelector(".donut");
  if (donut) markMetricTarget(donut, widget.title, [{ field: "Order date", value: "Jun 1–30" }, { field: "Orders", value: "500" }]);
  card.querySelectorAll(".status-legend > span").forEach(status => {
    const label = status.childNodes[1]?.textContent.trim() || status.textContent.trim().split(/\s+/)[0];
    const count = status.querySelector("b")?.textContent.trim() || "100";
    markMetricTarget(status, widget.title, [{ field: "Status", value: label }, { field: "Orders", value: count }]);
  });
  if (card.classList.contains("table-widget")) card.querySelectorAll("tbody tr").forEach(row => markMetricTarget(row, widget.title, [{ field: "Order", value: row.cells[0]?.textContent.trim() || "row" }]));
}

function populationRows(filters) {
  const status = filters.find(filter => filter.field === "Status")?.value;
  const order = filters.find(filter => filter.field === "Order")?.value;
  const date = filters.find(filter => filter.field === "Order date")?.value;
  const dates = date?.match(/Jun (\d+)[–-](\d+)/);
  return PREVIEW_POPULATION.filter(row =>
    (!status || row[2] === status) &&
    (!order || row[0] === order) &&
    (!dates || row[6] >= Number(dates[1]) && row[6] <= Number(dates[2]))
  );
}

function populationTable(filters) {
  const container = document.createElement("div");
  container.className = "population-view";
  const filter = document.createElement("div");
  filter.className = "population-filter";
  const filterLabel = document.createElement("span");
  filterLabel.textContent = "Applied filters";
  filter.append(filterLabel);
  for (const applied of filters) {
    const row = document.createElement("div");
    row.className = "population-filter-row";
    const field = document.createElement("span");
    field.textContent = applied.field;
    const value = document.createElement("strong");
    value.textContent = applied.value;
    row.append(field, value);
    filter.append(row);
  }
  const tableScroll = document.createElement("div");
  tableScroll.className = "population-table-scroll";
  const table = document.createElement("table");
  table.className = "population-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Order", "Customer", "Status", "Items", "Total", "Ordered"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  const rows = populationRows(filters);
  for (const values of rows) {
    const row = document.createElement("tr");
    for (const value of values.slice(0, 6)) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "population-empty";
    cell.textContent = "No preview rows match this selection.";
    row.append(cell);
    body.append(row);
  }
  table.append(head, body);
  tableScroll.append(table);
  container.append(filter, tableScroll);
  return container;
}

function openWidgetFocus(widgetId) {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetId);
  if (!widget) return;
  if (editMode) setEditMode(false);
  const sourceCard = elements.canvas.querySelector(`[data-widget-id="${widget.id}"]`);
  focusedSourceRect = sourceCard?.getBoundingClientRect() ?? null;
  focusedWidgetId = widget.id;
  const card = dashboardWidgetElement(widget);
  card.classList.add("focused-widget-card");
  card.removeAttribute("role");
  card.removeAttribute("tabindex");
  card.removeAttribute("aria-label");
  card.querySelector(".widget-edit-controls")?.remove();
  const close = document.createElement("button");
  close.type = "button";
  close.className = "button button-ghost focused-widget-close";
  close.textContent = "Close";
  close.setAttribute("aria-label", "Close expanded widget");
  const header = card.querySelector(":scope > header");
  header?.append(close);
  const body = document.createElement("div");
  body.className = "focused-widget-body";
  while (header?.nextSibling) body.append(header.nextSibling);
  card.append(body);
  markInspectableMetrics(card, widget);
  elements.widgetFocusContent.replaceChildren(card);
  elements.widgetInspector.classList.add("dismissed");
  elements.widgetFocus.classList.add("inspector-dismissed");
  elements.widgetFocus.hidden = false;
  elements.widgetFocus.classList.add("open");
  elements.workspace.classList.add("widget-focus-open");
  focusAnimation?.cancel();
  if (focusedSourceRect) {
    const target = elements.widgetFocus.getBoundingClientRect();
    focusAnimation = elements.widgetFocus.animate([
      { transformOrigin: "top left", transform: `translate(${focusedSourceRect.left - target.left}px, ${focusedSourceRect.top - target.top}px) scale(${focusedSourceRect.width / target.width}, ${focusedSourceRect.height / target.height})`, borderRadius: "8px", opacity: .72 },
      { transformOrigin: "top left", transform: "translate(0,0) scale(1)", borderRadius: "0", opacity: 1 }
    ], { duration: 280, easing: "cubic-bezier(.22,1,.36,1)" });
  }
}

function closeWidgetFocus(immediate = false) {
  if (!focusedWidgetId && elements.widgetFocus.hidden) return;
  focusAnimation?.cancel();
  focusAnimation = null;
  focusedWidgetId = null;
  elements.workspace.classList.remove("widget-focus-open");
  const finish = () => {
    if (focusedWidgetId) return;
    elements.widgetFocus.classList.remove("open");
    elements.widgetFocus.hidden = true;
    elements.widgetFocusContent.replaceChildren();
    focusedSourceRect = null;
  };
  if (immediate || !focusedSourceRect) return finish();
  const target = elements.widgetFocus.getBoundingClientRect();
  focusAnimation = elements.widgetFocus.animate([
    { transformOrigin: "top left", transform: "translate(0,0) scale(1)", borderRadius: "0", opacity: 1 },
    { transformOrigin: "top left", transform: `translate(${focusedSourceRect.left - target.left}px, ${focusedSourceRect.top - target.top}px) scale(${focusedSourceRect.width / target.width}, ${focusedSourceRect.height / target.height})`, borderRadius: "8px", opacity: .72 }
  ], { duration: 220, easing: "cubic-bezier(.4,0,.2,1)" });
  focusAnimation.finished.then(finish, finish);
}

function closeWidgetInspector() {
  elements.widgetInspector.classList.add("dismissed");
  elements.widgetFocus.classList.add("inspector-dismissed");
}

function openWidgetInspector(metricName, filters = []) {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === focusedWidgetId);
  if (!widget) return;
  elements.widgetInspectorTitle.textContent = metricName || widget.title;
  elements.widgetInspectorBody.replaceChildren(populationTable(filters));
  elements.widgetInspector.classList.remove("dismissed");
  elements.widgetFocus.classList.remove("inspector-dismissed");
}

function inspectMetric(metric) {
  openWidgetInspector(metric.dataset.inspectMetric, JSON.parse(metric.dataset.inspectFilters || "[]"));
}

function openExecutedSql(widget, population = false) {
  if (!widget) return;
  const execution = executedSqlByResult.get(`${widget.id}:${population ? "population" : "widget"}`);
  elements.sqlContext.textContent = population ? "Population result" : "Widget result";
  elements.sqlTitle.textContent = `${widget.title} SQL`;
  elements.sqlStatus.textContent = execution ? "The parameterized statement used for the currently displayed result." : "No SQL was executed for this static preview result.";
  elements.sqlCode.textContent = execution?.sql || "-- Preview data is embedded in Schemer; no database query was run.";
  elements.sqlParameters.hidden = !execution || execution.parameters === undefined;
  elements.sqlParameterCode.textContent = execution?.parameters === undefined ? "" : JSON.stringify(execution.parameters, null, 2);
  elements.sqlDialog.showModal();
}

function openDashboardForm(action) {
  if (action !== "create" && !activeDashboard) return;
  formAction = action;
  const sourceTitle = activeDashboard?.dashboard.title ?? "";
  elements.dashboardFormTitle.textContent = action === "create" ? "New dashboard" : action === "rename" ? "Rename dashboard" : "Duplicate dashboard";
  elements.dashboardFormCopy.textContent = action === "create" ? "Create an empty dashboard." : action === "rename" ? "Update this dashboard’s display name." : "Create an independent copy with the same widgets and layout.";
  elements.dashboardName.value = action === "create" ? "Untitled dashboard" : action === "duplicate" ? `${sourceTitle} copy` : sourceTitle;
  elements.dashboardFormStatus.textContent = "";
  elements.formDialog.showModal();
  elements.dashboardName.select();
}

async function submitDashboardForm() {
  const title = elements.dashboardName.value.trim();
  if (!title) return;
  elements.dashboardFormStatus.textContent = "Saving...";
  try {
    if (formAction === "rename") {
      activeDashboard.dashboard.title = title;
      changeGeneration += 1;
      await persistDashboard();
      renderDashboard();
    } else {
      const created = await dashboardRequest("/api/dashboards", {
        method: "POST",
        body: JSON.stringify({ title, ...(formAction === "duplicate" ? { sourceId: activeDashboard.id } : {}) })
      });
      await loadDashboards(created.id);
    }
    elements.formDialog.close();
  } catch (error) {
    elements.dashboardFormStatus.textContent = error.message;
  }
}

async function archiveDashboard() {
  if (!activeDashboard) return;
  activeDashboard.dashboard.archived = !activeDashboard.dashboard.archived;
  const archived = activeDashboard.dashboard.archived;
  changeGeneration += 1;
  await persistDashboard();
  await loadDashboards(archived ? null : activeDashboard.id);
}

async function deleteDashboard() {
  if (!activeDashboard || !confirm(`Permanently delete dashboard “${activeDashboard.dashboard.title}”?`)) return;
  const dashboardId = activeDashboard.id;
  await dashboardRequest(`/api/dashboards/${encodeURIComponent(dashboardId)}`, { method: "DELETE" });
  activeDashboard = null;
  await loadDashboards();
}

document.querySelector("#connections-button").addEventListener("click", async () => {
  elements.dialog.showModal();
  if (!profiles.length) await loadProfiles();
});
document.querySelector("#close-connections").addEventListener("click", () => elements.dialog.close());
document.querySelector("#new-connection").addEventListener("click", () => { selectedProfileId = null; renderProfiles(); fillProfileForm(); });
elements.connectionForm.addEventListener("submit", async event => {
  event.preventDefault();
  const profileId = document.querySelector("#profile-id").value;
  const path = profileId ? `/api/postgres/profiles/${encodeURIComponent(profileId)}` : "/api/postgres/profiles";
  setConnectionStatus("Saving connection...");
  try {
    const profile = await postgres.request(path, { method: profileId ? "PUT" : "POST", body: JSON.stringify(profilePayload()) });
    selectedProfileId = profile.id;
    document.querySelector("#profile-password").value = "";
    await loadProfiles();
  } catch (error) {
    setConnectionStatus(error.message, true);
  }
});
document.querySelector("#test-connection").addEventListener("click", async () => {
  const profileId = document.querySelector("#profile-id").value;
  if (!profileId) return setConnectionStatus("Save the connection before testing it.", true);
  setConnectionStatus("Testing connection...");
  try {
    const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/test`, { method: "POST", body: "{}" });
    setConnectionStatus(`Connected to ${result.database ?? "PostgreSQL"}.`);
  } catch (error) {
    setConnectionStatus(error.message, true);
  }
});
elements.namespaceSelect.addEventListener("change", () => {
  const profile = profiles.find(item => item.id === selectedProfileId);
  if (profile && elements.namespaceSelect.value) {
    elements.sourceDetail.textContent = `${profile.dbname}.${elements.namespaceSelect.value}`;
    loadRelations(profile, elements.namespaceSelect.value);
  }
});
document.querySelector("#refresh-button").addEventListener("click", async event => {
  if (!selectedProfileId || !elements.namespaceSelect.value) return elements.dialog.showModal();
  event.currentTarget.textContent = "Checking...";
  try {
    await postgres.request(`/api/postgres/profiles/${encodeURIComponent(selectedProfileId)}/fingerprint?namespace=${encodeURIComponent(elements.namespaceSelect.value)}`);
    elements.sourceDetail.textContent = `${profiles.find(profile => profile.id === selectedProfileId)?.dbname}.${elements.namespaceSelect.value} refreshed now`;
  } catch (error) {
    elements.sourceDetail.textContent = error.message;
  } finally {
    event.currentTarget.textContent = "Refresh";
  }
});

elements.editModeButton.addEventListener("click", () => setEditMode(!editMode));
elements.addWidgetButton.addEventListener("click", addWidget);
document.querySelector("#new-dashboard").addEventListener("click", () => openDashboardForm("create"));
document.querySelector("#rename-dashboard").addEventListener("click", () => openDashboardForm("rename"));
document.querySelector("#duplicate-dashboard").addEventListener("click", () => openDashboardForm("duplicate"));
document.querySelector("#archive-dashboard").addEventListener("click", archiveDashboard);
document.querySelector("#delete-dashboard").addEventListener("click", deleteDashboard);
document.querySelector("#show-active-dashboards").addEventListener("click", () => { showArchived = false; renderDashboardList(); });
document.querySelector("#show-archived-dashboards").addEventListener("click", () => { showArchived = true; renderDashboardList(); });
document.querySelector("#close-dashboard-form").addEventListener("click", () => elements.formDialog.close());
document.querySelector("#cancel-dashboard-form").addEventListener("click", () => elements.formDialog.close());
elements.dashboardForm.addEventListener("submit", event => { event.preventDefault(); submitDashboardForm(); });
document.querySelector("#reload-dashboard").addEventListener("click", () => loadDashboards(activeDashboard?.id));

elements.canvas.addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  const widgetId = event.target.closest(".widget")?.dataset.widgetId;
  if (action === "view-widget-sql") return openExecutedSql(activeDashboard?.dashboard.widgets.find(widget => widget.id === widgetId));
  if (action === "move-widget-earlier") return moveWidget(widgetId, -1);
  if (action === "move-widget-later") return moveWidget(widgetId, 1);
  if (action === "duplicate-widget") return duplicateWidget(widgetId);
  if (action === "delete-widget") return deleteWidget(widgetId);
  if (widgetId && !editMode) openWidgetFocus(widgetId);
});
function clearWidgetDropState() {
  for (const card of elements.canvas.querySelectorAll(".widget")) card.classList.remove("dragging", "swap-target");
}
function finishWidgetDrag() {
  const changed = dragOrderChanged;
  draggedWidgetId = null;
  dragOrderChanged = false;
  lastSwapTargetId = null;
  clearWidgetDropState();
  if (!changed || !activeDashboard) return;
  persistWidgetOrder(activeDashboard.dashboard.widgets, false);
  const cards = Array.from(elements.canvas.querySelectorAll(".widget"));
  cards.forEach((card, index) => {
    const earlier = card.querySelector('[data-action="move-widget-earlier"]');
    const later = card.querySelector('[data-action="move-widget-later"]');
    if (earlier) earlier.disabled = index === 0;
    if (later) later.disabled = index === cards.length - 1;
  });
}
elements.canvas.addEventListener("dragstart", event => {
  const card = event.target.closest(".widget");
  if (!editMode || !card || event.target.closest("button")) return event.preventDefault();
  const rect = card.getBoundingClientRect();
  draggedWidgetId = card.dataset.widgetId;
  dragCenterOffset = { x: rect.left + rect.width / 2 - event.clientX, y: rect.top + rect.height / 2 - event.clientY };
  dragOrderChanged = false;
  lastSwapTargetId = null;
  card.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", draggedWidgetId);
});
elements.canvas.addEventListener("dragover", event => {
  if (!editMode || !draggedWidgetId) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const center = { x: event.clientX + dragCenterOffset.x, y: event.clientY + dragCenterOffset.y };
  const target = Array.from(elements.canvas.querySelectorAll(".widget")).find(card => {
    if (card.dataset.widgetId === draggedWidgetId) return false;
    const rect = card.getBoundingClientRect();
    return center.x >= rect.left && center.x <= rect.right && center.y >= rect.top && center.y <= rect.bottom;
  });
  if (!target) {
    lastSwapTargetId = null;
    return;
  }
  if (target.dataset.widgetId === lastSwapTargetId) return;
  if (swapWidgets(draggedWidgetId, target.dataset.widgetId)) {
    lastSwapTargetId = target.dataset.widgetId;
    target.classList.add("swap-target");
    setTimeout(() => target.classList.remove("swap-target"), 260);
  }
});
elements.canvas.addEventListener("drop", event => {
  if (!editMode || !draggedWidgetId) return;
  event.preventDefault();
  finishWidgetDrag();
});
elements.canvas.addEventListener("dragend", finishWidgetDrag);
elements.canvas.addEventListener("keydown", event => {
  const card = event.target.closest(".widget");
  if (!card || event.target.closest("button") || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  openWidgetFocus(card.dataset.widgetId);
});
document.querySelector("#close-widget-inspector").addEventListener("click", closeWidgetInspector);
document.querySelector("#view-inspector-sql").addEventListener("click", () => openExecutedSql(activeDashboard?.dashboard.widgets.find(widget => widget.id === focusedWidgetId), true));
document.querySelector("#close-executed-sql").addEventListener("click", () => elements.sqlDialog.close());
elements.widgetFocusContent.addEventListener("click", event => {
  if (event.target.closest(".focused-widget-close")) return closeWidgetFocus();
  if (event.target.closest('[data-action="view-widget-sql"]')) return openExecutedSql(activeDashboard?.dashboard.widgets.find(widget => widget.id === focusedWidgetId));
  const metric = event.target.closest("[data-inspect-metric]");
  if (metric) inspectMetric(metric);
});
elements.widgetFocusContent.addEventListener("keydown", event => {
  const metric = event.target.closest("[data-inspect-metric]");
  if (!metric || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  inspectMetric(metric);
});
elements.workspace.addEventListener("scroll", () => {
  if (!activeDashboard || !editMode || focusedWidgetId) return;
  const mode = isMobileLayout() ? "mobile" : "desktop";
  const viewport = activeDashboard.dashboard.viewport[mode];
  if (viewport.x === elements.workspace.scrollLeft && viewport.y === elements.workspace.scrollTop) return;
  viewport.x = Math.round(elements.workspace.scrollLeft);
  viewport.y = Math.round(elements.workspace.scrollTop);
  markDashboardChanged();
}, { passive: true });
window.addEventListener("keydown", event => {
  if (event.key === "Escape" && focusedWidgetId) closeWidgetFocus();
});
window.addEventListener("beforeunload", () => { if (saveTimer) persistDashboard(); });

Promise.all([loadDashboards(), loadProfiles()]);
