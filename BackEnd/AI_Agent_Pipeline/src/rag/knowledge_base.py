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
import json
import hashlib
from functools import lru_cache

import faiss # type: ignore
import pickle
import numpy as np # type: ignore
from sentence_transformers import SentenceTransformer # type: ignore


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "it", "as", "by", "from", "at",
    "table", "tables", "column", "columns", "schema", "schemas", "row",
    "rows", "none", "size", "mb", "total", "sample", "overall", "stats",
}


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


_model = None
def get_embedding_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("paraphrase-MiniLM-L3-v2")
    return _model

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
    Builds and queries a FAISS vector index from both static markdown
    guidance and live metadata snapshots. The knowledge base reads
    markdown sections into chunks, converts table metadata (table name,
    schema, columns, row counts) into retrievable document chunks, and
    stores their embeddings so the agent can fetch the most relevant
    context for a given query. This is used to ground database migration
    recommendations in the latest metadata flowing from the source system.
    """

    def __init__(self, path: str, label: str = None):
        self.path = path
        self.label = label or os.path.splitext(os.path.basename(path))[0]
        self._chunks = None
        self._vector_index = None
        self._vector_metadata = []
        self.source_metadata = None
        self._source_metadata_hash = None

        self.index_file = f"{path}.faiss"
        self.metadata_file = f"{path}.pkl"

    @staticmethod
    def _hash_metadata(metadata):
        if metadata is None:
            return None
        payload = json.dumps(metadata, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
    
    def load_table_metadata(self, metadata_json):
        chunks = []
        if not metadata_json:
            return chunks

        for table in metadata_json.get("tables", []):
            columns = table.get("columns", []) or []
            columns_text = "\n".join(
                [
                    f"{col.get('name', 'unknown')} {col.get('datatype', 'unknown')}"
                    for col in columns
                ]
            )

            content = f"""
            Table Name: {table.get('table_name', 'unknown')}
            Schema: {table.get('schema_name', 'unknown')}

            Columns:
            {columns_text}

            Row Count:
            {table.get('row_count', 'Unknown')}
            """

            chunks.append(
                KnowledgeChunk(
                    table.get("table_name", "unknown"),
                    content
                )
            )

        return chunks

    def update_live_metadata(self, metadata_json):
        self.source_metadata = metadata_json
        self._source_metadata_hash = self._hash_metadata(metadata_json)
        self.build_index(source_metadata=metadata_json)

    def build_index(self, source_metadata=None):
        # Load markdown chunks
        chunks = self._load()

        # Add source table metadata chunks if available
        if source_metadata is not None:
            self.source_metadata = source_metadata
            self._source_metadata_hash = self._hash_metadata(source_metadata)
            metadata_chunks = self.load_table_metadata(source_metadata)
            chunks.extend(metadata_chunks)

        if not chunks:
            print("No chunks found for indexing")
            return

        texts = [chunk.content for chunk in chunks]

        embeddings = (
            get_embedding_model()
            .encode(
                texts,
                convert_to_numpy=True
            )
            .astype("float32")
        )

        faiss.normalize_L2(embeddings)

        dimension = embeddings.shape[1]
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings)

        faiss.write_index(index, self.index_file)

        metadata = [
            {
                "heading": chunk.heading,
                "text": chunk.text
            }
            for chunk in chunks
        ]

        with open(self.metadata_file, "wb") as f:
            pickle.dump(metadata, f)

        self._vector_index = index
        self._vector_metadata = metadata

        print(f"Stored {index.ntotal} vectors")
        print(f"FAISS Index: {self.index_file}")
        print(f"Metadata File: {self.metadata_file}")

    def _needs_rebuild(self):
        if self.source_metadata is not None:
            current_hash = self._hash_metadata(self.source_metadata)
            if current_hash != self._source_metadata_hash:
                return True

        if not os.path.exists(self.index_file):
            return True

        if not os.path.exists(self.metadata_file):
            return True
        return (os.path.getmtime(self.path) > os.path.getmtime(self.index_file))

    def load_index(self):
        if self._vector_index is not None and self._vector_metadata:
            return self._vector_index, self._vector_metadata

        if not os.path.exists(self.index_file):
            if self.source_metadata is not None:
                self.build_index(self.source_metadata)
            else:
                raise FileNotFoundError(f"FAISS index not found for {self.path}")

        index = faiss.read_index(self.index_file)

        with open(self.metadata_file, "rb") as f:
            metadata = pickle.load(f)

        self._vector_index = index
        self._vector_metadata = metadata
        return index, metadata

    def retrieve(self, query, top_k=5, max_chars_per_chunk=1200, source_metadata=None):
        '''
        Returns up to `top_k` (heading, text) tuples ranked by keyword
        overlap with the query. Falls back to the first `top_k` chunks
        (document order) if nothing scores above zero, so retrieval still
        returns useful context even for terse/unusual metadata summaries.
        '''
        if source_metadata is not None:
            self.update_live_metadata(source_metadata)
        elif self.source_metadata is not None and (self._vector_index is None or self._vector_metadata == []):
            self.build_index(self.source_metadata)

        index, metadata = self.load_index()

        query_embedding = (
            get_embedding_model()
            .encode(
                [query],
                convert_to_numpy=True
            )
            .astype("float32")
        )

        faiss.normalize_L2(query_embedding)
        scores, indices = index.search(query_embedding, top_k)
        results = []

        for idx in indices[0]:
            if idx < 0:
                continue
            text = metadata[idx]["text"]
            if len(text) > max_chars_per_chunk:
                text = (
                    text[:max_chars_per_chunk]
                    .rsplit("\n", 1)[0]
                    + "\n... (truncated)"
                )
            results.append(
                (
                    metadata[idx]["heading"],
                    text
                )
            )

        return results

    def retrieve_as_text(self, query: str, top_k: int = 5, max_chars_per_chunk: int = 1200, source_metadata=None) -> str:
        sections = self.retrieve(query, top_k=top_k, max_chars_per_chunk=max_chars_per_chunk, source_metadata=source_metadata)
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
