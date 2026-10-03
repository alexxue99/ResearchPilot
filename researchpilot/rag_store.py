from __future__ import annotations

import sqlite3
import struct
import math
from contextlib import closing
from pathlib import Path

from .rag import Chunk, TOKEN
from .embeddings import EmbeddingProvider, cosine_similarity


class PersistentChunkStore:
    """SQLite FTS5 retrieval with durable chunk-level provenance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT NOT NULL, section TEXT NOT NULL, text TEXT NOT NULL, page INTEGER, url TEXT)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(id UNINDEXED, title, section, text, tokenize='porter unicode61')")
            db.execute("CREATE TABLE IF NOT EXISTS chunk_vectors (chunk_id TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(chunk_id, model), FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE)")
            db.commit()

    def add(self, chunks: list[Chunk], embedder: EmbeddingProvider | None = None) -> int:
        vectors = embedder.embed([f"{chunk.title}\n{chunk.section}\n{chunk.text}" for chunk in chunks]) if embedder and chunks else []
        if embedder and (len(vectors) != len(chunks) or any(
                len(v) != embedder.dimensions or not all(math.isfinite(x) for x in v) for v in vectors)):
            raise ValueError("embedding provider returned invalid vectors")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for index, chunk in enumerate(chunks):
                old = db.execute("SELECT title,section,text FROM chunks WHERE id=?", (chunk.id,)).fetchone()
                if old and old != (chunk.title, chunk.section, chunk.text):
                    db.execute("DELETE FROM chunk_vectors WHERE chunk_id=?", (chunk.id,))
                db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,title=excluded.title,section=excluded.section,text=excluded.text,page=excluded.page,url=excluded.url",
                           (chunk.id, chunk.source_id, chunk.title, chunk.section, chunk.text, chunk.page, chunk.url))
                db.execute("DELETE FROM chunk_fts WHERE id=?", (chunk.id,))
                db.execute("INSERT INTO chunk_fts VALUES (?, ?, ?, ?)", (chunk.id, chunk.title, chunk.section, chunk.text))
                if embedder:
                    vector = vectors[index]
                    if len(vector) != embedder.dimensions: raise ValueError("embedding provider returned an invalid dimension")
                    blob = struct.pack(f"<{len(vector)}f", *vector)
                    db.execute("INSERT INTO chunk_vectors VALUES (?, ?, ?, ?) ON CONFLICT(chunk_id,model) DO UPDATE SET dimensions=excluded.dimensions,vector=excluded.vector",
                               (chunk.id, embedder.model, embedder.dimensions, blob))
            db.commit()
        return len(chunks)

    def delete_source(self, source_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            ids = [row[0] for row in db.execute("SELECT id FROM chunks WHERE source_id=?", (source_id,))]
            for chunk_id in ids:
                db.execute("DELETE FROM chunk_fts WHERE id=?", (chunk_id,))
            db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            db.commit()

    def search(self, query: str, limit: int = 5, source_id: str | None = None) -> list[tuple[Chunk, float]]:
        terms = list(dict.fromkeys(token.lower() for token in TOKEN.findall(query)))
        if not terms: return []
        expression = " OR ".join(f'"{term}"' for term in terms[:24])
        sql = "SELECT c.id,c.source_id,c.title,c.section,c.text,c.page,c.url,bm25(chunk_fts) FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.id WHERE chunk_fts MATCH ?"
        params: list[object] = [expression]
        if source_id:
            sql += " AND c.source_id=?"; params.append(source_id)
        sql += " ORDER BY bm25(chunk_fts) LIMIT ?"; params.append(limit)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(sql, params).fetchall()
        return [(Chunk(*row[:7]), -float(row[7])) for row in rows]

    def hybrid_search(self, query: str, embedder: EmbeddingProvider, limit: int = 5,
                      source_id: str | None = None, semantic_weight: float = 0.65) -> list[tuple[Chunk, float, dict[str, float]]]:
        if not 0 <= semantic_weight <= 1: raise ValueError("semantic_weight must be in [0, 1]")
        lexical = self.search(query, max(limit * 4, 20), source_id)
        lexical_rank = {chunk.id: 1 / (rank + 1) for rank, (chunk, _) in enumerate(lexical)}
        query_vector = embedder.embed([query])[0]
        sql = "SELECT c.id,c.source_id,c.title,c.section,c.text,c.page,c.url,v.dimensions,v.vector FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id WHERE v.model=?"
        params: list[object] = [embedder.model]
        if source_id: sql += " AND c.source_id=?"; params.append(source_id)
        with closing(sqlite3.connect(self.path)) as db: rows = db.execute(sql, params).fetchall()
        semantic: dict[str, float] = {}; chunks: dict[str, Chunk] = {chunk.id: chunk for chunk, _ in lexical}
        for row in rows:
            dimensions = int(row[7]); vector = list(struct.unpack(f"<{dimensions}f", row[8]))
            semantic[row[0]] = max(0.0, cosine_similarity(query_vector, vector)); chunks[row[0]] = Chunk(*row[:7])
        semantic_order = sorted((key for key in semantic if semantic[key] > 0), key=semantic.get, reverse=True)
        semantic_rank = {chunk_id: 1 / (rank + 1) for rank, chunk_id in enumerate(semantic_order)}
        results = []
        for chunk_id, chunk in chunks.items():
            lexical_component = lexical_rank.get(chunk_id, 0.0); semantic_component = semantic_rank.get(chunk_id, 0.0)
            score = (1 - semantic_weight) * lexical_component + semantic_weight * semantic_component
            if score > 0:
                results.append((chunk, score, {"lexical": lexical_component, "semantic": semantic.get(chunk_id, 0.0)}))
        return sorted(results, key=lambda item: item[1], reverse=True)[:limit]

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids: return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(f"SELECT id,source_id,title,section,text,page,url FROM chunks WHERE id IN ({placeholders})", chunk_ids).fetchall()
        by_id = {row[0]: Chunk(*row) for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

    def for_sources(self, source_ids: list[str]) -> list[Chunk]:
        if not source_ids: return []
        placeholders = ",".join("?" for _ in source_ids)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(f"SELECT id,source_id,title,section,text,page,url FROM chunks WHERE source_id IN ({placeholders}) ORDER BY source_id,id", source_ids).fetchall()
        return [Chunk(*row) for row in rows]

    def count(self, source_id: str | None = None) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            if source_id: return int(db.execute("SELECT count(*) FROM chunks WHERE source_id=?", (source_id,)).fetchone()[0])
            return int(db.execute("SELECT count(*) FROM chunks").fetchone()[0])
