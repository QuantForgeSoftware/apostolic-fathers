# Morpheus analysis

An independent, **rule/lexicon-based** morphological analysis of the Apostolic
Fathers, produced with [Morpheus](https://github.com/QuantForgeSoftware/morpheus)
(the morphological parser originally written for the Perseus Project).

The existing `data/morph/` analysis is partly machine-generated
(`grc_proiel_lg`) for forms not found in the Greek New Testament. Morpheus is a
different kind of analyzer — a hand-built stem/ending lexicon — so it provides a
useful cross-check, especially for the non-NT vocabulary.

## Method

1. Build Morpheus (`scripts/build.sh` in the Morpheus checkout).
2. Convert each Unicode token to beta code and run `cruncher` in
   accent-insensitive mode (recovers unaccented spellings such as verse-initial
   capitals, and elided forms like δι' that the accented match misses).
3. Rank the candidate analyses by lemma frequency derived from this corpus's own
   gold annotations (`data/morph/`) — Morpheus otherwise orders candidates
   alphabetically by lemma, which often puts the wrong reading first.
4. Map the chosen analysis to the MorphGNT-style POS + parsing codes used in
   `data/morph/`.
5. Rewrite the lemma column to the reference's dictionary-form convention where
   the two differ systematically (reduplication: γίγνομαι→γίνομαι; augment:
   ἐθέλω→θέλω; aorists lemmatized to their base verb: εἶδον→ὁράω,
   εἶπον→λέγω; accent placement). The mapping is derived at run time from the
   `source=MorphGNT` gold lines only, and a pair is applied when it is
   consistent (one target per source lemma), frequent (≥10 tokens) and never
   contradicted by an agreeing token — form-dependent splits (e.g. the
   κύριος/κυρία gender pair) are rejected and left contextual. Every applied
   mapping is listed in `summary.txt`; pass `--no-conventions` for purely
   independent output.
6. Tokens Morpheus cannot analyze keep the corpus's own annotation (their
   original fields, including source); they are listed in a `<name>.fb` sidecar
   file next to each output — so every token has an analysis and the gaps stay
   visible instead of being silently dropped or invented.

Options: `--tagger` disambiguates contiguous token runs with a Viterbi
POS-bigram tagger trained on this corpus's gold POS tags (trades ~1pp of lemma
accuracy for +1–2pp of POS); `--joint` ranks by joint (lemma, POS) frequency.

The wrapper is [morpheus-toolkit](https://github.com/QuantForgeSoftware/morpheus/tree/master/python).

## Files

`data/morph-morpheus/*.txt` mirrors `data/morph/*.txt` line for line in the same
column layout:

```
reference  POS  parsing  text  word  normalized  lemma  language  source
```

For analyzed tokens, POS/parsing/lemma come from Morpheus and the `source`
column is `morpheus`; all other columns are copied through. Fallback tokens keep
their original fields (and original source) and are listed in `<name>.fb`.
Non-Greek lines pass through unchanged. `summary.txt` records the exact
configuration, coverage, convention mappings and fallback breakdown of the
generated files.

`data/onomasticon-af.tsv` lists every proper name in the corpus (`lemma`,
`form`, `count`, `source`), extracted from the `NP`-tagged tokens — the
onomasticon that drove the biblical stemlib additions.

## Reproduce

```bash
# 1. Morpheus + toolkit (master at or after commit 23b33d3, "Round 7")
git clone https://github.com/QuantForgeSoftware/morpheus
cd morpheus && scripts/build.sh && cd ..
pip install "git+https://github.com/QuantForgeSoftware/morpheus#subdirectory=python"

# 2. Generate (the committed files use the defaults: AF frequency ranking,
#    top-1 candidate, convention layer on)
tools/morpheus/analyze.py --morpheus-dir /path/to/morpheus
```

## Agreement with `data/morph/`

The generated files agree with the reference as follows (line-by-line; fallback
tokens copy the reference annotation and are listed in the `.fb` sidecars):

| subset | tokens | lemma | accent-insensitive lemma | POS |
| --- | ---: | ---: | ---: | ---: |
| all Greek | 63,222 | 91.1% | 92.1% | 75.3% |
| `source=MorphGNT` | 52,094 | **96.6%** | **97.4%** | 75.7% |
| `source=grc_proiel_lg` | 11,128 | 65.4% | 67.2% | 73.1% |

At the candidate level (Morpheus's raw output before conventions or fallbacks;
measured with `scripts/agreement_report.py --frequencies …lemma_frequencies_af.json`
from the Morpheus checkout):

| subset | tokens | coverage | top-1 lemma (Morpheus order → ranked) |
| --- | ---: | ---: | ---: |
| all Greek | 63,222 | 91.2% | 84.8% → 90.5% |
| `source=MorphGNT` | 52,094 | 96.5% | 89.8% → 96.0% |
| `source=grc_proiel_lg` | 11,128 | 66.3% | 61.3% → 64.6% |

Notes:

- The previous generation (unranked candidates, no accent-insensitive mode)
  scored 84.6% / 89.7% / 60.5% top-1 lemma on the three rows above; corpus-derived
  ranking plus the convention layer lifts the trustworthy subset to 96.6%.
- The `source=MorphGNT` lines are the trustworthy gold (hand-checked against the
  New Testament); the `grc_proiel_lg` lines come from a machine model and their
  lower agreement partly reflects that source's own noise, not just Morpheus.
- Because step 5 aligns lemma spelling conventions with the reference, the first
  table measures how well "Morpheus + AF tables" follows the reference — not blind
  independence. The candidate-level table (and `--no-conventions`) show the
  independent analyzer's own performance.

## License / provenance

- Morpheus is licensed **MPL-2.0** (Perseus Project; see the Morpheus `LICENSE`).
- `morpheus-toolkit` is MIT.
- The generated analysis files are offered under the same **CC-BY-SA 4.0** terms
  as the rest of `data/`, with attribution to the Perseus Project.
