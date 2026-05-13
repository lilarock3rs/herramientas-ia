# Salesforce to HubSpot Attachment Migration
## Code Review Protocol

---

### Point 1 — Salesforce Connectivity and File Retrieval

- [ ] Authenticate via OAuth 2.0 and confirm token is obtained without errors
- [ ] Execute the `ContentVersion` SOQL query and verify it returns records
- [ ] Confirm `ContentDocumentLink` correctly resolves `LinkedEntityId` for at least one known SF record
- [ ] Verify the `VersionData` stream is read into memory without writing to disk (no temp files created)

**Evidence:** Run against a sandbox org with 2–3 known files and confirm metadata matches

---

### Point 2 — HubSpot Record Lookup and Association

- [ ] For each configured object in `HS_OBJECT_MAPPINGS`, query HubSpot using the SF ID field and confirm the correct record is returned
- [ ] Verify behavior when SF ID does not exist in HubSpot → must log `NOT_FOUND` and continue (no exception thrown)
- [ ] Confirm the note is associated to the correct HubSpot record ID, not just created in isolation

**Evidence:** Manually verify in HubSpot UI that the note appears on the expected contact / deal / object

---

### Point 3 — File Upload and Note Creation Integrity

- [ ] Confirm the file uploaded to HubSpot Files API matches the original (same name, extension, and size)
- [ ] Verify `file_size_mb` in the log matches the actual file size
- [ ] Confirm the note body references the uploaded file correctly
- [ ] Test with at least one file per common type (PDF, DOCX, image)

**Evidence:** Download the file from HubSpot after upload and compare against the original in Salesforce

---

### Point 4 — Idempotency and State Management

- [ ] Run the migration twice against the same dataset — second run must produce only `SKIPPED` statuses
- [ ] Confirm zero duplicate notes created in HubSpot after the second run
- [ ] Inspect the SQLite database and confirm processed `ContentVersion.Id` values are persisted correctly
- [ ] Simulate an interrupted run (kill mid-execution) and confirm re-run resumes cleanly from the last unprocessed record

**Evidence:** SQLite row count + HubSpot note count must match after both runs

---

### Point 5 — Rate Limiting, Error Handling, and Log Completeness

- [ ] Confirm the rate limiter enforces 190 req/10s (inspect sleep behavior under load or mock the limit)
- [ ] Simulate a 429 response and verify automatic retry with backoff — no crash, no data loss
- [ ] Introduce a deliberate error (wrong token, missing field) and confirm it logs `ERROR` with a meaningful message without stopping the entire run
- [ ] Verify every processed record appears in the log with all fields: `object`, `sf_attachment_id`, `hs_note_id`, `file_size_mb`, `message`
- [ ] Confirm the `.log` file is created and matches console output

**Evidence:** Review log file end-to-end — zero records without a status line