(() => {
  function createPostgresClient({ sessionClient = null, getToken, setToken, sessionPath = "/api/session" } = {}) {
    const client = sessionClient || window.SchemiiShared.createSessionClient({ getToken, setToken, sessionPath });
    const allowPath = window.SchemiiShared.createApiPathPredicate
      ? window.SchemiiShared.createApiPathPredicate("/api/postgres")
      : path => typeof path === "string" && (path === "/api/postgres" || path.startsWith("/api/postgres/"));
    function request(path, options = {}) {
      const validate = window.SchemiiShared.postgresResponseValidator?.(path, options.method || "GET") || null;
      return client.json(path, options, { allowPath, defaultMessage: "PostgreSQL request failed", validate });
    }
    return { request };
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createPostgresClient });
})();
