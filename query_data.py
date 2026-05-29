import warnings
warnings.filterwarnings("ignore")

import os


import logging
logging.disable(logging.CRITICAL)

import argparse
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
_DB = None
_PROMPT_TEMPLATE = None
_GEMINI_MODEL = None

PROMPT_TEMPLATE = """
Answer the question using ONLY the following context.
Use the conversation history only to understand follow-up references, such as "that", "it", "the previous one", or similar wording.
Do not use the conversation history as a source of facts unless the same facts are supported by the retrieved context.
If the context contains relevant information, answer directly and concisely.
If the context does not contain enough information to answer, say exactly:
"I don't have information about that in my documents."

Conversation history:
{history}

---

Context: {context}

---

Question: {question}
"""


def count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(str(text)))


def extract_response_text(raw) -> str:
    """Safely extract plain string from any Gemini response format."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query_text", nargs="?", type=str, help="The query text.")
    parser.add_argument("--k", type=int, default=None, help="Override the dynamic number of chunks to retrieve.")
    parser.add_argument("--debug", action="store_true", help="Print retrieval and model timing.")
    parser.add_argument("--no-llm", action="store_true", help="Only retrieve matching chunks.")
    parser.add_argument("--force-rag", action="store_true", help="Skip direct Markdown fact lookup.")
    parser.add_argument("--interactive", action="store_true", help="Answer multiple questions in one process.")
    args = parser.parse_args()

    if args.interactive:
        run_interactive(args.k, args.debug, args.no_llm, args.force_rag)
        return

    if not args.query_text:
        parser.error("query_text is required unless --interactive is used.")

    query_rag(args.query_text, k=args.k, debug=args.debug, no_llm=args.no_llm, force_rag=args.force_rag)


def query_rag(
    query_text: str,
    k=None,
    debug: bool = False,
    no_llm: bool = False,
    force_rag: bool = False,
):
    start_time = time.perf_counter()
    retrieval_plan = plan_retrieval(query_text, k_override=k)

    if not force_rag:
        direct_answer = find_direct_markdown_answer(query_text)
        if direct_answer:
            answer, source = direct_answer
            latency = time.perf_counter() - start_time
            prompt_tokens = count_tokens(query_text)
            response_tokens = count_tokens(answer)
            print(f"\nResponse: {answer}")
            print("Confidence: 100.0%")
            print(f"Sources:  ['{source}']")
            print(f"Latency:  {latency:.2f}s")
            print(f"Tokens:   prompt={prompt_tokens} · response={response_tokens} · total={prompt_tokens + response_tokens}\n")
            return answer

    db = get_vector_db()
    results = db.similarity_search_with_score(query_text, k=retrieval_plan["k"])
    retrieval_done_time = time.perf_counter()

    if debug:
        print(f"Retrieval: {retrieval_done_time - start_time:.2f}s")
        print(f"Scores: {[score for _, score in results]}")

    if not results or results[0][1] > THRESHOLD:
        lexical_answer = answer_from_lexical_fallback(query_text, "No previous conversation.", start_time)
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
        lexical_answer = answer_from_lexical_fallback(query_text, "No previous conversation.", start_time)
        if lexical_answer:
            print(f"\nResponse: {lexical_answer['response']}")
            print(f"Confidence: {lexical_answer['confidence']}%")
            print(f"Sources:  {lexical_answer['sources']}")
            print(f"Latency:  {lexical_answer['latency']:.2f}s")
            print(f"Tokens:   prompt={lexical_answer['prompt_tokens']} · response={lexical_answer['response_tokens']} · total={lexical_answer['total_tokens']}\n")
            return lexical_answer["response"]

    if no_llm:
        sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]
        print("\nRetrieved Context:\n")
        print(context_text)
        print(f"\nSources:  {sources}\n")
        return context_text

    prompt_template = get_prompt_template()
    prompt = prompt_template.format(
        context=context_text,
        history="No previous conversation.",
        question=query_text,
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)
    llm_done_time = time.perf_counter()

    if is_no_info_response(response_text):
        lexical_answer = answer_from_lexical_fallback(query_text, "No previous conversation.", start_time)
        if lexical_answer and not lexical_answer["no_info"]:
            print(f"\nResponse: {lexical_answer['response']}")
            print(f"Confidence: {lexical_answer['confidence']}%")
            print(f"Sources:  {lexical_answer['sources']}")
            print(f"Latency:  {lexical_answer['latency']:.2f}s")
            print(f"Tokens:   prompt={lexical_answer['prompt_tokens']} · response={lexical_answer['response_tokens']} · total={lexical_answer['total_tokens']}\n")
            return lexical_answer["response"]

    latency = llm_done_time - start_time
    response_tokens = count_tokens(response_text)
    total_tokens = prompt_tokens + response_tokens

    if debug:
        print(f"LLM generation: {llm_done_time - retrieval_done_time:.2f}s")
        print(f"Total: {latency:.2f}s")

    best_score = results[0][1]
    confidence = distance_to_confidence(best_score)
    sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]

    print(f"\nResponse: {response_text}")
    print(f"Confidence: {confidence:.1f}%")
    print(f"Sources:  {sources}")
    print(f"Latency:  {latency:.2f}s")
    print(f"Tokens:   prompt={prompt_tokens} · response={response_tokens} · total={total_tokens}\n")
    return response_text


def query_rag_web(query_text: str, chat_history=None):
    start_time = time.perf_counter()
    history_text = format_chat_history(chat_history)
    retrieval_query = build_retrieval_query(query_text, chat_history)
    retrieval_plan = plan_retrieval(query_text, chat_history=chat_history)

    direct_answer = find_direct_markdown_answer(query_text)
    if direct_answer:
        answer, source = direct_answer
        latency = time.perf_counter() - start_time
        prompt_tokens = count_tokens(query_text)
        response_tokens = count_tokens(answer)
        return {
            "response": answer,
            "confidence": 100.0,
            "sources": [source],
            "no_info": False,
            "latency": round(latency, 2),
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "total_tokens": prompt_tokens + response_tokens,
            "retrieval_mode": "direct",
            "retrieved_chunks": 0,
            "context_tokens": 0,
        }

    db = get_vector_db()
    results = db.similarity_search_with_score(retrieval_query, k=retrieval_plan["k"])

    if not results or results[0][1] > THRESHOLD:
        lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
        if lexical_answer:
            return lexical_answer

        latency = time.perf_counter() - start_time
        return {
            "response": "I don't have information about that in my documents.",
            "confidence": None,
            "sources": [],
            "no_info": True,
            "latency": round(latency, 2),
            "prompt_tokens": count_tokens(retrieval_query),
            "response_tokens": None,
            "total_tokens": count_tokens(retrieval_query),
            "retrieval_mode": retrieval_plan["mode"],
            "retrieved_chunks": 0,
            "context_tokens": 0,
        }

    context_text, relevant_results = build_dynamic_context(results, retrieval_plan, query_text)
    if not context_text:
        lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
        if lexical_answer:
            return lexical_answer

    prompt = get_prompt_template().format(
        context=context_text,
        history=history_text,
        question=query_text,
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)

    if is_no_info_response(response_text):
        lexical_answer = answer_from_lexical_fallback(query_text, history_text, start_time)
        if lexical_answer and not lexical_answer["no_info"]:
            return lexical_answer

    latency = time.perf_counter() - start_time
    response_tokens = count_tokens(response_text)
    total_tokens = prompt_tokens + response_tokens
    confidence = round(distance_to_confidence(results[0][1]), 1)
    sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]

    return {
        "response": response_text,
        "confidence": confidence,
        "sources": sources,
        "no_info": False,
        "latency": round(latency, 2),
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": total_tokens,
        "retrieval_mode": retrieval_plan["mode"],
        "retrieved_chunks": len(relevant_results),
        "context_tokens": count_tokens(context_text),
    }


def answer_from_lexical_fallback(query_text: str, history_text: str, start_time):
    context_text, sources = build_lexical_context(query_text)
    if not context_text:
        return None

    prompt = get_prompt_template().format(
        context=context_text,
        history=history_text,
        question=query_text,
    )
    prompt_tokens = count_tokens(prompt)

    from langchain_core.messages import HumanMessage
    raw = get_gemini_model().invoke([HumanMessage(content=prompt)]).content
    response_text = extract_response_text(raw)

    latency = time.perf_counter() - start_time
    response_tokens = count_tokens(response_text)
    total_tokens = prompt_tokens + response_tokens
    no_info = is_no_info_response(response_text)

    return {
        "response": response_text,
        "confidence": None if no_info else LEXICAL_FALLBACK_CONFIDENCE,
        "sources": [] if no_info else sources,
        "no_info": no_info,
        "latency": round(latency, 2),
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": total_tokens,
        "retrieval_mode": "keyword-fallback",
        "retrieved_chunks": 0 if no_info else len(sources),
        "context_tokens": count_tokens(context_text),
    }


def build_lexical_context(query_text: str):
    query_terms = extract_search_terms(query_text)
    if not query_terms:
        return "", []

    matches = []
    for path in Path(DATA_PATH).glob("*.md"):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(lines):
            normalized = normalize_for_search(line)
            line_terms = set(normalized.split())
            score = sum(1 for term in query_terms if term in line_terms)
            if score == 0:
                continue

            window_start = max(0, index - 2)
            window_end = min(len(lines), index + 8)
            snippet = "\n".join(lines[window_start:window_end]).strip()
            matches.append((score, path.as_posix(), index + 1, snippet))

    if not matches:
        return "", []

    matches.sort(key=lambda item: item[0], reverse=True)
    context_parts = []
    sources = []
    used = set()

    for _score, source, line_number, snippet in matches[:8]:
        source_id = f"{source}:{line_number}"
        if snippet in used:
            continue
        used.add(snippet)
        context_parts.append(f"Source: {source_id}\n{snippet}")
        sources.append(source_id)

    context = "\n\n---\n\n".join(context_parts)
    return trim_to_token_budget(context, 1800), sources


def extract_search_terms(query_text: str):
    stopwords = {
        "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is",
        "it", "of", "on", "or", "the", "their", "there", "to", "what", "who",
        "whom", "whose", "with", "tell", "me", "about", "give", "show", "te",
    }
    normalized = normalize_for_search(query_text)
    terms = [term for term in normalized.split() if len(term) > 1 and term not in stopwords]
    role = extract_role_from_query(query_text)
    if role:
        terms.append(role.lower())
    return sorted(set(terms), key=len, reverse=True)


def normalize_for_search(text: str):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def is_no_info_response(response_text: str):
    normalized = normalize_for_search(response_text)
    return "i don t have information about that in my documents" in normalized


def plan_retrieval(query_text: str, chat_history=None, k_override=None):
    query = query_text.lower()
    words = re.findall(r"\w+", query)
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

    broad_score = sum(1 for marker in broad_markers if marker in query)
    fact_score = sum(1 for marker in fact_markers if marker in query)

    if broad_score >= 2 or word_count > 18:
        mode = "broad"
        k = 12
        context_tokens = 3600
        max_chunk_tokens = 850
    elif broad_score == 1 or has_history:
        mode = "balanced"
        k = 9
        context_tokens = 2600
        max_chunk_tokens = 650
    elif fact_score >= 1 or word_count <= 8:
        mode = "focused"
        k = 6
        context_tokens = 1400
        max_chunk_tokens = 600  # increased from 380 so CEO/team lines aren't cut off
    else:
        mode = "balanced"
        k = DEFAULT_K
        context_tokens = 1600
        max_chunk_tokens = 550

    if k_override is not None:
        k = k_override

    return {
        "mode": mode,
        "k": max(1, min(MAX_RETRIEVAL_K, k)),
        "context_tokens": max(MIN_CONTEXT_TOKENS, min(MAX_CONTEXT_TOKENS, context_tokens)),
        "max_chunk_tokens": max(250, min(1000, max_chunk_tokens)),
    }


def build_dynamic_context(results, retrieval_plan, query_text=""):
    if not results:
        return "", []

    query_terms = set(extract_search_terms(query_text))
    candidate_results = [
        (doc, score)
        for doc, score in results
        if score <= THRESHOLD
    ]
    candidate_results.sort(
        key=lambda item: (
            -keyword_overlap(item[0].page_content, query_terms),
            item[1],
        )
    )

    context_parts = []
    selected_results = []
    used_tokens = 0
    token_budget = retrieval_plan["context_tokens"]
    per_chunk_budget = retrieval_plan["max_chunk_tokens"]

    for doc, score in candidate_results:
        chunk_text = trim_to_token_budget(doc.page_content, per_chunk_budget)
        chunk_tokens = count_tokens(chunk_text)
        separator_tokens = count_tokens("\n\n---\n\n") if context_parts else 0

        if used_tokens + separator_tokens + chunk_tokens > token_budget:
            remaining_tokens = token_budget - used_tokens - separator_tokens
            if remaining_tokens < 120:
                break
            chunk_text = trim_to_token_budget(chunk_text, remaining_tokens)
            chunk_tokens = count_tokens(chunk_text)

        context_parts.append(chunk_text)
        selected_results.append((doc, score))
        used_tokens += separator_tokens + chunk_tokens

    return "\n\n---\n\n".join(context_parts), selected_results


def keyword_overlap(text: str, query_terms):
    if not query_terms:
        return 0
    text_terms = set(normalize_for_search(text).split())
    return sum(1 for term in query_terms if term in text_terms)


def trim_to_token_budget(text: str, token_budget: int):
    if count_tokens(text) <= token_budget:
        return text

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(str(text))
    trimmed = enc.decode(tokens[:token_budget]).strip()
    return f"{trimmed}\n[...]"


def build_retrieval_query(query_text: str, chat_history=None):
    if not chat_history:
        return query_text

    recent_bits = []
    for turn in chat_history[-3:]:
        previous_query = str(turn.get("query", "")).strip()
        previous_response = str(turn.get("result", {}).get("response", "")).strip()
        if previous_query:
            recent_bits.append(f"Previous question: {previous_query}")
        if previous_response:
            recent_bits.append(f"Previous answer: {previous_response[:500]}")

    if not recent_bits:
        return query_text
    return "\n".join(recent_bits + [f"Follow-up question: {query_text}"])


def format_chat_history(chat_history=None):
    if not chat_history:
        return "No previous conversation."

    lines = []
    for turn in chat_history[-5:]:
        query = str(turn.get("query", "")).strip()
        result = turn.get("result", {}) or {}
        response = str(result.get("response", "")).strip()
        if query:
            lines.append(f"User: {query}")
        if response:
            lines.append(f"Assistant: {response}")

    history_text = "\n".join(lines).strip()
    if not history_text:
        return "No previous conversation."
    return history_text[-MAX_HISTORY_CHARS:]


def run_interactive(k: int, debug: bool, no_llm: bool, force_rag: bool):
    print("Interactive RAG mode. Type 'exit' or 'quit' to stop.")
    while True:
        query_text = input("\nQuestion: ").strip()
        if query_text.lower() in {"exit", "quit"}:
            break
        if not query_text:
            continue
        query_rag(query_text, k=k, debug=debug, no_llm=no_llm, force_rag=force_rag)


def get_vector_db():
    global _DB
    if _DB is None:
        from langchain_chroma import Chroma
        from get_embedding_function import get_embedding_function
        _DB = Chroma(persist_directory=CHROMA_PATH, embedding_function=get_embedding_function())
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
    distance = max(0.0, min(THRESHOLD, distance))
    confidence_range = 100.0 - MIN_CONFIDENCE
    return 100.0 - (distance / THRESHOLD) * confidence_range


def find_direct_markdown_answer(query_text: str):
    quote_answer = find_quote_author_answer(query_text)
    if quote_answer:
        return quote_answer

    role = extract_role_from_query(query_text)
    if not role:
        return None

    # Pattern 1: "CEO of Company: Name"
    role_pattern = re.compile(
        rf"^{re.escape(role)}\s+of\s+.+?:\s*(?P<name>.+?)\.?$", re.IGNORECASE,
    )
    # Pattern 2: "| Name | CEO |" (table)
    table_pattern = re.compile(
        rf"^\|\s*(?P<name>[^|]+?)\s*\|\s*{re.escape(role)}\s*\|", re.IGNORECASE,
    )
    # Pattern 3: "Name is CEO" (natural language — the format aliansoftware uses)
    is_pattern = re.compile(
        rf"^(?P<name>[A-Z][a-zA-Z\s]+?)\s+is\s+{re.escape(role)}\b", re.IGNORECASE,
    )
    # Pattern 4: "CEO: Name" or "CEO — Name"
    colon_pattern = re.compile(
        rf"^{re.escape(role)}\s*[:\-—]\s*(?P<name>.+?)\.?$", re.IGNORECASE,
    )

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
            overlap = len(query_terms & line_terms)
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
        r"\b(CEO|CFO|CMO|CTO|COO|Chief Executive Officer|Chief Financial Officer|Chief Marketing Officer|Chief Technology Officer)\b",
        query_text, re.IGNORECASE,
    )
    if not role_match:
        return None
    role = role_match.group(1).upper()
    return {"CHIEF EXECUTIVE OFFICER": "CEO", "CHIEF FINANCIAL OFFICER": "CFO",
            "CHIEF MARKETING OFFICER": "CMO", "CHIEF TECHNOLOGY OFFICER": "CTO"}.get(role, role)


if __name__ == "__main__":
    main()