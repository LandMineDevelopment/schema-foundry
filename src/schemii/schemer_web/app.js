const elements = {
  dialog: document.querySelector("#connections-dialog"),
  connectionList: document.querySelector("#connection-list"),
  connectionForm: document.querySelector("#connection-form"),
  connectionStatus: document.querySelector("#connection-status"),
  relationList: document.querySelector("#relation-list"),
  relationStatus: document.querySelector("#relation-browser-status"),
  relationDetail: document.querySelector("#relation-detail"),
  widgetEditor: document.querySelector("#widget-editor-dialog"),
  widgetEditorName: document.querySelector("#widget-editor-name"),
  widgetSourceProfile: document.querySelector("#widget-source-profile"),
  widgetSourceNamespace: document.querySelector("#widget-source-namespace"),
  widgetSourceEditor: document.querySelector("#widget-source-editor"),
  widgetQueryEditor: document.querySelector("#widget-query-editor"),
  widgetQueryFields: document.querySelector("#widget-query-fields"),
  widgetQueryHeading: document.querySelector("#widget-query-heading"),
  widgetQueryCopy: document.querySelector("#widget-query-copy"),
  widgetQueryLimit: document.querySelector("#widget-query-limit"),
  widgetQueryLimitField: document.querySelector("#widget-query-limit-field"),
  widgetQueryStatus: document.querySelector("#widget-query-status"),
  sourceSummary: document.querySelector(".source-summary"),
  sourceName: document.querySelector("#source-name"),
  sourceDetail: document.querySelector("#source-detail"),
  namespaceSelect: document.querySelector("#namespace-select"),
  workspace: document.querySelector(".dashboard-workspace"),
  canvas: document.querySelector("#dashboard-canvas"),
  dashboardList: document.querySelector("#dashboard-list"),
  mobileDashboardSelect: document.querySelector("#mobile-dashboard-select"),
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
let editedWidgetId = null;
let relationInspectionGeneration = 0;
let relationCatalogGeneration = 0;
let dashboards = [];
let activeDashboard = null;
let editMode = false;
let showArchived = false;
let saveTimer = null;
let saveTimerDashboardId = null;
let saveQueue = Promise.resolve();
let changeGeneration = 0;
let dashboardConflict = false;
let formAction = "create";
let focusedWidgetId = null;
let focusedSourceRect = null;
let focusedSourceElement = null;
let focusAnimation = null;
let draggedWidgetId = null;
let dragCenterOffset = { x: 0, y: 0 };
let dragOrderChanged = false;
let lastSwapTargetId = null;
let sourceVerificationGeneration = 0;
let queryExecutionGeneration = 0;
let widgetQueryDraft = null;
let widgetEditorSection = "source";
let widgetEditorGeneration = 0;
let widgetQueryApplySession = null;
const sourceVerification = new Map();
const widgetQueryResults = new Map();
const widgetQueryExecutionTokens = new Map();
const executedSqlByResult = new Map();
const postgres = window.SchemiiShared.createPostgresClient({
  getToken: () => sessionToken,
  setToken: token => { sessionToken = token; }
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function invalidateWidgetRuntime(widgetId) {
  widgetQueryExecutionTokens.set(`${widgetId}:publish`, {});
  widgetQueryExecutionTokens.set(`${widgetId}:draft`, {});
  widgetQueryResults.delete(widgetId);
  executedSqlByResult.delete(`${widgetId}:widget`);
  sourceVerification.delete(widgetId);
  sourceVerificationGeneration += 1;
  queryExecutionGeneration += 1;
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

function exactSourceIdentity(descriptor) {
  return {
    profileId: descriptor.profileId,
    database: descriptor.database,
    namespace: descriptor.namespace,
    relation: descriptor.relation,
    kind: descriptor.kind,
    fingerprint: descriptor.fingerprint,
    columns: descriptor.columns.map(column => ({
      name: column.name, type: column.type, nullable: column.nullable, ordinal: column.ordinal
    }))
  };
}

function sourceChangeMessage(result) {
  if (result.status === "missing") return `Saved relation ${result.database}.${result.namespace}.${result.relation} no longer exists.`;
  const details = [];
  if (result.expectedKind !== result.currentKind) details.push(`kind changed to ${result.currentKind}`);
  if (result.missingColumns?.length) details.push(`missing columns: ${result.missingColumns.join(", ")}`);
  if (result.changedColumns?.length) details.push(`changed columns: ${result.changedColumns.map(column => column.name).join(", ")}`);
  if (result.addedColumns?.length) details.push(`added columns: ${result.addedColumns.join(", ")}`);
  return details.length ? `Source changed (${details.join("; ")}). Reselect it to accept the live catalog.` : "Source definition changed. Reselect it to accept the live catalog.";
}

function renderSourceChangeNotice(verification) {
  if (verification?.state !== "error") return;
  const notice = document.createElement("section");
  notice.className = "relation-change-notice";
  const title = document.createElement("strong");
  title.textContent = verification.code === "relation_missing" ? "Saved source is missing" : "Saved source changed";
  const copy = document.createElement("p");
  copy.textContent = verification.message;
  notice.append(title, copy);
  elements.relationDetail.prepend(notice);
}

function renderRelationPreview(result, container) {
  const table = document.createElement("table");
  table.className = "relation-preview-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const column of result.columns) {
    const cell = document.createElement("th");
    cell.textContent = column.name;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const values of result.rows) {
    const row = document.createElement("tr");
    for (const column of result.columns) {
      const cell = document.createElement("td");
      const value = values[column.name];
      cell.textContent = value === null ? "NULL" : typeof value === "object" ? JSON.stringify(value) : String(value);
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  container.replaceChildren(table);
}

function nextQueryItemId(prefix) {
  const random = crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : Math.random().toString(16).slice(2);
  return `${prefix}_${random}`;
}

function defaultWidgetQuery() {
  return {
    version: 2,
    dimensions: [],
    measures: [{ id: nextQueryItemId("measure"), label: "Row count", column: null, aggregation: "count_rows", distinct: false, nullBehavior: "preserve", numberFormat: { style: "integer" } }],
    filters: [],
    sort: [],
    limit: 100
  };
}

function numericPostgresType(type) {
  return ["smallint", "integer", "bigint", "decimal", "numeric", "real", "double precision", "smallserial", "serial", "bigserial"].some(prefix => type.toLowerCase() === prefix || type.toLowerCase().startsWith(`${prefix}(`));
}

function sumPostgresType(type) {
  return numericPostgresType(type) || ["interval", "money"].includes(type.toLowerCase());
}

function averagePostgresType(type) {
  return numericPostgresType(type) || type.toLowerCase() === "interval";
}

function orderablePostgresType(type) {
  return !["boolean", "json", "jsonb", "xml", "box", "circle", "line", "lseg", "path", "point", "polygon"].includes(type.toLowerCase()) && !/^bit(?: varying)?(?:\([^)]*\))?$/.test(type.toLowerCase());
}

function comparablePostgresType(type) {
  return !["json", "xml", "box", "circle", "line", "lseg", "path", "point", "polygon"].includes(type.toLowerCase());
}

function textPostgresType(type) {
  return /^(text|character varying(?:\([^)]*\))?|character(?:\([^)]*\))?|varchar(?:\([^)]*\))?|char(?:\([^)]*\))?|citext|name)$/i.test(type);
}

function temporalPostgresType(type) {
  return /^(date|time(?:stamp)?(?: with(?:out)? time zone)?|interval)$/i.test(type);
}

function filterInputType(type) {
  const normalized = type.toLowerCase();
  if (normalized === "date") return "date";
  if (normalized.startsWith("timestamp")) return "datetime-local";
  if (normalized.startsWith("time")) return "text";
  if (numericPostgresType(type)) return "number";
  return "text";
}

function queryFilterInput(value, onChange, columnType) {
  const inputType = filterInputType(columnType);
  if (inputType === "date") return queryCalendarInput(value, onChange);
  if (inputType === "datetime-local") return queryCalendarInput(value, onChange, true);
  return queryInput(value, onChange, inputType);
}

