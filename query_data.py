import warnings
warnings.filterwarnings("ignore")

import os
import logging
logging.disable(logging.CRITICAL)

import argparse
import json
from pathlib import Path
import re
import time
import tiktoken
from dotenv import load_dotenv
load_dotenv()
os.environ["PYTHONWARNINGS"] = "ignore"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
CHROMA_PATH = "chroma"
DATA_PATH = "data"
THRESHOLD = 1.2
MIN_CONFIDENCE = 40.0
LEXICAL_FALLBACK_CONFIDENCE = 40.0
DEFAULT_K = 5
MIN_CONTEXT_TOKENS = 900
MAX_CONTEXT_TOKENS = 4200
MAX_RETRIEVAL_K = 14
MAX_HISTORY_CHARS = 2500
FOUL_LANGUAGE_RESPONSE = "Please keep the conversation respectful. I can't process requests that contain foul language."
FOUL_LANGUAGE_TERMS = {
    "ass",
    "asshole",
    "bastard",
    "bitch",
    "bullshit",
    "crap",
    "cunt",
    "damn",
    "dick",
    "fuck",
    "fucker",
    "fucking",
    "motherfucker",
    "piss",
    "prick",
    "shit",
    "slut",
    "whore",
}
_DB = None
_PROMPT_TEMPLATE = None
_GEMINI_MODEL = None


PROMPT_TEMPLATE = """
You are a helpful, conversational assistant. Use the rules below in order:

1. FOLLOW-UPS: If the question is a follow-up ("why?", "explain more", "what about that?", "how?"),
   use the conversation history to understand what is being referred to, then answer it naturally.

2. DOCUMENT CONTEXT: If the retrieved context contains relevant information, use it as your
   primary source and answer directly and concisely. Prefer context over general knowledge.

3. ALLOWED GENERAL: You may answer ONLY these types of questions from general knowledge:
   - Greetings and small talk ("hi", "how are you", "thanks")
   - Coding/technical help unrelated to the documents
   - Simple definitions of common technical terms

4. BLOCKED: For ANY question about a person, place, brand, product, event, or topic
   that is not mentioned in the retrieved context, respond with exactly:
   "I don't have information about that in my knowledge base."
   
{custom_instructions}

{dataset_note}

Conversation history:
{history}

---

Context from documents:
{context}

---

Question: {question}
"""


def count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(str(text)))


def normalize_for_profanity(text: str) -> str:
    normalized = str(text).lower()
    normalized = normalized.replace("@", "a").replace("$", "s").replace("!", "i")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def contains_foul_language(text: str) -> bool:
    words = set(normalize_for_profanity(text).split())
    return bool(words & FOUL_LANGUAGE_TERMS)


def foul_language_result(query_text: str, start_time: float | None = None,
                         dataset_filter: str = None) -> dict:
    latency = 0 if start_time is None else time.perf_counter() - start_time
    response_tokens = count_tokens(FOUL_LANGUAGE_RESPONSE)
    prompt_tokens = count_tokens(query_text)
    return {
        "response":        FOUL_LANGUAGE_RESPONSE,
        "confidence":      None,
        "sources":         [],
        "no_info":         True,
        "blocked":         True,
        "block_reason":    "foul_language",
        "latency":         round(latency, 2),
        "prompt_tokens":   prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens":    prompt_tokens + response_tokens,
        "retrieval_mode":  "guardrail",
        "retrieved_chunks": 0,
        "context_tokens":  0,
        "dataset_filter":  dataset_filter,
    }


