const TABLE_WIDTH = 270;
const MIN_ZOOM = .1;
const MAX_ZOOM = 1.7;
const SAVE_DELAY_MS = 180;
const LAYOUT_SAVE_DELAY_MS = 750;
const WHEEL_ZOOM_IDLE_MS = 140;
const ONBOARDING_DISABLED_KEY = "schemii.onboarding.disabled.v1";
const ONBOARDING_SERVER_KEY = "schemii.onboarding.server.v1";

function clampZoom(value, maximum = MAX_ZOOM) {
  return Math.min(maximum, Math.max(MIN_ZOOM, value));
}

const COLORS = ["#f4b942", "#65a9ff", "#9b82f4", "#59c894", "#ef7c8e", "#e58d4c"];
const DATA_TYPES = ["uuid", "varchar(255)", "text", "name", "integer", "bigint", "decimal(10,2)", "boolean", "date", "timestamp", "timestamp with time zone", "json", "jsonb"];
const starterSchema = {
  projectName: "Product workspace",
  tables: [
    {
      id: "table_users", name: "users", x: 120, y: 125, color: "#f4b942",
      columns: [
        { id: "col_user_id", name: "id", type: "uuid", primary: true, nullable: false, unique: true, default: "" },
        { id: "col_user_email", name: "email", type: "varchar(255)", primary: false, nullable: false, unique: true, default: "" },
        { id: "col_user_name", name: "display_name", type: "varchar(255)", primary: false, nullable: true, unique: false, default: "" },
        { id: "col_user_created", name: "created_at", type: "timestamp", primary: false, nullable: false, unique: false, default: "now()" }
      ],
      uniqueConstraints: []
    },
    {
      id: "table_projects", name: "projects", x: 520, y: 80, color: "#65a9ff",
      columns: [
        { id: "col_project_id", name: "id", type: "uuid", primary: true, nullable: false, unique: true, default: "" },
        { id: "col_project_owner", name: "owner_id", type: "uuid", primary: false, nullable: false, unique: false, default: "" },
        { id: "col_project_name", name: "name", type: "varchar(255)", primary: false, nullable: false, unique: false, default: "" },
        { id: "col_project_status", name: "status", type: "varchar(255)", primary: false, nullable: false, unique: false, default: "" }
      ],
      uniqueConstraints: []
    },
    {
      id: "table_tasks", name: "tasks", x: 920, y: 210, color: "#9b82f4",
      columns: [
        { id: "col_task_id", name: "id", type: "uuid", primary: true, nullable: false, unique: true, default: "" },
        { id: "col_task_project", name: "project_id", type: "uuid", primary: false, nullable: false, unique: false, default: "" },
        { id: "col_task_title", name: "title", type: "varchar(255)", primary: false, nullable: false, unique: false, default: "" },
        { id: "col_task_done", name: "completed", type: "boolean", primary: false, nullable: false, unique: false, default: "" }
      ],
      uniqueConstraints: []
    }
  ],
  relationships: [
    { id: "rel_owner", fromTableId: "table_projects", fromColumnId: "col_project_owner", toTableId: "table_users", toColumnId: "col_user_id" },
    { id: "rel_tasks", fromTableId: "table_tasks", fromColumnId: "col_task_project", toTableId: "table_projects", toColumnId: "col_project_id" }
  ],
  functions: []
};

const elements = {
  workspace: document.querySelector("#workspace"),
  mainLayout: document.querySelector("#main-layout"),
  toolRail: document.querySelector("#tool-rail"),
  stage: document.querySelector("#stage"),
  selectionMarquee: document.querySelector("#selection-marquee"),
  tablesLayer: document.querySelector("#tables-layer"),
  connections: document.querySelector("#connections"),
  inspector: document.querySelector("#inspector"),
  inspectorEmpty: document.querySelector("#inspector-empty"),
  inspectorContent: document.querySelector("#inspector-content"),
  schemaStats: document.querySelector("#schema-stats"),
  projectName: document.querySelector("#project-name"),
  saveStatus: document.querySelector("#save-status"),
  tableDialog: document.querySelector("#table-dialog"),
  tableForm: document.querySelector("#table-form"),
  tableNameInput: document.querySelector("#table-name-input"),
  relationshipEditorDialog: document.querySelector("#relationship-editor-dialog"),
  relationshipEditorForm: document.querySelector("#relationship-editor-form"),
  relationshipEditorTitle: document.querySelector("#relationship-editor-title"),
  relationshipEditorTables: document.querySelector("#relationship-editor-tables"),
  relationshipEditorName: document.querySelector("#relationship-editor-name"),
  relationshipEditorPairs: document.querySelector("#relationship-editor-pairs"),
  relationshipEditorStatus: document.querySelector("#relationship-editor-status"),
  relationshipAddPair: document.querySelector("#relationship-add-pair"),
  deleteRelationshipEditor: document.querySelector("#delete-relationship-editor"),
  relationTool: document.querySelector("#relationship-tool"),
  selectTool: document.querySelector("#select-tool"),
  relationBanner: document.querySelector("#relationship-banner"),
  relationInstruction: document.querySelector("#relationship-instruction"),
  undoButton: document.querySelector("#undo-button"),
  redoButton: document.querySelector("#redo-button"),
  zoomDisplay: document.querySelector("#zoom-display"),
  schemaDialog: document.querySelector("#schema-dialog"),
  schemaLibrary: document.querySelector("#schema-library"),
  schemaLibraryCount: document.querySelector("#schema-library-count"),
  sqlFileInput: document.querySelector("#sql-file-input"),
  toast: document.querySelector("#toast"),
  functionsDialog: document.querySelector("#functions-dialog"),
  functionsList: document.querySelector("#functions-list"),
  functionsCount: document.querySelector("#functions-count"),
  addFunctionButton: document.querySelector("#add-function-button"),
  closeFunctionsDialog: document.querySelector("#close-functions-dialog"),
  functionEditorDialog: document.querySelector("#function-editor-dialog"),
  functionEditorForm: document.querySelector("#function-editor-form"),
  functionEditorTitle: document.querySelector("#function-editor-title"),
  functionDefinitionInput: document.querySelector("#function-definition-input"),
  cancelFunctionEditor: document.querySelector("#cancel-function-editor"),
  deleteFunctionButton: document.querySelector("#delete-function-button"),
  postgresButton: document.querySelector("#postgres-button"),
  postgresDialog: document.querySelector("#postgres-dialog"),
  postgresProfilesList: document.querySelector("#postgres-profiles-list"),
  postgresNamespaceSelect: document.querySelector("#postgres-namespace-select"),
  postgresStatus: document.querySelector("#postgres-status"),
  postgresCatalogSummary: document.querySelector("#postgres-catalog-summary"),
  postgresRefreshButton: document.querySelector("#postgres-refresh-button"),
  postgresImportButton: document.querySelector("#postgres-import-button"),
  postgresPreviewButton: document.querySelector("#postgres-preview-button"),
  postgresObjectsButton: document.querySelector("#postgres-objects-button"),
  postgresProfileDialog: document.querySelector("#postgres-profile-dialog"),
  postgresProfileForm: document.querySelector("#postgres-profile-form"),
  postgresProfileTitle: document.querySelector("#postgres-profile-title"),
  postgresProfileStatus: document.querySelector("#postgres-profile-status"),
  postgresProfileName: document.querySelector("#postgres-profile-name"),
  postgresProfileHost: document.querySelector("#postgres-profile-host"),
  postgresProfilePort: document.querySelector("#postgres-profile-port"),
  postgresProfileDatabase: document.querySelector("#postgres-profile-database"),
  postgresProfileUser: document.querySelector("#postgres-profile-user"),
  postgresProfilePassword: document.querySelector("#postgres-profile-password"),
  postgresProfileSslmode: document.querySelector("#postgres-profile-sslmode"),
  postgresProfileTimeout: document.querySelector("#postgres-profile-timeout"),
  migrationDialog: document.querySelector("#migration-dialog"),
  migrationTarget: document.querySelector("#migration-target"),
  migrationSummary: document.querySelector("#migration-summary"),
  migrationWarnings: document.querySelector("#migration-warnings"),
  migrationSql: document.querySelector("#migration-sql"),
  includeDestructive: document.querySelector("#include-destructive"),
  destructiveConfirmation: document.querySelector("#destructive-confirmation"),
  confirmDestructive: document.querySelector("#confirm-destructive"),
  applyMigrationButton: document.querySelector("#apply-migration"),
  databaseObjectsDialog: document.querySelector("#database-objects-dialog"),
  databaseObjectsList: document.querySelector("#database-objects-list"),
  databaseObjectsCount: document.querySelector("#database-objects-count"),
  databaseObjectEditorDialog: document.querySelector("#database-object-editor-dialog"),
  databaseObjectEditorForm: document.querySelector("#database-object-editor-form"),
  databaseObjectEditorTitle: document.querySelector("#database-object-editor-title"),
  databaseObjectType: document.querySelector("#database-object-type"),
  databaseObjectTable: document.querySelector("#database-object-table"),
  databaseObjectName: document.querySelector("#database-object-name"),
  databaseObjectDefinition: document.querySelector("#database-object-definition"),
  deleteDatabaseObject: document.querySelector("#delete-database-object"),
  tableDataPanel: document.querySelector("#table-data-panel"),
  tableDataPanelTitle: document.querySelector("#table-data-panel-title"),
  tableDataPanelHead: document.querySelector("#table-data-panel-head"),
  tableDataCount: document.querySelector("#table-data-count"),
  tableDataWarning: document.querySelector("#table-data-warning"),
  tableDataScroll: document.querySelector("#table-data-scroll"),
  maximizeTableData: document.querySelector("#maximize-table-data"),
  minimizeTableData: document.querySelector("#minimize-table-data"),
  tableDataPaneContent: document.querySelector("#table-data-pane-content"),
  showTableDataPane: document.querySelector("#show-table-data-pane"),
  showSqlConsolePane: document.querySelector("#show-sql-console-pane"),
  sqlConsoleContent: document.querySelector("#sql-console-content"),
  sqlConsoleInput: document.querySelector("#sql-console-input"),
  sqlConsoleStatus: document.querySelector("#sql-console-status"),
  runSqlConsole: document.querySelector("#run-sql-console"),
  clearSqlConsole: document.querySelector("#clear-sql-console"),
  databaseDriftBanner: document.querySelector("#database-drift-banner"),
  databaseDriftMessage: document.querySelector("#database-drift-message"),
  objectIconMenu: document.querySelector("#object-icon-menu"),
  tooltip: document.querySelector("#app-tooltip"),
  aiButton: document.querySelector("#ai-button"),
  aiRailStatus: document.querySelector("#ai-rail-status"),
  aiPanel: document.querySelector("#ai-panel"),
  aiEmptyCopy: document.querySelector("#ai-empty-copy"),
  aiStatusPill: document.querySelector("#ai-status-pill"),
  aiHistoryButton: document.querySelector("#ai-history-button"),
  aiNewChat: document.querySelector("#ai-new-chat"),
  aiModelSelect: document.querySelector("#ai-model-select"),
  aiAccessSelect: document.querySelector("#ai-access-select"),
  aiSqlPolicyWrap: document.querySelector("#ai-sql-policy-wrap"),
  aiSqlPolicy: document.querySelector("#ai-sql-policy"),
  aiAccessDisclosure: document.querySelector("#ai-access-disclosure"),
  aiFunctionCaveat: document.querySelector("#ai-function-caveat"),
  aiMessages: document.querySelector("#ai-messages"),
  aiComposer: document.querySelector("#ai-composer"),
  aiInput: document.querySelector("#ai-input"),
  aiSendButton: document.querySelector("#ai-send-button"),
  aiSettingsDialog: document.querySelector("#ai-settings-dialog"),
  aiSettingsStatus: document.querySelector("#ai-settings-status"),
  aiProviders: document.querySelector("#ai-providers"),
  aiHistoryDialog: document.querySelector("#ai-history-dialog"),
  aiHistoryList: document.querySelector("#ai-history-list"),
  onboardingDialog: document.querySelector("#onboarding-dialog"),
  onboardingStepLabel: document.querySelector("#onboarding-step-label"),
  onboardingProgress: document.querySelector("#onboarding-progress"),
  onboardingDontShow: document.querySelector("#onboarding-dont-show"),
  onboardingBack: document.querySelector("#onboarding-back"),
  onboardingNext: document.querySelector("#onboarding-next"),
  onboardingSkip: document.querySelector("#onboarding-skip"),
  shutdownDialog: document.querySelector("#shutdown-dialog"),
  shutdownConfirmPanel: document.querySelector("#shutdown-confirm-panel"),
  shutdownComplete: document.querySelector("#shutdown-complete"),
  shutdownWarning: document.querySelector("#shutdown-warning"),
  confirmShutdown: document.querySelector("#confirm-shutdown")
};

let schemaLibrary = { activeId: null, schemas: [] };
let activeSchemaId = null;
let schema = clone(starterSchema);
let selectedTableId = null;
let selectedTableIds = new Set();
let relationSource = null;
let relationMode = false;
let relationshipEditorState = null;
let relationshipPairDragIndex = null;
let objectIconMenuTargets = [];
let inspectorObjectFocusTimer = null;
let view = { x: 45, y: 35, zoom: 1 };
let dragState = null;
let tablePressState = null;
let panState = null;
let marqueeState = null;
let middlePanPanelSnapshot = null;
let spacePressed = false;
let draggedColumnId = null;
let columnDropTarget = null;
let copiedTable = null;
let editingFunctionId = null;
let saveTimer = null;
let saveQueue = Promise.resolve();
let wheelZoomTimer = null;
let toastTimer = null;
let onboardingPage = 0;
let onboardingSeenServerId = null;
let serverStopped = false;
let activeTooltipTarget = null;
let tooltipHideTimer = null;
let undoStack = [];
let redoStack = [];
let historyGroup = null;
let tableDataRequestId = 0;
let tableDataPanelExpanded = false;
let tableDataPanelMaximized = false;
let tablePanelActivePane = "data";
let tableDataPanelTransitionTimer = null;
let tablePaneTransitionTimer = null;
let inspectorDismissed = false;
let inspectorContentCollapsed = false;
let inspectorContentTransitionTimer = null;
let inspectorDismissTransitionTimer = null;
let sqlConsoleRequestId = 0;
let tableDataState = {
  key: null,
  target: null,
  columns: [],
  rows: [],
  nextOffset: 0,
  hasMore: false,
  stableOrder: true,
  mode: "table",
  truncated: false,
  loading: false,
  error: null,
  requestId: 0
};
let sqlConsoleState = {
  key: null,
  columns: [],
  rows: [],
  truncated: false,
  loading: false,
  error: null,
  requestId: 0
};
let postgresState = {
  token: null,
  profiles: [],
  selectedProfileId: null,
  namespace: "",
  namespaces: [],
  busy: false,
  plan: null,
  schemaSnapshot: null,
  editingProfileId: null,
  editingObject: null,
  objectEditorContext: "objects",
  objectEditorOriginalDefinition: "",
  objectEditorDisplayDefinition: "",
  driftChecking: false,
  dismissedFingerprint: null
};
let aiState = {
  loaded: false,
  available: false,
  version: "",
  providers: [],
  authMethods: {},
  skills: [],
  sessionId: null,
  busy: false,
  requestGeneration: 0,
  sqlPolicyDeliberatelySelected: false,
  oauth: null
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function onboardingStorageValue(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function onboardingDisabled() {
  return onboardingStorageValue(ONBOARDING_DISABLED_KEY) === "1";
}

function shouldShowOnboarding(serverId) {
  if (!serverId || onboardingDisabled()) return false;
  if (onboardingSeenServerId === serverId || onboardingStorageValue(ONBOARDING_SERVER_KEY) === serverId) return false;
  onboardingSeenServerId = serverId;
  try { localStorage.setItem(ONBOARDING_SERVER_KEY, serverId); } catch { /* In-memory state still prevents repeats on this page. */ }
  return true;
}

function rememberOnboardingPreference() {
  try {
    if (elements.onboardingDontShow.checked) localStorage.setItem(ONBOARDING_DISABLED_KEY, "1");
    else localStorage.removeItem(ONBOARDING_DISABLED_KEY);
  } catch { /* Onboarding remains usable when browser storage is unavailable. */ }
}

function renderOnboardingPage() {
  const pages = [...elements.onboardingDialog.querySelectorAll("[data-onboarding-page]")];
  onboardingPage = Math.max(0, Math.min(pages.length - 1, onboardingPage));
  pages.forEach((page, index) => { page.hidden = index !== onboardingPage; });
  elements.onboardingStepLabel.textContent = `${onboardingPage + 1} of ${pages.length}`;
  elements.onboardingProgress.innerHTML = pages.map((_, index) => `<i class="${index === onboardingPage ? "active" : ""}"></i>`).join("");
  elements.onboardingBack.disabled = onboardingPage === 0;
  elements.onboardingNext.querySelector("span").textContent = onboardingPage === pages.length - 1 ? "Finish" : "Next";
}

function openOnboarding() {
  onboardingPage = 0;
  elements.onboardingDontShow.checked = onboardingDisabled();
  renderOnboardingPage();
  if (!elements.onboardingDialog.open) elements.onboardingDialog.showModal();
}

function closeOnboarding() {
  rememberOnboardingPreference();
  elements.onboardingDialog.close();
}

async function initializeOnboarding() {
  try {
    const response = await fetch("/api/session");
    const session = await response.json().catch(() => ({}));
    if (!response.ok || !session.token) return;
    postgresState.token = session.token;
    if (shouldShowOnboarding(session.serverId)) openOnboarding();
  } catch { /* Startup remains usable if the local session endpoint is unavailable. */ }
}

function activeShutdownOperation() {
  if (postgresState.busy) return "A PostgreSQL operation is still running. Wait for it to finish before shutting down.";
  if (aiState.busy) return "The AI assistant is still working. Wait for it to finish before shutting down.";
  if (tableDataState.loading || sqlConsoleState.loading) return "A data request is still running. Wait for it to finish before shutting down.";
  return "";
}

function openShutdownDialog() {
  const warning = activeShutdownOperation();
  elements.shutdownWarning.hidden = !warning;
  elements.shutdownWarning.textContent = warning;
  elements.confirmShutdown.disabled = Boolean(warning);
  elements.shutdownConfirmPanel.hidden = false;
  elements.shutdownComplete.hidden = true;
  if (!elements.shutdownDialog.open) elements.shutdownDialog.showModal();
}

async function shutdownSchemii() {
  const warning = activeShutdownOperation();
  if (warning) return openShutdownDialog();
  elements.confirmShutdown.disabled = true;
  elements.confirmShutdown.textContent = "Saving...";
  elements.shutdownWarning.hidden = true;
  try {
    await flushPendingSave();
    if (!postgresState.token) {
      const sessionResponse = await fetch("/api/session");
      const session = await sessionResponse.json().catch(() => ({}));
      if (!sessionResponse.ok || !session.token) throw new Error("Could not authorize shutdown");
      postgresState.token = session.token;
    }
    const response = await fetch("/api/shutdown", { method: "POST", headers: { "X-Schemii-Token": postgresState.token } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.shuttingDown) throw new Error(payload.error?.message || "Schemii could not be shut down");
    serverStopped = true;
    elements.shutdownConfirmPanel.hidden = true;
    elements.shutdownComplete.hidden = false;
  } catch (error) {
    elements.shutdownWarning.textContent = error.message;
    elements.shutdownWarning.hidden = false;
    elements.confirmShutdown.disabled = false;
    elements.confirmShutdown.textContent = "Shut down";
  }
}

function schemaForStorage(schemaValue, viewState = null) {
  const stored = clone(schemaValue);
  const tableLayout = {};
  for (const table of stored.tables) {
    tableLayout[table.id] = {
      x: Number.isFinite(table.x) ? table.x : 0,
      y: Number.isFinite(table.y) ? table.y : 0,
      color: table.color || COLORS[0],
      namespace: table.namespace ?? stored.postgres?.namespace ?? null,
      name: table.name,
      liveOid: table.postgres?.liveOid ?? null
    };
    delete table.x;
    delete table.y;
    delete table.color;
  }
  stored.layout = {
    version: 1,
    tables: tableLayout,
    view: clone(viewState ?? stored.layout?.view ?? { x: 45, y: 35, zoom: 1 })
  };
  return stored;
}

function readSchemaLibrary() {
  return clone(schemaLibrary);
}

function writeSchemaLibrary(library) {
  schemaLibrary = clone(library);
}

async function putRecordFile(record) {
  const response = await fetch(`/api/schemas/${encodeURIComponent(record.id)}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-Schemii-Layout-Protocol": "2",
        ...(record.layoutToken ? { "X-Schemii-Layout-Token": record.layoutToken } : {})
      },
      body: JSON.stringify(record)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || payload.error || "The schema file could not be saved");
    error.code = payload.error?.code;
    throw error;
  }
  return payload;
}

function saveRecordFile(record) {
  saveQueue = saveQueue.catch(() => {}).then(() => putRecordFile(record));
  return saveQueue;
}

async function postgresRequest(path, options = {}, retry = true) {
  if (typeof path !== "string" || !path.startsWith("/api/postgres/")) {
    throw new Error("PostgreSQL requests must use the local Schemii API");
  }
  if (!postgresState.token) {
    const sessionResponse = await fetch("/api/session");
    const session = await sessionResponse.json().catch(() => ({}));
    if (!sessionResponse.ok || !session.token) throw new Error(session.error?.message || "Could not start a PostgreSQL session");
    postgresState.token = session.token;
  }
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Schemii-Token": postgresState.token,
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (payload.error?.code === "invalid_session" && retry) {
      postgresState.token = null;
      return postgresRequest(path, options, false);
    }
    const error = new Error(payload.error?.message || payload.error || "PostgreSQL request failed");
    error.code = payload.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function aiRequest(path, options = {}, retry = true) {
  if (typeof path !== "string" || !path.startsWith("/api/ai/")) {
    throw new Error("AI requests must use the local Schemii API");
  }
  if (!postgresState.token) {
    const sessionResponse = await fetch("/api/session");
    const session = await sessionResponse.json().catch(() => ({}));
    if (!sessionResponse.ok || !session.token) throw new Error(session.error?.message || "Could not start a local AI session");
    postgresState.token = session.token;
  }
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Schemii-Token": postgresState.token,
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (payload.error?.code === "invalid_session" && retry) {
      postgresState.token = null;
      return aiRequest(path, options, false);
    }
    throw new Error(payload.error?.message || payload.error || "The AI service request failed");
  }
  return payload;
}

async function readAiActivity(sessionId, onEvent, signal, retry = true) {
  const path = `/api/ai/sessions/${encodeURIComponent(sessionId)}/activity`;
  if (!postgresState.token) {
    const sessionResponse = await fetch("/api/session", { signal });
    const session = await sessionResponse.json().catch(() => ({}));
    if (!sessionResponse.ok || !session.token) throw new Error(session.error?.message || "Could not start local agent activity");
    postgresState.token = session.token;
  }
  const response = await fetch(path, {
    method: "GET",
    signal,
    headers: { "X-Schemii-Token": postgresState.token }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (payload.error?.code === "invalid_session" && retry) {
      postgresState.token = null;
      return readAiActivity(sessionId, onEvent, signal, false);
    }
    throw new Error(payload.error?.message || "Agent activity is unavailable");
  }
  if (!response.body) throw new Error("Agent activity stream is unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try { onEvent(JSON.parse(line)); } catch { /* Ignore malformed or unknown activity records. */ }
    }
    if (done) break;
  }
}

async function checkPostgresDrift() {
  const source = schema?.postgres;
  if (!source?.sourceProfileId || !source.namespace || !source.fingerprint || document.hidden || postgresState.busy || postgresState.driftChecking) return;
  postgresState.driftChecking = true;
  try {
    const status = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(source.sourceProfileId)}/fingerprint?namespace=${encodeURIComponent(source.namespace)}`, { method: "GET" });
    if (status.fingerprint === source.fingerprint) {
      elements.databaseDriftBanner.hidden = true;
      postgresState.dismissedFingerprint = null;
    } else if (status.fingerprint !== postgresState.dismissedFingerprint) {
      elements.databaseDriftMessage.textContent = `PostgreSQL changed: ${status.tables} tables, ${status.relationships} relationships, ${status.functions} routines.`;
      elements.databaseDriftBanner.hidden = false;
      elements.databaseDriftBanner.dataset.fingerprint = status.fingerprint;
    }
  } catch {
    // Connection state is surfaced in the PostgreSQL dialog; polling stays quiet.
  } finally {
    postgresState.driftChecking = false;
  }
}

async function refreshLinkedPostgresDesign() {
  const source = schema.postgres;
  if (!source?.sourceProfileId || !source.namespace) return;
  if (!confirm("Refresh semantic database objects from PostgreSQL? Your table layout and colors will be preserved, but unapplied local schema edits will be replaced.")) return;
  const targetSchemaId = activeSchemaId;
  setPostgresBusy(true);
  elements.databaseDriftMessage.textContent = "Refreshing PostgreSQL changes...";
  try {
    await flushPendingSave();
    if (activeSchemaId !== targetSchemaId) return;
    const refreshed = migrateSchema(await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(source.sourceProfileId)}/introspect`, {
      method: "POST",
      body: JSON.stringify({ namespace: source.namespace })
    }));
    if (activeSchemaId !== targetSchemaId) return showToast("Database refresh cancelled because another design was opened");
    const merged = preserveTableLayout(refreshed, schema);
    merged.projectName = schema.projectName;
    await persistSchemaRecord(targetSchemaId, merged);
    if (activeSchemaId !== targetSchemaId) return;
    schema = merged;
    selectedTableId = null;
    selectedTableIds = new Set();
    elements.databaseDriftBanner.hidden = true;
    postgresState.dismissedFingerprint = null;
    render();
    showToast("PostgreSQL changes loaded without changing the layout");
  } catch (error) {
    elements.databaseDriftMessage.textContent = error.message;
    elements.databaseDriftBanner.hidden = false;
  } finally {
    setPostgresBusy(false);
  }
}

async function initializeSchemaLibrary() {
  try {
    const response = await fetch("/api/schemas");
    if (!response.ok) throw new Error("The schema file server is unavailable");
    const payload = await response.json();
    const records = Array.isArray(payload.schemas) ? payload.schemas : [];

    if (!records.length) {
      const record = { id: uid("schema"), schema: schemaForStorage(clone(starterSchema)), updatedAt: new Date().toISOString() };
      const result = await saveRecordFile(record);
      record.revision = result.revision;
      record.updatedAt = result.updatedAt;
      record.layoutToken = result.layoutToken;
      records.push(record);
    }

    records.sort((first, second) => new Date(second.updatedAt) - new Date(first.updatedAt));
    activeSchemaId = records[0].id;
    schemaLibrary = { activeId: activeSchemaId, schemas: records };
    schema = migrateSchema(clone(records.find(record => record.id === activeSchemaId).schema));
    view = clone(schema.layout.view);
    elements.saveStatus.textContent = "Saved to file";
    render();
  } catch (error) {
    activeSchemaId = "schema_unsaved";
    schemaLibrary = { activeId: activeSchemaId, schemas: [{ id: activeSchemaId, schema: clone(schema), updatedAt: new Date().toISOString() }] };
    schema = migrateSchema(clone(schema));
    elements.saveStatus.textContent = "File server offline";
    render();
    showToast("Run python3 server.py to save schema files");
  }
}

function uid(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function postgresNameWithSuffix(base, suffix) {
  const characters = Array.from(base);
  while (new TextEncoder().encode(characters.join("") + suffix).length > 63) characters.pop();
  return characters.join("") + suffix;
}

function defaultPrimaryKeyName(tableName) {
  return postgresNameWithSuffix(tableName, "_pkey");
}

function availablePrimaryKeyName(tableName, tableId = null) {
  const used = new Set(
    schema.tables.filter(table => table.id !== tableId).map(table => table.primaryKey?.name).filter(Boolean)
  );
  let suffix = "_pkey";
  let name = postgresNameWithSuffix(tableName, suffix);
  for (let copy = 2; used.has(name); copy += 1) {
    suffix = `_pkey_${copy}`;
    name = postgresNameWithSuffix(tableName, suffix);
  }
  return name;
}

function uniqueConstraintNameWithUsed(table, columnIds, used) {
  const columnNames = columnIds.map(columnId => table.columns.find(column => column.id === columnId)?.name).filter(Boolean);
  const base = `${table.name}_${columnNames.join("_") || "unique"}`;
  let suffix = "_key";
  let name = postgresNameWithSuffix(base, suffix);
  for (let copy = 2; used.has(name); copy += 1) {
    suffix = `_key_${copy}`;
    name = postgresNameWithSuffix(base, suffix);
  }
  return name;
}

function availableUniqueConstraintName(table, columnIds, constraintId = null) {
  const used = new Set();
  for (const item of schema.tables) {
    used.add(item.name);
    if (item.primaryKey?.name) used.add(item.primaryKey.name);
    for (const constraint of item.uniqueConstraints ?? []) {
      if (constraint.id !== constraintId && constraint.name) used.add(constraint.name);
    }
    for (const index of item.indexes ?? []) if (index.name) used.add(index.name);
  }
  return uniqueConstraintNameWithUsed(table, columnIds, used);
}

function availableCheckConstraintName(table, checkId = null) {
  const used = new Set((table.checks ?? []).filter(check => check.id !== checkId).map(check => check.name));
  let suffix = "_check";
  let name = postgresNameWithSuffix(table.name, suffix);
  for (let copy = 2; used.has(name); copy += 1) {
    suffix = `_check_${copy}`;
    name = postgresNameWithSuffix(table.name, suffix);
  }
  return name;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function elementHasTruncatedText(element) {
  if (!element || element.hidden) return false;
  const style = getComputedStyle(element);
  const lineClamp = Number.parseInt(style.webkitLineClamp, 10);
  const truncates = style.textOverflow === "ellipsis" || (Number.isFinite(lineClamp) && lineClamp > 0);
  if (!truncates) return false;
  return element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1;
}

function automaticTooltipText(element) {
  const value = typeof element.value === "string" ? element.value : element.textContent;
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function findAppTooltipTarget(start, includeDescendants = false) {
  for (let target = start; target && target !== document.body; target = target.parentElement) {
    const automatic = target.dataset?.tooltipAutomatic === "true";
    if (!automatic && (target.dataset?.tooltip || target.getAttribute?.("title"))) return target;
    const truncated = elementHasTruncatedText(target);
    if (automatic) {
      if (!truncated) {
        delete target.dataset.tooltip;
        delete target.dataset.tooltipAutomatic;
      } else {
        target.dataset.tooltip = automaticTooltipText(target);
      }
    }
    if (target.dataset?.tooltip || target.getAttribute?.("title")) return target;
    if (truncated) {
      const text = automaticTooltipText(target);
      if (text) {
        target.dataset.tooltip = text;
        target.dataset.tooltipAutomatic = "true";
        return target;
      }
    }
  }
  if (includeDescendants) {
    for (const target of start?.querySelectorAll?.("*") ?? []) {
      const match = findAppTooltipTarget(target);
      if (match) return match;
    }
  }
  return null;
}

function positionTooltip(target) {
  const targetRect = target.getBoundingClientRect();
  const tooltipRect = elements.tooltip.getBoundingClientRect();
  const gap = 9;
  const margin = 8;
  let placement = target.dataset.tooltipPlacement || (target.closest(".tool-rail") ? "right" : "top");
  let left;
  let top;

  if (placement === "right" && targetRect.right + gap + tooltipRect.width > window.innerWidth - margin) placement = "left";
  if (placement === "left" && targetRect.left - gap - tooltipRect.width < margin) placement = "right";
  if (placement === "top" && targetRect.top - gap - tooltipRect.height < margin) placement = "bottom";
  if (placement === "bottom" && targetRect.bottom + gap + tooltipRect.height > window.innerHeight - margin) placement = "top";

  if (placement === "right" || placement === "left") {
    left = placement === "right" ? targetRect.right + gap : targetRect.left - tooltipRect.width - gap;
    top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
  } else {
    left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
    top = placement === "bottom" ? targetRect.bottom + gap : targetRect.top - tooltipRect.height - gap;
  }

  elements.tooltip.dataset.placement = placement;
  elements.tooltip.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin))}px`;
  elements.tooltip.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - tooltipRect.height - margin))}px`;
}

function showTooltip(target) {
  const nativeTitle = target.getAttribute("title");
  if (nativeTitle) {
    target.dataset.tooltip = nativeTitle;
    target.removeAttribute("title");
  }
  if (!target.dataset.tooltip) return;
  clearTimeout(tooltipHideTimer);
  activeTooltipTarget = target;
  elements.tooltip.textContent = target.dataset.tooltip;
  elements.tooltip.classList.remove("visible");
  elements.tooltip.hidden = false;
  positionTooltip(target);
  requestAnimationFrame(() => {
    if (activeTooltipTarget === target) elements.tooltip.classList.add("visible");
  });
}

function hideTooltip() {
  activeTooltipTarget = null;
  elements.tooltip.classList.remove("visible");
  clearTimeout(tooltipHideTimer);
  tooltipHideTimer = setTimeout(() => { elements.tooltip.hidden = true; }, 150);
}

function updateTooltip(target, text) {
  target.dataset.tooltip = text;
  delete target.dataset.tooltipAutomatic;
  if (activeTooltipTarget !== target) return;
  elements.tooltip.textContent = text;
  positionTooltip(target);
}

function migrateSchema(schema) {
  if (!schema || typeof schema !== "object" || !Array.isArray(schema.tables)) {
    throw new Error("Invalid schema file");
  }
  if (typeof schema.projectName !== "string") schema.projectName = "Untitled schema";
  if (!Array.isArray(schema.relationships)) schema.relationships = [];
  const storedLayout = schema.layout?.tables && typeof schema.layout.tables === "object" ? schema.layout.tables : {};
  for (const [tableIndex, table] of schema.tables.entries()) {
    if (!table || typeof table !== "object" || !Array.isArray(table.columns)) {
      throw new Error("Invalid table in schema file");
    }
    if (!Array.isArray(table.uniqueConstraints)) table.uniqueConstraints = [];
    if (!Array.isArray(table.checks)) table.checks = [];
    if (!Array.isArray(table.indexes)) table.indexes = [];
    if (!Array.isArray(table.triggers)) table.triggers = [];
    const layout = storedLayout[table.id];
    table.x = Number.isFinite(layout?.x) ? layout.x : Number.isFinite(table.x) ? table.x : 100 + (tableIndex % 4) * 370;
    table.y = Number.isFinite(layout?.y) ? layout.y : Number.isFinite(table.y) ? table.y : 100 + Math.floor(tableIndex / 4) * 360;
    table.color = layout?.color || table.color || COLORS[tableIndex % COLORS.length];
    for (const [kind, items] of [["check", table.checks], ["index", table.indexes], ["trigger", table.triggers]]) {
      for (const item of items) if (!item.id) item.id = uid(kind);
    }
    for (const column of table.columns) {
      if (column.default == null) column.default = "";
    }
    table.uniqueConstraints = table.uniqueConstraints.filter(constraint => {
      if (constraint.columnIds?.length !== 1) return true;
      const column = table.columns.find(item => item.id === constraint.columnIds[0]);
      if (column) column.unique = true;
      return false;
    });
    for (const constraint of table.uniqueConstraints) {
      if (!constraint.id) constraint.id = uid("uc");
      if (!constraint.name) constraint.name = uniqueConstraintNameWithUsed(table, constraint.columnIds, new Set());
    }
    const primaryColumnIds = table.columns.filter(column => column.primary).map(column => column.id);
    if (primaryColumnIds.length) {
      table.primaryKey = {
        id: table.primaryKey?.id ?? uid("pk"),
        ...table.primaryKey,
        name: table.primaryKey?.name || defaultPrimaryKeyName(table.name),
        columnIds: primaryColumnIds
      };
    }
  }
  const primaryKeysByName = new Map();
  for (const table of schema.tables) {
    if (!table.primaryKey?.name) continue;
    const owners = primaryKeysByName.get(table.primaryKey.name) ?? [];
    owners.push(table);
    primaryKeysByName.set(table.primaryKey.name, owners);
  }
  const reservedPrimaryKeyNames = new Set(
    [...primaryKeysByName].filter(([, owners]) => owners.length === 1).map(([name]) => name)
  );
  for (const owners of primaryKeysByName.values()) {
    if (owners.length < 2) continue;
    for (const table of owners) {
      let suffix = "_pkey";
      let name = postgresNameWithSuffix(table.name, suffix);
      for (let copy = 2; reservedPrimaryKeyNames.has(name); copy += 1) {
        suffix = `_pkey_${copy}`;
        name = postgresNameWithSuffix(table.name, suffix);
      }
      table.primaryKey.name = name;
      reservedPrimaryKeyNames.add(name);
    }
  }
  const reservedRelationNames = new Set(schema.tables.map(table => table.name));
  for (const table of schema.tables) {
    if (table.primaryKey?.name) reservedRelationNames.add(table.primaryKey.name);
    for (const index of table.indexes) if (index.name) reservedRelationNames.add(index.name);
  }
  const uniqueConstraintsByName = new Map();
  for (const table of schema.tables) {
    for (const constraint of table.uniqueConstraints) {
      const owners = uniqueConstraintsByName.get(constraint.name) ?? [];
      owners.push({ table, constraint });
      uniqueConstraintsByName.set(constraint.name, owners);
    }
  }
  for (const [name, owners] of uniqueConstraintsByName) {
    if (owners.length === 1 && !reservedRelationNames.has(name)) {
      reservedRelationNames.add(name);
      continue;
    }
    for (const { table, constraint } of owners) {
      constraint.name = uniqueConstraintNameWithUsed(table, constraint.columnIds, reservedRelationNames);
      reservedRelationNames.add(constraint.name);
    }
  }
  if (!schema.functions) schema.functions = [];
  if (!Array.isArray(schema.views)) schema.views = [];
  for (const viewItem of schema.views) if (!viewItem.id) viewItem.id = uid("view");
  if (!Array.isArray(schema.functions)) throw new Error("Invalid functions in schema file");
  for (const fn of schema.functions) {
    if (!fn || typeof fn !== "object" || typeof fn.definition !== "string") {
      throw new Error("Invalid function in schema file");
    }
  }
  for (const relationship of schema.relationships) {
    const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
    const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
    if (!fromColumnIds.length || fromColumnIds.length !== toColumnIds.length || fromColumnIds.some(id => typeof id !== "string") || toColumnIds.some(id => typeof id !== "string")) {
      throw new Error("Invalid relationship in schema file");
    }
  }
  if (!schema.layout || typeof schema.layout !== "object") schema.layout = { version: 1, tables: {}, view: { x: 45, y: 35, zoom: 1 } };
  if (!schema.layout.view || typeof schema.layout.view !== "object") schema.layout.view = { x: 45, y: 35, zoom: 1 };
  return schema;
}

function sqlName(value) {
  return /^[a-z_][a-z0-9_]*$/i.test(value) ? value : `"${value.replaceAll('"', '""')}"`;
}

