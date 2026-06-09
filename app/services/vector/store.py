import logging
from typing import Any

from app.config import (
    CHROMA_COLLECTION_NAME,
    CHROMA_ENABLED,
    CHROMA_LOG_EMBEDDINGS,
    CHROMA_PATH,
)

logger = logging.getLogger(__name__)

_client: Any = None
_collection: Any = None
_init_failed = False


def _get_collection():
    global _client, _collection, _init_failed
    if not CHROMA_ENABLED:
        return None
    if _init_failed:
        return None
    if _collection is not None:
        return _collection

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        _collection = _client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("[VECTOR] ChromaDB ready at %s (collection=%s)", CHROMA_PATH, CHROMA_COLLECTION_NAME)
        return _collection
    except Exception:
        _init_failed = True
        logger.exception("[VECTOR] Failed to initialize ChromaDB")
        return None


def upsert_activity_chunk(
    *,
    chunk_id: int,
    recording_id: int,
    cleaned_text: str,
    app_name: str | None,
    window_name: str | None,
    browser_url: str | None,
    category: str,
    timestamp: str,
    frame_ids: list[int] | None = None,
    paddle_chars: int = 0,
    screenpipe_chars: int = 0,
) -> bool:
    """Embed and store a classified activity chunk."""
    if not cleaned_text.strip():
        return False

    collection = _get_collection()
    if collection is None:
        return False

    doc_id = f"chunk_{chunk_id}"
    ocr_sources: list[str] = []
    if paddle_chars > 0:
        ocr_sources.append("paddle")
    if screenpipe_chars > 0:
        ocr_sources.append("screenpipe")
    metadata = {
        "chunk_id": chunk_id,
        "recording_id": recording_id,
        "app_name": app_name or "",
        "window_name": (window_name or "")[:200],
        "browser_url": (browser_url or "")[:500],
        "category": category,
        "timestamp": timestamp,
        "ocr_sources": ",".join(ocr_sources) or "none",
        "paddle_chars": paddle_chars,
        "screenpipe_chars": screenpipe_chars,
        "merged_chars": len(cleaned_text.strip()),
    }

    try:
        collection.upsert(
            ids=[doc_id],
            documents=[cleaned_text],
            metadatas=[metadata],
        )
        total = collection.count()
        if CHROMA_LOG_EMBEDDINGS:
            preview = cleaned_text.replace("\n", " ")[:100]
            frame_ids_str = ", ".join(str(fid) for fid in frame_ids) if frame_ids else "n/a"
            logger.debug(
                "[VECTOR] Embedded %s | recording=%s category=%s frame_ids=[%s] "
                "chars=%s total_in_chroma=%s | preview: %s",
                doc_id,
                recording_id,
                category,
                frame_ids_str,
                len(cleaned_text),
                total,
                preview or "(empty)",
            )
        return True
    except Exception:
        logger.exception("[VECTOR] Failed to upsert %s", doc_id)
        return False


def chunk_exists_in_chroma(chunk_id: int) -> bool:
    collection = _get_collection()
    if collection is None:
        return False
    try:
        result = collection.get(ids=[f"chunk_{chunk_id}"])
        return bool(result.get("ids"))
    except Exception:
        logger.debug("[VECTOR] Failed to check chunk_%s in Chroma", chunk_id, exc_info=True)
        return False


