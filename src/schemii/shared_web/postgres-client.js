(() => {
  function createPostgresClient({ sessionClient = null, getToken, setToken, sessionPath = "/api/session" } = {}) {
    const client = sessionClient || window.SchemiiShared.createSessionClient({ getToken, setToken, sessionPath });
    const allowPath = path => typeof path === "string" && path.startsWith("/api/postgres/");
    function request(path, options = {}) {
      return client.json(path, options, { allowPath, defaultMessage: "PostgreSQL request failed" });
    }
    return { request };
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createPostgresClient });
})();
