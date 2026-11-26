"""Compression after redaction.

Levels:
- light: deterministic cleanup only: fillers, stutters and false starts removed; standalone
  backchannel turns dropped; consecutive turns by the same speaker merged.
- medium: light + near-duplicate removal (in-house MinHash) + heuristic triage (opening/closing
  small talk, short turns with no entities or numbers).
- aggressive: medium + extractive selection with Tier 1 sentence embeddings: within windows of
  turns, the least central turns are dropped (degree centrality on cosine similarity). It only
  selects real turns, never generates text, and never drops a turn holding a placeholder's last
  occurrence, a number, or a negation/decision word.

Invariants (checked, not assumed):
- Compression only deletes text; it never adds or rewrites words, so removed (redacted) text can
  never come back.
- A placeholder is never altered, and no turn is dropped if that would remove the last
  occurrence of a placeholder.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from offrecord.adapters._common import speaker_key
from offrecord.model import Document, Turn

LEVELS = ("none", "light", "medium", "aggressive")
SEMANTIC_DUP = 0.97  # cosine similarity for embedding-based dedupe (aggressive only)
AGGRESSIVE_KEEP = 0.5  # keep the most central half of droppable turns
AGGRESSIVE_WINDOW = 200
PLACEHOLDER = re.compile(r"<[A-Z]{1,6}\d+>")

# Fillers removed anywhere (case-insensitive, whole words). Multi-word fillers only when set off
# by commas or at a turn start, so "I mean it" and "you know the answer" survive.
_FILLER_WORDS = r"(?:um+|uh+|er+m?|ah+|hmm+|mm+|mhm)"
FILLER_RE = re.compile(rf"(?i)(?<![\w<-])(?:{_FILLER_WORDS})(?![\w>-])[,.]?\s*")
SOFT_FILLER_RE = re.compile(
    r"(?i)(?:(?<=^)|(?<=[,.;!?]\s))(?:you know|i mean|like|sort of|kind of|basically|literally|"
    r"so yeah|okay so|well)\s*,\s*"
)
BACKCHANNEL = frozenset(
    "yeah yep yup mm-hmm mhm uh-huh right ok okay sure cool great nice totally exactly "
    "gotcha got it i see absolutely definitely perfect awesome alright all right sounds good "
    "makes sense for sure thanks thank you true fair hmm uh huh mm".split()
)
BACKCHANNEL_PHRASES = frozenset(
    {"got it", "i see", "all right", "sounds good", "makes sense", "for sure", "thank you",
     "uh huh", "mm hmm", "oh okay", "oh ok", "oh right", "oh yeah"}
)  # fmt: skip
# Words whose presence or absence changes meaning: never collapsed, deduplicated across, or
# dropped as small talk ("No.", "Wait.", "never").
MEANING_WORDS = frozenset(
    "no not never none nor neither nothing nobody nowhere don't doesn't didn't won't wouldn't "
    "can't cannot couldn't shouldn't isn't aren't wasn't weren't haven't hasn't hadn't without "
    "yes stop wait cancel hold approve approved reject rejected decline declined agree agreed "
    "disagree confirm confirmed deny denied refuse refused veto go-ahead ahead proceed let's "
    "lets we'll decided decide decision".split()
)
_WORD = re.compile(r"[\w'-]+")
SMALL_TALK = re.compile(
    r"(?i)\b(?:weekend|weather|rain(?:ing)?|sunny|hear me|can you hear|you're on mute|on mute|"
    r"muted|good morning|good afternoon|morning|how are you|how's it going|thanks for joining|"
    r"thanks for making time|let's jump in|let's get started|let's start|bye|goodbye|"
    r"talk soon|take care|have a good|have a great|see you|cheers|holiday|vacation|traffic|"
    r"coffee|kids|echo|connection|lagging|frozen|camera|share my screen)\b"
)
EDGE_TURNS = 6  # opening/closing window for small-talk triage
SHORT_TURN_WORDS = 3
DUP_THRESHOLD = 0.8
NUM_HASHES = 64
SHINGLE = 3


@dataclass
class CompressionStats:
    level: str
    dropped_turns: list[int] = field(default_factory=list)  # original indices (positions only)
    turns_before: int = 0
    turns_after: int = 0
    dropped_backchannel: int = 0
    dropped_duplicates: int = 0
    dropped_small_talk: int = 0
    dropped_short: int = 0
    merged: int = 0


def _placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text)


def _protect(text: str) -> tuple[str, list[str]]:
    """Swap placeholders for inert tokens so no regex can touch them."""
    found: list[str] = []

    def keep(m: re.Match[str]) -> str:
        found.append(m.group())
        return f"\x00{len(found) - 1}\x00"

    return PLACEHOLDER.sub(keep, text), found


def _unprotect(text: str, found: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: found[int(m.group(1))], text)


def _collapse_tokens(text: str) -> str:
    """False starts ("I was- I am") and stutters ("the the"), in one linear pass over
    whitespace-separated tokens. Numbers, yes/no and negations are never collapsed."""
    toks = text.split(" ")
    out: list[str] = []
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if tok.endswith("-") and len(tok) > 1 and nxt[:1].isalpha():
            continue  # false start
        if out:
            a = out[-1].rstrip(",").lower()
            b = tok.rstrip(",").lower()
            same = a and a == b and _WORD.fullmatch(b) is not None
            if same and not any(c.isdigit() for c in b) and b not in MEANING_WORDS:
                if out[-1].endswith(",") and not tok.endswith(","):
                    out[-1] = out[-1][:-1]
                continue
        out.append(tok)
    return " ".join(out)


def _restorable(text: str) -> list[str]:
    """Everything restore would treat as a bracketed/escaped placeholder (its tolerant grammar)."""
    from offrecord.restore import TOKEN_RE

    return sorted(m.group(0) for m in TOKEN_RE.finditer(text) if not m.group("bare"))


def clean_text(text: str) -> str:
    """Light cleanup of one turn's text (deletions only)."""
    t, found = _protect(text)
    t = FILLER_RE.sub("", t)
    t = SOFT_FILLER_RE.sub("", t)
    t = _collapse_tokens(t)
    t = re.sub(r"\s*,(\s*,)+", ",", t)
    t = re.sub(r"^\s*[,.;]\s*", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    out = _unprotect(t, found)
    same_ph = sorted(_placeholders(out)) == sorted(_placeholders(text))
    if not same_ph or _restorable(out) != _restorable(text):
        # Never lose, alter or create a placeholder ("< um P1>" must not become "< P1>").
        return text
    return out


def _is_backchannel(text: str) -> bool:
    words = re.sub(r"[^\w\s'-]", " ", text.lower()).split()
    if not words or len(words) > 4 or _placeholders(text):
        return False
    phrase = " ".join(words)
    if phrase in BACKCHANNEL_PHRASES:
        return True
    return all(w in BACKCHANNEL or w in ("oh", "and", "so") for w in words)


def _has_content(text: str) -> bool:
    if _placeholders(text) or any(c.isdigit() for c in text):
        return True
    return any(w.lower() in MEANING_WORDS for w in _WORD.findall(text))


def _meaning_key(text: str) -> tuple[frozenset[str], frozenset[str]]:
    words = [w.lower() for w in _WORD.findall(text)]
    negs = frozenset(w for w in words if w in MEANING_WORDS)
    nums = frozenset(w for w in words if any(c.isdigit() for c in w))
    return negs, nums


def _shingles(text: str) -> set[str]:
    words = re.sub(r"[^\w<>\s]", " ", text.lower()).split()
    if len(words) < SHINGLE:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def _hash(s: str, salt: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8, salt=salt).digest(), "little")


