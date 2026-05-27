import warnings
warnings.filterwarnings("ignore")

import os
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

DATA_PATH = "data"


def clean_markdown(markdown: str) -> str:
    lines = [line.rstrip() for line in markdown.splitlines()]
    clean_lines = []
    previous_blank = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if not previous_blank:
                clean_lines.append("")
            previous_blank = True
            continue

        clean_lines.append(stripped)
        previous_blank = False

    return "\n".join(clean_lines).strip()


def scrape_and_save(url: str, filename: str):
    print(f"Scraping: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove junk tags before converting the page body to Markdown.
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
        tag.decompose()

    markdown = md(str(soup), heading_style="ATX", bullets="-", strip=["a"])
    clean_text = clean_markdown(markdown)

    os.makedirs(DATA_PATH, exist_ok=True)
    filename = os.path.splitext(filename)[0] + ".md"
    filepath = os.path.join(DATA_PATH, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(clean_text)

    print(f"Saved {len(clean_text)} characters to {filepath}")
    return filepath


def scrape_multiple(urls: dict):
    """
    Pass a dict of {filename: url} to scrape multiple pages.
    Example: {"chess.md": "https://www.chess.com/learn-how-to-play-chess"}
    """
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")


if __name__ == "__main__":
    # Single URL
    # scrape_and_save("https://example.com/article", "article.md")

    # Multiple URLs
    urls = {
        "example1.md": "https://aliansoftware.com/en",
        "example2.md": "https://aliansoftware.com/en/about",
        "example3.md": "https://aliansoftware.com/en/work",
        "example4.md": "https://aliansoftware.com/en/pricing",
        "example5.md": "https://aliansoftware.com/en/blog",
        "example6.md": "https://bvmengineering.ac.in/",
    }
    scrape_multiple(urls)