function getTable(tableId) {
  return schema.tables.find(table => table.id === tableId);
}

function getColumn(tableId, columnId) {
  return getTable(tableId)?.columns.find(column => column.id === columnId);
}

function isColumnReferencedInText(columnName, text) {
  if (!text) return false;
  const escaped = columnName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (text.includes(`"${columnName}"`)) return true;
  return new RegExp(`(?<=^|[^\\w$])${escaped}(?=[^\\w$]|$)`, "i").test(text);
}

function isColumnReferencedInObjectName(columnName, objName) {
  if (!objName) return false;
  const escaped = columnName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|_)${escaped}(?:_|$)`).test(objName);
}

function replaceSqlIdentifierToken(value, oldName, newName) {
  if (!value || !oldName || oldName === newName) return value;
  const escaped = oldName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return value.replace(new RegExp(`(?<![\\w$])${escaped}(?![\\w$])`, "g"), newName);
}

function findColumnDependentObjects(table, columnId) {
  const column = table.columns.find(c => c.id === columnId);
  if (!column) return [];
  const { name: colName } = column;
  const result = [];
  for (const [kind, key] of [["check", "checks"], ["index", "indexes"], ["trigger", "triggers"]]) {
    for (const item of table[key] ?? []) {
      if (isColumnReferencedInText(colName, item.definition) || isColumnReferencedInObjectName(colName, item.name)) {
        result.push({ kind, item });
      }
    }
  }
  return result;
}

function columnCheckConstraints(table, column) {
  return (table.checks ?? []).filter(check => {
    const columnIds = check.columnIds ?? [];
    return columnIds.includes(column.id) || (!columnIds.length && isColumnReferencedInText(column.name, check.definition));
  });
}

function updateTableNameInObjects(table, oldName, newName) {
  if (!oldName || !newName || oldName === newName) return;
  const renamePrefix = value => value?.startsWith(`${oldName}_`)
    ? `${newName}${value.slice(oldName.length)}`
    : value;
  const primaryColumnIds = table.columns.filter(column => column.primary).map(column => column.id);
  if (primaryColumnIds.length) {
    table.primaryKey = {
      id: table.primaryKey?.id ?? uid("pk"),
      ...table.primaryKey,
      name: availablePrimaryKeyName(newName, table.id),
      columnIds: primaryColumnIds
    };
  }
  for (const constraint of table.uniqueConstraints ?? []) {
    constraint.name = availableUniqueConstraintName(table, constraint.columnIds, constraint.id);
  }
  for (const key of ["checks", "indexes", "triggers"]) {
    for (const item of table[key] ?? []) {
      const oldItemName = item.name;
      if (item.name) item.name = renamePrefix(item.name);
      if (item.definition) {
        item.definition = replaceSqlIdentifierToken(item.definition, oldItemName, item.name);
        item.definition = replaceSqlIdentifierToken(item.definition, oldName, newName);
      }
    }
  }
  for (const relationship of schema.relationships) {
    if (relationship.fromTableId === table.id) {
      if (relationship.name) relationship.name = renamePrefix(relationship.name);
      if (relationship.constraintName) relationship.constraintName = renamePrefix(relationship.constraintName);
    }
    if (relationship.targetTableName === oldName) relationship.targetTableName = newName;
  }
  if (table.postgres?.parentTable === oldName) table.postgres.parentTable = newName;
}

function updateColumnNameInObjects(table, columnId, oldName, newName) {
  if (!oldName || !newName || oldName === newName) return;
  const escaped = oldName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const nameRe = new RegExp(`(^|_)${escaped}(?=_|$)`, "g");
  const renameToken = value => value?.replace(nameRe, (_, prefix) => `${prefix}${newName}`);
  for (const constraint of table.uniqueConstraints ?? []) {
    if (constraint.columnIds.includes(columnId)) {
      constraint.name = availableUniqueConstraintName(table, constraint.columnIds, constraint.id);
    }
  }
  for (const key of ["checks", "indexes", "triggers"]) {
    for (const item of table[key] ?? []) {
      const oldItemName = item.name;
      if (item.name) item.name = renameToken(item.name);
      if (item.definition) {
        item.definition = replaceSqlIdentifierToken(item.definition, oldItemName, item.name);
        item.definition = replaceSqlIdentifierToken(item.definition, oldName, newName);
      }
    }
  }
  for (const relationship of schema.relationships) {
    if (relationship.fromTableId !== table.id) continue;
    if (relationship.name) relationship.name = renameToken(relationship.name);
    if (relationship.constraintName) relationship.constraintName = renameToken(relationship.constraintName);
  }
}

function relationshipColumnPairs(relationship) {
  const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
  const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
  return fromColumnIds.map((fromColumnId, index) => ({ fromColumnId, toColumnId: toColumnIds[index] }));
}

function setRelationshipColumnPairs(relationship, pairs) {
  delete relationship.fromColumnId;
  delete relationship.toColumnId;
  delete relationship.fromColumnIds;
  delete relationship.toColumnIds;
  if (pairs.length === 1) {
    relationship.fromColumnId = pairs[0].fromColumnId;
    relationship.toColumnId = pairs[0].toColumnId;
  } else {
    relationship.fromColumnIds = pairs.map(pair => pair.fromColumnId);
    relationship.toColumnIds = pairs.map(pair => pair.toColumnId);
  }
  return relationship;
}

function reorderRelationshipPair(pairs, fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= pairs.length || toIndex >= pairs.length) return false;
  const [moved] = pairs.splice(fromIndex, 1);
  pairs.splice(toIndex, 0, moved);
  return true;
}

function dropRelationshipPair(pairs, fromIndex, targetIndex, after) {
  if (fromIndex === targetIndex || fromIndex < 0 || targetIndex < 0 || fromIndex >= pairs.length || targetIndex >= pairs.length) return false;
  const target = pairs[targetIndex];
  const [moved] = pairs.splice(fromIndex, 1);
  const currentTargetIndex = pairs.indexOf(target);
  pairs.splice(currentTargetIndex + (after ? 1 : 0), 0, moved);
  return true;
}

function columnForeignKeyRelationships(table, column) {
  const relationships = schema.relationships.filter(relationship => (
    relationship.fromTableId === table.id
    && relationshipColumnPairs(relationship).some(pair => pair.fromColumnId === column.id)
  ));
  return {
    single: relationships.filter(relationship => relationshipColumnPairs(relationship).length === 1),
    composite: relationships.filter(relationship => relationshipColumnPairs(relationship).length > 1)
  };
}

function columnDatabaseIconTargets(kind, table, column) {
  if (kind === "primary-key") {
    return column.primary ? [{ kind, tableId: table.id, columnId: column.id, label: table.primaryKey?.name || defaultPrimaryKeyName(table.name) }] : [];
  }
  if (kind === "column-unique") {
    return column.unique && !column.primary ? [{ kind, tableId: table.id, columnId: column.id, label: `${table.name}.${column.name} unique` }] : [];
  }
  if (kind === "column-nullable") {
    return column.nullable ? [{ kind, tableId: table.id, columnId: column.id, label: `${table.name}.${column.name} nullable` }] : [];
  }
  if (kind === "composite-unique") {
    return (table.uniqueConstraints ?? [])
      .filter(constraint => constraint.columnIds.includes(column.id))
      .map(constraint => ({ kind, tableId: table.id, id: constraint.id, label: constraint.name || "Composite unique" }));
  }
  if (kind === "check") {
    return columnCheckConstraints(table, column).map(check => ({ kind, tableId: table.id, id: check.id, label: check.name }));
  }
  const foreignKeys = columnForeignKeyRelationships(table, column);
  const relationships = kind === "foreign-key" ? foreignKeys.single : kind === "composite-foreign-key" ? foreignKeys.composite : [];
  return relationships.map(relationship => ({
    kind: "relationship",
    tableId: table.id,
    id: relationship.id,
    label: relationshipConstraintName(relationship) || "Unnamed foreign key"
  }));
}

function relationshipConstraintName(relationship) {
  return relationship.constraintName || relationship.name || "";
}

function availableRelationshipName(relationship, pairs, relationshipId = null) {
  const source = getTable(relationship.fromTableId);
  const columnNames = pairs.map(pair => getColumn(relationship.fromTableId, pair.fromColumnId)?.name).filter(Boolean);
  const base = `${source?.name ?? "relationship"}_${columnNames.join("_") || "foreign_key"}`;
  const used = new Set(schema.relationships
    .filter(item => item.id !== relationshipId && item.fromTableId === relationship.fromTableId)
    .map(relationshipConstraintName)
    .filter(Boolean));
  let suffix = "_fkey";
  let name = postgresNameWithSuffix(base, suffix);
  for (let copy = 2; used.has(name); copy += 1) {
    suffix = `_fkey_${copy}`;
    name = postgresNameWithSuffix(base, suffix);
  }
  return name;
}

function tableHasReferencedKey(table, columnIds) {
  const primaryColumnIds = table.primaryKey?.columnIds?.length
    ? table.primaryKey.columnIds
    : table.columns.filter(column => column.primary).map(column => column.id);
  const keys = [primaryColumnIds, ...(table.uniqueConstraints ?? []).map(constraint => constraint.columnIds)];
  for (const column of table.columns) if (column.unique) keys.push([column.id]);
  return keys.some(key => key.length === columnIds.length && key.every(columnId => columnIds.includes(columnId)));
}

function validateRelationshipDraft(relationship, pairs, constraintName) {
  const source = getTable(relationship.fromTableId);
  const target = getTable(relationship.toTableId);
  if (!source || !target) return "Both relationship tables must be in this design";
  if (!constraintName.trim()) return "Constraint name is required";
  if (new TextEncoder().encode(constraintName.trim()).length > 63) return "Constraint name must be at most 63 bytes";
  if (!pairs.length) return "Add at least one column pair";
  const fromIds = pairs.map(pair => pair.fromColumnId);
  const toIds = pairs.map(pair => pair.toColumnId);
  if (new Set(fromIds).size !== fromIds.length) return "Each foreign key column can appear only once";
  if (new Set(toIds).size !== toIds.length) return "Each referenced column can appear only once";
  if (fromIds.some(columnId => !getColumn(source.id, columnId)) || toIds.some(columnId => !getColumn(target.id, columnId))) return "Choose a column for every pair";
  if (!tableHasReferencedKey(target, toIds)) return "Referenced columns must match a primary or unique key";
  const duplicateName = schema.relationships.some(item => item.id !== relationship.id
    && item.fromTableId === source.id && relationshipConstraintName(item) === constraintName.trim());
  if (duplicateName) return "That foreign key name already exists on the source table";
  const duplicateRelationship = schema.relationships.some(item => {
    if (item.id === relationship.id || item.fromTableId !== source.id || item.toTableId !== target.id) return false;
    const existing = relationshipColumnPairs(item);
    return existing.length === pairs.length && existing.every(pair => pairs.some(candidate => (
      candidate.fromColumnId === pair.fromColumnId && candidate.toColumnId === pair.toColumnId
    )));
  });
  return duplicateRelationship ? "That relationship already exists" : "";
}

function relationshipIncludesColumn(relationship, columnId) {
  return relationshipColumnPairs(relationship).some(pair => pair.fromColumnId === columnId || pair.toColumnId === columnId);
}

function saveSchema(delay = SAVE_DELAY_MS) {
  elements.saveStatus.textContent = "Saving...";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    saveTimer = null;
    try {
      await persistCurrentSchema();
      elements.saveStatus.textContent = "Saved to file";
      elements.databaseDriftBanner.hidden = true;
    } catch (error) {
      reportSaveError(error);
    }
  }, delay);
}

function reportSaveError(error) {
  const conflict = error?.code === "schema_conflict" || error?.code === "layout_conflict";
  elements.saveStatus.textContent = conflict ? "Save conflict" : "Save failed";
  showToast(conflict ? "This design or its layout changed in another session. Reload before saving" : "Could not save the schema file");
}

function persistSchemaRecord(schemaId, schemaValue) {
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    const library = readSchemaLibrary();
    const record = library.schemas.find(item => item.id === schemaId);
    const savedRecord = {
      id: schemaId,
      revision: record?.revision ?? 0,
      layoutToken: record?.layoutToken,
      schema: schemaForStorage(schemaValue, schemaId === activeSchemaId ? view : schemaValue.layout?.view),
      updatedAt: record?.updatedAt ?? new Date().toISOString()
    };
    const result = await putRecordFile(savedRecord);
    savedRecord.revision = result.revision;
    savedRecord.updatedAt = result.updatedAt;
    savedRecord.layoutToken = result.layoutToken;
    if (record) Object.assign(record, savedRecord);
    else library.schemas.push(savedRecord);
    library.activeId = schemaId;
    writeSchemaLibrary(library);
  });
  return saveQueue;
}

function persistCurrentSchema() {
  return persistSchemaRecord(activeSchemaId, schema);
}

async function saveSchemaNow(showConfirmation = false) {
  clearTimeout(saveTimer);
  saveTimer = null;
  try {
    await persistCurrentSchema();
    elements.saveStatus.textContent = "Saved to file";
    elements.databaseDriftBanner.hidden = true;
    if (showConfirmation) showToast(`${schema.projectName || "Untitled schema"} saved to file`);
  } catch (error) {
    reportSaveError(error);
  }
}

async function flushPendingSave() {
  try {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
      await persistCurrentSchema();
    } else {
      await saveQueue;
    }
    elements.saveStatus.textContent = "Saved to file";
  } catch (error) {
    reportSaveError(error);
    throw error;
  }
}

function captureHistoryState() {
  return JSON.stringify({ schema, selectedTableId, selectedTableIds: [...selectedTableIds] });
}

function updateHistoryControls() {
  elements.undoButton.disabled = undoStack.length === 0;
  elements.redoButton.disabled = redoStack.length === 0;
}

function checkpointHistory(group = null) {
  if (group && group === historyGroup) return;
  const snapshot = captureHistoryState();
  if (undoStack.at(-1) !== snapshot) {
    undoStack.push(snapshot);
    if (undoStack.length > 100) undoStack.shift();
  }
  redoStack = [];
  historyGroup = group;
  updateHistoryControls();
}

function endHistoryGroup() {
  historyGroup = null;
}

function restoreHistoryState(snapshot) {
  const restored = JSON.parse(snapshot);
  schema = restored.schema;
  const availableTableIds = new Set(schema.tables.map(table => table.id));
  selectedTableIds = new Set((restored.selectedTableIds || [restored.selectedTableId]).filter(tableId => availableTableIds.has(tableId)));
  selectedTableId = selectedTableIds.has(restored.selectedTableId) ? restored.selectedTableId : [...selectedTableIds].at(-1) || null;
  historyGroup = null;
  setRelationMode(false);
  elements.saveStatus.textContent = "Saving...";
  persistCurrentSchema()
    .then(() => { elements.saveStatus.textContent = "Saved to file"; })
    .catch(reportSaveError);
  render();
}

function undo() {
  const snapshot = undoStack.pop();
  if (!snapshot) return showToast("Nothing to undo");
  redoStack.push(captureHistoryState());
  updateHistoryControls();
  restoreHistoryState(snapshot);
  showToast("Change undone");
}

function redo() {
  const snapshot = redoStack.pop();
  if (!snapshot) return showToast("Nothing to redo");
  undoStack.push(captureHistoryState());
  updateHistoryControls();
  restoreHistoryState(snapshot);
  showToast("Change redone");
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2200);
}

function resetSchemaSession() {
  clearTimeout(inspectorDismissTransitionTimer);
  inspectorDismissTransitionTimer = null;
  selectedTableId = null;
  selectedTableIds = new Set();
  inspectorDismissed = false;
  inspectorContentCollapsed = false;
  middlePanPanelSnapshot = null;
  panState = null;
  marqueeState = null;
  clearTimeout(wheelZoomTimer);
  wheelZoomTimer = null;
  relationSource = null;
  relationMode = false;
  undoStack = [];
  redoStack = [];
  historyGroup = null;
  updateHistoryControls();
  elements.workspace.classList.remove("relation-mode", "selecting", "panning", "table-dragging", "zooming");
  elements.selectionMarquee.hidden = true;
  elements.relationTool.classList.remove("active");
  elements.selectTool.classList.add("active");
  elements.relationBanner.hidden = true;
  elements.mainLayout.classList.remove("inspector-dismissed", "inspector-content-collapsed", "inspector-content-expanding");
  elements.inspector.classList.remove("mobile-open");
}

async function createNewSchema() {
  return createSchemaProject("Untitled schema");
}

async function createSchemaProject(projectName) {
  try {
    await flushPendingSave();
  } catch {
    return false;
  }
  const newSchemaId = uid("schema");
  const newSchema = { projectName, tables: [], relationships: [], functions: [] };
  try {
    await persistSchemaRecord(newSchemaId, newSchema);
  } catch {
    elements.saveStatus.textContent = "Save failed";
    showToast("Could not create the schema file");
    return false;
  }
  activeSchemaId = newSchemaId;
  schema = newSchema;
  view = { x: 45, y: 35, zoom: 1 };
  resetSchemaSession();
  elements.saveStatus.textContent = "Saved to file";
  if (elements.schemaDialog.open) elements.schemaDialog.close();
  render();
  showToast("New schema created");
  return true;
}

async function openSchema(schemaId, { fit = true } = {}) {
  if (schemaId === activeSchemaId) {
    if (elements.schemaDialog.open) elements.schemaDialog.close();
    return true;
  }
  try {
    await flushPendingSave();
  } catch {
    return false;
  }
  const library = readSchemaLibrary();
  const record = library.schemas.find(item => item.id === schemaId);
  if (!record) {
    showToast("That schema could not be found");
    return false;
  }
  activeSchemaId = record.id;
  schema = migrateSchema(clone(record.schema));
  library.activeId = activeSchemaId;
  writeSchemaLibrary(library);
  view = clone(schema.layout.view);
  resetSchemaSession();
  if (elements.schemaDialog.open) elements.schemaDialog.close();
  render();
  if (fit) requestAnimationFrame(fitDiagram);
  showToast(`${schema.projectName || "Untitled schema"} opened`);
  return true;
}

async function deleteSavedSchema(schemaId) {
  if (schemaId === activeSchemaId) return showToast("Open another schema before deleting this one");
  const library = readSchemaLibrary();
  const record = library.schemas.find(item => item.id === schemaId);
  if (!record || !confirm(`Delete ${record.schema.projectName || "Untitled schema"}?`)) return;
  await saveQueue.catch(() => {});
  try {
    const response = await fetch(`/api/schemas/${encodeURIComponent(schemaId)}`, { method: "DELETE" });
    if (!response.ok) return showToast("Could not delete the schema file");
  } catch {
    return showToast("Could not delete the schema file");
  }
  library.schemas = library.schemas.filter(item => item.id !== schemaId);
  writeSchemaLibrary(library);
  renderSchemaLibrary();
  showToast("Schema deleted");
}

function formatSavedDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently saved";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function postgresConnectionType(profile) {
  if (!profile) return "Linked DB";
  const host = String(profile.host ?? "").trim().toLowerCase();
  if (["127.0.0.1", "localhost", "::1"].includes(host)) return "Local DB";
  if (host === "postgres") return "Docker DB";
  if (host === "host.docker.internal") return "Host DB";
  return "Remote DB";
}

function schemaLibraryConnection(schemaValue) {
  const source = schemaValue?.postgres;
  if (!source || typeof source !== "object" || (!source.sourceProfileId && !source.database)) {
    return { type: "Local project", identity: "No PostgreSQL connection" };
  }
  const profile = postgresState.profiles.find(item => item.id === source.sourceProfileId) ?? null;
  const database = profile?.dbname || source.database || "Unknown database";
  const target = source.namespace ? `${database}.${source.namespace}` : database;
  if (!profile) return { type: "Linked DB", identity: `${source.sourceProfileId || "Unknown connection"} · ${target}` };
  return { type: postgresConnectionType(profile), identity: `${profile.name} (${profile.id}) · ${target}` };
}

async function loadSchemaLibraryConnections() {
  try {
    const payload = await postgresRequest("/api/postgres/profiles", { method: "GET" });
    postgresState.profiles = payload.profiles ?? [];
    renderSchemaLibrary();
  } catch {
    // Schema cards still show their saved database identity when profile metadata is unavailable.
  }
}

function renderSchemaLibrary() {
  const library = readSchemaLibrary();
  const records = [...library.schemas].sort((first, second) => {
    if (first.id === activeSchemaId) return -1;
    if (second.id === activeSchemaId) return 1;
    return new Date(second.updatedAt) - new Date(first.updatedAt);
  });
  elements.schemaLibraryCount.textContent = `${records.length} saved schema${records.length === 1 ? "" : "s"}`;
  elements.schemaLibrary.innerHTML = records.map(record => {
    const isCurrent = record.id === activeSchemaId;
    const tableCount = record.schema.tables.length;
    const columnCount = record.schema.tables.reduce((total, table) => total + table.columns.length, 0);
    const connection = schemaLibraryConnection(record.schema);
    return `
      <article class="schema-library-item ${isCurrent ? "current" : ""}">
        <div class="schema-library-copy">
          <div class="schema-library-name"><span>${escapeHtml(record.schema.projectName || "Untitled schema")}</span>${isCurrent ? '<span class="current-badge">Current</span>' : ""}</div>
          <div class="schema-library-meta"><span>${tableCount} table${tableCount === 1 ? "" : "s"}</span><span>${columnCount} columns</span><span>${escapeHtml(formatSavedDate(record.updatedAt))}</span></div>
          <div class="schema-library-connection"><span>${escapeHtml(connection.type)}</span><strong>${escapeHtml(connection.identity)}</strong></div>
        </div>
        <div class="schema-library-actions">
          <button class="button button-ghost" data-library-action="open" data-schema-id="${record.id}" type="button" ${isCurrent ? "disabled" : ""}>${isCurrent ? "Open" : "Open"}</button>
          <button class="library-delete" data-library-action="delete" data-schema-id="${record.id}" data-tooltip="Delete schema" aria-label="Delete schema" type="button" ${isCurrent ? "disabled" : ""}>
            <svg viewBox="0 0 20 20"><path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/></svg>
          </button>
        </div>
      </article>
    `;
  }).join("");
}

function render() {
  elements.projectName.value = schema.projectName;
  renderTables();
  renderConnections();
  renderInspector();
  applyView();
}

function renderTables() {
  elements.tablesLayer.innerHTML = schema.tables.map(table => `
    <article class="table-card ${selectedTableIds.has(table.id) ? "selected" : ""}" data-table-id="${table.id}" style="left:${table.x}px; top:${table.y}px; --table-color:${table.color}">
      <header class="table-header">
        <span class="table-accent"></span>
        <span class="table-name">${escapeHtml(table.name)}</span>
        <span class="table-count">${table.columns.length} col${(table.uniqueConstraints?.length ?? 0) ? ` · ${table.uniqueConstraints.length} uc` : ""}</span>
      </header>
      <div class="table-columns">
        ${table.columns.map(column => {
          const ucCount = (table.uniqueConstraints ?? []).filter(uc => uc.columnIds.includes(column.id)).length;
          const columnChecks = columnCheckConstraints(table, column);
          const foreignKeys = columnForeignKeyRelationships(table, column);
          const checkTooltip = `Check constraint${columnChecks.length === 1 ? "" : "s"}: ${columnChecks.map(check => check.name).join(", ")}`;
          const foreignKeyTooltip = `Foreign key${foreignKeys.single.length === 1 ? "" : "s"}: ${foreignKeys.single.map(relationship => relationshipConstraintName(relationship) || "Unnamed foreign key").join(", ")}`;
          const compositeForeignKeyTooltip = `Composite foreign key${foreignKeys.composite.length === 1 ? "" : "s"}: ${foreignKeys.composite.map(relationship => relationshipConstraintName(relationship) || "Unnamed foreign key").join(", ")}`;
          const hasKeyIcon = column.primary || foreignKeys.single.length || foreignKeys.composite.length;
          return `
          <div class="table-column ${relationSource?.columnId === column.id ? "relation-source" : ""}" data-column-id="${column.id}">
            <span class="key-badge ${column.primary ? "primary" : ""}">
              ${column.primary ? '<svg class="database-object-icon" data-object-icon="primary-key" viewBox="0 0 16 16" role="img" aria-label="Primary key" data-tooltip="Primary key · right-click to edit"><circle cx="5" cy="7" r="3"/><path d="m7.5 8.5 5 4M10 10.5l1.5-1.5"/></svg>' : ""}
              ${foreignKeys.single.length ? `<span class="database-object-icon foreign-key-icon" data-object-icon="foreign-key"><svg class="constraint-icon" viewBox="0 0 16 16" role="img" aria-label="${foreignKeys.single.length} foreign key${foreignKeys.single.length === 1 ? "" : "s"}" data-tooltip="${escapeHtml(foreignKeyTooltip)} · right-click to edit"><circle cx="5" cy="7" r="3"/><path d="m7.5 8.5 5 4M10 10.5l1.5-1.5"/></svg>${foreignKeys.single.length > 1 ? `<span class="foreign-key-count">${foreignKeys.single.length}</span>` : ""}</span>` : ""}
              ${foreignKeys.composite.length ? `<span class="database-object-icon composite-foreign-key-icon" data-object-icon="composite-foreign-key"><svg class="constraint-icon" viewBox="0 0 19 16" role="img" aria-label="${foreignKeys.composite.length} composite foreign key${foreignKeys.composite.length === 1 ? "" : "s"}" data-tooltip="${escapeHtml(compositeForeignKeyTooltip)} · right-click to edit"><g transform="translate(0 -1.5)"><circle cx="5" cy="7" r="2.6"/><path d="m7.2 8.3 4.5 3.7M9.3 10l1.3-1.3"/></g><g transform="translate(4 2)"><circle cx="5" cy="7" r="2.6"/><path d="m7.2 8.3 4.5 3.7M9.3 10l1.3-1.3"/></g></svg>${foreignKeys.composite.length > 1 ? `<span class="foreign-key-count">${foreignKeys.composite.length}</span>` : ""}</span>` : ""}
              ${hasKeyIcon ? "" : "·"}
            </span>
            <span class="column-name">${escapeHtml(column.name)}</span>
            <span class="column-constraints">
              ${column.nullable ? '<svg class="database-object-icon constraint-icon nullable-icon" data-object-icon="column-nullable" viewBox="0 0 12 12" role="img" aria-label="Nullable" data-tooltip="Nullable · right-click to edit"><circle cx="6" cy="6" r="4"/></svg>' : ""}
              ${column.unique && !column.primary ? '<svg class="database-object-icon constraint-icon unique-icon" data-object-icon="column-unique" viewBox="0 0 12 12" role="img" aria-label="Unique" data-tooltip="Unique · right-click to edit"><path d="M6 1.5 10.5 6 6 10.5 1.5 6Z"/></svg>' : ""}
              ${ucCount ? '<span class="database-object-icon uc-composite-icon" data-object-icon="composite-unique"><svg class="constraint-icon" viewBox="0 0 12 12" role="img" aria-label="Composite unique" data-tooltip="Composite unique · right-click to edit"><path d="M3.5 2v5.5a2.5 2.5 0 0 0 5 0V2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' + (ucCount > 1 ? '<span class="uc-multi-count">' + ucCount + '</span>' : "") + '</span>' : ""}
              ${columnChecks.length ? `<span class="database-object-icon check-constraint-icon" data-object-icon="check"><svg class="constraint-icon" viewBox="0 0 12 12" role="img" aria-label="${columnChecks.length} check constraint${columnChecks.length === 1 ? "" : "s"}" data-tooltip="${escapeHtml(checkTooltip)} · right-click to edit"><rect x="1.5" y="1.5" width="9" height="9" rx="2"/><path d="m3.3 6 1.6 1.6 3.8-4"/></svg>${columnChecks.length > 1 ? `<span class="check-multi-count">${columnChecks.length}</span>` : ""}</span>` : ""}
            </span>
            <span class="column-type">${escapeHtml(column.type)}</span>
            <span class="column-port"></span>
          </div>
        `;
          }).join("")}
      </div>
    </article>
  `).join("");
}

function collectColumnMetrics() {
  const columnMetrics = new Map();
  document.querySelectorAll(".table-card").forEach(card => {
    card.querySelectorAll(".table-column").forEach(row => {
      columnMetrics.set(`${card.dataset.tableId}:${row.dataset.columnId}`, {
        top: row.offsetTop,
        height: row.offsetHeight
      });
    });
  });
  return columnMetrics;
}

function connectionGeometry(relationship, pair, columnMetrics) {
  const fromTable = getTable(relationship.fromTableId);
  const toTable = getTable(relationship.toTableId);
  if (!fromTable || !toTable) return null;
  const fromRow = columnMetrics.get(`${relationship.fromTableId}:${pair.fromColumnId}`);
  const toRow = columnMetrics.get(`${relationship.toTableId}:${pair.toColumnId}`);
  if (!fromRow || !toRow) return null;
  const goesRight = fromTable.x < toTable.x;
  const fromX = fromTable.x + (goesRight ? TABLE_WIDTH : 0);
  const toX = toTable.x + (goesRight ? 0 : TABLE_WIDTH);
  const fromY = fromTable.y + fromRow.top + fromRow.height / 2;
  const toY = toTable.y + toRow.top + toRow.height / 2;
  const distance = Math.max(50, Math.abs(toX - fromX) * .48);
  const controlOne = fromX + (goesRight ? distance : -distance);
  const controlTwo = toX + (goesRight ? -distance : distance);
  return { fromX, fromY, toX, toY, path: `M ${fromX} ${fromY} C ${controlOne} ${fromY}, ${controlTwo} ${toY}, ${toX} ${toY}` };
}

function renderConnections() {
  const paths = [];
  const columnMetrics = collectColumnMetrics();
  for (const relationship of schema.relationships) {
    for (const pair of relationshipColumnPairs(relationship)) {
      const geometry = connectionGeometry(relationship, pair, columnMetrics);
      if (!geometry) continue;
      const selected = selectedTableIds.has(relationship.fromTableId) || selectedTableIds.has(relationship.toTableId);
      paths.push(`<path class="connection-shadow" d="${geometry.path}"/><path class="connection-hit" data-relationship-id="${escapeHtml(relationship.id)}" d="${geometry.path}"/><path class="connection-line ${selected ? "selected" : ""}" d="${geometry.path}"/><circle class="connection-dot ${selected ? "selected" : ""}" cx="${geometry.fromX}" cy="${geometry.fromY}" r="4"/><circle class="connection-dot ${selected ? "selected" : ""}" cx="${geometry.toX}" cy="${geometry.toY}" r="4"/>`);
    }
  }
  elements.connections.innerHTML = paths.join("");
}

function renderInspector() {
  const selectedTable = getTable(selectedTableId);
  if (!selectedTable) inspectorContentCollapsed = false;
  elements.mainLayout.classList.toggle("inspector-collapsed", !selectedTable);
  elements.mainLayout.classList.toggle("inspector-dismissed", Boolean(selectedTable) && inspectorDismissed);
  elements.mainLayout.classList.toggle("inspector-content-collapsed", Boolean(selectedTable) && inspectorContentCollapsed);
  elements.inspectorEmpty.hidden = Boolean(selectedTable);
  elements.inspectorContent.hidden = !selectedTable;
  renderTableDataPanel(selectedTable);

  const columnCount = schema.tables.reduce((total, table) => total + table.columns.length, 0);
  elements.schemaStats.innerHTML = `
    <div><dt>Tables</dt><dd>${schema.tables.length}</dd></div>
    <div><dt>Columns</dt><dd>${columnCount}</dd></div>
    <div><dt>Links</dt><dd>${schema.relationships.length}</dd></div>
    <div><dt>Functions</dt><dd>${(schema.functions ?? []).length}</dd></div>
  `;
  if (!selectedTable) return;

  const related = schema.relationships.filter(relation => relation.fromTableId === selectedTable.id || relation.toTableId === selectedTable.id);
  const checks = selectedTable.checks ?? [];
  const indexes = selectedTable.indexes ?? [];
  const triggers = selectedTable.triggers ?? [];
  const liveDataAvailable = Boolean(tableDataTarget(selectedTable));
  const primaryColumns = selectedTable.columns.filter(column => column.primary);
  const primaryKeyName = primaryColumns.length ? (selectedTable.primaryKey?.name || defaultPrimaryKeyName(selectedTable.name)) : "";
  elements.inspectorContent.innerHTML = `
    <div class="inspector-head">
      <button class="inspector-head-toggle" data-action="toggle-inspector-content" type="button" aria-expanded="${!inspectorContentCollapsed}" aria-label="${tableDataPanelExpanded ? "Minimize data tools" : inspectorContentCollapsed ? "Expand table properties" : "Collapse table properties"}"></button>
      <div class="inspector-head-top">
        <span class="eyebrow">Table properties</span>
        <div class="inspector-head-actions">
          ${liveDataAvailable ? `<button class="icon-button ${tableDataPanelExpanded ? "active" : ""}" data-action="toggle-table-data" data-tooltip="${tableDataPanelExpanded ? "Minimize" : "Show"} data tools" type="button" aria-label="${tableDataPanelExpanded ? "Minimize" : "Show"} data tools" aria-pressed="${tableDataPanelExpanded}">
            <svg viewBox="0 0 20 20"><rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M3 8h14M3 12h14"/></svg>
          </button>` : ""}
          <button class="icon-button" data-action="copy-table" data-tooltip="Copy table (Ctrl+C)" aria-label="Copy table" type="button">
            <svg viewBox="0 0 20 20"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/></svg>
          </button>
          <button class="icon-button" data-action="paste-table" data-tooltip="Paste table (Ctrl+V)" aria-label="Paste table" type="button" ${copiedTable ? "" : "disabled"}>
            <svg viewBox="0 0 20 20"><path d="M7 5V4h6v1M6 5h8a1 1 0 0 1 1 1v10H5V6a1 1 0 0 1 1-1Z"/><path d="M8 10h5M10.5 7.5v5"/></svg>
          </button>
          <button class="icon-button danger" data-action="delete-table" data-tooltip="Delete table" aria-label="Delete table" type="button">
            <svg viewBox="0 0 20 20"><path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/></svg>
          </button>
          <button class="icon-button" data-action="close-inspector" data-tooltip="Close table workspace (Esc)" type="button" aria-label="Close table workspace">
            <svg viewBox="0 0 20 20"><path d="m5 5 10 10M15 5 5 15"/></svg>
          </button>
        </div>
      </div>
      <h2 data-tooltip="${escapeHtml(selectedTable.name)}">${escapeHtml(selectedTable.name)}</h2>
    </div>
    <div class="inspector-body">
      ${selectedTableIds.size > 1 ? `<div class="multi-selection-notice"><strong>${selectedTableIds.size} tables selected</strong><span>Drag any selected table to move the group.</span></div>` : ""}
      <section class="inspector-section">
      <div class="section-title"><h3>General</h3></div>
      <label class="field"><span>Table name</span><input class="text-input" data-field="table-name" value="${escapeHtml(selectedTable.name)}" /></label>
      ${primaryColumns.length ? `<label class="field"><span>Primary key name</span><input class="text-input" data-field="primary-key-name" maxlength="63" value="${escapeHtml(primaryKeyName)}" /></label>` : ""}
      <div class="field"><span>Accent color</span><div class="color-row">
        ${COLORS.map(color => `<button class="color-swatch ${selectedTable.color === color ? "active" : ""}" style="--swatch:${color}" data-color="${color}" type="button" aria-label="Use ${color}"></button>`).join("")}
      </div></div>
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Columns</h3><span>${selectedTable.columns.length}</span></div>
      <div id="column-editors">
        ${selectedTable.columns.map(column => renderColumnEditor(column)).join("")}
      </div>
      <button class="add-column" data-action="add-column" type="button">+ Add column</button>
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Unique Constraints</h3><span>${selectedTable.uniqueConstraints?.length ?? 0}</span></div>
      <div id="unique-constraint-list">
        ${renderUniqueConstraintsList(selectedTable)}
      </div>
      <button class="add-column add-uc" data-action="add-uc" type="button" ${selectedTable.columns.length < 2 ? "disabled" : ""}>+ Add unique constraint</button>
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Check Constraints</h3><span>${checks.length}</span></div>
      ${checks.length ? `<div class="inspector-trigger-list">${checks.map(check => renderInspectorCheck(check)).join("")}</div>` : '<div class="no-relationships">No check constraints on this table.</div>'}
      <button class="add-column add-trigger" data-action="add-check" type="button">+ Add check constraint</button>
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Indexes</h3><span>${indexes.length}</span></div>
      ${indexes.length ? `<div class="inspector-trigger-list">${indexes.map(index => renderInspectorIndex(index)).join("")}</div>` : '<div class="no-relationships">No indexes on this table.</div>'}
      <button class="add-column add-trigger" data-action="add-index" type="button">+ Add index</button>
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Relationships</h3><span>${related.length}</span></div>
      ${related.length ? `<div class="relationship-list">${related.map(relation => renderRelationshipItem(relation)).join("")}</div>` : '<div class="no-relationships">No relationships yet. Use the link tool and select two columns.</div>'}
    </section>
    <section class="inspector-section">
      <div class="section-title"><h3>Triggers</h3><span>${triggers.length}</span></div>
      ${triggers.length ? `<div class="inspector-trigger-list">${triggers.map(trigger => renderInspectorTrigger(trigger)).join("")}</div>` : '<div class="no-relationships">No triggers on this table.</div>'}
      <button class="add-column add-trigger" data-action="add-trigger" type="button">+ Add trigger</button>
      </section>
    </div>
  `;
}

function describeInspectorIndex(index) {
  const definition = index.definition ?? "";
  const unique = Boolean(index.unique || /^\s*CREATE\s+UNIQUE\s+INDEX\b/i.test(definition));
  const method = (index.method || definition.match(/\bUSING\s+([A-Za-z0-9_]+)/i)?.[1] || "btree").toUpperCase();
  const keyMatch = definition.match(/\bUSING\s+[A-Za-z0-9_]+\s*\(([\s\S]*?)\)\s*(?:WHERE\b|$)/i);
  const keys = keyMatch?.[1].replace(/\s+/g, " ").trim() ?? "";
  const predicate = definition.match(/\bWHERE\s+([\s\S]+?);?\s*$/i)?.[1].replace(/\s+/g, " ").replace(/;$/, "").trim() ?? "";
  return {
    badge: unique ? "unique" : "index",
    summary: `${unique ? "UNIQUE " : ""}${method}${keys ? ` (${keys})` : ""}`,
    predicate: predicate ? `WHERE ${predicate}` : ""
  };
}

function describeInspectorCheck(check) {
  return (check.definition ?? "CHECK constraint").replace(/\s+/g, " ").trim();
}

function renderInspectorCheck(check) {
  const definition = describeInspectorCheck(check);
  return `
    <button class="inspector-trigger-item" data-check-id="${escapeHtml(check.id)}" type="button">
      <span class="inspector-trigger-copy">
        <strong>${escapeHtml(check.name)}</strong>
        <small data-tooltip="${escapeHtml(definition)}">${escapeHtml(definition)}</small>
      </span>
      <span class="inspector-trigger-mode">check</span>
    </button>
  `;
}

function renderInspectorIndex(index) {
  const description = describeInspectorIndex(index);
  return `
    <button class="inspector-trigger-item" data-index-id="${escapeHtml(index.id)}" type="button">
      <span class="inspector-trigger-copy">
        <strong>${escapeHtml(index.name)}</strong>
        <small>${escapeHtml(description.summary)}</small>
        ${description.predicate ? `<small class="inspector-index-predicate" data-tooltip="${escapeHtml(description.predicate)}">${escapeHtml(description.predicate)}</small>` : ""}
      </span>
      <span class="inspector-trigger-mode">${description.badge}</span>
    </button>
  `;
}

function renderInspectorTrigger(trigger) {
  const mode = { O: "enabled", D: "disabled", R: "replica", A: "always" }[trigger.enabled ?? "O"] ?? "enabled";
  const timing = trigger.definition.match(/\b(BEFORE|AFTER|INSTEAD\s+OF)\s+(.+?)\s+ON\b/is);
  const summary = timing ? `${timing[1]} ${timing[2]}`.replace(/\s+/g, " ").toUpperCase() : "PostgreSQL trigger";
  return `
    <button class="inspector-trigger-item" data-trigger-id="${escapeHtml(trigger.id)}" type="button">
      <span class="inspector-trigger-copy">
        <strong>${escapeHtml(trigger.name)}</strong>
        <small>${escapeHtml(summary)}</small>
      </span>
      <span class="inspector-trigger-mode ${mode === "disabled" ? "disabled" : ""}">${mode}</span>
    </button>
  `;
}

function tableDataTarget(table) {
  const profileId = schema.postgres?.sourceProfileId;
  const namespace = table.namespace ?? schema.postgres?.namespace;
  if (!profileId || !namespace || table.postgres?.liveOid == null) return null;
  return {
    key: `${profileId}:${namespace}:${table.postgres.liveOid}:${table.name}`,
    profileId,
    namespace,
    tableName: table.name
  };
}

function initializeTableData(table) {
  const target = tableDataTarget(table);
  if (!target) return null;
  if (tableDataState.key === target.key) return target;
  const requestId = ++tableDataRequestId;
  tableDataState = {
    key: target.key,
    target,
    columns: [],
    rows: [],
    nextOffset: 0,
    hasMore: false,
    stableOrder: true,
    mode: "table",
    truncated: false,
    loading: true,
    error: null,
    requestId
  };
  queueMicrotask(() => fetchTableDataPage(0, requestId));
  return target;
}

function quoteSqlIdentifier(value) {
  return `"${String(value).replace(/"/g, '""')}"`;
}

