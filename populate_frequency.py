#!/usr/bin/env python3
"""
Populate the "Frequency" field on notes in the "Kanji in Context: Revised Edition"
Anki deck using Japanese word-frequency data, so the field can be used as an
Anki sort field to study in roughly frequency order.

For each Expression, a frequency is looked up in this order:
  1. Exact match (after light normalization) in the University of Leeds
     Internet Corpus word list (https://github.com/hingston/japanese).
  2. Exact match in NINJAL's "Balanced Corpus of Contemporary Written
     Japanese" (BCCWJ) Long Unit Word frequency list
     (https://doi.org/10.15084/00003212), which also covers multi-kanji
     compounds the Leeds list lacks.
  3. If the Expression is a single content word in inflected/phrase form
     (e.g. "似ている", "急に"), fugashi/unidic is used to find its
     dictionary-form lemma (e.g. "似る", "急") and look that up instead.
  4. If the Expression is a short multi-word phrase (e.g. "夜が明ける"),
     each content word's lemma is looked up and the ranks are averaged -
     only when every lemma resolves within the SAME source, to avoid
     mixing the two corpora's incompatible rank scales.

The Frequency field is written as "<zero-padded rank>_<source>", e.g.
"0000134_UniversityOfLeeds", "0007454_NINJAL_BCCWJ", or
"0014936_BestGuess" for averaged phrase estimates. All ranks are padded to
the same width so the field sorts and displays consistently regardless of
source. NOTE: ranks are NOT comparable across sources - Leeds has ~44k
possible ranks and BCCWJ has ~2.4M, so the same rank number represents a
different relative frequency in each corpus.

Usage:
    python3 populate_frequency.py            # dry run, reports what would change
    python3 populate_frequency.py --apply    # writes changes to the collection

IMPORTANT: Anki must be closed (and no other tool such as DBeaver may have the
collection file open) while this script runs, otherwise it will fail to open
the collection.
"""
from __future__ import annotations

import argparse
import io
import re
import zipfile
from pathlib import Path

import requests
from fugashi import Tagger

from anki.collection import Collection

COLLECTION_PATH = "/Users/john/Library/Application Support/Anki2/User 1/collection.anki2"
DECK_NAME = "Kanji in Context: Revised Edition"

DATA_DIR = Path(__file__).parent / "data"

LEEDS_WORD_LIST_URL = (
    "https://raw.githubusercontent.com/hingston/japanese/master/"
    "44492-japanese-words-latin-lines-removed.txt"
)
LEEDS_CACHE_PATH = DATA_DIR / "44492-japanese-words-latin-lines-removed.txt"
LEEDS_SOURCE_LABEL = "UniversityOfLeeds"

BCCWJ_LUW_ZIP_URL = (
    "https://repository.ninjal.ac.jp/record/3228/files/"
    "BCCWJ_frequencylist_luw_ver1_0.zip"
)
BCCWJ_RANK_CACHE_PATH = DATA_DIR / "bccwj_luw_rank.tsv"
BCCWJ_SOURCE_LABEL = "NINJAL_BCCWJ"

BESTGUESS_SOURCE_LABEL = "BestGuess"

# Notes with no resolvable frequency at all get this fixed placeholder value,
# so they sort early (within the first 1000) for the user to review/study sooner
# rather than falling to the end of a frequency-sorted browse.
UNMATCHED_SOURCE_LABEL = "Unmatched"
UNMATCHED_RANK = 1000

RANK_WIDTH = 7  # shared across all sources/labels so the field sorts/displays uniformly

# Content-word part-of-speech categories used when lemmatizing phrases/inflected forms.
CONTENT_POS = {"名詞", "動詞", "形容詞", "形状詞", "副詞"}
# Light/auxiliary verbs to ignore when they follow a te-form (e.g. "て" + "いる").
AUX_LIGHT_VERBS = {"居る", "有る", "おく", "仕舞う", "来る", "行く", "見る", "貰う", "上げる", "呉れる", "下さる", "頂く"}


