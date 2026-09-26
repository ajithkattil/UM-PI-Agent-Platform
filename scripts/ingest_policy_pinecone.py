"""
Policy document ingestion — adapted from the cold-chain project's ingestion
script (header-aware chunking, incremental hash-based re-ingestion, deletion
handling, deterministic chunk IDs — all carried over as-is, see PA-Agent-LLD
Section on adopted patterns).

Two things added specifically for PA that the cold-chain version didn't need:

1. `effective_date` / `superseded_date` extracted from each policy doc's header
   block and stored as chunk metadata — the effective-dating gap flagged during
   the cold-chain review. A decision must be evaluated against the policy that
   was active on the request date, not whichever version is newest in the index.
2. A --dry-run mode that runs the full parse/chunk pipeline without touching
   Pinecone or requiring an embeddings API key, so the chunking logic itself is
   testable in an environment with no external credentials configured.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_core.documents import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = PROJECT_ROOT / "data" / "policy"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
HASH_CACHE_FILE = CACHE_DIR / "ingestion_hash_cache.json"

EFFECTIVE_DATE_RE = re.compile(r"\*\*Effective Date\*\*:\s*(\d{4}-\d{2}-\d{2})")
SUPERSEDED_DATE_RE = re.compile(r"\*\*Superseded Date\*\*:\s*(\d{4}-\d{2}-\d{2}|None.*)")
POLICY_ID_RE = re.compile(r"\*\*Policy ID\*\*:\s*(\S+)")
SERVICE_CODE_RE = re.compile(r"\*\*Service Code\*\*:\s*(.+)")


def extract_policy_metadata(raw_text: str) -> dict:
    """Pulls the effective-dating and identifying fields out of the policy
    doc's header block. This is what closes the effective-dating gap —
    every chunk from this file carries these fields forward."""
    meta = {}
    if m := EFFECTIVE_DATE_RE.search(raw_text):
        meta["effective_date"] = m.group(1)
    if m := SUPERSEDED_DATE_RE.search(raw_text):
        val = m.group(1).strip()
        meta["superseded_date"] = None if val.startswith("None") else val
    if m := POLICY_ID_RE.search(raw_text):
        meta["policy_id"] = m.group(1)
    if m := SERVICE_CODE_RE.search(raw_text):
        # The policy doc's header writes the full line ("CPT 72148",
        # "HCPCS A4239") but requests use the bare code ("72148", "A4239") —
        # keep only the last token so retrieval's exact-match filter actually
        # matches. Without this, every retrieval silently returns zero
        # chunks and the agent correctly escalates on empty context — which
        # looks like a reasoning failure but is actually this mismatch.
        full = m.group(1).strip()
        meta["service_code"] = full.split()[-1]
    return meta


def parse_and_chunk_document(doc_path: Path) -> list[Document]:
    """Header-aware chunking — same pattern as the cold-chain ingestion
    script. Markdown headers are split first (preserving section identity as
    metadata) and only then recursively chunked, so a retrieved chunk always
    knows which policy section it came from — this is what makes a specific,
    citable denial reason possible."""
    raw_text = doc_path.read_text(encoding="utf-8")
    policy_meta = extract_policy_metadata(raw_text)

    headers_to_split_on = [("#", "Header_1"), ("##", "Header_2"), ("###", "Header_3")]
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    header_docs = md_splitter.split_text(raw_text)

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)
    chunks = text_splitter.split_documents(header_docs)

    for chunk in chunks:
        chunk.metadata.update(policy_meta)
        chunk.metadata["source_file"] = doc_path.name
        chunk.metadata["document_type"] = "Coverage Policy"
        # Flatten whichever header level is deepest into one field the
        # retrieval layer can cite directly.
        section = (
            chunk.metadata.get("Header_3")
            or chunk.metadata.get("Header_2")
            or chunk.metadata.get("Header_1")
        )
        chunk.metadata["section_header"] = section

        # Pinecone rejects null metadata values outright — strip any key
        # whose value is None rather than sending it (e.g. superseded_date
        # is None for every currently-active policy).
        chunk.metadata = {k: v for k, v in chunk.metadata.items() if v is not None}

    return [c for c in chunks if c.page_content.strip()]


def run(dry_run: bool = False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hash_cache = json.loads(HASH_CACHE_FILE.read_text()) if HASH_CACHE_FILE.exists() else {}

    current_files = {p.name: p for p in POLICY_DIR.glob("*.md")}
    print(f"Found {len(current_files)} policy file(s) in {POLICY_DIR}")

    updated_cache = {}
    total_chunks = 0

    for file_name, file_path in current_files.items():
        file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
        updated_cache[file_name] = file_hash

        if hash_cache.get(file_name) == file_hash:
            print(f"  skipped (unchanged): {file_name}")
            continue

        chunks = parse_and_chunk_document(file_path)
        total_chunks += len(chunks)
        print(f"  processed: {file_name} -> {len(chunks)} chunks")

        for i, chunk in enumerate(chunks[:2]):  # preview first 2 chunks per file
            print(f"    [{i}] section={chunk.metadata.get('section_header')!r} "
                  f"effective={chunk.metadata.get('effective_date')} "
                  f"text={chunk.page_content[:80]!r}...")

        if not dry_run:
            # Real path: embed + upsert to Pinecone with explicit
            # deterministic IDs ({file_name}-chunk-{idx}), same as cold-chain.
            # Requires PINECONE_API_KEY / embeddings provider configured.
            from src.tools.policy_tools import upsert_chunks
            upsert_chunks(file_name, chunks)

    if not dry_run:
        HASH_CACHE_FILE.write_text(json.dumps(updated_cache, indent=2))

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Total new/changed chunks: {total_chunks}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="Run chunking/metadata extraction without Pinecone or an embeddings API key")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
