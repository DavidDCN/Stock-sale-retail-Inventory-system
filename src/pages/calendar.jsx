import React, { useEffect, useMemo, useState } from "react";
import {
  Download, HardDrive, Archive, KeyRound, UserCheck, Smartphone, Users, ShoppingCart, PackageMinus,
  PackagePlus, ScrollText, Wallet, ChevronDown, ChevronRight, Trash2, FolderOpen, Truck,
} from "lucide-react";
import SavedDataModal from "../components/savedData";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal, UserAvatar, PasswordInput, DownloadLink } from "../components/kit";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// CALENDAR — what happened each day while someone was in charge.
//   left : months, newest on top; scroll down for older ones. Each day shows the sales total,
//          who was in charge (desktop sign-ins) and how many staff were present.
//   right: the chosen day — who was in charge (first/last time), sales people on the phone,
//          staff present, every sale with its items, stock deducted, stock bought, money in/out,
//          and every action taken on the desktop. "Download day" = ZIP (CSV + receipt photos).
//   top  : disk space, and "Archive & free space" (download everything older, then delete old photos).
// ---------------------------------------------------------------------------
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const peso = (n) => `₱${Math.round(Number(n) || 0).toLocaleString()}`;
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayStr = () => ymd(new Date());
const mb = (b) => (b >= 1073741824 ? `${(b / 1073741824).toFixed(1)} GB` : b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(0, Math.round(b / 1024))} KB`);
const initials = (n) => n.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
const PEOPLE_COLORS = [ACCENT.cyan, ACCENT.copper, ACCENT.amber, "#a78bfa", "#38bdf8", ACCENT.danger];
const colorFor = (name) => PEOPLE_COLORS[[...name].reduce((s, c) => s + c.charCodeAt(0), 0) % PEOPLE_COLORS.length];

function monthStart(offset) {           // offset 0 = this month, 1 = last month ...
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth() - offset, 1);
}

function Section({ icon: Icon, title, right, children, color = "var(--muted)" }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wide uppercase" style={{ color }}>
          <Icon size={13} /> {title}
        </span>
        {right}
      </div>
      {children}
    </div>
  );
}
const Empty = ({ children }) => <p className="text-[11px] py-1" style={{ color: "var(--faint)" }}>{children}</p>;
const Row = ({ left, right, sub, avatar }) => (
  <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md text-xs" style={{ background: "var(--panelAlt)" }}>
    <span className="min-w-0 truncate flex items-center gap-2">{avatar}<span className="min-w-0 truncate">{left}{sub && <span style={{ color: "var(--muted)" }}> {sub}</span>}</span></span>
    <span className="font-mono shrink-0" style={{ color: "var(--muted)" }}>{right}</span>
  </div>
);

function SaleRow({ s }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md text-xs" style={{ background: "var(--panelAlt)" }}>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-left">
        <span className="flex items-center gap-1 min-w-0 truncate">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="font-mono" style={{ color: "var(--muted)" }}>{s.time}</span> {s.person}
          <span style={{ color: "var(--muted)" }}>· {s.items.length} item{s.items.length === 1 ? "" : "s"}</span>
        </span>
        <span className="font-mono shrink-0" style={{ color: ACCENT.cyan }}>{peso(s.total)}</span>
      </button>
      {open && (
        <div className="px-6 pb-2 flex flex-col gap-0.5">
          {s.items.map((i, k) => (
            <div key={k} className="flex justify-between" style={{ color: "var(--muted)" }}>
              <span>{i.item} × {i.qty} {i.unit}</span><span className="font-mono">{peso(i.amount)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DayPanel({ date }) {
  const [d, setD] = useState(null);
  useEffect(() => {
    setD(null);
    api.get(`/api/shop/calendar/day/${date}`).then(setD).catch(() => setD({ error: true }));
  }, [date]);
  const nice = new Date(`${date}T00:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }}
      className="rounded-xl p-4 flex flex-col gap-4 min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-base leading-tight">{nice}</h3>
          {d && !d.error && (
            <p className="text-[11px] mt-0.5" style={{ color: "var(--muted)" }}>
              Sales {peso(d.totals.sales)} · profit {peso(d.totals.profit)} · money in {peso(d.totals.in)} · out {peso(d.totals.out)}
            </p>
          )}
        </div>
        <DownloadLink path={`/api/shop/calendar/day/${date}/download`} style={{ background: ACCENT.cyan }}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-black/85 shrink-0" title="ZIP: summary, CSV files for Excel, receipt photos">
          <Download size={14} /> Download day
        </DownloadLink>
      </div>
      {!d && <Empty>Loading…</Empty>}
      {d && !d.error && (
        <>
          <Section icon={UserCheck} title="In charge (desktop)" color={ACCENT.cyan}>
            {d.inCharge.length ? d.inCharge.map((p) => (
              <Row key={p.username} left={p.username} avatar={<UserAvatar uid={p.uid} name={p.username} size={24} />} right={`${p.first} – ${p.last}`} />
            )) : <Empty>Nobody used the desktop this day.</Empty>}
          </Section>
          <Section icon={Smartphone} title="Sales people on the phone">
            {d.salesPeople.length ? d.salesPeople.map((p) => (
              <Row key={p.username} left={p.name} sub={`@${p.username}`} avatar={<UserAvatar uid={p.uid} name={p.name} size={24} color={ACCENT.copper} />} right={`${p.first} – ${p.last}`} />
            )) : <Empty>No phone activity.</Empty>}
          </Section>
          <Section icon={Users} title={`Present (${d.present.length})`}>
            {d.present.length ? d.present.map((p, i) => <Row key={i} left={p.name} sub={p.position} right={p.timeIn} />)
              : <Empty>No attendance checked.</Empty>}
          </Section>
          {(d.deliveries || []).length > 0 && (
            <Section icon={Truck} title={`Deliveries (${d.deliveries.length})`} color={ACCENT.amber}>
              {d.deliveries.map((x, i) => (
                <Row key={i} left={x.customer} sub={x.mode && x.mode !== "truck" ? `${x.truck}${x.trips > 1 ? ` · ${x.trips} trips` : ""}`
                  : `${x.truck || "truck"}${x.plate ? ` · ${x.plate}` : ""} · driver ${x.driver || "—"}${x.helpers.length ? ` + ${x.helpers.join(", ")}` : ""}`}
                  right={x.done ? (x.mode === "pickup" ? "picked up" : "delivered") : "not marked"} />
              ))}
            </Section>
          )}
          <Section icon={ShoppingCart} title={`Sales (${d.sales.length})`} right={<span className="font-mono text-xs">{peso(d.totals.sales)}</span>}>
            {d.sales.length ? d.sales.map((s) => <SaleRow key={s.id} s={s} />) : <Empty>No sales.</Empty>}
          </Section>
          <Section icon={PackageMinus} title="Stock deducted">
            {d.deducted.length ? d.deducted.map((g, i) => <Row key={i} left={g.item} sub={`− ${g.qty} ${g.unit}`} right={peso(g.amount)} />)
              : <Empty>Nothing left the shelves.</Empty>}
          </Section>
          {d.purchases.length > 0 && (
            <Section icon={PackagePlus} title="Stock bought">
              {d.purchases.map((x, i) => (
                <Row key={i} left={x.item} sub={`+ ${x.qty} ${x.unit} at ${peso(x.unitCost)}`}
                  right={<span style={{ color: x.savings >= 0 ? ACCENT.cyan : ACCENT.danger }}>{x.savings >= 0 ? "saved " : "over "}{peso(Math.abs(x.savings))}</span>} />
              ))}
            </Section>
          )}
          <Section icon={Wallet} title="Money">
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              {[["In", d.moneyIn, ACCENT.cyan], ["Out", d.moneyOut, ACCENT.danger]].map(([label, obj, c]) => (
                <div key={label} className="rounded-md px-2.5 py-2" style={{ background: "var(--panelAlt)" }}>
                  <p className="font-semibold mb-1" style={{ color: c }}>{label}</p>
                  {Object.entries(obj).filter(([, v]) => v).map(([k, v]) => (
                    <div key={k} className="flex justify-between" style={{ color: "var(--muted)" }}>
                      <span>{k.replace(/([A-Z])/g, " $1").toLowerCase()}</span><span className="font-mono">{peso(v)}</span>
                    </div>
                  ))}
                  {!Object.values(obj).some(Boolean) && <span style={{ color: "var(--faint)" }}>—</span>}
                </div>
              ))}
            </div>
          </Section>
          <Section icon={ScrollText} title={`What was done (${d.log.length})`}>
            <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto pr-1">
              {d.log.length ? d.log.map((x, i) => (
                <div key={i} className="text-[11px] grid gap-2 px-1 py-0.5" style={{ gridTemplateColumns: "38px 90px minmax(0,1fr)" }}>
                  <span className="font-mono" style={{ color: "var(--faint)" }}>{x.time}</span>
                  <span className="truncate" style={{ color: colorFor(x.user) }}>{x.user}</span>
                  <span className="min-w-0"><b className="font-medium">{x.action}</b> <span style={{ color: "var(--muted)" }}>{x.detail}</span></span>
                </div>
              )) : <Empty>No desktop actions.</Empty>}
            </div>
          </Section>
        </>
      )}
    </div>
  );
}

