# workers/summary_worker.py
import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

import dramatiq
import requests

from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from utils.progress_tracker import get_progress_tracker
from utils.queue_dispatch import enqueue_worker
from utils.worker_event_loop import start_worker_event_loop

start_worker_event_loop("summary")

print("✅ Summary worker loaded")

# ============================================================
# Config
# ============================================================
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_SUMMARY_MODEL", "gpt-oss:20b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))

SUMMARY_QUEUE_NAME = os.getenv("SUMMARY_QUEUE_NAME", "summaries")

# ============================================================
# Prompt
# ============================================================
PAGE_SUMMARY_PROMPT = """You are a data extraction assistant. You have been given chunks of content extracted from a single webpage. Your job is to generate a factual summary chunk that will be stored in a vector database and used to answer high-level questions about this page.

Page Title: {page_title}
Page URL: {page_url}
Total Chunks Extracted: {total_chunks}

Sample Content (first 10 chunks):
{sample_chunks}

Generate a structured factual summary in the following format EXACTLY:

PAGE SUMMARY
Title: <page title>
URL: <page url>
Content Type: <what kind of page this is, e.g. "list of countries", "product catalog", "documentation page">

FACTS
- This page contains exactly {total_chunks} content chunks.
- <fact about what entities/items are listed, e.g. "This page lists 250 countries with their capital, population and area.">
- <fact about the data fields available, e.g. "Each country entry includes: name, capital city, population, and area in km².">
- <any other notable fact about the page content>

COUNTS
- Total items/entries on this page: <number>
- <entity type> count: <number>

ANSWERABLE QUESTIONS
This page can answer questions like:
- How many <entity> are there?
- What is the <field> of <entity>?
- List all <entity> where <condition>

Do NOT invent data. Only state facts directly supported by the chunk content provided."""


# ============================================================
# Helpers
# ============================================================
def _truncate(text: str, max_chars: int = 1500) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def build_sample_chunks(
    chunks: List[Dict[str, Any]],
    limit: int = 10,
    per_chunk_chars: int = 1500,
) -> str:
    """Format the first N chunks for the prompt."""
    lines: List[str] = []
    for i, ch in enumerate(chunks[:limit], 1):
        ctype = ch.get("chunk_type", "unknown")
        content = _truncate(ch.get("content", ""), per_chunk_chars)
        lines.append(f"--- Chunk {i} ({ctype}) ---\n{content}")
    return "\n\n".join(lines) if lines else "(no chunks)"


