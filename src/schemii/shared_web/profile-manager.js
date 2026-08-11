(() => {
  const DEFAULTS = Object.freeze({
    name: "",
    host: "127.0.0.1",
    port: 5432,
    database: "",
    user: "",
    sslmode: "prefer",
    timeout: 10,
  });

  function createProfileForm({ fields, defaults = {} } = {}) {
    const required = ["name", "host", "port", "database", "user", "password", "sslmode", "timeout"];
    if (!fields || required.some(name => !fields[name])) throw new TypeError("All PostgreSQL profile fields are required");
    const initial = { ...DEFAULTS, ...defaults };

    function read() {
      return {
        name: fields.name.value.trim(),
        host: fields.host.value.trim(),
        port: Number(fields.port.value),
        dbname: fields.database.value.trim(),
        user: fields.user.value.trim(),
        password: fields.password.value,
        sslmode: fields.sslmode.value,
        timeout: Number(fields.timeout.value),
      };
    }

    function fill(profile = null) {
      if (fields.id) fields.id.value = profile?.id ?? "";
      fields.name.value = profile?.name ?? initial.name;
      fields.host.value = profile?.host ?? initial.host;
      fields.port.value = profile?.port ?? initial.port;
      fields.database.value = profile?.dbname ?? initial.database;
      fields.user.value = profile?.user ?? initial.user;
      fields.password.value = "";
      fields.sslmode.value = profile?.sslmode ?? initial.sslmode;
      fields.timeout.value = profile?.timeout ?? initial.timeout;
    }

    function clearPassword() {
      fields.password.value = "";
    }

    return Object.freeze({ read, fill, clearPassword, profileId: () => fields.id?.value ?? "" });
  }

  function createProfileRepository({ postgresClient } = {}) {
    if (!postgresClient || typeof postgresClient.request !== "function") throw new TypeError("A PostgreSQL client is required");
    return Object.freeze({
      async list() {
        const result = await postgresClient.request("/api/postgres/profiles");
        return Array.isArray(result.profiles) ? result.profiles : [];
      },
      save(profileId, payload) {
        const path = profileId ? `/api/postgres/profiles/${encodeURIComponent(profileId)}` : "/api/postgres/profiles";
        return postgresClient.request(path, {
          method: profileId ? "PUT" : "POST",
          body: JSON.stringify(payload),
        });
      },
      test(profileId) {
        return postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/test`, {
          method: "POST", body: "{}",
        });
      },
      remove(profileId) {
        return postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" });
      },
      async namespaces(profileId) {
        const result = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/namespaces`);
        return Array.isArray(result.namespaces) ? result.namespaces : [];
      },
    });
  }

  function initializeNamespaceSelect(select, namespaces, { preferred = null, emptyLabel = "No user namespaces found" } = {}) {
    const values = Array.isArray(namespaces) ? namespaces : [];
    select.replaceChildren(...(values.length ? values.map(value => new Option(value, value)) : [new Option(emptyLabel, "")]));
    const selected = values.includes(preferred) ? preferred : values[0] ?? null;
    if (selected) select.value = selected;
    select.disabled = !values.length;
    return selected;
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}), createProfileForm, createProfileRepository, initializeNamespaceSelect,
  });
})();
