import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Upload, UserPlus, Clock, ReceiptText, ClipboardCheck, Plus, X, Pencil, ListChecks, Search, Check, CalendarCheck, RotateCcw, Wallet } from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal, ThemedCheckbox, DatePicker, SuggestInput, HiddenToggle } from "./kit";
import SettleModal from "./settle";
import { api, imgUrl } from "../lib/api";

// ---------------------------------------------------------------------------
// Dashboard -> Employee Attendance card. Same card, same space; the tabs on the top left switch what is inside:
//   Attendance  check in / out (the default, as before)
//   Temporary   add extra help hired for a few days (job title from the list, or type another)
//   General     the whole month at once: Apply to all, uncheck who was absent, Save (a normal employee marked temporary: Cash Flow + payslip see it)
//   Overtime    overtime / undertime hours for one day: everyone present at once ("apply to all") or only the
//               people you search and add, each with their own hours. Cash Flow's Overtime / Undertime follow.
//   Payslip     each person's pay for a period from the day records; Edit fixes one day, Details lists every day
// Data: /api/shop/attendance, /employees/temporary, /work-hours, /payslip, /payslip/day
// ---------------------------------------------------------------------------

const PALETTE = [ACCENT.cyan, ACCENT.copper, ACCENT.amber, ACCENT.danger];
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localToday = () => ymd(new Date());
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const niceDay = (s) => { if (!s) return ""; const d = new Date(`${s}T00:00:00`); return `${WD[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}`; };
const peso = (n) => `${n < 0 ? "-" : ""}₱${Math.abs(Math.round((Number(n) || 0) * 100) / 100).toLocaleString()}`;
const hrs = (h) => `${Number(h || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}h`;
const inputCls = "text-sm rounded-lg px-2.5 py-1.5 outline-none";
const DEFAULT_POSITIONS = ["Helper", "Labor", "Sales Person", "Driver", "Backhoe Operator"];
const inputSt = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
const muted = { color: "var(--muted)" };
const TABS = [
  ["attendance", "Attendance", ClipboardCheck],
  ["temporary", "Temporary", UserPlus],
  ["general", "General", CalendarCheck],
  ["overtime", "Overtime", Clock],
  ["payslip", "Payslip", ReceiptText],
];

export const staffRows = (d) => d.staff.map((s, i) => ({
  id: s.employeeId, name: s.name, role: s.role, color: PALETTE[i % PALETTE.length], checked: s.checked, time: s.time,
  hasPhoto: s.hasPhoto, temporary: s.temporary, perDayRate: s.perDayRate, ratePerHour: s.ratePerHour, ot: s.ot, ut: s.ut,
}));

// small round face for the lists (photo from Cash Flow, or initials)
export function StaffFace({ a, ver, size = 26 }) {
  const [broken, setBroken] = useState(false);
  const color = a.color || ACCENT.cyan;
  if (a.hasPhoto && !broken) {
    return <img src={imgUrl(`/api/shop/employees/${a.id}/photo`, ver)} alt="" onError={() => setBroken(true)}
      style={{ width: size, height: size, borderRadius: "50%", objectFit: "cover", border: `1px solid ${color}70` }} className="shrink-0" />;
  }
  return (
    <div style={{ width: size, height: size, borderRadius: "50%", background: `${color}22`, border: `1px solid ${color}70`, color }}
      className="flex items-center justify-center text-[10px] font-semibold shrink-0">
      {a.name.split(" ").map((p) => p[0]).slice(0, 2).join("")}
    </div>
  );
}

function TempBadge() {
  return <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0" style={{ color: ACCENT.amber, background: `${ACCENT.amber}1f` }}>TEMP</span>;
}

// a person who was removed (Done / left): shown only with "Show hidden"
function RemovedBadge() {
  return <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0" style={{ color: "var(--muted)", background: "var(--raised)", border: "1px solid var(--border)" }}>REMOVED</span>;
}

