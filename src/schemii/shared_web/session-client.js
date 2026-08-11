(() => {
  function createSessionClient({ getToken, setToken, sessionPath = "/api/session" } = {}) {
    if (typeof getToken !== "function" || typeof setToken !== "function") throw new TypeError("Token accessors are required");

    async function ensureToken(options = {}) {
      let token = getToken();
      if (token) return token;
      const response = await fetch(sessionPath, { signal: options.signal });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.token) throw new Error(payload.error?.message || "Could not start a local session");
      token = payload.token;
      setToken(token);
      return token;
    }

    function validatePath(path, allowPath) {
      if (typeof path !== "string" || (typeof allowPath === "function" && !allowPath(path))) {
        throw new Error("Request must use an allowed local application API");
      }
    }

    async function authenticatedFetch(path, options = {}, requestOptions = {}) {
      const { allowPath, defaultMessage = "Local application request failed", retryInvalidSession = true } = requestOptions;
      validatePath(path, allowPath);
      const token = await ensureToken({ signal: options.signal });
      const response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
          "X-Schemii-Token": token,
        },
      });
      if (response.ok) return response;
      const payload = await response.clone().json().catch(() => ({}));
      if (payload.error?.code === "invalid_session" && retryInvalidSession) {
        setToken(null);
        return authenticatedFetch(path, options, { ...requestOptions, retryInvalidSession: false });
      }
      const error = new Error(payload.error?.message || payload.error || defaultMessage);
      error.code = payload.error?.code;
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    async function json(path, options = {}, requestOptions = {}) {
      const response = await authenticatedFetch(path, options, requestOptions);
      return response.json().catch(() => ({}));
    }

    return Object.freeze({ ensureToken, fetch: authenticatedFetch, json });
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createSessionClient });
})();
