import warnings
warnings.filterwarnings("ignore")

import os
import requests
from bs4 import BeautifulSoup

DATA_PATH = "data"

def scrape_and_save(url: str, filename: str):
    print(f"Scraping: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Remove junk tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
        tag.decompose()
    
    # Extract clean text
    text = soup.get_text(separator="\n", strip=True)
    
    # Remove excessive blank lines
    lines = [line for line in text.splitlines() if line.strip()]
    clean_text = "\n".join(lines)
    
    # Save to data folder
    os.makedirs(DATA_PATH, exist_ok=True)
    filepath = os.path.join(DATA_PATH, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {url}\n\n")
        f.write(clean_text)
    
    print(f"✅ Saved {len(clean_text)} characters to {filepath}")
    return filepath


def scrape_multiple(urls: dict):
    """
    Pass a dict of {filename: url} to scrape multiple pages.
    Example: {"chess.txt": "https://www.chess.com/learn-how-to-play-chess"}
    """
    for filename, url in urls.items():
        try:
            scrape_and_save(url, filename)
        except Exception as e:
            print(f"❌ Failed to scrape {url}: {e}")


if __name__ == "__main__":
    # ── Single URL ──────────────────────────────────────────────
    # scrape_and_save("https://example.com/article", "article.txt")

    # ── Multiple URLs ───────────────────────────────────────────
    urls = {
        "example1.txt": "https://aliansoftware.com/en",
        # "example2.txt": "https://example.com/page2",
    }
    scrape_multiple(urls)