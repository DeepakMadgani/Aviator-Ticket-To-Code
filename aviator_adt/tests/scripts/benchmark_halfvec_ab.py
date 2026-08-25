"""A/B benchmark for vector vs halfvec retrieval quality and latency.

This script creates two tables with identical embeddings:
- <prefix>_vector: embedding vector(N)
- <prefix>_halfvec: embedding halfvec(N)

It then compares ANN query latency and recall@k against an exact float32 baseline.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)


@dataclass
class QueryMetrics:
    """Per-query measurement container."""

    recall_vector: float
    recall_halfvec: float
    overlap_halfvec_vs_vector: float
    latency_vector_ms: float
    latency_halfvec_ms: float
    latency_exact_ms: float


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _random_unit_vector(rng: random.Random, dims: int) -> list[float]:
    vec = [rng.uniform(-1.0, 1.0) for _ in range(dims)]
    return _normalize(vec)


def _to_vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values_sorted = sorted(values)
    idx = int((len(values_sorted) - 1) * pct)
    return values_sorted[idx]


def _conn_string_from_env() -> str:
    env_conn = os.getenv("POSTGRES_CONNECTION")
    if env_conn:
        return env_conn

    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _create_tables(
    conn: psycopg.Connection,
    schema: str,
    prefix: str,
    dims: int,
    recreate: bool,
) -> tuple[str, str]:
    vector_table = f"{prefix}_vector"
    halfvec_table = f"{prefix}_halfvec"

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(sql.Identifier(schema)))

        if recreate:
            cur.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE;").format(
                    sql.Identifier(schema), sql.Identifier(vector_table)
                )
            )
            cur.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE;").format(
                    sql.Identifier(schema), sql.Identifier(halfvec_table)
                )
            )

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.{} (
                    id BIGINT PRIMARY KEY,
                    embedding vector({}) NOT NULL
                );
                """
            ).format(sql.Identifier(schema), sql.Identifier(vector_table), sql.SQL(str(dims)))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.{} (
                    id BIGINT PRIMARY KEY,
                    embedding halfvec({}) NOT NULL
                );
                """
            ).format(sql.Identifier(schema), sql.Identifier(halfvec_table), sql.SQL(str(dims)))
        )

    return vector_table, halfvec_table


def _load_embeddings(
    conn: psycopg.Connection,
    schema: str,
    vector_table: str,
    halfvec_table: str,
    vectors: list[list[float]],
) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("TRUNCATE TABLE {}.{};").format(sql.Identifier(schema), sql.Identifier(vector_table)))
        cur.execute(sql.SQL("TRUNCATE TABLE {}.{};").format(sql.Identifier(schema), sql.Identifier(halfvec_table)))

        rows_vector = [(i, _to_vector_literal(vec)) for i, vec in enumerate(vectors)]

        cur.executemany(
            sql.SQL("INSERT INTO {}.{} (id, embedding) VALUES (%s, %s::vector)").format(
                sql.Identifier(schema), sql.Identifier(vector_table)
            ),
            rows_vector,
        )
        cur.executemany(
            sql.SQL("INSERT INTO {}.{} (id, embedding) VALUES (%s, %s::halfvec)").format(
                sql.Identifier(schema), sql.Identifier(halfvec_table)
            ),
            rows_vector,
        )


def _create_indexes(
    conn: psycopg.Connection,
    schema: str,
    vector_table: str,
    halfvec_table: str,
    hnsw_m: int,
    hnsw_ef_construction: int,
) -> tuple[str, str]:
    vector_index = f"idx_{vector_table}_embedding"
    halfvec_index = f"idx_{halfvec_table}_embedding"

    with conn.cursor() as cur:
        cur.execute(sql.SQL("DROP INDEX IF EXISTS {}.{};").format(sql.Identifier(schema), sql.Identifier(vector_index)))
        cur.execute(
            sql.SQL("DROP INDEX IF EXISTS {}.{};").format(sql.Identifier(schema), sql.Identifier(halfvec_index))
        )

        cur.execute(
            sql.SQL(
                """
                CREATE INDEX {} ON {}.{}
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = {}, ef_construction = {});
                """
            ).format(
                sql.Identifier(vector_index),
                sql.Identifier(schema),
                sql.Identifier(vector_table),
                sql.SQL(str(hnsw_m)),
                sql.SQL(str(hnsw_ef_construction)),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE INDEX {} ON {}.{}
                USING hnsw (embedding halfvec_cosine_ops)
                WITH (m = {}, ef_construction = {});
                """
            ).format(
                sql.Identifier(halfvec_index),
                sql.Identifier(schema),
                sql.Identifier(halfvec_table),
                sql.SQL(str(hnsw_m)),
                sql.SQL(str(hnsw_ef_construction)),
            )
        )
        cur.execute(sql.SQL("ANALYZE {}.{};").format(sql.Identifier(schema), sql.Identifier(vector_table)))
        cur.execute(sql.SQL("ANALYZE {}.{};").format(sql.Identifier(schema), sql.Identifier(halfvec_table)))

    return vector_index, halfvec_index


