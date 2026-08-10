const elements = {
  dialog: document.querySelector("#connections-dialog"),
  connectionList: document.querySelector("#connection-list"),
  form: document.querySelector("#connection-form"),
  status: document.querySelector("#connection-status"),
  sourceSummary: document.querySelector(".source-summary"),
  sourceName: document.querySelector("#source-name"),
  sourceDetail: document.querySelector("#source-detail"),
  namespaceSelect: document.querySelector("#namespace-select"),
  canvas: document.querySelector("#dashboard-canvas")
};

let sessionToken = null;
let profiles = [];
let selectedProfileId = null;
let draggedWidget = null;
const postgres = window.SchemiiShared.createPostgresClient({
  getToken: () => sessionToken,
  setToken: token => { sessionToken = token; }
});

function setStatus(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
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
  setStatus("");
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
    setStatus(error.message, true);
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
    setStatus(namespaces.length ? `Connected to ${profile.dbname}.` : "Connected; no user namespaces were found.");
  } catch (error) {
    elements.namespaceSelect.replaceChildren(new Option("Connection unavailable", ""));
    elements.sourceSummary.classList.remove("connected");
    elements.sourceName.textContent = profile.name;
    elements.sourceDetail.textContent = error.message;
    setStatus(error.message, true);
  }
}

document.querySelector("#connections-button").addEventListener("click", async () => {
  elements.dialog.showModal();
  await loadProfiles();
});
document.querySelector("#close-connections").addEventListener("click", () => elements.dialog.close());
document.querySelector("#new-connection").addEventListener("click", () => {
  selectedProfileId = null;
  renderProfiles();
  fillProfileForm();
});
elements.form.addEventListener("submit", async event => {
  event.preventDefault();
  const profileId = document.querySelector("#profile-id").value;
  const path = profileId ? `/api/postgres/profiles/${encodeURIComponent(profileId)}` : "/api/postgres/profiles";
  setStatus("Saving connection...");
  try {
    const profile = await postgres.request(path, { method: profileId ? "PUT" : "POST", body: JSON.stringify(profilePayload()) });
    selectedProfileId = profile.id;
    document.querySelector("#profile-password").value = "";
    await loadProfiles();
  } catch (error) {
    setStatus(error.message, true);
  }
});
document.querySelector("#test-connection").addEventListener("click", async () => {
  const profileId = document.querySelector("#profile-id").value;
  if (!profileId) return setStatus("Save the connection before testing it.", true);
  setStatus("Testing connection...");
  try {
    const result = await postgres.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/test`, { method: "POST", body: "{}" });
    setStatus(`Connected to ${result.database ?? "PostgreSQL"}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});
elements.namespaceSelect.addEventListener("change", () => {
  const profile = profiles.find(item => item.id === selectedProfileId);
  if (profile && elements.namespaceSelect.value) elements.sourceDetail.textContent = `${profile.dbname}.${elements.namespaceSelect.value}`;
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

document.querySelector("#add-widget-button").addEventListener("click", () => {
  const widget = document.createElement("article");
  widget.className = "widget metric-widget";
  widget.draggable = true;
  const header = document.createElement("header");
  const label = document.createElement("span");
  label.textContent = "Untitled metric";
  const menu = document.createElement("button");
  menu.type = "button";
  menu.textContent = "...";
  header.append(label, menu);
  const value = document.createElement("strong");
  value.textContent = "--";
  const copy = document.createElement("p");
  copy.textContent = "Choose a query and value field";
  widget.append(header, value, copy);
  elements.canvas.prepend(widget);
});

elements.canvas.addEventListener("dragstart", event => {
  draggedWidget = event.target.closest(".widget");
  draggedWidget?.classList.add("dragging");
});
elements.canvas.addEventListener("dragover", event => {
  const target = event.target.closest(".widget");
  if (!draggedWidget || !target || target === draggedWidget) return;
  event.preventDefault();
  elements.canvas.querySelectorAll(".drag-over").forEach(widget => widget.classList.remove("drag-over"));
  target.classList.add("drag-over");
});
elements.canvas.addEventListener("drop", event => {
  const target = event.target.closest(".widget");
  if (draggedWidget && target && target !== draggedWidget) elements.canvas.insertBefore(draggedWidget, target);
});
elements.canvas.addEventListener("dragend", () => {
  elements.canvas.querySelectorAll(".dragging, .drag-over").forEach(widget => widget.classList.remove("dragging", "drag-over"));
  draggedWidget = null;
});

loadProfiles();