def _minhash(sh: set[str]) -> tuple[int, ...]:
    if not sh:
        return ()
    sig = []
    for seed in range(NUM_HASHES):
        salt = seed.to_bytes(2, "little")
        sig.append(min(_hash(s, salt) for s in sh))
    return tuple(sig)


def _similar(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def compress(doc: Document, level: str, embedder=None) -> tuple[Document, CompressionStats]:
    """``doc`` must already be redacted. ``embedder`` (Tier 1) enables semantic dedupe and is
    required for ``aggressive``."""
    if level not in LEVELS:
        raise ValueError("unknown compression level")
    if level == "aggressive" and embedder is None:
        raise ValueError("aggressive compression needs Tier 1 embeddings")
    stats = CompressionStats(level, turns_before=len(doc.turns))
    if level == "none":
        stats.turns_after = len(doc.turns)
        return doc, stats
    total = Counter(p for t in doc.turns for p in _placeholders(t.text))
    remaining = Counter(total)

    def can_drop(text: str) -> bool:
        return all(remaining[p] > n for p, n in Counter(_placeholders(text)).items())

    def drop(t: Turn) -> None:
        stats.dropped_turns.append(t.index)
        remaining.subtract(_placeholders(t.text))

    turns: list[Turn] = []
    for t in doc.turns:
        text = clean_text(t.text)
        if not text or (_is_backchannel(text) and can_drop(t.text)):
            if text:
                stats.dropped_backchannel += 1
            drop(t)
            continue
        turns.append(replace(t, text=text))

    if level in ("medium", "aggressive"):
        # medium behaves the same at every tier (MinHash); embeddings are used only by aggressive.
        turns = _triage(turns, stats, can_drop, drop)
        turns = _dedupe(turns, stats, can_drop, drop)
    if level == "aggressive":
        turns = _semantic_dedupe(turns, stats, can_drop, drop, embedder)
        turns = _extract(turns, stats, can_drop, drop, embedder)

    merged: list[Turn] = []
    for t in turns:
        if (
            merged
            and t.speaker is not None
            and merged[-1].speaker is not None
            and (speaker_key(merged[-1].speaker) == speaker_key(t.speaker))
        ):
            prev = merged[-1]
            merged[-1] = replace(prev, text=f"{prev.text} {t.text}".strip(), end=t.end or prev.end)
            stats.merged += 1
            continue
        merged.append(t)
    merged = [replace(t, index=i) for i, t in enumerate(merged)]
    stats.turns_after = len(merged)

    after = Counter(p for t in merged for p in _placeholders(t.text))
    lost = set(total) - set(after)
    if lost:  # pragma: no cover - guarded by construction
        raise AssertionError("compression dropped a placeholder")
    return replace(doc, turns=tuple(merged)), stats


def _triage(turns: Sequence[Turn], stats: CompressionStats, can_drop, drop) -> list[Turn]:
    n = len(turns)
    out = []
    for i, t in enumerate(turns):
        edge = i < EDGE_TURNS or i >= n - EDGE_TURNS
        words = len(t.text.split())
        if (
            edge
            and SMALL_TALK.search(t.text)
            and not _has_content(t.text)
            and words <= 20
            and can_drop(t.text)
        ):
            stats.dropped_small_talk += 1
            drop(t)
            continue
        if words <= SHORT_TURN_WORDS and not _has_content(t.text) and can_drop(t.text):
            stats.dropped_short += 1
            drop(t)
            continue
        out.append(t)
    return out


BANDS = 16  # LSH: 16 bands x 4 rows of the 64-value signature; candidates share a band


def _dedupe(turns: Sequence[Turn], stats: CompressionStats, can_drop, drop) -> list[Turn]:
    """Drop later near-duplicate turns. MinHash + LSH banding, so cost is linear in turns."""
    out: list[Turn] = []
    sigs: list[tuple[int, ...]] = []
    meaning: list[tuple[frozenset[str], frozenset[str]]] = []
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    rows = NUM_HASHES // BANDS
    for t in turns:
        sh = _shingles(t.text)
        sig = _minhash(sh) if len(sh) >= 2 else ()
        if sig:
            keys = [(b, sig[b * rows : (b + 1) * rows]) for b in range(BANDS)]
            cands = {i for k in keys for i in buckets.get(k, ())}
            mk = _meaning_key(t.text)
            # A near-duplicate only if negations and numbers are identical ("should take" vs
            # "should never take" is not a duplicate).
            dup = any(_similar(sig, sigs[i]) >= DUP_THRESHOLD and meaning[i] == mk for i in cands)
            if dup and can_drop(t.text):
                stats.dropped_duplicates += 1
                drop(t)
                continue
            for k in keys:
                buckets.setdefault(k, []).append(len(sigs))
            sigs.append(sig)
            meaning.append(mk)
        out.append(t)
    return out


def _semantic_dedupe(turns: Sequence[Turn], stats: CompressionStats, can_drop, drop,
                     embedder) -> list[Turn]:  # fmt: skip
    """Embedding-based near-duplicate removal within windows (memory stays flat)."""
    out: list[Turn] = []
    for w0 in range(0, len(turns), AGGRESSIVE_WINDOW):
        chunk = list(turns[w0 : w0 + AGGRESSIVE_WINDOW])
        vecs = embedder.embed([t.text for t in chunk])
        kept: list[int] = []
        for i, t in enumerate(chunk):
            mk = _meaning_key(t.text)
            dup = any(
                float(vecs[i] @ vecs[j]) >= SEMANTIC_DUP
                and _meaning_key(chunk[j].text) == mk
                and _same_content_words(t.text, chunk[j].text)
                for j in kept
            )
            if dup and len(t.text.split()) >= 4 and can_drop(t.text):
                stats.dropped_duplicates += 1
                drop(t)
                continue
            kept.append(i)
            out.append(t)
    return out


def _content_words(text: str) -> set[str]:
    from offrecord.redact.wordlists import FUNCTION_WORDS

    fillers = {"um", "uh", "like", "so", "well", "just", "really", "actually", "basically"}
    return {w.lower() for w in _WORD.findall(text)} - FUNCTION_WORDS - fillers


def _same_content_words(a: str, b: str) -> bool:
    """Embeddings see "earlier" and "later" as near-identical; a duplicate may differ only in
    fillers and function words."""
    return _content_words(a) == _content_words(b)


def _extract(
    turns: Sequence[Turn], stats: CompressionStats, can_drop, drop, embedder
) -> list[Turn]:
    out: list[Turn] = []
    for w0 in range(0, len(turns), AGGRESSIVE_WINDOW):
        chunk = list(turns[w0 : w0 + AGGRESSIVE_WINDOW])
        if len(chunk) < 4:
            out.extend(chunk)
            continue
        vecs = embedder.embed([t.text for t in chunk])
        sims = vecs @ vecs.T
        centrality = sims.sum(axis=1)
        # Never drop content, questions, or a turn whose reply carries meaning ("Why?" / "No.").
        droppable = [
            i
            for i, t in enumerate(chunk)
            if not _has_content(t.text)
            and "?" not in t.text
            and not (
                i + 1 < len(chunk) and (_has_content(chunk[i + 1].text) or "?" in chunk[i + 1].text)
            )
        ]
        n_drop = int(len(droppable) * (1 - AGGRESSIVE_KEEP))
        drop_set = set(sorted(droppable, key=lambda i: float(centrality[i]))[:n_drop])
        for i, t in enumerate(chunk):
            if i in drop_set and can_drop(t.text):
                stats.dropped_short += 1
                drop(t)
                continue
            out.append(t)
    return out