function initializeSqlConsole(target) {
  if (sqlConsoleState.key === target.key) return;
  const requestId = ++sqlConsoleRequestId;
  sqlConsoleState = {
    key: target.key,
    columns: [],
    rows: [],
    truncated: false,
    loading: false,
    error: null,
    requestId
  };
  elements.sqlConsoleInput.value = `SELECT *\nFROM ${quoteSqlIdentifier(target.namespace)}.${quoteSqlIdentifier(target.tableName)}\nLIMIT 100;`;
}

function tableDataValue(value) {
  if (value === null) return { text: "NULL", className: "null" };
  if (typeof value === "object") return { text: JSON.stringify(value), className: "" };
  if (value === "") return { text: "(empty)", className: "empty" };
  return { text: String(value), className: "" };
}

function renderTableDataContent() {
  if (tableDataState.error) {
    return `<div class="table-data-message error"><span>${escapeHtml(tableDataState.error)}</span><button data-action="refresh-table-data" type="button">Retry</button></div>`;
  }
  if (tableDataState.loading && !tableDataState.rows.length && !tableDataState.columns.length) {
    return '<div class="table-data-message">Loading first 50 rows...</div>';
  }
  const table = `
    <table class="table-data-table">
      <thead><tr>${tableDataState.columns.map(column => `<th data-tooltip="${escapeHtml(column.type)}">${escapeHtml(column.name)}${column.primary ? '<span class="table-data-key">PK</span>' : ""}</th>`).join("")}</tr></thead>
      <tbody>${tableDataState.rows.map(row => `<tr>${tableDataState.columns.map((column, index) => {
        const value = tableDataValue(Array.isArray(row) ? row[index] : row[column.name]);
        return `<td><span class="${value.className}" data-tooltip="${escapeHtml(value.text)}">${escapeHtml(value.text)}</span></td>`;
      }).join("")}</tr>`).join("")}</tbody>
    </table>
  `;
  if (tableDataState.loading && !tableDataState.rows.length) {
    return `${table}<div class="table-data-message empty-table">Refreshing rows...</div>`;
  }
  if (!tableDataState.rows.length) {
    const emptyMessage = tableDataState.mode === "query" ? "Query returned no rows." : "This table has no rows.";
    return `${table}<div class="table-data-message empty-table">${emptyMessage}</div>`;
  }
  const endMessage = tableDataState.mode === "query"
    ? (tableDataState.truncated ? "Result limited to 500 rows" : "End of result")
    : "End of table";
  return `
    ${table}
    ${tableDataState.loading ? '<div class="table-data-loading">Loading 50 more rows...</div>' : ""}
    ${!tableDataState.hasMore && !tableDataState.loading ? `<div class="table-data-end">${endMessage}</div>` : ""}
  `;
}

function setTableDataPanelVisible(visible) {
  clearTimeout(tableDataPanelTransitionTimer);
  if (visible) {
    elements.tableDataPanel.hidden = false;
    void elements.tableDataPanel.offsetWidth;
    elements.tableDataPanel.classList.add("open");
    return;
  }
  elements.tableDataPanel.classList.remove("open");
  tableDataPanelTransitionTimer = setTimeout(() => {
    if (!elements.tableDataPanel.classList.contains("open")) elements.tableDataPanel.hidden = true;
  }, 240);
}

function renderTableDataPanel(table) {
  const availableTarget = table && tableDataTarget(table);
  if (!availableTarget || !tableDataPanelExpanded) {
    setTableDataPanelVisible(false);
    return;
  }
  const targetChanged = tableDataState.key !== availableTarget.key;
  const target = initializeTableData(table);
  initializeSqlConsole(target);
  if (targetChanged) tablePanelActivePane = "data";
  setTableDataPanelVisible(true);
  elements.tableDataPanelTitle.textContent = target.tableName;
  elements.tableDataScroll.setAttribute("aria-label", `Rows from ${target.namespace}.${target.tableName}`);
  updateTableDataPanel();
  updateSqlConsolePanel();
  setTablePanelActivePane(tablePanelActivePane);
}

function updateTableDataPanel() {
  const table = getTable(selectedTableId);
  if (!table || tableDataTarget(table)?.key !== tableDataState.key) return;
  const scroll = elements.tableDataScroll;
  const scrollTop = scroll.scrollTop;
  const scrollLeft = scroll.scrollLeft;
  scroll.innerHTML = renderTableDataContent();
  scroll.scrollTop = scrollTop;
  scroll.scrollLeft = scrollLeft;
  const queryResult = tableDataState.mode === "query";
  elements.tableDataPanelTitle.textContent = queryResult ? "Query result" : tableDataState.target.tableName;
  elements.tableDataCount.textContent = `${tableDataState.rows.length}${tableDataState.hasMore ? "+" : ""} rows`;
  elements.tableDataWarning.hidden = queryResult || tableDataState.stableOrder;
}

async function fetchTableDataPage(offset, requestId = tableDataState.requestId) {
  const target = tableDataState.target;
  if (!target) return;
  try {
    const query = new URLSearchParams({
      namespace: target.namespace,
      table: target.tableName,
      offset: String(offset),
      limit: "50"
    });
    const result = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(target.profileId)}/data?${query}`);
    if (tableDataState.requestId !== requestId || tableDataState.key !== target.key) return;
    tableDataState.columns = result.columns;
    tableDataState.rows = offset === 0 ? result.rows : [...tableDataState.rows, ...result.rows];
    tableDataState.nextOffset = result.nextOffset;
    tableDataState.hasMore = result.hasMore;
    tableDataState.stableOrder = result.stableOrder;
    tableDataState.mode = "table";
    tableDataState.truncated = false;
    tableDataState.error = null;
  } catch (error) {
    if (tableDataState.requestId !== requestId) return;
    tableDataState.error = error.message;
    tableDataState.hasMore = false;
  } finally {
    if (tableDataState.requestId === requestId) {
      tableDataState.loading = false;
      updateTableDataPanel();
    }
  }
}

function refreshTableData() {
  const table = getTable(selectedTableId);
  if (!table || !tableDataTarget(table)) return;
  if (tableDataState.mode === "query") {
    executeSqlConsole();
    return;
  }
  reloadTableData();
}

function setTableDataPanelExpanded(expanded) {
  const table = getTable(selectedTableId);
  tableDataPanelExpanded = Boolean(expanded && table && tableDataTarget(table));
  if (!tableDataPanelExpanded) setTableDataPanelMaximized(false);
  renderTableDataPanel(table);
  const toggle = elements.inspectorContent.querySelector('[data-action="toggle-table-data"]');
  if (toggle) {
    toggle.classList.toggle("active", tableDataPanelExpanded);
    toggle.setAttribute("aria-pressed", String(tableDataPanelExpanded));
    updateTooltip(toggle, `${tableDataPanelExpanded ? "Minimize" : "Show"} data tools`);
  }
  updateInspectorHeaderToggle();
}

function setTableDataPanelMaximized(maximized) {
  tableDataPanelMaximized = Boolean(maximized && tableDataPanelExpanded);
  elements.mainLayout.classList.toggle("data-view-maximized", tableDataPanelMaximized);
  elements.maximizeTableData.classList.toggle("active", tableDataPanelMaximized);
  elements.maximizeTableData.setAttribute("aria-pressed", String(tableDataPanelMaximized));
  const tooltip = tableDataPanelMaximized ? "Restore split view" : "Maximize data tools";
  updateTooltip(elements.maximizeTableData, tooltip);
  elements.maximizeTableData.setAttribute("aria-label", tooltip);
  const minimizeTooltip = tableDataPanelMaximized ? "Restore table properties" : "Minimize data tools";
  updateTooltip(elements.minimizeTableData, minimizeTooltip);
  elements.minimizeTableData.setAttribute("aria-label", minimizeTooltip);
}

function reloadTableData() {
  if (!tableDataState.target) return;
  const requestId = ++tableDataRequestId;
  tableDataState = {
    ...tableDataState,
    rows: [],
    nextOffset: 0,
    hasMore: false,
    mode: "table",
    truncated: false,
    loading: true,
    error: null,
    requestId
  };
  updateTableDataPanel();
  fetchTableDataPage(0, requestId);
}

function tablePaneVisibility(pane) {
  const activePane = pane === "console" ? "console" : "data";
  return { activePane, dataHidden: activePane !== "data", consoleHidden: activePane !== "console" };
}

function setTablePanelActivePane(pane, focus = false) {
  const visibility = tablePaneVisibility(pane);
  const previousPane = elements.tableDataPanel.dataset.activePane;
  const transitioning = Boolean(previousPane && previousPane !== visibility.activePane);
  clearTimeout(tablePaneTransitionTimer);
  tablePanelActivePane = visibility.activePane;
  const dataActive = !visibility.dataHidden;
  if (transitioning) {
    elements.tableDataPaneContent.hidden = false;
    elements.sqlConsoleContent.hidden = false;
    void elements.tableDataPanel.offsetHeight;
  }
  elements.tableDataPanel.dataset.activePane = tablePanelActivePane;
  if (transitioning) {
    tablePaneTransitionTimer = setTimeout(() => {
      if (elements.tableDataPanel.dataset.activePane !== visibility.activePane) return;
      elements.tableDataPaneContent.hidden = visibility.dataHidden;
      elements.sqlConsoleContent.hidden = visibility.consoleHidden;
    }, 380);
  } else {
    elements.tableDataPaneContent.hidden = visibility.dataHidden;
    elements.sqlConsoleContent.hidden = visibility.consoleHidden;
  }
  elements.showTableDataPane.setAttribute("aria-expanded", String(dataActive));
  elements.showSqlConsolePane.setAttribute("aria-expanded", String(!dataActive));
  if (!dataActive) {
    updateSqlConsolePanel();
    if (focus) requestAnimationFrame(() => elements.sqlConsoleInput.focus());
  }
}

function toggleTablePanelActivePane(pane) {
  const nextPane = tablePanelActivePane === pane ? (pane === "data" ? "console" : "data") : pane;
  setTablePanelActivePane(nextPane, nextPane === "console");
}

function updateSqlConsolePanel() {
  elements.sqlConsoleStatus.textContent = sqlConsoleState.loading
    ? "Running..."
    : sqlConsoleState.error
      ? "Query failed"
      : sqlConsoleState.columns.length
        ? `${sqlConsoleState.rows.length}${sqlConsoleState.truncated ? "+" : ""} rows`
        : "SELECT · WITH · VALUES · TABLE · EXPLAIN";
  elements.runSqlConsole.disabled = sqlConsoleState.loading;
  elements.runSqlConsole.textContent = sqlConsoleState.loading ? "Running..." : "Run query";
}

async function executeSqlConsole() {
  const target = tableDataState.target;
  const statement = elements.sqlConsoleInput.value;
  if (!target || !statement.trim() || sqlConsoleState.loading) return;
  const requestId = ++sqlConsoleRequestId;
  sqlConsoleState = { ...sqlConsoleState, columns: [], rows: [], truncated: false, loading: true, error: null, requestId };
  updateSqlConsolePanel();
  try {
    const result = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(target.profileId)}/sql`, {
      method: "POST",
      body: JSON.stringify({ namespace: target.namespace, sql: statement })
    });
    if (sqlConsoleState.requestId !== requestId || sqlConsoleState.key !== target.key) return;
    sqlConsoleState.columns = result.columns;
    sqlConsoleState.rows = result.rows;
    sqlConsoleState.truncated = result.truncated;
    const dataRequestId = ++tableDataRequestId;
    const columns = result.columns.map(column => ({ name: column.name, type: "query result", primary: false }));
    tableDataState = {
      key: target.key,
      target,
      columns,
      rows: result.rows,
      nextOffset: 0,
      hasMore: false,
      stableOrder: true,
      mode: "query",
      truncated: result.truncated,
      loading: false,
      error: null,
      requestId: dataRequestId
    };
    updateTableDataPanel();
    setTablePanelActivePane("data");
  } catch (error) {
    if (sqlConsoleState.requestId !== requestId) return;
    sqlConsoleState.error = error.message;
    showToast(error.message);
  } finally {
    if (sqlConsoleState.requestId === requestId) {
      sqlConsoleState.loading = false;
      updateSqlConsolePanel();
    }
  }
}

function clearSqlConsole() {
  const requestId = ++sqlConsoleRequestId;
  sqlConsoleState = { ...sqlConsoleState, columns: [], rows: [], truncated: false, loading: false, error: null, requestId };
  elements.sqlConsoleInput.value = "";
  updateSqlConsolePanel();
  elements.sqlConsoleInput.focus();
}

function loadMoreTableData() {
  if (tableDataState.loading || !tableDataState.hasMore || tableDataState.error) return;
  tableDataState.loading = true;
  updateTableDataPanel();
  fetchTableDataPage(tableDataState.nextOffset);
}

function renderUniqueConstraintsList(table) {
  const constraints = table.uniqueConstraints ?? [];
  if (!constraints.length) return "";
  return constraints.map(uc => {
    const columnNames = uc.columnIds.map(cid => {
      const col = table.columns.find(c => c.id === cid);
      return col ? col.name : cid;
    });
    const constraintName = uc.name || availableUniqueConstraintName(table, uc.columnIds, uc.id);
    return `<div class="uc-item" data-uc-id="${uc.id}">
      <div class="uc-content">
        <input class="text-input uc-name-input" data-uc-name="${uc.id}" maxlength="63" value="${escapeHtml(constraintName)}" aria-label="Unique constraint name" />
        <div class="uc-column-list">
          <span class="uc-label">UNIQUE (</span>
          ${columnNames.map((name, i) => `
            <span class="uc-col-tag">
              <span class="uc-col-name">${escapeHtml(name)}</span>
              <button class="uc-remove-col" data-uc-remove-col="${uc.columnIds[i]}" type="button" data-tooltip="Remove column" aria-label="Remove column">&times;</button>
              ${i < columnNames.length - 1 ? '<span class="uc-sep">,</span>' : ""}
            </span>
          `).join("")}
          <span class="uc-label">)</span>
          <select class="uc-add-col-select" data-uc-add-col="${uc.id}" aria-label="Add column to unique constraint">
            <option value="">+ column</option>
            ${table.columns
              .filter(c => !uc.columnIds.includes(c.id))
              .map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
              .join("")}
          </select>
        </div>
      </div>
      <button class="mini-delete" data-uc-delete="${uc.id}" type="button" data-tooltip="Delete unique constraint" aria-label="Delete unique constraint">&times;</button>
    </div>`;
  }).join("");
}

function renderColumnEditor(column) {
  return `
    <div class="column-editor" data-editor-column-id="${column.id}">
      <div class="column-editor-main">
        <button class="column-drag-handle" draggable="true" type="button" data-tooltip="Drag to reorder column" aria-label="Drag ${escapeHtml(column.name)} to reorder">
          <svg viewBox="0 0 12 18" aria-hidden="true"><circle cx="3" cy="4" r="1"/><circle cx="9" cy="4" r="1"/><circle cx="3" cy="9" r="1"/><circle cx="9" cy="9" r="1"/><circle cx="3" cy="14" r="1"/><circle cx="9" cy="14" r="1"/></svg>
        </button>
        <input class="text-input" data-column-field="name" value="${escapeHtml(column.name)}" aria-label="Column name" />
        <select class="select-input" data-column-field="type" aria-label="Column type">
          ${DATA_TYPES.map(type => `<option ${type === column.type ? "selected" : ""}>${escapeHtml(type)}</option>`).join("")}
          ${DATA_TYPES.includes(column.type) ? "" : `<option selected>${escapeHtml(column.type)}</option>`}
        </select>
        <button class="mini-delete" data-action="delete-column" type="button" data-tooltip="Delete column" aria-label="Delete column"><svg viewBox="0 0 16 16"><path d="M3 5h10M6 3h4l1 2H5l1-2ZM5 5l1 8h4l1-8"/></svg></button>
      </div>
      <div class="column-options">
        <label class="check-option"><input type="checkbox" data-column-field="primary" ${column.primary ? "checked" : ""}/> Primary</label>
        <label class="check-option"><input type="checkbox" data-column-field="nullable" ${column.nullable ? "checked" : ""}/> Nullable</label>
        <label class="check-option"><input type="checkbox" data-column-field="unique" ${column.unique ? "checked" : ""}/> Unique</label>
      </div>
      <div class="column-default-row"><label>Default</label><input class="text-input" data-column-field="default" value="${escapeHtml(column.default ?? "")}" placeholder="e.g. now(), gen_random_uuid()" /></div>
    </div>
  `;
}

function renderRelationshipItem(relation) {
  const fromTable = getTable(relation.fromTableId);
  const toTable = getTable(relation.toTableId);
  const pairs = relationshipColumnPairs(relation);
  const fromColumns = pairs.map(pair => getColumn(relation.fromTableId, pair.fromColumnId)?.name).filter(Boolean);
  const toColumns = pairs.map(pair => getColumn(relation.toTableId, pair.toColumnId)?.name).filter(Boolean);
  const fromLabel = fromColumns.length === 1 ? fromColumns[0] : `(${fromColumns.join(", ")})`;
  const toLabel = toColumns.length === 1 ? toColumns[0] : `(${toColumns.join(", ")})`;
  const name = relationshipConstraintName(relation) || "Unnamed foreign key";
  return `<div class="relationship-item">
    <button class="relationship-edit" data-edit-relationship="${escapeHtml(relation.id)}" type="button">
      <code>${escapeHtml(fromTable?.name)}.${escapeHtml(fromLabel)} → ${escapeHtml(toTable?.name)}.${escapeHtml(toLabel)}</code>
      <small>${escapeHtml(name)} · ${pairs.length} column${pairs.length === 1 ? "" : "s"}</small>
    </button>
    <button data-action="delete-relationship" data-relation-id="${escapeHtml(relation.id)}" data-tooltip="Delete relationship" aria-label="Delete relationship" type="button">×</button>
  </div>`;
}

function relationshipColumnOptions(table, selectedId) {
  return table.columns.map(column => `<option value="${escapeHtml(column.id)}" ${column.id === selectedId ? "selected" : ""}>${escapeHtml(column.name)} · ${escapeHtml(column.type)}</option>`).join("");
}

function renderRelationshipEditor() {
  if (!relationshipEditorState) return;
  const { relationship, pairs } = relationshipEditorState;
  const source = getTable(relationship.fromTableId);
  const target = getTable(relationship.toTableId);
  elements.relationshipEditorTitle.textContent = relationshipEditorState.isNew ? "Create relationship" : "Edit relationship";
  elements.relationshipEditorTables.textContent = `${source.name} → ${target.name}`;
  elements.relationshipEditorName.value = relationshipEditorState.name;
  elements.relationshipEditorPairs.innerHTML = pairs.map((pair, index) => `
    <div class="relationship-pair" data-relationship-pair="${index}">
      <button class="relationship-pair-drag" draggable="true" type="button" data-tooltip="Drag to reorder pair" aria-label="Drag pair ${index + 1} to reorder">
        <svg viewBox="0 0 12 18" aria-hidden="true"><circle cx="3" cy="4" r="1"/><circle cx="9" cy="4" r="1"/><circle cx="3" cy="9" r="1"/><circle cx="9" cy="9" r="1"/><circle cx="3" cy="14" r="1"/><circle cx="9" cy="14" r="1"/></svg>
      </button>
      <label><span>Foreign key column</span><select data-relationship-side="from">${relationshipColumnOptions(source, pair.fromColumnId)}</select></label>
      <span class="relationship-pair-arrow">→</span>
      <label><span>Referenced column</span><select data-relationship-side="to">${relationshipColumnOptions(target, pair.toColumnId)}</select></label>
      <div class="relationship-pair-actions">
        <button type="button" data-move-pair="up" ${index === 0 ? "disabled" : ""} data-tooltip="Move pair up" aria-label="Move pair up">↑</button>
        <button type="button" data-move-pair="down" ${index === pairs.length - 1 ? "disabled" : ""} data-tooltip="Move pair down" aria-label="Move pair down">↓</button>
        <button class="danger" type="button" data-remove-pair ${pairs.length === 1 ? "disabled" : ""} data-tooltip="Remove pair" aria-label="Remove pair">×</button>
      </div>
    </div>
  `).join("");
  const usedFrom = new Set(pairs.map(pair => pair.fromColumnId));
  const usedTo = new Set(pairs.map(pair => pair.toColumnId));
  elements.relationshipAddPair.disabled = source.columns.every(column => usedFrom.has(column.id)) || target.columns.every(column => usedTo.has(column.id));
  const error = validateRelationshipDraft(relationship, pairs, relationshipEditorState.name);
  const typeChanges = pairs.filter(pair => getColumn(source.id, pair.fromColumnId)?.type !== getColumn(target.id, pair.toColumnId)?.type).length;
  elements.relationshipEditorStatus.classList.toggle("invalid", Boolean(error));
  elements.relationshipEditorStatus.textContent = error || (typeChanges
    ? `${typeChanges} foreign key column type${typeChanges === 1 ? "" : "s"} will be aligned when saved.`
    : `References a valid ${pairs.length === 1 ? "single-column" : `${pairs.length}-column`} key.`);
}

function openRelationshipEditor(relationship, isNew = false) {
  const source = getTable(relationship.fromTableId);
  const target = getTable(relationship.toTableId);
  if (!source || !target) return showToast("External relationships cannot be edited on the canvas");
  const pairs = relationshipColumnPairs(relationship).map(pair => ({ ...pair }));
  relationshipEditorState = {
    relationship: clone(relationship),
    pairs,
    isNew,
    nameTouched: !isNew,
    name: relationshipConstraintName(relationship) || availableRelationshipName(relationship, pairs, relationship.id)
  };
  elements.deleteRelationshipEditor.hidden = isNew;
  renderRelationshipEditor();
  elements.relationshipEditorDialog.showModal();
}

function closeRelationshipEditor() {
  elements.relationshipEditorDialog.close();
  relationshipEditorState = null;
  relationshipPairDragIndex = null;
}

function saveRelationshipEditor() {
  if (!relationshipEditorState) return;
  const { relationship, pairs, isNew } = relationshipEditorState;
  const name = relationshipEditorState.name.trim();
  const error = validateRelationshipDraft(relationship, pairs, name);
  if (error) return showToast(error);
  const source = getTable(relationship.fromTableId);
  const target = getTable(relationship.toTableId);
  const relationshipIndex = isNew ? -1 : schema.relationships.findIndex(item => item.id === relationship.id);
  if (!isNew && relationshipIndex === -1) return closeRelationshipEditor();
  checkpointHistory();
  let typeChanges = 0;
  for (const pair of pairs) {
    const sourceColumn = getColumn(source.id, pair.fromColumnId);
    const targetColumn = getColumn(target.id, pair.toColumnId);
    if (sourceColumn.type !== targetColumn.type) typeChanges += 1;
    sourceColumn.type = targetColumn.type;
  }
  const saved = { ...relationship, name, constraintName: name };
  saved.targetNamespace = target.namespace || schema.postgres?.namespace;
  saved.targetTableName = target.name;
  saved.targetColumnNames = pairs.map(pair => getColumn(target.id, pair.toColumnId).name);
  delete saved.definition;
  setRelationshipColumnPairs(saved, pairs);
  if (isNew) {
    schema.relationships.push(saved);
  } else {
    schema.relationships[relationshipIndex] = saved;
  }
  saveSchema();
  closeRelationshipEditor();
  render();
  showToast(typeChanges ? `Relationship saved and ${typeChanges} column type${typeChanges === 1 ? " was" : "s were"} aligned` : "Relationship saved");
}

