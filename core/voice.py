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

#: Velocidade da fala no SAPI (-10 lento .. +10 rápido).
#:
#: Acima do natural. Uma voz sintética em ritmo neutro lendo uma frase de dez
#: palavras não soa calma: soa arrastada, porque falta a entonação que num
#: humano quebraria a monotonia. Acelerar devolve parte desse ritmo — e o
#: recado do engenheiro é curto de propósito, então a pressa não come sílaba.
#:
#: O que NÃO se resolve aqui é frase longa: mesmo em +3, vinte palavras levam
#: cinco segundos. Recado que não cabe em ~12 palavras se conserta encurtando
#: o texto, não apressando a voz.
DEFAULT_RATE = 3

#: Tom da voz no SAPI (-10 a +10, 0 = natural).
#:
#: Bem grave, e isto é uma escolha de gosto com um preço conhecido: deslocar o
#: tom não muda como a voz é sintetizada — o Windows reamostra a fala já
#: pronta, e quanto maior o deslocamento mais artefato ela carrega. O valor
#: aqui foi escolhido de ouvido em `ajustar_voz.pyw`, comparando 0, -2 e -4 na
#: mesma frase: o timbre de rádio de equipe compensou o custo.
#:
#: Quem preferir a voz mais limpa põe `voice_pitch: 0` no config.json — e quem
#: quiser grave SEM artefato precisa de outra voz, não de outro número: a
#: neural (Kokoro) sintetiza no timbre em vez de deslocar depois.
DEFAULT_PITCH = -5

#: Quantos WAV sintetizados ficam guardados antes da faxina.
CACHE_MAX_FILES = 300

#: Categoria das vozes OneCore (Windows 10/11) — vozes de alta qualidade.
ONECORE_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
#: Desempate entre vozes do mesmo idioma, da preferida para a menos.
#: Masculinas primeiro: é o timbre pedido, e a Daniel (OneCore pt-BR) é
#: também a voz mais recente que o Windows instala em português — a Maria
#: clássica é de 2010 e soa bem mais sintética.
PREFERRED_VOICES = ("daniel", "antonio", "raul", "helio",
                    "francisca", "maria", "heloisa")
LANGUAGE_HINTS = ("portugu", "brazil", "brasil", "pt-br", "pt_br")

#: Nomes que identificam voz masculina nos pacotes de português do Windows.
#: Serve para escolher por GÊNERO quando a máquina tem um conjunto de vozes
#: diferente do previsto — o PC de jogo pode ser outro Windows, com outro
#: pacote de idioma instalado, e a lista acima não cobriria.
MALE_VOICE_NAMES = ("daniel", "antonio", "antónio", "raul", "helio", "hélio",
                    "julio", "júlio", "ricardo", "felipe", "fabio", "fábio")

#: Modelos Kokoro aceitos, do mais novo para o mais antigo. Só o v1.0 tem
#: português; o v0.19 é só inglês e por isso não serve para o engenheiro.
KOKORO_MODELS = (
    ("kokoro-v1.0.onnx", "voices-v1.0.bin"),
    ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"),
)
#: Voz e idioma do Kokoro. `pm_alex` é a voz MASCULINA pt-BR do pacote v1.0
#: (as femininas são `pf_dora`; `pm_santa` é o outro timbre masculino).
KOKORO_VOICE = os.environ.get("APEXVIEW_KOKORO_VOICE", "pm_alex")
KOKORO_LANG = os.environ.get("APEXVIEW_KOKORO_LANG", "pt-br")
#: 1.0 = ritmo natural do modelo. Acelerar uma voz neural desfaz justamente
#: a prosódia que a torna menos robótica.
KOKORO_SPEED = float(os.environ.get("APEXVIEW_KOKORO_SPEED", "1.0"))


# ---------------------------------------------------------------------------
# Fila de fala
# ---------------------------------------------------------------------------

