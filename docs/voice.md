# Local voice recognition

The native Hermes gateway recognizes incoming Telegram voice notes locally.
No speech API key, external speech service or new listener is needed. Recognized
text then enters the existing assistant conversation and its normal model/tools.
The main service's existing CPU and memory limits still apply; TravelLine is unchanged.

The image includes the upstream `whisper.cpp` 1.9.5 CPU CLI, multilingual Whisper
Small Q5_1 weights and Silero VAD 6.2.0. Source archives and model files are pinned
by SHA-256; models also use immutable Hugging Face revisions. Only runtime artifacts
are copied from the build stage. No package installation or model downloads happen
when a voice note arrives.

Compose sets `HERMES_LOCAL_STT_COMMAND` for the native gateway only and defaults
`HERMES_LOCAL_STT_LANGUAGE` to `ru`. Hermes' stock `local_command` adapter handles
audio conversion, path quoting, temporary transcript cleanup and a 300-second
process timeout. Whisper uses one CPU thread and greedy decoding; VAD drops silence.
Each invocation exits and releases its model memory. The existing Telegram media
cache/retention policy remains in force; this does not ingest voice from monitored
research channels.

A profile with an explicit cloud/disabled STT selection keeps that choice. For
this deployment leave STT autodetection enabled or select `local_command`. To turn
off transcription set `stt.enabled: false` in Hermes. Recognition quality depends
on recording quality; proper names and mixed languages can need correction.

Validation: `make test-integration` exercises real Russian speech converted to
Ogg/Opus plus silence, through Hermes with networking disabled. Test audio is
synthetic; see `tests/integration/fixtures/README.md`.
