# Measuring offrecord on your own transcripts

Every number in the README comes from synthetic transcripts. The only way to know how
offrecord does on your calls is to label a few of your own and score them. This page explains
how to do that without the transcripts or the results leaving your machine.

## Where to keep them

Make a folder outside the repository, for example `~/offrecord-private/`. Real transcripts
must never go inside the repository's tracked folders. If you prefer to keep them next to the
code, use `private/` at the repository root, which is git-ignored; `offrecord eval` warns if
you point it at a folder that could be committed.

Treat the folder like the transcripts themselves: keep it on an encrypted disk, out of cloud
sync, and delete it when you are done. Files that offrecord writes there are owner-only.

## How many, and which

Start with 10 transcripts. Pick the kind you actually plan to redact, and include a few hard
ones: lots of names, figures read aloud, people describing a situation rather than naming it.
Ten is enough to find the categories offrecord misses for you. It is not enough for precise
percentages, since with 10 transcripts each one moves the clean-transcript rate by 10 points.
Add more later if you want firmer numbers.

## Labelling

For each transcript, let offrecord write a draft and correct it:

```sh
offrecord review call.vtt --draft-labels -o ~/offrecord-private/call01.txt
```

The draft is plain text with offrecord's redactions marked inline, such as
`[[PERSON:Dana Whitfield]]` or `[[AMOUNT:$40k]]`. The format is described in
`docs/eval-format.md`, section 2. Open it in an editor and go through every line:

- Add markup around anything sensitive that offrecord missed. Misses are the point of the
  exercise, so read the unmarked text carefully: the draft makes it easy to accept what the
  tool did and overlook what it did not.
- Remove the markup from anything that should not have been redacted, or change it to
  `[[NEG:word]]` to record that it must stay visible.
- Fix wrong categories. Use the categories listed in `docs/eval-format.md`.
- Resolve every `[[CATEGORY?:...]]`. These are guesses (unknown words, review flags) that
  you have to decide on: change them to a real category, or remove the markup. The evaluation
  refuses to run while any are left, and the error names the line.

The labelling conventions in `eval/tuning_handwritten/README.md` cover the common judgement
calls: partial numbers, relative dates, which organisations count as clients, and so on.

To score with the same deny-list you use day to day, copy your config into the folder as
`denylist.yaml` (it is as sensitive as the transcripts).

## Scoring

```sh
offrecord eval --dir ~/offrecord-private
offrecord eval --dir ~/offrecord-private --system tier1-ensemble   # if Tier 1 is installed
```

The output contains numbers only, never transcript text: PII recall and the share of
transcripts with no personal data left (each with a 95% interval), recall per category,
character precision, how much of the other labelled items (figures, organisations, business
topics) was redacted or flagged, the clean-transcript rate overall and per category, and leaks
per transcript. Nothing is written to disk unless you redirect it, so keep any copy in the
private folder.

## Reading the results

- PII recall and the PII-clean rate are the numbers that matter most: they say how much
  personal data got through. Recall per category tells you which kinds; a category well below
  the synthetic numbers is worth a deny-list entry or a closer review habit.
- Label business context too (amounts, organisations, business topics) if you care about it.
  With the default protection it is flagged rather than redacted, and "other r/f" tells you
  how much of it was redacted or flagged. A flag only asks you to look; it protects nothing.
- The clean-transcript rate is the share of transcripts where nothing at all leaked. It is
  the honest answer to "could I paste this without checking it?", and on real data it will
  usually be low. Use it to decide how carefully to review, not whether to.
- High severity means a name, contact detail, account, card or SSN. If that rate is below
  100%, check those categories in every transcript before sharing.
- Precision tells you how much ordinary text was blanked out. Low precision makes the LLM's
  answer worse but leaks nothing.

`offrecord review call.vtt` shows the original and redacted text side by side, with review
flags marked, when you want to see what happened in a particular transcript.
