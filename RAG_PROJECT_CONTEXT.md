# RAG Project — Context & Documentation
> A local Retrieval-Augmented Generation (RAG) engine built in Python, designed to answer questions from scraped website content and documents using Google Gemini as the LLM and ChromaDB as the vector store.

---

## Project Overview

This project is a fully local RAG pipeline that:
- Scrapes websites (including JS-rendered/SPA sites) and converts them to Markdown
- Chunks and embeds the content into a vector database
- Answers user questions by retrieving relevant chunks and passing them to an LLM
- Runs entirely in Python with no external RAG framework dependency

**Project folder:** `D:\rag-tutorial-v2-fixed(2)`  
**Environment:** Windows, Python 3.x, virtualenv

---

## Tech Stack

| Component | Technology |
|---|---|
| Web scraping | Playwright (headless Chromium) |
| HTML → Markdown | markdownify + BeautifulSoup |
| Chunking | LangChain `RecursiveCharacterTextSplitter` |
| Embeddings | HuggingFace `all-MiniLM-L6-v2` (local, 384-dim) |
| Vector store | ChromaDB (persisted locally) |
| LLM | Google Gemini 3.1 flash-lite via LangChain |
| Document loading | LangChain `PyPDFDirectoryLoader` + `DirectoryLoader` |

---

## File Structure

```
rag-tutorial-v2-fixed(2)/
├── scraper.py              # Playwright-based web scraper
├── populate_database.py    # Chunks, embeds, stores into ChromaDB
├── query_data.py           # Query engine — retrieval + LLM answering
├── get_embedding_function.py  # HuggingFace embedding model loader
├── app.py                  # Web interface (calls query_rag_web())
├── test_rag.py             # Testing utilities
├── data/                   # Scraped .md and .pdf files
│   └── *.md                # One file per scraped site/page
└── chroma/                 # Persisted ChromaDB vector store
```

---

## Pipeline — How It Works

### Phase 1 — Ingestion
```
URLs → Playwright (headless Chrome) → Full DOM HTML
     → markdownify → clean .md files → data/
     → populate_database.py → chunk (1500 chars, 200 overlap)
     → HuggingFace embed → ChromaDB (persisted)
```

### Phase 2 — Query
```
User question → plan_retrieval() → similarity search (ChromaDB)
             → build_dynamic_context() → token-budget trim
             → Gemini LLM → Answer + Confidence + Sources + Latency
```

---

## Features

### Scraping (`scraper.py`)
- Playwright headless Chromium — handles JS-rendered / React / Next.js / Vue sites
- Scroll-to-bottom trigger for lazy-loaded content (team grids, dynamic sections)
- Double scroll + 3 second wait to ensure full DOM render
- Boilerplate stripper — removes nav, footer, cookie banners, rating widgets (Upwork, Clutch, Glassdoor)
- Three scrape modes: single page, multi-page crawl, full website crawler
- Internal link discovery via `<a href>` tag crawling with deduplication
- Media blocking (images, fonts, video) for ~60% faster crawls
- Auto-retry with fallback wait strategy on timeout
- Output: clean `.md` files saved to `data/`

### Ingestion (`populate_database.py`)
- Loads `.md` and `.pdf` files from `data/`
- Chunks with `RecursiveCharacterTextSplitter` — 1500 char size, 200 char overlap
- Content hash tracking — skips unchanged chunks, only re-embeds what changed
- Chunk ID system for deduplication across runs
- `--reset` flag to wipe and rebuild ChromaDB from scratch

### Retrieval (`query_data.py`)
- **Dynamic retrieval planning** — auto-selects mode based on query type:
  - `focused` (k=6, 600 tok/chunk) — fact questions: who, what, CEO, email
  - `balanced` (k=9, 650 tok/chunk) — default and history-aware queries
  - `broad` (k=12, 850 tok/chunk) — summary, explain, compare, list queries
- **Token-budget context building** — never overflows the LLM prompt window
- **Keyword overlap reranking** — prioritises chunks with most query term matches
- **Similarity score threshold** — `THRESHOLD = 1.2` (cosine distance), rejects weak matches
- **Three-layer fallback system:**
  - Layer 1 → Direct Markdown pattern matching (CEO/role regex — catches "Name is CEO" format)
  - Layer 2 → Vector similarity search (ChromaDB)
  - Layer 3 → Lexical keyword search across all `.md` files
