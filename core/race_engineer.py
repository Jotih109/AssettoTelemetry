"""
core/race_engineer.py — Engenheiro de pista (análise por regras)
===============================================================
Lê o que o app já mede e diz, em português, o que fazer com isso. Não usa
nenhum serviço externo nem modelo de linguagem: são regras sobre a telemetria,
o que significa custo zero, resposta instantânea e — o que mais importa num
painel de engenharia — número sempre exato, nunca inventado.

O foco é PILOTAGEM, não acerto de carro. O engenheiro fala de tempo, setor,
pedal, volante, marcha e traçado — coisas que o piloto muda na volta seguinte,
sem sair da pista. Câmber, pressão e ganho de force feedback ficam de fora de
propósito: no meio de uma volta, conselho de setup é ruído.

Três famílias de regras:

  * `analyze_live(state, now)`  — o que precisa ser dito AGORA: delta contra a
    referência, setor que acabou de fechar, melhor volta na reta final,
    bandeira, combustível, pneu, limitador, dano, condição de pista. Cada
    regra tem tempo de espera próprio, para não repetir a mesma frase a 60 Hz —
    e no box, no pit lane, em replay ou com o jogo pausado o engenheiro fica
    calado (só bandeira e penalidade valem em qualquer lugar).

  * `analyze_lap(...)` — o balanço da volta que acabou: em que setor o tempo
    foi embora, em que curva, e por quê (freou antes? entrou devagar? marcha
    errada no ápice? saiu da linha?). Sai ordenado pelo tempo em jogo, porque é
    isso que decide o que vale a pena ouvir primeiro.

  * `register_lap_time` / `register_fuel` — o que só existe COMPARANDO voltas:
    consistência de ritmo e consumo.

As MEDIDAS de pilotagem (sobreposição de pedais, suavidade de volante, pontos
de troca, desvio de traçado) ficam em `core/driving_analysis.py`. Aqui só
entram as regras: quando aquilo vira conselho, com que severidade e em que
palavras.

Nada aqui depende de PyQt: dá para testar tudo com voltas sintéticas.
"""

import dataclasses
import re
from typing import List, Optional

from core import driving_analysis as da

#: Números decimais no texto falado: em português quem lê "0.42" fala "zero
#: PONTO quarenta e dois", que soa errado. Com vírgula, o sintetizador lê
#: "zero vírgula quarenta e dois".
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")

# ---------------------------------------------------------------------------
# Severidade
# ---------------------------------------------------------------------------

INFO = "info"           # reforço positivo, contexto
ATTENTION = "atencao"   # dá tempo / pode piorar
CRITICAL = "critico"    # age agora ou quebra/perde a volta

_SEVERITY_ORDER = {CRITICAL: 0, ATTENTION: 1, INFO: 2}


# ---------------------------------------------------------------------------
# Limiares — todos nomeados, para poder discutir cada um
# ---------------------------------------------------------------------------

# --- Tempo, delta e setores ---
#: Delta ao vivo abaixo disto é ruído de medição, não pilotagem (s).
DELTA_LIVE_S = 0.10
#: A partir daqui a volta está indo embora de verdade (s).
DELTA_LIVE_BIG_S = 0.30
#: Só volta a comentar o delta quando ele mudou isto desde a última fala (s).
DELTA_CHANGE_S = 0.15
#: Setor com diferença menor que isto conta como "igual" (s).
SECTOR_TIE_S = 0.05
#: Perda de setor a partir da qual vale apontar o dedo (s).
SECTOR_LOSS_S = 0.12
#: Trecho final da volta em que a melhor volta vira assunto (posição 0..1).
BEST_LAP_ZONE = 0.93

#: Ignora ruído: curva com menos que isto de diferença não rende conselho.
MIN_CORNER_LOSS_S = 0.05
#: Quantas curvas comentar no balanço da volta.
MAX_CORNER_ADVICE = 3

# --- Curva ---
#: Diferença de V_min que já explica perda de tempo (km/h).
VMIN_DIFF_KMH = 2.0
#: Diferença de velocidade de saída que vale comentar (km/h).
VEXIT_DIFF_KMH = 3.0
#: Diferença de ponto de frenagem que conta como "freou antes/depois" (m).
BRAKE_DIFF_M = 8.0
#: Abaixo disto o ponto de freio está certo — é ajuste fino, não erro (m).
BRAKE_FINE_M = 4.0
#: Diferença de ponto de retomada que conta como "acelerou depois" (m).
THROTTLE_DIFF_M = 10.0
#: Desvio médio de traçado que já é sair da linha (m).
LINE_DEVIATION_M = 1.5

# --- Pedais e volante ---
#: Fração das freadas com os dois pés que caracteriza pé preso.
OVERLAP_FRACTION = 0.10
#: Fração das freadas com repisada que caracteriza freio solto em degraus.
BRAKE_JITTER_FRACTION = 0.40
#: Fração das freadas largadas de uma vez que vira conselho.
BRAKE_ABRUPT_FRACTION = 0.40
#: Fração de curva com volante travado e carro não virando = subesterço.
UNDERSTEER_FRACTION = 0.35
#: Volante quanto mais rápido que a referência já é "brusco".
STEER_ROUGH_RATIO = 1.25
#: ...e quanto mais lento já merece elogio.
STEER_SMOOTH_RATIO = 0.92

# --- Motor ---
#: Fração das trocas feitas cedo que vira conselho.
EARLY_SHIFT_FRACTION = 0.40
#: Fração da volta batendo no corte que vira conselho.
LIMITER_FRACTION = 0.03

# --- Eletrônica ---
#: Intervenção de ABS/TC (0..1) a partir da qual vale avisar.
ABS_LIVE_THRESHOLD = 0.55
TC_LIVE_THRESHOLD = 0.55
#: Fração da volta com ABS/TC atuando forte que caracteriza vício de pilotagem.
ABS_LAP_FRACTION = 0.04
TC_LAP_FRACTION = 0.06

