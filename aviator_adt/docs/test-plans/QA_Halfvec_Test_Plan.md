# QA Test Plan — Vector Storage and ANN Index Management

**Feature:** Configurable embedding storage type (`vector` / `halfvec`) and ANN index type (`hnsw` / `ivfflat`)
**Component:** `aviator_adt` — PGVector Store Adapter
**Date:** March 2026

---

## 1. Feature Summary

`VECTOR_STORAGE_TYPE` controls how embedding vectors are stored in PostgreSQL:

| Mode | Column Type | Index Operator | Precision | Storage |
|---|---|---|---|---|
| `vector` (default) | `vector(768)` — float32 | `vector_cosine_ops` | Full | 100% |
| `halfvec` | `halfvec(768)` — float16 | `halfvec_cosine_ops` | Slight loss | ~50% |

`VECTOR_INDEX_TYPE` controls which ANN index Aviator manages for the `embedding` column:

| Mode | Access Method | Managed Parameters | Default Behavior |
|---|---|---|---|
| `hnsw` (default) | `USING hnsw` | `m`, `ef_construction` | Uses configured HNSW settings |
| `ivfflat` | `USING ivfflat` | `lists`, query-time `probes` | Derives `lists = floor(sqrt(total_chunks))` and `probes = floor(sqrt(lists))` unless overridden |

On startup, Aviator now reconciles both column-type drift and index-definition drift:

- If the `embedding` column type does not match `VECTOR_STORAGE_TYPE`, the application drops embedding indexes, alters the column type, and recreates the configured ANN index.
- If the existing embedding index uses the wrong access method, operator class, or managed parameters, the application drops and recreates that index.

**Changed files:**
- `src/aviator/settings.py` — storage/index settings
- `src/aviator/vector_store/adapters/pgvector.py` — table creation, migration, index reconciliation
- `helm/aviator/values.yaml` — config surface
- `docs/halfvec-implementation.md` — operator documentation
- `tests/vector_store/adapters/test_pgvector.py` — unit coverage for migration and IVFFlat behavior

---

## 2. Prerequisites

| Item | Details |
|---|---|
| Docker + Docker Compose | Required for PostgreSQL (pgvector) and RabbitMQ |
| `uv` package manager | For Python dependency management |
| GCP credentials | `otl-cs-csai.json` in project root if using Google services |
| Local environment variables | Set through shell, `.env`, or `run_aviator.bat` |

### Start Infrastructure

Run from the repository root:

```bash
docker compose up -d db rabbitmq
```

### Verify Database