def extract_response_text(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(raw)


def resolve_source_path(filename: str) -> str:
    """Convert a bare filename like 'books_all.md' to its full relative path."""
    candidate = str(Path(DATA_PATH) / filename)
    if Path(candidate).exists():
        return candidate
    # Already a full path
    if Path(filename).exists():
        return filename
    return candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query_text", nargs="?", type=str)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--force-rag", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--dataset", type=str, default=None,
                        help="Restrict answers to a single .md file (filename only).")
    args = parser.parse_args()

    if args.interactive:
        run_interactive(args.k, args.debug, args.no_llm, args.force_rag)
        return
    if not args.query_text:
        parser.error("query_text is required unless --interactive is used.")

    query_rag(args.query_text, k=args.k, debug=args.debug,
              no_llm=args.no_llm, force_rag=args.force_rag,
              dataset_filter=args.dataset)


def query_rag(
    query_text: str,
    k=None,
    debug: bool = False,
    no_llm: bool = False,
    force_rag: bool = False,
    dataset_filter: str = None,
):
    start_time     = time.perf_counter()
    if contains_foul_language(query_text):
        result = foul_language_result(query_text, start_time, dataset_filter)
        print(f"\nResponse: {result['response']}")
        print("Guardrail: foul_language")
        print("Sources:  []")
        print(f"Latency:  {result['latency']:.2f}s")
        print(f"Tokens:   prompt={result['prompt_tokens']} · response={result['response_tokens']} · total={result['total_tokens']}\n")
        return result["response"]

    retrieval_plan = plan_retrieval(query_text, k_override=k)

    if not force_rag and not dataset_filter:
        direct_answer = find_direct_markdown_answer(query_text)
        if direct_answer:
            answer, source = direct_answer
            latency        = time.perf_counter() - start_time
            prompt_tokens  = count_tokens(query_text)
            response_tokens = count_tokens(answer)
            print(f"\nResponse: {answer}")
            print("Confidence: 100.0%")
            print(f"Sources:  ['{source}']")
            print(f"Latency:  {latency:.2f}s")
            print(f"Tokens:   prompt={prompt_tokens} · response={response_tokens} · total={prompt_tokens + response_tokens}\n")
            return answer

    db      = get_vector_db()
    results = similarity_search_filtered(db, query_text, retrieval_plan["k"], dataset_filter)
    retrieval_done_time = time.perf_counter()

    if debug:
        print(f"Retrieval: {retrieval_done_time - start_time:.2f}s")
        print(f"Scores: {[score for _, score in results]}")

    if not results or results[0][1] > THRESHOLD:
        if not dataset_filter:
            lexical_answer = answer_from_lexical_fallback(
                query_text, "No previous conversation.", start_time)
            if lexical_answer:
                print(f"\nResponse: {lexical_answer['response']}")
                print(f"Confidence: {lexical_answer['confidence']}%")
                print(f"Sources:  {lexical_answer['sources']}")
                print(f"Latency:  {lexical_answer['latency']:.2f}s")
                print(f"Tokens:   prompt={lexical_answer['prompt_tokens']} · response={lexical_answer['response_tokens']} · total={lexical_answer['total_tokens']}\n")
                return lexical_answer["response"]
        print("\nResponse: I don't have information about that in my documents.")
        print("Confidence: N/A")
        print("Sources:  []")
        print(f"Latency:  {time.perf_counter() - start_time:.2f}s")
        print("Tokens:   N/A\n")
        return

    context_text, relevant_results = build_dynamic_context(results, retrieval_plan, query_text)
    if not context_text:
        if not dataset_filter:
            lexical_answer = answer_from_lexical_fallback(
                query_text, "No previous conversation.", start_time)
            if lexical_answer:
                print(f"\nResponse: {lexical_answer['response']}")
                return lexical_answer["response"]
        print("\nResponse: I don't have information about that in my documents.")
        return

    if no_llm:
        sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]
        print("\nRetrieved Context:\n")
        print(context_text)
        print(f"\nSources:  {sources}\n")
        return context_text

    dataset_note = _dataset_note(dataset_filter)
    custom_instructions = get_published_prompt_instructions()
    prompt = get_prompt_template().format(
        context=context_text,
        history="No previous conversation.",
        question=query_text,
        dataset_note=dataset_note,
        custom_instructions=custom_instructions,
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw           = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)
    llm_done_time = time.perf_counter()

    if is_no_info_response(response_text) and not dataset_filter:
        lexical_answer = answer_from_lexical_fallback(
            query_text, "No previous conversation.", start_time)
        if lexical_answer and not lexical_answer["no_info"]:
            print(f"\nResponse: {lexical_answer['response']}")
            return lexical_answer["response"]

    latency         = llm_done_time - start_time
    response_tokens = count_tokens(response_text)
    total_tokens    = prompt_tokens + response_tokens
    best_score      = results[0][1]
    confidence      = distance_to_confidence(best_score)
    sources         = [doc.metadata.get("id", None) for doc, _score in relevant_results]

    print(f"\nResponse: {response_text}")
    print(f"Confidence: {confidence:.1f}%")
    print(f"Sources:  {sources}")
    print(f"Latency:  {latency:.2f}s")
    print(f"Tokens:   prompt={prompt_tokens} · response={response_tokens} · total={total_tokens}\n")
    return response_text


