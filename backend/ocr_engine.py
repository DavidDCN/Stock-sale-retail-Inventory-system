"""
ocr_engine.py  =  the PROCESSING file (no window code in here)

Contains
  * loading the OCR model (CPU or GPU) and reading an image
  * turning OCR text boxes into  item + quantity  rows
  * matching items against the database (fuzzy matching)
  * database + stock check + sale (auto deduction)

You normally don't run this file by hand, server.py imports it (it lives in the backend folder).
BUT when something goes wrong you can test ONLY the processing, with no window:

    python ocr_engine.py --info                      # versions + is the GPU usable?
    python ocr_engine.py my_image.png                # read an image, print rows + timing
    python ocr_engine.py my_image.png --device gpu --tier v5mobile --side 1280
"""

import gc
import math
import multiprocessing as mp
import os
import random
import re
import sqlite3
import statistics
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    from rapidfuzz import fuzz           # better fuzzy matching if installed
    HAVE_RF = True
except ImportError:
    from difflib import SequenceMatcher  # built into Python, works fine as a fallback
    HAVE_RF = False


# =====================================================================
# SETTINGS
# =====================================================================
# Where the shop's data lives (inventory.db, uploads, avatars, product_images, shop_server.log):
#   normal Python  -> next to this file (backend/)
#   the .exe       -> C:\Users\<name>\AppData\Local\TresNinosShop   (NOT the program folder: zipping or copying
#                     the program to another laptop can never carry the shop's records, and rebuilding never touches them)
#   SHOP_DATA_DIR=<folder> overrides both.
FROZEN = getattr(sys, "frozen", False)
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else None


def _data_dir():
    if os.environ.get("SHOP_DATA_DIR"):
        return Path(os.environ["SHOP_DATA_DIR"])
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "TresNinosShop"
    return Path(__file__).resolve().parent


APP_DIR = _data_dir()
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "inventory.db"
DATA_ITEMS = ["inventory.db", "inventory.db-wal", "inventory.db-shm", "uploads", "avatars", "product_images", "payment_photos",
              "opened_data",
              "shop_server.log"]


def move_old_data():
    """Older .exe versions kept the data NEXT TO the .exe. Move it to APP_DIR once, so the program folder is clean.
    If APP_DIR already has a database, the old files are kept (not merged) in APP_DIR/from_program_folder_<time>.
    Returns a short text of what happened (for the log), or "" when there was nothing to move."""
    if not FROZEN or EXE_DIR is None or EXE_DIR.resolve() == APP_DIR.resolve():
        return ""
    import shutil
    old = [n for n in DATA_ITEMS + ["ai"] if (EXE_DIR / n).exists()]
    if not old:
        return ""
    if (EXE_DIR / "ai").exists():                       # an old optional download, not shop data
        shutil.rmtree(EXE_DIR / "ai", ignore_errors=True)
        old.remove("ai")
    if not old:
        return ""
    target = APP_DIR
    if (APP_DIR / "inventory.db").exists() and (EXE_DIR / "inventory.db").exists():
        target = APP_DIR / f"from_program_folder_{datetime.now():%Y%m%d_%H%M%S}"
    target.mkdir(parents=True, exist_ok=True)
    for n in old:
        src, dst = EXE_DIR / n, target / n
        if src.is_dir() and dst.exists():
            for f in src.rglob("*"):
                if f.is_file():
                    d = dst / f.relative_to(src)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    if not d.exists():
                        shutil.move(str(f), str(d))
            shutil.rmtree(src, ignore_errors=True)
        elif not dst.exists():
            shutil.move(str(src), str(dst))
        else:
            src.unlink(missing_ok=True)
    if target == APP_DIR:                               # receipts remember the full photo path: point them at the new place
        try:
            c = sqlite3.connect(DB_PATH, timeout=30)
            c.execute("UPDATE receipts SET image = replace(image, ?, ?) WHERE image LIKE ?",
                      (str(EXE_DIR / "uploads"), str(APP_DIR / "uploads"), str(EXE_DIR / "uploads") + "%"))
            c.commit()
            c.close()
        except sqlite3.Error:
            pass
    return f"moved {', '.join(old)} from {EXE_DIR} to {target}"
AUTO_OK = 85               # match score >= this -> accepted automatically ("OK")
MIN_SHOW = 60              # match score <  this -> row ignored (unless it has a 'kg' quantity)
NUM_MISMATCH_PENALTY = 35  # '1/4' vs '1/2' look alike as text, so a size mismatch costs points
KEYWORD_MIN = 75           # a product word of the receipt must be this similar to a product word of the item
KEYWORD_MIN_LOOSE = 65     # ... on a handwritten delivery receipt ('feksclw' = 'tekscrew'); shown as "Please check"

# Which OCR models can be used. None = PaddleOCR's own default (PP-OCRv6 medium, what worked on your PC).
# (detection model, recognition model) = a specific pair.
MODEL_SETS = {
    "medium":   None,
    "v5mobile": ("PP-OCRv5_mobile_det", "en_PP-OCRv5_mobile_rec"),   # light + fast, English
    "small":    ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),        # only exists in newer installs
    "tiny":     ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"),          # only exists in newer installs
}
DEFAULT_SIDE = 1280        # the longest side of the photo is shrunk to this many pixels before detection

# name, unit, selling price per unit, supplier (cost) price per unit
SEED_ITEMS = [
    ("Nails 1/4 inch",        "kg", 140.0, 110.0),
    ("Nails 1/2 inch",        "kg", 135.0, 105.0),
    ("Nails 1 inch",          "kg", 130.0, 100.0),
    ("Nails 2 inch",          "kg", 125.0,  95.0),
    ("Nails 3 inch",          "kg", 120.0,  90.0),
    ("Wood Screws 1 inch",    "kg", 210.0, 165.0),
    ("Concrete Nails 2 inch", "kg", 150.0, 115.0),
]


def fmt(x):
    return f"{x:g}"


def log(msg):
    """Timestamped line in the terminal, so you can see exactly where the processing is."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def short_error(tb, limit=350):
    """Last line of a traceback = the actual error. Keep its START (the useful part), not its end."""
    lines = [ln for ln in str(tb).strip().splitlines() if ln.strip()]
    msg = lines[-1] if lines else "unknown error"
    return msg if len(msg) <= limit else msg[:limit] + " ..."


# =====================================================================
# DATABASE  (SQLite = one file, no server needed)
# =====================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)   # wait (not fail) when the phone server and the window write together
    conn.row_factory = sqlite3.Row   # lets us read columns by name: row["name"]
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE NOT NULL,
            unit       TEXT NOT NULL,
            quantity   REAL NOT NULL,        -- current stock
            sell_price REAL NOT NULL,        -- price per unit we sell at
            cost_price REAL NOT NULL         -- price per unit we bought from the supplier
        );
        CREATE TABLE IF NOT EXISTS sales (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, image TEXT, total REAL, profit REAL,
            user_id INTEGER                                 -- which phone account made the sale (NULL = desktop window)
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER, item_id INTEGER, qty REAL, sell_price REAL, cost_price REAL
        );
        CREATE TABLE IF NOT EXISTS ocr_runs (          -- the speed log
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, device TEXT, model TEXT, max_side INTEGER,
            img_w INTEGER, img_h INTEGER, seconds REAL, lines INTEGER
        );
    """)
    # migration: an inventory.db made before phone accounts existed has no sales.user_id column
    if "user_id" not in [r[1] for r in conn.execute("PRAGMA table_info(sales)")]:
        conn.execute("ALTER TABLE sales ADD COLUMN user_id INTEGER")
        conn.commit()
    # The database starts EMPTY: items are added in the React Supply page.
    # (For a quick OCR test with sample nails/screws run:  python server.py seed-demo)
    import shop_db                                # cashflow / supply / photo tables live in shop_db.py (same database)
    shop_db.ensure_schema(conn)
    return conn


def seed_demo(conn):
    """Only when asked (python server.py seed-demo): adds the 7 sample items with random stock 2-10."""
    added = 0
    with conn:
        for name, unit, sell, cost in SEED_ITEMS:
            added += conn.execute(
                "INSERT OR IGNORE INTO items(name,unit,quantity,sell_price,cost_price) VALUES(?,?,?,?,?)",
                (name, unit, random.randint(2, 10), sell, cost)).rowcount
    return added


def get_items(conn, everything=False):
    """The Supply items the reader matches and the phones pick from. everything=True also returns items that were
    deleted from Supply but still have history (deleted_at set): used where an existing receipt row or sale points
    to one (stock check, selling), never for matching."""
    try:
        if everything:
            return conn.execute("SELECT * FROM items ORDER BY name").fetchall()
        return conn.execute("SELECT * FROM items WHERE deleted_at IS NULL ORDER BY name").fetchall()
    except sqlite3.OperationalError:              # a database from before round 17 (no deleted_at column yet)
        return conn.execute("SELECT * FROM items ORDER BY name").fetchall()


def randomize_stock(conn):
    with conn:
        for row in get_items(conn):
            conn.execute("UPDATE items SET quantity=? WHERE id=?", (random.randint(2, 10), row["id"]))


def log_run(conn, device, model, side, w, h, seconds, lines):
    with conn:
        conn.execute("INSERT INTO ocr_runs(created,device,model,max_side,img_w,img_h,seconds,lines) VALUES(?,?,?,?,?,?,?,?)",
                     (datetime.now().strftime("%H:%M:%S"), device, model, side, w, h, seconds, lines))


def avg_time(conn, device, model, side):
    return conn.execute("SELECT AVG(seconds) FROM ocr_runs WHERE device=? AND model=? AND max_side=?",
                        (device, model, side)).fetchone()[0]


# =====================================================================
# MATCHING: turn messy OCR text into a real database item
# =====================================================================
def norm(s):
    """Lowercase + unify how sizes are written so '1/4"', '1/4in', '1/4 inch' all look the same."""
    s = re.sub(r"(?<![A-Za-z0-9])([lIoO|]{1,2}|[sS])(?=mm\b)",
               lambda m: m.group(1).translate(str.maketrans("lIoO|sS", "1100155")), s)          # 'lomm' -> '10mm'
    s = s.lower().replace('"', " inch ")
    s = re.sub(r"(?<=\d)\s*l\b", " liter", s)                    # '1L' paint can   -> '1 liter'
    s = re.sub(r"(?<![a-z0-9/])[il|](?=/\d)", "1", s)          # 'l/4'          -> '1/4'
    s = re.sub(r"(?<![a-z0-9/])[il|](?![a-z0-9/])", "1", s)    # 'Nails I inch' -> 'nails 1 inch'
    s = re.sub(r"(?<![a-z])(\d)([a-z])", r"\1 \2", s)           # '4in' -> '4 in'  (but leave 'nai1s' alone)
    s = re.sub(r"\b(inches|inch|in)\b", " inch ", s)
    s = re.sub(r"[^a-z0-9/. ]", " ", s)
    # paint / thinner cans: 'lit', 'ltr' = liter, 'gal' = gallon, 'btl', 'bot' = bottle (the shop's items say Liter, Gallon, Bottle)
    s = re.sub(r"\b(?:lit|litre|litres|liters|ltr|ltrs)\b\.?", "liter", s)
    s = re.sub(r"\b(?:gal|gals|gallons)\b\.?", "gallon", s)
    s = re.sub(r"\b(?:btl|btls|bot|bots|bottles)\b\.?", "bottle", s)
    return re.sub(r"\s+", " ", s).strip()


def numbers_in(s):
    """The size numbers in a name: 'nails 1/4 inch' -> {'1/4'}. A number before liter / gallon is the can, not a
    size ('QDE 1 lit' = the item 'QDE ... (Liter)')."""
    tokens = norm(s).split()
    return {t for k, t in enumerate(tokens) if re.fullmatch(r"\d+/\d+|\d+(?:\.\d+)?", t)       # only pure number tokens
            and not (k + 1 < len(tokens) and tokens[k + 1] in ("liter", "gallon"))}


def similarity(a, b):
    na, nb = norm(a), norm(b)
    if HAVE_RF:
        score = max(fuzz.token_sort_ratio(na, nb), fuzz.ratio(na, nb))
    else:
        score = SequenceMatcher(None, na, nb).ratio() * 100
    xa, xb = numbers_in(na), numbers_in(nb)
    if xa != xb:                            # different size = probably a different product
        # ... unless one only leaves a number out: 'boots size 43' vs 'Rubber Boots Size 43 (#9)'
        score -= NUM_MISMATCH_PENALTY // 3 if xa and xb and (xa < xb or xb < xa) else NUM_MISMATCH_PENALTY
    return max(score, 0)


# words that say nothing about WHAT the product is (sizes and units). Everything else is a product word.
UNIT_WORDS = {"inch", "inches", "in", "kg", "kgs", "kilo", "kilos", "g", "mm", "cm", "m", "meter", "meters", "ft", "feet",
              "pc", "pcs", "piece", "pieces", "bag", "bags", "sack", "sacks", "box", "boxes", "l", "liter", "liters", "litre",
              "gal", "gallon", "roll", "rolls", "mt", "ton", "tons", "x", "of", "per", "the", "and", "with",
              "mtr", "mtrs", "kl", "kls", "lbs", "lb", "amp", "amps", "watts", "pk", "btl", "bottle"}
# colour / finish words: they tell one variant of a product from another ('Boysen QDE Lemon Yellow' vs 'Emerald Green'),
# never WHAT the product is. A receipt row must share a real product word with an item ('wheel barrow yellow' is not a
# 'Boysen QDE Lemon Yellow' because of 'yellow'; 'RD' is not 'Red Cement'); the colour then picks the variant.
COLOR_WORDS = set("white black gray grey green red blue yellow brown beige orange cream lemon ivory mint jade emerald caramel "
                  "mahogany maple natural silver gold maroon violet pink purple terra cotta bahama mango choco chocolate "
                  "clear transparent light dark".split())
# OCR reads these handwritten letters for digits in product codes ('RS3' = RSB, 'G1' = GI)
CODE_LOOKALIKE = str.maketrans("0123456789", "oizbasgtbg")


def key_words(s):
    """Product words of a name: 'Concrete Nails 2 inch' -> ['concrete', 'nails']"""
    return [w for w in re.findall(r"[a-z]+", norm(s)) if w not in UNIT_WORDS and len(w) >= 2]


def word_sim(a, b):
    return fuzz.ratio(a, b) if HAVE_RF else SequenceMatcher(None, a, b).ratio() * 100


def kw_sim(t, w):
    """How much the receipt word t looks like the item's product word w (0-100). Short item words (QDE, PVC, RSB, GI,
    net) must be nearly the same length and spelling: 'monet' is not 'net', 'rd' is not 'red'."""
    if t == w:
        return 100
    if len(t) < 3:                                   # a 2-letter scrap of handwriting only counts when exact
        return 0
    s = word_sim(t, w)
    if len(w) >= 5 and len(t) >= 5:
        s = max(s, ocr_word_sim(t, w))             # 'amaur' = armour, 'elbew' = elbow
    if len(w) <= 4 and abs(len(t) - len(w)) > 1:
        return min(s, 50)
    if min(len(t), len(w)) / max(len(t), len(w)) < 0.6:
        return min(s, 60)
    return s


def item_keywords(name):
    """(product words, colour words) of an item name; an item named only by colours keeps them as product words"""
    kn = key_words(name)
    prod = [w for w in kn if w not in COLOR_WORDS]
    return (prod, [w for w in kn if w in COLOR_WORDS]) if prod else (kn, [])


# RARE WORDS: a word that only a few items of the shop have ('emerald', 'mahogany', 'hardiflex', 'fenolic') points
# straight at those items, even when it is a colour word. A word most items share ('green', 'white', 'boysen', 'qde')
# says little. The rare words of a receipt row are found once per row and decide between look-alike items
# ('Emerald Green QDE Lit' = QDE Emerald Green (Liter), not QDE Nile Green (Liter) at the same price).
RARE_SHARE = 0.01              # a word in at most 1% of the items (but never fewer than RARE_MIN items) is rare
RARE_MIN = 3
_WORD_COUNT = {"key": None, "count": {}}


# plain colours never prove which item it is, however few items have them ('wheel barrow yellow' is no yellow paint);
# a shade NAME does ('emerald', 'mahogany', 'caramel', 'jade', 'maroon')
PLAIN_COLORS = set("white black gray grey green red blue yellow brown beige orange cream pink purple violet silver gold "
                   "clear transparent light dark natural".split())


