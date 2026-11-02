# Google services

Calendar, Gmail and GA4 use the main profile's existing Desktop OAuth client and
private `google_token.json`. No OAuth keys are copied into specialist profiles.
`council_google` uses the existing internal authenticated workspace listener.
The public HTTP site gains no Google callback or API route.

## Connect

Enable Google Calendar API, Gmail API, Google Analytics Admin API and Google
Analytics Data API in the same Google Cloud project as Drive. Then, on the
operator's computer:

```bash
python3 scripts/setup-drive.py --services drive,calendar,gmail,analytics
python3 scripts/setup-drive.py --services drive,calendar,gmail,analytics --verify
```

Open the consent URL on that computer. The SSH tunnel receives the loopback
callback; do not paste authorization codes, callback URLs or credentials into
chat. This expands the saved scopes, preserves Drive and checks that the account
matches the current Drive owner. Existing granted scopes are retained. A denied,
partial or failed expansion does not replace the working grant. Read-only API
checks and a successful refresh precede atomic replacement. Enable all requested
APIs before consent; otherwise the existing grant remains unchanged.

Scopes: existing `drive`; `calendar.events`, `calendar.calendarlist.readonly`,
`calendar.freebusy`; `gmail.modify`; `analytics.readonly`. Gmail's modify scope
includes compose/send capability, but the integration exposes only bounded reads,
draft creation and explicitly requested sending. It exposes no mailbox deletion,
forwarding configuration, calendar ACL changes or Analytics administration.

Google Cloud **Testing** audience can expire refresh grants after seven days.
Select the appropriate publishing status for the existing private application.
The Google account must have access to the desired GA4 properties; granting OAuth
does not create a property, install tracking or grant access to another account.

## Use

`service=calendar` supports calendar discovery, event reads, busy windows, timed
meeting creation, updates and cancellation. Times need an explicit UTC offset;
the default IANA timezone is Europe/Moscow. Participants require actual email
addresses. Writes request `sendUpdates=all`, preserving notification intent.
Meeting creation can request Google Meet; conference provisioning may remain
pending, so do not claim a ready Meet URL until returned. Updates/cancellation
require a freshly read event ETag and use `If-Match`.

`service=gmail` supports profile, search, message reads, draft listing/reading,
draft creation, sending, replies and sending a reviewed saved draft. Reads do
not mark mail read. Reply recipients remain explicit and threading headers are
preserved. Draft sends include the reviewed raw snapshot, with its message ID
checked before sending. Mail attachments are listed but not downloaded by this
tool. Remote images are not loaded. HTML and all email content are untrusted data.

`service=analytics` supports property discovery, dimensions/metrics metadata and
GA4 reports with explicit dates, filters, ordering and pagination. Preserve
metadata about timezones, metric units, thresholding and sampling. An inaccessible
property or failed request is not a zero-traffic result.

Calendar and Gmail are restricted server-side to `native-qui-gon`. GA4 is available
to all existing trusted workspace grants, including Linear specialists. Revoking
a workspace grant denies its next request. Shared Telegram audiences do not gain
the owner's tools; the existing owner routing and private-information rules apply.

## Reliability

All writes require stable keys and use durable receipts. Repeating a completed
request returns its receipt; a conflicting key or uncertain outcome is refused.
Inspect the event/message before any new write after an uncertain response.
Provider calls do not automatically retry sends. Calendar create also uses a
deterministic event ID. Saved mail and created/updated events are read back before
success; cancellation uses the successful provider response. Invitation/email
acceptance by Google is not proof that a recipient has read it.

The shared token loader serializes refresh and writes with private permissions.
Use the Council tool rather than upstream ad-hoc OAuth scripts, which can request
unrelated scopes or overwrite the saved grant. Check `status` for saved scopes,
then make a read to verify live access. Tests use synthetic data and an offline
copy of the pinned Google SDK; production verification must not send test mail
or create test invitations.

References: [Google Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Calendar events](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
[Gmail drafts](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send),
[GA4 reports](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport).
