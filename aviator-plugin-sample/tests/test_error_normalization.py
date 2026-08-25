from ticket_to_code.utils.error_normalization import (
    normalize_error_identity,
    normalize_error_lines,
    strip_ansi,
)


def test_strip_ansi_removes_terminal_codes() -> None:
    raw = "src/app.component.html\x1b[0m:\x1b[93m104\x1b[0m:\x1b[93m48\x1b[0m - \x1b[91merror\x1b[0m TS2554"
    cleaned = strip_ansi(raw)

    assert "\x1b" not in cleaned
    assert "src/app.component.html:104:48 - error TS2554" in cleaned


def test_normalize_error_identity_ignores_line_shift_with_ansi() -> None:
    err_line_104 = "src/add-members.component.html\x1b[0m:\x1b[93m104\x1b[0m:\x1b[93m48\x1b[0m - \x1b[91merror\x1b[0m TS2554: Expected 2 arguments, but got 1."
    err_line_134 = "src/add-members.component.html\x1b[0m:\x1b[93m134\x1b[0m:\x1b[93m48\x1b[0m - \x1b[91merror\x1b[0m TS2554: Expected 2 arguments, but got 1."

    identity_104 = normalize_error_identity(err_line_104)
    identity_134 = normalize_error_identity(err_line_134)

    assert identity_104 == identity_134
    assert "TS2554" in identity_104
    assert ":104" not in identity_104
    assert ":134" not in identity_134


def test_normalize_error_lines_drops_empty_and_strips_ansi() -> None:
    lines = [
        "",
        "   ",
        "\x1b[91merror\x1b[0m TS2339: Property 'x' does not exist",
    ]
    normalized = normalize_error_lines(lines)

    assert normalized == ["error TS2339: Property 'x' does not exist"]