def item_words(name):
    """every word of an item name that can be rare: 4+ letters, not a unit / size word, not a plain colour"""
    return {w for w in re.findall(r"[a-z]+", norm(name))
            if len(w) >= 4 and w not in UNIT_WORDS and w not in STOP_WORDS and w not in PLAIN_COLORS}


def word_counts(items):
    """word -> how many of the shop's items have it (worked out again only when the item list changes)"""
    key = hash(tuple(it["name"] for it in items))
    if _WORD_COUNT["key"] != key:
        count = {}
        for it in items:
            for w in item_words(it["name"]):
                count[w] = count.get(w, 0) + 1
        _WORD_COUNT.update(key=key, count=count)
    return _WORD_COUNT["count"]


def rare_words(text, items):
    """The rare item words this text contains (spelling slips allowed for long words: 'emerlad' = emerald)."""
    count = word_counts(items)
    limit = max(RARE_MIN, int(len(items) * RARE_SHARE))
    rare = [w for w, n in count.items() if n <= limit]
    found = set()
    for t in {w for w in re.findall(r"[a-z]+", norm(text)) if len(w) >= 4 and w not in UNIT_WORDS}:
        for w in rare:
            if t == w or (len(t) >= 5 and len(w) >= 5 and kw_sim(t, w) >= 85):
                found.add(w)
    return found


CAN_WORDS = {"liter": "liter", "liters": "liter", "gallon": "gallon", "gallons": "gallon", "bottle": "bottle", "bottles": "bottle"}


def can_word(s):
    """'liter' / 'gallon' / 'bottle' when a name or receipt row says which can it is ('Lit', 'Gal', '(Liter)'), else None"""
    found = {CAN_WORDS[w] for w in norm(s).split() if w in CAN_WORDS}
    return found.pop() if len(found) == 1 else None


def can_fits(text, it):
    """a row that says 'Lit' is never the Gallon can of the same paint (and the other way round)"""
    a = can_word(text)
    if not a:
        return True
    try:
        unit = it["unit"]                      # a dict or a database row
    except (KeyError, IndexError):
        unit = ""
    b = can_word(it["name"]) or CAN_WORDS.get(str(unit or "").lower())
    return not b or a == b


def keyword_score(text, name, strict_short=False):
    """How well the best product word of the text matches a product word of the item name (0-100). Colour words of
    the item do not count (see COLOR_WORDS). strict_short is kept for old callers (2-letter scraps never count now)."""
    kn, _ = item_keywords(name)
    if not kn:
        return 100
    words = [w for w in key_words(text)] + [re.sub(r"\d", lambda m: m.group().translate(CODE_LOOKALIKE), w)
                                            for w in re.findall(r"[a-z]+\d[a-z\d]*|\d[a-z][a-z\d]*", norm(text))]
    best = 0
    for b in words:
        for a in kn:
            best = max(best, kw_sim(b, a))
    return best


def word_coverage(text, name, need=KEYWORD_MIN):
    """(share of the item's product words found in the text, share of the text's product words found in the item)"""
    kn, _ = item_keywords(name)
    tw = [w for w in key_words(text) if len(w) >= 3 and w not in COLOR_WORDS and w not in STOP_WORDS]
    tw += [re.sub(r"\d", lambda m: m.group().translate(CODE_LOOKALIKE), w)
           for w in re.findall(r"[a-z]+\d[a-z\d]*", norm(text))]                 # 'rs3' = rsb
    if not kn:
        return 1.0, 1.0
    hit_i = sum(1 for a in kn if any(kw_sim(b, a) >= need for b in tw))
    hit_t = sum(1 for b in tw if any(kw_sim(b, a) >= need for a in kn))
    return hit_i / len(kn), (hit_t / len(tw) if tw else 1.0)


def same_product(text, name, need=KEYWORD_MIN):
    """At least one product word must match (a typo is fine: 'nai1s' ~ 'nails').
    This is what stops 'Nails 1/2 inch' from being sold as 'bolt 1/2 inch' just because the sizes look alike."""
    return keyword_score(text, name) >= need


# units in families: a row counted in one family is never an item counted in another ('6 bags' of tile adhesive is
# not 6 liters of paint). 'pcs' is the generic unit: it fits anything.
UNIT_FAMILY = {"liters": "liquid", "gallons": "liquid", "bottles": "liquid", "cans": "liquid",
               "bags": "dry", "sacks": "dry", "packs": "dry", "kg": "dry", "metric tons": "dry", "cu. meters": "dry",
               "meters": "length", "rolls": "roll", "sheets": "sheet", "pairs": "pair"}


def units_fit(receipt_unit, item_unit):
    a, b = unit_word(receipt_unit or ""), unit_word(item_unit or "")
    if not a or not b or a == b or "pcs" in (a, b):
        return True
    fa, fb = UNIT_FAMILY.get(a), UNIT_FAMILY.get(b)
    return not (fa and fb and fa != fb)


def rank_matches(text, items, loose=False, unit=None, kw_min=None):
    """Every item that could be this text, best first: [(item, score, rank, product-word score)].
    Only items that share a product word are considered (colours do not count) and, when the receipt gives a unit,
    whose unit fits it (units_fit). If that word matches and the sizes agree, the row is at least MIN_SHOW (shown as
    'Please check'), so 'Cement 5 sacks' still finds 'portland cement normal'.
    loose=True (handwritten rows, where the reader mixes the sizes with the price digits):
    a matching product word is enough for 'Please check'; the closest spelling still decides between sizes."""
    out = []
    nt = numbers_in(text)
    rare = rare_words(text, items)
    for it in items:
        kw = keyword_score(text, it["name"])
        hits = len(rare & item_words(it["name"])) if rare else 0
        if hits:
            kw = max(kw, 90)                       # a rare word of this item is on the row: it IS one of very few items
        if kw < (kw_min if kw_min is not None else KEYWORD_MIN_LOOSE if loose else KEYWORD_MIN):
            continue
        if unit and not units_fit(unit, it["unit"]):
            continue
        if not can_fits(text, it):
            continue
        sc = raw = similarity(text, it["name"])
        ni = numbers_in(it["name"])
        if not nt or nt == ni or nt < ni:          # same sizes, or the text gives part of them ('size 43' of 'Size 43 (#9)')
            sc = max(sc, MIN_SHOW)
        elif loose:
            raw = fuzz.token_set_ratio(norm(text), norm(it["name"])) if HAVE_RF else raw
            sc = max(sc, min(MIN_SHOW + 5, AUTO_OK - 1))
        if hits:
            sc = max(sc, MIN_SHOW)
        out.append((it, sc, (hits, sc, kw + raw) if loose else (hits, sc, raw), kw))
    out.sort(key=lambda x: x[2], reverse=True)      # rare words first; stable: equal ranks keep the Supply order
    return out


def best_match(text, items, loose=False):
    """Best item for this text, or (None, 0) when the shop does not carry it (see rank_matches)."""
    ranked = rank_matches(text, items, loose)
    return (ranked[0][0], ranked[0][1]) if ranked else (None, 0)


# =====================================================================
# PARSING: OCR boxes -> rows -> (item text, quantity)
# =====================================================================
UNIT_RE = re.compile(r"(?<![\d/.])(\d+(?:\.\d+)?)\s*(?:kgs?|kilos?)\b", re.I)   # '2kg', '2.5 kg'
QTY_UNIT_RE = re.compile(r"(?<![\d/.])(\d+(?:\.\d+)?)\s*(?:sacks?|bags?|pcs?|pieces?|boxes|box|rolls?|tons?|mt|cu\.?\s*m|"
                         r"liters?|litres?|gallons?|meters?|lengths?|sheets?|cans?)\b", re.I)   # '5 sacks', '3 mt'
# short units written after the LAST number of the row: '... 10 m', '... 1 lit', '... 2 btl', '... 1 pair' (a number
# right after 'x' is a size, '2 x 10 Mtr.'; '6m' in 'Steel bar 12mm 6m 15' is a length, the 15 after it is the quantity)
SHORT_QTY_RE = re.compile(r"(?<![\d/.])(?<![x×])(?<![x×] )(\d+(?:\.\d+)?)\s*(?:m|mtrs?|lit|ltrs?|gals?|btls?|bots?|bottles?|"
                          r"pairs?|packs?)\.?(?![a-z])(?!.*\d)", re.I)
NUM_RE = re.compile(r"(?<![\d/.])\d+(?:\.\d+)?(?![\d/])")                       # a number that is NOT part of '1/4'


def photo_tilt(boxes):
    """How many degrees the photo is turned (from the long text boxes; read_image stores each box's angle 'a').
    0 when the photo is straight enough, or when the boxes have no angle (old results, tests)."""
    angles = [b["a"] for b in boxes if "a" in b and b.get("w", 0) > 3 * (b.get("th") or b["h"])]
    if len(angles) < 3:
        return 0.0
    a = statistics.median(angles)
    return a if 0.8 <= abs(a) <= 25 else 0.0


def straighten(boxes):
    """Boxes of a tilted photo with y moved to where their text line crosses the left edge (x = 0), so boxes of one
    written line get the same y. The tilt is measured near each box (a photo taken at an angle is turned more at the
    top than at the bottom). Straight photos and boxes without angles come back unchanged."""
    tilt = photo_tilt(boxes)
    if not tilt:
        return boxes
    wide = [(b["y"], b["a"]) for b in boxes if "a" in b and b.get("w", 0) > 3 * (b.get("th") or b["h"])]
    win = 4 * statistics.median((b.get("th") or b["h"]) for b in boxes)
    out = []
    for b in boxes:
        near = [a for y, a in wide if abs(y - b["y"]) <= win]
        a = statistics.median(near) if len(near) >= 2 else tilt
        slope = math.tan(math.radians(max(-25.0, min(25.0, a))))
        out.append({**b, "y": b["y"] - slope * b.get("cx", b["x"] + b.get("w", 0) / 2), "h": b.get("th") or b["h"],
                    "oy": b["y"]})
    return out


def group_rows(boxes):
    """Boxes on the same horizontal line become one row of text, read left to right.
    A tilted photo (phone held at an angle) is straightened first, so the quantity written at the left of a row still
    lands on the same row as the item written further right."""
    if not boxes:
        return []
    boxes = straighten(boxes)
    boxes = sorted(boxes, key=lambda b: b["y"])
    med_h = statistics.median(b["h"] for b in boxes)
    rows = []
    for b in boxes:
        if rows and abs(b["y"] - rows[-1]["y"]) <= 0.6 * med_h:
            rows[-1]["boxes"].append(b)
            rows[-1]["y"] = sum(x["y"] for x in rows[-1]["boxes"]) / len(rows[-1]["boxes"])
        else:
            rows.append({"y": b["y"], "boxes": [b]})
    return [" ".join(x["text"] for x in sorted(r["boxes"], key=lambda b: b["x"])) for r in rows]


def clean_item(s):
    s = s.strip(" :-=")
    s = re.sub(r"\s+\d[\d,]*\.\d{2}\b.*$", "", s).strip()      # drop a trailing price like '190.00'
    return re.sub(r"[\s(\[{#~=_.,/|\\-]+$", "", s).strip()     # and loose pen marks at the end: '(', '~', '-'


# Handwritten DELIVERY RECEIPT rows look like  'QTY UNIT ARTICLE (PRICE) = TOTAL'  ('10 pc Tubular 2x6 (2000) = 20,000').
# The reader often turns a written 10 into 'to' / 'lo' and 'pc' into 'p', 'c' or 'PL'.
LEAD_QTY_RE = re.compile(r"^\s*(\d{1,4}(?:\.\d+)?|[tlI|][oO0])\s*\.?\s*(?:pcs?|p|pl|pz|c|ea|sets?|units?|lengths?|bars?)\.?\s+"
                         r"(?=[A-Za-z(#])", re.I)
PRICE_TOTAL_RE = re.compile(r"\(?\s*(\d[\d,]*)\s*(?:[)/|\]]\s*[=\-]|=)\s*[-~]?\s*[₱P]?\s*(\d[\d,]{0,9})")


def lead_qty(s):
    """(quantity, match) of a quantity written in the first column ('10 pc ...', 'to p ...' = 10), or (None, None)"""
    m = LEAD_QTY_RE.match(s)
    if not m:
        return None, None
    tok = m.group(1)
    if not tok[0].isdigit():
        tok = "1" + tok[1:].replace("o", "0").replace("O", "0")
    try:
        q = float(tok)
    except ValueError:
        return None, None
    return (q, m) if q > 0 else (None, None)


def price_total_qty(s, lead=None):
    """'(815) = 24,450' -> 30 (the total divided by the price). The reader may glue the price to the size written
    before it ('2x3 1.5 (815)' -> '2x315815/'), so the endings of that digit run are tried too, longest first.
    Returns (quantity or None, start of the price digits used or None, where the item text ends when no quantity
    came out: before a '(' price, else before the '=')."""
    m = PRICE_TOTAL_RE.search(s)
    if not m:
        return None, None, None
    no_q = m.start() if s[m.start()] == "(" else m.end(1)
    digits = m.group(1).replace(",", "")
    try:
        total = float(m.group(2).replace(",", ""))
    except ValueError:
        return None, None, no_q
    found = []
    for k in range(len(digits), 0, -1):
        p = int(digits[-k:])
        if p <= 0 or total <= 0:
            continue
        q = total / p
        if abs(q - round(q)) < 1e-6 and 1 <= round(q) <= 100000:
            found.append((k, int(round(q))))
    if not found:
        return None, None, no_q
    pick = next(((k, q) for k, q in found if lead is not None and q == lead), None)
    if pick is None:
        k, q = found[0]
        if k != len(digits) and k < 3:                      # a 1-2 digit ending of a longer number: too likely luck
            return None, None, no_q
        pick = (k, q)
    k, q = pick
    digit_end = m.end(1)
    # where the used price digits start in s (commas inside the run are rare in handwriting, ignore them)
    start = digit_end - k if "," not in m.group(1) else m.start(1)
    if start > 0 and s[start - 1] == "(":
        start -= 1
    return float(q), start, m.end(1)


QTY_MAX = 100000      # a bigger "quantity" is a TIN, a phone number or the printer's BIR number, never an order


def parse_line(line):
    """Return (item_text, quantity or None). Handles 'Nails 1/4 inch 2kg', 'Nails 1/4 inch: 2', '2kg Nails 1/4 inch',
    and table rows like 'Nails 1 inch 10 kg 130.00 1300.00'."""
    item, qty = _parse_line(line)
    if qty is not None and qty >= QTY_MAX:
        qty = None
    return item, qty


def _parse_line(line):
    s = line.strip()

    # 0) a table row that ends with UNIT PRICE and LINE TOTAL ('Cement 40kg 20 sacks 285.00 5700.00'): the quantity is
    #    the number in the row that makes price x qty = total (so the '40' of '40kg' is not taken as the quantity)
    money = list(re.finditer(r"\d[\d,]*\.\d{2}\b", s))
    if len(money) >= 2:
        try:
            price, total = (float(x.group().replace(",", "")) for x in money[-2:])
        except ValueError:
            price = total = 0
        if price > 0 and total > 0:
            q = total / price
            if abs(q - round(q)) < 0.01 and round(q) > 0:
                head = s[:money[-2].start()]
                hits = [n for n in re.finditer(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.]|\s*(?:kg|g|mm|cm|inch|in|ft|m)\b)", head, re.I)
                        if abs(float(n.group()) - q) < 0.01]
                if hits and re.search(r"[A-Za-z]", head[:hits[-1].start()]):
                    return clean_item(head[:hits[-1].start()]), float(round(q))

    # 0b) handwritten 'QTY UNIT ARTICLE (PRICE) = TOTAL' (delivery receipts): the quantity column, checked against
    #     price x qty = total when the reader got those numbers
    lq, lm = lead_qty(s)
    pq, p_start, eq_at = price_total_qty(s, lq)
    if lq is not None or eq_at is not None:
        body_from = lm.end() if lm else 0
        cut = p_start if pq is not None else eq_at
        body = s[body_from:cut] if cut is not None and cut > body_from else s[body_from:]
        body = re.sub(r"\s+\d+\s*[x×*@]\s*$", "", body)          # '... 3 x' left over from '3 x 125 = 375'
        qty = pq if pq is not None else lq
        um = UNIT_RE.search(body) or QTY_UNIT_RE.search(body)
        if qty is None and um:                                   # 'Cement 10 sacks (285) = ...' with no total read
            qty = float(um.group(1))
        if um and qty is not None and abs(float(um.group(1)) - qty) < 1e-9:
            body = body[:um.start()] + body[um.end():]           # 'Cement 10 sacks (285) = 2850' -> 'Cement'
        item = clean_item(body)
        if re.search(r"[A-Za-z]{2}", item):
            return item, qty                                     # qty None: the reader missed the quantity column

    # a printed AMOUNT at the end ('2 btl Paint thinner 180.00') is money, never the quantity
    cut = re.sub(r"\s+[₱P]?\s?\d[\d,]*\.\d{2}\s*$", "", s)
    if cut != s and re.search(r"[A-Za-z]", cut):
        s = cut
    m = UNIT_RE.search(s) or QTY_UNIT_RE.search(s) or SHORT_QTY_RE.search(s)   # 1) a number + kg / sacks / pcs ... = qty
    if m:
        before, after = s[:m.start()], s[m.end():]
        item = clean_item(before if re.search(r"[A-Za-z]", before) else after)
        can = re.search(r"(lit|litre|liter|ltr|gal|bottle|btl|bot)", m.group(0), re.I)
        if can:              # '3 gal Lacquer thinner': the can tells Gallon from Bottle, keep it for the matching
            item = f"{item} {dict(lit='liter', litre='liter', liter='liter', ltr='liter', gal='gallon').get(can.group(1).lower(), 'bottle')}"
        return item, float(m.group(1))

    if ":" in s:                                            # 2) 'item : 2'
        left, right = s.rsplit(":", 1)
        n = NUM_RE.search(right)
        if n and re.search(r"[A-Za-z]", left):
            return clean_item(left), float(n.group())

    words = list(re.finditer(r"[A-Za-z]+", s))              # 3) first number after the last word
    if words:
        tail = s[words[-1].end():]
        n = NUM_RE.search(tail)
        if n:
            return clean_item(s[:words[-1].end()]), float(n.group())
        lead = NUM_RE.match(s)                              # 4) number at the very start: '2 nails 1/4 inch'
        if lead:
            return clean_item(s[lead.end():]), float(lead.group())
    return clean_item(s), None


