"""
core/voice.py — Voz do engenheiro (TTS)
=======================================
Fala os recados do engenheiro de pista. Se nenhum sintetizador estiver
disponível, o painel de texto continua funcionando igual — a voz é sempre um
extra, nunca um requisito.

O que um spotter precisa que um `print()` falado não dá:

  * **Prioridade.** "Bandeira preta" não pode esperar o balanço da volta
    terminar. Um recado crítico fura a fila e CORTA a fala em andamento.
  * **Validade.** Aviso guardado por 15 s não serve mais: o piloto já passou
    da curva. O que envelhece na fila é descartado em vez de dito atrasado.
  * **Uma frase por vez.** A reprodução espera o áudio acabar; sem isso as
    frases se atropelam e nenhuma é entendida.
  * **Fallback de verdade.** Se o backend neural falhar — na inicialização ou
    no meio da sessão — o SAPI do Windows assume sem perder o recado.

Backends, em ordem de preferência (`backend="auto"`):

  1. **Kokoro** (`kokoro-onnx` + modelo baixado): voz neural. Opcional; sem os
     arquivos do modelo ele simplesmente não entra.
  2. **SAPI** (pywin32): as vozes OneCore do Windows 10/11, bem menos robóticas
     que as "Desktop" legadas.
"""

import hashlib
import os
import sys
import threading
import time
import wave
from typing import Callable, List, Optional

from core.paths import get_app_dir

# ---------------------------------------------------------------------------
# Prioridades
# ---------------------------------------------------------------------------

#: Corta o que estiver sendo falado e nunca é descartado por idade.
PRIORITY_CRITICAL = 0
#: O caso comum — balanço da volta, avisos de atenção.
PRIORITY_NORMAL = 1
#: Reforço positivo, contexto. É o primeiro a cair quando a fila enche.
PRIORITY_LOW = 2

#: Mais que isto na fila e o mais antigo da menor prioridade é descartado.
MAX_QUEUE = 3
#: Idade a partir da qual um recado não crítico perde a validade (s).
#: Uma frase do engenheiro leva ~3 a 7 s para ser dita, então o limite precisa
#: caber duas: o segundo recado de um balanço de volta não pode ser descartado
#: só por ter esperado o primeiro terminar.
MAX_AGE_S = 15.0

#: Velocidade da fala no SAPI (-10 lento .. +10 rápido). Um pouco acima do
#: normal: as vozes OneCore falam devagar e o piloto já está duas curvas à
#: frente quando a frase termina.
DEFAULT_RATE = 2

#: Quantos WAV sintetizados ficam guardados antes da faxina.
CACHE_MAX_FILES = 300

#: Categoria das vozes OneCore (Windows 10/11) — vozes de alta qualidade.
ONECORE_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
#: Desempate entre vozes do mesmo idioma.
PREFERRED_VOICES = ("maria", "francisca", "antonio", "daniel")
LANGUAGE_HINTS = ("portugu", "brazil", "brasil", "pt-br", "pt_br")

#: Modelos Kokoro aceitos, do mais novo para o mais antigo. Só o v1.0 tem
#: português; o v0.19 é só inglês e por isso não serve para o engenheiro.
KOKORO_MODELS = (
    ("kokoro-v1.0.onnx", "voices-v1.0.bin"),
    ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"),
)
#: Voz e idioma do Kokoro (`pf_dora` é a voz feminina pt-BR do pacote v1.0).
KOKORO_VOICE = os.environ.get("APEXVIEW_KOKORO_VOICE", "pf_dora")
KOKORO_LANG = os.environ.get("APEXVIEW_KOKORO_LANG", "pt-br")
KOKORO_SPEED = float(os.environ.get("APEXVIEW_KOKORO_SPEED", "1.05"))


# ---------------------------------------------------------------------------
# Fila de fala
# ---------------------------------------------------------------------------

class _Utterance:
    """Uma frase esperando a vez."""

    __slots__ = ("text", "priority", "seq", "created_at")

    def __init__(self, text: str, priority: int, seq: int, created_at: float):
        self.text = text
        self.priority = priority
        self.seq = seq
        self.created_at = created_at

    def is_stale(self, now: float) -> bool:
        """Crítico nunca vence; o resto perde a validade."""
        return (self.priority > PRIORITY_CRITICAL
                and (now - self.created_at) > MAX_AGE_S)


