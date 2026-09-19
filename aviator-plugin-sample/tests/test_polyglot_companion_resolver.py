"""Tests for PolyglotCompanionResolver."""
from pathlib import Path
import pytest
from ticket_to_code.intelligence.contracts.companion_resolver import PolyglotCompanionResolver


def test_resolve_angular_companion(tmp_path: Path):
    comp_ts = tmp_path / "user-profile.component.ts"
    comp_ts.write_text("export class UserProfileComponent {}", encoding="utf-8")
    comp_html = tmp_path / "user-profile.component.html"
    comp_html.write_text("<div>Profile</div>", encoding="utf-8")

    # Resolve from HTML to TS
    res = PolyglotCompanionResolver.resolve_companion(str(comp_html), workspace_path=str(tmp_path))
    assert res is not None
    assert res[0].endswith("user-profile.component.ts")
    assert "UserProfileComponent" in res[1]

    # Resolve from TS to HTML
    res_html = PolyglotCompanionResolver.resolve_companion(str(comp_ts), workspace_path=str(tmp_path))
    assert res_html is not None
    assert res_html[0].endswith("user-profile.component.html")
    assert "Profile" in res_html[1]


def test_resolve_blazor_companion(tmp_path: Path):
    razor_cs = tmp_path / "Counter.razor.cs"
    razor_cs.write_text("public partial class Counter { int count = 0; }", encoding="utf-8")
    razor = tmp_path / "Counter.razor"
    razor.write_text("<button @onclick=\"Increment\">@count</button>", encoding="utf-8")

    res = PolyglotCompanionResolver.resolve_companion(str(razor), workspace_path=str(tmp_path))
    assert res is not None
    assert res[0].endswith("Counter.razor.cs")
    assert "count = 0" in res[1]


def test_resolve_react_companion(tmp_path: Path):
    css = tmp_path / "Button.module.css"
    css.write_text(".primary { color: blue; }", encoding="utf-8")
    tsx = tmp_path / "Button.tsx"
    tsx.write_text("export const Button = () => <button />;", encoding="utf-8")

    res = PolyglotCompanionResolver.resolve_companion(str(tsx), workspace_path=str(tmp_path))
    assert res is not None
    assert res[0].endswith("Button.module.css")
    assert ".primary" in res[1]


def test_resolve_python_views_companion(tmp_path: Path):
    templates = tmp_path / "templates"
    templates.mkdir()
    tpl = templates / "dashboard.html"
    tpl.write_text("<h1>Dashboard</h1>", encoding="utf-8")
    views = tmp_path / "views.py"
    views.write_text("def dashboard(request): pass", encoding="utf-8")

    res = PolyglotCompanionResolver.resolve_companion(str(tpl), workspace_path=str(tmp_path))
    assert res is not None
    assert res[0].endswith("views.py")
    assert "dashboard" in res[1]


def test_resolve_from_session_memory_priority(tmp_path: Path):
    comp_html = tmp_path / "test.component.html"
    comp_html.write_text("<div></div>", encoding="utf-8")

    # Disk has old version
    comp_ts = tmp_path / "test.component.ts"
    comp_ts.write_text("export class OldComponent {}", encoding="utf-8")

    # Session memory has uncommitted active changes
    session_files = {
        str(comp_ts): "export class UpdatedComponent { public inMemory: boolean = true; }"
    }

    res = PolyglotCompanionResolver.resolve_companion(
        str(comp_html),
        session_files=session_files,
        workspace_path=str(tmp_path)
    )
    assert res is not None
    # Must prioritize session memory over disk
    assert "UpdatedComponent" in res[1]
    assert "inMemory" in res[1]