def _query_topk(
    cur,
    schema: str,
    table: str,
    query_vec: str,
    k: int,
    cast_type: str,
) -> tuple[list[int], float]:
    start = time.perf_counter()
    cur.execute(
        sql.SQL("SELECT id FROM {}.{} ORDER BY embedding <=> (%s::{}) LIMIT %s").format(
            sql.Identifier(schema), sql.Identifier(table), sql.SQL(cast_type)
        ),
        (query_vec, k),
    )
    ids = [row[0] for row in cur.fetchall()]
    latency_ms = (time.perf_counter() - start) * 1000.0
    return ids, latency_ms


def _exact_topk(cur, schema: str, table: str, query_vec: str, k: int) -> tuple[list[int], float]:
    """Exact top-k by disabling index scans in this transaction."""
    start = time.perf_counter()
    cur.execute("BEGIN")
    cur.execute("SET LOCAL enable_indexscan = off")
    cur.execute("SET LOCAL enable_bitmapscan = off")
    cur.execute("SET LOCAL enable_indexonlyscan = off")
    cur.execute(
        sql.SQL("SELECT id FROM {}.{} ORDER BY embedding <=> (%s::vector) LIMIT %s").format(
            sql.Identifier(schema), sql.Identifier(table)
        ),
        (query_vec, k),
    )
    ids = [row[0] for row in cur.fetchall()]
    cur.execute("COMMIT")
    latency_ms = (time.perf_counter() - start) * 1000.0
    return ids, latency_ms


