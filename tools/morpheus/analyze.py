#!/usr/bin/env python3
"""
Generate an independent, rule-based morphological analysis of the Apostolic
Fathers with the Morpheus analyzer (the Perseus morphological parser).

For every Greek token this writes the same column layout as `data/morph/`:

  reference  POS  parsing  text  word  normalized  lemma  language  source

POS/parsing/lemma come from Morpheus (the `source` column becomes `morpheus`);
all other columns are copied through, and non-Greek lines pass through
unchanged. Tokens Morpheus cannot analyze keep the corpus's own annotation
(their original fields) and are listed in a `<name>.fb` sidecar file next to
each output — so every token has an analysis, and the gaps stay visible.

Method:
  * every distinct word form is analyzed with Morpheus in accent-insensitive
    mode (recovers unaccented spellings such as verse-initial capitals);
  * candidates are ranked by lemma frequency derived from this corpus's own
    gold annotations — Morpheus otherwise orders candidates alphabetically by
    lemma, which often puts the wrong reading first;
  * with --conventions (the default) the lemma column is additionally rewritten
    to the reference's dictionary-form convention where the two differ
    systematically (reduplication, augment, aorist-of-base-verb lemmas, accent
    placement). The mapping is derived at run time from the MorphGNT-derived
    gold lines only: a pair is applied when it is consistent (one target per
    source lemma), frequent (>=10 tokens) and never contradicted by an
    agreeing token. Every applied mapping is listed in summary.txt; use
    --no-conventions for purely independent output.
  * with --tagger, contiguous token runs are disambiguated with a Viterbi
    POS-bigram tagger whose model is derived from this corpus's gold POS tags
    (trades ~1pp of lemma accuracy for +1-2pp of POS);
  * with --joint, candidates rank by joint (lemma, POS) frequency.

Requirements (see tools/morpheus/README.md): a built Morpheus checkout and the
morpheus-toolkit Python package (master branch).

Usage:
  tools/morpheus/analyze.py --morpheus-dir /path/to/morpheus [--no-conventions]
      [--tagger] [--joint] [--only data/morph/011-didache.txt]
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import sys
import unicodedata

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    from morpheus_toolkit.api import Morpheus
    from morpheus_toolkit.beta import normalize_lemma, to_beta
    from morpheus_toolkit.morphgnt import parsing_code, pos_code
    from morpheus_toolkit.ranking import Ranker
    from morpheus_toolkit.tagger import PosTagger
except ImportError:
    sys.exit(
        "morpheus-toolkit is not installed. See tools/morpheus/README.md for install "
        "instructions (pip install the python/ subdirectory of a Morpheus checkout)."
    )

# A convention pair is applied only when at least this many gold tokens agree.
MIN_CONVENTION_TOKENS = 10


def deacc(s: str) -> str:
    """Lowercase and strip all diacritics (incl. breathings)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", s.lower())
                   if not unicodedata.combining(ch))


def is_token_line(line: str) -> bool:
    fields = line.split()
    return len(fields) >= 9 and fields[7] == "grc"


def pos_compatible(chosen_pos: str, gold_code: str) -> bool:
    """Coarse POS agreement between a Morpheus analysis and the reference code."""
    g = gold_code.rstrip("-")
    if len(g) == 1:
        return chosen_pos[0] == g and not (g == "N" and chosen_pos in ("A",))
    return chosen_pos == g


