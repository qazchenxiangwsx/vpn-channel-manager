/* 真实后端 API 封装,所有屏共用。失败抛 Error(消息含状态码+响应体)。 */
(function () {
  "use strict";
  async function req(method, url, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(url, opt);
    if (!r.ok) {
      const t = await r.text().catch(() => "");
      throw new Error(`${r.status} ${t}`.trim());
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? r.json() : r.text();
  }
  window.api = {
    channels: () => req("GET", "/api/channels"),
    system: () => req("GET", "/api/system"),
    create: (data) => req("POST", "/api/channels", data),
    login: (id) => req("GET", `/api/channels/${id}/login`),
    status: (id) => req("GET", `/api/channels/${id}/status`),
    addRules: (id, patterns, kind) =>
      req("POST", `/api/channels/${id}/rules`, kind ? { patterns, kind } : { patterns }),
    delRule: (id, rid) => req("DELETE", `/api/channels/${id}/rules/${rid}`),
    toggleRule: (id, rid, enabled) =>
      req("PATCH", `/api/channels/${id}/rules/${rid}`, { enabled }),
    start: (id) => req("POST", `/api/channels/${id}/start`),
    stop: (id) => req("POST", `/api/channels/${id}/stop`),
    remove: (id) => req("DELETE", `/api/channels/${id}`),
    logs: (id, tail) => req("GET", `/api/channels/${id}/logs?tail=${tail || 200}`),
    connections: () => req("GET", "/api/connections"),
    snippet: () => req("GET", "/api/clash-snippet"),
  };
})();
