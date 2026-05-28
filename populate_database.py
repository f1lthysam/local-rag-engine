import warnings
warnings.filterwarnings("ignore")

import os
os.environ["PYTHONWARNINGS"] = "ignore"

import logging
logging.disable(logging.CRITICAL)

import argparse
import hashlib
import shutil
from langchain_community.document_loaders import (
    DirectoryLoader,
    PyPDFDirectoryLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_chroma import Chroma

from get_embedding_function import get_embedding_function

CHROMA_PATH = "chroma"
DATA_PATH = "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Reset the database.")
    args = parser.parse_args()
    if args.reset:
        print("Clearing database")
        clear_database()

    documents = load_documents()
    chunks = split_documents(documents)
    add_to_chroma(chunks)


def load_documents():
    pdf_loader = PyPDFDirectoryLoader(DATA_PATH)
    markdown_loader = DirectoryLoader(
        DATA_PATH,
        glob="*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    return pdf_loader.load() + markdown_loader.load()


def split_documents(documents: list[Document]):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500,
        chunk_overlap=200,
        length_function=len,
        is_separator_regex=False,
    )
    return text_splitter.split_documents(documents)


def add_to_chroma(chunks: list[Document]):
    db = Chroma(
        persist_directory=CHROMA_PATH, embedding_function=get_embedding_function()
    )

    chunks_with_ids = calculate_chunk_ids(chunks)

    existing_items = db.get(include=["metadatas"])
    existing_by_id = {
        item_id: metadata or {}
        for item_id, metadata in zip(existing_items["ids"], existing_items["metadatas"])
    }
    existing_ids = set(existing_by_id)
    print(f"Number of existing documents in DB: {len(existing_ids)}")

    new_chunks = [c for c in chunks_with_ids if c.metadata["id"] not in existing_ids]
    changed_chunks = [
        c for c in chunks_with_ids
        if c.metadata["id"] in existing_ids
        and existing_by_id[c.metadata["id"]].get("content_hash") != c.metadata["content_hash"]
    ]

    if new_chunks:
        print(f"Adding new documents: {len(new_chunks)}")
        db.add_documents(new_chunks, ids=[c.metadata["id"] for c in new_chunks])
    if changed_chunks:
        print(f"Updating changed documents: {len(changed_chunks)}")
        db.update_documents(
            ids=[c.metadata["id"] for c in changed_chunks],
            documents=changed_chunks,
        )
    if not new_chunks and not changed_chunks:
        print("No new documents to add")


def calculate_chunk_ids(chunks):
    last_page_id = None
    current_chunk_index = 0

    for chunk in chunks:
        source = chunk.metadata.get("source")
        page = chunk.metadata.get("page")
        current_page_id = f"{source}:{page}"

        if current_page_id == last_page_id:
            current_chunk_index += 1
        else:
            current_chunk_index = 0

        chunk.metadata["id"] = f"{current_page_id}:{current_chunk_index}"
        chunk.metadata["content_hash"] = hashlib.sha1(
            chunk.page_content.encode("utf-8", errors="ignore")
        ).hexdigest()
        last_page_id = current_page_id

    return chunks


def clear_database():
    if os.path.exists(CHROMA_PATH):
        shutil.rmtree(CHROMA_PATH)


if __name__ == "__main__":
    main()
