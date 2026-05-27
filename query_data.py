import warnings
warnings.filterwarnings("ignore")

import os
os.environ["PYTHONWARNINGS"] = "ignore"

import logging
logging.disable(logging.CRITICAL)

import argparse
from pathlib import Path
import re
import time

CHROMA_PATH = "chroma"
DATA_PATH = "data"
THRESHOLD = 1.6
MIN_CONFIDENCE = 65.0
DEFAULT_K = 3
MAX_CONTEXT_CHARS = 2500
_DB = None
_PROMPT_TEMPLATE = None
_OLLAMA_MODEL = None

PROMPT_TEMPLATE = """
Answer the question using ONLY the following context.
If the context contains relevant information, answer directly and concisely.
If the context does not contain enough information to answer, say exactly:
"I don't have information about that in my documents."

Context: {context}

---

Question: {question}
"""


import tiktoken

def count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query_text", nargs="?", type=str, help="The query text.")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="Number of chunks to retrieve.")
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
    k: int = DEFAULT_K,
    debug: bool = False,
    no_llm: bool = False,
    force_rag: bool = False,
):
    start_time = time.perf_counter()

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
    results = db.similarity_search_with_score(query_text, k=k)
    retrieval_done_time = time.perf_counter()

    if debug:
        print(f"Retrieval: {retrieval_done_time - start_time:.2f}s")
        print(f"Scores: {[score for _, score in results]}")

    if not results or results[0][1] > THRESHOLD:
        print("\nResponse: I don't have information about that in my documents.")
        print("Confidence: N/A")
        print("Sources:  []")
        print(f"Latency:  {time.perf_counter() - start_time:.2f}s")
        print("Tokens:   N/A\n")
        return

    top_source = results[0][0].metadata.get("source")
    relevant_results = [
        (doc, score)
        for doc, score in results
        if score <= THRESHOLD and doc.metadata.get("source") == top_source
    ]
    context_text = "\n\n---\n\n".join([doc.page_content for doc, _score in relevant_results])
    context_text = context_text[:MAX_CONTEXT_CHARS]

    if no_llm:
        sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]
        print("\nRetrieved Context:\n")
        print(context_text)
        print(f"\nSources:  {sources}\n")
        return context_text

    prompt_template = get_prompt_template()
    prompt = prompt_template.format(context=context_text, question=query_text)
    prompt_tokens = count_tokens(prompt)

    model = get_ollama_model()
    response_text = model.invoke(prompt)
    llm_done_time = time.perf_counter()

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


def query_rag_web(query_text: str):
    start_time = time.perf_counter()

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
        }

    db = get_vector_db()
    results = db.similarity_search_with_score(query_text, k=DEFAULT_K)

    if not results or results[0][1] > THRESHOLD:
        latency = time.perf_counter() - start_time
        return {
            "response": "I don't have information about that in my documents.",
            "confidence": None,
            "sources": [],
            "no_info": True,
            "latency": round(latency, 2),
            "prompt_tokens": None,
            "response_tokens": None,
            "total_tokens": None,
        }

    top_source = results[0][0].metadata.get("source")
    relevant_results = [
        (doc, score)
        for doc, score in results
        if score <= THRESHOLD and doc.metadata.get("source") == top_source
    ]
    context_text = "\n\n---\n\n".join([doc.page_content for doc, _score in relevant_results])
    context_text = context_text[:MAX_CONTEXT_CHARS]

    prompt = get_prompt_template().format(context=context_text, question=query_text)
    prompt_tokens = count_tokens(prompt)

    response_text = get_ollama_model().invoke(prompt)
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
    }


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


def get_ollama_model():
    global _OLLAMA_MODEL
    if _OLLAMA_MODEL is None:
        from langchain_ollama import OllamaLLM
        _OLLAMA_MODEL = OllamaLLM(model="llama3.2", temperature=0.1)
    return _OLLAMA_MODEL


def distance_to_confidence(distance: float) -> float:
    distance = max(0.0, min(THRESHOLD, distance))
    confidence_range = 100.0 - MIN_CONFIDENCE
    return 100.0 - (distance / THRESHOLD) * confidence_range


def find_direct_markdown_answer(query_text: str):
    role = extract_role_from_query(query_text)
    if not role:
        return None

    role_pattern = re.compile(
        rf"^{re.escape(role)}\s+of\s+.+?:\s*(?P<name>.+?)\.?$", re.IGNORECASE,
    )
    table_pattern = re.compile(
        rf"^\|\s*(?P<name>[^|]+?)\s*\|\s*{re.escape(role)}\s*\|", re.IGNORECASE,
    )

    for path in Path(DATA_PATH).glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            role_match = role_pattern.match(line)
            if role_match:
                return f"The {role} is {role_match.group('name').strip()}.", path.as_posix()
            table_match = table_pattern.match(line)
            if table_match:
                return f"The {role} is {table_match.group('name').strip()}.", path.as_posix()

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
