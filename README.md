# anki-japanesekic-frequency

Scripts to enrich the **"Kanji in Context: Revised Edition"** Anki deck using the
official Anki Python API ([`anki`](https://pypi.org/project/anki/) package),
working directly against the local Anki collection:

```
/Users/john/Library/Application Support/Anki2/User 1/collection.anki2
```

The note type ("Japanese KIC") has these fields: `Expression`, `Reading`,
`Meaning`, `Notes`, `Frequency`, `Frequency_ID`, `Example_Japanese_01..03`,
`Example_Japanese_01..03_Furigana`, `Example_English_01..03`.

## ⚠️ Before running anything

**Quit Anki.app** (and close any other tool with the collection file open,
e.g. DBeaver). The `anki` library opens the SQLite collection directly and
will fail with `anki.errors.DBError: Anki already open` otherwise. You can
check with:

```sh
lsof "/Users/john/Library/Application Support/Anki2/User 1/collection.anki2"
```

All three scripts below:

- Run in **dry-run mode by default** - they only print what they would do.
- Require `--apply` to actually write changes.
- Automatically create an Anki backup (`col.create_backup(...)`) before
  writing anything, in addition to Anki's own `backups/` folder.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## `populate_frequency.py`

Fills the `Frequency` field with a word-frequency rank, so the field can be
used as an Anki sort field to study in roughly frequency order.

Lookup order per `Expression`:

1. **University of Leeds** Internet Corpus word list
   ([hingston/japanese](https://github.com/hingston/japanese)) - ~44k single
   tokens, normalized for multi-reading entries (`一日、一日`), optional
   kanji in parens, na-adjective/suru-verb/attributive-の/honorific-お
   stripping.
2. **NINJAL BCCWJ** ("Balanced Corpus of Contemporary Written Japanese")
   Long Unit Word frequency list - ~2.4M lemmas, covering multi-kanji
   compounds the Leeds list lacks.
3. **Lemmatized fallback** (via `fugashi`/unidic): if the Expression is an
   inflected single word (e.g. `似ている` → `似る`) or a short phrase whose
   content words _all_ resolve within the same source (e.g. `夜が明ける` →
   `夜` + `明ける`, averaged), tagged `BestGuess`.
4. Anything still unmatched gets a fixed `0001000_Unmatched` placeholder, so
   it sorts near the top for early review/study rather than being blank.

Field value format: `<7-digit zero-padded rank>_<SourceLabel>`, e.g.
`0000134_UniversityOfLeeds`, `0007454_NINJAL_BCCWJ`, `0014936_BestGuess`.
Padding keeps Anki's plain-text field sort in numeric order. Note that ranks
are **not comparable across sources** - Leeds tops out around 44k, BCCWJ
around 2.4M, so the same rank number means a different relative frequency in
each corpus.

```sh
python3 populate_frequency.py            # dry run
python3 populate_frequency.py --apply    # write changes
```

Unmatched expressions (before the placeholder is applied) are written to
`unmatched_expressions.txt` for review.

## `populate_examples.py`

Fills `Example_Japanese_01..03` / `Example_English_01..03` with up to 3
short example sentence pairs from the [Tatoeba](https://tatoeba.org) corpus.

- Downloads and caches the Japanese/English sentence exports and distills
  the (much larger, all-language) sentence-links export down to just
  Japanese↔English pairs.
- Builds a token-based search index (via `fugashi`) rather than doing plain
  substring search, so e.g. looking up `兄` doesn't false-positive match
  inside the unrelated word `兄弟`.
- Matching is tiered: exact Expression/compound forms are tried first;
  decomposed sub-word lemmas (e.g. `自己紹介` → `自己` + `紹介`) are only used
  to top up the result if there weren't enough direct matches, and only
  when _all_ decomposed lemmas co-occur in the same sentence (to avoid weak
  matches on generic bound morphemes like `率`).
- Among matches, the 3 shortest Japanese sentences are preferred, as a
  simple proxy for "easiest to study first".

```sh
python3 populate_examples.py            # dry run
python3 populate_examples.py --apply    # write changes
```

Words with no Tatoeba sentence available are written to
`unmatched_examples.txt` and left blank (no fabricated examples).

## `add_furigana.py`

Adds `Example_Japanese_01..03_Furigana` fields to the note type (if not
already present, positioned right after each corresponding
`Example_Japanese_0N` field) and fills them with Anki bracket furigana
(`食[た]べる`) generated from the existing `Example_Japanese_01..03` text via
`fugashi`/unidic token readings - no extra data download needed.

Furigana is generated at the **word/token level**, not per individual kanji
character like the deck's own `Reading` field (e.g. it produces `一分[いっぷん]`
or `一[いち]分[ふん]` rather than `一[いっ] 分[ぷん]`), trimming shared
okurigana so only the kanji portion of each token is bracketed.

```sh
python3 add_furigana.py            # dry run
python3 add_furigana.py --apply    # write changes
```

## `populate_frequency_id.py`

Adds a `Frequency_ID` field (if not already present, positioned right after
`Frequency`) and fills it with a plain sequential rank - `1` for the note
with the lowest `Frequency` value, `2` for the next, and so on.
Unlike `Frequency` (which encodes the source corpus and isn't comparable
across sources), `Frequency_ID` is just each note's position in the overall
frequency ordering.

```sh
python3 populate_frequency_id.py            # dry run
python3 populate_frequency_id.py --apply    # write changes
```

## Data caching

`populate_frequency.py` and `populate_examples.py` cache downloaded reference
data under `data/` (gitignored - regenerated automatically on first run):

| File                                                      | Source                                                         |
| --------------------------------------------------------- | -------------------------------------------------------------- |
| `44492-japanese-words-latin-lines-removed.txt`            | University of Leeds word list                                  |
| `bccwj_luw_rank.tsv`                                      | NINJAL BCCWJ, distilled to `lemma -> best rank`                |
| `tatoeba_jpn_sentences.tsv` / `tatoeba_eng_sentences.tsv` | Tatoeba sentence exports                                       |
| `tatoeba_jpn_eng_links.tsv`                               | Tatoeba links export, distilled to Japanese↔English pairs only |

## Known limitations

- Multi-word phrases built from grammatical particles (e.g. `全力で`,
  `目が覚める`) sometimes have no frequency/example match since they aren't
  single lexical items in either corpus - left blank/placeholder rather than
  guessed.
- `unidic-lite` (the lightweight dictionary used by `fugashi`) occasionally
  splits a real compound into two tokens it doesn't recognize (e.g. `放射能`
  → `放射` + `能`), which can very occasionally cause a low-quality single-
  character example match.
- Furigana/reading generation picks one dictionary reading per token without
  sentence-level context, so heteronyms can occasionally get the wrong
  reading (e.g. `一日` as `ついたち` "1st of the month" instead of `いちにち`
  "one day" when used as a duration).