def derive_tables(per_file_lines, joint=False):
    """Lemma frequencies (+ joint counts) from the corpus's own gold.

    Counts every Greek line; with ``joint`` also builds the POS bigram model
    over the same lines (one chain per file, as in Morpheus's build_pos_model).
    Returns (freq_data, bigram_model_or_None).
    """
    counts: collections.Counter = collections.Counter()
    by_pos: dict = collections.defaultdict(collections.Counter)
    unigram: collections.Counter = collections.Counter()
    bigram: dict = collections.defaultdict(collections.Counter)
    start: collections.Counter = collections.Counter()
    for lines in per_file_lines.values():
        previous = "<s>"
        for line in lines:
            if not is_token_line(line):
                continue  # non-Greek lines do not break the chain
            fields = line.split()
            lemma = normalize_lemma(fields[6])
            pos = fields[1].rstrip("-")
            counts[lemma] += 1
            by_pos[lemma][pos] += 1
            unigram[pos] += 1
            bigram[previous][pos] += 1
            if previous == "<s>":
                start[pos] += 1
            previous = pos

    model = None
    if joint:
        model = {
            "source": "Apostolic Fathers reference corpus",
            "unigrams": dict(unigram),
            "bigrams": {key: dict(value) for key, value in bigram.items()},
            "starts": dict(start),
        }
    return (
        {"frequencies": dict(counts.most_common()),
         "by_pos": {lemma: dict(counter) for lemma, counter in by_pos.items()}},
        model,
    )


def derive_conventions(per_file_lines, choices_by_file):
    """Derive the reference's lemma-convention table from its own gold.

    A source lemma enters the table only when all of its mismatching
    MorphGNT-derived tokens map to a single target (>= MIN_CONVENTION_TOKENS)
    and no such token agrees with the gold — otherwise the split is
    form-dependent (e.g. a gender or enclitic pair) and rewriting every
    occurrence would break tokens where Morpheus already agrees.

    Returns (table, considered, rejected).
    """
    targets = collections.defaultdict(collections.Counter)   # src key -> Counter(tgt keys)
    surfaces = collections.defaultdict(collections.Counter)  # src key -> Counter(raw gold forms)
    matching = collections.Counter()                         # src key -> agreeing lines
    considered = 0
    for path, lines in per_file_lines.items():
        choices = choices_by_file[path]
        for line, chosen in zip(lines, choices):
            if not is_token_line(line):
                continue
            fields = line.split()
            if "MorphGNT" not in fields[8]:
                continue  # only the trustworthy gold defines conventions
            if chosen is None or chosen.proposed:
                continue
            src_key = deacc(chosen.lemma)
            tgt_key = deacc(fields[6])
            if src_key == tgt_key:
                matching[src_key] += 1  # already agrees (accent-insensitively)
                continue
            if not pos_compatible(chosen.pos, fields[1]):
                continue
            considered += 1
            targets[src_key][tgt_key] += 1
            surfaces[src_key][fields[6]] += 1

    table = {}
    rejected = []
    for src_key, tgt_counts in sorted(targets.items()):
        count = sum(tgt_counts.values())
        if len(tgt_counts) > 1:
            rejected.append((src_key, "multiple targets", count))
        elif count < MIN_CONVENTION_TOKENS:
            rejected.append((src_key, f"< {MIN_CONVENTION_TOKENS} tokens", count))
        elif matching[src_key]:
            rejected.append((src_key, "source also attested in gold",
                             f"{count}/{matching[src_key]}"))
        else:
            table[src_key] = surfaces[src_key].most_common(1)[0][0]
    return table, considered, rejected


def choose_for_file(lines, candidates, tagger):
    """One analysis per line (Viterbi over contiguous runs, or top-ranked)."""
    choices = [None] * len(lines)
    run_items = []  # (line index, candidates)

    def flush_run():
        if not run_items:
            return
        if tagger is None:
            chosen_list = [(cands[0] if cands else None) for _, cands in run_items]
        else:
            chosen_list = tagger.tag([cands for _, cands in run_items])
        for (index, _), chosen in zip(run_items, chosen_list):
            choices[index] = chosen
        run_items.clear()

    for index, line in enumerate(lines):
        if not is_token_line(line):
            flush_run()
            continue
        beta = to_beta(line.split()[4])
        run_items.append((index, candidates.get(beta, [])))
    flush_run()
    return choices


