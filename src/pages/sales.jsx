import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Plus, Minus, Receipt, FileImage, Folder, Lock, RefreshCw, RotateCcw, Banknote, Landmark,
  PackagePlus, CheckCircle2, Circle, Truck, History, BadgeCheck, Trash2, ScanLine, Pencil, MessageCircle, MapPin, UserRound, Users,
  CalendarRange, X, Smartphone,
} from "lucide-react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal, Avatar, DatePicker, SuggestInput, HiddenToggle } from "../components/kit";
import { api, imgUrl } from "../lib/api";
import CrewPicker, { runModeOf } from "../components/crew";
import { useLocation, useNavigate } from "react-router-dom";

// ---------------------------------------------------------------------------
// SALES PAGE — everything here comes from the database (backend/inventory.db):
//   1) Sales Counters (top row): one card per sales person who has a phone
//      login (made in Cash Flow → Employee → Phone login). Their photo, their
//      receipts (photos sent from the phone, named month+day+hour_minute, e.g.
//      Jan 1 1:30 = 111_30) and their running total = what the phone showed
//      when they pressed Update. The "−" badge switches their login off
//      (past sales stay in the books).
//   2) Bulk Orders ("Add Order", right column): big/VIP clients. Each line item
//      has its own price, so the order total is auto-calculated; delivery is
//      one or more truck "runs". When fully delivered AND fully paid it can be
//      marked Done, which moves it to Order History.
const DELIVERY_GREEN = "#22c55e";
const PALETTE = [ACCENT.cyan, ACCENT.copper, ACCENT.amber, ACCENT.danger, "#a78bfa", "#38bdf8"];
const peso = (n) => `₱${Math.round(Number(n) || 0).toLocaleString()}`;
const STATE_LABEL = { sold: "Sold", partial: "Partly sold", ready: "Not updated yet", queued: "Waiting", processing: "Reading", failed: "Failed" };
const STATE_COLOR = { sold: ACCENT.cyan, partial: ACCENT.amber, ready: ACCENT.amber, failed: ACCENT.danger };
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// BULK ORDERS — helpers (same maths as before; the data now lives in the DB)
// ---------------------------------------------------------------------------
function itemSubtotal(item) {
  return (Number(item.qty) || 0) * (Number(item.price) || 0);
}
function itemsTotalOf(order) {
  return order.items.reduce((sum, i) => sum + itemSubtotal(i), 0);
}
// what the customer pays: the items, plus the delivery when the customer pays the delivery (the server works it out)
function orderTotal(order) {
  return order.total ?? itemsTotalOf(order);
}
// who delivers a run (components/crew.jsx RUN_MODES): our truck (driver + helpers), the customer picks it up with
// his own vehicle (no cost), or someone else (what they charge per trip)
function runCost(r, transports) {
  const mode = r.mode || "truck", trips = Number(r.trips) || 1;
  if (mode === "pickup") return 0;
  if (mode === "other") return (Number(r.carrierCost) || 0) * trips;
  const truck = transports.find((t) => t.id === r.transportId);
  return truck ? (truck.cost || 0) * trips : 0;
}
function deliveryCost(order, transports) {
  return (order.deliveryRuns || []).reduce((sum, r) => sum + runCost(r, transports), 0);
}
// a run that is ready to be saved: a truck run needs its truck; a pick-up / someone else needs nothing more
const runReady = (r) => (r.mode || "truck") !== "truck" || !!r.transportId;
function runLabel(r, transports) {
  const mode = r.mode || "truck";
  if (mode === "pickup") return `Customer picks up${r.carrier ? ` (${r.carrier})` : ""}`;
  if (mode === "other") return r.carrier ? `Delivered by ${r.carrier}` : "Someone else delivers";
  const t = transports.find((x) => x.id === r.transportId);
  return t ? `${t.truckName}${t.plate ? ` · ${t.plate}` : ""}` : "No truck picked";
}
function paidAmount(order) {
  return order.payments.reduce((sum, p) => sum + p.amount, 0);
}
function paymentPercent(order) {
  const total = orderTotal(order);
  if (!total) return 0;
  return Math.min(100, (paidAmount(order) / total) * 100);
}
function deliveryPercent(order) {
  if (!order.items.length) return 0;
  const done = order.items.filter((i) => i.delivered).length;
  return (done / order.items.length) * 100;
}
function isOrderComplete(order) {
  return deliveryPercent(order) === 100 && orderTotal(order) - paidAmount(order) <= 0;
}
// ---------------------------------------------------------------------------
// Small shared UI bits
// ---------------------------------------------------------------------------
function Field({ label, children }) {
  return (
    <div>
      {label && <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>{label}</label>}
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

function ProgressBar({ percent, color, label }) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <div>
      <div className="flex items-center justify-between text-[10px] mb-1" style={{ color: "var(--muted)" }}>
        <span>{label}</span>
        <span className="font-mono">{Math.round(pct)}%</span>
      </div>
      <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: "var(--panelAlt)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

// the sales person's photo (uploaded in Cash Flow → Employee); initials circle when there is none
function PersonPhoto({ p, color, size = 72 }) {
  const [broken, setBroken] = useState(false);
  if (p.hasPhoto && !broken) {
    return (
      <img src={imgUrl(`/api/shop/people/${p.userId}/photo`)} alt={p.name} onError={() => setBroken(true)}
        style={{ width: size, height: size, borderRadius: "50%", objectFit: "cover", border: `1.5px solid ${color}90`, boxShadow: `0 0 18px -4px ${color}80` }} />
    );
  }
  return <Avatar name={p.name} color={color} size={size} />;
}

// ---------------------------------------------------------------------------
// ORDER FORM (Add Order and Edit order share it)
//   * item name: type "B" -> inventory suggestions (Boysen Flat Latex White (Liter)...); picking one fills unit + price
//   * unit: drop-down list from the database (+ New unit…)
//   * dates: calendar picker
//   * trucks: capacity is a number + unit; the form adds up the order's weight and suggests the return trips
//     (10 t of goods, a 4 t truck -> 3 trips). Typing your own trip count keeps it.
//   * "Read from photo": the receipt reader fills the rows from a picture of the customer's list
// ---------------------------------------------------------------------------
const kgFromName = (name) => { const m = /(\d+(?:\.\d+)?)\s*kg\b/i.exec(name || ""); return m ? Number(m[1]) : null; };
// typed / inventory units like "MT", "tons", "kgs" are the same as the list's "metric tons", "kg" (same table as shop_db.py)
const UNIT_ALIASES = {
  mt: "metric tons", "m.t.": "metric tons", "m.t": "metric tons", ton: "metric tons", tons: "metric tons", tonne: "metric tons",
  tonnes: "metric tons", "metric ton": "metric tons", t: "metric tons", kgs: "kg", kilo: "kg", kilos: "kg", kilogram: "kg",
  kilograms: "kg", "kg.": "kg", grams: "g", gram: "g", sack: "sacks", bag: "bags", pc: "pcs", "pc.": "pcs", "pcs.": "pcs",
  piece: "pcs", pieces: "pcs", box: "boxes", roll: "rolls", sheet: "sheets", length: "lengths", meter: "meters", m: "meters",
  liter: "liters", l: "liters", gallon: "gallons", gal: "gallons", can: "cans", "cu.m": "cu. meters", "cu.m.": "cu. meters",
  cum: "cu. meters", "cubic meters": "cu. meters", "cubic meter": "cu. meters", trip: "trips",
};
const unitKey = (u) => { const k = (u || "").trim().toLowerCase().replace(/\s+/g, " "); return UNIT_ALIASES[k] || k; };
const fmtKg = (kg) => (kg >= 1000 ? `${(kg / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })} t` : `${Math.round(kg).toLocaleString()} kg`);
const newKey = () => `k${Date.now()}${Math.random().toString(36).slice(2, 6)}`;
const INPUT_ST = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };

// weight of ONE unit of this row: a weight unit (kg, metric tons), else typed kg/unit, else "40kg" in the item name
function rowKg(row, unitMap) {
  const u = unitMap[unitKey(row.unit)];
  if (u && u.toKg) return u.toKg;
  if (row.kgPerUnit !== "" && row.kgPerUnit != null && Number(row.kgPerUnit) > 0) return Number(row.kgPerUnit);
  return kgFromName(row.name);
}

const newRun = (transportId = "", date = "", mode = "truck") => ({ key: newKey(), mode, transportId, trips: 1, date, manual: false, driverId: null,
  helperIds: [], carrier: "", carrierCost: "" });
// ---- splitting the return trips of one truck over the days of the delivery window (3 trips in 2 days -> 2 + 1) ----
const pad2 = (n) => String(n).padStart(2, "0");
const dayStr = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const addDays = (s, n) => { const d = new Date(`${s}T00:00:00`); d.setDate(d.getDate() + n); return dayStr(d); };
const daysFromTo = (a, b) => Math.round((new Date(`${b}T00:00:00`) - new Date(`${a}T00:00:00`)) / 86400000);
const dayLabel = (s) => new Date(`${s}T00:00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const shortDay = (s) => new Date(`${s}T00:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });
function spreadTrips(trips, days) {           // as even as possible, the extra trips on the first days
  const base = Math.floor(trips / days), extra = trips % days;
  return Array.from({ length: days }, (_, i) => base + (i < extra ? 1 : 0));
}
function blankOrderDraft() {
  return {
    customer: "",
    address: "",
    items: [{ key: newKey(), name: "", qty: "", unit: "sacks", price: "", itemId: null, kgPerUnit: "" }],
    paid: "",
    paidBy: "cash",
    startDate: "",
    endDate: "",
    deliveryRuns: [],                    // "Add delivery" opens the delivery popup
    chargeDelivery: true,                // the customer pays the delivery: total = items + delivery
  };
}
function draftFromOrder(o) {
  return {
    customer: o.customer, address: o.address || "", paid: "", startDate: o.startDate, endDate: o.endDate,
    // taken = what the saved row already took out of Supply (still "ours" when the quantity is changed)
    items: o.items.map((i) => ({ key: newKey(), id: i.id, name: i.name, qty: i.qty, unit: i.unit, price: i.price, itemId: i.itemId,
      kgPerUnit: i.kgPerUnit ?? "", taken: Number(i.stockTaken) || 0 })),
    deliveryRuns: o.deliveryRuns.map((r) => ({ key: newKey(), id: r.id, mode: r.mode || "truck", transportId: r.transportId ?? "", trips: r.trips,
      date: r.date, manual: true, driverId: r.driverId ?? null, helperIds: r.helperIds || [], carrier: r.carrier || "", carrierCost: r.carrierCost || "" })),
    chargeDelivery: !!o.chargeDelivery,
  };
}
// a delivery receipt read from a phone -> the order form, filled in
function draftFromDelivery(d, transports) {
  const items = d.rows.map((r) => ({ key: newKey(), name: r.name, qty: r.qty ?? "", unit: r.unit, price: r.price, itemId: r.itemId, kgPerUnit: kgFromName(r.name) ?? "" }));
  return {
    customer: d.customer || "", address: d.address || "", paid: "", paidBy: "cash", startDate: d.date || "", endDate: d.date || "",
    items: items.length ? items : [{ key: newKey(), name: "", qty: "", unit: "sacks", price: "", itemId: null, kgPerUnit: "" }],
    deliveryRuns: [newRun(transports[0]?.id ?? "", d.date || "")],
    chargeDelivery: true,
  };
}

function UnitSelect({ value, onChange, units, onAddUnit, className = "", style }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  if (adding) {
    return (
      <div className={`flex gap-1 ${className}`}>
        <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="new unit"
          className="min-w-0 flex-1 text-sm rounded-lg px-2 py-2 outline-none" style={INPUT_ST}
          onKeyDown={(e) => { if (e.key === "Escape") setAdding(false); }} />
        <button type="button" onClick={async () => {
          const v = draft.trim();
          if (v) { try { await onAddUnit(v); onChange(v); } catch { /* toast */ } }
          setAdding(false); setDraft("");
        }} style={{ background: ACCENT.cyan }} className="px-2 rounded-lg text-xs font-semibold text-black/85">Add</button>
      </div>
    );
  }
  const names = units.map((u) => u.name);
  const list = value && !names.some((n) => unitKey(n) === unitKey(value)) ? [value, ...names] : names;
  const current = list.find((n) => unitKey(n) === unitKey(value)) || "";
  return (
    <select value={current} onChange={(e) => (e.target.value === "__add__" ? setAdding(true) : onChange(e.target.value))}
      className={`text-sm rounded-lg px-2 py-2 outline-none ${className}`} style={{ ...INPUT_ST, ...style }}>
      {!current && <option value="">unit…</option>}
      {list.map((n) => <option key={n} value={n}>{n}</option>)}
      <option value="__add__">+ New unit…</option>
    </select>
  );
}

