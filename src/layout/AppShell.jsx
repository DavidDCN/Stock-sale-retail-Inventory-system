import React, { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Menu, ArrowLeft, Sun, Moon, LayoutGrid, TrendingUp, Package, Wallet, Activity, LogOut, X, CalendarDays, Landmark, ArrowLeftRight, CalendarRange, Camera, Trash2, Wifi, WifiOff, Smartphone, ScanLine, RefreshCw } from "lucide-react";
import { ThemeProvider, useTheme, ACCENT } from "../lib/theme";
import AuthGate from "../lib/AuthGate";
import { api, hasToken, PACKAGED, SERVER_VERSION } from "../lib/api";
import { Modal, UserAvatar } from "../components/kit";

// Every failed server call shows here, ABOVE any open modal (z-index 300 > modal 100).
function ErrorToast() {
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);
  useEffect(() => {
    let t;
    const show = (good) => (e) => {
      setOk(good); setMsg(String(e.detail || "Something went wrong")); clearTimeout(t); t = setTimeout(() => setMsg(""), good ? 9000 : 7000);
    };
    const bad = show(false), saved = show(true);
    window.addEventListener("shop-error", bad);
    window.addEventListener("shop-saved", saved);          // "Saved: C:\...\file.zip" after a download in the .exe
    return () => { window.removeEventListener("shop-error", bad); window.removeEventListener("shop-saved", saved); clearTimeout(t); };
  }, []);
  if (!msg) return null;
  const tone = ok ? ACCENT.cyan : ACCENT.danger;
  return (
    <div style={{ position: "fixed", bottom: 20, left: "50%", transform: "translateX(-50%)", zIndex: 300, maxWidth: "min(560px, calc(100vw - 32px))",
      background: "var(--panel)", border: `1px solid ${tone}`, color: "var(--text)", boxShadow: "0 10px 30px rgba(0,0,0,.45)" }}
      className="rounded-lg px-4 py-2.5 flex items-start gap-3 text-sm">
      <span className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: tone }} />
      <span className="flex-1 min-w-0 break-words">{msg}</span>
      <button onClick={() => setMsg("")} style={{ color: "var(--muted)" }} className="shrink-0"><X size={15} /></button>
    </div>
  );
}

// Sidebar "SYSTEM STATUS": asks the server every 20 s (no login needed for /api/ping)
function useServerStatus() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.ping().then((p) => alive && setSt({ ...p, at: new Date() })).catch(() => alive && setSt({ offline: true, at: new Date() }));
    load();
    const t = setInterval(load, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return st;
}

// the signed-in desktop person (sidebar card + top bar). Reloads after a photo change ("shop-profile-changed").
function useMe(signedIn) {
  const [me, setMe] = useState(null);
  useEffect(() => {
    if (!signedIn) { setMe(null); return undefined; }
    const load = () => api.get("/api/shop/me").then(setMe).catch(() => {});
    load();
    window.addEventListener("shop-profile-changed", load);
    const t = setInterval(load, 60000);
    return () => { window.removeEventListener("shop-profile-changed", load); clearInterval(t); };
  }, [signedIn]);
  return me;
}

// Can phones reach this laptop? (Windows treats a new Wi-Fi as "Public" and then blocks them.) Checked every minute.
function useNetwork(signedIn) {
  const [net, setNet] = useState(null);
  useEffect(() => {
    if (!signedIn) { setNet(null); return undefined; }
    const load = () => api.getQuiet("/api/shop/network").then(setNet).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    window.addEventListener("shop-network-changed", load);
    return () => { clearInterval(t); window.removeEventListener("shop-network-changed", load); };
  }, [signedIn]);
  return net;
}

async function fixNetwork(setBusy, setMsg) {
  setBusy(true); setMsg("");
  try {
    await api.post("/api/shop/network/fix");
    setMsg("Done: phones can connect now. Open the shop app on the phone and sign in again.");
  } catch (e) {
    setMsg(e.message);
  } finally {
    setBusy(false);
    window.dispatchEvent(new Event("shop-network-changed"));
  }
}

