"""`offrecord doctor`: environment checks. Never touches the network."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from dataclasses import asdict, dataclass

from offrecord import netguard

GiB = 1024**3


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # OK | WARN | FAIL | INFO
    detail: str


def total_ram_bytes() -> int | None:
    """Total physical RAM without third-party deps. ``OFFRECORD_SIMULATE_RAM_MB`` overrides
    (used to simulate low-memory machines)."""
    sim = os.environ.get("OFFRECORD_SIMULATE_RAM_MB")
    if sim:
        try:
            return int(sim) * 1024 * 1024
        except ValueError:
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages * page_size)
    except (ValueError, OSError, AttributeError):
        pass
    return None


def recommended_tier(ram: int | None, cpus: int | None) -> int:
    """Highest tier the hardware floor allows (independent of what is installed)."""
    if ram is None:
        return 0
    if ram >= 8 * GiB * 0.95 and (cpus or 0) >= 4:
        return 2
    if ram >= 4 * GiB * 0.95:
        return 1
    return 0


def run_checks() -> list[Check]:
    checks: list[Check] = []

    v = sys.version_info
    checks.append(
        Check(
            "python",
            "OK" if v >= (3, 10) else "FAIL",
            f"Python {v.major}.{v.minor}.{v.micro} "
            f"({platform.python_implementation()}); need 3.10+",
        )
    )

    ram = total_ram_bytes()
    cpus = os.cpu_count()
    if ram is None:
        checks.append(Check("ram", "WARN", "could not determine total RAM; assuming Tier 0"))
    else:
        status = "OK" if ram >= 2 * GiB * 0.9 else "WARN"
        checks.append(Check("ram", status, f"{ram / GiB:.1f} GiB total; Tier 0 floor is 2 GiB"))
    checks.append(Check("cpu", "INFO", f"{cpus or 'unknown'} logical CPUs"))
    checks.append(
        Check(
            "hardware_tier",
            "INFO",
            f"hardware allows up to Tier {recommended_tier(ram, cpus)} "
            "(0=rules, 1=NER, 2=LLM labels)",
        )
    )

    checks.append(Check("tier0", "OK", "rules-only redaction (built in)"))
    t1 = importlib.util.find_spec("onnxruntime") is not None
    checks.append(
        Check("tier1", "INFO", "onnxruntime installed" if t1 else "not installed (optional: [ner])")
    )
    t2 = importlib.util.find_spec("llama_cpp") is not None
    checks.append(
        Check("tier2", "INFO", "llama-cpp installed" if t2 else "not installed (optional: [llm])")
    )

    from offrecord.models import MANIFEST, installed

    any_model = False
    for name, spec in MANIFEST.items():
        if installed(name):
            any_model = True
            ok = installed(name, verify=True)
            if ok:
                from offrecord.models import write_stamp

                write_stamp(name)
            state = "verified" if ok else "MISMATCH (reinstall with setup models)"
            detail = f"Tier {spec.tier}, {spec.license}; checksum {state}"
            checks.append(Check(f"model:{name}", "OK" if ok else "FAIL", detail))
    if not any_model:
        checks.append(Check("models", "INFO", "no optional models installed (Tier 0 needs none)"))

    tk = importlib.util.find_spec("tiktoken") is not None
    checks.append(
        Check(
            "tokenizer",
            "INFO",
            "tiktoken installed (encoding must be pre-cached by `setup`)"
            if tk
            else "tiktoken not installed; approximate token counts will be used",
        )
    )

    ok = netguard.self_test()
    checks.append(
        Check(
            "network_guard",
            "OK" if ok else "FAIL",
            "run-time network guard blocks connections"
            if ok
            else "network guard did NOT block a loopback connect",
        )
    )
    return checks


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"[{c.status:<4}] {c.name:<{width}}  {c.detail}" for c in checks)


def checks_as_dicts(checks: list[Check]) -> list[dict[str, str]]:
    return [asdict(c) for c in checks]
