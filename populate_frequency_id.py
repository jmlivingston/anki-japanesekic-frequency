#!/usr/bin/env python3
"""
Add a Frequency_ID field to the note type used by the "Kanji in Context:
Revised Edition" deck (positioned right after Frequency) and fill it with a
plain sequential rank: 1 for the note with the lowest Frequency value, 2 for
the next, and so on. Unlike Frequency itself (which encodes the source corpus
and isn't comparable across sources/formats), Frequency_ID is just each
note's position in the overall frequency ordering - useful for reasoning
about "roughly how far into the deck is this card" independent of the
underlying rank scale.

Usage:
    python3 populate_frequency_id.py            # dry run, reports what would change
    python3 populate_frequency_id.py --apply    # writes changes to the collection

IMPORTANT: Anki must be closed (and no other tool such as DBeaver may have the
collection file open) while this script runs, otherwise it will fail to open
the collection.
"""
from __future__ import annotations

import argparse

from anki.collection import Collection

import populate_frequency as pf

COLLECTION_PATH = pf.COLLECTION_PATH
DECK_NAME = pf.DECK_NAME

ID_FIELD = "Frequency_ID"


def ensure_frequency_id_field(col: Collection, notetype) -> None:
    """Add the Frequency_ID field if missing, positioned right after Frequency."""
    current_names = [f["name"] for f in notetype["flds"]]
    if ID_FIELD in current_names:
        return

    field = col.models.new_field(ID_FIELD)
    col.models.add_field(notetype, field)
    current_names = [f["name"] for f in notetype["flds"]]
    freq_idx = current_names.index("Frequency")
    col.models.reposition_field(notetype, field, freq_idx + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write field/content changes back to the collection. "
        "Without this flag, the script only reports what it would do.",
    )
    args = parser.parse_args()

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
            ensure_frequency_id_field(col, notetype)
            col.models.update_dict(notetype)
            print(f"Ensured field exists: {ID_FIELD}")
        else:
            current_names = [f["name"] for f in notetype["flds"]]
            if ID_FIELD not in current_names:
                print(f"Would add field: {ID_FIELD}")
            else:
                print(f"{ID_FIELD} field already exists.")

        # Re-fetch notes (rather than reusing any fetched above) so they reflect
        # the just-added field, then sort ascending by the existing Frequency field.
        notes = [col.get_note(nid) for nid in note_ids]
        notes = [n for n in notes if "Frequency" in n]
        notes.sort(key=lambda n: n["Frequency"])

        updated = 0
        for i, note in enumerate(notes, start=1):
            value = str(i)
            if ID_FIELD not in note or note[ID_FIELD] != value:
                updated += 1
                if args.apply:
                    note[ID_FIELD] = value
                    col.update_note(note)

        print(f"Would update (or updated): {updated}/{len(notes)}")

        if not args.apply:
            print("\nDry run only, no changes written. Re-run with --apply to save.")
    finally:
        col.close()


if __name__ == "__main__":
    main()
