"""Semantic matching layer.

Why this exists
---------------
The cl_watcher prefilter — and any naive substring-based category matcher —
loses listings that describe the right thing in different words.
Examples that get dropped today:
  "RPi 4 4GB"               → doesn't match "raspberry pi"
  "Intel NUC i5"            → doesn't match "single board computer"
  "lab DC source 0–30V"     → doesn't match "bench psu"
  "WTB: needs a working motor" → matches "motor" but should be killed as buyer
  "ISO an old ThinkPad"     → buyer-side, current rules miss "ISO"

This module embeds text into a vector and matches by cosine similarity, so
"RPi 4" lives next to "raspberry pi 4" in vector space and we don't drop
the listing.

Two backends
------------
'local'  : sentence-transformers, model 'all-MiniLM-L6-v2'. Free, offline.
           First run downloads ~90 MB; subsequent runs load from disk cache.
'voyage' : Voyage AI (Anthropic's recommended embeddings provider).
           Set VOYAGE_API_KEY in env or .env. Costs roughly $0.02 per 1M
           tokens with voyage-3-lite, so 2,000 listings is < 1¢.

Choose with APPRAISER_EMBED_BACKEND=local|voyage. Default: 'local'.

What we use it for
------------------
1. category_of_semantic(text)
     Maps an LLM-produced item_kind (or a raw listing title) to the nearest
     entry in config.CATEGORY_TABLE. Returns (category_key, similarity).
2. is_buyer_post_semantic(title)
     Catches "WTB", "ISO", "in search of", "looking to acquire", etc.
3. is_excluded_semantic(item_kind)
     Catches "exercise bike", "leather chair", "vintage CRT", etc.
4. SemanticIndex
     Reusable nearest-neighbor over a list of labeled phrases. We use one
     instance per concept (categories, buyer phrases, excluded phrases).

Embedding cache
---------------
Embeddings live in a small SQLite file keyed by SHA1(model|text). Both
backends share the cache. TTL is effectively forever — re-embed only when
the model changes.
"""
from __future__ import annotations
import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import config


log = logging.getLogger(__name__)

