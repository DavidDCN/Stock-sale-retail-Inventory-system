import React, { useEffect, useState } from "react";
import { Lock, User as UserIcon, Server, KeyRound, UserPlus, BadgeCheck, Eye, EyeOff, ChevronDown } from "lucide-react";
import { ACCENT } from "../lib/theme";
import { api, getBase, hasToken, PACKAGED } from "../lib/api";
import { UserAvatar } from "../components/kit";

// The desktop opens only for a signed-in DESKTOP account, on the shop laptop itself.
//   first run  -> choose the SYSTEM PASSWORD and make your own account
//   sign in    -> the system password + your username + password
//   new person -> whoever knows the system password makes their own username + password (e.g. a relative
//                 minding the shop while the owners are away). The username is the name shown everywhere (Calendar).
const box = { background: "var(--panelAlt)", border: "1px solid var(--border)" };

function Input({ icon: Icon, secret, ...props }) {
  const [show, setShow] = useState(false);
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={box}>
      <Icon size={15} style={{ color: "var(--muted)" }} className="shrink-0" />
      <input {...props} type={secret && !show ? "password" : "text"}
        className="bg-transparent outline-none text-sm w-full" style={{ color: "var(--text)" }} />
      {secret && (
        <button type="button" tabIndex={-1} onClick={() => setShow((s) => !s)}
          title={show ? "Hide password" : "Show password"} aria-label={show ? "Hide password" : "Show password"}
          className="shrink-0 p-0.5 rounded" style={{ color: show ? ACCENT.cyan : "var(--muted)" }}>
          {show ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      )}
    </div>
  );
}

