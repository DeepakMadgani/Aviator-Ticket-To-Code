# Halfvec Implementation Guide

## Overview

Two storage modes for embedding vectors in PostgreSQL with pgvector:

| Mode | Column Type | Index Ops | Storage | Recall | Complexity |
|---|---|---|---|---|---|
| **`vector`** (default) | `vector(N)` float32 | `vector_cosine_ops` | 100% (baseline) | Full precision | Low |
| **`halfvec`** | `halfvec(N)` float16 | `halfvec_cosine_ops` | **50%** (table + index) | ~98-99% recall@10 | Medium |

## Changes Made

### Settings (`src/aviator/settings.py`)

```python
vector_storage_type: Literal["vector", "halfvec"] = Field(
    default="vector",
    description="Storage mode: 'vector' (float32, full precision) or 'halfvec' (float16, 50% smaller, slight recall loss)."
)
```

### Adapter (`src/aviator/vector_store/adapters/pgvector.py`)

**New methods:**
- `_get_existing_embedding_type()`: Queries `pg_type.typname` from PostgreSQL catalog to detect actual column type
- `_validate_embedding_type()`: Prevents silent misconfiguration when switching modes on existing tables
- `_create_halfvec_table()`: Manual DDL for halfvec column (langchain-postgres doesn't natively support it)

**Updated methods:**
- `_create_indexes()`: Selects correct operator class (`vector_cosine_ops` vs `halfvec_cosine_ops`) based on mode
- `_init_table()`: Branches on mode — halfvec uses manual DDL, vector uses langchain-postgres

## How It Works

### Mode 1: Vector (Default)

```
VECTOR_STORAGE_TYPE=vector

┌──────────────────────────────────────┐
│ Table: embedding vector(768)         │  ← float32, 4 bytes/dim
│ Index: HNSW (vector_cosine_ops)      │  ← full precision ANN
│ Query: ORDER BY embedding <=> $1     │  ← index used ✅
└──────────────────────────────────────┘
```

### Mode 2: Halfvec

```
VECTOR_STORAGE_TYPE=halfvec

┌──────────────────────────────────────┐
│ Table: embedding halfvec(768)        │  ← float16, 2 bytes/dim
│ Index: HNSW (halfvec_cosine_ops)     │  ← half precision ANN
│ Query: ORDER BY embedding <=> $1     │  ← index used ✅
└──────────────────────────────────────┘
```

## Why Not Hybrid Mode?

A "hybrid" approach (float32 storage + float16 index) was evaluated and rejected due to
**implementation complexity that outweighs the marginal benefit over pure halfvec**.

### What hybrid would require

To properly implement hybrid mode with LangChain + pgvector, you would need **four interrelated database objects**:

1. **Expression index on the base table:**
   ```sql
   CREATE INDEX ON table USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops)
   ```

2. **A regular view** to rewrite LangChain's queries so the expression matches the index:
   ```sql
   CREATE VIEW table_halfvec_view AS
   SELECT langchain_id, embedding::halfvec(768) as embedding, ... FROM table;
   ```
   *(Note: You **cannot** create an index directly on a regular view — only on tables or materialized views. The expression index lives on the base table, the view rewrites queries to match it.)*

3. **An `INSTEAD OF INSERT` trigger** on the view, because the cast expression makes the view non-updatable:
   ```sql
   CREATE TRIGGER view_insert INSTEAD OF INSERT ON table_halfvec_view
   FOR EACH ROW EXECUTE FUNCTION redirect_insert_to_base_table();
   ```

4. **PGVectorStore pointed at the view** for both reads and writes.

### Why this was rejected

| Concern | Assessment |
|---|---|
| **View latency** | ❌ Not a real concern — PostgreSQL views are macro-expansions with zero runtime overhead |
| **Implementation complexity** | ✅ Real concern — 4 interdependent DB objects (table, expression index, view, trigger) |
| **Marginal benefit** | Hybrid saves ~0.5-1% recall over pure halfvec, at significant implementation cost |
| **Maintenance burden** | Trigger must stay in sync with table schema; harder to debug and monitor |
| **LangChain compatibility** | Requires non-standard patterns (view as query surface, trigger for writes) |

**Bottom line:** Pure halfvec gives you 50% savings on *both* storage and index with ~1-2% recall loss.
Hybrid would give 50% savings on index only (storage stays 100%), with ~0.5% recall loss.
The marginal recall improvement doesn't justify the complexity.

## Configuration

### Environment Variables

```bash
VECTOR_STORAGE_TYPE=vector           # One of: vector, halfvec
VECTOR_INDEX_TYPE=hnsw              # One of: hnsw, ivfflat
HNSW_M=16                            # Links per node (default: 16, range: 5-64)
HNSW_EF_CONSTRUCTION=64              # Build depth (default: 64, range: 10-500)
```

Advanced IVFFlat overrides:

```bash
IVFFLAT_LISTS=                        # Optional override; default is floor(sqrt(total_chunks))
IVFFLAT_PROBES=                       # Optional override; default is floor(sqrt(lists))
```

`VECTOR_INDEX_TYPE` controls which ANN index Aviator manages for the `embedding` column.
On startup, the application now reconciles index drift as well as column type drift: if the
existing embedding index uses the wrong access method, operator class, or managed parameters,
it is dropped and recreated with the configured definition.
When `VECTOR_INDEX_TYPE=ivfflat`, Aviator derives the managed defaults from the current embedding
row count when explicit overrides are not provided:

- `lists = floor(sqrt(total_chunks))`
- `probes = floor(sqrt(lists))`

## Migration

### From Vector → Halfvec

**Option 1: Clean Start** (Recommended for new deployments)
1. Set `VECTOR_STORAGE_TYPE=halfvec` before first startup
2. Application auto-creates `halfvec` table
3. Re-embed all documents

**Option 2: In-Place Migration** (Existing data, automatic)

On startup the application now reconciles an existing `embedding` column with the configured
`VECTOR_STORAGE_TYPE`. When it detects a mismatch, it:

1. Drops any indexes on `embedding`
2. Alters the column type to `halfvec(N)`
3. Recreates the configured ANN index with `halfvec_cosine_ops`

This preserves existing rows but still requires a table lock while the column type changes.

Manual SQL is only needed if you want to perform the migration ahead of the application restart.

```sql
-- 1. Convert column type (locks table, may take time)
ALTER TABLE public.aviator 
ALTER COLUMN embedding TYPE halfvec(768) USING embedding::halfvec(768);

-- 2. Drop old index
DROP INDEX idx_aviator_embedding_hnsw;

-- 3. Restart application with VECTOR_STORAGE_TYPE=halfvec
-- Application will recreate the configured ANN index with halfvec_cosine_ops
```

**Option 3: Zero-Downtime** (Parallel table)
1. Create new halfvec table alongside existing
2. Copy and convert data in batches
3. Switch application to new table
4. Drop old table

### From Halfvec → Vector

Startup handles this conversion automatically as well: it drops the existing `embedding` index,
alters the column back to `vector(N)`, then recreates the HNSW index with `vector_cosine_ops`.

```sql
-- 1. Convert column type
ALTER TABLE public.aviator 
ALTER COLUMN embedding TYPE vector(768);

-- 2. Drop old index
DROP INDEX idx_aviator_embedding_hnsw;

-- 3. Restart application with VECTOR_STORAGE_TYPE=vector
```

## Validation & Safety

### Type Mismatch Detection

If you switch `VECTOR_STORAGE_TYPE` on an existing table, the application **refuses to start**:

```
FATAL: Embedding column type mismatch for table 'aviator'.
  Existing column type: vector
  Configured mode:      halfvec (expects halfvec)

To resolve this issue:
  1. Drop the table: DROP TABLE public.aviator CASCADE;
  2. OR change VECTOR_STORAGE_TYPE to match the existing table
```

This prevents silent misconfiguration where the table stays float32 while config says halfvec.

### Dimension Validation

Both modes validate embedding dimensions at startup:
- If `VECTOR_SIZE` (768) doesn't match existing table, startup fails
- Dimensions must be set consistently across all deployments

## Performance Characteristics

### Storage (per dimension)

| Mode | Bytes/dim | Per 768-dim vector | Per 1M vectors | Savings |
|---|---|---|---|---|
| vector (float32) | 4 | 3,072 bytes | ~3 GB | baseline |
| halfvec (float16) | 2 | 1,536 bytes | ~1.5 GB | **50%** |

### Index Size (HNSW)

| Mode | Index Ops | Relative Size |
|---|---|---|
| vector | `vector_cosine_ops` | 100% (baseline) |
| halfvec | `halfvec_cosine_ops` | ~50% |

### Recall Quality

| Mode | Recall@10 | Notes |
|---|---|---|
| vector | ~100% | Full float32 precision |
| halfvec | ~98-99% | Float16 truncation at insert time; precision loss is permanent |

**Note:** Float32 → Float16 truncation happens at INSERT time. Original precision is lost and cannot be recovered.

## Troubleshooting

### "Extension vector not found"

```
CREATE EXTENSION IF NOT EXISTS vector;
```
Ensure pgvector is installed in PostgreSQL:
```bash
psql -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Index not being used in queries

Check index existence:
```sql
SELECT * FROM pg_indexes 
WHERE schemaname = 'public' AND tablename = 'aviator';
```

Verify query plan:
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM aviator 
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
LIMIT 10;
```

### Type mismatch on startup

Compare configured mode with actual table:
```sql
SELECT t.typname
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_type t ON a.atttypid = t.oid
WHERE n.nspname = 'public' AND c.relname = 'aviator' AND a.attname = 'embedding';
```

## References

- [pgvector Documentation](https://github.com/pgvector/pgvector)
- [PostgreSQL Type System](https://www.postgresql.org/docs/current/sql-createtype.html)
- [HNSW Index Parameters](https://github.com/pgvector/pgvector#hnsw)
- [LangChain PGVectorStore](https://python.langchain.com/docs/integrations/vectorstores/pgvector)
