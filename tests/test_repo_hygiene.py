"""Repository hygiene checks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_python_sources_are_ascii():
    """Invisible or look-alike characters in code are a review hazard (e.g. zero-width
    characters inside a deny-list of characters). Use \\u escapes instead."""
    bad = []
    for p in [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py")]:
        for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            if any(ord(c) > 127 for c in line):
                bad.append(f"{p.relative_to(ROOT)}:{i}")
    assert not bad, bad


def test_runtime_imports_are_declared_dependencies():
    """Every third-party module imported under src/ must be a declared dependency (a local venv
    with extra packages once hid a missing `cryptography` pin)."""
    import ast
    import sys

    declared = {"yaml", "rapidfuzz", "jellyfish", "cryptography"}
    # Imported only when installed: offline token counting, Tier 1 (ner extra), Tier 2 (llm extra).
    optional = {"tiktoken", "onnxruntime", "numpy", "tokenizers", "llama_cpp"}
    found = set()
    for p in ROOT.joinpath("src").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    third_party = {m for m in found if m not in sys.stdlib_module_names and m != "offrecord"}
    assert third_party <= declared | optional, third_party - declared - optional
    text = ROOT.joinpath("pyproject.toml").read_text()
    for pkg in ("PyYAML", "rapidfuzz", "jellyfish", "cryptography"):
        assert f'"{pkg}==' in text, pkg
