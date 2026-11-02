# Daily workspace navigation

The owner can add, rename, move or remove materials directly in Notion and Drive.
The generated **Навигация** page under the configured Notion root and **INDEX.md**
in the selected Drive root are rebuilt daily at **06:30 Europe/Moscow**. Nobody
needs to edit the index manually. Both show the same links, hierarchy, item types
and last successful scan time; document bodies are not copied into the index.
Database records and attachments embedded in documents are reached through their
source, not flattened into another database or private-contact export.

Enable after shared workspace setup and deployment of `workspace-index-v1`:

```bash
sudo python3 scripts/manage.py configure-workspace-index
```

The operator verifies a copy and creates one native Hermes Cron script job.
That job invokes the existing authenticated workspace service without an LLM.
The worker scans provider trees, reads back published results and keeps durable
write receipts. It yields the shared cloud lock between operations so other
workspace actions can continue. Index work does not create Linear issues.

Agents discover verified index links through `council_workspace context`.
`index_status` reports freshness and errors. `index_refresh` starts an asynchronous
rebuild for an explicit immediate refresh; poll status for completion. Agent writes
mark pending changes, which the next daily rebuild reconciles with manual edits.
A stale index is a discovery hint, not proof that an item does not exist.

A failed or incomplete scan does not publish a truncated replacement. Managed
indices are recognized by an explicit marker; existing user-authored files with
the same name are never overwritten. An interrupted create is reconciled against
the real root before retrying. If an index itself is moved/deleted or duplicated,
reconcile its identity before creating another copy. No sharing settings change.

Full-tree rebuilds currently allow up to 500 navigation items and 30,000 characters
per index. Exceeding a limit fails visibly and preserves the prior published
version; it requires splitting navigation by section, not silently dropping rows.
The watchdog reports failed refreshes or a last success older than 26 hours.

See [material intake](material-intake.md) and [recurring reports](recurring-work.md).
