import React, { useEffect, useMemo, useState } from "react";
import {
  Landmark, Banknote, Plus, ChevronDown, ChevronRight, ChevronLeft, ArrowLeftRight, AlertTriangle, CircleDashed, CheckCircle2,
  CalendarDays, ScanLine, Camera,
} from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { api, imgUrl } from "../lib/api";
import { Modal, DatePicker } from "../components/kit";

// ---------------------------------------------------------------------------
// PAYMENTS: every payment is CASH or BANK. No bank account is linked or kept here (the shop uses one bank).
//   * Cash payments / Bank payments: short lists (name, date, amount) with the total so far, month by month.
//     The details of a day (each sale, its items, the time) are in "Payments by day": click a line to open it.
//   * Sales from the phones: cash or bank is read from the receipt photo: CASH SALES ticked = cash; a PAID or
//     PAID IN BANK stamp (or bank / GCash / InstaPay written) = bank; nothing = cash. A sale can be switched by day.
//   * Order payments (Sales -> Add Order): Cash or Bank is picked when the payment is recorded.
//   * Payments for one order are one line in "Incomplete & missing" / "Completed payments" (click to see each).
// Data: /api/shop/payments, /payments/list?kind=cash|bank&month=, /payments/day, /payments/entries, /payments/link
// ---------------------------------------------------------------------------