function filterOptionsForColumn(column) {
  const nulls = [["is_null", "Is NULL"], ["is_not_null", "Is not NULL"]];
  const equality = [["eq", "Equals"], ["neq", "Does not equal"]];
  if (!comparablePostgresType(column.type)) return nulls;
  if (textPostgresType(column.type)) return equality.concat([["contains", "Contains"], ["starts_with", "Starts with"], ["ends_with", "Ends with"], ["like", "Matches LIKE pattern"], ["in", "In list"], ["not_in", "Not in list"]], nulls);
  if (numericPostgresType(column.type) || temporalPostgresType(column.type)) return equality.concat([["gt", "Greater than"], ["gte", "Greater than or equal"], ["lt", "Less than"], ["lte", "Less than or equal"], ["between", "Between"], ["in", "In list"], ["not_in", "Not in list"]], nulls);
  if (column.type.toLowerCase() === "boolean") return equality.concat(nulls);
  return equality.concat([["in", "In list"], ["not_in", "Not in list"]], nulls);
}

function queryLabel(text, control) {
  const label = document.createElement("label");
  label.append(document.createTextNode(text), control);
  return label;
}

function queryInput(value, onChange, type = "text") {
  const input = document.createElement("input");
  input.type = type;
  if (["number", "datetime-local", "time"].includes(type)) input.step = "any";
  input.value = value ?? "";
  input.addEventListener("change", () => onChange(input.value));
  return input;
}

function calendarValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function queryCalendarInput(value, onChange, includeTime = false) {
  const control = document.createElement("div");
  control.className = "query-calendar-control";
  const input = queryInput(value, onChange);
  input.placeholder = includeTime ? "YYYY-MM-DDTHH:MM" : "YYYY-MM-DD";
  input.inputMode = "numeric";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "query-calendar-toggle";
  toggle.textContent = "Calendar";
  toggle.setAttribute("aria-label", "Open calendar");
  const popup = document.createElement("section");
  popup.className = "query-calendar-popup";
  popup.hidden = true;
  let selected = /^\d{4}-\d{2}-\d{2}/.test(input.value) ? new Date(`${input.value.slice(0, 10)}T12:00:00`) : new Date();
  if (Number.isNaN(selected.getTime())) selected = new Date();
  let visibleMonth = new Date(selected.getFullYear(), selected.getMonth(), 1);

  const renderCalendar = () => {
    popup.replaceChildren();
    const header = document.createElement("header");
    const previous = document.createElement("button");
    previous.type = "button";
    previous.textContent = "<";
    previous.setAttribute("aria-label", "Previous month");
    const month = document.createElement("strong");
    month.textContent = visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = ">";
    next.setAttribute("aria-label", "Next month");
    previous.addEventListener("click", () => { visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1); renderCalendar(); });
    next.addEventListener("click", () => { visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1); renderCalendar(); });
    header.append(previous, month, next);
    const grid = document.createElement("div");
    grid.className = "query-calendar-grid";
    for (const weekday of ["S", "M", "T", "W", "T", "F", "S"]) {
      const label = document.createElement("span");
      label.textContent = weekday;
      grid.append(label);
    }
    const first = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth(), 1 - visibleMonth.getDay());
    const selectedDay = input.value.slice(0, 10);
    for (let index = 0; index < 42; index += 1) {
      const day = new Date(first.getFullYear(), first.getMonth(), first.getDate() + index);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = day.getDate();
      button.classList.toggle("outside", day.getMonth() !== visibleMonth.getMonth());
      button.classList.toggle("selected", calendarValue(day) === selectedDay);
      button.addEventListener("click", () => {
        const time = includeTime ? input.value.match(/T(\d{2}:\d{2})/)?.[1] ?? "00:00" : "";
        input.value = calendarValue(day) + (includeTime ? `T${time}` : "");
        onChange(input.value);
        popup.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      });
      grid.append(button);
    }
    const footer = document.createElement("footer");
    if (includeTime) {
      const time = queryInput(input.value.match(/T(\d{2}:\d{2})/)?.[1] ?? "00:00", value => {
        const date = /^\d{4}-\d{2}-\d{2}/.test(input.value) ? input.value.slice(0, 10) : calendarValue(selected);
        input.value = `${date}T${value}`;
        onChange(input.value);
      });
      time.className = "query-calendar-time";
      time.placeholder = "HH:MM";
      time.maxLength = 5;
      footer.append(queryLabel("Time", time));
    }
    const actions = document.createElement("div");
    const today = document.createElement("button");
    today.type = "button";
    today.textContent = "Today";
    today.addEventListener("click", () => {
      const now = new Date();
      input.value = calendarValue(now) + (includeTime ? `T${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}` : "");
      onChange(input.value);
      popup.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    });
    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => { input.value = ""; onChange(""); popup.hidden = true; toggle.setAttribute("aria-expanded", "false"); });
    actions.append(clear, today);
    footer.append(actions);
    popup.append(header, grid, footer);
  };
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    popup.hidden = !popup.hidden;
    toggle.setAttribute("aria-expanded", String(!popup.hidden));
    if (!popup.hidden) {
      const typed = /^\d{4}-\d{2}-\d{2}/.test(input.value) ? new Date(`${input.value.slice(0, 10)}T12:00:00`) : null;
      if (typed && !Number.isNaN(typed.getTime())) visibleMonth = new Date(typed.getFullYear(), typed.getMonth(), 1);
      renderCalendar();
    }
  });
  renderCalendar();
  control.append(input, toggle, popup);
  return control;
}

function queryTextarea(value, onChange) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.rows = 3;
  textarea.addEventListener("change", () => onChange(textarea.value));
  return textarea;
}

function querySelect(options, value, onChange) {
  const select = document.createElement("select");
  for (const [optionValue, label] of options) select.append(new Option(label, optionValue));
  select.value = value ?? "";
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function queryGroup(title, copy, addLabel, onAdd) {
  const section = document.createElement("section");
  section.className = "query-editor-group";
  const header = document.createElement("header");
  const heading = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = copy;
  heading.append(strong, paragraph);
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button button-ghost";
  add.textContent = addLabel;
  add.addEventListener("click", onAdd);
  header.append(heading, add);
  const rows = document.createElement("div");
  rows.className = "query-editor-rows";
  section.append(header, rows);
  return [section, rows, add];
}

function queryRow(item, collection, onRemove = null) {
  const row = document.createElement("div");
  row.className = "query-editor-row";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "query-remove";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => {
    collection.splice(collection.indexOf(item), 1);
    onRemove?.(item);
    renderWidgetQueryDraft();
  });
  return [row, remove];
}

function widgetQueryApplyActive() {
  return widgetQueryApplySession?.generation === widgetEditorGeneration && widgetQueryApplySession.widgetId === editedWidgetId;
}

