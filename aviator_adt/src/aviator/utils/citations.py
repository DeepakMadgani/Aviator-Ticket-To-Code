"""Utilities for extracting and processing inline citations from LLM responses."""

import logging
import re
from dataclasses import dataclass

from langchain.messages import ToolMessage

from aviator.models import (
    Chunk,
    ContextDocumentModel,
    ReferenceChunkModel,
    ReferenceModel,
    StateModel,
)

logger = logging.getLogger(__name__)

# Pattern to match [CHUNK_ID] citations in text
CITATION_PATTERN = re.compile(r"\[([^\]]+)\]")
# Conservative chunk-id token pattern used for safe removal (avoids stripping markdown links like [link text]).
CHUNK_ID_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass
class CitationChunk:
    """Chunk plus owning document/workspace context for reference resolution."""

    chunk: Chunk
    document_id: str
    workspace_id: str | None
    metadata: dict | None = None


@dataclass
class DocumentChunkGroup:
    """Preserves document-level grouping from RAG node."""

    document_id: str
    workspace_id: str | None
    chunks: list[CitationChunk]
    metadata: dict | None = None


def extract_chunk_ids_from_text(text: str) -> list[str]:
    """Extract all CHUNK_ID citations from the given text.

    Args:
        text: The text containing inline citations in [CHUNK_ID] format.

    Returns:
        List of unique chunk IDs found in the text, in order of first appearance.

    """
    if not text:
        return []

    matches = CITATION_PATTERN.findall(text)

    # Filter out common markdown patterns that aren't chunk IDs
    # (e.g., [link text], [1], [^1], etc.)
    chunk_ids = []
    seen = set()

    for match in matches:
        # Skip if it looks like a markdown link text, footnote, or simple number
        if match.isdigit() or match.startswith(("^", "!")):
            continue
        # Skip very short matches that are unlikely to be chunk IDs
        if len(match) < 3:
            continue
        # Deduplicate while preserving order
        if match not in seen:
            seen.add(match)
            chunk_ids.append(match)

    return chunk_ids


def extract_chunks_from_tool_messages(messages: list) -> tuple[dict[str, CitationChunk], list[DocumentChunkGroup]]:
    """Extract all chunks from rag_query tool messages, preserving document grouping.

    Args:
        messages: List of messages from the state.

    Returns:
        Tuple of (chunk_map, document_groups):
        - chunk_map: Dictionary mapping chunk_id to CitationChunk (for fast lookup)
        - document_groups: List of DocumentChunkGroup (preserves RAG node structure)

    """
    chunk_map: dict[str, CitationChunk] = {}
    document_groups: list[DocumentChunkGroup] = []

    for message in messages:
        # Only process rag_query tool messages (chunks only come from RAG)
        if not isinstance(message, ToolMessage):
            continue
        if not hasattr(message, "name") or message.name != "rag_query":
            continue

        # Get RAG result from artifact (list[dict] or list[ContextDocumentModel])
        # LangGraph stores tool return values in artifact field
        # Fallback to content if artifact not available (handles older versions gracefully)
        artifact = message.artifact if hasattr(message, "artifact") else None
        if not artifact:
            # Fallback: try parsing content (JSON string representation)
            content = message.content if hasattr(message, "content") else None
            if content and isinstance(content, str):
                try:
                    import json

                    artifact = json.loads(content)
                    logger.debug("Using content fallback (artifact not available)")
                except (json.JSONDecodeError, TypeError):
                    logger.warning("rag_query has no artifact and content is not valid JSON")
                    continue
            else:
                logger.warning("rag_query tool message has no artifact or parseable content")
                continue

        # Artifact should be list from rag_query return
        if not isinstance(artifact, list):
            logger.warning("rag_query artifact is not a list: %s", type(artifact))
            continue

        for doc_data in artifact:
            # Handle both dict (common) and ContextDocumentModel objects (if present)
            if isinstance(doc_data, ContextDocumentModel):
                # Already an object (rare but possible)
                doc = doc_data
            elif isinstance(doc_data, dict):
                # Parse from dict (common case - RAG returns JSON)
                try:
                    doc = ContextDocumentModel.model_validate(doc_data)
                except Exception as e:
                    logger.warning("Failed to parse document dict: %s", e)
                    continue
            else:
                logger.warning("Expected dict or ContextDocumentModel, got %s", type(doc_data))
                continue

            # Convert to DocumentChunkGroup
            citation_chunks = [
                CitationChunk(
                    chunk=chunk,
                    document_id=doc.document_id,
                    workspace_id=doc.workspace_id,
                    metadata=doc.metadata,
                )
                for chunk in doc.chunks
            ]

            # Add to chunk_map for fast lookup
            for citation_chunk in citation_chunks:
                chunk_map[citation_chunk.chunk.chunk_id] = citation_chunk

            # Preserve document grouping only for non-empty unique document/workspace pairs.
            document_id = doc.document_id
            workspace_id = doc.workspace_id
            if document_id:
                # Find existing group or create new
                for group in document_groups:
                    if group.document_id == document_id and group.workspace_id == workspace_id:
                        # Merge new chunks, avoiding duplicates
                        existing_chunk_ids = {c.chunk.chunk_id for c in group.chunks}
                        new_chunks = [c for c in citation_chunks if c.chunk.chunk_id not in existing_chunk_ids]
                        group.chunks.extend(new_chunks)
                        break
                else:
                    # No group found, create new
                    document_groups.append(
                        DocumentChunkGroup(
                            document_id=document_id,
                            workspace_id=workspace_id,
                            chunks=citation_chunks,
                            metadata=doc.metadata,
                        )
                    )

    logger.debug("Extracted %d chunks from RAG across %d documents", len(chunk_map), len(document_groups))
    return chunk_map, document_groups


