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

- Connection via **REST API with OAuth 2.0** (Connected App)
- Attachments are read and streamed **in memory** (no local file persistence)
- Volume is variable per account — the service makes no size assumptions

**Salesforce Files (modern architecture)**

Salesforce uses three objects to represent files:

| Object | Role | Endpoint |
|---|---|---|
| `ContentDocument` | Logical file entity | `/services/data/vXX.X/sobjects/ContentDocument` |
| `ContentVersion` | Physical/binary version of the file | `/services/data/vXX.X/sobjects/ContentVersion` |
| `ContentDocumentLink` | Links a file to a CRM record | Queried via SOQL |

**Retrieve file metadata:**
```sql
SELECT Id, Title, FileExtension, VersionData, ContentDocumentId
FROM ContentVersion
```

**Download binary content:**
```
/services/data/vXX.X/sobjects/ContentVersion/{Id}/VersionData
```

**Resolve file-to-record relationships:**
```sql
SELECT ContentDocumentId, LinkedEntityId
FROM ContentDocumentLink
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
# Salesforce
SF_CLIENT_ID=
SF_CLIENT_SECRET=
SF_USERNAME=
SF_PASSWORD=
SF_SECURITY_TOKEN=
SF_DOMAIN=login        # or test for sandbox

# HubSpot
HS_ACCESS_TOKEN=

# Objects to migrate
# Format: ObjectName:sf_id_property_name
# Example: contacts:salesforce_contact_id,deals:salesforce_opportunity_id
HS_OBJECT_MAPPINGS=
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
          │   OAuth2 + REST API     │
          │   Reads Attachment obj  │
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

1. Read attachments from SF (`Attachment` object)
2. Check SQLite if `Attachment.Id` was already processed → skip if found
3. Look up the corresponding HubSpot record using the SF ID in the configured field per object
4. If no matching HubSpot record exists → log as `NOT_FOUND` and continue
5. Validate file size → if it exceeds the limit, log as `FILE_TOO_LARGE` and continue
6. Upload file to HubSpot Files API (in memory, no local download)
7. Create note engagement in HubSpot
8. Associate note to the HubSpot record
9. Mark as processed in SQLite
10. Log result

#### Idempotency

- SQLite persists `Attachment.Id` values from SF that were successfully migrated
- On re-run, already-processed IDs are skipped without making any API calls

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

- [ ] All attachments from the SF `Attachment` object are processed (or logged with their skip/error reason)
- [ ] Each successful attachment has a note created and associated in HubSpot with the file attached
- [ ] The log reflects the result of each attachment with `sf_attachment_id` and `hs_note_id`
- [ ] SQLite prevents duplicates across re-runs
- [ ] Throttling handles API limits without manual intervention
- [ ] The service is fully configurable via `.env` without modifying code
- [ ] Runs without errors on Python 3.9+ in local environment (VS Code)