function renderWidgetQueryDraft() {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === editedWidgetId);
  const columns = widget?.configuration?.source?.columns ?? [];
  const applying = widgetQueryApplyActive();
  elements.widgetEditorName.disabled = applying;
  document.querySelector("#reset-widget-query").disabled = applying || !widgetQueryDraft;
  elements.widgetQueryLimit.disabled = applying || !widgetQueryDraft;
  if (!widgetQueryDraft || !columns.length) {
    elements.widgetQueryFields.replaceChildren();
    elements.widgetQueryStatus.textContent = "Assign a current source before configuring a query.";
    document.querySelector("#apply-widget-query").disabled = true;
    return;
  }
  document.querySelector("#apply-widget-query").disabled = false;
  const columnOptions = columns.map(column => [column.name, `${column.name} · ${column.type}`]);
  const dimensionColumns = columns.filter(column => comparablePostgresType(column.type));
  const [dimensions, dimensionRows, addDimension] = queryGroup("Groupings", "Check the columns that define each aggregate result row.", "", () => {});
  addDimension.remove();
  const groupingPicker = document.createElement("details");
  groupingPicker.className = "grouping-picker";
  const groupingSummary = document.createElement("summary");
  groupingSummary.textContent = widgetQueryDraft.dimensions.length ? `${widgetQueryDraft.dimensions.length} grouping column${widgetQueryDraft.dimensions.length === 1 ? "" : "s"} selected` : "Choose grouping columns";
  const groupingOptions = document.createElement("div");
  for (const column of dimensionColumns) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = widgetQueryDraft.dimensions.some(item => item.column === column.name);
    checkbox.disabled = !checkbox.checked && widgetQueryDraft.dimensions.length >= 32;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) widgetQueryDraft.dimensions.push({ id: nextQueryItemId("dimension"), label: column.name, column: column.name });
      else {
        const removed = widgetQueryDraft.dimensions.find(item => item.column === column.name);
        widgetQueryDraft.dimensions = widgetQueryDraft.dimensions.filter(item => item !== removed);
        if (removed) widgetQueryDraft.sort = widgetQueryDraft.sort.filter(sort => sort.targetId !== removed.id);
      }
      widgetQueryDraft.dimensions.sort((left, right) => columns.findIndex(item => item.name === left.column) - columns.findIndex(item => item.name === right.column));
      groupingSummary.textContent = widgetQueryDraft.dimensions.length ? `${widgetQueryDraft.dimensions.length} grouping column${widgetQueryDraft.dimensions.length === 1 ? "" : "s"} selected` : "Choose grouping columns";
      for (const option of groupingOptions.querySelectorAll('input[type="checkbox"]')) option.disabled = !option.checked && widgetQueryDraft.dimensions.length >= 32;
    });
    const copy = document.createElement("span");
    copy.textContent = column.name;
    const type = document.createElement("small");
    type.textContent = column.type;
    label.append(checkbox, copy, type);
    groupingOptions.append(label);
  }
  groupingPicker.append(groupingSummary, groupingOptions);
  groupingPicker.addEventListener("toggle", () => { if (!groupingPicker.open) renderWidgetQueryDraft(); });
  dimensionRows.append(groupingPicker);
  const [measures, measureRows, addMeasure] = queryGroup("Measures", "One or more aggregate values calculated for every grouping.", "+ Measure", () => {
    widgetQueryDraft.measures.push({ id: nextQueryItemId("measure"), label: "Row count", column: null, aggregation: "count_rows", distinct: false, nullBehavior: "preserve", numberFormat: { style: "integer" } });
    renderWidgetQueryDraft();
  });
  addMeasure.disabled = widgetQueryDraft.measures.length >= 32;
  for (const item of widgetQueryDraft.measures) {
    const [row, remove] = queryRow(item, widgetQueryDraft.measures, removed => { widgetQueryDraft.sort = widgetQueryDraft.sort.filter(sort => sort.targetId !== removed.id); });
    remove.disabled = widgetQueryDraft.measures.length === 1;
    const aggregationOptions = [["count_rows", "Count rows"], ["count", "Count column"]];
    if (columns.some(columnItem => sumPostgresType(columnItem.type))) aggregationOptions.push(["sum", "Sum"]);
    if (columns.some(columnItem => averagePostgresType(columnItem.type))) aggregationOptions.push(["average", "Average"]);
    if (columns.some(columnItem => orderablePostgresType(columnItem.type))) aggregationOptions.push(["minimum", "Minimum"], ["maximum", "Maximum"]);
    const eligibleColumns = item.aggregation === "count_rows" ? columns : item.aggregation === "sum" ? columns.filter(columnItem => sumPostgresType(columnItem.type)) : item.aggregation === "average" ? columns.filter(columnItem => averagePostgresType(columnItem.type)) : ["minimum", "maximum"].includes(item.aggregation) ? columns.filter(columnItem => orderablePostgresType(columnItem.type)) : columns;
    const column = querySelect(eligibleColumns.map(columnItem => [columnItem.name, `${columnItem.name} · ${columnItem.type}`]), item.column ?? "", value => { item.column = value || null; renderWidgetQueryDraft(); });
    column.disabled = item.aggregation === "count_rows";
    const distinct = document.createElement("input");
    distinct.type = "checkbox";
    distinct.checked = item.distinct;
    distinct.disabled = item.aggregation !== "count" || !comparablePostgresType(columns.find(columnItem => columnItem.name === item.column)?.type ?? "");
    if (distinct.disabled && item.distinct) item.distinct = distinct.checked = false;
    distinct.addEventListener("change", () => { item.distinct = distinct.checked; });
    const formatOptions = [["auto", "Automatic"], ["integer", "Integer"], ["decimal", "Decimal"], ["currency", "Currency"], ["percent", "Percent"]];
    const selectedColumn = columns.find(columnItem => columnItem.name === item.column);
    const zeroAllowed = ["sum", "average", "minimum", "maximum"].includes(item.aggregation) && numericPostgresType(selectedColumn?.type ?? "");
    if (!zeroAllowed && item.nullBehavior === "zero") item.nullBehavior = "preserve";
    const currency = queryInput(item.numberFormat.currency ?? "USD", value => { item.numberFormat.currency = value.trim().toUpperCase(); });
    currency.maxLength = 3;
    currency.disabled = item.numberFormat.style !== "currency";
    const fractionDigits = queryInput(item.numberFormat.fractionDigits ?? 2, value => { item.numberFormat.fractionDigits = Number(value); }, "number");
    fractionDigits.min = "0";
    fractionDigits.max = "6";
    fractionDigits.disabled = !["decimal", "currency", "percent"].includes(item.numberFormat.style);
    row.append(
      queryLabel("Label", queryInput(item.label, value => { item.label = value; })),
      queryLabel("Aggregation", querySelect(aggregationOptions, item.aggregation, value => {
        item.aggregation = value;
        if (value === "count_rows") item.column = null;
        else if (value === "sum" && !columns.some(columnItem => columnItem.name === item.column && sumPostgresType(columnItem.type))) item.column = columns.find(columnItem => sumPostgresType(columnItem.type))?.name ?? null;
        else if (value === "average" && !columns.some(columnItem => columnItem.name === item.column && averagePostgresType(columnItem.type))) item.column = columns.find(columnItem => averagePostgresType(columnItem.type))?.name ?? null;
        else if (["minimum", "maximum"].includes(value) && !columns.some(columnItem => columnItem.name === item.column && orderablePostgresType(columnItem.type))) item.column = columns.find(columnItem => orderablePostgresType(columnItem.type))?.name ?? null;
        else if (!item.column) item.column = columns[0]?.name ?? null;
        if (value !== "count") item.distinct = false;
        renderWidgetQueryDraft();
      })),
      queryLabel("Column", column),
      queryLabel("Distinct", distinct),
      queryLabel("Null result", querySelect(zeroAllowed ? [["preserve", "Preserve NULL"], ["zero", "Show zero"]] : [["preserve", "Preserve NULL"]], item.nullBehavior, value => { item.nullBehavior = value; })),
      queryLabel("Number format", querySelect(formatOptions, item.numberFormat.style, value => {
        item.numberFormat = value === "currency" ? { style: value, currency: "USD", fractionDigits: 2 } : ["decimal", "percent"].includes(value) ? { style: value, fractionDigits: 2 } : { style: value };
        renderWidgetQueryDraft();
      })),
      queryLabel("Currency", currency),
      queryLabel("Decimal places", fractionDigits),
      remove
    );
    measureRows.append(row);
  }
  const totalConditions = widgetQueryDraft.filters.reduce((total, group) => total + group.conditions.length, 0);
  const [filters, filterRows, addFilterGroup] = queryGroup("Filters", "Conditions inside a group use AND. Groups are combined with OR.", "+ OR group", () => {
    widgetQueryDraft.filters.push({ id: nextQueryItemId("filter_group"), conditions: [{ id: nextQueryItemId("filter"), column: columns[0].name, operator: filterOptionsForColumn(columns[0])[0][0], values: [""] }] });
    renderWidgetQueryDraft();
  });
  addFilterGroup.disabled = widgetQueryDraft.filters.length >= 32 || totalConditions >= 64;
  widgetQueryDraft.filters.forEach((group, groupIndex) => {
    if (groupIndex) {
      const separator = document.createElement("div");
      separator.className = "filter-group-join";
      separator.textContent = "OR";
      separator.setAttribute("aria-hidden", "true");
      filterRows.append(separator);
    }
    const groupElement = document.createElement("section");
    groupElement.className = "filter-condition-group";
    groupElement.setAttribute("aria-label", `OR filter group ${groupIndex + 1}; conditions use AND`);
    const groupHeader = document.createElement("header");
    const groupTitle = document.createElement("h3");
    groupTitle.textContent = `Condition group ${groupIndex + 1}`;
    const removeGroup = document.createElement("button");
    removeGroup.type = "button";
    removeGroup.className = "query-remove";
    removeGroup.textContent = "Remove group";
    removeGroup.addEventListener("click", () => { widgetQueryDraft.filters.splice(groupIndex, 1); renderWidgetQueryDraft(); });
    groupHeader.append(groupTitle, removeGroup);
    const conditions = document.createElement("div");
    conditions.className = "filter-conditions";
    group.conditions.forEach((item, conditionIndex) => {
      if (conditionIndex) {
        const separator = document.createElement("div");
        separator.className = "filter-condition-join";
        separator.textContent = "AND";
        separator.setAttribute("aria-hidden", "true");
        conditions.append(separator);
      }
      const [row, remove] = queryRow(item, group.conditions);
      remove.disabled = group.conditions.length === 1;
      const filterColumn = columns.find(columnItem => columnItem.name === item.column) ?? columns[0];
      const filterOptions = filterOptionsForColumn(filterColumn);
      if (!filterOptions.some(option => option[0] === item.operator)) {
        item.operator = filterOptions[0][0];
        item.values = ["is_null", "is_not_null"].includes(item.operator) ? [] : [""];
      }
      const listOperator = ["in", "not_in"].includes(item.operator);
      const betweenOperator = item.operator === "between";
      let valueControl;
      if (listOperator) {
        valueControl = queryLabel("Values (one per line)", queryTextarea(item.values.join("\n"), value => { item.values = value.split("\n").map(part => part.trim()).filter(Boolean); }));
      } else if (betweenOperator) {
        valueControl = document.createElement("div");
        valueControl.className = "filter-between-values";
        valueControl.append(
          queryLabel("From", queryFilterInput(item.values[0] ?? "", value => { item.values[0] = value; }, filterColumn.type)),
          queryLabel("To", queryFilterInput(item.values[1] ?? "", value => { item.values[1] = value; }, filterColumn.type))
        );
      } else {
        const input = queryFilterInput(item.values[0] ?? "", value => { item.values = [value]; }, filterColumn.type);
        for (const control of input.matches("input") ? [input] : input.querySelectorAll("input, button")) control.disabled = ["is_null", "is_not_null"].includes(item.operator);
        valueControl = queryLabel("Value", input);
      }
      row.append(
        queryLabel("Column", querySelect(columnOptions, item.column, value => {
          item.column = value;
          const nextColumn = columns.find(columnItem => columnItem.name === value);
          const nextOptions = filterOptionsForColumn(nextColumn);
          if (!nextOptions.some(option => option[0] === item.operator)) item.operator = nextOptions[0][0];
          item.values = ["is_null", "is_not_null"].includes(item.operator) ? [] : item.operator === "between" ? ["", ""] : [""];
          renderWidgetQueryDraft();
        })),
        queryLabel("Operator", querySelect(filterOptions, item.operator, value => { item.operator = value; item.values = ["is_null", "is_not_null"].includes(value) ? [] : value === "between" ? [item.values[0] ?? "", item.values[1] ?? ""] : ["in", "not_in"].includes(value) ? item.values.length ? item.values : [""] : [item.values[0] ?? ""]; renderWidgetQueryDraft(); })),
        valueControl,
        remove
      );
      conditions.append(row);
    });
    const addCondition = document.createElement("button");
    addCondition.type = "button";
    addCondition.className = "button button-ghost filter-add-condition";
    addCondition.textContent = "+ AND condition";
    addCondition.disabled = totalConditions >= 64;
    addCondition.addEventListener("click", () => {
      group.conditions.push({ id: nextQueryItemId("filter"), column: columns[0].name, operator: filterOptionsForColumn(columns[0])[0][0], values: [""] });
      renderWidgetQueryDraft();
    });
    groupElement.append(groupHeader, conditions, addCondition);
    filterRows.append(groupElement);
  });
  const unsortedDimensions = widgetQueryDraft.dimensions.filter(item => !widgetQueryDraft.sort.some(sort => sort.targetId === item.id));
  const unsortedMeasures = widgetQueryDraft.measures.filter(item => !widgetQueryDraft.sort.some(sort => sort.targetId === item.id));
  const [sorting, sortRows, addSort] = queryGroup("Sort", "Sort rows are applied top to bottom. Unlisted grouping columns remain automatic tie-breakers.", "+ Sort column", () => {
    const first = unsortedDimensions[0] ? ["dimension", unsortedDimensions[0].id] : ["measure", unsortedMeasures[0]?.id];
    if (first[1]) widgetQueryDraft.sort.push({ targetKind: first[0], targetId: first[1], direction: "asc", nulls: "last" });
    renderWidgetQueryDraft();
  });
  addSort.disabled = !unsortedDimensions.length && !unsortedMeasures.length;
  widgetQueryDraft.sort.forEach((item, sortIndex) => {
    const [row, remove] = queryRow(item, widgetQueryDraft.sort);
    const targets = widgetQueryDraft.dimensions.filter(target => target.id === item.targetId || !widgetQueryDraft.sort.some(sort => sort !== item && sort.targetId === target.id)).map(target => [`dimension:${target.id}`, `Grouping · ${target.label}`]).concat(widgetQueryDraft.measures.filter(target => target.id === item.targetId || !widgetQueryDraft.sort.some(sort => sort !== item && sort.targetId === target.id)).map(target => [`measure:${target.id}`, `Measure · ${target.label}`]));
    const priority = document.createElement("div");
    priority.className = "sort-priority";
    const priorityLabel = document.createElement("span");
    priorityLabel.textContent = `Order ${sortIndex + 1}`;
    for (const [label, offset] of [["Up", -1], ["Down", 1]]) {
      const move = document.createElement("button");
      move.type = "button";
      move.className = "sort-order-button";
      move.textContent = label;
      move.setAttribute("aria-label", `Move sort column ${label.toLowerCase()}`);
      move.disabled = sortIndex + offset < 0 || sortIndex + offset >= widgetQueryDraft.sort.length;
      move.addEventListener("click", () => {
        widgetQueryDraft.sort.splice(sortIndex, 1);
        widgetQueryDraft.sort.splice(sortIndex + offset, 0, item);
        renderWidgetQueryDraft();
      });
      priority.append(move);
    }
    priority.prepend(priorityLabel);
    row.append(
      queryLabel("Result field", querySelect(targets, `${item.targetKind}:${item.targetId}`, value => { [item.targetKind, item.targetId] = value.split(":"); })),
      queryLabel("Direction", querySelect([["asc", "Ascending"], ["desc", "Descending"]], item.direction, value => { item.direction = value; })),
      queryLabel("NULL placement", querySelect([["last", "NULLS LAST"], ["first", "NULLS FIRST"]], item.nulls, value => { item.nulls = value; })),
      priority,
      remove
    );
    sortRows.append(row);
  });
  const views = {
    query: { heading: "Groupings & Measures", copy: "Choose result groupings and aggregate measures.", sections: [dimensions, measures] },
    filters: { heading: "Filters", copy: "Build AND conditions inside separate OR groups.", sections: [filters] },
    sort: { heading: "Sort & Limit", copy: "Control stable result ordering and the maximum returned rows.", sections: [sorting] }
  };
  const view = views[widgetEditorSection] ?? views.query;
  elements.widgetQueryHeading.textContent = view.heading;
  elements.widgetQueryCopy.textContent = view.copy;
  elements.widgetQueryFields.replaceChildren(...view.sections);
  elements.widgetQueryLimit.value = widgetQueryDraft.limit;
  elements.widgetQueryLimitField.hidden = widgetEditorSection !== "sort";
  elements.widgetQueryStatus.textContent = "Name and source save automatically. Query changes remain local until applied.";
  document.querySelector("#apply-widget-query").disabled = applying;
  if (applying) for (const control of elements.widgetQueryEditor.querySelectorAll("button, input, select, textarea")) control.disabled = true;
}

