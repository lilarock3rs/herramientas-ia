"""Salesforce REST API client — OAuth2 (username-password flow) + SOQL + file download."""

import logging
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

_API_VERSION = "v57.0"
_BATCH_SIZE = 200  # max IDs per IN clause to stay well within SOQL limits


class SalesforceClient:
    def __init__(self, config) -> None:
        self._config = config
        self.access_token: str = ""
        self.instance_url: str = ""
        self._session = requests.Session()
        self._authenticate()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _authenticate(self) -> None:
        domain = self._config.sf_domain
        url = f"https://{domain}.salesforce.com/services/oauth2/token"
        payload = {
            "grant_type": "password",
            "client_id": self._config.sf_client_id,
            "client_secret": self._config.sf_client_secret,
            "username": self._config.sf_username,
            "password": self._config.sf_password + self._config.sf_security_token,
        }
        resp = requests.post(url, data=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.instance_url = data["instance_url"]
        self._session.headers.update({"Authorization": f"Bearer {self.access_token}"})
        logger.info("Authenticated to Salesforce: %s", self.instance_url)

    # ------------------------------------------------------------------
    # Internal GET with token-refresh on 401
    # ------------------------------------------------------------------

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET with one automatic re-auth retry on 401 (token expiry)."""
        resp = self._session.get(url, **kwargs)
        if resp.status_code == 401:
            logger.info("SF token expired — re-authenticating...")
            self._authenticate()
            resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # SOQL
    # ------------------------------------------------------------------

    def query(self, soql: str) -> List[Dict[str, Any]]:
        """Execute a SOQL query, handling pagination automatically."""
        url = f"{self.instance_url}/services/data/{_API_VERSION}/query"
        resp = self._get(url, params={"q": soql}, timeout=60)
        data = resp.json()
        records = list(data.get("records", []))

        while not data.get("done", True) and data.get("nextRecordsUrl"):
            next_url = self.instance_url + data["nextRecordsUrl"]
            resp = self._get(next_url, timeout=60)
            data = resp.json()
            records.extend(data.get("records", []))

        return records

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def get_content_versions(self) -> List[Dict[str, Any]]:
        """Return all latest ContentVersions with fields needed for migration."""
        soql = (
            "SELECT Id, Title, FileExtension, ContentDocumentId, ContentSize "
            "FROM ContentVersion "
            "WHERE IsLatest = true "
            "ORDER BY CreatedDate ASC"
        )
        return self.query(soql)

    def get_document_links(self, doc_ids: List[str]) -> List[Dict[str, Any]]:
        """Return ContentDocumentLinks for a batch of ContentDocumentIds."""
        if not doc_ids:
            return []
        results: List[Dict[str, Any]] = []
        for i in range(0, len(doc_ids), _BATCH_SIZE):
            batch = doc_ids[i : i + _BATCH_SIZE]
            id_list = ", ".join(f"'{d}'" for d in batch)
            soql = (
                f"SELECT ContentDocumentId, LinkedEntityId "
                f"FROM ContentDocumentLink "
                f"WHERE ContentDocumentId IN ({id_list})"
            )
            results.extend(self.query(soql))
        return results

    # ------------------------------------------------------------------
    # Binary download
    # ------------------------------------------------------------------

    def download_content_version(self, cv_id: str) -> bytes:
        """Download binary content of a ContentVersion entirely into memory."""
        url = (
            f"{self.instance_url}/services/data/{_API_VERSION}"
            f"/sobjects/ContentVersion/{cv_id}/VersionData"
        )
        resp = self._get(url, timeout=120)
        return resp.content
