# Proposal 0002: encryption and key handling (mapping file and audit log)

Status: **approved by the user, 2026-11-24** (D-021).

## Summary

- **Cipher**: Fernet (`cryptography`), as specified: AES-128-CBC + HMAC-SHA256, authenticated.
- **Key from passphrase**: scrypt, **N=2^17, r=8, p=1**, 16-byte random salt per file, 32-byte
  output → Fernet key. 128 MiB RAM; measured 0.26 s on the dev laptop (Apple Silicon); estimated
  1.5–2.5 s on a Pi 4 (memory-bandwidth bound), once per run. This is the OWASP-recommended
  scrypt minimum. Parameters are stored in the file header, so they can be raised later without
  breaking old files.
- **Key from key file**: 32 random bytes (base64) created by `offrecord keygen`; the key goes
  through HKDF-SHA256 (with the file's salt) rather than scrypt, because it already has full
  entropy.
- **Where secrets come from**: passphrase from an interactive prompt (`getpass`) or the
  `OFFRECORD_PASSPHRASE` environment variable; **never a command-line argument** (visible in the
  process list). Key file via `--key-file PATH` or `OFFRECORD_KEY_FILE`.
- **Where files live**: encrypted mappings and audit logs go to a private per-user store
  (`~/.local/share/offrecord/mappings/<id>.ormap`, dir `0700`, files `0600`) by default, not next
  to the output, so sharing an output folder does not share the mapping. `--mapping-out PATH`
  overrides. The tool **refuses** to write a mapping into the same directory as the key file,
  and refuses a key file that is group/world readable.
- **No silent one-way runs**: if neither a passphrase nor a key file is available and
  `--no-mapping` is not given, `redact` fails with a clear error instead of discarding the
  mapping.
- **Audit log**: off by default; `--audit` turns it on; same encryption and key, same private
  store, `<id>.oraudit`. `offrecord audit show` decrypts to stdout. Never written unencrypted.

## File format (version 1)

```
OFFRECORD-MAP 1\n                       # magic + format version (audit: OFFRECORD-AUDIT 1)
{"id": "<mapping id>", "kdf": {"name": "scrypt", "n": 131072, "r": 8, "p": 1,
 "salt": "<b64>"}}\n                    # header: JSON, no content, no timestamps
<Fernet token>\n
```

- Mapping ID: 128 random bits, base32 without padding (26 chars), e.g. `or1-…`. Nothing derived
  from content, file names or time.
- The ID and format version are repeated **inside** the encrypted payload and checked after
  decryption, so tampering with the plaintext header (or swapping headers between files) is
  detected.
- The decrypted payload is JSON: `{id, version, scope, entries: {placeholder: {category,
  canonical, occurrences: [surface forms]}}}`.

## Known limitations (to be documented)

- A Fernet token carries its creation time in clear (seconds). Low sensitivity; noted.
- Python cannot zero memory: the passphrase, derived key and decrypted mapping live in process
  memory until garbage collected (see security.md R10).
- Strength against offline guessing depends on the passphrase; `keygen` key files avoid this.

## Alternatives

| Choice | Alternative | Why not | Reversibility |
| --- | --- | --- | --- |
| Fernet | AES-256-GCM (`cryptography` AESGCM) | Spec names Fernet; Fernet is misuse-resistant (no nonce handling). AES-128 is ample. | Easy: header carries format version |
| scrypt N=2^17 | N=2^15 (32 MiB, ~0.4 s on Pi) | Below the OWASP minimum for passphrases | Easy: params in header |
| scrypt N=2^17 | Argon2id | Needs a new dependency (`argon2-cffi`) | Easy |
| HKDF for key files | scrypt for everything | Wastes 1–2 s on Pi for no security gain | Easy |
| Private store default | Next to output (`out.txt.ormap`) | Sharing a folder would share the encrypted mapping, exposing it to offline passphrase guessing | Easy: a flag |
| Env var / prompt | `--passphrase` flag | Leaks via process list and shell history | — |
