"""
core/race_engineer.py — Engenheiro de pista (análise por regras)
===============================================================
Lê o que o app já mede e diz, em português, o que fazer com isso. Não usa
nenhum serviço externo nem modelo de linguagem: são regras sobre a telemetria,
o que significa custo zero, resposta instantânea e — o que mais importa num
painel de engenharia — número sempre exato, nunca inventado.

Duas famílias de regras:

  * `analyze_live(state, now)`  — o que precisa ser dito AGORA, com o carro na
    pista: roda travando, TC cortando, pneu fervendo, bandeira, combustível.
    Cada regra tem tempo de espera próprio, para não repetir a mesma frase a
    60 Hz.

  * `analyze_lap(...)` — o balanço da volta que acabou: onde o tempo foi
    perdido, por quê (freou antes? entrou devagar? demorou para acelerar?) e o
    que tentar na próxima. Sai ordenado pelo tempo em jogo, porque é isso que
    decide o que vale a pena ouvir primeiro.

Nada aqui depende de PyQt: dá para testar tudo com voltas sintéticas.
"""

import dataclasses
import re
from typing import List, Optional

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

#: Ignora ruído: curva com menos que isto de diferença não rende conselho.
MIN_CORNER_LOSS_S = 0.05
#: Quantas curvas comentar no balanço da volta.
MAX_CORNER_ADVICE = 3

#: Diferença de V_min que já explica perda de tempo (km/h).
VMIN_DIFF_KMH = 2.0
#: Diferença de ponto de frenagem que conta como "freou antes/depois" (m).
BRAKE_DIFF_M = 8.0
#: Diferença de ponto de retomada que conta como "acelerou depois" (m).
THROTTLE_DIFF_M = 10.0

#: Intervenção de ABS/TC (0..1) a partir da qual vale avisar.
ABS_LIVE_THRESHOLD = 0.55
TC_LIVE_THRESHOLD = 0.55
#: Fração da volta com ABS/TC atuando forte que caracteriza vício de pilotagem.
ABS_LAP_FRACTION = 0.04
TC_LAP_FRACTION = 0.06

#: Janelas de trabalho.
TYRE_HOT_C = 105.0
TYRE_CRITICAL_C = 115.0
TYRE_COLD_C = 70.0
#: Diferença núcleo interno x externo que indica câmber fora de ponto.
TYRE_CAMBER_DIFF_C = 8.0
BRAKE_HOT_C = 800.0
BRAKE_COLD_C = 200.0

#: Combustível: abaixo disto de voltas restantes é aviso.
FUEL_LAPS_WARNING = 2.0
FUEL_LAPS_CRITICAL = 1.0

#: Force Feedback saturando (clipping) — perde informação de aderência.
FFB_CLIP_LEVEL = 0.97

#: Rodas fora da pista que já valem aviso de corta-caminho.
TYRES_OUT_LIMIT = 3

#: Desvio entre as últimas voltas que caracteriza falta de consistência (s).
CONSISTENCY_SPREAD_S = 0.6
CONSISTENCY_LAPS = 4

