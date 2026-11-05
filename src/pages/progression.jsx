import React, { useState, useRef, useEffect, useMemo } from "react";
import {
  RefreshCw, Truck, Wallet, Users, Repeat, Landmark, CheckCircle2, Circle,
  Plus, Minus, Gift, Percent, CalendarDays,
} from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal } from "../components/kit";
import CashFlowGraph from "../components/cashflowGraph";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// PROGRESSION — a stack of 5 analytics panels, top to bottom:
//   1) Delivery Pending   — of all active client orders, how much is left
//                            to ship, as one ring.
//   2) Payment Pending    — same idea, but for what clients still owe.
//   3) Total Salaries     — this cycle's payroll, broken down by employee.
//   4) Recurring Payments — the standing bills, broken down by payee.
//   5) Loan Payments      — outstanding loan balances, broken down by loan.
//
// GAUGE panels (1 & 2) work like this: with N clients, each client "owns"
// an equal 100/N share of the ring. A client who has delivered/paid nothing
// contributes their full share to the pending total; a client who's 50% done
// only contributes half their share. Fully done = 0 contribution. So with 10
// clients, if 8 have done nothing and 2 are 50% delivered, pending reads
// 8×10 + 2×5 = 90%. The undrawn part of the ring (gray) is what's already
// done. BREAKDOWN panels (3-5) don't gauge a percent — the ring is just
// each entry's peso share of that panel's total, so the ring is always 100%
// full, split by entry.
//
// Every panel has a small "↻" button that cycles how its data is drawn:
// donut ring → bar list → plain list. Clicking any client/employee/bill
// slice or row opens that entry's own profile, same "tap a card, see the
// full sheet" pattern as Sales and Cash Flow.
// ---------------------------------------------------------------------------

const VIEW_MODES = ["donut", "bar", "list"];
function nextViewMode(mode) {
  return VIEW_MODES[(VIEW_MODES.indexOf(mode) + 1) % VIEW_MODES.length];
}

// 10 visually distinct slice colors. Index 0 is amber (orange) on purpose —
// so "client 1" (or whichever entry sits first) reads as the orange slice.
const PALETTE = [
  ACCENT.amber, ACCENT.cyan, ACCENT.copper, ACCENT.danger,
  "#14b8a6", "#6366f1", "#ec4899", "#84cc16", "#64748b", "#38bdf8",
];

function peso(n) {
  const v = Number(n) || 0;
  return `${v < 0 ? "-" : ""}₱${Math.abs(v).toLocaleString()}`;
}

// ---------------------------------------------------------------------------
// DATA — loaded from the database and shaped for the panels below:
//   clients   = active bulk orders (Sales → Add Order)
//   employees = Cash Flow employees
//   recurring = Cash Flow "Payment" entries
//   loans     = Cash Flow "Loan" entries
// ---------------------------------------------------------------------------
const UNIT_DAYS = { days: 1, weeks: 7, months: 30.44 };
const sumAmounts = (arr) => (arr || []).reduce((s, x) => s + (Number(x.amount) || 0), 0);
function periodsBetween(e) {
  if (!e.startDate || !e.endDate) return 1;
  const days = (new Date(e.endDate) - new Date(e.startDate)) / 86400000;
  if (!(days > 0)) return 1;
  return Math.max(1, Math.round(days / (Math.max(1, e.freqValue || 1) * (UNIT_DAYS[e.freqUnit] || 30.44))));
}
function termMonthsOf(e) {
  if (!e.startDate || !e.endDate) return 12;
  const days = (new Date(e.endDate) - new Date(e.startDate)) / 86400000;
  return days > 0 ? Math.max(1, Math.round(days / 30.44)) : 12;
}
function toClient(o) {
  return { id: o.id, name: o.customer, items: o.items, total: o.total, payments: o.payments };
}
function toEmployee(e) {
  return { id: e.id, name: e.name, position: e.position, perDayRate: e.perDayRate, presentDays: e.presentDays,
    overtimeHours: e.overtimeHours, undertimeHours: e.undertimeHours, ratePerHour: e.ratePerHour, cashAdvance: sumAmounts(e.cashAdvances), bonuses: e.customPayments || [] };
}
function toRecurring(e) {
  return { id: e.id, name: e.name, payee: e.payee, amount: Math.round((e.finalPayment || 0) / periodsBetween(e)),
    finalPayment: e.finalPayment, freqValue: e.freqValue, freqUnit: e.freqUnit, status: e.status, payments: e.payments || [] };
}
function toLoan(e) {
  return { id: e.id, name: e.name, lender: e.lender, principal: e.principal, interestRate: e.interestRate, finalPayment: e.finalPayment,
    freqValue: e.freqValue, freqUnit: e.freqUnit, termMonths: termMonthsOf(e), status: e.status, payments: e.payments || [] };
}
// net pay this cycle = present days x rate + overtime (1.25x hourly) + bonuses - cash advance (same as Cash Flow)
function employeeNetPay(e) {
  const gross = (e.perDayRate || 0) * (e.presentDays || 0);
  const ot = (e.overtimeHours || 0) * (e.ratePerHour || 0) * 1.25;
  const bonus = (e.bonuses || []).reduce((s, b) => s + b.amount, 0);
  const ut = (e.undertimeHours || 0) * (e.ratePerHour || 0);
  return Math.max(0, gross + ot - ut - (e.cashAdvance || 0) + bonus);
}