# --- Pneus, pista e clima ---
TYRE_HOT_C = 105.0
TYRE_CRITICAL_C = 115.0
TYRE_COLD_C = 70.0
#: Freio acima disto perde mordida e a freada muda no meio da volta.
BRAKE_HOT_C = 800.0
#: Variação de temperatura de asfalto que muda o grip de forma perceptível.
TRACK_TEMP_STEP_C = 3.0
#: Grip da pista abaixo disto ainda está "verde".
GREEN_TRACK_GRIP = 0.96
#: Vento a partir do qual o carro sente na reta (m/s).
WIND_STRONG_MS = 8.0

# --- Combustível ---
FUEL_LAPS_WARNING = 2.0
FUEL_LAPS_CRITICAL = 1.0
#: Consumo acima desta fração da média é volta gastadeira.
FUEL_HIGH_RATIO = 1.12

# --- Diversos ---
#: Rodas fora da pista que já valem aviso de corta-caminho.
TYRES_OUT_LIMIT = 3
#: Dano acumulado (0..100, pior componente) que vale comentar.
DAMAGE_WARNING = 20.0
DAMAGE_CRITICAL = 50.0
#: Só avisa de novo se o dano PIOROU isto desde o último aviso.
DAMAGE_STEP = 8.0
#: Acima disto o carro está andando de verdade (km/h).
MOVING_KMH = 15.0

#: Desvio entre as últimas voltas que caracteriza falta de consistência (s).
CONSISTENCY_SPREAD_S = 0.6
CONSISTENCY_LAPS = 4

#: Tempo de espera padrão de cada regra ao vivo (s).
DEFAULT_COOLDOWN_S = 12.0
COOLDOWNS_S = {
    "abs": 8.0, "tc": 8.0, "flag": 6.0, "penalty": 10.0,
    "cut": 6.0, "fuel": 25.0, "fuel_ok": 120.0, "tyre_hot": 20.0,
    "tyre_cold": 40.0, "brake_hot": 20.0, "limiter": 8.0,
    "damage": 15.0, "last_lap": 60.0,
    "delta": 10.0, "sector": 3.0, "best_lap": 25.0,
    "track_temp": 120.0, "grip": 180.0, "wind": 180.0,
}

#: Intervalo mínimo entre duas falas, para não metralhar o piloto.
MIN_SPEAK_GAP_S = 3.5


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Advice:
    """Um recado do engenheiro."""
    key: str                    # identidade da regra (+alvo) para o tempo de espera
    severity: str
    text: str                   # frase curta — é isto que a voz fala
    detail: str = ""            # números; aparecem só no painel de texto
    corner: Optional[int] = None
    kind: str = "lap"           # "live" (na pista) ou "lap" (fim de volta)
    time_at_stake: float = 0.0  # segundos em jogo, usado para ordenar

    @property
    def display(self) -> str:
        return f"{self.text} — {self.detail}" if self.detail else self.text

    @property
    def spoken(self) -> str:
        """
        O texto como deve ser FALADO (o painel continua mostrando `display`).

        Só troca o separador decimal por vírgula: é o que faz o sintetizador
        ler "zero vírgula quarenta e dois" em vez de "zero ponto quatro dois".
        """
        return _DECIMAL_RE.sub(r"\1,\2", self.text)

    def __str__(self) -> str:
        return self.display


def _fmt_s(value: float) -> str:
    return f"{value:+.3f}s"


def _ordinal_setor(i: int) -> str:
    return f"setor {i}"


