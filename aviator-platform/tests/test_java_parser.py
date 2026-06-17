"""Smoke test: parse a small in-memory Java snippet end-to-end."""

from pathlib import Path

from aviator_core.indexer import index_repository
from aviator_core.models import EdgeKind, SymbolKind
from aviator_core.parsers import JavaParser
from aviator_core.storage import SqliteStore


JAVA_SRC = """\
package com.example.demo;

import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class UserService extends BaseService implements Auditable {

    private final UserRepository repo;

    public UserService(UserRepository repo) {
        this.repo = repo;
    }

    public User findById(long id) {
        return repo.findById(id);
    }

    @Override
    public void audit(String action) {
        log(action);
    }
}
"""


def _write_repo(tmp_path: Path) -> Path:
    src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "demo"
    src_dir.mkdir(parents=True)
    (src_dir / "UserService.java").write_text(JAVA_SRC, encoding="utf-8")
    return tmp_path


def test_parse_extracts_class_and_methods(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    parser = JavaParser()
    result = parser.parse_file(
        repo / "src" / "main" / "java" / "com" / "example" / "demo" / "UserService.java",
        repo,
    )
    kinds = {s.kind for s in result.symbols}
    assert SymbolKind.CLASS in kinds
    assert SymbolKind.METHOD in kinds
    assert SymbolKind.CONSTRUCTOR in kinds
    assert SymbolKind.FIELD in kinds

    cls = next(s for s in result.symbols if s.kind == SymbolKind.CLASS)
    assert cls.name == "UserService"
    assert cls.qualified_name == "com.example.demo.UserService"
    assert "Service" in cls.annotations

    methods = [s.name for s in result.symbols if s.kind == SymbolKind.METHOD]
    assert "findById" in methods
    assert "audit" in methods


def test_inheritance_and_call_edges(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    parser = JavaParser()
    result = parser.parse_file(
        repo / "src" / "main" / "java" / "com" / "example" / "demo" / "UserService.java",
        repo,
    )
    extends = {e.dst_name for e in result.edges if e.kind == EdgeKind.EXTENDS}
    implements = {e.dst_name for e in result.edges if e.kind == EdgeKind.IMPLEMENTS}
    calls = {e.dst_name for e in result.edges if e.kind == EdgeKind.CALLS}

    assert "BaseService" in extends
    assert "Auditable" in implements
    assert any("findById" in c for c in calls)
    assert "log" in calls


def test_index_repository_roundtrip(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    store = SqliteStore(tmp_path / "idx.db")
    stats = index_repository(repo, store)
    assert stats.files_parsed == 1
    assert stats.symbols > 0
    assert stats.edges > 0
    hits = store.search_symbols("UserService", kinds=["class"])
    assert len(hits) == 1
    store.close()
