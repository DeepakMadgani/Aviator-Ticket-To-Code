"""DevTools service for verifying document embeddings."""

from aviator.database.pg_client import fetch_one
from aviator.settings import settings
from aviator.vector_store.schema import quote_ident


async def get_document_chunk_count(schema_name: str, document_id: str) -> int:
    """Return the number of embedding chunks stored for a given document ID."""

    table = settings.vector_store_table_name
    query = f"SELECT COUNT(*) AS cnt FROM {quote_ident(schema_name)}.{quote_ident(table)} WHERE document_id = %s"

    row = await fetch_one(query, (document_id,))
    return int(row["cnt"]) if row else 0


async def check_document_summary_exists(schema_name: str, document_id: str) -> bool:
    """Check if a document summary exists for a given document ID."""

    table = settings.summary_table_name
    query = f"SELECT 1 FROM {schema_name}.{table} WHERE document_id = %s LIMIT 1"

    row = await fetch_one(query, (document_id,))
    return row is not None
