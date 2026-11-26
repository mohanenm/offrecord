"""Encryption for the mapping file and audit log.

File format (version 1):

    OFFRECORD-MAP 1                       (or OFFRECORD-AUDIT 1)
    {"id": "...", "kdf": {...}}           header: no content, no timestamps
    <Fernet token>

- Passphrase -> scrypt (N=2^17, r=8, p=1, 16-byte salt) -> 32 bytes -> Fernet key.
- Key file (32 random bytes, base64) -> HKDF-SHA256 with the file's salt -> Fernet key.
- The id and format version are repeated inside the encrypted payload and checked after
  decryption, so a swapped or edited header is detected.
- Errors never include key material, passphrases, paths or payload content.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from offrecord.errors import MappingError, UsageError

FORMAT_VERSION = 1
MAGIC = {"map": "OFFRECORD-MAP", "audit": "OFFRECORD-AUDIT"}
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
PASSPHRASE_ENV = "OFFRECORD_PASSPHRASE"  # noqa: S105 - an env var name
KEY_FILE_ENV = "OFFRECORD_KEY_FILE"
MIN_PASSPHRASE = 12
MAX_FILE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Secret:
    """A passphrase or key-file key. ``repr`` never shows the value."""

    kind: str  # "passphrase" | "keyfile"
    value: bytes
    key_path: Path | None = None
    source: str = ""  # "env" | "prompt" | "file": the CLI tells the user where it came from

    def __repr__(self) -> str:
        return f"Secret(kind={self.kind!r})"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    try:
        return base64.b64decode(s.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise MappingError("mapping header is malformed") from None


def derive_key(secret: Secret, kdf: dict[str, Any]) -> bytes:
    salt = _unb64(str(kdf.get("salt", "")))
    if len(salt) != SALT_BYTES:
        raise MappingError("mapping header is malformed")
    if kdf.get("name") == "scrypt":
        if secret.kind != "passphrase":
            raise MappingError("this mapping was encrypted with a passphrase, not a key file")
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        # Only parameters this version writes: a crafted header must not be able to demand
        # gigabytes of memory or minutes of CPU before decryption fails.
        if not (2**14 <= n <= 2**18 and n & (n - 1) == 0 and r == SCRYPT_R and p == SCRYPT_P):
            raise MappingError("mapping header has unsupported key-derivation parameters")
        raw = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p).derive(secret.value)
    elif kdf.get("name") == "hkdf-sha256":
        if secret.kind != "keyfile":
            raise MappingError("this mapping was encrypted with a key file, not a passphrase")
        raw = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt,
                   info=b"offrecord-mapping-v1").derive(secret.value)  # fmt: skip
    else:
        raise MappingError("mapping header names an unknown key-derivation function")
    return base64.urlsafe_b64encode(raw)


def new_kdf(secret: Secret, scrypt_n: int = SCRYPT_N) -> dict[str, Any]:
    salt = _b64(secrets.token_bytes(SALT_BYTES))
    if secret.kind == "passphrase":
        return {"name": "scrypt", "n": scrypt_n, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt}
    return {"name": "hkdf-sha256", "salt": salt}


def encrypt(kind: str, file_id: str, payload: dict[str, Any], secret: Secret,
            scrypt_n: int = SCRYPT_N) -> bytes:  # fmt: skip
    kdf = new_kdf(secret, scrypt_n)
    key = derive_key(secret, kdf)
    body = json.dumps({"id": file_id, "version": FORMAT_VERSION, "kind": kind, "payload": payload},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")  # fmt: skip
    token = Fernet(key).encrypt(body)
    header = json.dumps({"id": file_id, "kdf": kdf}, separators=(",", ":"))
    return f"{MAGIC[kind]} {FORMAT_VERSION}\n{header}\n".encode("ascii") + token + b"\n"


def read_header(data: bytes, kind: str) -> dict[str, Any]:
    try:
        first, header, _token = data.split(b"\n", 2)
        magic, version = first.decode("ascii").split(" ")
        hdr = json.loads(header.decode("ascii"))
    except (ValueError, UnicodeError):
        raise MappingError("not an offrecord mapping file, or it is corrupted") from None
    if magic != MAGIC[kind]:
        raise MappingError(f"not an offrecord {kind} file")
    if int(version) != FORMAT_VERSION:
        raise MappingError("unsupported mapping file version")
    if not isinstance(hdr, dict) or "id" not in hdr or "kdf" not in hdr:
        raise MappingError("mapping header is malformed")
    return hdr


def decrypt(data: bytes, kind: str, secret: Secret) -> tuple[str, dict[str, Any]]:
    hdr = read_header(data, kind)
    token = data.split(b"\n", 2)[2].strip()
    key = derive_key(secret, hdr["kdf"])
    try:
        body = json.loads(Fernet(key).decrypt(token).decode("utf-8"))
    except InvalidToken:
        raise MappingError(
            "cannot decrypt: wrong passphrase/key, or the file was modified"
        ) from None
    except (ValueError, UnicodeError):
        raise MappingError("decrypted mapping is malformed") from None
    if (
        body.get("id") != hdr["id"]
        or body.get("version") != FORMAT_VERSION
        or body.get("kind") != kind
    ):
        raise MappingError("mapping header does not match its encrypted contents")
    return hdr["id"], body["payload"]


# --- secrets ---------------------------------------------------------------------------------


def load_key_file(path: str | os.PathLike[str]) -> Secret:
    """Open first (no symlinks, non-blocking), then check the open descriptor: regular file,
    owned by the current user, not accessible to others."""
    p = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(p, flags)
    except FileNotFoundError:
        raise UsageError("key file not found") from None
    except OSError:
        raise UsageError("cannot open key file (symlinks are not accepted)") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UsageError("key file is not a regular file")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise UsageError("key file is owned by another user")
        if st.st_mode & 0o077:
            raise UsageError("key file is readable by other users; run: chmod 600 <key file>")
        data = os.read(fd, 4096)
    finally:
        os.close(fd)
    try:
        raw = base64.b64decode(data.decode("ascii").strip(), validate=True)
    except (ValueError, UnicodeError):
        raise UsageError("key file is not a valid offrecord key") from None
    if len(raw) != KEY_BYTES:
        raise UsageError("key file is not a valid offrecord key")
    return Secret("keyfile", raw, p.resolve(), source="file")


def generate_key() -> str:
    return _b64(secrets.token_bytes(KEY_BYTES)) + "\n"


def get_secret(key_file: str | None, confirm: bool = False) -> Secret | None:
    """Key file (argument or env), else passphrase (env or interactive prompt). Never argv."""
    kf = key_file or os.environ.get(KEY_FILE_ENV)
    env_pw = os.environ.get(PASSPHRASE_ENV)
    if kf and env_pw is not None:
        raise UsageError(f"both a key file and {PASSPHRASE_ENV} are set; use only one")
    if kf:
        return load_key_file(kf)
    pw = env_pw
    source = "env"
    if pw is None:
        pw = _prompt(confirm)
        source = "prompt"
    if pw is None:
        return None
    if len(pw) < MIN_PASSPHRASE:
        raise UsageError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    return Secret("passphrase", pw.encode("utf-8"), source=source)


def _prompt(confirm: bool) -> str | None:
    import getpass
    import sys

    if not sys.stdin.isatty() and not _has_tty():
        return None
    try:
        pw = getpass.getpass("offrecord passphrase: ")
        if confirm and getpass.getpass("repeat passphrase: ") != pw:
            raise UsageError("passphrases do not match")
    except (EOFError, KeyboardInterrupt):
        return None
    return pw


def _has_tty() -> bool:
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def check_separation(mapping_path: Path, secret: Secret) -> None:
    """Never store the mapping next to the key: not in the key's directory or anywhere below it
    (a key kept under ~/.config while the mapping goes to ~ is fine)."""
    if secret.key_path is None:
        return
    mdir = mapping_path.resolve().parent
    kdir = secret.key_path.resolve().parent
    if mdir == kdir or kdir in mdir.parents:
        raise UsageError("refusing to write the mapping inside the key file's directory")
