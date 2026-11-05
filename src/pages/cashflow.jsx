import React, { useState, useMemo, useEffect, useRef } from "react";
import {
  Plus, User, Landmark, Wrench, Receipt, Percent, CalendarDays, Gift, ShieldCheck, Pencil, Camera, KeyRound, RotateCcw, Wallet,
} from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal, StatCard, PasswordInput, HiddenToggle } from "../components/kit";
import SettleModal from "../components/settle";
import { api, imgUrl } from "../lib/api";

// ---- type meta (icon + color per entity type) ----------------------------
const CUSTOM_COLOR = "#a78bfa";
const TYPE_META = {
  employee: { label: "Employee", icon: User, color: ACCENT.cyan },
  loan: { label: "Loan", icon: Landmark, color: ACCENT.danger },
  payment: { label: "Payment", icon: Receipt, color: ACCENT.copper },
  maintenance: { label: "Maintenance", icon: Wrench, color: ACCENT.amber },
  custom: { label: "Custom", icon: ShieldCheck, color: CUSTOM_COLOR },
};
const ADD_ORDER = ["employee", "loan", "maintenance", "payment"];
const GROUP_TITLE = { employee: "Employees", loan: "Loans", payment: "Payments", maintenance: "Maintenance" };
const GROUP_NOUN = { employee: "employee", loan: "loan", payment: "payment", maintenance: "item", custom: "contribution" };
const FREQ_UNITS = ["days", "weeks", "months"];
const UNIT_ABBR = { days: "d", weeks: "wk", months: "mo" };
const UNIT_DAYS = { days: 1, weeks: 7, months: 30.44 };
const DEFAULT_POSITIONS = ["Labor", "Sales Person", "Driver", "Backhoe Operator"];
const BASIS_OPTIONS = [{ v: "per_employee", l: "Per employee" }, { v: "flat", l: "Flat total" }];
const WORK_DAYS = 22; // working days per month, used for monthly estimates
const OT_MULT = 1.25; // overtime premium on the hourly rate
const CUSTOM_SUGGESTIONS = ["PhilHealth", "Pag-IBIG", "13th Month Pay"];

// ---- helpers ---------------------------------------------------------------
function peso(n) {
  const v = Number(n) || 0;
  return `₱${Math.round(v).toLocaleString()}`;
}
// local date (YYYY-MM-DD). toISOString() is UTC, which in Manila gives YESTERDAY between midnight and 8 AM.
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const sum = (arr) => (arr || []).reduce((s, p) => s + (Number(p.amount) || 0), 0);
const groupOf = (e) => (e.type === "custom" ? e.customKey : e.type);
const everyText = (e) => `${e.freqValue} ${UNIT_ABBR[e.freqUnit] || e.freqUnit}`;

function perMonthFactor(v, u) {
  return 30.44 / (Math.max(1, v || 1) * (UNIT_DAYS[u] || 30.44));
}
function periodCount(e) {
  if (!e.startDate || !e.endDate) return 1;
  const days = (new Date(e.endDate) - new Date(e.startDate)) / 86400000;
  if (!(days > 0)) return 1;
  return Math.max(1, Math.round(days / (Math.max(1, e.freqValue || 1) * (UNIT_DAYS[e.freqUnit] || 30.44))));
}
const installmentOf = (e) => (e.finalPayment || 0) / periodCount(e);
const remainingOf = (e) => Math.max(0, (e.finalPayment || 0) - sum(e.payments));

function addToDate(str, v, unit) {
  const d = new Date(str);
  if (isNaN(d)) return null;
  if (unit === "months") d.setMonth(d.getMonth() + v);
  else d.setDate(d.getDate() + v * (unit === "weeks" ? 7 : 1));
  return d;
}
const fmtDate = (d) => (d ? d.toLocaleDateString([], { month: "short", day: "numeric" }) : "—");
// next due = last paid + frequency; never paid yet = the start date (null = no start date -> due now)
const nextDueOf = (e) => (e.lastPaid ? addToDate(e.lastPaid, e.freqValue || 1, e.freqUnit) : e.startDate ? new Date(e.startDate) : null);

// current income = present days × rate + overtime + bonus − undertime − cash advance
// (Dashboard → Overtime / Payslip writes the same overtime / undertime hours)
function computeEmployeePay(e) {
  const gross = (e.perDayRate || 0) * (e.presentDays || 0);
  const overtime = (e.overtimeHours || 0) * (e.ratePerHour || 0) * OT_MULT;
  const undertime = (e.undertimeHours || 0) * (e.ratePerHour || 0);
  const bonus = sum(e.customPayments);
  const advance = sum(e.cashAdvances);
  return { gross, overtime, undertime, bonus, advance, net: gross + overtime + bonus - undertime - advance };
}

function monthlyOf(e, headcount) {
  if (e.type === "employee") return (e.perDayRate || 0) * WORK_DAYS;
  const f = perMonthFactor(e.freqValue, e.freqUnit);
  if (e.type === "loan" || e.type === "payment") return installmentOf(e) * f;
  if (e.type === "maintenance") return (e.payment || 0) * f;
  if (e.type === "custom") return (e.amount || 0) * (e.basis === "per_employee" ? headcount : 1) * f;
  return 0;
}

function sortValue(e, headcount) {
  if (e.type === "employee") return e.perDayRate || 0;
  if (e.type === "loan" || e.type === "payment") return remainingOf(e);
  if (e.type === "maintenance") return e.payment || 0;
  return monthlyOf(e, headcount);
}

// ---- small shared bits ------------------------------------------------------
function Field({ label, children }) {
  return (
    <div>
      <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>{label}</label>
      {children}
    </div>
  );
}

function TextInput(props) {
  return (
    <input {...props}
      className="w-full text-sm rounded-lg px-3 py-2 outline-none"
      style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
  );
}

function Select({ value, onChange, options, labelKey, valueKey }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full text-sm rounded-lg px-3 py-2 outline-none"
      style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
      {options.map((o) => (
        <option key={valueKey ? o[valueKey] : o} value={valueKey ? o[valueKey] : o}>
          {labelKey ? o[labelKey] : o}
        </option>
      ))}
    </select>
  );
}

// select with a typeable "add new" escape hatch — used for employee position
function AddableSelect({ value, onChange, options, onAddOption }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  if (adding) {
    return (
      <div className="flex gap-1.5">
        <TextInput autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="New job title" />
        <button onClick={() => {
          const v = draft.trim();
          if (v) { onAddOption(v); onChange(v); }
          setDraft(""); setAdding(false);
        }} style={{ background: ACCENT.cyan }} className="px-3 rounded-lg text-xs font-semibold text-black/85 shrink-0">Add</button>
      </div>
    );
  }
  return (
    <select value={value} onChange={(e) => { if (e.target.value === "__add__") setAdding(true); else onChange(e.target.value); }}
      className="w-full text-sm rounded-lg px-3 py-2 outline-none"
      style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
      <option value="__add__">+ Add new…</option>
    </select>
  );
}

