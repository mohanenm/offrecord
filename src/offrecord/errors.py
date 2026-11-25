"""Error types and exit codes.

Rule: an error message must never contain transcript, config or mapping content. Messages are
built only from fixed templates plus positions (line, turn, offset), sizes, limit names and
categories. Third-party exception messages are never shown (they may embed input bytes).
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INTERNAL = 1  # unexpected exception (bug)
    USAGE = 2  # bad command-line usage (argparse also uses 2)
    INPUT = 3  # unreadable, malformed or over-limit input
    OUTPUT = 4  # output exists without --force, or cannot be written
    MAPPING = 5  # wrong mapping, decryption failure, mapping format error
    ENVIRONMENT = 6  # doctor found a failing check, missing optional component
    NETWORK = 7  # a network attempt was blocked at run time
    NOT_IMPLEMENTED = 64  # command exists but is not built yet
    INTERRUPTED = 130  # SIGINT / SIGTERM


class OffrecordError(Exception):
    """Base class for expected, user-facing errors.

    ``code`` is a short stable identifier (e.g. ``E_INPUT_LIMIT``). ``detail`` must be built
    from positions, sizes and categories only.
    """

    exit_code: ExitCode = ExitCode.INTERNAL
    code: str = "E_OFFRECORD"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class UsageError(OffrecordError):
    exit_code = ExitCode.USAGE
    code = "E_USAGE"


class InputError(OffrecordError):
    exit_code = ExitCode.INPUT
    code = "E_INPUT"


class InputLimitError(InputError):
    code = "E_INPUT_LIMIT"


class InputDecodeError(InputError):
    code = "E_INPUT_DECODE"


class InputFormatError(InputError):
    code = "E_INPUT_FORMAT"


class OutputError(OffrecordError):
    exit_code = ExitCode.OUTPUT
    code = "E_OUTPUT"


class OutputExistsError(OutputError):
    code = "E_OUTPUT_EXISTS"


class MappingError(OffrecordError):
    exit_code = ExitCode.MAPPING
    code = "E_MAPPING"


class EnvironmentCheckError(OffrecordError):
    exit_code = ExitCode.ENVIRONMENT
    code = "E_ENVIRONMENT"


class NetworkBlockedError(OffrecordError, ConnectionError):
    """Raised by the run-time network guard. Subclasses ConnectionError so that libraries
    which handle connection failures fail closed rather than crash in odd ways."""

    exit_code = ExitCode.NETWORK
    code = "E_NETWORK_BLOCKED"


class NotImplementedYetError(OffrecordError):
    exit_code = ExitCode.NOT_IMPLEMENTED
    code = "E_NOT_IMPLEMENTED"
