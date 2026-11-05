import React, { useEffect, useState } from "react";
import { X, UserRound, Users, AlertTriangle, Truck, Car, Handshake } from "lucide-react";
import { ACCENT } from "../lib/theme";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// WHO DELIVERS one run of an order (Sales -> Add Order -> Add delivery, Planner):
//   truck  = our truck with our driver and helpers (the truck's cost per trip)
//   pickup = the customer comes for it with his own vehicle (no truck, no crew, no delivery cost)
//   other  = someone else delivers it (a hired truck): who + what they charge per trip
// ---------------------------------------------------------------------------
export const RUN_MODES = [
  { key: "truck", label: "Our truck", Icon: Truck, color: ACCENT.cyan, hint: "Our truck, driver and helpers deliver it" },
  { key: "pickup", label: "Customer picks up", Icon: Car, color: "#a78bfa",
    hint: "The customer comes for it with his own vehicle: no truck, no crew, no delivery cost" },
  { key: "other", label: "Someone else", Icon: Handshake, color: ACCENT.amber,
    hint: "Another person or a hired truck delivers it: type who and what they charge per trip" },
];
export const runModeOf = (r) => RUN_MODES.find((m) => m.key === ((r && r.mode) || "truck")) || RUN_MODES[0];

export function ModeSwitch({ value, onChange, small = false }) {
  return (
    <div className="inline-flex rounded-lg p-0.5 gap-0.5 flex-wrap" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
      {RUN_MODES.map(({ key, label, Icon, color, hint }) => {
        const on = (value || "truck") === key;
        return (
          <button key={key} type="button" onClick={() => onChange(key)} title={hint}
            className={`flex items-center gap-1 rounded-md font-semibold ${small ? "text-[10px] px-2 py-1" : "text-[11px] px-2.5 py-1.5"}`}
            style={on ? { background: color, color: "#000" } : { color: "var(--muted)" }}>
            <Icon size={small ? 11 : 12} /> {label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Driver + helpers of ONE truck run (Sales -> Add Order, Planner).
//   Driver  = employees whose job says "driver" (the person in charge of the delivery)
//   Helpers = labor (labor / helper / pahinante...) + the TEMPORARY workers of that day only
//   Other staff (round 18) = everyone else (sales people, office...): they can be sent as driver or helper too
// "also on Kumon" = that person is already on another delivery that day.  Data: GET /api/shop/crew?date=&run=
// ---------------------------------------------------------------------------

const SEL = "text-sm rounded-lg px-2 py-1.5 outline-none min-w-0";
const SEL_ST = { background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" };
const muted = { color: "var(--muted)" };

const busyTxt = (p) => (p.busy && p.busy.length ? ` · also on ${[...new Set(p.busy.map((b) => b.customer))].join(", ")}` : "");
const dayTxt = (p, pool) => {
  if (p.temporary) return " · temporary";
  if (pool && (pool.isPast || pool.isToday) && !p.present) return pool.isPast ? " · absent" : " · not checked in yet";
  return "";
};

export function useCrewPool(date, runId, refresh = 0) {
  const [pool, setPool] = useState(null);
  useEffect(() => {
    let alive = true;
    const q = new URLSearchParams();
    if (date) q.set("date", date);
    if (runId) q.set("run", runId);
    api.getQuiet(`/api/shop/crew?${q.toString()}`).then((p) => alive && setPool(p)).catch(() => {});
    return () => { alive = false; };
  }, [date, runId, refresh]);
  return pool;
}

// refresh: change it to load the lists again (e.g. after another run of the same day changed its crew)
export default function CrewPicker({ date, runId, driverId, helperIds = [], onChange, pool: given, refresh = 0 }) {
  const own = useCrewPool(given ? null : date, given ? null : runId, refresh);
  const pool = given || own;
  if (!pool) return <p className="text-[11px]" style={muted}>Loading the crew…</p>;
  const others = pool.others || [];
  const everyone = [...pool.drivers, ...pool.helpers, ...others];
  const nameOf = (id) => (everyone.find((p) => p.id === id) || {}).name || `#${id}`;
  const driver = everyone.find((p) => p.id === driverId);
  const left = pool.helpers.filter((p) => !helperIds.includes(p.id) && p.id !== driverId);
  const leftOthers = [...others, ...pool.drivers].filter((p) => !helperIds.includes(p.id) && p.id !== driverId);
  const otherDrivers = [...pool.helpers, ...others];
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid gap-2 items-center" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}>
        <span className="text-[11px] flex items-center gap-1" style={muted}><UserRound size={12} /> Driver</span>
        <select value={driverId ?? ""} onChange={(e) => onChange({ driverId: e.target.value ? Number(e.target.value) : null, helperIds: helperIds.filter((h) => h !== Number(e.target.value)) })}
          className={SEL} style={SEL_ST}>
          <option value="">— no driver yet —</option>
          {driverId && !driver && <option value={driverId}>{nameOf(driverId)}</option>}
          {pool.drivers.length > 0 && (
            <optgroup label="Drivers">
              {pool.drivers.map((p) => <option key={p.id} value={p.id}>{p.name}{dayTxt(p, pool)}{busyTxt(p)}</option>)}
            </optgroup>
          )}
          {otherDrivers.length > 0 && (
            <optgroup label="Other staff">
              {otherDrivers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.position || "staff"}){dayTxt(p, pool)}{busyTxt(p)}</option>)}
            </optgroup>
          )}
        </select>
        <span className="text-[11px] flex items-center gap-1 self-start pt-1.5" style={muted}><Users size={12} /> Helpers</span>
        <div className="flex flex-wrap gap-1 items-center">
          {helperIds.map((id) => {
            const p = everyone.find((x) => x.id === id);
            const busy = p && p.busy && p.busy.length;
            return (
              <span key={id} className="text-[11px] pl-2 pr-1 py-0.5 rounded-full flex items-center gap-1"
                style={{ background: `${busy ? ACCENT.amber : ACCENT.cyan}1c`, border: `1px solid ${busy ? ACCENT.amber : ACCENT.cyan}66`, color: "var(--text)" }}
                title={p ? `${p.position || "helper"}${dayTxt(p, pool)}${busyTxt(p)}` : ""}>
                {nameOf(id)}{p && p.temporary && <span className="text-[9px] font-semibold" style={{ color: ACCENT.amber }}>TEMP</span>}
                <button type="button" onClick={() => onChange({ driverId, helperIds: helperIds.filter((h) => h !== id) })} style={muted}><X size={11} /></button>
              </span>
            );
          })}
          <select value="" onChange={(e) => e.target.value && onChange({ driverId, helperIds: [...helperIds, Number(e.target.value)] })}
            className={`${SEL} text-xs`} style={{ ...SEL_ST, color: ACCENT.cyan }}>
            <option value="">+ Add helper…</option>
            {left.length > 0 && (
              <optgroup label="Labor / helpers">
                {left.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.position || "helper"}){dayTxt(p, pool)}{busyTxt(p)}</option>)}
              </optgroup>
            )}
            {leftOthers.length > 0 && (
              <optgroup label="Other staff">
                {leftOthers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.position || "staff"}){dayTxt(p, pool)}{busyTxt(p)}</option>)}
              </optgroup>
            )}
          </select>
        </div>
      </div>
      {(!pool.drivers.length || !pool.helpers.length) && (
        <span className="text-[10px] flex items-center gap-1" style={{ color: ACCENT.amber }}>
          <AlertTriangle size={11} />
          {!pool.drivers.length ? "No employee has the job “Driver”. " : ""}{!pool.helpers.length ? "No labor or temporary helper for this day. " : ""}
          {others.length ? "You can pick anyone under “Other staff”, " : ""}set the job in Cash Flow, or add temporary help.
        </span>
      )}
      {!date && <span className="text-[10px]" style={{ color: "var(--faint)" }}>Pick the delivery day to see who is free that day.</span>}
    </div>
  );
}