const BANK_COLOR = "#2F80ED";
const CASH_COLOR = ACCENT.amber;
const KIND = {
  cash: { color: CASH_COLOR, icon: Banknote, title: "Cash payments", label: "CASH PAYMENTS", total: "Cash payment total",
    sub: "Sales paid in cash (CASH SALES ticked, or no PAID stamp) and order payments in cash" },
  bank: { color: BANK_COLOR, icon: Landmark, title: "Bank payments", label: "BANK PAYMENTS", total: "Bank payment total",
    sub: "Sales with a PAID / PAID IN BANK stamp (or GCash, InstaPay… written) and order payments by bank" },
};
const peso = (v) => `${v < 0 ? "-" : ""}₱${Math.abs(v || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const niceDate = (d) => { if (!d) return ""; const [y, m, day] = d.split("-"); return `${MONTHS[Number(m) - 1]} ${Number(day)}, ${y}`; };
const shortDate = (d) => niceDate(d).replace(/, \d+$/, "");
const monthName = (ym) => { if (!ym) return "All months"; const [y, m] = ym.split("-"); return `${LONG_MONTHS[Number(m) - 1]} ${y}`; };
const pad = (n) => String(n).padStart(2, "0");
const todayStr = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const shiftMonth = (ym, n) => { const [y, m] = ym.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };
const inputCls = "w-full text-sm rounded-lg px-3 py-2 outline-none";
const inputSt = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
const card = { border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW };
const muted = { color: "var(--muted)" };
const STATE = {
  complete: { label: "Complete", color: "#22c55e", icon: CheckCircle2 },
  incomplete: { label: "Incomplete", color: ACCENT.amber, icon: CircleDashed },
  missing: { label: "No payment yet", color: ACCENT.danger, icon: AlertTriangle },
};

function Label({ children }) {
  return <span className="text-[10px] font-semibold tracking-wide uppercase" style={muted}>{children}</span>;
}

function StateTag({ state }) {
  const s = STATE[state];
  if (!s) return null;
  const Icon = s.icon;
  return (
    <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full inline-flex items-center gap-1 shrink-0" style={{ color: s.color, background: `${s.color}1c` }}>
      <Icon size={10} /> {s.label}
    </span>
  );
}

// the note of an order payment without the "Cash" / "Bank" word it starts with (that is shown as a chip)
const noteOf = (n) => (n || "").replace(/^(cash|bank)\b\s*/i, "").trim();
// "Cash" / "Bank" (or "Not said": an order payment typed in Sales before cash / bank was asked)
const methodOf = (label) => (label === "Bank" || label === "bank" ? "bank" : label === "Cash" || label === "cash" ? "cash" : null);
function MethodChip({ m }) {
  const c = m === "cash" ? CASH_COLOR : m === "bank" ? BANK_COLOR : "#8996A6";
  return (
    <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 inline-flex items-center gap-1" style={{ color: c, background: `${c}1f` }}>
      {m === "cash" ? <Banknote size={10} /> : m === "bank" ? <Landmark size={10} /> : null}{m === "cash" ? "Cash" : m === "bank" ? "Bank" : "Not said"}
    </span>
  );
}

// Cash | Bank switch used when a payment is recorded
function CashBank({ value, onChange, small }) {
  return (
    <div className="flex rounded-lg p-0.5 shrink-0" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
      {[["cash", "Cash", Banknote, CASH_COLOR], ["bank", "Bank", Landmark, BANK_COLOR]].map(([k, l, Icon, c]) => (
        <button key={k} type="button" onClick={() => onChange(k)}
          className={`${small ? "text-[10px] px-2 py-0.5" : "text-[11px] px-2.5 py-1"} rounded-md font-semibold flex items-center gap-1`}
          style={value === k ? { background: c, color: "#000" } : muted}><Icon size={small ? 10 : 12} /> {l}</button>
      ))}
    </div>
  );
}

function PhotoModal({ entry, onClose }) {
  if (!entry) return null;
  return (
    <Modal title={`Receipt · ${peso(entry.amount)} · ${niceDate(entry.date)}`} onClose={onClose} width={560}>
      <img src={imgUrl(`/api/shop/payments/entries/${entry.id}/photo`, entry.id)} alt="Receipt" className="w-full rounded-lg" style={{ background: "var(--panelAlt)" }} />
    </Modal>
  );
}

// ---------------- Cash payments / Bank payments: name, date, amount ----------------
function PayList({ kind, onOpenDay }) {
  const k = KIND[kind];
  const Icon = k.icon;
  const [month, setMonth] = useState(todayStr().slice(0, 7));     // "" = all months
  const [d, setD] = useState(null);
  useEffect(() => {
    setD(null);
    api.get(`/api/shop/payments/list?kind=${kind}${month ? `&month=${month}` : ""}`).then(setD).catch(() => {});
  }, [kind, month]);
  const cols = "84px minmax(0,1fr) 110px 118px";
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl p-4 flex items-center gap-3" style={{ background: `${k.color}14`, border: `1px solid ${k.color}55` }}>
        <div style={{ width: 40, height: 40, borderRadius: 11, background: `${k.color}25`, color: k.color }} className="flex items-center justify-center shrink-0"><Icon size={20} /></div>
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-semibold tracking-wide" style={{ color: k.color }}>{k.label}</div>
          <div className="text-[11px]" style={muted}>{k.sub}</div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10px]" style={muted}>{k.total}{month ? ` · ${monthName(month)}` : ""}</div>
          <div className="font-mono text-lg font-semibold">{d ? peso(d.total) : "…"}</div>
          {d && month && <div className="text-[10px]" style={muted}>all months {peso(d.allTotal)}</div>}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button onClick={() => setMonth((m) => shiftMonth(m || todayStr().slice(0, 7), -1))} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }} title="Month before"><ChevronLeft size={14} /></button>
        <span className="text-sm font-semibold w-36 text-center">{monthName(month)}</span>
        <button onClick={() => setMonth((m) => shiftMonth(m || todayStr().slice(0, 7), 1))} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }} title="Next month"><ChevronRight size={14} /></button>
        <button onClick={() => setMonth(month ? "" : todayStr().slice(0, 7))} className="text-[11px] px-2 py-1 rounded-md ml-1"
          style={{ border: `1px solid ${month ? "var(--border)" : k.color}`, color: month ? "var(--muted)" : k.color }}>{month ? "All months" : "This month"}</button>
        {d && <span className="ml-auto text-[11px]" style={muted}>{d.rows.length} line{d.rows.length === 1 ? "" : "s"}</span>}
      </div>

      {!d ? <p className="text-xs" style={muted}>Loading…</p> : d.rows.length ? (
        <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          <div className="grid text-[10px] font-semibold tracking-wide uppercase px-3 py-2" style={{ gridTemplateColumns: cols, background: "var(--panelAlt)", ...muted }}>
            <span>Date</span><span>Name</span><span className="text-right">Amount</span><span className="text-right">Total so far</span>
          </div>
          {d.rows.map((x) => (
            <button key={x.key} onClick={() => onOpenDay(x.date)} title="Open this day in Payments by day"
              className="grid items-center text-xs px-3 py-2 w-full text-left hover:bg-white/[0.03]" style={{ gridTemplateColumns: cols, borderTop: "1px solid var(--border)" }}>
              <span className="font-mono text-[11px]" style={muted}>{shortDate(x.date)}</span>
              <span className="min-w-0 truncate"><span className="font-medium">{x.name}</span>
                {x.sub && <span className="text-[10px] ml-1.5" style={{ color: x.notSaid ? ACCENT.amber : "var(--muted)" }}>{x.sub}</span>}</span>
              <span className="font-mono text-right" style={{ color: k.color }}>{peso(x.amount)}</span>
              <span className="font-mono text-right font-semibold">{peso(x.soFar)}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="text-xs py-6 text-center" style={{ color: "var(--faint)" }}>No {kind} payments {month ? `in ${monthName(month)}` : "yet"}.</p>
      )}
      <p className="text-[10px]" style={{ color: "var(--faint)" }}>
        One line = the sales of one sales person on one day, or one order payment. Click a line to see that day.
        {kind === "bank" ? " Nothing here connects to a bank: it only adds up what was paid by bank." : ""}
      </p>
    </div>
  );
}

// ---------------- who still owes / completed orders ----------------
function OrdersView({ which, orders, onChanged }) {
  const [open, setOpen] = useState(null);
  const [paying, setPaying] = useState(null);
  const [photo, setPhoto] = useState(null);
  const groups = which === "owes"
    ? [["incomplete", "Incomplete payments", "Paid only part of the order"], ["missing", "Missing payments", "Nothing paid yet"]]
    : [["complete", "Completed payments", "Paid in full"]];
  async function link(p, method) {
    try { await api.post(`/api/shop/payments/link?paymentId=${p.id}&method=${method}`); onChanged(); } catch { /* toast */ }
  }
  return (
    <div className="flex flex-col gap-4">
      {groups.map(([state, title, sub]) => {
        const list = orders.filter((o) => o.state === state);
        const s = STATE[state];
        const Icon = s.icon;
        return (
          <div key={state} className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <Icon size={14} style={{ color: s.color }} />
              <span className="text-sm font-semibold">{title}</span>
              <span className="text-[11px]" style={muted}>{sub} · {list.length}</span>
              {state !== "complete" && list.length > 0 && <span className="ml-auto font-mono text-xs" style={{ color: s.color }}>{peso(list.reduce((a, o) => a + o.balance, 0))} owed</span>}
            </div>
            {!list.length && <span className="text-[11px] pl-6" style={{ color: "var(--faint)" }}>None.</span>}
            {list.map((o) => (
              <div key={o.id} className="rounded-lg" style={{ background: "var(--panelAlt)", border: `1px solid ${open === o.id ? s.color : "transparent"}` }}>
                <button onClick={() => setOpen((x) => (x === o.id ? null : o.id))} className="w-full flex items-center gap-2 px-3 py-2 text-xs text-left">
                  {open === o.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  <span className="flex-1 min-w-0 truncate font-medium">{o.customer}
                    <span className="font-normal text-[10px] ml-1.5" style={muted}>{o.lastPaid ? `last paid ${shortDate(o.lastPaid)}` : `order of ${shortDate(o.date)}`}</span></span>
                  {[...new Set(o.payments.map((p) => p.method || "none"))].map((m) => <MethodChip key={m} m={m === "none" ? null : m} />)}
                  <span className="font-mono shrink-0 w-32 text-right">{peso(o.paid)} <span style={muted}>/ {peso(o.total)}</span></span>
                </button>
                {open === o.id && (
                  <div className="px-3 pb-3 flex flex-col gap-1.5">
                    <div className="h-1.5 rounded-full overflow-hidden ml-5" style={{ background: "var(--panel)" }}>
                      <div className="h-full rounded-full" style={{ width: `${Math.min(100, (o.paid / o.total) * 100)}%`, background: s.color }} />
                    </div>
                    <div className="text-[10px] ml-5" style={muted}>order of {niceDate(o.date)} · delivered {o.delivered}{o.orderStatus === "done" ? " · done" : ""}</div>
                    {o.payments.map((p) => (
                      <div key={p.id} className="flex items-center gap-2 text-[11px] rounded-md px-2 py-1 ml-5" style={{ background: "var(--panel)" }}>
                        <span className="font-mono w-24 shrink-0" style={muted}>{niceDate(p.date)}</span>
                        <MethodChip m={p.method} />
                        <span className="flex-1 min-w-0 truncate" style={muted}>{[p.ref ? `ref ${p.ref}` : "", noteOf(p.note)].filter(Boolean).join(" · ")}</span>
                        {p.hasPhoto && <button onClick={() => setPhoto({ id: p.entryId, amount: p.amount, date: p.date })} style={{ color: ACCENT.cyan }}><Camera size={12} /></button>}
                        {!p.method && (
                          <span className="flex items-center gap-1 shrink-0" title="Say how this payment came in">
                            <button onClick={() => link(p, "cash")} className="text-[10px] px-1.5 rounded" style={{ border: `1px solid ${CASH_COLOR}`, color: CASH_COLOR }}>Cash</button>
                            <button onClick={() => link(p, "bank")} className="text-[10px] px-1.5 rounded" style={{ border: `1px solid ${BANK_COLOR}`, color: BANK_COLOR }}>Bank</button>
                          </span>
                        )}
                        <span className="font-mono shrink-0">{peso(p.amount)}</span>
                      </div>
                    ))}
                    {!o.payments.length && <span className="text-[11px] ml-5" style={muted}>No payment recorded yet.</span>}
                    {o.balance > 0 && (paying === o.id ? (
                      <PayOrder order={o} onCancel={() => setPaying(null)} onDone={() => { setPaying(null); onChanged(); }} />
                    ) : (
                      <button onClick={() => setPaying(o.id)} className="self-start ml-5 mt-1 flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg font-semibold text-black/85" style={{ background: ACCENT.cyan }}>
                        <Plus size={12} /> Record a payment ({peso(o.balance)} left)</button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })}
      <PhotoModal entry={photo} onClose={() => setPhoto(null)} />
    </div>
  );
}

// one payment for an order: Cash or Bank, amount, date, note
function PayOrder({ order, onCancel, onDone }) {
  const [f, setF] = useState({ method: "cash", amount: String(order.balance), date: todayStr(), note: "" });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await api.post("/api/shop/payments/entries", { method: f.method, direction: "in", amount: Number(f.amount), date: f.date, note: f.note, orderId: order.id });
      onDone();
    } catch { /* toast */ } finally { setBusy(false); }
  }
  return (
    <div className="ml-5 mt-1 rounded-xl p-3 flex flex-col gap-2" style={{ border: `1px dashed ${ACCENT.cyan}`, background: "var(--panel)" }}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold flex-1">Payment from {order.customer}</span>
        <CashBank value={f.method} onChange={(m) => setF((x) => ({ ...x, method: m }))} />
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: "120px 160px minmax(0,1fr)" }}>
        <label className="flex flex-col gap-1"><Label>Amount</Label>
          <input className={inputCls} style={inputSt} type="number" value={f.amount} onChange={(e) => setF((x) => ({ ...x, amount: e.target.value }))} autoFocus /></label>
        <label className="flex flex-col gap-1"><Label>Date</Label><DatePicker value={f.date} onChange={(v) => setF((x) => ({ ...x, date: v || todayStr() }))} /></label>
        <label className="flex flex-col gap-1"><Label>Note (optional)</Label>
          <input className={inputCls} style={inputSt} value={f.note} onChange={(e) => setF((x) => ({ ...x, note: e.target.value }))} placeholder="e.g. 2nd half" /></label>
      </div>
      {Number(f.amount) > order.balance + 0.005 && <span className="text-[11px]" style={{ color: ACCENT.amber }}>That is more than the {peso(order.balance)} still owed.</span>}
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="text-xs px-3 py-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }}>Cancel</button>
        <button onClick={save} disabled={busy || !(Number(f.amount) > 0)} className="text-xs px-3 py-1.5 rounded-lg font-semibold text-black/85 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
          {busy ? "Saving…" : `Save ${f.method} payment`}</button>
      </div>
    </div>
  );
}

// ---------------- payments of one day ----------------
// order payments (Sales -> Add Order) first, then each sales person's sales; each line: Cash or Bank + the time
function DayView({ date, setDate, onOpenOrders, onChanged }) {
  const [d, setD] = useState(null);
  const [open, setOpen] = useState({});
  const load = () => api.get(`/api/shop/payments/day?date=${date}`).then(setD).catch(() => {});
  useEffect(() => { setD(null); load(); /* eslint-disable-next-line */ }, [date]);
  async function switchPay(x) {
    const to = x.payMode === "bank" ? "cash" : "bank";
    try { await api.put(`/api/shop/sales/${x.id}/payment`, { mode: to }); await load(); onChanged(); } catch { /* toast */ }
  }
  async function switchOrderPay(p) {
    try {
      if (p.entryId) await api.put(`/api/shop/payments/entries/${p.entryId}/method`, { mode: p.kind === "bank" ? "cash" : "bank" });
      else await api.post(`/api/shop/payments/link?paymentId=${p.id}&method=bank`);
      await load(); onChanged();
    } catch { /* toast */ }
  }
  const shift = (n) => { const x = new Date(`${date}T00:00:00`); x.setDate(x.getDate() + n); setDate(`${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`); };
  const row = "flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-md";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="text-base font-semibold mr-2">Payments of the day</h3>
        <button onClick={() => shift(-1)} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }} title="Day before"><ChevronLeft size={14} /></button>
        <div className="w-44"><DatePicker value={date} onChange={(v) => setDate(v || todayStr())} /></div>
        <button onClick={() => shift(1)} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }} title="Next day"><ChevronRight size={14} /></button>
        {date !== todayStr() && <button onClick={() => setDate(todayStr())} className="text-[11px] px-2 py-1 rounded-md" style={{ color: ACCENT.cyan }}>Today</button>}
        {d && <span className="ml-auto font-mono text-sm font-semibold">{peso(d.total)}</span>}
      </div>
      {!d ? <p className="text-xs" style={muted}>Loading…</p> : (
        <>
          {d.byMethod.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {d.byMethod.map((m) => (
                <div key={m.method} className="rounded-lg px-3 py-1.5 flex items-center gap-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                  <MethodChip m={methodOf(m.method)} />
                  <span className="font-mono text-xs font-semibold">{peso(m.amount)}</span>
                </div>
              ))}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-1.5"><Landmark size={13} style={{ color: ACCENT.amber }} />
              <span className="text-[11px] font-semibold tracking-wide uppercase" style={{ color: ACCENT.amber }}>Sales orders (Add Order)</span>
              <span className="ml-auto text-[11px] font-mono" style={muted}>{peso(d.orders.reduce((a, o) => a + o.total, 0))}</span></div>
            {d.orders.map((o) => (
              <div key={o.orderId} className="rounded-lg px-3 py-2 flex flex-col gap-1" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium flex-1 truncate">{o.customer}</span>
                  <StateTag state={o.state} />
                  <span className="text-[10px]" style={muted}>order {peso(o.orderPaid)} / {peso(o.orderTotal)}</span>
                </div>
                {o.payments.map((p) => (
                  <div key={p.id} className={row} style={{ background: "var(--panel)" }}>
                    <span className="font-mono w-11 shrink-0" style={muted}>{p.time || "—"}</span>
                    <MethodChip m={methodOf(p.method)} />
                    <span className="flex-1 min-w-0 truncate" style={muted}>{[p.ref ? `ref ${p.ref}` : "", noteOf(p.note)].filter(Boolean).join(" · ")}</span>
                    <button type="button" onClick={() => switchOrderPay(p)} className="flex items-center gap-1 px-1.5 py-0.5 rounded shrink-0 text-[10px]"
                      style={{ border: "1px solid var(--border)", color: ACCENT.cyan }} title="Wrong? Switch it">
                      <ArrowLeftRight size={10} /> make it {p.kind === "bank" ? "cash" : "bank"}</button>
                    <span className="font-mono">{peso(p.amount)}</span>
                  </div>
                ))}
              </div>
            ))}
            {!d.orders.length && <span className="text-xs pl-5" style={{ color: "var(--faint)" }}>No order payment this day.</span>}
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-1.5"><Banknote size={13} style={{ color: ACCENT.cyan }} />
              <span className="text-[11px] font-semibold tracking-wide uppercase" style={{ color: ACCENT.cyan }}>Sales by sales person</span>
              <span className="ml-auto text-[11px] font-mono" style={muted}>{peso(d.people.reduce((a, p) => a + p.total, 0))}</span></div>
            {d.people.map((p) => (
              <div key={p.person} className="rounded-lg" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                <button onClick={() => setOpen((x) => ({ ...x, [p.person]: !x[p.person] }))} className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left">
                  {open[p.person] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  <span className="font-medium flex-1 truncate">{p.person}</span>
                  {[...new Set(p.sales.map((x) => x.payMode))].map((m) => <MethodChip key={m} m={m} />)}
                  <span className="text-[10px]" style={muted}>{p.sales.length} sale{p.sales.length === 1 ? "" : "s"}</span>
                  <span className="font-mono font-semibold w-24 text-right">{peso(p.total)}</span>
                </button>
                {open[p.person] && (
                  <div className="px-3 pb-2 flex flex-col gap-0.5">
                    {p.sales.map((x) => {
                      const k = `s${x.id}`;
                      return (
                        <div key={x.id} className="rounded-md" style={{ background: "var(--panel)" }}>
                          <button type="button" onClick={() => setOpen((o) => ({ ...o, [k]: !o[k] }))} className={`${row} w-full text-left`} title="Show the items of this sale">
                            {open[k] ? <ChevronDown size={12} style={muted} /> : <ChevronRight size={12} style={muted} />}
                            <span className="font-mono w-11 shrink-0" style={muted}>{x.time}</span>
                            <MethodChip m={x.payMode} />
                            <span className="flex-1 min-w-0 truncate" style={muted}>{x.items} item{x.items === 1 ? "" : "s"}
                              {x.why ? <span className="text-[10px]" style={{ color: "var(--faint)" }}> · {x.why}</span> : null}</span>
                            <span className="font-mono">{peso(x.total)}</span>
                          </button>
                          {open[k] && (
                            <div className="pl-9 pr-2.5 pb-2 flex flex-col gap-0.5">
                              <div className="flex items-center gap-2 text-[10px] pb-1" style={muted}>
                                <ScanLine size={11} /> <span className="flex-1 min-w-0 truncate">Paid by {x.payMode}{x.why ? ` — ${x.why}` : ""}</span>
                                <button type="button" onClick={() => switchPay(x)} className="flex items-center gap-1 px-1.5 py-0.5 rounded shrink-0"
                                  style={{ border: "1px solid var(--border)", color: ACCENT.cyan }} title="The receipt reader got it wrong? Switch it">
                                  <ArrowLeftRight size={10} /> make it {x.payMode === "bank" ? "cash" : "bank"}</button>
                              </div>
                              {x.lines.map((l, i) => (
                                <div key={i} className="flex items-center gap-2 text-[11px] px-2 py-1 rounded" style={{ background: "var(--panelAlt)" }}>
                                  <span className="font-mono w-20 shrink-0 text-right" style={muted}>{Number(l.qty).toLocaleString()} {l.unit}</span>
                                  <span className="flex-1 min-w-0 truncate">{l.item}</span>
                                  <span className="font-mono shrink-0" style={muted}>× {peso(l.price)}</span>
                                  <span className="font-mono shrink-0 w-20 text-right">{peso(l.amount)}</span>
                                </div>
                              ))}
                              {!x.lines.length && <span className="text-[11px]" style={{ color: "var(--faint)" }}>No item lines for this sale.</span>}
                              {x.lines.length > 0 && Math.abs(x.lines.reduce((a, l) => a + l.amount, 0) - x.total) > 0.5 && (
                                <span className="text-[10px]" style={{ color: ACCENT.amber }}>The items add up to {peso(x.lines.reduce((a, l) => a + l.amount, 0))}; the sale was saved as {peso(x.total)}.</span>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
            {!d.people.length && <span className="text-xs pl-5" style={{ color: "var(--faint)" }}>No sales this day.</span>}
            {d.people.length > 0 && (
              <span className="text-[10px] pl-5" style={{ color: "var(--faint)" }}>
                Cash or bank is read from each receipt photo: CASH SALES ticked = cash; a PAID or PAID IN BANK stamp (or “bank”, “GCash”,
                “InstaPay”… written on it) = bank; nothing = cash. Open a sale to switch it.
              </span>
            )}
          </div>

          {d.other.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold tracking-wide uppercase" style={muted}>Other money in / out</span>
              {d.other.map((x) => (
                <div key={x.id} className={row} style={{ background: "var(--panelAlt)" }}>
                  <span className="font-mono w-11 shrink-0" style={muted}>{x.time}</span>
                  <MethodChip m={methodOf(x.method)} />
                  <span className="flex-1 min-w-0 truncate">{x.party || x.note || (x.direction === "in" ? "Money in" : "Money out")}{x.ref ? <span style={muted}> · ref {x.ref}</span> : null}</span>
                  <span className="font-mono" style={{ color: x.direction === "in" ? ACCENT.cyan : ACCENT.danger }}>{x.direction === "in" ? "+" : "−"}{peso(x.amount)}</span>
                </div>
              ))}
            </div>
          )}
          <button onClick={onOpenOrders} className="self-start text-[11px]" style={{ color: ACCENT.cyan }}>See who still owes →</button>
        </>
      )}
    </div>
  );
}

// ---------------- the page ----------------
export default function PaymentsPage() {
  const [d, setD] = useState(null);
  const [view, setView] = useState({ kind: "day" });
  const [day, setDay] = useState(todayStr());
  const load = () => api.get("/api/shop/payments").then(setD).catch(() => {});
  useEffect(() => { load(); }, []);
  const owes = useMemo(() => (d ? d.orders.filter((o) => o.state !== "complete") : []), [d]);
  const done = useMemo(() => (d ? d.orders.filter((o) => o.state === "complete") : []), [d]);
  const openDay = (date) => { setDay(date); setView({ kind: "day" }); };

  function NavBtn({ active, onClick, icon: Icon, color, title, sub }) {
    return (
      <button onClick={onClick} className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-left"
        style={{ background: active ? `${color}1c` : "transparent", border: `1px solid ${active ? color : "transparent"}` }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: `${color}22`, color }} className="flex items-center justify-center shrink-0"><Icon size={15} /></div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold truncate">{title}</div>
          <div className="text-[10px] truncate" style={muted}>{sub}</div>
        </div>
      </button>
    );
  }

  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Payments</h2>
          <p className="text-xs" style={muted}>Cash or bank, per sale and per order payment · who still owes</p>
        </div>
        {d && (
          <div className="flex gap-2">
            {[["Cash this month", d.cashMonth.total, CASH_COLOR], ["Bank this month", d.bankMonth.total, BANK_COLOR]].map(([l, v, c]) => (
              <div key={l} className="rounded-lg px-3 py-1.5 text-right" style={card}>
                <div className="text-[10px]" style={muted}>{l}</div>
                <div className="font-mono text-sm font-semibold" style={{ color: c }}>{peso(v)}</div>
              </div>
            ))}
            <div className="rounded-lg px-3 py-1.5 text-right" style={card}>
              <div className="text-[10px]" style={muted}>Customers still owe</div>
              <div className="font-mono text-sm font-semibold" style={{ color: d.owed > 0 ? ACCENT.amber : "#22c55e" }}>{peso(d.owed)}</div>
            </div>
          </div>
        )}
      </div>

      {!d ? <p className="text-xs" style={muted}>Loading…</p> : (
        <div className="flex gap-5 items-start">
          <div className="w-64 shrink-0 rounded-xl p-2 flex flex-col gap-0.5" style={card}>
            <NavBtn active={view.kind === "day"} color={ACCENT.cyan} icon={CalendarDays} title="Payments by day"
              sub="Orders and each sales person, cash or bank" onClick={() => setView({ kind: "day" })} />
            <div className="my-1.5" style={{ borderTop: "1px solid var(--border)" }} />
            <NavBtn active={view.kind === "cash"} color={CASH_COLOR} icon={Banknote} title="Cash payments"
              sub={`${peso(d.cash.total)} in all · ${peso(d.cashMonth.total)} this month`} onClick={() => setView({ kind: "cash" })} />
            <NavBtn active={view.kind === "bank"} color={BANK_COLOR} icon={Landmark} title="Bank payments"
              sub={`${peso(d.bank.total)} in all · ${peso(d.bankMonth.total)} this month`} onClick={() => setView({ kind: "bank" })} />
            <div className="my-1.5" style={{ borderTop: "1px solid var(--border)" }} />
            <span className="text-[10px] font-semibold tracking-wide px-2 pt-1 pb-1" style={muted}>ORDERS (SALES → ADD ORDER)</span>
            <NavBtn active={view.kind === "owes"} color={ACCENT.danger} icon={AlertTriangle} title="Incomplete & missing"
              sub={`${owes.length} customer${owes.length === 1 ? "" : "s"} · ${peso(d.owed)} owed`} onClick={() => setView({ kind: "owes" })} />
            <NavBtn active={view.kind === "done"} color="#22c55e" icon={CheckCircle2} title="Completed payments"
              sub={`${done.length} order${done.length === 1 ? "" : "s"} paid in full`} onClick={() => setView({ kind: "done" })} />
          </div>

          <div className="flex-1 min-w-0 rounded-xl p-5" style={card}>
            {view.kind === "day" && <DayView date={day} setDate={setDay} onOpenOrders={() => setView({ kind: "owes" })} onChanged={load} />}
            {(view.kind === "cash" || view.kind === "bank") && <PayList key={view.kind} kind={view.kind} onOpenDay={openDay} />}
            {view.kind === "owes" && <OrdersView which="owes" orders={d.orders} onChanged={load} />}
            {view.kind === "done" && <OrdersView which="done" orders={d.orders} onChanged={load} />}
          </div>
        </div>
      )}
    </div>
  );
}
