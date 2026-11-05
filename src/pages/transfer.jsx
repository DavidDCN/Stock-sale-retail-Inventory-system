import React, { useRef, useState } from "react";
import { ArrowLeftRight, Download, Upload, CheckCircle2, ChevronDown, ChevronRight, Laptop, ShieldCheck, Info, Trash2, KeyRound, User as UserIcon, Lock, AlertTriangle } from "lucide-react";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { ThemedCheckbox, DatePicker, Modal, PasswordInput, DownloadLink } from "../components/kit";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// TRANSFER: move the SETUP of this shop to another laptop running the same system, or apply another laptop's
// setup here. Export = TresNinos_setup_<date>.zip (setup.json + photos). Import = read the file, see per part what is
// new / changed / the same, tick the parts, Apply. Rows are matched by name; nothing is deleted; applying the same
// file twice adds nothing twice. Sales & receipts move with Calendar -> Archive; accounts and passwords never move.
// Data: GET /api/shop/transfer/export, POST /transfer/read (file), POST /transfer/apply
// ---------------------------------------------------------------------------

const card = { border: "1px solid var(--border)", background: "var(--panel)", boxShadow: CARD_SHADOW };
const muted = { color: "var(--muted)" };
const INCLUDED = [
  "Employees with their photos, attendance, overtime / undertime, cash advances and payroll",
  "Loans & bills, maintenance, benefits (SSS, PhilHealth…) with their payments",
  "Supply: every item with prices, A-B-C levels, units and photos (stock counts optional)",
  "Trucks, orders (Add Order) with items, payments and deliveries",
  "Payments: every cash / bank payment line; cash on hand entries",
  "Optional switch: the sales (each sale with its items and the sales person)",
];

function Chip({ n, label, color }) {
  if (!n) return null;
  return <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full" style={{ color, background: `${color}1c` }}>{n} {label}</span>;
}