def load_leeds_frequency_map() -> dict[str, int]:
    """Map word -> 1-based rank, keeping the earliest (most frequent) occurrence."""
    if not LEEDS_CACHE_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(LEEDS_WORD_LIST_URL, timeout=30)
        resp.raise_for_status()
        LEEDS_CACHE_PATH.write_text(resp.text, encoding="utf-8")

    freq_map: dict[str, int] = {}
    for rank, word in enumerate(LEEDS_CACHE_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        word = word.strip()
        if word and word not in freq_map:
            freq_map[word] = rank
    return freq_map


def build_bccwj_rank_cache() -> None:
    """Download the BCCWJ LUW frequency list and distill it down to a small
    lemma -> best (lowest) rank TSV cache, without keeping the ~520MB raw file."""
    print("Downloading NINJAL BCCWJ frequency list (~57MB, one-time)...")
    resp = requests.get(BCCWJ_LUW_ZIP_URL, timeout=120)
    resp.raise_for_status()

    rank_by_lemma: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        inner_name = zf.namelist()[0]
        with zf.open(inner_name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8")
            next(text)  # header
            for line in text:
                rank_str, _, lemma, _rest = line.split("\t", 3)
                rank = int(rank_str)
                if lemma not in rank_by_lemma or rank < rank_by_lemma[lemma]:
                    rank_by_lemma[lemma] = rank

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(BCCWJ_RANK_CACHE_PATH, "w", encoding="utf-8") as out:
        for lemma, rank in rank_by_lemma.items():
            out.write(f"{lemma}\t{rank}\n")


def load_bccwj_frequency_map() -> dict[str, int]:
    if not BCCWJ_RANK_CACHE_PATH.exists():
        build_bccwj_rank_cache()

    freq_map: dict[str, int] = {}
    with open(BCCWJ_RANK_CACHE_PATH, encoding="utf-8") as f:
        for line in f:
            lemma, rank_str = line.rstrip("\n").split("\t")
            freq_map[lemma] = int(rank_str)
    return freq_map


_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")


def candidates(expression: str) -> list[str]:
    """Generate normalized lookup candidates for an Expression field value, in
    priority order, to handle optional kanji, na-adjectives, suru-verbs, and
    multi-reading entries (e.g. "一日、一日")."""
    expression = expression.strip()
    # Notes sometimes list multiple surface forms separated by a Japanese comma.
    parts = [p.strip() for p in re.split("[、,]", expression) if p.strip()]
    seen: list[str] = []

    def add(cand: str) -> None:
        cand = cand.strip("～〜 ")
        if cand and cand not in seen:
            seen.append(cand)

    for part in parts:
        add(part)
        # "汎用（性）" -> try "汎用性" (merged) and "汎用" (optional part dropped).
        if _PAREN_RE.search(part):
            add(_PAREN_RE.sub(r"\1", part))
            add(_PAREN_RE.sub("", part))
        # na-adjectives: "柔軟な" -> "柔軟"
        if part.endswith("な"):
            add(part[:-1])
        # suru-verbs: "密輸する" -> "密輸"
        if part.endswith("する"):
            add(part[:-2])
        # attributive "no"-nouns: "後続の" -> "後続"
        if part.endswith("の"):
            add(part[:-1])
        # honorific prefix: "お兄さん" -> "兄さん"
        if part.startswith("お") and len(part) > 1:
            add(part[1:])
    return seen


def content_lemmas(expression: str, tagger: Tagger) -> list[str]:
    """Tokenize an Expression and return the dictionary-form lemmas of its
    content words (nouns/verbs/adjectives/adverbs), dropping particles,
    auxiliaries, and grammaticalized light verbs (e.g. the "iru" in "~te iru").
    Used as a fallback for inflected single words and short phrases that have
    no exact match in either frequency list."""
    # Only the primary reading/surface form is used for multi-reading entries.
    primary = re.split("[、,]", expression.strip())[0].strip()
    tokens = list(tagger(primary))
    lemmas: list[str] = []
    for i, token in enumerate(tokens):
        lemma = token.feature.lemma or token.surface
        if token.feature.pos1 not in CONTENT_POS:
            continue
        if lemma in AUX_LIGHT_VERBS and i > 0 and tokens[i - 1].surface in ("て", "で"):
            continue
        if lemma not in lemmas:
            lemmas.append(lemma)
    return lemmas


def find_frequency_value(
    expression: str,
    leeds_map: dict[str, int],
    bccwj_map: dict[str, int],
    tagger: Tagger,
) -> str | None:
    cands = candidates(expression)
    for cand in cands:
        if cand in leeds_map:
            return f"{leeds_map[cand]:0{RANK_WIDTH}d}_{LEEDS_SOURCE_LABEL}"
    for cand in cands:
        if cand in bccwj_map:
            return f"{bccwj_map[cand]:0{RANK_WIDTH}d}_{BCCWJ_SOURCE_LABEL}"

    # Fallback: lemmatize inflected forms / short phrases and look up the
    # dictionary form(s) directly (e.g. "似ている" -> "似る", "夜が明ける" -> "夜"+"明ける").
    lemmas = content_lemmas(expression, tagger)
    if not lemmas:
        return None
    if len(lemmas) == 1:
        lemma = lemmas[0]
        if lemma in leeds_map:
            return f"{leeds_map[lemma]:0{RANK_WIDTH}d}_{LEEDS_SOURCE_LABEL}"
        if lemma in bccwj_map:
            return f"{bccwj_map[lemma]:0{RANK_WIDTH}d}_{BCCWJ_SOURCE_LABEL}"
        return None

    # Multi-word phrase: only average if every lemma resolves within the SAME
    # source, since Leeds and BCCWJ ranks are on incompatible scales.
    leeds_ranks = [leeds_map[l] for l in lemmas if l in leeds_map]
    if len(leeds_ranks) == len(lemmas):
        avg_rank = round(sum(leeds_ranks) / len(leeds_ranks))
        return f"{avg_rank:0{RANK_WIDTH}d}_{BESTGUESS_SOURCE_LABEL}"
    bccwj_ranks = [bccwj_map[l] for l in lemmas if l in bccwj_map]
    if len(bccwj_ranks) == len(lemmas):
        avg_rank = round(sum(bccwj_ranks) / len(bccwj_ranks))
        return f"{avg_rank:0{RANK_WIDTH}d}_{BESTGUESS_SOURCE_LABEL}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the Frequency field back to the collection. "
        "Without this flag, the script only reports what it would do.",
    )
    args = parser.parse_args()

    print("Loading University of Leeds frequency list...")
    leeds_map = load_leeds_frequency_map()
    print(f"Loaded {len(leeds_map)} unique Leeds words.")

    print("Loading NINJAL BCCWJ frequency list...")
    bccwj_map = load_bccwj_frequency_map()
    print(f"Loaded {len(bccwj_map)} unique BCCWJ lemmas.")

    tagger = Tagger()

    print(f"Opening collection: {COLLECTION_PATH}")
    col = Collection(COLLECTION_PATH)

    try:
        if args.apply:
            print("Creating a safety backup before making changes...")
            col.create_backup(
                backup_folder=col.media.dir().rsplit("/", 1)[0] + "/backups",
                force=True,
                wait_for_completion=True,
            )

        note_ids = col.find_notes(f'deck:"{DECK_NAME}"')
        print(f"Found {len(note_ids)} notes in deck '{DECK_NAME}'.")
        if not note_ids:
            print("No notes found; check the deck name.")
            return

        matched_leeds = 0
        matched_bccwj = 0
        matched_bestguess = 0
        unmatched: list[str] = []
        updated = 0

        for nid in note_ids:
            note = col.get_note(nid)
            if "Expression" not in note or "Frequency" not in note:
                continue
            expression = note["Expression"]
            value = find_frequency_value(expression, leeds_map, bccwj_map, tagger)
            if value is None:
                unmatched.append(expression)
                value = f"{UNMATCHED_RANK:0{RANK_WIDTH}d}_{UNMATCHED_SOURCE_LABEL}"
            elif value.endswith(LEEDS_SOURCE_LABEL):
                matched_leeds += 1
            elif value.endswith(BCCWJ_SOURCE_LABEL):
                matched_bccwj += 1
            else:
                matched_bestguess += 1
            if note["Frequency"] != value:
                updated += 1
                if args.apply:
                    note["Frequency"] = value
                    col.update_note(note)

        total_matched = matched_leeds + matched_bccwj + matched_bestguess
        print(f"Matched via Leeds: {matched_leeds}/{len(note_ids)}")
        print(f"Matched via BCCWJ: {matched_bccwj}/{len(note_ids)}")
        print(f"Matched via BestGuess (averaged phrase): {matched_bestguess}/{len(note_ids)}")
        print(f"Total matched: {total_matched}/{len(note_ids)}")
        print(f"Would update (or updated): {updated}")
        print(f"Unmatched: {len(unmatched)}")

        if unmatched:
            unmatched_path = Path(__file__).parent / "unmatched_expressions.txt"
            unmatched_path.write_text("\n".join(unmatched), encoding="utf-8")
            print(f"Unmatched expressions written to {unmatched_path}")

        if not args.apply:
            print("\nDry run only, no changes written. Re-run with --apply to save.")
    finally:
        col.close()


if __name__ == "__main__":
    main()