// "every N days/weeks/months" — number is typeable and scrollable (native spinner)
function FrequencyInput({ value, unit, onValue, onUnit }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs shrink-0" style={{ color: "var(--muted)" }}>Every</span>
      <input type="number" min={1} value={value} onChange={(e) => onValue(Math.max(1, Number(e.target.value) || 1))}
        className="w-16 text-sm rounded-lg px-2 py-2 outline-none text-center"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
      <select value={unit} onChange={(e) => onUnit(e.target.value)}
        className="flex-1 text-sm rounded-lg px-2 py-2 outline-none"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
        {FREQ_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
      </select>
    </div>
  );
}

// Generic amount log — used for "payments made" and "custom pay" (bonus / reward)
function AmountLog({ label, items, onAdd, notePlaceholder = "Note (optional)", accent = ACCENT.cyan, icon: Icon = Plus }) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  return (
    <div>
      <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>{label}</label>
      <div className="flex flex-col gap-1.5 max-h-32 overflow-y-auto pr-1 mb-2">
        {items.map((p) => (
          <div key={p.id} style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }}
            className="flex items-center justify-between px-2.5 py-1.5 rounded-md text-xs">
            <span style={{ color: "var(--muted)" }}>{p.date}{p.note ? ` · ${p.note}` : ""}</span>
            <span className="font-mono">{peso(p.amount)}</span>
          </div>
        ))}
        {items.length === 0 && <p className="text-[11px] text-center py-2" style={{ color: "var(--faint)" }}>Nothing logged yet.</p>}
      </div>
      <div className="flex gap-1.5">
        <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount"
          className="w-24 text-xs rounded-md px-2 py-2 outline-none"
          style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={notePlaceholder}
          className="flex-1 min-w-0 text-xs rounded-md px-2 py-2 outline-none"
          style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <button onClick={() => {
          const v = Number(amount);
          if (!v) return;
          onAdd({ id: `p${Date.now()}`, date: today(), amount: v, note: note.trim() });
          setAmount(""); setNote("");
        }} style={{ background: accent }} className="px-3 rounded-md text-xs font-semibold text-black/85 shrink-0">
          <Icon size={13} />
        </button>
      </div>
    </div>
  );
}

