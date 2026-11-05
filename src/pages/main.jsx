import React, { useEffect, useMemo, useRef, useState } from "react";
import { Upload, X, ShoppingCart, Users, AlertTriangle, Banknote, MessageCircle, ClipboardList, Smartphone } from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { ACCENT, CARD_SHADOW, useTheme } from "../lib/theme";
import { Modal, RangeSelect, StatCard } from "../components/kit";
import { api } from "../lib/api";
import { useNavigate } from "react-router-dom";
import StaffPanel, { staffRows } from "../components/staffPanel";
import { DraftOrderModal } from "./sales";

const DELIVERY_GREEN = "#22c55e";

const VERSES = [
  { text: "Commit to the Lord whatever you do, and He will establish your plans.", ref: "Proverbs 16:3" },
  { text: "Whatever you do, work at it with all your heart, as working for the Lord.", ref: "Colossians 3:23" },
  { text: "The Lord is my shepherd, I lack nothing.", ref: "Psalm 23:1" },
  { text: "I can do all this through Him who gives me strength.", ref: "Philippians 4:13" },
  { text: "Trust in the Lord with all your heart and lean not on your own understanding.", ref: "Proverbs 3:5" },
  { text: "Let all that you do be done in love.", ref: "1 Corinthians 16:14" },
  { text: "Be strong and courageous. Do not be afraid; the Lord your God goes with you.", ref: "Deuteronomy 31:6" },
];

// Priority cards are made by the server from the real data: low stock, loans / payments / maintenance due
// within 3 days, late bulk orders, deliveries today, receipts the phones have not updated yet.

// Priority colours: the server ranks every card by urgency (A-level stock and the closest deadlines first)
const SEVERITY = {
  critical: { color: ACCENT.danger, label: "Critical" },
  high: { color: ACCENT.amber, label: "High" },
  medium: { color: "#e8d44d", label: "Medium" },
  low: { color: "var(--muted)", label: "Low" },
};
const LEVEL_COLOR = { A: ACCENT.danger, B: ACCENT.amber, C: "#e8d44d" };
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const inputCls = "w-full text-sm rounded-lg px-3 py-2 outline-none";
const inputSt = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };

// Fix a stock item right here: record stock that arrived, change its A-B-C levels, or correct the count.
// a = { id, name, unit, qty, costPrice, levelA, levelB, levelC }. Used by the Priority cards and the Low Stock list.
function ItemFix({ a, onDone, compact }) {
  const navigate = useNavigate();
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState(a.costPrice ?? "");
  const [levels, setLevels] = useState({ levelA: a.levelA ?? "", levelB: a.levelB ?? "", levelC: a.levelC ?? "" });
  const [count, setCount] = useState("");
  const lvl = (v) => (String(v ?? "").trim() === "" ? null : Number(v));
  async function run(fn, msg) {
    try { await fn(); onDone(msg); } catch { /* toast */ }
  }
  const box = "rounded-lg p-3 flex flex-col gap-2";
  const boxSt = { background: compact ? "var(--panel)" : "var(--panelAlt)", border: "1px solid var(--border)" };
  const btn = { background: ACCENT.cyan };
  return (
    <>
      <div className={box} style={boxSt}>
        <p className="text-xs font-semibold">Stock arrived — record it</p>
        <div className="flex gap-2">
          <input type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)} placeholder={`qty (${a.unit})`} className={inputCls} style={inputSt} />
          <input type="number" min={0} value={cost} onChange={(e) => setCost(e.target.value)} placeholder={`paid per ${a.unit}`} className={inputCls} style={inputSt} />
          <button disabled={!(Number(qty) > 0) || cost === ""} style={btn} className="px-3 rounded-lg text-xs font-semibold text-black/85 shrink-0 disabled:opacity-40"
            onClick={() => run(() => api.post(`/api/shop/supply/items/${a.id}/purchase`, { qty: Number(qty), unitCost: Number(cost) }), `Added ${qty} ${a.unit} to ${a.name || "the item"}.`)}>Add</button>
        </div>
        <p className="text-[10px]" style={{ color: "var(--faint)" }}>Paying below the standard cost ({a.costPrice}) counts as extra profit.</p>
      </div>
      <div className={box} style={boxSt}>
        <p className="text-xs font-semibold">Change its A-B-C levels</p>
        <div className="flex items-center gap-2 flex-wrap">
          {[["levelA", "A"], ["levelB", "B"], ["levelC", "C"]].map(([k, L]) => (
            <label key={k} className="flex items-center gap-1 text-[11px] font-semibold whitespace-nowrap" style={{ color: LEVEL_COLOR[L] }}>
              {L} ≤ <input type="number" min={0} value={levels[k]} placeholder="–" onChange={(e) => setLevels((x) => ({ ...x, [k]: e.target.value }))}
                className="w-14 text-xs rounded-md px-1.5 py-1 outline-none font-normal" style={inputSt} />
            </label>
          ))}
          <button style={btn} className="ml-auto px-3 py-1.5 rounded-lg text-xs font-semibold text-black/85"
            onClick={() => run(() => api.patch(`/api/shop/supply/items/${a.id}`, { levelA: lvl(levels.levelA), levelB: lvl(levels.levelB), levelC: lvl(levels.levelC) }), "Levels saved.")}>Save</button>
        </div>
      </div>
      <div className={box} style={boxSt}>
        <p className="text-xs font-semibold">The count is wrong — set the real count</p>
        <div className="flex gap-2">
          <input type="number" min={0} value={count} onChange={(e) => setCount(e.target.value)} placeholder={`now ${a.qty} ${a.unit}`} className={inputCls} style={inputSt} />
          <button disabled={count === ""} style={btn} className="px-3 rounded-lg text-xs font-semibold text-black/85 shrink-0 disabled:opacity-40"
            onClick={() => run(() => api.post(`/api/shop/supply/items/${a.id}/adjust`, { delta: Number(count) - a.qty, note: "corrected from the dashboard" }), "Count corrected.")}>Save</button>
        </div>
      </div>
      <button onClick={() => navigate("/supply")} className="text-xs self-start" style={{ color: ACCENT.cyan }}>Open Supply →</button>
    </>
  );
}

// An item added to Supply with quantity N/A ("not sure yet"): type the count here. The card stays until you do.
function CountFix({ a, onDone }) {
  const [count, setCount] = useState("");
  const n = Number(count);
  const ok = count.trim() !== "" && Number.isFinite(n) && n >= 0;
  async function save() {
    if (!ok) return;
    try { await api.post(`/api/shop/supply/items/${a.id}/count`, { qty: n }); onDone(`${a.name}: ${n} ${a.unit} counted.`); } catch { /* toast */ }
  }
  return (
    <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: "var(--panelAlt)", border: `1px solid ${ACCENT.amber}66` }}>
      <p className="text-xs font-semibold">How many {a.unit} of {a.name} are there now?</p>
      <div className="flex gap-2">
        <input autoFocus type="number" min={0} step="any" value={count} onChange={(e) => setCount(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); }} placeholder={`count (${a.unit})`} className={inputCls} style={inputSt} />
        <button disabled={!ok} onClick={save} style={{ background: ACCENT.cyan }}
          className="px-4 rounded-lg text-xs font-semibold text-black/85 shrink-0 disabled:opacity-40">Save count</button>
      </div>
      <p className="text-[10px]" style={{ color: "var(--faint)" }}>From now on sales take the stock down from this number, and the low-stock levels work for it.</p>
    </div>
  );
}

