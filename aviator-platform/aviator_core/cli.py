"""`aviator` CLI — Repository Intelligence Core entry point.

Commands:

- `aviator index <repo>`     Parse a Java repository and build the SQLite index.
- `aviator stats`            Show counts (files / symbols / edges).
- `aviator search <text>`    Hybrid keyword + FTS search over symbols.
- `aviator show <id>`        Show a symbol and its outgoing edges.
- `aviator callers <name>`   Find call sites of a method name.

The default index path is `./.aviator/index.db`. Override with `--db PATH`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from aviator_core.indexer import index_repository
from aviator_core.query import callers as _callers
from aviator_core.query import find_symbols, neighbors
from aviator_core.storage import SqliteStore


app = typer.Typer(
    add_completion=False,
    help="Aviator Platform — Repository Intelligence Core (Java).",
    no_args_is_help=True,
)
console = Console()


def _default_db(repo: Optional[Path] = None) -> Path:
    base = repo if repo is not None else Path.cwd()
    return base / ".aviator" / "index.db"


def _open_store(db: Optional[Path]) -> SqliteStore:
    return SqliteStore(db or _default_db())


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@app.command()
def index(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    db: Optional[Path] = typer.Option(None, "--db", help="Path to the SQLite index."),
) -> None:
    """Parse every `.java` file in REPO and persist symbols + edges."""

    db_path = db or (repo / ".aviator" / "index.db")
    store = SqliteStore(db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("indexing", total=1)

        def on_progress(path: Path, done: int, total: int) -> None:
            progress.update(task, total=total, completed=done,
                            description=f"indexing [dim]{path.name}")

        stats = index_repository(repo, store, progress=on_progress)

    store.close()

    table = Table(title="Index complete", show_header=False)
    table.add_row("Files scanned", str(stats.files_scanned))
    table.add_row("Files parsed",  str(stats.files_parsed))
    table.add_row("Files failed",  str(stats.files_failed))
    table.add_row("Symbols",       str(stats.symbols))
    table.add_row("Edges",         str(stats.edges))
    table.add_row("Duration (s)",  str(stats.duration_seconds))
    table.add_row("Index path",    str(db_path))
    console.print(table)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    db: Optional[Path] = typer.Option(None, "--db", help="Path to the SQLite index."),
) -> None:
    """Show counts from an existing index."""

    store = _open_store(db)
    table = Table(title="Index stats", show_header=False)
    table.add_row("Files",   str(store.file_count()))
    table.add_row("Symbols", str(store.symbol_count()))
    table.add_row("Edges",   str(store.edge_count()))
    console.print(table)
    store.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command()
def search(
    text: str = typer.Argument(..., help="Keyword or FTS query."),
    kind: Optional[list[str]] = typer.Option(
        None, "--kind", "-k",
        help="Filter by symbol kind (class, method, field, ...). Repeatable.",
    ),
    limit: int = typer.Option(25, "--limit", "-n"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Hybrid keyword + FTS5 search over symbols."""

    store = _open_store(db)
    hits = find_symbols(store, text, kinds=kind, limit=limit)
    if not hits:
        console.print(f"[yellow]No symbols match[/] [bold]{text}[/]")
        store.close()
        return

    table = Table(title=f"Symbols matching '{text}'")
    table.add_column("kind", style="cyan")
    table.add_column("qualified_name")
    table.add_column("path", style="dim")
    table.add_column("line", justify="right")
    for h in hits:
        table.add_row(h.kind, h.qualified_name, h.path, str(h.start_line))
    console.print(table)
    store.close()


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command()
def show(
    symbol_id: str = typer.Argument(..., help="Symbol id (from `aviator search`)."),
    direction: str = typer.Option("out", "--direction", "-d",
                                  help="out | in | both"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Print a symbol and its adjacent edges."""

    store = _open_store(db)
    row = store.get_symbol(symbol_id)
    if row is None:
        console.print(f"[red]No symbol with id[/] [bold]{symbol_id}[/]")
        store.close()
        raise typer.Exit(1)

    meta = Table(title="Symbol", show_header=False)
    for col in row.keys():
        meta.add_row(col, str(row[col]))
    console.print(meta)

    edges = neighbors(store, symbol_id, direction=direction)
    if edges:
        table = Table(title=f"Edges ({direction})")
        table.add_column("kind", style="cyan")
        table.add_column("dst_name")
        table.add_column("dst_id", style="dim")
        table.add_column("path", style="dim")
        table.add_column("line", justify="right")
        for e in edges:
            table.add_row(e.kind, e.dst_name, e.dst_id or "", e.path or "",
                          str(e.start_line) if e.start_line else "")
        console.print(table)
    store.close()


# ---------------------------------------------------------------------------
# callers
# ---------------------------------------------------------------------------


@app.command(name="callers")
def callers_cmd(
    name: str = typer.Argument(..., help="Method name to find call sites for."),
    limit: int = typer.Option(25, "--limit", "-n"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Find best-effort call sites of a method name."""

    store = _open_store(db)
    hits = _callers(store, name, limit=limit)
    if not hits:
        console.print(f"[yellow]No callers of[/] [bold]{name}[/]")
        store.close()
        return

    table = Table(title=f"Callers of '{name}'")
    table.add_column("src_id", style="dim")
    table.add_column("dst_name")
    table.add_column("path")
    table.add_column("line", justify="right")
    for e in hits:
        table.add_row(e.src_id, e.dst_name, e.path or "",
                      str(e.start_line) if e.start_line else "")
    console.print(table)
    store.close()


if __name__ == "__main__":  # pragma: no cover
    app()
