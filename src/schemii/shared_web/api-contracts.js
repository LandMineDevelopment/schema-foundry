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
    if (!nonEmptyString(payload.token) || !nonEmptyString(payload.serverId)) throw new ApiContractError("The session response must include a token and server ID", { contract: "session", payload });
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

  function validateSchemaRecord(payload) {
    requireObject(payload, "schema");
    if (!nonEmptyString(payload.id) || !Number.isInteger(payload.revision) || payload.revision < 1 || !nonEmptyString(payload.layoutToken) || !isObject(payload.schema)) {
      throw new ApiContractError("The schema response is invalid", { contract: "schema", payload });
    }
    return payload;
  }

  function validateDashboardRecord(payload) {
    requireObject(payload, "dashboard");
    if (!nonEmptyString(payload.id) || !Number.isInteger(payload.revision) || payload.revision < 1 || !isObject(payload.dashboard) || !Array.isArray(payload.dashboard.widgets)) {
      throw new ApiContractError("The dashboard response is invalid", { contract: "dashboard", payload });
    }
    return payload;
  }

  function validateSchemasResponse(payload) {
    requireArray(payload, "schemas", "schemas");
    payload.schemas.forEach(validateSchemaRecord);
    return payload;
  }

  function validateDashboardsResponse(payload) {
    requireArray(payload, "dashboards", "dashboards");
    payload.dashboards.forEach(validateDashboardRecord);
    return payload;
  }

  function validateSchemaSaveResponse(payload) {
    requireObject(payload, "schema save");
    if (!nonEmptyString(payload.saved) || !Number.isInteger(payload.revision) || payload.revision < 1 || !nonEmptyString(payload.updatedAt) || !/^[0-9a-f]{64}$/.test(payload.layoutToken)) {
      throw new ApiContractError("The schema save response is invalid", { contract: "schema save", payload });
    }
    return payload;
  }

  function validateDeleteResponse(payload) {
    requireObject(payload, "delete");
    if (!nonEmptyString(payload.deleted)) throw new ApiContractError("The delete response is invalid", { contract: "delete", payload });
    return payload;
  }

  function validateShutdownResponse(payload) {
    requireObject(payload, "shutdown");
    if (payload.shuttingDown !== true) throw new ApiContractError("The shutdown response is invalid", { contract: "shutdown", payload });
    return payload;
  }

  function validateDeletionImpactResponse(payload) {
    requireObject(payload, "profile deletion impact");
    if (!nonEmptyString(payload.profileId) || !nonEmptyString(payload.profileFingerprint) || !/^[0-9a-f]{64}$/.test(payload.impactFingerprint) || !isObject(payload.impact)) {
      throw new ApiContractError("The profile deletion impact response is invalid", { contract: "profile deletion impact", payload });
    }
    for (const field of ["schemas", "dashboards", "activeChats", "plans", "operations"]) {
      if (!Array.isArray(payload.impact[field])) throw new ApiContractError("The profile deletion impact response is incomplete", { contract: "profile deletion impact", payload });
    }
    return payload;
  }

  function validateQueryResultResponse(payload) {
    requireObject(payload, "query result");
    if (!Array.isArray(payload.columns) || !Array.isArray(payload.rows) || !nonEmptyString(payload.sql) || !Array.isArray(payload.parameters)) {
      throw new ApiContractError("The query result response is invalid", { contract: "query result", payload });
    }
    return payload;
  }

  function validateDetailResultResponse(payload) {
    validateQueryResultResponse(payload);
    if (!Number.isInteger(payload.matchingRowCount) || typeof payload.hasMore !== "boolean") {
      throw new ApiContractError("The detail result response is invalid", { contract: "detail result", payload });
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
    if (/^\/api\/postgres\/profiles\/[^/]+\/deletion-impact$/.test(pathname)) return validateDeletionImpactResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/namespaces$/.test(pathname)) return payload => validateCatalogResponse(payload, "namespaces");
    if (/^\/api\/postgres\/profiles\/[^/]+\/relations$/.test(pathname)) return payload => validateCatalogResponse(payload, "relations");
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:preview|views\/preview)$/.test(pathname)) return validatePlanResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:relation\/query|saved-widgets\/aggregate)$/.test(pathname)) return validateQueryResultResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:relation\/detail|saved-widgets\/detail)$/.test(pathname)) return validateDetailResultResponse;
    return null;
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    ApiContractError,
    createApiPathPredicate,
    postgresResponseValidator,
    validateCatalogResponse,
    validateDashboardRecord,
    validateDashboardsResponse,
    validateDeleteResponse,
    validateDeletionImpactResponse,
    validateDetailResultResponse,
    validateOperationResponse,
    validatePlanResponse,
    validateProfilesResponse,
    validateQueryResultResponse,
    validateResourceSummariesResponse,
    validateSchemaRecord,
    validateSchemasResponse,
    validateSchemaSaveResponse,
    validateShutdownResponse,
    validateSessionResponse,
  });
})();
