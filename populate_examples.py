#!/usr/bin/env python3
"""
Populate Example_Japanese_01..03 / Example_English_01..03 on notes in the
"Kanji in Context: Revised Edition" Anki deck with up to 3 short Japanese/
English example sentence pairs from the Tatoeba corpus (https://tatoeba.org),
for the word in each note's Expression field.

Data source (weekly exports, CC BY 2.0 FR / CC0):
  - https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences.tsv.bz2
  - https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2
  - https://downloads.tatoeba.org/exports/links.tar.bz2 (all languages; distilled
    down to just the Japanese<->English pairs and cached, since the raw file
    covers every language pair in Tatoeba)

Matching: Expression is normalized the same way as populate_frequency.py
(parentheticals, na/suru/no/o- stripping, multi-reading splits) and also
lemmatized with fugashi for inflected single words. A sentence matches if any
of those candidate forms appears as a literal substring. Among matches, the
3 shortest Japanese sentences (with an available English translation) are
used, as a simple proxy for "easiest to study first".

Usage:
    python3 populate_examples.py            # dry run, reports what would change
    python3 populate_examples.py --apply    # writes changes to the collection

IMPORTANT: Anki must be closed (and no other tool such as DBeaver may have the
collection file open) while this script runs, otherwise it will fail to open
the collection.
"""
from __future__ import annotations

import argparse
import bz2
import io
import tarfile
from pathlib import Path

import requests
from fugashi import Tagger

from anki.collection import Collection

import populate_frequency as pf

COLLECTION_PATH = pf.COLLECTION_PATH
DECK_NAME = pf.DECK_NAME

DATA_DIR = Path(__file__).parent / "data"

JPN_SENTENCES_URL = "https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences.tsv.bz2"
ENG_SENTENCES_URL = "https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2"
LINKS_URL = "https://downloads.tatoeba.org/exports/links.tar.bz2"

JPN_SENTENCES_CACHE = DATA_DIR / "tatoeba_jpn_sentences.tsv"
ENG_SENTENCES_CACHE = DATA_DIR / "tatoeba_eng_sentences.tsv"
# Distilled jpn_id -> eng_id pairs, so we don't keep the full (all-language) raw file.
JPN_ENG_LINKS_CACHE = DATA_DIR / "tatoeba_jpn_eng_links.tsv"

MAX_EXAMPLES = 3


def _download_bz2_text(url: str, timeout: int) -> str:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return bz2.decompress(resp.content).decode("utf-8")