// ---------------------------------------------------------------------------
// Contribution math
// ---------------------------------------------------------------------------
function clientDeliveryPending(client) {
  const total = client.items.length;
  const delivered = client.items.filter((i) => i.delivered).length;
  return total ? (total - delivered) / total : 0; // 0..1
}
function clientPaidAmount(client) {
  return client.payments.reduce((s, p) => s + p.amount, 0);
}
function clientPaymentPending(client) {
  return client.total ? Math.max(0, client.total - clientPaidAmount(client)) / client.total : 0; // 0..1
}
function loanPaid(loan) {
  return loan.payments.reduce((s, p) => s + p.amount, 0);
}
function loanRemaining(loan) {
  return Math.max(0, loan.finalPayment - loanPaid(loan));
}

// ---------------------------------------------------------------------------
// Small shared form bits (used inside the profile modals below)
// ---------------------------------------------------------------------------
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
function Select({ value, onChange, options }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full text-sm rounded-lg px-3 py-2 outline-none"
      style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
function FrequencyInput({ value, unit, onValue, onUnit }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs shrink-0" style={{ color: "var(--muted)" }}>Every</span>
      <input type="number" min={1} value={value} onChange={(e) => onValue(Number(e.target.value))}
        className="w-16 text-sm rounded-lg px-2 py-2 outline-none text-center"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
      <select value={unit} onChange={(e) => onUnit(e.target.value)}
        className="flex-1 text-sm rounded-lg px-2 py-2 outline-none"
        style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
        {["days", "weeks", "months"].map((u) => <option key={u} value={u}>{u}</option>)}
      </select>
    </div>
  );
}
function AmountLog({ label, items, onAdd, notePlaceholder = "Note (optional)", accent = ACCENT.cyan, icon: Icon = Plus }) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  return (
    <div>
      <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>{label}</label>
      <div className="flex flex-col gap-1.5 max-h-28 overflow-y-auto pr-1 mb-2">
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
          onAdd({ id: `p${Date.now()}`, amount: v, note: note.trim() });   // the server stamps today's (local) date
          setAmount(""); setNote("");
        }} style={{ background: accent }} className="px-3 rounded-md text-xs font-semibold text-black/85 shrink-0">
          <Icon size={13} />
        </button>
      </div>
    </div>
  );
}
function ProgressBar({ percent, color, label }) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <div>
      <div className="flex items-center justify-between text-[10px] mb-1" style={{ color: "var(--muted)" }}>
        <span>{label}</span><span className="font-mono">{Math.round(pct)}%</span>
      </div>
      <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: "var(--panelAlt)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart primitives — the same `segments` feed all three view modes.
