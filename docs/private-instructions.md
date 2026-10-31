# Private instructions and skills

The public `instructions/` directory contains example/default roles for a new
installation. Personal instructions can instead come from a separate private
Git repository. It is fetched only by the host operator, never during an image
build or by an agent. Public CI uses fixture data and needs no private credentials.

## Layout

```text
private-skills/
├── assistant/
│   ├── SOUL.md
│   ├── AGENTS.md          # optional runtime instructions
│   └── skills/<name>/SKILL.md
├── dexter/
│   ├── SOUL.md
│   └── skills/            # optional profile-specific skills
└── secretary/
    ├── POLICY.md
    └── skills/{spouse,contacts,new-users}/SKILL.md
```

Isolated Linear roles use `linear/{qui-gon,dexter,marketing}/SOUL.md` and
`linear/<slug>/skills/<name>/SKILL.md`. Prepare each executor first and declare
`agent-specialists-v1` in `requires.json`. These files map only into that
executor's own profile; they grant no credentials or additional tools.

To keep identical research procedures in one source, a role may contain
`linear/dexter/skills-source.json` with `{"role":"dexter"}`. The installer copies
only that named role's `skills/` into the isolated executor. Its separate Linear
`SOUL.md` remains in charge of the execution workflow; config, memory and grants
are never shared. Conflicting duplicate destinations are rejected. Export maps
shared skills back to their canonical source and refuses diverged runtime copies
until reconciled.

Skill folders can contain nested categories, scripts and reference files.
Artifacts must be regular UTF-8 files, with no symlinks or hidden resources,
at most 1 MiB per file and 8 MiB/1,024 files per bundle. Common credential
patterns are rejected as a guardrail, not a complete secret-detection guarantee.
Keep credentials, configuration, databases, conversations and memory outside Git.

When a skill needs a newly released application capability, add an optional
root `requires.json`, for example:

```json
{"schema":1,"capabilities":["research-filing-v1"]}
```

The installer checks the running image's advertised capabilities before downtime
and records them with the installed revision. Copied-profile verification also
rejects incompatible images during upgrade/restore. A combined `deploy
--instructions-source PATH --instructions-revision FULL_SHA` checks the candidate
image and activates code plus matching rules in one rollback-protected cutover.
This manifest describes required code, never credentials or extra agent grants.
Keep third-party notices for modified upstream skills. Unchanged upstream skills
remain supplied by the pinned Hermes release.

The `assistant` directory maps to the default profile. Other named roles map to
existing `profiles/<name>/`; provision those profiles first. Prepare the secretary
before installing its four files. No instruction file changes execution permissions.

## Host setup and updates

Clone the private repository into a dedicated directory such as
`/var/lib/agent-council/skills-source`, owned by root with mode 0700. It must be
outside the public checkout, runtime data, workspace and backups. Give the host
a dedicated **read-only deploy key** for that repository and verify GitHub's host
key before cloning. Keep the key root-only, outside all container mounts. Never
add it to GitHub Actions or an agent profile.

Review and copy a full commit SHA, then run on the Docker host:

```bash
cd /opt/agent-council
sudo python3 scripts/manage.py apply-instructions \
  /var/lib/agent-council/skills-source --fetch --revision FULL_COMMIT_SHA
```

The shared maintenance lock covers fetch, checkout, idle checks, a consistent
checkpoint and installation. Git hooks are disabled for these operations. Dirty
or ignored working-copy files are rejected. New files adopt byte-identical runtime
copies; conflicting unmanaged files and unsaved managed edits stop installation.
Matching local edits already reviewed in the selected commit can be adopted.
For an existing unmanaged default that the reviewed candidate intentionally
replaces, pass `--adopt RELATIVE_RUNTIME_PATH=EXPECTED_CURRENT_SHA256` for each
file. Review those current bytes first. A missing/mismatched file or already
managed path is rejected; this cannot bypass managed-file drift. Adoption is
validated again on the copied profile and included in the normal rollback.

The installer applies the plan to a copied profile and verifies it offline in
the recorded image before changing live files. It installs only named artifacts,
records checksums/revision in `/opt/data/.council/instructions.json` and restarts
the owned services. Failure restores the prior profile, auth and Compose state.
An identical installed revision is a no-op. Removing tracked files removes only
those managed artifacts, preserving unrelated local files.

## Automatic updates

After the first reviewed installation, the private repository's `main` branch
can be the approved update channel. Only trusted maintainers should write there.
The host-only timer checks it every five minutes and uses the same guarded apply
path. Busy agents, dirty source checkouts and conflicting local edits stop that
attempt; the timer retries later. Documentation-only commits update the recorded
revision without restarting services. No code image is rebuilt.
Failed copied-profile validation or cutover quarantines that commit instead of
repeatedly stopping the service. A corrected `main` commit is eligible again;
an explicit successful `apply-instructions` also clears the failure record.
`doctor` reports the rejected revision and automation status.

```bash
sudo install -m 0644 config/agent-council-skills.service config/agent-council-skills.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/agent-council-skills.service /etc/systemd/system/agent-council-skills.timer
sudo systemctl daemon-reload
sudo systemctl enable --now agent-council-skills.timer
systemctl list-timers agent-council-skills.timer
```

Review the unit's checkout paths for a different host. Inspect failures with
`systemctl status agent-council-skills.service`; diagnostics omit private text
and keys. To freeze instruction releases, disable the timer. Local generated
skills are still exported and reviewed before publication to `main`.
Manual `restore` disables the instruction timer so automatic updates cannot
immediately undo the rollback. Re-enable it after reviewing the intended release.

The secretary receives only its own bundle at `/opt/data/instructions` inside
its separate profile mount. Its root-only environment selects
`COUNCIL_SECRETARY_INSTRUCTIONS=profile`; missing/invalid rules then prevent
startup, with no silent fallback. Explicitly removing that role through an apply
returns the selector to the public image defaults. The Git checkout, other roles
and GitHub key never enter the secretary container.

Ordinary image deployment does not fetch or overwrite private instructions.
The researcher provisioner remains a profile-initialization command; it refuses
to overwrite instructions changed through the private workflow.

## Export runtime edits

For a skill edited/created by Hermes, start with a clean private checkout at
the currently installed SHA:

```bash
sudo python3 scripts/manage.py export-instructions \
  /var/lib/agent-council/skills-source --revision INSTALLED_COMMIT_SHA
```

Use `--include-skill path/to/skill` to include a new default-profile skill, relative
to `/opt/data/skills/`. Export stages changes only in the private working copy;
it neither stops the service nor commits/pushes. Review the diff and resources,
then commit using an authorized writer account. The host deploy key cannot push.
Apply that reviewed commit to reconcile the runtime and recorded checksums.

Applied artifacts and their marker are included in profile backups. Recovery
does not need GitHub access to start those saved instructions. The private source
checkout and deploy key are not agent state; recreate host access separately.
See [backup recovery](backups.md) and the [instruction index](../instructions/README.md).