// ---------------- Temporary ----------------
function TemporaryTab({ staff, onRows, ver }) {
  const [f, setF] = useState({ name: "", role: "Helper", perDayRate: "", ratePerHour: "" });
  const [busy, setBusy] = useState(false);
  const [positions, setPositions] = useState(DEFAULT_POSITIONS);
  const [typing, setTyping] = useState(false);         // "Type another job…" chosen in the drop-down
  useEffect(() => {
    api.get("/api/shop/cashflow").then((d) => setPositions([...new Set([...DEFAULT_POSITIONS, ...(d.positions || [])])])).catch(() => {});
  }, []);
  const temps = staff.filter((a) => a.temporary);
  const [settle, setSettle] = useState(null);            // { id, name }: the "what we still owe" popup of Done
  const [showPast, setShowPast] = useState(false);
  const [past, setPast] = useState([]);                  // temporary helpers who are done (hidden)
  const loadPast = () => api.get("/api/shop/employees/removed").then((x) => setPast(x.filter((r) => r.temporary))).catch(() => {});
  useEffect(() => { loadPast(); }, []);
  const reload = () => api.get("/api/shop/attendance").then(onRows).catch(() => {});
  async function bringBack(r) {
    try { await api.post(`/api/shop/employees/${r.employeeId}/restore`); await reload(); loadPast(); } catch { /* toast */ }
  }
  async function add() {
    setBusy(true);
    try {
      onRows(await api.post("/api/shop/employees/temporary", {
        name: f.name.trim(), role: f.role.trim(), perDayRate: Number(f.perDayRate) || 0, ratePerHour: Number(f.ratePerHour) || 0, presentToday: true,
      }));
      setF({ name: "", role: f.role, perDayRate: f.perDayRate, ratePerHour: f.ratePerHour });
    } catch { /* toast */ } finally { setBusy(false); }
  }
  async function toggle(a) {
    try { onRows(await api.post("/api/shop/attendance", { employeeId: a.id, present: !a.checked })); } catch { /* toast */ }
  }
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-[11px]" style={muted}>Extra help for busy days. They are checked in today and show in the attendance, Cash Flow and the payslip like everyone else.</p>
      <div className="grid gap-2 items-end" style={{ gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) 92px 92px auto" }}>
        <label className="flex flex-col gap-1 min-w-0"><span className="text-[10px]" style={muted}>Name</span>
          <input className={inputCls} style={inputSt} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Jun Santos"
            onKeyDown={(e) => { if (e.key === "Enter" && f.name.trim()) add(); }} /></label>
        <label className="flex flex-col gap-1 min-w-0"><span className="text-[10px]" style={muted}>Job title</span>
          {typing ? (
            <span className="flex gap-1 min-w-0">
              <input autoFocus className={`${inputCls} flex-1 min-w-0`} style={inputSt} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} placeholder="type the job" />
              <button type="button" onClick={() => { setTyping(false); if (!positions.includes(f.role)) setF({ ...f, role: positions[0] }); }} title="Back to the list" style={muted}><X size={14} /></button>
            </span>
          ) : (
            <select className={inputCls} style={inputSt} value={positions.includes(f.role) ? f.role : "__type"}
              onChange={(e) => { if (e.target.value === "__type") { setTyping(true); setF({ ...f, role: "" }); } else setF({ ...f, role: e.target.value }); }}>
              {positions.map((p) => <option key={p} value={p}>{p}</option>)}
              <option value="__type">Type another job…</option>
            </select>
          )}</label>
        <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>₱ per day</span>
          <input className={inputCls} style={inputSt} type="number" value={f.perDayRate} onChange={(e) => setF({ ...f, perDayRate: e.target.value })} placeholder="0" /></label>
        <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>₱ per hour</span>
          <input className={inputCls} style={inputSt} type="number" value={f.ratePerHour} onChange={(e) => setF({ ...f, ratePerHour: e.target.value })} placeholder="0" /></label>
        <button onClick={add} disabled={busy || !f.name.trim()} className="text-xs px-3 py-2 rounded-lg font-semibold text-black/85 disabled:opacity-50 flex items-center gap-1"
          style={{ background: ACCENT.cyan }}><Plus size={13} /> Add</button>
      </div>
      <div className="flex flex-col gap-1.5">
        {temps.map((a) => (
          <div key={a.id} className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm" style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }}>
            <ThemedCheckbox checked={a.checked} onChange={() => toggle(a)} />
            <StaffFace a={a} ver={ver} />
            <span className="flex-1 min-w-0 truncate">{a.name} <span className="text-xs" style={muted}>· {a.role || "helper"} · {peso(a.perDayRate)}/day</span></span>
            <span className="font-mono text-xs" style={{ color: a.time ? ACCENT.cyan : "var(--faint)" }}>{a.time || "--:--"}</span>
            <button onClick={() => setSettle({ id: a.id, name: a.name })} title="Done helping: see what we still owe, pay, remove"
              className="text-[11px] px-2 py-1 rounded-md" style={{ border: "1px solid var(--border)", ...muted }}>Done</button>
          </div>
        ))}
        {!temps.length && <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>No temporary help right now.</p>}
        {(past.length > 0 || showPast) && (
          <div className="flex items-center justify-end pt-1">
            <HiddenToggle on={showPast} count={past.length} onChange={setShowPast} what="past helpers" />
          </div>
        )}
        {showPast && past.map((r) => (
          <div key={r.employeeId} className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-sm" style={{ border: "1px dashed var(--border)", opacity: 0.85 }}>
            <StaffFace a={{ id: r.employeeId, name: r.name, hasPhoto: r.hasPhoto, color: "#8996A6" }} ver={ver} />
            <span className="flex-1 min-w-0 truncate">{r.name} <span className="text-xs" style={muted}>· {r.role || "helper"} · {r.present} day(s){r.removed ? ` · done ${niceDay(r.removed)}` : ""}</span></span>
            {r.owed > 0
              ? <button onClick={() => setSettle({ id: r.employeeId, name: r.name })} className="text-[10px] px-1.5 py-0.5 rounded-full flex items-center gap-1"
                  style={{ color: ACCENT.amber, background: `${ACCENT.amber}1c` }} title="Pay what is still owed"><Wallet size={10} /> owed {peso(r.owed)}</button>
              : <span className="text-[10px]" style={{ color: "#22c55e" }}>paid</span>}
            <button onClick={() => bringBack(r)} className="text-[11px] px-2 py-1 rounded-md flex items-center gap-1" title="Back on the attendance list"
              style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}><RotateCcw size={11} /> Bring back</button>
          </div>
        ))}
      </div>
      {settle && (
        <SettleModal employeeId={settle.id} name={settle.name} canDelete onClose={() => setSettle(null)}
          onDone={() => { setSettle(null); reload(); loadPast(); }} />
      )}
    </div>
  );
}