def query_rag_web(query_text: str, chat_history=None, dataset_filter: str = None, collection_name: str = None):
    """
    Main web-facing query function.
    dataset_filter: bare filename like 'aliansoftware.com-en.md'
                    or None to search all datasets.
    """
    start_time      = time.perf_counter()
    if contains_foul_language(query_text):
        return foul_language_result(query_text, start_time, dataset_filter)

    history_text    = format_chat_history(chat_history)
    retrieval_query = build_retrieval_query(query_text, chat_history)
    retrieval_plan  = plan_retrieval(query_text, chat_history=chat_history)
    dataset_note    = _dataset_note(dataset_filter)
    custom_instructions = get_published_prompt_instructions()

    # Direct markdown lookup only when no dataset filter is active
    if not dataset_filter:
        direct_answer = find_direct_markdown_answer(query_text)
        if direct_answer:
            answer, source  = direct_answer
            latency         = time.perf_counter() - start_time
            prompt_tokens   = count_tokens(query_text)
            response_tokens = count_tokens(answer)
            return {
                "response":        answer,
                "confidence":      100.0,
                "sources":         [source],
                "no_info":         False,
                "latency":         round(latency, 2),
                "prompt_tokens":   prompt_tokens,
                "response_tokens": response_tokens,
                "total_tokens":    prompt_tokens + response_tokens,
                "retrieval_mode":  "direct",
                "retrieved_chunks": 0,
                "context_tokens":  0,
                "dataset_filter":  None,
            }

    db = get_vector_db(collection_name=collection_name)
    results = similarity_search_filtered(db, retrieval_query, retrieval_plan["k"], dataset_filter)

    if not results or results[0][1] > THRESHOLD:
        # If filtering is active, do NOT fall back to lexical (would leak other datasets)
        if not dataset_filter:
            lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
            if lexical_answer:
                lexical_answer["dataset_filter"] = None
                return lexical_answer

        latency = time.perf_counter() - start_time
        return _no_info_result(retrieval_query, retrieval_plan, latency, dataset_filter)

    context_text, relevant_results = build_dynamic_context(results, retrieval_plan, query_text)
    if not context_text:
        if not dataset_filter:
            lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
            if lexical_answer:
                lexical_answer["dataset_filter"] = None
                return lexical_answer
        latency = time.perf_counter() - start_time
        return _no_info_result(retrieval_query, retrieval_plan, latency, dataset_filter)

    prompt = get_prompt_template().format(
        context=context_text,
        history=history_text,
        question=query_text,
        dataset_note=dataset_note,
        custom_instructions=custom_instructions,
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw           = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)

    if is_no_info_response(response_text) and not dataset_filter:
        lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
        if lexical_answer and not lexical_answer["no_info"]:
            lexical_answer["dataset_filter"] = None
            return lexical_answer

    latency         = time.perf_counter() - start_time
    response_tokens = count_tokens(response_text)
    total_tokens    = prompt_tokens + response_tokens
    confidence      = round(distance_to_confidence(results[0][1]), 1)
    sources         = [doc.metadata.get("id", None) for doc, _score in relevant_results]

    return {
        "response":        response_text,
        "confidence":      confidence,
        "sources":         sources,
        "no_info":         False,
        "latency":         round(latency, 2),
        "prompt_tokens":   prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens":    total_tokens,
        "retrieval_mode":  retrieval_plan["mode"],
        "retrieved_chunks": len(relevant_results),
        "context_tokens":  count_tokens(context_text),
        "dataset_filter":  dataset_filter,
    }


