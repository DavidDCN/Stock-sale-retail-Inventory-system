import React, { useEffect, useState } from "react";
import { UserMinus, Trash2, Truck, Smartphone, AlertTriangle, CheckCircle2, RotateCcw, Wallet } from "lucide-react";
import { ACCENT } from "../lib/theme";
import { api } from "../lib/api";
import { Modal } from "./kit";

// ---------------------------------------------------------------------------
// Before a person is removed (Dashboard -> Temporary -> Done, Cash Flow -> an employee -> Remove), this popup shows
// exactly what the shop still owes them: every day worked x day rate, overtime, undertime, bonuses, cash advances,
// what was already paid, and the rest. "Pay & remove" records that last pay (payroll "Final pay") and removes them;
// "Remove without paying" keeps what is owed visible (Payslip / Show hidden). Removed = hidden, not deleted: their
// days stay in the books and "Bring back" undoes it. "Delete every record" is only for a person added by mistake.
// Data: /api/shop/employees/{id}/settlement, /remove, /restore   (delete forever: DELETE /api/shop/entries/emp-{id})
// ---------------------------------------------------------------------------
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const niceDay = (s) => { if (!s) return ""; const [, m, d] = s.split("-"); return `${MONTHS[Number(m) - 1]} ${Number(d)}`; };
const peso = (n) => `${n < 0 ? "-" : ""}₱${Math.abs(Math.round((Number(n) || 0) * 100) / 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const hrs = (h) => `${Number(h || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}h`;
const muted = { color: "var(--muted)" };
const inputSt = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
const GREEN = "#22c55e";

export default function SettleModal({ employeeId, name, onClose, onDone, canDelete = false }) {
  const [s, setS] = useState(null);
  const [pay, setPay] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get(`/api/shop/employees/${employeeId}/settlement`)
      .then((x) => { setS(x); setPay(x.owed > 0 ? String(x.owed) : ""); })
      .catch((e) => setErr(e.message));
  }, [employeeId]);
  const first = (name || "").split(" ")[0] || "them";
  const amount = Math.max(0, Number(pay) || 0);

  async function run(fn) {
    setBusy(true); setErr("");
    try { onDone(await fn()); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const remove = (p) => run(() => api.post(`/api/shop/employees/${employeeId}/remove`, { pay: p, note }));
  const bringBack = () => run(() => api.post(`/api/shop/employees/${employeeId}/restore`).then((x) => ({ restored: true, entry: x })));
  function deleteForever() {
    if (!window.confirm(`Delete EVERY record of ${name}: days, overtime, cash advances and payments? Only for a person added by mistake. This cannot be undone.`)) return;
    run(() => api.del(`/api/shop/entries/emp-${employeeId}`).then(() => ({ deleted: true })));
  }

  const line = (l, v, c) => (
    <div className="flex justify-between gap-3 text-xs"><span style={muted}>{l}</span><span className="font-mono shrink-0" style={{ color: c }}>{v}</span></div>
  );
  const removed = s && !s.active;
  return (
    <Modal title={removed ? `${name} · removed${s.removed ? ` ${niceDay(s.removed)}` : ""}` : `Remove ${name}?`} onClose={onClose} width={500}>
      {!s ? <p className="text-xs" style={err ? { color: ACCENT.danger } : muted}>{err || "Adding up the records…"}</p> : (
        <div className="flex flex-col gap-3">
          <div className="rounded-xl px-4 py-3 flex items-center gap-3"
            style={{ background: `${s.owed > 0 ? ACCENT.amber : s.theyOwe > 0 ? ACCENT.danger : GREEN}14`,
              border: `1px solid ${s.owed > 0 ? ACCENT.amber : s.theyOwe > 0 ? ACCENT.danger : GREEN}66` }}>
            {s.owed > 0 ? <Wallet size={22} style={{ color: ACCENT.amber }} /> : s.theyOwe > 0
              ? <AlertTriangle size={22} style={{ color: ACCENT.danger }} /> : <CheckCircle2 size={22} style={{ color: GREEN }} />}
            <div className="flex-1 min-w-0">
              <div className="text-[11px]" style={muted}>
                {s.owed > 0 ? `What the shop still owes ${first}` : s.theyOwe > 0 ? `${first} owes the shop (cash advances more than the pay)` : "Nothing is owed"}
              </div>
              <div className="font-mono text-2xl font-semibold" style={{ color: s.owed > 0 ? ACCENT.amber : s.theyOwe > 0 ? ACCENT.danger : GREEN }}>
                {peso(s.owed > 0 ? s.owed : s.theyOwe)}</div>
            </div>
            <div className="text-right text-[10px]" style={muted}>
              {s.from ? <>worked {niceDay(s.from)}{s.to !== s.from ? ` – ${niceDay(s.to)}` : ""}</> : "no days recorded"}
              {s.lastPay && <div>last pay {niceDay(s.lastPay.date)} · {peso(s.lastPay.amount)}</div>}
            </div>
          </div>

          <div className="rounded-lg px-3 py-2 flex flex-col gap-0.5" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            {line(`${s.present} day(s) × ${peso(s.perDayRate)}`, peso(s.gross))}
            {s.otHours > 0 && line(`Overtime ${hrs(s.otHours)} × ${peso(s.ratePerHour)} × 1.25`, `+${peso(s.otPay)}`, ACCENT.cyan)}
            {s.utHours > 0 && line(`Undertime ${hrs(s.utHours)} × ${peso(s.ratePerHour)}`, `−${peso(s.utLess)}`, ACCENT.amber)}
            {s.bonusTotal > 0 && line("Bonuses", `+${peso(s.bonusTotal)}`, ACCENT.cyan)}
            {s.advanceTotal > 0 && line("Cash advances", `−${peso(s.advanceTotal)}`, ACCENT.danger)}
            <div className="my-1" style={{ borderTop: "1px dashed var(--border)" }} />
            {line("Earned", peso(s.net))}
            {line("Already paid (payroll)", s.paidTotal ? `−${peso(s.paidTotal)}` : "₱0")}
            <div className="flex justify-between text-xs font-semibold mt-0.5">
              <span>{s.owed > 0 ? "Still owed" : s.theyOwe > 0 ? "They owe the shop" : "Balance"}</span>
              <span className="font-mono">{peso(s.owed > 0 ? s.owed : s.theyOwe ? -s.theyOwe : 0)}</span>
            </div>
          </div>

          <div className="flex flex-col gap-1 text-[11px]" style={muted}>
            {!removed && s.futureRuns.length > 0 && (
              <span className="flex items-start gap-1.5"><Truck size={13} className="shrink-0 mt-px" style={{ color: ACCENT.amber }} />
                Taken off {s.futureRuns.length} delivery{s.futureRuns.length > 1 ? " runs" : ""} still to come:{" "}
                {s.futureRuns.map((r) => `${r.customer} (${r.date ? niceDay(r.date) : "no day yet"}, ${r.role})`).join(", ")}</span>
            )}
            {!removed && s.login && (
              <span className="flex items-start gap-1.5"><Smartphone size={13} className="shrink-0 mt-px" style={{ color: ACCENT.amber }} />
                Their phone login @{s.login} is switched off (Bring back switches it on again).</span>
            )}
            {!removed && <span>Their days stay in the records. Find them again with <b>Show hidden</b>.</span>}
          </div>

          {(s.owed > 0 || removed) && (
            <div className="grid gap-2 items-end" style={{ gridTemplateColumns: "140px minmax(0,1fr)" }}>
              <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>Pay now (₱)</span>
                <input type="number" min="0" value={pay} onChange={(e) => setPay(e.target.value)} placeholder="0"
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono" style={inputSt} /></label>
              <label className="flex flex-col gap-1"><span className="text-[10px]" style={muted}>Note (optional)</span>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. cash, GCash"
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputSt} /></label>
            </div>
          )}
          {err && <p className="text-xs" style={{ color: ACCENT.danger }}>{err}</p>}

          {removed ? (
            <div className="flex gap-2">
              <button onClick={() => remove(amount)} disabled={busy || amount <= 0}
                className="flex-1 py-2 rounded-lg text-sm font-semibold text-black/85 disabled:opacity-50 flex items-center justify-center gap-1.5" style={{ background: ACCENT.cyan }}>
                <Wallet size={14} /> {amount > 0 ? `Pay ${peso(amount)}` : "Pay"}</button>
              <button onClick={bringBack} disabled={busy} className="px-4 py-2 rounded-lg text-sm flex items-center gap-1.5"
                style={{ border: `1px solid ${ACCENT.cyan}`, color: ACCENT.cyan }}><RotateCcw size={14} /> Bring back</button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <button onClick={() => remove(s.owed > 0 ? amount : 0)} disabled={busy || (s.owed > 0 && amount <= 0)}
                className="py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:opacity-50 flex items-center justify-center gap-1.5" style={{ background: ACCENT.cyan }}>
                <UserMinus size={15} /> {s.owed > 0 ? `Pay ${peso(amount)} and remove ${first}` : `Remove ${first}`}</button>
              {s.owed > 0 && (
                <button onClick={() => remove(0)} disabled={busy} className="py-2 rounded-lg text-xs"
                  style={{ border: "1px solid var(--border)", ...muted }}>
                  Remove without paying now ({peso(s.owed)} stays owed — Payslip → Show hidden)</button>
              )}
            </div>
          )}
          {canDelete && (
            <button onClick={deleteForever} disabled={busy} className="self-start text-[11px] flex items-center gap-1 mt-1" style={{ color: ACCENT.danger }}>
              <Trash2 size={12} /> Added by mistake? Delete every record of {first}</button>
          )}
        </div>
      )}
    </Modal>
  );
}