def search_activity_chunks(
    query: str,
    *,
    recording_id: int | None = None,
    category: str | None = None,
    n_results: int = 10,
) -> dict[str, Any]:
    collection = _get_collection()
    if collection is None:
        return {"enabled": False, "count": 0, "results": []}

    where = _build_where_filter(recording_id=recording_id, category=category)

    try:
        raw = collection.query(
            query_texts=[query],
            n_results=max(1, min(n_results, 50)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        logger.exception("[VECTOR] Search failed")
        return {"enabled": True, "count": 0, "results": []}

    results: list[dict[str, Any]] = []
    ids = raw.get("ids", [[]])[0]
    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]

    for idx, doc_id in enumerate(ids):
        distance = distances[idx] if idx < len(distances) else None
        similarity = None
        if distance is not None:
            similarity = round(1 - (distance / 2), 4)
        results.append(
            {
                "id": doc_id,
                "document": documents[idx] if idx < len(documents) else "",
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "distance": distance,
                "similarity": similarity,
            }
        )

    return {"enabled": True, "count": len(results), "results": results}


def _build_where_filter(
    *,
    recording_id: int | None = None,
    category: str | None = None,
) -> dict[str, Any] | None:
    if recording_id is not None and category is not None:
        return {"$and": [{"recording_id": recording_id}, {"category": category}]}
    if recording_id is not None:
        return {"recording_id": recording_id}
    if category is not None:
        return {"category": category}
    return None


def _count_collection(collection: Any, where: dict[str, Any] | None) -> int:
    if where is None:
        return collection.count()
    try:
        return collection.count(where=where)
    except TypeError:
        raw = collection.get(where=where, include=[])
        return len(raw.get("ids", []))


def _normalize_embedding_item(
    doc_id: str,
    document: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    meta = metadata or {}
    text = document or ""
    preview = text.replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:120] + "…"
    chunk_id = meta.get("chunk_id")
    return {
        "id": doc_id,
        "chunk_id": int(chunk_id) if chunk_id is not None else None,
        "recording_id": meta.get("recording_id"),
        "category": meta.get("category") or None,
        "app_name": meta.get("app_name") or None,
        "window_name": meta.get("window_name") or None,
        "browser_url": meta.get("browser_url") or None,
        "timestamp": meta.get("timestamp") or None,
        "ocr_sources": meta.get("ocr_sources") or None,
        "paddle_chars": meta.get("paddle_chars"),
        "screenpipe_chars": meta.get("screenpipe_chars"),
        "merged_chars": meta.get("merged_chars"),
        "document": text,
        "preview": preview,
    }


def list_activity_embeddings(
    *,
    recording_id: int | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List embedded activity chunks from Chroma with total count."""
    collection = _get_collection()
    if collection is None:
        return {
            "enabled": CHROMA_ENABLED,
            "ready": False,
            "collection": CHROMA_COLLECTION_NAME,
            "total": 0,
            "items": [],
        }

    where = _build_where_filter(recording_id=recording_id, category=category)
    try:
        total = _count_collection(collection, where)
        raw = collection.get(
            where=where,
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"],
        )
    except Exception:
        logger.exception("[VECTOR] Failed to list embeddings")
        return {
            "enabled": True,
            "ready": False,
            "collection": CHROMA_COLLECTION_NAME,
            "total": 0,
            "items": [],
        }

    items: list[dict[str, Any]] = []
    ids = raw.get("ids", [])
    documents = raw.get("documents", [])
    metadatas = raw.get("metadatas", [])
    for idx, doc_id in enumerate(ids):
        document = documents[idx] if idx < len(documents) else ""
        metadata = metadatas[idx] if idx < len(metadatas) else {}
        items.append(_normalize_embedding_item(doc_id, document, metadata))

    items.sort(key=lambda item: item.get("chunk_id") or 0, reverse=True)

    return {
        "enabled": True,
        "ready": True,
        "collection": CHROMA_COLLECTION_NAME,
        "total": total,
        "items": items,
    }


def get_vector_stats() -> dict[str, Any]:
    collection = _get_collection()
    if collection is None:
        return {
            "enabled": CHROMA_ENABLED,
            "ready": False,
            "path": str(CHROMA_PATH),
            "collection": CHROMA_COLLECTION_NAME,
            "count": 0,
            "sample": [],
        }

    try:
        count = collection.count()
        peek = collection.peek(min(5, count)) if count else {"ids": [], "documents": [], "metadatas": []}
        sample = []
        for idx, doc_id in enumerate(peek.get("ids", [])):
            sample.append(
                {
                    "id": doc_id,
                    "category": (peek.get("metadatas", [{}])[idx] or {}).get("category"),
                    "app_name": (peek.get("metadatas", [{}])[idx] or {}).get("app_name"),
                    "preview": (peek.get("documents", [""])[idx] or "")[:120],
                }
            )
        return {
            "enabled": True,
            "ready": True,
            "path": str(CHROMA_PATH),
            "collection": CHROMA_COLLECTION_NAME,
            "count": count,
            "sample": sample,
        }
    except Exception:
        logger.exception("[VECTOR] Failed to read stats")
        return {
            "enabled": True,
            "ready": False,
            "path": str(CHROMA_PATH),
            "collection": CHROMA_COLLECTION_NAME,
            "count": 0,
            "sample": [],
        }