# ── Dataset filter helpers ────────────────────────────────────────────────────

def similarity_search_filtered(db, query: str, k: int, dataset_filter: str = None):
    """
    Run ChromaDB similarity search.
    If dataset_filter is set, restrict results to chunks whose 'source'
    metadata ends with the selected filename.
    """
    if not dataset_filter:
        return db.similarity_search_with_score(query, k=k)

    # ChromaDB stores source as e.g. "data\\aliansoftware.com-en.md" or "data/aliansoftware.com-en.md"
    # We match using $contains on the source field
    try:
        return db.similarity_search_with_score(
            query,
            k=k,
            filter={"source": {"$contains": dataset_filter}},
        )
    except Exception:
        # Fallback: fetch more results and filter manually (handles older ChromaDB versions)
        all_results = db.similarity_search_with_score(query, k=k * 4)
        return [
            (doc, score)
            for doc, score in all_results
            if dataset_filter in (doc.metadata.get("source") or "")
        ][:k]


def _dataset_note(dataset_filter: str) -> str:
    """Build the dataset restriction note injected into the prompt."""
    if not dataset_filter:
        return ""
    return (
        f"IMPORTANT: You are restricted to the dataset '{dataset_filter}'. "
        f"Only answer using the context provided above. "
        f"If the question is not answerable from this dataset, say exactly: "
        f"\"I don't have information about that in the selected dataset.\""
    )