function SectionRow({ s, on, toggle }) {
  const [open, setOpen] = useState(false);
  const rows = [...s.new.map((x) => ({ ...x, st: "new" })), ...s.changed.map((x) => ({ ...x, st: "changed" }))];
  const nNew = s.newCount ?? s.new.length;
  const nChanged = s.changedCount ?? s.changed.length;
  const nothing = !nNew && !nChanged;
  return (
    <div className="rounded-lg" style={{ background: "var(--panelAlt)", border: `1px solid ${on ? `${ACCENT.cyan}80` : "var(--border)"}` }}>
      <div className="flex items-center gap-2.5 px-3 py-2">
        <ThemedCheckbox checked={on} onChange={toggle} />
        <button onClick={() => setOpen((o) => !o)} className="flex-1 min-w-0 flex items-center gap-2 text-left">
          {rows.length ? (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span className="w-[13px]" />}
          <span className="text-sm truncate">{s.title}</span>
        </button>
        <Chip n={nNew} label="new" color={ACCENT.cyan} />
        <Chip n={nChanged} label="changed" color={ACCENT.amber} />
        <Chip n={s.same} label="same" color="#8996A6" />
        {nothing && <span className="text-[10px]" style={muted}>already the same</span>}
      </div>
      {s.summary && s.summary.length > 0 && (
        <div className="px-3 pb-2 pl-12 flex flex-col gap-0.5">
          {s.summary.map((t) => <span key={t} className="text-[11px]" style={muted}>{t}</span>)}
        </div>
      )}
      {open && rows.length > 0 && (
        <div className="px-3 pb-2 pl-12 flex flex-col gap-0.5 max-h-56 overflow-y-auto">
          {nNew + nChanged > rows.length && <span className="text-[10px]" style={{ color: "var(--faint)" }}>showing the first {rows.length} of {nNew + nChanged}</span>}
          {rows.map((x, i) => (
            <div key={i} className="flex items-center gap-2 text-[11px]">
              <span className="w-14 shrink-0 font-semibold" style={{ color: x.st === "new" ? ACCENT.cyan : ACCENT.amber }}>{x.st}</span>
              <span className="truncate">{x.label}</span>
              {x.fields.length > 0 && <span className="truncate" style={muted}>· {x.fields.join(", ").replaceAll("_", " ")}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TransferPage() {
  const [plan, setPlan] = useState(null);
  const [picked, setPicked] = useState({});
  const [update, setUpdate] = useState(true);
  const [stock, setStock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const [withSales, setWithSales] = useState(false);
  const [salesFrom, setSalesFrom] = useState("");
  const [salesTo, setSalesTo] = useState("");
  const fileRef = useRef(null);

  async function read(file) {
    if (!file) return;
    setBusy(true); setDone(null);
    try {
      const p = await api.uploadFile("/api/shop/transfer/read", file);
      setPlan(p);
      setPicked(Object.fromEntries(p.sections.map((s) => [s.key, (s.newCount ?? s.new.length) + (s.changedCount ?? s.changed.length) > 0])));
    } catch { /* toast */ } finally { setBusy(false); }
  }
  async function apply() {
    const sections = Object.keys(picked).filter((k) => picked[k]);
    if (!sections.length) return;
    if (!window.confirm(`Apply ${sections.length} part(s) from ${plan.from || "the other laptop"} to THIS laptop? New rows are added${update ? " and matching ones updated" : ""}; nothing is deleted.`)) return;
    setBusy(true);
    try {
      const r = await api.post("/api/shop/transfer/apply", { token: plan.token, sections, updateExisting: update, stockCounts: stock });
      setDone({ ...r, titles: Object.fromEntries(plan.sections.map((s) => [s.key, s.title])) });
      setPlan(null);
    } catch { /* toast */ } finally { setBusy(false); }
  }

  return (
    <div className="p-6 flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2"><ArrowLeftRight size={18} style={{ color: ACCENT.cyan }} /> Transfer</h2>
        <p className="text-xs" style={muted}>Move this shop's setup to another laptop running the same system, or read another laptop's setup and apply it here.</p>
      </div>

      <div className="grid gap-5 items-start" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.35fr)" }}>
        <div className="rounded-xl p-5 flex flex-col gap-3" style={card}>
          <div className="flex items-center gap-2">
            <div style={{ width: 34, height: 34, borderRadius: 10, background: `${ACCENT.cyan}1c`, color: ACCENT.cyan }} className="flex items-center justify-center"><Download size={17} /></div>
            <div>
              <h3 className="text-sm font-semibold">Send this laptop's setup</h3>
              <p className="text-[11px]" style={muted}>One ZIP file to copy to the other laptop (USB drive, Messenger, Google Drive…)</p>
            </div>
          </div>
          <ul className="flex flex-col gap-1">
            {INCLUDED.map((t) => (
              <li key={t} className="text-xs flex items-start gap-2"><CheckCircle2 size={13} className="shrink-0 mt-0.5" style={{ color: "#22c55e" }} /> {t}</li>
            ))}
          </ul>
          <div className="text-[11px] rounded-lg px-3 py-2 flex items-start gap-2" style={{ background: "var(--panelAlt)", ...muted }}>
            <ShieldCheck size={13} className="shrink-0 mt-0.5" style={{ color: ACCENT.cyan }} />
            <span>Never in the file: receipt photos, passwords and the system password (a moved sales person gets a phone login
              without a password; set it in Cash Flow). Keep the file private: it has your payroll and payments.</span>
          </div>
          <div className="rounded-lg px-3 py-2.5 flex flex-col gap-2" style={{ border: `1px solid ${withSales ? ACCENT.cyan : "var(--border)"}`, background: withSales ? `${ACCENT.cyan}0c` : "transparent" }}>
            <div className="flex items-center gap-2.5">
              <button type="button" role="switch" aria-checked={withSales} onClick={() => setWithSales((v) => !v)}
                className="relative w-9 h-5 rounded-full shrink-0 transition-colors" style={{ background: withSales ? ACCENT.cyan : "var(--faint)" }}>
                <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all" style={{ left: withSales ? 18 : 2 }} />
              </button>
              <div className="flex-1">
                <div className="text-sm font-medium">Include the sales</div>
                <div className="text-[11px]" style={muted}>Every sale with its items and the sales person, so the other laptop can read and keep them (no receipt photos).</div>
              </div>
            </div>
            {withSales && (
              <div className="flex items-center gap-2 flex-wrap pl-11">
                <span className="text-[11px]" style={muted}>Only from</span>
                <div className="w-36"><DatePicker value={salesFrom} onChange={(v) => setSalesFrom(v || "")} placeholder="the first sale" /></div>
                <span className="text-[11px]" style={muted}>to</span>
                <div className="w-36"><DatePicker value={salesTo} onChange={(v) => setSalesTo(v || "")} placeholder="today" /></div>
              </div>
            )}
          </div>
          <DownloadLink path={`/api/shop/transfer/export${withSales ? `?sales=1${salesFrom ? `&salesFrom=${salesFrom}` : ""}${salesTo ? `&salesTo=${salesTo}` : ""}` : ""}`}
            className="py-2 rounded-lg text-sm font-semibold text-black/85 flex items-center justify-center gap-1.5"
            style={{ background: ACCENT.cyan }}><Download size={14} /> Make setup file{withSales ? " (with sales)" : ""}</DownloadLink>
          <ol className="text-[11px] list-decimal pl-4 flex flex-col gap-0.5" style={muted}>
            <li>Make the setup file here and copy it to the other laptop.</li>
            <li>There: menu Transfer → Read a setup file.</li>
            <li>Tick what to apply, then Apply.</li>
          </ol>
        </div>

        <div className="flex flex-col gap-5 min-w-0">
        <div className="rounded-xl p-5 flex flex-col gap-3" style={card}>
          <div className="flex items-center gap-2">
            <div style={{ width: 34, height: 34, borderRadius: 10, background: `${ACCENT.amber}1c`, color: ACCENT.amber }} className="flex items-center justify-center"><Upload size={17} /></div>
            <div className="flex-1">
              <h3 className="text-sm font-semibold">Apply another laptop's setup here</h3>
              <p className="text-[11px]" style={muted}>Read the file first: you see what is new, what changed and what is the same before anything is saved.</p>
            </div>
          </div>
          <input ref={fileRef} type="file" accept=".zip,.csv" className="hidden" onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; read(f); }} />
          <button onClick={() => fileRef.current && fileRef.current.click()} disabled={busy}
            className="py-2 rounded-lg text-sm font-semibold flex items-center justify-center gap-1.5 disabled:opacity-60" style={{ border: `1px solid ${ACCENT.amber}`, color: ACCENT.amber }}>
            <Upload size={14} /> {busy && !plan ? "Reading…" : "Read a setup file…"}
          </button>
          {/* an inventory list made in Excel (saved as CSV) is read the same way */}
          <div className="rounded-lg px-3 py-2 flex items-start gap-2 text-[11px]" style={{ background: "var(--panelAlt)", ...muted }}>
            <span className="flex-1">
              New items from a list? Get the <b style={{ color: "var(--text)" }}>inventory list</b> (your Supply as an Excel file), add rows
              (Product, Type, Unit, Selling price, Stock…), save it as <b style={{ color: "var(--text)" }}>CSV UTF-8</b>, then press Read a setup file and choose it.
            </span>
            <DownloadLink path="/api/shop/transfer/inventory-list" title="Your Supply as a CSV list (opens in Excel)"
              className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-md text-[11px] font-semibold"
              style={{ border: `1px solid ${ACCENT.cyan}70`, color: ACCENT.cyan }}><Download size={12} /> Inventory list</DownloadLink>
          </div>

          {plan && (
            <>
              <div className="rounded-lg px-3 py-2 flex items-center gap-2 text-xs" style={{ background: "var(--panelAlt)" }}>
                <Laptop size={14} style={{ color: ACCENT.cyan }} />
                <span>From <b>{plan.from || "another laptop"}</b>{plan.by ? ` (by ${plan.by})` : ""} · made {(plan.made || "").replace("T", " ").slice(0, 16)}</span>
              </div>
              <div className="flex flex-col gap-1.5">
                {plan.sections.map((s) => (
                  <SectionRow key={s.key} s={s} on={!!picked[s.key]} toggle={() => setPicked((p) => ({ ...p, [s.key]: !p[s.key] }))} />
                ))}
                {!plan.sections.length && <span className="text-xs" style={muted}>The file is empty.</span>}
              </div>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <ThemedCheckbox checked={update} onChange={() => setUpdate((u) => !u)} />
                Update the ones that already exist here (same name) with the file's values
              </label>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <ThemedCheckbox checked={stock} onChange={() => setStock((u) => !u)} />
                Also copy the stock COUNTS of items that already exist here (new items always get the file's count)
              </label>
              <div className="flex items-center gap-2 text-[11px]" style={muted}><Info size={12} /> Nothing here is deleted. Applying the same file again adds nothing twice.</div>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setPlan(null)} className="text-xs px-3 py-1.5 rounded-lg" style={{ border: "1px solid var(--border)", ...muted }}>Cancel</button>
                <button onClick={apply} disabled={busy || !Object.values(picked).some(Boolean)}
                  className="text-xs px-4 py-1.5 rounded-lg font-semibold text-black/85 disabled:opacity-50" style={{ background: ACCENT.cyan }}>
                  {busy ? "Applying…" : `Apply ${Object.values(picked).filter(Boolean).length} part(s)`}</button>
              </div>
            </>
          )}

          {done && (
            <div className="rounded-lg p-3 flex flex-col gap-1.5" style={{ border: "1px solid #22c55e70", background: "#22c55e10" }}>
              <span className="text-sm font-semibold flex items-center gap-1.5" style={{ color: "#22c55e" }}><CheckCircle2 size={15} /> Applied from {done.from || "the other laptop"}</span>
              {Object.entries(done.applied).map(([k, v]) => (
                <div key={k} className="text-xs flex gap-2">
                  <span className="flex-1 truncate">{done.titles[k] || k}</span>
                  <span style={muted}>{v.new} added · {v.updated} updated{v.lines ? ` · ${v.lines} record line(s)` : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <DeleteAllCard />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DELETE ALL RECORDS: a fresh start (after trying the mock data, or before giving the laptop away). Asks the system
// password + YOUR OWN username and password. The server first saves a full copy (database + photos) in
// backups/before_delete_all_<time>, so it can still be undone. The desktop accounts and the system password stay.
// ---------------------------------------------------------------------------
const PW_BOX = { background: "var(--panelAlt)", border: "1px solid var(--border)" };
function PwInput({ icon: Icon, type, ...props }) {
  if (type === "password") {      // the eye button shows what you typed
    return <PasswordInput icon={Icon} {...props} autoComplete="off" className="px-3 py-2 rounded-lg text-sm" style={PW_BOX} />;
  }
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={PW_BOX}>
      <Icon size={14} style={{ color: "var(--muted)" }} className="shrink-0" />
      <input {...props} type={type} className="bg-transparent outline-none text-sm w-full" style={{ color: "var(--text)" }} autoComplete="off" />
    </div>
  );
}

function DeleteAllCard() {
  const blank = { systemPassword: "", username: "", password: "", keepPhoneLogins: false, agree: false };
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const can = f.systemPassword && f.username.trim() && f.password && f.agree && !busy;
  async function run() {
    setBusy(true); setErr("");
    try {
      const r = await api.post("/api/shop/danger/delete-all", { systemPassword: f.systemPassword, username: f.username.trim(),
        password: f.password, keepPhoneLogins: f.keepPhoneLogins });
      setDone(r); setF(blank);
    } catch (e) { setErr(e.message); setF((x) => ({ ...x, password: "", systemPassword: "" })); } finally { setBusy(false); }
  }
  return (
    <div className="rounded-xl p-5 flex flex-col gap-3" style={{ ...card, border: `1px solid ${ACCENT.danger}66` }}>
      <div className="flex items-center gap-2">
        <div style={{ width: 34, height: 34, borderRadius: 10, background: `${ACCENT.danger}1c`, color: ACCENT.danger }} className="flex items-center justify-center"><Trash2 size={17} /></div>
        <div className="flex-1">
          <h3 className="text-sm font-semibold" style={{ color: ACCENT.danger }}>Delete all records</h3>
          <p className="text-[11px]" style={muted}>A fresh start: employees, sales, receipts, supply, cash flow, orders, payments… all go.
            Your sign-in account and the system password stay.</p>
        </div>
      </div>
      <button onClick={() => { setOpen(true); setErr(""); setDone(null); }} className="py-2 rounded-lg text-sm font-semibold flex items-center justify-center gap-1.5"
        style={{ border: `1px solid ${ACCENT.danger}`, color: ACCENT.danger }}><Trash2 size={14} /> Delete all records…</button>
      {open && (
        <Modal title={done ? "All records deleted" : "Delete all records"} onClose={() => { setOpen(false); if (done) window.location.reload(); }} width={440}>
          {done ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm flex items-start gap-2"><CheckCircle2 size={16} className="shrink-0 mt-0.5" style={{ color: "#22c55e" }} />
                Done. The program now starts empty{done.phoneLoginsRemoved ? `, and ${done.phoneLoginsRemoved} phone login(s) were removed` : ""}.</p>
              <p className="text-[11px] rounded-lg px-3 py-2" style={{ ...PW_BOX, color: "var(--muted)" }}>
                A full copy of everything (database + photos) was saved first in<br /><code style={{ color: ACCENT.cyan, wordBreak: "break-all" }}>{done.backup}</code><br />
                To undo it: close the program and copy that <b>inventory.db</b> back. Delete that folder yourself when you no longer need it.</p>
              <button onClick={() => window.location.reload()} className="py-2 rounded-lg text-sm font-semibold text-black/85" style={{ background: ACCENT.cyan }}>Reload the program</button>
            </div>
          ) : (
            <div className="flex flex-col gap-2.5">
              <p className="text-xs rounded-lg px-3 py-2 flex items-start gap-2" style={{ background: `${ACCENT.danger}12`, border: `1px solid ${ACCENT.danger}55` }}>
                <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: ACCENT.danger }} />
                <span>Every record in this program is deleted. Make a setup file first (left) if you may need the data again.
                  A copy is also saved in the program's backups folder before anything is deleted.</span>
              </p>
              <span className="text-[10px] font-semibold tracking-wide" style={{ color: ACCENT.cyan }}>SYSTEM PASSWORD</span>
              <PwInput icon={KeyRound} type="password" value={f.systemPassword} onChange={set("systemPassword")} placeholder="the shop's system password" autoFocus />
              <span className="text-[10px] font-semibold tracking-wide mt-1" style={{ color: ACCENT.cyan }}>YOUR OWN ACCOUNT</span>
              <PwInput icon={UserIcon} value={f.username} onChange={set("username")} placeholder="your username" />
              <PwInput icon={Lock} type="password" value={f.password} onChange={set("password")} placeholder="your password"
                onKeyDown={(e) => { if (e.key === "Enter" && can) run(); }} />
              <label className="flex items-center gap-2 text-xs cursor-pointer mt-1">
                <ThemedCheckbox checked={f.keepPhoneLogins} onChange={() => setF((x) => ({ ...x, keepPhoneLogins: !x.keepPhoneLogins }))} />
                Keep the phone logins of the sales persons
              </label>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <ThemedCheckbox checked={f.agree} onChange={() => setF((x) => ({ ...x, agree: !x.agree }))} />
                I understand that all records will be deleted
              </label>
              {err && <span className="text-xs" style={{ color: ACCENT.danger }}>{err}</span>}
              <button onClick={run} disabled={!can} className="py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-40 flex items-center justify-center gap-1.5"
                style={{ background: ACCENT.danger }}><Trash2 size={14} /> {busy ? "Deleting…" : "Delete all records"}</button>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