NOT_ITEMS = re.compile(r"\b(sub\s*total|total|vat|tax|discount|balance|change|cash|amount|date|invoice|receipt|account|"
                       r"sort\s*code|bank|postcode|tel|phone|contact|thank|registered|reg|item|qty|price)\b", re.I)
ORDER_ROW = re.compile(r"\b\d+(?:\.\d+)?\s*(?:kgs?|kilos?|pcs?|pieces?|bags?|sacks?|boxes|box|rolls?|tons?|mt|m|ft|l|liters?)\b"
                       r"|\d[\d,]*\.\d{2}", re.I)
# a row WITHOUT a quantity that is never an item: the shop's own name printed on top ("TRES NINOS HARDWARE" would
# otherwise look like 'Hardware Cloth'), the CASH SALES / CHARGE SALES line, the receipt's title, "Received by"...
# (a PAID stamp is skipped too: 'PAID' looks like 'Pail')
NOT_ITEM_ROW = re.compile(r"\btres\s*ni[ñnr]\w*s\b|construction\s*supply|hardware\s*(?:&|and)\s*construction|"
                          r"\bcash\s*sales?\b|\bcharge\s*sales?\b|\bsales\s*invoice\b|\bdelivery\s*receipt\b|"
                          r"\bofficial\s*receipt\b|\bthank\s*you\b|\breceived\b|\bvat\s*reg|\bnon[\s-]*vat\b|"
                          r"\bprop(?:rietor)?\b", re.I)
# rows that are never items WHATEVER number they carry (the 'Title No. 182581' of the address is not a quantity of
# 'Tile Grout', the 'Print No.036AU20240000005099' of the printer is not 2 trillion 'Paint Tray'):
#   the shop's own name and address printed on top, the form's labels (Sold to, Address, TIN, Terms, OSCA/PWD),
#   the footer (VATable Sales, Less: VAT, TOTAL AMOUNT DUE...), the printer's lines (BIR Authority to Print,
#   Date Issued, business forms, Tel.) and the receipt number ('No 320951', read as 'Ni320867' too)
JUNK_ROW = re.compile(
    r"\btres?[\s\-]*ni[ñnr]\w*s?\b|construction\s*sup|hardware\s*(?:&|and)\s*construction|\blot\s*no\b|\btitle\s*no\b|"
    r"gravel\s*(?:&|and)\s*sand|sand\s*trad|electrical\s*&|plumbing\s*mat|materials\s*trad|\bgood\s*order\b|"
    r"\bcond[il1]t[il1]on\b|received\s*the\s*above|\bauthori[sz]ed\b|"
    r"national\s*highway|puerto\s*princesa|\bpalawan\b|\bphils\b|\bsicsican\b|arambulo|\bprop(?:rietor)?\b\.?|"
    r"\bsold\s*to\b|^\W*address\b|\btin\s*(?:no\.?)?\s*[:#]|\breg\.?\s*tin\b|\bterms?\s*:|\bosca\b|\bsc\s*/\s*pwd\b|"
    r"\bpwd\s*id\b|\bsignature\b|print\s*name|\bsales\s*invoice\b|\bdelivery\s*receipt\b|\bofficial\s*receipt\b|"
    r"\bcash\s*sales?\b|\bcharge\s*sales?\b|^\W*date\s*:|"
    r"vatable|vat[\s-]*exempt|zero[\s-]*rated|\bvat\s*amount\b|total\s*sales|total\s*amount\s*due|\bamount\s*due\b|"
    r"\bless\s*:|\badd\s*:\s*vat|net\s*of\s*vat|"
    r"\bbir\b|authori[tl]y\s*to\s*print|date\s*issu|business\s*forms|copy\s*cent|accreditation|\bb[il]?[kl][il]?ts\b|"
    r"\bbooklets?\b|\btel\s*\.?\s*#|rizal\s*ave|\bppcity\b|non[\s-]*vat|\bvat\s*reg|"
    r"^\W*n[oi0º°]\.?\s*\d{5,}\W*$|^\W*[A-Za-z][A-Za-z .]{1,20}:\W*$", re.I)
HEADER_WORDS = re.compile(r"\b(?:qty|qnty|quantity|units?|descriptions?|articles?|particulars|items?|unit\s*price|"
                          r"prices?|amount|total|no)\b\.?", re.I)


def header_only(text):
    """'Qty. Unit DESCRIPTION Unit Price Amount': the printed column names and nothing else ('pty. Unit ARTICLES',
    the Qty read wrong, still is)"""
    if HEADER_WORDS.search(text) and not re.search(r"[A-Za-z]{2}", HEADER_WORDS.sub(" ", text)):
        return True
    words = re.findall(r"[A-Za-z']{2,}", text)
    return len(words) >= 2 and not re.search(r"\d", text) and \
        sum(1 for w in words if head_kind(w) or HEADER_WORDS.fullmatch(w)) >= len(words) - 1 >= 1


# the article table of a delivery receipt: rows after a header like 'Qty  Unit  ARTICLES' and before 'Total' / 'Received'
TABLE_HEAD = re.compile(r"\b(?:articles?|particulars|descriptions?)\b", re.I)
TABLE_END = re.compile(r"\b(?:total|received|prepared|checked|approved|signature|thank)\b", re.I)


def table_rows(rows):
    """index of the rows inside the article table (empty when the photo has no such header)"""
    start = next((i for i, t in enumerate(rows) if TABLE_HEAD.search(t)), None)
    inside = set()
    if start is not None:
        for i in range(start + 1, len(rows)):
            if TABLE_END.search(rows[i]):
                break
            inside.add(i)
    return inside


# =====================================================================
# SALES INVOICE: the printed item table   Qty | Unit | DESCRIPTION | Unit Price | Amount
# The columns are found from the printed header words, so each handwritten word is read in ITS column: the quantity
# from the Qty column, the price from the Price column, the money from the Amount column. Only what is written between
# the header and the footer (VATable Sales, Total Sales, TOTAL AMOUNT DUE) can be an item: the shop's name, its address,
# the TIN and the printer's BIR line never become items. A description written on two lines ('Cyclone Wire' /
# '5ft x 2x 6m') is ONE item. Numbers alone written in the table ('500 / 170') are a note (money received / change).
# Then the price decides: the item must sell at the price written on the receipt, otherwise the row is shown as
# "not in Supply" instead of being sold as a look-alike (₱330 'Tile Adhesive' is not the ₱120 'Bostik Adhesive').
# =====================================================================
HEAD_RE = {
    "qty": re.compile(r"^(?:qty|qt|qnty|quan|quantity|q'ty|pty|oty|gty|qtv|aty|qly)\W*$", re.I),
    "unit": re.compile(r"^(?:units?|uom|u/m|unt|umt|uni|unlt)\W*$", re.I),
    "desc": re.compile(r"^(?:descriptions?|articles?|particulars|items?)\W*$", re.I),
    "price": re.compile(r"^(?:prices?|u\.?price|@)\W*$", re.I),
    "amount": re.compile(r"^(?:amount|amt|total)\W*$", re.I),
}
FOOTER_RE = re.compile(r"vatable|vat[\s-]*exempt|zero[\s-]*rated|vat\s*amount|total\s*sales|total\s*amount|amount\s*due|"
                       r"\bless\s*:|\badd\s*:|net\s*of\s*vat|sub[\s-]*total|grand\s*total|^\W*total\b|received|"
                       r"prepared\s*by|checked\s*by|approved\s*by|nothing\s*follows|thank\s*you", re.I)
# the footer labels as the reader often spells them ('Zare-Rated Salas', 'VAT Exompt', '(VAT Inclusiva)')
FOOTER_PHRASES = ("vatable sales", "vat exempt sales", "zero rated sales", "vat amount", "total sales", "vat inclusive",
                  "amount net of vat", "less vat", "less discount", "withholding tax", "total amount due", "amount due",
                  "received the amount", "authorized signature", "solo parent", "nothing follows",
                  "received the above goods", "goods and services", "in good order", "customer signature")
TOTAL_RE = [(3, re.compile(r"total\s*amount\s*due", re.I)), (2, re.compile(r"grand\s*total", re.I)),
            (1, re.compile(r"^\W*(?:total|amount\s*due)\W*$", re.I))]
# letters the reader gives for handwritten digits (ONLY used inside the Qty / Price / Amount columns and for notes):
# 'aso' = 950, '50D' = 500, 'l' = 1, 'S' = 5, 'B' = 8, 'Z' = 2, 'G' = 6, 'T' = 7
DIGIT_LOOKALIKE = str.maketrans("oODQlI|i!sS$agqBzZbGT", "000011111555999822667")
QTY_LOOKALIKE = DIGIT_LOOKALIKE
ONE_LIKE = set("1lI|i!+tTfFjJ/\\)(][")         # a lone pen stroke in the Qty column is a 1 ('+' when a ruled line crosses it)
UNIT_CANON = {
    "pc": "pcs", "pcs": "pcs", "pce": "pcs", "piece": "pcs", "pieces": "pcs", "ea": "pcs", "each": "pcs", "unit": "pcs",
    "units": "pcs", "set": "pcs", "sets": "pcs", "bag": "bags", "bags": "bags", "sack": "sacks", "sacks": "sacks",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "box": "boxes", "boxes": "boxes", "bx": "boxes",
    "roll": "rolls", "rolls": "rolls", "rl": "rolls", "sheet": "sheets", "sheets": "sheets", "sht": "sheets",
    "length": "lengths", "lengths": "lengths", "lgth": "lengths", "lgths": "lengths", "len": "lengths",
    "m": "meters", "mtr": "meters", "mtrs": "meters", "meter": "meters", "meters": "meters", "mts": "meters",
    "cu.m": "cu. meters", "cum": "cu. meters", "liter": "liters", "liters": "liters", "ltr": "liters", "ltrs": "liters",
    "lit": "liters", "lt": "liters", "gal": "gallons", "gals": "gallons", "gallon": "gallons", "gallons": "gallons",
    "can": "cans", "cans": "cans", "btl": "bottles", "btls": "bottles", "bottle": "bottles", "bottles": "bottles",
    "pair": "pairs", "pairs": "pairs", "pack": "packs", "packs": "packs", "pck": "packs", "pk": "packs", "trip": "trips",
    "trips": "trips", "ton": "metric tons", "tons": "metric tons", "mt": "metric tons",
    # how the reader spells a handwritten unit: PCS ('ps', 'pis', 'pes', 'po', 'μs', 'pr'), BAG ('bap', 'bao', 'bcg',
    # 'bc5', 'dp6'), SACK ('sak', 'sck', 'sαk'), kilo ('kl', 'kl.', 'kil')
    "ps": "pcs", "pis": "pcs", "pes": "pcs", "pls": "pcs", "pz": "pcs", "po": "pcs", "pr": "pcs", "p": "pcs", "pcz": "pcs",
    "μs": "pcs", "µs": "pcs", "μ": "pcs", "pcs.": "pcs", "pce.": "pcs", "pq": "pcs",
    "bap": "bags", "bao": "bags", "bog": "bags", "baq": "bags", "bay": "bags", "bcg": "bags", "bcs": "bags", "bc5": "bags",
    "bcz": "bags", "ba6": "bags", "bac": "bags", "ba": "bags", "bg": "bags", "dp6": "bags", "da6": "bags", "b4g": "bags",
    "baa": "bags", "bags.": "bags", "bcy": "bags", "bes": "bags", "boc": "bags", "bax": "bags",
    "sak": "sacks", "sck": "sacks", "sαk": "sacks", "sαx": "sacks", "sax": "sacks", "sacx.": "sacks", "sac": "sacks", "sacc": "sacks", "sacl": "sacks",
    "scks": "sacks", "saks": "sacks", "sk": "sacks", "cak": "sacks", "sacx": "sacks", "sach": "sacks",
    "kl": "kg", "kls": "kg", "kil": "kg", "kilo.": "kg", "klo": "kg",
}
# common hardware words, to spell a row the shop does not carry yet ('AOHECLIVE' -> 'Adhesive'); the words of the
# Supply items are used first
HARDWARE_WORDS = (
    "adhesive tile tiles grout cement sand gravel hollow block blocks wire nails nail screw screws bolt nut washer "
    "pipe elbow tee coupling valve faucet paint thinner brush roller tray primer enamel latex gloss lacquer epoxy putty "
    "sealant silicone plywood lumber wood board marine phenolic galvanized steel bar rebar deformed angle channel "
    "purlins tubular flat sheet roofing gutter ridge cyclone barbed tie hose pail bucket shovel hammer saw drill disc "
    "cutting grinding welding rod electrode tape electrical outlet switch bulb socket breaker conduit pvc sanitary "
    "toilet bowl lavatory sink tank door knob lock hinge padlock skimcoat waterproofing white black gray grey green "
    "red blue yellow brown beige orange cream mixer bond bonding plaster gypsum ceiling insulation mesh netting "
    # seen on the shop's own receipts
    "armour wheel barrow wheelbarrow rsb smartbond smart crusher screen fine coarse ordinary hardiflex umbrella "
    "nipple reducer adaptor adapter male female teflon molding moulding gang clamp cable yero ultimate plain matting "
    "longspan omega chb cwn ppr gi bit plus sack sacks apron reducer union clip boots rake digger"
).split()
# small words kept as they are in a name (never 'corrected' into a product word: 'with' is not 'Switch')
STOP_WORDS = {"with", "and", "for", "of", "the", "per", "x", "in", "on", "by", "to", "at", "w", "no", "plus"}


# a printed column name read with a typo ('DESCRIPTI0N', 'Amonnt', 'PARTICULAR5', 'Quantlty') still counts (long words only)
HEAD_FUZZY = {"desc": ("description", "articles", "particulars"), "price": ("price",), "amount": ("amount",),
              "qty": ("quantity",)}


def head_kind(text, kinds=None):
    """which column a printed header word names: 'qty' | 'unit' | 'desc' | 'price' | 'amount' | None"""
    for k, rx in HEAD_RE.items():
        if (kinds is None or k in kinds) and rx.match(text):
            return k
    w = re.sub(r"[^a-z0-9]", "", text.lower()).translate(str.maketrans("015", "ols"))
    if len(w) >= 5:
        for k, words in HEAD_FUZZY.items():
            if (kinds is None or k in kinds) and any(word_sim(w, x) >= 80 for x in words):
                return k
    return None


def is_footer(text):
    """a printed footer label of the form (VATable Sales, Total Sales (VAT Inclusive), TOTAL AMOUNT DUE...), also when
    the reader spelled it a little wrong"""
    t = str(text or "")
    if FOOTER_RE.search(t):
        return True
    low = re.sub(r"[^a-z ]", " ", t.lower())
    low = re.sub(r"\s+", " ", low).strip()
    if len(low) < 7 or not HAVE_RF:
        return False
    return any(fuzz.partial_ratio(p, low) >= (80 if len(p) >= 15 else 86) for p in FOOTER_PHRASES if len(p) <= len(low) + 3)