function MonthGrid({ start, days, selected, onPick }) {
  const y = start.getFullYear(), m = start.getMonth();
  const count = new Date(y, m + 1, 0).getDate();
  const lead = (start.getDay() + 6) % 7;                 // Monday first
  const today = todayStr();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: count }, (_, i) => i + 1)];
  return (
    <div>
      <p className="text-sm font-semibold mb-2">{start.toLocaleDateString([], { month: "long", year: "numeric" })}</p>
      <div className="grid grid-cols-7 gap-1">
        {WEEK.map((w) => <span key={w} className="text-[9px] text-center font-semibold" style={{ color: "var(--faint)" }}>{w}</span>)}
        {cells.map((n, i) => {
          if (!n) return <span key={`e${i}`} />;
          const date = `${y}-${pad(m + 1)}-${pad(n)}`;
          const info = days[date];
          const future = date > today;
          const sel = date === selected;
          return (
            <button key={date} disabled={future} onClick={() => onPick(date)} title={info ? `${info.inCharge.join(", ") || "nobody"} in charge` : ""}
              className="rounded-md p-1 flex flex-col items-start gap-0.5 text-left disabled:opacity-30"
              style={{
                minHeight: 58,
                background: sel ? `${ACCENT.cyan}1c` : info ? "var(--panelAlt)" : "transparent",
                border: `1px solid ${sel ? ACCENT.cyan : date === today ? `${ACCENT.cyan}70` : "var(--border)"}`,
              }}>
              <span className="text-[11px] font-semibold" style={{ color: date === today ? ACCENT.cyan : "var(--text)" }}>{n}</span>
              {info && info.sales > 0 && <span className="text-[9px] font-mono leading-none" style={{ color: ACCENT.cyan }}>{peso(info.sales)}</span>}
              {info && (
                <span className="flex items-center gap-0.5 flex-wrap">
                  {info.inCharge.slice(0, 3).map((p) => (
                    <span key={p} className="text-[7px] font-bold rounded-full w-3.5 h-3.5 flex items-center justify-center"
                      style={{ background: `${colorFor(p)}30`, color: colorFor(p) }}>{initials(p)}</span>
                  ))}
                  {info.present > 0 && <span className="text-[8px]" style={{ color: "var(--muted)" }}>{info.present}👤</span>}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StorageCard({ onArchive }) {
  const [st, setSt] = useState(null);
  const load = () => api.get("/api/shop/storage").then(setSt).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!st) return null;
  const { total, free } = st.disk;
  const app = st.appTotal;
  const other = Math.max(0, total - free - app);
  const pct = (x) => `${Math.max(0.5, (x / total) * 100)}%`;
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW }} className="rounded-xl p-4 flex flex-col gap-2.5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="flex items-center gap-2 text-sm font-semibold"><HardDrive size={16} style={{ color: "var(--muted)" }} /> Disk space</span>
        <span className="text-xs" style={{ color: "var(--muted)" }}>{mb(free)} free of {mb(total)} · this system uses {mb(app)}</span>
        <button onClick={() => onArchive(load)} style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}
          className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg"><Archive size={13} /> Archive &amp; free space</button>
      </div>
      <div className="w-full h-2.5 rounded-full overflow-hidden flex" style={{ background: "var(--panelAlt)" }}>
        <div style={{ width: pct(other), background: "var(--faint)" }} title={`Other files ${mb(other)}`} />
        <div style={{ width: pct(app), background: ACCENT.cyan }} title={`This system ${mb(app)}`} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--muted)" }}>
        <span>Database {mb(st.app.database)}</span>
        <span>Receipt photos {mb(st.app.receiptPhotos + st.app.thumbnails)}</span>
        <span>Employee photos {mb(st.app.employeePhotos)}</span>
        <span>Item photos {mb(st.app.itemPhotos)}</span>
      </div>
    </div>
  );
}

function ArchiveModal({ onClose, onDone }) {
  const [before, setBefore] = useState(`${new Date().getFullYear()}-01-01`);
  const [downloaded, setDownloaded] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  async function cleanup() {
    if (!window.confirm(`Delete the receipt PHOTOS made before ${before}? The sales and all records stay. Make sure the archive ZIP is saved somewhere safe first.`)) return;
    setBusy(true);
    try { setResult(await api.post("/api/shop/archive/cleanup", { before })); onDone(); } catch { /* toast */ }
    setBusy(false);
  }
  return (
    <Modal title="Archive & free space" onClose={onClose} width={460}>
      <div className="flex flex-col gap-3.5 text-sm">
        <label className="text-xs" style={{ color: "var(--muted)" }}>Everything made before
          <input type="date" value={before} max={todayStr()} onChange={(e) => { setBefore(e.target.value); setDownloaded(false); setResult(null); }}
            className="w-full mt-1 text-sm rounded-lg px-3 py-2 outline-none" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
        </label>
        <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          <p className="text-xs font-semibold">1 · Transfer: download the archive</p>
          <p className="text-[11px]" style={{ color: "var(--muted)" }}>
            A ZIP with a full backup of the database, every record before that date as CSV (opens in Excel) and the old receipt photos.
            Save it on a USB drive or another computer.
          </p>
          <DownloadLink path={`/api/shop/archive?before=${before}`} onDone={() => setDownloaded(true)} style={{ background: ACCENT.cyan }}
            className="self-start flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-black/85"><Download size={13} /> Download archive</DownloadLink>
        </div>
        <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", opacity: downloaded ? 1 : 0.55 }}>
          <p className="text-xs font-semibold">2 · Free space: delete the old photos</p>
          <p className="text-[11px]" style={{ color: "var(--muted)" }}>
            Deletes receipt photos made before that date (only finished ones). Sales, stock, payroll and the Calendar records stay —
            they are tiny; the photos are what fill the disk.
          </p>
          <button onClick={cleanup} disabled={!downloaded || busy} style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}
            className="self-start flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold disabled:cursor-not-allowed">
            <Trash2 size={13} /> {busy ? "Deleting…" : "Delete old photos"}
          </button>
          {!downloaded && <p className="text-[10px]" style={{ color: "var(--faint)" }}>Download the archive first.</p>}
          {result && <p className="text-[11px]" style={{ color: ACCENT.cyan }}>Deleted {result.deletedFiles} file(s), freed {mb(result.freedBytes)}.</p>}
        </div>
      </div>
    </Modal>
  );
}

