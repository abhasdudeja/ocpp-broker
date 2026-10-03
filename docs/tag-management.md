# Tag Management

Tags (RFID cards, app tokens, ...) decide who may charge. When the broker acts as the central system ([broker mode](broker_as_backend.md)) it answers `Authorize`, `StartTransaction` and `StopTransaction` from the organization's tag list. In relay mode the backend authorizes, so the broker's tags are not consulted.

Tag management is always available; there is nothing to enable. Tags are managed per organization.

## Defining tags in `config.yaml`

```yaml
organizations:
  - name: "MyChargingStation"
    connect_to_backend: false
    tags:
      - id_tag: "ADMIN001"              # required, 1-20 printable ASCII characters
        status: "Accepted"              # required
        tag_type: "RFID"                # optional, default RFID
        expiry_date: "2030-12-31T23:59:59Z"
        parent_id_tag: null             # optional
        description: "Administrator card"
        metadata:                       # optional, free-form
          role: "admin"
      - id_tag: "USER123456"
        status: "Accepted"
        parent_id_tag: "ADMIN001"
        description: "Employee"
      - id_tag: "LOST001"
        status: "Blocked"
```

| Field | Values |
|-------|--------|
| `status` | `Accepted`, `Blocked`, `Expired`, `Invalid`, `ConcurrentTx` |
| `tag_type` | `RFID` (default), `NFC`, `QRCode`, `MobileApp`, `UserId` |
| `expiry_date` | ISO 8601 text. A date without a time zone is read as UTC. |
| `parent_id_tag` | Returned to the charger in `idTagInfo`. It has no other effect: a parent's status does not affect its children. |

There are no other tag settings in the configuration file.

## How authorization decides

| Tag | Reply (`idTagInfo.status`) |
|-----|----------------------------|
| not in the organization's list | `Invalid` |
| `expiry_date` in the past, or not a readable date | `Expired` (`expiryDate` is returned) |
| otherwise | the tag's own `status` |

Ids are matched exactly, **including case** (searching through the API is case-insensitive). If tag management is unavailable the answer is `Invalid`; there is no fallback that lets unknown tags charge.

```
> [2,"4","Authorize",{"idTag":"ADMIN001"}]
< [3,"4",{"idTagInfo":{"status":"Accepted","parentIdTag":"ROOT"}}]
```