def _tokens(b):
    """the words of one box with their estimated left / right x (the box width shared out by the letters)"""
    t = str(b.get("text") or "")
    n = max(1, len(t))
    w = b.get("w") or (b.get("th") or b["h"]) * n * 0.55
    return [{"text": m.group(), "x0": b["x"] + w * m.start() / n, "x1": b["x"] + w * m.end() / n, "y": b["y"],
             "h": b.get("th") or b["h"], "box": b} for m in re.finditer(r"\S+", t)]


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def unit_word(s):
    """'PC' -> 'pcs', 'bags' -> 'bags', 'Mtrs.' -> 'meters', 'Bap' -> 'bags'; None when it is not a unit"""
    raw = re.sub(r"[^a-z0-9μµα.]", "", (s or "").lower()).strip(".").replace("α", "a")
    if not raw:
        return None
    if raw in UNIT_CANON:
        return UNIT_CANON[raw]
    k = re.sub(r"[^a-z.]", "", raw).strip(".")
    if not k:
        return None
    if k in UNIT_CANON and (k == raw or len(k) >= 2):
        return UNIT_CANON[k]
    if len(k) >= 4:                                  # 'bottels', 'gallns'
        best = max(UNIT_CANON, key=lambda u: word_sim(k, u))
        if word_sim(k, best) >= 80:
            return UNIT_CANON[best]
    return None


def qty_cell(text):
    """(quantity, unit) from what is written in the Qty column: '1', '10', '2.5', '1/2', '1 1/2', '2pcs', '1/u' (1/4),
    a lone stroke read as 'l', 'I', '+' (= 1), 'to' / 'lo' (= 10). (None, None) when it is not a quantity."""
    s = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;-=~")
    if not s:
        return None, None
    unit = None
    m = re.match(r"^(.*?[\dA-Za-z|!+/\\()\[\]])\s*([A-Za-zμµα]{1,5}\.?)$", s)        # '2pcs', '2 bags', '4sack'
    if m and unit_word(m.group(2)) and re.search(r"\d", m.group(1)):
        unit, s = unit_word(m.group(2)), m.group(1).strip()
    else:                                          # the unit's first letter read as a digit: '45αx' = 4 sαx, '255k' = 25 sk
        m = re.fullmatch(r"(\d{1,5})\s*([A-Za-zμµα][A-Za-zμµα0-9.]{0,4})?", s)
        if m:
            head, rest = m.group(1), m.group(2) or ""
            for i in range(len(head), 0, -1):
                tail = head[i:] + rest
                if not tail:
                    continue
                alt = ("s" + tail[1:]) if tail[0] in "5S" else ("b" + tail[1:]) if tail[0] in "8B" else tail
                u = unit_word(tail) if not tail[0].isdigit() else None
                u = u or (unit_word(alt) if alt != tail else None)
                if u:
                    unit, s = u, head[:i]
                    break
    if re.fullmatch(r"[1lI|!]\s*[pPrR][cC][sS]?\.?", s):                   # '1 PC.' read as 'IRC.'
        return 1.0, "pcs"
    m = re.fullmatch(r"[tTlI|+][oO0D]\s*([A-Za-z]{1,4}\.?)", s)            # 'lop' = 10 p
    if m and unit_word(m.group(1)):
        return 10.0, unit_word(m.group(1))
    if len(s) == 1 and s in ONE_LIKE:
        return 1.0, unit
    if re.fullmatch(r"[tTlI|+][oO0D]", s):
        return 10.0, unit
    if re.fullmatch(r"[1lI|][lI|/\\]?[uU]\.?", s) or re.fullmatch(r"[1lI|][/\\][4uUyY]\.?", s):
        return 0.25, unit                          # '1/4' with a sloppy 4: 'Ilu', '1/u', 'I/y'
    if re.fullmatch(r"[1lI|][/\\][2zZ]\.?", s):
        return 0.5, unit
    if not re.search(r"\d", s):                      # 'bag', 'PC' in the Qty column: not a number
        return None, unit
    t = s.translate(QTY_LOOKALIKE).replace(" ", "")
    m = re.fullmatch(r"(\d{1,3})(\d)/(\d{1,2})", t) if " " in s else None   # '1 1/2' -> '11/2'
    if m and int(m.group(3)) > int(m.group(2)) > 0:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3)), unit
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
    if m and int(m.group(2)) > int(m.group(1)) > 0:
        return int(m.group(1)) / int(m.group(2)), unit
    if re.fullmatch(r"\d{1,5}(?:\.\d{1,3})?", t) and 0 < float(t) < QTY_MAX:
        return float(t), unit
    m = re.match(r"^(\d{1,4})(?=[^\d/.]|$)", t)      # '13pr' -> 13 ('pr' was not a known unit)
    if m and 0 < float(m.group(1)) < QTY_MAX and re.fullmatch(r"\d{1,4}[A-Za-z.]{1,4}", t):
        return float(m.group(1)), unit
    return None, unit


def money_cell(text, pick="last"):
    """(amount, real) from what is written in a Price / Amount column: '950', '1,250.00', 'P950', '950-',
    'aso' (= 950). real=False when no real digit was read (the value is then only trusted if another number agrees)."""
    vals = []
    for tok in re.findall(r"\S+", str(text or "")):
        t = re.sub(r"^[₱#@=(~\-*]+|^P(?=[\dA-Za-z])", "", tok)
        t = re.sub(r"[\-=~.,/_:;)*]+$", "", t)
        if not t or len(t) > 12:
            continue
        real = bool(re.search(r"\d", t))
        if not real and len(t) < 2:
            continue
        f = t.translate(DIGIT_LOOKALIKE)
        f = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", f)
        f = re.sub(r"(?<=\d)\.(?=\d{3}$)", "", f)              # '1.730' written with a dot for the comma
        if re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", f) and float(f) > 0:
            vals.append((float(f), real, len(re.sub(r"\D", "", f))))
    if not vals:
        return None, False
    v = max(vals, key=lambda x: (x[2], vals.index(x))) if pick == "longest" else (vals[-1] if pick == "last" else vals[0])
    return v[0], v[1]


def money_tol(amount):
    """how far a written amount may be from price x quantity: pesos are rounded (1/4 kilo at ₱75 = 18.75, written 19)"""
    return 0.6


def nice_qty(price, amount, most=1000):
    """the quantity that makes price x quantity = amount: a whole number, or a quarter / half / three quarters
    (1/4 kilo of nails at ₱75 = ₱19). None when no such quantity."""
    if not price or not amount or price <= 0 or amount <= 0:
        return None
    k = amount / price
    tol = money_tol(amount)
    q = round(k)
    if 1 <= q <= most and abs(q * price - amount) <= tol:
        return float(q)
    q4 = round(k * 4) / 4
    if (0 < q4 < 1 or q4 == 1.5) and abs(q4 * price - amount) <= tol:      # 1/4, 1/2, 3/4 kilo; 1 1/2
        return q4
    return None


def _digits(x):
    return str(int(round(x))) if x is not None and abs(x - round(x)) < 1e-6 else None


def one_digit_off(a, b):
    """True when the two whole numbers differ by ONE misread digit: 2600 / 7600, 275 / 75, 560 / 160"""
    sa, sb = _digits(a), _digits(b)
    if not sa or not sb or sa == sb or abs(len(sa) - len(sb)) > 1:
        return False
    if len(sa) == len(sb):
        return sum(x != y for x, y in zip(sa, sb)) == 1
    lo, hi = (sa, sb) if len(sa) < len(sb) else (sb, sa)
    return any(hi[:i] + hi[i + 1:] == lo for i in range(len(hi)))


QTY_AS_FRACTION = {14: 0.25, 12: 0.5, 34: 0.75, 4: 0.25, 2: 0.5}     # '1/4' read as '14' (or only its 4)


def repair_row(q, q_read, p, a, locked=False):
    """price x quantity = amount, mending the ONE number the reader got wrong. Tried in this order (the Amount cell
    has the most digits, so it is the likeliest misread; the Qty cell the least):
      the amount: 13 x 20 = 260 read as '200';  the price: 1100 / 4 = 275 read as '75' or '27';
      the quantity: 75 x 0.25 = 19 with '1/4' read as '14', or no quantity read at all.
    Returns (q, p, a)."""
    tol = money_tol(a)
    if p and a and q and abs(q * p - a) <= tol:
        return q, p, a
    if q_read and p and a and not locked and one_digit_off(q * p, a) and len(_digits(a) or "") >= 3 \
            and len(_digits(q * p) or "") == len(_digits(a) or ""):
        return q, p, round(q * p, 2)
    if q_read and a and q and one_digit_off(a / q, p) and abs(a / q - round(a / q)) < 1e-6:
        return q, round(a / q, 2), a
    if p and a:
        k = nice_qty(p, a)
        if k is not None and (not q_read or one_digit_off(k, q) or QTY_AS_FRACTION.get(int(q) if q == int(q) else -1) == k
                              or k < 1 or q >= 500 or (k == 1 and abs(p - a) < 0.01)):
            return k, p, a
        if k is None:                              # the price lost / changed a digit: 1650 / 275 = 6 (read '27')
            cands = set()
            sp = _digits(p)
            if sp:
                for i in range(len(sp) + 1):
                    for d in "0123456789":
                        for v in (sp[:i] + d + sp[i:], sp[:i] + d + sp[i + 1:]):
                            if v and not v.startswith("0") and v != sp:
                                k2 = nice_qty(float(v), a)
                                if k2 is not None and k2 >= 1 and (not q_read or one_digit_off(k2, q) or k2 == q):
                                    cands.add((float(v), k2))
            if cands:
                best = sorted(cands, key=lambda c: (-len(_digits(c[0]) or ""), c[1]))
                if len(best) == 1 or len(_digits(best[0][0]) or "") > len(_digits(best[1][0]) or ""):
                    p2, k2 = best[0]
                    return k2, p2, a
    if p and a and a < p / 10 and _digits(a * 1000):     # '2.30' for 2,300: the comma read as a point, a 0 lost
        for m in (1000, 100):
            k = nice_qty(p, a * m)
            if k is not None and k >= 1:
                return k, p, a * m
    return q, p, a


def split_glued(digits, qty=None, total=None, cut=None):
    """'7519' = '75' and '19' written so close the reader made one number of the Price and Amount cells.
    Returns (price, amount) when one cut makes price x quantity = amount (or the amount is the receipt's total).
    cut = where the column line runs through the digits (only cuts next to it are tried)."""
    best = None
    for i in range(1, len(digits)):
        if cut is not None and abs(i - cut) > 1:
            continue
        a_txt = digits[i:]
        if a_txt.startswith("0"):
            continue
        p, a = int(digits[:i]), int(a_txt)
        if p <= 0 or a <= 0:
            continue
        if qty and abs(qty * p - a) <= money_tol(a):
            ok = 3
        elif nice_qty(p, a, most=1000):
            ok = 2
        elif total and abs(a - total) <= money_tol(total):
            ok = 1
        else:
            continue
        key = (ok, -abs(len(digits[:i]) - len(a_txt)))
        if best is None or key > best[0]:
            best = (key, float(p), float(a))
    return (best[1], best[2]) if best else (None, None)


def find_item_table(boxes):
    """The printed item table of a sales invoice, or None when the photo has no header row with a Price or Amount
    column next to a DESCRIPTION / ARTICLES / PARTICULARS or a Qty / Quantity column (a delivery receipt or a plain list
    is read the old way). When the description header was not readable ('Prem Daverioion / Natare of Sarvke') its
    column is the widest gap between the columns that were found (left of Quantity on the Electrical & Plumbing form).
    Returns {'cols': [(kind, x_from, x_to)], 'head': {kind: header word}, 'top': y, 'bottom': y, 'boxes': straightened}"""
    sb = straighten(boxes)
    if not sb:
        return None
    toks = [t for b in sb for t in _tokens(b)]
    best = None
    for a in (t for t in toks if head_kind(t["text"], ("desc", "qty", "price", "amount"))):
        found, extra = {}, []
        for t in toks:
            if abs(t["y"] - a["y"]) > 1.6 * max(a["h"], 8):
                continue
            k = head_kind(t["text"])
            if k:
                found.setdefault(k, []).append(t)
        for u in list(found.get("unit", [])):         # the 'Unit' printed above 'Price' belongs to the price column
            for p in found.get("price", []):
                if (_overlap(u["x0"], u["x1"], p["x0"], p["x1"]) >= 0.4 * min(u["x1"] - u["x0"], p["x1"] - p["x0"])
                        or (u["box"] is p["box"] and u["x1"] <= p["x0"] + 1)):
                    found["unit"].remove(u)
                    extra.append(u)
                    break
        found = {k: v for k, v in found.items() if v}
        delivery = "desc" in found and ("qty" in found or "unit" in found) and any(
            re.match(r"(?i)artic|partic", t["text"]) for t in found["desc"])
        if "price" not in found and "amount" not in found and not delivery:
            continue
        if "desc" not in found and "qty" not in found:
            continue
        if delivery and "qty" not in found:              # 'Qty' unreadable on a delivery receipt: left of 'Unit'
            u = min(found["unit"], key=lambda t: t["x0"])
            w = max(8.0, u["x1"] - u["x0"])
            found["qty"] = [{"text": "Qty", "x0": u["x0"] - 1.6 * w, "x1": u["x0"] - 0.4 * w, "y": u["y"], "h": u["h"],
                             "box": u["box"]}]
        if "desc" not in found and len(found) < 3:       # 'Total Amount' in the footer is not a header row
            continue
        score = (len(found) + (1 if "desc" in found else 0), -a["y"])
        if best is None or score > best[0]:
            best = (score, a, found, extra)
    if best is None:
        return None
    _, a, found, extra = best
    cols = []
    for k, ts in found.items():
        t = min(ts, key=lambda t: abs(t["y"] - a["y"]))
        x0, x1 = t["x0"], t["x1"]
        if k == "price":
            for u in extra:
                x0, x1 = min(x0, u["x0"]), max(x1, u["x1"])
        if k == "desc":
            # 'Item Description / Nature of Service' is ONE heading: the column is as wide as all of it (the words after
            # DESCRIPTION in the same printed box, up to a word that names another column). Using only 'Item' put the
            # column line far too left and long descriptions spilled into the Quantity column.
            for d_ in ts:
                if abs(d_["y"] - t["y"]) > 0.8 * max(t["h"], 8):
                    continue
                x0 = min(x0, d_["x0"])
                x1 = max(x1, d_["x1"])
                after = False
                for u in _tokens(d_["box"]):
                    if not after:
                        after = abs(u["x0"] - d_["x0"]) < 0.5 and u["text"] == d_["text"]
                        continue
                    kk = head_kind(u["text"])
                    if kk and kk != "desc":
                        break
                    x1 = max(x1, u["x1"])
        cols.append((k, x0, x1, t))
    cols.sort(key=lambda c: (c[1] + c[2]) / 2)
    if "desc" not in found:                           # the description: the widest gap between the found columns
        left = min(b["x"] for b in sb)
        gaps = [(cols[0][1] - left, left, cols[0][1])] + [(c2[1] - c1[2], c1[2], c2[1]) for c1, c2 in zip(cols, cols[1:])]
        g = max(gaps)
        if g[0] <= 0:
            return None
        cols.append(("desc", g[1] + 0.1 * g[0], g[2] - 0.1 * g[0], None))
        cols.sort(key=lambda c: (c[1] + c[2]) / 2)
    bounds = []
    for c1, c2 in zip(cols, cols[1:]):                # each column reaches halfway to its neighbours
        bounds.append((c1[2] + c2[1]) / 2 if c1[2] <= c2[1] else ((c1[1] + c1[2]) / 2 + (c2[1] + c2[2]) / 2) / 2)
    ranges = [(c[0], bounds[i - 1] if i else -math.inf, bounds[i] if i < len(bounds) else math.inf)
              for i, c in enumerate(cols)]
    used = [c[3] for c in cols if c[3] is not None] + extra
    top = max(t["y"] + t["h"] / 2 for t in used)
    head_boxes = {id(t["box"]) for t in used}
    head_h = statistics.median(t["h"] for t in used)
    below = sorted((b for b in sb if b["y"] > top + 0.2 * head_h and id(b) not in head_boxes), key=lambda b: b["y"])
    foot = next((b for b in below if is_footer(b.get("text"))), None)
    bottom = foot["y"] - foot["h"] / 2 if foot else math.inf
    return {"cols": ranges, "head": {c[0]: c[3] for c in cols if c[3] is not None}, "top": top, "bottom": bottom,
            "boxes": sb, "head_boxes": head_boxes, "money": "price" in found or "amount" in found}