#: Tempo de espera padrão de cada regra ao vivo (s).
DEFAULT_COOLDOWN_S = 12.0
COOLDOWNS_S = {
    "abs": 8.0, "tc": 8.0, "ffb": 30.0, "flag": 6.0, "penalty": 10.0,
    "cut": 6.0, "fuel": 25.0, "tyre_hot": 20.0, "tyre_cold": 40.0,
    "brake_hot": 20.0, "brake_cold": 40.0,
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

    def reset(self):
        self._last_said.clear()
        self._last_speak_at = -999.0
        self._lap_times_ms.clear()

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

        # --- Bandeiras e penalidades: prioridade máxima ---
        flag = (getattr(state, "flag", "") or "").upper()
        if flag in ("AMARELA", "AZUL", "PRETA", "PENALIDADE"):
            frases = {
                "AMARELA": "Bandeira amarela, atenção na pista",
                "AZUL": "Bandeira azul, carro mais rápido chegando",
                "PRETA": "Bandeira preta",
                "PENALIDADE": "Você tem penalidade",
            }
            add(f"flag:{flag}", CRITICAL, frases[flag])

        if getattr(state, "penalty_time", 0.0) > 0:
            add("penalty", CRITICAL, "Cumpra a penalidade",
                f"{state.penalty_time:.0f}s pendentes")

        if getattr(state, "tyres_out", 0) >= TYRES_OUT_LIMIT:
            add("cut", ATTENTION, "Cuidado com os limites da pista",
                f"{state.tyres_out} rodas fora")

        # --- Combustível ---
        laps_left = getattr(state, "fuel_laps_remaining", 0.0)
        if 0.0 < laps_left <= FUEL_LAPS_CRITICAL:
            add("fuel", CRITICAL, "Combustível acabando",
                f"{laps_left:.1f} volta restante")
        elif 0.0 < laps_left <= FUEL_LAPS_WARNING:
            add("fuel", ATTENTION, "Combustível baixo",
                f"{laps_left:.1f} voltas restantes")

        # --- Eletrônica: só quando a intervenção é forte ---
        if getattr(state, "abs_intervention", 0.0) >= ABS_LIVE_THRESHOLD:
            add("abs", ATTENTION, "Freio travando, alivie a pressão",
                f"ABS em {state.abs_intervention * 100:.0f}%")

        if getattr(state, "tc_intervention", 0.0) >= TC_LIVE_THRESHOLD:
            add("tc", ATTENTION, "Excesso de acelerador na saída",
                f"TC cortando {state.tc_intervention * 100:.0f}%")

        # --- Pneus e freios ---
        nomes = ("dianteiro esquerdo", "dianteiro direito",
                 "traseiro esquerdo", "traseiro direito")

        temps = list(getattr(state, "tyre_temp", []) or [])
        for i, temp in enumerate(temps[:4]):
            if temp >= TYRE_CRITICAL_C:
                add(f"tyre_hot:{i}", CRITICAL,
                    f"Pneu {nomes[i]} superaquecido",
                    f"{temp:.0f} °C")
            elif temp >= TYRE_HOT_C:
                add(f"tyre_hot:{i}", ATTENTION,
                    f"Pneu {nomes[i]} quente",
                    f"{temp:.0f} °C")
            elif 0 < temp <= TYRE_COLD_C:
                add(f"tyre_cold:{i}", INFO,
                    f"Pneu {nomes[i]} frio ainda",
                    f"{temp:.0f} °C")

        brakes = list(getattr(state, "brake_temp", []) or [])
        if brakes:
            hottest = max(brakes[:4])
            if hottest >= BRAKE_HOT_C:
                add("brake_hot", ATTENTION, "Freios superaquecendo",
                    f"{hottest:.0f} °C")
            elif 0 < max(brakes[:4]) <= BRAKE_COLD_C:
                add("brake_cold", INFO, "Freios frios, aqueça antes de atacar",
                    f"{hottest:.0f} °C")

        # --- Force Feedback saturando ---
        if getattr(state, "ffb_level", 0.0) >= FFB_CLIP_LEVEL:
            add("ffb", INFO, "Force feedback saturando, reduza o ganho",
                f"{state.ffb_level * 100:.0f}%")

        out.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
        return out

    # -----------------------------------------------------------------
    # Fim de volta
    # -----------------------------------------------------------------

    def analyze_lap(self, comparisons: list, lap_telemetry: dict = None,
                    state=None, lap_time_str: str = "",
                    lap_delta_s: float = None) -> List[Advice]:
        """
        Balanço da volta que fechou.

        `comparisons` é a lista de CornerComparison de core.corner_analysis —
        é dela que sai o "onde" e o "por quê". `lap_telemetry` serve para as
        regras de ABS/TC ao longo da volta, e `state` para pneus/freios/
        combustível (que não são gravados quadro a quadro).
        """
        out = []
        lap_telemetry = lap_telemetry or {}

        # --- Resumo da volta ---
        if lap_delta_s is not None and lap_time_str:
            # Duas casas na fala (três milésimos ditos em voz alta viram
            # ladainha); o detalhe do painel mantém a precisão cheia
            if lap_delta_s <= -0.001:
                out.append(Advice(
                    key="lap:melhor", severity=INFO,
                    text=f"Boa volta, {abs(lap_delta_s):.2f} segundos melhor que a referência",
                    detail=f"{lap_time_str} ({_fmt_s(lap_delta_s)})",
                    kind="lap", time_at_stake=abs(lap_delta_s)))
            elif lap_delta_s > 0.001:
                out.append(Advice(
                    key="lap:pior", severity=INFO,
                    text=f"Volta {lap_delta_s:.2f} segundos acima da referência",
                    detail=f"{lap_time_str} ({_fmt_s(lap_delta_s)})",
                    kind="lap", time_at_stake=lap_delta_s))

        out.extend(self._corner_advice(comparisons))
        out.extend(self._electronics_advice(lap_telemetry, comparisons))
        if state is not None:
            out.extend(self._car_advice(state))

        # Ordena por severidade e, dentro dela, pelo tempo em jogo
        out.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9),
                                -a.time_at_stake))
        return out

    def _corner_advice(self, comparisons: list) -> List[Advice]:
        """Onde o tempo foi perdido e o que explica a perda."""
        perdas = [c for c in (comparisons or [])
                  if c.delta_time is not None and c.delta_time > MIN_CORNER_LOSS_S]
        perdas.sort(key=lambda c: -c.delta_time)

        out = []
        for cmp_ in perdas[:MAX_CORNER_ADVICE]:
            nome = cmp_.corner.name or f"C{cmp_.corner.index}"
            causas, dicas, numeros = [], [], []

            d_vmin = cmp_.delta_v_min
            if d_vmin is not None and d_vmin <= -VMIN_DIFF_KMH:
                causas.append(f"passou {abs(d_vmin):.0f} por hora mais devagar no ápice")
                dicas.append("carregue menos freio no meio da curva")
                numeros.append(f"V.min {cmp_.lap.v_min:.0f} km/h ({d_vmin:+.1f})")

            d_brake = cmp_.delta_braking_m
            if d_brake is not None:
                if d_brake <= -BRAKE_DIFF_M:
                    causas.append(f"freou {abs(d_brake):.0f} metros antes")
                    dicas.append("atrase a freada")
                    numeros.append(f"freio {d_brake:+.0f} m")
                elif d_brake >= BRAKE_DIFF_M:
                    causas.append(f"freou {d_brake:.0f} metros depois")
                    dicas.append("antecipe a freada para não perder o ápice")
                    numeros.append(f"freio {d_brake:+.0f} m")

            d_thr = cmp_.delta_throttle_m
            if d_thr is not None and d_thr >= THROTTLE_DIFF_M:
                causas.append(f"acelerou {d_thr:.0f} metros depois")
                dicas.append("abra o acelerador mais cedo na saída")
                numeros.append(f"retomada {d_thr:+.0f} m")

            perda = cmp_.delta_time
            if causas:
                texto = (f"{nome}: perdeu {perda:.2f} segundos, "
                         + " e ".join(causas[:2]) + ". " + dicas[0].capitalize())
            else:
                texto = f"{nome}: perdeu {perda:.2f} segundos"

            severidade = ATTENTION if perda >= 0.15 else INFO
            out.append(Advice(key=f"corner:{cmp_.corner.index}", severity=severidade,
                              text=texto, detail=" | ".join(numeros),
                              corner=cmp_.corner.index, kind="lap",
                              time_at_stake=perda))

        # Reforço positivo: a curva em que mais ganhou
        ganhos = [c for c in (comparisons or [])
                  if c.delta_time is not None and c.delta_time < -MIN_CORNER_LOSS_S]
        if ganhos:
            melhor = min(ganhos, key=lambda c: c.delta_time)
            nome = melhor.corner.name or f"C{melhor.corner.index}"
            out.append(Advice(
                key=f"corner_ok:{melhor.corner.index}", severity=INFO,
                text=f"{nome} foi bem, {abs(melhor.delta_time):.2f} segundos ganhos. Mantenha",
                detail=_fmt_s(melhor.delta_time),
                corner=melhor.corner.index, kind="lap",
                time_at_stake=abs(melhor.delta_time)))
        return out

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
                    text=("Você está travando a roda nas freadas, "
                          "aplique o freio de forma mais progressiva." + onde),
                    detail=f"ABS forte em {forte / len(abs_arr) * 100:.0f}% da volta",
                    kind="lap", time_at_stake=0.2))

        if tc_arr:
            forte = sum(1 for v in tc_arr if v >= TC_LIVE_THRESHOLD)
            if forte / len(tc_arr) >= TC_LAP_FRACTION:
                local = onde_pior(tc_arr)
                onde = f" Principalmente na saída de {local}." if local else ""
                out.append(Advice(
                    key="lap:tc", severity=ATTENTION,
                    text=("O controle de tração está cortando muito, "
                          "abra o acelerador mais devagar." + onde),
                    detail=f"TC forte em {forte / len(tc_arr) * 100:.0f}% da volta",
                    kind="lap", time_at_stake=0.2))
        return out

    def _car_advice(self, state) -> List[Advice]:
        """Pneus, câmber e combustível no fechamento da volta."""
        out = []
        nomes = ("dianteiro esquerdo", "dianteiro direito",
                 "traseiro esquerdo", "traseiro direito")

        internos = list(getattr(state, "tyre_temp_inner", []) or [])
        externos = list(getattr(state, "tyre_temp_outer", []) or [])
        for i in range(min(4, len(internos), len(externos))):
            diff = internos[i] - externos[i]
            if abs(diff) >= TYRE_CAMBER_DIFF_C:
                lado = "interna" if diff > 0 else "externa"
                ajuste = "reduza" if diff > 0 else "aumente"
                out.append(Advice(
                    key=f"lap:camber:{i}", severity=INFO,
                    text=(f"Pneu {nomes[i]} trabalhando mais na banda {lado}: "
                          f"{ajuste} o câmber negativo"),
                    detail=f"interna {internos[i]:.0f} °C x externa {externos[i]:.0f} °C",
                    kind="lap"))

        laps_left = getattr(state, "fuel_laps_remaining", 0.0)
        total_laps = getattr(state, "total_laps", 0) or 0
        done = getattr(state, "completed_laps", 0) or 0
        if total_laps > 0 and laps_left > 0:
            faltam = total_laps - done
            if faltam > 0 and laps_left < faltam:
                out.append(Advice(
                    key="lap:fuel_race", severity=ATTENTION,
                    text=("O combustível não fecha a corrida, "
                          "economize ou programe a parada"),
                    detail=f"{laps_left:.1f} voltas de autonomia para {faltam} restantes",
                    kind="lap", time_at_stake=0.5))
        return out

    # -----------------------------------------------------------------
    # Consistência entre voltas
    # -----------------------------------------------------------------

    def register_lap_time(self, lap_ms: int) -> Optional[Advice]:
        """
        Acumula os tempos de volta e avisa quando a variação está grande.

        Consistência não aparece em nenhuma volta isolada — só na comparação
        entre as últimas.
        """
        if lap_ms <= 30000:
            return None
        self._lap_times_ms.append(lap_ms)
        self._lap_times_ms = self._lap_times_ms[-CONSISTENCY_LAPS:]
        if len(self._lap_times_ms) < CONSISTENCY_LAPS:
            return None
        spread = (max(self._lap_times_ms) - min(self._lap_times_ms)) / 1000.0
        if spread < CONSISTENCY_SPREAD_S:
            return Advice(
                key="lap:consistencia_ok", severity=INFO,
                text=f"Ritmo consistente nas últimas {CONSISTENCY_LAPS} voltas",
                detail=f"variação de {spread:.3f}s", kind="lap")
        return Advice(
            key="lap:consistencia", severity=ATTENTION,
            text=(f"Suas últimas {CONSISTENCY_LAPS} voltas variaram "
                  f"{spread:.1f} segundos. Busque repetir a volta antes de atacar"),
            detail=f"variação de {spread:.3f}s", kind="lap",
            time_at_stake=spread)