# Threshold tuned by hand. MiniLM produces cosine similarities in the
# 0.40–0.85 band for "same concept, different words". Voyage tends a bit
# higher. We only use this as a coarse gate — the LLM still has the final
# say on item_kind.
SEMANTIC_MATCH_THRESHOLD = float(
    os.environ.get("APPRAISER_SEMANTIC_THRESHOLD", "0.55")
)
EMBED_BACKEND = os.environ.get("APPRAISER_EMBED_BACKEND", "local")
LOCAL_MODEL = os.environ.get(
    "APPRAISER_LOCAL_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
VOYAGE_MODEL = os.environ.get(
    "APPRAISER_VOYAGE_EMBED_MODEL", "voyage-3-lite"
)

EMBED_CACHE_PATH = config.APPRAISAL_DIR / "embed_cache.db"

# ---------- Cache ----------

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS embed_cache (
    key TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);
"""


def _open_cache() -> sqlite3.Connection:
    EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(EMBED_CACHE_PATH)
    conn.executescript(_CACHE_SCHEMA)
    return conn


def _cache_key(backend: str, model: str, text: str) -> str:
    return hashlib.sha1(f"{backend}|{model}|{text}".encode()).hexdigest()


def _cache_get(conn, key) -> Optional[list[float]]:
    row = conn.execute("SELECT dim, vec FROM embed_cache WHERE key=?",
                       (key,)).fetchone()
    if not row:
        return None
    import struct
    dim, blob = row
    return list(struct.unpack(f"<{dim}f", blob))


def _cache_put(conn, key, backend, model, vec) -> None:
    import struct
    blob = struct.pack(f"<{len(vec)}f", *vec)
    conn.execute(
        """INSERT OR REPLACE INTO embed_cache
           (key, backend, model, dim, vec) VALUES (?,?,?,?,?)""",
        (key, backend, model, len(vec), blob),
    )
    conn.commit()


# ---------- Embedder ----------

class Embedder:
    """Single embedder, lazy-loaded. Use embed_batch for many texts."""

    def __init__(self, backend: Optional[str] = None):
        self.backend = backend or EMBED_BACKEND
        self.model_name = (VOYAGE_MODEL if self.backend == "voyage"
                           else LOCAL_MODEL)
        self._st_model = None  # sentence-transformers lazy import
        self._voyage_client = None

    # ---- public ----

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[Optional[list[float]]] = [None] * len(texts)
        cache = _open_cache()
        try:
            misses = []
            miss_idx = []
            for i, t in enumerate(texts):
                k = _cache_key(self.backend, self.model_name, t)
                v = _cache_get(cache, k)
                if v is None:
                    misses.append(t)
                    miss_idx.append(i)
                else:
                    out[i] = v
            if misses:
                fresh = self._embed_uncached(misses)
                for j, (idx, t) in enumerate(zip(miss_idx, misses)):
                    out[idx] = fresh[j]
                    k = _cache_key(self.backend, self.model_name, t)
                    _cache_put(cache, k, self.backend,
                               self.model_name, fresh[j])
            return [v for v in out if v is not None]
        finally:
            cache.close()

    # ---- backends ----

    def _embed_uncached(self, texts: list[str]) -> list[list[float]]:
        if self.backend == "local":
            return self._embed_local(texts)
        if self.backend == "voyage":
            return self._embed_voyage(texts)
        raise ValueError(f"unknown embed backend: {self.backend}")

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        if self._st_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    "Local embedding backend requires "
                    "sentence-transformers. `pip install "
                    "sentence-transformers` or set "
                    "APPRAISER_EMBED_BACKEND=voyage with VOYAGE_API_KEY."
                ) from e
            log.info("Loading local embedding model %s (first run "
                     "downloads ~90 MB)", self.model_name)
            self._st_model = SentenceTransformer(self.model_name)
        vecs = self._st_model.encode(texts,
                                     normalize_embeddings=True,
                                     show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]

    def _embed_voyage(self, texts: list[str]) -> list[list[float]]:
        if self._voyage_client is None:
            try:
                import voyageai
            except ImportError as e:
                raise RuntimeError(
                    "Voyage backend requires `pip install voyageai` "
                    "and VOYAGE_API_KEY in env."
                ) from e
            self._voyage_client = voyageai.Client()
        # voyage-3-lite is fine for short strings up to 32k tokens.
        result = self._voyage_client.embed(
            texts=texts, model=self.model_name, input_type="document",
        )
        return [list(map(float, v)) for v in result.embeddings]


# ---------- Cosine + index ----------

def cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return s / (na * nb)


class SemanticIndex:
    """Tiny in-memory nearest-neighbor over a list of (key, phrase) pairs.

    Embeds all phrases once on construction; query() embeds one input and
    returns the top match by cosine similarity.
    """

    def __init__(self, items: list[tuple[str, str]],
                 embedder: Optional[Embedder] = None):
        # items: list of (key, phrase). If multiple phrases map to the
        # same key we keep them all and return the best.
        self.embedder = embedder or Embedder()
        self.items = items
        phrases = [p for _, p in items]
        self.vecs = self.embedder.embed_batch(phrases)

    def query(self, text: str) -> tuple[Optional[str], float]:
        if not self.items:
            return None, 0.0
        q = self.embedder.embed(text)
        best_key, best_sim = None, -1.0
        for (key, _), v in zip(self.items, self.vecs):
            sim = cosine(q, v)
            if sim > best_sim:
                best_sim = sim
                best_key = key
        return best_key, best_sim

    def query_above(self, text: str, threshold: float
                    ) -> tuple[Optional[str], float]:
        k, s = self.query(text)
        if s >= threshold:
            return k, s
        return None, s


# ---------- Pre-built indices ----------

# These build lazily on first use. Building involves embedding ~50–80
# phrases, which costs nothing measurable on either backend.

_category_index: Optional[SemanticIndex] = None
_buyer_index: Optional[SemanticIndex] = None
_excluded_index: Optional[SemanticIndex] = None
_accessory_index: Optional[SemanticIndex] = None


def _category_phrases() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, meta in config.CATEGORY_TABLE.items():
        # Several phrasings per category — both the key and a sentence
        # combining the key with its 'reason' field. This widens the
        # semantic neighborhood without hurting precision much.
        out.append((key, key))
        out.append((key, f"{key} for parts: {meta['reason']}"))
    # Hand-rolled extra synonyms that are commonly missed by substring.
    extras = [
        ("raspberry pi 4", "rpi 4"), ("raspberry pi 4", "rpi4"),
        ("raspberry pi 5", "rpi 5"), ("raspberry pi", "rpi"),
        ("raspberry pi", "single-board computer"),
        ("single board computer", "linux SBC"),
        ("bench psu", "DC bench supply 0-30V"),
        ("bench psu", "lab power supply variable"),
        ("bench psu", "regulated DC supply"),
        ("electric scooter", "e-scooter"),
        ("electric bike", "e-bike"),
        ("electric skateboard", "esk8 board"),
        ("electric skateboard", "boosted board electric"),
        ("kinect", "xbox depth camera"),
        ("kinect", "azure kinect dk"),
        ("3d printer", "FDM printer"),
        ("3d printer", "filament 3D printer"),
        ("oscilloscope", "scope analyzer"),
        ("oscilloscope", "DSO digital storage scope"),
        ("vesc", "VESC speed controller"),
        ("treadmill", "treadmill DC motor"),
        ("hoverboard", "self balancing scooter"),
        ("robot vacuum", "neato robot vacuum"),
        ("robot vacuum", "roborock vacuum"),
        ("router", "OpenWrt router"),
        ("cnc", "CNC router milling machine"),
        ("wheelchair", "power wheelchair scooter mobility"),
    ]
    return out + extras


def _buyer_phrases() -> list[tuple[str, str]]:
    return [
        ("buyer", "wanted to buy"),
        ("buyer", "WTB"),
        ("buyer", "in search of"),
        ("buyer", "ISO"),
        ("buyer", "looking for"),
        ("buyer", "looking to acquire"),
        ("buyer", "anyone selling"),
        ("buyer", "i need a"),
        ("buyer", "trade for"),
        ("buyer", "for trade"),
        ("buyer", "will pay cash for"),
    ]


def _excluded_phrases() -> list[tuple[str, str]]:
    return [
        ("bicycle", "regular bicycle non-electric"),
        ("bicycle", "road bike mountain bike"),
        ("office_chair", "office chair desk chair"),
        ("crt_tv", "CRT television tube TV"),
        ("loose_battery", "used loose lithium battery cell"),
        ("loose_battery", "salvaged 18650 cells unknown provenance"),
    ]


def _accessory_phrases() -> list[tuple[str, str]]:
    return [
        ("accessory", "ink cartridge only"),
        ("accessory", "toner only"),
        ("accessory", "filament spool"),
        ("accessory", "laptop bag case"),
        ("accessory", "drone propeller props"),
        ("accessory", "ebike battery only"),
        ("accessory", "scooter tire only"),
        ("accessory", "charger only no device"),
        ("accessory", "replacement parts kit only"),
    ]


def categories_index() -> SemanticIndex:
    global _category_index
    if _category_index is None:
        _category_index = SemanticIndex(_category_phrases())
    return _category_index


def buyer_index() -> SemanticIndex:
    global _buyer_index
    if _buyer_index is None:
        _buyer_index = SemanticIndex(_buyer_phrases())
    return _buyer_index


def excluded_index() -> SemanticIndex:
    global _excluded_index
    if _excluded_index is None:
        _excluded_index = SemanticIndex(_excluded_phrases())
    return _excluded_index


def accessory_index() -> SemanticIndex:
    global _accessory_index
    if _accessory_index is None:
        _accessory_index = SemanticIndex(_accessory_phrases())
    return _accessory_index


# ---------- High-level helpers ----------

def category_of_semantic(text: str,
                         threshold: float = SEMANTIC_MATCH_THRESHOLD
                         ) -> tuple[Optional[str], float]:
    return categories_index().query_above(text, threshold)


def is_buyer_post_semantic(text: str,
                           threshold: float = SEMANTIC_MATCH_THRESHOLD
                           ) -> tuple[bool, float]:
    k, s = buyer_index().query(text)
    return (k == "buyer" and s >= threshold), s


def is_excluded_semantic(text: str,
                         threshold: float = SEMANTIC_MATCH_THRESHOLD
                         ) -> tuple[Optional[str], float]:
    return excluded_index().query_above(text, threshold)


def is_accessory_only_semantic(text: str,
                               threshold: float = SEMANTIC_MATCH_THRESHOLD
                               ) -> tuple[bool, float]:
    k, s = accessory_index().query(text)
    return (k == "accessory" and s >= threshold), s
