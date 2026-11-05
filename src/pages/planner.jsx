import React, { useEffect, useMemo, useState } from "react";
import {
  CalendarRange, ChevronLeft, ChevronRight, Truck, MapPin, UserRound, Users, CheckCircle2, Circle, AlertTriangle,
  UserPlus, Clock, PackageOpen, CalendarX2, Plus, PackagePlus, Trash2,
} from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { DatePicker } from "../components/kit";
import CrewPicker, { runModeOf } from "../components/crew";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// PLANNER: the deliveries of the orders (Sales -> Add Order) on a calendar.
//   left : the month; each day shows how many deliveries and whether every truck has its driver + helpers
//          (red = late / no driver, amber = a helper is missing, green = all set). Below: deliveries with no day
//          yet, late deliveries and the next deliveries.
//   right: the chosen day: every delivery (whom to deliver, where, OUR truck, the items), the person in charge
//          (driver) and the companions (helpers) - change them right there - plus who is still FREE that day and a quick
//          "add temporary help" when there are not enough people. Deliveries are by our own truck only (round 17); a run
//          saved before as "customer picks up" / "someone else" shows "Use our truck" / "Remove this delivery".
// Data: /api/shop/planner?start=&end=, /planner/day/{date}, PUT /runs/{id}, /crew, /employees/temporary
// ---------------------------------------------------------------------------

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayStr = () => ymd(new Date());
const nice = (s) => { if (!s) return ""; const d = new Date(`${s}T00:00:00`); return `${WEEK[(d.getDay() + 6) % 7]} ${MONTHS[d.getMonth()].slice(0, 3)} ${d.getDate()}`; };
const card = { border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW };
const muted = { color: "var(--muted)" };
const GREEN = "#22c55e";
const STATUS = {
  overdue: { label: "Late", color: ACCENT.danger },
  today: { label: "Today", color: ACCENT.cyan },
  upcoming: { label: "Coming", color: "#a78bfa" },
  done: { label: "Delivered", color: GREEN },
  unscheduled: { label: "No day yet", color: ACCENT.amber },
};

function Chip({ status }) {
  const s = STATUS[status] || STATUS.upcoming;
  return <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0" style={{ color: s.color, background: `${s.color}1f` }}>{s.label}</span>;
}

function dayTone(d) {
  if (!d || !d.runs) return null;
  if (d.overdue || d.noDriver) return ACCENT.danger;
  if (d.noHelper) return ACCENT.amber;
  return d.done === d.runs ? "var(--faint)" : GREEN;
}

