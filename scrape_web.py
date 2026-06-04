import warnings
warnings.filterwarnings("ignore")

import os
import re
import time
import hashlib
from urllib.parse import urlparse, urljoin
from markdownify import markdownify as md

# ── Playwright ────────────────────────────────────────────────────────────────
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ── playwright-stealth: masks all headless-browser fingerprint tells ──────────
# Install: pip install playwright-stealth
# Fixes Cloudflare blocks, bot-detection timeouts, empty page responses.
try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True

    def run_playwright_stealth(page) -> None:
        stealth_sync(page)

except ImportError as old_api_exc:
    try:
        from playwright_stealth import Stealth

        STEALTH_AVAILABLE = True
        _STEALTH = Stealth()

        def run_playwright_stealth(page) -> None:
            _STEALTH.apply_stealth_sync(page)

    except ImportError as new_api_exc:
        STEALTH_AVAILABLE = False

        def run_playwright_stealth(page) -> None:
            raise RuntimeError("playwright-stealth is not available")

        print("[scraper] playwright-stealth not available — running without it.")
        print(f"[scraper] Import errors: {old_api_exc}; {new_api_exc}")
        print("[scraper] Install with: pip install playwright-stealth")

DATA_PATH = "data"

# ── Anti-detection constants ──────────────────────────────────────────────────

# Chromium launch args that hide automation signals                             ← NEW
BROWSER_ARGS = [                                                                # ← NEW
    "--disable-blink-features=AutomationControlled",                           # ← NEW
    "--disable-dev-shm-usage",                                                  # ← NEW
    "--no-sandbox",                                                             # ← NEW
    "--disable-setuid-sandbox",                                                 # ← NEW
    "--disable-infobars",                                                       # ← NEW
    "--disable-notifications",                                                  # ← NEW
    "--disable-popup-blocking",                                                 # ← NEW
    "--start-maximized",                                                        # ← NEW
]                                                                               # ← NEW

# Realistic HTTP headers that match a real Chrome browser                       ← NEW
EXTRA_HEADERS = {                                                               # ← NEW
    "Accept": (                                                                 # ← NEW
        "text/html,application/xhtml+xml,application/xml;"                     # ← NEW
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"                    # ← NEW
    ),                                                                          # ← NEW
    "Accept-Language": "en-US,en;q=0.9",                                       # ← NEW
    "Accept-Encoding": "gzip, deflate, br",                                    # ← NEW
    "DNT": "1",                                                                 # ← NEW
    "Upgrade-Insecure-Requests": "1",                                          # ← NEW
    "Sec-Fetch-Dest": "document",                                              # ← NEW
    "Sec-Fetch-Mode": "navigate",                                              # ← NEW
    "Sec-Fetch-Site": "none",                                                  # ← NEW
    "Sec-Fetch-User": "?1",                                                    # ← NEW
    "Cache-Control": "max-age=0",                                              # ← NEW
}                                                                               # ← NEW

