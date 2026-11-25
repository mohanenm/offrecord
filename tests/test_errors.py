from __future__ import annotations

from offrecord.errors import InputLimitError
from offrecord.safe_errors import format_exception, set_debug

CANARY = "ZQXCANARY7731"


def test_internal_error_withholds_message():
    try:
        raise ValueError(f"secret {CANARY}")
    except ValueError as e:
        for dbg in (False, True):
            set_debug(dbg)
            text = format_exception(e)
            assert CANARY not in text
            assert "ValueError" in text
    set_debug(False)


def test_offrecord_error_format():
    text = format_exception(InputLimitError("line 3 has 9 characters; limit is 5"))
    assert text == "error[E_INPUT_LIMIT]: line 3 has 9 characters; limit is 5"


def test_thread_and_unraisable_and_logging_paths_are_content_free(capfd):
    import gc
    import logging
    import threading

    from offrecord.safe_errors import install_hooks

    install_hooks()

    def boom():
        raise ValueError(CANARY + "-thread")

    t = threading.Thread(target=boom)
    t.start()
    t.join()

    class Bad:
        def __del__(self):
            raise KeyError(CANARY + "-del")

    Bad()
    gc.collect()
    logging.getLogger("somelib").error("parse failed on %s", CANARY + "-log")
    err = capfd.readouterr().err
    assert CANARY not in err
    assert "ValueError" in err


def test_usage_messages_drop_values():
    from offrecord.cli import sanitize_usage_message as s

    assert CANARY not in s(f"unrecognized arguments: --jsn {CANARY}-acme.vtt")
    assert s(f"unrecognized arguments: a {CANARY}") == "unrecognized arguments (2)"
    out = s(f"argument --set: invalid choice: '{CANARY}' (choose from 'tuning')")
    assert out == "argument --set: invalid choice"
    assert CANARY not in s(f"something odd: '{CANARY}'")
