import React, { useEffect, useMemo, useRef, useState } from "react";
import { FolderOpen, Trash2, ChevronLeft, ChevronRight, Users, ShoppingCart, Truck, FileArchive, Database, CalendarDays, ChevronDown, CalendarRange, CheckCircle2 } from "lucide-react";
import { ACCENT } from "../lib/theme";
import { Modal } from "./kit";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// Calendar -> "Open saved data": read the data you moved off this laptop (Archive & free space ZIP, a day ZIP,
// or an inventory_backup.db). Choose the file, pick a day, and see who was present, what each sales person sold
// (item by item), the orders and the deliveries of that day (truck, driver, helpers = the Planner). Only the database
// is kept here (opened_data/); photos stay in the ZIP.
// Data: /api/shop/archive/open, /archive/opened, /archive/opened/{id}/days, /archive/opened/{id}/day/{date}
// ---------------------------------------------------------------------------

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const pad = (n) => String(n).padStart(2, "0");
const peso = (n) => `₱${(Math.round((Number(n) || 0) * 100) / 100).toLocaleString()}`;
const short = (n) => (n >= 1000 ? `₱${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `₱${Math.round(n)}`);
const mb = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
const muted = { color: "var(--muted)" };
const niceDate = (s) => { if (!s) return "?"; const [y, m, d] = s.split("-"); return `${MONTHS[Number(m) - 1].slice(0, 3)} ${Number(d)}, ${y}`; };
const KIND = { archive: ["Archive ZIP", FileArchive], day: ["Day ZIP", CalendarDays], database: ["Database", Database] };

function MonthGrid({ month, days, selected, onPick }) {
  const [y, m] = month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const lead = (first.getDay() + 6) % 7;
  const n = new Date(y, m, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: n }, (_, i) => `${month}-${pad(i + 1)}`)];
  return (
    <div className="grid grid-cols-7 gap-1">
      {WEEK.map((w) => <span key={w} className="text-[9px] text-center font-semibold" style={muted}>{w}</span>)}
      {cells.map((d, i) => {
        if (!d) return <span key={`x${i}`} />;
        const info = days[d];
        const sel = d === selected;
        return (
          <button key={d} disabled={!info} onClick={() => onPick(d)} className="rounded-md px-1 py-1 text-left min-h-[46px] flex flex-col"
            style={{ background: sel ? `${ACCENT.cyan}26` : info ? "var(--panelAlt)" : "transparent", border: `1px solid ${sel ? ACCENT.cyan : info ? "var(--border)" : "transparent"}`,
              opacity: info ? 1 : 0.35, cursor: info ? "pointer" : "default" }}>
            <span className="text-[10px] font-semibold">{Number(d.slice(8))}</span>
            {info && info.sales > 0 && <span className="text-[9px] font-mono" style={{ color: ACCENT.cyan }}>{short(info.sales)}</span>}
            {info && (info.present > 0 || info.orders > 0) && (
              <span className="text-[8px]" style={muted}>{info.present ? `${info.present} in` : ""}{info.orders ? ` · ${info.orders} ord` : ""}</span>
            )}
            {info && info.deliveries > 0 && <span className="text-[8px]" style={{ color: "#22c55e" }}>{info.deliveries} truck{info.deliveries > 1 ? "s" : ""}</span>}
          </button>
        );
      })}
    </div>
  );
}

function Section({ icon: Icon, title, color, right, children }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <Icon size={13} style={{ color }} />
        <span className="text-[11px] font-semibold tracking-wide uppercase" style={{ color }}>{title}</span>
        <span className="ml-auto text-[11px]" style={muted}>{right}</span>
      </div>
      {children}
    </div>
  );
}

function PersonSales({ p }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-lg" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left">
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="flex-1 font-medium truncate">{p.person}</span>
        <span className="text-[11px]" style={muted}>{p.count} sale(s) · {p.items.length} item(s)</span>
        <span className="font-mono font-semibold w-24 text-right">{peso(p.total)}</span>
      </button>
      {open && (
        <div className="px-3 pb-2 flex flex-col gap-0.5">
          {p.items.map((it, i) => (
            <div key={i} className="flex items-center gap-2 text-xs px-2 py-1 rounded" style={{ background: "var(--panel)" }}>
              <span className="font-mono w-20 shrink-0 text-right" style={muted}>{Number(it.qty).toLocaleString()} {it.unit}</span>
              <span className="flex-1 truncate">{it.item}</span>
              <span className="font-mono">{peso(it.amount)}</span>
            </div>
          ))}
          {!p.items.length && <span className="text-[11px]" style={muted}>No item lines.</span>}
        </div>
      )}
    </div>
  );
}

function DayView({ d }) {
  if (!d) return <p className="text-xs" style={muted}>Pick a day on the left.</p>;
  const salesTotal = d.salesByPerson.reduce((a, p) => a + p.total, 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-3">
        <h3 className="text-base font-semibold">{niceDate(d.date)}</h3>
        <span className="text-xs" style={muted}>{d.present.length} present · {peso(salesTotal)} sales · {d.orders.length} order(s) · {(d.deliveries || []).length} delivery run(s)</span>
      </div>
      <Section icon={Users} title="Employees present" color={ACCENT.cyan} right={`${d.present.length}`}>
        {d.present.length ? (
          <div className="grid gap-1" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
            {d.present.map((p, i) => (
              <div key={i} className="flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-md" style={{ background: "var(--panelAlt)" }}>
                <span className="flex-1 truncate">{p.name}<span style={muted}> · {p.position || "staff"}</span></span>
                {p.ot > 0 && <span className="font-mono text-[10px]" style={{ color: ACCENT.cyan }}>+{p.ot}h</span>}
                {p.ut > 0 && <span className="font-mono text-[10px]" style={{ color: ACCENT.amber }}>−{p.ut}h</span>}
                <span className="font-mono" style={{ color: ACCENT.cyan }}>{p.timeIn}</span>
              </div>
            ))}
          </div>
        ) : <span className="text-xs" style={{ color: "var(--faint)" }}>Nobody was checked in.</span>}
      </Section>
      <Section icon={ShoppingCart} title="Sales by person" color={ACCENT.copper} right={peso(salesTotal)}>
        {d.salesByPerson.length ? d.salesByPerson.map((p) => <PersonSales key={p.person} p={p} />)
          : <span className="text-xs" style={{ color: "var(--faint)" }}>No sales that day.</span>}
      </Section>
      <Section icon={Truck} title="Orders (Add Order)" color={ACCENT.amber} right={`${d.orders.length}`}>
        {d.kind === "day" && <span className="text-xs" style={muted}>A day ZIP has no order details{d.orderMoney ? ` (order payments that day: ${peso(d.orderMoney)})` : ""}.</span>}
        {d.orders.map((o) => (
          <div key={o.id} className="rounded-lg px-3 py-2 flex flex-col gap-1" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium flex-1 truncate">{o.customer}</span>
              {o.events.map((e) => <span key={e} className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ color: ACCENT.amber, background: `${ACCENT.amber}1c` }}>{e}</span>)}
              <span className="font-mono text-xs">{peso(o.paidSoFar)} / {peso(o.total)}</span>
            </div>
            <div className="text-[11px]" style={muted}>
              {o.items.map((i) => `${Number(i.qty).toLocaleString()} ${i.unit} ${i.name}${i.delivered ? " ✓" : ""}`).join(" · ")}
            </div>
            {o.paidToday > 0 && <div className="text-[11px]" style={{ color: ACCENT.cyan }}>Paid this day: {peso(o.paidToday)}</div>}
          </div>
        ))}
        {d.kind !== "day" && !d.orders.length && <span className="text-xs" style={{ color: "var(--faint)" }}>No order activity that day.</span>}
      </Section>
      <Section icon={CalendarRange} title="Deliveries (Planner)" color="#22c55e" right={`${(d.deliveries || []).length}`}>
        {(d.deliveries || []).map((x, i) => (
          <div key={i} className="rounded-lg px-3 py-2 flex flex-col gap-0.5" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2 text-sm">
              <Truck size={13} style={{ color: "#22c55e" }} />
              <span className="font-medium flex-1 truncate">{x.customer}{x.address ? <span className="font-normal text-xs" style={muted}> · {x.address}</span> : null}</span>
              {x.done && <span className="text-[10px] px-1.5 py-0.5 rounded-full flex items-center gap-1" style={{ color: "#22c55e", background: "#22c55e1c" }}><CheckCircle2 size={10} /> delivered</span>}
            </div>
            <div className="text-[11px]" style={muted}>
              {x.truck || "no truck"}{x.plate ? ` ${x.plate}` : ""}{x.trips > 1 ? ` × ${x.trips} trips` : ""}
              {x.mode && x.mode !== "truck" ? "" : <> · driver {x.driver || "—"} · helpers {x.helpers.length ? x.helpers.join(", ") : "—"}</>}
            </div>
          </div>
        ))}
        {!(d.deliveries || []).length && <span className="text-xs" style={{ color: "var(--faint)" }}>No delivery run that day.</span>}
      </Section>
    </div>
  );
}

export default function SavedDataModal({ onClose }) {
  const [files, setFiles] = useState(null);
  const [cur, setCur] = useState(null);
  const [days, setDays] = useState([]);
  const [month, setMonth] = useState(null);
  const [sel, setSel] = useState(null);
  const [day, setDay] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const loadFiles = () => api.get("/api/shop/archive/opened").then((l) => { setFiles(l); setCur((c) => c && l.some((x) => x.id === c) ? c : l[0]?.id || null); }).catch(() => setFiles([]));
  useEffect(() => { loadFiles(); }, []);
  useEffect(() => {
    setDays([]); setSel(null); setDay(null);
    if (!cur) return;
    api.get(`/api/shop/archive/opened/${cur}/days`).then((l) => {
      setDays(l);
      const last = l.length ? l[l.length - 1].date : null;
      setMonth(last ? last.slice(0, 7) : null);
      if (last) setSel(last);
    }).catch(() => {});
  }, [cur]);
  useEffect(() => {
    setDay(null);
    if (cur && sel) api.get(`/api/shop/archive/opened/${cur}/day/${sel}`).then(setDay).catch(() => {});
  }, [cur, sel]);
  const byDate = useMemo(() => Object.fromEntries(days.map((x) => [x.date, x])), [days]);
  const months = useMemo(() => [...new Set(days.map((x) => x.date.slice(0, 7)))], [days]);
  const mi = months.indexOf(month);

  async function open(file) {
    if (!file) return;
    setBusy(true);
    try {
      const e = await api.uploadFile("/api/shop/archive/open", file);
      await loadFiles();
      setCur(e.id);
    } catch { /* toast */ } finally { setBusy(false); }
  }
  async function remove(f) {
    if (!window.confirm(`Remove "${f.name}" from this laptop? Your ZIP / backup file itself is not touched.`)) return;
    try { await api.del(`/api/shop/archive/opened/${f.id}`); if (cur === f.id) setCur(null); loadFiles(); } catch { /* toast */ }
  }

  return (
    <Modal title="Open saved data" onClose={onClose} width={1120}>
      <div className="flex gap-4 items-start" style={{ minHeight: 480 }}>
        <div className="w-72 shrink-0 flex flex-col gap-3">
          <input ref={fileRef} type="file" accept=".zip,.db" className="hidden" onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; open(f); }} />
          <button onClick={() => fileRef.current && fileRef.current.click()} disabled={busy}
            className="py-2 rounded-lg text-sm font-semibold text-black/85 flex items-center justify-center gap-1.5 disabled:opacity-60" style={{ background: ACCENT.cyan }}>
            <FolderOpen size={14} /> {busy ? "Reading the file…" : "Open a file…"}
          </button>
          <p className="text-[10px] leading-relaxed" style={muted}>
            Choose a <b>TresNinos_archive…zip</b> (Archive &amp; free space), a <b>TresNinos_&lt;date&gt;.zip</b> (Download day) or an
            <b> inventory_backup.db</b> from your USB drive or other PC. Only the database is copied here; photos stay in your file.
          </p>
          <div className="flex flex-col gap-1">
            {files && !files.length && <span className="text-xs" style={{ color: "var(--faint)" }}>Nothing opened yet.</span>}
            {(files || []).map((f) => {
              const [kl, KIcon] = KIND[f.kind] || KIND.database;
              return (
                <div key={f.id} className="flex items-start gap-2 rounded-lg px-2.5 py-2 cursor-pointer" onClick={() => setCur(f.id)}
                  style={{ background: cur === f.id ? `${ACCENT.cyan}18` : "var(--panelAlt)", border: `1px solid ${cur === f.id ? ACCENT.cyan : "var(--border)"}` }}>
                  <KIcon size={14} className="mt-0.5 shrink-0" style={{ color: ACCENT.cyan }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium truncate" title={f.name}>{f.name}</div>
                    <div className="text-[10px]" style={muted}>{kl} · {niceDate(f.from)} – {niceDate(f.to)} · {mb(f.bytes || 0)}</div>
                  </div>
                  <button onClick={(e) => { e.stopPropagation(); remove(f); }} title="Remove from this laptop" style={muted}><Trash2 size={13} /></button>
                </div>
              );
            })}
          </div>
          {cur && month && (
            <div className="flex flex-col gap-2 rounded-lg p-2" style={{ border: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between">
                <button disabled={mi <= 0} onClick={() => setMonth(months[mi - 1])} className="disabled:opacity-30" style={muted}><ChevronLeft size={16} /></button>
                <span className="text-xs font-semibold">{MONTHS[Number(month.slice(5)) - 1]} {month.slice(0, 4)}</span>
                <button disabled={mi >= months.length - 1} onClick={() => setMonth(months[mi + 1])} className="disabled:opacity-30" style={muted}><ChevronRight size={16} /></button>
              </div>
              <MonthGrid month={month} days={byDate} selected={sel} onPick={setSel} />
              <span className="text-[10px]" style={muted}>{days.length} day(s) with data · {months.length} month(s)</span>
            </div>
          )}
          {cur && !days.length && <span className="text-xs" style={muted}>No days with sales, attendance, orders or deliveries in this file.</span>}
        </div>
        <div className="flex-1 min-w-0 rounded-xl p-4" style={{ border: "1px solid var(--border)", background: "var(--panel)" }}>
          {cur ? <DayView d={day} /> : <p className="text-xs" style={muted}>Open a saved file to read its days.</p>}
        </div>
      </div>
    </Modal>
  );
}
