"""Input limits. Checked before and during parsing; errors name the limit and position only."""

from __future__ import annotations

from dataclasses import dataclass

from offrecord.errors import InputLimitError

MiB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 50 * MiB
    max_line_chars: int = 200_000
    max_turns: int = 200_000
    max_turn_chars: int = 1_000_000
    max_json_depth: int = 64
    max_metadata_fields: int = 1_000

    def check_size(self, n_bytes: int) -> None:
        if n_bytes > self.max_bytes:
            raise InputLimitError(
                f"input is {n_bytes} bytes; limit is {self.max_bytes} (max_bytes)"
            )

    def check_lines(self, text: str) -> None:
        """Raise if any line is longer than ``max_line_chars``. Reports the 1-based line number."""
        limit = self.max_line_chars
        start = 0
        line_no = 1
        n = len(text)
        while start <= n:
            nl = text.find("\n", start)
            end = n if nl == -1 else nl
            if end - start > limit:
                raise InputLimitError(
                    f"line {line_no} has {end - start} characters; "
                    f"limit is {limit} (max_line_chars)"
                )
            if nl == -1:
                break
            start = nl + 1
            line_no += 1

    def check_turn_count(self, n_turns: int) -> None:
        if n_turns > self.max_turns:
            raise InputLimitError(f"input has more than {self.max_turns} turns (max_turns)")

    def check_turn_chars(self, turn_index: int, n_chars: int) -> None:
        if n_chars > self.max_turn_chars:
            raise InputLimitError(
                f"turn {turn_index} has {n_chars} characters; "
                f"limit is {self.max_turn_chars} (max_turn_chars)"
            )


DEFAULT_LIMITS = Limits()
