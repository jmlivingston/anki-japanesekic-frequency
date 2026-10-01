#!/usr/bin/env python3
"""
Add Example_Japanese_01_Furigana / _02 / _03 fields to the note type used by
the "Kanji in Context: Revised Edition" deck, and fill them with Anki bracket
furigana ("漢字[かんじ]") generated from the existing Example_Japanese_01..03
fields via fugashi/unidic token readings.

Furigana is generated at the word/token level (not per individual kanji
character like the deck's own Reading field), trimming shared okurigana so
only the kanji portion of each token is annotated.

Usage:
    python3 add_furigana.py            # dry run, reports what would change
    python3 add_furigana.py --apply    # writes changes to the collection

IMPORTANT: Anki must be closed (and no other tool such as DBeaver may have the
collection file open) while this script runs, otherwise it will fail to open
the collection.
"""
from __future__ import annotations

import argparse

from fugashi import Tagger

from anki.collection import Collection

import populate_frequency as pf
from furigana import sentence_to_furigana

COLLECTION_PATH = pf.COLLECTION_PATH
DECK_NAME = pf.DECK_NAME

NUM_EXAMPLES = 3


def ensure_furigana_fields(col: Collection, notetype) -> list[str]:
    """Add any missing Example_Japanese_0N_Furigana fields, positioned right
    after their corresponding Example_Japanese_0N field. Returns the field names."""
    field_names = []
    for i in range(1, NUM_EXAMPLES + 1):
        jp_field = f"Example_Japanese_{i:02d}"
        furigana_field = f"{jp_field}_Furigana"
        field_names.append(furigana_field)

        current_names = [f["name"] for f in notetype["flds"]]
        if furigana_field in current_names:
            continue

        field = col.models.new_field(furigana_field)
        col.models.add_field(notetype, field)
        # Place it immediately after its corresponding Japanese sentence field.
        current_names = [f["name"] for f in notetype["flds"]]
        jp_idx = current_names.index(jp_field)
        col.models.reposition_field(notetype, field, jp_idx + 1)
    return field_names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write field/content changes back to the collection. "
        "Without this flag, the script only reports what it would do.",
    )
    args = parser.parse_args()

    tagger = Tagger()

    print(f"Opening collection: {COLLECTION_PATH}")
    col = Collection(COLLECTION_PATH)

    try:
        note_ids = col.find_notes(f'deck:"{DECK_NAME}"')
        print(f"Found {len(note_ids)} notes in deck '{DECK_NAME}'.")
        if not note_ids:
            print("No notes found; check the deck name.")
            return

        notetype = col.get_note(note_ids[0]).note_type()

        if args.apply:
            print("Creating a safety backup before making changes...")
            col.create_backup(
                backup_folder=col.media.dir().rsplit("/", 1)[0] + "/backups",
                force=True,
                wait_for_completion=True,
            )
            furigana_fields = ensure_furigana_fields(col, notetype)
            col.models.update_dict(notetype)
            print(f"Ensured fields exist: {furigana_fields}")
        else:
            current_names = [f["name"] for f in notetype["flds"]]
            furigana_fields = [f"Example_Japanese_{i:02d}_Furigana" for i in range(1, NUM_EXAMPLES + 1)]
            missing = [f for f in furigana_fields if f not in current_names]
            if missing:
                print(f"Would add fields: {missing}")
            else:
                print("Furigana fields already exist.")

        filled = 0
        updated = 0

        for nid in note_ids:
            note = col.get_note(nid)
            changed = False
            for i in range(1, NUM_EXAMPLES + 1):
                jp_field = f"Example_Japanese_{i:02d}"
                furigana_field = f"{jp_field}_Furigana"
                if jp_field not in note:
                    continue
                jp_text = note[jp_field]
                if not jp_text:
                    continue
                new_value = sentence_to_furigana(jp_text, tagger)
                filled += 1
                if args.apply:
                    if furigana_field in note and note[furigana_field] != new_value:
                        note[furigana_field] = new_value
                        changed = True
                elif furigana_field not in note or note[furigana_field] != new_value:
                    changed = True
            if changed:
                updated += 1
                if args.apply:
                    col.update_note(note)

        print(f"Furigana values generated: {filled}")
        print(f"Would update (or updated): {updated}")

        if not args.apply:
            print("\nDry run only, no changes written. Re-run with --apply to save.")
    finally:
        col.close()


if __name__ == "__main__":
    main()