function showWidgetEditorSection(section, activeButton = null) {
  const query = section !== "source";
  widgetEditorSection = section;
  elements.widgetSourceEditor.hidden = query;
  elements.widgetQueryEditor.hidden = !query;
  const buttons = elements.widgetEditor.querySelectorAll("[data-editor-section]");
  const selected = activeButton ?? Array.from(buttons).find(button => button.dataset.editorSection === section);
  for (const button of buttons) {
    const active = button === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  if (query) renderWidgetQueryDraft();
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
  for (const label of ["#", "Column", "PostgreSQL type", "Nullability", "Suggested roles"]) {
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
    const suggestionCell = document.createElement("td");
    const suggestions = document.createElement("div");
    suggestions.className = "column-suggestions";
    for (const suggestion of column.suggestions) {
      const badge = document.createElement("span");
      badge.textContent = suggestion;
      suggestions.append(badge);
    }
    if (!column.suggestions.length) suggestions.textContent = "None";
    suggestionCell.append(suggestions);
    row.append(suggestionCell);
    body.append(row);
  }
  table.append(head, body);
  const preview = document.createElement("section");
  preview.className = "relation-preview";
  const previewHeader = document.createElement("header");
  const previewTitle = document.createElement("strong");
  previewTitle.textContent = "Source rows";
  const previewButton = document.createElement("button");
  previewButton.type = "button";
  previewButton.className = "button button-ghost";
  previewButton.textContent = "Preview 20 rows";
  previewHeader.append(previewTitle, previewButton);
  const previewStatus = document.createElement("p");
  previewStatus.textContent = "Read-only preview; row order is not guaranteed.";
  const previewData = document.createElement("div");
  previewData.className = "relation-preview-data";
  previewData.tabIndex = 0;
  previewData.setAttribute("role", "region");
  previewData.setAttribute("aria-label", "Source row preview");
  previewButton.addEventListener("click", async () => {
    previewButton.disabled = true;
    previewStatus.textContent = "Loading verified source rows...";
    try {
      const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(descriptor.profileId)}/relation/preview`, {
        method: "POST",
        body: JSON.stringify({ source: exactSourceIdentity(descriptor), offset: 0, limit: 20 })
      });
      renderRelationPreview(result, previewData);
      previewStatus.textContent = `Showing ${result.rows.length} row${result.rows.length === 1 ? "" : "s"}${result.hasMore ? "; more rows are available" : ""}. Row order is not guaranteed.`;
    } catch (error) {
      previewData.replaceChildren();
      previewStatus.textContent = error.message;
    } finally {
      previewButton.disabled = false;
    }
  });
  preview.append(previewHeader, previewStatus, previewData);
  const assignment = document.createElement("div");
  assignment.className = "relation-assignment";
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === editedWidgetId);
  const assignmentLabel = document.createElement("strong");
  assignmentLabel.textContent = widget ? `Source for ${widget.title}` : "Widget unavailable";
  const actions = document.createElement("div");
  const assign = document.createElement("button");
  assign.type = "button";
  assign.className = "button button-primary";
  assign.textContent = "Assign source";
  assign.disabled = !editMode || !widget;
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "button button-ghost";
  clear.textContent = "Clear source";
  clear.disabled = !editMode || !widget?.configuration?.source;
  const assignmentStatus = document.createElement("span");
  assignmentStatus.textContent = "";
  const updateClearState = () => {
    clear.disabled = !editMode || !widget?.configuration?.source;
  };
  assign.addEventListener("click", () => {
    if (!editMode || !widget) return;
    const source = exactSourceIdentity(descriptor);
    const sameSource = JSON.stringify(widget.configuration?.source) === JSON.stringify(source);
    const savedQuery = sameSource ? widget.configuration?.query : null;
    widget.configuration = { source, ...(savedQuery ? { query: savedQuery } : {}) };
    widgetQueryDraft = clone(savedQuery ?? defaultWidgetQuery());
    if (!sameSource) {
      invalidateWidgetRuntime(widget.id);
    }
    sourceVerification.set(widget.id, { state: "verified" });
    assignmentStatus.textContent = `Assigned to ${widget.title}.`;
    markDashboardChanged(true);
    updateClearState();
  });
  clear.addEventListener("click", () => {
    if (!editMode || !widget?.configuration?.source) return;
    widget.configuration = {};
    widgetQueryDraft = null;
    invalidateWidgetRuntime(widget.id);
    assignmentStatus.textContent = `Cleared source from ${widget.title}.`;
    markDashboardChanged(true);
    updateClearState();
  });
  actions.append(assign, clear, assignmentStatus);
  assignment.append(assignmentLabel, actions);
  elements.relationDetail.replaceChildren(header, fingerprintLabel, fingerprint, table, preview, assignment);
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
    renderRelationDetail(descriptor);
  } catch (error) {
    if (generation !== relationInspectionGeneration) return;
    elements.relationStatus.textContent = error.message;
  }
}

async function verifyDashboardSources() {
  const dashboardId = activeDashboard?.id;
  const generation = ++sourceVerificationGeneration;
  sourceVerification.clear();
  widgetQueryResults.clear();
  widgetQueryExecutionTokens.clear();
  for (const key of executedSqlByResult.keys()) {
    if (key.endsWith(":widget")) executedSqlByResult.delete(key);
  }
  const sourcedWidgets = activeDashboard?.dashboard.widgets.filter(widget => widget.configuration?.source) ?? [];
  if (!sourcedWidgets.length) {
    widgetQueryResults.clear();
    return;
  }
  for (const widget of sourcedWidgets) sourceVerification.set(widget.id, { state: "checking" });
  renderDashboard();
  const uniqueSources = new Map(sourcedWidgets.map(widget => [JSON.stringify(widget.configuration.source), widget.configuration.source]));
  const results = new Map();
  await Promise.all(Array.from(uniqueSources, async ([key, source]) => {
    try {
      const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(source.profileId)}/relation/verify`, {
        method: "POST", body: JSON.stringify({ source })
      });
      results.set(key, result.matches ? { state: "verified" } : {
        state: "error", code: result.status === "missing" ? "relation_missing" : "relation_changed",
        message: sourceChangeMessage(result), details: result
      });
    } catch (error) {
      results.set(key, { state: "error", code: error.code || "source_unavailable", message: error.message });
    }
  }));
  if (generation !== sourceVerificationGeneration || activeDashboard?.id !== dashboardId) return;
  for (const widget of sourcedWidgets) {
    const sourceKey = JSON.stringify(widget.configuration.source);
    const currentWidget = activeDashboard?.dashboard.widgets.find(item => item.id === widget.id);
    if (currentWidget !== widget || JSON.stringify(currentWidget.configuration?.source) !== sourceKey) continue;
    sourceVerification.set(widget.id, results.get(sourceKey));
  }
  renderDashboard();
  executeDashboardQueries();
}