def build_references_from_citations(
    answer_text: str,
    document_groups: list[DocumentChunkGroup],
) -> list[ReferenceModel]:
    """Build ReferenceModel objects from cited chunk IDs, using preserved document grouping.

    Args:
        answer_text: The formatted answer containing [CHUNK_ID] citations.
        document_groups: Preserved document grouping from RAG node (avoids re-grouping).

    Returns:
        List of ReferenceModel objects grouped by document/workspace.

    """
    # Extract cited chunk IDs from the answer
    cited_chunk_ids = extract_chunk_ids_from_text(answer_text)

    if not cited_chunk_ids:
        return []

    cited_set = set(cited_chunk_ids)

    # Use preserved document groups instead of re-grouping
    references = []
    for doc_group in document_groups:
        # Find cited chunks within this document
        cited_chunks_in_doc = [
            (cited_chunk_ids.index(citation_chunk.chunk.chunk_id) + 1, citation_chunk)
            for citation_chunk in doc_group.chunks
            if citation_chunk.chunk.chunk_id in cited_set
        ]

        if not cited_chunks_in_doc:
            continue

        # Build ReferenceModel for this document
        reference_chunks = [
            ReferenceChunkModel(
                chunk_id=citation_chunk.chunk.chunk_id,
                citation=citation_num,
                content=citation_chunk.chunk.text,
                source="RAG",
                distance=citation_chunk.chunk.distance,
            )
            for citation_num, citation_chunk in cited_chunks_in_doc
        ]

        # Calculate average distance for the reference level (LLM contract compatibility)
        distances = [chunk.distance for chunk in reference_chunks if chunk.distance is not None]
        avg_distance = sum(distances) / len(distances) if distances else None

        reference = ReferenceModel(
            document_id=doc_group.document_id,
            workspace_id=doc_group.workspace_id,
            distance=avg_distance,
            chunks=reference_chunks,
        )
        references.append(reference)

    # Warn about citations not found in any document group
    found_chunk_ids = {chunk.chunk.chunk_id for group in document_groups for chunk in group.chunks}
    for chunk_id in cited_chunk_ids:
        if chunk_id not in found_chunk_ids:
            logger.warning("Citation [%s] not found in retrieved chunks", chunk_id)

    return references


def _remove_chunk_ids_from_answer_text(answer_text: str, chunk_ids_to_remove: list[str]) -> str:
    """Remove unresolved [CHUNK_ID] tokens from answer text.

    The removal is conservative: only citation ids that look like chunk-id tokens are removed,
    which avoids accidentally stripping markdown links such as [link text].
    """
    if not answer_text or not chunk_ids_to_remove:
        return answer_text

    cleaned = answer_text
    removable_ids = [chunk_id for chunk_id in chunk_ids_to_remove if CHUNK_ID_TOKEN_PATTERN.match(chunk_id)]

    for chunk_id in removable_ids:
        cleaned = re.sub(rf"\[{re.escape(chunk_id)}\]", "", cleaned)

    # Keep text readable after token removal.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n\s+", "\n", cleaned)

    return cleaned.strip()


