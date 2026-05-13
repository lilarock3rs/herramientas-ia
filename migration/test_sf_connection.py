#!/usr/bin/env python3
"""
Quick Salesforce connectivity check.

Usage:
    python test_sf_connection.py          # uses .env in current directory
    python test_sf_connection.py --env path/to/.env
"""

import argparse
import sys

from config import load_config
from sf_client import SalesforceClient


def main(env_file: str) -> None:
    print(f"\n=== Salesforce Connection Test ({env_file}) ===\n")

    # 1. Load config
    print("[1] Loading config...")
    try:
        config = load_config(env_file)
        print(f"    instance_url : {config.sf_instance_url}")
        print(f"    client_id    : {config.sf_client_id[:8]}...")
    except Exception as e:
        print(f"    FAIL — {e}")
        sys.exit(1)

    # 2. Authenticate
    print("\n[2] Authenticating (refresh_token grant)...")
    try:
        sf = SalesforceClient(config)
        print(f"    access_token : {sf.access_token[:12]}...")
        print(f"    instance_url : {sf.instance_url}")
    except Exception as e:
        print(f"    FAIL — {e}")
        sys.exit(1)

    # 3. Query ContentVersions (limit 5)
    print("\n[3] Querying ContentVersion (LIMIT 5)...")
    try:
        rows = sf.query(
            "SELECT Id, Title, FileExtension, ContentSize, ContentDocumentId "
            "FROM ContentVersion WHERE IsLatest = true LIMIT 5"
        )
        if not rows:
            print("    No ContentVersion records found.")
        for r in rows:
            size_kb = (r.get("ContentSize") or 0) / 1024
            print(
                f"    {r['Id']}  {r.get('Title','?')}.{r.get('FileExtension','')}  "
                f"{size_kb:.1f} KB"
            )
    except Exception as e:
        print(f"    FAIL — {e}")
        sys.exit(1)

    # 4. Resolve ContentDocumentLinks for those records
    if rows:
        print("\n[4] Querying ContentDocumentLink for the records above...")
        try:
            doc_ids = [r["ContentDocumentId"] for r in rows]
            links = sf.get_document_links(doc_ids)
            if not links:
                print("    No links found (files may not be attached to any record).")
            for lnk in links:
                print(f"    doc={lnk['ContentDocumentId']}  -> entity={lnk['LinkedEntityId']}")
        except Exception as e:
            print(f"    FAIL — {e}")
            sys.exit(1)

    print("\n=== All checks passed ===\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env", default=".env", metavar="FILE")
    args = p.parse_args()
    main(args.env)