async function loadRelations(profile, namespace) {
  const generation = ++relationCatalogGeneration;
  relationInspectionGeneration += 1;
  selectedRelationIdentity = null;
  elements.relationList.replaceChildren();
  elements.relationDetail.hidden = true;
  if (!profile || !namespace) {
    elements.relationStatus.textContent = "Select a connection and namespace.";
    return null;
  }
  elements.relationStatus.textContent = `Loading ${profile.dbname}.${namespace}...`;
  try {
    const catalog = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profile.id)}/relations?database=${encodeURIComponent(profile.dbname)}&namespace=${encodeURIComponent(namespace)}`);
    if (generation !== relationCatalogGeneration) return;
    elements.relationStatus.textContent = `${catalog.relations.length} supported relation${catalog.relations.length === 1 ? "" : "s"} in ${catalog.database}.${catalog.namespace}.`;
    renderRelations(catalog);
    return catalog;
  } catch (error) {
    if (generation !== relationCatalogGeneration) return;
    elements.relationStatus.textContent = error.message;
    return null;
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
  } catch (error) {
    elements.namespaceSelect.replaceChildren(new Option("Connection unavailable", ""));
    elements.sourceSummary.classList.remove("connected");
    elements.sourceName.textContent = profile.name;
    elements.sourceDetail.textContent = error.message;
    setConnectionStatus(error.message, true);
  }
}

async function loadWidgetSourceNamespaces(profile, preferredNamespace = null) {
  const widgetId = editedWidgetId;
  elements.widgetSourceNamespace.disabled = true;
  elements.widgetSourceNamespace.replaceChildren(new Option("Loading namespaces...", ""));
  elements.relationList.replaceChildren();
  elements.relationDetail.hidden = true;
  try {
    const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profile.id)}/namespaces`);
    if (editedWidgetId !== widgetId || elements.widgetSourceProfile.value !== profile.id) return;
    const namespaces = result.namespaces ?? [];
    elements.widgetSourceNamespace.replaceChildren(...namespaces.map(namespace => new Option(namespace, namespace)));
    const namespace = namespaces.includes(preferredNamespace) ? preferredNamespace : namespaces[0];
    if (namespace) elements.widgetSourceNamespace.value = namespace;
    elements.widgetSourceNamespace.disabled = !namespaces.length;
    return await loadRelations(profile, namespace);
  } catch (error) {
    if (editedWidgetId !== widgetId) return;
    elements.widgetSourceNamespace.replaceChildren(new Option("Connection unavailable", ""));
    elements.relationStatus.textContent = error.message;
  }
}

