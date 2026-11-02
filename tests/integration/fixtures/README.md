# Voice fixture

`voice-ru.wav` is synthetic Russian speech generated locally using macOS `say`,
voice Milena, 16 kHz mono PCM. It contains no recording of a real person or private data.

Text: «Завтра в десять утра встреча с командой. Подготовь список вопросов.»

The integration test converts it to Telegram's Ogg/Opus format and exercises the
real Hermes audio conversion and pinned local Whisper engine with networking off.