// "Low Stock" card: every item at its A / B / C level, most urgent first; tap one to restock / fix it here.
function LowStockModal({ items, onClose, onChanged }) {
  const [open, setOpen] = useState(null);
  const [done, setDone] = useState("");
  return (
    <Modal title={`Low stock — ${items.length} item${items.length === 1 ? "" : "s"}`} onClose={onClose} width={520}>
      <div className="flex flex-col gap-2">
        {items.length === 0 && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>Nothing is low. Set A / B / C levels in Supply so items show up here when they run low.</p>}
        {done && <p className="text-xs" style={{ color: ACCENT.cyan }}>{done}</p>}
        {items.map((x) => (
          <div key={x.id} className="rounded-lg" style={{ border: `1px solid ${open === x.id ? ACCENT.cyan : "var(--border)"}`, background: "var(--panelAlt)" }}>
            <button onClick={() => setOpen((o) => (o === x.id ? null : x.id))} className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left">
              <span className="text-[10px] font-bold w-6 h-6 rounded-full flex items-center justify-center shrink-0"
                style={{ color: LEVEL_COLOR[x.level], background: `${LEVEL_COLOR[x.level]}22`, border: `1px solid ${LEVEL_COLOR[x.level]}66` }}>{x.level}</span>
              <span className="flex-1 min-w-0">
                <span className="block text-sm font-medium truncate">{x.name}</span>
                <span className="block text-[10px]" style={{ color: "var(--muted)" }}>
                  A ≤ {x.levelA ?? "–"} · B ≤ {x.levelB ?? "–"} · C ≤ {x.levelC ?? "–"}
                </span>
              </span>
              <span className="font-mono text-sm shrink-0" style={{ color: LEVEL_COLOR[x.level] }}>{x.quantity} <span className="text-[10px]" style={{ color: "var(--muted)" }}>{x.unit}</span></span>
              <span className="text-[11px] font-semibold px-2.5 py-1 rounded-md shrink-0" style={{ background: ACCENT.cyan, color: "#000" }}>{open === x.id ? "Close" : "Restock / edit"}</span>
            </button>
            {open === x.id && (
              <div className="px-3 pb-3 flex flex-col gap-2">
                <ItemFix compact a={{ id: x.id, name: x.name, unit: x.unit, qty: x.quantity, costPrice: x.costPrice, levelA: x.levelA, levelB: x.levelB, levelC: x.levelC }}
                  onDone={(msg) => { setDone(msg); setOpen(null); onChanged(); }} />
              </div>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// "Today's Sales" card: the sales persons' receipts + desktop sales + orders added in Sales (Add Order) = the total
function SalesModal({ onClose }) {
  const [period, setPeriod] = useState("day");
  const [d, setD] = useState(null);
  useEffect(() => { setD(null); api.get(`/api/shop/sales-summary?period=${period}`).then(setD).catch(() => {}); }, [period]);
  const peso = (n) => `₱${Math.round(Number(n) || 0).toLocaleString()}`;
  const row = "flex items-center justify-between px-3 py-2 rounded-md text-sm";
  return (
    <Modal title="Sales" onClose={onClose} width={500}>
      <div className="flex flex-col gap-3">
        <div className="flex rounded-lg p-0.5 self-start" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {[["day", "Today"], ["week", "This week"], ["month", "This month"], ["all", "All"]].map(([k, l]) => (
            <button key={k} onClick={() => setPeriod(k)} className="text-[11px] px-2.5 py-1 rounded-md font-medium"
              style={period === k ? { background: ACCENT.cyan, color: "#000" } : { color: "var(--muted)" }}>{l}</button>
          ))}
        </div>
        {!d ? <p className="text-xs" style={{ color: "var(--muted)" }}>Loading…</p> : (
          <>
            <p className="text-[10px] font-semibold tracking-wide" style={{ color: "var(--muted)" }}>SALES PERSONS (RECEIPTS FROM THE PHONES)</p>
            {d.people.length ? d.people.map((p) => (
              <div key={p.userId} className={row} style={{ background: "var(--panelAlt)" }}>
                <span>{p.name} <span className="text-[11px]" style={{ color: "var(--muted)" }}>· {p.count} sale{p.count === 1 ? "" : "s"}</span></span>
                <span className="font-mono">{peso(p.total)}</span>
              </div>
            )) : <p className="text-xs" style={{ color: "var(--faint)" }}>No receipts sold yet.</p>}
            {d.desktop.count > 0 && (
              <div className={row} style={{ background: "var(--panelAlt)" }}>
                <span>Desktop sales <span className="text-[11px]" style={{ color: "var(--muted)" }}>· {d.desktop.count}</span></span>
                <span className="font-mono">{peso(d.desktop.total)}</span>
              </div>
            )}
            <p className="text-[10px] font-semibold tracking-wide mt-1" style={{ color: "var(--muted)" }}>ORDERS ADDED IN SALES (ADD ORDER)</p>
            {d.orders.length ? d.orders.map((o) => (
              <div key={o.id} className={row} style={{ background: "var(--panelAlt)" }}>
                <span className="min-w-0 truncate">{o.customer} <span className="text-[11px]" style={{ color: "var(--muted)" }}>· {o.date} · paid {peso(o.paid)}</span></span>
                <span className="font-mono shrink-0">{peso(o.total)}</span>
              </div>
            )) : <p className="text-xs" style={{ color: "var(--faint)" }}>No orders added.</p>}
            <div className="mt-1 pt-2.5 flex flex-col gap-1 text-sm" style={{ borderTop: "1px solid var(--border)" }}>
              <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Sales persons</span><span className="font-mono">{peso(d.counters)}</span></div>
              {d.desktop.count > 0 && <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Desktop</span><span className="font-mono">{peso(d.desktop.total)}</span></div>}
              <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Orders (Add Order)</span><span className="font-mono">{peso(d.ordersTotal)}</span></div>
              <div className="flex justify-between font-semibold"><span>Total</span><span className="font-mono" style={{ color: ACCENT.cyan }}>{peso(d.total)}</span></div>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// "Read" on a priority card: explains it AND lets you fix it here, so it leaves the list.
function PriorityModal({ m, onClose, onChanged }) {
  const a = m.action || {};
  const navigate = useNavigate();
  const [amount, setAmount] = useState(a.amount ?? "");
  const [done, setDone] = useState("");
  const sev = SEVERITY[m.severity] || SEVERITY.low;
  async function run(fn, msg) {
    try { await fn(); setDone(msg); onChanged(); setTimeout(onClose, 1400); } catch { /* toast */ }
  }
  const box = "rounded-lg p-3 flex flex-col gap-2";
  const boxSt = { background: "var(--panelAlt)", border: "1px solid var(--border)" };
  const btn = { background: ACCENT.cyan };
  return (
    <Modal title={m.title} onClose={onClose} width={440}>
      <div className="flex flex-col gap-3">
        <span className="self-start text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ color: sev.color, background: `${sev.color}1f`, border: `1px solid ${sev.color}55` }}>
          {sev.label} priority{m.level ? ` · level ${m.level}` : ""}
        </span>
        <p className="text-sm leading-relaxed">{m.body}</p>

        {a.type === "item" && <ItemFix a={a} onDone={(msg) => { setDone(msg); onChanged(); setTimeout(onClose, 1400); }} />}
        {a.type === "count" && <CountFix a={a} onDone={(msg) => { setDone(msg); onChanged(); setTimeout(onClose, 1400); }} />}

        {(a.type === "obligation" || a.type === "maintenance") && (
          <div className={box} style={boxSt}>
            <p className="text-xs font-semibold">Paid? Record it</p>
            {a.type === "obligation" ? (
              <div className="flex gap-2">
                <input type="number" min={0} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="amount" className={inputCls} style={inputSt} />
                <button disabled={!(Number(amount) > 0)} style={btn} className="px-3 rounded-lg text-xs font-semibold text-black/85 shrink-0 disabled:opacity-40"
                  onClick={() => run(() => api.post(`/api/shop/entries/${a.id}/log/payments`, { amount: Number(amount), note: "from the dashboard" }), "Payment recorded.")}>Paid</button>
              </div>
            ) : (
              <button style={btn} className="self-start px-3 py-2 rounded-lg text-xs font-semibold text-black/85"
                onClick={() => run(() => api.put(`/api/shop/entries/${a.id}`, { lastPaid: localToday() }), "Marked as paid today.")}>Paid today ({`₱${Math.round(a.amount || 0).toLocaleString()}`})</button>
            )}
            <button onClick={() => navigate("/cashflow")} className="text-xs self-start" style={{ color: ACCENT.cyan }}>Open Cash Flow →</button>
          </div>
        )}
        {a.type === "order" && (
          <button onClick={() => navigate("/sales")} style={btn} className="self-start px-3 py-2 rounded-lg text-xs font-semibold text-black/85">Open the order in Sales →</button>
        )}
        {done && <p className="text-xs" style={{ color: ACCENT.cyan }}>{done} The list is updated.</p>}
      </div>
    </Modal>
  );
}

export default function MainPage() {
  const { T } = useTheme();
  const navigate = useNavigate();
  const dayIdx = useMemo(() => Math.floor(Date.now() / 86400000) % VERSES.length, []);
  const [messages, setMessages] = useState([]);
  const [photoOpen, setPhotoOpen] = useState(false);
  const [photoWho, setPhotoWho] = useState("");
  const [photoVer, setPhotoVer] = useState(0);
  const fileRef = useRef(null);
  const loadPriority = () => api.get("/api/shop/priority").then((d) => setMessages(d.map((m) => ({ ...m, id: m.key })))).catch(() => {});
  useEffect(() => { loadPriority(); const t = setInterval(loadPriority, 20000); return () => clearInterval(t); }, []);
  function dismiss(m) {
    setMessages((prev) => prev.filter((x) => x.id !== m.id));
    api.post("/api/shop/priority/dismiss", { key: m.key }).catch(() => {});
  }
  const [readMsg, setReadMsg] = useState(null);
  const [draftId, setDraftId] = useState(null);            // a delivery order read from a phone's receipt
  const [attendance, setAttendance] = useState([]);
  const [cash, setCash] = useState(null);
  const [cashOpen, setCashOpen] = useState(false);
  const [cashDir, setCashDir] = useState("in");
  const [cashAmount, setCashAmount] = useState("");
  const [cashNote, setCashNote] = useState("");
  const [cashErr, setCashErr] = useState("");
  const toRows = staffRows;
  const loadAttendance = () => api.get("/api/shop/attendance").then((d) => setAttendance(toRows(d))).catch(() => {});
  async function uploadStaffPhoto(file) {
    if (!photoWho || !file) return;
    try {
      await api.upload(`/api/shop/entries/emp-${photoWho}/photo`, file);
      setPhotoVer((v) => v + 1);
      setPhotoOpen(false);
      loadAttendance();
    } catch { /* toast */ }
  }
  useEffect(() => { loadAttendance(); /* eslint-disable-next-line */ }, []);
  const loadCash = () => api.get("/api/shop/cash").then(setCash).catch(() => {});
  useEffect(() => { loadCash(); const t = setInterval(loadCash, 15000); return () => clearInterval(t); }, []);
  async function saveCash() {
    const amount = Number(cashAmount);
    if (!(amount > 0)) { setCashErr("Enter an amount above 0."); return; }
    try {
      setCash(await api.post("/api/shop/cash", { direction: cashDir, amount, note: cashNote.trim() }));
      setCashOpen(false); setCashAmount(""); setCashNote(""); setCashErr("");
    } catch (e) { setCashErr(e.message); }
  }
  const [lowOpen, setLowOpen] = useState(false);
  const [salesOpen, setSalesOpen] = useState(false);
  const [today, setToday] = useState(null);
  const loadToday = () => api.get("/api/shop/sales-summary?period=day").then(setToday).catch(() => {});
  useEffect(() => { loadToday(); const t = setInterval(loadToday, 15000); return () => clearInterval(t); }, []);
  const [range, setRange] = useState(5);
  const [chartData, setChartData] = useState([]);
  const [stats, setStats] = useState(null);
  useEffect(() => { api.get(`/api/shop/sales-daily?days=${range}`).then(setChartData).catch(() => {}); }, [range]);
  useEffect(() => {
    const load = () => api.get("/api/shop/stats?range=today").then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  const peso = (n) => `₱${Math.round(Number(n) || 0).toLocaleString()}`;
  const sizeH = { sm: 64, md: 80, lg: 96 };
  const activeStaff = attendance.filter((a) => a.checked).length;

  // check in / out is saved in the database (24-hour time); checking in also adds 1 to the employee's present days
  async function markTime(id) {
    const row = attendance.find((x) => x.id === id);
    try {
      setAttendance(toRows(await api.post("/api/shop/attendance", { employeeId: id, present: !row.checked })));
    } catch { /* keep the old state */ }
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* summary stat row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div onClick={() => setSalesOpen(true)} className="cursor-pointer" title="Tap to see each sales person, desktop sales and orders">
          <StatCard icon={ShoppingCart} label="Today's Sales" value={today ? peso(today.total) : "—"}
            tag={today ? `${today.people.reduce((n, p) => n + p.count, 0) + today.desktop.count} sale(s) · ${today.orders.length} order(s)` : "loading"} tagTone="good" />
        </div>
        <StatCard icon={Users} label="Active Staff" value={`${activeStaff}/${attendance.length}`} tag={activeStaff < attendance.length ? `${attendance.length - activeStaff} pending` : "full"} tagTone={activeStaff < attendance.length ? "warn" : "good"} />
        <div onClick={() => stats && setLowOpen(true)} className="cursor-pointer" title="Tap to see the low items and restock or edit them">
          <StatCard icon={AlertTriangle} label="Low Stock" value={stats ? `${stats.lowStock.length} items` : "—"} tag={stats && stats.lowStock.length ? `${stats.lowStock.filter((x) => x.level === "A").length} at A · ${stats.lowStock.filter((x) => x.level === "B").length} at B` : "all good"}
            tagTone={stats && stats.lowStock.some((x) => x.level === "A") ? "bad" : stats && stats.lowStock.length ? "warn" : "good"} />
        </div>
        <div onClick={() => setCashOpen(true)} className="cursor-pointer" title="Tap to add cash in / out">
          <StatCard icon={Banknote} label="Cash on Hand" value={cash ? peso(cash.onHand) : "—"} tag={cash ? `${peso(cash.inToday)} in today` : "loading"} tagTone="good" />
        </div>
      </div>

      <div className="grid gap-6" style={{ gridTemplateColumns: "minmax(0, 1.7fr) minmax(0, 1fr)" }}>
        <div className="flex flex-col gap-6 min-w-0">
          <div style={{ border: "1px solid var(--border)", background: "linear-gradient(135deg, var(--panel), var(--panelAlt))", boxShadow: CARD_SHADOW }} className="rounded-xl px-6 py-5 text-center">
            <p className="text-[15px] italic leading-relaxed">&ldquo;{VERSES[dayIdx].text}&rdquo;</p>
            <p className="text-xs mt-2 font-mono" style={{ color: ACCENT.cyan }}>{VERSES[dayIdx].ref}</p>
          </div>

          <StaffPanel attendance={attendance} onRows={(d) => setAttendance(toRows(d))} markTime={markTime} photoVer={photoVer}
            onUploadPhoto={() => { setPhotoWho(attendance[0] ? String(attendance[0].id) : ""); setPhotoOpen(true); }} />

          <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold text-sm">Sales Overview</h3>
              <RangeSelect value={range} onChange={setRange} options={[{ value: 5, label: "Last 5 days" }, { value: 15, label: "Last 15 days" }]} />
            </div>
            <div style={{ height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={ACCENT.cyan} stopOpacity={0.5} />
                      <stop offset="100%" stopColor={ACCENT.cyan} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={T.border} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: T.muted }} axisLine={{ stroke: T.border }} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: T.muted }} axisLine={false} tickLine={false} width={34} />
                  <Tooltip contentStyle={{ background: T.panelAlt, border: `1px solid ${T.border}`, fontSize: 12, borderRadius: 8 }} />
                  <Area type="monotone" dataKey="sales" stroke={ACCENT.cyan} strokeWidth={2} fill="url(#salesFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4 flex flex-col min-w-0">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-sm">Priority</h3>
            <span style={{ color: ACCENT.cyan, background: `${ACCENT.cyan}18` }} className="text-[10px] font-semibold px-2 py-0.5 rounded-full">{messages.length} active</span>
          </div>
          <div className="flex flex-col gap-2 overflow-y-auto pr-1" style={{ maxHeight: 560 }}>
            {messages.map((m) => {
              const dlv = m.kind === "delivery";              // a delivery order read from a phone's receipt: green, on top
              const req = m.kind === "request";               // a new item sent from a phone: accept / remove it in Supply
              const color = dlv ? DELIVERY_GREEN : (SEVERITY[m.severity] || SEVERITY.low).color;
              return (
                <div key={m.id} style={{ border: `1px solid ${dlv ? `${DELIVERY_GREEN}80` : "var(--border)"}`, borderLeft: `3px solid ${color}`,
                  background: dlv ? `${DELIVERY_GREEN}12` : "var(--panelAlt)", minHeight: sizeH[m.size], gridTemplateColumns: "16px minmax(0,1fr) 52px" }}
                  className="rounded-lg px-3 py-2.5 grid items-start gap-2">
                  {m.sticky ? (
                    req ? <span title="This card stays until the new item is accepted or removed in Supply" style={{ color: ACCENT.cyan }} className="mt-0.5 shrink-0"><Smartphone size={14} /></span>
                      : <span title="This card stays until the stock is counted" style={{ color: ACCENT.amber }} className="mt-0.5 shrink-0"><ClipboardList size={14} /></span>
                  ) : (
                    <button onClick={() => dismiss(m)} title="Hide this card" style={{ color: "var(--muted)" }} className="hover:text-current mt-0.5 shrink-0">
                      <X size={14} />
                    </button>
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate flex items-center gap-1.5" style={{ color: dlv ? DELIVERY_GREEN : m.severity === "critical" ? ACCENT.danger : undefined }}>
                      {dlv && <MessageCircle size={14} className="shrink-0" />}<span className="truncate">{m.title}</span>
                    </p>
                    <p className="text-xs mt-0.5 line-clamp-2" style={{ color: "var(--muted)" }}>{m.body}</p>
                  </div>
                  <button onClick={() => (dlv ? setDraftId(m.action.id) : req ? navigate(`/supply?request=${m.action.id}`) : setReadMsg(m))}
                    style={{ background: dlv ? DELIVERY_GREEN : ACCENT.cyan }}
                    title={req ? "Open Supply to accept or remove it" : undefined}
                    className="text-[11px] font-semibold text-black/85 px-2.5 py-1 rounded-md h-fit shrink-0">
                    {dlv ? "Open" : req ? "Supply" : m.kind === "count" ? "Count" : "Read"}
                  </button>
                </div>
              );
            })}
            {messages.length === 0 && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>Nothing urgent. Low stock, bills due and late deliveries show up here.</p>}
          </div>
        </div>
      </div>

      {cashOpen && (
        <Modal title="Cash on Hand" onClose={() => setCashOpen(false)} width={420}>
          {cash && (
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mb-3" style={{ color: "var(--muted)" }}>
              <span>Sales</span><span className="font-mono text-right">{peso(cash.in.sales)}</span>
              <span>Bulk order payments</span><span className="font-mono text-right">{peso(cash.in.orders)}</span>
              <span>Cash added by hand</span><span className="font-mono text-right">{peso(cash.in.manual)}</span>
              <span>Payroll &amp; bonuses</span><span className="font-mono text-right">−{peso(cash.out.payroll)}</span>
              <span>Loans &amp; payments</span><span className="font-mono text-right">−{peso(cash.out.obligations)}</span>
              <span>Maintenance</span><span className="font-mono text-right">−{peso(cash.out.maintenance)}</span>
              <span>Stock purchases</span><span className="font-mono text-right">−{peso(cash.out.purchases)}</span>
              <span>Taken out by hand</span><span className="font-mono text-right">−{peso(cash.out.manual)}</span>
            </div>
          )}
          <div style={{ borderTop: "1px solid var(--border)" }} className="pt-3 flex flex-col gap-2.5">
            <p className="text-xs" style={{ color: "var(--muted)" }}>Add money not recorded anywhere else — e.g. your opening balance, a deposit, or a withdrawal.</p>
            <div className="flex gap-1.5">
              {["in", "out"].map((d) => (
                <button key={d} onClick={() => setCashDir(d)} className="flex-1 py-1.5 rounded-lg text-xs font-semibold"
                  style={{ border: `1px solid ${cashDir === d ? ACCENT.cyan : "var(--border)"}`, color: cashDir === d ? ACCENT.cyan : "var(--muted)", background: cashDir === d ? `${ACCENT.cyan}14` : "transparent" }}>
                  {d === "in" ? "Cash in" : "Cash out"}
                </button>
              ))}
            </div>
            <input type="number" value={cashAmount} onChange={(e) => setCashAmount(e.target.value)} placeholder="Amount"
              className="text-sm rounded-lg px-3 py-2 outline-none" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
            <input value={cashNote} onChange={(e) => setCashNote(e.target.value)} placeholder="Note (optional)"
              className="text-sm rounded-lg px-3 py-2 outline-none" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
            {cashErr && <span className="text-xs" style={{ color: ACCENT.danger }}>{cashErr}</span>}
            <button onClick={saveCash} style={{ background: ACCENT.cyan }} className="py-2 rounded-lg text-sm font-semibold text-black/85">Save</button>
          </div>
        </Modal>
      )}

      {photoOpen && (
        <Modal title="Employee photo" onClose={() => setPhotoOpen(false)} width={380}>
          {attendance.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--muted)" }}>Add the employee in Cash Flow first.</p>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                The same photo shows in Cash Flow, on the Sales counter, and on the phone after that person signs in.
              </p>
              <select value={photoWho} onChange={(e) => setPhotoWho(e.target.value)} className="text-sm rounded-lg px-3 py-2 outline-none"
                style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }}>
                {attendance.map((a) => <option key={a.id} value={a.id}>{a.name}{a.role ? ` — ${a.role}` : ""}</option>)}
              </select>
              <input ref={fileRef} type="file" accept="image/*" className="hidden"
                onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; uploadStaffPhoto(f); }} />
              <button onClick={() => fileRef.current && fileRef.current.click()} style={{ background: ACCENT.cyan }}
                className="py-2 rounded-lg text-sm font-semibold text-black/85 flex items-center justify-center gap-1.5">
                <Upload size={14} /> Choose picture
              </button>
            </div>
          )}
        </Modal>
      )}

      {lowOpen && stats && (
        <LowStockModal items={stats.lowStock} onClose={() => setLowOpen(false)}
          onChanged={() => { loadPriority(); api.get("/api/shop/stats?range=today").then(setStats).catch(() => {}); }} />
      )}
      {salesOpen && <SalesModal onClose={() => setSalesOpen(false)} />}

      {draftId && (
        <DraftOrderModal draftId={draftId} onClose={() => setDraftId(null)} onDone={() => { setDraftId(null); loadPriority(); loadToday(); }} />
      )}
      {readMsg && (
        <PriorityModal m={readMsg} onClose={() => setReadMsg(null)}
          onChanged={() => { loadPriority(); api.get("/api/shop/stats?range=today").then(setStats).catch(() => {}); }} />
      )}
    </div>
  );
}
