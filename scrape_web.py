import warnings
warnings.filterwarnings("ignore")

import os
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

DATA_PATH = "data"

# ── Site-specific extractors ─────────────────────────────────────────────────

def extract_books_toscrape(soup: BeautifulSoup, url: str) -> str:
    """Extract book listings from books.toscrape.com cleanly."""
    lines = []

    title = soup.find("h1")
    if title:
        lines.append(f"# {title.get_text(strip=True)}\n")

    for article in soup.select("article.product_pod"):
        name_tag = article.select_one("h3 a")
        price_tag = article.select_one(".price_color")
        stock_tag = article.select_one(".availability")
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
    import re
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


# ── Main scraper ──────────────────────────────────────────────────────────────

def scrape_and_save(url: str, filename: str):
    print(f"Scraping: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace("www.", "")
    extractor = SITE_EXTRACTORS.get(domain)

    if extractor:
        clean_text = extractor(soup, url)
    else:
        clean_text = generic_extract(soup)

    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(clean_text)

    print(f"Saved {len(clean_text)} characters to {filepath}")
    return filepath


def scrape_multiple(urls: dict):
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


def scrape_all_pages(base_url: str, filename: str, max_pages: int = 50):
    """Scrape all paginated pages from a site like books.toscrape.com."""
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
    return filepath


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