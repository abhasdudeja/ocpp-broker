# Changing organizations while the broker runs

The admin API (and the console's **Admin** page, which uses it) lets you add, change and remove organizations, their backends and the credentials their chargers connect with, without editing `config.yaml` by hand and restarting the broker.

It is **off by default**, because it lets whoever holds an API key rewrite the configuration file and decide which chargers may connect:

```yaml
admin:
  enabled: true              # default false: every /api/admin route answers 403
  audit_log: /var/log/ocpp-broker/admin-audit.jsonl   # default: next to the configuration file
  keep_backups: 10           # copies of the configuration kept, default 10
```

Turn it on only where the API key is as private as the configuration file itself. Give each person their own labelled key (`security.api_keys`, see [Configuration](configuration.md)) so the audit log says who did what.

## What it changes

It changes the **`organizations`** of the configuration file the broker was started with (`-c`, or `OCPP_BROKER_CONFIG`, or `config.yaml`), and nothing else in it. Ports, MongoDB, security and the other sections are written back as they were read, and a value that came from an environment variable is not written into the file. For each organization it edits:

- whether it connects to backends, its OCPP subprotocol and its **backends** (id, address, leader, or `local` for this broker; see [Leader/Follower](leader-follower.md));
- the relay tuning: `backend_buffer_size`, `backend_outage_timeout`, `leader_failover_timeout`, and the `transaction_ids` settings;
- the **charger credentials**: which chargers may connect, whether they must authenticate, and their passwords.

Anything else the organization holds (its `tags`, `tag_management`, settings written by hand) is kept exactly as it is. Tags are edited on the Tags page. An organization's name cannot be changed (it is part of the address chargers connect to); add the new name and remove the old. A new name is 1 to 64 letters, digits, dots, dashes or underscores.

If there is no configuration file (the broker started with built-in defaults), there is nothing to change and every change is refused.

## Making a change: read, check, apply

1. **Read** (`GET /api/admin/config`): the organizations as the broker reads them, and the file's **revision**.
2. **Check** (`POST /api/admin/config/validate`): send the changes and the revision. The answer lists the errors and warnings and says in words what would change: `+ backend standby (ws://...)`, `~ credential for CP1: password replaced`, `connect_to_backend: false → true`. Nothing is written. It also says how many chargers are connected to each organization the changes touch.
3. **Apply** (`POST /api/admin/config/apply`): the same body, optionally with `drop_connections`. The configuration is checked again with the same rules the broker applies at startup; if it is sound, the file is replaced and the broker uses the new organizations from then on.

A change is a list of `upsert` (add the organization, or give the one with that name its new settings) and `delete`.

**What applying does to the file.** It writes a temporary file next to the configuration, flushes it, and replaces the configuration with it in one step, so a crash or a full disk leaves the old file whole. First it keeps a copy of the old file as `config.yaml.bak-<time>` (the latest `keep_backups` are kept) and the new file says which copy that is. The file keeps its permissions. **Writing the file drops the comments in it**: that is the price of rewriting YAML, and the copy is the way back.

**When the file changed meanwhile.** Every change names the revision it was made from. If the file is not that revision any more (someone edited it, or another change was applied), the change is refused with `409` rather than overwriting what is there. Read again and redo it.

**Several instances.** Each broker instance has its own configuration file. A change applied to one is not seen by the others until they are restarted (or changed the same way); keep that in mind before using this with more than one instance, or keep the file in one place and restart the others.

## What chargers see

The new organizations apply to **chargers that connect after the change**: a new organization accepts chargers, a removed one refuses them, a changed password is the one that counts for the next connection.

A charger that is **already connected keeps the settings it connected with** until it reconnects: its backends, its buffering, even a removed organization, and a password that was changed after it authenticated. The check shows how many chargers that is. To make them reconnect now, name the organization in `drop_connections` when applying: its connected chargers are disconnected (close code 1012, `Configuration changed`) and reconnect by themselves under the new settings. Only organizations the change touches can be named.

## Passwords

Passwords only go in. A password you send is hashed (`pbkdf2_sha256`) and **only the hash is written** to the file. Nothing the broker returns holds a password or a hash: the organizations list shows each credential by charger id and whether it is stored as a `hash` or as `plaintext` (an old file written by hand may have plaintext; set that password again to replace it with a hash). A credential sent without a password keeps what the file has; one left out of the list is removed. A password needs at least 8 characters (16 or more are recommended; OCPP calls it the authorization key and chargers are configured with it).

A request the broker cannot read is answered without repeating what was sent, so a password is not echoed back in the error.

## The audit log

Every change that was applied, refused or failed is logged: when, the address it came from (as the broker sees it: behind a proxy that is the proxy), the **label of the API key** it used, which organizations it touched and what changed in words, the new revision, and why it was refused or failed. Checking a change is not logged. The log never holds a password, a hash or an API key.

It is kept in memory (the latest 500, what `GET /api/admin/audit` and the console list, newest first) and appended to `admin.audit_log` as one JSON object per line, so it survives a restart. If the file cannot be written the broker logs that once and keeps the log in memory.

With one shared API key the label is `api-key` for everyone. Give people their own labelled keys to tell them apart.

## What this is not

- It does not manage users, roles or permissions: every API key opens everything the API allows, the admin routes included.
- It does not change the sections other than `organizations`; edit the file and restart for those.
- It does not undo: to go back, copy a `config.yaml.bak-...` file over the configuration and restart (or apply the reverse change).

See the [API reference](api-reference.md#admin-apiadmin) for the routes.
