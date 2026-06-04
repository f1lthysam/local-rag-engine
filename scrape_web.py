import warnings
warnings.filterwarnings("ignore")

import os
import re
import time
import json
import hashlib
import xml.etree.ElementTree as ET
from collections import deque
from urllib.parse import urlparse, urljoin, urldefrag
from markdownify import markdownify as md

# ── Playwright ────────────────────────────────────────────────────────────────
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ── playwright-stealth ────────────────────────────────────────────────────────
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

DATA_PATH = "data"

# ── Anti-detection constants ──────────────────────────────────────────────────

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--disable-notifications",
    "--disable-popup-blocking",
    "--start-maximized",
]

EXTRA_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
""".strip()

# Extensions to skip during deep crawl
SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip",
    ".css", ".js", ".xml", ".svg", ".mp4", ".mp3",
    ".ico", ".webp", ".woff", ".woff2", ".ttf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".gz", ".tar", ".rar", ".7z",
)

# URL path segments that usually indicate boilerplate/noise — skip them
SKIP_PATH_PATTERNS = [
    r"/cdn-cgi/",
    r"/wp-json/",
    r"/__data",
    r"/feed/",
    r"\?replytocom=",
    r"/comment-page-",
    r"/trackback/",
    r"/xmlrpc",
    r"\.rss$",
    r"\.atom$",
]


# ── Stealth helper ────────────────────────────────────────────────────────────

def apply_stealth(page) -> None:
    if STEALTH_AVAILABLE:
        run_playwright_stealth(page)
    else:
        page.add_init_script(STEALTH_INIT_JS)


# ── Cloudflare challenge detector ─────────────────────────────────────────────

def is_cf_challenge(html: str) -> bool:
    signals = [
        "cf-browser-verification", "cf_clearance",
        "Checking your browser", "DDoS protection by Cloudflare",
        "challenge-form", "_cf_chl_", "jschl-answer",
    ]
    lower = html.lower()
    return any(s.lower() in lower for s in signals)


# ── URL utilities ─────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Remove fragments, trailing slashes, and query strings (optional)."""
    url, _ = urldefrag(url)               # strip #fragment
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed.scheme + "://" + parsed.netloc + path


def should_skip_url(url: str) -> bool:
    """Return True if this URL should NOT be scraped."""
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    for pattern in SKIP_PATH_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


# ── Sitemap discovery ─────────────────────────────────────────────────────────

def discover_sitemap_urls(page, base_url: str) -> list[str]:
    """
    Try to fetch sitemap.xml (and sitemap index files) to pre-seed the URL
    queue. Returns a deduplicated list of URLs belonging to the same domain.
    """
    base_domain = urlparse(base_url).netloc
    roots = [
        base_url.rstrip("/") + "/sitemap.xml",
        base_url.rstrip("/") + "/sitemap_index.xml",
        base_url.rstrip("/") + "/robots.txt",
    ]
    found_urls: set[str] = set()

    def parse_sitemap_xml(xml_text: str) -> list[str]:
        urls = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            # sitemap index → recurse into child sitemaps
            for loc in root.findall(".//sm:loc", ns):
                urls.append(loc.text.strip())
        except ET.ParseError:
            pass
        return urls

    sitemap_queue: deque[str] = deque()

    # First try robots.txt to find Sitemap: directives
    try:
        robots_url = base_url.rstrip("/") + "/robots.txt"
        page.goto(robots_url, wait_until="domcontentloaded", timeout=15_000)
        robots_text = page.evaluate("document.body.innerText")
        for line in robots_text.splitlines():
            if line.lower().startswith("sitemap:"):
                sm_url = line.split(":", 1)[1].strip()
                sitemap_queue.append(sm_url)
    except Exception:
        pass

    # Add default sitemap locations
    for r in roots[:2]:
        sitemap_queue.append(r)

    visited_sitemaps: set[str] = set()

    while sitemap_queue:
        sm_url = sitemap_queue.popleft()
        if sm_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sm_url)

        try:
            page.goto(sm_url, wait_until="domcontentloaded", timeout=15_000)
            content = page.content()
            if "<urlset" in content or "<sitemapindex" in content:
                raw_urls = parse_sitemap_xml(
                    page.evaluate("document.documentElement.outerHTML")
                )
                for u in raw_urls:
                    if urlparse(u).netloc == base_domain:
                        if not should_skip_url(u):
                            found_urls.add(normalize_url(u))
                    elif u.endswith(".xml"):
                        # child sitemap from another domain is still valid
                        sitemap_queue.append(u)
        except Exception:
            pass

    print(f"  [sitemap] Discovered {len(found_urls)} URLs from sitemaps")
    return list(found_urls)


