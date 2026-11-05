import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Check, Loader2, ChevronDown, ChevronLeft, ChevronRight, CalendarDays, Sun, ArrowRightCircle, ChevronsRight, CalendarClock, Eye, EyeOff } from "lucide-react";
import { ACCENT, CARD_SHADOW, useTheme } from "../lib/theme";
import { imgUrl, downloadUrl, inAppWindow, saveDownload } from "../lib/api";

export function Modal({ title, onClose, children, width = 480 }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(6,9,13,0.65)", backdropFilter: "blur(3px)", zIndex: 100 }}
      className="flex items-center justify-center p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ width, maxWidth: "100%", maxHeight: "82vh", background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)", boxShadow: CARD_SHADOW }}
        className="rounded-xl flex flex-col overflow-hidden">
        <div style={{ borderBottom: "1px solid var(--border)" }} className="flex items-center justify-between px-5 py-3.5 shrink-0">
          <h3 className="font-semibold text-sm">{title}</h3>
          <button onClick={onClose} style={{ color: "var(--muted)" }} className="hover:text-current"><X size={18} /></button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Avatar({ name, color, size = 72 }) {
  const initials = name.split(" ").map((p) => p[0]).slice(0, 2).join("");
  return (
    <div style={{ width: size, height: size, borderRadius: "50%", background: `radial-gradient(circle at 32% 28%, ${color}55, ${color}15)`, border: `1.5px solid ${color}90`, boxShadow: `0 0 18px -4px ${color}80`, color }}
      className="flex items-center justify-center font-semibold text-lg shrink-0">
      {initials}
    </div>
  );
}

// A person's picture (desktop account or phone login). Falls back to initials when there is no photo.
//   uid  -> /api/shop/people/<uid>/photo        src -> any other picture URL        ver -> cache-buster after an upload
export function UserAvatar({ uid, src, name = "", size = 32, ver = 0, color = ACCENT.cyan, ring = true }) {
  const url = src || (uid ? imgUrl(`/api/shop/people/${uid}/photo`, ver) : null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [url]);
  const initials = (name || "?").replace(/^@/, "").split(/[\s._-]+/).filter(Boolean).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const base = { width: size, height: size, borderRadius: "50%", flexShrink: 0, border: ring ? `1.5px solid ${color}90` : "none" };
  if (url && !failed) {
    return <img src={url} alt={name} onError={() => setFailed(true)} style={{ ...base, objectFit: "cover", background: "var(--panelAlt)" }} />;
  }
  return (
    <div style={{ ...base, background: `radial-gradient(circle at 32% 28%, ${color}55, ${color}15)`, color, fontSize: Math.max(10, size * 0.38) }}
      className="flex items-center justify-center font-semibold">
      {initials || "?"}
    </div>
  );
}

// A password box with an eye button: press it to see what you typed (press again to hide it).
// className / style are for the box (same look as the other inputs); icon = optional lucide icon on the left.
export function PasswordInput({ className = "", style, icon: Icon, ...props }) {
  const [show, setShow] = useState(false);
  return (
    <div className={`flex items-center gap-2 ${className}`} style={style}>
      {Icon && <Icon size={14} style={{ color: "var(--muted)" }} className="shrink-0" />}
      <input {...props} type={show ? "text" : "password"} className="bg-transparent outline-none w-full min-w-0"
        style={{ color: "var(--text)", fontSize: "inherit", fontFamily: show ? "inherit" : undefined }} />
      <button type="button" tabIndex={-1} onClick={() => setShow((s) => !s)}
        title={show ? "Hide the password" : "Show the password"} aria-label={show ? "Hide the password" : "Show the password"}
        className="shrink-0 p-0.5 rounded" style={{ color: show ? ACCENT.cyan : "var(--muted)" }}>
        {show ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );
}

// "Show hidden (2)" / "Hide removed": people who were removed stay in the records; this lists them again.
// Shows nothing when there is nothing hidden.
export function HiddenToggle({ on, count = 0, onChange, what = "removed" }) {
  if (!count && !on) return null;
  return (
    <button type="button" onClick={() => onChange(!on)} title={on ? `Hide the ${what} again` : `Show the ${what} people too`}
      className="text-[11px] px-2 py-1 rounded-md inline-flex items-center gap-1 shrink-0 whitespace-nowrap"
      style={{ border: `1px solid ${on ? `${ACCENT.cyan}80` : "var(--border)"}`, color: on ? ACCENT.cyan : "var(--muted)", background: on ? `${ACCENT.cyan}12` : "transparent" }}>
      {on ? <EyeOff size={12} /> : <Eye size={12} />} {on ? `Hide ${what}` : `Show hidden${count ? ` (${count})` : ""}`}
    </button>
  );
}

export function ThemedCheckbox({ checked, onChange }) {
  return (
    <button type="button" onClick={onChange} className="shrink-0 w-[18px] h-[18px] rounded-[5px] flex items-center justify-center transition-colors"
      style={{ border: `1.5px solid ${checked ? ACCENT.cyan : "var(--faint)"}`, background: checked ? `${ACCENT.cyan}22` : "transparent" }}>
      {checked && <Check size={12} strokeWidth={3} style={{ color: ACCENT.cyan }} />}
    </button>
  );
}

export function RangeSelect({ value, onChange, options }) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}
        className="appearance-none text-xs rounded-md pl-2.5 pr-7 py-1.5 outline-none cursor-pointer"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted)" }} />
    </div>
  );
}

