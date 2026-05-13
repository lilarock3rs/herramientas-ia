# Salesforce to HubSpot Attachment Migration
## Technical Brief

---

### 1. Context

The client is migrating from Salesforce to HubSpot. As part of that migration, attachments from the Salesforce `Attachment` object must be transferred to HubSpot by creating a **note** engagement per attachment and associating it to the corresponding HubSpot record.

The service is designed to be **reusable across accounts** (different SF → HS clients) and will run locally in **VS Code** during the testing phase.

---

### 2. Tech Stack

| Component | Detail |
|---|---|
| Language | Python 3.9+ |
| Environment | Local (VS Code) |
| Configuration | Per-account `.env` file |
| State | Local SQLite for idempotency |

---

### 3. Technical Requirements

#### Source — Salesforce

- Connection via **REST API with OAuth 2.0** (Connected App) using the **refresh token grant**
- Attachments are read and streamed **in memory** (no local file persistence)
- Volume is variable per account — the service makes no size assumptions
- On HTTP 401 (token expiry), the client automatically re-authenticates using the refresh token and retries the request once

**OAuth 2.0 — Refresh Token flow**

```
POST {SF_INSTANCE_URL}/services/oauth2/token
  grant_type=refresh_token
  client_id={SF_CLIENT_ID}
  client_secret={SF_CLIENT_SECRET}
  refresh_token={SF_REFRESH_TOKEN}
```

Response provides a new `access_token` (and confirms `instance_url`).

**Salesforce Files (modern architecture)**

Salesforce uses three objects to represent files:

| Object | Role | Endpoint |
|---|---|---|
| `ContentDocument` | Logical file entity | `/services/data/vXX.X/sobjects/ContentDocument` |
| `ContentVersion` | Physical/binary version of the file | `/services/data/vXX.X/sobjects/ContentVersion` |
| `ContentDocumentLink` | Links a file to a CRM record | Queried via SOQL |

**Retrieve file metadata:**
```sql
SELECT Id, Title, FileExtension, ContentDocumentId, ContentSize
FROM ContentVersion
WHERE IsLatest = true
ORDER BY CreatedDate ASC
```

**Download binary content:**
```
GET {SF_INSTANCE_URL}/services/data/vXX.X/sobjects/ContentVersion/{Id}/VersionData
```

**Resolve file-to-record relationships:**
```sql
SELECT ContentDocumentId, LinkedEntityId
FROM ContentDocumentLink
WHERE ContentDocumentId IN (...)
```

#### Destination — HubSpot

- Connection via **HubSpot API** (Private App token in `.env`)
- Flow per attachment:
  1. Upload the file to HubSpot Files API
  2. Create a **note** engagement referencing the file
  3. Associate the note to the corresponding HubSpot record using the SF ID as a lookup field
- Rate limits enforced: **190 req / 10s** per app, **625,000 req / day** per account
- **Automatic** throttling with sleep + retry on 429

#### File Size Limits

| Tier | Limit |
|---|---|
| Free | 20 MB |
| Paid (general) | Up to 2 GB |
| Property Files | 20 MB (free) / 50 MB (paid) |

The service must validate file size before attempting upload and log it as an error if it exceeds the applicable limit.

---

### 4. Configuration (`.env`)

```env
# Salesforce — Connected App credentials + OAuth refresh token
SF_CLIENT_ID=
SF_CLIENT_SECRET=
SF_REFRESH_TOKEN=
SF_INSTANCE_URL=https://your-org.my.salesforce.com

# HubSpot — Private App token
HS_ACCESS_TOKEN=

# Objects to migrate
# Format: HubSpotObjectType:sf_id_property_name
# Example: contacts:salesforce_contact_id,deals:salesforce_opportunity_id
HS_OBJECT_MAPPINGS=

# Optional
# PAID_TIER=false      # set to true to allow up to 2 GB uploads (default: 20 MB)
# DB_PATH=migration_state.db
```

