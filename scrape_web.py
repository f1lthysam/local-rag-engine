import warnings
warnings.filterwarnings("ignore")

import os
import re
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urlparse, urljoin

DATA_PATH = "data"

# ── Site-specific extractors ─────────────────────────────────────────────────

def extract_books_toscrape(soup: BeautifulSoup, url: str) -> str:
    lines = []
    title = soup.find("h1")
    if title:
        lines.append(f"# {title.get_text(strip=True)}\n")

    for article in soup.select("article.product_pod"):
        name_tag   = article.select_one("h3 a")
        price_tag  = article.select_one(".price_color")
        stock_tag  = article.select_one(".availability")
        rating_tag = article.select_one(".star-rating")

        name   = name_tag["title"] if name_tag and name_tag.has_attr("title") else (name_tag.get_text(strip=True) if name_tag else "Unknown")
        price  = price_tag.get_text(strip=True).replace("Â", "").replace("\xa3", "£") if price_tag else "N/A"
        stock  = stock_tag.get_text(strip=True) if stock_tag else "N/A"
        rating = rating_tag["class"][1] if rating_tag and len(rating_tag["class"]) > 1 else "N/A"

        lines.append(f"**{name}**")
        lines.append(f"- Price: {price}")
        lines.append(f"- Stock: {stock}")
        lines.append(f"- Rating: {rating} stars\n")

    pager = soup.select_one("li.current")
    if pager:
        lines.append(f"\n_{pager.get_text(strip=True)}_")

    return "\n".join(lines)


SITE_EXTRACTORS = {
    "books.toscrape.com": extract_books_toscrape,
}

# ── Generic extractor ─────────────────────────────────────────────────────────

def generic_extract(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "img"]):
        tag.decompose()
    for img in soup.find_all("img"):
        img.decompose()
    markdown = md(str(soup), heading_style="ATX", bullets="-", strip=["a", "img"])
    return clean_markdown(markdown)


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


# ── Core fetch ────────────────────────────────────────────────────────────────

def fetch_and_extract(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    domain = urlparse(url).netloc.replace("www.", "")
    extractor = SITE_EXTRACTORS.get(domain)
    text = extractor(soup, url) if extractor else generic_extract(soup)
    return text, soup


# ── Single page scraper ───────────────────────────────────────────────────────

def scrape_and_save(url: str, filename: str) -> str:
    print(f"Scraping: {url}")
    text, _ = fetch_and_extract(url)

    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(text)

    print(f"Saved {len(text)} characters to {filepath}")
    return filepath


# ── Full website scraper — saves everything into ONE .md file ─────────────────

def scrape_full_website(start_url: str, base_filename: str, max_pages: int = 20) -> list:
    """
    Crawls the full website starting from start_url.
    Follows internal links up to max_pages.
    Saves ALL pages into a single .md file.
    Special case for books.toscrape.com — scrapes all 50 paginated pages.
    Returns list with single saved file path.
    """
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc

    # Special case: books.toscrape.com
    if "books.toscrape.com" in base_domain:
        return scrape_all_pages(start_url, base_filename + ".md")

    visited = set()
    to_visit = [start_url]
    all_sections = []
    page_count = 0

    while to_visit and page_count < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            print(f"Scraping page {page_count + 1}/{max_pages}: {url}")
            text, soup = fetch_and_extract(url)

            if not text.strip():
                continue

            # Append page content with source header
            all_sections.append(f"<!-- Page: {url} -->\n\n{text}")
            page_count += 1
            print(f"Fetched {len(text)} chars from {url}")

            # Find internal links to follow
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)

                if (parsed.netloc == base_domain and
                    full_url not in visited and
                    not parsed.fragment and
                    not parsed.path.endswith((".pdf", ".jpg", ".png", ".zip", ".css", ".js"))):
                    to_visit.append(full_url)

        except Exception as e:
            print(f"Failed: {url} — {e}")
            continue

    # Save everything into one single .md file
    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, base_filename + ".md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {start_url}\n\n")
        f.write("\n\n---\n\n".join(all_sections))

    print(f"Done. Saved {page_count} pages into {filepath}")
    return [filepath]


# ── Paginated scraper (books.toscrape.com) ────────────────────────────────────

def scrape_all_pages(base_url: str, filename: str, max_pages: int = 50) -> list:
    """Scrape all paginated pages from books.toscrape.com into one file."""
    all_text = []
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}catalogue/page-{page}.html"
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            text = extract_books_toscrape(soup, url)
            all_text.append(f"## Page {page}\n\n{text}")
            print(f"Scraped page {page}/{max_pages}")
        except Exception as e:
            print(f"Stopped at page {page}: {e}")
            break

    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {base_url}\n\n")
        f.write("\n\n".join(all_text))
    print(f"Saved all pages to {filepath}")
    return [filepath]


def scrape_multiple(urls: dict):
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


if __name__ == "__main__":
    # Scrape all 50 pages of books.toscrape.com
    scrape_all_pages("https://books.toscrape.com/", "books_all.md")

    # Single URL
    # scrape_and_save("https://books.toscrape.com", "books.md")

    # Multiple URLs
    # urls = {
    #     "example1.md": "https://aliansoftware.com/en",
    #     "example2.md": "https://aliansoftware.com/en/about",
    #     "example3.md": "https://aliansoftware.com/en/work",
    #     "example4.md": "https://aliansoftware.com/en/pricing",
    #     "example5.md": "https://aliansoftware.com/en/blog",
    #     "example6.md": "https://bvmengineering.ac.in/",
    # }
    # scrape_multiple(urls)