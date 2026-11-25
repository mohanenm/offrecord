# Your own evaluation set

> **Current contents (2026-11-24):** `u01`-`u08` and `denylist.yaml` were written, at the user's
> request, by an independent agent that read only the markup format docs (never the detectors,
> templates or generated sets). They stand in until you add transcripts in your own phrasing:
> they are not your phrasing. Never tune detectors on this folder. See DECISIONS D-053.

Put a small number of hand-written, **fake** transcripts here, in your own phrasing. They are
evaluated as a third set and reported separately. Detectors are never tuned on them.

- One `.txt` file per transcript, using inline markup for sensitive spans (format:
  `docs/eval-format.md`, section 2):

  ```text
  [[PERSON:Orla Venkatesh]]: Morning. Is [[PERSON#bram:Bram]] joining?
  [[PERSON#bram:Bram Okafor]]: Yes. The [[CLIENT:Quillfeather]] deal closed at [[AMOUNT:$42,000]].
  ```

- Optional `denylist.yaml` in this folder: the deny-list to use for these transcripts.
- Never put real names, companies, numbers or transcripts here.

Run: `offrecord eval --set user` (or `offrecord eval` for all three sets side by side).

A **private** hand-labelled set (real-ish data you keep outside the repo) uses exactly the same
format: `offrecord eval --dir /path/outside/the/repo`.
