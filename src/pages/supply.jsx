import React, { useState, useEffect, useRef, useMemo } from "react";
import { Plus, X, Trash2, ImagePlus, Camera, Pencil, Check, Search, FileImage, ClipboardList, Smartphone, PackageCheck, Printer, Download, ListChecks } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { ACCENT, CARD_SHADOW } from "../lib/theme";
import { Modal, DownloadLink } from "../components/kit";
import { api, imgUrl } from "../lib/api";

// Each item shows its PHOTO (uploaded here, saved on the shop PC). No photo yet = coloured initials.
const FALLBACK_COLORS = [ACCENT.cyan, ACCENT.copper, ACCENT.amber, ACCENT.danger];
function colorForName(name) {
  if (!name || name === "?") return "var(--muted)";
  const sum = [...name].reduce((s, c) => s + c.charCodeAt(0), 0);
  return FALLBACK_COLORS[sum % FALLBACK_COLORS.length];
}

// src = an image URL (saved photo) or a local preview (object URL of a picked file)
function ItemGlyph({ name, src, size = 24, box = 56 }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  const color = colorForName(name);
  if (src && !broken) {
    return <img src={src} alt="" onError={() => setBroken(true)}
      style={{ width: box, height: box, borderRadius: box * 0.25, objectFit: "cover", border: "1px solid var(--border)" }} className="shrink-0" />;
  }
  return (
    <div style={{ width: box, height: box, borderRadius: box * 0.25, background: `${color}18`, border: `1px solid ${color}45`, color }}
      className="flex items-center justify-center shrink-0">
      <span className="font-semibold" style={{ fontSize: size * 0.6 }}>{(name && name !== "?" ? name : "?").slice(0, 2).toUpperCase()}</span>
    </div>
  );
}
const productPhoto = (p) => (p.hasImage ? imgUrl(`/api/shop/supply/products/${p.id}/image`, p.imageVer) : null);

// A-B-C stock levels: A = critical (top of the dashboard Priority), B = low, C = watch
const LEVEL_COLOR = { A: ACCENT.danger, B: ACCENT.amber, C: "#e8d44d" };
const LEVEL_NAME = { A: "A · critical", B: "B · low", C: "C · watch" };
function LevelBadge({ level, small }) {
  if (!level) return null;
  const c = LEVEL_COLOR[level];
  return (
    <span className={`${small ? "text-[9px] px-1.5" : "text-[10px] px-2"} py-0.5 rounded-full font-semibold whitespace-nowrap`}
      style={{ color: c, background: `${c}1f`, border: `1px solid ${c}55` }}>{small ? level : LEVEL_NAME[level]}</span>
  );
}

// pick a photo: shows the preview, returns the File
function PhotoPicker({ name, preview, onPick, onClear }) {
  const ref = useRef(null);
  return (
    <div className="flex items-center gap-3">
      <button type="button" onClick={() => ref.current && ref.current.click()} title="Choose a photo"
        style={{ width: 84, height: 84, borderRadius: 18, border: `1.5px dashed ${preview ? "transparent" : "var(--faint)"}`, background: "var(--panelAlt)" }}
        className="flex items-center justify-center overflow-hidden shrink-0">
        {preview ? <img src={preview} alt="" className="w-full h-full object-cover" /> : <ImagePlus size={26} style={{ color: "var(--muted)" }} />}
      </button>
      <div className="flex flex-col gap-1 text-xs">
        <button type="button" onClick={() => ref.current && ref.current.click()} style={{ color: ACCENT.cyan }} className="font-medium text-left">
          {preview ? "Change photo" : "Upload photo"}
        </button>
        {preview && <button type="button" onClick={onClear} style={{ color: "var(--muted)" }} className="text-left">Remove</button>}
        <span className="text-[10px]" style={{ color: "var(--faint)" }}>Optional. Without a photo the card shows “{(name || "?").slice(0, 2).toUpperCase()}”.</span>
      </div>
      <input ref={ref} type="file" accept="image/*" className="hidden"
        onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) onPick(f); }} />
    </div>
  );
}