function focusInspectorDatabaseTarget(target) {
  const preservedView = { ...view };
  if (inspectorContentCollapsed) {
    inspectorContentCollapsed = false;
    clearTimeout(inspectorContentTransitionTimer);
    elements.mainLayout.classList.remove("inspector-content-collapsed", "inspector-content-expanding");
  }
  selectTable(target.tableId, false, true);
  view = preservedView;
  applyView();
  requestAnimationFrame(() => {
    let control;
    if (["primary-key", "column-unique", "column-nullable"].includes(target.kind)) {
      const editor = [...elements.inspectorContent.querySelectorAll("[data-editor-column-id]")]
        .find(item => item.dataset.editorColumnId === target.columnId);
      const field = { "primary-key": "primary", "column-unique": "unique", "column-nullable": "nullable" }[target.kind];
      control = editor?.querySelector(`[data-column-field="${field}"]`);
    }
    if (target.kind === "composite-unique") {
      const item = [...elements.inspectorContent.querySelectorAll("[data-uc-id]")].find(element => element.dataset.ucId === target.id);
      control = item?.querySelector("[data-uc-name]");
    }
    if (!control) return;
    control.focus({ preventScroll: true });
    clearTimeout(inspectorObjectFocusTimer);
    elements.inspectorContent.querySelectorAll(".inspector-object-focus, .inspector-object-focus-control").forEach(item => {
      item.classList.remove("inspector-object-focus", "inspector-object-focus-control");
    });
    const cue = control.closest(".check-option, .uc-item") || control;
    void cue.offsetWidth;
    cue.classList.add("inspector-object-focus");
    control.classList.add("inspector-object-focus-control");
    inspectorObjectFocusTimer = setTimeout(() => {
      cue.classList.remove("inspector-object-focus");
      control.classList.remove("inspector-object-focus-control");
    }, 1600);
    const inspectorBounds = elements.inspector.getBoundingClientRect();
    const controlBounds = control.getBoundingClientRect();
    const offset = controlBounds.top - inspectorBounds.top - (elements.inspector.clientHeight - controlBounds.height) / 2;
    elements.inspector.scrollTo({ top: Math.max(0, elements.inspector.scrollTop + offset), behavior: "auto" });
  });
}

function closeObjectIconMenu() {
  elements.objectIconMenu.hidden = true;
  elements.objectIconMenu.innerHTML = "";
  objectIconMenuTargets = [];
}

function openDatabaseIconTarget(target) {
  closeObjectIconMenu();
  if (target.kind === "check") {
    openDatabaseObjectEditor({ kind: "check", id: target.id, tableId: target.tableId }, "inspector");
    return;
  }
  if (target.kind === "relationship") {
    const relationship = schema.relationships.find(item => item.id === target.id);
    if (relationship) openRelationshipEditor(relationship);
    return;
  }
  focusInspectorDatabaseTarget(target);
}

function openObjectIconMenu(targets, clientX, clientY) {
  objectIconMenuTargets = targets;
  elements.objectIconMenu.innerHTML = targets.map((target, index) => `
    <button type="button" role="menuitem" data-object-menu-index="${index}">
      <span>${escapeHtml(target.label)}</span>
      <small>${target.kind === "relationship" ? "foreign key" : target.kind.replaceAll("-", " ")}</small>
    </button>
  `).join("");
  elements.objectIconMenu.hidden = false;
  const bounds = elements.objectIconMenu.getBoundingClientRect();
  elements.objectIconMenu.style.left = `${Math.max(8, Math.min(clientX, window.innerWidth - bounds.width - 8))}px`;
  elements.objectIconMenu.style.top = `${Math.max(8, Math.min(clientY, window.innerHeight - bounds.height - 8))}px`;
  elements.objectIconMenu.querySelector("button")?.focus();
}

function applyStageTransform() {
  elements.stage.style.transform = `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.zoom})`;
}

function applyView() {
  applyStageTransform();
  elements.zoomDisplay.textContent = `${Math.round(view.zoom * 100)}%`;
  const gridSize = 24 * view.zoom;
  elements.workspace.querySelector(".canvas-grid").style.backgroundSize = `${gridSize}px ${gridSize}px, ${gridSize}px ${gridSize}px, 100% 100%`;
  elements.workspace.querySelector(".canvas-grid").style.backgroundPosition = `${view.x}px ${view.y}px`;
}

function isAdditiveTableSelection(event) {
  return Boolean(event.shiftKey || event.ctrlKey || event.metaKey);
}

function selectionBounds(startX, startY, endX, endY) {
  return {
    left: Math.min(startX, endX),
    top: Math.min(startY, endY),
    right: Math.max(startX, endX),
    bottom: Math.max(startY, endY)
  };
}

function rectanglesIntersect(first, second) {
  return first.left <= second.right && first.right >= second.left
    && first.top <= second.bottom && first.bottom >= second.top;
}

function sameTableSelection(first, second) {
  return first.size === second.size && [...first].every(tableId => second.has(tableId));
}

function applyMarqueeSelection(bounds) {
  const hits = [...document.querySelectorAll(".table-card")]
    .filter(card => rectanglesIntersect(bounds, card.getBoundingClientRect()))
    .map(card => card.dataset.tableId);
  const nextSelection = new Set([...marqueeState.baseSelection, ...hits]);
  const nextSelectedTableId = hits.at(-1) || marqueeState.baseSelectedTableId || null;
  if (sameTableSelection(nextSelection, selectedTableIds) && nextSelectedTableId === selectedTableId) return;
  selectedTableIds = nextSelection;
  selectedTableId = nextSelectedTableId;
  inspectorContentCollapsed = Boolean(selectedTableId);
  inspectorDismissed = false;
  document.querySelectorAll(".table-card").forEach(card => card.classList.toggle("selected", selectedTableIds.has(card.dataset.tableId)));
  renderConnections();
  renderInspector();
  if (selectedTableId) openInspectorPane();
}

function updateMarqueeSelection(event) {
  const bounds = selectionBounds(marqueeState.startX, marqueeState.startY, event.clientX, event.clientY);
  const workspaceBounds = elements.workspace.getBoundingClientRect();
  Object.assign(elements.selectionMarquee.style, {
    left: `${bounds.left - workspaceBounds.left}px`,
    top: `${bounds.top - workspaceBounds.top}px`,
    width: `${bounds.right - bounds.left}px`,
    height: `${bounds.bottom - bounds.top}px`
  });
  elements.selectionMarquee.hidden = false;
  applyMarqueeSelection(bounds);
}

function finishMarqueeSelection() {
  marqueeState = null;
  elements.selectionMarquee.hidden = true;
  elements.workspace.classList.remove("selecting");
}

function openInspectorPane() {
  clearTimeout(inspectorDismissTransitionTimer);
  inspectorDismissTransitionTimer = null;
  inspectorDismissed = false;
  elements.mainLayout.classList.remove("inspector-dismissed");
  if (selectedTableId) elements.inspector.classList.add("mobile-open");
}

function updateInspectorHeaderToggle() {
  const toggle = elements.inspectorContent.querySelector(".inspector-head-toggle");
  toggle?.setAttribute("aria-expanded", String(!inspectorContentCollapsed));
  toggle?.setAttribute("aria-label", tableDataPanelExpanded
    ? "Minimize data tools"
    : `${inspectorContentCollapsed ? "Expand" : "Collapse"} table properties`);
}

function inspectorHeaderGesture(button, dataExpanded = tableDataPanelExpanded, inspectorCollapsed = inspectorContentCollapsed) {
  if (button === "left") {
    if (dataExpanded) return "hide-data";
    return inspectorCollapsed ? "expand-inspector" : "collapse-inspector";
  }
  if (inspectorCollapsed) return "expand-with-data";
  return dataExpanded ? "maximize-data" : "show-data";
}

function handleInspectorHeaderGesture(button) {
  const action = inspectorHeaderGesture(button);
  if (action === "hide-data") setTableDataPanelExpanded(false);
  if (action === "expand-inspector") setInspectorContentCollapsed(false);
  if (action === "collapse-inspector") setInspectorContentCollapsed(true);
  if (action === "expand-with-data") {
    setInspectorContentCollapsed(false);
    setTableDataPanelExpanded(true);
    setTableDataPanelMaximized(false);
  }
  if (action === "show-data") {
    setTableDataPanelExpanded(true);
    setTableDataPanelMaximized(false);
  }
  if (action === "maximize-data") setTableDataPanelMaximized(true);
}

function prepareInspectorTileForTablePress() {
  const hidden = elements.mainLayout.classList.contains("inspector-collapsed") || inspectorDismissed || tableDataPanelMaximized;
  inspectorContentCollapsed = true;
  clearTimeout(inspectorContentTransitionTimer);
  elements.inspector.scrollTop = 0;
  elements.mainLayout.classList.add("inspector-content-collapsed");
  elements.mainLayout.classList.remove("inspector-content-expanding");
  if (hidden) void elements.inspector.offsetHeight;
}

function collapseWorkspacePanelsForMiddlePan() {
  const snapshot = {
    inspectorDismissed,
    inspectorContentCollapsed,
    tableDataPanelExpanded,
    tableDataPanelMaximized,
    tablePanelActivePane
  };
  if (!selectedTableId || inspectorDismissed) return snapshot;
  prepareInspectorTileForTablePress();
  if (tableDataPanelExpanded) setTableDataPanelExpanded(false);
  updateInspectorHeaderToggle();
  openInspectorPane();
  return snapshot;
}

function restoreWorkspacePanelsAfterMiddlePan() {
  const snapshot = middlePanPanelSnapshot;
  middlePanPanelSnapshot = null;
  if (!snapshot || !selectedTableId) return;
  tablePanelActivePane = snapshot.tablePanelActivePane;
  if (!snapshot.inspectorDismissed) {
    prepareInspectorTileForTablePress();
    openInspectorPane();
  }
  if (snapshot.tableDataPanelExpanded) {
    setTableDataPanelExpanded(true);
    setTablePanelActivePane(snapshot.tablePanelActivePane);
    setTableDataPanelMaximized(snapshot.tableDataPanelMaximized);
  }
}

function setInspectorContentCollapsed(collapsed) {
  const nextCollapsed = Boolean(collapsed && selectedTableId);
  if (nextCollapsed === inspectorContentCollapsed) return;
  inspectorContentCollapsed = nextCollapsed;
  clearTimeout(inspectorContentTransitionTimer);
  elements.inspector.scrollTop = 0;
  elements.mainLayout.classList.toggle("inspector-content-collapsed", inspectorContentCollapsed);
  elements.mainLayout.classList.toggle("inspector-content-expanding", !inspectorContentCollapsed);
  updateInspectorHeaderToggle();
  if (!inspectorContentCollapsed) {
    inspectorContentTransitionTimer = setTimeout(() => elements.mainLayout.classList.remove("inspector-content-expanding"), 300);
  }
}

function closeInspectorPane() {
  setTableDataPanelExpanded(false);
  if (!selectedTableId) {
    setInspectorContentCollapsed(false);
    return;
  }
  clearTimeout(inspectorDismissTransitionTimer);
  const resetCollapsedContent = inspectorContentCollapsed;
  inspectorDismissed = true;
  elements.mainLayout.classList.add("inspector-dismissed");
  elements.inspector.classList.remove("mobile-open");
  if (resetCollapsedContent) {
    inspectorDismissTransitionTimer = setTimeout(() => {
      if (!inspectorDismissed) return;
      inspectorContentCollapsed = false;
      elements.mainLayout.classList.remove("inspector-content-collapsed", "inspector-content-expanding");
      elements.inspector.scrollTop = 0;
      updateInspectorHeaderToggle();
      inspectorDismissTransitionTimer = null;
    }, 280);
  }
}

function selectTable(tableId, additive = false, openInspector = true) {
  if (tableId && !additive && selectedTableId === tableId && selectedTableIds.size === 1) {
    if (openInspector) openInspectorPane();
    return;
  }
  if (!tableId) {
    selectedTableIds.clear();
    selectedTableId = null;
    tableDataPanelExpanded = false;
    setTableDataPanelMaximized(false);
    inspectorDismissed = false;
  } else if (additive) {
    if (selectedTableIds.has(tableId)) {
      selectedTableIds.delete(tableId);
      selectedTableId = [...selectedTableIds].at(-1) || null;
    } else {
      selectedTableIds.add(tableId);
      selectedTableId = tableId;
    }
  } else if (selectedTableIds.has(tableId) && selectedTableIds.size > 1) {
    selectedTableId = tableId;
  } else {
    selectedTableIds = new Set([tableId]);
    selectedTableId = tableId;
  }
  document.querySelectorAll(".table-card").forEach(card => {
    card.classList.toggle("selected", selectedTableIds.has(card.dataset.tableId));
  });
  renderConnections();
  renderInspector();
  if (!selectedTableId) {
    elements.mainLayout.classList.remove("inspector-dismissed");
    elements.inspector.classList.remove("mobile-open");
  } else if (!additive && openInspector) {
    openInspectorPane();
  }
}

function setRelationMode(enabled) {
  relationMode = enabled;
  relationSource = null;
  elements.workspace.classList.toggle("relation-mode", enabled);
  elements.relationTool.classList.toggle("active", enabled);
  elements.selectTool.classList.toggle("active", !enabled);
  elements.relationBanner.hidden = !enabled;
  elements.relationInstruction.textContent = "Select the foreign key column";
  elements.relationBanner.querySelector(".banner-step").textContent = "1";
  renderTables();
  renderConnections();
}

function handleRelationColumn(tableId, columnId) {
  if (!relationSource) {
    relationSource = { tableId, columnId };
    elements.relationInstruction.textContent = "Now select the referenced column";
    elements.relationBanner.querySelector(".banner-step").textContent = "2";
    renderTables();
    renderConnections();
    return;
  }
  if (relationSource.tableId === tableId && relationSource.columnId === columnId) {
    showToast("Choose a different column");
    return;
  }
  const foreignKeyColumn = getColumn(relationSource.tableId, relationSource.columnId);
  const referencedColumn = getColumn(tableId, columnId);
  if (!foreignKeyColumn || !referencedColumn) return setRelationMode(false);
  const relationship = {
    id: uid("rel"),
    fromTableId: relationSource.tableId,
    fromColumnId: relationSource.columnId,
    toTableId: tableId,
    toColumnId: columnId,
    targetNamespace: getTable(tableId)?.namespace || schema.postgres?.namespace,
    targetTableName: getTable(tableId)?.name,
    targetColumnNames: [referencedColumn.name],
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
    matchType: "SIMPLE",
    validated: true
  };
  setRelationMode(false);
  openRelationshipEditor(relationship, true);
}

function createTable(name) {
  const normalizedName = name.trim();
  if (!normalizedName) return;
  if (schema.tables.some(table => table.name.toLowerCase() === normalizedName.toLowerCase())) {
    showToast("A table with that name already exists");
    return;
  }
  const workspaceRect = elements.workspace.getBoundingClientRect();
  const x = (workspaceRect.width / 2 - view.x) / view.zoom - TABLE_WIDTH / 2;
  const y = (workspaceRect.height / 2 - view.y) / view.zoom - 100;
  const table = {
    id: uid("table"), name: normalizedName, x, y, color: COLORS[schema.tables.length % COLORS.length],
    columns: [{ id: uid("col"), name: "id", type: "uuid", primary: true, nullable: false, unique: true, default: "" }],
    uniqueConstraints: [], checks: [], indexes: [], triggers: []
  };
  checkpointHistory();
  schema.tables.push(table);
  selectedTableId = table.id;
  selectedTableIds = new Set([table.id]);
  saveSchema();
  render();
  openInspectorPane();
}

function copySelectedTable() {
  const table = getTable(selectedTableId);
  if (!table) return showToast("Select a table to copy");
  copiedTable = clone(table);
  renderInspector();
  showToast(`${table.name} copied. Press Ctrl+V to paste`);
}

function pasteCopiedTable() {
  if (!copiedTable) return showToast("Copy a table first");

  const baseName = `${copiedTable.name}_copy`;
  let name = baseName;
  let suffix = 2;
  while (schema.tables.some(table => table.name.toLowerCase() === name.toLowerCase())) {
    name = `${baseName}_${suffix}`;
    suffix += 1;
  }

  let x = copiedTable.x;
  let y = copiedTable.y;
  for (let offset = 40; offset <= 800; offset += 40) {
    x = copiedTable.x + offset;
    y = copiedTable.y + offset;
    if (!schema.tables.some(table => Math.abs(table.x - x) < 10 && Math.abs(table.y - y) < 10)) break;
  }

  const oldToNewCol = new Map();
  const newColumns = copiedTable.columns.map(column => {
    const newId = uid("col");
    oldToNewCol.set(column.id, newId);
    return { ...clone(column), id: newId };
  });
  const newUniqueConstraints = (copiedTable.uniqueConstraints ?? []).map(uc => ({
    ...clone(uc),
    id: uid("uc"),
    columnIds: uc.columnIds.map(cid => oldToNewCol.get(cid)).filter(Boolean),
    name: undefined
  })).filter(uc => uc.columnIds.length >= 2);
  const usedConstraintNames = new Set(schema.tables.flatMap(table => [
    table.name,
    table.primaryKey?.name,
    ...(table.uniqueConstraints ?? []).map(constraint => constraint.name),
    ...(table.indexes ?? []).map(index => index.name)
  ]).filter(Boolean));
  const copiedTableIdentity = { name, columns: newColumns };
  for (const constraint of newUniqueConstraints) {
    constraint.name = uniqueConstraintNameWithUsed(copiedTableIdentity, constraint.columnIds, usedConstraintNames);
    usedConstraintNames.add(constraint.name);
  }

  const table = {
    ...clone(copiedTable),
    id: uid("table"),
    name,
    x,
    y,
    columns: newColumns,
    uniqueConstraints: newUniqueConstraints,
    primaryKey: copiedTable.primaryKey ? { ...clone(copiedTable.primaryKey), id: uid("pk"), name: availablePrimaryKeyName(name), columnIds: copiedTable.primaryKey.columnIds.map(columnId => oldToNewCol.get(columnId)).filter(Boolean) } : null,
    checks: [],
    indexes: [],
    triggers: [],
    postgres: copiedTable.postgres ? { ...clone(copiedTable.postgres), liveOid: null } : undefined
  };
  checkpointHistory();
  schema.tables.push(table);
  selectedTableId = table.id;
  selectedTableIds = new Set([table.id]);
  saveSchema();
  render();
  openInspectorPane();
  showToast(`${table.name} pasted without relationships or database-managed objects`);
}

function deleteTable(tableId) {
  const table = getTable(tableId);
  if (!table || !confirm(`Delete ${table.name}, its constraints, and its relationships?`)) return;
  checkpointHistory();
  schema.tables = schema.tables.filter(item => item.id !== tableId);
  schema.relationships = schema.relationships.filter(relation => relation.fromTableId !== tableId && relation.toTableId !== tableId);
  selectedTableIds.delete(tableId);
  selectedTableId = [...selectedTableIds].at(-1) || null;
  saveSchema();
  render();
}

function updateColumn(columnId, field, value, historyKey = null) {
  const table = getTable(selectedTableId);
  const column = table?.columns.find(item => item.id === columnId);
  if (!column) return;
  const oldName = field === "name" ? column.name : null;
  checkpointHistory(historyKey);
  column[field] = value;
  if (oldName) updateColumnNameInObjects(table, columnId, oldName, value);
  if (field === "primary" && value) {
    column.nullable = false;
    column.unique = true;
  }
  if (field === "primary") {
    const primaryColumnIds = table.columns.filter(item => item.primary).map(item => item.id);
    table.primaryKey = primaryColumnIds.length ? {
      id: table.primaryKey?.id ?? uid("pk"),
      ...table.primaryKey,
      name: table.primaryKey?.name || availablePrimaryKeyName(table.name, table.id),
      columnIds: primaryColumnIds
    } : null;
  }
  saveSchema();
  renderTables();
  renderConnections();
}

function reorderColumn(columnId, targetColumnId, placeAfter) {
  const table = getTable(selectedTableId);
  if (!table || columnId === targetColumnId) return;
  const column = table.columns.find(item => item.id === columnId);
  const remainingColumns = table.columns.filter(item => item.id !== columnId);
  const targetIndex = remainingColumns.findIndex(item => item.id === targetColumnId);
  if (!column || targetIndex === -1) return;

  remainingColumns.splice(targetIndex + (placeAfter ? 1 : 0), 0, column);
  if (remainingColumns.every((item, index) => item.id === table.columns[index].id)) return;
  checkpointHistory();
  table.columns = remainingColumns;
  const inspectorScroll = elements.inspector.scrollTop;
  saveSchema();
  renderTables();
  renderConnections();
  renderInspector();
  elements.inspector.scrollTop = inspectorScroll;
}

function clearColumnDropState() {
  columnDropTarget = null;
  elements.inspectorContent.querySelectorAll(".column-editor").forEach(editor => {
    editor.classList.remove("dragging", "drop-before", "drop-after");
  });
}

function updateColumnDropTarget(clientY) {
  const editors = [...elements.inspectorContent.querySelectorAll(".column-editor")]
    .filter(editor => editor.dataset.editorColumnId !== draggedColumnId);
  elements.inspectorContent.querySelectorAll(".column-editor").forEach(editor => {
    editor.classList.remove("drop-before", "drop-after");
  });
  if (!editors.length) {
    columnDropTarget = null;
    return null;
  }
  const editor = editors.find(item => clientY < item.getBoundingClientRect().top + item.offsetHeight / 2) ?? editors.at(-1);
  const placeAfter = editor === editors.at(-1) && clientY >= editor.getBoundingClientRect().top + editor.offsetHeight / 2;
  columnDropTarget = { columnId: editor.dataset.editorColumnId, placeAfter };
  editor.classList.add(placeAfter ? "drop-after" : "drop-before");
  return columnDropTarget;
}

function fitDiagram() {
  if (!schema.tables.length) {
    view = { x: 45, y: 35, zoom: 1 };
    applyView();
    return;
  }
  const padding = 70;
  const bounds = schema.tables.reduce((result, table) => ({
    minX: Math.min(result.minX, table.x),
    minY: Math.min(result.minY, table.y),
    maxX: Math.max(result.maxX, table.x + TABLE_WIDTH),
    maxY: Math.max(result.maxY, table.y + 48 + table.columns.length * 35)
  }), { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
  const width = bounds.maxX - bounds.minX;
  const height = bounds.maxY - bounds.minY;
  const rect = elements.workspace.getBoundingClientRect();
  view.zoom = clampZoom(Math.min((rect.width - padding * 2) / width, (rect.height - padding * 2) / height), 1.25);
  view.x = (rect.width - width * view.zoom) / 2 - bounds.minX * view.zoom;
  view.y = (rect.height - height * view.zoom) / 2 - bounds.minY * view.zoom;
  applyView();
}

function setZoom(nextZoom, centerX, centerY, transient = false) {
  const rect = elements.workspace.getBoundingClientRect();
  const cursorX = centerX ?? rect.width / 2;
  const cursorY = centerY ?? rect.height / 2;
  const oldZoom = view.zoom;
  const newZoom = clampZoom(nextZoom);
  view.x = cursorX - (cursorX - view.x) * (newZoom / oldZoom);
  view.y = cursorY - (cursorY - view.y) * (newZoom / oldZoom);
  view.zoom = newZoom;
  if (transient) {
    applyStageTransform();
  } else {
    clearTimeout(wheelZoomTimer);
    wheelZoomTimer = null;
    elements.workspace.classList.remove("zooming");
    applyView();
    if (activeSchemaId) saveSchema(LAYOUT_SAVE_DELAY_MS);
  }
}

function finishWheelZoom() {
  clearTimeout(wheelZoomTimer);
  wheelZoomTimer = null;
  if (!elements.workspace.classList.contains("zooming")) return;
  elements.workspace.classList.remove("zooming");
  applyView();
  if (activeSchemaId) saveSchema(LAYOUT_SAVE_DELAY_MS);
}

const SQL_IDENTIFIER_PART = '(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`|\\[[^\\]]+\\]|[A-Za-z_][\\w$]*)';
const SQL_QUALIFIED_IDENTIFIER = `${SQL_IDENTIFIER_PART}(?:\\s*\\.\\s*${SQL_IDENTIFIER_PART})*`;

function stripSqlComments(sql) {
  let output = "";
  let quote = null;
  let dollarQuote = null;
  for (let index = 0; index < sql.length; index += 1) {
    const character = sql[index];
    const next = sql[index + 1];
    if (dollarQuote) {
      if (sql.startsWith(dollarQuote, index)) {
        output += dollarQuote;
        index += dollarQuote.length - 1;
        dollarQuote = null;
      } else {
        output += character;
      }
      continue;
    }
    if (quote) {
      output += character;
      if (character === quote) {
        if (next === quote) {
          output += next;
          index += 1;
        } else {
          quote = null;
        }
      } else if (character === "\\" && quote === "'" && next) {
        output += next;
        index += 1;
      }
      continue;
    }
    const dollarMatch = character === "$" ? sql.slice(index).match(/^\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$/) : null;
    if (dollarMatch) {
      dollarQuote = dollarMatch[0];
      output += dollarQuote;
      index += dollarQuote.length - 1;
      continue;
    }
    if (["'", '"', "`"].includes(character)) {
      quote = character;
      output += character;
      continue;
    }
    if (character === "-" && next === "-") {
      while (index < sql.length && sql[index] !== "\n") index += 1;
      output += "\n";
      continue;
    }
    if (character === "/" && next === "*") {
      index += 2;
      while (index < sql.length && !(sql[index] === "*" && sql[index + 1] === "/")) index += 1;
      index += 1;
      output += " ";
      continue;
    }
    output += character;
  }
  return output;
}

function splitSqlAtTopLevel(input, delimiter) {
  const parts = [];
  let start = 0;
  let depth = 0;
  let quote = null;
  let dollarQuote = null;
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    const next = input[index + 1];
    if (dollarQuote) {
      if (input.startsWith(dollarQuote, index)) {
        index += dollarQuote.length - 1;
        dollarQuote = null;
      }
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (next === quote) index += 1;
        else quote = null;
      } else if (character === "\\" && quote === "'" && next) {
        index += 1;
      }
      continue;
    }
    const dollarMatch = character === "$" ? input.slice(index).match(/^\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$/) : null;
    if (dollarMatch) {
      dollarQuote = dollarMatch[0];
      index += dollarQuote.length - 1;
    } else if (["'", '"', "`"].includes(character)) quote = character;
    else if (character === "[") quote = "]";
    else if (character === "(") depth += 1;
    else if (character === ")") depth = Math.max(0, depth - 1);
    else if (character === delimiter && depth === 0) {
      const part = input.slice(start, index).trim();
      if (part) parts.push(part);
      start = index + 1;
    }
  }
  const finalPart = input.slice(start).trim();
  if (finalPart) parts.push(finalPart);
  return parts;
}

function sqlIdentifierName(value) {
  const parts = value.match(new RegExp(SQL_IDENTIFIER_PART, "g")) || [value.trim()];
  const identifier = parts.at(-1).trim();
  if (identifier.startsWith('"')) return identifier.slice(1, -1).replaceAll('""', '"');
  if (identifier.startsWith("`")) return identifier.slice(1, -1).replaceAll("``", "`");
  if (identifier.startsWith("[")) return identifier.slice(1, -1).replaceAll("]]", "]");
  return identifier;
}

function sqlColumnNames(value) {
  return splitSqlAtTopLevel(value, ",").map(sqlIdentifierName);
}

function stripConstraintName(value) {
  return value.replace(new RegExp(`^CONSTRAINT\\s+${SQL_IDENTIFIER_PART}\\s+`, "i"), "").trim();
}

function addPendingForeignKey(pending, fromTable, sourceColumns, targetTable, targetColumns) {
  if (!sourceColumns.length || sourceColumns.length !== targetColumns.length) return;
  pending.push({
    fromTable,
    fromColumns: sourceColumns,
    toTable: sqlIdentifierName(targetTable),
    toColumns: targetColumns
  });
}

function parseCreateTable(statement, pendingRelationships) {
  const pattern = new RegExp(`^CREATE\\s+TABLE\\s+(?:IF\\s+NOT\\s+EXISTS\\s+)?(${SQL_QUALIFIED_IDENTIFIER})\\s*\\(([\\s\\S]*)\\)\\s*(?:[^)]*)$`, "i");
  const match = statement.match(pattern);
  if (!match) return null;
  const tableName = sqlIdentifierName(match[1]);
  const table = { id: uid("table"), name: tableName, x: 0, y: 0, color: COLORS[0], columns: [], uniqueConstraints: [] };
  const tablePrimaryKeys = [];
  const tableUniqueKeys = [];

  for (const rawItem of splitSqlAtTopLevel(match[2], ",")) {
    const item = stripConstraintName(rawItem);
    const primaryMatch = item.match(/^PRIMARY\s+KEY\s*\(([^)]+)\)/i);
    if (primaryMatch) {
      tablePrimaryKeys.push(...sqlColumnNames(primaryMatch[1]));
      continue;
    }
    const uniqueMatch = item.match(/^UNIQUE(?:\s+KEY)?(?:\s+[^\s(]+)?\s*\(([^)]+)\)/i);
    if (uniqueMatch) {
      tableUniqueKeys.push(sqlColumnNames(uniqueMatch[1]));
      continue;
    }
    const foreignMatch = item.match(new RegExp(`^FOREIGN\\s+KEY\\s*\\(([^)]+)\\)\\s+REFERENCES\\s+(${SQL_QUALIFIED_IDENTIFIER})\\s*\\(([^)]+)\\)`, "i"));
    if (foreignMatch) {
      addPendingForeignKey(pendingRelationships, tableName, sqlColumnNames(foreignMatch[1]), foreignMatch[2], sqlColumnNames(foreignMatch[3]));
      continue;
    }
    if (/^(?:CHECK|KEY|INDEX)\b/i.test(item)) continue;

    const columnMatch = item.match(new RegExp(`^(${SQL_IDENTIFIER_PART})\\s+([\\s\\S]+)$`, "i"));
    if (!columnMatch) continue;
    const columnName = sqlIdentifierName(columnMatch[1]);
    const definition = columnMatch[2].trim();
    const constraintIndex = definition.search(/\s+(?:CONSTRAINT|PRIMARY\s+KEY|NOT\s+NULL|NULL|UNIQUE|DEFAULT|REFERENCES|CHECK|COLLATE|GENERATED|IDENTITY)\b/i);
    const type = (constraintIndex === -1 ? definition : definition.slice(0, constraintIndex)).trim().replace(/\s+/g, " ");
    if (!type) continue;
    const primary = /\bPRIMARY\s+KEY\b/i.test(definition);
    const defaultMatch = definition.match(/\bDEFAULT\s+('[^']*'|[^\s,;]+)/i);
    const column = {
      id: uid("col"),
      name: columnName,
      type,
      primary,
      nullable: primary ? false : !/\bNOT\s+NULL\b/i.test(definition),
      unique: primary || /\bUNIQUE\b/i.test(definition),
      default: defaultMatch ? defaultMatch[1] : ""
    };
    table.columns.push(column);
    const inlineReference = definition.match(new RegExp(`\\bREFERENCES\\s+(${SQL_QUALIFIED_IDENTIFIER})\\s*\\(([^)]+)\\)`, "i"));
    if (inlineReference) addPendingForeignKey(pendingRelationships, tableName, [columnName], inlineReference[1], sqlColumnNames(inlineReference[2]));
  }

  const compositePrimaryKey = tablePrimaryKeys.length > 1;
  for (const column of table.columns) {
    if (tablePrimaryKeys.some(name => name.toLowerCase() === column.name.toLowerCase())) {
      column.primary = true;
      column.nullable = false;
      column.unique = !compositePrimaryKey;
    }
  }
  for (const names of tableUniqueKeys) {
    if (names.length === 1) {
      const col = table.columns.find(c => c.name.toLowerCase() === names[0].toLowerCase());
      if (col) col.unique = true;
    } else {
      const ids = names.map(n => {
        const col = table.columns.find(c => c.name.toLowerCase() === n.toLowerCase());
        return col ? col.id : null;
      }).filter(Boolean);
      if (ids.length >= 2) table.uniqueConstraints.push({ id: uid("uc"), columnIds: ids });
    }
  }
  return table.columns.length ? table : null;
}

function layoutImportedTables(tables) {
  const columnsPerRow = Math.max(1, Math.ceil(Math.sqrt(tables.length)));
  let y = 100;
  for (let start = 0; start < tables.length; start += columnsPerRow) {
    const row = tables.slice(start, start + columnsPerRow);
    row.forEach((table, index) => {
      table.x = 100 + index * 370;
      table.y = y;
      table.color = COLORS[(start + index) % COLORS.length];
    });
    const rowColumns = Math.max(...row.map(table => table.columns.length));
    y += 110 + rowColumns * 35;
  }
}

