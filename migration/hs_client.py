"""HubSpot API client — Files, Notes (Engagements), Associations + rate limiter."""

import json
import logging
import time
from collections import deque
from threading import Lock
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com"

# Standard HubSpot-defined association type IDs: note → object
_NOTE_ASSOC_TYPE = {
    "contacts": 202,
    "companies": 190,
    "deals": 214,
    "tickets": 216,
}


class _RateLimiter:
    """Sliding-window rate limiter: max 190 requests per 10 seconds."""

    def __init__(self, max_requests: int = 190, window_seconds: float = 10.0) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            # Evict timestamps outside the current window
            while self._timestamps and now - self._timestamps[0] >= self._window:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._max:
                wait = self._window - (now - self._timestamps[0]) + 0.05
                logger.debug("Rate limit: sleeping %.3fs", wait)
                time.sleep(wait)
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self._window:
                    self._timestamps.popleft()

            self._timestamps.append(time.monotonic())


class HubSpotClient:
    def __init__(self, config) -> None:
        self._limiter = _RateLimiter()
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {config.hs_access_token}"}
        )

    # ------------------------------------------------------------------
    # Internal request helper (rate-limit + 429 retry)
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        self._limiter.acquire()
        url = f"{_BASE}{path}"
        backoff = 1
        for attempt in range(6):
            resp = self._session.request(method, url, timeout=60, **kwargs)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", backoff))
                logger.warning(
                    "429 — retrying in %ds (attempt %d/6)", retry_after, attempt + 1
                )
                time.sleep(retry_after)
                backoff = min(backoff * 2, 60)
                self._limiter.acquire()
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp  # unreachable, satisfies type checker

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def search_record(
        self, object_type: str, property_name: str, sf_id: str
    ) -> Optional[str]:
        """Return the HubSpot record ID whose `property_name` equals `sf_id`, or None."""
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": property_name,
                            "operator": "EQ",
                            "value": sf_id,
                        }
                    ]
                }
            ],
            "properties": ["id"],
            "limit": 1,
        }
        resp = self._request("POST", f"/crm/v3/objects/{object_type}/search", json=body)
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None

    def upload_file(self, content: bytes, filename: str, mime_type: str) -> str:
        """Upload a file to HubSpot Files API v3. Returns the file ID."""
        files = {
            "file": (filename, content, mime_type),
            "options": (
                None,
                json.dumps(
                    {
                        "access": "PRIVATE",
                        "overwrite": False,
                        "duplicateValidationStrategy": "NONE",
                    }
                ),
                "application/json",
            ),
            "folderPath": (None, "/sf-migration"),
        }
        resp = self._request("POST", "/files/v3/files", files=files)
        return str(resp.json()["id"])

    def create_note(self, body_text: str, file_id: str, timestamp_ms: int) -> str:
        """Create a note engagement with an attached file. Returns the note ID."""
        props = {
            "hs_note_body": body_text,
            "hs_timestamp": str(timestamp_ms),
            "hs_attachment_ids": file_id,
        }
        resp = self._request("POST", "/crm/v3/objects/notes", json={"properties": props})
        return str(resp.json()["id"])

    def associate_note(
        self, note_id: str, object_type: str, object_id: str
    ) -> None:
        """Associate a note to a HubSpot record via CRM Associations API v4."""
        assoc_type_id = _NOTE_ASSOC_TYPE.get(object_type)
        if assoc_type_id is None:
            logger.warning(
                "No built-in association type for object '%s' — skipping association "
                "(note %s created but not linked to record %s)",
                object_type,
                note_id,
                object_id,
            )
            return

        path = f"/crm/v4/objects/notes/{note_id}/associations/{object_type}/{object_id}"
        body = [
            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": assoc_type_id}
        ]
        self._request("PUT", path, json=body)