`StartTransaction` also returns a `transactionId` when the tag is not accepted; the charger is expected to stop the transaction (see [Broker-as-Backend](broker_as_backend.md#transactions)).

## Managing tags with the REST API

All calls need the API key (`X-API-Key`). Full reference: [API Reference](api-reference.md#tags-apitags).

```bash
export BROKER=http://localhost:8765
H='-H "X-API-Key: '$OCPP_BROKER_API_KEY'" -H "Content-Type: application/json"'

# add
curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags" $H \
  -d '{"id_tag": "NEW_USER", "status": "Accepted", "tag_type": "RFID", "metadata": {"dept": "Sales"}}'

# read, replace, delete
curl "$BROKER/api/tags/organizations/MyChargingStation/tags/NEW_USER" $H
curl -X PUT "$BROKER/api/tags/organizations/MyChargingStation/tags/NEW_USER" $H \
  -d '{"id_tag": "NEW_USER", "status": "Blocked"}'
curl -X DELETE "$BROKER/api/tags/organizations/MyChargingStation/tags/NEW_USER" $H

# search
curl "$BROKER/api/tags/organizations/MyChargingStation/tags?status=Accepted&tag_type=RFID&limit=50" $H
```

(On Windows PowerShell, pass the headers explicitly with `-H` instead of the `$H` shorthand.)

Notes:

- `PUT` replaces the whole tag; the body's `id_tag` must be the one in the URL.
- Adding an id that exists returns `400 {"detail": "Failed to add tag"}`.
- Changes take effect for the next `Authorize` immediately.

### Statistics

```bash
curl "$BROKER/api/tags/organizations/MyChargingStation/statistics" $H
```

```json
{"total_tags": 3, "active_tags": 2, "expired_tags": 0, "blocked_tags": 1,
 "tags_by_type": {"RFID": 3}, "tags_by_status": {"Accepted": 2, "Blocked": 1}}
```

`active_tags` counts `Accepted` tags that have not expired; `expired_tags` counts tags with status `Expired` or a past `expiry_date`.

### Validating a tag

`POST .../tags/validate` checks a tag without storing it:

```bash
curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags/validate" $H \
  -d '{"id_tag": "USER123456", "status": "Accepted", "parent_id_tag": "GHOST"}'
```

```json
{"is_valid": false,
 "errors": ["Tag USER123456 already exists in organization MyChargingStation",
            "parent_id_tag 'GHOST' does not exist in organization MyChargingStation"],
 "warnings": []}
```

Errors: an id that is not printable ASCII, an id that already exists (add `?for_update=true` to skip this check), an unreadable `expiry_date`, a `parent_id_tag` that does not exist or is the tag itself. A past `expiry_date` is only a warning.

### Bulk changes

```bash
curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags/bulk" $H -d '{
  "operation": "add",
  "tags": [{"id_tag": "BULK001", "status": "Accepted"}, {"id_tag": "BULK002", "status": "Accepted"}]
}'
```

`operation` is `add`, `update` or `delete`. Each tag succeeds or fails on its own; the response lists every tag with `success` and an `error` such as `already exists` or `not found`.

### Import and export

```bash
curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags/import" $H -d '{
  "source": "json",
  "data": "{\"tags\": [{\"id_tag\": \"IMPORT001\", \"status\": \"Accepted\"}]}",
  "overwrite_existing": false,
  "validate_only": false
}'

curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags/export" $H \
  -d '{"format": "csv", "include_metadata": false}' --output tags.csv
```

- **Sources and formats:** `json` (either `{"tags": [...]}` or a bare list) and `csv`.
- **CSV columns:** `id_tag,status,tag_type,expiry_date,parent_id_tag,description`, plus `created_at,updated_at,metadata` (JSON text) when metadata is included. Empty cells are treated as absent.
- **Bad records** are reported in `errors` (`record` number, `id_tag`, reason) and the rest are imported. Duplicate ids inside one import are rejected after the first.
- **Existing tags** are skipped (`skipped`) unless `overwrite_existing` is true, in which case they are replaced (`updated`).
- **Parents** may be defined in the same import, in any order.
- **`validate_only: true`** reports what would happen and changes nothing.
- **Unparseable text** is `400`.
- **Exports** re-import without loss. CSV comes back as a file download, JSON as `{"organization", "exported_at", "count", "tags": [...]}`.

## Persistence and MongoDB

Without MongoDB the tag list lives in memory: tags in `config.yaml` come back on restart, tags added through the API do not.

With MongoDB enabled every API change is also written to the `tags` collection, and a tag that is not in memory is looked up there when needed. See [MongoDB Integration](mongodb-integration.md#tags).

### Syncing with MongoDB

If tags are changed in MongoDB directly (or by another broker instance), tell the broker to reload:

```bash
curl -X POST "$BROKER/api/tags/sync" $H                       # every organization
curl -X POST "$BROKER/api/tags/sync?org_name=MyChargingStation" $H
```

```json
{"success": true, "message": "Synced tags from MongoDB (all organizations)",
 "organizations": {"MyChargingStation": {"loaded": 3, "seeded": 0, "dropped": 0}}}
```

- For an organization that has stored tags, MongoDB wins: the broker's list is replaced by what is stored, so a tag removed or revoked in MongoDB stops working after a sync. `dropped` counts in-memory tags MongoDB did not have.
- An organization that has tags in memory (typically from `config.yaml`) but none stored is pushed into MongoDB instead (`seeded`), so config tags are never wiped.
- Without MongoDB the call returns `503`.

Until a sync, tags the broker already holds in memory keep their old status; each broker instance has its own copy.

## Common situations

| Charger gets | Cause |
|--------------|-------|
| `Invalid` | The id is not in the organization's list (check spelling and case, and that the charger's organization is the one you added the tag to), or the broker could not reach tag management |
| `Expired` | `expiry_date` is in the past, or it is not a valid ISO 8601 date |
| `Blocked` | The tag's `status` is `Blocked` |

To test what a charger would get without a charger:

```bash
curl -X POST "$BROKER/api/tags/organizations/MyChargingStation/tags/authorize" $H -d '"USER123456"'
```

```json
{"idTag": "USER123456", "idTagInfo": {"status": "Accepted", "expiryDate": null, "parentIdTag": "ADMIN001"}}
```

## Related documentation

- [Broker-as-Backend Mode](broker_as_backend.md)
- [API Reference](api-reference.md)
- [Configuration Guide](configuration.md)
- [MongoDB Integration](mongodb-integration.md)
- [Troubleshooting](troubleshooting.md)