function SystemPasswordModal({ onClose }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [msg, setMsg] = useState("");
  async function save() {
    if (newPw !== newPw2) { setMsg("The two new passwords are not the same."); return; }
    try { await api.changeSystemPassword(oldPw, newPw); setMsg("Saved. Tell only the people you trust."); setOldPw(""); setNewPw(""); setNewPw2(""); }
    catch (e) { setMsg(e.message); }
  }
  const inp = "w-full text-sm rounded-lg px-3 py-2 outline-none";
  const st = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
  return (
    <Modal title="System password" onClose={onClose} width={380}>
      <div className="flex flex-col gap-2.5">
        <p className="text-xs" style={{ color: "var(--muted)" }}>Whoever knows it can make a desktop account. Existing accounts keep working after a change.</p>
        <PasswordInput className={inp} style={st} placeholder="current system password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="off" />
        <PasswordInput className={inp} style={st} placeholder="new system password (4+ characters)" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
        <PasswordInput className={inp} style={st} placeholder="new system password again" value={newPw2} onChange={(e) => setNewPw2(e.target.value)} autoComplete="new-password" />
        {msg && <p className="text-xs" style={{ color: msg.startsWith("Saved") ? ACCENT.cyan : ACCENT.danger }}>{msg}</p>}
        <button onClick={save} disabled={!oldPw || newPw.length < 4} style={{ background: ACCENT.cyan }} className="py-2 rounded-lg text-sm font-semibold text-black/85 disabled:opacity-50">Change</button>
      </div>
    </Modal>
  );
}

