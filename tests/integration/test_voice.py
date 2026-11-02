"""Real, offline Russian voice-note recognition through Hermes preprocessing."""

import os
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from tools.transcription_tools import _get_provider, transcribe_audio

COMMAND = (
    "/opt/voice/whisper-cli -m /opt/voice/models/small-q5_1.bin "
    "-f {input_path} -l {language} -t 1 -bs 1 -bo 1 -ng -nt -otxt "
    "-of {output_dir}/transcript --vad -vm /opt/voice/models/silero-v6.2.0.bin -vsd 500"
)


class VoiceTests(unittest.TestCase):
    def test_russian_telegram_codec_and_silence_without_network_or_cloud(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "voice note.ogg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(Path(__file__).parent / "fixtures/voice-ru.wav"),
                    "-c:a",
                    "libopus",
                    str(audio),
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
            silence = root / "silence.wav"
            with wave.open(str(silence), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0" * 32000)
            with (
                patch.dict(
                    os.environ,
                    {
                        "HERMES_LOCAL_STT_COMMAND": COMMAND,
                        "HERMES_LOCAL_STT_LANGUAGE": "ru",
                    },
                ),
                patch(
                    "tools.transcription_tools._load_stt_config",
                    return_value={"enabled": True, "provider": "local_command", "language": "ru"},
                ),
            ):
                self.assertEqual(_get_provider({"provider": "local_command"}), "local_command")
                result = transcribe_audio(str(audio), source="gateway")
                self.assertTrue(result.get("success"), result.get("error"))
                self.assertEqual(result.get("provider"), "local_command")
                text = result["transcript"].lower()
                for word in ("завтра", "встреча", "командой", "список", "вопросов"):
                    self.assertIn(word, text)
                result = transcribe_audio(str(silence), source="gateway")
                # Silence must never become an invented instruction.
                self.assertFalse(result.get("transcript", "").strip(), result)