async function openWidgetEditor(widgetId) {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetId);
  if (!editMode || !widget) return;
  widgetEditorGeneration += 1;
  editedWidgetId = widget.id;
  widgetQueryDraft = clone(widget.configuration?.query ?? defaultWidgetQuery());
  elements.widgetEditorName.disabled = false;
  document.querySelector("#reset-widget-query").disabled = false;
  elements.widgetQueryLimit.disabled = false;
  showWidgetEditorSection("source");
  elements.widgetEditorName.value = widget.title;
  elements.widgetEditorName.setCustomValidity("");
  elements.relationList.replaceChildren();
  elements.relationDetail.replaceChildren();
  elements.relationDetail.hidden = true;
  elements.widgetEditor.showModal();
  elements.relationStatus.textContent = "Loading widget sources...";
  await loadProfiles();
  if (editedWidgetId !== widget.id) return;
  elements.widgetSourceProfile.replaceChildren(...profiles.map(profile => new Option(`${profile.name} · ${profile.dbname}`, profile.id)));
  const currentSource = widget.configuration?.source;
  const profile = profiles.find(item => item.id === currentSource?.profileId) ?? profiles.find(item => item.id === selectedProfileId) ?? profiles[0];
  if (!profile) {
    elements.widgetSourceProfile.replaceChildren(new Option("No saved connections", ""));
    elements.widgetSourceProfile.disabled = true;
    await loadRelations(null, null);
    return;
  }
  elements.widgetSourceProfile.disabled = false;
  elements.widgetSourceProfile.value = profile.id;
  const catalog = await loadWidgetSourceNamespaces(profile, currentSource?.profileId === profile.id ? currentSource.namespace : null);
  if (editedWidgetId !== widget.id || !currentSource || !catalog || currentSource.profileId !== profile.id || currentSource.namespace !== catalog.namespace) return;
  const relation = catalog.relations.find(item => item.name === currentSource.relation);
  const verification = sourceVerification.get(widget.id);
  if (!relation) {
    elements.relationStatus.textContent = verification?.message || `Saved relation ${currentSource.database}.${currentSource.namespace}.${currentSource.relation} is unavailable.`;
    return;
  }
  for (const item of elements.relationList.querySelectorAll(".relation-item")) {
    item.classList.toggle("active", item.querySelector("strong")?.textContent === relation.name);
  }
  await inspectSelectedRelation(catalog, relation);
  renderSourceChangeNotice(verification);
}

function closeWidgetEditor() {
  elements.widgetEditor.close();
}

function commitWidgetEditorName() {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === editedWidgetId);
  if (!widget) return;
  const title = elements.widgetEditorName.value.trim();
  if (!title) {
    elements.widgetEditorName.setCustomValidity("Widget name is required.");
    elements.widgetEditorName.reportValidity();
    elements.widgetEditorName.value = widget.title;
    return;
  }
  elements.widgetEditorName.setCustomValidity("");
  elements.widgetEditorName.value = title;
  if (title === widget.title) return;
  widget.title = title;
  renderDashboard();
  markDashboardChanged(true);
}