def _col_at(tab, x):
    return next((k for k, lo, hi in tab["cols"] if lo <= x < hi), tab["cols"][-1][0])


def table_lines(tab):
    """The written lines inside the table, top to bottom: [{'y', 'cells': {kind: text}, 'pieces': [...]}]"""
    inside = [b for b in tab["boxes"] if id(b) not in tab["head_boxes"] and tab["top"] < b["y"] < tab["bottom"]]
    if not inside:
        return []
    med_h = statistics.median(b["h"] for b in inside)
    if tab["bottom"] < math.inf:                   # numbers written just above the footer belong to the footer
        inside = [b for b in inside if b["y"] < tab["bottom"] - 0.4 * med_h]
    pieces = []
    for b in inside:
        x0 = b["x"]
        x1 = x0 + (b.get("w") or 0)
        spans = [(_overlap(x0, x1, lo, hi), k) for k, lo, hi in tab["cols"]]
        if len(str(b["text"]).split()) <= 1 or max(spans)[0] >= 0.75 * max(1.0, x1 - x0):
            k = _col_at(tab, b.get("cx", (x0 + x1) / 2))
            over = {kk: o for o, kk in spans}
            glued = None
            if re.fullmatch(r"[\d,.]{4,}", str(b["text"]).strip()) and \
                    min(over.get("price", 0), over.get("amount", 0)) >= 0.2 * max(1.0, x1 - x0):
                line_x = next(hi for kk, lo, hi in tab["cols"] if kk == "price")      # the Price | Amount column line
                n = len(re.sub(r"\D", "", str(b["text"])))
                glued = (str(b["text"]).strip(), round(n * (line_x - x0) / max(1.0, x1 - x0)))
            pieces.append({"kind": k, "text": str(b["text"]).strip(), "x": x0, "y": b["y"], "h": b["h"], "box": b,
                           "glued": glued})
        else:                                      # one box over several columns ('1 pc Cyclone wire'): word by word
            parts = [(_col_at(tab, (t["x0"] + t["x1"]) / 2), t) for t in _tokens(b)]
            has_desc = any(k == "desc" for k, _ in parts)
            for k, t in parts:
                if k in ("price", "amount") and has_desc and not re.search(r"\d", t["text"]):
                    k = "desc"                     # a long description running over the Price line: still words
                pieces.append({"kind": k, "text": t["text"], "x": t["x0"], "y": b["y"], "h": b["h"], "box": b})
    lines = []
    for p in sorted(pieces, key=lambda p: p["y"]):
        if lines and abs(p["y"] - lines[-1]["y"]) <= 0.6 * med_h:
            lines[-1]["pieces"].append(p)
            lines[-1]["y"] = sum(x["y"] for x in lines[-1]["pieces"]) / len(lines[-1]["pieces"])
        else:
            lines.append({"y": p["y"], "pieces": [p]})
    for L in lines:
        cells = {}
        for p in sorted(L["pieces"], key=lambda p: p["x"]):
            if p["kind"] == "desc" and p["box"].get("score", 1) < 0.5 and len(re.sub(r"[^A-Za-z]", "", p["text"])) <= 2 \
                    and not re.search(r"\d", p["text"]) and len(L["pieces"]) > 1:
                continue                           # a scrap the reader was unsure of ('AZ' from a stamp's ink)
            cells.setdefault(p["kind"], []).append(p["text"])
        L["cells"] = {k: " ".join(v) for k, v in cells.items()}
        L["h"] = statistics.median(p["h"] for p in L["pieces"])
    return lines


def _numeric_text(s):
    """'50D 170' -> [500, 170]; None when the text has words in it"""
    out = []
    for tok in re.findall(r"\S+", s or ""):
        t = tok.strip("()[]/|=-~.,:;*")
        if not t:
            continue
        if not re.search(r"\d", t):
            return None
        v, _ = money_cell(t)
        if v is None:
            return None
        out.append(v)
    return out or None


def _noise_line(L, tilt):
    """a line of the table that is no writing of the item: a stamp across the table ('RECEIVED BY' read as 'CIUEVIO',
    'OU'), the reader unsure of every word, or the words turned against the page"""
    words = [p for p in L["pieces"] if not re.search(r"\d", p["text"])]
    if not words or len(words) != len(L["pieces"]):
        return False
    text = " ".join(p["text"] for p in words)
    if len(re.sub(r"[^A-Za-z]", "", text)) <= 2:
        return True
    for p in words:
        b = p["box"]
        sc = b.get("score", 1.0)
        turned = "a" in b and abs(b["a"] - tilt) > 6
        if not (sc < 0.5 or (turned and sc < 0.8)):
            return False
    return True


def table_entries(lines, tilt=0.0, money_in_desc=False):
    """Join the written lines into items. A line with something in the Qty / Unit column starts an item; a line with
    only a description continues the item above ('Cyclone Wire' / '5ft x 2x 6m'), and so does a price or amount
    written a little lower. Lines with numbers only are notes ('500 170' = paid 500, change 170). Stamp text and
    unreadable scraps are left out (_noise_line)."""
    entries, notes, cur = [], [], None
    med_h = statistics.median(L["h"] for L in lines) if lines else 0
    for L in lines:
        c = L["cells"]
        desc = c.get("desc", "")
        head = c.get("qty") or c.get("unit")
        money = [k for k in ("price", "amount") if c.get(k)]
        if not head and not money and desc and _noise_line(L, tilt):
            continue
        if not head and (not desc or _numeric_text(desc)):
            if not desc and money and cur is not None and not any(cur["cells"].get(k) for k in money) \
                    and L["y"] - cur["y_last"] <= 2.6 * med_h:
                for k in money:                    # an amount written a bit lower than its item
                    cur["cells"][k] = c[k]
                cur["lines"].append(L)
                cur["y_last"] = L["y"]
            else:
                notes.append(L)
            continue
        cur_numbers = cur is not None and any(cur["cells"].get(k) for k in ("qty", "unit", "price", "amount"))
        if money_in_desc and cur is not None and (
                MONEY_TAIL.search(cur["cells"].get("desc", ""))
                or re.search(r"\s\d{2,}[\d,]*\W*$", cur["cells"].get("desc", ""))):
            cur = None                             # delivery receipt: '... (115) = 1,725' / '... 200' ends its article
        new = (cur is None or (head and (cur_numbers or L["y"] - cur["y_last"] > 1.7 * med_h))
               or (money and any(cur["cells"].get(k) for k in ("price", "amount")))
               or L["y"] - cur["y_last"] > 2.6 * med_h)
        if new:
            cur = {"lines": [L], "cells": dict(c), "y_last": L["y"]}
            entries.append(cur)
        else:
            for k, v in c.items():
                cur["cells"][k] = f"{cur['cells'].get(k, '')} {v}".strip()
            cur["lines"].append(L)
            cur["y_last"] = L["y"]
    return entries, notes


def invoice_total(sb):
    """the money written next to TOTAL AMOUNT DUE (or Grand total / Total), or None"""
    best = None
    for b in sb:
        text = str(b.get("text") or "")
        rank = next((r for r, rx in TOTAL_RE if rx.search(text)), None)
        if rank is None or (best and best[0] >= rank):
            continue
        right = b["x"] + 0.5 * (b.get("w") or 0)
        near = []
        for x in sb:
            if x is b or x["x"] < right or abs(x["y"] - b["y"]) > 1.2 * max(b["h"], x["h"]):
                continue
            v, real = money_cell(x["text"])
            if v is not None and real:
                near.append((abs(x["y"] - b["y"]), v))
        if near:
            best = (rank, min(near)[1])
    return best[1] if best else None


def note_numbers(sb, tab, used):
    """Amounts written by hand outside the item rows: money received (often circled) and the change, in the empty
    rows, in the VATable Sales boxes or under 'Received the amount of'. Footer values (Total Sales 2142.85, Less VAT,
    TOTAL AMOUNT DUE under the Amount column) and printed numbers (invoice No., TIN, BIR) are not notes."""
    money_x = min((lo for k, lo, hi in tab["cols"] if k in ("price", "amount") and lo > -math.inf), default=math.inf)
    nums = []
    for b in sb:
        if id(b) in used or b["y"] <= tab["top"]:
            continue
        t = re.sub(r"[()\s]", "", str(b.get("text") or ""))
        if not re.fullmatch(r"[₱P]?\d[\d,.]{0,7}[/\-=]?", t) or re.search(r"\d\.\d\d$", t) or b.get("score", 1) < 0.5:
            continue                               # only a written amount ('2,500', '(1,000)'); 2142.85 is the VAT line
        if b["y"] >= tab["bottom"] and b.get("cx", b["x"]) >= money_x:
            continue                               # under the Amount column below the table: the footer's values
        v, real = money_cell(t, pick="longest")
        if v is not None and real and v >= 1:
            nums.append(v)
    return nums


def pay_pair(nums, total, single=True):
    """(paid, change) for this total: '2,500' and '100' (2500 - 100 = 2400); single=True also takes a lone round amount
    at or above the total ('1000' for 950: change 50). (None, None) when nothing fits."""
    if not total:
        return None, None
    for x in nums:
        for y in nums:
            if x > y > 0 and abs(x - y - total) <= 0.6:
                return x, y
    if single:
        # the circled amount received is often unreadable, the change is not: 1730 + 270 = 2000, a round amount
        best = None
        for y in nums:
            x = total + y
            if 0 < y < 5000 and abs(x - round(x)) < 0.01 and x % 100 == 0:
                rank = 3 if x % 1000 == 0 else 2 if x % 500 == 0 else 1
                if best is None or rank > best[0]:
                    best = (rank, x, y)
        if best is not None:
            return float(best[1]), best[2]
        for x in nums:
            if total <= x <= total + 5000 and (x % 50 == 0 or abs(x - total) < 0.01):
                return x, round(x - total, 2)
    return None, None


def money_notes(sb, tab, used, total):
    """(paid, change) written anywhere on the receipt (note_numbers + pay_pair)"""
    return pay_pair(note_numbers(sb, tab, used), total)


def _qty_pieces(e, tab):
    """(quantity, unit) of an item from what is written in its Qty (and Unit) column. When the Qty cell holds several
    things ('3' from the description's 3" running over the column line, and '2R.'), the one nearest the middle of the
    printed Qty heading wins."""
    L = e["lines"][0]
    ps = [p for p in L["pieces"] if p["kind"] == "qty"]
    head = tab["head"].get("qty")
    if len(ps) > 1 and head is not None:
        mid = (head["x0"] + head["x1"]) / 2
        found = []
        for p in ps:
            q, u = qty_cell(p["text"])
            if q is not None:
                cx = p["box"].get("cx", p["x"]) if len(str(p["box"].get("text", "")).split()) <= 1 else p["x"]
                found.append((u is None, abs(cx - mid), q, u))
        if found:
            _, _, q, u = min(found)
            return q, u
    return qty_cell(e["cells"].get("qty", ""))


def _settle(e, locked=False):
    """price x quantity = amount for one row, after the receipt's total had its say (repair_row)"""
    q, p, a = repair_row(e["qty"], e["qty_read"], e["price"], e["amount"], locked)
    e["qty"], e["price"], e["amount"] = q, p, a
    if p is None and a and q:
        e["price"] = round(a / q, 2)
    if a is None and p and q:
        e["amount"] = round(p * q, 2)


MONEY_TAIL = re.compile(r"[(\[{]?\s*(?P<p>\d[\d,.]*)\s*[)\]}/|]\s*[=\-~:]*\s*[₱P]?\s*(?P<a>\d[\d,]*(?:\.\d{1,2})?)\W*$"
                        r"|[(\[{]\s*(?P<p2>\d[\d,.]*)\s*[)\]}/|]?\s*[=\-~:]+\s*[₱P]?\s*(?P<a2>\d[\d,]*(?:\.\d{1,2})?)\W*$"
                        r"|(?P<p3>\d[\d,.]*)\s*[=:]+\s*[₱P]?\s*(?P<a3>\d[\d,]*(?:\.\d{1,2})?)\W*$"
                        r"|[=]+\s*[₱P]?\s*(?P<a4>\d[\d,]*(?:\.\d{1,2})?)\W*$")


def desc_money(desc, qty):
    """(description, price, amount) when the money is written after the article ('Tubular 2x6 1.5 (2000) = 20,000',
    the reader often loses the '(': '1.52000)20,000'). The price digits glued to a size before them are cut where
    quantity x price = amount ('1.5' + '2000')."""
    m = MONEY_TAIL.search(desc or "")
    if not m:
        return desc, None, None
    p_txt = m.group("p") or m.group("p2") or m.group("p3")
    a_txt = m.group("a") or m.group("a2") or m.group("a3") or m.group("a4")
    amount, _ = money_cell(a_txt)
    if amount is None:
        return desc, None, None
    head = desc[:m.start()]
    if not p_txt:
        return head.strip(" =-~"), None, amount
    digits = re.sub(r"[^\d]", "", p_txt)
    best = None
    for k in range(len(digits), 0, -1):            # the longest ending of the digit run that fits
        pv = int(digits[-k:])
        if pv <= 0:
            continue
        if qty and abs(qty * pv - amount) <= money_tol(amount):
            best = (k, pv)
            break
        if not qty and best is None and nice_qty(pv, amount) and nice_qty(pv, amount) >= 1 and (k == len(digits) or k >= 2):
            best = (k, pv)
    if best is None:
        pv, _ = money_cell(p_txt)
        if pv is None or pv > amount + money_tol(amount) or \
                (qty and abs(qty * pv - amount) > money_tol(amount) and not nice_qty(pv, amount)):
            return (head + " " + p_txt).strip(" =-~("), None, amount
        return head.strip(" =-~("), pv, amount
    k, pv = best
    keep = re.sub(r"[,.]$", "", p_txt[:len(p_txt) - len(digits[-k:])]) if k < len(digits) else ""
    return (head + (" " + keep if keep else "")).strip(" =-~("), float(pv), amount