function parseSqlSchema(sql, projectName) {
  const statements = splitSqlAtTopLevel(stripSqlComments(sql), ";");
  const pendingRelationships = [];
  const tables = statements.map(statement => parseCreateTable(statement, pendingRelationships)).filter(Boolean);
  const tableNames = new Set(tables.map(table => table.name.toLowerCase()));

  if (!tables.length) throw new Error("No CREATE TABLE statements were found");
  if (tableNames.size !== tables.length) throw new Error("The SQL file contains duplicate table names");
  const tableMap = new Map(tables.map(table => [table.name.toLowerCase(), table]));

  const functions = [];
  for (const statement of statements) {
    const fnMatch = statement.match(/^CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\s+([^\s(]+)\s*\(/i);
    if (fnMatch) {
      const name = sqlIdentifierName(fnMatch[3]);
      const langMatch = statement.match(/LANGUAGE\s+(\w+)/i);
      const returnMatch = statement.match(/RETURNS\s+(\S+)/i);
      functions.push({
        id: uid("func"),
        name,
        kind: fnMatch[2].toLowerCase() === "procedure" ? "procedure" : "function",
        language: langMatch ? langMatch[1].toLowerCase() : "sql",
        returnType: returnMatch ? returnMatch[1] : "",
        definition: statement.trim()
      });
      continue;
    }
    const foreignMatch = statement.match(new RegExp(`^ALTER\\s+TABLE\\s+(?:ONLY\\s+)?(${SQL_QUALIFIED_IDENTIFIER})\\s+ADD\\s+(?:CONSTRAINT\\s+${SQL_IDENTIFIER_PART}\\s+)?FOREIGN\\s+KEY\\s*\\(([^)]+)\\)\\s+REFERENCES\\s+(${SQL_QUALIFIED_IDENTIFIER})\\s*\\(([^)]+)\\)`, "i"));
    if (foreignMatch) {
      addPendingForeignKey(pendingRelationships, sqlIdentifierName(foreignMatch[1]), sqlColumnNames(foreignMatch[2]), foreignMatch[3], sqlColumnNames(foreignMatch[4]));
      continue;
    }
    const keyMatch = statement.match(new RegExp(`^ALTER\\s+TABLE\\s+(?:ONLY\\s+)?(${SQL_QUALIFIED_IDENTIFIER})\\s+ADD\\s+(?:CONSTRAINT\\s+${SQL_IDENTIFIER_PART}\\s+)?(PRIMARY\\s+KEY|UNIQUE)\\s*\\(([^)]+)\\)`, "i"));
    if (!keyMatch) continue;
    const table = tableMap.get(sqlIdentifierName(keyMatch[1]).toLowerCase());
    const columnNames = sqlColumnNames(keyMatch[3]);
    const columns = columnNames.map(columnName => table?.columns.find(item => item.name.toLowerCase() === columnName.toLowerCase())).filter(Boolean);
    if (!table || columns.length !== columnNames.length) continue;
    if (/^PRIMARY/i.test(keyMatch[2])) {
      for (const column of columns) {
        column.primary = true;
        column.nullable = false;
        column.unique = columns.length === 1;
      }
    } else if (columns.length === 1) {
      columns[0].unique = true;
    } else {
      table.uniqueConstraints.push({ id: uid("uc"), columnIds: columns.map(column => column.id) });
    }
  }

  layoutImportedTables(tables);
  const relationshipKeys = new Set();
  const relationships = [];
  for (const pending of pendingRelationships) {
    const fromTable = tableMap.get(pending.fromTable.toLowerCase());
    const toTable = tableMap.get(pending.toTable.toLowerCase());
    const fromColumns = pending.fromColumns.map(name => fromTable?.columns.find(column => column.name.toLowerCase() === name.toLowerCase())).filter(Boolean);
    const toColumns = pending.toColumns.map(name => toTable?.columns.find(column => column.name.toLowerCase() === name.toLowerCase())).filter(Boolean);
    if (!fromTable || !toTable || fromColumns.length !== pending.fromColumns.length || toColumns.length !== pending.toColumns.length) continue;
    const fromColumnIds = fromColumns.map(column => column.id);
    const toColumnIds = toColumns.map(column => column.id);
    const key = `${fromColumnIds.join(",")}:${toColumnIds.join(",")}`;
    if (relationshipKeys.has(key)) continue;
    relationshipKeys.add(key);
    const relationship = { id: uid("rel"), fromTableId: fromTable.id, toTableId: toTable.id };
    if (fromColumnIds.length === 1) {
      relationship.fromColumnId = fromColumnIds[0];
      relationship.toColumnId = toColumnIds[0];
    } else {
      relationship.fromColumnIds = fromColumnIds;
      relationship.toColumnIds = toColumnIds;
    }
    relationships.push(relationship);
  }
  return { projectName, tables, relationships, functions };
}

async function importSqlFile(file) {
  try {
    const importedSchema = parseSqlSchema(await file.text(), file.name.replace(/\.sql$/i, "") || "Imported schema");
    await flushPendingSave();
    const importedSchemaId = uid("schema");
    await persistSchemaRecord(importedSchemaId, importedSchema);
    activeSchemaId = importedSchemaId;
    schema = importedSchema;
    view = { x: 45, y: 35, zoom: 1 };
    resetSchemaSession();
    elements.saveStatus.textContent = "Saved to file";
    render();
    requestAnimationFrame(fitDiagram);
    showToast(`Imported ${schema.tables.length} tables and ${schema.relationships.length} relationships`);
  } catch (error) {
    showToast(error.message || "The SQL file could not be imported");
  } finally {
    elements.sqlFileInput.value = "";
  }
}

function exportFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function generateSql() {
  const namespace = schema.postgres?.namespace;
  const tableSqlName = table => namespace ? `${sqlName(namespace)}.${sqlName(table.name)}` : sqlName(table.name);
  const tableStatements = schema.tables.map(table => {
    const primaryColumns = table.columns.filter(column => column.primary);
    const storedSingleUniqueColumnIds = new Set(
      (table.uniqueConstraints ?? []).filter(constraint => constraint.columnIds.length === 1).map(constraint => constraint.columnIds[0])
    );
    const columns = table.columns.map(column => {
      let out = `  ${sqlName(column.name)} ${column.type.toUpperCase()}`;
      if (!column.primary) {
        if (!column.nullable) out += " NOT NULL";
        if (column.unique && !storedSingleUniqueColumnIds.has(column.id)) out += " UNIQUE";
      }
      if (column.default) out += ` DEFAULT ${column.default}`;
      return out;
    });
    const primaryLines = primaryColumns.length
      ? [`  CONSTRAINT ${sqlName(table.primaryKey?.name || defaultPrimaryKeyName(table.name))} PRIMARY KEY (${primaryColumns.map(column => sqlName(column.name)).join(", ")})`]
      : [];
    const uniqueLines = (table.uniqueConstraints ?? []).map(uc => {
      const names = uc.columnIds.map(cid => {
        const col = table.columns.find(c => c.id === cid);
        return col ? sqlName(col.name) : cid;
      });
      const constraintName = uc.name || availableUniqueConstraintName(table, uc.columnIds, uc.id);
      return `  CONSTRAINT ${sqlName(constraintName)} UNIQUE (${names.join(", ")})`;
    });
    const checkLines = (table.checks ?? []).map(check => `  CONSTRAINT ${sqlName(check.name)} ${check.definition}`);
    const allLines = [...columns, ...primaryLines, ...uniqueLines, ...checkLines];
    return `CREATE TABLE ${tableSqlName(table)} (\n${allLines.join(",\n")}\n);`;
  });
  const relations = schema.relationships.map(relation => {
    const fromTable = getTable(relation.fromTableId);
    const toTable = getTable(relation.toTableId);
    const pairs = relationshipColumnPairs(relation);
    const fromColumns = pairs.map(pair => getColumn(relation.fromTableId, pair.fromColumnId)).filter(Boolean);
    const toColumns = pairs.map(pair => getColumn(relation.toTableId, pair.toColumnId)).filter(Boolean);
    if (!fromTable || !toTable || fromColumns.length !== pairs.length || toColumns.length !== pairs.length) return "";
    const fromNames = fromColumns.map(column => sqlName(column.name));
    const toNames = toColumns.map(column => sqlName(column.name));
    const constraint = sqlName(relation.constraintName || relation.name || `fk_${fromTable.name}_${fromColumns.map(column => column.name).join("_")}`);
    return `ALTER TABLE ${tableSqlName(fromTable)} ADD CONSTRAINT ${constraint} FOREIGN KEY (${fromNames.join(", ")}) REFERENCES ${tableSqlName(toTable)} (${toNames.join(", ")});`;
  }).filter(Boolean);
  const indexStatements = schema.tables.flatMap(table => (table.indexes ?? []).map(index => index.definition.trim().replace(/;?$/, ";")));
  const viewStatements = (schema.views ?? []).map(viewItem => viewItem.definition.trim().replace(/;?$/, ";"));
  const triggerStatements = schema.tables.flatMap(table => (table.triggers ?? []).map(trigger => trigger.definition.trim().replace(/;?$/, ";")));
  const functionStatements = (schema.functions ?? []).map(fn => {
    const def = fn.definition.trim();
    if (/^CREATE\s+(OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\b/i.test(def)) return `${def.replace(/;\s*$/, "")};`;
    const args = (fn.args ?? []).map(a => `${sqlName(a.name)} ${a.type}`).join(", ");
    const returns = fn.returnType ? ` RETURNS ${fn.returnType}` : "";
    const language = fn.language ? ` LANGUAGE ${fn.language}` : "";
    return `CREATE OR REPLACE FUNCTION ${sqlName(fn.name)} (${args})${returns} AS $$ \n${def}\n$$${language};`;
  });
  return `-- ${schema.projectName}\n-- Generated by Schemii\n\n${tableStatements.join("\n\n")}\n\n${relations.join("\n")}\n\n${functionStatements.join("\n\n")}\n\n${indexStatements.join("\n\n")}\n\n${viewStatements.join("\n\n")}\n\n${triggerStatements.join("\n\n")}`;
}

function currentPostgresProfile() {
  return postgresState.profiles.find(profile => profile.id === postgresState.selectedProfileId) ?? null;
}

function designMatchesPostgresTarget() {
  const profile = currentPostgresProfile();
  return Boolean(
    profile
    && schema.postgres?.sourceProfileId === profile.id
    && schema.postgres?.database === profile.dbname
    && schema.postgres?.namespace === postgresState.namespace
  );
}

function routineDeclarationIdentity(definition) {
  const start = definition.match(/^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\s+[^()]+\(/i);
  if (!start) return "";
  let depth = 1;
  let quote = null;
  for (let index = start[0].length; index < definition.length; index += 1) {
    const character = definition[index];
    const next = definition[index + 1];
    if (quote) {
      if (character === quote) {
        if (next === quote) index += 1;
        else quote = null;
      }
      continue;
    }
    if (["'", '"'].includes(character)) quote = character;
    else if (character === "(") depth += 1;
    else if (character === ")") {
      depth -= 1;
      if (depth === 0) return definition.slice(0, index + 1).replace(/\s+/g, " ").trim().toLowerCase();
    }
  }
  return "";
}

function setPostgresStatus(message = "", error = false, profileEditor = false) {
  const target = profileEditor ? elements.postgresProfileStatus : elements.postgresStatus;
  target.textContent = message;
  target.hidden = !message;
  target.classList.toggle("error", error);
}

function updatePostgresControls() {
  const ready = Boolean(postgresState.selectedProfileId && postgresState.namespace);
  elements.postgresNamespaceSelect.disabled = postgresState.busy || !postgresState.selectedProfileId;
  elements.postgresRefreshButton.disabled = postgresState.busy || !postgresState.selectedProfileId;
  elements.postgresImportButton.disabled = postgresState.busy || !ready;
  elements.postgresPreviewButton.disabled = postgresState.busy || !ready;
  elements.postgresObjectsButton.disabled = postgresState.busy || !ready;
  elements.postgresImportButton.textContent = designMatchesPostgresTarget() ? "Refresh design" : "Import";
  elements.applyMigrationButton.disabled = postgresState.busy || !postgresState.plan;
  document.querySelectorAll("[data-postgres-action]").forEach(button => { button.disabled = postgresState.busy; });
}

function setPostgresBusy(busy, message = "") {
  postgresState.busy = busy;
  if (message) setPostgresStatus(message);
  updatePostgresControls();
}

function renderPostgresProfiles() {
  if (!postgresState.profiles.length) {
    elements.postgresProfilesList.innerHTML = '<div class="postgres-empty">No connections yet. Add a server-side profile to import a PostgreSQL namespace.</div>';
    updatePostgresControls();
    return;
  }
  elements.postgresProfilesList.innerHTML = postgresState.profiles.map(profile => `
    <article class="postgres-profile-item ${profile.id === postgresState.selectedProfileId ? "selected" : ""}" data-profile-id="${escapeHtml(profile.id)}">
      <div>
        <div class="postgres-profile-name">${escapeHtml(profile.name)}${profile.id === postgresState.selectedProfileId ? '<span class="current-badge">Selected</span>' : ""}</div>
        <div class="postgres-profile-meta">${escapeHtml(profile.user)}@${escapeHtml(profile.host)}:${profile.port} / ${escapeHtml(profile.dbname)} · SSL ${escapeHtml(profile.sslmode)}</div>
      </div>
      <div class="postgres-profile-actions">
        <button class="button button-ghost" data-postgres-action="test" type="button">Test</button>
        <button class="button button-ghost" data-postgres-action="edit" type="button">Edit</button>
        <button class="button button-ghost" data-postgres-action="delete" type="button">Delete</button>
      </div>
    </article>
  `).join("");
  updatePostgresControls();
}

function renderNamespaceOptions() {
  elements.postgresNamespaceSelect.innerHTML = postgresState.namespaces.length
    ? postgresState.namespaces.map(namespace => `<option value="${escapeHtml(namespace)}" ${namespace === postgresState.namespace ? "selected" : ""}>${escapeHtml(namespace)}</option>`).join("")
    : '<option value="">No user namespaces found</option>';
  updatePostgresControls();
}

function renderPostgresCatalogSummary() {
  const checks = schema.tables.reduce((total, table) => total + (table.checks?.length ?? 0), 0);
  const indexes = schema.tables.reduce((total, table) => total + (table.indexes?.length ?? 0), 0);
  const triggers = schema.tables.reduce((total, table) => total + (table.triggers?.length ?? 0), 0);
  const source = schema.postgres;
  elements.postgresCatalogSummary.hidden = false;
  elements.postgresCatalogSummary.textContent = `${source ? `${source.database}.${source.namespace} · ` : "Current design · "}${schema.tables.length} tables · ${(schema.views ?? []).length} views · ${(schema.functions ?? []).length} routines · ${indexes} indexes · ${checks} checks · ${triggers} triggers`;
}

async function loadPostgresProfiles() {
  setPostgresBusy(true, "Loading PostgreSQL connections...");
  try {
    const payload = await postgresRequest("/api/postgres/profiles", { method: "GET" });
    postgresState.profiles = payload.profiles ?? [];
    if (!postgresState.profiles.some(profile => profile.id === postgresState.selectedProfileId)) {
      const sourceProfile = schema.postgres?.sourceProfileId;
      postgresState.selectedProfileId = postgresState.profiles.some(profile => profile.id === sourceProfile) ? sourceProfile : postgresState.profiles[0]?.id ?? null;
    }
    renderPostgresProfiles();
    if (postgresState.selectedProfileId) await loadPostgresNamespaces();
    else {
      postgresState.namespaces = [];
      postgresState.namespace = "";
      renderNamespaceOptions();
    }
    setPostgresStatus("");
  } catch (error) {
    setPostgresStatus(error.message, true);
  } finally {
    setPostgresBusy(false);
    renderPostgresCatalogSummary();
  }
}

async function loadPostgresNamespaces() {
  if (!postgresState.selectedProfileId) return false;
  setPostgresBusy(true, "Connecting and loading namespaces...");
  try {
    const payload = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(postgresState.selectedProfileId)}/namespaces`, { method: "GET" });
    postgresState.namespaces = payload.namespaces ?? [];
    const preferred = schema.postgres?.sourceProfileId === postgresState.selectedProfileId ? schema.postgres.namespace : "public";
    if (!postgresState.namespaces.includes(postgresState.namespace)) {
      postgresState.namespace = postgresState.namespaces.includes(preferred) ? preferred : postgresState.namespaces[0] ?? "";
    }
    renderNamespaceOptions();
    setPostgresStatus(postgresState.namespace ? "Connected. Choose Import or preview the current design." : "The connection has no user namespaces.");
    return true;
  } catch (error) {
    postgresState.namespaces = [];
    postgresState.namespace = "";
    renderNamespaceOptions();
    setPostgresStatus(error.message, true);
    return false;
  } finally {
    setPostgresBusy(false);
  }
}

function openPostgresProfileEditor(profileId = null) {
  const profile = postgresState.profiles.find(item => item.id === profileId);
  postgresState.editingProfileId = profile?.id ?? null;
  elements.postgresProfileTitle.textContent = profile ? "Edit connection" : "New connection";
  elements.postgresProfileName.value = profile?.name ?? "";
  elements.postgresProfileHost.value = profile?.host ?? "127.0.0.1";
  elements.postgresProfilePort.value = profile?.port ?? 5432;
  elements.postgresProfileDatabase.value = profile?.dbname ?? "";
  elements.postgresProfileUser.value = profile?.user ?? "";
  elements.postgresProfilePassword.value = "";
  elements.postgresProfileSslmode.value = profile?.sslmode ?? "prefer";
  elements.postgresProfileTimeout.value = profile?.timeout ?? 10;
  setPostgresStatus("", false, true);
  if (elements.postgresDialog.open) elements.postgresDialog.close();
  elements.postgresProfileDialog.showModal();
}

function postgresProfilePayload() {
  return {
    name: elements.postgresProfileName.value.trim(),
    host: elements.postgresProfileHost.value.trim(),
    port: Number(elements.postgresProfilePort.value),
    dbname: elements.postgresProfileDatabase.value.trim(),
    user: elements.postgresProfileUser.value.trim(),
    password: elements.postgresProfilePassword.value,
    sslmode: elements.postgresProfileSslmode.value,
    timeout: Number(elements.postgresProfileTimeout.value)
  };
}

async function savePostgresProfile(reopen = true) {
  const profileId = postgresState.editingProfileId;
  const path = profileId ? `/api/postgres/profiles/${encodeURIComponent(profileId)}` : "/api/postgres/profiles";
  setPostgresStatus("Saving connection...", false, true);
  try {
    const profile = await postgresRequest(path, { method: profileId ? "PUT" : "POST", body: JSON.stringify(postgresProfilePayload()) });
    postgresState.editingProfileId = profile.id;
    postgresState.selectedProfileId = profile.id;
    elements.postgresProfilePassword.value = "";
    await loadPostgresProfiles();
    if (elements.postgresProfileDialog.open) elements.postgresProfileDialog.close();
    if (reopen && !elements.postgresDialog.open) elements.postgresDialog.showModal();
    return profile;
  } catch (error) {
    setPostgresStatus(error.message, true, true);
    return null;
  }
}

async function testPostgresProfile(profileId) {
  setPostgresBusy(true, "Testing PostgreSQL connection...");
  try {
    const result = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(profileId)}/test`, { method: "POST", body: "{}" });
    setPostgresStatus(`Connected to ${result.database}. ${result.serverVersion}`);
  } catch (error) {
    setPostgresStatus(error.message, true);
  } finally {
    setPostgresBusy(false);
  }
}

function preserveTableLayout(importedSchema, previousSchema) {
  const positions = new Map(previousSchema.tables.map(table => [`${table.namespace ?? previousSchema.postgres?.namespace ?? ""}.${table.name}`, table]));
  const positionsByOid = new Map(previousSchema.tables.filter(table => table.postgres?.liveOid != null).map(table => [String(table.postgres.liveOid), table]));
  for (const table of importedSchema.tables) {
    const prior = table.postgres?.liveOid != null
      ? positionsByOid.get(String(table.postgres.liveOid)) ?? positions.get(`${table.namespace ?? importedSchema.postgres?.namespace ?? ""}.${table.name}`)
      : positions.get(`${table.namespace ?? importedSchema.postgres?.namespace ?? ""}.${table.name}`);
    if (prior) {
      Object.assign(table, { x: prior.x, y: prior.y, color: prior.color });
      const available = [...(table.columns ?? [])];
      const ordered = [];
      for (const previousColumn of prior.columns ?? []) {
        const matchIndex = available.findIndex(column =>
          column.name === previousColumn.name
          || column.id === previousColumn.id
          || (column.ordinal != null && previousColumn.ordinal != null && column.ordinal === previousColumn.ordinal)
        );
        if (matchIndex !== -1) ordered.push(...available.splice(matchIndex, 1));
      }
      table.columns = [...ordered, ...available];
    }
  }
  return importedSchema;
}

async function importPostgresSchema() {
  if (!postgresState.selectedProfileId || !postgresState.namespace) return;
  setPostgresBusy(true, "Reading PostgreSQL catalog...");
  try {
    await flushPendingSave();
    const imported = migrateSchema(await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(postgresState.selectedProfileId)}/introspect`, {
      method: "POST",
      body: JSON.stringify({ namespace: postgresState.namespace })
    }));
    if (designMatchesPostgresTarget()) {
      const projectName = schema.projectName;
      schema = preserveTableLayout(imported, schema);
      schema.projectName = projectName;
      selectedTableId = null;
      selectedTableIds = new Set();
      setRelationMode(false);
      await persistCurrentSchema();
      elements.saveStatus.textContent = "Saved to file";
      elements.postgresDialog.close();
      render();
      showToast(`Refreshed ${schema.postgres.database}.${schema.postgres.namespace} without changing the layout`);
      return;
    }
    const importedSchemaId = uid("schema");
    await persistSchemaRecord(importedSchemaId, imported);
    activeSchemaId = importedSchemaId;
    schema = imported;
    view = { x: 45, y: 35, zoom: 1 };
    resetSchemaSession();
    elements.saveStatus.textContent = "Saved to file";
    elements.postgresDialog.close();
    render();
    requestAnimationFrame(fitDiagram);
    showToast(`Imported ${schema.postgres.database}.${schema.postgres.namespace}`);
  } catch (error) {
    setPostgresStatus(error.message, true);
  } finally {
    setPostgresBusy(false);
    renderPostgresCatalogSummary();
  }
}

function renderMigrationPreview() {
  const profile = currentPostgresProfile();
  const plan = postgresState.plan;
  elements.migrationTarget.textContent = `${profile?.name ?? "PostgreSQL"} · ${profile?.dbname ?? ""}.${postgresState.namespace}`;
  const counts = new Map();
  for (const step of plan.steps) counts.set(step.action, (counts.get(step.action) ?? 0) + 1);
  elements.migrationSummary.innerHTML = plan.steps.length
    ? [...counts].map(([action, count]) => `<span>${count} ${escapeHtml(action.replaceAll("_", " "))}</span>`).join("")
    : "<span>No database changes</span>";
  const warningMessages = (plan.warnings ?? []).map(warning => warning.message);
  elements.migrationWarnings.hidden = !warningMessages.length;
  elements.migrationWarnings.innerHTML = warningMessages.map(message => `<div>${escapeHtml(message)}</div>`).join("");
  elements.migrationSql.value = plan.steps.length
    ? `BEGIN;\n\n${plan.steps.map(step => `-- ${step.action} ${step.objectType}: ${step.name}\n${step.sql}`).join("\n\n")}\n\nCOMMIT;`
    : "-- The design already matches the selected PostgreSQL namespace.";
  elements.destructiveConfirmation.hidden = !plan.destructive;
  elements.confirmDestructive.checked = false;
  elements.applyMigrationButton.textContent = plan.steps.length ? "Apply migration" : "Done";
  updatePostgresControls();
}

async function previewPostgresMigration() {
  if (!postgresState.selectedProfileId || !postgresState.namespace) return;
  setPostgresBusy(true, "Comparing the design with PostgreSQL...");
  try {
    await flushPendingSave();
    postgresState.schemaSnapshot = JSON.stringify(schema);
    postgresState.plan = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(postgresState.selectedProfileId)}/preview`, {
      method: "POST",
      body: JSON.stringify({ namespace: postgresState.namespace, schema, allowDestructive: elements.includeDestructive.checked })
    });
    renderMigrationPreview();
    if (elements.postgresDialog.open) elements.postgresDialog.close();
    if (!elements.migrationDialog.open) elements.migrationDialog.showModal();
  } catch (error) {
    if (elements.migrationDialog.open) {
      elements.migrationWarnings.hidden = false;
      elements.migrationWarnings.textContent = error.message;
    } else {
      setPostgresStatus(error.message, true);
    }
  } finally {
    setPostgresBusy(false);
  }
}

async function applyPostgresMigration() {
  if (!postgresState.plan) return;
  if (postgresState.schemaSnapshot !== JSON.stringify(schema)) return showToast("The design changed. Refresh the migration preview");
  if (!postgresState.plan.steps.length) {
    elements.migrationDialog.close();
    showToast("Design and PostgreSQL are already synchronized");
    return;
  }
  if (postgresState.plan.destructive && !elements.confirmDestructive.checked) return showToast("Confirm the destructive migration first");
  const targetSchemaId = activeSchemaId;
  const targetSchema = clone(schema);
  const targetSnapshot = postgresState.schemaSnapshot;
  setPostgresBusy(true);
  elements.applyMigrationButton.textContent = "Applying...";
  let databaseApplied = false;
  try {
    const refreshed = migrateSchema(await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(postgresState.selectedProfileId)}/plans/${encodeURIComponent(postgresState.plan.id)}/apply`, {
      method: "POST",
      body: JSON.stringify({ confirmDestructive: elements.confirmDestructive.checked })
    }));
    databaseApplied = true;
    const merged = preserveTableLayout(refreshed, targetSchema);
    merged.projectName = targetSchema.projectName;
    postgresState.plan = null;
    postgresState.schemaSnapshot = null;
    elements.databaseDriftBanner.hidden = true;
    if (activeSchemaId === targetSchemaId && JSON.stringify(schema) !== targetSnapshot) {
      elements.migrationWarnings.hidden = false;
      elements.migrationWarnings.textContent = "PostgreSQL was updated, but the local design changed during apply. Preview again to reconcile those newer edits.";
      showToast("Database updated; local edits still need reconciliation");
      return;
    }
    try {
      await persistSchemaRecord(targetSchemaId, merged);
    } catch (error) {
      elements.migrationWarnings.hidden = false;
      elements.migrationWarnings.textContent = `PostgreSQL was updated, but the local design could not be saved: ${error.message}`;
      if (activeSchemaId === targetSchemaId) {
        schema = merged;
        render();
      }
      showToast("Database updated; local design save needs attention");
      return;
    }
    if (activeSchemaId === targetSchemaId) {
      schema = merged;
      render();
    }
    elements.migrationDialog.close();
    const warning = refreshed.postgres?.refreshWarning || refreshed.postgres?.historyWarning;
    showToast(warning ? warning : "PostgreSQL migration applied successfully");
  } catch (error) {
    elements.migrationWarnings.hidden = false;
    elements.migrationWarnings.textContent = databaseApplied ? `PostgreSQL was updated, but local synchronization failed: ${error.message}` : error.message;
  } finally {
    elements.applyMigrationButton.textContent = postgresState.plan?.steps?.length ? "Apply migration" : "Done";
    setPostgresBusy(false);
  }
}

function collectDatabaseObjects() {
  const objects = (schema.views ?? []).map(viewItem => ({ kind: viewItem.materialized ? "materialized-view" : "view", item: viewItem, table: null }));
  for (const table of schema.tables) {
    for (const item of table.checks ?? []) objects.push({ kind: "check", item, table });
    for (const item of table.indexes ?? []) objects.push({ kind: "index", item, table });
    for (const item of table.triggers ?? []) objects.push({ kind: "trigger", item, table });
  }
  return objects;
}

function renderDatabaseObjects() {
  const objects = collectDatabaseObjects();
  elements.databaseObjectsCount.textContent = `${objects.length} managed object${objects.length === 1 ? "" : "s"}`;
  elements.databaseObjectsList.innerHTML = objects.length ? objects.map(({ kind, item, table }) => `
    <article class="database-object-item" data-object-kind="${kind}" data-object-id="${escapeHtml(item.id)}" data-object-table-id="${escapeHtml(table?.id ?? "")}">
      <span class="database-object-kind">${escapeHtml(kind.replace("-", " "))}</span>
      <span class="database-object-name">${escapeHtml(item.name)}</span>
      <span class="database-object-table">${escapeHtml(table?.name ?? item.namespace ?? schema.postgres?.namespace ?? "")}</span>
    </article>
  `).join("") : '<div class="postgres-empty">No checks, indexes, views, or triggers in this design.</div>';
}

function tableDatabaseObjectKey(kind) {
  if (kind === "check") return "checks";
  if (kind === "index") return "indexes";
  return `${kind}s`;
}

function findDatabaseObject(reference) {
  if (!reference) return null;
  if (["view", "materialized-view"].includes(reference.kind)) return (schema.views ?? []).find(item => item.id === reference.id) ?? null;
  const table = getTable(reference.tableId);
  const key = tableDatabaseObjectKey(reference.kind);
  return table?.[key]?.find(item => item.id === reference.id) ?? null;
}

function formatDatabaseObjectDefinition(definition, kind) {
  if (kind !== "trigger" || !definition || definition.includes("\n")) return definition ?? "";
  return definition
    .replace(/\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+/i, "\n$1 ")
    .replace(/\s+(FOR\s+EACH\s+(?:ROW|STATEMENT))\s+/i, "\n$1\n")
    .replace(/\s+(WHEN\s*\()/i, "\n$1")
    .replace(/\s+(EXECUTE\s+(?:FUNCTION|PROCEDURE))\s+/i, "\n$1 ");
}

function updateDatabaseObjectTableState() {
  const needsTable = !["view", "materialized-view"].includes(elements.databaseObjectType.value);
  elements.databaseObjectType.disabled = postgresState.objectEditorContext === "inspector";
  elements.databaseObjectTable.disabled = !needsTable || postgresState.objectEditorContext === "inspector";
  elements.databaseObjectTable.closest("label").style.opacity = needsTable ? "1" : ".4";
}

function openDatabaseObjectEditor(reference = null, context = "objects") {
  const item = findDatabaseObject(reference);
  postgresState.editingObject = reference;
  postgresState.objectEditorContext = context;
  const inspectorEditor = context === "inspector";
  const objectLabel = reference?.kind === "check" ? "check constraint" : (reference?.kind ?? "database object").replace("-", " ");
  elements.databaseObjectEditorTitle.textContent = inspectorEditor ? `${item ? "Edit" : "New"} ${objectLabel}` : (item ? "Edit database object" : "New database object");
  elements.databaseObjectType.value = reference?.kind ?? "check";
  elements.databaseObjectTable.innerHTML = schema.tables.map(table => `<option value="${escapeHtml(table.id)}">${escapeHtml(table.name)}</option>`).join("");
  elements.databaseObjectTable.value = reference?.tableId ?? schema.tables[0]?.id ?? "";
  const selectedTable = getTable(reference?.tableId);
  elements.databaseObjectName.value = item?.name
    ?? (inspectorEditor && reference?.kind === "check" && selectedTable ? availableCheckConstraintName(selectedTable) : "");
  elements.databaseObjectName.placeholder = reference?.kind === "check" && selectedTable ? `${selectedTable.name}_check` : "";
  postgresState.objectEditorOriginalDefinition = item?.definition ?? "";
  postgresState.objectEditorDisplayDefinition = formatDatabaseObjectDefinition(postgresState.objectEditorOriginalDefinition, reference?.kind);
  elements.databaseObjectDefinition.value = postgresState.objectEditorDisplayDefinition;
  elements.databaseObjectDefinition.placeholder = reference?.kind === "check" ? "CHECK (column_name > 0)" : "";
  elements.deleteDatabaseObject.hidden = !item;
  updateDatabaseObjectTableState();
  if (elements.databaseObjectsDialog.open) elements.databaseObjectsDialog.close();
  elements.databaseObjectEditorDialog.showModal();
}

function closeDatabaseObjectEditor() {
  const returnToObjects = postgresState.objectEditorContext === "objects";
  elements.databaseObjectEditorDialog.close();
  postgresState.editingObject = null;
  postgresState.objectEditorOriginalDefinition = "";
  postgresState.objectEditorDisplayDefinition = "";
  if (returnToObjects) {
    renderDatabaseObjects();
    elements.databaseObjectsDialog.showModal();
  } else {
    renderInspector();
  }
}

function removeDatabaseObject(reference) {
  if (!reference) return;
  if (["view", "materialized-view"].includes(reference.kind)) {
    schema.views = (schema.views ?? []).filter(item => item.id !== reference.id);
    return;
  }
  const table = getTable(reference.tableId);
  const key = tableDatabaseObjectKey(reference.kind);
  if (table) table[key] = (table[key] ?? []).filter(item => item.id !== reference.id);
}

function saveDatabaseObject() {
  const kind = elements.databaseObjectType.value;
  const table = getTable(elements.databaseObjectTable.value);
  if (!["view", "materialized-view"].includes(kind) && !table) return showToast("Choose a table for this object");
  const previous = findDatabaseObject(postgresState.editingObject);
  const editedDefinition = elements.databaseObjectDefinition.value.trim();
  const definitionUnchanged = previous
    && kind === postgresState.editingObject?.kind
    && editedDefinition === postgresState.objectEditorDisplayDefinition.trim();
  const item = {
    ...(previous ?? {}),
    id: previous?.id ?? uid(kind.replace("-", "_")),
    name: elements.databaseObjectName.value.trim(),
    definition: definitionUnchanged ? postgresState.objectEditorOriginalDefinition : editedDefinition
  };
  if (!item.name || !item.definition) return showToast("Object name and SQL definition are required");
  if (kind === "check" && !/^CHECK\s*\(/i.test(item.definition)) return showToast("Check definitions must start with CHECK (");
  if (kind === "check" && table.checks?.some(check => check.id !== previous?.id && check.name === item.name)) return showToast("A check constraint with that name already exists");
  checkpointHistory();
  removeDatabaseObject(postgresState.editingObject);
  if (["view", "materialized-view"].includes(kind)) {
    item.namespace = schema.postgres?.namespace ?? postgresState.namespace ?? "public";
    item.materialized = kind === "materialized-view";
    if (!schema.views) schema.views = [];
    schema.views.push(item);
  } else {
    const key = tableDatabaseObjectKey(kind);
    if (!table[key]) table[key] = [];
    table[key].push(item);
  }
  saveSchema();
  closeDatabaseObjectEditor();
}

const AI_SCHEMA_ACTIONS = new Set(["populate_schema", "add_table", "rename_table", "add_column", "update_column", "delete_element", "add_relationship"]);
const AI_NAVIGATION_ACTIONS = new Set(["create_project", "open_project", "open_connection"]);
const AI_TOOL_LABELS = {
  schema_read_query: "Preparing read-only SQL",
  schema_add_table: "Drafting a table",
  schema_rename_table: "Drafting a table rename",
  schema_add_column: "Drafting a column",
  schema_update_column: "Drafting a column change",
  schema_delete_element: "Reviewing a deletion",
  schema_add_relationship: "Drafting a relationship",
  schema_populate: "Designing a complete schema",
  schema_connection_setup: "Preparing connection settings",
  schema_project_create: "Preparing a local project",
  schema_project_open: "Finding a local project",
  schema_connection_open: "Finding a saved connection",
  schema_migration_preview: "Preparing migration preview",
  schema_migration_apply: "Reviewing migration apply"
};
const AI_SKILL_LABELS = {
  "schemii-help": "Schemii guidance",
  "connection-setup": "Connection safety",
  "migration-safety": "Migration safety",
  "schema-design-layout": "Schema design and layout",
  "read-only-query-safety": "Read-only query safety",
  "target-selection": "Target verification"
};

function aiActionType(action) {
  return String(action?.type ?? action?.action ?? action?.kind ?? "").toLowerCase().replaceAll("-", "_");
}

function aiActionPayload(action) {
  return action?.payload && typeof action.payload === "object" ? action.payload : action?.data && typeof action.data === "object" ? action.data : action ?? {};
}

function aiNamedTable(schemaValue, payload, prefix = "") {
  const id = payload[`${prefix}TableId`] ?? (prefix ? payload[prefix]?.tableId : payload.tableId);
  const nested = prefix ? payload[prefix] : payload.table;
  const name = payload[`${prefix}TableName`] ?? (prefix ? nested?.tableName ?? nested?.table ?? (typeof nested === "string" ? nested : nested?.name) : payload.tableName ?? payload.table?.name);
  return schemaValue.tables.find(table => table.id === id || (!id && table.name === name)) ?? null;
}

function aiNamedColumn(table, payload, prefix = "") {
  const id = payload[`${prefix}ColumnId`] ?? (prefix ? payload[prefix]?.columnId : payload.columnId);
  const nested = prefix ? payload[prefix] : payload.column;
  const name = payload[`${prefix}ColumnName`] ?? (prefix ? nested?.columnName ?? nested?.column ?? (typeof nested === "string" ? nested : nested?.name) : payload.columnName ?? payload.column?.name);
  return table?.columns.find(column => column.id === id || (!id && column.name === name)) ?? null;
}

function validAiName(value, label) {
  if (typeof value !== "string" || !value.trim()) return `${label} is required`;
  if (new TextEncoder().encode(value.trim()).length > 63) return `${label} must be at most 63 bytes`;
  return "";
}

function canonicalAiRelationshipType(value) {
  const type = String(value ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  const aliases = {
    serial: "integer", int: "integer", int4: "integer",
    bigserial: "bigint", int8: "bigint",
    smallserial: "smallint", int2: "smallint",
    bool: "boolean",
    varchar: "character varying"
  };
  return aliases[type] ?? type;
}

function validateAiPopulateAction(schemaValue, payload) {
  const allowedPayload = new Set(["type", "purpose", "tables", "relationships", "requiresConfirmation"]);
  if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).some(key => !allowedPayload.has(key))) return { ok: false, error: "The schema proposal contains unsupported fields" };
  if (!Array.isArray(payload.tables) || payload.tables.length < 1 || payload.tables.length > 20) return { ok: false, error: "A complete schema proposal needs 1 to 20 tables" };
  if (!Array.isArray(payload.relationships) || payload.relationships.length > 50) return { ok: false, error: "Schema relationships must be a list of at most 50 items" };
  const tables = [];
  const names = new Set(schemaValue.tables.map(table => table.name.toLowerCase()));
  for (const proposedTable of payload.tables) {
    if (!proposedTable || typeof proposedTable !== "object" || Array.isArray(proposedTable) || Object.keys(proposedTable).some(key => !["name", "purpose", "columns"].includes(key))) return { ok: false, error: "A proposed table contains unsupported fields" };
    const tableNameError = validAiName(proposedTable.name, "Table name");
    if (tableNameError) return { ok: false, error: tableNameError };
    const tableName = proposedTable.name.trim();
    if (names.has(tableName.toLowerCase())) return { ok: false, error: `Table ${tableName} already exists or is duplicated` };
    names.add(tableName.toLowerCase());
    if (!Array.isArray(proposedTable.columns) || proposedTable.columns.length < 1 || proposedTable.columns.length > 50) return { ok: false, error: `Table ${tableName} needs 1 to 50 columns` };
    const columnNames = new Set();
    const columns = [];
    for (const proposedColumn of proposedTable.columns) {
      if (!proposedColumn || typeof proposedColumn !== "object" || Array.isArray(proposedColumn) || Object.keys(proposedColumn).some(key => !["name", "type", "primary", "nullable", "unique", "default"].includes(key))) return { ok: false, error: `Table ${tableName} has a column with unsupported fields` };
      const columnNameError = validAiName(proposedColumn.name, "Column name");
      if (columnNameError) return { ok: false, error: columnNameError };
      const columnName = proposedColumn.name.trim();
      if (columnNames.has(columnName.toLowerCase())) return { ok: false, error: `Column ${columnName} is duplicated in ${tableName}` };
      columnNames.add(columnName.toLowerCase());
      if (typeof proposedColumn.type !== "string" || !proposedColumn.type.trim() || new TextEncoder().encode(proposedColumn.type.trim()).length > 128) return { ok: false, error: `Column ${tableName}.${columnName} has an invalid type` };
      for (const key of ["primary", "nullable", "unique"]) if (proposedColumn[key] != null && typeof proposedColumn[key] !== "boolean") return { ok: false, error: `${tableName}.${columnName} ${key} must be true or false` };
      if (proposedColumn.default != null && (typeof proposedColumn.default !== "string" || new TextEncoder().encode(proposedColumn.default).length > 1000 || /[\x00]/.test(proposedColumn.default))) return { ok: false, error: `${tableName}.${columnName} has an invalid default` };
      columns.push({
        name: columnName, type: proposedColumn.type.trim(), primary: proposedColumn.primary === true,
        nullable: proposedColumn.primary === true ? false : proposedColumn.nullable !== false,
        unique: proposedColumn.primary === true || proposedColumn.unique === true,
        default: proposedColumn.default ?? ""
      });
    }
    tables.push({ name: tableName, columns });
  }
  const allTables = [...schemaValue.tables, ...tables];
  const relationships = [];
  const relationshipKeys = new Set();
  const actions = new Set(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]);
  for (const relation of payload.relationships) {
    if (!relation || typeof relation !== "object" || Array.isArray(relation) || Object.keys(relation).some(key => !["fromTableName", "fromColumnName", "toTableName", "toColumnName", "constraintName", "onDelete", "onUpdate"].includes(key))) return { ok: false, error: "A relationship contains unsupported fields" };
    const fromTable = allTables.find(table => table.name.toLowerCase() === String(relation.fromTableName ?? "").toLowerCase());
    const toTable = allTables.find(table => table.name.toLowerCase() === String(relation.toTableName ?? "").toLowerCase());
    const fromColumn = fromTable?.columns.find(column => column.name.toLowerCase() === String(relation.fromColumnName ?? "").toLowerCase());
    const toColumn = toTable?.columns.find(column => column.name.toLowerCase() === String(relation.toColumnName ?? "").toLowerCase());
    if (!fromTable || !toTable || !fromColumn || !toColumn) return { ok: false, error: "Every relationship must reference proposed or existing table columns" };
    if (canonicalAiRelationshipType(fromColumn.type) !== canonicalAiRelationshipType(toColumn.type)) return { ok: false, error: `Relationship ${fromTable.name}.${fromColumn.name} has mismatched column types` };
    const targetKey = toColumn.primary || toColumn.unique || (toTable.uniqueConstraints ?? []).some(constraint => constraint.columnIds?.length === 1 && constraint.columnIds[0] === toColumn.id);
    if (!targetKey) return { ok: false, error: `Relationship target ${toTable.name}.${toColumn.name} must be primary or unique` };
    const onDelete = relation.onDelete ?? "NO ACTION";
    const onUpdate = relation.onUpdate ?? "NO ACTION";
    if (!actions.has(onDelete) || !actions.has(onUpdate)) return { ok: false, error: "Relationship actions are invalid" };
    if (onDelete === "SET NULL" && !fromColumn.nullable) return { ok: false, error: `SET NULL requires nullable column ${fromTable.name}.${fromColumn.name}` };
    const key = `${fromTable.name}.${fromColumn.name}->${toTable.name}.${toColumn.name}`.toLowerCase();
    if (relationshipKeys.has(key)) return { ok: false, error: "A proposed relationship is duplicated" };
    relationshipKeys.add(key);
    if (relation.constraintName != null) {
      const constraintError = validAiName(relation.constraintName, "Relationship constraint name");
      if (constraintError) return { ok: false, error: constraintError };
    }
    relationships.push({ fromTableName: fromTable.name, fromColumnName: fromColumn.name, toTableName: toTable.name, toColumnName: toColumn.name, constraintName: relation.constraintName?.trim(), onDelete, onUpdate });
  }
  return { ok: true, type: "populate_schema", tables, relationships };
}

function validateAiNavigationAction(action) {
  const type = aiActionType(action);
  const payload = aiActionPayload(action);
  if (!AI_NAVIGATION_ACTIONS.has(type) || !payload || typeof payload !== "object" || Array.isArray(payload)) return { ok: false, error: "Unsupported navigation action" };
  const allowed = {
    create_project: new Set(["type", "projectName", "requiresConfirmation"]),
    open_project: new Set(["type", "schemaId", "projectName", "requiresConfirmation"]),
    open_connection: new Set(["type", "profileId", "name", "database", "namespace", "requiresConfirmation"])
  }[type];
  if (Object.keys(payload).some(key => !allowed.has(key))) return { ok: false, error: "The navigation proposal contains unsupported fields" };
  if (type === "create_project") {
    if (typeof payload.projectName !== "string" || !payload.projectName.trim() || new TextEncoder().encode(payload.projectName.trim()).length > 256 || /[\x00-\x1f\x7f]/.test(payload.projectName)) return { ok: false, error: "Project name is invalid" };
    return { ok: true, type, projectName: payload.projectName.trim() };
  }
  if (type === "open_project") {
    if (typeof payload.schemaId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(payload.schemaId)) return { ok: false, error: "Project ID is invalid" };
    const record = readSchemaLibrary().schemas.find(item => item.id === payload.schemaId);
    if (!record) return { ok: false, error: "That local project no longer exists" };
    if (record.id === activeSchemaId) return { ok: false, error: "That local project is already open" };
    if (typeof payload.projectName !== "string" || record.schema.projectName !== payload.projectName) return { ok: false, error: "The local project identity changed" };
    return { ok: true, type, record };
  }
  if (typeof payload.profileId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(payload.profileId)) return { ok: false, error: "Connection ID is invalid" };
  if (typeof payload.name !== "string" || !payload.name.trim() || typeof payload.database !== "string" || !payload.database.trim()) return { ok: false, error: "Connection identity is invalid" };
  if (payload.namespace != null && (typeof payload.namespace !== "string" || !payload.namespace.trim() || new TextEncoder().encode(payload.namespace).length > 63 || /[\x00-\x1f\x7f]/.test(payload.namespace))) return { ok: false, error: "Namespace is invalid" };
  return { ok: true, type, payload };
}

function validateAiSchemaAction(schemaValue, action) {
  const type = aiActionType(action);
  const payload = aiActionPayload(action);
  if (!AI_SCHEMA_ACTIONS.has(type)) return { ok: false, error: "Unsupported schema action" };
  if (!schemaValue || !Array.isArray(schemaValue.tables) || !Array.isArray(schemaValue.relationships)) return { ok: false, error: "The active schema is invalid" };
  if (type === "populate_schema") return validateAiPopulateAction(schemaValue, payload);
  if (type === "add_table") {
    const proposal = payload.table ?? payload;
    const error = validAiName(proposal.name, "Table name");
    if (error) return { ok: false, error };
    if (schemaValue.tables.some(table => table.name.toLowerCase() === proposal.name.trim().toLowerCase())) return { ok: false, error: "A table with that name already exists" };
    if (proposal.columns != null && !Array.isArray(proposal.columns)) return { ok: false, error: "Table columns must be a list" };
    for (const column of proposal.columns ?? []) {
      const columnError = validAiName(column?.name, "Column name");
      if (columnError) return { ok: false, error: columnError };
      if (typeof column.type !== "string" || !column.type.trim()) return { ok: false, error: "Column type is required" };
    }
    const names = (proposal.columns ?? []).map(column => column.name.toLowerCase());
    if (new Set(names).size !== names.length) return { ok: false, error: "Column names must be unique within a table" };
    return { ok: true, type, payload: proposal };
  }
  const table = aiNamedTable(schemaValue, payload);
  if (type !== "add_relationship" && !table) return { ok: false, error: "The target table no longer exists" };
  if (type === "rename_table") {
    const newName = payload.newName ?? payload.name;
    const error = validAiName(newName, "New table name");
    if (error) return { ok: false, error };
    if (schemaValue.tables.some(item => item.id !== table.id && item.name.toLowerCase() === newName.trim().toLowerCase())) return { ok: false, error: "A table with that name already exists" };
    return { ok: true, type, payload, table, newName: newName.trim() };
  }
  if (type === "add_column") {
    const column = payload.column ?? payload;
    const error = validAiName(column.name, "Column name");
    if (error) return { ok: false, error };
    if (typeof column.type !== "string" || !column.type.trim()) return { ok: false, error: "Column type is required" };
    if (table.columns.some(item => item.name.toLowerCase() === column.name.trim().toLowerCase())) return { ok: false, error: "That column already exists" };
    return { ok: true, type, payload, table, column };
  }
  if (type === "update_column") {
    const column = aiNamedColumn(table, payload);
    if (!column) return { ok: false, error: "The target column no longer exists" };
    const changes = payload.changes ?? payload.update ?? payload.column ?? {};
    const allowed = ["name", "type", "nullable", "unique", "primary", "default"];
    const keys = Object.keys(changes).filter(key => allowed.includes(key));
    if (!keys.length) return { ok: false, error: "No supported column changes were supplied" };
    if (keys.includes("name")) {
      const error = validAiName(changes.name, "Column name");
      if (error) return { ok: false, error };
      if (table.columns.some(item => item.id !== column.id && item.name.toLowerCase() === changes.name.trim().toLowerCase())) return { ok: false, error: "That column name already exists" };
    }
    if (keys.includes("type") && (typeof changes.type !== "string" || !changes.type.trim())) return { ok: false, error: "Column type is required" };
    for (const key of ["nullable", "unique", "primary"]) if (keys.includes(key) && typeof changes[key] !== "boolean") return { ok: false, error: `${key} must be true or false` };
    return { ok: true, type, payload, table, column, changes: Object.fromEntries(keys.map(key => [key, changes[key]])) };
  }
  if (type === "delete_element") {
    const elementType = String(payload.elementType ?? payload.targetType ?? (payload.columnId || payload.columnName ? "column" : "table")).toLowerCase();
    if (elementType === "table") return { ok: true, type, payload, table, elementType };
    if (elementType !== "column") return { ok: false, error: "Only table or column deletion is supported" };
    const column = aiNamedColumn(table, payload);
    if (!column) return { ok: false, error: "The target column no longer exists" };
    if (table.columns.length === 1) return { ok: false, error: "A table needs at least one column" };
    return { ok: true, type, payload, table, column, elementType };
  }
  const fromTable = aiNamedTable(schemaValue, payload, "from") ?? table;
  const toTable = aiNamedTable(schemaValue, payload, "to");
  const fromColumn = aiNamedColumn(fromTable, payload, "from");
  const toColumn = aiNamedColumn(toTable, payload, "to");
  if (!fromTable || !toTable || !fromColumn || !toColumn) return { ok: false, error: "Both relationship columns must exist" };
  if (canonicalAiRelationshipType(fromColumn.type) !== canonicalAiRelationshipType(toColumn.type)) return { ok: false, error: "Relationship columns must have matching types" };
  const targetKey = toColumn.primary || toColumn.unique || (toTable.uniqueConstraints ?? []).some(constraint => constraint.columnIds.length === 1 && constraint.columnIds[0] === toColumn.id);
  if (!targetKey) return { ok: false, error: "The referenced column must be primary or unique" };
  const constraintName = payload.constraintName ?? payload.name;
  if (constraintName != null) {
    const error = validAiName(constraintName, "Relationship constraint name");
    if (error) return { ok: false, error };
  }
  if (schemaValue.relationships.some(item => item.fromTableId === fromTable.id && item.toTableId === toTable.id && relationshipColumnPairs(item).some(pair => pair.fromColumnId === fromColumn.id && pair.toColumnId === toColumn.id))) return { ok: false, error: "That relationship already exists" };
  return { ok: true, type, payload, table: fromTable, fromTable, toTable, fromColumn, toColumn };
}

function applyAiPopulation(schemaValue, validated, { startX, startY, gridColumns }) {
  const colorStart = schemaValue.tables.length;
  for (const [index, proposedTable] of validated.tables.entries()) {
    schemaValue.tables.push({
      id: uid("table"), name: proposedTable.name,
      x: startX + (index % gridColumns) * (TABLE_WIDTH + 55),
      y: startY + Math.floor(index / gridColumns) * 235,
      color: COLORS[(colorStart + index) % COLORS.length],
      columns: proposedTable.columns.map(column => ({ id: uid("col"), ...column })),
      uniqueConstraints: [], checks: [], indexes: [], triggers: []
    });
  }
  for (const relation of validated.relationships) {
    const fromTable = schemaValue.tables.find(table => table.name === relation.fromTableName);
    const toTable = schemaValue.tables.find(table => table.name === relation.toTableName);
    const fromColumn = fromTable.columns.find(column => column.name === relation.fromColumnName);
    const toColumn = toTable.columns.find(column => column.name === relation.toColumnName);
    schemaValue.relationships.push({
      id: uid("rel"), fromTableId: fromTable.id, fromColumnId: fromColumn.id,
      toTableId: toTable.id, toColumnId: toColumn.id,
      constraintName: relation.constraintName || `${fromTable.name}_${fromColumn.name}_fkey`,
      targetNamespace: toTable.namespace || schemaValue.postgres?.namespace,
      targetTableName: toTable.name, targetColumnNames: [toColumn.name],
      onUpdate: relation.onUpdate, onDelete: relation.onDelete,
      deferrable: false, initiallyDeferred: false, matchType: "SIMPLE", validated: true
    });
  }
}

async function applyAiSchemaAction(action) {
  const validated = validateAiSchemaAction(schema, action);
  if (!validated.ok) throw new Error(validated.error);
  const schemaBefore = clone(schema);
  checkpointHistory();
  if (validated.type === "populate_schema") {
    const rect = elements.workspace.getBoundingClientRect();
    const gridColumns = Math.ceil(Math.sqrt(validated.tables.length));
    const gridRows = Math.ceil(validated.tables.length / gridColumns);
    const startX = (rect.width / 2 - view.x) / view.zoom - ((gridColumns - 1) * (TABLE_WIDTH + 55) + TABLE_WIDTH) / 2;
    const startY = (rect.height / 2 - view.y) / view.zoom - (gridRows * 235) / 2;
    applyAiPopulation(schema, validated, { startX, startY, gridColumns });
  } else if (validated.type === "add_table") {
    const rect = elements.workspace.getBoundingClientRect();
    const columns = validated.payload.columns?.length ? validated.payload.columns : [{ name: "id", type: "uuid", primary: true, nullable: false, unique: true, default: "" }];
    const table = {
      id: uid("table"), name: validated.payload.name.trim(),
      x: (rect.width / 2 - view.x) / view.zoom - TABLE_WIDTH / 2,
      y: (rect.height / 2 - view.y) / view.zoom - 100,
      color: COLORS[schema.tables.length % COLORS.length],
      columns: columns.map(column => ({ id: uid("col"), name: column.name.trim(), type: column.type.trim(), primary: column.primary === true, nullable: column.primary ? false : column.nullable !== false, unique: column.primary || column.unique === true, default: typeof column.default === "string" ? column.default : "" })),
      uniqueConstraints: [], checks: [], indexes: [], triggers: []
    };
    schema.tables.push(table);
  } else if (validated.type === "rename_table") {
    const oldName = validated.table.name;
    validated.table.name = validated.newName;
    updateTableNameInObjects(validated.table, oldName, validated.newName);
  } else if (validated.type === "add_column") {
    const column = validated.column;
    validated.table.columns.push({ id: uid("col"), name: column.name.trim(), type: column.type.trim(), primary: column.primary === true, nullable: column.primary ? false : column.nullable !== false, unique: column.primary || column.unique === true, default: typeof column.default === "string" ? column.default : "" });
  } else if (validated.type === "update_column") {
    const oldName = validated.column.name;
    Object.assign(validated.column, validated.changes);
    if (validated.column.primary) {
      validated.column.nullable = false;
      validated.column.unique = true;
    }
    if (validated.changes.name && oldName !== validated.changes.name) updateColumnNameInObjects(validated.table, validated.column.id, oldName, validated.changes.name);
  } else if (validated.type === "delete_element" && validated.elementType === "table") {
    schema.tables = schema.tables.filter(table => table.id !== validated.table.id);
    schema.relationships = schema.relationships.filter(relation => relation.fromTableId !== validated.table.id && relation.toTableId !== validated.table.id);
  } else if (validated.type === "delete_element") {
    const dependent = findColumnDependentObjects(validated.table, validated.column.id);
    for (const { kind, item } of dependent) {
      const key = tableDatabaseObjectKey(kind);
      validated.table[key] = (validated.table[key] ?? []).filter(candidate => candidate.id !== item.id);
    }
    validated.table.columns = validated.table.columns.filter(column => column.id !== validated.column.id);
    validated.table.uniqueConstraints = (validated.table.uniqueConstraints ?? []).filter(constraint => !constraint.columnIds.includes(validated.column.id));
    schema.relationships = schema.relationships.filter(relation => !relationshipIncludesColumn(relation, validated.column.id));
  } else if (validated.type === "add_relationship") {
    schema.relationships.push({
      id: uid("rel"), fromTableId: validated.fromTable.id, fromColumnId: validated.fromColumn.id,
      toTableId: validated.toTable.id, toColumnId: validated.toColumn.id,
      constraintName: validated.payload.constraintName || validated.payload.name || `${validated.fromTable.name}_${validated.fromColumn.name}_fkey`,
      targetNamespace: validated.toTable.namespace || schema.postgres?.namespace,
      targetTableName: validated.toTable.name, targetColumnNames: [validated.toColumn.name],
      onUpdate: validated.payload.onUpdate || "NO ACTION", onDelete: validated.payload.onDelete || "NO ACTION",
      deferrable: false, initiallyDeferred: false, matchType: "SIMPLE", validated: true
    });
  }
  for (const table of schema.tables) {
    const primaryColumnIds = table.columns.filter(column => column.primary).map(column => column.id);
    table.primaryKey = primaryColumnIds.length ? { id: table.primaryKey?.id ?? uid("pk"), ...table.primaryKey, name: table.primaryKey?.name || availablePrimaryKeyName(table.name, table.id), columnIds: primaryColumnIds } : null;
  }
  selectedTableId = null;
  selectedTableIds = new Set();
  render();
  elements.saveStatus.textContent = "Saving...";
  try {
    await persistCurrentSchema();
    elements.saveStatus.textContent = "Saved to file";
  } catch (error) {
    schema = schemaBefore;
    render();
    reportSaveError(error);
    throw error;
  }
}

const AI_MODEL_STORAGE_KEY = "schemii.ai.lastModel";

function normalizeStoredAiModel(value) {
  if (typeof value !== "string" || !value || value.length > 1024) return "";
  try {
    const model = JSON.parse(value);
    if (!model || typeof model !== "object" || Array.isArray(model) || Object.keys(model).sort().join(",") !== "modelId,providerId") return "";
    if (typeof model.providerId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(model.providerId)) return "";
    if (typeof model.modelId !== "string" || !model.modelId || model.modelId !== model.modelId.trim() || model.modelId.length > 256 || /[\x00-\x1f\x7f]/.test(model.modelId)) return "";
    return JSON.stringify({ providerId: model.providerId, modelId: model.modelId });
  } catch {
    return "";
  }
}

function storedAiModel() {
  try { return normalizeStoredAiModel(localStorage.getItem(AI_MODEL_STORAGE_KEY)); } catch { return ""; }
}

function rememberAiModel(value) {
  const normalized = normalizeStoredAiModel(value);
  if (!normalized) return;
  try { localStorage.setItem(AI_MODEL_STORAGE_KEY, normalized); } catch { /* Model preference persistence is optional. */ }
}

function setAiPanelOpen(open) {
  elements.mainLayout.classList.toggle("ai-open", open);
  elements.toolRail.inert = open;
  elements.toolRail.setAttribute("aria-hidden", String(open));
  elements.aiPanel.classList.toggle("open", open);
  elements.aiPanel.setAttribute("aria-hidden", String(!open));
  elements.aiButton.classList.toggle("active", open);
  elements.aiButton.setAttribute("aria-expanded", String(open));
  if (open) {
    loadAiStatus();
    requestAnimationFrame(() => elements.aiInput.focus());
  }
}

function setAiBusy(busy) {
  aiState.busy = busy;
  elements.aiSendButton.disabled = busy || !aiState.available || !elements.aiModelSelect.value;
  elements.aiInput.disabled = busy || !aiState.available || !elements.aiModelSelect.value;
  elements.aiNewChat.disabled = busy;
  elements.aiHistoryButton.disabled = busy;
  elements.aiModelSelect.disabled = busy || !elements.aiModelSelect.value;
  elements.aiAccessSelect.disabled = busy;
  elements.aiSqlPolicy.disabled = busy;
  elements.aiSendButton.textContent = busy ? "Working..." : "Send";
}

function renderAiModels() {
  const previous = normalizeStoredAiModel(elements.aiModelSelect.value) || storedAiModel();
  elements.aiModelSelect.replaceChildren();
  for (const provider of aiState.providers.filter(item => item.connected && item.models?.length)) {
    const group = document.createElement("optgroup");
    group.label = provider.name;
    for (const model of provider.models) {
      const option = document.createElement("option");
      option.value = JSON.stringify({ providerId: provider.id, modelId: model.id });
      option.textContent = model.name;
      group.append(option);
    }
    elements.aiModelSelect.append(group);
  }
  if (previous && [...elements.aiModelSelect.options].some(option => option.value === previous)) elements.aiModelSelect.value = previous;
  elements.aiModelSelect.disabled = !elements.aiModelSelect.options.length;
  if (!elements.aiModelSelect.options.length) {
    const option = document.createElement("option");
    option.textContent = "Connect a provider in settings";
    option.value = "";
    elements.aiModelSelect.append(option);
  }
  const hasModel = Boolean(elements.aiModelSelect.value);
  elements.aiInput.placeholder = hasModel ? "Ask about this schema..." : "Connect a provider in settings to start chatting";
  if (elements.aiEmptyCopy) {
    elements.aiEmptyCopy.textContent = hasModel
      ? "Ask about the active design. Proposed changes and database operations always wait for your confirmation."
      : "Connect OpenAI, GitHub Copilot, GitLab, or an API-key provider in settings to start chatting.";
  }
  setAiBusy(aiState.busy);
}

function aiAuthMethods(providerId) {
  const methods = aiState.authMethods?.[providerId] ?? [];
  if (Array.isArray(methods)) return methods.map(method => typeof method === "string" ? { id: method, name: method } : method);
  return Object.entries(methods).map(([id, method]) => typeof method === "string" ? { id, name: method } : { id, ...method });
}

function renderAiProviders() {
  elements.aiProviders.replaceChildren();
  for (const provider of aiState.providers) {
    const card = document.createElement("article");
    card.className = "ai-provider-card";
    const heading = document.createElement("div");
    heading.className = "ai-provider-heading";
    const name = document.createElement("strong");
    name.textContent = provider.name;
    const indicator = document.createElement("span");
    indicator.className = provider.connected ? "connected" : "";
    const anonymousFreeAccess = provider.connected && provider.authenticated === false;
    indicator.textContent = anonymousFreeAccess ? "Free access" : provider.connected ? "Connected" : "Not connected";
    heading.append(name, indicator);
    card.append(heading);
    if (provider.connected && !anonymousFreeAccess) {
      const disconnect = document.createElement("button");
      disconnect.type = "button";
      disconnect.className = "button button-ghost";
      disconnect.textContent = "Disconnect";
      disconnect.addEventListener("click", async () => {
        if (!confirm(`Disconnect ${provider.name}?`)) return;
        await aiRequest(`/api/ai/auth/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
        await loadAiStatus(true);
      });
      card.append(disconnect);
    } else {
      const methods = aiAuthMethods(provider.id);
      for (const method of methods) card.append(buildAiAuthForm(provider, method));
      if (!methods.length) {
        const note = document.createElement("p");
        note.textContent = "This provider did not advertise a supported authentication method.";
        card.append(note);
      }
    }
    elements.aiProviders.append(card);
  }
}