```bash
docker compose exec db psql -U postgres -d postgres -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

Expected: pgvector extension is installed and returns a version string.

---

## 3. Environment Variable Reference

| Variable | Values | Default | Notes |
|---|---|---|---|
| `VECTOR_STORAGE_TYPE` | `vector`, `halfvec` | `vector` | Controls embedding column type |
| `VECTOR_INDEX_TYPE` | `hnsw`, `ivfflat` | `hnsw` | Controls managed ANN index type |
| `VECTOR_STORE` | `pgvectorstore`, `memory` | `pgvectorstore` in DB-backed runs | Must be `pgvectorstore` for DB tests |
| `VECTOR_SIZE` | integer | `768` | Must match embedding model output |
| `HNSW_M` | 5–64 | `24` | HNSW links per node |
| `HNSW_EF_CONSTRUCTION` | 10–500 | `200` | HNSW build depth |
| `IVFFLAT_LISTS` | integer, empty | derived | Advanced override for IVFFlat list count |
| `IVFFLAT_PROBES` | integer, empty | derived | Advanced override for query-time probed lists |

---

## 4. Test Cases

### TC-01: Default Mode — Vector + HNSW

**Objective:** Verify default startup creates a `vector` type table with an HNSW index.

**Steps:**
1. Ensure no existing `aviator` table:
   ```bash
   docker compose exec db psql -U postgres -d postgres -c "DROP TABLE IF EXISTS public.aviator CASCADE;"
   ```
2. Set environment:
   ```
   VECTOR_STORE=pgvectorstore
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=hnsw
   ```
3. Start the application.
4. Verify column type:
   ```sql
   SELECT t.typname
   FROM pg_attribute a
   JOIN pg_class c ON a.attrelid = c.oid
   JOIN pg_namespace n ON c.relnamespace = n.oid
   JOIN pg_type t ON a.atttypid = t.oid
   WHERE n.nspname = 'public' AND c.relname = 'aviator' AND a.attname = 'embedding';
   ```
5. Verify indexes:
   ```sql
   SELECT indexname, indexdef FROM pg_indexes
   WHERE schemaname = 'public' AND tablename = 'aviator';
   ```

**Expected:**
- Column type: `vector`
- Embedding index uses `USING hnsw`
- Embedding index definition contains `vector_cosine_ops`
- GIN index on metadata column exists
- BTREE indexes on `workspace_id` and `document_id` exist

---

### TC-02: Halfvec Mode — Fresh Table

**Objective:** Verify halfvec startup creates a `halfvec` type table.

**Steps:**
1. Drop existing table:
   ```bash
   docker compose exec db psql -U postgres -d postgres -c "DROP TABLE IF EXISTS public.aviator CASCADE;"
   ```
2. Set environment:
   ```
   VECTOR_STORE=pgvectorstore
   VECTOR_STORAGE_TYPE=halfvec
   VECTOR_INDEX_TYPE=hnsw
   ```
3. Start the application.
4. Verify column type and indexes using the same SQL as TC-01.

**Expected:**
- Column type: `halfvec`
- Embedding index uses `USING hnsw`
- Embedding index definition contains `halfvec_cosine_ops`
- GIN and BTREE indexes exist on the same table

---

### TC-03: In-Place Type Migration — vector → halfvec

**Objective:** Verify application automatically migrates an existing `vector` table to `halfvec`.

**Steps:**
1. Complete TC-01 so the table exists as `vector`.
2. Change environment to:
   ```
   VECTOR_STORAGE_TYPE=halfvec
   VECTOR_INDEX_TYPE=hnsw
   ```
3. Start the application.
4. Verify column type and index definition.
5. Verify existing row count remains unchanged.

**Expected:**
- Application starts successfully
- Column type changes to `halfvec`
- Embedding ANN index is recreated with `halfvec_cosine_ops`
- Existing rows remain present after startup

---

### TC-04: In-Place Type Migration — halfvec → vector

**Objective:** Verify application automatically migrates an existing `halfvec` table back to `vector`.

**Steps:**
1. Complete TC-02 so the table exists as `halfvec`.
2. Change environment to:
   ```
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=hnsw
   ```
3. Start the application.
4. Verify column type and index definition.

**Expected:**
- Application starts successfully
- Column type changes to `vector`
- Embedding ANN index is recreated with `vector_cosine_ops`

---

### TC-05: Dimension Mismatch Guard

**Objective:** Verify application refuses to start when vector dimension changes.

**Steps:**
1. Complete TC-01 or TC-02 so the table exists with dimension 768.
2. Change `VECTOR_SIZE=1536`.
3. Start the application.

**Expected:**
- Application fails to start with an error containing:
  ```
  FATAL: Vector dimension mismatch for table 'aviator'.
    Existing table: 768 dimensions
    Configuration:  1536 dimensions
  ```

---

### TC-06: Embed + Retrieve — Vector Mode

**Objective:** End-to-end embed and chat flow with vector mode.

**Steps:**
1. Complete TC-01 setup.
2. Send embedding request:
   ```bash
   curl -X POST http://localhost:3000/v1/embeddings \
     -H "Content-Type: application/json" \
     -H "auth-ticket: test-ticket" \
     -d '{
       "metadata": {"workspace_id": "qa-test", "document_id": "doc-001"},
       "content": "The capital of France is Paris. It is known for the Eiffel Tower."
     }'
   ```
3. Wait for the worker to process the embedding.
4. Send a chat request asking for the capital of France.

**Expected:**
- Embedding request returns `202 Accepted`
- Chat response references Paris or Eiffel Tower
- Response includes relevant context or references

---

### TC-07: Embed + Retrieve — Halfvec Mode

**Objective:** Same as TC-06 but with halfvec mode.

**Steps:**
1. Complete TC-02 setup.
2. Repeat the embed + chat steps from TC-06.

**Expected:**
- Same functional behavior as TC-06
- Chat response is relevant and accurate
- No application or worker errors related to vector storage mode

---

### TC-08: HNSW Parameter Customization

**Objective:** Verify custom HNSW parameters are applied.

**Steps:**
1. Drop table.
2. Set:
   ```
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=hnsw
   HNSW_M=32
   HNSW_EF_CONSTRUCTION=128
   ```
3. Start the application.
4. Check the embedding index definition:
   ```sql
   SELECT indexdef FROM pg_indexes
   WHERE schemaname = 'public' AND tablename = 'aviator'
   AND indexname LIKE '%embedding%';
   ```

**Expected:**
- Index definition contains `WITH (m = 32, ef_construction = 128)` or PostgreSQL-equivalent formatting

---

### TC-09: IVFFlat Fresh Startup

**Objective:** Verify IVFFlat startup creates an IVFFlat embedding index instead of HNSW.

**Steps:**
1. Drop existing table:
   ```bash
   docker compose exec db psql -U postgres -d postgres -c "DROP TABLE IF EXISTS public.aviator CASCADE;"
   ```
2. Set:
   ```
   VECTOR_STORE=pgvectorstore
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=ivfflat
   IVFFLAT_LISTS=64
   IVFFLAT_PROBES=8
   ```
3. Start the application.
4. Check indexes:
   ```sql
   SELECT indexname, indexdef FROM pg_indexes
   WHERE schemaname = 'public' AND tablename = 'aviator';
   ```

**Expected:**
- Embedding index uses `USING ivfflat`
- Index definition contains the correct operator class for the storage mode
- Index definition includes `lists = 64` or equivalent PostgreSQL rendering

---

### TC-10: IVFFlat Derived Defaults

**Objective:** Verify IVFFlat derives `lists` and `probes` when overrides are omitted.

**Steps:**
1. Drop the existing table.
2. Start with:
   ```
   VECTOR_STORE=pgvectorstore
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=ivfflat
   IVFFLAT_LISTS=
   IVFFLAT_PROBES=
   ```
3. Insert or embed enough content to reach a known row count, for example 10,000 rows.
4. Restart the application.
5. Inspect index reloptions:
   ```sql
   SELECT idx.relname, idx.reloptions
   FROM pg_class idx
   JOIN pg_namespace ns ON idx.relnamespace = ns.oid
   WHERE ns.nspname = 'public'
   AND idx.relname = 'idx_aviator_embedding_hnsw';
   ```

**Expected:**
- For 10,000 rows, IVFFlat index uses `lists=100`
- Query-time PostgreSQL options include `ivfflat.probes=10`
- No explicit override is required to get the derived values

---

### TC-11: Index Drift Reconciliation

**Objective:** Verify startup rebuilds a drifted embedding index to match the configured ANN mode and parameters.

**Steps:**
1. Start once with:
   ```
   VECTOR_STORE=pgvectorstore
   VECTOR_STORAGE_TYPE=vector
   VECTOR_INDEX_TYPE=hnsw
   HNSW_M=24
   HNSW_EF_CONSTRUCTION=200
   ```
2. Manually replace the embedding index in PostgreSQL with a mismatched definition, such as IVFFlat or an HNSW index with different parameters.
3. Restart the application with the original configuration unchanged.
4. Check the embedding index definition and reloptions.

**Expected:**
- Startup drops the drifted embedding index
- Startup recreates exactly one managed embedding index
- Recreated index matches the configured access method, operator class, and parameters

---

### TC-12: Idempotent Restart

**Objective:** Verify restarting with the same config does not error or recreate incompatible objects.

**Steps:**
1. Complete TC-01, TC-02, or TC-09.
2. Stop the application.
3. Restart with the same environment variables.

**Expected:**
- Application starts successfully
- No duplicate tables or duplicate managed indexes are created
- Existing data is preserved

---

### TC-13: Unit Tests Pass

**Objective:** Automated regression tests pass.

**Steps:**
```bash
uv run pytest tests/vector_store/adapters/test_pgvector.py -v
```

**Expected:**
- All tests pass, including:
  - `test_migrate_embedding_type_drops_indexes_and_converts_to_halfvec`
  - `test_migrate_embedding_type_drops_indexes_and_converts_to_vector`
  - `test_migrate_embedding_type_preserves_ivfflat_lists`
  - `test_create_indexes_uses_halfvec_opclass`
  - `test_create_indexes_uses_ivfflat_with_preserved_lists`
  - `test_get_configured_ivfflat_lists_derives_from_total_chunks`
  - `test_get_effective_ivfflat_probes_derives_from_lists`
  - `test_reconcile_embedding_index_drops_mismatched_indexes`

---

### TC-14: A/B Benchmark

**Objective:** Quantitative comparison of vector vs halfvec accuracy and performance.

**Steps:**
```bash
uv run python tests/scripts/benchmark_halfvec_ab.py \
  --connection "postgresql://postgres:postgres@localhost:5432/postgres" \
  --recreate --rows 5000 --queries 200 --dims 768 --k 10