// segments: [{ id, label, color, value }]
// capacity: the value the full ring represents. For gauge panels this is a
// fixed 100 (percent) with any leftover drawn as a gray "done" arc. For
// breakdown panels this is just the sum of all segment values (ring is
// always full, no gray remainder).
// ---------------------------------------------------------------------------
function DonutChart({ segments, capacity, selectedId, onSelect, valueFormat }) {
  const size = 160, r = 58, cx = 80, cy = 80, sw = 18;
  const circumference = 2 * Math.PI * r;
  const total = segments.reduce((s, seg) => s + seg.value, 0);
  let cumulative = 0;
  const arcs = segments.map((seg) => {
    const len = capacity ? (seg.value / capacity) * circumference : 0;
    const arc = { ...seg, len, offset: cumulative };
    cumulative += len;
    return arc;
  });
  const remainder = Math.max(0, circumference - cumulative);
  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
        <g transform={`rotate(-90 ${cx} ${cy})`}>
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--panelAlt)" strokeWidth={sw} />
          {arcs.map((a) => (
            <circle key={a.id} cx={cx} cy={cy} r={r} fill="none" stroke={a.color}
              strokeWidth={selectedId === a.id ? sw + 5 : sw}
              strokeDasharray={`${a.len} ${circumference - a.len}`}
              strokeDashoffset={-a.offset}
              opacity={selectedId && selectedId !== a.id ? 0.45 : 1}
              style={{ cursor: "pointer", transition: "all .15s" }}
              onClick={() => onSelect(a.id)} />
          ))}
          {remainder > 0.5 && (
            <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border)" strokeWidth={sw}
              strokeDasharray={`${remainder} ${circumference - remainder}`} strokeDashoffset={-cumulative} />
          )}
        </g>
        <text x={cx} y={cy - 3} textAnchor="middle" className="font-mono font-semibold" style={{ fontSize: 20, fill: "var(--text)" }}>
          {valueFormat ? valueFormat(total) : total}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" style={{ fontSize: 9, fill: "var(--muted)" }}>total</text>
      </svg>
      <div className="flex flex-col gap-1 min-w-0 flex-1">
        {segments.slice(0, 4).map((seg) => (
          <button key={seg.id} onClick={() => onSelect(seg.id)}
            className="flex items-center gap-1.5 text-left"
            style={{ opacity: selectedId && selectedId !== seg.id ? 0.5 : 1 }}>
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: seg.color }} />
            <span className="text-[11px] truncate" style={{ color: "var(--muted)" }}>{seg.label}</span>
          </button>
        ))}
        {segments.length > 4 && <span className="text-[10px]" style={{ color: "var(--faint)" }}>+{segments.length - 4} more</span>}
      </div>
    </div>
  );
}
function BarView({ segments, onSelect, selectedId, valueFormat }) {
  const max = Math.max(1, ...segments.map((s) => s.value));
  return (
    <div className="flex flex-col gap-2">
      {segments.map((seg) => (
        <button key={seg.id} onClick={() => onSelect(seg.id)} className="flex items-center gap-2 text-left"
          style={{ opacity: selectedId && selectedId !== seg.id ? 0.55 : 1 }}>
          <span className="text-[11px] w-28 truncate shrink-0" style={{ color: "var(--muted)" }}>{seg.label}</span>
          <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: "var(--panelAlt)" }}>
            <div className="h-full rounded-full" style={{ width: `${(seg.value / max) * 100}%`, background: seg.color }} />
          </div>
          <span className="text-[10px] font-mono w-14 text-right shrink-0">{valueFormat ? valueFormat(seg.value) : seg.value}</span>
        </button>
      ))}
    </div>
  );
}
function ListView({ segments, onSelect, selectedId, valueFormat }) {
  return (
    <div className="flex flex-col gap-1">
      {segments.map((seg) => (
        <button key={seg.id} onClick={() => onSelect(seg.id)}
          className="flex items-center justify-between px-2.5 py-1.5 rounded-md text-xs"
          style={{ background: selectedId === seg.id ? `${seg.color}18` : "var(--panelAlt)", border: `1px solid ${selectedId === seg.id ? seg.color : "var(--border)"}` }}>
          <span className="flex items-center gap-1.5 truncate">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: seg.color }} />
            {seg.label}
          </span>
          <span className="font-mono shrink-0">{valueFormat ? valueFormat(seg.value) : seg.value}</span>
        </button>
      ))}
    </div>
  );
}

