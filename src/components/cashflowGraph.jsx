import React, { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, Scale, X, PieChart, PiggyBank, Repeat, PackageCheck, ChevronDown, PackageSearch } from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { api } from "../lib/api";
import { Modal } from "./kit";

// ---------------------------------------------------------------------------
// CASH IN vs CASH OUT (Progression page, right column)
//   One ring: the cool slices are money that came in (sales per sales person, bulk order payments, cash added),
//   the red / orange slices are money that went out, one slice per thing it was spent on (payroll, each loan or bill,
//   each maintenance item such as "Truck maintenance", stock purchases, cash advances...).
//   Cash Flow items (loans, bills, maintenance, salaries from attendance, benefits) count what is DUE in the period,
//   each due date marked paid / to pay / overdue, so the monthly report is complete even before payments are logged.
//   A cost due every few months (change oil P2,500 every 6 months) counts as its MONTHLY SHARE (P417) in each month.
//   Click a slice (or its row) to see every payment inside it. Data: GET /api/shop/cashflow-breakdown?period=
//   TO SAVE (bottom): what to put aside every month (shares of those costs + the restock fund = the original price of
//   what was sold). Data: GET /api/shop/savings-plan
//   AUTO INVENTORY (button above the restock fund): every item at its C / B / A stock level, what was sold since it was
//   last bought and how much to buy. An item joins when it drops to C and leaves once it is bought again. No cost price
//   given = 65% of the selling price.
// ---------------------------------------------------------------------------

const IN_COLORS = ["#3FDCC8", "#22c55e", "#2BA9A0", "#86EFAC", "#7FE5D6", "#15803D", "#5EEAD4", "#1F8F86"];
const OUT_COLORS = ["#E15A4C", "#F0795B", "#C9793D", "#E3A93A", "#B8473D", "#F29E7A", "#D9623B", "#F4B860", "#9F3A30", "#FB923C"];
const PERIODS = [["month", "This month"], ["3m", "3 months"], ["12m", "12 months"], ["all", "All"]];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const peso = (v) => `${v < 0 ? "-" : ""}₱${Math.round(Math.abs(v || 0)).toLocaleString()}`;
const short = (v) => {
  const a = Math.abs(v || 0);
  const s = a >= 1e6 ? `₱${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M` : a >= 1e3 ? `₱${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}k` : `₱${Math.round(a)}`;
  return v < 0 ? `-${s}` : s;
};
const niceDate = (d) => {
  if (!d) return "";
  const [y, m, day] = d.split("-");
  return day ? `${MONTHS[Number(m) - 1]} ${Number(day)}` : `${MONTHS[Number(m) - 1]} ${y}`;
};

function Ring({ slices, total, selected, onSelect, net }) {
  const size = 230;
  const cx = size / 2;
  const r = 86;
  const sw = 26;
  const C = 2 * Math.PI * r;
  const gap = slices.length > 1 ? 1.5 : 0;
  let acc = 0;
  const arcs = slices.map((s) => {
    const len = total ? (s.amount / total) * C : 0;
    const a = { ...s, len: Math.max(0, len - gap), offset: acc };
    acc += len;
    return a;
  });
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0" role="img" aria-label="Cash in and cash out by category">
      <g transform={`rotate(-90 ${cx} ${cx})`}>
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--panelAlt)" strokeWidth={sw} />
        {arcs.map((a) => (
          <circle key={a.key} cx={cx} cy={cx} r={r} fill="none" stroke={a.color}
            strokeWidth={selected === a.key ? sw + 8 : sw}
            strokeDasharray={`${a.len} ${C - a.len}`} strokeDashoffset={-a.offset}
            opacity={selected && selected !== a.key ? 0.35 : 1}
            style={{ cursor: "pointer", transition: "all .15s" }} onClick={() => onSelect(a.key)}>
            <title>{`${a.label}: ${peso(a.amount)}`}</title>
          </circle>
        ))}
      </g>
      <text x={cx} y={cx - 14} textAnchor="middle" style={{ fontSize: 10, fill: "var(--muted)", letterSpacing: 1 }}>NET</text>
      <text x={cx} y={cx + 8} textAnchor="middle" className="font-mono font-semibold"
        style={{ fontSize: 22, fill: net >= 0 ? "#22c55e" : ACCENT.danger }}>{short(net)}</text>
      <text x={cx} y={cx + 26} textAnchor="middle" style={{ fontSize: 9, fill: "var(--muted)" }}>{net >= 0 ? "more in than out" : "more out than in"}</text>
    </svg>
  );
}