```

**Expected (approximate):**

| Metric | Acceptable Range |
|---|---|
| Recall@10 (halfvec) | ≥ 0.95 |
| Recall delta (halfvec − vector) | ≥ −0.03 |
| P95 latency delta | ≤ +10% |
| Table size reduction | ~45–55% |
| Index size reduction | ~45–55% |

Results are written to `results/halfvec_benchmark_summary.csv`.

---

## 5. Verification SQL Cheat Sheet

Run inside `docker compose exec db psql -U postgres -d postgres`:

```sql
-- Check column type
SELECT t.typname
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_type t ON a.atttypid = t.oid
WHERE n.nspname = 'public' AND c.relname = 'aviator' AND a.attname = 'embedding';

-- Check all indexes
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'aviator';

-- Check access method and reloptions for the embedding index
SELECT idx.relname, am.amname, idx.reloptions, pg_get_indexdef(idx.oid)
FROM pg_class idx
JOIN pg_namespace ns ON idx.relnamespace = ns.oid
JOIN pg_am am ON idx.relam = am.oid
WHERE ns.nspname = 'public'
AND idx.relname = 'idx_aviator_embedding_hnsw';

-- Check table size
SELECT pg_size_pretty(pg_total_relation_size('public.aviator'));

-- Check index sizes
SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass))
FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'aviator';

