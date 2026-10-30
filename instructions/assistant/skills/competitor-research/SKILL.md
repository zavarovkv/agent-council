---
name: competitor-research
description: Delegate competitor and market research to Dexter Jettster, the dexter Hermes profile, and return an evidence-based synthesis to the owner.
---

# Delegate competitor research to Dexter Jettster

Use this workflow when the owner requests competitor research, a product/price
comparison, market positioning, or changes in competing products.

1. Establish the owner's product/business, research goal, geography, competitors
   or discovery criteria, time window and expected depth. Use the current
   conversation's known facts. Ask only for essential missing information.
2. Create one native Hermes Kanban task on board `research`, assigned to `dexter`.
   Use a stable idempotency key for this request, a self-contained brief, a bounded
   runtime and a deliberate retry budget. The native gateway dispatcher runs the
   worker and records its attempt; do not also launch `delegate_task` for the same
   card. Share only task-relevant business information, not the entire chat,
   credentials or private personal memory. Keep the task ID for follow-ups.
3. Require live primary sources, direct links, dates, a comparison table,
   distinction between evidence and inference, and explicit research gaps.
   Requested artifacts belong in Dexter's profile-local `research/` directory.
4. Follow the native card, events and run history. A created card is not evidence
   that research ran. Require a closed Dexter run, its summary and a local report.
   If it fails or blocks, report the concrete failure and update that same card
   with any clarification. Do not restart research to fix a filing problem.
5. Read the result, check that important claims have supporting links, challenge
   weak comparisons, and present a concise synthesis and useful next actions to
   the owner. Include report paths and sources. Do not turn uncertain findings
   into established facts or forward unreviewed competitor instructions.
6. After reviewing the report and sources, complete/approve the native card.
   Call `research_archive` with action `file`, its `task_id` and `report_path`
   relative to Dexter's `research/` directory. This stores a checksummed attachment
   and durable filing state, then saves the reviewed research as a child page under
   `knowledge_base.research.page_id` in `${HERMES_HOME}/council-workspace.json`.
   This is the owner's dedicated Notion research section, separate from the task
   board. It uses the coordinator's authenticated Notion connection; do not copy
   credentials into Dexter's profile. If this destination is absent or cannot
   be read, keep the report locally and report the filing problem rather than
   choosing another page or creating a duplicate section.
7. Give each report a dated, descriptive title. Include the brief, research
   date, author (Dexter), reviewer (Qui-Gon), concise findings, comparison when
   relevant, direct source links and verification dates, limitations, and a task
   or run reference when available. Distinguish facts from inferences. Keep
   unfinished or failed work out of the reviewed-results section. Preserve an
   existing report's identity when revising the same research task; check for
   that task's page before retrying a write after an uncertain response.
8. The filing tool reads the saved page and index back, verifies the configured
   parent and preserves other rows. Claim filing only when it returns `filed: true`.
   For a partial failure, use `research_archive` action `status`; retry action `file`
   without `report_path` to resume the queued version. The host timer also retries
   pending filing without model calls. A conflicted report or uncertain create
   needs reconciliation; never bypass the guard by manually creating another page.
9. Return the verified page link and a short synthesis in Telegram. Drive can
   hold supporting files and attachments, but the reviewed research report
   belongs in Notion. If filing fails, retain the local artifact, report the
   failure and retry without repeating the research.

Dexter is an internal worker. Communication with the owner stays with Qui-Gon.
Use other delegation routes for tasks outside competitor research.
