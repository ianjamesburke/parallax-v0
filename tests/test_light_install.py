"""Core CLI paths must not import optional alignment dependencies."""
import subprocess
import sys


def test_core_cli_without_alignment():
    code = '''
import sys
sys.modules['whisperx'] = None
sys.modules['torch'] = None
import parallax.cli
import parallax.cli._produce, parallax.cli._cut, parallax.cli._audio
import parallax.cut, parallax.stages, parallax.whisper_backend
from typer.testing import CliRunner
result = CliRunner().invoke(parallax.cli.app, ['guide', 'front_half'])
assert result.exit_code == 0, (result.output, result.exception)
try:
    parallax.whisper_backend.transcribe_wav('unused.wav')
except parallax.whisper_backend.AlignmentUnavailable as exc:
    assert "optional 'align' extra" in str(exc)
else:
    raise AssertionError('missing alignment dependency accepted')
'''
    subprocess.run([sys.executable, '-c', code], check=True)