def read_invoice(boxes):
    """A sales invoice read column by column, or None when the photo has no such table:
    {'entries': [{'desc', 'qty', 'unit', 'price', 'amount', 'ocr', 'qty_read'}], 'total', 'paid', 'change', 'notes'}
    The numbers of a row check each other: price x quantity = amount. Rows whose amount was misread are corrected from
    price x quantity when that makes the rows add up to the TOTAL AMOUNT DUE; a quantity the reader got wrong or could
    not read comes from the amount and the price (1/4 kilo at ₱75 = ₱19); two cells read as one number ('7519') are
    cut where the numbers agree. A TOTAL misread is replaced by the sum of rows that all check out."""
    tab = find_item_table(boxes)
    if tab is None:
        return None
    lines = table_lines(tab)
    raw_entries, notes = table_entries(lines, photo_tilt(boxes), money_in_desc=not tab.get("money", True))
    total = invoice_total(tab["boxes"])
    entries = []
    for e in raw_entries:
        c = e["cells"]
        desc = c.get("desc", "").strip()
        qty, unit = _qty_pieces(e, tab)
        u_text = c.get("unit", "")
        if qty is None and u_text:                 # '1 bag' written in the Unit column
            q2, u2 = qty_cell(u_text)
            if q2 is not None and u2:
                qty, unit = q2, u2
        unit = unit or unit_word(u_text)
        if unit is None and u_text:                # 'bag' read as part of the unit cell ('4Bc5' = 4 bags)
            q2, u2 = qty_cell(u_text)
            if u2:
                unit = u2
                qty = qty if qty is not None else q2
        if qty is None:                            # everything written in the description: '1 bag Tile adhesive'
            m = re.match(r"^\s*(\d{1,4}(?:\.\d+)?)\s*([A-Za-z]{1,7}\.?)?\s+(?=[A-Za-z])", desc)
            if m and (m.group(2) is None or unit_word(m.group(2))):
                qty, unit = float(m.group(1)), unit or unit_word(m.group(2) or "")
                desc = desc[m.end():]
        price, p_real = money_cell(c.get("price", ""), pick="first")
        amount, a_real = money_cell(c.get("amount", ""), pick="longest")
        if price is None and amount is None and not tab.get("money", True):     # delivery receipt: '(2000) = 20,000'
            desc2, p2, a2 = desc_money(desc, qty)
            if a2 is None:                         # ... or just the amount at the end: 'GI wire #16  100'
                m = re.search(r"\s(\d{2,}[\d,]*)\W*$", desc)
                if m and re.search(r"[A-Za-z]{3}", desc[:m.start()]):
                    a2, _ = money_cell(m.group(1))
                    desc2, p2 = desc[:m.start()].strip(), None
            if a2 is not None:
                desc, price, amount, p_real, a_real = desc2, p2, a2, True, True
        at = re.search(r"\s*@\s*[₱P]?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:/\s*[A-Za-z.]+)?\s*$", desc)
        if at:                                     # 'Cement @260' / 'Cement @ 260/bag': the price written in the description
            if price is None:
                price, p_real = float(at.group(1).replace(",", "")), True
            desc = desc[:at.start()]
        # a value read from letters only ('aso') is kept only when another number on the receipt agrees with it
        if price is not None and not p_real and price not in (amount, total):
            price = None
        if amount is not None and not a_real and amount not in (price, total):
            amount = None
        qty_read = qty is not None
        # two cells read as one long number ('7519' = 75 | 19, '24602400' = 2460 | 2400): the box sits over both the
        # Price and the Amount column, or the "amount" is more than the whole receipt
        glued = [p["glued"] for L in e["lines"] for p in L["pieces"] if p.get("glued")]
        one = amount if amount is not None else price
        if not glued and one and total and one > total + money_tol(total) and (price is None or amount is None):
            glued = [(str(int(one)), None)] if one == int(one) else []
        agree = bool(price and amount) and ((nice_qty(price, amount) is not None) if qty is None
                                            else abs(qty * price - amount) <= money_tol(amount))
        if glued and not agree:
            digits = re.sub(r"\D", "", glued[0][0].translate(DIGIT_LOOKALIKE))
            n_items = sum(1 for x in raw_entries if key_words(x["cells"].get("desc", "")) or x["cells"].get("price")
                          or x["cells"].get("amount"))
            p2, a2 = split_glued(digits, qty, total if n_items == 1 else None, glued[0][1]) \
                if len(digits) >= 4 else (None, None)
            if p2 is not None:
                price, amount = p2, a2
        ocr = " | ".join(" ".join(L["cells"].get(k, "") for k in ("qty", "unit", "desc", "price", "amount")
                                  if L["cells"].get(k)) for L in e["lines"])
        entries.append({"desc": re.sub(r"\s+", " ", desc).strip(), "qty": qty, "unit": unit, "price": price,
                        "amount": amount, "ocr": ocr, "qty_read": qty_read, "y": e["lines"][0]["y"],
                        "boxes": [id(p["box"]) for L in e["lines"] for p in L["pieces"]]})
    items_ = [e for e in entries if key_words(e["desc"]) or e["amount"] or e["price"]]
    used = {i for e in entries for i in e["boxes"]}
    nums = [v for L in notes for v in (_numeric_text(" ".join(L["cells"].values())) or [])] + \
        note_numbers(tab["boxes"], tab, used)
    off = lambda e: bool(e["qty_read"] and e["price"] and e["amount"]
                         and abs(e["qty"] * e["price"] - e["amount"]) > money_tol(e["amount"]))
    have_all = bool(items_) and all(e["amount"] for e in items_)
    s_read = round(sum(e["amount"] for e in items_), 2) if have_all else None
    s_calc = round(sum((e["qty"] * e["price"]) if off(e) else e["amount"] for e in items_), 2) if have_all else None
    # which total is the real one? The money received and the change written on the receipt decide (3500 - 250 =
    # 3250), else the TOTAL AMOUNT DUE when the rows agree with it. A total nobody agrees with does not change rows.
    trusted = None
    for t_ in (total, s_read, s_calc):
        if t_ and pay_pair(nums, t_, single=False)[0] is not None:
            trusted = t_
            break
    if trusted is None and total and (total == s_read or total == s_calc):
        trusted = total
    locked = False
    if trusted and have_all:
        if abs(s_read - trusted) <= money_tol(trusted):
            locked = True                          # the amounts as read add up: they are right
        else:
            bad = [e for e in items_ if off(e)][:8]        # amounts misread ('7,600' for 2,600): price x quantity
            for n in range(1, len(bad) + 1):
                hit = None
                for mask in range(1, 1 << len(bad)):
                    if bin(mask).count("1") != n:
                        continue
                    fix = [b for i, b in enumerate(bad) if mask >> i & 1]
                    s_ = sum((x["qty"] * x["price"]) if x in fix else x["amount"] for x in items_)
                    if abs(s_ - trusted) <= money_tol(trusted):
                        hit = fix
                        break
                if hit:
                    for x in hit:
                        x["amount"] = round(x["qty"] * x["price"], 2)
                    locked = True
                    break
    t_known = trusted or total
    if t_known and sum(1 for e in items_ if e["amount"] is None) == 1:
        rest = sum(e["amount"] or 0 for e in items_)
        e = next(e for e in items_ if e["amount"] is None)
        if t_known - rest > 0:                     # the one amount the reader missed = TOTAL - the others
            e["amount"] = round(t_known - rest, 2)
    if len(items_) == 1 and t_known and not locked:
        e = items_[0]                              # one item, its price agrees with the TOTAL AMOUNT DUE: that is its amount
        k = nice_qty(e["price"], t_known) if e["price"] else None
        # ... unless the row already agrees with itself: 'Fine Sand | 4 sack | 70 | 280' with a scribbled total read as
        # 980 is 4 x 70 = 280 (the quantity cell was not readable), not 14 sacks
        own = bool(e["price"] and e["amount"]) and (
            (bool(e["qty"]) and abs(e["qty"] * e["price"] - e["amount"]) <= money_tol(e["amount"]))
            or (not e["qty"] and nice_qty(e["price"], e["amount"]) is not None))
        if k is not None and k >= 1 and not own and (e["amount"] is None or not e["qty"]
                                                    or abs(e["qty"] * e["price"] - e["amount"]) > money_tol(e["amount"])):
            e["amount"], e["qty"] = t_known, k
            locked = True
    for e in entries:
        _settle(e, locked)
    if items_ and all(e["amount"] for e in items_):
        s_ = round(sum(e["amount"] for e in items_), 2)
        checked = all(e["qty"] and e["price"] and abs(e["qty"] * e["price"] - e["amount"]) <= money_tol(e["amount"])
                      for e in items_)
        if trusted:
            total = trusted
        elif checked and (not total or abs(s_ - total) > money_tol(total)):
            total = s_                             # every row checks out: the TOTAL was misread ('1.678' for 1,628)
    paid, change = pay_pair(nums, total) if total else (None, None)
    if paid is None:
        for s2 in {x for x in (s_read, s_calc) if x}:
            paid, change = pay_pair(nums, s2, single=False)
            if paid is not None:
                break
    for e in entries:
        e.pop("boxes", None)
    return {"entries": entries, "total": total, "paid": paid, "change": change,
            "notes": [" ".join(L["cells"].values()) for L in notes]}


def price_fit(price, sell):
    """points for how well the price on the receipt agrees with the item's selling price
    (an item without a selling price in Supply cannot be confirmed by the price: a few points less)"""
    if not price:
        return 0
    if not sell or sell <= 0:
        return -15
    d = abs(sell - price)
    if d <= max(1.0, 0.01 * price):
        return 30
    if d <= 0.10 * price:
        return 10
    return -25


# letters the reader mixes up in handwriting, for spelling a name ('AOHFLLIVE' = ADHESIVE, 'TIVE' = TILE)
_CONFUSE = [set(g) for g in ("od0q", "il1|tj", "s5z", "b8", "g69q", "uvy", "rn", "ce", "aoe", "mn", "fe", "lt", "hn", "ck")]


def ocr_word_sim(a, b):
    """Like word_sim, but a letter the reader often mistakes for another costs less (0-100)."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 100
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                sub = 0
            elif any(a[i - 1] in g and b[j - 1] in g for g in _CONFUSE):
                sub = 0.4
            else:
                sub = 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub)
        prev = cur
    return max(0.0, 100.0 * (1 - prev[m] / max(n, m)))


SIZE_TOKEN = re.compile(r"^([0-9oOlI|sSzZ.]+)(mm|cm|m|ft|in|kg|mtrs?|\")$", re.I)


def tidy_name(text, ranked=(), items=()):
    """A readable name for a row the shop does not carry yet, to start the Supply form with: each word that looks like
    a word of the nearest Supply items (or a common hardware word) is spelled like it ('TIVE AOHFLLIVE' -> 'Tile
    Adhesive', 'RS3 lomm' -> 'RSB 10mm', 'TIpVellow' -> 'Tip Yellow'); small words ('with') stay as they are."""
    near = {}
    for it, *_ in list(ranked)[:3]:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]+", it["name"]):
            near.setdefault(w.lower(), w)
    vocab = {}
    for it in items:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]+", it["name"]):
            if len(w) >= 3:
                vocab.setdefault(w.lower(), w)
    for w in HARDWARE_WORDS:
        vocab.setdefault(w, w.upper() if len(w) <= 3 and w not in ("bar", "rod", "tie", "tee", "saw", "nut", "bit") else w.capitalize())
    vocab.update(near)
    text = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", str(text or ""))          # 'TIpVellow' -> 'TIp Vellow'
    out = []
    for tok in re.findall(r"\S+", text):
        low = tok.lower()
        m = SIZE_TOKEN.match(tok)
        if m and re.search(r"\d", m.group(1).translate(QTY_LOOKALIKE)) and not re.fullmatch(r"[a-z]+", low):
            out.append(m.group(1).translate(QTY_LOOKALIKE) + m.group(2).lower())          # 'lomm' -> '10mm'
            continue
        if re.fullmatch(r"[lIoO0|sSzZ]{1,2}mm", tok):
            out.append(tok[:-2].translate(QTY_LOOKALIKE) + "mm")
            continue
        if low in STOP_WORDS:
            out.append(low)
            continue
        if re.fullmatch(r"[a-z]+\d[a-z\d]*", low) and low.translate(CODE_LOOKALIKE) in vocab:   # 'RS3' -> 'RSB'
            out.append(vocab[low.translate(CODE_LOOKALIKE)])
            continue
        word = re.fullmatch(r"[a-z]{3,}", low)
        if word and low in vocab:
            tok = vocab[low]
        elif word:
            pick = None
            if near:                               # the words of the nearest Supply items first
                c = max(((ocr_word_sim(low, w), -abs(len(w) - len(low)), w) for w in near
                         if abs(len(w) - len(low)) <= 1 and w not in COLOR_WORDS), default=None)
                if c and c[0] >= (75 if c[1] == 0 else 80):
                    pick = near[c[2]]
            if pick is None and len(low) >= 4:     # then any shop word; letters the reader mixes up cost less
                cand = sorted(((ocr_word_sim(low, w), -abs(len(w) - len(low)), w) for w in vocab
                               if abs(len(w) - len(low)) <= 2), reverse=True)
                if cand:
                    sc, dl, w = cand[0]
                    second = cand[1][0] if len(cand) > 1 else 0
                    if sc >= 80 or (sc >= 75 and dl == 0) or (len(low) >= 5 and sc >= 66 and sc - second >= 6):
                        pick = vocab[w]
            tok = pick or tok.capitalize()
        out.append(tok)
    return " ".join(out).strip()


def invoice_lines(inv, items):
    """Rows of a read invoice -> (rows matched to OUR items, rows the shop does not carry). The text must share a real
    product word with the item (colours do not count), the unit written on the receipt must fit the item's unit, and
    the price written on the receipt must be the item's price; when no item fits, the row is 'not in Supply' (with
    its price and unit, so it can be added to Supply in one click)."""
    lines, unmatched = [], []
    for e in inv["entries"]:
        desc = e["desc"]
        if PAID_STAMP_RE.match(desc) or JUNK_ROW.search(desc) or header_only(desc) or is_footer(desc):
            continue
        has_words = bool([w for w in key_words(desc) if len(w) >= 3])
        if not has_words and not (e["qty_read"] and (e["price"] or e["amount"])):
            continue                               # a scrap with no product word and no numbers of its own
        q = e["qty"]
        price = e["price"] or (e["amount"] if e["amount"] and q is None else None)   # no quantity read: take it as 1
        sure_price = e["price"] is not None             # read from the Price column (or amount / quantity read)
        ranked = rank_matches(desc, items, loose=False, unit=e["unit"], kw_min=70) if has_words else []
        rare = rare_words(desc, items) if ranked else set()
        best = None
        for it, sc, rank, kw in ranked:
            fit = price_fit(price, it["sell_price"]) if sure_price else 0
            cov_item, cov_text = word_coverage(desc, it["name"], 70 if fit == 30 else KEYWORD_MIN)
            if rare and rare <= item_words(it["name"]):
                cov_item, cov_text = max(cov_item, 0.99), max(cov_text, 0.5)   # every rare word of the row is in this item
            if kw < KEYWORD_MIN and fit != 30:
                continue                           # 'adhoscire' is adhesive only at the adhesive's own price
            if fit == 10 and kw < 85:
                continue                           # 'PIRE' (PPR) at 275 is not 'Wire' / 'Pipe' at 280: near is not enough
            if fit < 0 and not (kw >= 90 and cov_item >= 0.99):
                continue                           # another price than the item's: not this item
            if fit <= 0 and not (cov_item >= 0.99 or (kw >= 90 and cov_item >= 0.5 and cov_text >= 0.5)):
                continue                           # no price to confirm it: every product word must be there ('atic')
            if (cov_item < 0.5 or cov_text < 0.5) and not (fit == 30 and max(cov_item, cov_text) >= 0.5
                                                           and cov_text >= (0.33 if kw >= 85 else 0.5)):
                continue                           # one word in common and nothing else to confirm it ('Screen sand')
            key = (min(100, sc + max(fit, 0)) - (0 if fit >= 0 else 15), rank)
            if best is None or key > best[0]:
                best = (key, it, fit)
        if best is not None and best[0][0] >= MIN_SHOW:
            (score, _), it, fit = best
            if fit < 30 and score >= AUTO_OK and (not sure_price or fit < 0):
                score = AUTO_OK - 1                # not confirmed by the price: a person checks it
            mine = rare & item_words(it["name"])
            if fit == 30 and score >= AUTO_OK:     # same price as another item that reads almost the same: check it
                twins = [r for i2, sc2, r, _ in ranked if i2["id"] != it["id"]
                         and price_fit(price, i2["sell_price"]) == 30 and r[-1] >= best[0][1][-1] - 10
                         and (rare & item_words(i2["name"])) >= mine]   # a twin must have the same rare words too
                if twins:
                    score = AUTO_OK - 1
            if mine and mine == rare and fit >= 0 and score < AUTO_OK and \
                    not [1 for i2, *_ in ranked if i2["id"] != it["id"] and (rare & item_words(i2["name"])) >= mine
                         and (not price or price_fit(price, i2["sell_price"]) >= 0)]:
                score = AUTO_OK                    # only this item has the row's rare words ('emerald' + Lit): it is this one
            qty = q
            if qty is None and e["amount"] and it["sell_price"]:
                qty = nice_qty(it["sell_price"], e["amount"])
            lines.append({"ocr": e["ocr"], "item_id": it["id"], "score": score, "qty": qty, "verified": False,
                          "price": price, "unit": e["unit"], "item": desc})
        else:
            qty = q if q is not None else (1.0 if price else None)
            unmatched.append({"ocr": e["ocr"], "item": tidy_name(desc, ranked, items) or e["ocr"], "qty": qty,
                              "price": price, "unit": e["unit"]})
    return lines, unmatched


def maybe_item(text, price, unit, items):
    """For a row the reader did not dare to match: the Supply item it most likely is, or None. The item must share a
    product word (handwriting allowed: 'Adhoscire' = adhesive), fit the unit, and sell at exactly the price written on
    the receipt (no price written: the words must match well). Shown as "Is it ...?" so the owner links the row instead
    of adding the same item to Supply twice."""
    if not text or not [w for w in key_words(text) if len(w) >= 3]:
        return None
    best = None
    for it, sc, rank, kw in rank_matches(text, items, loose=False, unit=unit, kw_min=70):
        fit = price_fit(price, it["sell_price"]) if price else None
        cov_item, _ = word_coverage(text, it["name"], 70)
        if fit == 30 or (fit is None and kw >= 85 and cov_item >= 0.5):
            key = (fit == 30, kw, rank)
            if best is None or key > best[0]:
                best = (key, it)
    return best[1] if best else None


