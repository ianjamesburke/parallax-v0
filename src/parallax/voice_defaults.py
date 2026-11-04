"""Shared narration defaults and compatibility resolution."""

from __future__ import annotations

DEFAULT_VOICE_MODEL = "tts-gemini"
DEFAULT_VOICE = "Charon"
_MODEL_DEFAULT_VOICE = {"tts-mini": "nova", "tts-gemini": DEFAULT_VOICE}

# Preserve plans authored before Gemini became the default when they name one
# of the established chat-audio voices without also naming its model.
TTS_MINI_VOICES = frozenset({
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse",
})


def default_voice_for(voice_model: str) -> str:
    """Return a compatible default voice for ``voice_model``."""
    return _MODEL_DEFAULT_VOICE.get(voice_model, DEFAULT_VOICE)


def resolve_voice_defaults(
    voice_model: str | None = None, voice: str | None = None,
) -> tuple[str, str]:
    """Resolve omitted fields, inferring mini only for known mini voices."""
    if voice_model is None:
        voice_model = "tts-mini" if voice in TTS_MINI_VOICES else DEFAULT_VOICE_MODEL
    return voice_model, voice or default_voice_for(voice_model)