function MonthGrid({ month, days, selected, onPick }) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;
  const n = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: n }, (_, i) => ymd(new Date(month.getFullYear(), month.getMonth(), i + 1)))];
  const t = todayStr();
  return (
    <div className="grid grid-cols-7 gap-1.5">
      {WEEK.map((w) => <span key={w} className="text-[10px] text-center font-semibold" style={muted}>{w}</span>)}
      {cells.map((d, i) => {
        if (!d) return <span key={`x${i}`} />;
        const info = days[d];
        const tone = dayTone(info);
        const sel = d === selected;
        return (
          <button key={d} onClick={() => onPick(d)} className="rounded-lg p-1.5 text-left min-h-[64px] flex flex-col gap-0.5"
            style={{ background: sel ? `${ACCENT.cyan}22` : info ? "var(--panelAlt)" : "transparent",
              border: `1px solid ${sel ? ACCENT.cyan : d === t ? `${ACCENT.cyan}66` : info ? "var(--border)" : "transparent"}` }}>
            <span className="text-[11px] font-semibold" style={{ color: d === t ? ACCENT.cyan : undefined }}>{Number(d.slice(8))}</span>
            {info && (
              <>
                <span className="text-[10px] flex items-center gap-1"><Truck size={11} style={{ color: tone }} /> {info.runs - info.done}{info.done ? <span style={muted}>+{info.done}✓</span> : null}</span>
                <span className="text-[9px] flex items-center gap-1" style={muted}><Users size={10} /> {info.crew}</span>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}

function TempHelperForm({ date, onAdded }) {
  const [f, setF] = useState({ name: "", rate: "" });
  const [busy, setBusy] = useState(false);
  async function add() {
    setBusy(true);
    try {
      await api.post("/api/shop/employees/temporary", { name: f.name.trim(), role: "Helper", perDayRate: Number(f.rate) || 0, ratePerHour: 0,
        presentToday: date === todayStr() });
      setF({ name: "", rate: f.rate });
      onAdded();
    } catch { /* toast */ } finally { setBusy(false); }
  }
  const inp = "text-sm rounded-lg px-2.5 py-1.5 outline-none min-w-0";
  const st = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
  return (
    <div className="flex gap-2 items-center">
      <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="name of the temporary helper" className={`${inp} flex-1`} style={st}
        onKeyDown={(e) => { if (e.key === "Enter" && f.name.trim()) add(); }} />
      <input type="number" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} placeholder="₱ / day" className={`${inp} w-24`} style={st} />
      <button onClick={add} disabled={busy || !f.name.trim()} className="text-xs px-3 py-1.5 rounded-lg font-semibold text-black/85 disabled:opacity-50 flex items-center gap-1"
        style={{ background: ACCENT.amber }}><UserPlus size={13} /> Add</button>
    </div>
  );
}

function RunCard({ run, refresh, onChanged, transports = [] }) {
  const [busy, setBusy] = useState(false);
  async function patch(body) {
    setBusy(true);
    try { await api.put(`/api/shop/runs/${run.id}`, body); onChanged(); } catch { /* toast */ } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`Remove this delivery of ${run.customer}? The order stays; plan its delivery again under NO DAY YET.`)) return;
    setBusy(true);
    try { await api.del(`/api/shop/runs/${run.id}`); onChanged(); } catch { /* toast */ } finally { setBusy(false); }
  }
  // deliveries are by our own truck only. A run saved before as "the customer picks it up" / "someone else delivers"
  // is shown as it is, with one click to make it our truck (or to remove it).
  const legacy = (run.mode || "truck") !== "truck";
  const m = runModeOf(run);
  const left = run.items.filter((i) => !i.delivered);
  const s = STATUS[run.status] || STATUS.upcoming;
  const inp = "text-xs rounded-lg px-2.5 py-1.5 outline-none min-w-0";
  const inpSt = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
  return (
    <div className="rounded-xl p-3.5 flex flex-col gap-2.5" style={{ background: "var(--panelAlt)", border: `1px solid ${s.color}55`, opacity: busy ? 0.7 : 1 }}>
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2"><span className="text-sm font-semibold truncate">{run.customer}</span><Chip status={run.status} /></div>
          <div className="text-[11px] flex items-center gap-1 mt-0.5" style={muted}>
            <MapPin size={11} /> {run.address || "no address written (edit the order in Sales)"}
          </div>
        </div>
        <button onClick={() => patch({ done: !run.done })} className="text-[11px] px-2.5 py-1 rounded-lg flex items-center gap-1 shrink-0"
          title={run.done ? "Not delivered after all" : "Delivered: when the last trip of the order is delivered, all its items are marked delivered"}
          style={run.done ? { background: GREEN, color: "#000" } : { border: `1px solid ${GREEN}`, color: GREEN }}>
          {run.done ? <CheckCircle2 size={13} /> : <Circle size={13} />} {run.done ? "Delivered" : "Mark delivered"}
        </button>
      </div>
      {legacy ? (
        <div className="rounded-lg px-2.5 py-2 flex flex-col gap-1.5 text-[11px]" style={{ border: `1px dashed ${m.color}88` }}>
          <span className="flex items-center gap-1.5"><m.Icon size={13} style={{ color: m.color }} />
            Saved before as <b>{run.mode === "pickup" ? "the customer picks it up" : `${run.carrier || "someone else"} delivers it`}</b>.
            Deliveries are by our own truck now.</span>
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={() => patch({ mode: "truck" })} disabled={!transports.length}
              className="px-2.5 py-1 rounded-md font-semibold text-black/85 flex items-center gap-1 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
              <Truck size={12} /> Use our truck</button>
            <button onClick={remove} className="px-2.5 py-1 rounded-md" style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}>
              Remove this delivery</button>
            {!transports.length && <span style={muted}>Add a truck first (Sales → Add Order → Add delivery).</span>}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <Truck size={14} style={{ color: m.color }} />
          {transports.length ? (
            <select value={run.transportId || ""} onChange={(e) => patch({ transportId: Number(e.target.value) })} className={inp} style={inpSt}>
              {!run.transportId && <option value="">pick a truck…</option>}
              {transports.map((t) => <option key={t.id} value={t.id}>{t.truckName}{t.plate ? ` · ${t.plate}` : ""}{t.capacity ? ` · ${t.capacity}` : ""}</option>)}
            </select>
          ) : <span>{run.truck.name}{run.truck.plate ? ` · ${run.truck.plate}` : ""}{run.truck.capacity ? ` · ${run.truck.capacity}` : ""}</span>}
          <span style={muted}>· {run.trips} trip{run.trips === 1 ? "" : "s"}</span>
          <span className="ml-auto flex items-center gap-1.5 text-[11px]" style={muted}>Day
            <span className="w-36"><DatePicker value={run.date} onChange={(v) => patch({ date: v || null })} placeholder="no day" /></span>
          </span>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {(left.length ? left : run.items).map((i) => (
          <span key={i.id} className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "var(--panel)", border: "1px solid var(--border)", textDecoration: i.delivered ? "line-through" : "none" }}>
            {Number(i.qty).toLocaleString()} {i.unit} {i.name}
          </span>
        ))}
        {left.length > 0 && left.length < run.items.length && <span className="text-[10px] self-center" style={muted}>+{run.items.length - left.length} already delivered</span>}
      </div>
      {!legacy && (
        <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
          <CrewPicker date={run.date} runId={run.id} driverId={run.driverId} helperIds={run.helperIds} refresh={refresh}
            onChange={(c) => patch({ driverId: c.driverId, helperIds: c.helperIds })} />
        </div>
      )}
    </div>
  );
}

// An order with no delivery planned yet: plan it here with our truck — pick the truck and the day.
function PlanOrderRow({ order, transports = [], onPlanned }) {
  const [truck, setTruck] = useState(transports.length ? transports[0].id : "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!truck && transports.length) setTruck(transports[0].id); }, [transports, truck]);
  async function plan(date) {
    setBusy(true);
    try {
      await api.post(`/api/shop/orders/${order.orderId}/runs`, { mode: "truck", transportId: Number(truck) || null, date: date || "" });
      onPlanned(date);
    } catch { /* toast */ } finally { setBusy(false); }
  }
  const inpSt = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
  return (
    <div className="flex flex-col gap-1.5 text-xs px-2.5 py-2 rounded-lg" style={{ background: "var(--panelAlt)", opacity: busy ? 0.6 : 1 }}>
      <div className="flex items-center gap-2">
        <PackageOpen size={13} style={muted} />
        <span className="flex-1 min-w-0 truncate">{order.customer}<span style={muted}> · no delivery planned yet</span></span>
      </div>
      {transports.length ? (
        <div className="flex items-center gap-2 flex-wrap pl-5">
          <Truck size={13} style={{ color: ACCENT.cyan }} />
          <select value={truck} onChange={(e) => setTruck(e.target.value)} className="text-xs rounded-lg px-2 py-1.5 outline-none min-w-0" style={inpSt}>
            {transports.map((t) => <option key={t.id} value={t.id}>{t.truckName}{t.plate ? ` · ${t.plate}` : ""}</option>)}
          </select>
          <span className="w-36 ml-auto"><DatePicker value="" onChange={(v) => { if (v) plan(v); }} placeholder="Pick a day" /></span>
        </div>
      ) : (
        <span className="pl-5 text-[11px]" style={muted}>Add a truck first (Sales → Add Order → Add delivery), then pick the day here.</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TRUCK TRIPS OF THEIR OWN (round 18): the truck goes somewhere without an order — to deliver something, to get
// materials, or for personal use. Where, when, which truck, the driver and helpers (they count as busy that day).
// Data: POST /api/shop/trips, PUT / DELETE /api/shop/runs/{id}
// ---------------------------------------------------------------------------
const PURPOSES = [
  { key: "deliver", label: "Deliver", Icon: Truck, color: ACCENT.cyan },
  { key: "get", label: "Get materials", Icon: PackagePlus, color: ACCENT.amber },
  { key: "personal", label: "Personal use", Icon: UserRound, color: "#a78bfa" },
];
const purposeOf = (k) => PURPOSES.find((p) => p.key === k) || PURPOSES[0];
const inpSt = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
const inp = "text-xs rounded-lg px-2.5 py-1.5 outline-none min-w-0";

function PurposeSwitch({ value, onChange }) {
  return (
    <div className="inline-flex rounded-lg p-0.5 gap-0.5 flex-wrap" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
      {PURPOSES.map(({ key, label, Icon, color }) => (
        <button key={key} type="button" onClick={() => onChange(key)}
          className="flex items-center gap-1 rounded-md font-semibold text-[10px] px-2 py-1"
          style={value === key ? { background: color, color: "#000" } : { color: "var(--muted)" }}>
          <Icon size={11} /> {label}
        </button>
      ))}
    </div>
  );
}

function TripForm({ date, transports, onAdded, onCancel }) {
  const [f, setF] = useState({ purpose: "get", place: "", note: "", time: "", transportId: transports.length ? transports[0].id : "" });
  const [busy, setBusy] = useState(false);
  async function add() {
    setBusy(true);
    try {
      await api.post("/api/shop/trips", { ...f, date, transportId: Number(f.transportId) || null, place: f.place.trim(), note: f.note.trim() });
      onAdded();
    } catch { /* toast */ } finally { setBusy(false); }
  }
  return (
    <div className="rounded-xl p-3.5 flex flex-col gap-2.5" style={{ background: "var(--panelAlt)", border: `1px dashed ${ACCENT.cyan}88` }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold">New truck trip · {nice(date)}</span>
        <span className="ml-auto"><PurposeSwitch value={f.purpose} onChange={(k) => setF({ ...f, purpose: k })} /></span>
      </div>
      <div className="flex items-center gap-2"><MapPin size={13} style={muted} className="shrink-0" />
        <input value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })} autoFocus className={`${inp} flex-1`} style={inpSt}
          placeholder={f.purpose === "get" ? "where to get it (e.g. the supplier in Puerto Princesa)" : f.purpose === "deliver" ? "where to deliver" : "where it goes"} /></div>
      <input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} className={inp} style={inpSt}
        placeholder={f.purpose === "get" ? "what to get (e.g. 100 bags cement)" : f.purpose === "deliver" ? "what to bring" : "why (optional)"} />
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Truck size={13} style={muted} />
        {transports.length ? (
          <select value={f.transportId} onChange={(e) => setF({ ...f, transportId: e.target.value })} className={inp} style={inpSt}>
            {transports.map((t) => <option key={t.id} value={t.id}>{t.truckName}{t.plate ? ` · ${t.plate}` : ""}</option>)}
          </select>
        ) : <span style={{ color: ACCENT.amber }}>Add a truck first (Sales → Add Order → Add delivery).</span>}
        <span className="flex items-center gap-1" style={muted}><Clock size={12} /> leaves at
          <input type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} className={inp} style={inpSt} /></span>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={add} disabled={busy || !transports.length} style={{ background: ACCENT.cyan }}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold text-black/85 disabled:opacity-50">{busy ? "Adding…" : "Add the trip"}</button>
        <button onClick={onCancel} className="text-xs px-2" style={muted}>Cancel</button>
        <span className="text-[10px] ml-auto" style={{ color: "var(--faint)" }}>Then pick the driver and helpers on it.</span>
      </div>
    </div>
  );
}

function TripCard({ run, refresh, onChanged, transports = [] }) {
  const [busy, setBusy] = useState(false);
  const [place, setPlace] = useState(run.place || "");
  const [note, setNote] = useState(run.note || "");
  useEffect(() => { setPlace(run.place || ""); setNote(run.note || ""); }, [run.place, run.note]);
  async function patch(body) {
    setBusy(true);
    try { await api.put(`/api/shop/runs/${run.id}`, body); onChanged(); } catch { /* toast */ } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`Remove this truck trip (${run.label})?`)) return;
    setBusy(true);
    try { await api.del(`/api/shop/runs/${run.id}`); onChanged(); } catch { /* toast */ } finally { setBusy(false); }
  }
  const pu = purposeOf(run.purpose);
  return (
    <div className="rounded-xl p-3.5 flex flex-col gap-2.5" style={{ background: "var(--panelAlt)", border: `1px solid ${pu.color}66`, opacity: busy ? 0.7 : 1 }}>
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0 flex flex-col gap-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1" style={{ color: pu.color }}><pu.Icon size={12} /> TRUCK TRIP</span>
            <Chip status={run.status} />
            {run.time && <span className="text-[11px] flex items-center gap-1" style={muted}><Clock size={11} /> {run.time}</span>}
          </div>
          <PurposeSwitch value={run.purpose} onChange={(k) => { if (k !== run.purpose) patch({ purpose: k }); }} />
        </div>
        <button onClick={() => patch({ done: !run.done })} className="text-[11px] px-2.5 py-1 rounded-lg flex items-center gap-1 shrink-0"
          style={run.done ? { background: GREEN, color: "#000" } : { border: `1px solid ${GREEN}`, color: GREEN }}>
          {run.done ? <CheckCircle2 size={13} /> : <Circle size={13} />} {run.done ? "Done" : "Mark done"}
        </button>
      </div>
      <div className="flex items-center gap-2"><MapPin size={13} style={muted} className="shrink-0" />
        <input value={place} onChange={(e) => setPlace(e.target.value)} placeholder="where it goes" className={`${inp} flex-1`} style={inpSt}
          onBlur={() => { if (place.trim() !== (run.place || "")) patch({ place }); }} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} /></div>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what to bring / get, or why" className={inp} style={inpSt}
        onBlur={() => { if (note.trim() !== (run.note || "")) patch({ note }); }} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      <div className="flex items-center gap-2 text-xs flex-wrap">
        <Truck size={14} style={muted} />
        {transports.length ? (
          <select value={run.transportId || ""} onChange={(e) => patch({ transportId: Number(e.target.value) })} className={inp} style={inpSt}>
            {!run.transportId && <option value="">pick a truck…</option>}
            {transports.map((t) => <option key={t.id} value={t.id}>{t.truckName}{t.plate ? ` · ${t.plate}` : ""}</option>)}
          </select>
        ) : <span>{run.truck.name}</span>}
        <span className="flex items-center gap-1" style={muted}><Clock size={12} />
          <input type="time" value={run.time || ""} onChange={(e) => patch({ time: e.target.value })} className={inp} style={inpSt} /></span>
        <span className="ml-auto flex items-center gap-1.5 text-[11px]" style={muted}>Day
          <span className="w-36"><DatePicker value={run.date} onChange={(v) => { if (v) patch({ date: v }); }} placeholder="day" /></span>
        </span>
      </div>
      <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
        <CrewPicker date={run.date} runId={run.id} driverId={run.driverId} helperIds={run.helperIds} refresh={refresh}
          onChange={(c) => patch({ driverId: c.driverId, helperIds: c.helperIds })} />
      </div>
      <button onClick={remove} className="self-start text-[11px] flex items-center gap-1" style={{ color: ACCENT.danger }}><Trash2 size={12} /> Remove this trip</button>
    </div>
  );
}