---

### 5. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CLI Runner                        │
│              python migrate.py                       │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │    Config Loader        │
          │    (.env parser)        │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │    State Manager        │
          │    (SQLite local)       │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │   Salesforce Client     │
          │   OAuth2 refresh_token  │
          │   ContentVersion SOQL   │
          └────────────┬────────────┘
                       │ streamed in memory
          ┌────────────▼────────────┐
          │   HubSpot Client        │
          │   Files API             │
          │   Engagements API       │
          │   Associations API      │
          │   Internal rate limiter │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │      Logger             │
          │  (console + file)       │
          └─────────────────────────┘
```

---

### 6. Expected Behavior

#### Main flow per attachment

1. Fetch all `ContentVersion` records from SF (latest versions only)
2. Resolve `ContentDocumentLink` to map each file to its linked SF record(s) — batched in groups of 200
3. For each `ContentVersion`:
   1. Check SQLite if `ContentVersion.Id` was already processed → skip if found (`SKIPPED`)
   2. If no `ContentDocumentLink` exists for the file → log `NOT_FOUND` and continue
   3. Validate `ContentSize` against the configured tier limit → log `FILE_TOO_LARGE` and continue if exceeded
   4. Search HubSpot for a matching record using the SF entity ID and configured property name
   5. If no matching HubSpot record found → log `NOT_FOUND` and continue
   6. Download binary from SF into memory via `VersionData` endpoint
   7. Upload file to HubSpot Files API (multipart, in memory)
   8. Create note engagement referencing the uploaded file
   9. Associate note to the HubSpot record
   10. Mark `ContentVersion.Id` as `SUCCESS` in SQLite
   11. Log result

#### Idempotency

- SQLite persists `ContentVersion.Id` values from SF that were successfully migrated
- On re-run, `SUCCESS` records are skipped without making any API calls
- `ERROR` and `NOT_FOUND` records are retried on each run

#### Throttling

- Internal rate limiter enforces 190 req/10s
- On 429 response: automatic sleep + retry with exponential backoff

---

### 7. Output / Log

Line format:

```
[TIMESTAMP] [STATUS] object=<hs_object> sf_attachment_id=<id> hs_note_id=<id|null> file_size_mb=<size> message=<description>
```

**Possible statuses:**

| Status | Description |
|---|---|
| `SUCCESS` | Migration complete. Includes `hs_note_id` and `sf_attachment_id` |
| `SKIPPED` | Already processed in a previous run |
| `NOT_FOUND` | No HubSpot record found with that SF ID |
| `FILE_TOO_LARGE` | File exceeds tier limit |
| `ERROR` | API failure or unexpected error |

Log is emitted to console **and** persisted to a `.log` file.

---

### 8. Constraints

- No local file downloads — file binaries are streamed in memory (HTTP stream from Salesforce → multipart upload to HubSpot Files API). Files are never written to disk, but **do pass through RAM**. File size is logged on every record (`file_size_mb`) to allow post-run identification of large files.
- One account per execution (one active `.env` at a time)
- HubSpot objects and their SF ID fields are configured in `.env`, not hardcoded
- The service does not create or modify HubSpot properties — it only reads and writes records

---

### 9. Definition of Done (DoD)

Work is considered complete when:

- [ ] All `ContentVersion` records from SF are processed (or logged with their skip/error reason)
- [ ] Each successful file has a note created and associated in HubSpot with the file attached
- [ ] The log reflects the result of each record with `sf_attachment_id` (`ContentVersion.Id`) and `hs_note_id`
- [ ] SQLite prevents duplicate notes across re-runs
- [ ] Throttling handles HubSpot API limits without manual intervention
- [ ] Token expiry (401) is handled transparently via refresh token re-auth
- [ ] The service is fully configurable via `.env` without modifying code
- [ ] Runs without errors on Python 3.9+ in local environment (VS Code)