// Truck picker. "+ Add transport…" opens a small form; capacity = number + unit from the list.
function TransportPicker({ value, onChange, transports, onAddTransport, units, onAddUnit }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ truckName: "", plate: "", capacityValue: "", capacityUnit: "metric tons", cost: "" });
  if (adding) {
    return (
      <div className="flex flex-col gap-2 p-3 rounded-lg" style={{ border: "1px dashed var(--border)" }}>
        <TextInput placeholder="Truck name (e.g. Isuzu Elf)" value={draft.truckName} onChange={(e) => setDraft({ ...draft, truckName: e.target.value })} />
        <TextInput placeholder="Plate number" value={draft.plate} onChange={(e) => setDraft({ ...draft, plate: e.target.value })} />
        <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1.3fr" }}>
          <TextInput type="number" placeholder="Capacity" value={draft.capacityValue} onChange={(e) => setDraft({ ...draft, capacityValue: e.target.value })} />
          <UnitSelect value={draft.capacityUnit} onChange={(u) => setDraft({ ...draft, capacityUnit: u })} units={units} onAddUnit={onAddUnit} />
        </div>
        <TextInput type="number" placeholder="Cost per delivery trip (₱)" value={draft.cost} onChange={(e) => setDraft({ ...draft, cost: e.target.value })} />
        <div className="flex gap-2">
          <button type="button" onClick={async () => {
            if (!draft.truckName.trim()) return;
            try {
              const t = await onAddTransport({ ...draft, capacityValue: Number(draft.capacityValue) || null, cost: Number(draft.cost) || 0 });
              onChange(t.id);
              setDraft({ truckName: "", plate: "", capacityValue: "", capacityUnit: "metric tons", cost: "" });
              setAdding(false);
            } catch { /* toast */ }
          }} style={{ background: ACCENT.cyan }} className="px-3 py-1.5 rounded-lg text-xs font-semibold text-black/85">Save truck</button>
          <button type="button" onClick={() => setAdding(false)} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: "var(--muted)" }}>Cancel</button>
        </div>
      </div>
    );
  }
  return (
    <select value={value} onChange={(e) => (e.target.value === "__add__" ? setAdding(true) : onChange(Number(e.target.value)))}
      className="w-full text-sm rounded-lg px-2 py-2 outline-none" style={INPUT_ST}>
      <option value="" disabled>Select truck</option>
      {transports.map((t) => <option key={t.id} value={t.id}>{t.truckName}{t.plate ? ` · ${t.plate}` : ""}{t.capacity ? ` · ${t.capacity}` : ""}</option>)}
      <option value="__add__">+ Add transport…</option>
    </select>
  );
}

