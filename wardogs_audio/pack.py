"""Resolve the finite built-in vocabulary and assemble PCM without any TTS engine."""
import json
from pathlib import Path
import re
import wave


def number_words(value, language):
    negative = value.startswith('-')
    integer, _, decimal = value.lstrip('+-').partition('.')
    n = int(integer)
    if n > 99999999:
        raise ValueError('Число слишком большое для встроенного голосового пакета')
    if language == 'zh-CN':
        digits = '零一二三四五六七八九'
        def say(n, leading=True):
            if n < 10:
                return digits[n]
            for base, unit in [(10000, '万'), (1000, '千'), (100, '百'), (10, '十')]:
                if n >= base:
                    head, tail = divmod(n, base)
                    prefix = '' if base == 10 and head == 1 and leading else say(head)
                    return prefix + unit + (('零' if tail < base // 10 else '') + say(tail, False) if tail else '')
        text = say(n)
        return ('负' if negative else '') + text + ('点' + ''.join(digits[int(d)] for d in decimal) if decimal else '')
    small = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
    tens = 'zero ten twenty thirty forty fifty sixty seventy eighty ninety'.split()
    def say(n):
        if n < 20:
            return small[n]
        if n < 100:
            return tens[n // 10] + (' ' + small[n % 10] if n % 10 else '')
        for base, unit in [(1000000, 'million'), (1000, 'thousand'), (100, 'hundred')]:
            if n >= base:
                head, tail = divmod(n, base)
                return say(head) + ' ' + unit + (' ' + say(tail) if tail else '')
    return ('minus ' if negative else '') + say(n) + (' point ' + ' '.join(small[int(d)] for d in decimal) if decimal else '')


def normalize(text):
    return re.sub(r'[\s，。！？、：；,.!?:;]+', '', text).casefold()


class DefaultVoicePack:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(__file__).with_name('assets') / 'default'
        self._entries = None

    def languages(self):
        try:
            return set(json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))['entries'])
        except (OSError, ValueError, KeyError):
            return set()

    def segments(self, text, language='zh-CN'):
        if self._entries is None:
            manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
            self._entries = {lang: sorted(((normalize(t), name) for t, name in entries.items()),
                            key=lambda item: len(item[0]), reverse=True) for lang, entries in manifest['entries'].items()}
        if language not in self._entries:
            raise ValueError('Встроенный голосовой пакет не поддерживает язык: ' + language)
        text = re.sub(r'[+-]?\d+(?:\.\d+)?', lambda m: number_words(m[0], language), text)
        rest = normalize(text)
        files = []
        while rest:
            for phrase, filename in self._entries[language]:
                if rest.startswith(phrase):
                    path = self.root / filename
                    if path.parent != self.root or not path.is_file():
                        raise ValueError('Нет файла встроенного голосового пакета: ' + filename)
                    files.append(path)
                    rest = rest[len(phrase):]
                    break
            else:
                raise ValueError('Встроенный пакет не умеет произносить этот текст, нужен SpeechKit или системный голос: ' + rest[:24])
            if len(files) > 512:
                raise ValueError('Слишком длинная фраза')
        if not files:
            raise ValueError('Пустая фраза')
        return files

    def render(self, text, language, destination):
        chunks = []
        format_ = None
        for path in self.segments(text, language):
            with wave.open(str(path), 'rb') as audio:
                current = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
                if current != (1, 2, 24000) or audio.getnframes() < 1:
                    raise ValueError('Неверный формат звука в пакете: ' + path.name)
                if format_ is None:
                    format_ = current
                chunks.append(audio.readframes(audio.getnframes()))
        with wave.open(str(destination), 'wb') as audio:
            audio.setnchannels(format_[0]); audio.setsampwidth(format_[1]); audio.setframerate(format_[2])
            audio.writeframes(b''.join(chunks))
        return str(destination)