function NetworkBanner({ net }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const problem = net?.problems?.[0];
  if (!problem && !msg) return null;
  const ok = msg.startsWith("Done");
  return (
    <div className="mx-6 mt-4 rounded-xl px-4 py-3 flex items-center gap-3 text-sm"
      style={{ background: ok ? "#22c55e14" : `${ACCENT.amber}14`, border: `1px solid ${ok ? "#22c55e66" : `${ACCENT.amber}66`}` }}>
      {ok ? <Wifi size={18} style={{ color: "#22c55e" }} className="shrink-0" /> : <WifiOff size={18} style={{ color: ACCENT.amber }} className="shrink-0" />}
      <div className="flex-1 min-w-0">
        {msg ? <span style={{ color: ok ? "#22c55e" : ACCENT.danger }}>{msg}</span> : (
          <>
            <b>Phones cannot connect to this laptop.</b>{" "}
            <span style={{ color: "var(--muted)" }}>
              {problem.text} Press Fix it, then press <b>Yes</b> on the Windows question.
            </span>
          </>
        )}
      </div>
      {!ok && problem && (
        <button onClick={() => fixNetwork(setBusy, setMsg)} disabled={busy} style={{ background: ACCENT.amber }}
          className="shrink-0 text-xs font-semibold text-black/85 px-3 py-2 rounded-lg disabled:opacity-60">
          {busy ? "Waiting for Windows…" : "Fix it"}
        </button>
      )}
      {msg && <button onClick={() => setMsg("")} style={{ color: "var(--muted)" }} className="shrink-0"><X size={15} /></button>}
    </div>
  );
}

// The server window runs an older version than this page (it was not restarted after an update). Checked with every
// /api/ping (20 s), so it goes away by itself after the restart.
function VersionBanner({ status }) {
  if (PACKAGED || !status || status.offline || status.version === SERVER_VERSION) return null;
  return (
    <div className="mx-6 mt-4 rounded-xl px-4 py-3 flex items-start gap-3 text-sm"
      style={{ background: `${ACCENT.danger}14`, border: `1px solid ${ACCENT.danger}66` }}>
      <RefreshCw size={18} style={{ color: ACCENT.danger }} className="shrink-0 mt-0.5" />
      <div className="min-w-0">
        <b>Restart the shop server.</b>{" "}
        <span style={{ color: "var(--muted)" }}>
          The server window is still running an older version{status.version ? ` (${status.version})` : ""} than this page ({SERVER_VERSION}),
          so new buttons answer “Method Not Allowed”. Click the window where <code>python server.py serve</code> runs, press <b>Ctrl+C</b>,
          then run <code>python server.py serve</code> again. This message goes away by itself.
        </span>
      </div>
    </div>
  );
}