def reread_cells(ocr, path, boxes):
    """A second, closer look at the item table of a sales invoice with PaddleOCR's text recognizer alone: the detector
    often misses a lone '1' in the Qty column (a single thin pen stroke). The empty Qty cell of each item is cut out
    of the photo, the printed ruled lines are wiped, and the ink left is read. Returns extra boxes ('src': 'reread')
    for the cells it could read; [] when there is nothing to do. Never raises."""
    try:
        tab = find_item_table(boxes)
        if not tab or "qty" not in tab["head"]:
            return []
        entries, _ = table_entries(table_lines(tab), photo_tilt(boxes))
        todo = [e for e in entries if not e["cells"].get("qty") and (e["cells"].get("desc") or e["cells"].get("unit"))]
        if not todo:
            return []
        import cv2
        img = cv2.imread(str(path))
        if img is None:
            return []
        rec = ocr.paddlex_pipeline.text_rec_model
        qx0, qx1 = next((lo, hi) for k, lo, hi in tab["cols"] if k == "qty")
        qh = tab["head"]["qty"]
        if qx0 == -math.inf:
            qx0 = qh["x0"] - 0.6 * max(qh["x1"] - qh["x0"], qh["h"])
        out = []
        for e in todo:
            L = e["lines"][0]
            ref = min(L["pieces"], key=lambda p: p["x"])["box"]            # the nearest box on that line
            th = ref.get("th") or ref["h"]
            oy = ref.get("oy", ref["y"])
            a = ref.get("a", 0.0) if abs(ref.get("a", 0.0)) < 25 else 0.0
            x1 = min([qx1] + [p["x"] - 2 for p in L["pieces"]])
            x0 = max(0.0, qx0)
            if x1 - x0 < 0.5 * th:
                continue
            ymid = oy + math.tan(math.radians(a)) * ((x0 + x1) / 2 - ref.get("cx", ref["x"]))
            Y0, Y1 = int(max(0, ymid - 0.8 * th)), int(min(img.shape[0], ymid + 0.8 * th))
            X0, X1 = int(x0), int(min(img.shape[1], x1))
            crop = img[Y0:Y1, X0:X1]
            if crop.size == 0 or min(crop.shape[:2]) < 8:
                continue
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            H, W = g.shape
            bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
            hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, W * 2 // 3), 1))
            ruled = cv2.dilate(cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk), np.ones((3, 3), np.uint8))   # the ruled rows
            n, lab, st, _ = cv2.connectedComponentsWithStats(bw & ~ruled, connectivity=8)
            # pen marks: not specks, not the printed column line running through the whole cell (even when slanted)
            keep = [i for i in range(1, n) if st[i][cv2.CC_STAT_AREA] >= max(12, 0.004 * H * W)
                    and 0.3 * th <= st[i][cv2.CC_STAT_HEIGHT] < 0.92 * H and st[i][cv2.CC_STAT_WIDTH] < 0.9 * W]
            if not keep:
                continue                              # nothing written there
            comps = [st[i] for i in keep]
            ix0 = min(c[cv2.CC_STAT_LEFT] for c in comps)
            iy0 = min(c[cv2.CC_STAT_TOP] for c in comps)
            ix1 = max(c[cv2.CC_STAT_LEFT] + c[cv2.CC_STAT_WIDTH] for c in comps)
            iy1 = max(c[cv2.CC_STAT_TOP] + c[cv2.CC_STAT_HEIGHT] for c in comps)
            clean = crop.copy()
            clean[(bw > 0) & ~np.isin(lab, keep)] = (255, 255, 255)        # wipe the lines and the specks
            pad = int(0.25 * th)
            piece = clean[max(0, iy0 - pad):iy1 + pad, max(0, ix0 - pad):ix1 + pad]
            piece = cv2.copyMakeBorder(piece, pad, pad, pad * 2, pad * 2, cv2.BORDER_CONSTANT, value=(255, 255, 255))
            r = next(iter(rec([piece])), None)
            if not r:
                continue
            text, score = str(r["rec_text"]).strip(), float(r["rec_score"])
            q, _u = qty_cell(text)
            stroke = len(comps) == 1 and (iy1 - iy0) >= 2 * (ix1 - ix0)        # one tall thin stroke
            if q is None and stroke and score < 0.5:
                text, q = "1", 1.0                    # a single stroke the recognizer could not name
            if q is None or (score < 0.3 and not stroke):
                continue
            out.append({"text": text, "x": float(X0 + ix0), "y": float(ymid), "w": float(ix1 - ix0),
                        "h": float(th), "cx": float(X0 + (ix0 + ix1) / 2), "a": 0.0, "th": float(th),
                        "score": round(score, 3), "src": "reread"})
        return out
    except Exception:
        log("[engine] second look at the invoice table failed:\n" + traceback.format_exc(limit=3))
        return []


def build_lines_full(boxes, conn, loose=False):
    """OCR boxes -> (rows matched to OUR items, rows that look like an order line but the shop does not carry).
    A row is only matched when it shares a product word with an item; otherwise it is reported, never guessed.
    A sales invoice with a printed item table (Qty / Unit / Description / Price / Amount) is read column by column,
    with the price check (read_invoice). Other photos, row by row:
    loose=True (a delivery receipt): rows inside its article table may match on the product word alone, and every
    table row that matches nothing is listed so the owner sees it."""
    items = get_items(conn)
    try:
        inv = read_invoice(boxes)
    except Exception:
        log("[engine] invoice table not read:\n" + traceback.format_exc(limit=3))
        inv = None
    if inv is not None:
        return invoice_lines(inv, items)
    lines, unmatched = [], []
    rows = group_rows(boxes)
    table = table_rows(rows) if loose else set()
    for i, text in enumerate(rows):
        item_text, qty = parse_line(text)
        if not re.search(r"[A-Za-z]", item_text):
            continue
        if PAID_STAMP_RE.match(text) or JUNK_ROW.search(text) or header_only(text) or \
                (qty is None and NOT_ITEM_ROW.search(text)):
            continue                          # a PAID stamp, the shop's name, the BIR line...: never an item row
        best, score = best_match(item_text, items, loose=i in table)
        if best is not None and i not in table:
            cov_item, cov_text = word_coverage(item_text, best["name"])
            rare = rare_words(item_text, items)
            if not (cov_item >= 0.99 or (keyword_score(item_text, best["name"]) >= 90 and cov_item >= 0.5)
                    or (cov_item >= 0.5 and cov_text >= 0.5 and qty is not None)
                    or (rare and rare <= item_words(best["name"]))):
                best, score = None, 0              # 'k6NiRE416' is not 'Chicken Wire' because of 'wire' alone
        if best is not None and score >= MIN_SHOW:
            lines.append({"ocr": text, "item_id": best["id"], "score": score, "qty": qty, "verified": False,
                          "item": item_text})
        elif (qty is not None and ORDER_ROW.search(text) or i in table) and not NOT_ITEMS.search(item_text) \
                and len(key_words(item_text)) > 0:
            unmatched.append({"ocr": text, "item": item_text, "qty": qty})   # e.g. 'Nails 1 inch 10 kg' and we sell no nails
    return lines, unmatched


# =====================================================================
# PAYMENT: since round 17 the reader does not look for it any more (no tick boxes, no PAID stamps, no bank words):
# the sales person picks Cash or Bank on the phone when pressing Update (server.py /api/receipts/{id}/commit).
# A PAID stamp is still never read as an item row:
# a short text that STARTS with "paid" (PAID, PAID IN BANK, PAID 10/24/26). The stamp ink is often read as 'PA1D' / 'PAlD'.
# =====================================================================
PAID_STAMP_RE = re.compile(r"^\W*pa[il1|!][dt]\b", re.I)


def build_lines(boxes, conn):
    """Old signature (the desktop window / tests): (rows, number_ignored)."""
    lines, unmatched = build_lines_full(boxes, conn)
    return lines, len(group_rows(boxes)) - len(lines)


# =====================================================================
# STOCK CHECK + SALE (auto deduction)
# =====================================================================
def qty_unknown(it):
    """True when the stock of this item was not counted yet (added to Supply with quantity N/A): it is sold without
    a stock check and its quantity is left alone until somebody counts it"""
    try:
        return bool(it["qty_unknown"])
    except (IndexError, KeyError):
        return False


def stock_check(conn, lines):
    """What does this order need, and which items do we NOT have enough of?
    Returns (items_by_id, need_by_item_id, short_item_ids, report_text)."""
    items = {i["id"]: i for i in get_items(conn, everything=True)}
    need = defaultdict(float)                         # same item on two lines -> add them together
    for l in lines:
        need[l["item_id"]] += l["qty"]
    short = {i for i, n in need.items() if not qty_unknown(items[i]) and n > items[i]["quantity"] + 1e-9}
    report = "\n".join(
        f"- {items[i]['name']}: need {fmt(need[i])} {items[i]['unit']}, only {fmt(items[i]['quantity'])} in stock"
        f"  ->  missing {fmt(need[i] - items[i]['quantity'])} {items[i]['unit']}"
        for i in sorted(short))
    return items, need, short, report


def annotate_lines(conn, lines):
    """Status of every row of a receipt, in the same order as `lines`.
    Each line is a dict with item_id, qty, score, verified, sold. Returns one of:
      'sold'   already sold
      'no_qty' quantity missing or 0 (must be edited before selling)
      'short'  not enough stock for this item (all unsold rows of the item together)
      'ok'     matched well (score >= AUTO_OK) or checked by hand
      'check'  weak match, a person should look at it"""
    items = {i["id"]: i for i in get_items(conn, everything=True)}
    need = defaultdict(float)
    for l in lines:
        if not l.get("sold") and l.get("qty") and l["qty"] > 0:
            need[l["item_id"]] += l["qty"]
    out = []
    for l in lines:
        if l.get("sold"):
            out.append("sold")
        elif not l.get("qty") or l["qty"] <= 0:
            out.append("no_qty")
        elif l["item_id"] in items and not qty_unknown(items[l["item_id"]]) \
                and need[l["item_id"]] > items[l["item_id"]]["quantity"] + 1e-9:
            out.append("short")
        elif l.get("verified") or (l.get("score") or 0) >= AUTO_OK:
            out.append("ok")
        else:
            out.append("check")
    return out


def commit_sale(conn, lines, image_path, user_id=None, on_sale=None):
    """Deduct stock and save the sale in ONE database transaction (all or nothing).
    Returns (sale_id, total, profit). Raises ValueError (and changes nothing) if stock ran out meanwhile.
    user_id: which phone account sold it (None for the desktop window).
    on_sale(cursor, sale_id): optional, runs INSIDE the same transaction (the server uses it to mark receipt rows sold)."""
    items = {i["id"]: i for i in get_items(conn, everything=True)}
    demand = defaultdict(float)
    for l in lines:
        demand[l["item_id"]] += l["qty"]
    total = profit = 0.0
    with conn:                                        # commits on success, rolls back on any error
        cur = conn.cursor()
        cur.execute("INSERT INTO sales(created,image,total,profit,user_id) VALUES(?,?,0,0,?)",
                    (datetime.now().isoformat(timespec="seconds"), image_path, user_id))
        sale_id = cur.lastrowid
        for iid, need in demand.items():
            it = items[iid]
            if not qty_unknown(it):                   # stock not counted yet (N/A): sold, the count is left alone
                cur.execute("UPDATE items SET quantity = quantity - ? WHERE id = ? AND quantity >= ?", (need, iid, need - 1e-9))
                if cur.rowcount == 0:
                    raise ValueError(f"Not enough stock for {it['name']}")
            cost = it["cost_price"] if (it["cost_price"] or 0) > 0 else round((it["sell_price"] or 0) * 0.65, 2)  # no cost: 65%
            cur.execute("INSERT INTO sale_items(sale_id,item_id,qty,sell_price,cost_price) VALUES(?,?,?,?,?)",
                        (sale_id, iid, need, it["sell_price"], cost))
            total += need * it["sell_price"]
            profit += need * (it["sell_price"] - cost)
        cur.execute("UPDATE sales SET total=?, profit=? WHERE id=?", (total, profit, sale_id))
        if on_sale:
            on_sale(cur, sale_id)
    return sale_id, total, profit


# =====================================================================
# OCR: load the model, read an image
# =====================================================================
def gpu_info():
    """Is a CUDA GPU usable by THIS Python? Returns a dict with the details (also useful for debugging)."""
    info = {"ok": False, "name": "", "version": "unknown", "compiled": False, "count": 0, "exe": sys.executable}
    try:
        import paddle
        info["version"] = paddle.__version__
        info["compiled"] = bool(paddle.is_compiled_with_cuda())
        if info["compiled"]:
            info["count"] = int(paddle.device.cuda.device_count())
        info["ok"] = info["compiled"] and info["count"] > 0
        if info["ok"]:
            try:
                info["name"] = paddle.device.cuda.get_device_name(0)
            except Exception:
                info["name"] = "NVIDIA GPU"
    except Exception as e:
        info["error"] = short_error(e, 200)
    return info


# The reading model files can travel WITH the program (build_exe.py copies them into <program>/ocr_models), so a new
# laptop needs no internet and no first-start download. Without that folder PaddleOCR downloads them once into
# C:/Users/<name>/.paddlex (internet needed; phones show "waiting" until it is done).
DET_PREFERENCE = ["PP-OCRv5_server_det", "PP-OCRv5_mobile_det", "PP-OCRv4_server_det", "PP-OCRv4_mobile_det"]
REC_PREFERENCE = ["en_PP-OCRv5_mobile_rec", "en_PP-OCRv4_mobile_rec", "PP-OCRv5_server_rec", "PP-OCRv5_mobile_rec"]


def bundled_models():
    d = os.environ.get("SHOP_OCR_MODELS")
    d = Path(d) if d else ((EXE_DIR or APP_DIR) / "ocr_models")
    return d if d.is_dir() and any(d.iterdir()) else None


# ---- the FAST CPU mode (Intel oneDNN / "MKLDNN") ----
# On a CPU the reader is 5-8 times faster with it (a receipt in ~6-10 s instead of ~50 s). Very old Paddle versions
# crashed with it on Windows, so it is switched on carefully: a test read at start; if that fails, or the OCR process
# ever dies while it is on, this PC goes back to the plain (slow, always working) mode for good:
#   ocr_fast_mode_off.txt   in the data folder = never use it here (delete the file to try again)
#   SHOP_OCR_FAST=0 / 1     environment variable = force it off / on
FAST_OFF_FILE = APP_DIR / "ocr_fast_mode_off.txt"
FAST_TRY_FILE = APP_DIR / "ocr_fast_mode_trying.txt"     # exists only while the fast mode is loaded + test-read
FAST_RETRIED_FILE = APP_DIR / "ocr_fast_mode_retried.txt"   # the one second chance was used (see fast_mode_wanted)
# Before 2026-11-04 the program also switched the fast mode off when the WINDOW WAS CLOSED (the OCR process stops
# then too), so many PCs read in the slow mode (~35 s a photo) for no reason. Such an off-file gets ONE second chance.
FALSE_OFF_REASON = "the OCR process stopped while reading a photo"


def fast_mode_wanted(device, safe=False):
    if device != "cpu":
        return False
    env = os.environ.get("SHOP_OCR_FAST", os.environ.get("SHOP_OCR_MKLDNN"))
    if env is not None and env.strip() != "":
        return env.strip().lower() not in ("0", "false", "no", "off")
    if safe:
        return False
    if FAST_OFF_FILE.exists():
        try:
            why = FAST_OFF_FILE.read_text(encoding="utf-8")
        except OSError:
            why = ""
        if FALSE_OFF_REASON in why and not FAST_RETRIED_FILE.exists():
            try:                               # probably switched off by closing the program: try the fast mode again
                FAST_RETRIED_FILE.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  fast mode tried again after: {why}",
                                             encoding="utf-8")
                FAST_OFF_FILE.unlink(missing_ok=True)
                log("[engine] fast CPU mode: trying it again (it was switched off when the program closed)")
            except OSError:
                return False
        else:
            return False
    if FAST_TRY_FILE.exists():                 # the last try never finished: the OCR process died during its test
        fast_mode_failed("the OCR process stopped while the fast mode was being tested")
        return False
    return True


def fast_mode_failed(why):
    """Remember on this PC that the fast CPU mode does not work (the plain mode is used from now on)."""
    try:
        FAST_OFF_FILE.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {why}\n"
                                 "The receipt reader uses the plain (slower) CPU mode on this PC. Delete this file to try "
                                 "the fast mode again.\n", encoding="utf-8")
        FAST_TRY_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    log(f"[engine] fast CPU mode OFF: {why}")