function DayPanel({ date, version, onChanged }) {
  const [d, setD] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [tripOpen, setTripOpen] = useState(false);
  useEffect(() => { setTripOpen(false); }, [date]);
  const load = () => api.get(`/api/shop/planner/day/${date}`).then((x) => { setD(x); setRefresh((n) => n + 1); }).catch(() => {});
  useEffect(() => { setD(null); load(); /* eslint-disable-next-line */ }, [date]);
  useEffect(() => { if (version) load(); /* eslint-disable-next-line */ }, [version]);
  const changed = () => onChanged();                    // the page reloads the month, then this day
  if (!d) return <p className="text-xs" style={muted}>Loading…</p>;
  const st = d.stats;
  const short = st.open > 0 && (st.noHelper > st.helpersFree || st.noDriver > st.driversFree);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h3 className="text-base font-semibold">{nice(d.date)}</h3>
        <span className="text-xs" style={muted}>{st.open} to deliver{st.runs - st.open ? ` · ${st.runs - st.open} delivered` : ""}{st.trucks ? ` · ${st.trucks} truck(s)` : ""}
          {st.pickups ? ` · ${st.pickups} pick-up${st.pickups === 1 ? "" : "s"} by the customer` : ""}{st.others ? ` · ${st.others} by someone else` : ""}</span>
      </div>
      <div className={`grid gap-2 ${st.staff ? "grid-cols-3" : "grid-cols-2"}`}>
        {[["Drivers free", st.driversFree, st.drivers, UserRound], ["Helpers free", st.helpersFree, st.helpers, Users],
          ...(st.staff ? [["Other staff free", st.staffFree, st.staff, Users]] : [])].map(([l, free, all, Icon]) => (
          <div key={l} className="rounded-lg px-3 py-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            <div className="text-[10px] flex items-center gap-1" style={muted}><Icon size={11} /> {l}</div>
            <div className="font-mono text-sm font-semibold" style={{ color: free ? GREEN : all ? ACCENT.amber : ACCENT.danger }}>{free} <span style={muted}>of {all}</span></div>
          </div>
        ))}
      </div>
      {(st.noDriver > 0 || st.noHelper > 0) && (
        <div className="text-[11px] rounded-lg px-3 py-2 flex items-start gap-2" style={{ background: `${ACCENT.amber}14`, border: `1px solid ${ACCENT.amber}55` }}>
          <AlertTriangle size={13} className="shrink-0 mt-0.5" style={{ color: ACCENT.amber }} />
          <span>{st.noDriver ? `${st.noDriver} delivery(ies) without a driver. ` : ""}{st.noHelper ? `${st.noHelper} without a helper. ` : ""}
            {short ? "There are not enough free people for this day: add temporary help below." : "Pick them below."}</span>
        </div>
      )}
      {st.doubleBooked.length > 0 && (
        <div className="text-[11px] flex items-center gap-1.5" style={muted}><Clock size={12} /> On 2 or more deliveries this day: {st.doubleBooked.join(", ")}</div>
      )}
      {d.freeHelpers.length + d.freeDrivers.length > 0 && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-[10px] font-semibold tracking-wide mr-1" style={muted}>STILL FREE</span>
          {[...d.freeDrivers.map((p) => ({ ...p, role: "driver" })), ...d.freeHelpers.map((p) => ({ ...p, role: p.temporary ? "temporary" : "helper" }))].map((p) => (
            <span key={p.id} className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: `${GREEN}14`, border: `1px solid ${GREEN}55` }}>
              {p.name} <span style={muted}>· {p.role}</span>
            </span>
          ))}
        </div>
      )}
      <div className="rounded-lg px-3 py-2.5 flex flex-col gap-1.5" style={{ border: `1px dashed ${ACCENT.amber}88` }}>
        <span className="text-[11px] font-semibold flex items-center gap-1.5" style={{ color: ACCENT.amber }}><UserPlus size={13} /> Need more people this day? Add temporary help</span>
        <TempHelperForm date={d.date} onAdded={changed} />
        <span className="text-[10px]" style={{ color: "var(--faint)" }}>They join the helper lists right away (and the Dashboard attendance, Cash Flow and payslip).</span>
      </div>
      <div className="flex flex-col gap-2.5">
        {tripOpen ? (
          <TripForm date={d.date} transports={d.transports || []} onCancel={() => setTripOpen(false)}
            onAdded={() => { setTripOpen(false); changed(); }} />
        ) : (
          <button onClick={() => setTripOpen(true)} className="self-start flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg"
            style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
            title="The truck goes somewhere without an order: deliver something, get materials, or personal use">
            <Plus size={13} /> Truck trip (deliver · get materials · personal)</button>
        )}
        {d.runs.map((r) => (r.isTrip
          ? <TripCard key={r.id} run={r} refresh={refresh} onChanged={changed} transports={d.transports || []} />
          : <RunCard key={r.id} run={r} refresh={refresh} onChanged={changed} transports={d.transports || []} />))}
        {!d.runs.length && !tripOpen && <p className="text-xs py-4 text-center" style={{ color: "var(--faint)" }}>No delivery or truck trip on this day.</p>}
      </div>
    </div>
  );
}