// One panel: header (title, icon, ↻ toggle) + whichever view is active.
function AnalyticsPanel({ title, icon: Icon, iconColor, viewMode, onCycleView, segments, capacity, selectedId, onSelect, valueFormat, footnote }) {
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div style={{ width: 30, height: 30, borderRadius: 8, background: `${iconColor}18`, border: `1px solid ${iconColor}45`, color: iconColor }} className="flex items-center justify-center shrink-0">
            <Icon size={15} />
          </div>
          <h3 className="font-semibold text-sm">{title}</h3>
        </div>
        <button onClick={onCycleView} title="Change how this is shown"
          className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-md"
          style={{ border: "1px solid var(--border)", color: "var(--muted)" }}>
          <RefreshCw size={11} /> {viewMode}
        </button>
      </div>

      {viewMode === "donut" && <DonutChart segments={segments} capacity={capacity} selectedId={selectedId} onSelect={onSelect} valueFormat={valueFormat} />}
      {viewMode === "bar" && <BarView segments={segments} selectedId={selectedId} onSelect={onSelect} valueFormat={valueFormat} />}
      {viewMode === "list" && <ListView segments={segments} selectedId={selectedId} onSelect={onSelect} valueFormat={valueFormat} />}

      {footnote && <p className="text-[10px]" style={{ color: "var(--faint)" }}>{footnote}</p>}
    </div>
  );
}

// A tiny inline line chart the chat drops into a message when a question
// calls for a trend (e.g. a loan balance shrinking over its term) rather
// than a single number.