export default function CalendarPage() {
  const [monthsShown, setMonthsShown] = useState(3);
  const [days, setDays] = useState({});
  const [firstDate, setFirstDate] = useState(null);
  const [selected, setSelected] = useState(todayStr());
  const [archive, setArchive] = useState(null);         // callback to refresh the storage card
  const [pwOpen, setPwOpen] = useState(false);
  const [savedOpen, setSavedOpen] = useState(false);
  const months = useMemo(() => Array.from({ length: monthsShown }, (_, i) => monthStart(i)), [monthsShown]);

  useEffect(() => {
    const start = ymd(months[months.length - 1]);
    api.get(`/api/shop/calendar?start=${start}&end=${todayStr()}`)
      .then((d) => { setDays(Object.fromEntries(d.days.map((x) => [x.date, x]))); setFirstDate(d.firstDate); })
      .catch(() => {});
  }, [months]);
  const moreOlder = firstDate && ymd(months[months.length - 1]) > firstDate;

  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Calendar</h2>
        <div className="flex items-center gap-2">
          <button onClick={() => setSavedOpen(true)} style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan, background: `${ACCENT.cyan}0d` }}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg" title="Read data you archived / moved off this laptop">
            <FolderOpen size={13} /> Open saved data</button>
          <button onClick={() => setPwOpen(true)} style={{ border: "1px solid var(--border)", color: "var(--muted)" }}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg"><KeyRound size={13} /> System password</button>
        </div>
      </div>
      {savedOpen && <SavedDataModal onClose={() => setSavedOpen(false)} />}
      <StorageCard onArchive={(reload) => setArchive(() => reload)} />

      <div className="grid gap-5 items-start" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.15fr)" }}>
        <div style={{ border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW, maxHeight: "calc(100vh - 230px)" }}
          className="rounded-xl p-4 flex flex-col gap-6 overflow-y-auto">
          {months.map((m) => <MonthGrid key={ymd(m)} start={m} days={days} selected={selected} onPick={setSelected} />)}
          {moreOlder || monthsShown < 12 ? (
            <button onClick={() => setMonthsShown((n) => n + 3)} style={{ border: "1px dashed var(--border)", color: "var(--muted)" }}
              className="py-2 rounded-lg text-xs">Show older months</button>
          ) : null}
        </div>
        <div className="sticky top-0"><DayPanel date={selected} /></div>
      </div>

      {archive && <ArchiveModal onClose={() => setArchive(null)} onDone={() => archive && archive()} />}
      {pwOpen && <SystemPasswordModal onClose={() => setPwOpen(false)} />}
    </div>
  );
}