// ---------------- General attendance ----------------
// The whole month at once: everyone x every day. "Apply to all" checks every box; uncheck the days someone was
// absent; Save sends only the boxes that changed. Days checked here have no time in.
function monthRange() {
  const t = new Date();
  return [ymd(new Date(t.getFullYear(), t.getMonth(), 1)), ymd(t)];
}

function GeneralTab({ onSaved }) {
  const [[start, end], setRange] = useState(monthRange());
  const [d, setD] = useState(null);
  const [cells, setCells] = useState({});              // "empId|date" -> true/false (what the grid shows)
  const [orig, setOrig] = useState({});
  const [skipSun, setSkipSun] = useState(false);
  const [hidden, setHidden] = useState(false);          // also the people who were removed
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get(`/api/shop/attendance/range?start=${start}&end=${end}${hidden ? "&hidden=1" : ""}`).then((x) => {
    const c = {};
    x.staff.forEach((p) => x.days.forEach((day) => { c[`${p.employeeId}|${day}`] = (x.present[p.employeeId] || []).includes(day); }));
    setD(x); setCells(c); setOrig(c); setMsg("");
  }).catch(() => {});
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [start, end, hidden]);
  const isSun = (day) => new Date(`${day}T00:00:00`).getDay() === 0;
  const changed = Object.keys(cells).filter((k) => cells[k] !== orig[k]);
  const setMany = (pred, v) => setCells((c) => Object.fromEntries(Object.entries(c).map(([k, old]) => [k, pred(k) ? v : old])));
  const applyAll = () => setMany((k) => !(skipSun && isSun(k.split("|")[1])), true);
  const toggleDay = (day) => { const on = d.staff.every((p) => cells[`${p.employeeId}|${day}`]); setMany((k) => k.endsWith(`|${day}`), !on); };
  const togglePerson = (id) => { const on = d.days.every((day) => cells[`${id}|${day}`]); setMany((k) => k.startsWith(`${id}|`) && !(skipSun && isSun(k.split("|")[1]) && !on), !on); };
  async function save() {
    setBusy(true);
    try {
      const marks = changed.map((k) => { const [id, date] = k.split("|"); return { employeeId: Number(id), date, present: cells[k] }; });
      const r = await api.post("/api/shop/attendance/bulk", { marks });
      setMsg(`Saved: ${r.added} day(s) checked, ${r.removed} unchecked.`);
      await load();
      onSaved();
      setMsg(`Saved: ${r.added} day(s) checked, ${r.removed} unchecked.`);
    } catch { /* toast */ } finally { setBusy(false); }
  }
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="w-36"><DatePicker value={start} onChange={(v) => v && setRange([v, end < v ? v : end])} /></div>
        <span className="text-xs" style={muted}>to</span>
        <div className="w-36"><DatePicker value={end} onChange={(v) => v && setRange([start > v ? v : start, v])} /></div>
        <button onClick={applyAll} className="text-xs px-3 py-1.5 rounded-lg font-semibold" style={{ border: `1px solid ${ACCENT.cyan}`, color: ACCENT.cyan }}>
          <span className="flex items-center gap-1"><Check size={12} /> Apply to all</span></button>
        <button onClick={() => setMany(() => true, false)} className="text-xs px-2.5 py-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }}>Clear all</button>
        <label className="flex items-center gap-1.5 text-[11px] cursor-pointer" style={muted}>
          <ThemedCheckbox checked={skipSun} onChange={() => setSkipSun((x) => !x)} /> skip Sundays
        </label>
        {d && <span className="ml-auto"><HiddenToggle on={hidden} count={d.hiddenCount} onChange={setHidden} /></span>}
      </div>
      {!d ? <p className="text-xs" style={muted}>Loading…</p> : (
        <div className="overflow-x-auto rounded-lg" style={{ border: "1px solid var(--border)" }}>
          <table className="text-[11px] border-collapse">
            <thead>
              <tr style={{ background: "var(--panelAlt)" }}>
                <th className="sticky left-0 z-10 text-left px-2 py-1 font-semibold min-w-[130px]" style={{ background: "var(--panelAlt)", ...muted }}>Name</th>
                {d.days.map((day) => (
                  <th key={day} onClick={() => toggleDay(day)} title="Check / uncheck everyone this day" className="px-0.5 py-1 cursor-pointer font-medium"
                    style={{ color: isSun(day) ? ACCENT.danger : "var(--muted)", minWidth: 22 }}>
                    <div className="text-[8px] leading-none">{WD[new Date(`${day}T00:00:00`).getDay()][0]}</div>
                    <div>{Number(day.slice(8))}</div>
                  </th>
                ))}
                <th className="px-2 py-1 font-semibold" style={muted}>Days</th>
              </tr>
            </thead>
            <tbody>
              {d.staff.map((p) => {
                const n = d.days.filter((day) => cells[`${p.employeeId}|${day}`]).length;
                return (
                  <tr key={p.employeeId} style={{ borderTop: "1px solid var(--border)" }}>
                    <td onClick={() => togglePerson(p.employeeId)} title="Check / uncheck every day" className="sticky left-0 z-10 px-2 py-1 cursor-pointer whitespace-nowrap"
                      style={{ background: "var(--panel)" }}>
                      <span className="truncate inline-block max-w-[120px] align-middle" style={p.active ? undefined : muted}>{p.name}</span>
                      {p.temporary && <span className="ml-1"><TempBadge /></span>}{!p.active && <span className="ml-1"><RemovedBadge /></span>}
                    </td>
                    {d.days.map((day) => {
                      const k = `${p.employeeId}|${day}`;
                      const on = cells[k];
                      const ch = on !== orig[k];
                      return (
                        <td key={day} className="text-center p-0.5">
                          <button onClick={() => setCells((c) => ({ ...c, [k]: !c[k] }))} title={`${p.name} · ${niceDay(day)}`}
                            className="w-[18px] h-[18px] rounded-[4px] inline-flex items-center justify-center align-middle"
                            style={{ border: `1.5px solid ${on ? ACCENT.cyan : ch ? ACCENT.amber : "var(--faint)"}`, background: on ? `${ACCENT.cyan}26` : "transparent",
                              boxShadow: ch ? `0 0 0 1px ${ACCENT.amber}` : "none" }}>
                            {on && <Check size={11} strokeWidth={3} style={{ color: ACCENT.cyan }} />}
                          </button>
                        </td>
                      );
                    })}
                    <td className="px-2 font-mono text-right">{n}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!d.staff.length && <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>No employees yet.</p>}
        </div>
      )}
      <div className="flex items-center gap-2 justify-end">
        <span className="text-[10px] mr-auto" style={{ color: "var(--faint)" }}>Click a day to check / uncheck everyone that day, a name for all their days. Orange = not saved yet.</span>
        {msg && <span className="text-[11px] flex items-center gap-1" style={{ color: "#22c55e" }}><Check size={12} /> {msg}</span>}
        <button onClick={save} disabled={busy || !changed.length} className="text-xs px-4 py-1.5 rounded-lg font-semibold text-black/85 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
          {busy ? "Saving…" : `Save${changed.length ? ` (${changed.length} change${changed.length > 1 ? "s" : ""})` : ""}`}</button>
      </div>
    </div>
  );
}

// ---------------- Overtime ----------------
function OvertimeTab({ ver, onSaved }) {
  const [date, setDate] = useState(localToday());
  const [kind, setKind] = useState("ot");              // ot | ut
  const [mode, setMode] = useState("all");             // all present | only these people
  const [staff, setStaff] = useState([]);
  const [sel, setSel] = useState({});                  // employeeId -> hours (string)
  const [allHours, setAllHours] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get(`/api/shop/attendance?date=${date}`).then((d) => {
      const rows = staffRows(d);
      setStaff(rows);
      const had = Object.fromEntries(rows.filter((r) => (kind === "ot" ? r.ot : r.ut) > 0).map((r) => [r.id, String(kind === "ot" ? r.ot : r.ut)]));
      setSel(had);
      if (Object.keys(had).length) setMode("some");
      setMsg("");
    }).catch(() => {});
  }, [date, kind]);
  const present = staff.filter((a) => a.checked);
  const everyone = present.length ? present : staff;
  const shown = mode === "all" ? everyone : staff.filter((a) => a.id in sel);
  function applyAll() {
    const h = allHours;
    setSel((cur) => ({ ...cur, ...Object.fromEntries(everyone.map((a) => [a.id, h])) }));
  }
  async function save() {
    setBusy(true);
    try {
      const field = kind === "ot" ? "ot" : "ut";
      const ids = new Set([...shown.map((a) => a.id), ...staff.filter((a) => (kind === "ot" ? a.ot : a.ut) > 0).map((a) => a.id)]);
      const rows = [...ids].map((id) => ({ employeeId: id, [field]: shown.some((a) => a.id === id) ? Number(sel[id]) || 0 : 0 }));
      const d = await api.post("/api/shop/work-hours", { date, rows });
      setStaff(staffRows(d));
      setMsg(`Saved ${kind === "ot" ? "overtime" : "undertime"} for ${niceDay(date)}.`);
      onSaved();
    } catch { /* toast */ } finally { setBusy(false); }
  }
  const options = staff.filter((a) => !(a.id in sel)).map((a) => ({ key: a.id, label: a.name, sub: a.role }));
  const color = kind === "ot" ? ACCENT.cyan : ACCENT.amber;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="w-40"><DatePicker value={date} onChange={(v) => setDate(v || localToday())} /></div>
        <div className="flex rounded-lg p-0.5" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {[["ot", "Overtime"], ["ut", "Undertime"]].map(([k, l]) => (
            <button key={k} onClick={() => setKind(k)} className="text-[11px] px-2.5 py-1 rounded-md font-medium"
              style={kind === k ? { background: k === "ot" ? ACCENT.cyan : ACCENT.amber, color: "#000" } : muted}>{l}</button>
          ))}
        </div>
        <div className="flex rounded-lg p-0.5" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {[["all", present.length ? `Everyone present (${present.length})` : "Everyone"], ["some", "Only these people"]].map(([k, l]) => (
            <button key={k} onClick={() => setMode(k)} className="text-[11px] px-2.5 py-1 rounded-md font-medium"
              style={mode === k ? { background: "var(--raised)", color: "var(--text)", border: "1px solid var(--border)" } : muted}>{l}</button>
          ))}
        </div>
      </div>

      {mode === "all" ? (
        <div className="flex items-center gap-2">
          <span className="text-xs" style={muted}>Apply to all:</span>
          <input type="number" min="0" max="24" step="0.5" value={allHours} onChange={(e) => setAllHours(e.target.value)} placeholder="hours"
            onKeyDown={(e) => { if (e.key === "Enter") applyAll(); }} className={`${inputCls} w-24`} style={inputSt} />
          <button onClick={applyAll} disabled={allHours === ""} className="text-xs px-3 py-1.5 rounded-lg font-semibold disabled:opacity-50"
            style={{ border: `1px solid ${color}`, color }}>Apply to all</button>
          <span className="text-[10px]" style={{ color: "var(--faint)" }}>then change anyone below</span>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Search size={14} style={muted} />
          <SuggestInput value={q} onChange={setQ} options={options} placeholder="Search a name and add them…"
            onPick={(o) => { setSel((cur) => ({ ...cur, [o.key]: cur[o.key] ?? allHours })); setQ(""); }}
            className={`${inputCls} flex-1`} style={inputSt} />
          <input type="number" min="0" max="24" step="0.5" value={allHours} onChange={(e) => setAllHours(e.target.value)} placeholder="hours"
            title="Hours for the next person you add" className={`${inputCls} w-20`} style={inputSt} />
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        {shown.map((a) => (
          <div key={a.id} className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-sm" style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }}>
            <StaffFace a={a} ver={ver} />
            <span className="flex-1 min-w-0 truncate">{a.name} <span className="text-xs" style={muted}>{a.role}</span></span>
            {a.temporary && <TempBadge />}
            {!a.checked && <span className="text-[10px]" style={{ color: "var(--faint)" }}>not checked in</span>}
            <input type="number" min="0" max="24" step="0.5" value={sel[a.id] ?? ""} placeholder="0"
              onChange={(e) => setSel((cur) => ({ ...cur, [a.id]: e.target.value }))}
              className={`${inputCls} w-20 text-right font-mono`} style={{ ...inputSt, borderColor: Number(sel[a.id]) > 0 ? color : "var(--border)" }} />
            <span className="text-[11px] w-5" style={muted}>h</span>
            {mode === "some" && (
              <button onClick={() => setSel((cur) => { const n = { ...cur }; delete n[a.id]; return n; })} style={muted} title="Remove"><X size={14} /></button>
            )}
          </div>
        ))}
        {!shown.length && <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>{mode === "all" ? "No employees yet." : "Search a name above to add the people who did overtime."}</p>}
      </div>
      <div className="flex items-center gap-2 justify-end">
        {msg && <span className="text-[11px] flex items-center gap-1" style={{ color: "#22c55e" }}><Check size={12} /> {msg}</span>}
        <button onClick={save} disabled={busy} className="text-xs px-4 py-1.5 rounded-lg font-semibold text-black/85 disabled:opacity-50" style={{ background: color }}>
          {busy ? "Saving…" : `Save ${kind === "ot" ? "overtime" : "undertime"}`}</button>
      </div>
    </div>
  );
}

