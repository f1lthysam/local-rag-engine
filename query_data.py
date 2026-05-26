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
THRESHOLD = 1.6  # L2 distance cutoff (~65% confidence)

PROMPT_TEMPLATE = """
Answer the question based ONLY on the following context. 
If the context does not contain the answer, say exactly: 
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

    results = db.similarity_search_with_score(query_text, k=2)
    print([score for _, score in results])

    if not results or results[0][1] > THRESHOLD:
        print("\nResponse: I don't have information about that in my documents.")
        print("Confidence: N/A")
        print("Sources:  []\n")
        return

    context_text = "\n\n---\n\n".join([doc.page_content for doc, _score in results])
    prompt_template = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    prompt = prompt_template.format(context=context_text, question=query_text)

    model = OllamaLLM(model="phi")
    response_text = model.invoke(prompt)

    best_score = results[0][1]
    confidence = max(0.0, min(100.0, (1 - best_score) * 100))

    sources = [doc.metadata.get("id", None) for doc, _score in results]

    print(f"\nResponse: {response_text}")
    print(f"Confidence: {confidence:.1f}%")
    print(f"Sources:  {sources}\n")
    return response_text


if __name__ == "__main__":
    main()