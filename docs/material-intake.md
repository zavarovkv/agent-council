# Owner attachment intake

Qui-Gon uses the reviewed private `material-intake` skill to inspect attachments,
identify the project from content and conversation context, and choose existing
Notion/Drive locations. Originals go to Drive; useful structured notes, extracts
and edited text go to Notion with a link to the original. A plain photo need not
create an empty Notion page. Ambiguous projects go to the selected Drive root's
«Входящие» folder, followed by one concise clarification.

`material_read` extracts bounded, paginated text from PDF, DOCX, XLSX, PPTX and
UTF-8 files. A PDF page can be rendered for the existing vision tool to inspect
scans. Extraction runs in a subprocess with time/memory limits and never executes
Office macros or formulas. It does not claim to read embedded Office images or
recalculate spreadsheets. The PDF dependency is hash-pinned in the image, not
installed by the agent during an unattended run.

The shared `council_workspace` tool adds `drive_upload` for the main native owner
assistant. It reads a regular original file under its inbound `cache/images`,
`documents`, `audio` or `videos` directory, up to 50 MiB. Profile settings, secrets,
symlinks and other agents' filesystem paths are excluded. The file is uploaded
without base64 in the model context. Specialists use the resulting shared link.

Uploads preserve bytes and verify size/MD5 on readback. A SHA-256 property detects
identical bytes already in the selected folder; stable write keys preserve known
results and stop blind replays after uncertain cloud writes. Local originals are
not deleted. Uploading is distinct from successfully extracting all content:
corrupt, encrypted, unsupported or partly read files must be reported honestly.
No permission changes or public sharing are part of intake.