// ---------------- Payslip ----------------
function rangeOf(k) {
  const t = new Date();
  if (k === "week") { const d = new Date(t); d.setDate(t.getDate() - ((t.getDay() + 6) % 7)); return [ymd(d), ymd(t)]; }
  if (k === "15") { const d = new Date(t); d.setDate(t.getDate() - 14); return [ymd(d), ymd(t)]; }
  return [ymd(new Date(t.getFullYear(), t.getMonth(), 1)), ymd(t)];
}

function EditDay({ p, onClose, onSaved }) {
  const last = p.days.length ? p.days[p.days.length - 1].date : localToday();
  const [date, setDate] = useState(localToday() >= last ? localToday() : last);
  const [f, setF] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get(`/api/shop/payslip?start=${date}&end=${date}`).then((d) => {
      const me = d.people.find((x) => x.employeeId === p.employeeId);
      const day = me && me.days[0];
      setF({ present: !!(day && day.present), timeIn: (day && day.timeIn) || "", ot: day ? String(day.ot || "") : "", ut: day ? String(day.ut || "") : "" });
    }).catch(() => {});
  }, [date, p.employeeId]);
  async function save() {
    setBusy(true);
    try {
      await api.put("/api/shop/payslip/day", { employeeId: p.employeeId, date, present: f.present, timeIn: f.present ? f.timeIn : null,
        ot: Number(f.ot) || 0, ut: Number(f.ut) || 0 });
      onSaved();
    } catch { /* toast */ } finally { setBusy(false); }
  }
  return (
    <Modal title={`Edit · ${p.name}`} onClose={onClose} width={420}>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1"><span className="text-[10px] font-semibold tracking-wide uppercase" style={muted}>Date</span>
          <DatePicker value={date} onChange={(v) => setDate(v || localToday())} /></label>
        {!f ? <p className="text-xs" style={muted}>Loading…</p> : (
          <>
            <div className="flex items-center gap-2.5">
              <ThemedCheckbox checked={f.present} onChange={() => setF({ ...f, present: !f.present })} />
              <span className="text-sm">Present on {niceDay(date)}</span>
              {f.present && (
                <input value={f.timeIn} onChange={(e) => setF({ ...f, timeIn: e.target.value })} placeholder="time in 07:30"
                  className={`${inputCls} w-28 ml-auto font-mono`} style={inputSt} />
              )}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>Overtime (hours)</span>
                <input type="number" min="0" max="24" step="0.5" value={f.ot} onChange={(e) => setF({ ...f, ot: e.target.value })} className={inputCls} style={inputSt} placeholder="0" /></label>
              <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>Undertime (hours)</span>
                <input type="number" min="0" max="24" step="0.5" value={f.ut} onChange={(e) => setF({ ...f, ut: e.target.value })} className={inputCls} style={inputSt} placeholder="0" /></label>
            </div>
            <p className="text-[11px]" style={muted}>
              Day pay: {peso((f.present ? p.perDayRate : 0) + (Number(f.ot) || 0) * p.ratePerHour * 1.25 - (Number(f.ut) || 0) * p.ratePerHour)}
              {" "}({peso(p.perDayRate)}/day · {peso(p.ratePerHour)}/hour, overtime ×1.25)
            </p>
            <button onClick={save} disabled={busy} className="py-2 rounded-lg text-sm font-semibold text-black/85 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
              {busy ? "Saving…" : "Save this day"}</button>
          </>
        )}
      </div>
    </Modal>
  );
}