def emit_file(path, lines, choices, conv_table):
    """Write the re-annotated file (+ .fb sidecar); returns (tokens, analyzed)."""
    out_lines = []
    fallback_lines = []
    tokens = 0
    for line, chosen in zip(lines, choices):
        if not is_token_line(line):
            out_lines.append(line)
            continue
        tokens += 1
        fields = line.split()
        if chosen is None or chosen.proposed:
            out_lines.append(line)  # fallback: corpus's own annotation
            fallback_lines.append(len(out_lines))
            continue
        lemma_out = chosen.lemma
        if conv_table and deacc(lemma_out) in conv_table:
            lemma_out = conv_table[deacc(lemma_out)]
        out_lines.append(" ".join([
            fields[0], pos_code(chosen), parsing_code(chosen),
            fields[3], fields[4], fields[5], lemma_out, fields[7], "morpheus",
        ]))

    with open(path, "w", encoding="utf-8") as out:
        out.write("\n".join(out_lines) + "\n")
    if fallback_lines:
        with open(path + ".fb", "w", encoding="utf-8") as out:
            out.write("\n".join(str(n) for n in fallback_lines) + "\n")
    return tokens, tokens - len(fallback_lines), len(fallback_lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--morpheus-dir", required=True, help="path to a built morpheus checkout")
    parser.add_argument("--only", help="analyze a single input file")
    parser.add_argument("--limit", type=int, default=0, help="limit Greek tokens per file (debugging)")
    parser.add_argument(
        "--no-conventions",
        action="store_true",
        help="do not rewrite lemmas to the reference's dictionary-form convention "
             "(purely independent output)",
    )
    parser.add_argument(
        "--tagger",
        action="store_true",
        help="disambiguate with a Viterbi POS-bigram tagger (POS-optimized; "
             "trades ~1pp of lemma on this corpus)",
    )
    parser.add_argument(
        "--joint",
        action="store_true",
        help="rank by joint (lemma, POS) frequency — POS-optimized; trades ~1pp of lemma",
    )
    args = parser.parse_args()

    inputs = [args.only] if args.only else sorted(glob.glob(os.path.join(REPO, "data", "morph", "*.txt")))
    if not inputs:
        parser.error("no input files found under data/morph/")

    # Pass 1: read every file (applying --limit to the Greek token lines).
    per_file_lines = {}
    total_tokens = 0
    for path in inputs:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for line in handle]
        if args.limit:
            kept = 0
            trimmed = []
            for line in lines:
                if is_token_line(line):
                    kept += 1
                    if kept > args.limit:
                        continue
                trimmed.append(line)
            lines = trimmed
        per_file_lines[path] = lines
        total_tokens += sum(1 for line in lines if is_token_line(line))

    # Tables derived from the corpus's own gold (always current with data/morph).
    freq_data, bigram_model = derive_tables(per_file_lines, joint=args.tagger)
    tagger = PosTagger(bigram_model) if args.tagger else None

    morpheus = Morpheus(morpheus_dir=args.morpheus_dir, ignore_accents=True)
    morpheus.ranker = Ranker(
        freq_data["frequencies"],
        joint_frequencies=freq_data["by_pos"] if args.joint else None,
    )

    # Pass 2: analyze every distinct word form (accent-insensitive + ranked).
    distinct = set()
    for lines in per_file_lines.values():
        for line in lines:
            if is_token_line(line):
                beta = to_beta(line.split()[4])
                if beta:
                    distinct.add(beta)

    candidates: dict[str, list] = {}
    forms = sorted(distinct)
    BATCH = 2000
    for i in range(0, len(forms), BATCH):
        chunk = forms[i : i + BATCH]
        for result in morpheus.analyze_beta(chunk):
            if result.analyses:
                candidates[result.beta] = list(result.analyses)
        print(f"  analyzed {min(i + BATCH, len(forms))}/{len(forms)} distinct forms", flush=True)

    # Pass 3a: choose one analysis per token in every file.
    choices_by_file = {}
    for path in inputs:
        choices_by_file[path] = choose_for_file(per_file_lines[path], candidates, tagger)

    # Pass 3b: derive the convention table from the trustworthy gold (skipped
    # for --only, where a single file's gold is too small to be reliable).
    conv_table = {}
    considered = 0
    rejected = []
    conventions_enabled = not args.no_conventions and not args.only
    if conventions_enabled:
        conv_table, considered, rejected = derive_conventions(per_file_lines, choices_by_file)
        print(f"conventions: {len(conv_table)} mappings derived "
              f"({considered} mismatching MorphGNT tokens examined; "
              f"{len(rejected)} pairs rejected by the guards)", flush=True)

    # Pass 3c: emit output.
    out_dir = os.path.join(REPO, "data", "morph-morpheus")
    os.makedirs(out_dir, exist_ok=True)
    total_analyzed = 0
    total_fallback = 0
    fallback_by_type: collections.Counter = collections.Counter()
    rewrite_pairs: collections.Counter = collections.Counter()
    book_stats = {}
    for path in inputs:
        name = os.path.basename(path)
        lines = per_file_lines[path]
        choices = choices_by_file[path]

        # Count rewrites for the summary (emit_file applies them).
        if conv_table:
            for line, chosen in zip(lines, choices):
                if is_token_line(line) and chosen is not None and deacc(chosen.lemma) in conv_table:
                    rewrite_pairs[(chosen.lemma, conv_table[deacc(chosen.lemma)])] += 1

        _tokens, analyzed, _fallbacks = emit_file(
            os.path.join(out_dir, name), lines, choices, conv_table
        )
        tokens = _tokens
        for line, chosen in zip(lines, choices):
            if is_token_line(line) and (chosen is None or chosen.proposed):
                fallback_by_type[line.split()[1]] += 1
        total_analyzed += analyzed
        total_fallback += tokens - analyzed
        book_stats[name] = tokens
        print(f"{name:<30} {analyzed:>6}/{tokens:<6} tokens  ->  data/morph-morpheus/{name}")

    # Summary (provenance for the generated files).
    summary = [
        "Morpheus AF annotation — summary",
        f"generated by tools/morpheus/analyze.py against {args.morpheus_dir}",
        f"ranking: {'joint (lemma, POS) frequency' if args.joint else 'plain lemma frequency'}"
        + (" (Viterbi disambiguation)" if args.tagger else " (top-1 candidate)"),
        f"conventions: {'af-conventions' if conventions_enabled else 'off (--no-conventions / --only)'}",
        f"files={len(inputs)} tokens={total_tokens} distinct_forms={len(distinct)}",
        f"analyzed_by_morpheus={total_analyzed} ({100 * total_analyzed / max(total_tokens, 1):.2f}%)",
        f"fallback_to_corpus={total_fallback} ({100 * total_fallback / max(total_tokens, 1):.2f}%)"
        " (original fields kept; listed in the *.fb sidecars)",
    ]
    if conv_table:
        summary.append(
            f"convention rewrites: {len(conv_table)} mappings applied to "
            f"{sum(rewrite_pairs.values())} tokens (derived from {considered} mismatching MorphGNT tokens)"
        )
        for (src, tgt), count in rewrite_pairs.most_common(20):
            summary.append(f"  {src} -> {tgt}   ({count})")
    if rejected:
        summary += ["", "pairs rejected by the guards (left contextual):"]
        for src_key, reason, detail in sorted(rejected, key=lambda r: -int(str(r[2]).split('/')[0]))[:15]:
            summary.append(f"  {src_key}: {reason} ({detail})")
    if fallback_by_type:
        summary += ["", "fallbacks by corpus type:"]
        for typ, count in fallback_by_type.most_common(15):
            summary.append(f"  {typ:6s} {count}")
    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as out:
        out.write("\n".join(summary) + "\n")

    print(f"\n{total_analyzed}/{total_tokens} Greek tokens analyzed "
          f"({100 * total_analyzed / max(total_tokens, 1):.2f}%); "
          f"{total_fallback} kept the corpus annotation; summary at data/morph-morpheus/summary.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
