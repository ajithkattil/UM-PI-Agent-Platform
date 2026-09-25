"""Policy retrieval — wraps Pinecone query + effective-date filtering.

Requires PINECONE_API_KEY and an embeddings provider to actually run; not
executable in this environment. Structured so scripts/ingest_policy_pinecone.py
--dry-run can exercise the chunking/metadata logic above it without touching
this module at all.
"""

from datetime import date
from src.schemas import PolicyChunk
from src import config


def upsert_chunks(file_name: str, chunks: list) -> None:
    """Embeds and upserts chunks to Pinecone with deterministic IDs
    ({file_name}-chunk-{idx}), same pattern as the cold-chain ingestion
    script — this is what makes purge-and-replace-on-update safe."""
    from pinecone import Pinecone
    from langchain_pinecone import PineconeVectorStore
    from langchain_openai import OpenAIEmbeddings

    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    embeddings = OpenAIEmbeddings()
    vector_store = PineconeVectorStore(index_name=config.POLICY_INDEX_NAME, embedding=embeddings)

    index_client = pc.Index(config.POLICY_INDEX_NAME)
    try:
        index_client.delete(filter={"source_file": {"$eq": file_name}})
    except Exception:
        pass

    ids = [f"{file_name}-chunk-{i}" for i in range(len(chunks))]
    vector_store.add_documents(documents=chunks, ids=ids)


def retrieve_policy(service_code: str, request_date: date, k: int = None) -> list[PolicyChunk]:
    """Retrieves policy chunks for a service code, filtered to the policy
    version that was actually in effect on the request date — not whatever's
    newest in the index. This is the effective-dating check the cold-chain
    ingestion pattern didn't need but PA does."""
    from pinecone import Pinecone
    from langchain_pinecone import PineconeVectorStore
    from langchain_openai import OpenAIEmbeddings

    k = k or config.RETRIEVAL_TOP_K
    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    embeddings = OpenAIEmbeddings()
    vector_store = PineconeVectorStore(index_name=config.POLICY_INDEX_NAME, embedding=embeddings)

    results = vector_store.similarity_search(
        query=f"coverage policy for service code {service_code}",
        k=k,
        filter={"service_code": {"$eq": service_code}},
    )

    chunks = []
    for doc in results:
        meta = doc.metadata
        effective = meta.get("effective_date")
        superseded = meta.get("superseded_date")

        if effective and str(effective) > request_date.isoformat():
            continue  # policy wasn't effective yet on the request date
        if superseded and str(superseded) <= request_date.isoformat():
            continue  # policy was already superseded by the request date

        chunks.append(PolicyChunk(
            text=doc.page_content,
            source_file=meta.get("source_file", "unknown"),
            section_header=meta.get("section_header"),
            effective_date=effective,
        ))
    return chunks
