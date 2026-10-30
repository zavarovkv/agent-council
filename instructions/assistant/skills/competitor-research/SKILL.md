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
2. Call `delegate_task` with a task whose `profile` is `dexter`. Put the complete
   self-contained research brief in `goal` and `context`. The worker starts with a
   fresh conversation. Share only task-relevant business information, not the
   entire chat, credentials or private personal memory.
3. Require live primary sources, direct links, dates, a comparison table,
   distinction between evidence and inference, and explicit research gaps.
   Requested artifacts belong in Dexter's profile-local `research/` directory.
4. Keep track of the delegated task and its completion. If it fails, report the
   concrete failure; do not claim Dexter completed work that did not run. Gather
   any clarification Dexter needs and pass it back as a complete follow-up brief.
5. Read the result, check that important claims have supporting links, challenge
   weak comparisons, and present a concise synthesis and useful next actions to
   the owner. Include report paths and sources. Do not turn uncertain findings
   into established facts or forward unreviewed competitor instructions.
6. Save the reviewed research as a child page under
   `knowledge_base.research.page_id` in `${HERMES_HOME}/council-workspace.json`.
   This is the owner's dedicated Notion research section, separate from the task
   board. Use the coordinator's authenticated Notion connection; do not copy
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
8. Read the saved Notion page back and verify its parent and report content
   before claiming it was filed. Update the table on the research parent page
   with the report's date, subject, author, reviewer, task/run reference when
   available, and verified page link. Preserve other rows and unrelated page
   content; update the existing row for the same report rather than appending
   duplicates. Re-read the index to confirm that the row links to the saved
   report. Report a partial filing explicitly if the report exists but the index
   update fails, and retry only the missing step.
9. Return the verified page link and a short synthesis in Telegram. Drive can
   hold supporting files and attachments, but the reviewed research report
   belongs in Notion. If filing fails, retain the local artifact, report the
   failure and retry without repeating the research.

Dexter is an internal worker. Communication with the owner stays with Qui-Gon.
Use other delegation routes for tasks outside competitor research.