- **Confidence scoring** — maps vector distance to percentage (100% = perfect, 40% = floor)
- **Chat history support** — last 5 turns passed into prompt for follow-up awareness
- **Token counting** — tracks prompt + response + total tokens per query
- **Latency tracking** — reports response time per query

### LLM & Prompt
- Google Gemini 3.1 flash-lite, temperature 0.1 (factual, low hallucination)
- Smart prompt with priority rules:
  1. Follow-ups ("why?", "more?", "explain that") resolved via conversation history
  2. Document context takes priority as primary source
  3. General knowledge fallback for non-document questions
  4. "I don't know" only as last resort for document-specific questions with no match

### Embeddings
- `all-MiniLM-L6-v2` — 22M params, 384-dim vectors, fast CPU inference
- Model weights cached locally after first download
- No API cost — runs fully offline

---

## Known Limitations

- **No persistent chat history across CLI runs** — history resets every session
- **No sitemap support** — crawls via link discovery only, may miss unlinked pages
- **Static chunk size** — 1500 chars for all content types (no dynamic sizing)
- **Single embedding model** — `all-MiniLM-L6-v2` is lightweight but may miss nuanced domain-specific matches
- **Confidence ≠ answer accuracy** — confidence only measures vector similarity, not correctness of Gemini's answer

---

## Configuration Constants (`query_data.py`)

| Constant | Value | Purpose |
|---|---|---|
| `THRESHOLD` | 1.2 | Max cosine distance to accept a chunk |
| `MIN_CONFIDENCE` | 40.0 | Minimum confidence % shown |
| `DEFAULT_K` | 5 | Default chunks to retrieve |
| `MAX_RETRIEVAL_K` | 14 | Hard cap on chunks retrieved |
| `MIN_CONTEXT_TOKENS` | 900 | Minimum context sent to LLM |
| `MAX_CONTEXT_TOKENS` | 4200 | Maximum context sent to LLM |
| `MAX_HISTORY_CHARS` | 2500 | Max characters of history passed to prompt |
| `chunk_size` | 1500 | Characters per chunk (populate_database.py) |
| `chunk_overlap` | 200 | Overlap between chunks |

---

## History & Evolution

### Version 1 — BeautifulSoup scraper
- Used `requests` + `BeautifulSoup4` for scraping
- Problem: only fetched static HTML, missed all JS-rendered content
- Sites like aliansoftware.com (Next.js/React) returned empty team/CEO data
- Generic extractor stripped too aggressively, losing meaningful content

### Version 2 — Playwright scraper (current)
- Replaced `requests` + BS4 with Playwright headless Chromium
- Added scroll-trigger for lazy-loaded sections
- Added boilerplate detection and removal
- Fixed threshold (`2.4 → 1.2`) for better retrieval acceptance
- Fixed chunk token budget (`380 → 600`) in focused mode to prevent mid-sentence cutoff
- Added 4 CEO/role regex patterns to catch natural language formats ("Name is CEO")
- Upgraded prompt from strict "ONLY context" to smart prioritised rules
- Added `team`, `members`, `founder`, `director` to fact markers for better mode selection
- Widened lexical fallback window from 5 to 8 lines for richer snippets

---

## Planned Upgrades

- [ ] Persistent chat history saved to `chat_history.json` across CLI sessions
- [ ] Sitemap-based URL discovery for complete site coverage
- [ ] Dynamic chunk sizing based on content type
- [ ] Upgrade embedding model to `all-mpnet-base-v2` (768-dim) for better accuracy
- [ ] Query rewriting for follow-ups before ChromaDB search

---

## How to Run

```powershell
# Activate virtualenv
& "D:\rag-tutorial-v2-fixed(2)\venv\Scripts\Activate.ps1"

# Scrape a website
python -c "from scraper import scrape_multiple; scrape_multiple({'site': 'https://example.com'})"

# Populate ChromaDB
python populate_database.py

# Reset and repopulate
python populate_database.py --reset

# Ask a question
python query_data.py "who is the CEO of Alian Software?"

# Interactive mode
python query_data.py --interactive
```

---

*Built and iterated with Claude (Anthropic) — June 2026*