class _SpeechQueue:
    """
    Fila ordenada por prioridade, com descarte do que envelheceu.

    Só a thread de fala consome; qualquer thread pode enfileirar. `peek_priority`
    existe para a preempção: quem está falando consulta se chegou algo mais
    urgente e corta a frase no meio.
    """

    def __init__(self, maxsize: int = MAX_QUEUE):
        self.maxsize = maxsize
        self._items: List[_Utterance] = []
        self._cond = threading.Condition()
        self._closed = False
        self._seq = 0

    def put(self, text: str, priority: int, now: float):
        with self._cond:
            if self._closed:
                return
            if any(u.text == text for u in self._items):
                return                      # já está na fila: não duplica
            self._seq += 1
            self._items.append(_Utterance(text, priority, self._seq, now))
            self._items.sort(key=lambda u: (u.priority, u.seq))
            while len(self._items) > self.maxsize:
                self._items.pop(self._drop_index())
            self._cond.notify()

    def _drop_index(self) -> int:
        """
        Quem cai primeiro: o mais antigo da MENOR prioridade.

        Como a lista está ordenada por (prioridade, ordem de chegada), o
        primeiro item da pior prioridade é justamente o mais velho dela.
        """
        pior = self._items[-1].priority
        for i, u in enumerate(self._items):
            if u.priority == pior:
                return i
        return 0

    def get(self) -> Optional[_Utterance]:
        """Bloqueia até haver frase. Devolve None quando a fila é encerrada."""
        with self._cond:
            while not self._items and not self._closed:
                self._cond.wait()
            if not self._items:
                return None
            return self._items.pop(0)

    def peek_priority(self) -> Optional[int]:
        with self._cond:
            return self._items[0].priority if self._items else None

    def clear(self):
        with self._cond:
            self._items.clear()

    def close(self):
        with self._cond:
            self._closed = True
            self._items.clear()
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# Reprodução de WAV (backends neurais entregam áudio, não fala)
# ---------------------------------------------------------------------------

def _wav_duration_s(path: str) -> float:
    try:
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate() or 1
            return wf.getnframes() / float(rate)
    except Exception:
        return 0.0


def _play_wav(path: str, should_stop: Callable[[], bool]) -> bool:
    """
    Toca o arquivo e SÓ VOLTA quando o áudio acaba.

    A espera é o que impede duas frases de saírem juntas. `should_stop` é
    consultado durante a espera para um recado crítico poder cortar a fala.
    """
    if not path or not os.path.exists(path) or sys.platform != "win32":
        return False
    try:
        import winsound
    except ImportError:
        return False

    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        print(f"[Voice] Falha ao tocar áudio: {e}")
        return False

    # +0.1 s de folga: a duração do arquivo é o mínimo, não o exato.
    fim = time.monotonic() + _wav_duration_s(path) + 0.1
    while time.monotonic() < fim:
        if should_stop():
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            break
        time.sleep(0.05)
    return True


# ---------------------------------------------------------------------------
# Cache de áudio
# ---------------------------------------------------------------------------

