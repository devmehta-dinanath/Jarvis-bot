from fastapi import APIRouter, Query

from app.schemas import (
    VectorEmbeddingListResponse,
    VectorSearchRequest,
    VectorSearchResponse,
    VectorStatsResponse,
)
from app.services.activity.categories import ALL_CATEGORIES
from app.services.vector.store import (
    get_vector_stats,
    list_activity_embeddings,
    search_activity_chunks,
)

router = APIRouter(prefix="/api/v1/vector", tags=["vector"])


@router.get("/stats", response_model=VectorStatsResponse)
def vector_stats() -> VectorStatsResponse:
    data = get_vector_stats()
    return VectorStatsResponse(**data)


@router.get("/embeddings", response_model=VectorEmbeddingListResponse)
def list_embeddings(
    recording_id: int | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> VectorEmbeddingListResponse:
    data = list_activity_embeddings(
        recording_id=recording_id,
        category=category,
        limit=limit,
        offset=offset,
    )
    return VectorEmbeddingListResponse(**data)


@router.get("/categories")
def list_categories() -> dict[str, list[str]]:
    return {"categories": sorted(category.value for category in ALL_CATEGORIES)}


@router.post("/search", response_model=VectorSearchResponse)
def vector_search(payload: VectorSearchRequest) -> VectorSearchResponse:
    data = search_activity_chunks(
        payload.query,
        recording_id=payload.recording_id,
        category=payload.category,
        n_results=payload.limit,
    )
    return VectorSearchResponse(**data)


@router.get("/search", response_model=VectorSearchResponse)
def vector_search_get(
    query: str = Query(..., min_length=1),
    recording_id: int | None = None,
    category: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> VectorSearchResponse:
    data = search_activity_chunks(
        query,
        recording_id=recording_id,
        category=category,
        n_results=limit,
    )
    return VectorSearchResponse(**data)