function buildAiAuthForm(provider, method) {
  const form = document.createElement("form");
  form.className = "ai-auth-form";
  const methodId = Number(method.id);
  const apiKeyMethod = method.type === "api" || /api.?key/i.test(method.name ?? method.label ?? "");
  const label = document.createElement("strong");
  label.textContent = method.name ?? (apiKeyMethod ? "API key" : "OAuth");
  form.append(label);
  if (method.helpUrl) {
    try {
      const helpUrl = new URL(method.helpUrl);
      if (["http:", "https:"].includes(helpUrl.protocol)) {
        const help = document.createElement("a");
        help.className = "ai-auth-help";
        help.href = helpUrl.href;
        help.target = "_blank";
        help.rel = "noopener noreferrer";
        help.textContent = method.helpLabel || "Create provider key";
        form.append(help);
      }
    } catch { /* Ignore invalid provider help links. */ }
  }
  const appendProviderInputs = () => {
    for (const inputDefinition of method.inputs ?? method.prompts ?? []) {
      const inputName = inputDefinition.id ?? inputDefinition.key ?? inputDefinition.name;
      let input;
      if (inputDefinition.type === "select") {
        input = document.createElement("select");
        for (const item of inputDefinition.options ?? []) {
          const option = document.createElement("option");
          option.value = item.value;
          option.textContent = item.label || item.value;
          input.append(option);
        }
      } else {
        input = document.createElement("input");
        input.placeholder = inputDefinition.label ?? inputDefinition.message ?? inputName;
        input.autocomplete = "off";
      }
      input.name = inputName;
      input.required = inputDefinition.required !== false;
      form.append(input);
    }
  };
  if (apiKeyMethod) {
    const input = document.createElement("input");
    input.type = "password";
    input.name = "key";
    input.autocomplete = "off";
    input.placeholder = "API key";
    input.required = true;
    form.append(input);
    appendProviderInputs();
  } else {
    appendProviderInputs();
  }
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "button button-primary";
  submit.textContent = apiKeyMethod ? "Connect" : "Start authorization";
  form.append(submit);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    try {
      if (apiKeyMethod) {
        const keyInput = form.elements.key;
        const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name && name !== "key"));
        await aiRequest("/api/ai/auth/api", { method: "POST", body: JSON.stringify({ providerId: provider.id, key: keyInput.value, inputs }) });
        keyInput.value = "";
        await loadAiStatus(true);
      } else {
        const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name));
        const authorization = await aiRequest("/api/ai/auth/oauth/authorize", { method: "POST", body: JSON.stringify({ providerId: provider.id, method: methodId, inputs }) });
        aiState.oauth = { providerId: provider.id, method: methodId, flow: authorization.method };
        renderAiOauthCompletion(authorization);
        if (authorization.url) {
          try {
            const url = new URL(authorization.url);
            if (["http:", "https:"].includes(url.protocol)) window.open(url.href, "_blank", "noopener,noreferrer");
          } catch { /* Instructions and callback completion remain available without a valid link. */ }
        }
      }
    } catch (error) {
      elements.aiSettingsStatus.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

function renderAiOauthCompletion(authorization) {
  const box = document.createElement("form");
  box.className = "ai-oauth-completion";
  const instructions = document.createElement("p");
  instructions.textContent = authorization.instructions || "Complete authorization in the opened page, then enter the returned code if requested.";
  const link = document.createElement("a");
  link.textContent = "Open authorization page";
  const authorizationUrl = (() => {
    try {
      const parsed = new URL(authorization.url);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
    } catch { return ""; }
  })();
  link.href = authorizationUrl || "#";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const code = document.createElement("input");
  code.name = "code";
  code.autocomplete = "off";
  code.placeholder = "Callback code (if provided)";
  const finish = document.createElement("button");
  finish.className = "button button-primary";
  finish.type = "submit";
  finish.textContent = "Complete connection";
  box.append(instructions);
  if (authorizationUrl) box.append(link);
  box.append(code, finish);
  box.addEventListener("submit", async event => {
    event.preventDefault();
    const oauth = aiState.oauth;
    if (!oauth) return;
    try {
      await aiRequest("/api/ai/auth/oauth/callback", { method: "POST", body: JSON.stringify({ providerId: oauth.providerId, method: oauth.method, ...(code.value.trim() ? { code: code.value.trim() } : {}) }) });
      code.value = "";
      aiState.oauth = null;
      await loadAiStatus(true);
    } catch (error) {
      elements.aiSettingsStatus.textContent = error.message;
    }
  });
  elements.aiSettingsStatus.replaceChildren(box);
}

async function loadAiStatus(renderSettings = false) {
  elements.aiStatusPill.textContent = "Checking";
  try {
    const status = await aiRequest("/api/ai/status", { method: "GET" });
    Object.assign(aiState, { loaded: true, available: status.available === true || status.healthy === true, version: status.version ?? "", providers: status.providers ?? [], authMethods: status.authMethods ?? {}, skills: status.skills ?? [] });
    const connected = aiState.providers.filter(provider => provider.connected).length;
    elements.aiStatusPill.textContent = aiState.available ? `${connected} connected` : "Unavailable";
    elements.aiStatusPill.classList.toggle("available", aiState.available);
    elements.aiRailStatus.classList.toggle("available", aiState.available);
    elements.aiSettingsStatus.textContent = aiState.available ? `OpenCode ${aiState.version || "available"}` : "OpenCode is unavailable. Schema design remains fully usable without AI.";
    renderAiModels();
    if (renderSettings || elements.aiSettingsDialog.open) renderAiProviders();
  } catch (error) {
    Object.assign(aiState, { loaded: true, available: false, providers: [] });
    elements.aiStatusPill.textContent = "Offline";
    elements.aiSettingsStatus.textContent = `AI unavailable: ${error.message}`;
    renderAiModels();
    if (renderSettings || elements.aiSettingsDialog.open) renderAiProviders();
  }
}

function appendAiMessage(role, text) {
  elements.aiMessages.querySelector(".ai-empty-state")?.remove();
  const message = document.createElement("article");
  message.className = `ai-message ${role}`;
  const label = document.createElement("span");
  label.textContent = role === "assistant" ? "Assistant" : role === "tool" ? "Query result" : "You";
  const body = document.createElement("p");
  body.textContent = String(text ?? "");
  message.append(label, body);
  elements.aiMessages.append(message);
  elements.aiMessages.scrollTop = elements.aiMessages.scrollHeight;
  return message;
}

function formatAiDuration(milliseconds) {
  const seconds = Math.max(0, milliseconds) / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
}

function beginAiActivity(modelName) {
  elements.aiMessages.querySelector(".ai-empty-state")?.remove();
  const startedAt = performance.now();
  const details = document.createElement("details");
  details.className = "ai-run active";
  details.open = true;
  details.setAttribute("role", "status");
  const summary = document.createElement("summary");
  const indicator = document.createElement("span");
  indicator.className = "ai-progress-grid";
  indicator.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 25; index += 1) {
    const dot = document.createElement("i");
    dot.style.setProperty("--dot-index", index);
    indicator.append(dot);
  }
  const title = document.createElement("span");
  title.className = "ai-run-title shimmer";
  title.textContent = "Starting assistant";
  const elapsed = document.createElement("time");
  elapsed.className = "ai-run-time";
  elapsed.textContent = "0.0s";
  summary.append(indicator, title, elapsed);
  const steps = document.createElement("div");
  steps.className = "ai-run-steps";
  details.append(summary, steps);
  elements.aiMessages.append(details);
  elements.aiMessages.scrollTop = elements.aiMessages.scrollHeight;

  const stageElements = new Map();
  let retryAt = null;
  let finished = false;
  const setStage = (key, label, state = "running") => {
    const safeState = ["running", "completed", "error"].includes(state) ? state : "running";
    let row = stageElements.get(key);
    if (!row) {
      row = document.createElement("div");
      row.className = "ai-run-step";
      const marker = document.createElement("span");
      marker.className = "ai-run-step-marker";
      marker.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "ai-run-step-copy";
      row.append(marker, copy);
      steps.append(row);
      stageElements.set(key, row);
    }
    row.className = `ai-run-step ${safeState}`;
    row.querySelector(".ai-run-step-copy").textContent = label;
    return row;
  };
  setStage("request", `Opening ${modelName || "selected model"}`);

  const tick = () => {
    elapsed.textContent = formatAiDuration(performance.now() - startedAt);
    if (retryAt) {
      const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      title.textContent = `Retrying in ${remaining}s`;
    }
  };
  const timer = setInterval(tick, 100);

  return {
    details,
    update(event) {
      if (finished || !event || typeof event !== "object") return;
      if (event.type === "connection") {
        if (event.state === "connected") {
          title.textContent = "Waiting for model";
          setStage("request", `Connected to ${modelName || "selected model"}`, "completed");
        } else {
          title.textContent = "Working without live updates";
          setStage("stream", "Live activity disconnected", "error");
        }
      } else if (event.type === "session" && event.state === "busy") {
        retryAt = null;
        title.textContent = "Agent is working";
        setStage("model", "Model started", "running");
      } else if (event.type === "session" && event.state === "retry") {
        retryAt = Number.isFinite(event.retryAt) ? event.retryAt : null;
        title.textContent = "Retrying provider";
        setStage("retry", `Provider retry ${Number.isInteger(event.attempt) ? event.attempt : ""}`.trim(), "running");
      } else if (event.type === "session" && event.state === "error") {
        title.textContent = "Provider reported an issue";
        setStage("provider-error", "Provider issue detected", "error");
      } else if (event.type === "session" && event.state === "idle") {
        retryAt = null;
        title.textContent = "Finalizing response";
        setStage("model", "Model finished", "completed");
      } else if (event.type === "compaction") {
        title.textContent = "Compacting context";
        setStage("compaction", "Context compacted", event.state === "completed" ? "completed" : "running");
      } else if (event.type === "part" && event.kind === "reasoning") {
        title.textContent = event.state === "completed" ? "Preparing response" : "Reasoning";
        setStage(event.key, "Reasoning", event.state);
      } else if (event.type === "part" && event.kind === "text") {
        title.textContent = "Writing response";
        setStage(event.key, "Writing response", event.state);
      } else if (event.type === "part" && event.kind === "tool" && AI_TOOL_LABELS[event.tool]) {
        title.textContent = AI_TOOL_LABELS[event.tool];
        setStage(event.key, AI_TOOL_LABELS[event.tool], event.state);
      } else if (event.type === "part" && event.kind === "skill" && AI_SKILL_LABELS[event.skill]) {
        title.textContent = `Loading ${AI_SKILL_LABELS[event.skill]}`;
        setStage(event.key, AI_SKILL_LABELS[event.skill], event.state);
      }
      elements.aiMessages.scrollTop = elements.aiMessages.scrollHeight;
    },
    finish(outcome) {
      if (finished) return;
      clearInterval(timer);
      retryAt = null;
      tick();
      finished = true;
      const failed = outcome === "error";
      details.classList.remove("active");
      details.classList.add(failed ? "failed" : "completed");
      title.classList.remove("shimmer");
      title.textContent = failed ? "Agent stopped" : "Completed";
      setStage("model", failed ? "Response failed" : "Model finished", failed ? "error" : "completed");
      if (!failed) setStage("delivered", "Response delivered", "completed");
      if (!failed) setTimeout(() => { details.open = false; }, 650);
    }
  };
}