function Details({ p, start, end, onClose, onEdit }) {
  const line = (l, v, c) => (
    <div className="flex justify-between text-xs"><span style={muted}>{l}</span><span className="font-mono" style={{ color: c }}>{v}</span></div>
  );
  return (
    <Modal title={`${p.name} · ${niceDay(start)} – ${niceDay(end)}`} onClose={onClose} width={560}>
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <div className="flex-1 flex flex-col gap-0.5">
            {line(`${p.present} day(s) × ${peso(p.perDayRate)}`, peso(p.gross))}
            {p.otHours > 0 && line(`Overtime ${hrs(p.otHours)} × ${peso(p.ratePerHour)} × 1.25`, `+${peso(p.otPay)}`, ACCENT.cyan)}
            {p.utHours > 0 && line(`Undertime ${hrs(p.utHours)} × ${peso(p.ratePerHour)}`, `−${peso(p.utLess)}`, ACCENT.amber)}
            {p.bonusTotal > 0 && line("Bonuses", `+${peso(p.bonusTotal)}`, ACCENT.cyan)}
            {p.advanceTotal > 0 && line("Cash advances", `−${peso(p.advanceTotal)}`, ACCENT.danger)}
            {p.paidTotal > 0 && line("Already paid (payroll)", peso(p.paidTotal))}
          </div>
          <div className="text-right rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            <div className="text-[10px]" style={muted}>Total</div>
            <div className="font-mono text-lg font-semibold">{peso(p.net)}</div>
            {p.paidTotal > 0 && <div className="text-[10px]" style={{ color: p.balance > 0 ? ACCENT.amber : "#22c55e" }}>{p.balance > 0 ? `${peso(p.balance)} left to pay` : "paid"}</div>}
          </div>
        </div>
        <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          <div className="grid text-[10px] font-semibold uppercase tracking-wide px-3 py-1.5" style={{ gridTemplateColumns: "1.3fr 1fr 70px 70px 90px", background: "var(--panelAlt)", ...muted }}>
            <span>Day</span><span>Present</span><span className="text-right">Overtime</span><span className="text-right">Undertime</span><span className="text-right">Day pay</span>
          </div>
          {p.days.map((d) => (
            <div key={d.date} className="grid text-xs px-3 py-1.5 items-center" style={{ gridTemplateColumns: "1.3fr 1fr 70px 70px 90px", borderTop: "1px solid var(--border)" }}>
              <span>{niceDay(d.date)}</span>
              <span style={{ color: d.present ? "#22c55e" : "var(--faint)" }}>{d.present ? `✓ in ${d.timeIn || ""}` : "absent"}</span>
              <span className="text-right font-mono" style={{ color: d.ot ? ACCENT.cyan : "var(--faint)" }}>{d.ot ? hrs(d.ot) : "–"}</span>
              <span className="text-right font-mono" style={{ color: d.ut ? ACCENT.amber : "var(--faint)" }}>{d.ut ? hrs(d.ut) : "–"}</span>
              <span className="text-right font-mono">{peso(d.pay)}</span>
            </div>
          ))}
          {!p.days.length && <p className="text-xs text-center py-4" style={{ color: "var(--faint)" }}>No days recorded in this period.</p>}
        </div>
        <button onClick={onEdit} className="self-start text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5" style={{ border: `1px solid ${ACCENT.cyan}`, color: ACCENT.cyan }}>
          <Pencil size={12} /> Edit a day</button>
      </div>
    </Modal>
  );
}

