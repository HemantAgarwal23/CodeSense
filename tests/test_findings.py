from app.services.findings import build_findings, entry_line


def test_build_findings_merges_duplicates_and_splits_runtime_risks() -> None:
    division = {
        "description": "Division by zero in average",
        "category": "runtime",
        "line": 3,
        "severity": "high",
        "file": "stats.py",
    }
    files = [
        {
            "file": "stats.py",
            "bugs": [
                division,
                dict(division),
                {"description": "Loop bound skips the last element", "category": "logic", "line": 7},
            ],
            "fixes": [
                {"description": "Guard the divisor", "code": "def average(values):\n    return 0.0"},
                {"description": "Review the issue manually"},
            ],
            "suggestions": ["Consider a clearer name for x"],
        }
    ]

    findings = build_findings(files)

    assert [item["description"] for item in findings["runtime_risks"]] == ["Division by zero in average"]
    assert findings["runtime_risks"][0]["count"] == 2
    assert findings["runtime_risks"][0]["severity"] == "high"
    assert findings["runtime_risks"][0]["line"] == 3
    assert [item["description"] for item in findings["code_issues"]] == ["Loop bound skips the last element"]
    assert [item["description"] for item in findings["fixes"]] == ["Guard the divisor"]
    assert findings["suggestions"][0]["description"] == "Consider a clearer name for x"


def test_build_findings_drops_python_overflow_and_type_validation_noise() -> None:
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    files = [
        {
            "bugs": [
                "Integer overflow possible when adding large numbers",
                "Missing input validation for a and b",
            ]
        }
    ]

    findings = build_findings(files, code)

    assert findings["runtime_risks"] == []
    assert findings["code_issues"] == []


def test_entry_line_reads_top_level_and_nested_locations() -> None:
    assert entry_line({"description": "x", "line": 4}) == 4
    assert entry_line({"description": "x", "location": {"file": "a.py", "line": "3"}}) == 3
    assert entry_line({"description": "x", "line": 0}) is None
    assert entry_line("plain text finding") is None