def fast_mode_tested():
    """The fast mode loaded and read the test picture: no crash during its start on this PC."""
    try:
        FAST_TRY_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _test_image():
    """A small picture with printed words and numbers, so the test read runs the text finder AND the text reader."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (640, 220), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=40)
    except TypeError:                          # an old Pillow: the small built-in font, made bigger afterwards
        font = None
    if font is None:
        small = Image.new("RGB", (160, 55), "white")
        ImageDraw.Draw(small).text((6, 6), "CEMENT 40 BAGS", fill="black")
        ImageDraw.Draw(small).text((6, 30), "TOTAL 2,650.00", fill="black")
        return np.asarray(small.resize((640, 220)))
    d.text((24, 30), "CEMENT 40 BAGS", fill="black", font=font)
    d.text((24, 120), "TOTAL 2,650.00", fill="black", font=font)
    return np.asarray(im)


def _build(device, tier, fast=False):
    from paddleocr import PaddleOCR
    kw = dict(
        use_doc_orientation_classify=False,    # these three extras cost time; a clear
        use_doc_unwarping=False,               # straight photo does not need them
        use_textline_orientation=False,
        device="gpu:0" if device == "gpu" else "cpu",
        enable_mkldnn=bool(fast),              # the fast CPU mode (see fast_mode_wanted)
    )
    names = MODEL_SETS[tier]
    b = bundled_models()
    if b:                                      # use the model files packed with the program: no download
        det = next((n for n in ([names[0]] if names else DET_PREFERENCE) if (b / n).is_dir()), None)
        rec = next((n for n in ([names[1]] if names else REC_PREFERENCE) if (b / n).is_dir()), None)
        if det and rec:
            kw.update(text_detection_model_name=det, text_detection_model_dir=str(b / det),
                      text_recognition_model_name=rec, text_recognition_model_dir=str(b / rec))
            return PaddleOCR(**kw)
    if names is None:
        kw["lang"] = "en"                      # exactly what your first (working) version did
    else:
        kw["text_detection_model_name"], kw["text_recognition_model_name"] = names
    return PaddleOCR(**kw)


FAST_ON = False                                # the loaded model runs in the fast CPU mode


def load_model(device, tier, safe=False):
    """Load a model. If the chosen one cannot load, fall back to 'medium' (known to work).
    On a CPU the fast mode is tried first (see fast_mode_wanted); safe=True never tries it.
    Returns (ocr, tier_actually_used, note)."""
    global FAST_ON
    gc.collect()
    note = ""
    fast = fast_mode_wanted(device, safe)
    if fast:
        try:
            FAST_TRY_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        except OSError:
            pass

    def attempt(t):
        nonlocal fast
        if fast:
            try:
                o = _build(device, t, True)
                o.predict(_test_image())       # the test read: the text finder AND the text reader must work
                fast_mode_tested()
                return o
            except Exception as e:
                print(traceback.format_exc())  # full details stay in the terminal
                err = short_error(e, 200)
            o = _build(device, t, False)       # raises when the model itself cannot load (not the fast mode's fault)
            fast_mode_failed(err)              # the plain mode works: the fast mode was the problem on this PC
            fast = False
            return o
        return _build(device, t, False)

    try:
        ocr = attempt(tier)
    except Exception as first:
        if tier == "medium":
            raise
        print(traceback.format_exc())
        note = f"'{tier}' model could not load ({short_error(first, 160)}) -> using medium instead"
        tier = "medium"
        ocr = attempt(tier)
    FAST_ON = fast
    if fast:
        note = (note + " · " if note else "") + "fast CPU mode"
    try:                                       # warm-up so the first real photo is not charged for start-up
        t0 = time.perf_counter()
        ocr.predict(_warmup_image(), text_det_limit_side_len=DEFAULT_SIDE, text_det_limit_type="max")
        log(f"[engine] warm-up read {time.perf_counter() - t0:.1f} s")
    except Exception:
        pass
    return ocr, tier, note


def _warmup_image():
    """A receipt-sized page (the size real photos are read at) with a table of printed lines: the first read of a
    new picture size is the slow one (the reader prepares itself for that size), so it is done here, at start-up,
    and the first photo from a phone is read as fast as the next ones."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = DEFAULT_SIDE * 3 // 4, DEFAULT_SIDE
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:
        font = None
    rows = ["SALES INVOICE  No 010417", "QTY  UNIT  DESCRIPTION        PRICE   AMOUNT"] + \
        [f"{k + 1}  box  Boysen QDE Green Lit   {230 + k * 15}   {(k + 1) * (230 + k * 15):,}" for k in range(14)] + \
        ["TOTAL AMOUNT DUE   12,345.00"]
    for k, text in enumerate(rows):
        d.text((40, 60 + k * 70), text, fill="black", font=font)
    return np.asarray(im)


def read_image(ocr, path, side=DEFAULT_SIDE):
    """Run OCR. Returns (boxes, seconds, (width, height)).
    limit_type='max' shrinks the LONGEST side to `side` px (PaddleOCR's default 'min' never shrinks big photos)."""
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
    t0 = time.perf_counter()
    results = ocr.predict(path, text_det_limit_side_len=side, text_det_limit_type="max")
    boxes = []
    for res in results:
        scores = list(res.get("rec_scores") or [])
        for k, (text, poly) in enumerate(zip(res["rec_texts"], res["rec_polys"])):
            b = box_info(text, poly)
            if k < len(scores):
                b["score"] = round(float(scores[k]), 3)      # how sure the reader is of this text (0-1)
            boxes.append(b)
    return boxes, time.perf_counter() - t0, size


def page_turn(ocr, path, boxes):
    """How many degrees CLOCKWISE the photo must be turned so its text stands upright: 0, 90, 180 or 270.
    A receipt photographed sideways has its text lines running up or down the photo (tall, thin boxes); which way is
    decided by reading the biggest of those lines both ways with the text recognizer alone (cheap) and keeping the way
    that reads clearly. A photo upside down reads badly as it is and well turned round. 0 = leave it (also on any problem)."""
    try:
        def tall(b):
            return b.get("h", 0) >= 1.5 * max(1.0, b.get("w", 0)) and b.get("h", 0) >= 40
        def wide(b):
            return b.get("w", 0) >= 1.5 * max(1.0, b.get("h", 0)) and b.get("w", 0) >= 40
        n_tall, n_wide = sum(map(tall, boxes)), sum(map(wide, boxes))
        sideways = n_tall >= 6 and n_tall > 2 * n_wide
        if not sideways:
            if n_wide < 6:
                return 0
            sc = [b["score"] for b in boxes if wide(b) and "score" in b]
            if not sc or statistics.mean(sc) >= 0.62:      # reads well as it is
                return 0
        import cv2
        img = cv2.imread(str(path))
        if img is None:
            return 0
        H, W = img.shape[:2]
        pick = sorted((b for b in boxes if (tall(b) if sideways else wide(b))), key=lambda b: -b["w"] * b["h"])[:24]
        crops = []
        for b in pick:
            x0, x1 = int(max(0, b["x"])), int(min(W, b["x"] + b["w"]))
            y0, y1 = int(max(0, b["y"] - b["h"] / 2)), int(min(H, b["y"] + b["h"] / 2))
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                crops.append(img[y0:y1, x0:x1])
        if len(crops) < 4:
            return 0
        rec = ocr.paddlex_pipeline.text_rec_model

        def mean_score(turned):
            return statistics.mean(float(r["rec_score"]) for r in rec([np.ascontiguousarray(c) for c in turned]))
        if sideways:              # the page turned clockwise by 90 = each line crop turned clockwise (np.rot90 k=-1)
            cw, ccw = mean_score([np.rot90(c, -1) for c in crops]), mean_score([np.rot90(c, 1) for c in crops])
            log(f"[engine] sideways photo: reads {cw:.2f} turned right, {ccw:.2f} turned left")
            if max(cw, ccw) < 0.5 or abs(cw - ccw) < 0.08:
                return 0
            return 90 if cw > ccw else 270
        same, flip = mean_score(crops), mean_score([np.rot90(c, 2) for c in crops])
        log(f"[engine] photo reads {same:.2f} as it is, {flip:.2f} upside down")
        return 180 if flip > 0.6 and flip > same + 0.15 else 0
    except Exception:
        log("[engine] could not check which way up the photo is:\n" + traceback.format_exc(limit=3))
        return 0


def turn_photo(path, turn):
    """Turn the photo file itself (clockwise degrees), so it is stored upright: the receipt shows the right way up in
    the app and the second look (reread_cells, tick boxes) cuts from the upright picture."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB").rotate(-turn, expand=True)
    im.save(path, "JPEG", quality=92)


def read_upright(ocr, path, side=DEFAULT_SIDE):
    """read_image + a photo taken sideways / upside down is turned upright and read again, then the second look at
    the item table. Returns (boxes, seconds, (w, h), turn)."""
    boxes, secs, size = read_image(ocr, path, side)
    turn = page_turn(ocr, path, boxes)
    if turn:
        turn_photo(path, turn)
        log(f"[engine] photo turned {turn} degrees to stand upright; reading it again")
        boxes, secs2, size = read_image(ocr, path, side)
        secs += secs2
    extra = reread_cells(ocr, path, boxes)              # a closer look at empty Qty cells of a sales invoice
    if extra:
        boxes = boxes + extra
        log(f"[engine] second look at the item table: {', '.join(repr(b['text']) for b in extra)}")
    return boxes, secs, size, turn


def box_info(text, poly):
    """One text box: x = left edge, y = middle, h = height on the photo, w = width, cx = centre x,
    a = angle of the text line in degrees (tilted photo), th = the real letter height along the tilt."""
    p = np.asarray(poly, dtype=float).reshape(-1, 2)
    xs, ys = p[:, 0], p[:, 1]
    b = {"text": text, "x": float(xs.min()), "y": float(ys.mean()), "h": float(ys.max() - ys.min()),
         "w": float(xs.max() - xs.min()), "cx": float(xs.mean())}
    if len(p) == 4:
        e1, e2 = p[1] - p[0], p[2] - p[1]               # two sides of the box; the longer one runs along the text
        along, across = (e1, e2) if np.hypot(*e1) >= np.hypot(*e2) else (e2, e1)
        if along[0] < 0:
            along = -along
        b["a"] = round(float(np.degrees(np.arctan2(along[1], along[0]))), 2)
        b["th"] = round(float(np.hypot(*across)), 1)
    return b


def _do_load(out, device, tier, safe=False):
    t0 = time.perf_counter()
    log(f"[engine] loading model {device.upper()} / {tier} ...")
    try:
        ocr, tier, note = load_model(device, tier, safe)
        log(f"[engine] model ready ({device.upper()} / {tier}{' / fast CPU mode' if FAST_ON else ''}) "
            f"after {time.perf_counter() - t0:.1f} s")
        out.put(("model_ready", {"device": device, "tier": tier, "note": note, "fast": FAST_ON,
                                 "seconds": time.perf_counter() - t0}))
        return ocr
    except Exception:
        log("[engine] model load FAILED")
        out.put(("load_error", {"device": device, "tier": tier, "msg": traceback.format_exc()}))
        return None


def _do_ocr(out, ocr, path, side, device, tier, tag=None):
    """tag = any id the caller wants back with the result (the server passes the receipt id).
    Without a tag the old behaviour is kept: failures are reported as ("error", traceback)."""
    log(f"[engine] OCR started: {path}  (shrink to {side}px, {device.upper()})")
    try:
        boxes, secs, (w, h), turn = read_upright(ocr, path, side)
        log(f"[engine] OCR finished in {secs:.1f} s, {len(boxes)} text boxes")
        out.put(("ocr_done", {"boxes": boxes, "seconds": secs, "w": w, "h": h, "turned": turn,
                              "side": side, "device": device, "tier": tier, "tag": tag}))
    except Exception:
        log("[engine] OCR FAILED")
        if tag is None:
            out.put(("error", traceback.format_exc()))
        else:
            out.put(("job_error", {"tag": tag, "msg": traceback.format_exc()}))


def _worker_main(job_q, out_q):
    """Runs inside a SEPARATE PROCESS: its own memory, no window code, no GIL shared with the window.
    Jobs wait in job_q (first come, first served); everything is reported back through out_q."""
    if bundled_models():                      # models are local: skip PaddleX's "checking the model hosters" (slow offline)
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    out_q.put(("gpu_info", gpu_info()))
    ocr = None
    while True:
        job = job_q.get()
        if job is None:                       # the window is closing
            break
        if job[0] == "load":
            ocr = None
            gc.collect()                      # free the old model (and GPU memory) first
            ocr = _do_load(out_q, *job[1:])
        elif job[0] == "ocr":
            _do_ocr(out_q, ocr, *job[1:])


class OcrWorker:
    """Starts the OCR process and talks to it through two queues.
        worker.jobs.put(("load", device, tier[, safe]))       -> load a model (safe=True: no fast CPU mode)
        worker.jobs.put(("ocr", path, side, device, tier))    -> read an image
        worker.jobs.put(("ocr", path, side, device, tier, tag))  -> same, the result comes back with "tag" (server.py)
        worker.out.get_nowait()                               -> results coming back
    A separate PROCESS (not a thread) keeps Paddle fast and means a GPU crash cannot take the window down."""

    def __init__(self):
        ctx = mp.get_context("spawn")         # same behaviour on Windows, Linux and Mac
        self.jobs = ctx.Queue()
        self.out = ctx.Queue()
        self.proc = ctx.Process(target=_worker_main, args=(self.jobs, self.out), daemon=True)

    def start(self):
        self.proc.start()

    def is_alive(self):
        return self.proc.is_alive()

    def stop(self):
        try:
            self.jobs.put(None)
            self.proc.join(timeout=1.0)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()


# =====================================================================
# RUN THIS FILE DIRECTLY = test ONLY the processing, no window
# =====================================================================
def _print_info():
    import platform
    from importlib.metadata import PackageNotFoundError, version
    print("Python      :", platform.python_version())
    print("Python path :", sys.executable)
    for pkg in ("paddlepaddle", "paddlepaddle-gpu", "paddleocr", "paddlex", "rapidfuzz", "pillow"):
        try:
            print(f"{pkg:<17}:", version(pkg))
        except PackageNotFoundError:
            print(f"{pkg:<17}: (not installed)")
    g = gpu_info()
    print("Paddle build:", f"version {g['version']}, compiled with CUDA = {g['compiled']}, GPUs seen = {g['count']}")
    print("GPU usable  :", f"YES - {g['name']}" if g["ok"] else "NO" + (f"  ({g['error']})" if g.get("error") else ""))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Test the OCR + matching WITHOUT the window.")
    ap.add_argument("image", nargs="?", help="path to the photo to read")
    ap.add_argument("--device", choices=["cpu", "gpu"], help="default: gpu if usable, else cpu")
    ap.add_argument("--tier", choices=list(MODEL_SETS), default="medium")
    ap.add_argument("--side", type=int, default=DEFAULT_SIDE, help="shrink the longest side to this many px")
    ap.add_argument("--info", action="store_true", help="show versions and GPU status, then exit")
    a = ap.parse_args()

    if a.info or not a.image:
        _print_info()
        return
    device = a.device or ("gpu" if gpu_info()["ok"] else "cpu")

    print(f"Loading model: {device.upper()} / {a.tier} ...")
    t0 = time.perf_counter()
    ocr, tier, note = load_model(device, a.tier)
    print(f"  loaded in {time.perf_counter() - t0:.1f} s" + (f"   NOTE: {note}" if note else ""))

    print(f"Reading {a.image} (shrink to {a.side}px) ...")
    boxes, secs, (w, h) = read_image(ocr, a.image, a.side)
    print(f"  OCR took {secs:.1f} s   photo {w}x{h}   {len(boxes)} text boxes")

    conn = init_db()
    lines, ignored = build_lines(boxes, conn)
    items = {i["id"]: i for i in get_items(conn)}
    print(f"\n{'OCR text':<44} {'matched item':<22} {'%':>4} {'qty':>5} {'stock':>6}")
    for l in lines:
        it = items[l["item_id"]]
        q = "-" if l["qty"] is None else fmt(l["qty"])
        print(f"{l['ocr'][:43]:<44} {it['name']:<22} {l['score']:>4.0f} {q:>5} {fmt(it['quantity']):>6}")
    print(f"\n{len(lines)} item row(s), {ignored} other row(s) ignored.")


if __name__ == "__main__":
    main()