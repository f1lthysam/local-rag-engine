import warnings
warnings.filterwarnings("ignore")

import os
os.environ["PYTHONWARNINGS"] = "ignore"

import logging
logging.disable(logging.CRITICAL)

import argparse
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM

from get_embedding_function import get_embedding_function

CHROMA_PATH = "chroma"
THRESHOLD = 1.6  # L2 distance cutoff for relevant chunks.
MIN_CONFIDENCE = 65.0  # Confidence shown for answers right at the cutoff.

PROMPT_TEMPLATE = """
Answer the question using ONLY the following context.
If the context contains relevant information, answer directly and concisely.
If the context does not contain enough information to answer, say exactly:
"I don't have information about that in my documents."

Context: {context}

---

Question: {question}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query_text", type=str, help="The query text.")
    args = parser.parse_args()
    query_rag(args.query_text)


def query_rag(query_text: str):
    embedding_function = get_embedding_function()
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embedding_function)

    results = db.similarity_search_with_score(query_text, k=5)
    print([score for _, score in results])

    if not results or results[0][1] > THRESHOLD:
        print("\nResponse: I don't have information about that in my documents.")
        print("Confidence: N/A")
        print("Sources:  []\n")
        return

    top_source = results[0][0].metadata.get("source")
    relevant_results = [
        (doc, score)
        for doc, score in results
        if score <= THRESHOLD and doc.metadata.get("source") == top_source
    ]
    context_text = "\n\n---\n\n".join(
        [doc.page_content for doc, _score in relevant_results]
    )
    prompt_template = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    prompt = prompt_template.format(context=context_text, question=query_text)

    model = OllamaLLM(model="phi", temperature=0.5)
    response_text = model.invoke(prompt)

    best_score = results[0][1]
    confidence = distance_to_confidence(best_score)

    sources = [doc.metadata.get("id", None) for doc, _score in relevant_results]

    print(f"\nResponse: {response_text}")
    print(f"Confidence: {confidence:.1f}%")
    print(f"Sources:  {sources}\n")
    return response_text


def distance_to_confidence(distance: float) -> float:
    """Convert Chroma's L2 distance into a user-facing confidence percentage."""
    distance = max(0.0, min(THRESHOLD, distance))
    confidence_range = 100.0 - MIN_CONFIDENCE
    return 100.0 - (distance / THRESHOLD) * confidence_range


if __name__ == "__main__":
    main()
