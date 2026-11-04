import base64
import io
import json
import wave

import pytest

from wardogs_audio import speechkit
from wardogs_audio.speechkit import SpeechKit


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_synthesizes_once_and_reuses_cache(tmp_path):
    calls = []
    pcm = b'\x01\x00' * 480

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        assert request.full_url == speechkit.ENDPOINT
        assert request.get_header('Authorization') == 'Api-Key secret'
        line = json.dumps({'result': {'audioChunk': {'data': base64.b64encode(pcm).decode()}}})
        return FakeResponse((line + '\n').encode())

    engine = SpeechKit({'api_key': 'secret', 'voice': 'marina', 'role': 'friendly'}, tmp_path / 'cache', opener)
    assert engine.render('Через 300 метров поверните налево', tmp_path / 'a.wav') is False
    assert engine.render('Через 300 метров поверните налево', tmp_path / 'b.wav') is True
    assert len(calls) == 1
    assert {'voice': 'marina'} in calls[0]['hints'] and {'role': 'friendly'} in calls[0]['hints']
    with wave.open(str(tmp_path / 'b.wav'), 'rb') as audio:
        assert (audio.getframerate(), audio.getnframes()) == (speechkit.SAMPLE_RATE, 480)


def test_without_key_raises(tmp_path):
    with pytest.raises(RuntimeError):
        SpeechKit({'voice': 'marina'}, tmp_path).render('Вы прибыли', tmp_path / 'x.wav')


def test_environment_overrides_config(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    (tmp_path / 'WardogsNavigator').mkdir()
    (tmp_path / 'WardogsNavigator' / 'speechkit.json').write_text('{"api_key": "file", "voice": "jane"}', encoding='utf-8')
    monkeypatch.setenv('YANDEX_SPEECHKIT_API_KEY', 'env')
    config = speechkit.load_config()
    assert config['api_key'] == 'env' and config['voice'] == 'jane' and config['role'] == ''