def _collect_sizes(conn: psycopg.Connection, schema: str, table: str, index: str) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size(%s)", (f"{schema}.{table}",))
        table_bytes = int(cur.fetchone()[0])
        cur.execute("SELECT pg_relation_size(%s)", (f"{schema}.{index}",))
        index_bytes = int(cur.fetchone()[0])
    return table_bytes, index_bytes


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def run_benchmark(args: argparse.Namespace) -> Path:
    conn_str = args.connection or _conn_string_from_env()
    rng = random.Random(args.seed)

    vectors = [_random_unit_vector(rng, args.dims) for _ in range(args.rows)]
    queries = [_random_unit_vector(rng, args.dims) for _ in range(args.queries)]

    with psycopg.connect(conn_str, autocommit=True) as conn:
        vector_table, halfvec_table = _create_tables(conn, args.schema, args.prefix, args.dims, args.recreate)
        _load_embeddings(conn, args.schema, vector_table, halfvec_table, vectors)
        vector_index, halfvec_index = _create_indexes(
            conn,
            args.schema,
            vector_table,
            halfvec_table,
            args.hnsw_m,
            args.hnsw_ef_construction,
        )

        metrics: list[QueryMetrics] = []

        with conn.cursor() as cur:
            cur.execute(f"SET hnsw.ef_search = {args.hnsw_ef_search}")

            for q in queries:
                qlit = _to_vector_literal(q)
                exact_ids, exact_ms = _exact_topk(cur, args.schema, vector_table, qlit, args.k)
                vector_ids, vector_ms = _query_topk(cur, args.schema, vector_table, qlit, args.k, "vector")
                halfvec_ids, halfvec_ms = _query_topk(cur, args.schema, halfvec_table, qlit, args.k, "halfvec")

                exact_set = set(exact_ids)
                vector_set = set(vector_ids)
                halfvec_set = set(halfvec_ids)

                recall_vector = len(vector_set.intersection(exact_set)) / args.k
                recall_halfvec = len(halfvec_set.intersection(exact_set)) / args.k
                overlap_halfvec_vs_vector = len(halfvec_set.intersection(vector_set)) / args.k

                metrics.append(
                    QueryMetrics(
                        recall_vector=recall_vector,
                        recall_halfvec=recall_halfvec,
                        overlap_halfvec_vs_vector=overlap_halfvec_vs_vector,
                        latency_vector_ms=vector_ms,
                        latency_halfvec_ms=halfvec_ms,
                        latency_exact_ms=exact_ms,
                    )
                )

        vec_table_bytes, vec_index_bytes = _collect_sizes(conn, args.schema, vector_table, vector_index)
        half_table_bytes, half_index_bytes = _collect_sizes(conn, args.schema, halfvec_table, halfvec_index)

    vec_latencies = [m.latency_vector_ms for m in metrics]
    half_latencies = [m.latency_halfvec_ms for m in metrics]
    exact_latencies = [m.latency_exact_ms for m in metrics]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "timestamp_utc",
        "schema",
        "prefix",
        "rows",
        "dims",
        "queries",
        "k",
        "hnsw_m",
        "hnsw_ef_construction",
        "hnsw_ef_search",
        "recall_vector_mean",
        "recall_halfvec_mean",
        "recall_delta_halfvec_minus_vector",
        "overlap_halfvec_vs_vector_mean",
        "latency_vector_p50_ms",
        "latency_vector_p95_ms",
        "latency_halfvec_p50_ms",
        "latency_halfvec_p95_ms",
        "latency_exact_p50_ms",
        "latency_exact_p95_ms",
        "vector_table_bytes",
        "vector_index_bytes",
        "halfvec_table_bytes",
        "halfvec_index_bytes",
        "table_size_reduction_pct",
        "index_size_reduction_pct",
    ]

    recall_vector_mean = _mean([m.recall_vector for m in metrics])
    recall_halfvec_mean = _mean([m.recall_halfvec for m in metrics])
    overlap_mean = _mean([m.overlap_halfvec_vs_vector for m in metrics])

    table_reduction_pct = (1 - (half_table_bytes / vec_table_bytes)) * 100 if vec_table_bytes else 0.0
    index_reduction_pct = (1 - (half_index_bytes / vec_index_bytes)) * 100 if vec_index_bytes else 0.0

    row = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "schema": args.schema,
        "prefix": args.prefix,
        "rows": args.rows,
        "dims": args.dims,
        "queries": args.queries,
        "k": args.k,
        "hnsw_m": args.hnsw_m,
        "hnsw_ef_construction": args.hnsw_ef_construction,
        "hnsw_ef_search": args.hnsw_ef_search,
        "recall_vector_mean": round(recall_vector_mean, 6),
        "recall_halfvec_mean": round(recall_halfvec_mean, 6),
        "recall_delta_halfvec_minus_vector": round(recall_halfvec_mean - recall_vector_mean, 6),
        "overlap_halfvec_vs_vector_mean": round(overlap_mean, 6),
        "latency_vector_p50_ms": round(_percentile(vec_latencies, 0.50), 3),
        "latency_vector_p95_ms": round(_percentile(vec_latencies, 0.95), 3),
        "latency_halfvec_p50_ms": round(_percentile(half_latencies, 0.50), 3),
        "latency_halfvec_p95_ms": round(_percentile(half_latencies, 0.95), 3),
        "latency_exact_p50_ms": round(_percentile(exact_latencies, 0.50), 3),
        "latency_exact_p95_ms": round(_percentile(exact_latencies, 0.95), 3),
        "vector_table_bytes": vec_table_bytes,
        "vector_index_bytes": vec_index_bytes,
        "halfvec_table_bytes": half_table_bytes,
        "halfvec_index_bytes": half_index_bytes,
        "table_size_reduction_pct": round(table_reduction_pct, 2),
        "index_size_reduction_pct": round(index_reduction_pct, 2),
    }

    write_header = not output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    logger = logging.getLogger(__name__)
    logger.info("Benchmark complete")
    logger.info("Output", extra={"output_path": output_path})
    logger.info("Recall mean (vector)", extra={"recall_vector_mean": row["recall_vector_mean"]})
    logger.info("Recall mean (halfvec)", extra={"recall_halfvec_mean": row["recall_halfvec_mean"]})
    logger.info("P95 latency ms (vector)", extra={"latency_vector_p95_ms": row["latency_vector_p95_ms"]})
    logger.info("P95 latency ms (halfvec)", extra={"latency_halfvec_p95_ms": row["latency_halfvec_p95_ms"]})
    logger.info("Table size reduction (%)", extra={"table_size_reduction_pct": row["table_size_reduction_pct"]})
    logger.info("Index size reduction (%)", extra={"index_size_reduction_pct": row["index_size_reduction_pct"]})

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark vector vs halfvec quality and performance")
    parser.add_argument("--connection", type=str, default=None, help="PostgreSQL connection string")
    parser.add_argument("--schema", type=str, default="public", help="Database schema")
    parser.add_argument("--prefix", type=str, default="aviator_halfvec_bench", help="Benchmark table prefix")
    parser.add_argument("--rows", type=int, default=3000, help="Number of embedding rows")
    parser.add_argument("--dims", type=int, default=768, help="Embedding dimensions")
    parser.add_argument("--queries", type=int, default=100, help="Number of ANN queries")
    parser.add_argument("--k", type=int, default=10, help="Top-k")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--hnsw-m", type=int, default=16, help="HNSW m parameter")
    parser.add_argument("--hnsw-ef-construction", type=int, default=64, help="HNSW ef_construction")
    parser.add_argument("--hnsw-ef-search", type=int, default=100, help="HNSW ef_search for queries")
    parser.add_argument(
        "--output",
        type=str,
        default="results/halfvec_benchmark_summary.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        default=False,
        help="Drop benchmark tables before creating them",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()
    run_benchmark(args)