function PayslipTab({ ver }) {
  const [quick, setQuick] = useState("month");
  const [[start, end], setRange] = useState(rangeOf("month"));
  const [d, setD] = useState(null);
  const [edit, setEdit] = useState(null);
  const [detail, setDetail] = useState(null);
  const [hidden, setHidden] = useState(false);          // also the people who were removed (paid off, hidden)
  const [settle, setSettle] = useState(null);
  const load = () => api.get(`/api/shop/payslip?start=${start}&end=${end}${hidden ? "&hidden=1" : ""}`).then(setD).catch(() => {});
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [start, end, hidden]);
  const pick = (k) => { setQuick(k); setRange(rangeOf(k)); };
  const people = d ? d.people : [];
  const cur = (id) => people.find((x) => x.employeeId === id);
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex rounded-lg p-0.5" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {[["week", "This week"], ["15", "Last 15 days"], ["month", "This month"]].map(([k, l]) => (
            <button key={k} onClick={() => pick(k)} className="text-[11px] px-2.5 py-1 rounded-md font-medium"
              style={quick === k ? { background: ACCENT.cyan, color: "#000" } : muted}>{l}</button>
          ))}
        </div>
        <div className="w-36"><DatePicker value={start} onChange={(v) => { if (v) { setQuick(""); setRange([v, end < v ? v : end]); } }} /></div>
        <span className="text-xs" style={muted}>to</span>
        <div className="w-36"><DatePicker value={end} onChange={(v) => { if (v) { setQuick(""); setRange([start > v ? v : start, v]); } }} /></div>
        {d && <span className="ml-auto"><HiddenToggle on={hidden} count={d.hiddenCount} onChange={setHidden} /></span>}
      </div>
      <div className="flex flex-col gap-1.5">
        {people.map((p, i) => (
          <div key={p.employeeId} className="grid items-center gap-2.5 px-3 py-2 rounded-lg text-sm"
            style={{ border: "1px solid var(--border)", background: "var(--panelAlt)", gridTemplateColumns: "26px minmax(0,1fr) auto auto 96px" }}>
            <StaffFace a={{ id: p.employeeId, name: p.name, hasPhoto: p.hasPhoto, color: PALETTE[i % PALETTE.length] }} ver={ver} />
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 min-w-0"><span className="truncate" style={p.active ? undefined : muted}>{p.name}</span>{p.temporary && <TempBadge />}
                {!p.active && <RemovedBadge />}
                {!p.active && (p.balance > 0
                  ? <button onClick={() => setSettle({ id: p.employeeId, name: p.name })} className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0"
                      style={{ color: ACCENT.amber, background: `${ACCENT.amber}1c` }} title="Pay what is still owed">owed {peso(p.balance)}</button>
                  : <span className="text-[9px] shrink-0" style={{ color: "#22c55e" }}>paid</span>)}</div>
              <div className="text-[10px] truncate" style={muted}>
                {p.present} day(s){p.otHours ? ` · OT ${hrs(p.otHours)}` : ""}{p.utHours ? ` · UT ${hrs(p.utHours)}` : ""}{p.advanceTotal ? ` · advance ${peso(p.advanceTotal)}` : ""}
              </div>
            </div>
            <button onClick={() => setEdit(p.employeeId)} className="text-[11px] px-2 py-1 rounded-md flex items-center gap-1" style={{ border: "1px solid var(--border)", ...muted }}>
              <Pencil size={11} /> Edit</button>
            <button onClick={() => setDetail(p.employeeId)} className="text-[11px] px-2 py-1 rounded-md flex items-center gap-1" style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}>
              <ListChecks size={11} /> Details</button>
            <span className="font-mono text-right font-semibold">{peso(p.net)}</span>
          </div>
        ))}
        {d && !people.length && <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>No employees yet.</p>}
      </div>
      {d && people.length > 0 && (
        <div className="flex justify-end items-baseline gap-2 text-xs px-3">
          <span style={muted}>Total payroll {niceDay(start)} – {niceDay(end)}{hidden && d.hiddenCount ? " (with the removed people)" : ""}</span>
          <span className="font-mono text-sm font-semibold">{peso(d.total)}</span>
        </div>
      )}
      {d && !hidden && d.hiddenCount > 0 && (
        <p className="text-[10px] text-right px-3" style={{ color: "var(--faint)" }}>
          Not in this list: {d.hiddenCount} removed person{d.hiddenCount > 1 ? "s" : ""} who worked in these days ({peso(d.hiddenTotal)}
          {d.hiddenOwed > 0 ? <span style={{ color: ACCENT.amber }}>, {peso(d.hiddenOwed)} still owed</span> : ", all paid"}). Press Show hidden to see them.
        </p>
      )}
      {settle && <SettleModal employeeId={settle.id} name={settle.name} onClose={() => setSettle(null)} onDone={() => { setSettle(null); load(); }} />}
      {edit && cur(edit) && <EditDay p={cur(edit)} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
      {detail && cur(detail) && <Details p={cur(detail)} start={start} end={end} onClose={() => setDetail(null)} onEdit={() => { setEdit(detail); setDetail(null); }} />}
    </div>
  );
}