const INPUT = { background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" };
const peso = (n) => `₱${(Number(n) || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

// spec | unit | opening stock | sell price | standard cost   (numbers are kept as text while typing so a box can be empty)
const TYPE_COLS = "minmax(0,1fr) 56px 64px 64px 64px 22px";
const blankType = () => ({ id: `n${Date.now()}${Math.random().toString(36).slice(2, 6)}`, spec: "", unit: "", qty: "", qtyUnknown: false,
  sellPrice: "", costPrice: "", levelA: "", levelB: "", levelC: "" });
const lvl = (v) => (String(v ?? "").trim() === "" ? null : Number(v));
const typeHasData = (t) => t.qtyUnknown || [t.spec, t.unit, t.qty, t.sellPrice, t.costPrice].some((v) => String(v ?? "").trim() !== "");
// qtyUnknown = "N/A (not sure yet)": saved without a stock count; the dashboard Priority asks for it until it is typed
const typePayload = (t) => ({ spec: t.spec.trim(), unit: t.unit.trim() || "pc", qty: t.qtyUnknown ? 0 : Number(t.qty) || 0,
  qtyUnknown: !!t.qtyUnknown, sellPrice: Number(t.sellPrice) || 0, costPrice: Number(t.costPrice) || 0,
  levelA: lvl(t.levelA), levelB: lvl(t.levelB), levelC: lvl(t.levelC) });
const NA_COLOR = ACCENT.amber;
function NaBadge({ small }) {
  return (
    <span className={`${small ? "text-[9px] px-1.5" : "text-[10px] px-2"} py-0.5 rounded-full font-semibold whitespace-nowrap`} title="Stock not counted yet (N/A)"
      style={{ color: NA_COLOR, background: `${NA_COLOR}1f`, border: `1px solid ${NA_COLOR}55` }}>{small ? "N/A" : "N/A · count it"}</span>
  );
}
// every typed word must appear in the item name or one of its types ("cyc 5ft" finds Cyclone Wire 5 Ft. ...)
function matchesSearch(item, q) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [item.name, ...item.types.map((t) => `${t.spec} ${t.unit}`)].join(" ").toLowerCase().replace(/\s+/g, " ");
  return words.every((w) => hay.includes(w));
}

// the three alert levels (stock at or below the number). Used when adding and in the item profile.
function LevelInputs({ value, onChange, onBlur, unit }) {
  const field = (k, letter) => (
    <label className="flex items-center gap-1 text-[10px] font-semibold" style={{ color: LEVEL_COLOR[letter] }} title={LEVEL_NAME[letter]}>
      {letter} ≤
      <input type="number" min={0} value={value[k] ?? ""} placeholder="–" onChange={(e) => onChange(k, e.target.value)} onBlur={onBlur}
        className="w-14 text-xs rounded-md px-1.5 py-1 outline-none font-normal" style={INPUT} />
    </label>
  );
  return (
    <div className="flex items-center gap-2.5 flex-wrap">
      <span className="text-[10px]" style={{ color: "var(--muted)" }}>Alert when stock{unit ? ` (${unit})` : ""} is</span>
      {field("levelA", "A")}{field("levelB", "B")}{field("levelC", "C")}
    </div>
  );
}

function TypeHeader() {
  return (
    <div className="grid gap-2 px-0.5 text-[10px] font-medium" style={{ gridTemplateColumns: TYPE_COLS, color: "var(--muted)" }}>
      <span>Type / spec</span><span>Unit</span><span>Stock</span><span>Sell ₱</span><span title="Standard (benchmark) cost per unit. Empty = 65% of the selling price">Cost ₱</span><span />
    </div>
  );
}

function TypeRow({ type, onChange, onRemove, canRemove = true }) {
  const num = (k) => (e) => onChange({ ...type, [k]: e.target.value });
  return (
    <div className="grid gap-2 items-center" style={{ gridTemplateColumns: TYPE_COLS }}>
      <input value={type.spec} onChange={(e) => onChange({ ...type, spec: e.target.value })}
        placeholder="e.g. 40kg bag (blank = none)" className="text-xs rounded-md px-2.5 py-2 outline-none min-w-0" style={INPUT} />
      <input value={type.unit} onChange={(e) => onChange({ ...type, unit: e.target.value })}
        placeholder="kg, pcs" className="text-xs rounded-md px-2 py-2 outline-none min-w-0" style={INPUT} />
      {type.qtyUnknown ? (
        <button type="button" onClick={() => onChange({ ...type, qtyUnknown: false })} title="Not counted yet. Click to type the stock instead"
          className="text-xs rounded-md px-2 py-2 font-semibold min-w-0" style={{ ...INPUT, color: NA_COLOR, borderColor: `${NA_COLOR}80` }}>N/A</button>
      ) : (
        <input type="number" min={0} value={type.qty} onChange={num("qty")}
          placeholder="0" title="Opening stock" className="text-xs rounded-md px-2 py-2 outline-none min-w-0" style={INPUT} />
      )}
      <input type="number" min={0} value={type.sellPrice} onChange={num("sellPrice")}
        placeholder="0" title="Selling price per unit" className="text-xs rounded-md px-2 py-2 outline-none min-w-0" style={INPUT} />
      <input type="number" min={0} value={type.costPrice} onChange={num("costPrice")}
        placeholder={Number(type.sellPrice) > 0 ? String(Math.round(Number(type.sellPrice) * 65) / 100) : "65%"}
        title="Standard (benchmark) cost per unit. Left empty = 65% of the selling price" className="text-xs rounded-md px-2 py-2 outline-none min-w-0" style={INPUT} />
      {canRemove
        ? <button onClick={onRemove} style={{ color: "var(--muted)" }} className="hover:text-current"><X size={14} /></button>
        : <span />}
      <div style={{ gridColumn: "1 / -1" }} className="pl-1 -mt-0.5 pb-1.5 flex flex-col gap-1.5" >
        <label className="flex items-center gap-1.5 text-[10px] cursor-pointer select-none" style={{ color: type.qtyUnknown ? NA_COLOR : "var(--muted)" }}
          title="Not sure how many there are yet? Save it with N/A; the dashboard Priority keeps asking for the count until you type it">
          <input type="checkbox" checked={!!type.qtyUnknown} onChange={(e) => onChange({ ...type, qtyUnknown: e.target.checked })} />
          Stock not sure yet — N/A (count it later; Priority reminds you)
        </label>
        <LevelInputs value={type} unit={type.unit} onChange={(k, v) => onChange({ ...type, [k]: v })} />
      </div>
    </div>
  );
}

// internal (we make it) vs external (we buy it): unit cost + hauling + upkeep per unit, cheaper one is highlighted
function SourcingModal({ type, onClose }) {
  const empty = { unitCost: 0, transferCost: 0, maintenanceCost: 0 };
  const [data, setData] = useState({ internal: empty, external: empty });
  const [cheaper, setCheaper] = useState(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get(`/api/shop/supply/items/${type.id}/sourcing`).then((d) => {
      setData({ internal: d.internal || empty, external: d.external || empty });
      setCheaper(d.cheaper);
    }).catch((e) => setMsg(e.message));
    // eslint-disable-next-line
  }, [type.id]);
  const set = (src, k, v) => setData((d) => ({ ...d, [src]: { ...d[src], [k]: Number(v) } }));
  async function save() {
    try {
      const d = await api.put(`/api/shop/supply/items/${type.id}/sourcing`, { internal: data.internal, external: data.external });
      setCheaper(d.cheaper); setMsg("Saved.");
    } catch (e) { setMsg(e.message); }
  }
  const fields = [["unitCost", "Cost per unit"], ["transferCost", "Hauling per unit"], ["maintenanceCost", "Upkeep per unit"]];
  const landed = (x) => x.unitCost + x.transferCost + x.maintenanceCost;
  return (
    <Modal title={`Sourcing — ${type.spec || "item"}`} onClose={onClose} width={440}>
      <div className="grid grid-cols-2 gap-4">
        {[["internal", "Internal (we make it)"], ["external", "External (we buy it)"]].map(([src, title]) => (
          <div key={src} className="rounded-lg p-3 flex flex-col gap-2"
            style={{ border: `1px solid ${cheaper === src ? ACCENT.cyan : "var(--border)"}`, background: "var(--panelAlt)" }}>
            <p className="text-xs font-semibold">{title}</p>
            {fields.map(([k, label]) => (
              <label key={k} className="text-[10px]" style={{ color: "var(--muted)" }}>{label}
                <input type="number" value={data[src][k]} onChange={(e) => set(src, k, e.target.value)}
                  className="w-full text-xs rounded-md px-2 py-1.5 mt-0.5 outline-none" style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
              </label>
            ))}
            <p className="text-[11px] font-mono" style={{ color: cheaper === src ? ACCENT.cyan : "var(--muted)" }}>
              Total {peso(landed(data[src]))}{cheaper === src ? " · cheaper" : ""}
            </p>
          </div>
        ))}
      </div>
      {msg && <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>{msg}</p>}
      <button onClick={save} style={{ background: ACCENT.cyan }} className="mt-3 w-full py-2.5 rounded-lg text-sm font-semibold text-black/85">Save</button>
    </Modal>
  );
}

// the stock count: type the real number, then ✓ (or Enter). Esc puts the old number back.
// An item saved with N/A shows an empty amber box: the first count typed here makes it a normal item.
function QtyEditor({ t, onSet }) {
  const shown = t.qtyUnknown ? "" : String(t.qty);
  const [v, setV] = useState(shown);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setV(shown); }, [shown]);
  const n = Number(v);
  const changed = v.trim() !== "" && Number.isFinite(n) && n >= 0 && (t.qtyUnknown || n !== t.qty);
  async function apply() {
    if (!changed || busy) return;
    setBusy(true);
    try { await onSet(n); } finally { setBusy(false); }
  }
  return (
    <div className="flex items-center gap-1">
      <input type="number" min={0} step="any" value={v} onChange={(e) => setV(e.target.value)} title="Type the real count, then press ✓ or Enter"
        onKeyDown={(e) => { if (e.key === "Enter") apply(); if (e.key === "Escape") setV(shown); }} placeholder={t.qtyUnknown ? "N/A" : ""}
        className="w-20 text-sm font-mono text-right rounded-md px-2 py-0.5 outline-none"
        style={{ ...INPUT, borderColor: changed || t.qtyUnknown ? ACCENT.amber : "var(--border)" }} />
      <span style={{ color: "var(--muted)", fontSize: 11 }} className="w-9 truncate">{t.unit}</span>
      {changed && (
        <button onClick={apply} disabled={busy} title="Apply the new count" style={{ background: ACCENT.cyan }}
          className="h-6 px-2 rounded-md flex items-center gap-1 text-[11px] font-semibold text-black/85 disabled:opacity-50"><Check size={12} /> Apply</button>
      )}
    </div>
  );
}

// one variant inside the item profile: stock correction, prices, buying stock, sourcing
function TypeProfileRow({ t, onSetQty, onChanged, onDelete }) {
  const [sell, setSell] = useState(t.sellPrice);
  const [cost, setCost] = useState(t.costPrice);
  const [buyQty, setBuyQty] = useState("");
  const [buyCost, setBuyCost] = useState("");
  const [source, setSource] = useState("external");
  const [open, setOpen] = useState(false);
  const [sourcing, setSourcing] = useState(false);
  const [saved, setSaved] = useState(null);
  const [msg, setMsg] = useState("");
  const [levels, setLevels] = useState({ levelA: t.levelA, levelB: t.levelB, levelC: t.levelC });
  const [spec, setSpec] = useState(t.spec);
  const [unit, setUnit] = useState(t.unit);
  const [savedMsg, setSavedMsg] = useState("");
  useEffect(() => { setSell(t.sellPrice); setCost(t.costPrice); setSpec(t.spec); setUnit(t.unit); }, [t.sellPrice, t.costPrice, t.spec, t.unit]);
  useEffect(() => { setLevels({ levelA: t.levelA, levelB: t.levelB, levelC: t.levelC }); }, [t.levelA, t.levelB, t.levelC]);
  const dirty = spec !== t.spec || unit !== t.unit || Number(sell) !== t.sellPrice || Number(cost) !== t.costPrice ||
    lvl(levels.levelA) !== t.levelA || lvl(levels.levelB) !== t.levelB || lvl(levels.levelC) !== t.levelC;

  // one Save for everything in Edit: name of the type, unit, selling price, standard cost, A-B-C levels
  async function saveEdit() {
    try {
      await api.patch(`/api/shop/supply/items/${t.id}`, {
        spec: spec.trim(), unit: unit.trim() || t.unit, sellPrice: Number(sell) || 0, costPrice: Number(cost) || 0,
        levelA: lvl(levels.levelA), levelB: lvl(levels.levelB), levelC: lvl(levels.levelC),
      });
      setMsg(""); setSavedMsg("Saved."); setTimeout(() => setSavedMsg(""), 2500); onChanged();
    } catch (e) { setMsg(e.message); }
  }
  async function buy() {
    const q = Number(buyQty), c = Number(buyCost);
    if (!(q > 0) || !(c >= 0) || buyCost === "") return;
    try {
      const d = await api.post(`/api/shop/supply/items/${t.id}/purchase`, { qty: q, unitCost: c, source });
      setSaved(d.purchase.savings); setBuyQty(""); setBuyCost(""); setMsg(""); onChanged();
    } catch (e) { setMsg(e.message); }
  }
  const preview = buyQty !== "" && buyCost !== "" ? (Number(cost) - Number(buyCost)) * Number(buyQty) : null;
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--panelAlt)" }} className="rounded-lg px-3 py-2.5 text-sm">
      <div className="flex items-center justify-between">
        <button onClick={() => setOpen((o) => !o)} className="truncate min-w-0 text-left flex items-center gap-2">
          {t.spec || "(no spec)"} <LevelBadge level={t.level} />{t.qtyUnknown && <NaBadge />}
        </button>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => setOpen((o) => !o)} title="Edit prices, A-B-C levels, buy stock"
            style={{ border: `1px solid ${open ? ACCENT.cyan : "var(--border)"}`, color: open ? ACCENT.cyan : "var(--text)" }}
            className="h-6 px-2 rounded-md flex items-center gap-1 text-[11px] font-medium hover:bg-white/5"><Pencil size={11} /> Edit</button>
          <QtyEditor t={t} onSet={onSetQty} />
        </div>
      </div>
      {t.qtyUnknown && (
        <p className="text-[11px] mt-1" style={{ color: NA_COLOR }}>Stock not counted yet (N/A). Type how many there are in the box and press Apply.</p>
      )}
      <p className="text-[10px] mt-1" style={{ color: "var(--faint)" }}>
        Sell ₱{t.sellPrice} · standard cost ₱{t.costPrice}{t.costAuto ? " (65% of the selling price — no cost given)" : ""}{t.levelA != null || t.levelB != null || t.levelC != null
          ? ` · alert A ≤ ${t.levelA ?? "–"}, B ≤ ${t.levelB ?? "–"}, C ≤ ${t.levelC ?? "–"}` : " · no alert levels yet"}. The count box only corrects a wrong count; stock you bought goes in Edit → Buy stock.
      </p>
      {open && (
        <div className="mt-2.5 flex flex-col gap-2.5 pt-2.5" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="rounded-md p-2.5 flex flex-col gap-2" style={{ border: `1px solid ${ACCENT.cyan}55` }}>
            <p className="text-[11px] font-semibold" style={{ color: ACCENT.cyan }}>Edit this type</p>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[10px]" style={{ color: "var(--muted)" }}>Type / spec
                <input value={spec} onChange={(e) => setSpec(e.target.value)} placeholder="e.g. 40kg bag"
                  className="w-full text-xs rounded-md px-2 py-1.5 mt-0.5 outline-none" style={INPUT} /></label>
              <label className="text-[10px]" style={{ color: "var(--muted)" }}>Unit
                <input value={unit} onChange={(e) => setUnit(e.target.value)}
                  className="w-full text-xs rounded-md px-2 py-1.5 mt-0.5 outline-none" style={INPUT} /></label>
              <label className="text-[10px]" style={{ color: "var(--muted)" }}>Sell price / {unit || t.unit}
                <input type="number" value={sell} onChange={(e) => setSell(e.target.value)}
                  className="w-full text-xs rounded-md px-2 py-1.5 mt-0.5 outline-none" style={INPUT} /></label>
              <label className="text-[10px]" style={{ color: "var(--muted)" }}>Standard cost / {unit || t.unit}{t.costAuto && Number(cost) === t.costPrice ? " · 65% of sell (assumed)" : ""}
                <input type="number" value={cost} onChange={(e) => setCost(e.target.value)} title="Your real cost. Empty or 0 = 65% of the selling price"
                  className="w-full text-xs rounded-md px-2 py-1.5 mt-0.5 outline-none" style={INPUT} /></label>
            </div>
            <LevelInputs value={levels} unit={unit || t.unit} onChange={(k, v) => setLevels((x) => ({ ...x, [k]: v }))} />
            <p className="text-[10px]" style={{ color: "var(--faint)" }}>
              At or below A it goes to the TOP of the dashboard Priority, then B, then C. Leave a box empty to skip that level.
              New prices count from now on; past sales keep the price they were sold at.
            </p>
            <div className="flex items-center gap-2">
              <button onClick={saveEdit} disabled={!dirty} style={{ background: ACCENT.cyan }}
                className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-semibold text-black/85 disabled:opacity-40"><Check size={12} /> Save changes</button>
              {savedMsg && <span className="text-[11px]" style={{ color: ACCENT.cyan }}>{savedMsg}</span>}
            </div>
          </div>
          <div>
            <p className="text-[10px] mb-1" style={{ color: "var(--muted)" }}>Buy stock</p>
            <div className="flex gap-1.5">
              <input type="number" value={buyQty} onChange={(e) => setBuyQty(e.target.value)} placeholder="qty"
                className="w-16 text-xs rounded-md px-2 py-1.5 outline-none" style={INPUT} />
              <input type="number" value={buyCost} onChange={(e) => setBuyCost(e.target.value)} placeholder={`paid / ${t.unit}`}
                className="flex-1 min-w-0 text-xs rounded-md px-2 py-1.5 outline-none" style={INPUT} />
              <select value={source} onChange={(e) => setSource(e.target.value)} className="text-xs rounded-md px-1.5 py-1.5 outline-none" style={INPUT}>
                <option value="external">outside</option><option value="internal">own</option>
              </select>
              <button onClick={buy} style={{ background: ACCENT.cyan }} className="px-3 rounded-md text-xs font-semibold text-black/85">Add</button>
            </div>
            {preview !== null && (
              <p className="text-[10px] mt-1" style={{ color: preview >= 0 ? ACCENT.cyan : ACCENT.danger }}>
                {preview >= 0 ? `Saves ${peso(preview)} vs the standard cost` : `Costs ${peso(-preview)} more than the standard cost`}
              </p>
            )}
            {saved !== null && <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>Last purchase recorded. Saving: {peso(saved)}.</p>}
          </div>
          {!!t.savings && (
            <p className="text-[11px] font-mono" style={{ color: t.savings > 0 ? ACCENT.cyan : ACCENT.danger }}>
              {t.savings > 0 ? `Extra profit from buying below standard: ${peso(t.savings)}` : `Paid ${peso(-t.savings)} above standard so far`}
            </p>
          )}
          <div className="flex items-center justify-between">
            <button onClick={() => setSourcing(true)} style={{ color: ACCENT.cyan }} className="text-[11px] font-medium text-left">Sourcing costs (own vs outside supplier) →</button>
            <button onClick={onDelete} title="Delete this type (only if it was never sold)" style={{ color: ACCENT.danger }}
              className="text-[11px] flex items-center gap-1"><Trash2 size={12} /> Delete type</button>
          </div>
          {msg && <p className="text-[10px]" style={{ color: ACCENT.danger }}>{msg}</p>}
        </div>
      )}
      {sourcing && <SourcingModal type={t} onClose={() => setSourcing(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// NEW ITEMS FROM THE PHONES (round 17): a sales person marked a "not sold in this shop" row as a new item (name, price,
// how many the customer bought, how many are left). Accept = it is added to Supply (or linked to an item already here)
// and goes back on the receipt: the sales person sells it with Update on the phone (Cash or Bank); Remove = nothing. Data: GET /api/shop/requests,
// POST /requests/{id}/accept, DELETE /requests/{id}. The dashboard has a card for each until it is handled.
// ---------------------------------------------------------------------------
function RequestCard({ req, onAccept, onRemove }) {
  return (
    <div className="rounded-lg px-3 py-2.5 flex items-start gap-3" style={{ background: "var(--panel)", border: `1px solid ${ACCENT.cyan}55`, boxShadow: CARD_SHADOW }}>
      <Smartphone size={16} style={{ color: ACCENT.cyan }} className="shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold truncate">{req.name}</p>
        <p className="text-[11px]" style={{ color: "var(--muted)" }}>
          {req.qty} {req.unit} × {peso(req.price)} = <span className="font-mono">{peso(req.amount)}</span>
          {" · "}{req.by || "a phone"}{req.receiptLabel ? ` · receipt ${req.receiptLabel}` : ""}{req.byHand ? " · typed on the phone" : ""}
          {req.stock != null ? <span style={{ color: ACCENT.cyan }}> · says {req.stock} left</span> : null}</p>
        {!req.byHand && req.ocr && <p className="text-[10px] truncate" style={{ color: "var(--faint)" }} title={req.ocr}>read: “{req.ocr}”</p>}
        {req.maybe && <p className="text-[10px]" style={{ color: ACCENT.amber }}>Maybe it is already here: {req.maybe.name}</p>}
      </div>
      <div className="flex flex-col gap-1.5 shrink-0">
        <button onClick={onAccept} className="text-[11px] font-semibold px-3 py-1 rounded-md text-black/85 flex items-center gap-1" style={{ background: ACCENT.cyan }}>
          <PackageCheck size={12} /> Accept</button>
        <button onClick={onRemove} className="text-[11px] px-3 py-1 rounded-md" style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}>Remove</button>
      </div>
    </div>
  );
}

function AcceptRequestModal({ req, products, onClose, onDone }) {
  const [how, setHow] = useState(req.maybe ? "link" : "new");
  const [linkId, setLinkId] = useState(req.maybe ? String(req.maybe.itemId) : "");
  const [name, setName] = useState(req.name || "");
  const [unit, setUnit] = useState(req.unit || "");
  const [sell, setSell] = useState(req.price ? String(req.price) : "");
  const [cost, setCost] = useState("");
  // the sales person may have said how many are left: the owner approves (or changes) it here
  const [na, setNa] = useState(req.stock == null);
  const [stock, setStock] = useState(req.stock == null ? "" : String(req.stock));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const types = useMemo(() => products.flatMap((p) => p.types.map((t) => ({ id: t.id, label: `${p.name}${t.spec ? ` ${t.spec}` : ""}`,
    unit: t.unit, sell: t.sellPrice }))).sort((a, b) => a.label.localeCompare(b.label)), [products]);
  const ok = how === "link" ? !!linkId : name.trim().length >= 2 && Number(sell) > 0 && (na || stock.trim() !== "");
  async function accept() {
    if (!ok || busy) return;
    setBusy(true); setErr("");
    const body = how === "link" ? { itemId: Number(linkId) }
      : { name: name.trim(), unit: unit.trim(), sellPrice: Number(sell), costPrice: Number(cost) || null,
        stock: na ? null : Number(stock) };
    try { onDone(await api.post(`/api/shop/requests/${req.id}/accept`, body)); } catch (e) { setErr(e.message); }
    setBusy(false);
  }
  const lab = "text-[11px] font-medium mb-1 block";
  return (
    <Modal title="Accept the new item" onClose={onClose} width={500}>
      <div className="flex flex-col gap-3">
        <div className="rounded-lg px-3 py-2 text-[11px] flex items-start gap-2" style={{ border: `1px solid ${ACCENT.cyan}55`, background: `${ACCENT.cyan}0d` }}>
          <Smartphone size={14} style={{ color: ACCENT.cyan }} className="shrink-0 mt-0.5" />
          <span style={{ color: "var(--muted)" }}>
            <b style={{ color: "var(--text)" }}>{req.by || "A sales person"}</b> sold <b style={{ color: "var(--text)" }}>{req.qty} {req.unit} {req.name}</b> at {peso(req.price)} each
            {" "}(= {peso(req.amount)}){req.receiptLabel ? <> on receipt <a href={imgUrl(`/api/shop/receipts/${req.receiptId}/image`)} target="_blank" rel="noreferrer" className="underline">{req.receiptLabel}</a></> : null}.
            {!req.byHand && req.read ? <> The reader read “{req.read}”.</> : null}
          </span>
        </div>

        <div className="flex rounded-lg p-0.5 self-start" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)" }}>
          {[["new", "Add it as a new item"], ["link", "It is already in Supply"]].map(([k, l]) => (
            <button key={k} type="button" onClick={() => setHow(k)} className="text-xs px-3 py-1.5 rounded-md font-semibold"
              style={how === k ? { background: ACCENT.cyan, color: "#000" } : { color: "var(--muted)" }}>{l}</button>
          ))}
        </div>

        {how === "link" ? (
          <div>
            <label className={lab} style={{ color: "var(--muted)" }}>Which Supply item is it?</label>
            <select value={linkId} onChange={(e) => setLinkId(e.target.value)} className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT}>
              <option value="">choose the item…</option>
              {types.map((t) => <option key={t.id} value={t.id}>{t.label} — {peso(t.sell)} / {t.unit}</option>)}
            </select>
            <p className="text-[10px] mt-1" style={{ color: "var(--faint)" }}>When the sales person presses Update it is sold at that item's Supply price, and its stock goes down by {req.qty}.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className={lab} style={{ color: "var(--muted)" }}>Name in Supply</label>
              <input value={name} onChange={(e) => setName(e.target.value)} className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT} />
            </div>
            <div>
              <label className={lab} style={{ color: "var(--muted)" }}>Unit</label>
              <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="pcs, bags, kg…" className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT} />
            </div>
            <div>
              <label className={lab} style={{ color: "var(--muted)" }}>Selling price (₱ per {unit || "unit"})</label>
              <input type="number" min={0} value={sell} onChange={(e) => setSell(e.target.value)} className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT} />
            </div>
            <div>
              <label className={lab} style={{ color: "var(--muted)" }}>Cost ₱ (empty = 65%)</label>
              <input type="number" min={0} value={cost} onChange={(e) => setCost(e.target.value)}
                placeholder={Number(sell) > 0 ? String(Math.round(Number(sell) * 65) / 100) : "65%"} className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT} />
            </div>
            <div>
              <label className={lab} style={{ color: "var(--muted)" }}>Left in the shop (after this sale){req.stock != null ? ` — ${req.by || "the phone"} says ${req.stock}` : ""}</label>
              {na ? (
                <button type="button" onClick={() => setNa(false)} className="w-full text-sm rounded-lg px-3 py-2 font-semibold text-left"
                  style={{ ...INPUT, color: NA_COLOR, borderColor: `${NA_COLOR}80` }} title="Click to type the count">N/A — count it later</button>
              ) : (
                <input type="number" min={0} value={stock} onChange={(e) => setStock(e.target.value)} autoFocus placeholder="how many are left"
                  className="w-full text-sm rounded-lg px-3 py-2 outline-none" style={INPUT} />
              )}
            </div>
            <label className="col-span-2 flex items-center gap-1.5 text-[10px] cursor-pointer select-none -mt-1" style={{ color: na ? NA_COLOR : "var(--muted)" }}>
              <input type="checkbox" checked={na} onChange={(e) => setNa(e.target.checked)} />
              Stock not counted yet — N/A (the dashboard Priority asks for the count)
            </label>
          </div>
        )}

        <div className="rounded-lg px-3 py-2 text-[11px]" style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--muted)" }}>
          Accepting does <b style={{ color: "var(--text)" }}>not</b> sell it yet: it goes back on {req.by || "the sales person"}'s receipt as a normal row.
          {" "}{req.by || "The sales person"} presses <b style={{ color: "var(--text)" }}>Update</b> on the phone to sell it and says Cash or Bank.
        </div>
        {err && <p className="text-xs rounded-lg px-3 py-2" style={{ color: ACCENT.danger, border: `1px solid ${ACCENT.danger}55` }}>{err}</p>}
        <button onClick={accept} disabled={!ok || busy} style={{ background: ok ? ACCENT.cyan : "var(--faint)" }}
          className="py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:cursor-not-allowed">{busy ? "Saving…" : "Accept"}</button>
      </div>
    </Modal>
  );
}

// ---- Restock list: the A / B / C items, printed (or saved for Excel) at the end of the day to buy them again ----
const fmtQty = (v) => (v === null || v === undefined ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
const fmtPeso = (v) => (v === null || v === undefined ? "" : `₱${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const sumTotal = (xs) => xs.reduce((s, x) => s + (x.total || 0), 0);

function restockHtml(items, at) {
  const when = new Date(at || Date.now()).toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" });
  const part = (lv) => {
    const rows = items.filter((x) => x.level === lv);
    if (!rows.length) return "";
    return `<h2>${esc(LEVEL_NAME[lv])} <small>(${rows.length})</small></h2>
<table><thead><tr><th class="box"></th><th>Item</th><th class="n">Stock now</th><th class="n">Buy (suggested)</th><th class="n">Standard cost</th>
<th class="n">Total</th><th class="w">Bought</th><th class="w">Price paid</th></tr></thead><tbody>
${rows.map((x) => `<tr><td class="box">&#9744;</td><td>${esc(x.name)}</td><td class="n">${esc(fmtQty(x.quantity))} ${esc(x.unit)}</td>
<td class="n">${x.buy ? `${esc(fmtQty(x.buy))} ${esc(x.unit)}` : ""}</td><td class="n">${esc(fmtPeso(x.costPrice))}</td>
<td class="n">${esc(fmtPeso(x.total))}</td><td class="w"></td><td class="w"></td></tr>`).join("\n")}
<tr class="sum"><td></td><td colspan="4">Total ${esc(LEVEL_NAME[lv])}</td><td class="n">${esc(fmtPeso(sumTotal(rows)))}</td><td class="w"></td><td class="w"></td></tr>
</tbody></table>`;
  };
  return `<!doctype html><html><head><meta charset="utf-8"><title>Restock list</title><style>
body{font-family:Segoe UI,Arial,sans-serif;color:#000;margin:18px;font-size:12px}
h1{font-size:18px;margin:0}p.sub{margin:2px 0 10px;color:#444}
h2{font-size:14px;margin:14px 0 4px}h2 small{font-weight:normal;color:#555}
table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #888;padding:5px 6px;text-align:left;vertical-align:top}
th{background:#eee}td.n,th.n{text-align:right;width:96px}td.box,th.box{width:22px;text-align:center}td.w,th.w{width:76px}
tr.sum td{font-weight:bold;background:#f6f6f6}p.grand{font-size:14px;font-weight:bold;text-align:right;margin:14px 0 0}
tr{page-break-inside:avoid}@page{margin:12mm}
</style></head><body><h1>Tres Ni&ntilde;os Hardware &mdash; Restock list</h1>
<p class="sub">${esc(when)} &middot; ${items.length} item${items.length === 1 ? "" : "s"} &middot; A = critical, B = low, C = watch.
Buy (suggested) = enough to be above the item's highest level again. Total = buy &times; standard cost.</p>
${part("A")}${part("B")}${part("C")}
${items.length ? `<p class="grand">Total to buy: ${esc(fmtPeso(sumTotal(items)))}</p>` : "<p>Nothing to restock: every item is above its levels.</p>"}
</body></html>`;
}

// print through a hidden frame (works in the browser and in the .exe window)
function printHtml(html) {
  const f = document.createElement("iframe");
  f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  document.body.appendChild(f);
  const doc = f.contentWindow.document;
  doc.open();
  doc.write(html);
  doc.close();
  setTimeout(() => {
    try { f.contentWindow.focus(); f.contentWindow.print(); } finally { setTimeout(() => f.remove(), 60000); }
  }, 250);
}

function RestockModal({ onClose }) {
  const [data, setData] = useState(null);
  const [lv, setLv] = useState({ A: true, B: true, C: true });
  useEffect(() => { api.get("/api/shop/restock").then(setData).catch(() => setData({ items: [], error: true })); }, []);
  const all = data ? data.items : [];
  const shown = all.filter((x) => lv[x.level]);
  const count = (l) => all.filter((x) => x.level === l).length;
  const th = "font-medium px-2 py-1.5";
  return (
    <Modal title="Restock list — A / B / C" onClose={onClose} width={820}>
      <div className="flex flex-col gap-3">
        <p className="text-[11px]" style={{ color: "var(--muted)" }}>
          The items at stock level A (critical), B (low) or C (watch). Print it at the end of the day and take it when buying.
          Total = buy × the standard cost. Set the levels and the cost of an item in its profile.
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          {["A", "B", "C"].map((l) => (
            <button key={l} onClick={() => setLv((v) => ({ ...v, [l]: !v[l] }))}
              className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
              style={lv[l] ? { background: LEVEL_COLOR[l], color: "#000" } : { color: LEVEL_COLOR[l], border: `1px solid ${LEVEL_COLOR[l]}70` }}>
              {LEVEL_NAME[l]} ({count(l)})
            </button>
          ))}
        </div>
        <div className="rounded-lg overflow-y-auto" style={{ border: "1px solid var(--border)", maxHeight: "46vh" }}>
          {!data && <p className="text-xs p-4" style={{ color: "var(--faint)" }}>Loading…</p>}
          {data && shown.length === 0 && <p className="text-xs p-4" style={{ color: "var(--faint)" }}>Nothing to restock{all.length ? " at the chosen levels" : ""}.</p>}
          {shown.length > 0 && (
            <table className="w-full text-xs">
              <thead className="sticky top-0"><tr style={{ color: "var(--muted)", background: "var(--panelAlt)" }}>
                <th className={`text-left pl-3 ${th}`}>Level</th><th className={`text-left ${th}`}>Item</th>
                <th className={`text-right ${th}`}>Stock now</th><th className={`text-right ${th}`}>Buy (suggested)</th>
                <th className={`text-right ${th}`}>Standard cost</th><th className={`text-right pr-3 ${th}`}>Total</th>
              </tr></thead>
              <tbody>
                {shown.map((x) => (
                  <tr key={x.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="pl-3 px-2 py-1.5"><LevelBadge level={x.level} small /></td>
                    <td className="px-2 py-1.5">{x.name}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">{fmtQty(x.quantity)} {x.unit}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">{x.buy ? `${fmtQty(x.buy)} ${x.unit}` : "—"}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap" style={{ color: "var(--muted)" }}>{fmtPeso(x.costPrice) || "—"}</td>
                    <td className="px-2 pr-3 py-1.5 text-right whitespace-nowrap font-semibold">{x.total ? fmtPeso(x.total) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {shown.length > 0 && (
          <p className="text-sm text-right">Total to buy{shown.length < all.length ? " (chosen levels)" : ""}:{" "}
            <b style={{ color: ACCENT.cyan }}>{fmtPeso(sumTotal(shown))}</b></p>
        )}
        <div className="flex items-center gap-2 justify-end flex-wrap">
          <DownloadLink path="/api/shop/restock/download" title="CSV file: opens in Excel (all levels)"
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold"
            style={{ color: ACCENT.cyan, border: `1px solid ${ACCENT.cyan}70` }}>
            <Download size={14} /> Save for Excel
          </DownloadLink>
          <button onClick={() => printHtml(restockHtml(shown, data && data.at))} disabled={!shown.length}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold text-black/85 disabled:opacity-50"
            style={{ background: ACCENT.cyan }}>
            <Printer size={14} /> Print ({shown.length})
          </button>
        </div>
      </div>
    </Modal>
  );
}

export default function SupplyPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [onlyNa, setOnlyNa] = useState(false);
  // opened from Sales -> Receipt -> "Add to Supply": { rid, row, label, ocr, qty } (the row is linked after saving)
  const [fromReceipt, setFromReceipt] = useState(null);
  const [profileFor, setProfileFor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState("");          // shown INSIDE the modal, next to the Apply button
  const [newType, setNewType] = useState(null);        // "+ Add type" row inside the item profile
  const [requests, setRequests] = useState([]);           // new items sent from the phones, waiting for the owner
  const [acceptFor, setAcceptFor] = useState(null);
  const [reqMsg, setReqMsg] = useState("");
  const [wantReq, setWantReq] = useState(null);         // /supply?request=<id> (dashboard card / receipt): open it
  const [restockOpen, setRestockOpen] = useState(false);
  const reqRef = useRef(null);
  // errors from the server also show in the app's toast (lib/api.js)
  async function reload() {
    try {
      const d = await api.get("/api/shop/supply");
      setItems(d);
      setProfileFor((prev) => (prev ? d.find((x) => x.id === prev.id) || null : prev));
    } catch { /* toast already shows it */ }
    try { setRequests(await api.get("/api/shop/requests")); } catch { /* toast */ }
    setLoaded(true);
  }
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, []);
  useEffect(() => {
    const id = new URLSearchParams(location.search).get("request");
    if (!id) return;
    setWantReq(Number(id));
    navigate("/supply", { replace: true });
    // eslint-disable-next-line
  }, [location.search]);
  useEffect(() => {
    if (!wantReq || !loaded) return;
    const r = requests.find((x) => x.id === wantReq);
    if (r) setAcceptFor(r);
    else setReqMsg("That new item was already accepted or removed.");
    setWantReq(null);
    setTimeout(() => reqRef.current && reqRef.current.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }, [wantReq, loaded, requests]);
  async function removeRequest(r) {
    if (!window.confirm(`Remove "${r.name}" sent by ${r.by || "a phone"}? It is not added to Supply and not sold.`)) return;
    try { await api.del(`/api/shop/requests/${r.id}`); setReqMsg(`Removed: ${r.name}.`); await reload(); } catch { /* toast */ }
  }
  // /supply?add=<name>&price=&unit=&qty=&receipt=<id>&row=u12&label=&ocr=  -> the Add form, filled in from the receipt row
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    if (!q.has("add")) return;
    setDraftName(q.get("add") || "");
    setDraftTypes([{ ...blankType(), unit: q.get("unit") || "", sellPrice: q.get("price") || "" }]);
    setFromReceipt(q.get("receipt") ? { rid: q.get("receipt"), row: q.get("row") || "", label: q.get("label") || "",
      ocr: q.get("ocr") || "", qty: q.get("qty") || "", price: q.get("price") || "" } : null);
    setAddOpen(true);
    navigate("/supply", { replace: true });          // a reload does not open the form again
    // eslint-disable-next-line
  }, [location.search]);
  const naCount = useMemo(() => items.filter((it) => it.types.some((t) => t.qtyUnknown)).length, [items]);
  const shownItems = useMemo(() => items.filter((it) => matchesSearch(it, search) && (!onlyNa || it.types.some((t) => t.qtyUnknown))),
    [items, search, onlyNa]);

  // add-item draft state; the photo is uploaded right after the item is saved
  const [draftName, setDraftName] = useState("");
  const [draftPhoto, setDraftPhoto] = useState(null);          // File
  const [draftPreview, setDraftPreview] = useState(null);      // object URL for the preview
  const [draftTypes, setDraftTypes] = useState([blankType()]);
  const profilePhotoRef = useRef(null);
  const [renaming, setRenaming] = useState(null);
  async function renameProduct() {
    const name = (renaming || "").trim();
    if (!name || name === profileFor.name) { setRenaming(null); return; }
    try { await api.put(`/api/shop/supply/products/${profileFor.id}`, { name }); setRenaming(null); await reload(); } catch { /* toast */ }
  }

  function pickDraftPhoto(f) {
    if (draftPreview) URL.revokeObjectURL(draftPreview);
    setDraftPhoto(f); setDraftPreview(f ? URL.createObjectURL(f) : null);
  }
  function resetDraft() {
    setDraftName(""); pickDraftPhoto(null); setFormErr("");
    setDraftTypes([blankType()]); setFromReceipt(null);
  }
  async function changeProfilePhoto(file) {
    try { await api.upload(`/api/shop/supply/products/${profileFor.id}/image`, file); await reload(); } catch { /* toast */ }
  }

  function updateType(id, next) {
    setDraftTypes((prev) => prev.map((t) => (t.id === id ? next : t)));
  }
  function addTypeRow() {
    setDraftTypes((prev) => [...prev, blankType()]);
  }
  function removeTypeRow(id) {
    setDraftTypes((prev) => (prev.length > 1 ? prev.filter((t) => t.id !== id) : prev));
  }

  // Apply: any row with something typed in is saved (a blank spec = the item has no separate types).
  async function apply() {
    if (!draftName.trim() || busy) return;
    const rows = draftTypes.filter(typeHasData);
    const specs = rows.map((t) => t.spec.trim().toLowerCase());
    if (new Set(specs).size !== specs.length) { setFormErr("Two types have the same spec. Give each type its own spec (e.g. 40kg bag, 25kg bag)."); return; }
    setBusy(true); setFormErr("");
    try {
      const created = await api.post("/api/shop/supply/products", { name: draftName.trim(), types: rows.map(typePayload) });
      if (draftPhoto) {
        try { await api.upload(`/api/shop/supply/products/${created.id}/image`, draftPhoto); } catch { /* toast; the item itself is saved */ }
      }
      const back = fromReceipt;
      if (back && back.row && created.types.length) {   // the receipt row now IS this item: back to that receipt
        const kind = back.row.slice(0, 1), rowId = back.row.slice(1);
        try { await api.post(`/api/shop/receipts/${back.rid}/rows/${kind}/${rowId}/item`, { itemId: created.types[0].id }); } catch { /* toast */ }
      }
      await reload();
      setAddOpen(false);
      resetDraft();
      if (back) navigate(`/sales?receipt=${back.rid}`);
    } catch (e) { setFormErr(e.message); }
    setBusy(false);
  }
  async function setQty(t, newQty) {
    try {
      await api.post(`/api/shop/supply/items/${t.id}/count`, { qty: newQty });      // the count IS this number from now on
      await reload();
    } catch { /* toast */ }
  }
  async function addTypeToProduct() {
    if (!newType || !typeHasData(newType)) return;
    try {
      await api.post(`/api/shop/supply/products/${profileFor.id}/types`, typePayload(newType));
      setNewType(null);
      await reload();
    } catch { /* toast */ }
  }
  // Deleting also works for items that were sold before: they leave Supply, the phones and the receipt reader, and
  // their past sales stay in the books as "<name> (deleted)".
  async function deleteType(t) {
    const nm = t.spec ? `${profileFor.name} ${t.spec}` : profileFor.name;
    if (!window.confirm(`Delete "${nm}"?\n\nIt leaves Supply, the phones and the receipt reader. If it was sold before, `
      + `those past sales stay in the books as "${nm} (deleted)".`)) return;
    try { await api.del(`/api/shop/supply/items/${t.id}`); await reload(); } catch { /* toast */ }
  }
  async function deleteProduct() {
    if (!window.confirm(`Delete "${profileFor.name}" and all its types?\n\nIt leaves Supply, the phones and the receipt reader. `
      + "If it was sold before, those past sales stay in the books (marked “deleted”).")) return;
    try { await api.del(`/api/shop/supply/products/${profileFor.id}`); setProfileFor(null); await reload(); } catch { /* toast */ }
  }
  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-lg font-semibold">Inventory Items</h2>
        <div className="flex items-center gap-2 flex-1 justify-end min-w-0">
          <div className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 w-full max-w-xs" style={INPUT}>
            <Search size={14} style={{ color: "var(--muted)" }} className="shrink-0" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search items (e.g. cyclone 5ft)"
              onKeyDown={(e) => { if (e.key === "Escape") setSearch(""); }}
              className="flex-1 min-w-0 bg-transparent outline-none text-xs" style={{ color: "var(--text)" }} />
            {search && <button onClick={() => setSearch("")} title="Clear" style={{ color: "var(--muted)" }}><X size={13} /></button>}
          </div>
          {naCount > 0 && (
            <button onClick={() => setOnlyNa((v) => !v)} title="Items saved with stock N/A: count them"
              className="flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1.5 rounded-lg shrink-0"
              style={onlyNa ? { background: NA_COLOR, color: "#000" } : { color: NA_COLOR, border: `1px solid ${NA_COLOR}70` }}>
              <ClipboardList size={12} /> Not counted ({naCount})
            </button>
          )}
          <button onClick={() => setRestockOpen(true)} title="Print the A / B / C items to buy again"
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg shrink-0"
            style={{ color: ACCENT.cyan, border: `1px solid ${ACCENT.cyan}70` }}>
            <ListChecks size={14} /> Restock list
          </button>
          <button onClick={() => setAddOpen(true)} style={{ background: ACCENT.cyan }}
            className="flex items-center gap-1.5 text-xs font-semibold text-black/85 px-3.5 py-2 rounded-lg shrink-0">
            <Plus size={14} /> Add Item
          </button>
        </div>
      </div>

      {loaded && items.length === 0 && (
        <p className="text-xs py-6" style={{ color: "var(--faint)" }}>
          No items yet. Press Add Item — the phone's receipt reader matches receipts against these names.
        </p>
      )}
      {loaded && items.length > 0 && shownItems.length === 0 && (
        <p className="text-xs py-6" style={{ color: "var(--faint)" }}>
          No item matches “{search}”{onlyNa ? " among the items not counted yet" : ""}. Check the spelling, or press Add Item to add it.
        </p>
      )}
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
        {shownItems.map((it) => {
          const counted = it.types.filter((t) => !t.qtyUnknown);
          const na = it.types.length - counted.length;
          const totalQty = counted.reduce((s, t) => s + t.qty, 0);
          const units = [...new Set(it.types.map((t) => t.unit))];
          const worst = ["A", "B", "C"].find((l) => it.types.some((t) => t.level === l));
          return (
            <button key={it.id} onClick={() => setProfileFor(it)}
              className="relative rounded-xl p-4 flex flex-col items-center gap-2 text-left hover:border-current"
              style={{ border: `1px solid ${worst ? `${LEVEL_COLOR[worst]}80` : "var(--border)"}`, background: "var(--panel)", boxShadow: CARD_SHADOW }}>
              {(worst || na > 0) && <span className="absolute top-2 right-2 flex gap-1">{na > 0 && <NaBadge small />}{worst && <LevelBadge level={worst} small />}</span>}
              <ItemGlyph name={it.name} src={productPhoto(it)} box={64} />
              <p className="text-sm font-semibold text-center leading-tight">{it.name}</p>
              <p className="text-[11px]" style={{ color: "var(--muted)" }}>{it.types.length} type{it.types.length !== 1 ? "s" : ""} · {
                na === it.types.length ? <span style={{ color: NA_COLOR }}>N/A</span>
                  : <>{totalQty.toLocaleString()}{units.length === 1 ? ` ${units[0]}` : " total"}{na > 0 && <span style={{ color: NA_COLOR }}> + N/A</span>}</>}</p>
            </button>
          );
        })}
      </div>

      {/* ---- New items sent from the phones: below the inventory items ---- */}
      <div ref={reqRef} className="flex flex-col gap-2 scroll-mt-4">
        {(requests.length > 0 || reqMsg) && (
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold flex items-center gap-1.5"><Smartphone size={15} style={{ color: ACCENT.cyan }} />
              New items from the phones{requests.length ? ` (${requests.length})` : ""}</h3>
            <span className="text-[11px]" style={{ color: "var(--muted)" }}>A sales person sold something that is not in Supply yet. Accept = it is added to Supply; the sales person then sells it with Update on the phone.</span>
          </div>
        )}
        {reqMsg && (
          <p className="text-xs rounded-lg px-3 py-2 flex items-center gap-2" style={{ color: ACCENT.cyan, border: `1px solid ${ACCENT.cyan}55` }}>
            <Check size={13} /> <span className="flex-1">{reqMsg}</span>
            <button onClick={() => setReqMsg("")} style={{ color: "var(--muted)" }}><X size={13} /></button></p>
        )}
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))" }}>
          {requests.map((r) => <RequestCard key={r.id} req={r} onAccept={() => setAcceptFor(r)} onRemove={() => removeRequest(r)} />)}
        </div>
      </div>
      {restockOpen && <RestockModal onClose={() => setRestockOpen(false)} />}
      {acceptFor && (
        <AcceptRequestModal req={acceptFor} products={items} onClose={() => setAcceptFor(null)}
          onDone={async (res) => { setAcceptFor(null); setReqMsg(res.message || "Accepted."); await reload(); }} />
      )}

      {/* ---- Add item modal ---- */}
      {addOpen && (
        <Modal title="Add Supply Item" onClose={() => { setAddOpen(false); resetDraft(); }} width={560}>
          <div className="flex flex-col gap-4">
            {fromReceipt && (
              <div className="rounded-lg px-3 py-2 text-[11px] flex items-start gap-2" style={{ border: `1px solid ${ACCENT.cyan}55`, background: `${ACCENT.cyan}0d` }}>
                <FileImage size={14} style={{ color: ACCENT.cyan }} className="shrink-0 mt-0.5" />
                <span style={{ color: "var(--muted)" }}>
                  From receipt <b style={{ color: "var(--text)" }}>{fromReceipt.label || `#${fromReceipt.rid}`}</b>
                  {fromReceipt.ocr ? <> · read as “<span className="font-mono">{fromReceipt.ocr}</span>”</> : null}
                  {fromReceipt.qty ? <> · {fromReceipt.qty} sold</> : null}{fromReceipt.price ? <> at ₱{fromReceipt.price}</> : null}.
                  {" "}Check the name, unit and price. Not sure how many are left? Tick N/A. After Apply you go back to the receipt with this row matched.
                </span>
              </div>
            )}
            <div>
              <label className="text-xs font-medium mb-1.5 block" style={{ color: "var(--muted)" }}>Photo</label>
              <PhotoPicker name={draftName} preview={draftPreview} onPick={pickDraftPhoto} onClear={() => pickDraftPhoto(null)} />
            </div>

            <div>
              <label className="text-xs font-medium mb-1.5 block" style={{ color: "var(--muted)" }}>Name</label>
              <input value={draftName} onChange={(e) => setDraftName(e.target.value)} placeholder="e.g. Portland Cement"
                className="w-full text-sm rounded-lg px-3 py-2 outline-none"
                style={{ background: "var(--panelAlt)", border: "1px solid var(--border)", color: "var(--text)" }} />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-medium" style={{ color: "var(--muted)" }}>Types</label>
                <button onClick={addTypeRow} style={{ color: ACCENT.cyan }} className="text-xs font-medium flex items-center gap-1">
                  <Plus size={12} /> Add type
                </button>
              </div>
              <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
                <TypeHeader />
                {draftTypes.map((t) => (
                  <TypeRow key={t.id} type={t} onChange={(next) => updateType(t.id, next)} onRemove={() => removeTypeRow(t.id)} canRemove={draftTypes.length > 1} />
                ))}
              </div>
              <p className="text-[10px] mt-1.5" style={{ color: "var(--faint)" }}>
                Spec (e.g. "1/2 bolts", "40kg bag"), any unit (kg, L, pcs, bags…). Stock = what you have now. Cost = your standard
                (benchmark) cost per unit: buying below it later counts as extra profit. A / B / C = stock levels that put the item on
                the dashboard Priority (A critical on top, B low, C watch) — e.g. A ≤ 5, B ≤ 15, C ≤ 30.
              </p>
            </div>

            {formErr && <p className="text-xs rounded-lg px-3 py-2" style={{ color: ACCENT.danger, border: `1px solid ${ACCENT.danger}55` }}>{formErr}</p>}
            <button onClick={apply} disabled={!draftName.trim() || busy} style={{ background: draftName.trim() ? ACCENT.cyan : "var(--faint)" }}
              className="mt-1 py-2.5 rounded-lg text-sm font-semibold text-black/85 disabled:cursor-not-allowed">
              {busy ? "Saving…" : "Apply"}
            </button>
          </div>
        </Modal>
      )}

      {/* ---- Item profile modal ---- */}
      {profileFor && (
        <Modal title="" onClose={() => { setProfileFor(null); setNewType(null); }} width={520}>
          <div className="flex items-center gap-3 mb-4">
            <button onClick={() => profilePhotoRef.current && profilePhotoRef.current.click()} title="Change photo" className="relative shrink-0">
              <ItemGlyph name={profileFor.name} src={productPhoto(profileFor)} size={22} box={56} />
              <span style={{ background: ACCENT.cyan }} className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full flex items-center justify-center text-black/85">
                <Camera size={12} />
              </span>
            </button>
            <input ref={profilePhotoRef} type="file" accept="image/*" className="hidden"
              onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) changeProfilePhoto(f); }} />
            {renaming !== null ? (
              <div className="flex items-center gap-1.5 flex-1">
                <input autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") renameProduct(); if (e.key === "Escape") setRenaming(null); }}
                  className="flex-1 min-w-0 text-sm rounded-md px-2 py-1.5 outline-none" style={INPUT} />
                <button onClick={renameProduct} style={{ background: ACCENT.cyan }} className="px-2.5 py-1.5 rounded-md text-xs font-semibold text-black/85">Save</button>
                <button onClick={() => setRenaming(null)} className="text-xs px-1.5" style={{ color: "var(--muted)" }}>Cancel</button>
              </div>
            ) : (
              <>
                <h3 className="font-semibold text-base">{profileFor.name}</h3>
                <button onClick={() => setRenaming(profileFor.name)} title="Rename" style={{ color: "var(--muted)" }} className="p-1"><Pencil size={13} /></button>
              </>
            )}
          </div>

          <div className="flex flex-col gap-2 max-h-96 overflow-y-auto pr-1">
            {profileFor.types.map((t) => (
              <TypeProfileRow key={t.id} t={t} onSetQty={(q) => setQty(t, q)} onChanged={reload} onDelete={() => deleteType(t)} />
            ))}
            {profileFor.types.length === 0 && <p className="text-xs text-center py-4" style={{ color: "var(--faint)" }}>No types added yet.</p>}
          </div>

          {newType ? (
            <div className="mt-3 flex flex-col gap-2 p-2.5 rounded-lg" style={{ border: "1px dashed var(--border)" }}>
              <TypeHeader />
              <TypeRow type={newType} onChange={setNewType} onRemove={() => setNewType(null)} />
              <button onClick={addTypeToProduct} disabled={!typeHasData(newType)} style={{ background: ACCENT.cyan }}
                className="py-1.5 rounded-lg text-xs font-semibold text-black/85 disabled:opacity-40">Add this type</button>
            </div>
          ) : (
            <button onClick={() => setNewType(blankType())} style={{ color: ACCENT.cyan }} className="mt-3 text-xs font-medium flex items-center gap-1">
              <Plus size={12} /> Add type
            </button>
          )}

          <div className="mt-4 flex gap-2">
            <button onClick={deleteProduct} style={{ border: `1px solid ${ACCENT.danger}70`, color: ACCENT.danger }}
              className="px-4 py-2.5 rounded-lg text-sm font-medium">Delete</button>
            <button onClick={() => { setProfileFor(null); setNewType(null); }} style={{ background: ACCENT.cyan }}
              className="flex-1 py-2.5 rounded-lg text-sm font-semibold text-black/85">
              Save
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}