def call_ollama(prompt: str) -> Optional[str]:
    """Call Ollama /api/generate. Returns text or None on any failure."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 800},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        return text or None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Ollama call failed: {e}")
        return None
    except (ValueError, KeyError) as e:
        print(f"⚠️ Ollama response malformed: {e}")
        return None


def build_fallback_summary(
    page_title: str,
    page_url: str,
    total_chunks: int,
    chunk_type_counts: Dict[str, int],
) -> str:
    """Deterministic summary used when the LLM is unavailable/slow."""
    lines = [
        "PAGE SUMMARY",
        f"Title: {page_title or 'Untitled Page'}",
        f"URL: {page_url or ''}",
        "Content Type: auto-generated (LLM unavailable)",
        "",
        "FACTS",
        f"- This page contains exactly {total_chunks} content chunks.",
    ]

    # Only mention types that actually appear
    type_summaries = {
        'section': 'content section(s)',
        'table_summary': 'table(s)',
        'table_row': 'table row(s)',
        'list': 'list(s)',
        'list_item': 'list item(s)',
        'card': 'card(s)',
        'code': 'code block(s)',
        'image_description': 'image description(s)',
        'product': 'product record(s)',
        'structured_data': 'structured data block(s)',
    }
    for ctype, count in sorted(chunk_type_counts.items()):
        if ctype in ('summary',):
            continue
        label = type_summaries.get(ctype)
        if label and count > 0:
            lines.append(f"- Contains {count} {label}.")

    lines += [
        "",
        "COUNTS",
        f"- Total chunks on this page: {total_chunks}",
        "",
        "ANSWERABLE QUESTIONS",
        "This page can answer questions like:",
        "- How many items are on this page?",
        "- What sections does this page contain?",
    ]
    return "\n".join(lines)


def generate_page_summary(
    page_title: str,
    page_url: str,
    chunks: List[Dict[str, Any]],
    chunk_type_counts: Dict[str, int],
) -> str:
    """
    Produce the factual summary chunk text for a page.
    Tries Ollama first; falls back to a deterministic template.
    Never returns None for a non-empty page.
    """
    total_chunks = len(chunks)

    prompt = PAGE_SUMMARY_PROMPT.format(
        page_title=page_title or "Untitled",
        page_url=page_url or "",
        total_chunks=total_chunks,
        sample_chunks=build_sample_chunks(chunks, limit=10),
    )

    summary = call_ollama(prompt)

    if summary and "PAGE SUMMARY" in summary.upper():
        return summary

    print("ℹ️ Using fallback summary (LLM unavailable or invalid output)")
    return build_fallback_summary(
        page_title=page_title,
        page_url=page_url,
        total_chunks=total_chunks,
        chunk_type_counts=chunk_type_counts,
    )


# ============================================================
# Actor
# ============================================================
@dramatiq.actor(
    actor_name="workers.summary_worker.generate_page_summary",
    queue_name=SUMMARY_QUEUE_NAME,
    max_retries=1,
    time_limit=120000,
)
def generate_page_summary_actor(chat_id: str, document_id: str):
    """
    Generate the page-level factual summary chunk and insert it.
    Runs out-of-band from the main chunking pipeline.
    Non-fatal: failures never abort the document.
    """
    print(f"🧠 Generating page summary for document: {document_id} (chat: {chat_id})")

    tracker = get_progress_tracker(chat_id)

    try:
        doc = execute_one(
            """SELECT id, page_version_id, metadata
               FROM documents WHERE id = %s""",
            (document_id,),
        )
        if not doc:
            print(f"⚠️ Summary: document not found: {document_id}")
            return

        page_version_id = doc["page_version_id"]

        metadata_raw = doc.get("metadata")
        if isinstance(metadata_raw, str):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = metadata_raw or {}

        page_title = (
            metadata.get("page_title")
            or metadata.get("title")
            or "Untitled Page"
        )
        page_url = metadata.get("url") or metadata.get("source_url") or ""

        # ------------------------------------------------------------
        # Fetch the real chunks that were just persisted
        # ------------------------------------------------------------
        rows = execute_query(
            """SELECT id, chunk_index, chunk_type, content, entity_type
               FROM chunks
               WHERE document_id = %s
                 AND entity_type IS DISTINCT FROM 'page_summary'
               ORDER BY chunk_index ASC""",
            (document_id,),
        )
        if not rows:
            print(f"⚠️ Summary: no chunks found for document {document_id}")
            return

        chunks = [
            {
                "content": r.get("content", ""),
                "chunk_type": r.get("chunk_type", "text"),
                "entity_type": r.get("entity_type", "content"),
            }
            for r in rows
        ]
        total_chunks = len(chunks)

        chunk_type_counts: Dict[str, int] = {}
        for c in chunks:
            ct = c["chunk_type"]
            chunk_type_counts[ct] = chunk_type_counts.get(ct, 0) + 1

        # ------------------------------------------------------------
        # Guard: skip if a page_summary already exists for this version
        # ------------------------------------------------------------
        existing = execute_one(
            """SELECT id FROM chunks
               WHERE page_version_id = %s AND entity_type = 'page_summary'
               LIMIT 1""",
            (page_version_id,),
        )
        if existing:
            print(f"ℹ️ Summary already exists for page_version {page_version_id}")
            return

        # ------------------------------------------------------------
        # Generate
        # ------------------------------------------------------------
        summary_text = generate_page_summary(
            page_title=page_title,
            page_url=page_url,
            chunks=chunks,
            chunk_type_counts=chunk_type_counts,
        )

        if not summary_text:
            print("⚠️ Summary generation returned empty; skipping insert")
            return

        content_hash = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()

        # Dedup against identical content already on this version
        if execute_one(
            "SELECT id FROM chunks WHERE page_version_id = %s AND chunk_hash = %s",
            (page_version_id, content_hash),
        ):
            print("ℹ️ Identical summary already stored; skipping insert")
            return

        # ------------------------------------------------------------
        # Insert summary chunk
        # ------------------------------------------------------------
        summary_chunk_id = str(uuid.uuid4())
        now = get_current_datetime().isoformat()
        heading_path = json.dumps([page_title])

        result = execute_update(
            """INSERT INTO chunks
               (id, page_version_id, document_id, chunk_index, chunk_type,
                content, context_prefix, heading_path, token_count, chunk_hash,
                entity_type, chunk_category, section_title, position_in_page,
                information_density, embedding_status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (page_version_id, chunk_hash) DO NOTHING
            """,
            (
                summary_chunk_id,
                page_version_id,
                document_id,
                -1,                     # sorts before content chunks
                'summary',
                summary_text,
                page_title,
                heading_path,
                len(summary_text.split()),
                content_hash,
                'page_summary',
                'main_content',
                page_title,
                0.0,
                1.0,
                'PENDING',
                now,
                now,
            ),
        )

        if result == 0:
            print("ℹ️ Summary insert conflicted; no new row")
            return

        print(f"✅ Inserted page summary chunk {summary_chunk_id} "
              f"({total_chunks} source chunks summarized)")

        # ------------------------------------------------------------
        # Trigger embedding for the summary chunk
        # ------------------------------------------------------------
        enqueue_worker(
            "workers.embedder_worker.embed_chunks",
            chat_id,
            document_id,
        )

    except Exception as e:
        print(f"⚠️ Summary worker error (non-fatal): {e}")
        import traceback
        traceback.print_exc()
        # Deliberately do not re-raise: summary is best-effort.
        return


print(f"📋 Summary worker listening on queue: {SUMMARY_QUEUE_NAME}")