// "Split the trips over the days": one row per day of the delivery window with how many return trips that day.
// Applying turns the run into one run per day (same truck, driver and helpers), e.g. 3 trips in 2 days = 2 + 1.
function SplitTrips({ run, startDate, endDate, onApply, onCancel }) {
  const from = startDate || run.date || dayStr(new Date());
  const windowDays = startDate && endDate && endDate >= startDate ? daysFromTo(startDate, endDate) + 1 : 2;
  const [days, setDays] = useState(Math.max(2, Math.min(14, windowDays)));
  const [counts, setCounts] = useState(() => spreadTrips(run.trips || 1, Math.max(2, Math.min(14, windowDays))));
  const setN = (n) => { const k = Math.max(2, Math.min(14, n)); setDays(k); setCounts(spreadTrips(run.trips || 1, k)); };
  const total = counts.reduce((a, b) => a + (Number(b) || 0), 0);
  const plan = counts.map((c, i) => ({ date: addDays(from, i), trips: Number(c) || 0 })).filter((x) => x.trips > 0);
  const outside = endDate && plan.some((x) => x.date > endDate);
  return (
    <div className="rounded-lg p-2.5 flex flex-col gap-2" style={{ border: `1px dashed ${ACCENT.cyan}`, background: "var(--panel)" }}>
      <div className="flex items-center gap-2 text-[11px]">
        <CalendarRange size={13} style={{ color: ACCENT.cyan }} />
        <span className="flex-1"><b>{run.trips} trip{run.trips === 1 ? "" : "s"}</b> over
          <span className="inline-flex items-center gap-1 mx-1.5">
            <button type="button" onClick={() => setN(days - 1)} className="w-5 h-5 rounded flex items-center justify-center" style={INPUT_ST}><Minus size={10} /></button>
            <b className="font-mono">{days}</b>
            <button type="button" onClick={() => setN(days + 1)} className="w-5 h-5 rounded flex items-center justify-center" style={INPUT_ST}><Plus size={10} /></button>
          </span>
          days from {dayLabel(from)}</span>
        <button type="button" onClick={onCancel} style={{ color: "var(--muted)" }} title="Close"><X size={13} /></button>
      </div>
      <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
        {counts.map((c, i) => (
          <label key={i} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px]" style={INPUT_ST} title={dayLabel(addDays(from, i))}>
            <span className="flex-1 truncate" style={{ color: endDate && addDays(from, i) > endDate ? ACCENT.amber : "var(--muted)" }}>
              <b style={{ color: "var(--text)" }}>Day {i + 1}</b> · {shortDay(addDays(from, i))}</span>
            <input type="number" min={0} value={c} onChange={(e) => setCounts((x) => x.map((v, j) => (j === i ? Math.max(0, Number(e.target.value) || 0) : v)))}
              className="w-10 text-xs rounded px-1 py-0.5 outline-none text-center font-mono" style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2 text-[11px]">
        <span style={{ color: total === run.trips ? "var(--muted)" : ACCENT.amber }}>
          {total === run.trips ? `${total} trips in all` : `These add up to ${total} trips (was ${run.trips})`}
          {outside ? " · some days are after the target end" : ""}
        </span>
        <button type="button" disabled={plan.length < 1} onClick={() => onApply(plan)}
          className="ml-auto px-3 py-1 rounded-md text-xs font-semibold text-black/85 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
          Split into {plan.length} day{plan.length === 1 ? "" : "s"}</button>
      </div>
    </div>
  );
}

function DeliveryRunRow({ run, transports, onChange, onAddTransport, onRemove, showRemove, units, onAddUnit, suggestion,
  truckTrips, truckRows, firstOfTruck, startDate, endDate, onSplit }) {
  const mode = run.mode || "truck";
  const truck = mode === "truck" ? transports.find((t) => t.id === run.transportId) : null;
  const [splitting, setSplitting] = useState(false);
  const many = truckRows > 1;
  const windowDays = startDate && endDate && endDate > startDate ? daysFromTo(startDate, endDate) + 1 : 0;
  const cost = runCost(run, transports);
  const panel = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
  const m = runModeOf(run);
  // Add delivery plans OUR truck only. A run where the customer picks it up / someone else delivers is set in the
  // Planner (who delivers): it is shown here as it is and kept when the order is saved.
  if (mode !== "truck") {
    return (
      <div className="flex items-center gap-2 p-2.5 rounded-lg text-xs" style={{ border: `1px solid ${m.color}55`, background: "var(--panelAlt)" }}>
        <m.Icon size={15} style={{ color: m.color }} className="shrink-0" />
        <span className="flex-1 min-w-0">
          <span className="block truncate">{runLabel(run, transports)} · {run.trips} trip{Number(run.trips) === 1 ? "" : "s"} · {run.date ? dayLabel(run.date) : <span style={{ color: ACCENT.amber }}>no day yet</span>}</span>
          <span className="text-[10px]" style={{ color: "var(--faint)" }}>Who delivers was set in the Planner — change it there.</span>
        </span>
        <span className="font-mono shrink-0" style={{ color: "var(--muted)" }}>{mode === "pickup" ? "no cost" : peso(cost)}</span>
        {showRemove && (
          <button type="button" onClick={onRemove} title="Remove this delivery" className="p-1.5 rounded-lg flex items-center justify-center" style={{ color: ACCENT.danger }}><Minus size={14} /></button>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 p-2.5 rounded-lg" style={{ border: `1px solid ${m.color}55`, background: "var(--panelAlt)" }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 text-xs font-semibold" style={{ color: m.color }}><Truck size={14} /> Our truck</span>
        <span className="ml-auto text-xs font-mono px-2 py-1 rounded-md" style={{ ...panel, color: "var(--muted)" }} title="Cost of this run (per trip x trips)">
          {!truck ? "—" : peso(cost)}</span>
        {showRemove && (
          <button type="button" onClick={onRemove} title="Remove this run" className="p-1.5 rounded-lg flex items-center justify-center" style={{ color: ACCENT.danger }}><Minus size={14} /></button>
        )}
      </div>
      <TransportPicker value={run.transportId} transports={transports} units={units} onAddUnit={onAddUnit}
        onChange={(id) => onChange({ ...run, transportId: id, manual: false })} onAddTransport={onAddTransport} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="Return trips">
          <input type="number" min={1} value={run.trips} onChange={(e) => onChange({ ...run, trips: Math.max(1, Number(e.target.value) || 1), manual: true })}
            className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={panel} />
        </Field>
        <Field label="Delivery schedule">
          <DatePicker value={run.date} onChange={(v) => onChange({ ...run, date: v })} placeholder="Pick a day" style={{ background: "var(--panel)" }} />
        </Field>
      </div>
      {run.trips > 1 && !splitting && (
        <button type="button" onClick={() => setSplitting(true)} className="self-start -mt-0.5 text-[11px] flex items-center gap-1 font-medium" style={{ color: ACCENT.cyan }}
          title="Not all trips on one day: say how many trips go each day">
          <CalendarRange size={12} /> Split the {run.trips} trips over {windowDays > 1 ? `the ${windowDays} days (${dayLabel(startDate)} – ${dayLabel(endDate)})` : "the days"}
        </button>
      )}
      {splitting && (
        <SplitTrips run={run} startDate={startDate} endDate={endDate} onCancel={() => setSplitting(false)}
          onApply={(plan) => { setSplitting(false); onSplit(plan); }} />
      )}
      {mode === "truck" && (
        <>
          <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
            <CrewPicker date={run.date} runId={run.id} driverId={run.driverId} helperIds={run.helperIds || []}
              onChange={(c) => onChange({ ...run, ...c })} />
          </div>
          <div className="flex items-center justify-between text-[10px]" style={{ color: "var(--faint)" }}>
            <span>
              {suggestion && run.transportId && (many
                ? (firstOfTruck && <span style={{ color: truckTrips === suggestion ? ACCENT.cyan : ACCENT.amber }}>
                    this truck: {truckTrips} trips in all · {suggestion} suggested for this load</span>)
                : (run.manual && run.trips !== suggestion
                  ? <button type="button" onClick={() => onChange({ ...run, trips: suggestion, manual: false })} style={{ color: ACCENT.cyan }}>Use suggested {suggestion} trips</button>
                  : <span style={{ color: ACCENT.cyan }}>{suggestion} trip{suggestion === 1 ? "" : "s"} suggested for this load</span>))}
            </span>
            {truck && <span>{truck.capacity ? `${truck.capacity} · ` : ""}{peso(truck.cost)} per trip</span>}
          </div>
        </>
      )}
    </div>
  );
}

// ---- the order takes its items out of Supply (backend: shop_api write_order_rows) ----
// A row picked from Supply, in the item's own unit, takes its quantity when the order is saved. Asking for more than
// Supply has opens "This is our only supply" (when leaving the Qty box, and again on Apply; the server checks too).
// Rows typed by hand, another unit than the item's, or an item whose stock was never counted (N/A) take nothing.
// checkStock=false: an order made from a phone's DELIVERY receipt (the phone's Update of that receipt takes the stock).
const fmtQ = (n) => Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 3 });
function OnlySupplyModal({ short, onUse, onClose }) {
  const none = short.every((s) => !(s.have > 0));
  return (
    <Modal title="This is our only supply" onClose={onClose} width={440}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          {short.map((s) => (
            <div key={s.itemId} className="rounded-lg px-3 py-2.5 flex flex-col gap-0.5" style={{ background: `${ACCENT.amber}12`, border: `1px solid ${ACCENT.amber}66` }}>
              <span className="text-sm font-semibold">{s.name}</span>
              <span className="text-xs" style={{ color: "var(--muted)" }}>
                The order asks for <b style={{ color: "var(--text)" }}>{fmtQ(s.want)} {s.unit}</b> — Supply has only{" "}
                <b style={{ color: s.have > 0 ? ACCENT.amber : ACCENT.danger }}>{fmtQ(s.have)} {s.unit}</b>{s.have > 0 ? "." : " (none left)."}
              </span>
            </div>
          ))}
        </div>
        <p className="text-[11px]" style={{ color: "var(--faint)" }}>
          An order takes its items out of Supply when it is saved, so it cannot ask for more than we have. Buy more first
          (Supply → add stock), or order what is left.</p>
        <div className="flex gap-2">
          <button type="button" onClick={onUse} style={{ background: ACCENT.cyan }} className="flex-1 py-2.5 rounded-lg text-sm font-semibold text-black/85">
            {none ? "Remove it from the order" : short.length === 1 ? `Use the ${fmtQ(short[0].have)} ${short[0].unit} we have` : "Use what we have"}</button>
          <button type="button" onClick={onClose} className="px-4 py-2.5 rounded-lg text-sm" style={{ border: "1px solid var(--border)", color: "var(--muted)" }}>
            Change it</button>
        </div>
      </div>
    </Modal>
  );
}

function OrderForm({ mode, initial, transports, units, inventory, onAddTransport, onAddUnit, onSubmit, onClose,
  title, banner, onDismiss, initialNote = null, checkStock = true }) {
  const [d, setD] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [reading, setReading] = useState(false);
  const [readNote, setReadNote] = useState(initialNote);    // { rows, unmatched }
  const [deliveryOpen, setDeliveryOpen] = useState(false);  // the "Add delivery" popup
  const [shortList, setShortList] = useState(null);         // "This is our only supply" popup
  const fileRef = useRef(null);
  const unitMap = useMemo(() => Object.fromEntries(units.map((u) => [unitKey(u.name), u])), [units]);
  const options = useMemo(() => inventory.map((i) => ({
    key: String(i.itemId), label: i.name, sub: `₱${i.price.toLocaleString()}/${i.unit} · ${i.qtyUnknown ? "stock not counted" : `${fmtQ(i.stock)} left`}`, ...i,
  })), [inventory]);
  const invById = useMemo(() => Object.fromEntries(inventory.map((i) => [i.itemId, i])), [inventory]);
  // what the saved order already took per item (it is still this order's when the quantity changes)
  const takenBefore = useMemo(() => {
    const m = {};
    (initial.items || []).forEach((i) => { if (i.id && i.itemId && i.taken) m[i.itemId] = (m[i.itemId] || 0) + Number(i.taken); });
    return m;
  }, [initial]);
  const takesStock = (it) => {
    const inv = it.itemId ? invById[it.itemId] : null;
    return checkStock && !!inv && !inv.qtyUnknown && (!it.unit || unitKey(it.unit) === unitKey(inv.unit));
  };
  const stockFor = (itemId) => (Number(invById[itemId]?.stock) || 0) + (takenBefore[itemId] || 0);
  function shortages(rows, onlyItem = null) {
    const want = {};
    rows.forEach((it) => {
      if (it.name.trim() && takesStock(it) && Number(it.qty) > 0) want[it.itemId] = (want[it.itemId] || 0) + Number(it.qty);
    });
    return Object.entries(want).map(([id, w]) => ({ itemId: Number(id), want: w, have: stockFor(Number(id)), name: invById[id].name, unit: invById[id].unit }))
      .filter((s) => s.want > s.have + 1e-9 && (onlyItem == null || s.itemId === onlyItem));
  }
  // "Use what we have": the item's rows are cut down (the last ones first) to what Supply has; none left = rows removed
  function useWhatWeHave() {
    setD((x) => {
      let items = [...x.items];
      (shortList || []).forEach((s) => {
        let over = items.filter((it) => it.itemId === s.itemId && takesStock(it)).reduce((a, it) => a + (Number(it.qty) || 0), 0) - s.have;
        for (let k = items.length - 1; k >= 0 && over > 1e-9; k -= 1) {
          const it = items[k];
          if (it.itemId !== s.itemId || !takesStock(it)) continue;
          const q = Number(it.qty) || 0;
          const cut = Math.min(q, over);
          over -= cut;
          items[k] = { ...it, qty: Math.round((q - cut) * 1000) / 1000 };
        }
        items = items.filter((it) => !(it.itemId === s.itemId && takesStock(it) && !(Number(it.qty) > 0)));
      });
      if (!items.length) items = [{ key: newKey(), name: "", qty: "", unit: "sacks", price: "", itemId: null, kgPerUnit: "" }];
      return { ...x, items };
    });
    setShortList(null);
  }

  const setItem = (idx, patch) => setD((x) => ({ ...x, items: x.items.map((it, i) => (i === idx ? { ...it, ...patch } : it)) }));
  const setRun = (idx, run) => setD((x) => ({ ...x, deliveryRuns: x.deliveryRuns.map((r, i) => (i === idx ? run : r)) }));
  const canonUnit = (u) => units.find((x) => unitKey(x.name) === unitKey(u))?.name || u;    // "MT" -> "metric tons"
  const pick = (idx, o) => setItem(idx, { name: o.name, unit: canonUnit(o.unit), price: o.price, itemId: o.itemId, kgPerUnit: kgFromName(o.name) ?? "" });

  // ---- weight of the order -> suggested return trips ----
  const filled = d.items.filter((i) => i.name.trim());
  let totalKg = 0;
  const unknown = [];
  filled.forEach((i) => { const k = rowKg(i, unitMap); if (k == null) unknown.push(i); else totalKg += (Number(i.qty) || 0) * k; });
  // trucks that go at the same time: each truck counted once (a truck split over several days is still one truck)
  const isTruck = (r) => (r.mode || "truck") === "truck";
  const truckIds = [...new Set(d.deliveryRuns.filter(isTruck).map((r) => r.transportId).filter(Boolean))];
  const capKg = truckIds.reduce((s, id) => s + (transports.find((t) => t.id === id)?.capacityKg || 0), 0);
  const suggestion = totalKg > 0 && capKg > 0 ? Math.max(1, Math.ceil(totalKg / capKg - 1e-9)) : null;
  const rowsOf = (id) => d.deliveryRuns.filter((r) => isTruck(r) && r.transportId === id);
  useEffect(() => {
    if (!suggestion) return;
    setD((x) => {
      let changed = false;
      const runs = x.deliveryRuns.map((r) => {
        if (r.manual || !isTruck(r) || !r.transportId || r.trips === suggestion) return r;
        if (x.deliveryRuns.filter((o) => isTruck(o) && o.transportId === r.transportId).length > 1) return r;   // split over days: keep
        changed = true;
        return { ...r, trips: suggestion };
      });
      return changed ? { ...x, deliveryRuns: runs } : x;
    });
  }, [suggestion]);
  // one run -> one run per day (same truck, driver and helpers); the first keeps the run's id (when editing)
  function splitRun(idx, plan) {
    setD((x) => {
      const r0 = x.deliveryRuns[idx];
      const rows = plan.map((p, i) => ({ ...r0, key: newKey(), id: i === 0 ? r0.id : undefined, date: p.date, trips: p.trips, manual: true }));
      return { ...x, deliveryRuns: [...x.deliveryRuns.slice(0, idx), ...rows, ...x.deliveryRuns.slice(idx + 1)] };
    });
  }
  const byDay = {};
  d.deliveryRuns.filter((r) => runReady(r) && r.date).forEach((r) => { byDay[r.date] = (byDay[r.date] || 0) + (Number(r.trips) || 0); });
  const dayPlan = Object.keys(byDay).sort();

  const readyRuns = d.deliveryRuns.filter(runReady);
  const itemsTotal = filled.reduce((s, i) => s + itemSubtotal(i), 0);
  const runsCost = readyRuns.reduce((s, r) => s + runCost(r, transports), 0);
  const tripsTotal = readyRuns.filter((r) => r.mode !== "pickup").reduce((s, r) => s + (Number(r.trips) || 0), 0);
  const trucksNoCap = d.deliveryRuns.filter((r) => isTruck(r) && r.transportId && !transports.find((t) => t.id === r.transportId)?.capacityKg);
  const charge = d.chargeDelivery !== false;
  const grandTotal = itemsTotal + (charge ? runsCost : 0);     // what the customer pays
  const paidNow = Number(d.paid) || 0;
  function openDelivery() {                                    // the first time: one run with our first truck, on the start day
    if (!d.deliveryRuns.length) setD((x) => ({ ...x, deliveryRuns: [newRun(transports[0]?.id ?? "", x.startDate || "")] }));
    setDeliveryOpen(true);
  }

  async function readPhoto(file) {
    setReading(true); setReadNote(null);
    try {
      const res = await api.upload("/api/shop/orders/read-image", file);
      setD((x) => {
        const keep = x.items.filter((i) => i.name.trim());
        const add = res.rows.map((r) => ({ key: newKey(), name: r.name, qty: r.qty ?? "", unit: canonUnit(r.unit), price: r.price, itemId: r.itemId, kgPerUnit: kgFromName(r.name) ?? "" }));
        return { ...x, customer: x.customer.trim() ? x.customer : res.customer || x.customer, address: (x.address || "").trim() ? x.address : res.address || x.address,
          items: [...keep, ...add].length ? [...keep, ...add] : x.items };
      });
      setReadNote({ rows: res.rows.length, unmatched: res.unmatched });
    } catch { /* toast */ }
    setReading(false);
  }
  function addAnyway(u) {
    setD((x) => ({ ...x, items: [...x.items.filter((i) => i.name.trim()), { key: newKey(), name: u.item, qty: u.qty ?? "", unit: u.unit ? canonUnit(u.unit) : "",
      price: u.price ?? "", itemId: null, kgPerUnit: "" }] }));
    setReadNote((n) => ({ ...n, unmatched: n.unmatched.filter((x) => x !== u) }));
  }

  async function submit() {
    if (!d.customer.trim() || filled.length === 0 || saving) return;
    const short = shortages(filled);
    if (short.length) { setShortList(short); return; }
    setSaving(true);
    try {
      await onSubmit({
        customer: d.customer.trim(),
        address: (d.address || "").trim(),
        items: filled.map((i) => ({ id: i.id, name: i.name.trim(), qty: Number(i.qty) || 0, unit: i.unit, price: Number(i.price) || 0,
          itemId: i.itemId || null, kgPerUnit: rowKg(i, {}) })),
        paid: Number(d.paid) || 0, paidBy: d.paidBy || "cash", startDate: d.startDate, endDate: d.endDate,
        deliveryRuns: readyRuns.map((r) => ({ id: r.id || null, mode: r.mode || "truck", transportId: isTruck(r) ? r.transportId : null,
          carrier: (r.carrier || "").trim(), carrierCost: Number(r.carrierCost) || 0, trips: r.trips, date: r.date,
          driverId: isTruck(r) ? r.driverId ?? null : null, helperIds: isTruck(r) ? r.helperIds || [] : [] })),
        chargeDelivery: charge,
      });
    } catch (e) {
      // the server's own check (Supply changed since the form opened): same popup, with the server's numbers
      if (e && e.status === 409 && e.detail && Array.isArray(e.detail.short)) setShortList(e.detail.short);
    }
    setSaving(false);
  }

  return (
    <Modal title={title || (mode === "edit" ? `Edit order — ${initial.customer}` : "Add Order")} onClose={onClose} width={600}>
      <div className="flex flex-col gap-3.5">
        {banner}
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <Field label="Customer name">
              <TextInput value={d.customer} onChange={(e) => setD({ ...d, customer: e.target.value })} placeholder="e.g. Dela Cruz Construction" />
            </Field>
          </div>
          <button type="button" onClick={() => fileRef.current && fileRef.current.click()} disabled={reading}
            style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg shrink-0 disabled:opacity-60" title="Read the order from a photo of the customer's list">
            <ScanLine size={14} /> {reading ? "Reading photo…" : "Read from photo"}
          </button>
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) readPhoto(f); }} />
        </div>
        <Field label="Delivery address (optional)">
          <TextInput value={d.address || ""} onChange={(e) => setD({ ...d, address: e.target.value })} placeholder="e.g. 12 Rizal St, Pasig" />
        </Field>
        {reading && <p className="text-[11px]" style={{ color: "var(--muted)" }}>The receipt reader is reading the photo (about 5–20 seconds)…</p>}
        {readNote && (
          <div className="rounded-lg px-3 py-2 text-[11px] flex flex-col gap-1" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            <span style={{ color: ACCENT.cyan }}>Read {readNote.rows} item row(s) from the photo — check the quantities below.</span>
            {readNote.unmatched.length > 0 && (
              <>
                <span style={{ color: ACCENT.amber }}>Not in the inventory (not added):</span>
                {readNote.unmatched.map((u, i) => (
                  <span key={i} className="flex items-center justify-between gap-2" style={{ color: "var(--muted)" }}>
                    <span>• {u.item}{u.qty != null ? ` × ${u.qty}` : ""}{u.price ? ` · ₱${u.price.toLocaleString()} each` : ""}</span>
                    <button type="button" onClick={() => addAnyway(u)} style={{ color: ACCENT.cyan }}>add anyway</button>
                  </span>
                ))}
              </>
            )}
          </div>
        )}

        <div>
          <div className="grid gap-2 text-[10px] font-medium mb-1" style={{ gridTemplateColumns: ITEM_COLS, color: "var(--muted)" }}>
            <span>Order items — type to search the inventory</span><span className="text-center">Qty</span><span>Unit</span><span className="text-center">₱ / unit</span><span />
          </div>
          <div className="flex flex-col gap-2">
            {d.items.map((it, idx) => {
              const kg = rowKg(it, unitMap);
              const inv = it.itemId ? invById[it.itemId] : null;
              const takes = takesStock(it);
              const have = takes ? stockFor(it.itemId) : null;
              const wantAll = takes ? d.items.filter((x) => x.itemId === it.itemId && takesStock(x)).reduce((a, x) => a + (Number(x.qty) || 0), 0) : 0;
              const over = takes && wantAll > have + 1e-9;
              return (
                <div key={it.key} className="flex flex-col gap-1">
                  <div className="grid gap-2 items-center" style={{ gridTemplateColumns: ITEM_COLS }}>
                    <SuggestInput value={it.name} options={options} placeholder="e.g. B → Boysen…"
                      onChange={(v) => setItem(idx, { name: v, itemId: null })} onPick={(o) => pick(idx, o)}
                      className="min-w-0 text-sm rounded-lg px-3 py-2 outline-none" style={INPUT_ST} />
                    <input type="number" min={0} value={it.qty} onChange={(e) => setItem(idx, { qty: e.target.value })} placeholder="Qty"
                      onBlur={() => { const s = takes ? shortages(d.items, it.itemId) : []; if (s.length) setShortList(s); }}
                      title={takes ? `${fmtQ(have)} ${inv.unit} in Supply` : undefined}
                      className="min-w-0 text-sm rounded-lg px-2 py-2 outline-none text-center"
                      style={over ? { ...INPUT_ST, border: `1px solid ${ACCENT.danger}`, color: ACCENT.danger } : INPUT_ST} />
                    <UnitSelect value={it.unit} onChange={(u) => setItem(idx, { unit: u })} units={units} onAddUnit={onAddUnit} className="min-w-0" />
                    <input type="number" min={0} value={it.price} onChange={(e) => setItem(idx, { price: e.target.value })} placeholder="₱"
                      className="min-w-0 text-sm rounded-lg px-2 py-2 outline-none text-center" style={INPUT_ST} />
                    {d.items.length > 1 ? (
                      <button type="button" onClick={() => setD((x) => ({ ...x, items: x.items.filter((_, i) => i !== idx) }))} className="p-1.5 rounded-lg" style={{ color: ACCENT.danger }}><Minus size={14} /></button>
                    ) : <span />}
                  </div>
                  {it.name.trim() && (
                    <div className="flex items-center gap-2 pl-1 text-[10px]" style={{ color: "var(--faint)" }}>
                      {it.itemId ? <span style={{ color: ACCENT.cyan }}>from inventory</span> : <span>typed (not taken from Supply)</span>}
                      {takes && (
                        <span style={{ color: over ? ACCENT.danger : "var(--muted)" }}>
                          · {over ? `only ${fmtQ(have)} ${inv.unit} in Supply` : `${fmtQ(have)} ${inv.unit} in Supply`}</span>
                      )}
                      {inv && checkStock && !takes && (
                        <span>· {inv.qtyUnknown ? "stock not counted yet (N/A)" : `not taken from Supply (Supply counts it in ${inv.unit})`}</span>
                      )}
                      {unitMap[unitKey(it.unit)]?.toKg && kg != null && <span>· {fmtKg((Number(it.qty) || 0) * kg)}</span>}
                      <span className="ml-auto font-mono" style={{ color: "var(--muted)" }}>{peso(itemSubtotal(it))}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <button type="button" onClick={() => setD((x) => ({ ...x, items: [...x.items, { key: newKey(), name: "", qty: "", unit: "sacks", price: "", itemId: null, kgPerUnit: "" }] }))}
            className="mt-2 text-xs flex items-center gap-1 font-medium" style={{ color: ACCENT.cyan }}>
            <Plus size={12} /> Add another item
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Target start delivery">
            <DatePicker value={d.startDate} onChange={(v) => setD((x) => ({ ...x, startDate: v,
              deliveryRuns: x.deliveryRuns.map((r) => (r.date ? r : { ...r, date: v })) }))} />
          </Field>
          <Field label="Target end delivery"><DatePicker value={d.endDate} onChange={(v) => setD({ ...d, endDate: v })} /></Field>
        </div>

        {/* ---- Delivery: a summary here, everything else in the "Add delivery" popup ---- */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[11px] font-medium" style={{ color: "var(--muted)" }}>Delivery</label>
            {readyRuns.length > 0 && (
              <button type="button" onClick={openDelivery} className="text-[11px] flex items-center gap-1 font-medium" style={{ color: ACCENT.cyan }}>
                <Pencil size={11} /> Edit delivery</button>
            )}
          </div>
          {readyRuns.length === 0 ? (
            <button type="button" onClick={openDelivery} className="w-full rounded-lg px-3 py-3 flex items-center gap-2.5 text-left hover:bg-white/5"
              style={{ border: `1.5px dashed ${ACCENT.cyan}80`, color: ACCENT.cyan }}>
              <Truck size={18} className="shrink-0" />
              <span className="flex flex-col">
                <span className="text-sm font-semibold">Add delivery</span>
                <span className="text-[11px]" style={{ color: "var(--muted)" }}>Our truck · trips, days, driver and helpers (a customer pick-up or another carrier: set it in the Planner)</span>
              </span>
            </button>
          ) : (
            <button type="button" onClick={openDelivery} className="w-full rounded-lg px-3 py-2 flex flex-col gap-1.5 text-left hover:bg-white/5"
              style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }} title="Change the trucks, trips, days and crew">
              {readyRuns.map((r) => {
                const m = runModeOf(r);
                const crew = isTruck(r) ? [r.driverId ? "driver set" : "no driver", (r.helperIds || []).length ? `${r.helperIds.length} helper(s)` : "no helper"] : [];
                return (
                  <span key={r.key} className="flex items-center gap-2 text-xs">
                    <m.Icon size={14} style={{ color: m.color }} className="shrink-0" />
                    <span className="flex-1 min-w-0 truncate">
                      {runLabel(r, transports)} · {r.trips} trip{Number(r.trips) === 1 ? "" : "s"} · {r.date ? dayLabel(r.date) : <span style={{ color: ACCENT.amber }}>no day yet</span>}
                      {crew.length > 0 && <span style={{ color: "var(--muted)" }}> · {crew.join(", ")}</span>}
                    </span>
                    <span className="font-mono shrink-0" style={{ color: "var(--muted)" }}>{r.mode === "pickup" ? "no cost" : peso(runCost(r, transports))}</span>
                  </span>
                );
              })}
            </button>
          )}
        </div>

        {deliveryOpen && (
          <Modal title={`Delivery${d.customer.trim() ? ` — ${d.customer.trim()}` : ""}`} onClose={() => setDeliveryOpen(false)} width={620}>
            <div className="flex flex-col gap-3">
              <div className="text-[11px] rounded-lg px-3 py-2 flex flex-col gap-0.5" style={{ background: `${ACCENT.cyan}0f`, border: `1px solid ${ACCENT.cyan}40` }}>
                <span>
                  Load to deliver: <b className="font-mono">{totalKg > 0 ? fmtKg(totalKg) : "—"}</b>
                  {capKg > 0 && <> · our trucks carry <b className="font-mono">{fmtKg(capKg)}</b> per round</>}
                  {suggestion && <> → <b style={{ color: ACCENT.cyan }}>{suggestion} return trip{suggestion === 1 ? "" : "s"}</b></>}
                  {(d.startDate || d.endDate) && <span style={{ color: "var(--muted)" }}> · target {d.startDate ? dayLabel(d.startDate) : "?"} – {d.endDate ? dayLabel(d.endDate) : "?"}</span>}
                </span>
                {unknown.length > 0 && totalKg > 0 && <span style={{ color: "var(--faint)" }}>Not in the load (no weight): {unknown.map((i) => i.name).join(", ")}.</span>}
                {totalKg === 0 && <span style={{ color: "var(--faint)" }}>Trips are suggested when the items are sold by weight (kg, tons) or have the weight in their name (e.g. Cement 40kg); otherwise type the trips.</span>}
                {trucksNoCap.length > 0 && <span style={{ color: ACCENT.amber }}>A selected truck has no capacity in kg/tons, so trips cannot be suggested for it.</span>}
              </div>
              <div className="flex flex-col gap-2">
                {d.deliveryRuns.map((run, idx) => {
                  const same = isTruck(run) && run.transportId ? rowsOf(run.transportId) : [run];
                  return (
                    <DeliveryRunRow key={run.key} run={run} transports={transports} units={units} onAddUnit={onAddUnit} suggestion={suggestion}
                      truckTrips={same.reduce((a, r) => a + (Number(r.trips) || 0), 0)} truckRows={same.length} firstOfTruck={same[0] === run}
                      startDate={d.startDate} endDate={d.endDate} onSplit={(plan) => splitRun(idx, plan)}
                      onChange={(r) => setRun(idx, r)} onAddTransport={onAddTransport}
                      onRemove={() => setD((x) => ({ ...x, deliveryRuns: x.deliveryRuns.filter((_, i) => i !== idx) }))}
                      showRemove />
                  );
                })}
                {d.deliveryRuns.length === 0 && (
                  <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>No delivery: the order is kept without a delivery plan.</p>
                )}
              </div>
              {dayPlan.length > 1 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span style={{ color: "var(--muted)" }}>Trips per day:</span>
                  {dayPlan.map((day) => (
                    <span key={day} className="px-2 py-0.5 rounded-full font-medium" style={{ background: `${ACCENT.cyan}14`, border: `1px solid ${ACCENT.cyan}40`, color: ACCENT.cyan }}>
                      {dayLabel(day)} · {byDay[day]} trip{byDay[day] === 1 ? "" : "s"}</span>
                  ))}
                </div>
              )}
              <button type="button" onClick={() => setD((x) => ({ ...x, deliveryRuns: [...x.deliveryRuns, newRun(transports[0]?.id ?? "", x.startDate || "")] }))}
                className="self-start text-xs flex items-center gap-1 font-medium" style={{ color: ACCENT.cyan }}>
                <Plus size={12} /> Add another truck run (another truck or day)
              </button>
              <div className="flex items-center justify-between text-xs rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                <span style={{ color: "var(--muted)" }}>Delivery cost · {tripsTotal} trip{tripsTotal === 1 ? "" : "s"}{dayPlan.length > 1 ? ` in ${dayPlan.length} days` : ""}</span>
                <span className="font-mono font-semibold text-sm">{peso(runsCost)}</span>
              </div>
              <button type="button" onClick={() => setDeliveryOpen(false)} style={{ background: ACCENT.cyan }}
                className="py-2.5 rounded-lg text-sm font-semibold text-black/85">Done</button>
            </div>
          </Modal>
        )}

        {shortList && <OnlySupplyModal short={shortList} onUse={useWhatWeHave} onClose={() => setShortList(null)} />}

        <div className="rounded-xl p-3 flex flex-col gap-2" style={{ border: `1px solid ${ACCENT.cyan}55`, background: `${ACCENT.cyan}0a` }}>
          <div className="flex items-center justify-between text-xs">
            <span style={{ color: "var(--muted)" }}>Order total · {filled.length} item{filled.length === 1 ? "" : "s"}</span>
            <span className="font-mono font-semibold text-sm">{peso(itemsTotal)}</span>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span style={{ color: "var(--muted)" }}>
              {readyRuns.length ? `Delivery · ${tripsTotal} trip${tripsTotal === 1 ? "" : "s"}${dayPlan.length > 1 ? ` in ${dayPlan.length} days` : ""}` : "Delivery · none yet"}
            </span>
            <span className="font-mono font-semibold text-sm" style={charge ? {} : { color: "var(--faint)", textDecoration: runsCost ? "line-through" : "none" }}>
              {charge ? "+ " : ""}{peso(runsCost)}</span>
          </div>
          {runsCost > 0 && (
            <label className="flex items-center gap-1.5 text-[11px] cursor-pointer select-none -mt-1" style={{ color: "var(--muted)" }}
              title="Ticked: the delivery is added to what the customer pays. Not ticked: the shop pays it (free delivery)">
              <input type="checkbox" checked={charge} onChange={(e) => setD((x) => ({ ...x, chargeDelivery: e.target.checked }))} />
              The customer pays the delivery {charge ? "(added to the total)" : "— not ticked: free delivery, the shop pays it"}
            </label>
          )}
          <div className="flex items-center justify-between pt-2" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="text-sm font-semibold">Total</span>
            <span className="font-mono font-bold text-base" style={{ color: ACCENT.cyan }}>{peso(grandTotal)}</span>
          </div>
          {mode === "add" && (
            <>
              <div style={{ borderTop: "1px dashed var(--border)" }} />
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <Field label="Paid now / down payment (₱)">
                    <TextInput type="number" value={d.paid} placeholder="0" onChange={(e) => setD({ ...d, paid: e.target.value })} />
                  </Field>
                </div>
                <div className="flex rounded-lg p-0.5 shrink-0 mb-0.5" style={{ background: "var(--panel)", border: "1px solid var(--border)" }} title="How the money came in">
                  {[["cash", "Cash", Banknote, ACCENT.amber], ["bank", "Bank", Landmark, "#2F80ED"]].map(([k, l, Icon, c]) => (
                    <button key={k} type="button" onClick={() => setD((x) => ({ ...x, paidBy: k }))}
                      className="text-[11px] px-2.5 py-1.5 rounded-md font-semibold flex items-center gap-1"
                      style={(d.paidBy || "cash") === k ? { background: c, color: "#000" } : { color: "var(--muted)" }}><Icon size={12} /> {l}</button>
                  ))}
                </div>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span style={{ color: "var(--muted)" }}>Unpaid balance (total − paid now)</span>
                <span className="font-mono font-semibold text-sm" style={{ color: grandTotal - paidNow > 0 ? ACCENT.amber : "#22c55e" }}>
                  {peso(Math.max(0, grandTotal - paidNow))}</span>
              </div>
            </>
          )}
        </div>

        <button type="button" onClick={submit} disabled={saving || !d.customer.trim() || filled.length === 0}
          style={{ background: d.customer.trim() ? ACCENT.cyan : "var(--faint)" }}
          className="mt-1 py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:cursor-not-allowed">
          {saving ? "Saving…" : mode === "edit" ? "Save changes" : "Apply"}
        </button>
        {onDismiss && (
          <button type="button" onClick={onDismiss} className="text-[11px] -mt-1" style={{ color: "var(--muted)" }}>
            Not a delivery order — dismiss it
          </button>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// A DELIVERY receipt from a phone, already read into an order: the same Add Order form, filled in. The owner checks
// it, edits anything, picks the truck / driver / helpers, and presses Apply. Used by the green bubble on a sales
// counter and by the dashboard's Priority card.   Data: /api/shop/delivery-drafts/{id} (+ /apply, /dismiss)
// ---------------------------------------------------------------------------
// how a receipt was paid: the sales person picks Cash or Bank on the phone when pressing Update (round 17; before,
// it was read from the photo). mode null = not chosen yet (the phone has not pressed Update).
function PaidTag({ payment }) {
  if (!payment) return null;
  if (!payment.mode) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full" title="The sales person picks Cash or Bank on the phone when pressing Update"
        style={{ color: "var(--muted)", border: "1px dashed var(--border)" }}>not chosen yet</span>
    );
  }
  const bank = payment.mode === "bank";
  const c = bank ? "#2F80ED" : ACCENT.amber;
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full" title={payment.why}
      style={{ color: c, background: `${c}1f` }}>{bank ? <Landmark size={10} /> : <Banknote size={10} />}{bank ? "Bank" : "Cash"}{payment.sure ? "" : "?"}</span>
  );
}

// "Read the photo again": the receipt reader reads it once more (e.g. after the items were added to Supply).
// Waits (up to 3 minutes) until the reader is done; onFresh gets the new result.
async function readAgainAndWait(receiptId, poll, onFresh, alive) {
  await api.post(`/api/shop/receipts/${receiptId}/read-again`);
  for (let i = 0; i < 90 && alive.current; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const fresh = await poll();
    const st = fresh.receiptStatus || fresh.status;
    if (st === "done" || st === "failed") { onFresh(fresh); return; }
  }
}

export function DraftOrderModal({ draftId, onClose, onDone }) {
  const [ctx, setCtx] = useState(null);
  const [rereading, setRereading] = useState(false);
  const [formVer, setFormVer] = useState(0);              // the form starts again after the photo was read again
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => {
    let alive = true;
    Promise.all([api.get(`/api/shop/delivery-drafts/${draftId}`), api.get("/api/shop/orders"), api.get("/api/shop/supply")])
      .then(([draft, o, sup]) => alive && setCtx({
        draft, transports: o.transports, units: o.units || [],
        inventory: sup.flatMap((p) => p.types.map((t) => ({ itemId: t.id, name: t.spec ? `${p.name} ${t.spec}` : p.name, unit: t.unit, price: t.sellPrice,
          stock: t.qty, qtyUnknown: !!t.qtyUnknown }))),
      }))
      .catch(() => onClose());
    return () => { alive = false; };
    // eslint-disable-next-line
  }, [draftId]);
  if (!ctx) return <Modal title="New delivery order" onClose={onClose} width={420}><p className="text-xs" style={{ color: "var(--muted)" }}>Loading…</p></Modal>;
  const d = ctx.draft;
  if (d.status !== "new") {
    return (
      <Modal title="Delivery order" onClose={onClose} width={420}>
        <p className="text-sm">{d.status === "done" ? "This delivery order was already applied." : "This delivery order was dismissed."}</p>
      </Modal>
    );
  }
  const banner = (
    <div className="rounded-lg p-2.5 flex gap-3 items-start" style={{ background: `${DELIVERY_GREEN}12`, border: `1px solid ${DELIVERY_GREEN}66` }}>
      {d.hasImage && (
        <a href={imgUrl(`/api/shop/receipts/${d.receiptId}/image`)} target="_blank" rel="noreferrer" title="Open the receipt photo" className="shrink-0">
          <img src={imgUrl(`/api/shop/receipts/${d.receiptId}/image?w=300`)} alt="receipt" className="w-16 h-20 object-cover rounded-md" style={{ background: "#000" }} />
        </a>
      )}
      <div className="flex-1 min-w-0 text-[11px] flex flex-col gap-0.5">
        <span className="text-xs font-semibold flex items-center gap-1.5" style={{ color: DELIVERY_GREEN }}>
          <MessageCircle size={13} /> Read from {d.person ? `${d.person.name}'s` : "a"} receipt {d.receiptLabel}
        </span>
        <span style={{ color: "var(--muted)" }}>Found on the photo: {d.words.join(", ")}. Check every row, then press Apply.</span>
        {d.payment && (
          <span className="flex items-center gap-1.5 flex-wrap" style={{ color: "var(--muted)" }}>
            Paid: <PaidTag payment={d.payment} /> <span style={{ color: "var(--faint)" }}>{d.payment.why}</span>
          </span>
        )}
        {d.canReadAgain && (
          <button type="button" onClick={readAgain} disabled={rereading} className="self-start flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md mt-0.5 disabled:opacity-60"
            style={{ border: `1px solid ${DELIVERY_GREEN}80`, color: DELIVERY_GREEN }} title="Let the receipt reader read this photo again (e.g. after adding its items to Supply)">
            <RefreshCw size={10} className={rereading ? "animate-spin" : ""} /> {rereading ? "Reading the photo again…" : "Read the photo again"}</button>
        )}
        {d.alsoSold && (
          <span style={{ color: ACCENT.amber }}>
            {d.person ? d.person.name : "The sales person"} already pressed Update on this receipt (it is counted as a sale).
            Applying makes an order too, so check it is not counted twice.
          </span>
        )}
      </div>
    </div>
  );
  async function addTransport(t) {
    const saved = await api.post("/api/shop/transports", t);
    setCtx((c) => ({ ...c, transports: [...c.transports, saved] }));
    return saved;
  }
  async function addUnit(name) {
    const units = await api.post("/api/shop/units", { name });
    setCtx((c) => ({ ...c, units }));
  }
  async function readAgain() {
    setRereading(true);
    try {
      await readAgainAndWait(d.receiptId, () => api.getQuiet(`/api/shop/delivery-drafts/${draftId}`),
        (fresh) => { if (alive.current) { setCtx((c) => ({ ...c, draft: fresh })); setFormVer((v) => v + 1); } }, alive);
    } catch { /* toast */ } finally { if (alive.current) setRereading(false); }
  }
  return (
    <OrderForm key={formVer} mode="add" title={`New delivery order${d.person ? ` from ${d.person.name}` : ""}`} banner={banner}
      initial={draftFromDelivery(d, ctx.transports)} initialNote={d.unmatched.length ? { rows: d.rows.length, unmatched: d.unmatched } : null}
      transports={ctx.transports} units={ctx.units} inventory={ctx.inventory} onAddTransport={addTransport} onAddUnit={addUnit}
      onClose={onClose} checkStock={false}
      onSubmit={async (body) => { const o = await api.post(`/api/shop/delivery-drafts/${draftId}/apply`, body); onDone(o); }}
      onDismiss={async () => {
        if (!window.confirm("Dismiss it? It is not made into an order (the receipt itself stays).")) return;
        try { await api.post(`/api/shop/delivery-drafts/${draftId}/dismiss`); onDone(null); } catch { /* toast */ }
      }} />
  );
}
const ITEM_COLS = "minmax(0,1fr) 64px 118px 80px 28px";

function OrderCard({ order, onClick }) {
  return (
    <button onClick={onClick} className="text-left rounded-lg p-3 flex flex-col gap-2 w-full"
      style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold truncate">{order.customer}</span>
        <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--muted)" }}>{peso(orderTotal(order))}</span>
      </div>
      <ProgressBar percent={deliveryPercent(order)} color={DELIVERY_GREEN} label="Delivered" />
      <ProgressBar percent={paymentPercent(order)} color={ACCENT.cyan} label="Paid" />
    </button>
  );
}

// "Complete delivery & payment": what is still open on the order, done in one step — the items not ticked yet are
// marked delivered, the unpaid balance is recorded as paid (Cash or Bank), and the order is Done (order history).
function FinishOrderModal({ order, onClose, onDone }) {
  const [method, setMethod] = useState("cash");
  const [date, setDate] = useState(() => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; });
  const [busy, setBusy] = useState(false);
  const left = order.items.filter((i) => !i.delivered);
  const unpaid = Math.max(0, orderTotal(order) - paidAmount(order));
  async function finish() {
    setBusy(true);
    try {
      const h = await api.post(`/api/shop/orders/${order.id}/finish`, { method, date });
      onDone(h);
    } catch { /* toast */ } finally { setBusy(false); }
  }
  const box = { background: "var(--panelAlt)", border: "1px solid var(--border)" };
  return (
    <Modal title={`Complete the order — ${order.customer}`} onClose={onClose} width={440}>
      <div className="flex flex-col gap-3 text-sm">
        <div className="rounded-lg px-3 py-2.5 flex flex-col gap-1.5" style={box}>
          <span className="flex items-center gap-2 font-medium">
            {left.length ? <Circle size={15} style={{ color: ACCENT.amber }} /> : <CheckCircle2 size={15} style={{ color: DELIVERY_GREEN }} />}
            {left.length ? `Mark ${left.length} item${left.length === 1 ? "" : "s"} delivered` : "Every item is delivered"}</span>
          {left.length > 0 && (
            <span className="text-[11px] pl-6" style={{ color: "var(--muted)" }}>{left.map((i) => `${i.qty} ${i.unit || ""} ${i.name}`.replace(/\s+/g, " ")).join(" · ")}</span>
          )}
        </div>
        <div className="rounded-lg px-3 py-2.5 flex flex-col gap-2" style={box}>
          <span className="flex items-center gap-2 font-medium">
            {unpaid > 0 ? <Circle size={15} style={{ color: ACCENT.amber }} /> : <CheckCircle2 size={15} style={{ color: DELIVERY_GREEN }} />}
            {unpaid > 0 ? <>The customer pays the rest: <span className="font-mono">{peso(unpaid)}</span></> : "Fully paid"}</span>
          {unpaid > 0 && (
            <div className="flex items-center gap-2 pl-6 flex-wrap">
              <div className="flex rounded-md p-0.5 shrink-0" style={{ background: "var(--panel)", border: "1px solid var(--border)" }} title="How the money came in">
                {[["cash", "Cash", ACCENT.amber], ["bank", "Bank", "#2F80ED"]].map(([k, l, c]) => (
                  <button key={k} type="button" onClick={() => setMethod(k)} className="text-[11px] px-2.5 py-1 rounded font-semibold"
                    style={method === k ? { background: c, color: "#000" } : { color: "var(--muted)" }}>{l}</button>
                ))}
              </div>
              <span className="w-40"><DatePicker value={date} onChange={(v) => setDate(v || date)} placeholder="Paid on" /></span>
            </div>
          )}
        </div>
        <p className="text-[11px]" style={{ color: "var(--muted)" }}>Then the order is Done: it moves to the order history{unpaid > 0 ? " and the payment shows in Payments" : ""}.</p>
        <button onClick={finish} disabled={busy} style={{ background: DELIVERY_GREEN }}
          className="py-2.5 rounded-lg text-sm font-semibold text-black/85 flex items-center justify-center gap-2 disabled:opacity-60">
          <BadgeCheck size={16} /> {busy ? "Finishing…" : "Complete delivery & payment"}</button>
      </div>
    </Modal>
  );
}

function OrderPaymentLog({ payments, onAdd }) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [method, setMethod] = useState("cash");
  const chip = (m) => {
    const c = m === "bank" ? "#2F80ED" : m === "cash" ? ACCENT.amber : "#8996A6";
    return (
      <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 inline-flex items-center gap-1" style={{ color: c, background: `${c}1f` }}>
        {m === "bank" ? <Landmark size={9} /> : m === "cash" ? <Banknote size={9} /> : null}{m === "bank" ? "Bank" : m === "cash" ? "Cash" : "not said"}</span>
    );
  };
  return (
    <div>
      <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>Payments received</label>
      <div className="flex flex-col gap-1.5 max-h-28 overflow-y-auto pr-1 mb-2">
        {payments.map((p) => (
          <div key={p.id} className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md text-xs"
            style={{ border: "1px solid var(--border)", background: "var(--panel)" }}>
            <span className="flex items-center gap-1.5 min-w-0" style={{ color: "var(--muted)" }}>{chip(p.method)}
              <span className="truncate">{p.date}{p.note && !/^(cash|bank)$/i.test(p.note) ? ` · ${p.note.replace(/^(cash|bank)\s+/i, "")}` : ""}</span></span>
            <span className="font-mono shrink-0">{peso(p.amount)}</span>
          </div>
        ))}
        {payments.length === 0 && <p className="text-[11px] text-center py-2" style={{ color: "var(--faint)" }}>No payments logged yet.</p>}
      </div>
      <div className="flex gap-1.5">
        <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount"
          className="w-24 text-xs rounded-md px-2 py-2 outline-none"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <div className="flex rounded-md p-0.5 shrink-0" style={{ background: "var(--panel)", border: "1px solid var(--border)" }} title="How the money came in">
          {[["cash", "Cash", ACCENT.amber], ["bank", "Bank", "#2F80ED"]].map(([k, l, c]) => (
            <button key={k} type="button" onClick={() => setMethod(k)} className="text-[10px] px-2 rounded font-semibold"
              style={method === k ? { background: c, color: "#000" } : { color: "var(--muted)" }}>{l}</button>
          ))}
        </div>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)"
          className="flex-1 min-w-0 text-xs rounded-md px-2 py-2 outline-none"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <button onClick={async () => {
          const v = Number(amount);
          if (!(v > 0)) return;
          try { await onAdd({ amount: v, note: note.trim(), method }); setAmount(""); setNote(""); } catch { /* toast */ }
        }} style={{ background: ACCENT.cyan }} className="px-3 rounded-md text-xs font-semibold text-black/85 shrink-0">
          <Plus size={13} />
        </button>
      </div>
    </div>
  );
}

