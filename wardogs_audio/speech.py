"""Asynchronous speech: Yandex SpeechKit (cached), isolated SAPI, finite local WAV pack."""
from pathlib import Path
import wave
from PySide6.QtCore import QObject, QThread, Signal
from .bridge import WindowsBridge
from .pack import DefaultVoicePack
from .speechkit import SpeechKit, configured as speechkit_configured


class SpeechJob(QThread):
    ready = Signal(str, str)
    failed = Signal(str)
    system_failed = Signal(str)

    def __init__(self, bridge, pack, text, language, voice, rate, path, local, backend='auto', speechkit=None):
        super().__init__()
        self.bridge, self.pack, self.local, self.backend = bridge, pack, local, backend
        self.speechkit = speechkit
        self.text, self.language, self.voice, self.rate, self.path = text, language, voice, rate, str(path)

    def _speechkit(self):
        engine = self.speechkit or SpeechKit()
        engine.render(self.text, self.path, speed=2 ** max(-1., min(1., self.rate / 10)))
        return 'speechkit'

    def _system(self):
        self.bridge.call('speak', text=self.text, language=self.language,
                         voice=self.voice, rate=self.rate, path=self.path, timeout=12.)
        with wave.open(self.path, 'rb') as audio:
            if audio.getnframes() == 0:
                raise ValueError('Системный голос вернул пустой звук')
        return 'system'

    def run(self):
        try:
            if self.local:
                self.pack.render(self.text, self.language, self.path)
                provider = 'local'
            elif self.backend == 'speechkit' or (self.backend == 'auto' and (self.speechkit or speechkit_configured())):
                try:
                    provider = self._speechkit()
                except Exception as exc:
                    if self.backend == 'speechkit' or self.isInterruptionRequested():
                        raise
                    self.system_failed.emit('SpeechKit недоступен, говорит системный голос: ' + str(exc))
                    provider = self._system()
            else:
                try:
                    provider = self._system()
                except Exception as exc:
                    if self.isInterruptionRequested():
                        return
                    if self.language not in self.pack.languages():
                        raise
                    self.system_failed.emit('Системный голос не сработал, включён встроенный пакет: ' + str(exc))
                    self.pack.render(self.text, self.language, self.path)
                    provider = 'local'
            if not self.isInterruptionRequested():
                self.ready.emit(self.path, provider)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class VoiceJob(QThread):
    ready = Signal(list)
    failed = Signal(str)

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge

    def run(self):
        try:
            result = self.bridge.call('voices')
            if not self.isInterruptionRequested():
                self.ready.emit(result['voices'])
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
        finally:
            self.bridge.close()


class SpeechService(QObject):
    ready = Signal(str, int, str)
    failed = Signal(str, int)
    status = Signal(str)
    voices_ready = Signal(list)
    idle = Signal()

    def __init__(self, parent=None, *, bridge_factory=WindowsBridge, pack=None, speechkit=None):
        super().__init__(parent)
        self.speechkit = speechkit
        self.bridge_factory = bridge_factory
        self.pack = pack or DefaultVoicePack()
        self.bridge = None
        self.job = None
        self.voice_job = None
        self.unavailable = {}
        self.revision = 0
        self.voice_revision = 0
        self.closed = False
        self._jobs = set()

    @property
    def busy(self):
        return self.job is not None and self.job.isRunning()

    def _retain(self, job):
        self._jobs.add(job)
        job.finished.connect(lambda j=job: self._finished(j))
        job.start()

    def _finished(self, job):
        self._jobs.discard(job)
        if self.job is job:
            self.job = None
        if self.voice_job is job:
            self.voice_job = None
        if isinstance(job, SpeechJob) and job.isInterruptionRequested():
            Path(job.path).unlink(missing_ok=True)
        job.deleteLater()
        if not self.closed:
            self.idle.emit()

    def load_voices(self):
        if self.closed or (self.voice_job is not None and self.voice_job.isRunning()):
            return
        self.voice_job = VoiceJob(self.bridge_factory())
        revision = self.voice_revision
        self.voice_job.ready.connect(lambda voices: self.voices_ready.emit(voices) if revision == self.voice_revision else None)
        self.voice_job.failed.connect(lambda message: self._voices_failed(message) if revision == self.voice_revision else None)
        self._retain(self.voice_job)

    def _voices_failed(self, message):
        self.voices_ready.emit([])
        self.status.emit('Не удалось получить список системных голосов: ' + message)

    def synthesize(self, text, language, voice, rate, path, generation, backend='auto'):
        if self.closed or self.busy:
            return False
        key = (language, voice)
        local = backend == 'local' or (backend == 'system' and key in self.unavailable and language in self.pack.languages())
        if not local and backend != 'speechkit' and (self.bridge is None or self.bridge.closed):
            self.bridge = self.bridge_factory()
        self.job = SpeechJob(self.bridge, self.pack, text, language, voice, rate, path, local, backend, self.speechkit)
        revision = self.revision
        self.job.ready.connect(lambda p, provider: self.ready.emit(p, generation, provider) if revision == self.revision else None)
        self.job.failed.connect(lambda message: self.failed.emit(message, generation) if revision == self.revision else None)
        self.job.system_failed.connect(lambda message: self._system_failed(key, message, revision))
        self._retain(self.job)
        return True

    def _system_failed(self, key, message, revision):
        if revision == self.revision:
            self.unavailable[key] = message
            self.status.emit('' + message)

    def retry_system(self):
        self.cancel()
        self.cancel_voices()
        self.unavailable.clear()

    def cancel_voices(self):
        self.voice_revision += 1
        if self.voice_job is not None:
            self.voice_job.requestInterruption()
            self.voice_job.bridge.close()

    def cancel(self):
        self.revision += 1
        if self.job is not None:
            self.job.requestInterruption()
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None

    def close(self):
        self.closed = True
        self.cancel()
        self.cancel_voices()
        for job in list(self._jobs):
            job.requestInterruption()
            if getattr(job, 'bridge', None) is not None:
                job.bridge.close()
        for job in list(self._jobs):
            job.wait(5000)
