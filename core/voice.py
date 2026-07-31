"""
core/voice.py — Voz do engenheiro (TTS)
=======================================
Fala as mensagens do engenheiro de pista usando o SAPI do próprio Windows, via
pywin32 — sem instalar mais nada. Se o SAPI não estiver disponível (outro
sistema, pywin32 ausente), a classe continua funcionando e simplesmente não
fala: o painel de texto é a fonte de verdade, a voz é um extra.

Dois cuidados que definem o desenho:

  * **Nunca bloquear a interface.** Falar é lento (segundos). Toda a fala roda
    numa thread própria, alimentada por uma fila; quem chama `say()` volta na
    hora.
  * **Não acumular fila.** Se o piloto entrou numa sequência de curvas e três
    avisos saíram juntos, falar todos com 5 s de atraso é pior que não falar.
    A fila tem tamanho máximo pequeno e descarta o mais antigo.

O COM precisa ser inicializado na thread que o usa (CoInitialize), por isso o
objeto de voz é criado dentro da própria thread, não no construtor.
"""

import queue
import threading

#: Mais que isso na fila e as mensagens antigas perdem a validade.
MAX_QUEUE = 3

#: Categoria das vozes OneCore (Windows 10/11). São bem mais naturais que as
#: "Desktop" do SAPI clássico — e o SAPI NÃO as enumera por padrão, é preciso
#: pedir esta categoria explicitamente. Num Windows típico em português isso é
#: a diferença entre "Microsoft Maria Desktop" (robótica) e "Microsoft Daniel".
ONECORE_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"

#: Nome preferido quando há mais de uma voz boa no mesmo idioma. Troque para
#: "maria" se preferir voz feminina — a escolha cai para a melhor disponível
#: se o nome pedido não existir.
PREFERRED_VOICE_NAME = "daniel"

#: Pistas de idioma: engenheiro falando português ganha de qualquer outra coisa.
LANGUAGE_HINTS = ("portugu", "brazil", "brasil")


class VoiceEngine:
    """
    Fila de fala com uma thread dedicada.

    Uso:
        voz = VoiceEngine()
        voz.enabled = True
        voz.say("Freio travando")
        ...
        voz.stop()
    """

    def __init__(self, enabled: bool = True, rate: int = 1):
        self.enabled = enabled
        #: -10 (lento) a 10 (rápido) na escala do SAPI. 1 = pouco acima do normal,
        #: que é o ritmo de quem passa informação no rádio.
        self.rate = rate
        self.available = False
        self.voice_name = ""

        self._queue = queue.Queue()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="VoiceEngine",
                                        daemon=True)
        self._thread.start()

    # -- API pública ------------------------------------------------------

    def say(self, text: str):
        """Enfileira uma fala. Retorna imediatamente."""
        if not text or not self.enabled or self._stopping.is_set():
            return
        # Descarta o mais antigo em vez de deixar a fila crescer
        while self._queue.qsize() >= MAX_QUEUE:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(text)

    def clear(self):
        """Esvazia o que ainda não foi falado (troca de sessão, por exemplo)."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self):
        """Encerra a thread de fala."""
        self._stopping.set()
        self._queue.put(None)

    # -- Thread de fala ---------------------------------------------------

    @staticmethod
    def voice_score(description: str) -> int:
        """
        Nota de uma voz pela descrição. Maior é melhor.

        A regra que mais importa: voz "Desktop" é a geração antiga do SAPI e
        soa mecânica; a mesma locutora na versão OneCore soa muito melhor. Como
        as duas se chamam quase igual ("Microsoft Maria Desktop" x "Microsoft
        Maria"), é o sufixo que as separa.
        """
        d = (description or "").lower()
        score = 0
        if any(hint in d for hint in LANGUAGE_HINTS):
            score += 100
        if "desktop" not in d:
            score += 50
        if PREFERRED_VOICE_NAME and PREFERRED_VOICE_NAME in d:
            score += 10
        return score

    def _all_tokens(self, win32com, voice):
        """
        Todas as vozes instaladas: OneCore + SAPI clássico.

        `voice.GetVoices()` só devolve as do SAPI clássico; as OneCore ficam em
        outra categoria do registro e precisam ser pedidas à parte.
        """
        tokens = []
        try:
            cat = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
            cat.SetId(ONECORE_CATEGORY, False)
            enum = cat.EnumerateTokens()
            tokens += [enum.Item(i) for i in range(enum.Count)]
        except Exception:
            pass   # Windows sem OneCore (ou outro sistema): segue com o clássico
        try:
            classic = voice.GetVoices()
            tokens += [classic.Item(i) for i in range(classic.Count)]
        except Exception:
            pass
        return tokens

    def _best_token(self, win32com, voice):
        """A melhor voz disponível pelo critério de `voice_score`."""
        melhor, melhor_nota = None, -1
        for token in self._all_tokens(win32com, voice):
            try:
                nota = self.voice_score(token.GetDescription())
            except Exception:
                continue
            if nota > melhor_nota:
                melhor, melhor_nota = token, nota
        return melhor

    def _make_voice(self):
        """
        Cria o objeto SAPI dentro da thread. Devolve (voice, com_module) ou
        (None, None) quando não há TTS disponível.
        """
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            print("[Voice] pywin32 não encontrado: a voz do engenheiro fica desligada "
                  "(o painel de texto continua funcionando).")
            return None, None

        try:
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Rate = self.rate

            token = self._best_token(win32com, voice)
            if token is not None:
                try:
                    voice.Voice = token
                    self.voice_name = token.GetDescription()
                except Exception as e:
                    print(f"[Voice] Não foi possível usar a voz escolhida "
                          f"({type(e).__name__}); seguindo com a voz padrão.")

            self.available = True
            print(f"[Voice] Engenheiro com voz: {self.voice_name or 'voz padrão'}")
            return voice, pythoncom
        except Exception as e:
            print(f"[Voice] SAPI indisponível ({type(e).__name__}: {e}); "
                  "a voz do engenheiro fica desligada.")
            return None, None

    def _run(self):
        voice, com = self._make_voice()
        try:
            while not self._stopping.is_set():
                text = self._queue.get()
                if text is None:
                    break
                if voice is None or not self.enabled:
                    continue
                try:
                    voice.Speak(text)
                except Exception as e:
                    # Uma fala que falha não pode derrubar a thread nem o app
                    print(f"[Voice] Falha ao falar ({type(e).__name__}: {e})")
        finally:
            if com is not None:
                try:
                    com.CoUninitialize()
                except Exception:
                    pass
