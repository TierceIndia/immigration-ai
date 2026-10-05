const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const TOKEN_KEY = "ik_admin_token";
const ROLE_KEY = "ik_admin_role";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRole() {
  return localStorage.getItem(ROLE_KEY);
}

export function setSession(token, role) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail = (data && data.detail) || res.statusText || "Request failed";
    throw new ApiError(detail, res.status);
  }
  return data;
}

export const api = {
  login(email, password) {
    return request("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    });
  },
  listSources() {
    return request("/api/v1/admin/sources");
  },
  listRuns(limit = 50) {
    return request(`/api/v1/admin/runs?limit=${limit}`);
  },
  getRun(runId) {
    return request(`/api/v1/admin/runs/${runId}`);
  },
  triggerSync() {
    return request("/api/v1/admin/sync", { method: "POST" });
  },
};

export { ApiError };
