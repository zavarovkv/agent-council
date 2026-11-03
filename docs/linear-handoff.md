# Linear message protocol v1

This capability separates a public message from evidence used by the coordinator.
It applies to isolated Linear sessions. Native roles continue to use their own
task tools. It grants no new project, document, owner or deployment access.

## Specialist outcome

Use `linear_handoff(action="submit", ...)` once the outcome is ready. Submit one
immutable event per run, reuse its key and identical payload on retry, then finish
the turn. The adapter supplies sender, issue, coordinator and event identity from
the authorized session; model arguments cannot choose another identity.

| Field | Contract |
| --- | --- |
| `key` | Stable request key, 1–80 characters. |
| `kind` | `result` or `blocked`. |
| `summary` | Concrete outcome or missing dependency, at most 500 characters. |
| `details` | Complete internal evidence, at most 24,000 characters. |
| `artifacts` | At most eight HTTPS links with their verified version, at most 160 characters per version. |
| `limitations` | Material uncertainty or incomplete verification, at most 500 characters. |
| `next_action` | Responsible party and next step, at most 300 characters; required for `blocked`. |

The rendered summary, limitations and next action together must fit 700
characters, including labels. Oversized input is rejected before a mutation;
neither results nor evidence should be silently cut to fit. `result` requires
details or an artifact. Artifact metadata is not automatically copied into the
public message: put the useful verified deliverable link in the summary. Combined
metadata is additionally limited to 24,000 encoded bytes so a complete page fits
the internal tool response limit.

`submit` does not complete or accept an issue. For a finished verified deliverable,
use `linear_issues review` first, then submit the internal handoff. A successful
review in the same run already publishes the visible result, so the adapter does
not publish a second result from that handoff. A blocker remains unfinished and
must not be submitted for review.

Store full human-facing deliverables in an authorized document or other artifact.
Internal `details` are accessible to the authoring session and its authorized
coordinator, not through the public Linear discussion. An internal storage receipt
is not evidence that an external document was saved, verified or accepted.

## Coordinator delivery and continuation

Normal results wake the coordinator after all children finish. A blocker wakes it
without waiting for siblings. The notification carries a concise result and an
event reference. Use `linear_handoff(action="get", event_id=..., offset=...)` to
read the immutable evidence in 6,000-character pages while `next_offset` supplies
the next position. The digest identifies stored content, not the truth of its sources.

If a run fails after submission, the immutable event remains available and is not
treated as verified completion. `checkpoint_available` and `checkpoint_chars`
indicate whether newer partial work is retained. Read it with `view="checkpoint"`;
the selected content still arrives in the `details` field using the same paging
fields. The default `view="details"` reads the submitted event's evidence.
Check `revision` and `content_digest` across every page of one view. The revision
is the event ID for details and the event ID plus `:checkpoint` for a failed-run
checkpoint; `content_digest` is SHA-256 of the complete selected content. The
original event `digest` does not change. Neither view proves task acceptance.

Child delivery takes precedence over ordinary queued follow-ups in the same
session. The follow-up stays queued until that delivery finishes; an active run or
owner stop is never preempted. Delivery identity includes the immutable handoff
event, so a newly recorded result is not mistaken for an earlier notification of
the same execution. A repeated notification of the same event remains idempotent.

After resolving a blocked dependency, use
`linear_handoff(action="resume", event_id=..., key=..., message=...)`. Only the
actual coordinator can resume its latest blocked child outcome. The message is
bounded to 2,000 characters and starts another run in the same child session;
it does not create another delegated issue. Reuse the same key and payload when
the response is uncertain. An obsolete event must not overwrite or resume newer
work. New results produce new event references, even with the same summary.

Use `clarify` only when a real owner decision, permission or missing input is
needed. An agent mention in a comment is not a substitute for a queued handoff.
If the coordinator cannot resolve a dependency, it asks the owner once with the
specific decision, consequences and recommended next step.

## Rollout and rollback

1. Review and test the platform change before enabling skills that require
   `linear-handoff-v1`. Deployments remain an explicit operator action.
2. Install the compatible skills bundle by its reviewed full commit SHA. Check
   the capability requirement before changing the instruction snapshot.
3. Exercise a small authorized task with a result, a blocked sibling, a resumed
   child, a same-key retry and a revised result. Confirm evidence survives and
   only one public outcome appears per completed review.
4. Observe completion quality as well as message size: no lost evidence, stale
   resume, duplicate work, incorrect success or unsupported acceptance. Measure
   blocker-to-coordinator delay and public character counts separately.

Structured evidence is bounded to 24,000 characters; legacy executor output is
retained in full up to 128,000 characters. Larger output and exhausted storage
quotas are explicit failures, not silently shortened successes. Each session can
retain at most 128 events, and the store at most 4,096 events. Settled evidence is
pruned after 30 days only when no active sender/coordinator work or unresolved
child depends on it. Its job and delegation lineage remain available while the
event is retained.

Lifecycle operations check retained handoff records through a read-only database
connection. A rollback image without `linear-handoff-v1` is rejected while such
records exist; old databases without this table remain compatible. Roll back to
a compatible platform release, and do not delete retained state to bypass the
check. Skills may be reverted separately while keeping a compatible platform.