// shown ONLY when the receipt reader is not working (phones then see "waiting" on their photos)
function ReaderBanner({ status }) {
  if (!status || status.offline || status.ready) return null;
  if (!status.error && !(status.startingFor > 20)) return null;
  const mins = Math.max(1, Math.round((status.startingFor || 0) / 60));
  return (
    <div className="mx-6 mt-4 rounded-xl px-4 py-3 flex items-start gap-3 text-sm"
      style={{ background: `${status.error ? ACCENT.danger : ACCENT.amber}14`, border: `1px solid ${status.error ? ACCENT.danger : ACCENT.amber}66` }}>
      <ScanLine size={18} style={{ color: status.error ? ACCENT.danger : ACCENT.amber }} className="shrink-0 mt-0.5" />
      <div className="min-w-0">
        {status.error ? (
          <><b>The receipt reader could not start.</b> <span style={{ color: "var(--muted)" }}>{status.error} — phone photos wait until it works.</span></>
        ) : (
          <>
            <b>The receipt reader is still starting ({mins} min).</b>{" "}
            <span style={{ color: "var(--muted)" }}>
              {status.bundledModels
                ? "On a slower laptop the first start takes a little longer. Phones show “waiting” until it is ready."
                : "On a new laptop the first start downloads the reading model once (about 100–200 MB): keep the internet on. Phones show “waiting” until it is ready."}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// The phones' own Wi-Fi made by this laptop (Windows Mobile Hotspot): for when the shop Wi-Fi (an extender...) does not
// let the phones reach the laptop. Phones join it once; it turns on by itself every time the program opens.
function PhoneWifi() {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [showPw, setShowPw] = useState(false);
  const load = () => api.getQuiet("/api/shop/hotspot").then(setSt).catch(() => setSt({ windows: false }));
  useEffect(() => { load(); }, []);
  if (!st || !st.windows) return null;
  async function sw(on) {
    setBusy(true); setMsg("");
    try {
      const r = await api.post("/api/shop/hotspot", { on, auto: on });
      setMsg(on ? `On. Connect each phone once to “${r.ssid}”.` : "Turned off.");
      window.dispatchEvent(new Event("shop-network-changed"));
    } catch (e) { setMsg(e.message); } finally { setBusy(false); load(); }
  }
  const on = st.on;
  return (
    <div className="w-full rounded-lg px-3 py-2.5 flex flex-col gap-1.5 text-xs" style={{ background: "var(--panelAlt)", border: `1px solid ${on ? `${ACCENT.cyan}70` : "var(--border)"}` }}>
      <div className="flex items-center gap-1.5 font-semibold">
        <Wifi size={13} style={{ color: on ? ACCENT.cyan : "var(--muted)" }} /> Phones' own Wi-Fi (from this laptop)
        <span className="ml-auto text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
          style={on ? { background: ACCENT.cyan, color: "#000" } : { color: "var(--muted)", border: "1px solid var(--border)" }}>{on ? "ON" : "OFF"}</span>
      </div>
      <span className="text-[11px] leading-relaxed" style={{ color: "var(--muted)" }}>
        Use this when the phones cannot reach the laptop on the shop Wi-Fi (for example a TP-Link extender). The laptop makes its own
        Wi-Fi; the phones still get internet through it.
      </span>
      {st.supported === false && !on && <span style={{ color: ACCENT.amber }}>{st.error || "This laptop cannot make a Mobile Hotspot."}</span>}
      <div className="rounded-md px-2.5 py-2 flex flex-col gap-0.5" style={{ border: "1px solid var(--border)" }}>
        <span style={{ color: "var(--muted)" }}>Wi-Fi name: <b className="font-mono select-all" style={{ color: "var(--text)" }}>{st.ssid}</b></span>
        <span style={{ color: "var(--muted)" }}>Password: <b className="font-mono select-all" style={{ color: "var(--text)" }}>{showPw ? st.password : "••••••••••"}</b>
          <button onClick={() => setShowPw((v) => !v)} className="ml-2 underline" style={{ color: ACCENT.cyan }}>{showPw ? "hide" : "show"}</button></span>
        {on && <span style={{ color: "var(--muted)" }}>Phones on it now: <b style={{ color: "var(--text)" }}>{st.clients}</b>{st.max ? ` of ${st.max}` : ""}</span>}
      </div>
      <button onClick={() => sw(!on)} disabled={busy}
        style={on ? { border: "1px solid var(--border)", color: "var(--muted)" } : { background: ACCENT.cyan, color: "#000" }}
        className="self-start text-[11px] font-semibold px-2.5 py-1.5 rounded-md disabled:opacity-60">
        {busy ? "Waiting for Windows…" : on ? "Turn off" : "Turn on (and every time the program opens)"}
      </button>
      {msg && <span style={{ color: msg.startsWith("On") || msg.startsWith("Turned") ? "#22c55e" : ACCENT.danger }}>{msg}</span>}
      <span className="text-[10px] leading-relaxed" style={{ color: "var(--faint)" }}>
        Each phone, once: Settings → Wi-Fi → “{st.ssid}” → type the password → Connect (it then joins by itself). The shop app finds the
        laptop by itself. The first time you turn it on, Windows asks for permission: press Yes.
      </span>
    </div>
  );
}

function PhoneConnection({ net }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!net) return null;
  const pub = net.problems?.length > 0;
  return (
    <div className="w-full rounded-lg px-3 py-2.5 flex flex-col gap-1.5 text-xs" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-1.5 font-semibold"><Smartphone size={13} style={{ color: ACCENT.cyan }} /> Phones connect to this laptop at</div>
      {net.addresses?.length ? net.addresses.map((a) => (
        <code key={a} className="font-mono text-[13px] select-all" style={{ color: ACCENT.cyan, background: "transparent", padding: 0 }}>{a}:{net.port}</code>
      )) : <span style={{ color: ACCENT.danger }}>No Wi-Fi connection found on this laptop.</span>}
      {net.networks?.map((n) => (
        <div key={n.alias} className="flex items-center gap-1.5" style={{ color: "var(--muted)" }}>
          {n.category === "Public" ? <WifiOff size={12} style={{ color: ACCENT.amber }} /> : <Wifi size={12} style={{ color: "#22c55e" }} />}
          <span className="truncate">{n.name}</span>
          <span className="ml-auto font-semibold" style={{ color: n.category === "Public" ? ACCENT.amber : "#22c55e" }}>{n.category}</span>
        </div>
      ))}
      {pub && net.problems.map((p, i) => <span key={i} style={{ color: ACCENT.amber }}>{p.text}</span>)}
      {net.windows && !pub && net.firewall && (
        <span style={{ color: "#22c55e" }}>Windows lets phones in on this Wi-Fi.</span>
      )}
      {net.windows && !pub && net.firewallUnknown && (
        <span style={{ color: ACCENT.amber }}>Could not read the Windows Firewall. If phones cannot connect, press the button below.</span>
      )}
      {net.windows && (
        <button onClick={() => fixNetwork(setBusy, setMsg)} disabled={busy}
          style={pub || net.firewallUnknown ? { background: ACCENT.amber } : { border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
          className={`self-start text-[11px] font-semibold px-2.5 py-1.5 rounded-md disabled:opacity-60 ${pub || net.firewallUnknown ? "text-black/85" : ""}`}
          title="Sets this Wi-Fi to Private and allows TresNinosShop through Windows Firewall (Windows asks: press Yes)">
          {busy ? "Waiting for Windows…" : pub || net.firewallUnknown ? "Fix it: let phones connect" : "Phones still not connecting? Allow them again"}
        </button>
      )}
      {msg && <span style={{ color: msg.startsWith("Done") ? "#22c55e" : ACCENT.danger }}>{msg}</span>}
      <span className="text-[10px] leading-relaxed" style={{ color: "var(--faint)" }}>
        Still not connecting? The phone must be on the same Wi-Fi as this laptop (not a Guest network, not mobile data),
        and the router's “AP / client isolation” must be off. An antivirus with its own firewall (Avast, Kaspersky, ESET…)
        must allow TresNinosShop too. In the shop app you can also type the address above in “PC address (only if it is not found)”.
      </span>
    </div>
  );
}

function ProfileModal({ me, net, onClose }) {
  const fileRef = React.useRef(null);
  const [busy, setBusy] = useState(false);
  async function upload(file) {
    if (!file) return;
    setBusy(true);
    try { await api.upload("/api/shop/me/photo", file); window.dispatchEvent(new Event("shop-profile-changed")); } catch { /* toast */ } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await api.del("/api/shop/me/photo"); window.dispatchEvent(new Event("shop-profile-changed")); } catch { /* toast */ } finally { setBusy(false); }
  }
  return (
    <Modal title="Your profile" onClose={onClose} width={380}>
      <div className="flex flex-col items-center gap-3 pb-1">
        <button onClick={() => fileRef.current?.click()} className="relative group" title="Change photo" disabled={busy}>
          <UserAvatar uid={me.id} ver={me.photoVer} name={me.username} size={104} />
          <span className="absolute inset-0 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
            style={{ background: "rgba(0,0,0,.45)", color: "#fff" }}><Camera size={22} /></span>
        </button>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        <div className="text-center">
          <div className="text-lg font-semibold">{me.username}</div>
          <div className="text-xs" style={{ color: "var(--muted)" }}>{me.since ? `In charge today since ${me.since}` : "Desktop account"}</div>
        </div>
        <div className="flex gap-2">
          <button onClick={() => fileRef.current?.click()} disabled={busy} style={{ background: ACCENT.cyan }}
            className="text-xs font-semibold text-black/85 px-3 py-2 rounded-lg flex items-center gap-1.5 disabled:opacity-50">
            <Camera size={13} /> {me.anyPhoto ? "Change photo" : "Add a photo"}
          </button>
          {me.hasPhoto && (
            <button onClick={remove} disabled={busy} className="text-xs px-3 py-2 rounded-lg flex items-center gap-1.5" style={{ border: "1px solid var(--border)", color: "var(--muted)" }}>
              <Trash2 size={13} /> Remove
            </button>
          )}
        </div>
        <p className="text-[11px] text-center" style={{ color: "var(--faint)" }}>Your photo shows on the sign-in screen, the top bar and the Calendar.</p>
        <PhoneConnection net={net} />
        <PhoneWifi />
        <button onClick={() => { onClose(); api.logout(); }} className="w-full text-xs py-2 rounded-lg flex items-center justify-center gap-1.5"
          style={{ border: `1px solid ${ACCENT.danger}55`, color: ACCENT.danger }}>
          <LogOut size={13} /> Sign out
        </button>
      </div>
    </Modal>
  );
}

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutGrid, end: true },
  { to: "/sales", label: "Sales", icon: TrendingUp },
  { to: "/supply", label: "Supply", icon: Package },
  { to: "/cashflow", label: "Cash Flow", icon: Wallet },
  { to: "/progression", label: "Progression", icon: Activity },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/payments", label: "Payments", icon: Landmark },
  { to: "/transfer", label: "Transfer", icon: ArrowLeftRight },
  { to: "/planner", label: "Planner", icon: CalendarRange },
];

function ShellInner() {
  const { theme, toggleTheme, T, cssVars } = useTheme();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const onDashboard = location.pathname === "/";
  const status = useServerStatus();
  const [signedIn, setSignedIn] = useState(hasToken());
  const [profileOpen, setProfileOpen] = useState(false);
  useEffect(() => {
    const lost = () => setSignedIn(false);
    const t = setInterval(() => setSignedIn(hasToken()), 1500);
    window.addEventListener("shop-auth-lost", lost);
    return () => { window.removeEventListener("shop-auth-lost", lost); clearInterval(t); };
  }, []);
  const me = useMe(signedIn);
  const net = useNetwork(signedIn && !!me);
  const offline = !!status?.offline;

  return (
    <div style={{ ...cssVars, background: "var(--bg)", color: "var(--text)" }} className="h-screen font-sans flex flex-col">
      {/* topbar */}
      <div style={{ borderBottom: "1px solid var(--border)", background: "var(--panel)" }} className="flex items-center justify-between px-4 py-3 shrink-0">
        <div className="flex items-center gap-3">
          {!onDashboard ? (
            <button onClick={() => navigate("/")} style={{ color: "var(--text)" }} className="p-1.5 rounded-md hover:bg-white/5">
              <ArrowLeft size={20} />
            </button>
          ) : (
            <button onClick={() => setSidebarOpen((s) => !s)} style={{ color: "var(--text)" }} className="p-1.5 rounded-md hover:bg-white/5">
              <Menu size={20} />
            </button>
          )}
          <button onClick={() => setSidebarOpen((s) => !s)} className="flex items-center gap-2.5">
            <img src="/logo.png" alt="Tres Niños Hardware" style={{ width: 36, height: 36, borderRadius: "50%" }}
              onError={(e) => { e.currentTarget.style.display = "none"; }} />
          </button>
          <span className="font-semibold text-[15px] tracking-wide hidden sm:inline">Tres Niños Hardware</span>
        </div>

        <div className="flex items-center gap-2">
          <button onClick={toggleTheme} style={{ border: "1px solid var(--border)", color: "var(--text)" }} className="p-1.5 rounded-md hover:bg-white/5">
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          {signedIn && me && (
            <button onClick={() => setProfileOpen(true)} title="Your profile"
              style={{ border: "1px solid var(--border)", color: "var(--text)" }} className="flex items-center gap-2 pl-1 pr-2.5 py-1 rounded-full hover:bg-white/5">
              <UserAvatar uid={me.id} ver={me.photoVer} name={me.username} size={24} />
              <span className="text-xs font-medium hidden sm:inline">{me.username}</span>
            </button>
          )}
          {signedIn && (
            <button onClick={() => api.logout()} title={`Sign out${me ? ` (${me.username})` : ""}`}
              style={{ border: "1px solid var(--border)", color: "var(--text)" }} className="p-1.5 rounded-md hover:bg-white/5">
              <LogOut size={16} />
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* sidebar */}
        <div style={{ width: sidebarOpen ? 210 : 0, borderRight: sidebarOpen ? "1px solid var(--border)" : "none", background: "var(--panel)", transition: "width .22s ease" }}
          className="overflow-hidden shrink-0 flex flex-col">
          <div style={{ width: 210 }} className="flex-1 py-4">
            <p className="px-6 mb-2 text-[10px] font-semibold tracking-widest" style={{ color: "var(--faint)" }}>MENU</p>
            <nav className="flex flex-col">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <div key={item.to} className="px-3 pb-2.5">
                    <NavLink to={item.to} end={item.end}
                      className="relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
                      style={({ isActive }) => ({
                        background: isActive ? "var(--raised)" : "transparent",
                        color: isActive ? ACCENT.cyan : "var(--muted)",
                        border: isActive ? `1px solid ${ACCENT.cyan}40` : "1px solid transparent",
                      })}>
                      {({ isActive }) => (
                        <>
                          <Icon size={17} />
                          <span>{item.label}</span>
                          {isActive && (
                            <span className="absolute left-[10%] right-[10%] -bottom-[6px] h-[3px] rounded-full"
                              style={{ background: ACCENT.cyan, boxShadow: `0 0 10px 1px ${ACCENT.cyan}, 0 5px 10px -2px ${ACCENT.cyan}` }} />
                          )}
                        </>
                      )}
                    </NavLink>
                  </div>
                );
              })}
            </nav>
          </div>
          <div style={{ width: 210, borderTop: "1px solid var(--border)" }} className="px-3 py-3">
            {signedIn && me ? (
              <button onClick={() => setProfileOpen(true)} title="Your profile — click to change your photo"
                className="w-full flex items-center gap-2.5 px-2 py-2 rounded-xl text-left hover:bg-white/5 transition-colors"
                style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }}>
                <UserAvatar uid={me.id} ver={me.photoVer} name={me.username} size={38} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold truncate">{me.username}</span>
                  <span className="block text-[10px] truncate" style={{ color: offline ? ACCENT.danger : "var(--muted)" }}>
                    {offline ? "Not connected — reconnecting…" : me.since ? `In charge since ${me.since}` : "Signed in"}
                  </span>
                </span>
              </button>
            ) : (
              <div className="px-2 py-1">
                <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Not signed in</p>
                {offline && <p className="text-[10px]" style={{ color: ACCENT.danger }}>Not connected — reconnecting…</p>}
              </div>
            )}
          </div>
        </div>

        {/* routed page content */}
        <div className="flex-1 min-w-0 overflow-y-auto">
          <VersionBanner status={status} />
          <AuthGate>{signedIn && <ReaderBanner status={status} />}{signedIn && <NetworkBanner net={net} />}<Outlet /></AuthGate>
        </div>
      </div>
      <ErrorToast />
      {profileOpen && me && <ProfileModal me={me} net={net} onClose={() => setProfileOpen(false)} />}
    </div>
  );
}

export default function AppShell() {
  return (
    <ThemeProvider>
      <ShellInner />
    </ThemeProvider>
  );
}