function formatQueryValue(value, format = { style: "auto" }) {
  if (value === null) return "NULL";
  if (format.style === "auto") return typeof value === "object" ? JSON.stringify(value) : String(value);
  if (typeof value === "string" && value.replace(/[^0-9]/g, "").replace(/^0+/, "").length > 15) return value;
  const numericValue = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
  if (numericValue === null) return typeof value === "object" ? JSON.stringify(value) : String(value);
  const digits = format.fractionDigits;
  if (format.style === "integer") return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(numericValue);
  if (format.style === "currency") return new Intl.NumberFormat(undefined, { style: "currency", currency: format.currency, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(numericValue);
  if (format.style === "percent") return new Intl.NumberFormat(undefined, { style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(numericValue);
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(numericValue);
}

function renderQueryResult(card, widget) {
  if (!widget.configuration?.query) return;
  while (card.querySelector(":scope > header")?.nextSibling) card.querySelector(":scope > header").nextSibling.remove();
  card.classList.add("query-result-widget");
  const execution = widgetQueryResults.get(widget.id);
  if (!execution || execution.state !== "ready") {
    const status = document.createElement("p");
    status.className = `query-result-status${execution?.state === "error" ? " error" : ""}`;
    status.textContent = execution?.message || "Waiting for source verification...";
    card.append(status);
    return;
  }
  const scroll = document.createElement("div");
  scroll.className = "query-result-scroll";
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", `${widget.title} query results`);
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const column of execution.result.columns) {
    const cell = document.createElement("th");
    cell.textContent = column.label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const values of execution.result.rows) {
    const row = document.createElement("tr");
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = formatQueryValue(value, execution.result.columns[index].numberFormat);
      row.append(cell);
    });
    body.append(row);
  }
  if (!execution.result.rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = execution.result.columns.length;
    cell.textContent = "No rows matched this query.";
    row.append(cell);
    body.append(row);
  }
  table.append(head, body);
  scroll.append(table);
  const summary = document.createElement("p");
  summary.className = "query-result-summary";
  summary.textContent = `${execution.result.rowCount} result row${execution.result.rowCount === 1 ? "" : "s"}${execution.result.truncated ? ` · limited to ${execution.result.limit}` : ""}`;
  card.append(scroll, summary);
}

async function executeWidgetQuery(widget, query = widget.configuration?.query, { render = true, publish = true } = {}) {
  if (!widget.configuration?.source || !query) return null;
  const dashboardId = activeDashboard?.id;
  const sourceSnapshot = clone(widget.configuration.source);
  const querySnapshot = clone(query);
  const executionToken = {};
  const tokenKey = `${widget.id}:${publish ? "publish" : "draft"}`;
  widgetQueryExecutionTokens.set(tokenKey, executionToken);
  if (publish) widgetQueryResults.set(widget.id, { state: "loading", message: "Running verified aggregate query..." });
  if (publish && render) renderDashboard();
  try {
    const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(widget.configuration.source.profileId)}/relation/query`, {
      method: "POST", body: JSON.stringify({ source: sourceSnapshot, query: querySnapshot })
    });
    const currentWidget = activeDashboard?.dashboard.widgets.find(item => item.id === widget.id);
    const sourceCurrent = currentWidget === widget && JSON.stringify(widget.configuration?.source) === JSON.stringify(sourceSnapshot);
    const queryCurrent = !publish || JSON.stringify(widget.configuration?.query) === JSON.stringify(querySnapshot);
    if (activeDashboard?.id !== dashboardId || widgetQueryExecutionTokens.get(tokenKey) !== executionToken || !sourceCurrent || !queryCurrent) throw new Error("Query execution was superseded; run it again");
    if (publish) {
      widgetQueryResults.set(widget.id, { state: "ready", result });
      executedSqlByResult.set(`${widget.id}:widget`, { sql: result.sql, parameters: result.parameters });
    }
    if (publish && render) renderDashboard();
    return result;
  } catch (error) {
    if (activeDashboard?.id !== dashboardId || widgetQueryExecutionTokens.get(tokenKey) !== executionToken) throw error;
    if (publish) {
      widgetQueryResults.set(widget.id, { state: "error", message: error.message });
      executedSqlByResult.delete(`${widget.id}:widget`);
    }
    if (publish && render) renderDashboard();
    throw error;
  }
}

async function executeDashboardQueries() {
  const dashboardId = activeDashboard?.id;
  const generation = ++queryExecutionGeneration;
  const widgets = activeDashboard?.dashboard.widgets.filter(widget => widget.configuration?.query && sourceVerification.get(widget.id)?.state === "verified") ?? [];
  for (const widget of widgets) widgetQueryResults.set(widget.id, { state: "loading", message: "Running verified aggregate query..." });
  if (widgets.length) renderDashboard();
  await Promise.all(widgets.map(async widget => {
    try {
      await executeWidgetQuery(widget, widget.configuration.query, { render: false });
    } catch (_error) {
      // Each widget displays its own safe execution error.
    }
  }));
  if (generation === queryExecutionGeneration && activeDashboard?.id === dashboardId) renderDashboard();
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
    copy.textContent = "Assign a source and query in Edit mode";
    card.append(header, mark, copy);
  }
  card.dataset.widgetId = widget.id;
  card.tabIndex = 0;
  card.draggable = editMode;
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
    const verification = sourceVerification.get(widget.id);
    const suffix = verification?.state === "checking" ? " · checking" : verification?.state === "error" ? verification.code === "relation_changed" ? " · source changed" : verification.code === "relation_missing" ? " · source missing" : " · source unavailable" : "";
    sourceLabel.textContent = `${source.database}.${source.namespace}.${source.relation}${suffix}`;
    card.dataset.sourceState = verification?.state || "unverified";
    if (verification?.state === "error") {
      card.classList.add("source-invalid");
      sourceLabel.title = verification.message;
    }
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
  const edit = document.createElement("button");
  edit.type = "button";
  edit.dataset.action = "edit-widget";
  edit.textContent = "Edit";
  edit.setAttribute("aria-label", `Edit ${widget.title}`);
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
  controls.append(edit, moveEarlier, moveLater, duplicate, remove);
  card.querySelector("header")?.append(viewSql, controls);
  renderQueryResult(card, widget);
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
  elements.mobileDashboardSelect.replaceChildren(...dashboards.map(record => new Option(`${record.dashboard.archived ? "Archived · " : ""}${record.dashboard.title}`, record.id)));
  elements.mobileDashboardSelect.value = activeDashboard?.id ?? "";
  elements.mobileDashboardSelect.disabled = !dashboards.length;
  document.querySelector("#show-active-dashboards").setAttribute("aria-pressed", String(!showArchived));
  document.querySelector("#show-archived-dashboards").setAttribute("aria-pressed", String(showArchived));
  for (const record of visible) {
    const button = document.createElement("button");
    button.className = `dashboard-link${record.id === activeDashboard?.id ? " active" : ""}`;
    button.type = "button";
    if (record.id === activeDashboard?.id) button.setAttribute("aria-current", "page");
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
  if (saveTimer && saveTimerDashboardId !== dashboardId) {
    clearTimeout(saveTimer);
    saveTimer = null;
    saveTimerDashboardId = null;
  }
  const record = dashboards.find(item => item.id === dashboardId);
  activeDashboard = record ? clone(record) : null;
  queryExecutionGeneration += 1;
  widgetQueryResults.clear();
  widgetQueryExecutionTokens.clear();
  executedSqlByResult.clear();
  dashboardConflict = false;
  elements.conflict.hidden = true;
  setEditMode(false, false);
  renderDashboardList();
  renderDashboard();
  setSaveStatus(activeDashboard ? "Saved" : "No dashboard", activeDashboard ? "saved" : "");
  verifyDashboardSources();
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
  const dashboardId = activeDashboard.id;
  changeGeneration += 1;
  setSaveStatus("Unsaved changes", "dirty");
  if (render) renderDashboard();
  clearTimeout(saveTimer);
  saveTimerDashboardId = dashboardId;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    saveTimerDashboardId = null;
    if (activeDashboard?.id === dashboardId) persistDashboard(dashboardId);
  }, 450);
}

async function persistDashboard(expectedDashboardId = activeDashboard?.id) {
  if (!activeDashboard || dashboardConflict || activeDashboard.id !== expectedDashboardId) return;
  const dashboardId = expectedDashboardId;
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
        saveTimerDashboardId = dashboardId;
        saveTimer = setTimeout(() => {
          saveTimer = null;
          saveTimerDashboardId = null;
          if (activeDashboard?.id === dashboardId) persistDashboard(dashboardId);
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
    const dashboardId = saveTimerDashboardId;
    saveTimerDashboardId = null;
    if (dashboardId && activeDashboard?.id === dashboardId) await persistDashboard(dashboardId);
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
  if (!editMode && elements.widgetEditor.open) closeWidgetEditor();
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
  if (sourceVerification.has(source.id)) sourceVerification.set(duplicate.id, sourceVerification.get(source.id));
  markDashboardChanged(true);
  if (duplicate.configuration?.query && sourceVerification.get(duplicate.id)?.state === "verified") executeWidgetQuery(duplicate).catch(() => {});
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
  sourceVerification.delete(widgetId);
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
  tableScroll.tabIndex = 0;
  tableScroll.setAttribute("role", "region");
  tableScroll.setAttribute("aria-label", "Selected population rows");
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
  const verification = sourceVerification.get(widget.id);
  if (widget.configuration?.source && verification?.state !== "verified") {
    elements.sourceDetail.textContent = verification?.message || "Verifying widget source before opening results";
    return;
  }
  if (editMode) setEditMode(false);
  const sourceCard = elements.canvas.querySelector(`[data-widget-id="${widget.id}"]`);
  focusedSourceElement = sourceCard;
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
  elements.widgetInspector.inert = true;
  elements.widgetInspector.setAttribute("aria-hidden", "true");
  elements.widgetFocus.classList.add("inspector-dismissed");
  elements.widgetFocus.hidden = false;
  elements.widgetFocus.classList.add("open");
  elements.workspace.classList.add("widget-focus-open");
  elements.canvas.inert = true;
  document.querySelector(".dashboard-toolbar").inert = true;
  document.querySelector(".dashboard-sidebar").inert = true;
  document.querySelector(".topbar").inert = true;
  elements.conflict.inert = true;
  focusAnimation?.cancel();
  if (focusedSourceRect && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const target = elements.widgetFocus.getBoundingClientRect();
    focusAnimation = elements.widgetFocus.animate([
      { transformOrigin: "top left", transform: `translate(${focusedSourceRect.left - target.left}px, ${focusedSourceRect.top - target.top}px) scale(${focusedSourceRect.width / target.width}, ${focusedSourceRect.height / target.height})`, borderRadius: "8px", opacity: .72 },
      { transformOrigin: "top left", transform: "translate(0,0) scale(1)", borderRadius: "0", opacity: 1 }
    ], { duration: 280, easing: "cubic-bezier(.22,1,.36,1)" });
  }
  close.focus();
}

function closeWidgetFocus(immediate = false) {
  if (!focusedWidgetId && elements.widgetFocus.hidden) return;
  focusAnimation?.cancel();
  focusAnimation = null;
  focusedWidgetId = null;
  elements.workspace.classList.remove("widget-focus-open");
  elements.canvas.inert = false;
  document.querySelector(".dashboard-toolbar").inert = false;
  document.querySelector(".dashboard-sidebar").inert = false;
  document.querySelector(".topbar").inert = false;
  elements.conflict.inert = false;
  const finish = () => {
    if (focusedWidgetId) return;
    elements.widgetFocus.classList.remove("open");
    elements.widgetFocus.hidden = true;
    elements.widgetFocusContent.replaceChildren();
    focusedSourceRect = null;
    focusedSourceElement?.focus();
    focusedSourceElement = null;
  };
  if (immediate || !focusedSourceRect || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return finish();
  const target = elements.widgetFocus.getBoundingClientRect();
  focusAnimation = elements.widgetFocus.animate([
    { transformOrigin: "top left", transform: "translate(0,0) scale(1)", borderRadius: "0", opacity: 1 },
    { transformOrigin: "top left", transform: `translate(${focusedSourceRect.left - target.left}px, ${focusedSourceRect.top - target.top}px) scale(${focusedSourceRect.width / target.width}, ${focusedSourceRect.height / target.height})`, borderRadius: "8px", opacity: .72 }
  ], { duration: 220, easing: "cubic-bezier(.4,0,.2,1)" });
  focusAnimation.finished.then(finish, finish);
}

function closeWidgetInspector() {
  elements.widgetInspector.classList.add("dismissed");
  elements.widgetInspector.inert = true;
  elements.widgetInspector.setAttribute("aria-hidden", "true");
  elements.widgetFocus.classList.add("inspector-dismissed");
}

function openWidgetInspector(metricName, filters = []) {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === focusedWidgetId);
  if (!widget) return;
  elements.widgetInspectorTitle.textContent = metricName || widget.title;
  elements.widgetInspectorBody.replaceChildren(populationTable(filters));
  elements.widgetInspector.classList.remove("dismissed");
  elements.widgetInspector.inert = false;
  elements.widgetInspector.removeAttribute("aria-hidden");
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
      await flushPendingSave();
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
  clearTimeout(saveTimer);
  saveTimer = null;
  saveTimerDashboardId = null;
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
  }
});
document.querySelector("#refresh-button").addEventListener("click", async event => {
  if (!selectedProfileId || !elements.namespaceSelect.value) return elements.dialog.showModal();
  event.currentTarget.textContent = "Checking...";
  try {
    await postgres.request(`/api/postgres/profiles/${encodeURIComponent(selectedProfileId)}/fingerprint?namespace=${encodeURIComponent(elements.namespaceSelect.value)}`);
    await verifyDashboardSources();
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
document.querySelector("#mobile-new-dashboard").addEventListener("click", () => openDashboardForm("create"));
elements.mobileDashboardSelect.addEventListener("change", async () => {
  await flushPendingSave();
  openDashboard(elements.mobileDashboardSelect.value);
});
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
document.querySelector("#close-widget-editor").addEventListener("click", closeWidgetEditor);
elements.widgetEditor.addEventListener("close", () => {
  widgetEditorGeneration += 1;
  editedWidgetId = null;
  widgetQueryDraft = null;
  relationInspectionGeneration += 1;
  relationCatalogGeneration += 1;
});
for (const button of elements.widgetEditor.querySelectorAll("[data-editor-section]")) {
  button.addEventListener("click", () => showWidgetEditorSection(button.dataset.editorSection, button));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = Array.from(elements.widgetEditor.querySelectorAll("[data-editor-section]"));
    const current = tabs.indexOf(button);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    showWidgetEditorSection(tabs[next].dataset.editorSection, tabs[next]);
  });
}
elements.widgetEditorName.addEventListener("change", commitWidgetEditorName);
elements.widgetEditorName.addEventListener("keydown", event => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  commitWidgetEditorName();
});
elements.widgetQueryLimit.addEventListener("change", () => {
  if (widgetQueryDraft) widgetQueryDraft.limit = Number(elements.widgetQueryLimit.value);
});
document.querySelector("#reset-widget-query").addEventListener("click", () => {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === editedWidgetId);
  widgetQueryDraft = clone(widget?.configuration?.query ?? defaultWidgetQuery());
  renderWidgetQueryDraft();
});
document.querySelector("#apply-widget-query").addEventListener("click", async event => {
  const widget = activeDashboard?.dashboard.widgets.find(item => item.id === editedWidgetId);
  if (!widget?.configuration?.source || !widgetQueryDraft || widgetQueryApplyActive()) return;
  widgetQueryDraft.limit = Number(elements.widgetQueryLimit.value);
  const dashboardId = activeDashboard.id;
  const widgetId = widget.id;
  const source = clone(widget.configuration.source);
  const draft = clone(widgetQueryDraft);
  const applySession = { dashboardId, widgetId, generation: widgetEditorGeneration };
  widgetQueryApplySession = applySession;
  renderWidgetQueryDraft();
  elements.widgetQueryStatus.textContent = "Validating and running against the verified source...";
  let finalMessage = "";
  try {
    const result = await executeWidgetQuery(widget, draft, { publish: false });
    const currentWidget = activeDashboard?.dashboard.widgets.find(item => item.id === widgetId);
    if (activeDashboard?.id !== dashboardId || editedWidgetId !== widgetId || widgetEditorGeneration !== applySession.generation || currentWidget !== widget || sourceVerification.get(widgetId)?.state !== "verified" || JSON.stringify(widget.configuration.source) !== JSON.stringify(source)) return;
    widget.configuration = { source, query: draft };
    widgetQueryDraft = clone(draft);
    widgetQueryExecutionTokens.set(`${widget.id}:publish`, {});
    widgetQueryResults.set(widget.id, { state: "ready", result });
    executedSqlByResult.set(`${widget.id}:widget`, { sql: result.sql, parameters: result.parameters });
    markDashboardChanged(true);
    finalMessage = "Query applied. The live result is displayed on this widget.";
  } catch (error) {
    finalMessage = error.message;
  } finally {
    if (widgetQueryApplySession === applySession) widgetQueryApplySession = null;
    if (activeDashboard?.id === dashboardId && editedWidgetId === widgetId && widgetEditorGeneration === applySession.generation) {
      renderWidgetQueryDraft();
      elements.widgetQueryStatus.textContent = finalMessage || "Query was not applied because the widget changed.";
    }
  }
});
elements.widgetSourceProfile.addEventListener("change", () => {
  const profile = profiles.find(item => item.id === elements.widgetSourceProfile.value);
  if (profile) loadWidgetSourceNamespaces(profile);
});
elements.widgetSourceNamespace.addEventListener("change", () => {
  const profile = profiles.find(item => item.id === elements.widgetSourceProfile.value);
  if (profile) loadRelations(profile, elements.widgetSourceNamespace.value);
});
document.addEventListener("click", event => {
  for (const popup of document.querySelectorAll(".query-calendar-popup:not([hidden])")) {
    const control = popup.closest(".query-calendar-control");
    if (control?.contains(event.target)) continue;
    popup.hidden = true;
    control?.querySelector(".query-calendar-toggle")?.setAttribute("aria-expanded", "false");
  }
});

elements.canvas.addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  const widgetId = event.target.closest(".widget")?.dataset.widgetId;
  if (action === "view-widget-sql") return openExecutedSql(activeDashboard?.dashboard.widgets.find(widget => widget.id === widgetId));
  if (action === "edit-widget") return openWidgetEditor(widgetId);
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
  if (event.key !== "Escape") return;
  let calendarClosed = false;
  for (const popup of document.querySelectorAll(".query-calendar-popup:not([hidden])")) {
    popup.hidden = true;
    popup.closest(".query-calendar-control")?.querySelector(".query-calendar-toggle")?.setAttribute("aria-expanded", "false");
    calendarClosed = true;
  }
  if (!calendarClosed && focusedWidgetId) closeWidgetFocus();
});
window.addEventListener("beforeunload", () => { if (saveTimer) persistDashboard(); });

Promise.all([loadDashboards(), loadProfiles()]);
