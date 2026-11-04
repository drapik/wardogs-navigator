"""Optional Yandex SpeechKit v3 synthesis with a persistent WAV cache.

The only network request in the application. It is made solely when an API key
is configured, goes only to tts.api.cloud.yandex.net and sends only the phrase
text. Every phrase is cached on disk and never synthesized twice.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import Request, urlopen
import wave

ENDPOINT = 'https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis'
SAMPLE_RATE = 48000
DEFAULT_VOICE = 'marina'
DEFAULT_ROLE = 'friendly'
# Voice families documented for SpeechKit v3 (ru-RU). There is no "alice" voice.
VOICES = {
    'marina': ('neutral', 'whisper', 'friendly'), 'dasha': ('neutral', 'good', 'friendly'),
    'jane': ('neutral', 'good', 'evil'), 'julia': ('neutral', 'strict'), 'lera': ('neutral', 'friendly'),
    'masha': ('good', 'strict', 'friendly'), 'omazh': ('neutral', 'evil'),
    'alexander': ('neutral', 'good'), 'kirill': ('neutral', 'strict', 'good'), 'anton': ('neutral', 'good'),
    'ermil': ('neutral', 'good'), 'zahar': ('neutral', 'good'), 'filipp': (),
}


def app_dir():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'WardogsNavigator'


def config_path():
    return app_dir() / 'speechkit.json'


def load_config():
    """Environment variables win over %LOCALAPPDATA%\\WardogsNavigator\\speechkit.json."""
    config = {}
    try:
        config = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    env = {'api_key': 'YANDEX_SPEECHKIT_API_KEY', 'folder_id': 'YANDEX_SPEECHKIT_FOLDER_ID',
           'voice': 'YANDEX_SPEECHKIT_VOICE', 'role': 'YANDEX_SPEECHKIT_ROLE'}
    for field, name in env.items():
        if os.environ.get(name):
            config[field] = os.environ[name]
    config.setdefault('voice', DEFAULT_VOICE)
    config.setdefault('role', DEFAULT_ROLE if DEFAULT_ROLE in VOICES.get(config['voice'], ()) else '')
    return config


def configured():
    return bool(load_config().get('api_key'))


class SpeechKit:
    def __init__(self, config=None, cache=None, opener=urlopen):
        self.config = load_config() if config is None else config
        self.cache = Path(cache) if cache else app_dir() / 'speechkit-cache'
        self.opener = opener

    def cache_file(self, text, speed):
        key = json.dumps([text.strip(), self.config.get('voice'), self.config.get('role'), round(speed, 2)], ensure_ascii=False)
        return self.cache / (hashlib.sha256(key.encode('utf-8')).hexdigest()[:32] + '.wav')

    def render(self, text, destination, speed=1.):
        """Write `text` as WAV to destination; returns True when served from cache."""
        cached = self.cache_file(text, speed)
        if cached.is_file():
            shutil.copyfile(cached, destination)
            return True
        if not self.config.get('api_key'):
            raise RuntimeError('Ключ Yandex SpeechKit не задан')
        hints = [{'voice': self.config['voice']}, {'speed': max(.1, min(3., speed))}]
        if self.config.get('role'):
            hints.append({'role': self.config['role']})
        body = {'text': text, 'hints': hints, 'loudnessNormalizationType': 'LUFS',
                'outputAudioSpec': {'rawAudio': {'audioEncoding': 'LINEAR16_PCM', 'sampleRateHertz': SAMPLE_RATE}}}
        headers = {'Authorization': 'Api-Key ' + self.config['api_key'], 'Content-Type': 'application/json'}
        if self.config.get('folder_id'):
            headers['x-folder-id'] = self.config['folder_id']
        request = Request(ENDPOINT, json.dumps(body, ensure_ascii=False).encode('utf-8'), headers, method='POST')
        pcm = bytearray()
        try:
            with self.opener(request, timeout=10) as response:
                for line in response:
                    if line.strip():
                        chunk = json.loads(line).get('result', {}).get('audioChunk', {}).get('data')
                        if chunk:
                            pcm += base64.b64decode(chunk)
        except OSError as exc:
            message = getattr(exc, 'read', lambda: b'')()[:200].decode('utf-8', 'replace')
            raise RuntimeError(f'SpeechKit: {exc} {message}'.strip()) from None
        if not pcm:
            raise RuntimeError('SpeechKit вернул пустой звук')
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.cache, suffix='.part', delete=False) as handle:
            temporary = Path(handle.name)
        with wave.open(str(temporary), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(SAMPLE_RATE)
            audio.writeframes(bytes(pcm))
        os.replace(temporary, cached)
        shutil.copyfile(cached, destination)
        return False
