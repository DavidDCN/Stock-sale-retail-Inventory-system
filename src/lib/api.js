// One place that talks to the laptop server (backend/server.py). Everything the React app saves lives in backend/inventory.db.
const DEFAULT_BASE = "http://localhost:8000";
// Opened from the server itself (the .exe window, or http://127.0.0.1:8000/)? Then the server is this same address.
// Only the Vite dev server (npm run dev, port 5173...) needs the separate address.
const SERVED_BY_SERVER = typeof window !== "undefined" && /^https?:$/.test(window.location.protocol)
  && !/^[45]17\d$/.test(window.location.port);

// true inside TresNinosShop.exe (or any page served by the shop server itself)
export const PACKAGED = SERVED_BY_SERVER;
// The page files and backend/server.py are updated together and carry the same number. When the server answers with
// another number, its window is still running the OLD server (it was not restarted after an update): new buttons then
// get "Method Not Allowed" / "Not Found". The app shell shows a banner, and such errors say what to do.
export const SERVER_VERSION = "2026.11.04-r23";
export const RESTART_MSG = "The shop server window is still running the old version. Restart it: click that window, press Ctrl+C, "
  + "then run  python server.py serve  again (and reload this page).";

export const OFFLINE_MSG = SERVED_BY_SERVER
  ? "The shop program is not answering. It reconnects by itself; if this stays, close the window and open TresNinosShop again."
  : "Cannot reach the shop server. Open a terminal in the backend folder and run:  python server.py serve";

export const getBase = () => {
  if (SERVED_BY_SERVER) return window.location.origin;
  try { return localStorage.getItem("shop_api") || DEFAULT_BASE; } catch { return DEFAULT_BASE; }
};
export const getToken = () => {
  try { return localStorage.getItem("shop_token") || ""; } catch { return ""; }
};
export const hasToken = () => !!getToken();
const setToken = (t) => { try { t ? localStorage.setItem("shop_token", t) : localStorage.removeItem("shop_token"); } catch { /* private mode */ } };

// Every failed call is announced, so the app shell can show it in a toast ABOVE any open modal
// (before, errors were printed on the page behind the modal, so buttons like Apply looked dead).
function announce(message) {
  window.dispatchEvent(new CustomEvent("shop-error", { detail: message }));
}

const AUTH_PATHS = ["/api/login", "/api/desk-login", "/api/register", "/api/setup"];

