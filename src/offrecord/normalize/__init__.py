"""Normalisation: Document -> Fields (orig + norm views), speaker unification, windows."""

from offrecord.normalize.fields import Window, build_fields, iter_windows, speaker_keys

__all__ = ["Window", "build_fields", "iter_windows", "speaker_keys"]