class _Utterance:
    """Uma frase esperando a vez."""

    __slots__ = ("text", "priority", "seq", "created_at", "ttl", "volume")

    def __init__(self, text: str, priority: int, seq: int, created_at: float,
                 ttl: float = None, volume: int = None):
        self.text = text
        self.priority = priority
        self.seq = seq
        self.created_at = created_at
        #: Validade PRÓPRIA deste recado, em segundos. Sem ela vale MAX_AGE_S.
        self.ttl = ttl
        #: Volume DESTE recado (0-100). `None` = o volume geral da voz. É o
        #: que a mesa de som usa para deixar o coach de curva no ouvido e o
        #: aviso de consumo lá no fundo, sem mexer no volume do resto.
        self.volume = volume

    def is_stale(self, now: float) -> bool:
        """
        Crítico nunca vence; o resto perde a validade.

        A validade padrão serve para recado de contexto ("asfalto esquentando"),
        que continua verdadeiro por um bom tempo. Recado com hora marcada traz
        a sua: uma dica de "Ferradura chegando" vale os três segundos até a
        freada — dita dez segundos depois, ela chega em cima de OUTRA curva e
        manda o piloto frear no lugar errado.
        """
        limite = self.ttl if self.ttl is not None else MAX_AGE_S
        return (self.priority > PRIORITY_CRITICAL
                and (now - self.created_at) > limite)


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

    def put(self, text: str, priority: int, now: float, ttl: float = None,
            volume: int = None):
        with self._cond:
            if self._closed:
                return
            for u in self._items:
                if u.text != text:
                    continue
                # Já está na fila: não duplica. Mas se a MESMA frase voltou
                # mais urgente (o aviso de pneu virou crítico), promove a que
                # está lá — senão o recado urgente seria engolido pelo dedupe
                # e continuaria valendo como recado comum: não cortaria a fala
                # em andamento e ainda poderia vencer por idade.
                if priority < u.priority:
                    u.priority = priority
                    u.created_at = now
                    u.ttl = ttl
                    u.volume = volume
                    self._items.sort(key=lambda x: (x.priority, x.seq))
                    self._cond.notify()
                return
            self._seq += 1
            self._items.append(
                _Utterance(text, priority, self._seq, now, ttl, volume))
            self._items.sort(key=lambda u: (u.priority, u.seq))
            while len(self._items) > self.maxsize:
                self._items.pop(self._drop_index())
            self._cond.notify()

    def _drop_index(self) -> int:
        """
        Quem cai primeiro: o mais antigo da MENOR prioridade.

        Como a lista está ordenada por (prioridade, ordem de chegada), o
        primeiro item da pior prioridade é justamente o mais velho dela.

        Numa fila só de críticos, sai o crítico mais velho — e é o que se quer:
        a garantia do PRIORITY_CRITICAL é não vencer por IDADE, não ocupar
        vaga para sempre. Com a fila cheia de emergências, a mais recente é a
        que descreve a situação atual do carro.
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
        """
        Se a voz pedida não existir no modelo, usa outra em português.

        Entre as em português, prefere a masculina: no Kokoro o prefixo é
        `pm_` para masculina e `pf_` para feminina. Sem essa preferência, um
        modelo sem a `pm_alex` cairia na feminina e desfaria a escolha de voz.
        """
        try:
            vozes = list(self._engine.get_voices())
        except Exception:
            return self.voice
        if not vozes or self.voice in vozes:
            return self.voice
        # No Kokoro o prefixo "p" identifica as vozes em português.
        pt = [v for v in vozes if v.startswith("p")]
        masculinas = [v for v in pt if v.startswith("pm")]
        escolhida = (masculinas or pt or vozes)[0]
        print(f"[Voice/Kokoro] Voz '{self.voice}' não existe no modelo; "
              f"usando '{escolhida}'")
        return escolhida

    # -- síntese ---------------------------------------------------------

    def _synth(self, text: str, volume: int = None) -> bytes:
        """Sintetiza e devolve os bytes de um WAV mono 16 bits."""
        import io

        import numpy as np

        samples, sample_rate = self._engine.create(
            text, voice=self.voice, speed=self.speed, lang=self.lang)
        audio = np.asarray(samples)
        if volume is not None:
            # Escala linear na amostra. Um backend neural entrega o áudio, não
            # fala: sem isto o Kokoro ignoraria a mesa de som em silêncio, e o
            # piloto acharia que os faders quebraram ao instalar a voz neural.
            audio = audio * (max(0, min(100, int(volume))) / 100.0)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm.tobytes())
        return buffer.getvalue()

    def speak(self, text: str, should_stop: Callable[[], bool],
              volume: int = None) -> bool:
        if not self.available or self._engine is None:
            return False
        try:
            # O volume entra na CHAVE do cache: o mesmo texto em dois volumes
            # são dois áudios diferentes, e tocar o do volume errado desfaria
            # a mesa de som em silêncio.
            chave = text if volume is None else f"{text}\x00v{int(volume)}"
            path = self._cache.get_cached_path(chave)
            if path is None:
                path = self._cache.save_wav(chave, self._synth(text, volume))
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
    #: Interpreta marcação XML no texto — é o que permite ajustar o tom.
    _IS_XML = 8

    def __init__(self, rate: int = DEFAULT_RATE, volume: int = 100,
                 pitch: int = DEFAULT_PITCH, voice_name: str = "",
                 prefer_male: bool = True):
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.voice_name = voice_name
        self.prefer_male = prefer_male
        self.available = False
        self.description = ""
        #: Descrição de todas as vozes que a máquina oferece. Fica guardada
        #: para o app poder LISTAR as opções: o piloto só consegue escolher
        #: `voice_name` se souber o que existe instalado.
        self.installed: List[str] = []
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
        self.installed = []
        for token in self._all_tokens(win32com, voice):
            try:
                descricao = token.GetDescription()
                nota = VoiceEngine.voice_score(
                    descricao, prefer_name=self.voice_name,
                    prefer_male=self.prefer_male)
            except Exception:
                continue
            if descricao not in self.installed:
                self.installed.append(descricao)
            if nota > melhor_nota:
                melhor, melhor_nota = token, nota
        return melhor

    # -- fala ------------------------------------------------------------

    @staticmethod
    def _escape_xml(text: str) -> str:
        """
        Escapa o texto para ir dentro de marcação XML do SAPI.

        Sem isto, um recado que contenha "&" ou "<" faria o SAPI engolir a
        frase inteira em silêncio — e um recado não dito é pior que um recado
        sem entonação.
        """
        return (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))

    def _marcado(self, text: str, volume: int = None) -> tuple:
        """
        O texto e as flags a usar, com os ajustes de tom e volume quando houver.

        Devolve o texto cru e as flags sem XML quando não há ajuste nenhum:
        assim o caminho comum não depende do parser XML do SAPI.
        """
        marcas = ""
        if self.pitch:
            marcas += f'<pitch absmiddle="{max(-10, min(10, int(self.pitch)))}"/>'
        if volume is not None:
            marcas += f'<volume level="{max(0, min(100, int(volume)))}"/>'
        if not marcas:
            return text, self._ASYNC
        return marcas + self._escape_xml(text), self._ASYNC | self._IS_XML

    def speak(self, text: str, should_stop: Callable[[], bool],
              volume: int = None) -> bool:
        if not self.available or self._voice is None:
            return False
        falado, flags = self._marcado(text, volume)
        try:
            # Assíncrono + espera em fatias: é o que permite cortar a frase
            # quando chega um recado crítico.
            self._voice.Speak(falado, flags)
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
                 backend: str = "auto", volume: int = 100,
                 pitch: int = DEFAULT_PITCH, preferred_voice: str = "",
                 prefer_male: bool = True):
        self.enabled = enabled
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        #: Nome (ou pedaço do nome) da voz escolhida pelo piloto. Vazio = o
        #: app escolhe a melhor que encontrar.
        self.preferred_voice = preferred_voice
        self.prefer_male = prefer_male
        self.backend_option = backend
        self.available = False
        self.voice_name = ""
        #: Vozes que a máquina oferece, para o app poder listá-las.
        self.installed_voices: List[str] = []

        self._queue = _SpeechQueue()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        #: Ligado quando não há nada na fila NEM nada sendo falado. É o que
        #: permite a alguém esperar a fala acabar — a ferramenta de ajuste de
        #: voz precisa disso para medir quanto tempo um recado realmente leva,
        #: que é a pergunta que decide se a frase está longa demais.
        self._idle = threading.Event()
        self._idle.set()
        self._backends: List[object] = []
        self._thread = threading.Thread(target=self._run, name="VoiceEngine",
                                        daemon=True)
        self._thread.start()

    # -- API pública ------------------------------------------------------

    def say(self, text: str, priority: int = PRIORITY_NORMAL,
            ttl: float = None, volume: int = None):
        """
        Enfileira uma fala. Volta imediatamente.

        `ttl` é a validade do recado em segundos: passado esse tempo na fila,
        ele é descartado em vez de dito atrasado. Recado com hora marcada —
        uma dica de curva — deve declarar a sua; sem `ttl` vale MAX_AGE_S.

        `volume` (0-100) é o volume DESTE recado; sem ele vale o volume geral.
        É por aqui que a mesa de som (core/voice_mix.py) mistura os assuntos.
        """
        text = (text or "").strip()
        if not text or not self.enabled or self._stopping.is_set():
            return
        self._idle.clear()
        self._queue.put(text, priority, time.monotonic(), ttl, volume)

    def clear(self):
        """Esvazia o que ainda não foi falado (não corta a frase em curso)."""
        self._queue.clear()

    def is_idle(self) -> bool:
        """
        Nada na fila e nada sendo falado, AGORA — sem bloquear.

        Não existe versão que ESPERA de propósito: quem quer saber quando a
        fala acabou é uma interface, e bloquear a interface até a frase
        terminar é justamente o que não se pode fazer. Quem pergunta aqui é um
        timer, que continua desenhando entre uma checagem e outra.
        """
        return self._idle.is_set()

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
    def voice_score(description: str, prefer_name: str = "",
                    prefer_male: bool = True) -> int:
        """
        Nota de uma voz do Windows pela descrição.

        A ordem dos pesos é a ordem do que estraga a fala:

        1. **Idioma** (200) — voz em inglês lendo português fica
           incompreensível. Nada compensa isso.
        2. **Geração** (80 / -100) — as vozes OneCore do Windows 10/11 são
           muito melhores que as "Desktop", de 2010.
        3. **Gênero** (60) — masculina quando pedida. Pesa menos que idioma e
           geração de propósito: numa máquina que só tenha voz feminina em
           português, é melhor uma voz feminina boa do que uma masculina em
           inglês, ou a Desktop antiga.
        4. **Nome preferido** (até 40) — só desempate.

        `prefer_name` vem da configuração e vence tudo: se o piloto escolheu
        uma voz pelo nome, é essa que ele quer ouvir, e o app não deve
        adivinhar melhor que ele.
        """
        d = (description or "").lower()

        # A voz escolhida pelo piloto ganha de qualquer critério nosso, mas o
        # bônus é SOMADO à nota normal, não substitui: uma máquina com
        # "Maria" clássica e "Maria" OneCore atende as duas pelo nome, e é a
        # nota que decide qual — senão a escolha ficava na ordem de
        # enumeração do Windows, que não é nossa para garantir.
        score = 0
        alvo = (prefer_name or "").strip().lower()
        if alvo and alvo in d:
            score += 10 ** 6

        if any(hint in d for hint in LANGUAGE_HINTS):
            score += 200
        score += -100 if "desktop" in d else 80
        if prefer_male and any(nome in d for nome in MALE_VOICE_NAMES):
            score += 60
        for i, name in enumerate(PREFERRED_VOICES):
            if name in d:
                score += max(40 - i * 5, 5)     # daniel=40, antonio=35, ...
                break
        return score

    # -- thread de fala ---------------------------------------------------

    def _make_backends(self) -> List[object]:
        """Backends na ordem de preferência, já filtrados pela opção escolhida."""
        candidatos = []
        if self.backend_option in ("auto", "kokoro"):
            candidatos.append(KokoroBackend())
        if self.backend_option in ("auto", "sapi"):
            candidatos.append(SapiBackend(
                rate=self.rate, volume=self.volume, pitch=self.pitch,
                voice_name=self.preferred_voice,
                prefer_male=self.prefer_male))
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
        for backend in self._backends:
            for descricao in getattr(backend, "installed", []) or []:
                if descricao not in self.installed_voices:
                    self.installed_voices.append(descricao)

        if self.available:
            self.voice_name = self._backends[0].description or self._backends[0].name
            print(f"[Voice] Engenheiro com voz: {self.voice_name} "
                  f"(ritmo {self.rate:+d}, tom {self.pitch:+d})")
            # Lista o que existe instalado. É a única forma de o piloto saber
            # o que pode pôr em `voice_name` no config.json — as vozes variam
            # de máquina para máquina conforme o pacote de idioma instalado.
            outras = [v for v in self.installed_voices if v != self.voice_name]
            if outras:
                print("[Voice] Outras vozes nesta máquina (use 'voice_name' "
                      "no config.json para escolher): " + "; ".join(outras))
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
                if self._queue.peek_priority() is None:
                    self._idle.set()
                fala = self._queue.get()
                if fala is None:
                    break
                self._idle.clear()
                try:
                    if not self.enabled or not self.available:
                        continue
                    if fala.is_stale(time.monotonic()):
                        continue                # perdeu a validade na fila
                    self._speak(fala)
                finally:
                    if self._queue.peek_priority() is None:
                        self._idle.set()
        finally:
            self._idle.set()
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
                if backend.speak(fala.text, interrompe, fala.volume):
                    return
            except Exception as e:
                print(f"[Voice] {backend.name} falhou ({type(e).__name__}: {e})")

        # Todos caíram: para de tentar falar, o painel de texto segue.
        if not any(getattr(b, "available", False) for b in self._backends):
            self.available = False
            print("[Voice] Sem backend de voz utilizável; a fala foi desligada")