export default function AuthGate({ children }) {
  const [authed, setAuthed] = useState(hasToken());
  const [server, setServer] = useState(getBase());
  const [showServer, setShowServer] = useState(false);
  const [mode, setMode] = useState("checking");          // checking | offline | setup | login | register
  const [sysPw, setSysPw] = useState("");
  const [sysPw2, setSysPw2] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [face, setFace] = useState("");                  // the typed username's picture (sign in)
  useEffect(() => {
    const u = username.trim();
    if (mode !== "login" || !u) { setFace(""); return undefined; }
    const t = setTimeout(() => setFace(`${getBase()}/api/desk-avatar/${encodeURIComponent(u)}?t=${Date.now()}`), 350);
    return () => clearTimeout(t);
  }, [username, mode]);

  useEffect(() => {
    const lost = () => setAuthed(false);
    window.addEventListener("shop-auth-lost", lost);
    return () => window.removeEventListener("shop-auth-lost", lost);
  }, []);

  async function check(addr) {
    setMode("checking"); setError("");
    try {
      const p = await api.ping(addr);
      setMode(p.needs_setup ? "setup" : "login");
    } catch (e) {
      setMode("offline"); setError(e.message);
    }
  }
  useEffect(() => { if (!authed) check(); /* eslint-disable-next-line */ }, [authed]);
  // offline: keep trying by itself every 3 s, so the screen recovers without a click
  useEffect(() => {
    if (mode !== "offline") return undefined;
    const t = setInterval(() => { api.ping(showServer ? server : undefined).then((p) => setMode(p.needs_setup ? "setup" : "login")).catch(() => {}); }, 3000);
    return () => clearInterval(t);
  }, [mode, showServer, server]);

  function switchTo(m) {
    setMode(m); setError(""); setPassword(""); setPassword2(""); setSysPw(""); setSysPw2("");
  }

  async function submit() {
    setBusy(true); setError("");
    try {
      if (mode === "setup") {
        if (sysPw.length < 4) throw new Error("The system password needs at least 4 characters.");
        if (sysPw !== sysPw2) throw new Error("The two system passwords are not the same.");
        await api.setup(sysPw, username.trim(), password);
      } else if (mode === "register") {
        if (password !== password2) throw new Error("The two passwords are not the same.");
        await api.register(sysPw, username.trim(), password);
      } else {
        await api.deskLogin(sysPw, username.trim(), password);
      }
      setPassword(""); setPassword2(""); setSysPw(""); setSysPw2("");
      setAuthed(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (authed) return children;
  const enter = (e) => { if (e.key === "Enter" && canSubmit && !busy) submit(); };
  const canSubmit = mode === "login"
    ? sysPw && username.trim() && password
    : mode === "setup"
      ? sysPw && sysPw2 && username.trim() && password.length >= 4
      : sysPw && username.trim() && password.length >= 4 && password2;
  const title = { setup: "First run: set the system password", register: "New person", login: "Sign in", offline: "Shop server", checking: "Connecting…" }[mode];
  const TitleIcon = { setup: BadgeCheck, register: UserPlus }[mode];

  return (
    <div className="h-full flex items-center justify-center p-6">
      <div style={{ border: "1px solid var(--border)", background: "var(--panel)" }} className="rounded-xl p-6 w-[380px] max-w-full flex flex-col gap-3">
        {(mode === "login" || mode === "register" || mode === "setup") && (
          <div className="flex flex-col items-center gap-1.5 pb-1">
            <UserAvatar key={face || username} src={face || undefined} name={username.trim() || "TN"} size={76}
              color={username.trim() ? ACCENT.cyan : ACCENT.copper} />
            <span className="text-sm font-semibold h-5" style={{ color: username.trim() ? "var(--text)" : "var(--faint)" }}>
              {username.trim() || "Tres Niños Hardware"}
            </span>
          </div>
        )}
        <h2 className="text-lg font-semibold flex items-center gap-2">
          {TitleIcon && <TitleIcon size={18} style={{ color: ACCENT.cyan }} />}{title}
        </h2>
        <p className="text-xs leading-relaxed" style={{ color: "var(--muted)" }}>
          {mode === "setup" && "Choose the shop's system password. Anyone who knows it can later make their own account here (for example while you are on vacation). Then make your own account. Already have a desktop account? Type its username and password to keep it."}
          {mode === "register" && "Type the system password, then choose your own username and password. Your username is the name shown in the Calendar as the person in charge."}
          {mode === "login" && "Type the shop's system password, then your own username and password."}
          {mode === "offline" && (PACKAGED ? "The shop program is starting or not answering. This screen reconnects by itself." : "The shop server is not answering.")}
        </p>

        {mode === "offline" && PACKAGED && (
          <>
            <p className="text-[11px] leading-relaxed rounded-lg px-3 py-2" style={{ ...box, color: "var(--muted)" }}>
              If this stays for more than a minute, close this window and open <b style={{ color: "var(--text)" }}>TresNinosShop</b> again.
            </p>
            <button onClick={() => check()} style={{ background: ACCENT.cyan }} className="py-2 rounded-lg text-sm font-semibold text-black/85">Try again</button>
          </>
        )}
        {mode === "offline" && !PACKAGED && (
          <>
            <p className="text-[11px] leading-relaxed rounded-lg px-3 py-2" style={{ ...box, color: "var(--muted)" }}>
              Open <b style={{ color: "var(--text)" }}>TresNinosShop.exe</b>, or start the server in a terminal:<br />
              <code style={{ color: ACCENT.cyan }}>cd backend</code><br />
              <code style={{ color: ACCENT.cyan }}>python server.py serve</code>
            </p>
            <button onClick={() => setShowServer((s) => !s)} className="text-[11px] flex items-center gap-1 self-start" style={{ color: "var(--muted)" }}>
              <ChevronDown size={12} style={{ transform: showServer ? "rotate(180deg)" : "none" }} /> Server address (advanced)
            </button>
            {showServer && (
              <Input icon={Server} value={server} onChange={(e) => setServer(e.target.value)} title="Shop server address" />
            )}
            <button onClick={() => check(showServer ? server : undefined)} style={{ background: ACCENT.cyan }} className="py-2 rounded-lg text-sm font-semibold text-black/85">Try again</button>
          </>
        )}

        {(mode === "setup" || mode === "register" || mode === "login") && (
          <div className="flex flex-col gap-2 rounded-lg p-2.5" style={{ border: `1px dashed ${ACCENT.cyan}70` }}>
            <span className="text-[10px] font-semibold tracking-wide" style={{ color: ACCENT.cyan }}>SYSTEM PASSWORD</span>
            <Input icon={KeyRound} secret value={sysPw} onChange={(e) => setSysPw(e.target.value)} onKeyDown={enter}
              placeholder={mode === "setup" ? "new system password (4+ characters)" : "system password"} autoFocus />
            {mode === "setup" && (
              <Input icon={KeyRound} secret value={sysPw2} onChange={(e) => setSysPw2(e.target.value)} onKeyDown={enter}
                placeholder="type the system password again" />
            )}
          </div>
        )}

        {mode !== "offline" && mode !== "checking" && (
          <>
            <Input icon={UserIcon} value={username} onChange={(e) => setUsername(e.target.value)} onKeyDown={enter}
              placeholder={mode === "login" ? "username" : "choose a username (this is your name)"} />
            <Input icon={Lock} secret value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={enter}
              placeholder={mode === "login" ? "password" : "choose a password (4+ characters)"} />
            {mode === "register" && (
              <Input icon={Lock} secret value={password2} onChange={(e) => setPassword2(e.target.value)} onKeyDown={enter}
                placeholder="type your password again" />
            )}
            <button onClick={submit} disabled={busy || !canSubmit} style={{ background: ACCENT.cyan }}
              className="py-2 rounded-lg text-sm font-semibold text-black/85 disabled:opacity-50">
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : mode === "setup" ? "Save and sign in" : "Create my account"}
            </button>
            {mode === "login" && (
              <button onClick={() => switchTo("register")} className="text-xs flex items-center justify-center gap-1.5 py-1" style={{ color: ACCENT.cyan }}>
                <UserPlus size={13} /> New person? Make your own account
              </button>
            )}
            {mode === "register" && (
              <button onClick={() => switchTo("login")} className="text-xs py-1" style={{ color: "var(--muted)" }}>
                I already have an account — sign in
              </button>
            )}
          </>
        )}
        {error && mode !== "offline" && <span className="text-xs" style={{ color: ACCENT.danger }}>{error}</span>}
      </div>
    </div>
  );
}
