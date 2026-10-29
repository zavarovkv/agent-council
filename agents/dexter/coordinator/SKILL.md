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

Dexter is an internal worker. Communication with the owner stays with Qui-Gon.
Use other delegation routes for tasks outside competitor research.