function startAiActivityStream(sessionId, activity) {
  const controller = new AbortController();
  let resolveReady;
  let readyResolved = false;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const markReady = () => {
    if (readyResolved) return;
    readyResolved = true;
    resolveReady();
  };
  const done = readAiActivity(sessionId, event => {
    if (event?.type === "connection") markReady();
    activity.update(event);
  }, controller.signal).catch(error => {
    markReady();
    if (error.name !== "AbortError") activity.update({ type: "connection", state: "disconnected" });
  }).finally(markReady);
  return { ready, done, abort: () => controller.abort() };
}

function renderAiReasoning(part) {
  if (!part.text) return;
  const details = document.createElement("details");
  details.className = "ai-reasoning";
  const summary = document.createElement("summary");
  summary.textContent = `Thought${Number.isFinite(part.durationMs) ? ` / ${formatAiDuration(part.durationMs)}` : ""}`;
  const body = document.createElement("p");
  body.textContent = part.text;
  details.append(summary, body);
  elements.aiMessages.append(details);
}

function renderAiToolPart(part) {
  const label = part.type === "skill" ? AI_SKILL_LABELS[part.skill] : AI_TOOL_LABELS[part.tool];
  if (!label) return;
  const safeStatus = ["pending", "running", "completed", "error"].includes(part.status) ? part.status : "completed";
  const card = document.createElement("div");
  card.className = `ai-tool-part ${safeStatus}`;
  const marker = document.createElement("span");
  marker.className = "ai-tool-marker";
  marker.setAttribute("aria-hidden", "true");
  const name = document.createElement("strong");
  name.textContent = label;
  const status = document.createElement("span");
  status.textContent = safeStatus;
  card.append(marker, name, status);
  elements.aiMessages.append(card);
}

function renderAiResponse(response, context) {
  let renderedText = false;
  for (const part of response.parts ?? []) {
    if (part?.type === "text" && part.text) {
      appendAiMessage("assistant", part.text);
      renderedText = true;
    } else if (part?.type === "reasoning") {
      renderAiReasoning(part);
    } else if (part?.type === "tool" || part?.type === "skill") {
      renderAiToolPart(part);
    }
  }
  if (!renderedText && response.text) appendAiMessage("assistant", response.text);
  for (const action of response.actions ?? []) renderAiAction(action, context);
  elements.aiMessages.scrollTop = elements.aiMessages.scrollHeight;
}

function aiActionSummary(action) {
  const type = aiActionType(action);
  const payload = aiActionPayload(action);
  if (type === "schema_read_query") return "Read-only SQL query";
  if (type === "populate_schema") return "Populate the active schema";
  if (type === "connection_setup") return "Set up a PostgreSQL connection";
  if (type === "create_project") return "Create a local project";
  if (type === "open_project") return "Open a local project";
  if (type === "open_connection") return "Open a saved PostgreSQL connection";
  if (type === "migration_preview") return "Preview migration";
  if (type === "migration_apply") return "Review migration for apply";
  return String(action.title ?? payload.title ?? type.replaceAll("_", " ") ?? "Proposed action");
}

function renderAiAction(action, context) {
  const card = document.createElement("section");
  card.className = "ai-action-card";
  const title = document.createElement("strong");
  title.textContent = aiActionSummary(action);
  const detail = document.createElement("p");
  detail.textContent = String(action.description ?? aiActionPayload(action).description ?? "Review this action before continuing.");
  card.append(title, detail);
  const type = aiActionType(action);
  if (type === "schema_read_query") {
    const sql = document.createElement("pre");
    sql.textContent = String(aiActionPayload(action).sql ?? "");
    card.append(sql);
  } else {
    const payload = aiActionPayload(action);
    const review = document.createElement("pre");
    review.className = "ai-action-review";
    review.textContent = JSON.stringify(payload, null, 2);
    card.append(review);
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = AI_SCHEMA_ACTIONS.has(type) || AI_NAVIGATION_ACTIONS.has(type) || ["connection_setup", "migration_preview", "migration_apply"].includes(type) ? "button button-primary" : "button button-ghost";
  button.textContent = type === "schema_read_query" ? "Run query" : "Review & confirm";
  if (type === "schema_read_query" && elements.aiSqlPolicy.value === "disabled") {
    button.disabled = true;
    detail.textContent = "Rejected: SQL policy is disabled. The generated SQL is shown for review.";
  }
  button.addEventListener("click", () => confirmAiAction(action, context, card, button));
  card.append(button);
  elements.aiMessages.append(card);
  if (type === "schema_read_query" && elements.aiSqlPolicy.value === "allow-session" && aiState.sqlPolicyDeliberatelySelected) {
    button.disabled = true;
    executeAiReadQuery(action, context, card, button);
  }
}

async function confirmAiAction(action, context, card, button) {
  card.querySelectorAll(".ai-action-error").forEach(error => error.remove());
  const type = aiActionType(action);
  if (type === "schema_read_query") {
    if (elements.aiSqlPolicy.value === "disabled") return;
    if (elements.aiSqlPolicy.value === "ask" && !confirm("Run this generated read-only SQL query? PostgreSQL functions can still have side effects outside the database.")) return;
    return executeAiReadQuery(action, context, card, button);
  }
  if (AI_NAVIGATION_ACTIONS.has(type)) return confirmAiNavigationAction(action, context, card, button);
  if (!confirm(`Confirm action: ${aiActionSummary(action)}?`)) return;
  if (activeSchemaId !== context.schemaId) return showToast("The active design changed. Ask the assistant for a fresh proposal");
  if (AI_SCHEMA_ACTIONS.has(type)) {
    if (JSON.stringify(schema) !== context.schemaSnapshot) return showToast("The design changed. Ask the assistant for a fresh proposal");
    try {
      await applyAiSchemaAction(action);
      button.disabled = true;
      button.textContent = "Applied";
      showToast("Confirmed AI schema proposal applied");
    } catch (error) {
      detailAiActionError(card, error.message);
    }
    return;
  }
  if (type === "connection_setup") {
    const profile = aiActionPayload(action).profile ?? aiActionPayload(action);
    openPostgresProfileEditor();
    elements.postgresProfileName.value = profile.name ?? "";
    elements.postgresProfileHost.value = profile.host ?? "127.0.0.1";
    elements.postgresProfilePort.value = profile.port ?? 5432;
    elements.postgresProfileDatabase.value = profile.dbname ?? profile.database ?? "";
    elements.postgresProfileUser.value = profile.user ?? "";
    elements.postgresProfilePassword.value = "";
    if ([...elements.postgresProfileSslmode.options].some(option => option.value === profile.sslmode)) elements.postgresProfileSslmode.value = profile.sslmode;
    return;
  }
  if (["migration_preview", "migration_apply"].includes(type)) {
    if (!context.profileId || !context.namespace || postgresState.selectedProfileId !== context.profileId || postgresState.namespace !== context.namespace) return showToast("Select the original PostgreSQL profile and namespace before previewing");
    await previewPostgresMigration();
    return;
  }
  detailAiActionError(card, "This action type is not supported by the frontend");
}

async function confirmAiNavigationAction(action, context, card, button) {
  const validated = validateAiNavigationAction(action);
  if (!validated.ok) return detailAiActionError(card, validated.error);
  if (activeSchemaId !== context.schemaId) return showToast("The active design changed. Ask the assistant for a fresh proposal");
  if (validated.type === "create_project") {
    if (!confirm(`Create and open local project “${validated.projectName}”? Pending changes to the current project will be saved first.`)) return;
    if (await createSchemaProject(validated.projectName)) {
      button.disabled = true;
      button.textContent = "Created";
    }
    return;
  }
  if (validated.type === "open_project") {
    const name = validated.record.schema.projectName || "Untitled schema";
    if (!confirm(`Open local project “${name}”? Pending changes to the current project will be saved first.`)) return;
    if (await openSchema(validated.record.id, { fit: false })) {
      button.disabled = true;
      button.textContent = "Opened";
    }
    return;
  }
  try {
    const payload = await postgresRequest("/api/postgres/profiles", { method: "GET" });
    const profiles = payload.profiles ?? [];
    const profile = profiles.find(item => item.id === validated.payload.profileId);
    if (!profile || profile.name !== validated.payload.name || profile.dbname !== validated.payload.database) return detailAiActionError(card, "The saved connection identity changed. Ask for a fresh proposal.");
    if (!confirm(`Open saved connection “${profile.name}” for database “${profile.dbname}”? Schemii will contact PostgreSQL using the credentials already stored on this server.`)) return;
    postgresState.profiles = profiles;
    postgresState.selectedProfileId = profile.id;
    postgresState.namespace = "";
    renderPostgresProfiles();
    if (!elements.postgresDialog.open) elements.postgresDialog.showModal();
    renderPostgresCatalogSummary();
    const connected = await loadPostgresNamespaces();
    if (!connected) return detailAiActionError(card, "The saved PostgreSQL connection could not be opened.");
    const requestedNamespace = validated.payload.namespace;
    if (requestedNamespace && postgresState.namespaces.includes(requestedNamespace)) {
      postgresState.namespace = requestedNamespace;
      renderNamespaceOptions();
    } else if (requestedNamespace) {
      showToast(`Connected, but namespace “${requestedNamespace}” was not found`);
    }
    button.disabled = true;
    button.textContent = "Opened";
  } catch (error) {
    detailAiActionError(card, error.message);
  }
}

function detailAiActionError(card, message) {
  const error = document.createElement("p");
  error.className = "ai-action-error";
  error.textContent = message;
  card.append(error);
}

function boundedAiQueryResult(result) {
  const columns = (result.columns ?? []).slice(0, 50).map(column => column.name ?? String(column));
  const rows = (result.rows ?? []).slice(0, 50).map(row => Array.isArray(row) ? row.slice(0, 50) : row);
  const serialized = JSON.stringify({ columns, rows, truncated: Boolean(result.truncated || (result.rows?.length ?? 0) > rows.length) });
  return serialized.length > 24000 ? `${serialized.slice(0, 24000)}…` : serialized;
}

async function executeAiReadQuery(action, context, card, button) {
  const sql = String(aiActionPayload(action).sql ?? "").trim();
  if (!sql) return detailAiActionError(card, "No SQL was supplied");
  if (elements.aiSqlPolicy.value === "disabled") return detailAiActionError(card, "SQL policy is disabled");
  if (!context.profileId || !context.namespace || postgresState.selectedProfileId !== context.profileId || postgresState.namespace !== context.namespace) return detailAiActionError(card, "The selected PostgreSQL profile or namespace changed");
  button.disabled = true;
  button.textContent = "Running...";
  try {
    const result = await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(context.profileId)}/sql`, { method: "POST", body: JSON.stringify({ namespace: context.namespace, sql }) });
    const text = `Tool result for SQL:\n${sql}\n${boundedAiQueryResult(result)}`;
    appendAiMessage("tool", text);
    button.textContent = "Ran query";
    await sendAiMessage(text, "tool");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Run query";
    detailAiActionError(card, error.message);
  }
}

async function ensureAiSession(model) {
  if (aiState.sessionId) return aiState.sessionId;
  const session = await aiRequest("/api/ai/sessions", { method: "POST", body: JSON.stringify({ title: schema.projectName || "Schemii chat", model }) });
  aiState.sessionId = session.id;
  return session.id;
}

async function sendAiMessage(text, renderedRole = "user") {
  if (!text.trim() || aiState.busy) return;
  let model;
  try {
    model = JSON.parse(elements.aiModelSelect.value);
  } catch {
    return showToast("Connect and select an AI model first");
  }
  const modelName = elements.aiModelSelect.selectedOptions[0]?.textContent || model.modelId || "selected model";
  const requestGeneration = ++aiState.requestGeneration;
  if (renderedRole === "user") appendAiMessage("user", text);
  const linkedProfileId = schema.postgres?.sourceProfileId;
  const linkedNamespace = schema.postgres?.namespace;
  const selectedProfileId = postgresState.selectedProfileId || linkedProfileId;
  const selectedNamespace = postgresState.selectedProfileId ? postgresState.namespace : linkedNamespace;
  const context = {
    schemaId: activeSchemaId,
    schemaSnapshot: JSON.stringify(schema),
    profileId: selectedProfileId || undefined,
    namespace: selectedNamespace || undefined
  };
  const activity = beginAiActivity(modelName);
  let activityStream = null;
  setAiBusy(true);
  try {
    const sessionId = await ensureAiSession(model);
    if (requestGeneration !== aiState.requestGeneration) return;
    activityStream = startAiActivityStream(sessionId, activity);
    await Promise.race([
      activityStream.ready,
      new Promise(resolve => setTimeout(resolve, 1500))
    ]);
    const response = await aiRequest(`/api/ai/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        text, model, schemaId: activeSchemaId, accessLevel: elements.aiAccessSelect.value,
        ...(context.profileId ? { profileId: context.profileId } : {}),
        ...(context.profileId && context.namespace ? { namespace: context.namespace } : {})
      })
    });
    if (requestGeneration !== aiState.requestGeneration) return;
    if (activityStream) {
      await Promise.race([
        activityStream.done,
        new Promise(resolve => setTimeout(resolve, 750))
      ]);
    }
    renderAiResponse(response, context);
    activity.finish("completed");
  } catch (error) {
    if (requestGeneration === aiState.requestGeneration) {
      activity.finish("error");
      appendAiMessage("assistant", `AI unavailable: ${error.message}`);
    }
  } finally {
    activityStream?.abort();
    if (requestGeneration === aiState.requestGeneration) setAiBusy(false);
  }
}

async function startNewAiChat() {
  if (aiState.busy) return showToast("Wait for the current response to finish");
  aiState.requestGeneration += 1;
  aiState.sessionId = null;
  aiState.sqlPolicyDeliberatelySelected = false;
  elements.aiSqlPolicy.value = "disabled";
  elements.aiMessages.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "ai-empty-state";
  const title = document.createElement("strong");
  title.textContent = "New conversation";
  const copy = document.createElement("p");
  copy.textContent = "Proposals will use the currently active design.";
  empty.append(title, copy);
  elements.aiMessages.append(empty);
}

function formatAiHistoryDate(value) {
  if (!Number.isFinite(value)) return "Saved conversation";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Saved conversation" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function renderAiHistory(sessions) {
  elements.aiHistoryList.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "ai-history-empty";
    empty.textContent = "No saved conversations yet.";
    elements.aiHistoryList.append(empty);
    return;
  }
  for (const session of sessions) {
    const item = document.createElement("article");
    item.className = `ai-history-item${session.id === aiState.sessionId ? " current" : ""}`;
    const copy = document.createElement("div");
    copy.className = "ai-history-copy";
    const title = document.createElement("strong");
    title.textContent = session.title || "Untitled chat";
    const date = document.createElement("span");
    date.textContent = `${formatAiHistoryDate(session.updatedAt ?? session.createdAt)}${session.id === aiState.sessionId ? " / Current" : ""}`;
    copy.append(title, date);
    const open = document.createElement("button");
    open.type = "button";
    open.className = "button button-ghost";
    open.textContent = session.id === aiState.sessionId ? "Reopen" : "Open";
    open.addEventListener("click", () => restoreAiSession(session.id));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-ghost ai-history-delete";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteAiHistorySession(session.id, session.title));
    item.append(copy, open, remove);
    elements.aiHistoryList.append(item);
  }
}

async function openAiHistory() {
  if (aiState.busy) return showToast("Wait for the current response to finish");
  elements.aiHistoryList.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "ai-history-empty";
  loading.textContent = "Loading conversations...";
  elements.aiHistoryList.append(loading);
  elements.aiHistoryDialog.showModal();
  try {
    const history = await aiRequest("/api/ai/sessions", { method: "GET" });
    renderAiHistory(history.sessions ?? []);
  } catch (error) {
    loading.textContent = `Could not load chat history: ${error.message}`;
  }
}

async function restoreAiSession(sessionId) {
  if (aiState.busy) return;
  try {
    const history = await aiRequest(`/api/ai/sessions/${encodeURIComponent(sessionId)}/messages`, { method: "GET" });
    aiState.requestGeneration += 1;
    aiState.sessionId = sessionId;
    aiState.sqlPolicyDeliberatelySelected = false;
    elements.aiSqlPolicy.value = "disabled";
    elements.aiMessages.replaceChildren();
    for (const message of history.messages ?? []) {
      if (message.role === "user") appendAiMessage("user", message.text);
      if (message.role === "assistant") renderAiResponse({ parts: message.parts ?? [], text: message.text ?? "", actions: [] }, null);
    }
    if (!elements.aiMessages.children.length) appendAiMessage("assistant", "This saved conversation has no displayable messages.");
    const modelValue = normalizeStoredAiModel(JSON.stringify(history.model ?? {}));
    if (modelValue && [...elements.aiModelSelect.options].some(option => option.value === modelValue)) {
      elements.aiModelSelect.value = modelValue;
      rememberAiModel(modelValue);
    }
    setAiBusy(false);
    elements.aiHistoryDialog.close();
    setAiPanelOpen(true);
    elements.aiMessages.scrollTop = elements.aiMessages.scrollHeight;
    elements.aiInput.focus();
  } catch (error) {
    showToast(`Could not open chat: ${error.message}`);
  }
}