// chart labels for a period key ("2026-10-15" day / week-start, "2026-10" month)
function periodLabel(key, group) {
  if (group === "month") return new Date(`${key}-01T00:00:00`).toLocaleDateString([], { month: "short" });
  const d = new Date(`${key}T00:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });
  return group === "week" ? `wk ${d}` : d;
}
function nextPeriodLabel(lastKey, group) {
  if (group === "month") { const d = new Date(`${lastKey}-01T00:00:00`); d.setMonth(d.getMonth() + 1); return `${d.toLocaleDateString([], { month: "short" })} (est.)`; }
  const d = new Date(`${lastKey}T00:00:00`); d.setDate(d.getDate() + (group === "week" ? 7 : 1));
  return `${group === "week" ? "wk " : ""}${d.toLocaleDateString([], { month: "short", day: "numeric" })} (est.)`;
}
// straight-line trend through the periods shown, one step ahead (never below 0)
function forecastNext(values) {
  const n = values.length;
  if (!n) return 0;
  const xm = (n - 1) / 2;
  const ym = values.reduce((s, v) => s + v, 0) / n;
  let num = 0, den = 0;
  values.forEach((v, i) => { num += (i - xm) * (v - ym); den += (i - xm) ** 2; });
  const slope = den ? num / den : 0;
  return Math.max(0, Math.round(ym + slope * (n - xm)));
}
const PERIODS = [["day", "Today"], ["week", "This week"], ["month", "This month"], ["all", "All time"]];
const GROUPS = [["day", "Daily", 14], ["week", "Weekly", 8], ["month", "Monthly", 6]];
function Segmented({ value, onChange, options }) {
  return (
    <div className="flex rounded-lg overflow-hidden text-[11px] shrink-0" style={{ border: "1px solid var(--border)" }}>
      {options.map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)} className="px-2.5 py-1"
          style={{ background: value === v ? `${ACCENT.cyan}22` : "transparent", color: value === v ? ACCENT.cyan : "var(--muted)", fontWeight: value === v ? 600 : 400 }}>{l}</button>
      ))}
    </div>
  );
}

export default function SalesPage() {
  const location = useLocation();
  const navigate = useNavigate();
  // --- sales counters (from the phones) -----------------------------------
  const [people, setPeople] = useState([]);
  const [period, setPeriod] = useState("month");            // counters: day | week | month | all
  const [group, setGroup] = useState("month");              // chart: day | week | month
  const [series, setSeries] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [receiptFor, setReceiptFor] = useState(null);
  const [receipts, setReceipts] = useState(null);
  const [viewId, setViewId] = useState(null);
  const [viewData, setViewData] = useState(null);
  const [txnFor, setTxnFor] = useState(null);
  const [txns, setTxns] = useState(null);
  const [totalSalesOpen, setTotalSalesOpen] = useState(false);
  const [allTxns, setAllTxns] = useState({});
  const [summary, setSummary] = useState(null);          // counters + desktop + orders added (Add Order) for the period
  const [hiddenPeople, setHiddenPeople] = useState([]);    // phone logins that were switched off (their sales stay)
  const [showHiddenPeople, setShowHiddenPeople] = useState(false);
  const [rereading, setRereading] = useState(false);
  const viewAlive = useRef(true);
  useEffect(() => () => { viewAlive.current = false; }, []);

  // --- bulk orders --------------------------------------------------------
  const [orders, setOrders] = useState([]);
  const [orderHistory, setOrderHistory] = useState([]);
  const [transports, setTransports] = useState([]);
  const [units, setUnits] = useState([]);
  const [inventory, setInventory] = useState([]);           // for the item suggestions in the order form
  const [form, setForm] = useState(null);                   // { mode: "add" | "edit", order?, initial }
  const [orderProfileId, setOrderProfileId] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [draftId, setDraftId] = useState(null);             // a delivery receipt read into an order (green bubble)
  const [draftList, setDraftList] = useState(null);         // { person, list } when one person sent several
  const orderProfile = orders.find((o) => o.id === orderProfileId) || null;
  const colorOf = (i) => PALETTE[i % PALETTE.length];

  async function loadPeople() {
    try {
      const count = GROUPS.find((g) => g[0] === group)[2];
      const [p, sr, sm, hp] = await Promise.all([api.get(`/api/shop/sales-people?period=${period}`), api.get(`/api/shop/sales-series?group=${group}&count=${count}`),
        api.get(`/api/shop/sales-summary?period=${period}`), api.getQuiet("/api/shop/sales-people/removed").catch(() => [])]);
      setPeople(p); setSeries(sr); setSummary(sm); setHiddenPeople(hp);
    } catch { /* toast */ }
    setLoaded(true);
  }
  async function loadOrders() {
    try {
      const d = await api.get("/api/shop/orders");
      setOrders(d.active); setOrderHistory(d.history); setTransports(d.transports); setUnits(d.units || []);
    } catch { /* toast */ }
  }
  async function loadInventory() {
    try {
      const sup = await api.get("/api/shop/supply");
      setInventory(sup.flatMap((p) => p.types.map((t) => ({ itemId: t.id, name: t.spec ? `${p.name} ${t.spec}` : p.name, unit: t.unit, price: t.sellPrice,
        stock: t.qty, qtyUnknown: !!t.qtyUnknown }))));
    } catch { /* toast */ }
  }
  useEffect(() => { loadOrders(); }, []);
  useEffect(() => {
    loadPeople();
    const t = setInterval(loadPeople, 15000);         // new phone sales show up by themselves
    return () => clearInterval(t);
    // eslint-disable-next-line
  }, [period, group]);

  useEffect(() => {
    if (!receiptFor) return;
    setReceipts(null);
    api.get(`/api/shop/sales-people/${receiptFor.userId}/receipts`).then(setReceipts).catch(() => setReceipts([]));
  }, [receiptFor]);
  useEffect(() => {
    if (!viewId) { setViewData(null); return; }
    api.get(`/api/shop/receipts/${viewId}`).then(setViewData).catch(() => {});
  }, [viewId]);
  // /sales?receipt=<id> (back from Supply after "Add to Supply"): open that receipt again
  useEffect(() => {
    const id = Number(new URLSearchParams(location.search).get("receipt"));
    if (id > 0) { setViewData(null); setViewId(id); navigate("/sales", { replace: true }); }
    // eslint-disable-next-line
  }, [location.search]);
  // Receipt -> a row the shop does not carry (or read as the wrong item): add it to Supply, already filled in.
  // Supply links the row to the new item and comes back here.
  function addToSupply(row, kind) {
    const q = new URLSearchParams({ add: row.name || "", receipt: String(viewId), row: `${kind}${row.id}`,
      label: viewData ? viewData.label : "", ocr: (row.ocr || "").slice(0, 120) });
    if (row.price) q.set("price", String(Math.round(row.price * 100) / 100));
    if (row.unit) q.set("unit", row.unit);
    if (row.qty) q.set("qty", String(row.qty));
    navigate(`/supply?${q.toString()}`);
  }
  useEffect(() => {
    if (!txnFor) return;
    setTxns(null);
    api.get(`/api/shop/sales-people/${txnFor.userId}/transactions`).then(setTxns).catch(() => setTxns([]));
  }, [txnFor]);
  useEffect(() => {
    if (!totalSalesOpen) return;
    Promise.all(people.map((p) => api.get(`/api/shop/sales-people/${p.userId}/transactions?limit=50`).then((t) => [p.userId, t])))
      .then((pairs) => setAllTxns(Object.fromEntries(pairs))).catch(() => {});
  }, [totalSalesOpen, people]);

  async function openDrafts(p) {
    try {
      const list = await api.get(`/api/shop/delivery-drafts?userId=${p.userId}`);
      if (list.length === 1) setDraftId(list[0].id);
      else if (list.length) setDraftList({ person: p, list });
      else loadPeople();
    } catch { /* toast */ }
  }

  // The "−" badge: the phone login stops working; receipts and sales stay in the records.
  async function removeSalesPerson(id) {
    const person = people.find((p) => p.userId === id);
    if (!person) return;
    if (!window.confirm(`Remove ${person.name}'s counter? Their phone login stops working. Their past receipts and sales stay in the records `
      + "(Show hidden brings the counter back).")) return;
    try { await api.post(`/api/shop/sales-people/${id}/remove`); loadPeople(); } catch { /* toast */ }
  }
  async function restoreSalesPerson(h) {
    try { await api.post(`/api/shop/sales-people/${h.userId}/restore`); loadPeople(); } catch { /* toast */ }
  }
  // Receipt -> Delete: the photo and its rows go. A receipt already sold also undoes that sale (stock back, total down).
  async function deleteReceipt(r) {
    const msg = r.sold
      ? `Delete receipt ${r.label}? It was already SOLD (${peso(r.total)}). Deleting it also undoes that sale: the stock goes back `
        + `and ${peso(r.total)} leaves ${receiptFor ? `${receiptFor.name}'s` : "the"} sales. This cannot be undone.`
      : `Delete receipt ${r.label}? The photo and the rows read from it are removed. This cannot be undone.`;
    if (!window.confirm(msg)) return;
    try {
      await api.del(`/api/shop/receipts/${r.id}${r.sold ? "?revert=true" : ""}`);
      setReceipts((list) => (list || []).filter((x) => x.id !== r.id));
      if (viewId === r.id) setViewId(null);
      loadPeople();
    } catch { /* toast */ }
  }
  // "Is it ...?" on a row not in Supply: the row is that Supply item
  async function linkRow(rowId, itemId) {
    try { setViewData(await api.post(`/api/shop/receipts/${viewId}/rows/u/${rowId}/item`, { itemId })); } catch { /* toast */ }
  }
  async function readReceiptAgain() {
    const id = viewId;
    setRereading(true);
    try {
      await readAgainAndWait(id, () => api.getQuiet(`/api/shop/receipts/${id}`), (fresh) => { if (viewAlive.current) setViewData(fresh); }, viewAlive);
    } catch { /* toast */ } finally { if (viewAlive.current) setRereading(false); }
  }

  // --- chart: totals per day / week / month + the next period's estimate (dashed) ---
  const chart = useMemo(() => {
    if (!series) return [];
    const rows = series.keys.map((k) => {
      const row = { month: periodLabel(k, series.group) };
      people.forEach((p) => { row[p.name] = 0; });
      return row;
    });
    series.rows.forEach((r) => {
      const p = people.find((x) => x.userId === r.uid);
      const i = series.keys.indexOf(r.k);
      if (p && i >= 0) rows[i][p.name] = r.total;
    });
    if (!rows.length) return rows;
    const nextRow = { month: nextPeriodLabel(series.keys[series.keys.length - 1], series.group) };
    people.forEach((p) => {
      rows[rows.length - 1][`${p.name} est`] = rows[rows.length - 1][p.name] || 0;
      nextRow[`${p.name} est`] = forecastNext(rows.map((r) => r[p.name] || 0));
    });
    return [...rows, nextRow];
  }, [series, people]);
  const hasSales = !!series && series.rows.length > 0;
  const counterTotal = people.reduce((s, p) => s + p.total, 0);
  const grand = summary ? summary.total : counterTotal;
  const periodName = PERIODS.find((x) => x[0] === period)[1];

  // --- order form (add / edit) ---
  async function addTransport(t) {
    const saved = await api.post("/api/shop/transports", t);
    setTransports((prev) => [...prev, saved]);
    return saved;
  }
  async function addUnit(name) {
    setUnits(await api.post("/api/shop/units", { name }));
  }
  function openAdd() { loadInventory(); setForm({ mode: "add", initial: blankOrderDraft() }); }
  function openEdit(order) { loadInventory(); setForm({ mode: "edit", order, initial: draftFromOrder(order), checkStock: !order.fromReceipt }); }
  async function submitForm(body) {
    if (form.mode === "edit") {
      const o = await api.put(`/api/shop/orders/${form.order.id}`, body);
      setOrders((prev) => prev.map((x) => (x.id === o.id ? o : x)));
    } else {
      const o = await api.post("/api/shop/orders", body);
      setOrders((prev) => [...prev, o]);
    }
    setForm(null);
  }
  const replaceOrder = (o) => setOrders((prev) => prev.map((x) => (x.id === o.id ? o : x)));
  async function toggleItemDelivered(orderId, itemId) {
    try { replaceOrder(await api.post(`/api/shop/orders/${orderId}/items/${itemId}/toggle`)); } catch { /* toast */ }
  }
  async function addOrderPayment(orderId, payment) {
    replaceOrder(await api.post(`/api/shop/orders/${orderId}/payments`, payment));
  }
  const [finishFor, setFinishFor] = useState(null);       // the "Complete delivery & payment" popup
  function orderFinished(h) {
    setOrderHistory((prev) => [h, ...prev]);
    setOrders((prev) => prev.filter((o) => o.id !== h.id));
    setFinishFor(null);
    setOrderProfileId(null);
  }
  async function completeOrder(order) {
    try {
      const h = await api.post(`/api/shop/orders/${order.id}/complete`);
      setOrderHistory((prev) => [h, ...prev]);
      setOrders((prev) => prev.filter((o) => o.id !== order.id));
      setOrderProfileId(null);
    } catch { /* toast */ }
  }
  async function deleteOrder(order) {
    const back = order.items.filter((i) => (i.stockTaken || 0) > 0 && !i.delivered);
    if (!window.confirm(`Delete the order of ${order.customer}? Use this only for a mistake — it removes its payments too.`
      + (back.length ? `\n\nBack to Supply (not delivered yet): ${back.map((i) => `${fmtQ(i.stockTaken)} ${i.unit} ${i.name}`).join(", ")}.` : ""))) return;
    try {
      await api.del(`/api/shop/orders/${order.id}`);
      setOrders((prev) => prev.filter((o) => o.id !== order.id));
      setOrderProfileId(null);
    } catch { /* toast */ }
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* ---- Sales counters row: one card per sales person with a phone login ---- */}
      <div className="flex items-center justify-between gap-3 -mb-2">
        <h3 className="font-semibold text-sm">Sales counters <span className="font-normal text-xs" style={{ color: "var(--muted)" }}>· {periodName.toLowerCase()}</span></h3>
        <div className="flex items-center gap-2">
          <HiddenToggle on={showHiddenPeople} count={hiddenPeople.length} onChange={setShowHiddenPeople} what="removed counters" />
          <Segmented value={period} onChange={setPeriod} options={PERIODS} />
        </div>
      </div>
      <div className="flex gap-4 overflow-x-auto pb-1">
        {people.map((p, i) => (
          <div key={p.userId} style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }}
            className="relative rounded-xl p-4 flex flex-col items-center gap-2 w-[168px] shrink-0">
            <button onClick={() => removeSalesPerson(p.userId)} title="Remove this sales counter"
              className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center"
              style={{ background: `${ACCENT.danger}18`, color: ACCENT.danger, border: `1px solid ${ACCENT.danger}45` }}>
              <Minus size={12} />
            </button>
            {p.drafts > 0 && (
              <button onClick={() => openDrafts(p)} title="A delivery order was read from this person's receipt: open it"
                className="absolute top-2 left-2 flex items-center gap-1 pl-1.5 pr-2 py-0.5 rounded-full text-[10px] font-semibold text-white shadow"
                style={{ background: DELIVERY_GREEN }}>
                <span className="relative flex w-2 h-2">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-white opacity-75 animate-ping" />
                  <span className="relative inline-flex rounded-full w-2 h-2 bg-white" />
                </span>
                <MessageCircle size={11} /> {p.drafts} new order{p.drafts > 1 ? "s" : ""}
              </button>
            )}
            <PersonPhoto p={p} color={colorOf(i)} />
            {/* the name always takes 2 lines, so every card has its buttons at the same height */}
            <p className="text-sm font-semibold text-center leading-tight h-[2.25rem] w-full flex items-center justify-center" title={p.name}>
              <span className="line-clamp-2 break-words">{p.name}</span></p>
            <p className="text-[11px]" style={{ color: "var(--muted)" }}>{p.counter}</p>
            <div className="mt-auto w-full flex flex-col gap-2 pt-1">
              <button onClick={() => setReceiptFor(p)} style={{ border: "1px solid var(--border)", color: "var(--text)" }}
                className="w-full flex items-center justify-center gap-1.5 text-xs py-1.5 rounded-lg hover:bg-white/5">
                <Receipt size={13} /> Receipt
              </button>
              <button onClick={() => setTxnFor(p)} style={{ background: `${colorOf(i)}18`, border: `1px solid ${colorOf(i)}55`, color: colorOf(i) }}
                className="w-full text-xs py-1.5 rounded-lg font-semibold font-mono">
                {peso(p.total)}
              </button>
            </div>
          </div>
        ))}
        {showHiddenPeople && hiddenPeople.map((h) => (
          <div key={h.userId} style={{ border: "1px dashed var(--border)", background: "var(--panel)", opacity: 0.8 }}
            className="relative rounded-xl p-4 flex flex-col items-center gap-2 w-[168px] shrink-0">
            <span className="absolute top-2 right-2 text-[9px] font-semibold px-1.5 py-0.5 rounded-full" style={{ color: "var(--muted)", border: "1px solid var(--border)" }}>REMOVED</span>
            <Avatar name={h.name} color="#8996A6" size={64} />
            <p className="text-sm font-semibold text-center leading-tight h-[2.25rem] w-full flex items-center justify-center" title={h.name}>
              <span className="line-clamp-2 break-words">{h.name}</span></p>
            <p className="text-[11px] text-center" style={{ color: "var(--muted)" }}>@{h.username} · {h.nSales} sale{h.nSales === 1 ? "" : "s"}<br />{peso(h.total)} in all</p>
            <button onClick={() => restoreSalesPerson(h)} className="w-full flex items-center justify-center gap-1.5 text-xs py-1.5 rounded-lg mt-auto"
              style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }} title="Switch the phone login on again (same password)">
              <RotateCcw size={12} /> Switch back on</button>
          </div>
        ))}
        {loaded && people.length === 0 && (
          <p className="text-xs py-6" style={{ color: "var(--faint)" }}>
            No sales counters yet. Add the person in Cash Flow → Employee (with their photo), then create their phone login there — their counter appears here.
          </p>
        )}
      </div>

      <button onClick={() => setTotalSalesOpen(true)} style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }}
        className="rounded-xl px-5 py-3 flex items-center justify-between text-sm">
        <span className="font-semibold">Total Sales — Counters + Orders <span className="font-normal text-xs" style={{ color: "var(--muted)" }}>({periodName.toLowerCase()})</span></span>
        <span className="flex items-center gap-3">
          {summary && (
            <span className="text-[11px] font-mono hidden md:inline" style={{ color: "var(--muted)" }}>
              counters {peso(summary.counters)}{summary.desktop.total ? ` + desktop ${peso(summary.desktop.total)}` : ""} + orders {peso(summary.ordersTotal)} =
            </span>
          )}
          <span className="font-mono font-semibold" style={{ color: ACCENT.cyan }}>{peso(grand)}</span>
        </span>
      </button>

      {/* ---- Split view: chart on the left, bulk/VIP Orders on the right ---- */}
      <div className="flex gap-4 items-stretch">
        <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4 flex-[2] min-w-0">
          <div className="flex items-center justify-between gap-2 mb-2">
            <h3 className="font-semibold text-sm">Sales Prediction — Per Counter</h3>
            <Segmented value={group} onChange={setGroup} options={GROUPS.map(([v, l]) => [v, l])} />
          </div>
          <div style={{ height: 220 }}>
            {!hasSales ? (
              <p className="text-xs text-center pt-20" style={{ color: "var(--faint)" }}>
                No sales in this view yet. Totals (and the next {group === "day" ? "day" : group}'s estimate) appear after a sales person presses Update on the phone.
              </p>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 10, fill: "var(--muted)" }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: "var(--muted)" }} axisLine={false} tickLine={false} width={44} />
                  <Tooltip contentStyle={{ background: "var(--panelAlt)", border: "1px solid var(--border)", fontSize: 12, borderRadius: 8 }}
                    formatter={(v, n) => [peso(v), n]} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {people.map((p, i) => <Line key={p.userId} type="monotone" dataKey={p.name} stroke={colorOf(i)} strokeWidth={2} dot={false} connectNulls={false} />)}
                  {people.map((p, i) => (
                    <Line key={`${p.userId}-est`} type="monotone" dataKey={`${p.name} est`} stroke={colorOf(i)} strokeWidth={2}
                      strokeDasharray="4 4" dot={false} legendType="none" />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }}
          className="rounded-xl p-4 w-72 shrink-0 flex flex-col gap-3">
          <button onClick={openAdd}
            style={{ background: ACCENT.cyan }}
            className="w-full flex items-center justify-center gap-2 text-sm font-semibold text-black/85 py-2.5 rounded-lg">
            <PackagePlus size={16} /> Add Order
          </button>
          <p className="text-[10px]" style={{ color: "var(--faint)" }}>For big/VIP clients — bulk cement, gravel, etc.</p>
          <div className="flex flex-col gap-2 overflow-y-auto pr-1" style={{ maxHeight: 240 }}>
            {orders.length === 0 && (
              <p className="text-[11px] text-center py-4" style={{ color: "var(--faint)" }}>No active bulk orders.</p>
            )}
            {orders.map((o) => (
              <OrderCard key={o.id} order={o} onClick={() => setOrderProfileId(o.id)} />
            ))}
          </div>
          <button onClick={() => setHistoryOpen(true)}
            style={{ border: "1px solid var(--border)", color: "var(--muted)" }}
            className="w-full flex items-center justify-center gap-1.5 text-xs py-2 rounded-lg hover:bg-white/5">
            <History size={13} /> Order History
          </button>
        </div>
      </div>

      {/* ---- Receipts of one sales person: name = month+day+hour_minute (Jan 1, 1:30 = 111_30), total on the right ---- */}
      {receiptFor && (
        <Modal title={`Receipts — ${receiptFor.name}`} onClose={() => setReceiptFor(null)}>
          <div className="flex items-center gap-2 mb-3 text-xs" style={{ color: "var(--muted)" }}>
            <Folder size={14} /> Shop server · Receipts / {receiptFor.counter}
          </div>
          <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto pr-1">
            {receipts === null && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>Loading…</p>}
            {receipts && receipts.map((r) => (
              <div key={r.id} style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }} className="flex items-center rounded-lg text-sm">
                <button onClick={() => setViewId(r.id)} className="flex-1 min-w-0 flex items-center justify-between px-3 py-2 text-left">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileImage size={15} style={{ color: ACCENT.cyan }} />
                    <div className="min-w-0">
                      <p className="truncate">{r.label}</p>
                      <p className="text-[10px]" style={{ color: STATE_COLOR[r.state] || "var(--muted)" }}>
                        {STATE_LABEL[r.state] || r.state} · {new Date(r.created).toLocaleDateString([], { month: "short", day: "numeric" })}
                        {r.notInSupply > 0 && <span style={{ color: ACCENT.amber }}> · {r.notInSupply} not in Supply{r.notInSupplyTotal ? ` (${peso(r.notInSupplyTotal)})` : ""}</span>}
                      </p>
                    </div>
                  </div>
                  <span className="font-mono text-xs shrink-0" style={{ color: r.sold ? ACCENT.cyan : "var(--muted)" }}>{peso(r.total)}</span>
                </button>
                <button onClick={() => deleteReceipt(r)} title={r.sold ? "Delete this receipt (also undoes its sale)" : "Delete this receipt"}
                  className="px-2.5 py-2 shrink-0 opacity-70 hover:opacity-100" style={{ color: ACCENT.danger }}><Trash2 size={14} /></button>
              </div>
            ))}
            {receipts && receipts.length === 0 && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>No receipts yet.</p>}
          </div>
          <p className="text-[10px] mt-2" style={{ color: "var(--faint)" }}>
            Name = month · day · hour _ minute (Jan 1, 1:30 → 111_30). Grey totals are not updated on the phone yet.
          </p>
        </Modal>
      )}

      {/* ---- One receipt: the photo + the rows the sales person confirmed ---- */}
      {viewId && (
        <Modal title={viewData ? `Receipt ${viewData.label}` : "Receipt"} onClose={() => setViewId(null)} width={520}>
          <div className="flex flex-col gap-3">
            <a href={imgUrl(`/api/shop/receipts/${viewId}/image`)} target="_blank" rel="noreferrer" title="Open full size">
              <img src={imgUrl(`/api/shop/receipts/${viewId}/image?w=800`)} alt="receipt"
                style={{ maxHeight: 320, objectFit: "contain", border: "1px solid var(--border)", background: "#000" }} className="w-full rounded-lg" />
            </a>
            {viewData && (
              <>
                <div className="flex items-center gap-2 text-[11px] flex-wrap" style={{ color: "var(--muted)" }}>
                  {viewData.payment && <>Paid: <PaidTag payment={viewData.payment} /> <span style={{ color: "var(--faint)" }}>
                    {viewData.payment.mode ? viewData.payment.why : "the sales person picks Cash or Bank on the phone when pressing Update"}</span></>}
                  {!viewData.lines.some((l) => l.sold) && (
                    <button type="button" onClick={readReceiptAgain} disabled={rereading || ["queued", "processing"].includes(viewData.status)}
                      className="ml-auto flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md disabled:opacity-60" style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
                      title="Let the receipt reader read this photo again">
                      <RefreshCw size={10} className={rereading ? "animate-spin" : ""} /> {rereading ? "Reading again…" : "Read the photo again"}</button>
                  )}
                </div>
                <div className="flex flex-col gap-1 max-h-56 overflow-y-auto pr-1">
                  {viewData.lines.map((l) => (
                    <div key={l.id} style={{ background: "var(--panelAlt)" }} className="px-3 py-1.5 rounded-md text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0">{l.item} <span style={{ color: "var(--muted)" }}>× {l.qty ?? "?"} {l.unit}</span>
                          {!l.sold && <span style={{ color: ACCENT.amber }}> · not updated</span>}
                          {!l.sold && !l.sure && <span style={{ color: "var(--faint)" }}> · please check</span>}</span>
                        <span className="font-mono shrink-0">{peso(l.lineTotal)}</span>
                      </div>
                      {(l.ocr || (l.price && Math.abs(l.price - l.sellPrice) >= 0.5)) && (
                        <div className="flex items-center justify-between gap-2 mt-0.5 text-[10px]" style={{ color: "var(--faint)" }}>
                          <span className="truncate min-w-0" title={l.ocr}>read: “{l.ocr}”
                            {l.price && Math.abs(l.price - l.sellPrice) >= 0.5 ? <span style={{ color: ACCENT.amber }}> · receipt says ₱{l.price.toLocaleString()}, Supply ₱{l.sellPrice.toLocaleString()}</span> : null}</span>
                          {!l.sold && (
                            <button onClick={() => addToSupply({ id: l.id, name: l.suggest, price: l.price, qty: l.qty, ocr: l.ocr }, "l")}
                              title="Not this item? Add what the receipt says as a new Supply item; this row then points to it"
                              className="shrink-0 underline-offset-2 hover:underline" style={{ color: ACCENT.cyan }}>Not this item?</button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                  {viewData.lines.length === 0 && !(viewData.unmatched || []).length && (
                    <p className="text-xs text-center py-3" style={{ color: "var(--faint)" }}>No item rows were read from this photo.</p>
                  )}
                  {(viewData.unmatched || []).length > 0 && (
                    <p className="text-[10px] font-semibold tracking-wide mt-1.5" style={{ color: ACCENT.amber }}>NOT IN SUPPLY — {viewData.unmatched.length} ROW{viewData.unmatched.length === 1 ? "" : "S"}</p>
                  )}
                  {(viewData.unmatched || []).map((u) => (
                    <div key={`u${u.id}`} style={{ background: "var(--panelAlt)", border: `1px dashed ${ACCENT.amber}70` }} className="px-3 py-1.5 rounded-md text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0">{u.item || "?"} <span style={{ color: "var(--muted)" }}>× {u.qty ?? "?"} {u.unit}</span>
                          {u.price ? <span style={{ color: "var(--muted)" }}> · ₱{u.price.toLocaleString()} each</span> : null}</span>
                        {!u.request && (
                          <button onClick={() => addToSupply({ id: u.id, name: u.item, price: u.price, unit: u.unit, qty: u.qty, ocr: u.ocr }, "u")}
                            title="Add this item to Supply (name, price and unit filled in); the receipt row is then matched to it"
                            className="shrink-0 flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md text-black/85" style={{ background: ACCENT.cyan }}>
                            <PackagePlus size={12} /> Add to Supply</button>
                        )}
                      </div>
                      {u.request && (
                        <div className="flex items-center gap-2 mt-1 text-[11px] rounded-md px-2 py-1" style={{ background: `${ACCENT.cyan}12`, color: "var(--muted)" }}>
                          <Smartphone size={12} style={{ color: ACCENT.cyan }} className="shrink-0" />
                          <span className="min-w-0 flex-1">{u.request.by || "The sales person"} sent it as a new item: <b style={{ color: "var(--text)" }}>{u.request.name}</b>,
                            {" "}{u.request.qty} {u.request.unit} at ₱{(u.request.price || 0).toLocaleString()}</span>
                          <button onClick={() => navigate(`/supply?request=${u.id}`)} className="shrink-0 font-semibold underline-offset-2 hover:underline"
                            style={{ color: ACCENT.cyan }} title="Accept it into Supply (it is then sold) or remove it">Accept / remove in Supply →</button>
                        </div>
                      )}
                      {u.maybe && !u.request && (
                        <div className="flex items-center gap-2 mt-1 text-[11px]" style={{ color: "var(--muted)" }}>
                          <span className="min-w-0 truncate">Is it <b style={{ color: "var(--text)" }}>{u.maybe.name}</b>
                            {u.maybe.sellPrice ? ` (₱${u.maybe.sellPrice.toLocaleString()} / ${u.maybe.unit})` : ""}?</span>
                          <button onClick={() => linkRow(u.id, u.maybe.itemId)} className="shrink-0 font-semibold underline-offset-2 hover:underline"
                            style={{ color: ACCENT.cyan }} title="This row is that Supply item: it moves up to the receipt's items">Yes, it is</button>
                        </div>
                      )}
                      {u.ocr && <p className="text-[10px] mt-0.5 truncate" style={{ color: "var(--faint)" }} title={u.ocr}>read: “{u.ocr}”</p>}
                    </div>
                  ))}
                </div>
                <div style={{ borderTop: "1px solid var(--border)" }} className="pt-2 flex justify-between text-sm font-semibold">
                  <span>Total</span><span className="font-mono">{peso(viewData.lines.reduce((s, l) => s + l.lineTotal, 0))}</span>
                </div>
                {(viewData.unmatched || []).some((u) => u.price) && (
                  <p className="text-[10px] -mt-2 text-right" style={{ color: ACCENT.amber }}>
                    + {peso((viewData.unmatched || []).reduce((s, u) => s + (u.price || 0) * (u.qty || 1), 0))} on the receipt for items not in Supply yet
                  </p>
                )}
              </>
            )}
            <div className="flex items-center gap-2">
              <p className="text-[10px] flex items-center gap-1 flex-1" style={{ color: "var(--faint)" }}>
                <Lock size={10} /> The sales person checks and corrects the quantities on the phone before pressing Update.
              </p>
              {viewData && (
                <button onClick={() => {
                  const soldTotal = viewData.lines.filter((l) => l.sold).reduce((a, l) => a + l.lineTotal, 0);
                  const fromList = (receipts || []).find((x) => x.id === viewId);
                  deleteReceipt(fromList || { id: viewId, label: viewData.label, sold: viewData.lines.some((l) => l.sold), total: soldTotal });
                }} className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md shrink-0" style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}>
                  <Trash2 size={12} /> Delete receipt</button>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* ---- Per-person transactions (every item sold) ---- */}
      {txnFor && (
        <Modal title={`Transactions — ${txnFor.name}`} onClose={() => setTxnFor(null)}>
          <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto pr-1">
            {txns === null && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>Loading…</p>}
            {txns && txns.map((t) => (
              <div key={t.id} style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }} className="flex items-center justify-between px-3 py-2 rounded-lg text-sm">
                <span className="min-w-0 truncate">{t.item} × {t.qty} {t.unit}
                  <span className="text-[10px]" style={{ color: "var(--muted)" }}> · {new Date(t.created).toLocaleDateString([], { month: "short", day: "numeric" })}</span></span>
                <span className="font-mono shrink-0" style={{ color: ACCENT.cyan }}>{peso(t.amount)}</span>
              </div>
            ))}
            {txns && txns.length === 0 && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>Nothing sold yet.</p>}
          </div>
          <div style={{ borderTop: "1px solid var(--border)" }} className="mt-3 pt-2.5 flex justify-between text-sm font-semibold">
            <span>Total</span><span className="font-mono">{peso(txnFor.total)}</span>
          </div>
        </Modal>
      )}

      {/* ---- All-counters rollup ---- */}
      {totalSalesOpen && (
        <Modal title="Total Sales — Counters + Orders" onClose={() => setTotalSalesOpen(false)} width={520}>
          <div className="flex flex-col gap-3 max-h-80 overflow-y-auto pr-1">
            {people.map((p, i) => (
              <div key={p.userId}>
                <div className="flex items-center justify-between text-sm font-semibold mb-1.5">
                  <span style={{ color: colorOf(i) }}>{p.name}</span>
                  <span className="font-mono">{peso(p.total)}</span>
                </div>
                <div className="flex flex-col gap-1">
                  {(allTxns[p.userId] || []).map((t) => (
                    <div key={t.id} style={{ background: "var(--panelAlt)" }} className="flex items-center justify-between px-3 py-1.5 rounded-md text-xs">
                      <span style={{ color: "var(--muted)" }}>{t.item} × {t.qty} {t.unit}</span>
                      <span className="font-mono">{peso(t.amount)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {people.length === 0 && <p className="text-xs text-center py-6" style={{ color: "var(--faint)" }}>No sales counters yet.</p>}
            {summary && summary.desktop.count > 0 && (
              <div className="flex items-center justify-between text-sm font-semibold">
                <span>Desktop sales</span><span className="font-mono">{peso(summary.desktop.total)}</span>
              </div>
            )}
            {summary && (
              <div>
                <div className="flex items-center justify-between text-sm font-semibold mb-1.5">
                  <span style={{ color: ACCENT.copper }}>Orders added (Add Order)</span>
                  <span className="font-mono">{peso(summary.ordersTotal)}</span>
                </div>
                <div className="flex flex-col gap-1">
                  {summary.orders.map((o) => (
                    <div key={o.id} style={{ background: "var(--panelAlt)" }} className="flex items-center justify-between px-3 py-1.5 rounded-md text-xs">
                      <span style={{ color: "var(--muted)" }}>{o.customer} · {o.date} · paid {peso(o.paid)}</span>
                      <span className="font-mono">{peso(o.total)}</span>
                    </div>
                  ))}
                  {summary.orders.length === 0 && <span className="text-[11px]" style={{ color: "var(--faint)" }}>No orders added in this period.</span>}
                </div>
              </div>
            )}
          </div>
          <div style={{ borderTop: "1px solid var(--border)" }} className="mt-3 pt-2.5 flex flex-col gap-1 text-sm">
            <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Counters{summary && Math.abs(summary.counters - counterTotal) > 0.005 ? " (with removed counters)" : ""}</span>
              <span className="font-mono">{peso(summary ? summary.counters : counterTotal)}</span></div>
            {summary && summary.desktop.total > 0 && <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Desktop</span><span className="font-mono">{peso(summary.desktop.total)}</span></div>}
            {summary && <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Orders</span><span className="font-mono">{peso(summary.ordersTotal)}</span></div>}
            <div className="flex justify-between font-semibold"><span>Total</span><span className="font-mono">{peso(grand)}</span></div>
          </div>
        </Modal>
      )}

      {/* ---- Add / Edit Order ---- */}
      {form && (
        <OrderForm mode={form.mode} initial={form.initial} transports={transports} units={units} inventory={inventory}
          onAddTransport={addTransport} onAddUnit={addUnit} onSubmit={submitForm} onClose={() => setForm(null)}
          checkStock={form.checkStock !== false} />
      )}

      {/* ---- Order profile: delivery checklist + payment log + Done ---- */}
      {orderProfile && (
        <Modal title={orderProfile.customer} onClose={() => setOrderProfileId(null)} width={480}>
          {(() => {
            const total = orderTotal(orderProfile);
            const unpaid = Math.max(0, total - paidAmount(orderProfile));
            const runsCost = orderProfile.deliveryCost ?? deliveryCost(orderProfile, transports);
            const itemsSum = orderProfile.itemsTotal ?? itemsTotalOf(orderProfile);
            const complete = isOrderComplete(orderProfile);
            return (
              <div className="flex flex-col gap-4">
                {orderProfile.address && (
                  <div className="text-xs flex items-center gap-1.5 -mb-1" style={{ color: "var(--muted)" }}><MapPin size={13} /> {orderProfile.address}</div>
                )}
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                    <p style={{ color: "var(--muted)" }}>Target start</p>
                    <p className="font-mono">{orderProfile.startDate || "—"}</p>
                  </div>
                  <div className="rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                    <p style={{ color: "var(--muted)" }}>Target end</p>
                    <p className="font-mono">{orderProfile.endDate || "—"}</p>
                  </div>
                </div>

                {(orderProfile.deliveryRuns || []).length > 0 && (
                  <div>
                    <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>Delivery</label>
                    <div className="flex flex-col gap-1.5">
                      {orderProfile.deliveryRuns.map((r) => {
                        const m = runModeOf(r);
                        return (
                          <div key={r.id} className="flex flex-col gap-1 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                            <div className="flex items-center gap-2.5">
                              <m.Icon size={15} style={{ color: r.done ? DELIVERY_GREEN : m.color }} />
                              <span className="flex-1">{runLabel(r, transports)} · {r.trips} trip{r.trips === 1 ? "" : "s"} · {r.date || "no date set"}{r.done ? (r.mode === "pickup" ? " · picked up" : " · delivered") : ""}</span>
                              <span className="font-mono shrink-0">{r.mode === "pickup" ? "no cost" : peso(r.cost ?? runCost(r, transports))}</span>
                            </div>
                            {r.mode === "pickup" || r.mode === "other" ? (
                              <div className="pl-6 text-[11px]" style={{ color: "var(--muted)" }}>
                                {r.mode === "pickup" ? "The customer comes for it with his own vehicle." : `${r.carrier || "Someone else"} delivers it${r.carrierCost ? ` · ${peso(r.carrierCost)} per trip` : ""}.`}
                              </div>
                            ) : (
                              <div className="flex items-center gap-3 pl-6 text-[11px]" style={{ color: "var(--muted)" }}>
                                <span className="flex items-center gap-1"><UserRound size={11} /> {r.driverName || <span style={{ color: ACCENT.amber }}>no driver yet</span>}</span>
                                <span className="flex items-center gap-1 min-w-0 truncate"><Users size={11} /> {r.helpers && r.helpers.length ? r.helpers.map((h) => h.name).join(", ") : <span style={{ color: ACCENT.amber }}>no helper yet</span>}</span>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    <div className="text-[11px] flex items-center justify-between mt-1.5" style={{ color: "var(--muted)" }}>
                      <span>Delivery cost{orderProfile.chargeDelivery ? " (the customer pays it)" : " (the shop pays it)"}</span>
                      <span className="font-mono">{peso(runsCost)}</span>
                    </div>
                  </div>
                )}

                <div>
                  <label className="text-[11px] font-medium mb-1 block" style={{ color: "var(--muted)" }}>Items (tap to mark delivered)</label>
                  <div className="flex flex-col gap-1">
                    {orderProfile.items.map((it) => (
                      <button key={it.id} onClick={() => toggleItemDelivered(orderProfile.id, it.id)}
                        className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-md text-sm"
                        style={{ background: "var(--panelAlt)" }}>
                        {it.delivered
                          ? <CheckCircle2 size={16} style={{ color: DELIVERY_GREEN }} />
                          : <Circle size={16} style={{ color: "var(--faint)" }} />}
                        <span className="flex-1" style={{ opacity: it.delivered ? 0.6 : 1, textDecoration: it.delivered ? "line-through" : "none" }}>
                          {it.qty} {it.unit} — {it.name}
                          {(it.stockTaken || 0) > 0 && <span className="text-[10px] ml-1.5" style={{ color: "var(--faint)" }} title="This many came out of Supply when the order was saved">· out of Supply</span>}
                        </span>
                        <span className="font-mono text-xs shrink-0" style={{ color: "var(--muted)" }}>{peso(itemSubtotal(it))}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <ProgressBar percent={deliveryPercent(orderProfile)} color={DELIVERY_GREEN} label="Delivery progress" />

                <OrderPaymentLog payments={orderProfile.payments} onAdd={(payment) => addOrderPayment(orderProfile.id, payment)} />
                <ProgressBar percent={paymentPercent(orderProfile)} color={ACCENT.cyan} label="Payment progress" />
                <div className="rounded-lg px-3 py-2.5 flex flex-col gap-1 text-xs" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                  <div className="flex justify-between" style={{ color: "var(--muted)" }}><span>Items</span><span className="font-mono">{peso(itemsSum)}</span></div>
                  {runsCost > 0 && (
                    <div className="flex justify-between" style={{ color: "var(--muted)" }}>
                      <span>Delivery{orderProfile.chargeDelivery ? "" : " (free: the shop pays it)"}</span>
                      <span className="font-mono" style={orderProfile.chargeDelivery ? {} : { textDecoration: "line-through" }}>{orderProfile.chargeDelivery ? "+ " : ""}{peso(runsCost)}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-semibold text-sm pt-1" style={{ borderTop: "1px solid var(--border)" }}><span>Order total</span><span className="font-mono">{peso(total)}</span></div>
                  <div className="flex justify-between font-semibold" style={{ color: unpaid > 0 ? ACCENT.amber : DELIVERY_GREEN }}><span>Unpaid balance</span><span className="font-mono">{peso(unpaid)}</span></div>
                </div>

                <button onClick={() => { const o = orderProfile; setOrderProfileId(null); openEdit(o); }}
                  style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
                  className="flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium">
                  <Pencil size={14} /> Edit order (quantities, items, delivery, dates)
                </button>
                <button onClick={() => (complete ? completeOrder(orderProfile) : setFinishFor(orderProfile))}
                  style={{ background: DELIVERY_GREEN }}
                  title={complete ? "Delivered and paid: move it to the order history" : "Mark the rest delivered, record the unpaid balance, and finish"}
                  className="flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold text-black/85">
                  <BadgeCheck size={16} /> {complete ? "Mark as Done" : "Complete delivery & payment…"}
                </button>
                <button onClick={() => deleteOrder(orderProfile)} className="text-[11px] flex items-center justify-center gap-1" style={{ color: ACCENT.danger }}>
                  <Trash2 size={12} /> Delete this order (mistake)
                </button>
              </div>
            );
          })()}
        </Modal>
      )}

      {finishFor && <FinishOrderModal order={finishFor} onClose={() => setFinishFor(null)} onDone={orderFinished} />}

      {/* ---- delivery orders read from a phone's receipt ---- */}
      {draftList && (
        <Modal title={`New delivery orders — ${draftList.person.name}`} onClose={() => setDraftList(null)} width={440}>
          <div className="flex flex-col gap-1.5">
            {draftList.list.map((x) => (
              <button key={x.id} onClick={() => { setDraftList(null); setDraftId(x.id); }} className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left"
                style={{ background: "var(--panelAlt)", border: `1px solid ${DELIVERY_GREEN}55` }}>
                <MessageCircle size={14} style={{ color: DELIVERY_GREEN }} />
                <span className="flex-1 min-w-0 truncate">{x.customer || "Customer not read"}
                  <span className="text-[10px] ml-1.5" style={{ color: "var(--muted)" }}>receipt {x.receiptLabel} · {x.rows.length} item(s){x.date ? ` · deliver ${x.date}` : ""}</span></span>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {draftId && (
        <DraftOrderModal draftId={draftId} onClose={() => setDraftId(null)}
          onDone={(o) => { setDraftId(null); if (o) setOrders((prev) => [...prev, o]); loadPeople(); }} />
      )}

      {/* ---- Order History ---- */}
      {historyOpen && (
        <Modal title="Order History" onClose={() => setHistoryOpen(false)} width={480}>
          <div className="flex flex-col gap-1.5 max-h-80 overflow-y-auto pr-1">
            {orderHistory.length === 0 && (
              <p className="text-[11px] text-center py-6" style={{ color: "var(--faint)" }}>No completed orders yet.</p>
            )}
            {orderHistory.map((h) => (
              <div key={h.id} style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }} className="flex items-center justify-between px-3 py-2.5 rounded-lg text-sm">
                <div className="flex flex-col">
                  <span className="font-semibold">{h.customer}</span>
                  <span className="text-[10px]" style={{ color: "var(--muted)" }}>Completed {h.completeDate}</span>
                </div>
                <span className="font-mono font-semibold" style={{ color: DELIVERY_GREEN }}>{peso(h.total)}</span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