// Top-row summary card, e.g. "Today's Sales  ₱12,450  +8.2%"
export function StatCard({ icon: Icon, label, value, tag, tagTone = "neutral" }) {
  const tones = {
    neutral: { color: "var(--muted)", bg: "var(--raised)" },
    good: { color: ACCENT.cyan, bg: `${ACCENT.cyan}18` },
    warn: { color: ACCENT.amber, bg: `${ACCENT.amber}18` },
    bad: { color: ACCENT.danger, bg: `${ACCENT.danger}18` },
  };
  const tone = tones[tagTone];
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl px-4 py-3 flex flex-col gap-1.5 min-w-0">
      <div className="flex items-center justify-between">
        <Icon size={15} style={{ color: "var(--muted)" }} />
        {tag && <span style={{ color: tone.color, background: tone.bg }} className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full">{tag}</span>}
      </div>
      <span className="text-lg font-semibold truncate">{value}</span>
      <span className="text-xs" style={{ color: "var(--muted)" }}>{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DatePicker — a button that opens a small calendar (quick picks on the left:
// Today / Tomorrow / Next Monday / In a week, a month grid, Clear / Done).
// value / onChange use "YYYY-MM-DD" ("" = no date). Rendered in a portal so a
// scrolling modal never cuts it off.
// ---------------------------------------------------------------------------
const pad2 = (n) => String(n).padStart(2, "0");
const toYmd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const fromYmd = (s) => (s ? new Date(`${s}T00:00:00`) : null);
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

export function DatePicker({ value, onChange, placeholder = "Pick a date", style }) {
  const btnRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [draft, setDraft] = useState(value || "");
  const [month, setMonth] = useState(() => { const d = fromYmd(value) || new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });

  function openIt() {
    const r = btnRef.current.getBoundingClientRect();
    const width = 530, height = 380;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const top = r.bottom + height + 8 > window.innerHeight ? Math.max(8, r.top - height - 6) : r.bottom + 6;
    setPos({ top, left });
    setDraft(value || "");
    const d = fromYmd(value) || new Date();
    setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open]);

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const plus = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };
  const nextMonday = plus(((8 - today.getDay()) % 7) || 7);
  const quick = [
    { icon: Sun, label: "Today", d: today },
    { icon: ArrowRightCircle, label: "Tomorrow", d: plus(1) },
    { icon: ChevronsRight, label: "Next Monday", d: nextMonday },
    { icon: CalendarClock, label: "In a week", d: plus(7) },
  ];
  const lead = (month.getDay() + 6) % 7;
  const first = new Date(month); first.setDate(1 - lead);
  const cells = Array.from({ length: 42 }, (_, i) => { const d = new Date(first); d.setDate(first.getDate() + i); return d; });
  const label = value ? fromYmd(value).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : placeholder;
  const pick = (d) => { setDraft(toYmd(d)); if (d.getMonth() !== month.getMonth()) setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); };
  const done = (v) => { onChange(v); setOpen(false); };
  const accent = ACCENT.cyan;

  return (
    <>
      <button ref={btnRef} type="button" onClick={openIt}
        className="w-full flex items-center justify-between gap-2 text-sm rounded-lg px-3 py-2 outline-none text-left"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: value ? "var(--text)" : "var(--faint)", ...style }}>
        <span className="truncate">{label}</span>
        <CalendarDays size={15} style={{ color: "var(--muted)" }} className="shrink-0" />
      </button>
      {open && createPortal(
        <div style={{ position: "fixed", inset: 0, zIndex: 250 }} onMouseDown={() => setOpen(false)}>
          <div onMouseDown={(e) => e.stopPropagation()}
            style={{ position: "fixed", top: pos.top, left: pos.left, width: 530, background: "#24262d", border: "1px solid #3a3d46",
              boxShadow: "0 18px 40px rgba(0,0,0,.55)", color: "#e9eaee" }}
            className="rounded-xl flex overflow-hidden">
            <div className="flex flex-col py-2" style={{ width: 196, borderRight: "1px solid #3a3d46" }}>
              {quick.map(({ icon: Icon, label: l, d }) => (
                <button key={l} type="button" onClick={() => done(toYmd(d))}
                  className="flex items-center gap-2.5 px-3 py-2 text-[13px] text-left hover:bg-white/5">
                  <Icon size={17} style={{ color: "#a9adb8" }} />
                  <span className="flex-1 whitespace-nowrap">{l}</span>
                  <span className="text-[11px]" style={{ color: "#8b8f9a" }}>{d.toLocaleDateString([], { weekday: "short" })} {d.getDate()}</span>
                </button>
              ))}
            </div>
            <div className="flex-1 p-3 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} className="p-1 rounded hover:bg-white/10"><ChevronLeft size={16} /></button>
                <span className="text-sm font-semibold">{month.toLocaleDateString([], { month: "long", year: "numeric" })}</span>
                <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} className="p-1 rounded hover:bg-white/10"><ChevronRight size={16} /></button>
              </div>
              <div className="grid grid-cols-7 gap-0.5 text-center">
                {WEEKDAYS.map((w) => <span key={w} className="text-[11px] py-1" style={{ color: "#8b8f9a" }}>{w}</span>)}
                {cells.map((d) => {
                  const ymd = toYmd(d);
                  const sel = ymd === draft;
                  const other = d.getMonth() !== month.getMonth();
                  const isToday = d.getTime() === today.getTime();
                  return (
                    <button key={ymd} type="button" onClick={() => pick(d)} onDoubleClick={() => done(ymd)}
                      className="h-8 rounded-lg text-[13px]"
                      style={{ background: sel ? accent : "transparent", color: sel ? "#0b1014" : other ? "#5d616c" : "#e9eaee",
                        fontWeight: sel ? 700 : 400, boxShadow: isToday && !sel ? `inset 0 0 0 1px ${accent}` : "none" }}>
                      {d.getDate()}
                    </button>
                  );
                })}
              </div>
              <div className="text-center text-sm font-mono rounded-md py-1.5" style={{ background: "#2d3038" }}>
                {draft ? fromYmd(draft).toLocaleDateString([], { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }) : "no date"}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => done("")} className="py-1.5 rounded-lg text-sm" style={{ border: "1px solid #4a4e59" }}>Clear</button>
                <button type="button" onClick={() => done(draft)} className="py-1.5 rounded-lg text-sm font-semibold" style={{ background: accent, color: "#0b1014" }}>Done</button>
              </div>
            </div>
          </div>
        </div>, document.body)}
    </>
  );
}