async function deleteAiHistorySession(sessionId, title) {
  if (!confirm(`Permanently delete chat “${title || "Untitled chat"}”?`)) return;
  try {
    await aiRequest(`/api/ai/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    if (aiState.sessionId === sessionId) await startNewAiChat();
    const history = await aiRequest("/api/ai/sessions", { method: "GET" });
    renderAiHistory(history.sessions ?? []);
  } catch (error) {
    showToast(`Could not delete chat: ${error.message}`);
  }
}

function updateAiAccessDisclosure() {
  const access = elements.aiAccessSelect.value;
  elements.aiSqlPolicyWrap.hidden = access !== "data";
  elements.aiFunctionCaveat.hidden = access !== "data";
  elements.aiAccessDisclosure.textContent = access === "metadata"
    ? "Active design metadata plus bounded local project and redacted connection identities are sent to the selected external AI provider."
    : access === "schema"
      ? "The active schema definition is disclosed to the selected external AI provider; database rows are not included."
      : "Schema context and explicitly approved query results may be disclosed to the selected external AI provider. Queries use the selected UI profile only.";
}

elements.tablesLayer.addEventListener("pointerdown", event => {
  if (wheelZoomTimer !== null) finishWheelZoom();
  const card = event.target.closest(".table-card");
  if (!card) return;
  const tableId = card.dataset.tableId;
  const column = event.target.closest(".table-column");
  if (relationMode && column) {
    event.preventDefault();
    handleRelationColumn(tableId, column.dataset.columnId);
    return;
  }
  if (event.button !== 0) return;
  const additiveSelection = isAdditiveTableSelection(event);
  if (!additiveSelection) prepareInspectorTileForTablePress();
  setTableDataPanelExpanded(false);
  tablePressState = {
    pointerId: event.pointerId,
    tableId,
    startX: event.clientX,
    startY: event.clientY,
    moved: false,
    additive: additiveSelection
  };
  card.setPointerCapture(event.pointerId);
  selectTable(tableId, additiveSelection, false);
  if (!additiveSelection && selectedTableIds.has(tableId)) {
    updateInspectorHeaderToggle();
    openInspectorPane();
  }
  if (!selectedTableIds.has(tableId)) return;
  if (!event.target.closest(".table-header") || event.button !== 0) return;
  const tablePositions = [...selectedTableIds].map(selectedId => {
    const table = getTable(selectedId);
    return { tableId: selectedId, x: table.x, y: table.y, card: document.querySelector(`[data-table-id="${selectedId}"]`) };
  });
  dragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    tablePositions,
    deltaX: 0,
    deltaY: 0,
    historyRecorded: false
  };
  card.setPointerCapture(event.pointerId);
});

elements.tablesLayer.addEventListener("contextmenu", event => {
  const icon = event.target.closest("[data-object-icon]");
  const row = icon?.closest(".table-column");
  const card = icon?.closest(".table-card");
  if (!icon || !row || !card) return;
  const table = getTable(card.dataset.tableId);
  const column = getColumn(card.dataset.tableId, row.dataset.columnId);
  if (!table || !column) return;
  const targets = columnDatabaseIconTargets(icon.dataset.objectIcon, table, column);
  if (!targets.length) return;
  event.preventDefault();
  event.stopPropagation();
  hideTooltip();
  if (targets.length === 1) openDatabaseIconTarget(targets[0]);
  else openObjectIconMenu(targets, event.clientX, event.clientY);
});

elements.connections.addEventListener("contextmenu", event => {
  const connection = event.target.closest("[data-relationship-id]");
  if (!connection) return;
  const relationship = schema.relationships.find(item => item.id === connection.dataset.relationshipId);
  if (!relationship) return;
  event.preventDefault();
  event.stopPropagation();
  hideTooltip();
  openDatabaseIconTarget({ kind: "relationship", id: relationship.id, tableId: relationship.fromTableId });
});

elements.tablesLayer.addEventListener("pointermove", event => {
  if (tablePressState?.pointerId === event.pointerId && Math.hypot(event.clientX - tablePressState.startX, event.clientY - tablePressState.startY) > 3) {
    tablePressState.moved = true;
  }
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  if (!dragState.historyRecorded && (event.clientX !== dragState.startX || event.clientY !== dragState.startY)) {
    checkpointHistory();
    dragState.historyRecorded = true;
    elements.workspace.classList.add("table-dragging");
    dragState.tablePositions.forEach(position => position.card.classList.add("dragging"));
  }
  const deltaX = (event.clientX - dragState.startX) / view.zoom;
  const deltaY = (event.clientY - dragState.startY) / view.zoom;
  dragState.deltaX = deltaX;
  dragState.deltaY = deltaY;
  for (const position of dragState.tablePositions) {
    position.card.style.transform = `translate3d(${deltaX}px, ${deltaY}px, 0)`;
  }
});

elements.tablesLayer.addEventListener("pointerup", event => {
  let tableMoved = false;
  if (dragState?.pointerId === event.pointerId) {
    tableMoved = dragState.historyRecorded;
    if (tableMoved) {
      for (const position of dragState.tablePositions) {
        const table = getTable(position.tableId);
        table.x = position.x + dragState.deltaX;
        table.y = position.y + dragState.deltaY;
        position.card.style.left = `${table.x}px`;
        position.card.style.top = `${table.y}px`;
        position.card.style.transform = "";
        position.card.classList.remove("dragging");
      }
      elements.workspace.classList.remove("table-dragging");
      renderConnections();
      saveSchema(LAYOUT_SAVE_DELAY_MS);
    }
    dragState = null;
  }
  if (tablePressState?.pointerId === event.pointerId) {
    const pressedTableId = tablePressState.tableId;
    const openInspector = !tablePressState.additive && selectedTableId === pressedTableId && selectedTableIds.has(pressedTableId);
    tablePressState = null;
    if (openInspector) {
      setInspectorContentCollapsed(false);
      openInspectorPane();
    }
  }
});

elements.tablesLayer.addEventListener("pointercancel", event => {
  if (dragState?.pointerId === event.pointerId) {
    dragState.tablePositions.forEach(position => {
      position.card.style.transform = "";
      position.card.classList.remove("dragging");
    });
    dragState = null;
    elements.workspace.classList.remove("table-dragging");
  }
  if (tablePressState?.pointerId === event.pointerId) tablePressState = null;
});

elements.tablesLayer.addEventListener("dblclick", event => {
  const card = event.target.closest(".table-card");
  if (!card || relationMode) return;
  event.preventDefault();
  selectTable(card.dataset.tableId, false, false);
  openInspectorPane();
  if (!tableDataTarget(getTable(card.dataset.tableId))) {
    showToast("Live data is not available for this table");
    return;
  }
  setTableDataPanelExpanded(true);
});

elements.workspace.addEventListener("pointerdown", event => {
  if (wheelZoomTimer !== null) finishWheelZoom();
  if (event.button === 1) {
    event.preventDefault();
    panState = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, viewX: view.x, viewY: view.y };
    elements.workspace.setPointerCapture(event.pointerId);
    elements.workspace.classList.add("panning");
    middlePanPanelSnapshot = collapseWorkspacePanelsForMiddlePan();
    return;
  }
  if (event.target.closest(".table-card") || event.target.closest(".connection-hit") || event.target.closest(".relationship-banner") || event.target.closest(".table-data-panel")) return;
  if (spacePressed || event.pointerType === "touch") {
    panState = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, viewX: view.x, viewY: view.y };
    elements.workspace.setPointerCapture(event.pointerId);
    elements.workspace.classList.add("panning");
  } else if (event.button === 0) {
    if (relationMode) return;
    event.preventDefault();
    const additive = isAdditiveTableSelection(event);
    marqueeState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
      baseSelection: additive ? new Set(selectedTableIds) : new Set(),
      baseSelectedTableId: additive ? selectedTableId : null
    };
    elements.workspace.setPointerCapture(event.pointerId);
  }
});

elements.workspace.addEventListener("pointermove", event => {
  if (marqueeState?.pointerId === event.pointerId) {
    if (!marqueeState.moved && Math.hypot(event.clientX - marqueeState.startX, event.clientY - marqueeState.startY) <= 3) return;
    if (!marqueeState.moved) {
      marqueeState.moved = true;
      setTableDataPanelExpanded(false);
      if (selectedTableId) {
        prepareInspectorTileForTablePress();
        openInspectorPane();
      }
      elements.workspace.classList.add("selecting");
    }
    updateMarqueeSelection(event);
    return;
  }
  if (!panState || panState.pointerId !== event.pointerId) return;
  view.x = panState.viewX + event.clientX - panState.startX;
  view.y = panState.viewY + event.clientY - panState.startY;
  applyStageTransform();
});

elements.workspace.addEventListener("pointerup", event => {
  if (marqueeState?.pointerId === event.pointerId) {
    if (marqueeState.moved) updateMarqueeSelection(event);
    else selectTable(null, false, false);
    finishMarqueeSelection();
    return;
  }
  if (panState?.pointerId !== event.pointerId) return;
  const moved = event.clientX !== panState.startX || event.clientY !== panState.startY;
  panState = null;
  elements.workspace.classList.remove("panning");
  applyView();
  restoreWorkspacePanelsAfterMiddlePan();
  if (moved) saveSchema(LAYOUT_SAVE_DELAY_MS);
});

elements.workspace.addEventListener("pointercancel", event => {
  if (marqueeState?.pointerId === event.pointerId) {
    selectedTableIds = new Set(marqueeState.baseSelection);
    selectedTableId = marqueeState.baseSelectedTableId;
    document.querySelectorAll(".table-card").forEach(card => card.classList.toggle("selected", selectedTableIds.has(card.dataset.tableId)));
    renderConnections();
    renderInspector();
    finishMarqueeSelection();
    return;
  }
  if (panState?.pointerId !== event.pointerId) return;
  panState = null;
  elements.workspace.classList.remove("panning");
  applyView();
  restoreWorkspacePanelsAfterMiddlePan();
});

elements.workspace.addEventListener("wheel", event => {
  if (event.target.closest(".table-data-panel")) return;
  event.preventDefault();
  const rect = elements.workspace.getBoundingClientRect();
  elements.workspace.classList.add("zooming");
  setZoom(view.zoom * (event.deltaY > 0 ? .9 : 1.1), event.clientX - rect.left, event.clientY - rect.top, true);
  clearTimeout(wheelZoomTimer);
  wheelZoomTimer = setTimeout(finishWheelZoom, WHEEL_ZOOM_IDLE_MS);
}, { passive: false });

elements.inspectorContent.addEventListener("input", event => {
  const table = getTable(selectedTableId);
  if (!table) return;
  if (event.target.dataset.field === "table-name") {
    const oldName = table.name;
    const newName = event.target.value;
    checkpointHistory(`table:${table.id}:name`);
    table.name = newName;
    updateTableNameInObjects(table, oldName, newName);
    saveSchema();
    renderTables();
    renderConnections();
    const heading = elements.inspectorContent.querySelector(".inspector-head h2");
    heading.textContent = table.name;
    updateTooltip(heading, table.name);
    const primaryKeyInput = elements.inspectorContent.querySelector('[data-field="primary-key-name"]');
    if (primaryKeyInput && table.primaryKey) primaryKeyInput.value = table.primaryKey.name;
    return;
  }
  if (event.target.dataset.field === "primary-key-name") {
    const primaryColumnIds = table.columns.filter(column => column.primary).map(column => column.id);
    if (!primaryColumnIds.length) return;
    const name = postgresNameWithSuffix(event.target.value, "");
    event.target.value = name;
    checkpointHistory(`table:${table.id}:primary-key-name`);
    table.primaryKey = {
      id: table.primaryKey?.id ?? uid("pk"),
      ...table.primaryKey,
      name,
      columnIds: primaryColumnIds
    };
    saveSchema();
    return;
  }
  const uniqueConstraintId = event.target.dataset.ucName;
  if (uniqueConstraintId) {
    const constraint = (table.uniqueConstraints ?? []).find(item => item.id === uniqueConstraintId);
    if (!constraint) return;
    const name = postgresNameWithSuffix(event.target.value, "");
    event.target.value = name;
    checkpointHistory(`unique-constraint:${uniqueConstraintId}:name`);
    constraint.name = name;
    saveSchema();
    return;
  }
  const editor = event.target.closest("[data-editor-column-id]");
  const field = event.target.dataset.columnField;
  if (editor && field && event.target.type !== "checkbox") {
    updateColumn(editor.dataset.editorColumnId, field, event.target.value, `column:${editor.dataset.editorColumnId}:${field}`);
  }
});

elements.inspectorContent.addEventListener("change", event => {
  const editor = event.target.closest("[data-editor-column-id]");
  const field = event.target.dataset.columnField;
  if (editor && field && event.target.type === "checkbox") {
    endHistoryGroup();
    updateColumn(editor.dataset.editorColumnId, field, event.target.checked);
    renderInspector();
  }
});

elements.inspectorContent.addEventListener("focusout", endHistoryGroup);

elements.inspectorContent.addEventListener("dragstart", event => {
  const handle = event.target.closest(".column-drag-handle");
  const editor = handle?.closest("[data-editor-column-id]");
  if (!editor) return;
  draggedColumnId = editor.dataset.editorColumnId;
  columnDropTarget = null;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", draggedColumnId);
  requestAnimationFrame(() => editor.classList.add("dragging"));
});

elements.inspectorContent.addEventListener("dragover", event => {
  const columnList = event.target.closest("#column-editors");
  if (!draggedColumnId || !columnList) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  updateColumnDropTarget(event.clientY);
});

elements.inspectorContent.addEventListener("drop", event => {
  const columnList = event.target.closest("#column-editors");
  if (!draggedColumnId || !columnList) return;
  event.preventDefault();
  const target = columnDropTarget ?? updateColumnDropTarget(event.clientY);
  if (!target) return;
  const columnId = draggedColumnId;
  draggedColumnId = null;
  reorderColumn(columnId, target.columnId, target.placeAfter);
  clearColumnDropState();
});

elements.inspectorContent.addEventListener("dragend", () => {
  draggedColumnId = null;
  clearColumnDropState();
});

elements.inspector.addEventListener("scroll", () => {
  if (inspectorContentCollapsed && elements.inspector.scrollTop) elements.inspector.scrollTop = 0;
});

elements.inspectorContent.addEventListener("keydown", event => {
  const handle = event.target.closest(".column-drag-handle");
  if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
  const table = getTable(selectedTableId);
  const editor = handle.closest("[data-editor-column-id]");
  const index = table?.columns.findIndex(column => column.id === editor.dataset.editorColumnId) ?? -1;
  const targetIndex = index + (event.key === "ArrowUp" ? -1 : 1);
  if (!table || index === -1 || targetIndex < 0 || targetIndex >= table.columns.length) return;
  event.preventDefault();
  reorderColumn(editor.dataset.editorColumnId, table.columns[targetIndex].id, event.key === "ArrowDown");
  elements.inspectorContent.querySelector(`[data-editor-column-id="${editor.dataset.editorColumnId}"] .column-drag-handle`)?.focus();
});

elements.inspectorContent.addEventListener("click", event => {
  const table = getTable(selectedTableId);
  if (!table) return;
  if (event.target.closest(".inspector-head") && !event.target.closest(".inspector-head-actions")) {
    handleInspectorHeaderGesture("left");
    return;
  }
  const checkItem = event.target.closest("[data-check-id]");
  if (checkItem) {
    openDatabaseObjectEditor({ kind: "check", id: checkItem.dataset.checkId, tableId: table.id }, "inspector");
    return;
  }
  const triggerItem = event.target.closest("[data-trigger-id]");
  if (triggerItem) {
    openDatabaseObjectEditor({ kind: "trigger", id: triggerItem.dataset.triggerId, tableId: table.id }, "inspector");
    return;
  }
  const indexItem = event.target.closest("[data-index-id]");
  if (indexItem) {
    openDatabaseObjectEditor({ kind: "index", id: indexItem.dataset.indexId, tableId: table.id }, "inspector");
    return;
  }
  const relationshipItem = event.target.closest("[data-edit-relationship]");
  if (relationshipItem) {
    const relationship = schema.relationships.find(item => item.id === relationshipItem.dataset.editRelationship);
    if (relationship) openRelationshipEditor(relationship);
    return;
  }
  const color = event.target.closest("[data-color]")?.dataset.color;
  if (color) {
    checkpointHistory();
    table.color = color;
    saveSchema();
    render();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "close-inspector") {
    closeInspectorPane();
    return;
  }
  if (action === "toggle-table-data") {
    setTableDataPanelExpanded(!tableDataPanelExpanded);
    return;
  }
  if (action === "copy-table") copySelectedTable();
  if (action === "paste-table") pasteCopiedTable();
  if (action === "delete-table") deleteTable(table.id);
  if (action === "add-column") {
    checkpointHistory();
    table.columns.push({ id: uid("col"), name: `column_${table.columns.length + 1}`, type: "varchar(255)", primary: false, nullable: true, unique: false, default: "" });
    saveSchema();
    render();
  }
  if (action === "add-trigger") {
    openDatabaseObjectEditor({ kind: "trigger", id: null, tableId: table.id }, "inspector");
    return;
  }
  if (action === "add-check") {
    openDatabaseObjectEditor({ kind: "check", id: null, tableId: table.id }, "inspector");
    return;
  }
  if (action === "add-index") {
    openDatabaseObjectEditor({ kind: "index", id: null, tableId: table.id }, "inspector");
    return;
  }
  if (action === "delete-column") {
    const columnId = event.target.closest("[data-editor-column-id]").dataset.editorColumnId;
    if (table.columns.length === 1) return showToast("A table needs at least one column");
    const dependent = findColumnDependentObjects(table, columnId);
    if (dependent.length > 0) {
      const names = dependent.map(o => `${o.kind} "${o.item.name}"`).join(", ");
      showToast(`Warning: deleting column also removes ${names}`);
      for (const { kind, item } of dependent) {
        const key = tableDatabaseObjectKey(kind);
        table[key] = (table[key] ?? []).filter(i => i.id !== item.id);
      }
    }
    checkpointHistory();
    const removedCol = table.columns.find(c => c.id === columnId);
    table.columns = table.columns.filter(column => column.id !== columnId);
    schema.relationships = schema.relationships.filter(relation => !relationshipIncludesColumn(relation, columnId));
    if (removedCol) {
      for (const uc of table.uniqueConstraints ?? []) {
        uc.columnIds = uc.columnIds.filter(cid => cid !== columnId);
        if (uc.columnIds.length >= 2) uc.name = availableUniqueConstraintName(table, uc.columnIds, uc.id);
      }
      table.uniqueConstraints = (table.uniqueConstraints ?? []).filter(uc => uc.columnIds.length >= 2);
    }
    saveSchema();
    render();
  }
  if (action === "delete-relationship") {
    const relationId = event.target.closest("[data-relation-id]").dataset.relationId;
    checkpointHistory();
    schema.relationships = schema.relationships.filter(relation => relation.id !== relationId);
    saveSchema();
    render();
  }
  const ucDelete = event.target.closest("[data-uc-delete]");
  if (ucDelete) {
    const ucId = ucDelete.dataset.ucDelete;
    checkpointHistory();
    table.uniqueConstraints = (table.uniqueConstraints ?? []).filter(uc => uc.id !== ucId);
    saveSchema();
    render();
  }
  const ucRemoveCol = event.target.closest("[data-uc-remove-col]");
  if (ucRemoveCol) {
    const colId = ucRemoveCol.dataset.ucRemoveCol;
    const ucEl = ucRemoveCol.closest("[data-uc-id]");
    const ucId = ucEl?.dataset.ucId;
    if (!ucId) return;
    checkpointHistory();
    const uc = (table.uniqueConstraints ?? []).find(c => c.id === ucId);
    if (uc) {
      uc.columnIds = uc.columnIds.filter(cid => cid !== colId);
      if (uc.columnIds.length < 2) {
        table.uniqueConstraints = (table.uniqueConstraints ?? []).filter(c => c.id !== ucId);
      } else {
        uc.name = availableUniqueConstraintName(table, uc.columnIds, uc.id);
      }
    }
    saveSchema();
    render();
  }
  if (action === "add-uc") {
    const available = table.columns.filter(c => !c.primary);
    if (available.length < 2) return showToast("Need at least 2 non-primary columns for a unique constraint");
    checkpointHistory();
    if (!table.uniqueConstraints) table.uniqueConstraints = [];
    const constraint = { id: uid("uc"), columnIds: [available[0].id, available[1].id] };
    constraint.name = availableUniqueConstraintName(table, constraint.columnIds, constraint.id);
    table.uniqueConstraints.push(constraint);
    saveSchema();
    render();
  }
});

elements.inspectorContent.addEventListener("contextmenu", event => {
  if (!event.target.closest(".inspector-head") || event.target.closest(".inspector-head-actions")) return;
  const table = getTable(selectedTableId);
  if (!table || !tableDataTarget(table)) return showToast("Live data is not available for this table");
  event.preventDefault();
  hideTooltip();
  handleInspectorHeaderGesture("right");
});

elements.tableDataPanel.addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "refresh-table-data") refreshTableData();
});
elements.maximizeTableData.addEventListener("click", () => setTableDataPanelMaximized(!tableDataPanelMaximized));
elements.minimizeTableData.addEventListener("click", () => {
  if (tableDataPanelMaximized) {
    setTableDataPanelMaximized(false);
    setInspectorContentCollapsed(false);
  } else {
    setTableDataPanelExpanded(false);
  }
});
elements.tableDataPanelHead.addEventListener("click", event => {
  if (event.target.closest(".table-data-panel-actions button")) return;
  toggleTablePanelActivePane("data");
});
elements.showSqlConsolePane.addEventListener("click", () => toggleTablePanelActivePane("console"));
elements.tableDataPanelHead.addEventListener("contextmenu", event => {
  if (event.target.closest(".table-data-panel-actions")) return;
  event.preventDefault();
  setTableDataPanelMaximized(!tableDataPanelMaximized);
});
elements.showSqlConsolePane.addEventListener("contextmenu", event => {
  event.preventDefault();
  setTableDataPanelMaximized(!tableDataPanelMaximized);
});
elements.runSqlConsole.addEventListener("click", executeSqlConsole);
elements.clearSqlConsole.addEventListener("click", clearSqlConsole);
elements.sqlConsoleInput.addEventListener("keydown", event => {
  if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
  event.preventDefault();
  executeSqlConsole();
});
elements.tableDataScroll.addEventListener("scroll", () => {
  if (elements.tableDataScroll.scrollHeight - elements.tableDataScroll.scrollTop - elements.tableDataScroll.clientHeight < 60) loadMoreTableData();
});

elements.inspectorContent.addEventListener("change", event => {
  const table = getTable(selectedTableId);
  if (!table) return;
  const ucAddCol = event.target.closest("[data-uc-add-col]");
  if (!ucAddCol) return;
  const colId = ucAddCol.value;
  if (!colId) return;
  const ucId = ucAddCol.dataset.ucAddCol;
  const uc = (table.uniqueConstraints ?? []).find(c => c.id === ucId);
  if (!uc || uc.columnIds.includes(colId)) return;
  checkpointHistory();
  uc.columnIds.push(colId);
  uc.name = availableUniqueConstraintName(table, uc.columnIds, uc.id);
  saveSchema();
  render();
});

document.querySelector("#add-table-button").addEventListener("click", () => {
  elements.tableNameInput.value = "";
  elements.tableDialog.showModal();
  requestAnimationFrame(() => elements.tableNameInput.focus());
});
document.querySelector("#cancel-table-dialog").addEventListener("click", () => elements.tableDialog.close());
elements.tableForm.addEventListener("submit", event => {
  event.preventDefault();
  const name = elements.tableNameInput.value;
  if (!name.trim()) return;
  const exists = schema.tables.some(table => table.name.toLowerCase() === name.trim().toLowerCase());
  if (exists) return showToast("A table with that name already exists");
  elements.tableDialog.close();
  createTable(name);
});
elements.relationshipEditorForm.addEventListener("submit", event => {
  event.preventDefault();
  saveRelationshipEditor();
});
elements.relationshipEditorName.addEventListener("input", event => {
  if (!relationshipEditorState) return;
  relationshipEditorState.name = event.target.value;
  relationshipEditorState.nameTouched = true;
  const error = validateRelationshipDraft(relationshipEditorState.relationship, relationshipEditorState.pairs, relationshipEditorState.name);
  elements.relationshipEditorStatus.classList.toggle("invalid", Boolean(error));
  elements.relationshipEditorStatus.textContent = error || "Constraint name is available.";
});
elements.relationshipEditorPairs.addEventListener("change", event => {
  if (!relationshipEditorState) return;
  const row = event.target.closest("[data-relationship-pair]");
  const side = event.target.dataset.relationshipSide;
  if (!row || !side) return;
  relationshipEditorState.pairs[Number(row.dataset.relationshipPair)][`${side}ColumnId`] = event.target.value;
  if (!relationshipEditorState.nameTouched) {
    relationshipEditorState.name = availableRelationshipName(relationshipEditorState.relationship, relationshipEditorState.pairs, relationshipEditorState.relationship.id);
  }
  renderRelationshipEditor();
});
elements.relationshipEditorPairs.addEventListener("click", event => {
  if (!relationshipEditorState) return;
  const row = event.target.closest("[data-relationship-pair]");
  if (!row) return;
  const index = Number(row.dataset.relationshipPair);
  if (event.target.closest("[data-remove-pair]") && relationshipEditorState.pairs.length > 1) {
    relationshipEditorState.pairs.splice(index, 1);
  } else {
    const direction = event.target.closest("[data-move-pair]")?.dataset.movePair;
    const targetIndex = index + (direction === "up" ? -1 : direction === "down" ? 1 : 0);
    if (!direction || targetIndex < 0 || targetIndex >= relationshipEditorState.pairs.length) return;
    reorderRelationshipPair(relationshipEditorState.pairs, index, targetIndex);
  }
  if (!relationshipEditorState.nameTouched) {
    relationshipEditorState.name = availableRelationshipName(relationshipEditorState.relationship, relationshipEditorState.pairs, relationshipEditorState.relationship.id);
  }
  renderRelationshipEditor();
});
function clearRelationshipPairDropState() {
  elements.relationshipEditorPairs.querySelectorAll(".dragging, .drop-before, .drop-after").forEach(row => row.classList.remove("dragging", "drop-before", "drop-after"));
}
elements.relationshipEditorPairs.addEventListener("dragstart", event => {
  const handle = event.target.closest(".relationship-pair-drag");
  const row = handle?.closest("[data-relationship-pair]");
  if (!row || !relationshipEditorState) return;
  relationshipPairDragIndex = Number(row.dataset.relationshipPair);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(relationshipPairDragIndex));
  event.dataTransfer.setDragImage(row, 18, Math.min(24, row.offsetHeight / 2));
  requestAnimationFrame(() => row.classList.add("dragging"));
});
elements.relationshipEditorPairs.addEventListener("dragover", event => {
  const row = event.target.closest("[data-relationship-pair]");
  if (!row || relationshipPairDragIndex == null) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
  clearRelationshipPairDropState();
  elements.relationshipEditorPairs.querySelector(`[data-relationship-pair="${relationshipPairDragIndex}"]`)?.classList.add("dragging");
  row.classList.add(after ? "drop-after" : "drop-before");
});
elements.relationshipEditorPairs.addEventListener("drop", event => {
  const row = event.target.closest("[data-relationship-pair]");
  if (!row || relationshipPairDragIndex == null || !relationshipEditorState) return;
  event.preventDefault();
  const targetIndex = Number(row.dataset.relationshipPair);
  const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
  const moved = dropRelationshipPair(relationshipEditorState.pairs, relationshipPairDragIndex, targetIndex, after);
  relationshipPairDragIndex = null;
  clearRelationshipPairDropState();
  if (moved) renderRelationshipEditor();
});
elements.relationshipEditorPairs.addEventListener("dragend", () => {
  relationshipPairDragIndex = null;
  clearRelationshipPairDropState();
});
elements.relationshipAddPair.addEventListener("click", () => {
  if (!relationshipEditorState) return;
  const source = getTable(relationshipEditorState.relationship.fromTableId);
  const target = getTable(relationshipEditorState.relationship.toTableId);
  const usedFrom = new Set(relationshipEditorState.pairs.map(pair => pair.fromColumnId));
  const usedTo = new Set(relationshipEditorState.pairs.map(pair => pair.toColumnId));
  const availableFrom = source.columns.filter(column => !usedFrom.has(column.id));
  const availableTo = target.columns.filter(column => !usedTo.has(column.id));
  let sourceColumn;
  let targetColumn;
  for (const candidate of availableFrom) {
    const match = availableTo.find(column => column.name === candidate.name && column.type === candidate.type);
    if (match) { sourceColumn = candidate; targetColumn = match; break; }
  }
  sourceColumn ??= availableFrom[0];
  targetColumn ??= availableTo.find(column => column.type === sourceColumn?.type) ?? availableTo[0];
  if (!sourceColumn || !targetColumn) return showToast("No unused column pair is available");
  relationshipEditorState.pairs.push({ fromColumnId: sourceColumn.id, toColumnId: targetColumn.id });
  if (!relationshipEditorState.nameTouched) {
    relationshipEditorState.name = availableRelationshipName(relationshipEditorState.relationship, relationshipEditorState.pairs, relationshipEditorState.relationship.id);
  }
  renderRelationshipEditor();
});
document.querySelector("#cancel-relationship-editor").addEventListener("click", closeRelationshipEditor);
elements.deleteRelationshipEditor.addEventListener("click", () => {
  if (!relationshipEditorState || relationshipEditorState.isNew) return;
  checkpointHistory();
  schema.relationships = schema.relationships.filter(item => item.id !== relationshipEditorState.relationship.id);
  saveSchema();
  closeRelationshipEditor();
  render();
});
elements.relationshipEditorDialog.addEventListener("close", () => {
  relationshipEditorState = null;
  relationshipPairDragIndex = null;
});
elements.relationTool.addEventListener("click", () => setRelationMode(!relationMode));
elements.selectTool.addEventListener("click", () => setRelationMode(false));
document.querySelector("#cancel-relationship").addEventListener("click", () => setRelationMode(false));
elements.undoButton.addEventListener("click", undo);
elements.redoButton.addEventListener("click", redo);
document.querySelector("#fit-button").addEventListener("click", fitDiagram);
document.querySelector("#zoom-in-button").addEventListener("click", () => setZoom(view.zoom * 1.15));
document.querySelector("#zoom-out-button").addEventListener("click", () => setZoom(view.zoom / 1.15));

elements.projectName.addEventListener("input", event => {
  checkpointHistory("project-name");
  schema.projectName = event.target.value;
  saveSchema();
});
elements.projectName.addEventListener("blur", endHistoryGroup);

const exportMenu = document.querySelector("#export-menu");
const appMenu = document.querySelector("#app-menu");
document.querySelector("#export-json-button").addEventListener("click", () => {
  exportMenu.removeAttribute("open");
  exportFile(`${schema.projectName || "schema"}.json`, JSON.stringify(schema, null, 2), "application/json");
  showToast("JSON downloaded");
});
document.querySelector("#export-sql-button").addEventListener("click", () => {
  exportMenu.removeAttribute("open");
  exportFile(`${schema.projectName || "schema"}.sql`, generateSql(), "text/sql");
  showToast("SQL downloaded");
});
document.addEventListener("pointerdown", event => {
  if (exportMenu.open && !event.target.closest("#export-menu")) exportMenu.removeAttribute("open");
  if (appMenu.open && !event.target.closest("#app-menu")) appMenu.removeAttribute("open");
  if (!event.target.closest("#object-icon-menu")) closeObjectIconMenu();
});
elements.objectIconMenu.addEventListener("click", event => {
  const item = event.target.closest("[data-object-menu-index]");
  if (!item) return;
  const target = objectIconMenuTargets[Number(item.dataset.objectMenuIndex)];
  if (target) openDatabaseIconTarget(target);
});
document.querySelector("#import-sql-button").addEventListener("click", () => elements.sqlFileInput.click());
elements.sqlFileInput.addEventListener("change", () => {
  const [file] = elements.sqlFileInput.files;
  if (file) importSqlFile(file);
});
document.querySelector("#save-schema-button").addEventListener("click", () => saveSchemaNow(true));
document.querySelector("#new-design-button").addEventListener("click", createNewSchema);
document.querySelector("#show-onboarding-button").addEventListener("click", () => {
  appMenu.removeAttribute("open");
  openOnboarding();
});
document.querySelector("#shutdown-button").addEventListener("click", () => {
  appMenu.removeAttribute("open");
  openShutdownDialog();
});
elements.onboardingBack.addEventListener("click", () => {
  onboardingPage -= 1;
  renderOnboardingPage();
});
elements.onboardingNext.addEventListener("click", () => {
  const pageCount = elements.onboardingDialog.querySelectorAll("[data-onboarding-page]").length;
  if (onboardingPage >= pageCount - 1) return closeOnboarding();
  onboardingPage += 1;
  renderOnboardingPage();
});
elements.onboardingSkip.addEventListener("click", closeOnboarding);
elements.onboardingDialog.addEventListener("close", rememberOnboardingPreference);
document.querySelector("#cancel-shutdown").addEventListener("click", () => elements.shutdownDialog.close());
elements.confirmShutdown.addEventListener("click", shutdownSchemii);
elements.shutdownDialog.addEventListener("cancel", event => { if (serverStopped) event.preventDefault(); });
document.querySelector("#open-schema-button").addEventListener("click", async () => {
  try {
    await flushPendingSave();
  } catch {
    return;
  }
  renderSchemaLibrary();
  elements.schemaDialog.showModal();
  await loadSchemaLibraryConnections();
});
document.querySelector("#close-schema-dialog").addEventListener("click", () => elements.schemaDialog.close());
document.querySelector("#new-schema-from-dialog").addEventListener("click", createNewSchema);
elements.schemaLibrary.addEventListener("click", event => {
  const button = event.target.closest("[data-library-action]");
  if (!button || button.disabled) return;
  if (button.dataset.libraryAction === "open") openSchema(button.dataset.schemaId);
  if (button.dataset.libraryAction === "delete") deleteSavedSchema(button.dataset.schemaId);
});

elements.postgresButton.addEventListener("click", async () => {
  renderPostgresCatalogSummary();
  elements.postgresDialog.showModal();
  await loadPostgresProfiles();
});
elements.aiButton.addEventListener("click", () => setAiPanelOpen(!elements.aiPanel.classList.contains("open")));
document.querySelector("#ai-close-button").addEventListener("click", () => setAiPanelOpen(false));
elements.aiNewChat.addEventListener("click", startNewAiChat);
elements.aiHistoryButton.addEventListener("click", openAiHistory);
document.querySelector("#ai-history-close").addEventListener("click", () => elements.aiHistoryDialog.close());
document.querySelector("#ai-settings-button").addEventListener("click", async () => {
  elements.aiSettingsDialog.showModal();
  await loadAiStatus(true);
});
document.querySelector("#ai-settings-close").addEventListener("click", () => elements.aiSettingsDialog.close());
elements.aiAccessSelect.addEventListener("change", updateAiAccessDisclosure);
elements.aiModelSelect.addEventListener("change", () => {
  rememberAiModel(elements.aiModelSelect.value);
  setAiBusy(aiState.busy);
});
elements.aiSqlPolicy.addEventListener("change", () => {
  aiState.sqlPolicyDeliberatelySelected = true;
});
elements.aiComposer.addEventListener("submit", event => {
  event.preventDefault();
  const text = elements.aiInput.value.trim();
  if (!text) return;
  elements.aiInput.value = "";
  sendAiMessage(text);
});
elements.aiInput.addEventListener("keydown", event => {
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  elements.aiComposer.requestSubmit();
});
document.querySelector("#close-postgres-dialog").addEventListener("click", () => elements.postgresDialog.close());
document.querySelector("#add-postgres-profile-button").addEventListener("click", () => openPostgresProfileEditor());
elements.postgresRefreshButton.addEventListener("click", loadPostgresProfiles);
elements.postgresNamespaceSelect.addEventListener("change", event => {
  postgresState.namespace = event.target.value;
  postgresState.plan = null;
  updatePostgresControls();
});
elements.postgresImportButton.addEventListener("click", importPostgresSchema);
elements.postgresPreviewButton.addEventListener("click", previewPostgresMigration);
elements.postgresObjectsButton.addEventListener("click", () => {
  renderDatabaseObjects();
  elements.postgresDialog.close();
  elements.databaseObjectsDialog.showModal();
});
elements.postgresProfilesList.addEventListener("click", async event => {
  if (postgresState.busy) return;
  const profileItem = event.target.closest("[data-profile-id]");
  if (!profileItem) return;
  const profileId = profileItem.dataset.profileId;
  const action = event.target.closest("[data-postgres-action]")?.dataset.postgresAction;
  if (action === "edit") return openPostgresProfileEditor(profileId);
  if (action === "test") return testPostgresProfile(profileId);
  if (action === "delete") {
    const profile = postgresState.profiles.find(item => item.id === profileId);
    if (!confirm(`Delete PostgreSQL connection ${profile?.name ?? profileId}?`)) return;
    setPostgresBusy(true, "Deleting connection...");
    try {
      await postgresRequest(`/api/postgres/profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" });
      if (postgresState.selectedProfileId === profileId) postgresState.selectedProfileId = null;
      await loadPostgresProfiles();
    } catch (error) {
      setPostgresStatus(error.message, true);
    } finally {
      setPostgresBusy(false);
    }
    return;
  }
  postgresState.selectedProfileId = profileId;
  postgresState.namespace = "";
  postgresState.plan = null;
  renderPostgresProfiles();
  await loadPostgresNamespaces();
});
elements.postgresProfileForm.addEventListener("submit", async event => {
  event.preventDefault();
  await savePostgresProfile(true);
});
document.querySelector("#cancel-postgres-profile").addEventListener("click", () => {
  elements.postgresProfileDialog.close();
  elements.postgresDialog.showModal();
});
document.querySelector("#test-postgres-profile").addEventListener("click", async () => {
  const profile = await savePostgresProfile(true);
  if (profile) await testPostgresProfile(profile.id);
});

document.querySelector("#close-database-objects").addEventListener("click", () => elements.databaseObjectsDialog.close());
document.querySelector("#add-database-object").addEventListener("click", () => openDatabaseObjectEditor());
elements.databaseObjectsList.addEventListener("click", event => {
  const item = event.target.closest("[data-object-id]");
  if (!item) return;
  openDatabaseObjectEditor({ kind: item.dataset.objectKind, id: item.dataset.objectId, tableId: item.dataset.objectTableId || null });
});
elements.databaseObjectType.addEventListener("change", updateDatabaseObjectTableState);
elements.databaseObjectEditorForm.addEventListener("submit", event => {
  event.preventDefault();
  saveDatabaseObject();
});
document.querySelector("#cancel-database-object").addEventListener("click", () => {
  closeDatabaseObjectEditor();
});
elements.deleteDatabaseObject.addEventListener("click", () => {
  const item = findDatabaseObject(postgresState.editingObject);
  if (!item || !confirm(`Delete ${item.name}?`)) return;
  checkpointHistory();
  removeDatabaseObject(postgresState.editingObject);
  saveSchema();
  closeDatabaseObjectEditor();
});

document.querySelector("#close-migration-dialog").addEventListener("click", () => elements.migrationDialog.close());
document.querySelector("#refresh-migration-preview").addEventListener("click", previewPostgresMigration);
elements.includeDestructive.addEventListener("change", previewPostgresMigration);
elements.confirmDestructive.addEventListener("change", updatePostgresControls);
elements.applyMigrationButton.addEventListener("click", applyPostgresMigration);
document.querySelector("#refresh-database-drift").addEventListener("click", refreshLinkedPostgresDesign);
document.querySelector("#dismiss-database-drift").addEventListener("click", () => {
  postgresState.dismissedFingerprint = elements.databaseDriftBanner.dataset.fingerprint || null;
  elements.databaseDriftBanner.hidden = true;
});

function renderFunctionsList() {
  const fns = schema.functions ?? [];
  elements.functionsCount.textContent = `${fns.length} routine${fns.length === 1 ? "" : "s"}`;
  elements.functionsList.innerHTML = fns.map(fn => {
    const sig = fn.identityArguments != null
      ? `${fn.namespace ? `${fn.namespace}.` : ""}${fn.name}(${fn.identityArguments})`
      : (fn.definition.match(/CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+(.+?)(?:\(|\s+RETURNS)/is) || [])[1] || fn.name;
    return `
      <div class="function-item" data-function-id="${fn.id}">
        <div>
          <div class="function-item-name">${escapeHtml(fn.name)}</div>
          <div class="function-item-meta">${escapeHtml(sig)}</div>
        </div>
        <button class="function-delete" data-function-delete="${fn.id}" type="button" data-tooltip="Delete function" aria-label="Delete function">×</button>
      </div>
    `;
  }).join("");
}

function openFunctionEditor(functionId) {
  const fn = functionId ? (schema.functions ?? []).find(f => f.id === functionId) : null;
  editingFunctionId = functionId || null;
  elements.functionEditorTitle.textContent = fn ? "Edit function" : "New function";
  elements.functionDefinitionInput.value = fn?.definition ?? "";
  elements.deleteFunctionButton.hidden = !fn;
  elements.functionEditorDialog.showModal();
}

document.querySelector("#functions-button").addEventListener("click", () => {
  renderFunctionsList();
  elements.functionsDialog.showModal();
});
elements.closeFunctionsDialog.addEventListener("click", () => elements.functionsDialog.close());
elements.addFunctionButton.addEventListener("click", () => openFunctionEditor(null));

elements.functionsList.addEventListener("click", event => {
  const item = event.target.closest(".function-item");
  const del = event.target.closest("[data-function-delete]");
  if (del) {
    event.stopPropagation();
    const id = del.dataset.functionDelete;
    checkpointHistory();
    schema.functions = (schema.functions ?? []).filter(f => f.id !== id);
    saveSchema();
    renderFunctionsList();
    renderInspector();
    return;
  }
  if (item) openFunctionEditor(item.dataset.functionId);
});

elements.functionEditorForm.addEventListener("submit", event => {
  event.preventDefault();
  const definition = elements.functionDefinitionInput.value.trim();
  if (!definition) return showToast("Function body is required");
  const match = definition.match(new RegExp(`^\\s*CREATE\\s+(?:OR\\s+REPLACE\\s+)?(FUNCTION|PROCEDURE)\\s+(${SQL_QUALIFIED_IDENTIFIER})\\s*\\(`, "i"));
  if (!match) return showToast("Enter a complete CREATE FUNCTION or PROCEDURE statement");
  const name = sqlIdentifierName(match[2]);
  const existing = editingFunctionId ? schema.functions.find(fn => fn.id === editingFunctionId) : null;
  const signatureChanged = existing && routineDeclarationIdentity(existing.definition) !== routineDeclarationIdentity(definition);
  const fn = {
    ...(existing ?? {}),
    id: editingFunctionId || uid("func"),
    name,
    kind: match[1].toLowerCase() === "procedure" ? "procedure" : "function",
    namespace: existing?.namespace ?? schema.postgres?.namespace,
    identityArguments: signatureChanged ? "" : existing?.identityArguments ?? "",
    definition
  };
  if (!schema.functions) schema.functions = [];
  checkpointHistory();
  if (editingFunctionId) {
    const idx = schema.functions.findIndex(f => f.id === editingFunctionId);
    if (idx !== -1) schema.functions[idx] = fn;
  } else {
    schema.functions.push(fn);
  }
  saveSchema();
  elements.functionEditorDialog.close();
  renderFunctionsList();
  renderInspector();
});
elements.cancelFunctionEditor.addEventListener("click", () => elements.functionEditorDialog.close());
elements.deleteFunctionButton.addEventListener("click", () => {
  if (!editingFunctionId) return;
  checkpointHistory();
  schema.functions = (schema.functions ?? []).filter(f => f.id !== editingFunctionId);
  saveSchema();
  elements.functionEditorDialog.close();
  renderFunctionsList();
  renderInspector();
});

document.addEventListener("pointerover", event => {
  const target = findAppTooltipTarget(event.target);
  if (target && target !== activeTooltipTarget) showTooltip(target);
});
document.addEventListener("pointerout", event => {
  if (!activeTooltipTarget || activeTooltipTarget.contains(event.relatedTarget)) return;
  hideTooltip();
});
document.addEventListener("focusin", event => {
  const target = findAppTooltipTarget(event.target, true);
  if (target) showTooltip(target);
});
document.addEventListener("focusout", event => {
  if (activeTooltipTarget?.contains(event.relatedTarget)) return;
  hideTooltip();
});
document.addEventListener("pointerdown", hideTooltip);
document.addEventListener("click", hideTooltip);
document.addEventListener("scroll", () => {
  hideTooltip();
  closeObjectIconMenu();
}, true);

window.addEventListener("keydown", event => {
  const editing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) || document.activeElement.isContentEditable;
  const shortcut = event.ctrlKey || event.metaKey;
  if (shortcut && event.key.toLowerCase() === "z") {
    if (editing) return;
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
    return;
  }
  if (shortcut && event.key.toLowerCase() === "y") {
    if (editing) return;
    event.preventDefault();
    redo();
    return;
  }
  if (shortcut && !editing && event.key.toLowerCase() === "c") {
    event.preventDefault();
    copySelectedTable();
  }
  if (shortcut && !editing && event.key.toLowerCase() === "v") {
    event.preventDefault();
    pasteCopiedTable();
  }
  if (event.code === "Space" && !editing) {
    spacePressed = true;
    event.preventDefault();
  }
  if (event.key === "Escape") {
    hideTooltip();
    closeObjectIconMenu();
    if (document.querySelector("dialog[open]")) return;
    setRelationMode(false);
    closeInspectorPane();
  }
});
window.addEventListener("keyup", event => { if (event.code === "Space") spacePressed = false; });
window.addEventListener("resize", () => {
  hideTooltip();
  closeObjectIconMenu();
  renderConnections();
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkPostgresDrift(); });
setInterval(checkPostgresDrift, 15000);

initializeSchemaLibrary().finally(() => {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.body.classList.remove("app-hydrating");
    initializeOnboarding();
  }));
});
