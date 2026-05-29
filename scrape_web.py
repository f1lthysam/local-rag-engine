import warnings
warnings.filterwarnings("ignore")

import os
import re
import time
import hashlib
from urllib.parse import urlparse, urljoin
from markdownify import markdownify as md

# ── Playwright replaces requests + BeautifulSoup ──────────────────────────────
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DATA_PATH = "data"

# ── Markdown cleaner (unchanged from original) ────────────────────────────────

def clean_markdown(text: str) -> str:
    text = text.replace("Â£", "£").replace("Â", "").replace("\xa3", "£")
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)          # remove image refs
    text = re.sub(r"\n{3,}", "\n\n", text)               # collapse excess newlines
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

    wait_for options:
        "networkidle"  → waits until no network requests for 500ms (best for SPAs)
        "domcontentloaded" → faster, good for mostly-static sites
        "load"         → waits for the load event
    """
    try:
        page.goto(url, wait_until=wait_for, timeout=45_000)
        # Scroll to bottom to trigger lazy-loaded sections (team grids, etc.)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Generous wait for JS-heavy SPAs (React/Vue/Next.js)
        page.wait_for_timeout(3000)
        return page.content()
    except PlaywrightTimeoutError:
        print(f"  [timeout] Retrying {url} with domcontentloaded …")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
            return page.content()
        except Exception as e:
            print(f"  [error] Could not fetch {url}: {e}")
            return ""


def html_to_markdown(html: str) -> str:
    """
    Convert raw HTML to clean Markdown.
    Strips boilerplate tags before conversion.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove repeated boilerplate blocks (footer ratings, cookie banners, etc.)
    for tag in soup.find_all(["footer", "nav", "header", "aside",
                               "script", "style", "noscript", "iframe",
                               "form", "svg", "button"]):
        tag.decompose()

    # Also remove any div/section that only contains rating links (Upwork, Clutch, etc.)
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


# ── Internal link extractor ───────────────────────────────────────────────────

def get_internal_links(page, base_domain: str, current_url: str) -> list[str]:
    """Extract all internal hrefs from the current page via Playwright."""
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
        # Keep only same-domain, non-fragment, non-binary URLs
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
            # Normalise: drop query strings for dedup (keep if needed)
            clean = parsed.scheme + "://" + parsed.netloc + parsed.path
            clean = clean.rstrip("/") or clean   # normalise trailing slash
            links.append(clean)
    return list(set(links))


# ── Single-page scrape ────────────────────────────────────────────────────────

def scrape_and_save(url: str, filename: str) -> str:
    """Scrape a single URL, save as .md, return filepath."""
    print(f"Scraping: {url}")
    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

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
) -> list[str]:
    """
    Crawl an entire website with a single persistent browser session.
    Follows internal links up to max_pages.
    All pages are merged into ONE .md file (same contract as original).
    Returns list containing the single saved filepath.
    """
    parsed_start = urlparse(start_url)
    base_domain  = parsed_start.netloc

    visited    = set()
    to_visit   = [start_url.rstrip("/")]
    all_sections: list[str] = []
    page_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            # Block images/fonts to speed up crawling
            java_script_enabled=True,
        )

        # Block heavy media to reduce crawl time
        def block_media(route, request):
            if request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
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

            # Discover new links
            new_links = get_internal_links(page, base_domain, url)
            for link in new_links:
                if link not in visited and link not in to_visit:
                    to_visit.append(link)

        browser.close()

    # Save combined output
    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, base_filename + ".md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {start_url}\n\n")
        f.write("\n\n---\n\n".join(all_sections))

    print(f"\nDone. {page_count} pages → {filepath}")
    return [filepath]


# ── Multiple URLs helper ──────────────────────────────────────────────────────

def scrape_multiple(urls: dict):
    """Scrape a dict of {filename: url} one by one."""
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


# ── Paginated scraper (books.toscrape.com kept for compatibility) ─────────────

def scrape_all_pages(base_url: str, filename: str, max_pages: int = 50) -> list[str]:
    """
    Kept for backward-compat. Scrapes paginated books.toscrape.com.
    Now also uses Playwright for consistency.
    """
    all_text: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

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
    # ── Example 1: scrape a full website (JS-rendered) ────────────────────────
    scrape_full_website(
        "https://aliansoftware.com/en",
        "aliansoftware",
        max_pages=20,
    )

    # ── Example 2: single page ────────────────────────────────────────────────
    # scrape_and_save("https://bvmengineering.ac.in/", "bvm.md")

    # ── Example 3: multiple URLs ──────────────────────────────────────────────
    # scrape_multiple({
    #     "alian_home":    "https://aliansoftware.com/en",
    #     "alian_about":   "https://aliansoftware.com/en/about",
    #     "alian_work":    "https://aliansoftware.com/en/work",
    #     "alian_pricing": "https://aliansoftware.com/en/pricing",
    #     "alian_blog":    "https://aliansoftware.com/en/blog",
    #     "bvm":           "https://bvmengineering.ac.in/",
    # })

    # ── Example 4: books.toscrape.com (paginated, static) ────────────────────
    # scrape_all_pages("https://books.toscrape.com/", "books_all.md")