// ---------------- the card ----------------
export default function StaffPanel({ attendance, onRows, markTime, photoVer, onUploadPhoto }) {
  const [tab, setTab] = useState("attendance");
  const listRef = useRef(null);
  const [listH, setListH] = useState(0);
  useLayoutEffect(() => {                         // the other tabs keep the space the attendance list takes
    if (tab === "attendance" && listRef.current) setListH(listRef.current.offsetHeight);
  }, [tab, attendance.length]);
  const boxH = Math.max(listH, 240);
  const regular = useMemo(() => attendance, [attendance]);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-1 flex-wrap">
          {TABS.map(([k, label, Icon]) => (
            <button key={k} onClick={() => setTab(k)} className="flex items-center gap-1 text-xs px-2 py-1.5 rounded-lg font-semibold whitespace-nowrap"
              style={tab === k ? { background: `${ACCENT.cyan}1c`, color: ACCENT.cyan, border: `1px solid ${ACCENT.cyan}70` } : { color: "var(--muted)", border: "1px solid transparent" }}>
              <Icon size={13} /> {k === "attendance" ? "Employee Attendance" : label}
            </button>
          ))}
        </div>
        {tab === "attendance" && (
          <button onClick={onUploadPhoto}
            title="Upload an employee's photo"
            style={{ border: `1px dashed ${ACCENT.cyan}70`, color: ACCENT.cyan, background: `${ACCENT.cyan}0d` }} className="flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg hover:bg-[color:var(--raised)] shrink-0 whitespace-nowrap">
            <Upload size={13} /> Photo
          </button>
        )}
      </div>
      {tab === "attendance" ? (
        <div ref={listRef} className="flex flex-col gap-1.5">
          {regular.map((a) => (
            <div key={a.id} style={{ border: "1px solid var(--border)", background: "var(--panelAlt)", gridTemplateColumns: "18px 30px minmax(0,1fr) 128px 56px" }}
              className="grid items-center gap-3 px-3 py-2.5 rounded-lg text-sm">
              <ThemedCheckbox checked={a.checked} onChange={() => markTime(a.id)} />
              <StaffFace a={a} ver={photoVer} />
              <span className="truncate flex items-center gap-1.5 min-w-0"><span className="truncate">{a.name}</span>{a.temporary && <TempBadge />}
                {(a.ot > 0 || a.ut > 0) && <span className="text-[10px] font-mono shrink-0" style={{ color: a.ot ? ACCENT.cyan : ACCENT.amber }}>{a.ot ? `+${hrs(a.ot)} OT` : `−${hrs(a.ut)} UT`}</span>}</span>
              <span className="text-xs whitespace-nowrap truncate" style={{ color: "var(--muted)" }}>{a.role}</span>
              <span className="font-mono text-xs text-right" style={{ color: a.time ? ACCENT.cyan : "var(--faint)" }}>{a.time || "--:--"}</span>
            </div>
          ))}
          {attendance.length === 0 && (
            <p className="text-xs text-center py-4" style={{ color: "var(--faint)" }}>No employees yet — add them in Cash Flow → Employee, or add temporary help.</p>
          )}
        </div>
      ) : (
        <div className="overflow-y-auto pr-1" style={{ height: boxH }}>
          {tab === "temporary" && <TemporaryTab staff={attendance} onRows={onRows} ver={photoVer} />}
          {tab === "general" && <GeneralTab ver={photoVer} onSaved={() => api.get("/api/shop/attendance").then(onRows).catch(() => {})} />}
          {tab === "overtime" && <OvertimeTab ver={photoVer} onSaved={() => api.get("/api/shop/attendance").then(onRows).catch(() => {})} />}
          {tab === "payslip" && <PayslipTab ver={photoVer} />}
        </div>
      )}
    </div>
  );
}
