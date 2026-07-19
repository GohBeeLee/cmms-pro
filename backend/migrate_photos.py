"""
One-time migration: pulls base64 photos that are still embedded inline in
older work orders' `description` text (from before photo_storage.py
existed) out onto disk, and strips the base64 blocks out of the
description afterward.

Why this matters: even after the disk-storage change, any work order
completed/submitted BEFORE that change still has its photos sitting in
`description` as raw base64 text. Every list/analysis/export request that
touches those rows still loads that full base64 blob into memory — this
script is what actually stops that for existing data, not just new data
going forward.

Safe to run more than once — a work order is only touched if its
description still contains a [PHOTO:...] tag, and those tags are removed
once migrated, so a second run is a fast no-op over already-migrated rows.

Usage (from the backend/ directory, same place you'd run the server):
    python migrate_photos.py
    python migrate_photos.py --dry-run     # report what it would do, change nothing
"""
import argparse
import asyncio
import re
import sys

from sqlalchemy import select

from db import AsyncSessionLocal
from models import WorkOrder, WorkOrderPhoto
from photo_storage import save_photo

PHOTO_TAG_RE = re.compile(r"\[PHOTO:([^\|]+)\|([^\]]+)\]")
BLOCK_RE = re.compile(r"\[(OPERATOR_PHOTOS|COMPLETION_PHOTOS)\](.*?)\[/\1\]", re.DOTALL)


def _kind_for_block(tag: str) -> str:
    return "operator" if tag == "OPERATOR_PHOTOS" else "completion"


def _strip_and_extract(description: str):
    """
    Returns (cleaned_description, [(kind, filename, data_url_or_None), ...]).
    Photos are pulled out block-by-block so each one is tagged with the
    right kind (operator vs completion); anything outside a recognized
    block falls back to 'completion' defensively.
    """
    found = []

    def handle_block(m):
        kind = _kind_for_block(m.group(1))
        inner = m.group(2)
        for pm in PHOTO_TAG_RE.finditer(inner):
            filename, payload = pm.group(1), pm.group(2)
            found.append((kind, filename, None if payload == "TOO_LARGE" else payload))
        return ""

    cleaned = BLOCK_RE.sub(handle_block, description or "")

    # Defensive: any stray [PHOTO:...] tags not inside a recognized block
    def handle_stray(m):
        filename, payload = m.group(1), m.group(2)
        found.append(("completion", filename, None if payload == "TOO_LARGE" else payload))
        return ""

    cleaned = PHOTO_TAG_RE.sub(handle_stray, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found


async def migrate(dry_run: bool = False):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WorkOrder))
        all_wos = result.scalars().all()

        candidates = [wo for wo in all_wos if wo.description and "[PHOTO:" in wo.description]
        print(f"Found {len(candidates)} work order(s) with embedded photos out of {len(all_wos)} total.")

        wos_migrated = 0
        photos_saved = 0
        photos_unrecoverable = 0  # were marked TOO_LARGE at the time, no data left to save
        bytes_before = 0
        bytes_after = 0

        for wo in candidates:
            before_len = len(wo.description or "")
            bytes_before += before_len

            cleaned, extracted = _strip_and_extract(wo.description)

            if dry_run:
                bytes_after += len(cleaned)
                wos_migrated += 1
                photos_saved += sum(1 for _, _, data in extracted if data)
                photos_unrecoverable += sum(1 for _, _, data in extracted if not data)
                continue

            for kind, filename, data_url in extracted:
                if not data_url:
                    photos_unrecoverable += 1
                    continue
                saved = save_photo(data_url, f"{kind}/{wo.id}")
                if saved:
                    db.add(WorkOrderPhoto(
                        work_order_id=wo.id, kind=kind, filename=filename,
                        thumb_path=saved["thumb_path"], full_path=saved["full_path"],
                    ))
                    photos_saved += 1
                else:
                    photos_unrecoverable += 1  # corrupt/undecodable base64

            wo.description = cleaned
            bytes_after += len(cleaned)
            wos_migrated += 1
            await db.commit()
            print(f"  migrated {wo.wo_number}: {before_len:,} -> {len(cleaned):,} bytes of description text")

        print()
        print("=" * 60)
        print(f"{'[DRY RUN] ' if dry_run else ''}Work orders migrated : {wos_migrated}")
        print(f"Photos saved to disk         : {photos_saved}")
        if photos_unrecoverable:
            print(f"Photos NOT recoverable       : {photos_unrecoverable} "
                  f"(were already marked TOO_LARGE, or corrupt base64 — original bytes are gone)")
        print(f"description text size        : {bytes_before:,} -> {bytes_after:,} bytes "
              f"({'no change (dry run)' if dry_run else f'saved {bytes_before - bytes_after:,} bytes'})")
        print("=" * 60)
        if dry_run:
            print("Dry run only — nothing was written. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))