// left: type an amount + Add · right: running total that gets deducted from final pay
function CashAdvanceBox({ items, onAdd }) {
  const [amount, setAmount] = useState("");
  const total = sum(items);
  function submit() {
    const v = Number(amount);
    if (!v) return;
    onAdd({ id: `a${Date.now()}`, date: today(), amount: v, note: "" });
    setAmount("");
  }
  return (
    <div>
      <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>Cash advance</label>
      <div className="flex items-stretch gap-3">
        <div className="flex gap-1.5 flex-1 min-w-0 items-center">
          <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount to add"
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            className="flex-1 min-w-0 text-sm rounded-lg px-3 py-2 outline-none"
            style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
          <button onClick={submit} style={{ background: ACCENT.cyan }}
            className="px-3.5 py-2 rounded-lg text-xs font-semibold text-black/85 shrink-0">Add</button>
        </div>
        <div className="rounded-lg px-3 py-1.5 text-right shrink-0" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          <p className="text-[10px]" style={{ color: "var(--muted)" }}>Deducted from final pay</p>
          <p className="font-mono text-sm font-semibold" style={{ color: items.length ? ACCENT.danger : "var(--faint)" }}>{peso(total)}</p>
        </div>
      </div>
      {items.length > 0 && (
        <div className="mt-1.5 flex flex-col gap-1 max-h-20 overflow-y-auto pr-1">
          {items.map((a) => (
            <div key={a.id} className="flex justify-between text-[11px] px-2" style={{ color: "var(--muted)" }}>
              <span>{a.date}</span><span className="font-mono">{peso(a.amount)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EntryGlyph({ type, size = 20, box = 44 }) {
  const meta = TYPE_META[type];
  const Icon = meta.icon;
  return (
    <div style={{ width: box, height: box, borderRadius: box * 0.25, background: `${meta.color}18`, border: `1px solid ${meta.color}45`, color: meta.color }}
      className="flex items-center justify-center shrink-0">
      <Icon size={size} />
    </div>
  );
}

function PerPaymentHint({ e }) {
  const n = periodCount(e);
  return (
    <p className="text-[10px] mt-1" style={{ color: "var(--faint)" }}>
      ≈ {peso(installmentOf(e))} × {n} payment{n === 1 ? "" : "s"} · {peso(remainingOf(e))} left
    </p>
  );
}

// round photo with a camera button (employees only). ver changes after an upload so the new picture shows.
function EmployeePhoto({ entry, ver, onPick, size = 56 }) {
  const n = String(entry.id).split("-")[1];
  const inputRef = useRef(null);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {entry.hasPhoto ? (
        <img src={imgUrl(`/api/shop/employees/${n}/photo`, ver)} alt="" style={{ width: size, height: size, borderRadius: "50%", objectFit: "cover", border: `1px solid ${ACCENT.cyan}55` }} />
      ) : (
        <EntryGlyph type="employee" box={size} size={size * 0.4} />
      )}
      <button onClick={() => inputRef.current && inputRef.current.click()} title="Upload photo"
        style={{ background: ACCENT.cyan }} className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full flex items-center justify-center text-black/85">
        <Camera size={12} />
      </button>
      <input ref={inputRef} type="file" accept="image/*" className="hidden"
        onChange={(e) => { const f = e.target.files[0]; if (f) onPick(f); e.target.value = ""; }} />
    </div>
  );
}

// phone login for a sales person: once linked, the photo above is what the phone loads at sign-in
// The sales person's PHONE LOGIN: the only thing the phone app needs (it finds the shop PC on the Wi-Fi by itself).
// New: type a username + password. Existing: change the username and/or set a new password (the phone is signed out).
// An existing login that belongs to nobody (e.g. "alice" made earlier) can be linked by typing its username.
function PhoneLogin({ entry, onSave }) {
  const [u, setU] = useState(entry.username || "");
  const [p, setP] = useState("");
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState("");
  useEffect(() => { setU(entry.username || ""); setP(""); }, [entry.id, entry.username]);
  const has = !!entry.userId;
  const changed = u.trim() && (u.trim() !== (entry.username || "") || p.length > 0);
  const valid = has ? (p.length === 0 || p.length >= 4) : p.length >= 4 || false;
  async function save() {
    setMsg(""); setOk("");
    try {
      await onSave(entry, u.trim(), p);
      setP("");
      setOk(has ? (p ? "Saved. The phone must sign in again with the new password." : "Username saved.") : "Phone login ready. Use it on the phone app.");
    } catch (e) { setMsg(e.message); }
  }
  const st = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
  return (
    <div className="rounded-lg p-2.5 flex flex-col gap-1.5" style={{ border: `1px solid ${has ? `${ACCENT.cyan}55` : "var(--border)"}` }}>
      <label className="text-[11px] font-medium flex items-center gap-1.5" style={{ color: has ? ACCENT.cyan : "var(--muted)" }}>
        <KeyRound size={13} /> {has ? `Phone login: @${entry.username}` : "Phone login (sales people) — none yet"}
      </label>
      <div className="flex gap-1.5">
        <input value={u} onChange={(e) => setU(e.target.value)} placeholder="username"
          className="flex-1 min-w-0 text-xs rounded-md px-2 py-2 outline-none" style={st} />
        <PasswordInput value={p} onChange={(e) => setP(e.target.value)} placeholder={has ? "new password (optional)" : "password (4+)"}
          autoComplete="new-password" className="flex-1 min-w-0 text-xs rounded-md px-2 py-1.5" style={st} />
        <button disabled={!changed || !valid} onClick={save}
          style={{ background: ACCENT.cyan }} className="px-3 rounded-md text-xs font-semibold text-black/85 shrink-0 disabled:opacity-40">
          {has ? "Save" : "Create"}</button>
      </div>
      <p className="text-[10px]" style={{ color: "var(--faint)" }}>
        Only this username and password are needed on the phone (same Wi-Fi as this PC). The photo above shows on the phone.
      </p>
      {msg && <p className="text-[10px]" style={{ color: ACCENT.danger }}>{msg}</p>}
      {ok && <p className="text-[10px]" style={{ color: ACCENT.cyan }}>{ok}</p>}
    </div>
  );
}

// ---- default drafts per type -----------------------------------------------
// custom entries are always monthly (freqValue 1 / months), so no "Paid every" field
function blankDraft(type) {
  switch (type) {
    case "employee":
      return { name: "", position: DEFAULT_POSITIONS[0], ratePerHour: 0, perDayRate: 0, cashAdvance: 0, presentDays: 0, absentDays: 0, overtimeHours: 0, notes: "",
        phoneUser: "", phonePass: "" };
    case "loan":
      return { name: "", lender: "", principal: 0, interestRate: 0, startDate: "", endDate: "", freqValue: 1, freqUnit: "months", finalPayment: 0, status: "active" };
    case "payment":
      return { name: "", payee: "", startDate: "", endDate: "", freqValue: 1, freqUnit: "months", finalPayment: 0, status: "active" };
    case "maintenance":
      return { name: "", payment: 0, startDate: today(), freqValue: 1, freqUnit: "months", notes: "" };
    case "custom":
      return { name: "", amount: 0, basis: "per_employee", freqValue: 1, freqUnit: "months", notes: "" };
    default:
      return {};
  }
}

export default function CashflowPage() {
  const [entries, setEntries] = useState([]);
  const [customTypes, setCustomTypes] = useState([]);
  const [positions, setPositions] = useState(DEFAULT_POSITIONS);
  const [loadErr, setLoadErr] = useState("");
  const [photoVer, setPhotoVer] = useState(0);
  const [addPhoto, setAddPhoto] = useState(null);
  const [removed, setRemoved] = useState([]);              // people who were removed (hidden, their records stay)
  const [showRemoved, setShowRemoved] = useState(false);
  const [settle, setSettle] = useState(null);              // { id, name, entryId }: what we still owe before removing
  const loadRemoved = () => api.get("/api/shop/employees/removed").then(setRemoved).catch(() => {});
  useEffect(() => { loadRemoved(); }, []);
  const saveTimers = useRef({});
  const pending = useRef({});
  const fail = (e) => setLoadErr(e.message || "Something went wrong");
  useEffect(() => {
    api.get("/api/shop/cashflow")
      .then((d) => { setEntries(d.entries); setCustomTypes(d.customTypes); setPositions(d.positions); setLoadErr(""); })
      .catch(fail);
  }, []);
  function addPosition(v) {
    setPositions((p) => (p.includes(v) ? p : [...p, v]));
    api.post("/api/shop/positions", { label: v }).catch(fail);
  }
  const replaceEntry = (next) => setEntries((prev) => prev.map((e) => (e.id === next.id ? next : e)));

  const [addValue, setAddValue] = useState("employee"); // dropdown value next to the + button
  const [addOpen, setAddOpen] = useState(false);
  const [addType, setAddType] = useState("employee");
  const [addCustomKey, setAddCustomKey] = useState(null);
  const [draft, setDraft] = useState(blankDraft("employee"));

  const [newTypeOpen, setNewTypeOpen] = useState(false);
  const [newTypeName, setNewTypeName] = useState("");

  const [groupKey, setGroupKey] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [profileId, setProfileId] = useState(null);

  const profileFor = useMemo(() => entries.find((e) => e.id === profileId) || null, [entries, profileId]);
  const headcount = entries.filter((e) => e.type === "employee").length;

  const groups = useMemo(() => [
    ...ADD_ORDER.map((t) => ({ key: t, type: t, title: GROUP_TITLE[t] })),
    ...customTypes.map((c) => ({ key: c.key, type: "custom", title: c.label })),
  ], [customTypes]);

  const customLabel = (key) => (customTypes.find((c) => c.key === key) || {}).label || "Custom";
  const typeLabelOf = (e) => (e.type === "custom" ? customLabel(e.customKey) : TYPE_META[e.type].label);

  const count = (t) => entries.filter((e) => e.type === t).length;
  const estMonthly = entries.reduce((s, e) => s + monthlyOf(e, headcount), 0);

  // ---- add flow ----
  function openAdd() {
    setAddPhoto(null);
    if (addValue === "__custom__") { setNewTypeName(""); setNewTypeOpen(true); return; }
    if (addValue.startsWith("custom:")) {
      setAddType("custom"); setAddCustomKey(addValue.slice(7)); setDraft(blankDraft("custom"));
    } else {
      setAddType(addValue); setAddCustomKey(null); setDraft(blankDraft(addValue));
    }
    setAddOpen(true);
  }

  async function createCustomType(label) {
    const clean = label.trim();
    if (!clean) return;
    try {
      const t = await api.post("/api/shop/custom-types", { label: clean });
      setCustomTypes((p) => [...p, t]);
      setAddValue(`custom:${t.key}`);
      setNewTypeOpen(false);
    } catch (e) { fail(e); }
  }

  async function deleteCustomType(key) {
    if (!window.confirm("Delete this category and everything under it?")) return;
    try {
      await api.del(`/api/shop/custom-types/${key}`);
      setCustomTypes((p) => p.filter((c) => c.key !== key));
      setEntries((p) => p.filter((e) => !(e.type === "custom" && e.customKey === key)));
      setAddValue("employee");
      setGroupKey(null);
      setSelectedId(null);
    } catch (e) { fail(e); }
  }

  async function apply() {
    const finalName = draft.name.trim() || (addType === "custom" ? customLabel(addCustomKey) : "");
    if (!finalName) return;
    const body = { ...draft, type: addType, name: finalName };
    if (addType === "employee") {
      const adv = Number(draft.cashAdvance) || 0;
      delete body.cashAdvance;
      body.cashAdvances = adv ? [{ amount: adv, date: today(), note: "Initial" }] : [];
    }
    if (addType === "custom") body.customKey = addCustomKey;
    try {
      const phoneUser = (body.phoneUser || "").trim(), phonePass = body.phonePass || "";
      delete body.phoneUser; delete body.phonePass;
      let created = await api.post("/api/shop/entries", body);
      if (addType === "employee" && addPhoto) {
        await api.upload(`/api/shop/entries/${created.id}/photo`, addPhoto);
        created = { ...created, hasPhoto: true };
      }
      if (addType === "employee" && phoneUser) {
        try {   // the employee is saved even if the username is taken; it can be fixed in the profile
          created = await api.put(`/api/shop/entries/${created.id}/account`, { username: phoneUser, password: phonePass });
        } catch { /* toast */ }
      }
      setEntries((prev) => [...prev, created]);
      setAddOpen(false);
      setAddPhoto(null);
    } catch (e) { fail(e); }
  }

  // ---- edit helpers (profile modal) ----
  // typing updates the screen at once; the database is saved 0.4 s after the last keystroke
  function updateField(field, value) {
    const id = profileId;
    setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, [field]: value } : e)));
    pending.current[id] = { ...(pending.current[id] || {}), [field]: value };
    clearTimeout(saveTimers.current[id]);
    saveTimers.current[id] = setTimeout(() => {
      const body = pending.current[id];
      delete pending.current[id];
      api.put(`/api/shop/entries/${id}`, body).catch(fail);
    }, 400);
  }
  // closing the profile saves anything still waiting for the 0.4 s timer
  function closeProfile() {
    const id = profileId;
    if (id && pending.current[id]) {
      clearTimeout(saveTimers.current[id]);
      const body = pending.current[id];
      delete pending.current[id];
      api.put(`/api/shop/entries/${id}`, body).catch(fail);
    }
    setProfileId(null);
  }
  // logs (cash advances, bonuses, payments) are saved straight away and the server's copy replaces ours
  const pushTo = (field) => async (item) => {
    try {
      replaceEntry(await api.post(`/api/shop/entries/${profileId}/log/${field}`, { amount: item.amount, note: item.note, date: item.date }));
    } catch (e) { fail(e); }
  };
  async function uploadPhoto(file) {
    try {
      await api.upload(`/api/shop/entries/${profileId}/photo`, file);
      setEntries((prev) => prev.map((e) => (e.id === profileId ? { ...e, hasPhoto: true } : e)));
      setPhotoVer((v) => v + 1);
    } catch (e) { fail(e); }
  }
  async function saveAccount(entry, username, password) {
    replaceEntry(await api.put(`/api/shop/entries/${entry.id}/account`, { username: username.trim(), password }));
  }
  async function removeEntry() {
    if (profileFor.type === "employee") {           // a person: first what the shop still owes them, then remove (hide)
      setSettle({ id: Number(String(profileFor.id).slice(4)), name: profileFor.name, entryId: profileFor.id });
      return;
    }
    if (!window.confirm(`Delete "${profileFor.name}"? This cannot be undone.`)) return;
    try {
      await api.del(`/api/shop/entries/${profileFor.id}`);
      setEntries((prev) => prev.filter((e) => e.id !== profileFor.id));
      setProfileId(null);
    } catch (e) { fail(e); }
  }

  function settled(result) {
    const s = settle;
    setSettle(null);
    if (result && result.restored) {
      if (result.entry) setEntries((prev) => [...prev.filter((e) => e.id !== result.entry.id), result.entry]);
    } else if (s && s.entryId) {                    // removed or deleted: off the Cash Flow lists
      setEntries((prev) => prev.filter((e) => e.id !== s.entryId));
      setProfileId(null);
    }
    loadRemoved();
  }
  async function bringBack(r) {
    try {
      const entry = await api.post(`/api/shop/employees/${r.employeeId}/restore`);
      setEntries((prev) => [...prev.filter((e) => e.id !== entry.id), entry]);
      loadRemoved();
    } catch (e) { fail(e); }
  }

  // ---- group modal data ----
  const openGroupMeta = groups.find((g) => g.key === groupKey) || null;
  const groupEntries = useMemo(() => {
    if (!groupKey) return [];
    return entries.filter((e) => groupOf(e) === groupKey)
      .sort((a, b) => sortValue(b, headcount) - sortValue(a, headcount));
  }, [entries, groupKey, headcount]);
  const selected = groupEntries.find((e) => String(e.id) === String(selectedId)) || groupEntries[0] || null;

  function openGroup(key) {
    const first = entries.filter((e) => groupOf(e) === key)
      .sort((a, b) => sortValue(b, headcount) - sortValue(a, headcount))[0];
    setGroupKey(key);
    setSelectedId(first ? first.id : null);
  }

  // ---- card summary rows per group ----
  function cardRows(g, list) {
    if (list.length === 0) return [{ label: "Nothing added yet", value: "" }];
    if (g.type === "employee") {
      const adv = list.reduce((s, e) => s + sum(e.cashAdvances), 0);
      return [
        { label: "Daily", value: peso(list.reduce((s, e) => s + (e.perDayRate || 0), 0)) },
        { label: "Monthly", value: peso(list.reduce((s, e) => s + monthlyOf(e, headcount), 0)) },
        { label: "Cash adv.", value: adv ? `−${peso(adv)}` : "₱0", color: adv ? ACCENT.danger : undefined },
      ];
    }
    if (g.type === "loan" || g.type === "payment") {
      return [
        { label: "Remaining", value: peso(list.reduce((s, e) => s + remainingOf(e), 0)) },
        { label: "Monthly", value: peso(list.reduce((s, e) => s + monthlyOf(e, headcount), 0)) },
      ];
    }
    if (g.type === "maintenance") {
      const dues = list.map(nextDueOf).filter(Boolean).sort((a, b) => a - b);
      return [
        { label: "Monthly", value: peso(list.reduce((s, e) => s + monthlyOf(e, headcount), 0)) },
        { label: "Next due", value: list.some((e) => !nextDueOf(e)) ? "Due now" : fmtDate(dues[0]) },
      ];
    }
    return [
      { label: "Monthly", value: peso(list.reduce((s, e) => s + monthlyOf(e, headcount), 0)) },
      { label: "Covers", value: list.some((e) => e.basis === "per_employee") ? `${headcount} staff` : "Flat" },
    ];
  }

  // ---- spreadsheet layout per type ----
  function sheetFor(type) {
    const meta = TYPE_META[type];
    if (type === "employee") return {
      template: "minmax(0,1.4fr) minmax(0,1fr) 78px 84px 92px", align: ["l", "l", "r", "r", "r"],
      heads: ["Name", "Position", "Daily", "Cash adv.", "Current"],
      cells: (e) => {
        const p = computeEmployeePay(e);
        return [
          { t: e.name },
          { t: e.position, muted: true },
          { t: peso(e.perDayRate), mono: true },
          { t: p.advance ? `−${peso(p.advance)}` : "—", mono: true, color: p.advance ? ACCENT.danger : undefined },
          { t: peso(p.net), mono: true, bold: true, color: meta.color },
        ];
      },
    };
    if (type === "loan" || type === "payment") return {
      template: "minmax(0,1.4fr) minmax(0,1fr) 56px 78px 88px", align: ["l", "l", "r", "r", "r"],
      heads: ["Name", type === "loan" ? "Lender" : "Payee", "Every", "Per pay", "Left"],
      cells: (e) => [
        { t: e.name },
        { t: (type === "loan" ? e.lender : e.payee) || "—", muted: true },
        { t: everyText(e), mono: true, muted: true },
        { t: peso(installmentOf(e)), mono: true },
        { t: peso(remainingOf(e)), mono: true, bold: true, color: meta.color },
      ],
    };
    if (type === "maintenance") return {
      template: "minmax(0,1.4fr) 56px 76px 72px 84px", align: ["l", "r", "r", "r", "r"],
      heads: ["Name", "Every", "Payment", "Last paid", "Next due"],
      cells: (e) => {
        const nd = nextDueOf(e);
        const late = !nd || nd < new Date();
        return [
          { t: e.name },
          { t: everyText(e), mono: true, muted: true },
          { t: peso(e.payment), mono: true },
          { t: e.lastPaid ? fmtDate(new Date(e.lastPaid)) : "—", mono: true, muted: true },
          { t: nd ? fmtDate(nd) : "Due now", mono: true, bold: true, color: late ? ACCENT.danger : meta.color },
        ];
      },
    };
    return {
      template: "minmax(0,1.4fr) 84px 78px 88px", align: ["l", "l", "r", "r"],
      heads: ["Name", "Basis", "Amount", "Monthly"],
      cells: (e) => [
        { t: e.name },
        { t: e.basis === "per_employee" ? "Per emp." : "Flat", muted: true },
        { t: peso(e.amount), mono: true },
        { t: peso(monthlyOf(e, headcount)), mono: true, bold: true, color: meta.color },
      ],
    };
  }

  const numSet = (obj, setObj, field) => (ev) => setObj({ ...obj, [field]: Number(ev.target.value) });
  const canApply = (draft.name.trim() || addType === "custom") && (!(draft.phoneUser || "").trim() || (draft.phonePass || "").length >= 4);

  return (
    <div className="p-6 flex flex-col gap-5">
      {loadErr && entries.length === 0 && (
        <p className="text-xs rounded-lg px-3 py-2" style={{ color: ACCENT.danger, border: `1px solid ${ACCENT.danger}55` }}>{loadErr}</p>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={User} label="Employees" value={count("employee")} tag="on payroll" tagTone="good" />
        <StatCard icon={Landmark} label="Loans" value={count("loan")} tag="active" tagTone="warn" />
        <StatCard icon={Receipt} label="Recurring Payments" value={count("payment")} tag="active" tagTone="warn" />
        <StatCard icon={Wrench} label="Est. Monthly Outflow" value={peso(estMonthly)} tag="all sources" tagTone="bad" />
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Cash Flow</h2>
        <div className="flex items-center rounded-lg overflow-hidden shrink-0" style={{ border: `1px solid ${ACCENT.cyan}` }}>
          <button onClick={openAdd} style={{ background: ACCENT.cyan }} className="flex items-center px-3 py-2 text-black/85">
            <Plus size={15} />
          </button>
          <select value={addValue} onChange={(e) => setAddValue(e.target.value)}
            className="text-xs font-medium pl-2 pr-3 py-2 outline-none"
            style={{ background: "var(--panelAlt)", color: "var(--text)", border: "none" }}>
            {ADD_ORDER.map((t) => <option key={t} value={t}>{TYPE_META[t].label}</option>)}
            {customTypes.map((c) => <option key={c.key} value={`custom:${c.key}`}>{c.label}</option>)}
            <option value="__custom__">Custom…</option>
          </select>
        </div>
      </div>

      {/* one card per group — details live inside */}
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
        {groups.map((g) => {
          const list = entries.filter((e) => groupOf(e) === g.key);
          const meta = TYPE_META[g.type];
          return (
            <button key={g.key} onClick={() => openGroup(g.key)}
              style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }}
              className="rounded-xl p-4 flex flex-col gap-3 text-left hover:border-current">
              <div className="flex items-center gap-3 w-full">
                <EntryGlyph type={g.type} />
                <div className="min-w-0">
                  <p className="text-sm font-semibold leading-tight truncate">{g.title}</p>
                  <p className="text-[11px]" style={{ color: meta.color }}>
                    {list.length} {GROUP_NOUN[g.type]}{list.length === 1 ? "" : "s"}
                  </p>
                </div>
              </div>
              <div className="w-full flex flex-col gap-1 pt-2.5" style={{ borderTop: "1px solid var(--border)" }}>
                {cardRows(g, list).map((r) => (
                  <div key={r.label} className="flex items-center justify-between text-[11px]">
                    <span style={{ color: "var(--muted)" }}>{r.label}</span>
                    <span className="font-mono" style={{ color: r.color || "var(--text)" }}>{r.value}</span>
                  </div>
                ))}
              </div>
            </button>
          );
        })}
      </div>

      {/* ---- Create custom category ---- */}
      {newTypeOpen && (
        <Modal title="New Custom Category" onClose={() => setNewTypeOpen(false)} width={400}>
          <div className="flex flex-col gap-3.5">
            <p className="text-xs" style={{ color: "var(--muted)" }}>
              Name anything not listed yet — like a contribution the business pays on top of wages. It will appear in the dropdown, then press + to fill in its details.
            </p>
            <Field label="Name">
              <TextInput autoFocus value={newTypeName} onChange={(e) => setNewTypeName(e.target.value)} placeholder="e.g. PhilHealth"
                onKeyDown={(e) => { if (e.key === "Enter") createCustomType(newTypeName); }} />
            </Field>
            <div className="flex flex-wrap gap-1.5">
              {CUSTOM_SUGGESTIONS.filter((s) => !customTypes.some((c) => c.label === s)).map((s) => (
                <button key={s} onClick={() => setNewTypeName(s)}
                  style={{ border: `1px solid ${CUSTOM_COLOR}55`, color: CUSTOM_COLOR, background: `${CUSTOM_COLOR}12` }}
                  className="text-[11px] font-medium px-2.5 py-1 rounded-full">{s}</button>
              ))}
            </div>
            <button onClick={() => createCustomType(newTypeName)} disabled={!newTypeName.trim()}
              style={{ background: newTypeName.trim() ? ACCENT.cyan : "var(--faint)" }}
              className="py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:cursor-not-allowed">
              Create
            </button>
          </div>
        </Modal>
      )}

      {/* ---- Add entry modal ---- */}
      {addOpen && (
        <Modal title={`Add ${addType === "custom" ? customLabel(addCustomKey) : TYPE_META[addType].label}`} onClose={() => setAddOpen(false)} width={440}>
          <div className="flex flex-col gap-3.5">
            <Field label={addType === "maintenance" ? "Name (describes itself, e.g. 'Aircon Cleaning')" : "Name"}>
              <TextInput value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder={addType === "employee" ? "e.g. Mark" : addType === "loan" ? "e.g. Truck Repair Loan" : addType === "payment" ? "e.g. Boysen Distribution" : addType === "custom" ? `e.g. ${customLabel(addCustomKey)} employer share` : "e.g. Aircon Cleaning"} />
            </Field>

            {addType === "employee" && (
              <>
                <Field label="Photo (optional — also shown on their phone and their Sales counter)">
                  <input type="file" accept="image/*" onChange={(e) => setAddPhoto(e.target.files[0] || null)} className="text-xs w-full" style={{ color: "var(--muted)" }} />
                </Field>
                <div className="rounded-lg p-2.5 flex flex-col gap-1.5"
                  style={{ border: `1px solid ${draft.position === "Sales Person" ? `${ACCENT.cyan}70` : "var(--border)"}` }}>
                  <span className="text-[11px] font-medium flex items-center gap-1.5" style={{ color: draft.position === "Sales Person" ? ACCENT.cyan : "var(--muted)" }}>
                    <KeyRound size={13} /> Phone login for the scanner app {draft.position === "Sales Person" ? "" : "(sales people only)"}
                  </span>
                  <div className="grid grid-cols-2 gap-2">
                    <TextInput value={draft.phoneUser} onChange={(e) => setDraft({ ...draft, phoneUser: e.target.value })} placeholder="username" autoComplete="off" />
                    <PasswordInput value={draft.phonePass} onChange={(e) => setDraft({ ...draft, phonePass: e.target.value })} placeholder="password (4+)" autoComplete="new-password"
                      className="w-full text-sm rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
                  </div>
                  <span className="text-[10px]" style={{ color: "var(--faint)" }}>Leave empty if this person does not sell. You can set or change it later in their profile.</span>
                </div>
                <Field label="Position / Job">
                  <AddableSelect value={draft.position} onChange={(v) => setDraft({ ...draft, position: v })}
                    options={positions} onAddOption={addPosition} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Per day rate"><TextInput type="number" value={draft.perDayRate} onChange={numSet(draft, setDraft, "perDayRate")} /></Field>
                  <Field label="Income per hour"><TextInput type="number" value={draft.ratePerHour} onChange={numSet(draft, setDraft, "ratePerHour")} /></Field>
                  <Field label="Present (days)"><TextInput type="number" value={draft.presentDays} onChange={numSet(draft, setDraft, "presentDays")} /></Field>
                  <Field label="Absent (days)"><TextInput type="number" value={draft.absentDays} onChange={numSet(draft, setDraft, "absentDays")} /></Field>
                  <Field label="Overtime (hours)"><TextInput type="number" value={draft.overtimeHours} onChange={numSet(draft, setDraft, "overtimeHours")} /></Field>
                  <Field label="Cash advance"><TextInput type="number" value={draft.cashAdvance} onChange={numSet(draft, setDraft, "cashAdvance")} /></Field>
                </div>
                <Field label="Notes"><TextInput value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} placeholder="optional" /></Field>
              </>
            )}

            {(addType === "loan" || addType === "payment") && (
              <>
                <Field label={addType === "loan" ? "Lender" : "Payee"}>
                  <TextInput value={addType === "loan" ? draft.lender : draft.payee}
                    onChange={(e) => setDraft({ ...draft, [addType === "loan" ? "lender" : "payee"]: e.target.value })}
                    placeholder="Who is being paid" />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Start date"><TextInput type="date" value={draft.startDate} onChange={(e) => setDraft({ ...draft, startDate: e.target.value })} /></Field>
                  <Field label="End date"><TextInput type="date" value={draft.endDate} onChange={(e) => setDraft({ ...draft, endDate: e.target.value })} /></Field>
                </div>
                <Field label="Pay every">
                  <FrequencyInput value={draft.freqValue} unit={draft.freqUnit}
                    onValue={(v) => setDraft({ ...draft, freqValue: v })} onUnit={(u) => setDraft({ ...draft, freqUnit: u })} />
                </Field>
                {addType === "loan" && (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Principal (borrowed)"><TextInput type="number" value={draft.principal} onChange={numSet(draft, setDraft, "principal")} /></Field>
                    <Field label="Interest rate (%)"><TextInput type="number" value={draft.interestRate} onChange={numSet(draft, setDraft, "interestRate")} /></Field>
                  </div>
                )}
                <Field label={addType === "loan" ? "Final payment (with markup)" : "Final payment"}>
                  <TextInput type="number" value={draft.finalPayment} onChange={numSet(draft, setDraft, "finalPayment")} />
                  <PerPaymentHint e={draft} />
                </Field>
              </>
            )}

            {addType === "maintenance" && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Payment"><TextInput type="number" value={draft.payment} onChange={numSet(draft, setDraft, "payment")} /></Field>
                  <Field label="Starts on (first due)"><TextInput type="date" value={draft.startDate} onChange={(e) => setDraft({ ...draft, startDate: e.target.value })} /></Field>
                </div>
                <Field label="Occurs every">
                  <FrequencyInput value={draft.freqValue} unit={draft.freqUnit}
                    onValue={(v) => setDraft({ ...draft, freqValue: v })} onUnit={(u) => setDraft({ ...draft, freqUnit: u })} />
                </Field>
                <Field label="Notes"><TextInput value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} placeholder="optional" /></Field>
              </>
            )}

            {addType === "custom" && (
              <>
                <Field label="Monthly contribution amount"><TextInput type="number" value={draft.amount} onChange={numSet(draft, setDraft, "amount")} /></Field>
                <Field label="Applies to">
                  <Select value={draft.basis} onChange={(v) => setDraft({ ...draft, basis: v })} options={BASIS_OPTIONS} labelKey="l" valueKey="v" />
                  <p className="text-[10px] mt-1" style={{ color: "var(--faint)" }}>
                    {draft.basis === "per_employee" ? `Charged for each employee — ${headcount} on payroll right now.` : "One flat amount for the whole business."}
                  </p>
                </Field>
                <Field label="Notes"><TextInput value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} placeholder="optional" /></Field>
              </>
            )}

            <button onClick={apply} disabled={!canApply} style={{ background: canApply ? ACCENT.cyan : "var(--faint)" }}
              className="mt-1 py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:cursor-not-allowed">
              Apply
            </button>
          </div>
        </Modal>
      )}

      {/* ---- Group modal: pick one from the dropdown, Edit, spreadsheet below ---- */}
      {openGroupMeta && (() => {
        const meta = TYPE_META[openGroupMeta.type];
        const sheet = sheetFor(openGroupMeta.type);
        return (
          <Modal title="" onClose={() => { setGroupKey(null); setSelectedId(null); }} width={640}>
            <div className="flex items-center gap-3 mb-4">
              <EntryGlyph type={openGroupMeta.type} />
              <div className="min-w-0">
                <h3 className="font-semibold text-base leading-tight">{openGroupMeta.title}</h3>
                <span style={{ color: meta.color }} className="text-[11px] font-medium">
                  {groupEntries.length} {GROUP_NOUN[openGroupMeta.type]}{groupEntries.length === 1 ? "" : "s"}
                </span>
              </div>
              {openGroupMeta.type === "custom" && (
                <button onClick={() => deleteCustomType(openGroupMeta.key)}
                  style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}
                  className="ml-auto mr-8 text-[11px] font-medium px-2.5 py-1 rounded-md shrink-0">
                  Delete category
                </button>
              )}
            </div>

            {groupEntries.length === 0 ? (
              <p className="text-xs text-center py-8" style={{ color: "var(--faint)" }}>
                Nothing here yet — pick “{openGroupMeta.title}” in the dropdown beside + and press +.
              </p>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <div className="flex-1 min-w-0">
                    <Select value={selected ? selected.id : ""} onChange={setSelectedId} options={groupEntries} labelKey="name" valueKey="id" />
                  </div>
                  <button onClick={() => selected && setProfileId(selected.id)} style={{ background: meta.color }}
                    className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold text-black/85 shrink-0">
                    <Pencil size={13} /> Edit
                  </button>
                </div>

                <div style={{ border: "1px solid var(--border)" }} className="rounded-lg overflow-hidden">
                  <div className="grid gap-2 px-3 py-2 text-[10px] font-semibold"
                    style={{ gridTemplateColumns: sheet.template, background: "var(--panelAlt)", color: "var(--muted)", borderBottom: "1px solid var(--border)" }}>
                    {sheet.heads.map((h, i) => <span key={h} className={sheet.align[i] === "r" ? "text-right" : ""}>{h}</span>)}
                  </div>
                  <div className="overflow-y-auto" style={{ maxHeight: 264, overscrollBehavior: "contain" }}>
                    {groupEntries.map((e) => {
                      const active = selected && selected.id === e.id;
                      return (
                        <button key={e.id} onClick={() => setSelectedId(e.id)}
                          className="grid gap-2 items-center w-full px-3 py-2.5 text-left"
                          style={{
                            gridTemplateColumns: sheet.template,
                            background: active ? `${meta.color}12` : "transparent",
                            borderBottom: "1px solid var(--border)",
                            boxShadow: active ? `inset 2px 0 0 ${meta.color}` : "none",
                          }}>
                          {sheet.cells(e).map((c, i) => (
                            <span key={i} className={`truncate text-xs ${sheet.align[i] === "r" ? "text-right" : ""} ${c.mono ? "font-mono" : ""} ${c.bold ? "font-semibold" : ""}`}
                              style={{ color: c.color || (c.muted ? "var(--muted)" : "var(--text)") }}>
                              {c.t}
                            </span>
                          ))}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <p className="text-[10px] mt-2" style={{ color: "var(--faint)" }}>Sorted highest first. Click a row to select it, then Edit.</p>
              </>
            )}
            {openGroupMeta.type === "employee" && (removed.length > 0 || showRemoved) && (
              <div className="mt-3 flex flex-col gap-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] flex-1" style={{ color: "var(--faint)" }}>Removed people are not counted in Cash Flow; their days stay in the records.</span>
                  <HiddenToggle on={showRemoved} count={removed.length} onChange={setShowRemoved} />
                </div>
                {showRemoved && removed.map((r) => (
                  <div key={r.employeeId} className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg" style={{ border: "1px dashed var(--border)" }}>
                    <span className="flex-1 min-w-0 truncate">{r.name}
                      <span style={{ color: "var(--muted)" }}> · {r.role || "—"}{r.temporary ? " · temporary" : ""}{r.removed ? ` · removed ${r.removed}` : ""} · {r.present} day(s)</span></span>
                    {r.owed > 0 ? (
                      <button onClick={() => setSettle({ id: r.employeeId, name: r.name })} title="Pay what is still owed"
                        className="text-[10px] px-1.5 py-0.5 rounded-full flex items-center gap-1 shrink-0" style={{ color: ACCENT.amber, background: `${ACCENT.amber}1c` }}>
                        <Wallet size={10} /> owed {peso(r.owed)}</button>
                    ) : <span className="text-[10px] shrink-0" style={{ color: "#22c55e" }}>paid</span>}
                    <button onClick={() => bringBack(r)} className="text-[11px] px-2 py-1 rounded-md flex items-center gap-1 shrink-0"
                      style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}><RotateCcw size={11} /> Bring back</button>
                  </div>
                ))}
              </div>
            )}
          </Modal>
        );
      })()}

      {/* ---- Profile modal (opens over the group modal) ---- */}
      {profileFor && (
        <Modal title="" onClose={closeProfile} width={460}>
          <div className="flex items-center gap-3 mb-4">
            {profileFor.type === "employee"
              ? <EmployeePhoto entry={profileFor} ver={photoVer} onPick={uploadPhoto} />
              : <EntryGlyph type={profileFor.type} />}
            <div>
              <h3 className="font-semibold text-base leading-tight">{profileFor.name}</h3>
              <span style={{ color: TYPE_META[profileFor.type].color }} className="text-[11px] font-medium">{typeLabelOf(profileFor)}</span>
            </div>
          </div>

          <div className="flex flex-col gap-3.5 pr-1">
            <Field label="Name">
              <TextInput value={profileFor.name} onChange={(e) => updateField("name", e.target.value)} />
            </Field>

            {profileFor.type === "employee" && (
              <>
                <Field label="Position / Job">
                  <AddableSelect value={profileFor.position} onChange={(v) => updateField("position", v)}
                    options={positions} onAddOption={addPosition} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Per day rate"><TextInput type="number" value={profileFor.perDayRate} onChange={(e) => updateField("perDayRate", Number(e.target.value))} /></Field>
                  <Field label="Income per hour"><TextInput type="number" value={profileFor.ratePerHour} onChange={(e) => updateField("ratePerHour", Number(e.target.value))} /></Field>
                  <Field label="Present (days)"><TextInput type="number" value={profileFor.presentDays} onChange={(e) => updateField("presentDays", Number(e.target.value))} /></Field>
                  <Field label="Absent (days)"><TextInput type="number" value={profileFor.absentDays} onChange={(e) => updateField("absentDays", Number(e.target.value))} /></Field>
                  <Field label="Overtime (hours)"><TextInput type="number" value={profileFor.overtimeHours || 0} onChange={(e) => updateField("overtimeHours", Number(e.target.value))} /></Field>
                  <Field label="Undertime (hours)"><TextInput type="number" value={profileFor.undertimeHours || 0} onChange={(e) => updateField("undertimeHours", Number(e.target.value))} /></Field>
                </div>

                <PhoneLogin entry={profileFor} onSave={saveAccount} />

                <CashAdvanceBox items={profileFor.cashAdvances || []} onAdd={pushTo("cashAdvances")} />

                {(() => {
                  const pay = computeEmployeePay(profileFor);
                  return (
                    <div className="rounded-lg px-3 py-2.5 flex items-center justify-between"
                      style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                      <div>
                        <p className="text-[11px]" style={{ color: "var(--muted)" }}>Current income</p>
                        <p className="text-[10px]" style={{ color: "var(--faint)" }}>
                          {peso(pay.gross)} days{pay.overtime ? ` + ${peso(pay.overtime)} OT` : ""}{pay.bonus ? ` + ${peso(pay.bonus)} bonus` : ""}{pay.undertime ? ` − ${peso(pay.undertime)} UT` : ""} − {peso(pay.advance)} advance
                        </p>
                      </div>
                      <span className="font-mono font-semibold text-sm">{peso(pay.net)}</span>
                    </div>
                  );
                })()}

                <Field label="Notes"><TextInput value={profileFor.notes} onChange={(e) => updateField("notes", e.target.value)} /></Field>
                <AmountLog label="Payments made" items={profileFor.payments} onAdd={pushTo("payments")} />
                <AmountLog
                  label="Custom pay (bonus / reward)"
                  items={profileFor.customPayments || []}
                  onAdd={pushTo("customPayments")}
                  notePlaceholder="e.g. Bonus, reward for good deed"
                  accent={ACCENT.amber}
                  icon={Gift}
                />
              </>
            )}

            {(profileFor.type === "loan" || profileFor.type === "payment") && (
              <>
                <Field label={profileFor.type === "loan" ? "Lender" : "Payee"}>
                  <TextInput value={(profileFor.type === "loan" ? profileFor.lender : profileFor.payee) || ""}
                    onChange={(e) => updateField(profileFor.type === "loan" ? "lender" : "payee", e.target.value)} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Start date"><TextInput type="date" value={profileFor.startDate} onChange={(e) => updateField("startDate", e.target.value)} /></Field>
                  <Field label="End date"><TextInput type="date" value={profileFor.endDate} onChange={(e) => updateField("endDate", e.target.value)} /></Field>
                </div>
                <Field label="Pay every">
                  <FrequencyInput value={profileFor.freqValue} unit={profileFor.freqUnit}
                    onValue={(v) => updateField("freqValue", v)} onUnit={(u) => updateField("freqUnit", u)} />
                </Field>
                {profileFor.type === "loan" && (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Principal (borrowed)"><TextInput type="number" value={profileFor.principal} onChange={(e) => updateField("principal", Number(e.target.value))} /></Field>
                    <Field label="Interest rate (%)">
                      <div className="relative">
                        <TextInput type="number" value={profileFor.interestRate} onChange={(e) => updateField("interestRate", Number(e.target.value))} />
                        <Percent size={12} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--faint)" }} />
                      </div>
                    </Field>
                  </div>
                )}
                <Field label={profileFor.type === "loan" ? "Final payment (with markup)" : "Final payment"}>
                  <TextInput type="number" value={profileFor.finalPayment} onChange={(e) => updateField("finalPayment", Number(e.target.value))} />
                  <PerPaymentHint e={profileFor} />
                </Field>
                <Field label="Status">
                  <Select value={profileFor.status} onChange={(v) => updateField("status", v)} options={["active", "paused", "completed"]} />
                </Field>
                <AmountLog label="Payments made" items={profileFor.payments} onAdd={pushTo("payments")} />
              </>
            )}

            {profileFor.type === "maintenance" && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Payment"><TextInput type="number" value={profileFor.payment} onChange={(e) => updateField("payment", Number(e.target.value))} /></Field>
                  <Field label="Starts on (first due)"><TextInput type="date" value={profileFor.startDate || ""} onChange={(e) => updateField("startDate", e.target.value)} /></Field>
                </div>
                <Field label="Occurs every">
                  <FrequencyInput value={profileFor.freqValue} unit={profileFor.freqUnit}
                    onValue={(v) => updateField("freqValue", v)} onUnit={(u) => updateField("freqUnit", u)} />
                </Field>
                <Field label="Last paid">
                  <div className="flex gap-1.5">
                    <div className="relative flex-1 min-w-0">
                      <TextInput type="date" value={profileFor.lastPaid || ""} onChange={(e) => updateField("lastPaid", e.target.value)} />
                      <CalendarDays size={13} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--faint)" }} />
                    </div>
                    <button onClick={() => updateField("lastPaid", today())} style={{ background: ACCENT.amber }}
                      className="px-3 rounded-lg text-xs font-semibold text-black/85 shrink-0">Paid today</button>
                  </div>
                  <p className="text-[10px] mt-1" style={{ color: "var(--faint)" }}>
                    Next due: {nextDueOf(profileFor) ? fmtDate(nextDueOf(profileFor)) : "now"}
                  </p>
                </Field>
                <Field label="Notes"><TextInput value={profileFor.notes} onChange={(e) => updateField("notes", e.target.value)} /></Field>
              </>
            )}

            {profileFor.type === "custom" && (
              <>
                <Field label="Monthly contribution amount"><TextInput type="number" value={profileFor.amount} onChange={(e) => updateField("amount", Number(e.target.value))} /></Field>
                <Field label="Applies to">
                  <Select value={profileFor.basis} onChange={(v) => updateField("basis", v)} options={BASIS_OPTIONS} labelKey="l" valueKey="v" />
                </Field>
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between"
                  style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                  <div>
                    <p className="text-[11px]" style={{ color: "var(--muted)" }}>Monthly cost</p>
                    <p className="text-[10px]" style={{ color: "var(--faint)" }}>
                      {profileFor.basis === "per_employee" ? `${peso(profileFor.amount)} × ${headcount} employees` : "flat amount"}
                    </p>
                  </div>
                  <span className="font-mono font-semibold text-sm">{peso(monthlyOf(profileFor, headcount))}</span>
                </div>
                <Field label="Notes"><TextInput value={profileFor.notes || ""} onChange={(e) => updateField("notes", e.target.value)} /></Field>
              </>
            )}
          </div>

          <div className="mt-4 flex gap-2">
            <button onClick={removeEntry} style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}
              title={profileFor.type === "employee" ? "See what the shop still owes, pay, then remove" : "Delete"}
              className="px-4 py-2.5 rounded-lg text-sm font-medium">{profileFor.type === "employee" ? "Remove…" : "Delete"}</button>
            <button onClick={closeProfile} style={{ background: ACCENT.cyan }}
              className="flex-1 py-2.5 rounded-lg text-sm font-semibold text-black/85">
              Save
            </button>
          </div>
        </Modal>
      )}
      {settle && <SettleModal employeeId={settle.id} name={settle.name} canDelete={!!settle.entryId} onClose={() => setSettle(null)} onDone={settled} />}
    </div>
  );
}
