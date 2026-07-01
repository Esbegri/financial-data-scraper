import csv
import os
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

# =========================
# CONFIG
# =========================
BATCH_SIZE = 1000
DONE_FILE = Path("done.txt")
FAILED_FILE = Path("failed.csv")
URLS_FILE = "urls_50.txt"  # buraya 5000 url listeni koyacaksın

OUT_INCOME = "income.csv"
OUT_BALANCE = "balance.csv"
OUT_CASHFLOW = "cashflow.csv"

TAB_NAMES = {
    "income": "Income Statement",
    "balance": "Balance Sheet",
    "cashflow": "Statement Of Cashflows",
}

TICKER_RE = re.compile(r"finbox\.com/([^/]+)/models/historical-10yr", re.I)

# =========================
# HELPERS: IO
# =========================
def normalize_url(u: str) -> str:
    u = u.strip()
    if not u:
        return u
    if "models/historical-10yr" in u and not u.endswith("/"):
        u += "/"
    return u


def read_urls(path: str) -> list[str]:
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = normalize_url(line)
            if u:
                urls.append(u)
    return urls


def ensure_header(path: str, header: list[str]):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(header)


def append_rows(path: str, rows: list[list[str]]):
    if not rows:
        return
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def load_done_set() -> set[str]:
    if not DONE_FILE.exists():
        return set()
    return set(
        x.strip()
        for x in DONE_FILE.read_text(encoding="utf-8").splitlines()
        if x.strip()
    )


def mark_done(url: str):
    with DONE_FILE.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def log_failed(url: str, reason):
    first = not FAILED_FILE.exists()
    with FAILED_FILE.open("a", encoding="utf-8") as f:
        if first:
            f.write("url,reason\n")
        r = str(reason).replace("\n", " ").replace("\r", " ").replace(",", ";")
        f.write(f"{url},{r}\n")


def part_filename(base_name: str, part_no: int) -> str:
    # "income.csv" -> "income_part_0001.csv"
    stem = base_name[:-4] if base_name.lower().endswith(".csv") else base_name
    return f"{stem}_part_{part_no:04d}.csv"


def ticker_from_url(url: str) -> str:
    m = TICKER_RE.search(url)
    return m.group(1) if m else ""


# =========================
# HELPERS: PAGE ACTIONS
# =========================
def click_view_model_if_present(page) -> bool:
    # bazen button, bazen düz text
    try:
        b = page.get_by_role("button", name="VIEW MODEL")
        if b.count() > 0 and b.first.is_visible(timeout=1200):
            b.first.click(timeout=15000)
            page.wait_for_timeout(2500)
            return True
    except:
        pass

    try:
        t = page.locator("text=VIEW MODEL").first
        if t.is_visible(timeout=1200):
            t.click(timeout=15000)
            page.wait_for_timeout(2500)
            return True
    except:
        pass

    return False