def _load_sentences(cache_path: Path, url: str) -> dict[int, str]:
    if not cache_path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {url}...")
        cache_path.write_text(_download_bz2_text(url, timeout=120), encoding="utf-8")
    sentences: dict[int, str] = {}
    with open(cache_path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                sentences[int(parts[0])] = parts[2]
    return sentences


def load_jpn_sentences() -> dict[int, str]:
    return _load_sentences(JPN_SENTENCES_CACHE, JPN_SENTENCES_URL)


def load_eng_sentences() -> dict[int, str]:
    return _load_sentences(ENG_SENTENCES_CACHE, ENG_SENTENCES_URL)


def build_jpn_eng_links_cache(jpn_ids: set[int], eng_ids: set[int]) -> None:
    """Download the full (all-language) links export once and distill it down
    to just the Japanese<->English pairs, without keeping the much larger raw file."""
    print("Downloading Tatoeba sentence links (~150MB, one-time)...")
    resp = requests.get(LINKS_URL, timeout=300)
    resp.raise_for_status()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:bz2") as tf:
        member = tf.getmembers()[0]
        raw = tf.extractfile(member)
        text = io.TextIOWrapper(raw, encoding="utf-8")
        with open(JPN_ENG_LINKS_CACHE, "w", encoding="utf-8") as out:
            for line in text:
                a_str, b_str = line.rstrip("\n").split("\t")
                a, b = int(a_str), int(b_str)
                if a in jpn_ids and b in eng_ids:
                    out.write(f"{a}\t{b}\n")
                elif a in eng_ids and b in jpn_ids:
                    out.write(f"{b}\t{a}\n")


def load_jpn_eng_links(jpn_ids: set[int], eng_ids: set[int]) -> dict[int, list[int]]:
    if not JPN_ENG_LINKS_CACHE.exists():
        build_jpn_eng_links_cache(jpn_ids, eng_ids)
    links: dict[int, list[int]] = {}
    with open(JPN_ENG_LINKS_CACHE, encoding="utf-8") as f:
        for line in f:
            a_str, b_str = line.rstrip("\n").split("\t")
            links.setdefault(int(a_str), []).append(int(b_str))
    return links


def gather_candidate_keys(expressions: list[str], tagger: Tagger) -> set[str]:
    keys: set[str] = set()
    for expr in expressions:
        keys.update(pf.candidates(expr))
        keys.update(pf.content_lemmas(expr, tagger))
    keys.discard("")
    return keys


MAX_NGRAM_WINDOW = 4  # longest run of consecutive tokens checked for compound matches


def build_word_index(
    jpn_sentences: dict[int, str],
    linked_jpn_ids: set[int],
    keys: set[str],
    tagger: Tagger,
) -> dict[str, list[int]]:
    """Map each candidate word/lemma to the ids of (translated) Japanese
    sentences containing it as a whole token, or as a run of consecutive
    tokens (to catch compounds split by the tokenizer, e.g. two tokens
    "自己" + "紹介"). Matching on token boundaries, rather than raw
    substrings, avoids false positives like "兄" matching inside the
    unrelated word "兄弟"."""
    index: dict[str, list[int]] = {}
    for sid in linked_jpn_ids:
        text = jpn_sentences.get(sid)
        if not text:
            continue
        surfaces = [w.surface for w in tagger(text)]
        found: set[str] = set()
        for i in range(len(surfaces)):
            joined = ""
            for w in range(MAX_NGRAM_WINDOW):
                if i + w >= len(surfaces):
                    break
                joined += surfaces[i + w]
                if joined in keys:
                    found.add(joined)
        for key in found:
            index.setdefault(key, []).append(sid)
    return index


def find_examples(
    expression: str,
    tagger: Tagger,
    index: dict[str, list[int]],
    jpn_sentences: dict[int, str],
    eng_sentences: dict[int, str],
    links: dict[int, list[int]],
) -> list[tuple[str, str]]:
    # Whole-word/compound forms are tried first; sub-word lemmas from a
    # tokenized breakdown (e.g. "自己紹介" -> "自己", "紹介") are only used to
    # top up the result if the direct match didn't yield enough sentences,
    # since they're a less precise proxy for the original Expression.
    primary_keys = [k for k in pf.candidates(expression) if k]
    fallback_keys = [k for k in pf.content_lemmas(expression, tagger) if k and k not in primary_keys]

    seen: set[int] = set()

    def sorted_new(ids: set[int] | list[int]) -> list[int]:
        fresh = sorted(set(ids) - seen, key=lambda sid: len(jpn_sentences[sid]))
        seen.update(fresh)
        return fresh

    def collect_any(keys: list[str]) -> list[int]:
        ids: list[int] = []
        for k in keys:
            ids.extend(index.get(k, []))
        return sorted_new(ids)

    sentence_ids = collect_any(primary_keys)
    if len(sentence_ids) < MAX_EXAMPLES and fallback_keys:
        if len(fallback_keys) == 1:
            # Single leftover content word (e.g. an inflected verb/adjective
            # reduced to its dictionary form) - safe to match directly.
            sentence_ids += collect_any(fallback_keys)
        else:
            # Multiple sub-word lemmas (e.g. "視聴"+"率") - require ALL of
            # them in the same sentence, since any single lemma alone (like
            # a generic suffix such as "率") is too weak a proxy on its own.
            per_lemma_ids = [set(index.get(k, [])) for k in fallback_keys]
            if all(per_lemma_ids):
                sentence_ids += sorted_new(set.intersection(*per_lemma_ids))
    if not sentence_ids:
        return []

    pairs: list[tuple[str, str]] = []
    seen_japanese: set[str] = set()
    seen_english: set[str] = set()
    for sid in sentence_ids:
        eng_ids = links.get(sid)
        if not eng_ids:
            continue
        jp_text = jpn_sentences[sid]
        if jp_text in seen_japanese:
            continue
        # A Japanese sentence can have multiple English translations linked;
        # prefer the shortest one that isn't a duplicate of one already
        # picked for this note (e.g. two different Japanese sentences both
        # translated as "Do you know how to play?").
        eng_text = None
        for eid in sorted(eng_ids, key=lambda eid: len(eng_sentences.get(eid, ""))):
            text = eng_sentences.get(eid)
            if text and text not in seen_english:
                eng_text = text
                break
        if eng_text is None:
            continue
        pairs.append((jp_text, eng_text))
        seen_japanese.add(jp_text)
        seen_english.add(eng_text)
        if len(pairs) == MAX_EXAMPLES:
            break
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the Example fields back to the collection. "
        "Without this flag, the script only reports what it would do.",
    )
    args = parser.parse_args()

    print("Loading Tatoeba Japanese sentences...")
    jpn_sentences = load_jpn_sentences()
    print(f"Loaded {len(jpn_sentences)} Japanese sentences.")

    print("Loading Tatoeba English sentences...")
    eng_sentences = load_eng_sentences()
    print(f"Loaded {len(eng_sentences)} English sentences.")

    jpn_ids = set(jpn_sentences)
    eng_ids = set(eng_sentences)
    links = load_jpn_eng_links(jpn_ids, eng_ids)
    print(f"{len(links)} Japanese sentences have an English translation.")

    tagger = Tagger()

    print(f"Opening collection: {COLLECTION_PATH}")
    col = Collection(COLLECTION_PATH)

    try:
        note_ids = col.find_notes(f'deck:"{DECK_NAME}"')
        print(f"Found {len(note_ids)} notes in deck '{DECK_NAME}'.")
        if not note_ids:
            print("No notes found; check the deck name.")
            return

        expressions = []
        for nid in note_ids:
            note = col.get_note(nid)
            if "Expression" in note:
                expressions.append(note["Expression"])

        print("Gathering candidate word forms...")
        keys = gather_candidate_keys(expressions, tagger)
        print(f"Building search index for {len(keys)} candidate forms over {len(links)} sentences...")
        index = build_word_index(jpn_sentences, set(links), keys, tagger)

        if args.apply:
            print("Creating a safety backup before making changes...")
            col.create_backup(
                backup_folder=col.media.dir().rsplit("/", 1)[0] + "/backups",
                force=True,
                wait_for_completion=True,
            )

        matched = 0
        unmatched: list[str] = []
        updated = 0

        for nid in note_ids:
            note = col.get_note(nid)
            if "Expression" not in note:
                continue
            expression = note["Expression"]
            pairs = find_examples(expression, tagger, index, jpn_sentences, eng_sentences, links)
            if pairs:
                matched += 1
            else:
                unmatched.append(expression)

            changed = False
            for i in range(MAX_EXAMPLES):
                jp_field = f"Example_Japanese_{i + 1:02d}"
                en_field = f"Example_English_{i + 1:02d}"
                if jp_field not in note or en_field not in note:
                    continue
                jp_text, en_text = pairs[i] if i < len(pairs) else ("", "")
                if note[jp_field] != jp_text:
                    note[jp_field] = jp_text
                    changed = True
                if note[en_field] != en_text:
                    note[en_field] = en_text
                    changed = True

            if changed:
                updated += 1
                if args.apply:
                    col.update_note(note)

        print(f"Matched (at least 1 example): {matched}/{len(note_ids)}")
        print(f"Unmatched (no examples found): {len(unmatched)}")
        print(f"Would update (or updated): {updated}")

        if unmatched:
            unmatched_path = Path(__file__).parent / "unmatched_examples.txt"
            unmatched_path.write_text("\n".join(unmatched), encoding="utf-8")
            print(f"Unmatched expressions written to {unmatched_path}")

        if not args.apply:
            print("\nDry run only, no changes written. Re-run with --apply to save.")
    finally:
        col.close()


if __name__ == "__main__":
    main()