async function request(method, path, body, form, quiet) {
  const headers = {};
  if (getToken()) headers.Authorization = `Bearer ${getToken()}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(getBase() + path, { method, headers, body: payload });
  } catch {
    const msg = OFFLINE_MSG;
    if (!quiet) announce(msg);
    throw new Error(msg);
  }
  if (res.status === 401 && !AUTH_PATHS.includes(path)) {
    setToken("");
    window.dispatchEvent(new Event("shop-auth-lost"));
  }
  if (!res.ok) {
    let msg = `Error ${res.status}`;
    let detail = null;
    try {
      const j = await res.json();
      detail = j.detail ?? null;
      msg = typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || j.message || JSON.stringify(j.detail || j);
    } catch { /* not json */ }
    // a route this page knows but the running server does not = an old server that was not restarted
    if (!SERVED_BY_SERVER && path.startsWith("/api/") && (res.status === 405 || (res.status === 404 && /^not found$/i.test(msg.trim())))) {
      msg = RESTART_MSG;
    }
    // "not enough in Supply" (detail.short) is shown by the page itself as a popup, not as a toast
    const handled = !!(detail && typeof detail === "object" && Array.isArray(detail.short));
    if (!quiet && !handled && res.status !== 401) announce(msg);
    const err = new Error(msg);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  get: (p) => request("GET", p),
  getQuiet: (p) => request("GET", p, undefined, undefined, true),     // no error toast (background checks)
  post: (p, b) => request("POST", p, b ?? {}),
  put: (p, b) => request("PUT", p, b ?? {}),
  patch: (p, b) => request("PATCH", p, b ?? {}),
  del: (p) => request("DELETE", p),
  upload: (p, file) => {
    const f = new FormData();
    f.append("image", file);
    return request("POST", p, undefined, f);
  },
  // any file (a saved data ZIP, a setup file to transfer): sent as the form field "file"
  uploadFile: (p, file) => {
    const f = new FormData();
    f.append("file", file);
    return request("POST", p, undefined, f);
  },
  // used by the sign-in screen (it shows its own errors, so these are quiet)
  ping: (server) => {
    if (server) { try { localStorage.setItem("shop_api", server.replace(/\/+$/, "")); } catch { /* ignore */ } }
    return request("GET", "/api/ping", undefined, undefined, true);
  },
  // first run: choose the system password + your own account.  later: "New person" = system password + own account
  // (the username is the name shown everywhere)
  async setup(systemPassword, username, password) {
    return signedIn(await request("POST", "/api/setup", { system_password: systemPassword, username, password }, undefined, true));
  },
  async register(systemPassword, username, password) {
    return signedIn(await request("POST", "/api/register", { system_password: systemPassword, username, password }, undefined, true));
  },
  // the desktop sign-in: system password + own username and password
  async deskLogin(systemPassword, username, password) {
    return signedIn(await request("POST", "/api/desk-login", { system_password: systemPassword, username, password }, undefined, true));
  },
  changeSystemPassword: (oldPw, newPw) => request("POST", "/api/system-password", { old: oldPw, new: newPw }),
  // a streamed answer (the Business Advisor): calls onLine(obj) for every JSON line as it arrives
  async stream(path, body, onLine, signal) {
    let res;
    try {
      res = await fetch(getBase() + path, {
        method: "POST", signal,
        headers: { "Content-Type": "application/json", ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
        body: JSON.stringify(body),
      });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error(OFFLINE_MSG);
    }
    if (res.status === 401) { setToken(""); window.dispatchEvent(new Event("shop-auth-lost")); }
    if (!res.ok) {
      let msg = `Error ${res.status}`;
      try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) { try { onLine(JSON.parse(line)); } catch { /* partial */ } }
      }
    }
    if (buf.trim()) { try { onLine(JSON.parse(buf)); } catch { /* ignore */ } }
  },
  logout() {
    request("POST", "/api/logout", undefined, undefined, true).catch(() => {});
    setToken("");
    window.dispatchEvent(new Event("shop-auth-lost"));
  },
};

function signedIn(d) {
  setToken(d.token);
  try { localStorage.setItem("shop_user", d.user.username); } catch { /* ignore */ }
  return d.user;
}

// a link that downloads a file (ZIP) from the server; <a href> cannot send the header, so the token goes in the URL
export const downloadUrl = (path) => `${getBase()}${path}${path.includes("?") ? "&" : "?"}token=${getToken()}`;

// Inside TresNinosShop.exe the window (WebView2) does not show downloads like a web browser does, so every
// Download button asks there WHERE to save (a normal Windows "Save as" box) - backend/app_launcher.py SaveFiles.
export const inAppWindow = () => typeof window !== "undefined" && !!(window.pywebview && window.pywebview.api
  && window.pywebview.api.save_download);
// a green "done" toast (the red one is for errors)
export function announceSaved(message) {
  window.dispatchEvent(new CustomEvent("shop-saved", { detail: message }));
}
// Download a file from the server: the .exe asks where to save it and opens that folder afterwards;
// a web browser (npm run dev) just downloads it into its Downloads folder.
export async function saveDownload(path) {
  const link = downloadUrl(path);
  if (inAppWindow()) {
    const r = (await window.pywebview.api.save_download(link)) || {};
    if (r.ok) announceSaved(`Saved: ${r.path}`);
    else if (r.error) announce(r.error);
    return r;
  }
  const a = document.createElement("a");
  a.href = link;
  document.body.appendChild(a);
  a.click();
  a.remove();
  return { ok: true, browser: true };
}

export const currentUserName = () => {
  try { return localStorage.getItem("shop_user") || ""; } catch { return ""; }
};

// <img src> cannot send a header, so photos use ?token=   (v = cache-buster after uploading a new picture)
export const imgUrl = (path, v = 0) =>
  `${getBase()}${path}${path.includes("?") ? "&" : "?"}token=${getToken()}${v ? `&v=${v}` : ""}`;