function ListBlock({ icon: Icon, title, color, children, empty }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1" style={{ color }}><Icon size={12} /> {title}</span>
      {children}
      {empty && <span className="text-[11px] pl-4" style={{ color: "var(--faint)" }}>{empty}</span>}
    </div>
  );
}

export default function PlannerPage() {
  const [month, setMonth] = useState(() => { const t = new Date(); return new Date(t.getFullYear(), t.getMonth(), 1); });
  const [selected, setSelected] = useState(todayStr());
  const [plan, setPlan] = useState(null);
  const [version, setVersion] = useState(0);
  const start = ymd(month);
  const end = ymd(new Date(month.getFullYear(), month.getMonth() + 1, 0));
  const load = () => api.get(`/api/shop/planner?start=${start}&end=${end}`).then(setPlan).catch(() => {});
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [start, end, version]);
  const days = useMemo(() => Object.fromEntries((plan ? plan.days : []).map((d) => [d.date, d])), [plan]);
  const pick = (d) => {
    setSelected(d);
    const x = new Date(`${d}T00:00:00`);
    if (x.getMonth() !== month.getMonth() || x.getFullYear() !== month.getFullYear()) setMonth(new Date(x.getFullYear(), x.getMonth(), 1));
  };
  async function setDay(runId, d) {
    try { await api.put(`/api/shop/runs/${runId}`, { date: d || null }); setVersion((v) => v + 1); if (d) pick(d); } catch { /* toast */ }
  }
  const row = "flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-lg";

  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2"><CalendarRange size={18} style={{ color: ACCENT.cyan }} /> Planner</h2>
          <p className="text-xs" style={muted}>The truck, day by day: the deliveries of the orders and its other trips (deliver, get materials, personal use) — who drives, who goes with them, and who is still free.</p>
        </div>
      </div>

      <div className="grid gap-5 items-start" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.1fr)" }}>
        <div className="flex flex-col gap-4 min-w-0">
          <div className="rounded-xl p-4 flex flex-col gap-3" style={card}>
            <div className="flex items-center justify-between">
              <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }}><ChevronLeft size={15} /></button>
              <span className="text-sm font-semibold">{MONTHS[month.getMonth()]} {month.getFullYear()}</span>
              <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} className="p-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }}><ChevronRight size={15} /></button>
            </div>
            <MonthGrid month={month} days={days} selected={selected} onPick={pick} />
            <div className="flex flex-wrap gap-3 text-[10px]" style={muted}>
              <span className="flex items-center gap-1"><Truck size={11} style={{ color: GREEN }} /> all set</span>
              <span className="flex items-center gap-1"><Truck size={11} style={{ color: ACCENT.amber }} /> a helper is missing</span>
              <span className="flex items-center gap-1"><Truck size={11} style={{ color: ACCENT.danger }} /> late or no driver</span>
              <span className="flex items-center gap-1"><Users size={11} /> people going</span>
            </div>
          </div>

          {plan && (
            <div className="rounded-xl p-4 flex flex-col gap-4" style={card}>
              <ListBlock icon={CalendarX2} title="NO DAY YET" color={ACCENT.amber}
                empty={!plan.unscheduled.length && !plan.ordersWithoutRun.length ? "Every delivery has a day." : null}>
                {plan.unscheduled.map((r) => (
                  <div key={r.id} className={row} style={{ background: "var(--panelAlt)" }}>
                    <span className="flex-1 min-w-0 truncate font-medium">{r.customer}<span className="font-normal" style={muted}> · {r.truck.name}</span></span>
                    <span className="w-36"><DatePicker value="" onChange={(v) => setDay(r.id, v)} placeholder="Pick a day" /></span>
                  </div>
                ))}
                {plan.ordersWithoutRun.map((o) => (
                  <PlanOrderRow key={o.orderId} order={o} transports={plan.transports || []}
                    onPlanned={(d) => { setVersion((v) => v + 1); if (d) pick(d); }} />
                ))}
              </ListBlock>
              <ListBlock icon={AlertTriangle} title="LATE (NOT DELIVERED)" color={ACCENT.danger} empty={!plan.overdue.length ? "None." : null}>
                {plan.overdue.map((r) => (
                  <button key={r.id} onClick={() => pick(r.date)} className={`${row} text-left`} style={{ background: "var(--panelAlt)" }}>
                    <span className="font-mono w-20 shrink-0" style={{ color: ACCENT.danger }}>{nice(r.date)}</span>
                    <span className="flex-1 min-w-0 truncate">{r.customer}</span>
                    <span className="text-[10px]" style={muted}>{r.mode === "truck" ? r.driverName || "no driver" : r.truck.name}</span>
                  </button>
                ))}
              </ListBlock>
              <ListBlock icon={Truck} title="NEXT DELIVERIES" color={ACCENT.cyan} empty={!plan.upcoming.length ? "No delivery planned." : null}>
                {plan.upcoming.slice(0, 12).map((r) => (
                  <button key={r.id} onClick={() => pick(r.date)} className={`${row} text-left`} style={{ background: "var(--panelAlt)" }}>
                    <span className="font-mono w-20 shrink-0" style={muted}>{nice(r.date)}</span>
                    <span className="flex-1 min-w-0 truncate">{r.customer}</span>
                    {r.isTrip && <span className="text-[9px] font-semibold px-1.5 rounded-full shrink-0" style={{ color: purposeOf(r.purpose).color, border: `1px solid ${purposeOf(r.purpose).color}66` }}>TRIP</span>}
                    {r.mode === "truck" ? (
                      <span className="text-[10px] truncate max-w-[45%]" style={{ color: r.driverName ? "var(--muted)" : ACCENT.amber }}>
                        {r.driverName || "no driver"}{r.helpers.length ? ` + ${r.helpers.map((h) => h.name).join(", ")}` : " · no helper"}
                      </span>
                    ) : (
                      <span className="text-[10px] truncate max-w-[45%]" style={{ color: runModeOf(r).color }}>{r.truck.name}</span>
                    )}
                  </button>
                ))}
              </ListBlock>
            </div>
          )}
        </div>

        <div className="rounded-xl p-5 min-w-0" style={card}>
          <DayPanel date={selected} version={version} onChanged={() => setVersion((v) => v + 1)} />
        </div>
      </div>
    </div>
  );
}
