"""Exact, versioned, expiring cache for structured low temperature calls."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class CachedProvider:
    def __init__(self, provider, path: str | Path, version: str, ttl_seconds: int):
        self.provider, self.path, self.version, self.ttl_seconds = provider, Path(path), version, ttl_seconds
        self.name = provider.name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, expires REAL, result TEXT NOT NULL)")
            db.commit()
        self.last_usage = {}
        self.last_cost_usd = 0.0
        self.price_known = getattr(provider, "price_known", False)
        self.cache_hit = False
        self.last_web_search_calls = 0

    @property
    def supports_web_search(self):
        return bool(getattr(self.provider, "supports_web_search", False))

    def generate(self, messages, schema):
        return self._generate(messages, schema)

    def generate_code(self, messages, schema):
        return self._generate(messages, schema, code=True)

    def generate_plan(self, messages, schema):
        return self._generate(messages, schema, planning=True)

    def generate_with_search(self, messages, schema, *, max_tool_calls=4):
        return self._generate(messages, schema, search_options={"max_tool_calls": max_tool_calls})

    def _generate(self, messages, schema, search_options=None, *, code=False, planning=False):
        self.cache_hit, self.last_usage, self.last_cost_usd = False, {}, 0.0
        self.last_web_search_calls = 0
        key = self._key(messages, schema, search_options)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT result FROM llm_cache WHERE key=? AND expires>?", (key, time.time())).fetchone()
        if row:
            self.cache_hit, self.last_usage, self.last_cost_usd = True, {}, 0.0
            return json.loads(row[0])
        try:
            generate = (getattr(self.provider, "generate_code", self.provider.generate)
                        if code else self.provider.generate)
            if planning:
                generate = getattr(self.provider, "generate_plan", self.provider.generate)
            result = (self.provider.generate_with_search(messages, schema, **search_options)
                      if search_options else generate(messages, schema))
        except Exception:
            self.last_usage = getattr(self.provider, "last_usage", {})
            self.last_cost_usd = getattr(self.provider, "last_cost_usd", 0.0)
            self.last_web_search_calls = getattr(self.provider, "last_web_search_calls", 0)
            raise
        self.last_usage = getattr(self.provider, "last_usage", {})
        self.last_cost_usd = getattr(self.provider, "last_cost_usd", 0.0)
        self.last_web_search_calls = getattr(self.provider, "last_web_search_calls", 0)
        self.price_known = getattr(self.provider, "price_known", False)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?)",
                       (key, time.time() + self.ttl_seconds, json.dumps(result)))
            db.commit()
        return result

    def _key(self, messages, schema, search_options=None):
        values = [self.version, self.name, messages, schema]
        if search_options:
            values.append(search_options)
        key = hashlib.sha256(json.dumps(values,
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return key

    def will_hit(self, messages, schema, *, search_options=None) -> bool:
        key = self._key(messages, schema, search_options)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT result FROM llm_cache WHERE key=? AND expires>?", (key, time.time())).fetchone()
        return row is not None