export default function ProgressionPage() {
  const [orders, setOrders] = useState([]);
  const [entries, setEntries] = useState([]);
  async function load() {
    try {
      const [o, c] = await Promise.all([api.get("/api/shop/orders"), api.get("/api/shop/cashflow")]);
      setOrders(o.active); setEntries(c.entries);
    } catch { /* toast */ }
  }
  useEffect(() => { load(); }, []);
  const clients = useMemo(() => orders.map(toClient), [orders]);
  const employees = useMemo(() => entries.filter((e) => e.type === "employee").map(toEmployee), [entries]);
  const recurring = useMemo(() => entries.filter((e) => e.type === "payment" && e.status !== "completed").map(toRecurring), [entries]);
  const loans = useMemo(() => entries.filter((e) => e.type === "loan" && e.status !== "completed").map(toLoan), [entries]);
  const replaceEntry = (next) => setEntries((prev) => prev.map((e) => (e.id === next.id ? next : e)));
  const replaceOrder = (next) => setOrders((prev) => prev.map((o) => (o.id === next.id ? next : o)));
  // typing changes the screen at once; the database is saved 0.4 s after the last keystroke (same as Cash Flow)
  const timers = useRef({});
  const pendingEdits = useRef({});
  function editEntry(id, field, value) {
    setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, [field]: value } : e)));
    pendingEdits.current[id] = { ...(pendingEdits.current[id] || {}), [field]: value };
    clearTimeout(timers.current[id]);
    timers.current[id] = setTimeout(() => {
      const body = pendingEdits.current[id];
      delete pendingEdits.current[id];
      api.put(`/api/shop/entries/${id}`, body).catch(() => {});
    }, 400);
  }
  async function logTo(id, field, item) {
    try { replaceEntry(await api.post(`/api/shop/entries/${id}/log/${field}`, { amount: item.amount, note: item.note, date: item.date })); } catch { /* toast */ }
  }

  const [viewModes, setViewModes] = useState({ delivery: "donut", payment: "donut", salary: "donut", recurring: "donut", loan: "donut" });
  const [selected, setSelected] = useState({ delivery: null, payment: null, salary: null, recurring: null, loan: null });
  const [profile, setProfile] = useState(null); // { type: 'client' | 'employee' | 'recurring' | 'loan', id }

  function cycleView(key) {
    setViewModes((v) => ({ ...v, [key]: nextViewMode(v[key]) }));
  }
  function openProfile(key, type, id) {
    setSelected((s) => ({ ...s, [key]: id }));
    setProfile({ type, id });
  }

  // ---- Delivery Pending segments ----
  const deliverySegments = clients.map((c, i) => ({
    id: c.id, label: c.name, color: PALETTE[i % PALETTE.length],
    value: (100 / clients.length) * clientDeliveryPending(c),
  }));
  const deliveryPendingTotal = deliverySegments.reduce((s, seg) => s + seg.value, 0);

  // ---- Payment Pending segments ----
  const paymentSegments = clients.map((c, i) => ({
    id: c.id, label: c.name, color: PALETTE[i % PALETTE.length],
    value: (100 / clients.length) * clientPaymentPending(c),
  }));
  const paymentPendingTotal = paymentSegments.reduce((s, seg) => s + seg.value, 0);

  // ---- Salary breakdown ----
  const salarySegments = employees.map((e, i) => ({ id: e.id, label: e.name, color: PALETTE[i % PALETTE.length], value: employeeNetPay(e) }));
  const salaryTotal = salarySegments.reduce((s, seg) => s + seg.value, 0);

  // ---- Recurring payments breakdown ----
  // per MONTH: a bill paid every 3 months counts 1/3 of it, a weekly one about 4.3x (same as Cash In vs Cash Out)
  const perMonth = (r) => {
    const v = Math.max(1, Number(r.freqValue) || 1);
    return r.freqUnit === "weeks" ? (r.amount * 30.44) / (7 * v) : r.freqUnit === "days" ? (r.amount * 30.44) / v : r.amount / v;
  };
  const recurringSegments = recurring.map((r, i) => ({ id: r.id, label: r.name, color: PALETTE[i % PALETTE.length], value: perMonth(r) }));
  const recurringTotal = recurringSegments.reduce((s, seg) => s + seg.value, 0);

  // ---- Loan payments breakdown (outstanding balance per loan) ----
  const loanSegments = loans.map((l, i) => ({ id: l.id, label: l.name, color: PALETTE[i % PALETTE.length], value: loanRemaining(l) }));
  const loanTotal = loanSegments.reduce((s, seg) => s + seg.value, 0);

  // ---- profile lookups + mutators ----
  const clientProfile = profile?.type === "client" ? clients.find((c) => c.id === profile.id) : null;
  const employeeProfile = profile?.type === "employee" ? employees.find((e) => e.id === profile.id) : null;
  const recurringProfile = profile?.type === "recurring" ? recurring.find((r) => r.id === profile.id) : null;
  const loanProfile = profile?.type === "loan" ? loans.find((l) => l.id === profile.id) : null;

  async function toggleClientItem(clientId, itemId) {
    try { replaceOrder(await api.post(`/api/shop/orders/${clientId}/items/${itemId}/toggle`)); } catch { /* toast */ }
  }
  async function addClientPayment(clientId, payment) {
    try { replaceOrder(await api.post(`/api/shop/orders/${clientId}/payments`, { amount: payment.amount, note: payment.note, date: payment.date })); } catch { /* toast */ }
  }
  const updateEmployeeField = (id, field, value) => editEntry(id, field, value);
  const addEmployeeBonus = (id, bonus) => logTo(id, "customPayments", bonus);
  const updateRecurringField = (id, field, value) => editEntry(id, field, value);
  const addRecurringPayment = (id, payment) => logTo(id, "payments", payment);
  const updateLoanField = (id, field, value) => editEntry(id, field, value);
  const addLoanPayment = (id, payment) => logTo(id, "payments", payment);

  return (
    <div className="p-6 flex gap-5 items-start">
    <div className="flex flex-col gap-5 max-w-xl flex-1 min-w-0">
      <h2 className="text-lg font-semibold">Progression</h2>

      <AnalyticsPanel title="Delivery Pending" icon={Truck} iconColor={ACCENT.amber}
        viewMode={viewModes.delivery} onCycleView={() => cycleView("delivery")}
        segments={deliverySegments} capacity={100}
        selectedId={selected.delivery} onSelect={(id) => openProfile("delivery", "client", id)}
        valueFormat={(v) => `${Math.round(v)}%`}
        footnote={clients.length ? `${Math.round(deliveryPendingTotal)}% still owed across ${clients.length} clients — each client is an equal share of the ring.` : "No active bulk orders (Sales → Add Order)."} />

      <AnalyticsPanel title="Payment Pending" icon={Wallet} iconColor={ACCENT.danger}
        viewMode={viewModes.payment} onCycleView={() => cycleView("payment")}
        segments={paymentSegments} capacity={100}
        selectedId={selected.payment} onSelect={(id) => openProfile("payment", "client", id)}
        valueFormat={(v) => `${Math.round(v)}%`}
        footnote={clients.length ? `${Math.round(paymentPendingTotal)}% of total order value still unpaid across ${clients.length} clients.` : "No active bulk orders."} />

      <AnalyticsPanel title="Total Salaries" icon={Users} iconColor={ACCENT.cyan}
        viewMode={viewModes.salary} onCycleView={() => cycleView("salary")}
        segments={salarySegments} capacity={salaryTotal}
        selectedId={selected.salary} onSelect={(id) => openProfile("salary", "employee", id)}
        valueFormat={(v) => peso(Math.round(v))}
        footnote={`${peso(salaryTotal)} payroll this cycle across ${employees.length} employees.`} />

      <AnalyticsPanel title="Recurring Payments" icon={Repeat} iconColor={ACCENT.copper}
        viewMode={viewModes.recurring} onCycleView={() => cycleView("recurring")}
        segments={recurringSegments} capacity={recurringTotal}
        selectedId={selected.recurring} onSelect={(id) => openProfile("recurring", "recurring", id)}
        valueFormat={(v) => peso(Math.round(v))}
        footnote={`${peso(Math.round(recurringTotal))} a month in standing bills (a bill every few months counts its monthly share).`} />

      <AnalyticsPanel title="Loan Payments" icon={Landmark} iconColor={ACCENT.danger}
        viewMode={viewModes.loan} onCycleView={() => cycleView("loan")}
        segments={loanSegments} capacity={loanTotal}
        selectedId={selected.loan} onSelect={(id) => openProfile("loan", "loan", id)}
        valueFormat={(v) => peso(Math.round(v))}
        footnote={`${peso(loanTotal)} still outstanding across ${loans.length} loans.`} />

      {/* ---- Client profile: same "checklist + payment log" pattern as
          Sales' order profile — here's the list, here's what's accomplished. ---- */}
      {clientProfile && (
        <Modal title={clientProfile.name} onClose={() => setProfile(null)} width={460}>
          <div className="flex flex-col gap-4">
            <div>
              <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>Items (tap to mark delivered)</label>
              <div className="flex flex-col gap-1 max-h-56 overflow-y-auto pr-1">
                {clientProfile.items.map((it) => (
                  <button key={it.id} onClick={() => toggleClientItem(clientProfile.id, it.id)}
                    className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-md text-sm"
                    style={{ background: "var(--panelAlt)" }}>
                    {it.delivered ? <CheckCircle2 size={16} style={{ color: "#22c55e" }} /> : <Circle size={16} style={{ color: "var(--faint)" }} />}
                    <span style={{ opacity: it.delivered ? 0.6 : 1, textDecoration: it.delivered ? "line-through" : "none" }}>{it.name}</span>
                  </button>
                ))}
              </div>
            </div>
            <ProgressBar percent={(1 - clientDeliveryPending(clientProfile)) * 100} color="#22c55e" label="Delivered" />

            <AmountLog label="Payments received" items={clientProfile.payments} onAdd={(p) => addClientPayment(clientProfile.id, p)} />
            <ProgressBar percent={(1 - clientPaymentPending(clientProfile)) * 100} color={ACCENT.cyan} label="Paid" />
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                <p style={{ color: "var(--muted)" }}>Order total</p>
                <p className="font-mono font-semibold">{peso(clientProfile.total)}</p>
              </div>
              <div className="rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                <p style={{ color: "var(--muted)" }}>Unpaid balance</p>
                <p className="font-mono font-semibold">{peso(clientProfile.total - clientPaidAmount(clientProfile))}</p>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* ---- Employee profile ---- */}
      {employeeProfile && (
        <Modal title={employeeProfile.name} onClose={() => setProfile(null)} width={420}>
          <div className="flex flex-col gap-3.5">
            <Field label="Position / Job"><TextInput value={employeeProfile.position} onChange={(e) => updateEmployeeField(employeeProfile.id, "position", e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Per day rate"><TextInput type="number" value={employeeProfile.perDayRate} onChange={(e) => updateEmployeeField(employeeProfile.id, "perDayRate", Number(e.target.value))} /></Field>
              <Field label="Present (days)"><TextInput type="number" value={employeeProfile.presentDays} onChange={(e) => updateEmployeeField(employeeProfile.id, "presentDays", Number(e.target.value))} /></Field>
              <Field label="Cash advance (add in Cash Flow)"><TextInput type="number" value={employeeProfile.cashAdvance} readOnly /></Field>
            </div>
            <div className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
              <span className="text-[11px]" style={{ color: "var(--muted)" }}>Net pay this cycle</span>
              <span className="font-mono font-semibold text-sm">{peso(employeeNetPay(employeeProfile))}</span>
            </div>
            <AmountLog label="Bonuses" items={employeeProfile.bonuses} onAdd={(b) => addEmployeeBonus(employeeProfile.id, b)} notePlaceholder="e.g. Overtime, reward" accent={ACCENT.amber} icon={Gift} />
          </div>
        </Modal>
      )}

      {/* ---- Recurring payment profile ---- */}
      {recurringProfile && (
        <Modal title={recurringProfile.name} onClose={() => setProfile(null)} width={420}>
          <div className="flex flex-col gap-3.5">
            <Field label="Payee"><TextInput value={recurringProfile.payee} onChange={(e) => updateRecurringField(recurringProfile.id, "payee", e.target.value)} /></Field>
            <Field label="Total to pay (per cycle is worked out from the dates)"><TextInput type="number" value={recurringProfile.finalPayment} onChange={(e) => updateRecurringField(recurringProfile.id, "finalPayment", Number(e.target.value))} /></Field>
            <p className="text-[10px] -mt-2" style={{ color: "var(--faint)" }}>≈ {peso(recurringProfile.amount)} per cycle</p>
            <Field label="Occurs every">
              <FrequencyInput value={recurringProfile.freqValue} unit={recurringProfile.freqUnit}
                onValue={(v) => updateRecurringField(recurringProfile.id, "freqValue", v)} onUnit={(u) => updateRecurringField(recurringProfile.id, "freqUnit", u)} />
            </Field>
            <Field label="Status"><Select value={recurringProfile.status} onChange={(v) => updateRecurringField(recurringProfile.id, "status", v)} options={["active", "paused", "completed"]} /></Field>
            <AmountLog label="Payments made" items={recurringProfile.payments} onAdd={(p) => addRecurringPayment(recurringProfile.id, p)} />
          </div>
        </Modal>
      )}

      {/* ---- Loan profile ---- */}
      {loanProfile && (
        <Modal title={loanProfile.name} onClose={() => setProfile(null)} width={420}>
          <div className="flex flex-col gap-3.5">
            <Field label="Lender"><TextInput value={loanProfile.lender} onChange={(e) => updateLoanField(loanProfile.id, "lender", e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Principal (borrowed)"><TextInput type="number" value={loanProfile.principal} onChange={(e) => updateLoanField(loanProfile.id, "principal", Number(e.target.value))} /></Field>
              <Field label="Interest rate (%)">
                <div className="relative">
                  <TextInput type="number" value={loanProfile.interestRate} onChange={(e) => updateLoanField(loanProfile.id, "interestRate", Number(e.target.value))} />
                  <Percent size={12} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--faint)" }} />
                </div>
              </Field>
            </div>
            <Field label="Final payment (with markup)"><TextInput type="number" value={loanProfile.finalPayment} onChange={(e) => updateLoanField(loanProfile.id, "finalPayment", Number(e.target.value))} /></Field>
            <Field label="Pay every">
              <FrequencyInput value={loanProfile.freqValue} unit={loanProfile.freqUnit}
                onValue={(v) => updateLoanField(loanProfile.id, "freqValue", v)} onUnit={(u) => updateLoanField(loanProfile.id, "freqUnit", u)} />
            </Field>
            <Field label="Status"><Select value={loanProfile.status} onChange={(v) => updateLoanField(loanProfile.id, "status", v)} options={["active", "paused", "completed"]} /></Field>
            <ProgressBar percent={loanProfile.finalPayment ? (loanPaid(loanProfile) / loanProfile.finalPayment) * 100 : 0} color={ACCENT.cyan} label="Paid off" />
            <div className="rounded-lg px-3 py-2 flex items-center justify-between text-xs" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
              <span style={{ color: "var(--muted)" }}>Remaining balance</span>
              <span className="font-mono font-semibold">{peso(loanRemaining(loanProfile))}</span>
            </div>
            <AmountLog label="Payments made" items={loanProfile.payments} onAdd={(p) => addLoanPayment(loanProfile.id, p)} />
          </div>
        </Modal>
      )}
    </div>

      <CashFlowGraph />
    </div>
  );
}