function InOutBars({ series, byDay }) {
  const data = byDay ? series : series.slice(-12);
  if (!data.length) return null;
  const max = Math.max(1, ...data.flatMap((d) => [d.in, d.out]));
  return (
    <div>
      <div className="flex items-end gap-[3px] h-20">
        {data.map((d) => (
          <div key={d.key} className="flex-1 min-w-0 h-full flex items-end gap-[1px]" title={`${niceDate(d.key)}: in ${peso(d.in)}, out ${peso(d.out)}`}>
            <div className="flex-1 rounded-t-[3px]" style={{ height: `${(d.in / max) * 100}%`, background: ACCENT.cyan, minHeight: d.in ? 2 : 0 }} />
            <div className="flex-1 rounded-t-[3px]" style={{ height: `${(d.out / max) * 100}%`, background: ACCENT.danger, minHeight: d.out ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[9px] mt-1" style={{ color: "var(--faint)" }}>
        <span>{niceDate(data[0].key)}</span>
        {data.length > 1 && <span>{niceDate(data[data.length - 1].key)}</span>}
      </div>
    </div>
  );
}

const longDate = (d) => {
  if (!d) return "";
  const [y, m, day] = d.split("-");
  return `${MONTHS[Number(m) - 1]} ${Number(day)}, ${y}`;
};

// ---------------- TO SAVE ----------------
const LEVEL_COLOR = { A: ACCENT.danger, B: ACCENT.amber, C: "#e8d44d" };
function LevelTag({ level }) {
  if (!level) return null;
  const c = LEVEL_COLOR[level];
  return <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0" style={{ color: c, background: `${c}1f`, border: `1px solid ${c}55` }}>{level}</span>;
}
const num = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const levelsText = (x) => [["A", x.levelA], ["B", x.levelB], ["C", x.levelC]].filter(([, v]) => v != null).map(([k, v]) => `${k} ≤ ${num(v)}`).join(" · ");

// Auto inventory: what to buy again, most urgent first
function AutoInventory({ items, total, onClose }) {
  const sub = { color: "var(--muted)" };
  const groups = [["A", "A · critical"], ["B", "B · low"], ["C", "C · watch"]];
  return (
    <Modal title="Auto inventory — items to buy again" onClose={onClose} width={620}>
      <div className="flex flex-col gap-3">
        <div className="rounded-xl px-3 py-2.5 flex items-center gap-3" style={{ background: `${ACCENT.cyan}10`, border: `1px solid ${ACCENT.cyan}55` }}>
          <PackageSearch size={20} style={{ color: ACCENT.cyan }} />
          <div className="flex-1 text-xs">
            <b>{items.length} item{items.length === 1 ? "" : "s"}</b> at a critical stock level
            <div className="text-[10px]" style={sub}>Buying them back costs about this much (the cost price, or 65% of the selling price when no cost was given).</div>
          </div>
          <span className="font-mono text-base font-semibold" style={{ color: ACCENT.cyan }}>{peso(total)}</span>
        </div>
        {!items.length && <p className="text-xs text-center py-6" style={sub}>No item is at its C, B or A level. Nothing to buy yet.</p>}
        <div className="flex flex-col gap-3 max-h-[60vh] overflow-y-auto pr-1">
          {groups.map(([lv, title]) => {
            const list = items.filter((x) => x.level === lv);
            if (!list.length) return null;
            return (
              <div key={lv} className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold tracking-wide" style={{ color: LEVEL_COLOR[lv] }}>{title.toUpperCase()} · {list.length}</span>
                {list.map((x) => (
                  <div key={x.id} className="rounded-lg px-2.5 py-2 grid items-center gap-2 text-xs" style={{ background: "var(--panelAlt)", gridTemplateColumns: "minmax(0,1fr) 120px 96px" }}>
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0"><LevelTag level={x.level} /><span className="truncate font-medium">{x.name}</span></div>
                      <div className="text-[10px] truncate" style={sub}>
                        {x.qty > 0 ? `${num(x.qty)} ${x.unit} left` : "out of stock"}{levelsText(x) ? ` · ${levelsText(x)}` : ""}
                        {x.units > 0 ? ` · ${num(x.units)} sold since ${x.sinceKind === "bought" ? `you bought it (${niceDate(x.since)})` : "the 1st"}` : ""}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px]" style={sub}>buy</div>
                      <div className="font-mono font-semibold">{x.buy ? `${num(x.buy)} ${x.unit}` : "as needed"}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px]" style={sub}>at {peso(x.costNow)}{x.costAuto ? "*" : ""}</div>
                      <div className="font-mono font-semibold" style={{ color: ACCENT.cyan }}>{x.buy ? peso(x.buyCost) : "—"}</div>
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
        <p className="text-[10px]" style={{ color: "var(--faint)" }}>
          An item joins this list when its stock drops to its C level and leaves it by itself once you buy it again
          (Supply → the item → Edit → Buy stock) or correct the count. “Buy” = what was sold since you last bought it, at least
          enough to be above C again. * = no cost price given: 65% of the selling price.
        </p>
      </div>
    </Modal>
  );
}

function Bar({ v, max, color }) {
  return (
    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--panel)" }}>
      <div className="h-full rounded-full" style={{ width: `${max ? Math.min(100, (v / max) * 100) : 0}%`, background: color }} />
    </div>
  );
}

function ToSave() {
  const [p, setP] = useState(null);
  const [all, setAll] = useState(false);
  const [autoOpen, setAutoOpen] = useState(false);
  useEffect(() => { api.get("/api/shop/savings-plan").then(setP).catch(() => {}); }, []);
  if (!p) return null;
  const restock = all ? p.restock : p.restock.slice(0, 6);
  const sub = { color: "var(--muted)" };
  return (
    <div className="flex flex-col gap-2.5 pt-3" style={{ borderTop: "1px dashed var(--border)" }}>
      <div className="flex items-center gap-1.5">
        <PiggyBank size={14} style={{ color: ACCENT.amber }} />
        <span className="text-[10px] font-semibold tracking-wide" style={{ color: ACCENT.amber }}>TO SAVE</span>
        <span className="text-[10px]" style={sub}>· put aside every month so big costs never pile up</span>
      </div>

      <div className="rounded-xl px-3 py-2.5" style={{ background: `${ACCENT.amber}12`, border: `1px solid ${ACCENT.amber}55` }}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-semibold">Save this month</span>
          <span className="font-mono text-base font-semibold" style={{ color: ACCENT.amber }}>{peso(p.saveThisMonth)}</span>
        </div>
        <div className="text-[10px] mt-0.5" style={sub}>
          {peso(p.recurringMonthly)} for costs that come every few months + {peso(p.cogsThisMonth)} original price of what was sold this month (to buy it again)
        </div>
      </div>

      <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1 mt-1" style={sub}><Repeat size={11} /> COSTS THAT COME EVERY FEW MONTHS</span>
      {p.recurring.length ? p.recurring.map((r) => {
        const over = r.status === "overdue";
        const c = over ? ACCENT.danger : ACCENT.amber;
        return (
          <div key={`${r.kind}${r.id}`} className="rounded-lg px-2.5 py-2 flex flex-col gap-1.5" style={{ background: "var(--panelAlt)" }}>
            <div className="flex items-center gap-2 text-xs">
              <span className="flex-1 min-w-0 truncate font-medium">{r.name}
                <span className="font-normal text-[10px] ml-1.5" style={sub}>{peso(r.amount)} every {r.every}</span>
              </span>
              <span className="font-mono shrink-0" style={{ color: c }}>{peso(r.monthly)}<span className="text-[10px]" style={sub}>/mo</span></span>
            </div>
            {r.nextDue ? (
              <>
                <Bar v={r.shouldHave} max={r.amount} color={c} />
                <div className="text-[10px]" style={sub}>
                  {over
                    ? <span style={{ color: ACCENT.danger }}>Due since {longDate(r.nextDue)}: have the full {peso(r.amount)} ready</span>
                    : <>Month {r.monthIndex} of {r.months}: you should have <b style={{ color: "var(--text)" }}>{peso(r.shouldHave)}</b> saved · due {longDate(r.nextDue)}</>}
                </div>
              </>
            ) : <div className="text-[10px]" style={sub}>Set its start date in Cash Flow to see how much should be saved by now.</div>}
          </div>
        );
      }) : <span className="text-[11px]" style={{ color: "var(--faint)" }}>None yet. Maintenance (change oil, tires…) or bills paid every 2+ months show here with their monthly share.</span>}

      <button onClick={() => setAutoOpen(true)} className="mt-1 rounded-xl px-3 py-2.5 flex items-center gap-2.5 text-left"
        style={{ background: `${ACCENT.cyan}10`, border: `1px solid ${ACCENT.cyan}66` }} title="Items at their C / B / A stock level: what to buy again">
        <PackageSearch size={18} style={{ color: ACCENT.cyan }} />
        <span className="flex-1 min-w-0">
          <span className="text-xs font-semibold block">Auto inventory</span>
          <span className="text-[10px] block truncate" style={sub}>
            {p.autoCount ? `${p.autoCount} item${p.autoCount === 1 ? "" : "s"} at a critical level · buying them back ≈ ${peso(p.autoCost)}` : "No item is at a critical level"}</span>
        </span>
        <span className="text-[11px] font-semibold px-2.5 py-1 rounded-md shrink-0" style={{ background: ACCENT.cyan, color: "#000" }}>Show</span>
      </button>
      {autoOpen && <AutoInventory items={p.auto} total={p.autoCost} onClose={() => setAutoOpen(false)} />}

      <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1 mt-1" style={sub}><PackageCheck size={11} /> RESTOCK FUND · keep the original price of what you sold</span>
      {p.restock.length ? (
        <>
          <div className="grid grid-cols-3 gap-1.5 text-center">
            {[["Kept to rebuy", p.restockSaved, ACCENT.cyan], ["Rebuy now costs", p.restockRebuy, "var(--text)"],
              [p.restockExtra >= 0 ? "Extra from profit" : "Left over", Math.abs(p.restockExtra), p.restockExtra > 0 ? ACCENT.amber : "#22c55e"]].map(([l, v, c]) => (
              <div key={l} className="rounded-lg px-1.5 py-1.5" style={{ background: "var(--panelAlt)" }}>
                <div className="text-[9px]" style={sub}>{l}</div>
                <div className="font-mono text-xs font-semibold" style={{ color: c }}>{peso(v)}</div>
              </div>
            ))}
          </div>
          {restock.map((x) => (
            <div key={x.id} className="rounded-lg px-2.5 py-2 flex flex-col gap-0.5 text-xs" style={{ background: "var(--panelAlt)" }}>
              <div className="flex items-center gap-2">
                <LevelTag level={x.level} />
                <span className="flex-1 min-w-0 truncate font-medium">{x.name}</span>
                <span className="font-mono shrink-0" style={{ color: ACCENT.cyan }}>{peso(x.saved)}</span>
              </div>
              <div className="text-[10px]" style={sub}>
                {x.units.toLocaleString()} {x.unit} sold since {x.sinceKind === "bought" ? `you last bought it (${niceDate(x.since)})` : "the 1st of the month"}: keep {peso(x.saved)} to buy them again
              </div>
              {Math.abs(x.extra) >= 1 && (
                <div className="text-[10px]" style={{ color: x.extra > 0 ? ACCENT.amber : "#22c55e" }}>
                  {x.extra > 0
                    ? `Cost price is now ${peso(x.costNow)}: rebuying costs ${peso(x.rebuy)}, so add ${peso(x.extra)} from the ${peso(x.profit)} profit`
                    : `Cost price is now ${peso(x.costNow)}: rebuying costs ${peso(x.rebuy)}, ${peso(-x.extra)} left over`}
                </div>
              )}
            </div>
          ))}
          {p.restock.length > 6 && (
            <button onClick={() => setAll((a) => !a)} className="text-[11px] self-start flex items-center gap-1" style={{ color: ACCENT.cyan }}>
              <ChevronDown size={12} style={{ transform: all ? "rotate(180deg)" : "none" }} /> {all ? "Show fewer" : `Show all ${p.restock.length} items`}
            </button>
          )}
        </>
      ) : <span className="text-[11px]" style={{ color: "var(--faint)" }}>No item at its C / B / A level was sold since it was last bought. An item shows here when its stock drops to C and leaves once it is bought again.</span>}
    </div>
  );
}

const STATUS = { paid: "#22c55e", "to pay": ACCENT.amber, overdue: ACCENT.danger };

function Row({ s, pct, selected, onSelect }) {
  return (
    <button onClick={() => onSelect(s.key)} className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs text-left"
      style={{ background: selected ? `${s.color}1f` : "var(--panelAlt)", border: `1px solid ${selected ? s.color : "transparent"}` }}>
      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: s.color }} />
      <span className="flex-1 min-w-0 truncate">{s.label}
        {s.shared && <span className="text-[10px] ml-1.5" style={{ color: "var(--muted)" }}>{peso(s.full)} / {s.every}</span>}
        {s.unpaid > 0 && <span className="text-[10px] ml-1.5" style={{ color: ACCENT.amber }}>{peso(s.unpaid)} {s.shared ? "to set aside" : "to pay"}</span>}
      </span>
      <span className="text-[10px] shrink-0" style={{ color: "var(--muted)" }}>{pct}%</span>
      <span className="font-mono shrink-0 w-20 text-right">{peso(s.amount)}</span>
    </button>
  );
}

export default function CashFlowGraph() {
  const [period, setPeriod] = useState("month");
  const [d, setD] = useState(null);
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    let alive = true;
    setSelected(null);
    api.get(`/api/shop/cashflow-breakdown?period=${period}`).then((x) => alive && setD(x)).catch(() => {});
    return () => { alive = false; };
  }, [period]);

  const slices = useMemo(() => {
    if (!d) return [];
    return [
      ...d.in.map((s, i) => ({ ...s, color: IN_COLORS[i % IN_COLORS.length] })),
      ...d.out.map((s, i) => ({ ...s, color: OUT_COLORS[i % OUT_COLORS.length] })),
    ];
  }, [d]);
  const total = slices.reduce((a, s) => a + s.amount, 0);
  const pick = (k) => setSelected((cur) => (cur === k ? null : k));
  const sel = slices.find((s) => s.key === selected);
  const pctOf = (s) => {
    const base = s.kind === "in" ? d.totalIn : d.totalOut;
    return base ? Math.round((s.amount / base) * 100) : 0;
  };

  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW, maxHeight: "calc(100vh - 48px)" }}
      className="flex-1 min-w-[340px] sticky top-6 rounded-xl flex flex-col overflow-hidden">
      <div className="flex items-center gap-2.5 px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div style={{ width: 32, height: 32, borderRadius: 9, background: `${ACCENT.cyan}18`, border: `1px solid ${ACCENT.cyan}45`, color: ACCENT.cyan }} className="flex items-center justify-center shrink-0">
          <PieChart size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-sm leading-tight">Cash In vs Cash Out</h3>
          <p className="text-[10px]" style={{ color: "var(--muted)" }}>What came in, and everything that is due or paid — click a slice</p>
        </div>
      </div>

      <div className="overflow-y-auto px-4 py-4 flex flex-col gap-4">
        <div className="flex rounded-lg p-0.5 self-start" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {PERIODS.map(([k, label]) => (
            <button key={k} onClick={() => setPeriod(k)} className="text-[11px] px-2.5 py-1 rounded-md font-medium"
              style={period === k ? { background: ACCENT.cyan, color: "#000" } : { color: "var(--muted)" }}>{label}</button>
          ))}
        </div>

        {!d ? <p className="text-xs" style={{ color: "var(--muted)" }}>Loading…</p> : (
          <>
            <div className="grid grid-cols-3 gap-2">
              {[
                { icon: ArrowDownLeft, label: "Cash in", v: d.totalIn, c: ACCENT.cyan },
                { icon: ArrowUpRight, label: "Cash out", v: d.totalOut, c: ACCENT.danger },
                { icon: Scale, label: "Net", v: d.net, c: d.net >= 0 ? "#22c55e" : ACCENT.danger },
              ].map(({ icon: Icon, label, v, c }) => (
                <div key={label} className="rounded-lg px-2.5 py-2 min-w-0" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center gap-1 text-[10px]" style={{ color: "var(--muted)" }}><Icon size={11} style={{ color: c }} /> {label}</div>
                  <div className="font-mono text-sm font-semibold truncate" style={{ color: c }}>{peso(v)}</div>
                  {label === "Cash out" && d.outUnpaid > 0 && (
                    <div className="text-[9px] truncate" style={{ color: "var(--muted)" }}>
                      {peso(d.outPaid)} paid · <span style={{ color: ACCENT.amber }}>{peso(d.outUnpaid)} to pay</span>
                    </div>
                  )}
                </div>
              ))}
            </div>

            {total > 0 ? (
              <>
                <div className="flex justify-center"><Ring slices={slices} total={total} selected={selected} onSelect={pick} net={d.net} /></div>
                {/* in vs out split */}
                <div>
                  <div className="flex h-2 rounded-full overflow-hidden">
                    <div style={{ width: `${(d.totalIn / total) * 100}%`, background: ACCENT.cyan }} />
                    <div style={{ width: `${(d.totalOut / total) * 100}%`, background: ACCENT.danger }} />
                  </div>
                  <div className="flex justify-between text-[10px] mt-1" style={{ color: "var(--muted)" }}>
                    <span>in {Math.round((d.totalIn / total) * 100)}%</span><span>out {Math.round((d.totalOut / total) * 100)}%</span>
                  </div>
                </div>

                {sel && (
                  <div className="rounded-xl p-3 flex flex-col gap-2" style={{ border: `1px solid ${sel.color}`, background: `${sel.color}10` }}>
                    <div className="flex items-start gap-2">
                      <span className="w-3 h-3 rounded-full shrink-0 mt-1" style={{ background: sel.color }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">{sel.label}</div>
                        <div className="text-[11px]" style={{ color: "var(--muted)" }}>
                          {peso(sel.amount)} · {pctOf(sel)}% of cash {sel.kind} · {sel.count} {sel.count === 1 ? "entry" : "entries"}
                          {sel.unpaid > 0 && <span style={{ color: ACCENT.amber }}> · {peso(sel.unpaid)} still to pay</span>}
                        </div>
                      </div>
                      <button onClick={() => setSelected(null)} style={{ color: "var(--muted)" }}><X size={15} /></button>
                    </div>
                    <div className="flex flex-col gap-1 max-h-56 overflow-y-auto pr-1">
                      {sel.items.map((it, i) => (
                        <div key={i} className="flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs" style={{ background: "var(--panel)" }}>
                          <span className="font-mono text-[10px] w-12 shrink-0" style={{ color: "var(--muted)" }}>{niceDate(it.date)}</span>
                          <span className="flex-1 min-w-0 truncate">{it.label}{it.note && !it.status ? <span style={{ color: "var(--muted)" }}> · {it.note}</span> : null}
                            {it.status && it.note && it.note !== it.status ? <span style={{ color: "var(--muted)" }}> · {it.note.replace(/ · (paid|to pay|overdue)$/, "")}</span> : null}
                          </span>
                          {it.status && (
                            <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0"
                              style={{ color: STATUS[it.status] || "var(--muted)", background: `${STATUS[it.status] || "#888"}1f` }}>{it.status}</span>
                          )}
                          <span className="font-mono shrink-0">{peso(it.amount)}</span>
                        </div>
                      ))}
                      {sel.count > sel.items.length && <span className="text-[10px]" style={{ color: "var(--faint)" }}>+{sel.count - sel.items.length} older</span>}
                    </div>
                  </div>
                )}

                <div className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1" style={{ color: ACCENT.cyan }}><ArrowDownLeft size={11} /> CASH IN</span>
                  {d.in.length ? slices.filter((s) => s.kind === "in").map((s) => <Row key={s.key} s={s} pct={pctOf(s)} selected={selected === s.key} onSelect={pick} />)
                    : <span className="text-[11px]" style={{ color: "var(--faint)" }}>Nothing came in.</span>}
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-semibold tracking-wide flex items-center gap-1" style={{ color: ACCENT.danger }}><ArrowUpRight size={11} /> CASH OUT</span>
                  {d.out.length ? slices.filter((s) => s.kind === "out").map((s) => <Row key={s.key} s={s} pct={pctOf(s)} selected={selected === s.key} onSelect={pick} />)
                    : <span className="text-[11px]" style={{ color: "var(--faint)" }}>Nothing went out.</span>}
                </div>

                <div className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-semibold tracking-wide" style={{ color: "var(--muted)" }}>{d.byDay ? "DAY BY DAY" : "MONTH BY MONTH"}</span>
                  <InOutBars series={d.series} byDay={d.byDay} />
                  <div className="flex gap-3 text-[10px]" style={{ color: "var(--muted)" }}>
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ background: ACCENT.cyan }} /> in</span>
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ background: ACCENT.danger }} /> out</span>
                  </div>
                </div>
              </>
            ) : (
              <p className="text-xs py-6 text-center" style={{ color: "var(--muted)" }}>
                No money recorded in this period yet. Sales, order payments, payroll, loan / bill / maintenance payments and stock purchases show up here.
              </p>
            )}
          </>
        )}
        <ToSave />
      </div>
    </div>
  );
}
