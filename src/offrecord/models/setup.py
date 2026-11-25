"""``offrecord setup models``: the only code path that downloads model files.

Downloads stream to a temp file in the model directory, are verified against the pinned SHA-256
and size, and only then are atomically renamed into place. ``--from DIR`` installs from local
copies (air-gapped machines): the same verification applies. No transcript data is ever sent.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from offrecord.errors import EnvironmentCheckError, UsageError
from offrecord.models import MANIFEST, ModelFile, ModelSpec, model_path
from offrecord.safeio import ensure_private_dir

CHUNK = 1 << 20


def _url(spec: ModelSpec, f: ModelFile) -> str:
    return f"https://huggingface.co/{spec.repo}/resolve/{spec.revision}/{f.remote}"


def _install_stream(src, dest: Path, f: ModelFile) -> None:
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".offrecord-", suffix=".part")
    h = hashlib.sha256()
    n = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                b = src.read(CHUNK)
                if not b:
                    break
                n += len(b)
                if n > f.size:
                    raise EnvironmentCheckError("model file is larger than expected; not installed")
                h.update(b)
                out.write(b)
            out.flush()
            os.fsync(out.fileno())
        if n != f.size or h.hexdigest() != f.sha256:
            raise EnvironmentCheckError("model file checksum does not match; not installed")
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install(name: str, from_dir: str | None = None) -> None:
    if name not in MANIFEST:
        raise UsageError(f"unknown model; known: {', '.join(sorted(MANIFEST))}")
    spec = MANIFEST[name]
    d = ensure_private_dir(model_path(name))
    for f in spec.files_for_this_machine():
        dest = d / f.local
        if from_dir:
            src_path = Path(from_dir) / f.remote
            if not src_path.is_file():
                src_path = Path(from_dir) / f.local
            if not src_path.is_file():
                raise UsageError("a model file is missing from the --from directory")
            with src_path.open("rb") as src:
                _install_stream(src, dest, f)
        else:
            mb = f.size / 1e6
            print(
                f"offrecord: downloading {name}/{f.local} ({mb:.0f} MB, {spec.license})",
                file=sys.stderr,
            )
            req = urllib.request.Request(  # noqa: S310 - fixed https URL from the manifest
                _url(spec, f), headers={"User-Agent": "offrecord-setup"}
            )
            with urllib.request.urlopen(req, timeout=120) as src:  # noqa: S310 - fixed https URL
                _install_stream(src, dest, f)
    from offrecord.models import write_stamp

    write_stamp(name)
    print(f"offrecord: {name} installed and verified", file=sys.stderr)


def remove(name: str) -> None:
    shutil.rmtree(model_path(name), ignore_errors=True)
