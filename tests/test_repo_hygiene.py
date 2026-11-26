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


def _check_hygiene():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_hygiene", ROOT / "scripts" / "check_hygiene.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_never_track_patterns():
    bad = ["notes/x.md", "docs/PROGRESS.md", "DECISIONS.md", "docs/proposals/0001.md",
           "CLAUDE.local.md", ".claude/settings.local.json", ".env", "x/.env.prod",
           "docs/OPEN_QUESTIONS.md", "a.ormap", "b.oraudit", "k.key", "m/x.onnx",
           "offrecord.local.yaml", "private/a.txt", "eval/private/x.txt", "out/a.txt"]  # fmt: skip
    ok = ["CLAUDE.md", "AGENTS.md", "docs/architecture.md", "src/offrecord/notes.py", ".envrc.md"]
    assert _check_hygiene().offending(bad + ok) == bad


def test_no_local_only_file_is_tracked():
    assert _check_hygiene().main([]) == 0


def test_distribution_allowlist(tmp_path):
    import io
    import tarfile
    import zipfile

    mod = _check_hygiene()
    whl = tmp_path / "offrecord-1-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n in ("offrecord/cli.py", "offrecord-1.dist-info/METADATA", "notes/PROGRESS.md",
                  "offrecord/m.onnx"):  # fmt: skip
            z.writestr(n, "x")
    assert mod.bad_members(whl) == ["notes/PROGRESS.md", "offrecord/m.onnx"]
    sdist = tmp_path / "offrecord-1.tar.gz"
    with tarfile.open(sdist, "w:gz") as t:
        for n in ("src/offrecord/cli.py", "README.md", "eval/user_set/u.txt", "CLAUDE.local.md"):
            info = tarfile.TarInfo(f"offrecord-1/{n}")
            t.addfile(info, io.BytesIO(b""))
    assert mod.bad_members(sdist) == ["eval/user_set/u.txt", "CLAUDE.local.md"]
