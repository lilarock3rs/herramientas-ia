#!/usr/bin/env python3
"""
Salesforce → HubSpot attachment migration CLI.

Usage:
    python migrate.py [--env .env] [--log-file migration.log]

Each Salesforce ContentVersion is:
  1. Looked up in SQLite — skipped if already SUCCESS.
  2. Matched to a HubSpot record via configured SF-ID properties.
  3. Uploaded to HubSpot Files API (in memory, no disk writes).
  4. Linked to a new HubSpot note engagement.
  5. Recorded in SQLite as SUCCESS (or the relevant error status).
"""

import argparse
import logging
import mimetypes
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import load_config
from hs_client import HubSpotClient
from log_setup import setup_logging
from sf_client import SalesforceClient
from state_manager import StateManager

logger = logging.getLogger(__name__)

_LIMIT_FREE_BYTES = 20 * 1024 * 1024        # 20 MB
_LIMIT_PAID_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


# ---------------------------------------------------------------------------
# Structured log line
# ---------------------------------------------------------------------------

def _log_result(
    status: str,
    object_type: Optional[str],
    sf_attachment_id: str,
    hs_note_id: Optional[str],
    file_size_bytes: int,
    message: str,
) -> None:
    mb = file_size_bytes / (1024 * 1024)
    logger.info(
        "[%s] object=%s sf_attachment_id=%s hs_note_id=%s file_size_mb=%.3f message=%s",
        status,
        object_type or "unknown",
        sf_attachment_id,
        hs_note_id or "null",
        mb,
        message,
    )


# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------

