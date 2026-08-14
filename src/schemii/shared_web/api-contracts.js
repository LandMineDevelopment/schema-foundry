(() => {
  class ApiContractError extends Error {
    constructor(message, { contract = null, payload = null } = {}) {
      super(message);
      this.name = "ApiContractError";
      this.code = "invalid_api_response";
      this.contract = contract;
      this.payload = payload;
    }
  }

  const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const nonEmptyString = value => typeof value === "string" && value.length > 0;

  function requireObject(payload, contract) {
    if (!isObject(payload)) throw new ApiContractError(`The ${contract} response must be an object`, { contract, payload });
    return payload;
  }

  function requireArray(payload, field, contract) {
    requireObject(payload, contract);
    if (!Array.isArray(payload[field])) throw new ApiContractError(`The ${contract} response must include a ${field} array`, { contract, payload });
    return payload;
  }

  function validateSessionResponse(payload) {
    requireObject(payload, "session");
    if (!nonEmptyString(payload.token)) throw new ApiContractError("The session response must include a token", { contract: "session", payload });
    return payload;
  }

  function validateProfilesResponse(payload) {
    requireArray(payload, "profiles", "profiles");
    if (payload.profiles.some(profile => !isObject(profile) || !nonEmptyString(profile.id))) {
      throw new ApiContractError("The profiles response contains an invalid profile", { contract: "profiles", payload });
    }
    return payload;
  }

  function validateCatalogResponse(payload, kind = null) {
    requireObject(payload, "catalog");
    if (kind === "namespaces" || Object.hasOwn(payload, "namespaces")) {
      requireArray(payload, "namespaces", "catalog");
      if (payload.namespaces.some(namespace => !nonEmptyString(namespace))) {
        throw new ApiContractError("The catalog response contains an invalid namespace", { contract: "catalog", payload });
      }
    } else if (kind === "relations" || Object.hasOwn(payload, "relations")) {
      requireArray(payload, "relations", "catalog");
      if (payload.relations.some(relation => !isObject(relation) || !nonEmptyString(relation.name))) {
        throw new ApiContractError("The catalog response contains an invalid relation", { contract: "catalog", payload });
      }
    } else if (!nonEmptyString(payload.relation) && !nonEmptyString(payload.fingerprint)) {
      throw new ApiContractError("The catalog response has no recognized catalog data", { contract: "catalog", payload });
    }
    return payload;
  }

  function validatePlanResponse(payload) {
    requireObject(payload, "plan");
    const plan = isObject(payload.plan) ? payload.plan : payload;
    if (!nonEmptyString(plan.id) || !Array.isArray(plan.steps) || !Array.isArray(plan.warnings) || typeof plan.destructive !== "boolean") {
      throw new ApiContractError("The plan response is invalid", { contract: "plan", payload });
    }
    return payload;
  }

  function validateOperationResponse(payload) {
    requireObject(payload, "operation");
    const operation = isObject(payload.operation) ? payload.operation : payload;
    if (!nonEmptyString(operation.id) || !nonEmptyString(operation.state)) {
      throw new ApiContractError("The operation response is invalid", { contract: "operation", payload });
    }
    return payload;
  }

  function validateResourceSummariesResponse(payload) {
    requireObject(payload, "resource summaries");
    const summaries = Array.isArray(payload.resources) ? payload.resources : payload.summaries;
    if (!Array.isArray(summaries)) {
      throw new ApiContractError("The resource summaries response must include a resources or summaries array", { contract: "resource summaries", payload });
    }
    if (summaries.some(resource => !isObject(resource) || !nonEmptyString(resource.id))) {
      throw new ApiContractError("The resource summaries response contains an invalid resource", { contract: "resource summaries", payload });
    }
    return payload;
  }

  function createApiPathPredicate(prefix) {
    if (typeof prefix !== "string" || !prefix.startsWith("/") || prefix.endsWith("/")) throw new TypeError("An absolute API path prefix is required");
    return value => {
      if (typeof value !== "string") return false;
      const path = value.split(/[?#]/, 1)[0];
      return path === prefix || path.startsWith(`${prefix}/`);
    };
  }

  function postgresResponseValidator(path, method = "GET") {
    if (typeof path !== "string") return null;
    const pathname = path.split(/[?#]/, 1)[0];
    if (pathname === "/api/postgres/profiles" && String(method).toUpperCase() === "GET") return validateProfilesResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/namespaces$/.test(pathname)) return payload => validateCatalogResponse(payload, "namespaces");
    if (/^\/api\/postgres\/profiles\/[^/]+\/relations$/.test(pathname)) return payload => validateCatalogResponse(payload, "relations");
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:preview|views\/preview)$/.test(pathname)) return validatePlanResponse;
    return null;
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    ApiContractError,
    createApiPathPredicate,
    postgresResponseValidator,
    validateCatalogResponse,
    validateOperationResponse,
    validatePlanResponse,
    validateProfilesResponse,
    validateResourceSummariesResponse,
    validateSessionResponse,
  });
})();
