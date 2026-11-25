"""Command-line entry point.

stdout carries only command output (for piping). Every message goes to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import sys
from collections.abc import Sequence
from typing import NoReturn

from offrecord import __version__, netguard, safe_errors
from offrecord.errors import ExitCode, NotImplementedYetError, OffrecordError

# Commands that may use the network. Only `setup`, and only when the user runs it explicitly.
_NETWORK_ALLOWED = frozenset({"setup"})


def sanitize_usage_message(message: str) -> str:
    """argparse quotes argument values, and file names often carry client names. Keep only
    option names and the kind of error."""
    m = re.match(r"unrecognized arguments: (.*)", message)
    if m:
        return f"unrecognized arguments ({len(m.group(1).split())})"
    m = re.match(
        r"argument ([-\w/]+(?:/[-\w]+)*): (invalid choice|invalid \w+ value|expected [^:]*)",
        message,
    )
    if m:
        return f"argument {m.group(1)}: {m.group(2)}"
    if message.startswith("the following arguments are required"):
        return message  # lists option names only
    return re.sub(r"'[^']*'", "'...'", message).split(":")[0]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"offrecord: usage error: {sanitize_usage_message(message)}", file=sys.stderr)
        raise SystemExit(ExitCode.USAGE)


def _not_built(name: str, phase: int) -> int:
    raise NotImplementedYetError(f"`{name}` is not built yet (planned for phase {phase})")


def _cmd_doctor(args: argparse.Namespace) -> int:
    from offrecord import doctor

    checks = doctor.run_checks()
    if args.json:
        sys.stdout.write(json.dumps(doctor.checks_as_dicts(checks), indent=2) + "\n")
    else:
        sys.stdout.write(doctor.format_checks(checks) + "\n")
    return ExitCode.ENVIRONMENT if any(c.status == "FAIL" for c in checks) else ExitCode.OK


def _cmd_eval(args: argparse.Namespace) -> int:
    from pathlib import Path

    from offrecord.errors import UsageError
    from offrecord.evaluation import runner, sets

    available = sets.systems()
    system_name = args.system or ("tier0" if "tier0" in available else "baseline")
    if system_name not in available:
        raise UsageError(f"unknown system; available: {', '.join(sorted(available))}")
    targets: list[tuple[str, Path]] = []
    if args.dir:
        targets += [(f"dir:{i}", Path(d)) for i, d in enumerate(args.dir)]
    if args.set or not args.dir:
        root = sets.repo_root()
        targets += [(n, sets.set_dir(n, root)) for n in (args.set or sets.SET_NAMES)]

    reports = []
    extras = []
    for name, d in targets:
        if not d.is_dir():
            print(f"offrecord: note: set {name} not found; skipped", file=sys.stderr)
            continue
        if any(d.glob("*.labels.json")):
            items, cfg = runner.load_generated(d)
        else:
            items, cfg = runner.load_markup_dir(d)
        if not items:
            print(f"offrecord: note: set {name} has no transcripts; skipped", file=sys.stderr)
            continue
        if (name == "heldout" or sets.is_heldout(d)) and not sets.verify_manifest(
            d, sets.HELDOUT_MANIFEST_SHA256
        ):
            raise UsageError("the held-out set does not match its pinned manifest (frozen set)")
        names = [system_name] + (
            ["baseline"] if args.baseline and system_name != "baseline" else []
        )
        for sysname in names:
            reports.append(runner.score(items, available[sysname](cfg), f"{name} [{sysname}]"))
        if args.extras:
            from offrecord.evaluation.extras import run_extras

            extras.append(run_extras(items, cfg, name))
    if not reports:
        raise UsageError("no evaluation sets found")
    if args.json:
        data = [r.to_json() for r in reports] + [e.to_json() for e in extras]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        sys.stdout.write(runner.format_report(reports) + "\n")
        if extras:
            from offrecord.evaluation.extras import format_extras

            sys.stdout.write("\n" + format_extras(extras) + "\n")
        sys.stdout.write("Synthetic data only. Recall = labels fully covered by a redaction.\n")
    return ExitCode.OK


# Mirrors offrecord.evaluation.sets.SET_NAMES (kept literal so the CLI starts without importing
# the evaluation package; a test checks they match).
SET_CHOICES = ("tuning", "tuning-hw", "heldout", "user")
EXTENSIONS = {"txt": ".txt", "vtt": ".vtt", "srt": ".srt", "json": ".json"}


def resolve_tier(requested: str, cfg: object) -> int:
    """``auto``: the highest tier this machine's RAM allows whose packages and models are
    installed. An explicit tier that cannot run says exactly what is missing."""
    import importlib.util

    from offrecord import models as mdl
    from offrecord.doctor import recommended_tier, total_ram_bytes
    from offrecord.errors import EnvironmentCheckError
    from offrecord.models import installed

    pkgs1 = all(importlib.util.find_spec(m) for m in ("onnxruntime", "numpy", "tokenizers"))
    pkgs2 = importlib.util.find_spec("llama_cpp") is not None
    files1 = all(installed(n) for n in cfg.ner_models)  # type: ignore[attr-defined]
    if not installed(cfg.llm_model):  # type: ignore[attr-defined]
        # `setup models --tier 2` installs the fallback on smaller machines: use what is there.
        for alt in (mdl.TIER2_FALLBACK, mdl.TIER2_DEFAULT):
            if installed(alt):
                cfg.llm_model = alt  # type: ignore[attr-defined]
                break
    files2 = installed(cfg.llm_model)  # type: ignore[attr-defined]
    ram = total_ram_bytes()
    if requested == "auto":
        hw = recommended_tier(ram, os.cpu_count())
        if hw >= 2 and pkgs1 and files1 and pkgs2 and files2:
            return 2
        if hw >= 1 and pkgs1 and files1:
            return 1
        return 0
    tier = int(requested) if requested is not None else cfg.tier  # type: ignore[attr-defined]
    if tier >= 1 and not pkgs1:
        raise EnvironmentCheckError("Tier 1 needs the [ner] extra: pip install 'offrecord[ner]'")
    if tier >= 1 and not files1:
        raise EnvironmentCheckError("Tier 1 models are not installed: offrecord setup models")
    if tier >= 2 and not pkgs2:
        raise EnvironmentCheckError("Tier 2 needs the [llm] extra: pip install 'offrecord[llm]'")
    if tier >= 2 and not files2:
        raise EnvironmentCheckError(
            "Tier 2 model is not installed: offrecord setup models --tier 2"
        )
    if tier >= 2:
        floor = mdl.MANIFEST[cfg.llm_model].min_ram_gb  # type: ignore[attr-defined]
        if ram and ram < floor * 0.95 * 1024**3:
            print(
                f"offrecord: warning: the Tier 2 model needs about {floor:.0f} GB of RAM and this "
                "machine has less (models.llm: qwen3-1.7b-q4 fits 4 GB)",
                file=sys.stderr,
            )
    return tier


def _secret_notice(secret: object) -> None:
    if getattr(secret, "source", "") == "env":
        print("offrecord: using the passphrase from OFFRECORD_PASSPHRASE", file=sys.stderr)


def _cmd_redact(args: argparse.Namespace) -> int:
    import dataclasses
    from pathlib import Path

    from offrecord import adapters, audit, crypto, output
    from offrecord.compress import compress
    from offrecord.errors import OutputExistsError, UsageError
    from offrecord.limits import DEFAULT_LIMITS, Limits
    from offrecord.paths import mapping_store
    from offrecord.redact import config as config_mod
    from offrecord.redact.pipeline import redact_document, review_flags
    from offrecord.redact.replace import Mapping
    from offrecord.restore import bare_collisions
    from offrecord.safeio import ensure_private_dir, read_input_bytes, read_input_text, write_atomic
    from offrecord.tokens import get_counter

    if args.mapping_out and len(args.inputs) > 1 and args.mapping_scope != "project":
        raise UsageError("--mapping-out with several inputs needs --mapping-scope project")
    if args.mapping_in and args.no_mapping:
        raise UsageError("--mapping-in cannot be combined with --no-mapping")
    if len(args.inputs) > 1 and not args.out_dir:
        raise UsageError("several inputs need --out-dir")
    if args.output and len(args.inputs) > 1:
        raise UsageError("-o/--output takes a single input")
    secret = None
    if not args.no_mapping or args.audit:
        secret = crypto.get_secret(args.key_file, confirm=True)
        if secret is None:
            raise UsageError(
                "no passphrase or key file: set OFFRECORD_PASSPHRASE, pass --key-file (see "
                "`offrecord keygen`), run interactively, or pass --no-mapping"
            )
        _secret_notice(secret)
    cfg = config_mod.load(args.config) if args.config else config_mod.Config()
    cfg.tier = resolve_tier(args.tier, cfg)
    embedder = None
    if args.compress == "aggressive":
        import importlib.util

        from offrecord.models import installed, model_path

        if installed("minilm-l6-v2") and importlib.util.find_spec("onnxruntime"):
            from offrecord.models.embed import Embedder

            embedder = Embedder(model_path("minilm-l6-v2"), cfg.model_threads)
        elif args.compress == "aggressive":
            raise UsageError("--compress aggressive needs Tier 1 models: offrecord setup models")
    print(f"offrecord: tier {cfg.tier}", file=sys.stderr)

    def mapping_dest(mapping_id: str) -> tuple[Path, bool]:
        if args.mapping_in:
            return Path(args.mapping_in), True
        if args.mapping_out:
            return Path(args.mapping_out), args.force
        return ensure_private_dir(mapping_store()) / f"{mapping_id}.ormap", False

    # Check where the mapping will go *before* writing any output, so a refused mapping can never
    # leave an output whose header names a mapping that was not saved.
    if secret is not None and not args.no_mapping:
        probe, force = mapping_dest("or1-probe")
        crypto.check_separation(probe, secret)
        if args.mapping_out and not force and (probe.exists() or probe.is_symlink()):
            raise OutputExistsError("mapping file exists; pass --force to overwrite")

    shared = Mapping(scope="project") if args.mapping_scope == "project" else None
    if args.mapping_in:
        data = read_input_bytes(args.mapping_in, Limits(max_bytes=crypto.MAX_FILE_BYTES))
        _id, payload = crypto.decrypt(data, "map", secret)  # type: ignore[arg-type]
        shared = Mapping.from_payload(payload)
        shared.scope = "project"

    def save_mapping(mapping: Mapping, rendered: list[str]) -> None:
        if secret is None or args.no_mapping:
            return
        payload = mapping.to_payload()
        # Bare tokens that occur naturally in the final output (metadata included).
        payload["bare_collisions"] = bare_collisions(rendered, list(mapping.entries))
        dest, force = mapping_dest(mapping.id)
        crypto.check_separation(dest, secret)
        write_atomic(dest, crypto.encrypt("map", mapping.id, payload, secret), force=force)
        print(f"offrecord: mapping {mapping.id} written to {dest}", file=sys.stderr)

    project_rendered: list[str] = []
    pending: list[tuple[Path | None, str]] = []
    audit_entries: list[dict] = []
    counter = get_counter(args.tokenizer)
    for n_input, path in enumerate(args.inputs):
        text = read_input_text(path, DEFAULT_LIMITS)
        adapter = (
            adapters.get(args.format) if args.format != "auto" else adapters.detect(text, path)
        )
        doc = adapter.parse(text, DEFAULT_LIMITS)
        mapping = shared if shared is not None else Mapping()
        red, spans, mapping, stats = redact_document(doc, cfg, mapping)
        flags = review_flags(spans, cfg.review_threshold)
        if args.audit:
            audit_entries.extend(audit.entries(n_input, doc, stats.records, flags))
        mapping_id = None if args.no_mapping else mapping.id
        small, cstats = compress(red, args.compress, embedder)
        if args.output_format == "original":
            if small is not red and small.format == "json":
                small = dataclasses.replace(small, render_state=None)  # turns were merged/dropped
            rendered = output.render_original(small, mapping_id)
        else:
            rendered = output.render_compact(small, mapping_id)
        before, after = counter.count(text), counter.count(rendered)
        pct = (1 - after / before) * 100 if before else 0.0
        label = "approx" if counter.approximate else counter.name
        print(
            f"offrecord: tokens ({label}, estimate): {before} -> {after} ({pct:.0f}% fewer); "
            f"compression {args.compress}: {cstats.turns_before} -> {cstats.turns_after} turns",
            file=sys.stderr,
        )
        dest: Path | None = None
        if args.out_dir:
            # Output names never reuse input names or suffixes (they can carry client names).
            suffix = (
                EXTENSIONS.get(doc.format, ".txt") if args.output_format == "original" else ".txt"
            )
            stem = f"{n_input + 1:03d}-{mapping.id if not args.no_mapping else 'redacted'}"
            dest = Path(args.out_dir) / f"{stem}{suffix}"
        elif args.output:
            dest = Path(args.output)
        if shared is None:
            save_mapping(mapping, [rendered])  # mapping first, then the output
            _emit(dest, rendered, args.force, write_atomic, n_input)
        else:
            project_rendered.append(rendered)
            pending.append((dest, rendered))
        print(
            f"offrecord: redacted {stats.replaced} span(s), {len(flags)} review flag(s)"
            + (f", escaped {stats.escaped} placeholder-like string(s)" if stats.escaped else ""),
            file=sys.stderr,
        )
    if shared is not None:
        save_mapping(shared, project_rendered)
        for i, (dest, rendered) in enumerate(pending):
            _emit(dest, rendered, args.force, write_atomic, i)
    if args.audit and secret is not None:
        audit_id = audit.new_id()
        dest_a = ensure_private_dir(mapping_store()) / f"{audit_id}.oraudit"
        crypto.check_separation(dest_a, secret)
        payload = {"entries": audit_entries}
        write_atomic(dest_a, crypto.encrypt("audit", audit_id, payload, secret))
        print(f"offrecord: encrypted audit log written to {dest_a}", file=sys.stderr)
    return ExitCode.OK


def _emit(dest, rendered: str, force: bool, write_atomic, n: int) -> None:
    if dest is None:
        sys.stdout.write(rendered)
        return
    write_atomic(dest, rendered.encode("utf-8"), force=force)
    print(f"offrecord: input #{n + 1} -> {dest}", file=sys.stderr)


def _cmd_restore(args: argparse.Namespace) -> int:
    from offrecord import crypto
    from offrecord.errors import MappingError, UsageError
    from offrecord.restore import find_header_id, restore_text
    from offrecord.safeio import read_input_bytes, read_input_text, write_atomic

    text = read_input_text(args.input)
    if find_header_id(text) is None and not args.assume_mapping:
        raise UsageError(
            "this text has no offrecord mapping header (e.g. an LLM answer), so the mapping "
            "cannot be verified; pass --assume-mapping if you are sure this mapping is the "
            "right one"
        )
    secret = crypto.get_secret(args.key_file)
    if secret is None:
        raise UsageError("no passphrase or key file: set OFFRECORD_PASSPHRASE or pass --key-file")
    _secret_notice(secret)
    from offrecord.limits import Limits

    data = read_input_bytes(args.mapping, Limits(max_bytes=crypto.MAX_FILE_BYTES))
    mapping_id, payload = crypto.decrypt(data, "map", secret)
    out, rep = restore_text(text, payload, mapping_id)
    if args.output:
        write_atomic(args.output, out.encode("utf-8"), force=args.force)
    else:
        sys.stdout.write(out)
    print(f"offrecord: restored {rep.restored} placeholder(s)", file=sys.stderr)
    if rep.unknown:
        names = ", ".join(sorted(rep.unknown))
        print(f"offrecord: warning: {sum(rep.unknown.values())} placeholder(s) not in this "
              f"mapping: {names}", file=sys.stderr)  # fmt: skip
        if rep.header_id is None:
            print(
                "offrecord: warning: no mapping header; is this the right mapping file?",
                file=sys.stderr,
            )
    if rep.ambiguous:
        print(f"offrecord: warning: {sum(rep.ambiguous.values())} bare placeholder-like token(s) "
              "left as is because they also occur naturally in the redacted text",
              file=sys.stderr)  # fmt: skip
    if args.strict and (rep.unknown or rep.ambiguous):
        raise MappingError("restore incomplete (--strict)")
    return ExitCode.OK


def _cmd_review(args: argparse.Namespace) -> int:
    """Side-by-side review. Prints original text: for the user's own terminal or an owner-only
    file only."""
    import json as _json
    from pathlib import Path

    from offrecord import adapters, review
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import parse_markup_transcript
    from offrecord.limits import DEFAULT_LIMITS
    from offrecord.redact import config as config_mod
    from offrecord.redact.pipeline import redact_document
    from offrecord.safeio import read_input_text, write_atomic

    cfg = config_mod.load(args.config) if args.config else config_mod.Config()
    text = read_input_text(args.input, DEFAULT_LIMITS)
    labels: list[Label] = []
    if args.markup:
        lt, doc = parse_markup_transcript(Path(args.input).stem, text)
        labels = lt.labels
    else:
        adapter = (
            adapters.get(args.format)
            if args.format != "auto"
            else adapters.detect(text, args.input)
        )
        doc = adapter.parse(text, DEFAULT_LIMITS)
        lp = Path(args.labels) if args.labels else Path(args.input).with_suffix(".labels.json")
        if lp.exists():
            raw = read_input_text(lp, DEFAULT_LIMITS)
            labels = [Label.from_json(d) for d in _json.loads(raw)["labels"]]
    red, spans, _mapping, _stats = redact_document(doc, cfg)
    out = review.side_by_side(doc, red, spans, labels, width=args.width,
                              threshold=cfg.review_threshold)  # fmt: skip
    misses = review.find_misses(doc, spans, labels) if labels else []
    if labels:
        out += f"\n{len(misses)} missed label(s) of {len(labels)}\n"
    if args.suggest:
        out += "\n" + review.denylist_suggestions(misses)
    if args.output:
        write_atomic(args.output, out.encode("utf-8"), force=args.force)
    else:
        sys.stdout.write(out)
    return ExitCode.OK


def _cmd_setup(args: argparse.Namespace) -> int:
    """The only command allowed to use the network, and only for what the user asks for."""

    from offrecord import tokens
    from offrecord.errors import EnvironmentCheckError, UsageError
    from offrecord.safeio import ensure_private_dir, write_atomic

    if args.what == "models":
        from offrecord import models
        from offrecord.doctor import GiB, total_ram_bytes
        from offrecord.models.setup import install

        names = list(args.model or [])
        if not names:
            if args.tier == 2:
                ram = total_ram_bytes() or 0
                names = [models.TIER2_DEFAULT if ram >= 7.5 * GiB else models.TIER2_FALLBACK]
            else:
                names = list(models.TIER1_DEFAULT)
        for name in names:
            install(name, args.from_file)
        return ExitCode.OK
    enc = args.encoding
    if enc not in tokens.ENCODING_URLS:
        raise UsageError(f"unknown encoding; known: {', '.join(sorted(tokens.ENCODING_URLS))}")
    if args.from_file:
        from offrecord.limits import Limits
        from offrecord.safeio import read_input_bytes

        data = read_input_bytes(args.from_file, Limits(max_bytes=64 * 1024 * 1024))
    else:
        import urllib.request

        url = tokens.ENCODING_URLS[enc]
        print(
            f"offrecord: downloading the {enc} encoding (setup only; no transcript data is sent)",
            file=sys.stderr,
        )
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https URL
            data = resp.read(64 * 1024 * 1024)
    if not tokens.verify_encoding_file(enc, data):
        raise EnvironmentCheckError("encoding file checksum does not match; not installed")
    ensure_private_dir(tokens.tiktoken_cache_dir())
    write_atomic(tokens.cache_path(enc), data, force=True)
    print(f"offrecord: {enc} installed for offline use", file=sys.stderr)
    return ExitCode.OK


def _cmd_audit(args: argparse.Namespace) -> int:
    """Decrypt an audit log to stdout (it contains original text: local use only)."""
    from offrecord import crypto
    from offrecord.errors import UsageError
    from offrecord.limits import Limits
    from offrecord.safeio import read_input_bytes

    secret = crypto.get_secret(args.key_file)
    if secret is None:
        raise UsageError("no passphrase or key file: set OFFRECORD_PASSPHRASE or pass --key-file")
    _secret_notice(secret)
    data = read_input_bytes(args.file, Limits(max_bytes=crypto.MAX_FILE_BYTES))
    _id, payload = crypto.decrypt(data, "audit", secret)
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return ExitCode.OK


def _cmd_keygen(args: argparse.Namespace) -> int:
    from pathlib import Path

    from offrecord import crypto
    from offrecord.paths import default_key_path
    from offrecord.safeio import ensure_private_dir, write_atomic

    dest = Path(args.path) if args.path else default_key_path()
    # Our own default key directory is made private; a directory you name is left as it is
    # (the key file itself is always 0600).
    ensure_private_dir(dest.parent, chmod_existing=not args.path)
    write_atomic(dest, crypto.generate_key().encode("ascii"), force=args.force)
    print(f"offrecord: key written to {dest} (keep it away from mapping files and backups "
          "of them)", file=sys.stderr)  # fmt: skip
    return ExitCode.OK


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="offrecord", description="Offline transcript redaction and compression.")
    p.add_argument("--version", action="version", version=f"offrecord {__version__}")
    p.add_argument(
        "--debug",
        action="store_true",
        help="show content-free stack traces for internal errors",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    sp = sub.add_parser("redact", help="redact (and optionally compress) a transcript")
    sp.add_argument("inputs", nargs="+", metavar="INPUT", help="transcript file(s), or - for stdin")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--out-dir", help="output directory (for several inputs)")
    sp.add_argument("--format", default="auto", help="input format: auto, txt, vtt, srt, json")
    sp.add_argument("--config", help="config file (YAML) or deny-list (CSV); sensitive")
    sp.add_argument("--output-format", choices=["compact", "original"], default="compact")
    sp.add_argument(
        "--compress",
        choices=["none", "light", "medium", "aggressive"],
        default="light",
        help="light: fillers/backchannel/merges; medium: + dedupe and triage; "
        "aggressive: + extractive selection (Tier 1 embeddings)",
    )
    sp.add_argument(
        "--tokenizer", default="auto", help="auto | approx | tiktoken:cl100k_base (estimates)"
    )
    sp.add_argument(
        "--mapping-scope",
        choices=["document", "project"],
        default="document",
        help="project: one mapping shared by all inputs (same entity, same placeholder)",
    )
    sp.add_argument(
        "--tier",
        choices=["0", "1", "2", "auto"],
        default=None,
        help="0: rules only (default); 1: + NER models; 2: + LLM labels; auto: by RAM and models",
    )
    sp.add_argument("--no-mapping", action="store_true", help="one-way run: discard the mapping")
    sp.add_argument("--key-file", help="key file for the mapping (else OFFRECORD_KEY_FILE, "
                    "OFFRECORD_PASSPHRASE, or a prompt)")  # fmt: skip
    sp.add_argument("--mapping-out", help="where to write the encrypted mapping "
                    "(default: private per-user store)")  # fmt: skip
    sp.add_argument("--mapping-in", help="extend an existing (project) mapping file in place")
    sp.add_argument(
        "--audit",
        action="store_true",
        help="write an encrypted audit log (contains original text) to the private store",
    )
    sp.add_argument("--force", action="store_true", help="overwrite existing output files")
    sp.set_defaults(func=_cmd_redact)

    sp = sub.add_parser("restore", help="restore placeholders using a mapping file")
    sp.add_argument("input", help="redacted text or an LLM answer about it (- for stdin)")
    sp.add_argument("--mapping", required=True, help="encrypted mapping file (.ormap)")
    sp.add_argument("--key-file", help="key file (else OFFRECORD_KEY_FILE / OFFRECORD_PASSPHRASE)")
    sp.add_argument(
        "-o", "--output", help="output file (default: stdout); restored text is sensitive"
    )
    sp.add_argument("--force", action="store_true", help="overwrite an existing output file")
    sp.add_argument("--strict", action="store_true", help="fail if anything could not be restored")
    sp.add_argument(
        "--assume-mapping",
        action="store_true",
        help="accept header-less text (an LLM answer): the mapping cannot be verified",
    )
    sp.set_defaults(func=_cmd_restore)

    sp = sub.add_parser("audit", help="decrypt and print an audit log (local use only)")
    sp.add_argument("file", help="encrypted audit log (.oraudit)")
    sp.add_argument("--key-file", help="key file (else OFFRECORD_KEY_FILE / OFFRECORD_PASSPHRASE)")
    sp.set_defaults(func=_cmd_audit)

    sp = sub.add_parser("keygen", help="create a random key file for mappings and audit logs")
    sp.add_argument("path", nargs="?", help="where to write it (default: per-user config dir)")
    sp.add_argument("--force", action="store_true", help="overwrite an existing key file")
    sp.set_defaults(func=_cmd_keygen)

    sp = sub.add_parser("eval", help="evaluate recall/precision on synthetic or labelled sets")
    sp.add_argument("--set", action="append", choices=list(SET_CHOICES),
                    help="built-in set (repeatable; default: all)")  # fmt: skip
    sp.add_argument("--dir", action="append", help="a labelled folder, e.g. a private set")
    sp.add_argument("--system", help="system under test (default: tier0, else baseline)")
    sp.add_argument("--baseline", action="store_true", help="also score the regex baseline")
    sp.add_argument(
        "--extras",
        action="store_true",
        help="placeholder consistency, restore accuracy and token reduction (Tier 0)",
    )
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=_cmd_eval)

    sp = sub.add_parser("review", help="side-by-side review of original and redacted text")
    sp.add_argument("input", help="transcript (prints ORIGINAL text: local use only)")
    sp.add_argument("--config", help="config / deny-list")
    sp.add_argument("--format", default="auto")
    sp.add_argument(
        "--markup", action="store_true", help="input uses inline [[CATEGORY:...]] labels"
    )
    sp.add_argument("--labels", help="labels JSON (default: <input>.labels.json if present)")
    sp.add_argument("--suggest", action="store_true", help="suggest deny-list entries for misses")
    sp.add_argument("--width", type=int, default=60)
    sp.add_argument("-o", "--output", help="write to an owner-only file instead of stdout")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=_cmd_review)

    sp = sub.add_parser("setup", help="explicitly download optional models / token encoding")
    sp.add_argument("what", choices=["tokenizer", "models"])
    sp.add_argument("--encoding", default="cl100k_base")
    sp.add_argument("--from", dest="from_file", help="install from a local file or directory")
    sp.add_argument("--tier", type=int, choices=[1, 2], default=1, help="models: which tier")
    sp.add_argument("--model", action="append", help="models: a specific model (repeatable)")
    sp.set_defaults(func=_cmd_setup)

    sp = sub.add_parser("doctor", help="check Python, RAM, tiers, models and the network guard")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=_cmd_doctor)
    return p


def _on_sigterm(signum: int, frame: object) -> None:
    # Convert to an exception so context managers (temp dir cleanup) run.
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    safe_errors.install_hooks()
    parser = build_parser()
    args = parser.parse_args(argv)
    safe_errors.set_debug(args.debug)
    if args.command is None:
        parser.print_help(sys.stderr)
        return ExitCode.USAGE

    if args.command not in _NETWORK_ALLOWED:
        netguard.install()
    # Not available off the main thread or on some platforms.
    for sig in ("SIGTERM", "SIGHUP"):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(getattr(signal, sig), _on_sigterm)

    try:
        rc = int(args.func(args))
    except OffrecordError as e:
        safe_errors.report(e)
        rc = int(e.exit_code)
    except KeyboardInterrupt:
        print("offrecord: interrupted", file=sys.stderr)
        rc = ExitCode.INTERRUPTED
    except Exception as e:  # noqa: BLE001 - top-level, content-free report
        safe_errors.report(e)
        rc = ExitCode.INTERNAL

    if netguard.is_installed() and netguard.attempts() > 0:
        print(
            f"offrecord: error[E_NETWORK_BLOCKED]: {netguard.attempts()} network attempt(s) "
            "were blocked during this run; treating the run as failed",
            file=sys.stderr,
        )
        rc = ExitCode.NETWORK
    return rc


def entry() -> NoReturn:  # pragma: no cover - thin wrapper
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    entry()