# Fallback JS patches for when playwright-stealth is not installed              ← NEW
STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
""".strip()                                                                     # ← NEW


# ── Stealth helper ────────────────────────────────────────────────────────────

def apply_stealth(page) -> None:                                                # ← NEW
    """                                                                         # ← NEW
    Apply all available anti-detection measures to a Playwright page.          # ← NEW
    Uses playwright-stealth if installed, otherwise falls back to manual JS     # ← NEW
    patches that hide the most common headless-browser signals.                 # ← NEW
    """                                                                         # ← NEW
    if STEALTH_AVAILABLE:                                                       # ← NEW
        run_playwright_stealth(page)                                            # ← NEW
    else:                                                                       # ← NEW
        page.add_init_script(STEALTH_INIT_JS)                                  # ← NEW


# ── Cloudflare challenge detector ─────────────────────────────────────────────

def is_cf_challenge(html: str) -> bool:                                         # ← NEW
    """                                                                         # ← NEW
    Returns True if Cloudflare served a browser challenge instead of the page. # ← NEW
    When this is True we wait longer before reading page content.               # ← NEW
    """                                                                         # ← NEW
    signals = [                                                                 # ← NEW
        "cf-browser-verification",                                             # ← NEW
        "cf_clearance",                                                        # ← NEW
        "Checking your browser",                                               # ← NEW
        "DDoS protection by Cloudflare",                                       # ← NEW
        "challenge-form",                                                      # ← NEW
        "_cf_chl_",                                                            # ← NEW
        "jschl-answer",                                                        # ← NEW
    ]                                                                           # ← NEW
    lower = html.lower()                                                        # ← NEW
    return any(s.lower() in lower for s in signals)                             # ← NEW


# ── Markdown cleaner (unchanged) ──────────────────────────────────────────────

def clean_markdown(text: str) -> str:
    text = text.replace("Â£", "£").replace("Â", "").replace("\xa3", "£")
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    clean_lines = []
    previous_blank = False
    for line in lines:
        if not line.strip():
            if not previous_blank:
                clean_lines.append("")
            previous_blank = True
        else:
            clean_lines.append(line.strip())
            previous_blank = False
    return "\n".join(clean_lines).strip()


# ── Core Playwright fetch ─────────────────────────────────────────────────────

def fetch_page_html(page, url: str, wait_for: str = "networkidle") -> str:
    """
    Navigate to URL using an already-open Playwright page.
    Returns the fully-rendered HTML string.

    Updated: detects Cloudflare challenge pages and waits for them to resolve   ← NEW
    before reading content. Also increases retry timeout to 40s.                ← NEW
    """
    try:
        page.goto(url, wait_until=wait_for, timeout=45_000)

        # ── Cloudflare challenge check ────────────────────────────────────    ← NEW
        initial_html = page.content()                                           # ← NEW
        if is_cf_challenge(initial_html):                                       # ← NEW
            print(f"  [cloudflare] Challenge detected on {url} — waiting …")   # ← NEW
            page.wait_for_timeout(6000)          # give CF JS time to run      # ← NEW
            page.wait_for_load_state("networkidle", timeout=20_000)            # ← NEW
        # ─────────────────────────────────────────────────────────────────    ← NEW

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        return page.content()

    except PlaywrightTimeoutError:
        print(f"  [timeout] Retrying {url} with domcontentloaded …")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40_000)      # ← CHANGED 25s → 40s
            page.wait_for_timeout(5000)          # extra wait for CF/JS        # ← CHANGED 3s → 5s
            html = page.content()                                               # ← NEW
            if is_cf_challenge(html):                                           # ← NEW
                print(f"  [cloudflare] Still on challenge page — waiting …")   # ← NEW
                page.wait_for_timeout(8000)                                    # ← NEW
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)                                        # ← CHANGED 3s → 2s
            return page.content()
        except Exception as e:
            print(f"  [error] Could not fetch {url}: {e}")
            return ""


def html_to_markdown(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["footer", "nav", "header", "aside",
                               "script", "style", "noscript", "iframe",
                               "form", "svg", "button"]):
        tag.decompose()

    boilerplate_domains = ["upwork.com", "clutch.co", "goodfirms.co",
                           "glassdoor.com", "codecanyon.net"]
    for tag in soup.find_all(["div", "section", "ul"]):
        links = tag.find_all("a", href=True)
        if links and all(
            any(d in (a.get("href") or "") for d in boilerplate_domains)
            for a in links
        ):
            tag.decompose()

    raw_md = md(
        str(soup),
        heading_style="ATX",
        bullets="-",
        strip=["img"],
        newline_style="backslash",
    )
    return clean_markdown(raw_md)


# ── Internal link extractor (unchanged) ──────────────────────────────────────

def get_internal_links(page, base_domain: str, current_url: str) -> list[str]:
    raw_hrefs = page.eval_on_selector_all(
        "a[href]",
        "elements => elements.map(e => e.getAttribute('href'))"
    )
    links = []
    for href in raw_hrefs:
        if not href:
            continue
        full_url = urljoin(current_url, href)
        parsed = urlparse(full_url)
        if (
            parsed.netloc == base_domain
            and not parsed.fragment
            and not parsed.path.endswith(
                (".pdf", ".jpg", ".jpeg", ".png", ".gif",
                 ".zip", ".css", ".js", ".xml", ".svg",
                 ".mp4", ".mp3", ".ico")
            )
            and "#" not in full_url.split("?")[0]
        ):
            clean = parsed.scheme + "://" + parsed.netloc + parsed.path
            clean = clean.rstrip("/") or clean
            links.append(clean)
    return list(set(links))


# ── Single-page scrape ────────────────────────────────────────────────────────

def scrape_and_save(url: str, filename: str, headless: bool = True) -> str:   # ← headless param added
    """Scrape a single URL, save as .md, return filepath."""
    print(f"Scraping: {url}")
    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with sync_playwright() as p:
        browser = p.chromium.launch(                                            # ← NEW: added args + headless param
            headless=headless,                                                  # ← NEW
            args=BROWSER_ARGS,                                                  # ← NEW
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers=EXTRA_HEADERS,                                  # ← NEW
        )
        page = context.new_page()
        apply_stealth(page)                                                     # ← NEW

        html = fetch_page_html(page, url)
        text = html_to_markdown(html) if html else "(no content)"

        browser.close()

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(text)

    print(f"Saved {len(text):,} characters → {filepath}")
    return filepath


# ── Full website crawler ──────────────────────────────────────────────────────

def scrape_full_website(
    start_url: str,
    base_filename: str,
    max_pages: int = 30,
    wait_for: str = "networkidle",
    headless: bool = True,                                                      # ← NEW param
) -> list[str]:
    """
    Crawl an entire website with a single persistent browser session.
    Follows internal links up to max_pages.
    All pages are merged into ONE .md file.

    headless=False is more reliable against Cloudflare but opens a              ← NEW
    visible browser window. Use True for servers, False for local runs.         ← NEW
    """
    parsed_start = urlparse(start_url)
    base_domain  = parsed_start.netloc

    visited    = set()
    to_visit   = [start_url.rstrip("/")]
    all_sections: list[str] = []
    page_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(                                            # ← NEW: added args + headless
            headless=headless,                                                  # ← NEW
            args=BROWSER_ARGS,                                                  # ← NEW
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers=EXTRA_HEADERS,                                  # ← NEW
            java_script_enabled=True,
        )

        def block_media(route, request):
            if request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        apply_stealth(page)                                                     # ← NEW
        page.route("**/*", block_media)

        while to_visit and page_count < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)

            print(f"[{page_count + 1}/{max_pages}] Scraping: {url}")

            html = fetch_page_html(page, url, wait_for=wait_for)
            if not html:
                continue

            text = html_to_markdown(html)
            if not text.strip():
                print(f"  → empty after extraction, skipping")
                continue

            all_sections.append(f"<!-- Page: {url} -->\n\n{text}")
            page_count += 1
            print(f"  → {len(text):,} chars extracted")

            new_links = get_internal_links(page, base_domain, url)
            for link in new_links:
                if link not in visited and link not in to_visit:
                    to_visit.append(link)

        browser.close()

    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, base_filename + ".md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {start_url}\n\n")
        f.write("\n\n---\n\n".join(all_sections))

    print(f"\nDone. {page_count} pages → {filepath}")
    return [filepath]


# ── Multiple URLs helper (unchanged) ─────────────────────────────────────────

def scrape_multiple(urls: dict):
    """Scrape a dict of {filename: url} one by one."""
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


# ── Paginated scraper (unchanged) ─────────────────────────────────────────────

def scrape_all_pages(base_url: str, filename: str, max_pages: int = 50) -> list[str]:
    all_text: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=BROWSER_ARGS,                                                  # ← NEW
        )
        page = browser.new_page()
        apply_stealth(page)                                                     # ← NEW

        for i in range(1, max_pages + 1):
            url = base_url if i == 1 else f"{base_url}catalogue/page-{i}.html"
            try:
                html = fetch_page_html(page, url, wait_for="domcontentloaded")
                text = html_to_markdown(html)
                all_text.append(f"## Page {i}\n\n{text}")
                print(f"  Scraped page {i}/{max_pages}")
            except Exception as e:
                print(f"  Stopped at page {i}: {e}")
                break

        browser.close()

    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {base_url}\n\n")
        f.write("\n\n".join(all_text))
    print(f"Saved all pages → {filepath}")
    return [filepath]


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Scrape Cloudflare-protected site (minimalist.ae) ──────────────────    ← NEW example
    scrape_full_website(                                                        # ← NEW
        "https://minimalist.ae/",                                              # ← NEW
        "minimalist",                                                          # ← NEW
        max_pages=30,                                                          # ← NEW
        headless=True,   # set False if still blocked — opens visible Chrome   # ← NEW
    )                                                                           # ← NEW

    # ── Alian Software ────────────────────────────────────────────────────────
    # scrape_full_website(
    #     "https://aliansoftware.com/en",
    #     "aliansoftware",
    #     max_pages=20,
    # )

    # ── Single page ───────────────────────────────────────────────────────────
    # scrape_and_save("https://bvmengineering.ac.in/", "bvm.md")

    # ── Multiple URLs ─────────────────────────────────────────────────────────
    # scrape_multiple({
    #     "alian_home":    "https://aliansoftware.com/en",
    #     "alian_about":   "https://aliansoftware.com/en/about",
    #     "alian_work":    "https://aliansoftware.com/en/work",
    #     "alian_pricing": "https://aliansoftware.com/en/pricing",
    #     "alian_blog":    "https://aliansoftware.com/en/blog",
    #     "bvm":           "https://bvmengineering.ac.in/",
    # })

    # ── Books (paginated static) ──────────────────────────────────────────────
    # scrape_all_pages("https://books.toscrape.com/", "books_all.md")