class RaceEngineer:
    """
    Guarda o estado necessário para não repetir conselho e para comparar voltas.

    O relógio vem de fora (`now`, em segundos) — assim os testes controlam o
    tempo e nada aqui depende do relógio real.
    """

    def __init__(self):
        self._last_said = {}       # key -> now
        self._last_speak_at = -999.0
        self._lap_times_ms = []
        self._last_damage = 0.0
        self._last_consistency = None
        self._last_delta_said = None
        self._sector_said = [None, None, None]
        self._sector_history = []  # uma lista de 3 deltas por volta
        self._track_temp_ref = None
        self._fuel_history = []
        self._last_fuel = None

    def reset(self):
        self._last_said.clear()
        self._last_speak_at = -999.0
        self._lap_times_ms.clear()
        self._last_damage = 0.0
        self._last_consistency = None
        self._last_delta_said = None
        self._sector_said = [None, None, None]
        self._sector_history.clear()
        self._track_temp_ref = None
        self._fuel_history.clear()
        self._last_fuel = None

    # -- controle de repetição -------------------------------------------

    def _ready(self, key: str, now: float) -> bool:
        """Já passou o tempo de espera desta regra?"""
        cooldown = COOLDOWNS_S.get(key.split(":")[0], DEFAULT_COOLDOWN_S)
        last = self._last_said.get(key)
        return last is None or (now - last) >= cooldown

    def _mark(self, key: str, now: float):
        self._last_said[key] = now

    def should_speak(self, now: float) -> bool:
        """
        Respeita o intervalo mínimo entre BLOCOS de fala.

        Vale para o bloco, não para cada frase: as frases de um mesmo balanço
        de volta são ditas em sequência pela fila de voz, e espaçá-las aqui só
        faria a segunda ser descartada.
        """
        return (now - self._last_speak_at) >= MIN_SPEAK_GAP_S

    def mark_spoken(self, now: float):
        self._last_speak_at = now

    @staticmethod
    def pick_for_voice(advices: List[Advice], limit: int = 2) -> List[Advice]:
        """
        Escolhe o que vale falar em voz alta — o resto fica no painel.

        Ordem de importância para quem está dirigindo:
          1. qualquer coisa crítica (bandeira, pneu estourando, combustível);
          2. a curva onde mais tempo foi perdido, que é a informação que muda
             a próxima volta;
          3. o que sobrar, na ordem em que já veio.
        """
        picked: List[Advice] = []

        criticos = [a for a in advices if a.severity == CRITICAL]
        if criticos:
            picked.append(criticos[0])

        curvas = [a for a in advices
                  if a.key.startswith("corner:") and a.time_at_stake > 0]
        if curvas:
            pior = max(curvas, key=lambda a: a.time_at_stake)
            if pior not in picked:
                picked.append(pior)

        for advice in advices:
            if len(picked) >= limit:
                break
            if advice not in picked:
                picked.append(advice)

        return picked[:limit]

    @staticmethod
    def _is_race(state) -> bool:
        """Sessão de corrida — o que muda o que vale a pena dizer sobre tanque."""
        if (getattr(state, "total_laps", 0) or 0) > 0:
            return True
        return "race" in (getattr(state, "session_type", "") or "").lower()

    @staticmethod
    def is_on_track(state) -> bool:
        """
        O carro está em pista, sob comando do piloto?

        Em replay, com o jogo pausado ou parado no box, tudo o que o engenheiro
        tem a dizer sobre delta e pneu frio é ruído — e ruído em voz alta é o
        jeito mais rápido de o piloto desligar a voz.
        """
        if getattr(state, "is_replay", False) or getattr(state, "is_paused", False):
            return False
        return not (getattr(state, "in_pit", False)
                    or getattr(state, "in_pit_lane", False))

    # -----------------------------------------------------------------
    # Ao vivo
    # -----------------------------------------------------------------

    def analyze_live(self, state, now: float) -> List[Advice]:
        """
        O que precisa ser dito com o carro andando. Devolve no máximo alguns
        recados por chamada — e nunca o mesmo antes do tempo de espera.
        """
        out = []

        def add(key, severity, text, detail=""):
            if not self._ready(key, now):
                return
            self._mark(key, now)
            out.append(Advice(key=key, severity=severity, text=text,
                              detail=detail, kind="live"))

        na_pista = self.is_on_track(state)

        # --- Bandeiras e penalidades: valem em qualquer lugar ---
        flag = (getattr(state, "flag", "") or "").upper()
        if flag in ("AMARELA", "AZUL", "PRETA", "PENALIDADE"):
            frases = {
                "AMARELA": "Bandeira amarela, reduza o ritmo",
                "AZUL": "Bandeira azul, deixa passar",
                "PRETA": "Bandeira preta, entra nos boxes",
                "PENALIDADE": "Piloto, você tem uma penalidade",
            }
            add(f"flag:{flag}", CRITICAL, frases[flag])
        elif flag == "XADREZ":
            add("flag:XADREZ", INFO, "Bandeira quadriculada, fim de sessão")

        if getattr(state, "penalty_time", 0.0) > 0:
            add("penalty", CRITICAL, "Cumpra a penalidade nos boxes",
                f"{state.penalty_time:.0f}s pendentes")

        if not na_pista:
            out.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
            return out

        self._live_lap_state(state, add)
        self._live_time(state, add, now)
        self._live_car(state, add, now)
        self._live_track(state, add)

        out.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
        return out

    # -- ao vivo: situação da volta e da corrida --------------------------

    def _live_lap_state(self, state, add):
        total_laps = getattr(state, "total_laps", 0) or 0
        done = getattr(state, "completed_laps", 0) or 0
        if total_laps > 0 and done == total_laps - 1:
            add(f"last_lap:{total_laps}", INFO, "Última volta, é essa aí")

        if (getattr(state, "pit_limiter", False)
                and getattr(state, "speed_kmh", 0.0) > MOVING_KMH):
            add("limiter", CRITICAL, "Desliga o limitador, você está na pista")

        if getattr(state, "tyres_out", 0) >= TYRES_OUT_LIMIT:
            add("cut", ATTENTION, "Passou dos limites da pista, esse tempo não vale",
                f"{state.tyres_out} rodas fora")

    # -- ao vivo: o tempo, que é o assunto principal ----------------------

    def _live_time(self, state, add, now: float):
        """
        Delta contra a referência, setor que fechou e a melhor volta em jogo.

        É o que um spotter fala o tempo todo, e o que muda a pilotagem no meio
        da volta — por isso vem antes de temperatura e clima.
        """
        delta = getattr(state, "delta_time", 0.0) or 0.0
        pos = getattr(state, "track_position", 0.0) or 0.0

        # --- Delta ao vivo, só quando MUDOU: repetir "mais 0.15" a cada dez
        # segundos não informa nada novo.
        mudou = (self._last_delta_said is None
                 or abs(delta - self._last_delta_said) >= DELTA_CHANGE_S)
        if abs(delta) >= DELTA_LIVE_S and mudou and self._ready("delta", now):
            if delta >= DELTA_LIVE_BIG_S:
                texto = (f"Delta mais {delta:.2f}, tá perdendo tempo. "
                         "Respira e refaz a volta")
                sev = ATTENTION
            elif delta > 0:
                texto = f"Delta mais {delta:.2f}, um pouco atrás da referência"
                sev = INFO
            else:
                texto = f"Delta menos {abs(delta):.2f}, tá voando. Mantém assim"
                sev = INFO
            add("delta", sev, texto, f"delta {_fmt_s(delta)}")
            self._last_delta_said = delta

        # --- Setor que acabou de fechar ---
        for i in (1, 2, 3):
            d = getattr(state, f"s{i}_delta", 0.0) or 0.0
            if d == 0.0:
                self._sector_said[i - 1] = None      # setor reaberto na volta nova
                continue
            if self._sector_said[i - 1] == d:
                continue
            self._sector_said[i - 1] = d
            if d <= -SECTOR_TIE_S:
                add(f"sector:{i}", INFO,
                    f"Bateu a referência no {_ordinal_setor(i)}, "
                    f"ganhou {abs(d):.2f}", f"S{i} {_fmt_s(d)}")
            elif d >= SECTOR_LOSS_S:
                add(f"sector:{i}", ATTENTION,
                    f"Perdeu {d:.2f} no {_ordinal_setor(i)}",
                    f"S{i} {_fmt_s(d)}")

        # --- Melhor volta em jogo na reta final ---
        if pos >= BEST_LAP_ZONE:
            length = getattr(state, "track_length", 0.0) or 0.0
            faltam = int(max(0.0, (1.0 - pos) * length))
            resto = f", faltam {faltam} metros" if faltam > 0 else ""
            if delta <= -DELTA_LIVE_S:
                add("best_lap", INFO,
                    f"Melhor volta na mão{resto}. Segura o carro na pista",
                    f"delta {_fmt_s(delta)}")
            elif 0 < delta <= DELTA_LIVE_BIG_S:
                add("best_lap", ATTENTION,
                    f"Melhor volta ameaçada{resto}, precisa desse último setor",
                    f"delta {_fmt_s(delta)}")

    # -- ao vivo: o carro -------------------------------------------------

    def _live_car(self, state, add, now: float):
        # Dano: só quando PIOROU, senão a mesma amassada seria anunciada a
        # cada tempo de espera até o fim da sessão.
        dano = float(getattr(state, "car_damage", 0.0) or 0.0)
        if (dano >= DAMAGE_WARNING and dano >= self._last_damage + DAMAGE_STEP
                and self._ready("damage", now)):
            severidade = CRITICAL if dano >= DAMAGE_CRITICAL else ATTENTION
            add("damage", severidade,
                "Dano sério no carro, avalia se dá para seguir"
                if severidade == CRITICAL
                else "Carro danificado, sente como ele está respondendo",
                f"{dano:.0f}% de dano")
            self._last_damage = dano

        # Combustível
        laps_left = getattr(state, "fuel_laps_remaining", 0.0)
        if 0.0 < laps_left <= FUEL_LAPS_CRITICAL:
            add("fuel", CRITICAL, "Combustível crítico, menos de uma volta",
                f"{laps_left:.1f} volta restante")
        elif 0.0 < laps_left <= FUEL_LAPS_WARNING:
            add("fuel", ATTENTION, "Cuidado, combustível ficando baixo",
                f"{laps_left:.1f} voltas restantes")
        elif laps_left > FUEL_LAPS_WARNING and self._is_race(state):
            # Só em corrida: num treino de 60 minutos com o tanque cheio, ouvir
            # "dá pra mais 30 voltas" a cada dois minutos não serve para nada.
            add("fuel_ok", INFO,
                f"Combustível tranquilo, dá pra mais {int(laps_left)} voltas",
                f"{laps_left:.1f} voltas de autonomia")

        # Eletrônica: só quando a intervenção é forte
        if getattr(state, "abs_intervention", 0.0) >= ABS_LIVE_THRESHOLD:
            add("abs", ATTENTION, "Travando a roda na freada, alivia a pressão",
                f"ABS em {state.abs_intervention * 100:.0f}%")

        if getattr(state, "tc_intervention", 0.0) >= TC_LIVE_THRESHOLD:
            add("tc", ATTENTION, "Tração cortando, abre o gás mais devagar",
                f"TC cortando {state.tc_intervention * 100:.0f}%")

        # Pneus
        nomes = ("dianteiro esquerdo", "dianteiro direito",
                 "traseiro esquerdo", "traseiro direito")
        temps = list(getattr(state, "tyre_temp", []) or [])[:4]
        for i, temp in enumerate(temps):
            if temp >= TYRE_CRITICAL_C:
                add(f"tyre_hot:{i}", CRITICAL,
                    f"Pneu {nomes[i]} superaquecido, alivia que ele vai embora",
                    f"{temp:.0f} °C")
            elif temp >= TYRE_HOT_C:
                add(f"tyre_hot:{i}", ATTENTION,
                    f"Pneu {nomes[i]} esquentando, cuidado com a sobrecarga",
                    f"{temp:.0f} °C")

        # Frio quase sempre é o carro inteiro (volta de saída do box). Dizer
        # roda por roda seriam quatro frases para uma informação só.
        frios = [i for i, temp in enumerate(temps) if 0 < temp <= TYRE_COLD_C]
        if len(frios) >= 3:
            add("tyre_cold", INFO, "Pneus ainda frios, aquece antes de atacar",
                f"{min(temps[i] for i in frios):.0f} a "
                f"{max(temps[i] for i in frios):.0f} °C")
        else:
            for i in frios:
                add(f"tyre_cold:{i}", INFO, f"Pneu {nomes[i]} ainda está frio",
                    f"{temps[i]:.0f} °C")

        # Freio quente muda a freada no meio da volta — é limite do carro, o
        # piloto precisa saber para antecipar o ponto.
        brakes = list(getattr(state, "brake_temp", []) or [])[:4]
        if brakes and max(brakes) >= BRAKE_HOT_C:
            add("brake_hot", ATTENTION,
                "Freios superaquecendo, antecipa o ponto que a mordida vai cair",
                f"{max(brakes):.0f} °C")

    # -- ao vivo: a pista -------------------------------------------------

    def _live_track(self, state, add):
        """Grip disponível: o que a pista está entregando hoje."""
        temp = float(getattr(state, "track_temp", 0.0) or 0.0)
        if temp > 0:
            if self._track_temp_ref is None:
                self._track_temp_ref = temp
            elif temp <= self._track_temp_ref - TRACK_TEMP_STEP_C:
                add("track_temp", INFO,
                    "Asfalto esfriando, o grip vai cair. Cuidado nas freadas",
                    f"{self._track_temp_ref:.0f} para {temp:.0f} °C")
                self._track_temp_ref = temp
            elif temp >= self._track_temp_ref + TRACK_TEMP_STEP_C:
                add("track_temp", INFO,
                    "Asfalto mais quente agora, tem mais grip disponível",
                    f"{self._track_temp_ref:.0f} para {temp:.0f} °C")
                self._track_temp_ref = temp

        grip = float(getattr(state, "surface_grip", 1.0) or 1.0)
        if 0 < grip < GREEN_TRACK_GRIP:
            add("grip", INFO,
                "Pista ainda verde, o grip vai melhorar conforme a borracha entra",
                f"aderência em {grip * 100:.0f}%")

        vento = float(getattr(state, "wind_speed", 0.0) or 0.0)
        if vento >= WIND_STRONG_MS:
            add("wind", INFO, "Vento forte na pista, segura o carro nas retas",
                f"{vento:.0f} m/s")

    # -----------------------------------------------------------------
    # Fim de volta
    # -----------------------------------------------------------------

    def analyze_lap(self, comparisons: list, lap_telemetry: dict = None,
                    state=None, lap_time_str: str = "",
                    lap_delta_s: float = None, ref_telemetry: dict = None,
                    sector_times_ms: list = None,
                    ref_sector_times_ms: list = None) -> List[Advice]:
        """
        Balanço da volta que fechou.

        `comparisons` é a lista de CornerComparison de core.corner_analysis —
        é dela que sai o "onde" e o "por quê" das curvas. `lap_telemetry` e
        `ref_telemetry` são os canais brutos das duas voltas, de onde saem as
        medidas de pedal, volante, marcha e traçado. `state` dá o RPM máximo do
        carro e o contexto de corrida.
        """
        out = []
        lap = da.LapChannels(lap_telemetry or {})
        ref = da.LapChannels(ref_telemetry or {}) if ref_telemetry else None
        track_length = (getattr(state, "track_length", 0.0) if state else 0.0) \
            or lap.lap_length_m

        # --- Resumo da volta ---
        if lap_delta_s is not None and lap_time_str:
            # Duas casas na fala (três milésimos ditos em voz alta viram
            # ladainha); o detalhe do painel mantém a precisão cheia
            if lap_delta_s <= -0.001:
                out.append(Advice(
                    key="lap:melhor", severity=INFO,
                    text=f"Boa volta! {abs(lap_delta_s):.2f} segundos abaixo da referência",
                    detail=f"{lap_time_str} ({_fmt_s(lap_delta_s)})",
                    kind="lap", time_at_stake=abs(lap_delta_s)))
            elif lap_delta_s > 0.001:
                out.append(Advice(
                    key="lap:pior", severity=INFO,
                    text=f"Perdemos {lap_delta_s:.2f} segundos pra referência nessa volta",
                    detail=f"{lap_time_str} ({_fmt_s(lap_delta_s)})",
                    kind="lap", time_at_stake=lap_delta_s))

        out.extend(self._sector_advice(sector_times_ms, ref_sector_times_ms))
        out.extend(self._corner_advice(comparisons, lap, ref, track_length))
        out.extend(self._pedal_advice(lap))
        out.extend(self._steering_advice(lap, ref))
        out.extend(self._engine_advice(lap, state))
        out.extend(self._electronics_advice(lap_telemetry or {}, comparisons))
        if state is not None:
            out.extend(self._race_advice(state))

        # Ordena por severidade e, dentro dela, pelo tempo em jogo
        out.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9),
                                -a.time_at_stake))
        return out

    # -- setores ----------------------------------------------------------

    def _sector_advice(self, sector_times_ms, ref_sector_times_ms) -> List[Advice]:
        """
        Onde o tempo foi, em blocos grandes.

        O setor é a primeira coisa que o piloto consegue guardar de cabeça: dá
        para ouvir "o tempo tá indo no S2" e usar isso na volta seguinte sem
        lembrar de curva nenhuma.
        """
        if not sector_times_ms or not ref_sector_times_ms:
            return []
        deltas = []
        for i in range(3):
            feito = sector_times_ms[i] if i < len(sector_times_ms) else 0
            base = ref_sector_times_ms[i] if i < len(ref_sector_times_ms) else 0
            deltas.append((feito - base) / 1000.0 if feito > 0 and base > 0 else None)
        if all(d is None for d in deltas):
            return []

        self._sector_history.append(deltas)
        self._sector_history = self._sector_history[-CONSISTENCY_LAPS:]

        def cor(d):
            if d is None:
                return "sem dado"
            if d <= -SECTOR_TIE_S:
                return "verde"
            if d < SECTOR_LOSS_S:
                return "amarelo"
            return "vermelho"

        resumo = ", ".join(f"S{i + 1} {cor(d)}" for i, d in enumerate(deltas))
        numeros = " | ".join(f"S{i + 1} {_fmt_s(d)}" for i, d in enumerate(deltas)
                             if d is not None)

        out = []
        perdas = [(i, d) for i, d in enumerate(deltas)
                  if d is not None and d >= SECTOR_LOSS_S]
        if perdas:
            pior_i, pior_d = max(perdas, key=lambda p: p[1])
            out.append(Advice(
                key=f"lap:sector:{pior_i + 1}", severity=ATTENTION,
                text=(f"{resumo}. O tempo tá indo embora no "
                      f"{_ordinal_setor(pior_i + 1)}, foca ali"),
                detail=numeros, kind="lap", time_at_stake=pior_d))
        else:
            out.append(Advice(
                key="lap:sector_ok", severity=INFO,
                text=f"{resumo}. Volta bem distribuída",
                detail=numeros, kind="lap"))

        # Ponto forte: só depois de algumas voltas, senão é sorte, não padrão.
        forte = self._strong_sector()
        if forte is not None:
            out.append(Advice(
                key=f"lap:sector_forte:{forte + 1}", severity=INFO,
                text=(f"O {_ordinal_setor(forte + 1)} é seu ponto forte, "
                      "é de lá que sai o tempo. Aproveita"),
                detail=f"média de {self._sector_mean(forte):+.3f}s "
                       f"em {len(self._sector_history)} voltas",
                kind="lap"))
        return out

    def _sector_mean(self, i: int) -> float:
        valores = [v[i] for v in self._sector_history if v[i] is not None]
        return sum(valores) / len(valores) if valores else 0.0

    def _strong_sector(self) -> Optional[int]:
        """Setor em que o piloto bate a referência de forma consistente."""
        if len(self._sector_history) < 3:
            return None
        medias = []
        for i in range(3):
            valores = [v[i] for v in self._sector_history if v[i] is not None]
            if len(valores) < 3:
                return None
            medias.append(sum(valores) / len(valores))
        melhor = min(range(3), key=lambda i: medias[i])
        if medias[melhor] > -SECTOR_TIE_S:
            return None                     # não é ponto forte, é empate
        return melhor

    # -- curvas -----------------------------------------------------------

    def _corner_advice(self, comparisons: list, lap: "da.LapChannels",
                       ref: Optional["da.LapChannels"],
                       track_length: float) -> List[Advice]:
        """Onde o tempo foi perdido e o que, na pilotagem, explica a perda."""
        perdas = [c for c in (comparisons or [])
                  if c.delta_time is not None and c.delta_time > MIN_CORNER_LOSS_S]
        perdas.sort(key=lambda c: -c.delta_time)

        out = []
        for cmp_ in perdas[:MAX_CORNER_ADVICE]:
            nome = cmp_.corner.name or f"C{cmp_.corner.index}"
            causas, dicas, numeros = [], [], []

            d_vmin = cmp_.delta_v_min
            if d_vmin is not None and d_vmin <= -VMIN_DIFF_KMH:
                alvo = cmp_.ref.v_min if cmp_.ref else None
                causas.append(f"entrou {abs(d_vmin):.0f} por hora mais devagar")
                dicas.append(f"tenta chegar a {alvo:.0f} no ápice" if alvo
                             else "carrega menos freio no meio da curva")
                numeros.append(f"V.min {cmp_.lap.v_min:.0f} km/h ({d_vmin:+.1f})")

            d_brake = cmp_.delta_braking_m
            if d_brake is not None:
                if d_brake <= -BRAKE_DIFF_M:
                    causas.append(f"freou {abs(d_brake):.0f} metros antes")
                    dicas.append("atrasa a freada")
                    numeros.append(f"freio {d_brake:+.0f} m")
                elif d_brake >= BRAKE_DIFF_M:
                    causas.append(f"freou {d_brake:.0f} metros depois")
                    dicas.append("antecipa a freada pra não perder o ápice")
                    numeros.append(f"freio {d_brake:+.0f} m")
                elif abs(d_brake) >= BRAKE_FINE_M:
                    # Ponto de freio praticamente certo: é ajuste fino, e vale
                    # dizer isso — o piloto para de mexer no que já está bom.
                    lado = "antecipa" if d_brake > 0 else "atrasa"
                    dicas.append(f"ponto de freio bom, só {lado} uns "
                                 f"{abs(d_brake):.0f} metros")
                    numeros.append(f"freio {d_brake:+.0f} m")

            d_thr = cmp_.delta_throttle_m
            if d_thr is not None and d_thr >= THROTTLE_DIFF_M:
                causas.append(f"acelerou {d_thr:.0f} metros depois")
                dicas.append("abre o gás mais cedo na saída")
                numeros.append(f"retomada {d_thr:+.0f} m")

            causas, dicas, numeros = self._corner_channels(
                cmp_, lap, ref, track_length, causas, dicas, numeros)

            perda = cmp_.delta_time
            if causas:
                texto = (f"{nome}: perdeu {perda:.2f} segundos, "
                         + " e ".join(causas[:2]) + ". "
                         + (dicas[0].capitalize() if dicas else ""))
            elif dicas:
                texto = f"{nome}: perdeu {perda:.2f} segundos. {dicas[0].capitalize()}"
            else:
                texto = f"{nome}: perdeu {perda:.2f} segundos"

            severidade = ATTENTION if perda >= 0.15 else INFO
            out.append(Advice(key=f"corner:{cmp_.corner.index}", severity=severidade,
                              text=texto.strip(), detail=" | ".join(numeros),
                              corner=cmp_.corner.index, kind="lap",
                              time_at_stake=perda))

        # Reforço positivo: a curva em que mais ganhou — e POR QUÊ, que é o que
        # o piloto precisa para repetir de propósito em vez de por acaso.
        ganhos = [c for c in (comparisons or [])
                  if c.delta_time is not None and c.delta_time < -MIN_CORNER_LOSS_S]
        if ganhos:
            melhor = min(ganhos, key=lambda c: c.delta_time)
            nome = melhor.corner.name or f"C{melhor.corner.index}"
            motivo, numeros = self._corner_praise(melhor, lap, ref, track_length)
            out.append(Advice(
                key=f"corner_ok:{melhor.corner.index}", severity=INFO,
                text=(f"{nome} tá ótima, ganhou {abs(melhor.delta_time):.2f} segundos"
                      + (f", {motivo}" if motivo else "") + ". Continua assim"),
                detail=" | ".join([_fmt_s(melhor.delta_time)] + numeros),
                corner=melhor.corner.index, kind="lap",
                time_at_stake=abs(melhor.delta_time)))
        return out

    def _corner_praise(self, cmp_, lap, ref, track_length):
        """O que explicou o GANHO: saída mais rápida ou linha mais colada."""
        if ref is None or not len(lap) or not track_length:
            return "", []

        fim_m = cmp_.corner.end * track_length
        v_saida = da.speed_at(lap, fim_m)
        v_saida_ref = da.speed_at(ref, fim_m)
        if (v_saida is not None and v_saida_ref is not None
                and v_saida - v_saida_ref >= VEXIT_DIFF_KMH):
            return ("saiu {:.0f} por hora mais rápido".format(v_saida - v_saida_ref),
                    [f"saída {v_saida:.0f} km/h (+{v_saida - v_saida_ref:.0f})"])

        desvio = da.line_deviation_m(lap, ref, cmp_.corner.start * track_length,
                                     fim_m)
        if desvio is not None and desvio < LINE_DEVIATION_M / 2:
            return "trajetória colada no ápice", [f"traçado {desvio:.1f} m da referência"]
        return "", []

    def _corner_channels(self, cmp_, lap, ref, track_length,
                         causas, dicas, numeros):
        """
        O que só os canais brutos contam sobre a curva: marcha no ápice,
        velocidade de saída, traçado e curva de gás cheio.

        Tudo aqui é opcional — ghost antigo não tem marcha nem coordenada, e
        nesse caso o conselho continua valendo com o que dá para medir.
        """
        if not len(lap) or not track_length:
            return causas, dicas, numeros

        i0, i1 = lap.corner_window(cmp_.corner, track_length)
        fim_m = cmp_.corner.end * track_length

        # --- Curva de gás cheio que virou freada ---
        if ref is not None and len(ref):
            j0, j1 = ref.corner_window(cmp_.corner, track_length)
            ref_flat = da.is_flat_out(ref, j0, j1)
            lap_flat = da.is_flat_out(lap, i0, i1)
            if ref_flat and lap_flat is False:
                causas.append("freou numa curva que é de gás cheio")
                dicas.append("essa curva é sem freio, dá pra passar inteiro")
                numeros.append("referência: sem freio")

        # --- Marcha no ápice ---
        if cmp_.lap.v_min_m is not None:
            marcha = da.gear_at(lap, cmp_.lap.v_min_m)
            marcha_ref = (da.gear_at(ref, cmp_.ref.v_min_m)
                          if ref is not None and cmp_.ref
                          and cmp_.ref.v_min_m is not None else None)
            if marcha and marcha_ref and marcha != marcha_ref:
                causas.append(f"passou de {marcha}ª onde a referência usa {marcha_ref}ª")
                dicas.append(f"tenta a {marcha_ref}ª nessa curva")
                numeros.append(f"marcha {marcha}ª x {marcha_ref}ª")

        # --- Velocidade de saída ---
        if ref is not None and len(ref):
            v_saida = da.speed_at(lap, fim_m)
            v_saida_ref = da.speed_at(ref, fim_m)
            if v_saida is not None and v_saida_ref is not None:
                d_saida = v_saida - v_saida_ref
                if d_saida <= -VEXIT_DIFF_KMH:
                    causas.append(f"saiu {abs(d_saida):.0f} por hora mais devagar")
                    dicas.append("a saída dessa curva se paga na reta inteira")
                    numeros.append(f"saída {v_saida:.0f} km/h ({d_saida:+.0f})")
                elif d_saida >= VEXIT_DIFF_KMH:
                    numeros.append(f"saída {v_saida:.0f} km/h ({d_saida:+.0f})")

        # --- Traçado ---
        if ref is not None:
            desvio = da.line_deviation_m(lap, ref, cmp_.corner.start * track_length,
                                         fim_m)
            if desvio is not None and desvio >= LINE_DEVIATION_M:
                causas.append(f"passou {desvio:.1f} metros fora da linha da referência")
                dicas.append("cola mais no ápice")
                numeros.append(f"traçado {desvio:.1f} m fora")
        return causas, dicas, numeros

    # -- pedais -----------------------------------------------------------

    def _pedal_advice(self, lap: "da.LapChannels") -> List[Advice]:
        """Como o freio e o acelerador foram usados na volta inteira."""
        out = []

        overlap = da.brake_throttle_overlap(lap)
        if overlap is not None and overlap >= OVERLAP_FRACTION:
            out.append(Advice(
                key="lap:overlap", severity=ATTENTION,
                text=("Você está pisando no freio e no acelerador ao mesmo tempo. "
                      "Solta um antes de pisar no outro"),
                detail=f"os dois pedais em {overlap * 100:.0f}% da frenagem",
                kind="lap", time_at_stake=0.25))

        freio = da.brake_release_report(lap)
        if freio is None:
            return out

        if freio.fraction >= BRAKE_JITTER_FRACTION:
            out.append(Advice(
                key="lap:brake_jitter", severity=ATTENTION,
                text=("Tá soltando o freio em degraus, o carro balança na entrada. "
                      "Alivia contínuo até o ápice"),
                detail=(f"repisada em {freio.jitter_zones} das {freio.zones} "
                        f"freadas da volta"),
                kind="lap", time_at_stake=0.2))

        # Largar o pedal de uma vez é erro diferente de repisar: tira carga da
        # dianteira bem na hora em que a curva precisa dela, e o carro para de
        # girar na entrada.
        abrupto = freio.abrupt_fraction
        if abrupto is not None and abrupto >= BRAKE_ABRUPT_FRACTION:
            out.append(Advice(
                key="lap:brake_abrupt", severity=ATTENTION,
                text=("Tá largando o freio de uma vez. Solta mais suave, "
                      "aliviando até o ápice, que o carro entra girando"),
                detail=(f"soltura seca em {freio.abrupt_zones} das {freio.zones} "
                        f"freadas (menos de "
                        f"{da.ABRUPT_RELEASE_S:.2f}s do freio forte ao pedal solto)"),
                kind="lap", time_at_stake=0.25))
        return out

    # -- volante ----------------------------------------------------------

    def _steering_advice(self, lap: "da.LapChannels",
                         ref: Optional["da.LapChannels"]) -> List[Advice]:
        """Suavidade de volante e subesterço, medidos contra a referência."""
        out = []

        sub = da.understeer_fraction(lap)
        if sub is not None and sub >= UNDERSTEER_FRACTION:
            out.append(Advice(
                key="lap:understeer", severity=ATTENTION,
                text=("Volante travado sem o carro virar: você está entrando com "
                      "ângulo demais. Abre a mão e deixa o carro girar"),
                detail=f"subesterço em {sub * 100:.0f}% do tempo de curva",
                kind="lap", time_at_stake=0.3))

        taxa = da.steering_rate(lap)
        taxa_ref = da.steering_rate(ref) if ref is not None else None
        if taxa is not None and taxa_ref:
            razao = taxa / taxa_ref
            if razao >= STEER_ROUGH_RATIO:
                out.append(Advice(
                    key="lap:steer_rough", severity=ATTENTION,
                    text=("Volante mais brusco que a referência. "
                          "Suaviza a entrada que o pneu agradece"),
                    detail=f"{taxa:.0f} contra {taxa_ref:.0f} graus por segundo",
                    kind="lap", time_at_stake=0.2))
            elif razao <= STEER_SMOOTH_RATIO and sub is not None and sub < UNDERSTEER_FRACTION:
                out.append(Advice(
                    key="lap:steer_smooth", severity=INFO,
                    text="Boa suavidade no volante essa volta",
                    detail=f"{taxa:.0f} contra {taxa_ref:.0f} graus por segundo",
                    kind="lap"))
        return out

    # -- motor ------------------------------------------------------------

    def _engine_advice(self, lap: "da.LapChannels", state) -> List[Advice]:
        """Onde as marchas estão sendo trocadas."""
        max_rpm = float(getattr(state, "max_rpm", 0.0) or 0.0) if state else 0.0
        rep = da.shift_report(lap, max_rpm)
        if rep is None:
            return []

        out = []
        if rep.early_fraction >= EARLY_SHIFT_FRACTION and rep.worst_early_rpm:
            out.append(Advice(
                key="lap:shift_early", severity=ATTENTION,
                text=(f"Trocando cedo demais, o motor cai fora da faixa. "
                      f"Estica até perto de {int(max_rpm / 100) * 100} giros"),
                detail=(f"{rep.early} de {rep.upshifts} trocas abaixo de "
                        f"{max_rpm * da.EARLY_SHIFT_FRACTION:.0f} rpm "
                        f"(pior: {rep.worst_early_rpm:.0f})"),
                kind="lap", time_at_stake=0.2))

        total = max(len(lap), 1)
        if rep.on_limiter / total >= LIMITER_FRACTION:
            out.append(Advice(
                key="lap:limiter", severity=INFO,
                text="Tá batendo no corte antes de trocar, sobe a marcha um pouco antes",
                detail=f"no corte em {rep.on_limiter / total * 100:.0f}% da volta",
                kind="lap", time_at_stake=0.1))
        return out

    # -- eletrônica -------------------------------------------------------

    def _electronics_advice(self, lap_telemetry: dict, comparisons: list) -> List[Advice]:
        """ABS e TC ao longo da volta: vício de pilotagem, não evento isolado."""
        out = []
        abs_arr = lap_telemetry.get("abs_intervention") or []
        tc_arr = lap_telemetry.get("tc_intervention") or []
        total = max(len(abs_arr), len(tc_arr))
        if total < 20:
            return out

        def onde_pior(arr):
            """Curva onde o canal teve o maior pico, se houver mapeamento."""
            if not arr or not comparisons:
                return None
            distances = lap_telemetry.get("distance") or []
            if len(distances) < len(arr):
                return None
            pico_i = max(range(len(arr)), key=lambda i: arr[i])
            d = distances[pico_i]
            for cmp_ in comparisons:
                lap_len = max(distances) or 1.0
                if cmp_.corner.start * lap_len <= d <= cmp_.corner.end * lap_len:
                    return cmp_.corner.name or f"C{cmp_.corner.index}"
            return None

        if abs_arr:
            forte = sum(1 for v in abs_arr if v >= ABS_LIVE_THRESHOLD)
            if forte / len(abs_arr) >= ABS_LAP_FRACTION:
                local = onde_pior(abs_arr)
                onde = f" O pior ponto foi em {local}." if local else ""
                out.append(Advice(
                    key="lap:abs", severity=ATTENTION,
                    text=("O ABS atuou muito nessa volta, você está freando além do limite. "
                          "Chega mais suave ao ponto de frenagem." + onde),
                    detail=f"ABS forte em {forte / len(abs_arr) * 100:.0f}% da volta",
                    kind="lap", time_at_stake=0.2))

        if tc_arr:
            forte = sum(1 for v in tc_arr if v >= TC_LIVE_THRESHOLD)
            if forte / len(tc_arr) >= TC_LAP_FRACTION:
                local = onde_pior(tc_arr)
                onde = f" Principalmente na saída de {local}." if local else ""
                out.append(Advice(
                    key="lap:tc", severity=ATTENTION,
                    text=("A tração cortou bastante nessa volta. "
                          "Abre o gás mais devagar nas saídas." + onde),
                    detail=f"TC forte em {forte / len(tc_arr) * 100:.0f}% da volta",
                    kind="lap", time_at_stake=0.2))
        return out

    # -- corrida ----------------------------------------------------------

    def _race_advice(self, state) -> List[Advice]:
        """Estratégia grosseira: o combustível fecha a corrida?"""
        out = []
        laps_left = getattr(state, "fuel_laps_remaining", 0.0)
        total_laps = getattr(state, "total_laps", 0) or 0
        done = getattr(state, "completed_laps", 0) or 0
        if total_laps > 0 and laps_left > 0:
            faltam = total_laps - done
            if faltam > 0 and laps_left < faltam:
                out.append(Advice(
                    key="lap:fuel_race", severity=ATTENTION,
                    text=("Combustível não fecha a corrida, "
                          "vai precisar economizar ou parar nos boxes"),
                    detail=f"{laps_left:.1f} voltas de autonomia para {faltam} restantes",
                    kind="lap", time_at_stake=0.5))
        return out

    # -----------------------------------------------------------------
    # Entre voltas
    # -----------------------------------------------------------------

    def register_lap_time(self, lap_ms: int) -> Optional[Advice]:
        """
        Acumula os tempos de volta e avisa quando a variação está grande.

        Consistência não aparece em nenhuma volta isolada — só na comparação
        entre as últimas. E só é notícia quando MUDA: repetir "ritmo consistente"
        a cada volta ensina o piloto a ignorar o engenheiro.
        """
        if lap_ms <= 30000:
            return None
        self._lap_times_ms.append(lap_ms)
        self._lap_times_ms = self._lap_times_ms[-CONSISTENCY_LAPS:]
        if len(self._lap_times_ms) < CONSISTENCY_LAPS:
            return None
        spread = (max(self._lap_times_ms) - min(self._lap_times_ms)) / 1000.0

        consistente = spread < CONSISTENCY_SPREAD_S
        if consistente == self._last_consistency:
            return None
        self._last_consistency = consistente

        if consistente:
            return Advice(
                key="lap:consistencia_ok", severity=INFO,
                text=f"Ritmo bem consistente nas últimas {CONSISTENCY_LAPS} voltas",
                detail=f"variação de {spread:.3f}s", kind="lap")
        return Advice(
            key="lap:consistencia", severity=ATTENTION,
            text=(f"Suas últimas {CONSISTENCY_LAPS} voltas variaram "
                  f"{spread:.1f} segundos. Busca repetir mais antes de tentar atacar"),
            detail=f"variação de {spread:.3f}s", kind="lap",
            time_at_stake=spread)

    def register_fuel(self, fuel_left: float) -> Optional[Advice]:
        """
        Consumo da volta que fechou, contra a média das anteriores.

        Consumo é resultado de pilotagem: quem estica marcha e abre o gás de
        supetão gasta mais. Vira conselho quando a volta destoa da média.
        """
        if fuel_left is None or fuel_left <= 0:
            return None
        anterior, self._last_fuel = self._last_fuel, fuel_left
        if anterior is None or fuel_left > anterior:
            return None                         # reabasteceu ou primeira volta
        gasto = anterior - fuel_left
        if gasto <= 0:
            return None

        media = (sum(self._fuel_history) / len(self._fuel_history)
                 if self._fuel_history else None)
        self._fuel_history.append(gasto)
        self._fuel_history = self._fuel_history[-CONSISTENCY_LAPS:]

        if media is None or len(self._fuel_history) < 3:
            return None
        if gasto < media * FUEL_HIGH_RATIO:
            return None
        return Advice(
            key="lap:fuel_high", severity=INFO,
            text=("Consumo alto nessa volta. Suaviza o acelerador na saída "
                  "das curvas que ele volta pra média"),
            detail=f"{gasto:.2f} L contra média de {media:.2f} L",
            kind="lap")