def close_overlays(page):
    candidates = [
        "button:has-text('Close')",
        "button:has-text('No Thanks')",
        "button:has-text('Dismiss')",
        "button:has-text('Continue')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "button:has-text('Accept')",
        "button:has-text('I Agree')",
        "button:has-text('Agree')",
        "button:has-text('×')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=600):
                loc.click(timeout=2000)
                page.wait_for_timeout(600)
        except:
            pass


def ensure_statement_tabs(page, timeout_ms=60000):
    """
    Income Statement tabı görünür olana kadar:
    1) Overlay kapat
    2) VIEW MODEL varsa tıkla
    3) Yine yoksa Models -> Historical Financials -> View dene
    """
    income = page.get_by_role("tab", name="Income Statement")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except:
        pass
    page.wait_for_timeout(1200)

    try:
        income.wait_for(state="visible", timeout=5000)
        return
    except:
        pass

    close_overlays(page)
    click_view_model_if_present(page)
    close_overlays(page)

    try:
        income.wait_for(state="visible", timeout=15000)
        return
    except:
        pass

    try:
        page.get_by_role("tab", name="Models").click(timeout=20000)
        page.wait_for_timeout(1500)
        close_overlays(page)

        hf = page.locator("text=Historical Financials").first
        hf.wait_for(timeout=15000)

        view_btn = hf.locator(
            "xpath=ancestor::div[1]//button[normalize-space()='View'] | "
            "ancestor::div[2]//button[normalize-space()='View'] | "
            "ancestor::div[3]//button[normalize-space()='View']"
        ).first
        view_btn.click(timeout=20000)
        page.wait_for_timeout(2500)

        close_overlays(page)
        income.wait_for(state="visible", timeout=30000)
        return

    except Exception as e:
        try:
            tabs = page.get_by_role("tab")
            names = []
            for i in range(min(tabs.count(), 30)):
                t = tabs.nth(i).inner_text().strip()
                if t:
                    names.append(t)
            raise RuntimeError("Income Statement tab still not visible. Tabs seen: " + " | ".join(names)) from e
        except:
            raise


def click_tab(page, tab_name: str):
    tab = page.get_by_role("tab", name=tab_name)
    tab.wait_for(state="visible", timeout=30000)
    tab.click(timeout=30000)
    page.wait_for_timeout(800)


def extract_header_info(page) -> dict:
    """
    Tries to extract:
      - Company name (h1)
      - Country / Sector / Industry line like:
        "India / Industrials / Electrical Equipment"
    Robust for multi-word countries (e.g., "United States", "United Arab Emirates").
    """
    def one_line(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()

    def looks_like_segmentation_line(s: str) -> bool:
        # must look like "X / Y / Z" (exactly 2 slashes)
        t = one_line(s)

        if t.count("/") != 2:
            return False

        # reduce false positives
        bad_fragments = ["http", "finbox", "models", "historical", "privacy", "terms"]
        if any(b in t.lower() for b in bad_fragments):
            return False

        # segmentation line usually does NOT include exchange tickers like "NSEI:SUZLON"
        if ":" in t:
            return False

        # reasonable length
        if not (10 <= len(t) <= 120):
            return False

        # ensure it has 3 non-empty parts
        parts = [p.strip() for p in t.split("/")]
        return len(parts) == 3 and all(parts)

    info = {"name": "", "country": "", "sector": "", "industry": ""}

    # 1) Company name from h1
    try:
        info["name"] = one_line(page.locator("h1").first.inner_text(timeout=2000).split("\n")[0])
    except:
        pass

    # 2) Try to find segmentation line near the h1 area first (more accurate)
    candidates = []

    try:
        h1 = page.locator("h1").first
        # scan a couple of ancestors near header area
        containers = [
            h1.locator("xpath=ancestor::div[1]"),
            h1.locator("xpath=ancestor::div[2]"),
            h1.locator("xpath=ancestor::section[1]"),
            h1.locator("xpath=ancestor::header[1]"),
        ]

        for c in containers:
            try:
                txt = c.first.inner_text(timeout=1200)
            except:
                continue

            # break into lines; the segmentation line often appears as a single line
            for line in txt.splitlines():
                line = one_line(line)
                if looks_like_segmentation_line(line):
                    candidates.append(line)
    except:
        pass

    # 3) Fallback: global scan for " / " but filtered tightly
    if not candidates:
        try:
            loc = page.locator("div:has-text(' / ')")
            for i in range(min(loc.count(), 80)):
                try:
                    t = one_line(loc.nth(i).inner_text(timeout=600))
                except:
                    continue
                if looks_like_segmentation_line(t):
                    candidates.append(t)
                    break
        except:
            pass

    # 4) Pick the best candidate (usually the shortest valid one)
    if candidates:
        best = sorted(set(candidates), key=len)[0]
        parts = [one_line(p) for p in best.split("/")]
        if len(parts) == 3:
            info["country"], info["sector"], info["industry"] = parts[0], parts[1], parts[2]

    # 5) Final normalize (no newlines in cells)
    info["name"] = one_line(info["name"])
    info["country"] = one_line(info["country"])
    info["sector"] = one_line(info["sector"])
    info["industry"] = one_line(info["industry"])

    return info



def pick_best_table_on_page(page):
    """
    Aynı tab içinde birden fazla tablo olabiliyor.
    Geniş (>=10 kolon) ve en uzun (max row) tabloyu seçiyoruz.
    """
    all_tables = page.locator("table")
    target_table = None
    max_rows = 0

    for i in range(all_tables.count()):
        t = all_tables.nth(i)
        try:
            row_count = t.locator("tr").count()
            tr0 = t.locator("tr").first
            if tr0.count() == 0:
                continue
            col_count = tr0.locator("td, th").count()
        except:
            continue

        if col_count >= 10 and row_count > max_rows:
            try:
                t_text = t.inner_text(timeout=800).lower()
                if "summary financials" in t_text and row_count < 20:
                    continue
            except:
                pass

            max_rows = row_count
            target_table = t

    return target_table


# =========================
# TABLE PARSING (CLEAN CSV)
# =========================
def is_junk_header_row(cells: list[str]) -> bool:
    """
    Finbox tablosunda veri olmayan üst başlıkları ayıkla:
    - "INR Fiscal Year Ending Latest"
    - "FY - 9 FY - 8 ..."
    - "Statement of Cashflows"
    - "(in millions) ..."
    """
    if not cells:
        return True

    first = cells[0].strip().lower()
    joined = " ".join(c.strip().lower() for c in cells if c).strip()

    # Tek satır büyük başlıklar
    if "fiscal year" in joined or "fiscal year ending" in joined:
        return True
    if joined.startswith("inr") and ("fiscal" in joined or "latest" in joined):
        return True

    # FY satırı
    if first.startswith("fy"):
        return True

    # Statement header satırı
    if "statement" in first and "cash" in first:
        return True

    # Bu satır "year row" ise junk sayma (header olarak kullanacağız)
    if first.startswith("(in") or first == "in millions" or "(in millions)" in first:
        return False

    return False


def detect_year_header_row(rows_cells: list[list[str]]) -> tuple[int, list[str]] | tuple[None, None]:
    """
    "(in millions) Mar-16 ... Mar-25 ... LTM" satırını bul.
    Bulursak years = cells[1:].
    """
    for idx, cells in enumerate(rows_cells):
        if not cells:
            continue
        first = cells[0].strip().lower()
        if first.startswith("(in") or first == "in millions" or "(in millions)" in first:
            # years are remaining cells
            years = [c.strip().replace("\xa0", " ") for c in cells[1:] if c.strip() != ""]
            if len(years) >= 8:  # en az bir kaç yıl olsun
                return idx, years
    return None, None


def read_table_rows_clean(target_table, url: str, header_info: dict) -> tuple[list[str], list[list[str]]]:
    rows_loc = target_table.locator("tr")
    raw_rows: list[list[str]] = []

    for i in range(rows_loc.count()):
        row = rows_loc.nth(i)
        cells = [c.strip().replace("\xa0", " ") for c in row.locator("td, th").all_inner_texts()]
        cells = [c for c in cells if c != ""]  # boşları at
        if len(cells) < 2:
            continue
        raw_rows.append(cells)

    yr_idx, years = detect_year_header_row(raw_rows)

    # Eğer yıl satırını bulamazsak eski yönteme geri dön (en azından boş dönmeyelim)
    if years is None:
        # fallback: ilk geniş satırı years gibi kabul et
        widest = max(raw_rows, key=lambda r: len(r), default=None)
        years = widest[1:] if widest and len(widest) >= 10 else []

    ticker = ticker_from_url(url)

    header = ["URL", "Company Name", "Ticker", "Country", "Sector", "Industry", "Line Item"] + list(years)

    table_rows: list[list[str]] = []
    for i, cells in enumerate(raw_rows):
        # yıl satırını yazma
        if yr_idx is not None and i == yr_idx:
            continue

        if is_junk_header_row(cells):
            continue

        line_item = cells[0].strip()
        values = cells[1:]

        # values sayısını years ile hizala (trim/pad)
        if years:
            if len(values) > len(years):
                values = values[: len(years)]
            elif len(values) < len(years):
                values = values + ([""] * (len(years) - len(values)))

        enriched = [
            url,
            header_info.get("name", ""),
            ticker,
            header_info.get("country", ""),
            header_info.get("sector", ""),
            header_info.get("industry", ""),
            line_item,
        ] + values

        table_rows.append(enriched)

    return header, table_rows


# =========================
# SCRAPER (ONE URL)
# =========================
def scrape_one(page, url: str):
    url = normalize_url(url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    ensure_statement_tabs(page, timeout_ms=60000)
    header_info = extract_header_info(page)

    final_data = {"income": ([], []), "balance": ([], []), "cashflow": ([], [])}

    for key, tab_label in TAB_NAMES.items():
        click_tab(page, tab_label)

        # tablo tam yüklensin diye az scroll
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
            page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        except:
            pass

        target = pick_best_table_on_page(page)
        if not target:
            raise RuntimeError(f"{tab_label} için uygun tablo bulunamadı.")

        h, rows = read_table_rows_clean(target, url, header_info)
        final_data[key] = (h, rows)

    return final_data


# =========================
# MAIN
# =========================
def main():
    done = load_done_set()
    print(f"✅ done.txt loaded: {len(done)} URLs already processed")

    urls_all = read_urls(URLS_FILE)
    urls = [u for u in urls_all if u not in done]
    print(f"📌 Remaining URLs: {len(urls)} / {len(urls_all)}")

    done_count_at_start = len(done)
    processed_this_run = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(
            storage_state="storage_state.json",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Europe/Istanbul",
        )
        page = context.new_page()

        # JS alert/confirm çıkarsa kabul et
        page.on("dialog", lambda d: d.accept())

        for idx, url in enumerate(urls, start=1):
            print(f"[{idx}/{len(urls)}] {url}")

            try:
                data = scrape_one(page, url)

                inc_h, inc_rows = data["income"]
                bal_h, bal_rows = data["balance"]
                csh_h, csh_rows = data["cashflow"]

                # empty-data guard
                if not inc_h or not inc_rows:
                    raise RuntimeError("Income empty")
                if not bal_h or not bal_rows:
                    raise RuntimeError("Balance empty")
                if not csh_h or not csh_rows:
                    raise RuntimeError("Cashflow empty")

                part_no = ((done_count_at_start + processed_this_run) // BATCH_SIZE) + 1
                out_income = part_filename(OUT_INCOME, part_no)
                out_balance = part_filename(OUT_BALANCE, part_no)
                out_cashflow = part_filename(OUT_CASHFLOW, part_no)

                ensure_header(out_income, inc_h)
                ensure_header(out_balance, bal_h)
                ensure_header(out_cashflow, csh_h)

                append_rows(out_income, inc_rows)
                append_rows(out_balance, bal_rows)
                append_rows(out_cashflow, csh_rows)

                mark_done(url)
                processed_this_run += 1

                if idx % 50 == 0:
                    print(
                        f"✅ progress: {idx}/{len(urls)} (this run), "
                        f"done total: {done_count_at_start + processed_this_run}"
                    )

                time.sleep(1.2)

            except Exception as e:
                log_failed(url, e)
                print("  FAILED:", e)
                time.sleep(2)
                continue

        browser.close()
        print("✅ Done. Created part files based on:", OUT_INCOME, OUT_BALANCE, OUT_CASHFLOW)


if __name__ == "__main__":
    main()