class AudioCacheManager:
    """
    Guarda o WAV das frases já sintetizadas.

    A chave inclui a voz e a velocidade: trocar de voz não pode fazer o app
    tocar o áudio antigo, gravado com a voz anterior. A pasta é podada para
    não crescer sem fim — frase com número é quase sempre única.
    """

    def __init__(self, cache_dir: Optional[str] = None, namespace: str = ""):
        self.cache_dir = cache_dir or get_app_dir(
            os.path.join("telemetry_data", "audio_cache"))
        self.namespace = namespace
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_filename(self, text: str) -> str:
        chave = f"{self.namespace}|{text.strip().lower()}"
        digest = hashlib.md5(chave.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"voice_{digest[:16]}.wav")

    def get_cached_path(self, text: str) -> Optional[str]:
        path = self._get_filename(text)
        return path if os.path.exists(path) else None

    def save_wav(self, text: str, wav_bytes: bytes) -> str:
        """Grava em arquivo temporário e só então renomeia: nunca toca meio WAV."""
        path = self._get_filename(text)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(wav_bytes)
            os.replace(tmp, path)
            return path
        except OSError as e:
            print(f"[AudioCache] Erro ao salvar cache de áudio: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return ""

    def prune(self, keep: int = CACHE_MAX_FILES):
        """Mantém os `keep` arquivos mais recentes."""
        try:
            arquivos = [os.path.join(self.cache_dir, f)
                        for f in os.listdir(self.cache_dir)
                        if f.startswith("voice_") and f.endswith(".wav")]
            if len(arquivos) <= keep:
                return
            arquivos.sort(key=os.path.getmtime, reverse=True)
            for path in arquivos[keep:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    def play_wav(self, path: str, should_stop: Callable[[], bool] = None) -> bool:
        return _play_wav(path, should_stop or (lambda: False))


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class KokoroBackend:
    """
    Síntese neural via `kokoro-onnx`.

    Totalmente opcional: sem o pacote ou sem os arquivos do modelo em
    `telemetry_data/models/`, o backend se declara indisponível e o SAPI assume.
    A inicialização faz uma síntese de teste — é a única forma de saber se a voz
    e o idioma pedidos existem NESTE modelo, e é melhor descobrir isso agora do
    que ficar mudo no meio de uma volta.
    """

    name = "Kokoro"

    def __init__(self, voice: str = KOKORO_VOICE, lang: str = KOKORO_LANG,
                 speed: float = KOKORO_SPEED):
        self.voice = voice
        self.lang = lang
        self.speed = speed
        self.available = False
        self.description = ""
        self._engine = None
        self._cache = None

    # -- ciclo de vida ---------------------------------------------------

    def start(self) -> bool:
        try:
            import kokoro_onnx
        except ImportError:
            return False

        model_path, voices_path = self._find_model()
        if not model_path:
            return False

        try:
            self._engine = kokoro_onnx.Kokoro(model_path, voices_path)
            self.voice = self._resolve_voice()
            self._synth("teste")            # prova de que a voz/idioma existem
        except Exception as e:
            print(f"[Voice/Kokoro] Indisponível ({type(e).__name__}: {e})")
            self._engine = None
            return False

        self._cache = AudioCacheManager(
            namespace=f"kokoro|{self.voice}|{self.lang}|{self.speed:.2f}")
        self._cache.prune()
        self.available = True
        self.description = f"Kokoro neural ({self.voice})"
        return True

    def shutdown(self):
        self._engine = None
        self.available = False

    def _find_model(self):
        models_dir = get_app_dir(os.path.join("telemetry_data", "models"))
        for modelo, vozes in KOKORO_MODELS:
            mp = os.path.join(models_dir, modelo)
            vp = os.path.join(models_dir, vozes)
            if os.path.exists(mp) and os.path.exists(vp):
                return mp, vp
        return None, None

    def _resolve_voice(self) -> str:
        """Se a voz pedida não existir no modelo, usa a primeira em português."""
        try:
            vozes = list(self._engine.get_voices())
        except Exception:
            return self.voice
        if not vozes or self.voice in vozes:
            return self.voice
        # No Kokoro o prefixo "p" identifica as vozes em português.
        pt = [v for v in vozes if v.startswith("p")]
        escolhida = (pt or vozes)[0]
        print(f"[Voice/Kokoro] Voz '{self.voice}' não existe no modelo; "
              f"usando '{escolhida}'")
        return escolhida

    # -- síntese ---------------------------------------------------------

    def _synth(self, text: str) -> bytes:
        """Sintetiza e devolve os bytes de um WAV mono 16 bits."""
        import io

        import numpy as np

        samples, sample_rate = self._engine.create(
            text, voice=self.voice, speed=self.speed, lang=self.lang)
        pcm = (np.clip(np.asarray(samples), -1.0, 1.0) * 32767).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm.tobytes())
        return buffer.getvalue()

    def speak(self, text: str, should_stop: Callable[[], bool]) -> bool:
        if not self.available or self._engine is None:
            return False
        try:
            path = self._cache.get_cached_path(text)
            if path is None:
                path = self._cache.save_wav(text, self._synth(text))
            if not path:
                return False
            return _play_wav(path, should_stop)
        except Exception as e:
            # Falhou no meio da sessão: sai de cena para o SAPI assumir.
            print(f"[Voice/Kokoro] Erro ao sintetizar ({type(e).__name__}: {e}); "
                  "voltando para o SAPI")
            self.available = False
            return False


class SapiBackend:
    """
    SAPI do Windows via pywin32 — a voz que existe em qualquer máquina.

    O objeto COM é criado e usado SEMPRE na thread de fala: SAPI é apartment
    threaded e usá-lo de duas threads trava o processo.
    """

    name = "SAPI"

    #: Flags do SAPI (`ISpVoice::Speak`).
    _ASYNC = 1
    _PURGE = 2

    def __init__(self, rate: int = DEFAULT_RATE, volume: int = 100):
        self.rate = rate
        self.volume = volume
        self.available = False
        self.description = ""
        self._voice = None
        self._com = None

    # -- ciclo de vida ---------------------------------------------------

    def start(self) -> bool:
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            return False

        try:
            pythoncom.CoInitialize()
            self._com = pythoncom
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._voice.Rate = self.rate        # -10 (lento) a +10 (rápido)
            self._voice.Volume = max(0, min(100, self.volume))
            token = self._best_token(win32com, self._voice)
            if token is not None:
                try:
                    self._voice.Voice = token
                    self.description = token.GetDescription()
                except Exception:
                    self.description = "voz padrão"
            self.available = True
            return True
        except Exception as e:
            print(f"[Voice/SAPI] Indisponível ({type(e).__name__}: {e})")
            self._voice = None
            self.available = False
            return False

    def shutdown(self):
        self.available = False
        self._voice = None
        if self._com is not None:
            try:
                self._com.CoUninitialize()
            except Exception:
                pass
            self._com = None

    # -- escolha da voz --------------------------------------------------

    def _all_tokens(self, win32com, voice) -> list:
        tokens = []
        try:
            cat = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
            cat.SetId(ONECORE_CATEGORY, False)
            enum = cat.EnumerateTokens()
            tokens += [enum.Item(i) for i in range(enum.Count)]
        except Exception:
            pass                                # sem OneCore: só as clássicas
        try:
            classic = voice.GetVoices()
            tokens += [classic.Item(i) for i in range(classic.Count)]
        except Exception:
            pass
        return tokens

    def _best_token(self, win32com, voice):
        melhor, melhor_nota = None, -10 ** 6
        for token in self._all_tokens(win32com, voice):
            try:
                nota = VoiceEngine.voice_score(token.GetDescription())
            except Exception:
                continue
            if nota > melhor_nota:
                melhor, melhor_nota = token, nota
        return melhor

    # -- fala ------------------------------------------------------------

    def speak(self, text: str, should_stop: Callable[[], bool]) -> bool:
        if not self.available or self._voice is None:
            return False
        try:
            # Assíncrono + espera em fatias: é o que permite cortar a frase
            # quando chega um recado crítico.
            self._voice.Speak(text, self._ASYNC)
            while not self._voice.WaitUntilDone(50):
                if should_stop():
                    self._voice.Speak("", self._PURGE)
                    break
            return True
        except Exception as e:
            print(f"[Voice/SAPI] Falha ao falar ({type(e).__name__}: {e})")
            try:                                # última tentativa, síncrona
                self._voice.Speak(text)
                return True
            except Exception:
                self.available = False
                return False


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

class VoiceEngine:
    """
    Fila de fala com thread dedicada. `say()` volta na hora — nada aqui bloqueia
    a interface, nem quando o sintetizador demora.
    """

    def __init__(self, enabled: bool = True, rate: int = DEFAULT_RATE,
                 backend: str = "auto", volume: int = 100):
        self.enabled = enabled
        self.rate = rate
        self.volume = volume
        self.backend_option = backend
        self.available = False
        self.voice_name = ""

        self._queue = _SpeechQueue()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._backends: List[object] = []
        self._thread = threading.Thread(target=self._run, name="VoiceEngine",
                                        daemon=True)
        self._thread.start()

    # -- API pública ------------------------------------------------------

    def say(self, text: str, priority: int = PRIORITY_NORMAL):
        """Enfileira uma fala. Volta imediatamente."""
        text = (text or "").strip()
        if not text or not self.enabled or self._stopping.is_set():
            return
        self._queue.put(text, priority, time.monotonic())

    def clear(self):
        """Esvazia o que ainda não foi falado (não corta a frase em curso)."""
        self._queue.clear()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """
        Espera a thread terminar de escolher o backend.

        Existe porque `available` e `voice_name` só ficam corretos depois disso —
        quem quer MOSTRAR o estado (uma tela de status) precisa esperar; quem só
        quer falar, não.
        """
        return self._ready.wait(timeout)

    def stop(self):
        """Encerra a thread de fala e espera ela sair."""
        self._stopping.set()
        self._queue.close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # -- pontuação das vozes ---------------------------------------------

    @staticmethod
    def voice_score(description: str) -> int:
        """
        Nota de uma voz do Windows pela descrição.

        O idioma pesa mais que tudo: voz em inglês lendo português fica
        incompreensível. Depois vem a geração — as vozes OneCore do Windows
        10/11 são muito melhores que as "Desktop", que são de 2010.
        """
        d = (description or "").lower()
        score = 0
        if any(hint in d for hint in LANGUAGE_HINTS):
            score += 200
        score += -100 if "desktop" in d else 80
        for i, name in enumerate(PREFERRED_VOICES):
            if name in d:
                score += max(40 - i * 8, 5)     # maria=40, francisca=32, ...
                break
        return score

    # -- thread de fala ---------------------------------------------------

    def _make_backends(self) -> List[object]:
        """Backends na ordem de preferência, já filtrados pela opção escolhida."""
        candidatos = []
        if self.backend_option in ("auto", "kokoro"):
            candidatos.append(KokoroBackend())
        if self.backend_option in ("auto", "sapi"):
            candidatos.append(SapiBackend(rate=self.rate, volume=self.volume))
        return candidatos

    def _start_backends(self):
        for backend in self._make_backends():
            try:
                ok = backend.start()
            except Exception as e:
                print(f"[Voice] {backend.name} falhou ao iniciar "
                      f"({type(e).__name__}: {e})")
                ok = False
            if ok:
                self._backends.append(backend)

        self.available = bool(self._backends)
        if self.available:
            self.voice_name = self._backends[0].description or self._backends[0].name
            print(f"[Voice] Engenheiro com voz: {self.voice_name}")
        else:
            print("[Voice] Nenhum sintetizador disponível; "
                  "o painel de texto continua normalmente")

    def _run(self):
        try:
            self._start_backends()
        finally:
            self._ready.set()

        try:
            while not self._stopping.is_set():
                fala = self._queue.get()
                if fala is None:
                    break
                if not self.enabled or not self.available:
                    continue
                if fala.is_stale(time.monotonic()):
                    continue                    # perdeu a validade na fila
                self._speak(fala)
        finally:
            for backend in self._backends:
                try:
                    backend.shutdown()
                except Exception:
                    pass

    def _speak(self, fala: _Utterance):
        """
        Fala usando o primeiro backend que topar.

        `interrompe` só é verdadeiro para quem NÃO é crítico: um recado crítico
        nunca é cortado por outro, senão dois avisos urgentes se anulariam. O
        fechamento do app corta qualquer fala — ninguém quer esperar a frase
        terminar para a janela sumir.
        """
        def interrompe() -> bool:
            if self._stopping.is_set():
                return True
            if fala.priority <= PRIORITY_CRITICAL:
                return False
            return self._queue.peek_priority() == PRIORITY_CRITICAL

        for backend in self._backends:
            if not getattr(backend, "available", False):
                continue
            try:
                if backend.speak(fala.text, interrompe):
                    return
            except Exception as e:
                print(f"[Voice] {backend.name} falhou ({type(e).__name__}: {e})")

        # Todos caíram: para de tentar falar, o painel de texto segue.
        if not any(getattr(b, "available", False) for b in self._backends):
            self.available = False
            print("[Voice] Sem backend de voz utilizável; a fala foi desligada")