def run_migration(env_file: str, log_file: Optional[str]) -> None:
    log_path = setup_logging(log_file)
    logger.info("Log file: %s", log_path)

    config = load_config(env_file)
    state = StateManager(config.db_path)
    sf = SalesforceClient(config)
    hs = HubSpotClient(config)

    file_limit = _LIMIT_PAID_BYTES if config.paid_tier else _LIMIT_FREE_BYTES
    tier_label = "paid (2 GB)" if config.paid_tier else "free (20 MB)"
    logger.info(
        "Starting migration — %d object mapping(s), file-size tier: %s",
        len(config.object_mappings),
        tier_label,
    )

    # ------------------------------------------------------------------
    # 1. Fetch all latest ContentVersions from Salesforce
    # ------------------------------------------------------------------
    logger.info("Fetching ContentVersions from Salesforce...")
    content_versions = sf.get_content_versions()
    logger.info("Found %d ContentVersion record(s)", len(content_versions))

    if not content_versions:
        logger.info("Nothing to migrate.")
        return

    # ------------------------------------------------------------------
    # 2. Build ContentDocumentId → [LinkedEntityId] map
    # ------------------------------------------------------------------
    doc_ids = list({cv["ContentDocumentId"] for cv in content_versions})
    logger.info("Fetching ContentDocumentLinks for %d document(s)...", len(doc_ids))

    doc_to_entities: Dict[str, List[str]] = {}
    for link in sf.get_document_links(doc_ids):
        doc_to_entities.setdefault(link["ContentDocumentId"], []).append(
            link["LinkedEntityId"]
        )

    # ------------------------------------------------------------------
    # 3. Process each ContentVersion
    # ------------------------------------------------------------------
    counts = {"SUCCESS": 0, "SKIPPED": 0, "NOT_FOUND": 0, "FILE_TOO_LARGE": 0, "ERROR": 0}
    total = len(content_versions)

    for idx, cv in enumerate(content_versions, 1):
        cv_id: str = cv["Id"]
        doc_id: str = cv["ContentDocumentId"]
        title: str = cv.get("Title") or "untitled"
        ext: str = cv.get("FileExtension") or ""
        filename = f"{title}.{ext}" if ext else title
        file_size: int = cv.get("ContentSize") or 0

        logger.debug("[%d/%d] Processing %s (id=%s)", idx, total, filename, cv_id)

        # Idempotency check
        if state.is_processed(cv_id):
            _log_result("SKIPPED", None, cv_id, None, file_size, "Already processed")
            counts["SKIPPED"] += 1
            continue

        # Resolve linked SF entity IDs
        linked_entities = doc_to_entities.get(doc_id, [])
        if not linked_entities:
            _log_result(
                "NOT_FOUND", None, cv_id, None, file_size,
                "No ContentDocumentLink found for this document"
            )
            counts["NOT_FOUND"] += 1
            continue

        # File size validation (before any API call)
        if file_size > file_limit:
            _log_result(
                "FILE_TOO_LARGE", None, cv_id, None, file_size,
                f"File is {file_size / 1024 / 1024:.1f} MB — exceeds {tier_label} limit"
            )
            state.mark(cv_id, "FILE_TOO_LARGE")
            counts["FILE_TOO_LARGE"] += 1
            continue

        # Find the first matching HubSpot record across all linked entities + all mappings
        hs_record_id: Optional[str] = None
        matched_object_type: Optional[str] = None
        matched_sf_entity_id: Optional[str] = None

        for sf_entity_id in linked_entities:
            for mapping in config.object_mappings:
                try:
                    rid = hs.search_record(
                        mapping.hs_object, mapping.sf_id_property, sf_entity_id
                    )
                except Exception as exc:
                    logger.warning(
                        "Search error for %s/%s: %s", mapping.hs_object, sf_entity_id, exc
                    )
                    continue
                if rid:
                    hs_record_id = rid
                    matched_object_type = mapping.hs_object
                    matched_sf_entity_id = sf_entity_id
                    break
            if hs_record_id:
                break

        if not hs_record_id:
            _log_result(
                "NOT_FOUND", None, cv_id, None, file_size,
                f"No HubSpot record matched SF entity IDs: {linked_entities}"
            )
            counts["NOT_FOUND"] += 1
            continue

        # ------------------------------------------------------------------
        # Upload → note → associate → mark
        # ------------------------------------------------------------------
        try:
            mime_type, _ = mimetypes.guess_type(filename)
            mime_type = mime_type or "application/octet-stream"

            content_bytes = sf.download_content_version(cv_id)

            file_id = hs.upload_file(content_bytes, filename, mime_type)

            ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            note_body = (
                f"Migrated from Salesforce\n"
                f"File: {filename}\n"
                f"SF ContentVersion ID: {cv_id}\n"
                f"SF Linked Entity ID: {matched_sf_entity_id}"
            )
            note_id = hs.create_note(note_body, file_id, ts_ms)

            hs.associate_note(note_id, matched_object_type, hs_record_id)

            state.mark(cv_id, "SUCCESS", note_id)
            _log_result(
                "SUCCESS", matched_object_type, cv_id, note_id, file_size,
                f"Linked to {matched_object_type}/{hs_record_id}"
            )
            counts["SUCCESS"] += 1

        except Exception as exc:
            logger.exception("Unexpected error on cv_id=%s", cv_id)
            state.mark(cv_id, "ERROR")
            _log_result("ERROR", matched_object_type, cv_id, None, file_size, str(exc))
            counts["ERROR"] += 1

    logger.info(
        "Done. success=%d skipped=%d not_found=%d too_large=%d error=%d",
        counts["SUCCESS"],
        counts["SKIPPED"],
        counts["NOT_FOUND"],
        counts["FILE_TOO_LARGE"],
        counts["ERROR"],
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate Salesforce attachments (ContentVersion) to HubSpot notes."
    )
    p.add_argument(
        "--env",
        default=".env",
        metavar="FILE",
        help="Path to the .env file (default: .env)",
    )
    p.add_argument(
        "--log-file",
        metavar="FILE",
        help="Log file path (default: migration_<timestamp>.log)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_migration(env_file=args.env, log_file=args.log_file)