# ── Markdown cleaner ──────────────────────────────────────────────────────────

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
    try:
        page.goto(url, wait_until=wait_for, timeout=45_000)
        initial_html = page.content()
        if is_cf_challenge(initial_html):
            print(f"  [cloudflare] Challenge detected on {url} — waiting …")
            page.wait_for_timeout(6000)
            page.wait_for_load_state("networkidle", timeout=20_000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        return page.content()

    except PlaywrightTimeoutError:
        print(f"  [timeout] Retrying {url} with domcontentloaded …")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            page.wait_for_timeout(5000)
            html = page.content()
            if is_cf_challenge(html):
                print(f"  [cloudflare] Still on challenge page — waiting …")
                page.wait_for_timeout(8000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            return page.content()
        except Exception as e:
            print(f"  [error] Could not fetch {url}: {e}")
            return ""


def html_to_markdown(html: str, source_url: str = "") -> str:
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


# ── Internal link extractor (deep version) ────────────────────────────────────

def get_internal_links(page, base_domain: str, current_url: str) -> list[str]:
    """
    Extract ALL internal links from the current page, including links nested
    inside dynamically rendered content.  Filters out noise URLs.
    """
    raw_hrefs = page.eval_on_selector_all(
        "a[href]",
        "elements => elements.map(e => e.getAttribute('href'))"
    )
    links = []
    for href in raw_hrefs:
        if not href or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        full_url = urljoin(current_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc != base_domain:
            continue
        if should_skip_url(full_url):
            continue

        clean = normalize_url(full_url)
        links.append(clean)

    return list(set(links))


# ── Crawl state persistence ───────────────────────────────────────────────────

class CrawlState:
    """
    Persists crawl progress to disk so interrupted scrapes can be resumed.
    State file: data/<base_filename>_crawl_state.json
    """

    def __init__(self, base_filename: str):
        os.makedirs(DATA_PATH, exist_ok=True)
        self.path = os.path.join(DATA_PATH, f"{base_filename}_crawl_state.json")
        self.data: dict = {
            "visited": [],
            "to_visit": [],
            "failed": [],
            "page_count": 0,
        }

    def load(self) -> bool:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            print(f"  [resume] Loaded crawl state: "
                  f"{len(self.data['visited'])} visited, "
                  f"{len(self.data['to_visit'])} pending")
            return True
        return False

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def mark_visited(self, url: str) -> None:
        if url not in self.data["visited"]:
            self.data["visited"].append(url)

    def mark_failed(self, url: str) -> None:
        if url not in self.data["failed"]:
            self.data["failed"].append(url)

    def add_to_queue(self, urls: list[str]) -> None:
        visited_set = set(self.data["visited"])
        queued_set  = set(self.data["to_visit"])
        for u in urls:
            if u not in visited_set and u not in queued_set:
                self.data["to_visit"].append(u)

    def pop_next(self) -> str | None:
        if self.data["to_visit"]:
            return self.data["to_visit"].pop(0)
        return None

    def increment_page_count(self) -> None:
        self.data["page_count"] += 1

    @property
    def page_count(self) -> int:
        return self.data["page_count"]

    def is_visited(self, url: str) -> bool:
        return url in set(self.data["visited"])

    def queue_length(self) -> int:
        return len(self.data["to_visit"])

    def delete(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)


# ── Per-page file saver ───────────────────────────────────────────────────────

def save_page_file(base_filename: str, url: str, text: str) -> str:
    """
    Save a single page as its own .md file inside data/<base_filename>_pages/.
    Filename is derived from the URL path (safe for filesystem).
    Returns the filepath.
    """
    pages_dir = os.path.join(DATA_PATH, f"{base_filename}_pages")
    os.makedirs(pages_dir, exist_ok=True)

    parsed = urlparse(url)
    slug = parsed.path.strip("/").replace("/", "__") or "index"
    slug = re.sub(r"[^\w\-]", "_", slug)[:120]
    slug = slug or hashlib.md5(url.encode()).hexdigest()[:12]

    filepath = os.path.join(pages_dir, slug + ".md")
    # handle collisions
    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(pages_dir, f"{slug}_{counter}.md")
        counter += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(text)

    return filepath


# ── Batch processor ───────────────────────────────────────────────────────────

def process_batch(
    page,
    urls: list[str],
    base_domain: str,
    state: CrawlState,
    base_filename: str,
    wait_for: str,
    save_individual: bool,
    max_pages: int,
) -> list[str]:
    """
    Scrape one batch of URLs.  Returns list of (url, markdown_text) tuples
    collected in this batch.
    """
    results: list[tuple[str, str]] = []

    for url in urls:
        if state.page_count >= max_pages:
            break
        if state.is_visited(url):
            continue

        print(f"  [{state.page_count + 1}/{max_pages}] {url}")
        state.mark_visited(url)

        html = fetch_page_html(page, url, wait_for=wait_for)
        if not html:
            state.mark_failed(url)
            state.save()
            continue

        text = html_to_markdown(html, source_url=url)
        if not text.strip():
            print(f"    → empty after extraction, skipping")
            state.save()
            continue

        state.increment_page_count()
        print(f"    → {len(text):,} chars")

        results.append((url, text))

        if save_individual:
            fp = save_page_file(base_filename, url, text)
            print(f"    → saved: {fp}")

        # ── Deep link discovery ────────────────────────────────────────────
        new_links = get_internal_links(page, base_domain, url)
        state.add_to_queue(new_links)
        state.save()

    return results


# ── Full website deep crawler with batching ───────────────────────────────────

def scrape_full_website(
    start_url: str,
    base_filename: str,
    max_pages: int = 200,
    batch_size: int = 10,
    wait_for: str = "networkidle",
    headless: bool = True,
    save_individual_pages: bool = True,
    use_sitemap: bool = True,
    resume: bool = True,
    depth_first: bool = False,
) -> list[str]:
    """
    Deep-crawl an entire website with batching and resume support.

    Parameters
    ----------
    start_url            : Seed URL to begin crawling.
    base_filename        : Base name for output files (no extension).
    max_pages            : Hard cap on total pages scraped.
    batch_size           : URLs to process per browser session batch.
                           After each batch the merged .md is flushed to disk.
    wait_for             : Playwright wait_until strategy.
    headless             : False opens a visible Chrome window (helps vs CF).
    save_individual_pages: If True, each page is also saved as its own .md
                           file in data/<base_filename>_pages/ — ideal for RAG.
    use_sitemap          : Try sitemap.xml / robots.txt for URL discovery.
    resume               : Resume from a previous crawl state if one exists.
    depth_first          : True = DFS traversal; False = BFS (default, better
                           for broad coverage of big sites).
    """
    parsed_start = urlparse(start_url)
    base_domain  = parsed_start.netloc

    state = CrawlState(base_filename)
    resumed = False
    if resume:
        resumed = state.load()

    if not resumed:
        state.add_to_queue([normalize_url(start_url)])

    merged_output_path = os.path.join(DATA_PATH, base_filename + ".md")
    all_sections: list[str] = []

    # If resuming, reload already-scraped pages into all_sections from disk
    pages_dir = os.path.join(DATA_PATH, f"{base_filename}_pages")
    if resumed and os.path.isdir(pages_dir):
        for fn in sorted(os.listdir(pages_dir)):
            if fn.endswith(".md"):
                with open(os.path.join(pages_dir, fn), "r", encoding="utf-8") as f:
                    all_sections.append(f.read())
        print(f"  [resume] Re-loaded {len(all_sections)} already-scraped pages")

    print(f"\n{'='*60}")
    print(f"  Deep crawl starting: {start_url}")
    print(f"  Max pages : {max_pages}")
    print(f"  Batch size: {batch_size}")
    print(f"  Depth-first: {depth_first}")
    print(f"  Save individual pages: {save_individual_pages}")
    print(f"{'='*60}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=BROWSER_ARGS,
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers=EXTRA_HEADERS,
            java_script_enabled=True,
        )

        def block_media(route, request):
            if request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        apply_stealth(page)
        page.route("**/*", block_media)

        # ── Sitemap pre-seeding ────────────────────────────────────────────
        if use_sitemap and not resumed:
            print("[sitemap] Probing sitemap / robots.txt …")
            sitemap_urls = discover_sitemap_urls(page, start_url)
            state.add_to_queue(sitemap_urls)
            state.save()
            print(f"[sitemap] Queue size after sitemap: {state.queue_length()}\n")

        batch_num = 0

        while state.queue_length() > 0 and state.page_count < max_pages:
            batch_num += 1

            # Pull next batch from the front (BFS) or back (DFS) of queue
            batch_urls: list[str] = []
            for _ in range(batch_size):
                if state.queue_length() == 0:
                    break
                if depth_first:
                    url = state.data["to_visit"].pop()   # DFS: pop from back
                else:
                    url = state.data["to_visit"].pop(0)  # BFS: pop from front
                if not state.is_visited(url):
                    batch_urls.append(url)

            if not batch_urls:
                continue

            remaining = max_pages - state.page_count
            batch_urls = batch_urls[:remaining]

            print(f"\n── Batch {batch_num} ({len(batch_urls)} URLs) "
                  f"| scraped {state.page_count}/{max_pages} "
                  f"| queue {state.queue_length()} ──")

            batch_results = process_batch(
                page=page,
                urls=batch_urls,
                base_domain=base_domain,
                state=state,
                base_filename=base_filename,
                wait_for=wait_for,
                save_individual=save_individual_pages,
                max_pages=max_pages,
            )

            # Append new content and flush merged file after every batch
            for url, text in batch_results:
                all_sections.append(f"<!-- Page: {url} -->\n\n{text}")

            os.makedirs(DATA_PATH, exist_ok=True)
            with open(merged_output_path, "w", encoding="utf-8") as f:
                f.write(f"Source: {start_url}\n\n")
                f.write("\n\n---\n\n".join(all_sections))

            print(f"  [flush] Merged file updated → {merged_output_path} "
                  f"({len(all_sections)} pages, "
                  f"{os.path.getsize(merged_output_path):,} bytes)")

            state.save()

        browser.close()

    # ── Final flush ────────────────────────────────────────────────────────
    with open(merged_output_path, "w", encoding="utf-8") as f:
        f.write(f"Source: {start_url}\n\n")
        f.write("\n\n---\n\n".join(all_sections))

    print(f"\n{'='*60}")
    print(f"  Crawl complete!")
    print(f"  Pages scraped : {state.page_count}")
    print(f"  Failed URLs   : {len(state.data['failed'])}")
    print(f"  Merged output : {merged_output_path}")
    if save_individual_pages:
        print(f"  Per-page dir  : {pages_dir}/")
    print(f"{'='*60}\n")

    # Clean up state file on successful completion
    state.delete()

    output_files = [merged_output_path]
    if save_individual_pages and os.path.isdir(pages_dir):
        output_files += [
            os.path.join(pages_dir, fn)
            for fn in sorted(os.listdir(pages_dir))
            if fn.endswith(".md")
        ]
    return output_files


# ── Single-page scrape ────────────────────────────────────────────────────────

def scrape_and_save(url: str, filename: str, headless: bool = True) -> str:
    print(f"Scraping: {url}")
    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=BROWSER_ARGS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers=EXTRA_HEADERS,
        )
        page = context.new_page()
        apply_stealth(page)

        html = fetch_page_html(page, url)
        text = html_to_markdown(html, source_url=url) if html else "(no content)"
        browser.close()

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(text)

    print(f"Saved {len(text):,} characters → {filepath}")
    return filepath


# ── Multiple URLs helper ──────────────────────────────────────────────────────

def scrape_multiple(urls: dict):
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


# ── Paginated scraper ─────────────────────────────────────────────────────────

def scrape_all_pages(base_url: str, filename: str, max_pages: int = 50) -> list[str]:
    all_text: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = browser.new_page()
        apply_stealth(page)

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

    # ── Example 1: Deep crawl with batching (recommended for RAG) ────────────
    scrape_full_website(
        start_url="https://minimalist.ae/",
        base_filename="minimalist",
        max_pages=200,             # total page cap
        batch_size=10,             # flush merged .md every 10 pages
        headless=True,             # set False if Cloudflare blocks you
        save_individual_pages=True,# True = each page saved separately (great for RAG chunking)
        use_sitemap=True,          # auto-discover URLs via sitemap.xml / robots.txt
        resume=True,               # resume if the last run was interrupted
        depth_first=False,         # False = BFS (broad coverage); True = DFS (deep paths first)
    )

    # ── Example 2: Resume an interrupted crawl ────────────────────────────────
    # Just re-run the same scrape_full_website call — it will pick up where it
    # left off because resume=True and the state file still exists.

    # ── Example 3: Single page ────────────────────────────────────────────────
    # scrape_and_save("https://bvmengineering.ac.in/", "bvm.md")

    # ── Example 4: Multiple specific URLs ────────────────────────────────────
    # scrape_multiple({
    #     "alian_home":  "https://aliansoftware.com/en",
    #     "alian_about": "https://aliansoftware.com/en/about",
    # })

    # ── Example 5: Paginated static site ─────────────────────────────────────
    # scrape_all_pages("https://books.toscrape.com/", "books_all.md")