def get_published_prompt_instructions() -> str:
    settings_path = Path("prompt_settings.json")
    if not settings_path.exists():
        return ""
    try:
        data = json.loads(settings_path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    published = data.get("published") or {}
    role = str(published.get("role") or "").strip()
    constraints = str(published.get("constraints") or "").strip()
    if not role and not constraints:
        return ""

    parts = ["CUSTOM DEPLOYED PROMPT SETTINGS:"]
    if role:
        parts.append(f"- Role: {role}")
    if constraints:
        parts.append(f"- Additional constraints: {constraints}")
    return "\n".join(parts)


def _no_info_result(retrieval_query: str, retrieval_plan: dict,
                    latency: float, dataset_filter: str) -> dict:
    msg = (
        f"I don't have information about that in the selected dataset '{dataset_filter}'."
        if dataset_filter
        else "I don't have information about that in my documents."
    )
    return {
        "response":        msg,
        "confidence":      None,
        "sources":         [],
        "no_info":         True,
        "latency":         round(latency, 2),
        "prompt_tokens":   count_tokens(retrieval_query),
        "response_tokens": None,
        "total_tokens":    count_tokens(retrieval_query),
        "retrieval_mode":  retrieval_plan["mode"],
        "retrieved_chunks": 0,
        "context_tokens":  0,
        "dataset_filter":  dataset_filter,
    }


# ── Lexical fallback ──────────────────────────────────────────────────────────

def answer_from_lexical_fallback(query_text: str, history_text: str, start_time, data_path: str = DATA_PATH):
    context_text, sources = build_lexical_context(query_text, data_path)
    if not context_text:
        return None

    prompt = get_prompt_template().format(
        context=context_text,
        history=history_text,
        question=query_text,
        dataset_note="",
        custom_instructions=get_published_prompt_instructions(),
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw           = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)

    latency         = time.perf_counter() - start_time
    response_tokens = count_tokens(response_text)
    total_tokens    = prompt_tokens + response_tokens
    no_info         = is_no_info_response(response_text)

    return {
        "response":        response_text,
        "confidence":      None if no_info else LEXICAL_FALLBACK_CONFIDENCE,
        "sources":         [] if no_info else sources,
        "no_info":         no_info,
        "latency":         round(latency, 2),
        "prompt_tokens":   prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens":    total_tokens,
        "retrieval_mode":  "keyword-fallback",
        "retrieved_chunks": 0 if no_info else len(sources),
        "context_tokens":  count_tokens(context_text),
    }


def build_lexical_context(query_text: str, data_path: str = DATA_PATH):
    query_terms = extract_search_terms(query_text)
    if not query_terms:
        return "", []
    matches = []
    for path in Path(DATA_PATH).glob("*.md"):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(lines):
            normalized = normalize_for_search(line)
            line_terms = set(normalized.split())
            score      = sum(1 for term in query_terms if term in line_terms)
            if score == 0:
                continue
            window_start = max(0, index - 2)
            window_end   = min(len(lines), index + 8)
            snippet      = "\n".join(lines[window_start:window_end]).strip()
            matches.append((score, path.as_posix(), index + 1, snippet))

    if not matches:
        return "", []

    matches.sort(key=lambda item: item[0], reverse=True)
    context_parts = []
    sources       = []
    used          = set()

    for _score, source, line_number, snippet in matches[:8]:
        source_id = f"{source}:{line_number}"
        if snippet in used:
            continue
        used.add(snippet)
        context_parts.append(f"Source: {source_id}\n{snippet}")
        sources.append(source_id)

    context = "\n\n---\n\n".join(context_parts)
    return trim_to_token_budget(context, 1800), sources


# ── Retrieval planning ────────────────────────────────────────────────────────

def extract_search_terms(query_text: str):
    stopwords = {
        "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is",
        "it", "of", "on", "or", "the", "their", "there", "to", "what", "who",
        "whom", "whose", "with", "tell", "me", "about", "give", "show", "te",
    }
    normalized = normalize_for_search(query_text)
    terms      = [t for t in normalized.split() if len(t) > 1 and t not in stopwords]
    role       = extract_role_from_query(query_text)
    if role:
        terms.append(role.lower())
    return sorted(set(terms), key=len, reverse=True)


def normalize_for_search(text: str):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def is_no_info_response(response_text: str):
    normalized = normalize_for_search(response_text)
    return "i don t have information about that in my documents" in normalized


def plan_retrieval(query_text: str, chat_history=None, k_override=None):
    query      = query_text.lower()
    words      = re.findall(r"\w+", query)
    word_count = len(words)
    has_history = bool(chat_history)

    broad_markers = {
        "summarize", "summary", "explain", "describe", "overview", "details",
        "compare", "difference", "differences", "list", "all", "features",
        "services", "steps", "process", "why", "how",
    }
    fact_markers = {
        "who", "what", "when", "where", "which", "email", "phone", "price",
        "cost", "ceo", "cfo", "cto", "address", "team", "members", "founder",
        "director", "engineer", "staff", "people", "person", "name",
    }

    broad_score = sum(1 for m in broad_markers if m in query)
    fact_score  = sum(1 for m in fact_markers  if m in query)

    if broad_score >= 2 or word_count > 18:
        mode, k, context_tokens, max_chunk_tokens = "broad",    12, 3600, 850
    elif broad_score == 1 or has_history:
        mode, k, context_tokens, max_chunk_tokens = "balanced",  9, 2600, 650
    elif fact_score >= 1 or word_count <= 8:
        mode, k, context_tokens, max_chunk_tokens = "focused",   6, 1400, 600
    else:
        mode, k, context_tokens, max_chunk_tokens = "balanced",  DEFAULT_K, 1600, 550

    if k_override is not None:
        k = k_override

    return {
        "mode":             mode,
        "k":                max(1, min(MAX_RETRIEVAL_K, k)),
        "context_tokens":   max(MIN_CONTEXT_TOKENS, min(MAX_CONTEXT_TOKENS, context_tokens)),
        "max_chunk_tokens": max(250, min(1000, max_chunk_tokens)),
    }


def build_dynamic_context(results, retrieval_plan, query_text=""):
    if not results:
        return "", []

    query_terms = set(extract_search_terms(query_text))
    candidate_results = [
        (doc, score) for doc, score in results if score <= THRESHOLD
    ]
    candidate_results.sort(key=lambda item: (
        -keyword_overlap(item[0].page_content, query_terms), item[1]
    ))

    context_parts   = []
    selected_results = []
    used_tokens     = 0
    token_budget    = retrieval_plan["context_tokens"]
    per_chunk_budget = retrieval_plan["max_chunk_tokens"]

    for doc, score in candidate_results:
        chunk_text      = trim_to_token_budget(doc.page_content, per_chunk_budget)
        chunk_tokens    = count_tokens(chunk_text)
        separator_tokens = count_tokens("\n\n---\n\n") if context_parts else 0

        if used_tokens + separator_tokens + chunk_tokens > token_budget:
            remaining = token_budget - used_tokens - separator_tokens
            if remaining < 120:
                break
            chunk_text   = trim_to_token_budget(chunk_text, remaining)
            chunk_tokens = count_tokens(chunk_text)

        context_parts.append(chunk_text)
        selected_results.append((doc, score))
        used_tokens += separator_tokens + chunk_tokens

    return "\n\n---\n\n".join(context_parts), selected_results


def keyword_overlap(text: str, query_terms):
    if not query_terms:
        return 0
    text_terms = set(normalize_for_search(text).split())
    return sum(1 for t in query_terms if t in text_terms)


def trim_to_token_budget(text: str, token_budget: int):
    if count_tokens(text) <= token_budget:
        return text
    enc     = tiktoken.get_encoding("cl100k_base")
    tokens  = enc.encode(str(text))
    trimmed = enc.decode(tokens[:token_budget]).strip()
    return f"{trimmed}\n[...]"


def build_retrieval_query(query_text: str, chat_history=None):
    if not chat_history:
        return query_text
    recent_bits = []
    for turn in chat_history[-3:]:
        pq = str(turn.get("query", "")).strip()
        pr = str((turn.get("result") or {}).get("response", "")).strip()
        if pq:
            recent_bits.append(f"Previous question: {pq}")
        if pr:
            recent_bits.append(f"Previous answer: {pr[:500]}")
    if not recent_bits:
        return query_text
    return "\n".join(recent_bits + [f"Follow-up question: {query_text}"])


def format_chat_history(chat_history=None):
    if not chat_history:
        return "No previous conversation."
    lines = []
    for turn in chat_history[-5:]:
        q = str(turn.get("query", "")).strip()
        r = str((turn.get("result") or {}).get("response", "")).strip()
        if q:
            lines.append(f"User: {q}")
        if r:
            lines.append(f"Assistant: {r}")
    history_text = "\n".join(lines).strip()
    if not history_text:
        return "No previous conversation."
    return history_text[-MAX_HISTORY_CHARS:]


def run_interactive(k, debug, no_llm, force_rag):
    print("Interactive RAG mode. Type 'exit' or 'quit' to stop.")
    while True:
        query_text = input("\nQuestion: ").strip()
        if query_text.lower() in {"exit", "quit"}:
            break
        if not query_text:
            continue
        query_rag(query_text, k=k, debug=debug, no_llm=no_llm, force_rag=force_rag)


# ── DB / model singletons ─────────────────────────────────────────────────────

def get_vector_db(collection_name: str = None):
    from langchain_chroma import Chroma
    from get_embedding_function import get_embedding_function
    if collection_name:
        return Chroma(
            persist_directory=CHROMA_PATH,
            embedding_function=get_embedding_function(),
            collection_name=collection_name,
        )
    global _DB
    if _DB is None:
        _DB = Chroma(
            persist_directory=CHROMA_PATH,
            embedding_function=get_embedding_function(),
        )
    return _DB


def get_prompt_template():
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        from langchain_core.prompts import ChatPromptTemplate
        _PROMPT_TEMPLATE = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    return _PROMPT_TEMPLATE


def get_gemini_model():
    global _GEMINI_MODEL
    if _GEMINI_MODEL is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _GEMINI_MODEL = ChatGoogleGenerativeAI(
            model="gemini-3.1-flash-lite",
            temperature=0.1,
            google_api_key=GOOGLE_API_KEY,
        )
    return _GEMINI_MODEL


def distance_to_confidence(distance: float) -> float:
    distance        = max(0.0, min(THRESHOLD, distance))
    confidence_range = 100.0 - MIN_CONFIDENCE
    return 100.0 - (distance / THRESHOLD) * confidence_range


# ── Direct markdown answer shortcuts ─────────────────────────────────────────

def find_direct_markdown_answer(query_text: str):
    quote_answer = find_quote_author_answer(query_text)
    if quote_answer:
        return quote_answer

    role = extract_role_from_query(query_text)
    if not role:
        return None

    role_pattern  = re.compile(rf"^{re.escape(role)}\s+of\s+.+?:\s*(?P<name>.+?)\.?$", re.IGNORECASE)
    table_pattern = re.compile(rf"^\|\s*(?P<name>[^|]+?)\s*\|\s*{re.escape(role)}\s*\|", re.IGNORECASE)
    is_pattern    = re.compile(rf"^(?P<name>[A-Z][a-zA-Z\s]+?)\s+is\s+{re.escape(role)}\b", re.IGNORECASE)
    colon_pattern = re.compile(rf"^{re.escape(role)}\s*[:\-—]\s*(?P<name>.+?)\.?$", re.IGNORECASE)

    for path in Path(DATA_PATH).glob("*.md"):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            for pattern in [role_pattern, colon_pattern, is_pattern, table_pattern]:
                m = pattern.match(line)
                if m:
                    return f"The {role} of Alian Software is {m.group('name').strip()}.", path.as_posix()
    return None


def find_quote_author_answer(query_text: str):
    if not re.search(r"\b(who|author|said|wrote|by)\b", query_text, re.IGNORECASE):
        return None
    query_terms = set(extract_search_terms(query_text))
    if len(query_terms) < 3:
        return None
    best_match = None
    for path in Path(DATA_PATH).glob("*.md"):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(lines):
            line_terms = set(normalize_for_search(line).split())
            overlap    = len(query_terms & line_terms)
            if overlap < 3:
                continue
            author = find_nearby_author(lines, index)
            if not author:
                continue
            if best_match is None or overlap > best_match[0]:
                best_match = (overlap, author, path.as_posix())
    if not best_match:
        return None
    _overlap, author, source = best_match
    return f"The quote is by {author}.", source


def find_nearby_author(lines, quote_index):
    for line in lines[quote_index + 1: quote_index + 5]:
        match = re.match(r"^\s*by\s+(.+?)\s*$", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_role_from_query(query_text: str):
    role_match = re.search(
        r"\b(CEO|CFO|CMO|CTO|COO|Chief Executive Officer|Chief Financial Officer|"
        r"Chief Marketing Officer|Chief Technology Officer)\b",
        query_text, re.IGNORECASE,
    )
    if not role_match:
        return None
    role = role_match.group(1).upper()
    return {
        "CHIEF EXECUTIVE OFFICER": "CEO", "CHIEF FINANCIAL OFFICER": "CFO",
        "CHIEF MARKETING OFFICER": "CMO", "CHIEF TECHNOLOGY OFFICER": "CTO",
    }.get(role, role)


if __name__ == "__main__":
    main()
