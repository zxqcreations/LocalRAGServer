/** 管理端 API 封装：JSON + CSRF 头 + 错误信封解析。 */
let csrfToken = "";

export function setCsrfToken(token) {
  csrfToken = token;
}

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const resp = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  const data = await resp.json().catch(() => ({}));
  if (!data.success) {
    const err = new Error(data.error?.message || `HTTP ${resp.status}`);
    err.code = data.error?.code;
    err.status = resp.status;
    throw err;
  }
  return data.data;
}

export const api = {
  login: (username, password) => request("POST", "/admin/api/login", { username, password }),
  logout: () => request("POST", "/admin/api/logout"),
  me: () => request("GET", "/admin/api/me"),
  changePassword: (current_password, new_password) =>
    request("POST", "/admin/api/change-password", { current_password, new_password }),
  listKbs: () => request("GET", "/admin/api/kb"),
  createKey: (name, kb_acl) => request("POST", "/admin/api/keys", { name, kb_acl }),
  listKeys: () => request("GET", "/admin/api/keys"),
  revokeKey: (id) => request("DELETE", `/admin/api/keys/${id}`),
  metrics: () => request("GET", "/admin/api/metrics"),
  audit: (limit = 50) => request("GET", `/admin/api/audit?limit=${limit}`),
  annotate: (payload) => request("POST", "/admin/api/annotations", payload),
  listAnnotations: (kb_id) => request("GET", `/admin/api/annotations?kb_id=${kb_id}`),
  searchDebug: (kb_id, query) =>
    request("POST", "/admin/api/search-debug", { kb_id, query }),
};