// Suggest-as-you-type text box: typing "G" lists everything whose name has a word starting with "g".
// options: [{ key, label, sub, ... }]; onPick(option) is called when one is chosen (click or Enter).
export function SuggestInput({ value, onChange, options, onPick, placeholder, className, style, max = 8 }) {
  const ref = useRef(null);
  const { T } = useTheme();
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const [rect, setRect] = useState(null);
  const q = (value || "").trim().toLowerCase();
  const list = q
    ? options.filter((o) => {
        const l = o.label.toLowerCase();
        return l.startsWith(q) || l.split(/[\s\-/]+/).some((w) => w.startsWith(q)) || (q.length > 2 && l.includes(q));
      }).slice(0, max)
    : [];
  const show = open && list.length > 0 && !(list.length === 1 && list[0].label.toLowerCase() === q);
  function measure() { if (ref.current) setRect(ref.current.getBoundingClientRect()); }
  function choose(o) { onPick(o); setOpen(false); }
  return (
    <>
      <input ref={ref} value={value} placeholder={placeholder} className={className} style={style} autoComplete="off"
        onChange={(e) => { onChange(e.target.value); setOpen(true); setHi(0); measure(); }}
        onFocus={() => { setOpen(true); measure(); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!show) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, list.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); choose(list[hi]); }
          else if (e.key === "Escape") setOpen(false);
        }} />
      {show && rect && createPortal(
        <div style={{ position: "fixed", top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 340), zIndex: 260,
          background: T.panel, border: `1px solid ${T.border}`, boxShadow: "0 12px 30px rgba(0,0,0,.45)", color: T.text }}
          className="rounded-lg py-1 max-h-64 overflow-y-auto">
          {list.map((o, i) => (
            <button key={o.key} type="button" onMouseDown={(e) => { e.preventDefault(); choose(o); }} onMouseEnter={() => setHi(i)}
              className="w-full flex flex-col px-3 py-1.5 text-left text-sm"
              style={{ background: i === hi ? `${ACCENT.cyan}22` : "transparent" }}>
              <span className="truncate font-medium">{o.label}</span>
              {o.sub && <span className="text-[11px] font-mono truncate" style={{ color: T.muted }}>{o.sub}</span>}
            </button>
          ))}
        </div>, document.body)}
    </>
  );
}

// Every Download button. In a web browser it is a plain link (the browser shows it in Downloads).
// In TresNinosShop.exe it asks where to save the file and then opens that folder (lib/api.js saveDownload).
export function DownloadLink({ path, onDone, children, className = "", style, title }) {
  const [busy, setBusy] = useState(false);
  const click = async (e) => {
    if (!inAppWindow()) { if (onDone) onDone({ ok: true, browser: true }); return; }
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const r = await saveDownload(path);
      if (r.ok && onDone) onDone(r);
    } catch { /* the toast shows it */ } finally { setBusy(false); }
  };
  return (
    <a href={downloadUrl(path)} onClick={click} title={title} style={{ ...style, opacity: busy ? 0.7 : 1, cursor: busy ? "wait" : "pointer" }}
      className={className}>
      {busy ? <><Loader2 size={14} className="animate-spin" /> Preparing…</> : children}
    </a>
  );
}
