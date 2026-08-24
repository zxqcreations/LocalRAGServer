/** 管理端 API 封装：JSON + CSRF 头 + 错误信封解析。 */
const CSRF_KEY = "rag_admin_csrf_token";

function loadCsrfToken() {
  // 隐私模式/沙箱 iframe 下 localStorage 可能抛 SecurityError，降级为空串
  // （等同原内存态；会话刷新后经 /me 自愈重新取回）
  try {
    return localStorage.getItem(CSRF_KEY) || "";
  } catch {
    return "";
  }
}

function storeCsrfToken(token) {
  try {
    localStorage.setItem(CSRF_KEY, token);
  } catch {
    /* 存储不可用时仅保留内存态（当前会话内仍可用） */
  }
}

// CSRF token 持久化：页面刷新/多标签页仍能签发状态变更请求。
// token 为独立随机值（安全审查 H-1，≠ 会话凭证），会话凭证仍是 HttpOnly Cookie；
// 多标签页切换会话后经 /admin/api/me 自愈（App.vue refreshMe）
let csrfToken = loadCsrfToken();

export function setCsrfToken(token) {
  csrfToken = token;
  storeCsrfToken(token);
}

export function clearCsrfToken() {
  csrfToken = "";
  try {
    localStorage.removeItem(CSRF_KEY);
  } catch {
    /* 存储不可用则仅清内存态 */
  }
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
  // ---------- KB CRUD ----------
  createKb: (name, kb_type, description) =>
    request("POST", "/admin/api/kb", { name, kb_type, description }),
  listKbStats: () => request("GET", "/admin/api/kb/stats"),
  getKbDetail: (kb_id) => request("GET", `/admin/api/kb/${kb_id}`),
  updateKb: (kb_id, data) => request("PUT", `/admin/api/kb/${kb_id}`, data),
  deleteKb: (kb_id) => request("DELETE", `/admin/api/kb/${kb_id}`),
  listDocs: (kb_id) => request("GET", `/admin/api/kb/${kb_id}/documents`),
  deleteDoc: (kb_id, doc_id) => request("DELETE", `/admin/api/kb/${kb_id}/documents/${doc_id}`),
  // 文件上传（base64 JSON，避免 multipart 代理问题）
  uploadDocument: (kb_id, filename, base64Data) =>
    request("POST", `/admin/api/kb/${kb_id}/documents/upload-json`, { filename, data: base64Data }),
  // ---------- Subscriptions (复用已有后端接口) ----------
  listSubscriptions: (kb_id) => request("GET", `/admin/api/subscriptions?kb_id=${kb_id}`),
  createSubscription: (kb_id, url, interval_hours) =>
    request("POST", "/admin/api/subscriptions", { kb_id, url, interval_hours }),
  toggleSubscription: (sub_id, enabled) =>
    request("POST", `/admin/api/subscriptions/${sub_id}/toggle`, { enabled }),
  deleteSubscription: (sub_id) => request("DELETE", `/admin/api/subscriptions/${sub_id}`),
};

// ---------- multipart/form-data 上传 helper ----------

// 导出 csrfToken（供文件上传等直接 fetch 场景使用）
export const getCsrfToken = () => csrfToken;

// 别名：旧代码可能直接 import listKbStats
export const listKbStats = () => api.listKbStats();

export async function apiUpload(path, formData) {
  const headers = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const resp = await fetch(path, {
    method: "POST",
    headers,
    body: formData,
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
