(() => {
  function createPostgresClient({ getToken, setToken, sessionPath = "/api/session" } = {}) {
    if (typeof getToken !== "function" || typeof setToken !== "function") throw new TypeError("Token accessors are required");
    async function request(path, options = {}, retry = true) {
      if (typeof path !== "string" || !path.startsWith("/api/postgres/")) {
        throw new Error("PostgreSQL requests must use the local application API");
      }
      let token = getToken();
      if (!token) {
        const sessionResponse = await fetch(sessionPath);
        const session = await sessionResponse.json().catch(() => ({}));
        if (!sessionResponse.ok || !session.token) throw new Error(session.error?.message || "Could not start a PostgreSQL session");
        token = session.token;
        setToken(token);
      }
      const response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          "X-Schemii-Token": token,
          ...(options.headers || {})
        }
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (payload.error?.code === "invalid_session" && retry) {
          setToken(null);
          return request(path, options, false);
        }
        const error = new Error(payload.error?.message || payload.error || "PostgreSQL request failed");
        error.code = payload.error?.code;
        error.status = response.status;
        throw error;
      }
      return payload;
    }
    return { request };
  }

  window.SchemiiShared = Object.freeze({ createPostgresClient });
})();
