"""
Lightweight, dependency-free RAG knowledge base for the AI Agent Pipeline.

This intentionally does NOT pull in a vector database or embeddings model -
the pipeline doesn't use one anywhere else, and the instructions are to
avoid introducing new technologies. Instead it chunks a markdown knowledge
file by its top-level headings and retrieves the most relevant chunks for
a given query using simple keyword-overlap scoring. This is enough to keep
the agent's prompt focused on the handful of sections that actually matter
for the metadata being assessed, instead of dumping the entire knowledge
file into every request.

Each markdown file under `rag/knowledge/` is treated as one knowledge base
(e.g. one per source database platform, plus the common Fabric one).
"""
import os
import re
from functools import lru_cache

import faiss # type: ignore
import pickle
import numpy as np # type: ignore
from sentence_transformers import SentenceTransformer # type: ignore

EMBEDDING_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "it", "as", "by", "from", "at",
    "table", "tables", "column", "columns", "schema", "schemas", "row",
    "rows", "none", "size", "mb", "total", "sample", "overall", "stats",
}


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


class KnowledgeChunk:
    __slots__ = ("heading", "text", "tokens")

    def __init__(self, heading: str, text: str):
        self.heading = heading
        self.text = text
        self.tokens = _tokenize(f"{heading} {text}")

    @property
    def content(self):
        return f"{self.heading}\n{self.text}"

class MarkdownKnowledgeBase:
    """
    Loads a markdown file and splits it into chunks on top-level ('# ')
    headings. Provides simple keyword-overlap retrieval so an agent's
    prompt can be grounded in the most relevant sections only.
    """

    def __init__(self, path: str, label: str = None):
        self.path = path
        self.label = label or os.path.splitext(os.path.basename(path))[0]
        self._chunks = None

        self.index_file = f"{path}.faiss"
        self.metadata_file = f"{path}.pkl"

    def _load(self):
        if self._chunks is not None:
            return self._chunks
        if not os.path.exists(self.path):
            self._chunks = []
            return self._chunks

        with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Chunk by markdown heading. Different knowledge files use different
        # heading conventions (numbered "# " sections vs. a single "# " title
        # with "## " subsections), so pick whichever level actually produces
        # multiple chunks rather than assuming one file's style.
        parts = re.split(r"(?m)^# ", content)
        if len([p for p in parts if p.strip()]) <= 1:
            parts = re.split(r"(?m)^## ", content)

        chunks = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.split("\n", 1)
            heading = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            chunks.append(KnowledgeChunk(heading, body))
        self._chunks = chunks
        return self._chunks

    def build_index(self):
        chunks = self._load()
        if not chunks:
            return
        texts = [chunk.content for chunk in chunks]
        embeddings = EMBEDDING_MODEL.encode(texts,convert_to_numpy=True).astype("float32")
        faiss.normalize_L2(embeddings)
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings)
    
        faiss.write_index(
            index,
            self.index_file
            )
    
        metadata = [
        {
            "heading": chunk.heading,
            "text": chunk.text
        }
        for chunk in chunks
        ]
    
        with open(self.metadata_file, "wb") as f:
            pickle.dump(metadata, f)
    
        print(f"FAISS index created successfully.")
        print(f"Stored vectors: {index.ntotal}")
        print(f"Index File: {self.index_file}")

    def _needs_rebuild(self):
        if not os.path.exists(self.index_file):
            return True

        if not os.path.exists(self.metadata_file):
            return True
        return (os.path.getmtime(self.path) > os.path.getmtime(self.index_file))

    def load_index(self):
        if self._needs_rebuild():
            print("Building FAISS index...")

            self.build_index()
        index = faiss.read_index(self.index_file)

        with open(self.metadata_file,"rb") as f:
            metadata = pickle.load(f)

        return index, metadata

    def retrieve(self, query: str,top_k: int = 5,max_chars_per_chunk: int = 1200):
        """
        Returns up to `top_k` (heading, text) tuples ranked by keyword
        overlap with the query. Falls back to the first `top_k` chunks
        (document order) if nothing scores above zero, so retrieval still
        returns useful context even for terse/unusual metadata summaries.
        """
        index, metadata = self.load_index()
        query_embedding = EMBEDDING_MODEL.encode(
            [query],
            convert_to_numpy=True
        )

        query_embedding = query_embedding.astype(np.float32)

        faiss.normalize_L2(query_embedding)
        scores, indices = index.search(
            query_embedding,
            top_k
        )

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue

            chunk = metadata[idx]
            text = chunk["text"]

            if len(text) > max_chars_per_chunk:
                text = (text[:max_chars_per_chunk].rsplit("\n", 1)[0]+ "\n... (truncated)")

            results.append((chunk.heading, text))
        return results

    def retrieve_as_text(self, query: str, top_k: int = 5, max_chars_per_chunk: int = 1200) -> str:
        sections = self.retrieve(query, top_k=top_k, max_chars_per_chunk=max_chars_per_chunk)
        if not sections:
            return ""
        formatted = [f"[{self.label}] {heading}\n{text}" for heading, text in sections]
        return "\n\n".join(formatted)


@lru_cache(maxsize=None)
def _cached_kb(path: str) -> MarkdownKnowledgeBase:
    return MarkdownKnowledgeBase(path)


def get_knowledge_base(path: str) -> MarkdownKnowledgeBase:
    """Returns a cached MarkdownKnowledgeBase instance for the given path."""
    return _cached_kb(path)