-- Count rows
SELECT count(*) FROM public.aviator;

-- Sample a row
SELECT langchain_id, left(content, 80) FROM public.aviator LIMIT 3;
```

---

## 6. Pass / Fail Criteria

| # | Test Case | Pass Criteria |
|---|---|---|
| TC-01 | Vector + HNSW fresh table | Column = `vector`, embedding index = `USING hnsw` |
| TC-02 | Halfvec fresh table | Column = `halfvec`, embedding index = `halfvec_cosine_ops` |
| TC-03 | Type migration (v→h) | Startup succeeds and column/index are migrated |
| TC-04 | Type migration (h→v) | Startup succeeds and column/index are migrated |
| TC-05 | Dimension mismatch | Startup fails with clear error |
| TC-06 | E2E embed+chat (vector) | Relevant answer returned |
| TC-07 | E2E embed+chat (halfvec) | Relevant answer returned |
| TC-08 | Custom HNSW params | Index reflects custom `m`/`ef_construction` |
| TC-09 | IVFFlat fresh startup | Embedding index uses `USING ivfflat` |
| TC-10 | IVFFlat derived defaults | Derived `lists` and `probes` are applied |
| TC-11 | Index drift reconciliation | Startup rebuilds mismatched embedding index |
| TC-12 | Idempotent restart | No errors, data preserved |
| TC-13 | Unit tests | Current PGVector adapter test suite passes |
| TC-14 | A/B benchmark | Recall ≥ 0.95, storage ~50% reduction |

---

## 7. Known Limitations

- Type changes still require a table lock during `ALTER COLUMN`, so startup migration can be slow on large tables.
- `halfvec` truncation (float32 → float16) is permanent at insert time; original precision cannot be recovered.
- IVFFlat derived defaults depend on the current embedding-row count, so tiny or partially loaded datasets may not represent production tuning.
- The benchmark script uses synthetic random vectors; real-world recall may differ slightly.