def replace_chunk_ids_with_citation_numbers(
    answer_text: str,
    references: list[ReferenceModel | dict],
) -> str:
    """Replace inline [CHUNK_ID] tokens with [citation_number] using resolved references.

    Groups consecutive identical chunk IDs and keeps only the last occurrence before replacement.
    Example: sentence1[chunk_1], sentence2[chunk_1] -> sentence1, sentence2[1]
    """
    if not answer_text or not references:
        return answer_text

    citation_by_chunk_id: dict[str, int] = {}

    for reference in references:
        chunks = reference.chunks if isinstance(reference, ReferenceModel) else reference.get("chunks", [])

        for chunk in chunks:
            if isinstance(chunk, ReferenceChunkModel):
                chunk_id = chunk.chunk_id
                citation = chunk.citation
            else:
                chunk_id = chunk.get("chunkID") or chunk.get("chunk_id")
                citation = chunk.get("citation")

            if chunk_id and citation is not None:
                citation_by_chunk_id[str(chunk_id)] = int(citation)

    if not citation_by_chunk_id:
        return answer_text

    # Drop consecutive duplicate [CHUNK_ID] tokens, keeping only the last one in each run.
    matches = list(CITATION_PATTERN.finditer(answer_text))
    if len(matches) < 2:
        result = answer_text
    else:
        parts: list[str] = []
        cursor = 0
        for i in range(len(matches) - 1):
            m = matches[i]
            if m.group(1) == matches[i + 1].group(1):
                parts.append(answer_text[cursor : m.start()])
                cursor = m.end()

        if cursor == 0:
            result = answer_text  # nothing removed
        else:
            parts.append(answer_text[cursor:])
            result = "".join(parts)

    # Replace remaining chunk IDs with citation numbers
    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        citation = citation_by_chunk_id.get(token)
        if citation is None:
            return match.group(0)
        return f"[{citation}]"

    return CITATION_PATTERN.sub(_replace, result)


def extract_citations_from_state(state: StateModel) -> list[ReferenceModel]:
    """Extract citations from the state and build references.

    It extracts [CHUNK_ID] citations from the last AI message and
    looks them up in the tool messages to build proper references.

    Args:
        state: The current state containing messages.

    Returns:
        List of ReferenceModel objects for the cited chunks.

    """
    if not state.messages:
        logger.debug("No messages in state")
        return []

    # Get the last message (should be the formatted answer)
    last_message = state.messages[-1]
    answer_text = last_message.text if hasattr(last_message, "text") else str(last_message.content)

    if not answer_text:
        logger.debug("Last message has no text content")
        return []

    # Extract cited chunk IDs from the answer
    cited_chunk_ids = extract_chunk_ids_from_text(answer_text)
    logger.debug("Found %d cited chunk IDs in answer: %s", len(cited_chunk_ids), cited_chunk_ids[:5])

    if not cited_chunk_ids:
        logger.debug("No citations found in answer text")
        return []

    # Extract chunks from all tool messages (preserves document grouping)
    chunk_map, document_groups = extract_chunks_from_tool_messages(state.messages)

    if not chunk_map:
        logger.warning("No chunks found in tool messages. Unable to resolve citations.")
        cleaned_text = _remove_chunk_ids_from_answer_text(answer_text, cited_chunk_ids)
        if cleaned_text != answer_text and hasattr(last_message, "content"):
            last_message.content = cleaned_text
            logger.info("Removed %d unresolved citations from answer text", len(cited_chunk_ids))
        return []

    logger.debug("Chunk map keys: %s", list(chunk_map.keys())[:5])

    # Remove citations that cannot be resolved against retrieved chunks.
    unresolved_chunk_ids = [chunk_id for chunk_id in cited_chunk_ids if chunk_id not in chunk_map]
    if unresolved_chunk_ids:
        cleaned_text = _remove_chunk_ids_from_answer_text(answer_text, unresolved_chunk_ids)
        if cleaned_text != answer_text and hasattr(last_message, "content"):
            last_message.content = cleaned_text
            answer_text = cleaned_text
            logger.info("Removed %d unresolved citations from answer text", len(unresolved_chunk_ids))

    # Build references from citations using preserved document groups
    references = build_references_from_citations(answer_text, document_groups)

    logger.info("Extracted %d references from %d cited chunks", len(references), len(cited_chunk_ids))

    return references
