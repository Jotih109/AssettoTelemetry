"""
tests/weekend_sim.py — Simulador de fim de semana de corrida
=============================================================

Gera telemetria sintética, quadro a quadro, no MESMO formato que o provider do
Assetto Corsa entrega — inclusive as manhas que o app teve que aprender a
tratar:

  * `distance_traveled` é a distância DENTRO da volta (0..comprimento), e
    volta a zero ao cruzar a linha;
  * o tempo oficial da volta (`last_time`) só aparece alguns quadros DEPOIS do
    cruzamento, nunca no mesmo;
  * os primeiros quadros de uma sessão já trazem o `last_time` de antes;
  * no box e no pit lane as quatro rodas contam como fora da pista.

Não é um modelo de física: é um gerador de perfil de velocidade por distância,
construído a partir do mapa de curvas REAL do repositório. O que importa é que
as curvas do sinal batam com as curvas do mapa, para que a análise curva a
curva, o engenheiro e o coach ao vivo tenham material de verdade para medir.

O piloto é parametrizável ([DriverStyle]): dá para mandá-lo frear 20 metros
cedo na Ferradura, chegar devagar no ápice do Pinheirinho e melhorar entre uma
sessão e outra — que é como se testa um coach.

Uso::

    from tests.weekend_sim import DriverStyle, Session, TRACK_NAME
    sess = Session("Practice", driver=DriverStyle())
    for state in sess.lap_frames(lap_number=1):
        session_manager.process_state(state)
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses
from typing import Dict, List, Optional

from core.models import TelemetryState
from core import corner_analysis as ca

# ---------------------------------------------------------------------------
# A pista: o mapa manual que já vem no repositório
# ---------------------------------------------------------------------------

_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "track_maps",
                         "autodromo_jose_carlos_pace_grand_prix_mock.json")

with open(_MAP_PATH, encoding="utf-8") as _f:
    _MAP_DATA = json.load(_f)

TRACK_NAME = _MAP_DATA["track"]
TRACK_LENGTH = float(_MAP_DATA["track_length"])
CAR_NAME = "Porsche 992 GT3 Cup"

CORNER_MAP = ca.parse_corner_map(_MAP_DATA, TRACK_LENGTH)
CORNERS: List[ca.Corner] = CORNER_MAP.corners

#: Quadros por segundo, como a engine real
HZ = 60.0
DT = 1.0 / HZ

#: Desaceleração de frenagem (m/s²). Um GT3 em pneu slick faz mais que isto,
#: mas o número só precisa ser coerente com o resto do perfil.
BRAKE_DECEL = 12.0
#: Aceleração de saída de curva (m/s²).
EXIT_ACCEL = 6.0

#: Consumo por volta (L) e capacidade do tanque
FUEL_PER_LAP = 2.6
FUEL_CAPACITY = 95.0


# ---------------------------------------------------------------------------
# O piloto
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DriverStyle:
    """
    Como este piloto anda. Tudo é relativo ao ideal, para os testes lerem bem.

    `brake_early_m` e `vmin_deficit_kmh` são mapas *índice da curva -> erro*.
    Uma curva ausente é feita no ponto: é assim que o teste cria um piloto que
    erra a Ferradura e acerta o resto, e depois verifica que o coach falou da
    Ferradura e ficou calado sobre as outras.
    """
    v_max_kmh: float = 285.0
    #: Velocidade de ápice "ideal" por curva; ausente = usa o padrão
    apex_kmh: Dict[int, float] = dataclasses.field(default_factory=dict)
    apex_default_kmh: float = 115.0
    #: Metros a MAIS de freada (positivo = freia cedo, perde tempo)
    brake_early_m: Dict[int, float] = dataclasses.field(default_factory=dict)
    #: km/h a MENOS no ápice (positivo = entra devagar, perde tempo)
    vmin_deficit_kmh: Dict[int, float] = dataclasses.field(default_factory=dict)
    #: Metros a MAIS até abrir o gás na saída (positivo = demora, perde tempo)
    throttle_late_m: Dict[int, float] = dataclasses.field(default_factory=dict)
    #: Ruído determinístico de volta para volta (s de variação no ritmo)
    inconsistency_s: float = 0.0

    def copy(self) -> "DriverStyle":
        return DriverStyle(
            v_max_kmh=self.v_max_kmh,
            apex_kmh=dict(self.apex_kmh),
            apex_default_kmh=self.apex_default_kmh,
            brake_early_m=dict(self.brake_early_m),
            vmin_deficit_kmh=dict(self.vmin_deficit_kmh),
            throttle_late_m=dict(self.throttle_late_m),
            inconsistency_s=self.inconsistency_s,
        )

    def for_lap(self, lap_index: int) -> "DriverStyle":
        """
        O mesmo piloto nesta volta específica, com a variação dele.

        Um piloto real não repete a volta: ele varia — e varia MAIS justamente
        onde tem menos confiança. É isso que dá ao coach material para
        aprender: numa volta ele acerta a Ferradura, na outra freia 20 metros
        antes, e a média conta a história.

        A variação é pseudoaleatória mas DETERMINÍSTICA (derivada do número da
        volta), para o teste dar sempre o mesmo resultado.
        """
        if self.inconsistency_s <= 0:
            return self
        novo = self.copy()
        for curva in set(self.brake_early_m) | set(self.vmin_deficit_kmh):
            # Sequência estável e sem correlação óbvia entre curvas
            semente = (lap_index * 2654435761 + curva * 40503) % 1000
            fator = 1.0 + self.inconsistency_s * ((semente / 500.0) - 1.0)
            if curva in novo.brake_early_m:
                novo.brake_early_m[curva] = self.brake_early_m[curva] * fator
            if curva in novo.vmin_deficit_kmh:
                novo.vmin_deficit_kmh[curva] = self.vmin_deficit_kmh[curva] * fator
        return novo

    def improved(self, factor: float = 0.5) -> "DriverStyle":
        """
        O mesmo piloto, com os erros reduzidos — é o que acontece quando ele
        ouve o coach. `factor=0` corrige tudo, `factor=1` não muda nada.
        """
        novo = self.copy()
        novo.brake_early_m = {k: v * factor for k, v in self.brake_early_m.items()}
        novo.vmin_deficit_kmh = {k: v * factor
                                 for k, v in self.vmin_deficit_kmh.items()}
        novo.throttle_late_m = {k: v * factor
                                for k, v in self.throttle_late_m.items()}
        return novo

    # -- geometria da curva para este piloto ---------------------------------

    def apex_speed(self, corner: ca.Corner) -> float:
        base = self.apex_kmh.get(corner.index, self.apex_default_kmh)
        return max(45.0, base - self.vmin_deficit_kmh.get(corner.index, 0.0))

    def brake_point_m(self, corner: ca.Corner, v_entry_kmh: float) -> float:
        """Onde este piloto pisa no freio para esta curva."""
        apex_m = _apex_m(corner)
        v0 = v_entry_kmh / 3.6
        v1 = self.apex_speed(corner) / 3.6
        ideal = max(20.0, (v0 * v0 - v1 * v1) / (2.0 * BRAKE_DECEL))
        return apex_m - ideal - self.brake_early_m.get(corner.index, 0.0)

    def throttle_point_m(self, corner: ca.Corner) -> float:
        return _apex_m(corner) + 15.0 + self.throttle_late_m.get(corner.index, 0.0)


def _apex_m(corner: ca.Corner) -> float:
    return (corner.start + corner.end) / 2.0 * TRACK_LENGTH


# ---------------------------------------------------------------------------
# Perfil de velocidade por distância
# ---------------------------------------------------------------------------

class SpeedProfile:
    """
    Velocidade alvo (km/h) em função da distância na volta.

    Montado em DOIS PASSOS. No primeiro, a freada de cada curva é calculada
    supondo que o carro chega nela na velocidade máxima. Só que entre duas
    curvas coladas — a Descida do Lago e a Ferradura, aqui — ele nunca chega:
    a freada calculada assim começava antes do fim da curva anterior, e a
    análise curva a curva (que procura a freada só dentro do trecho da curva)
    não a encontrava. O segundo passo refaz a conta com a velocidade que o
    carro REALMENTE tem chegando, e o traçado passa a fechar.
    """

    def __init__(self, style: DriverStyle, pace_scale: float = 1.0):
        self.style = style
        #: <1 deixa o piloto mais rápido em tudo (pneu novo, tanque leve)
        self.pace_scale = pace_scale
        self._zones = self._build(None)
        entradas = {}
        for z in self._zones:
            # Velocidade um pouco antes da freada estimada no primeiro passo
            entradas[z["corner"].index] = self._speed_on(
                self._zones, max(0.0, z["brake_m"] - 10.0))
        self._zones = self._build(entradas)

    @property
    def v_max(self) -> float:
        return self.style.v_max_kmh * self.pace_scale

    def _build(self, entry_speeds):
        zones = []
        for corner in CORNERS:
            apex_m = _apex_m(corner)
            vmin = self.style.apex_speed(corner) * self.pace_scale
            v_entrada = (entry_speeds or {}).get(corner.index, self.style.v_max_kmh)
            v_entrada = max(vmin + 10.0, v_entrada)
            brake_m = self.style.brake_point_m(corner, v_entrada)
            thr_m = self.style.throttle_point_m(corner)
            v0, v1 = vmin / 3.6, self.style.v_max_kmh / 3.6
            accel_len = max(30.0, (v1 * v1 - v0 * v0) / (2.0 * EXIT_ACCEL))
            zones.append({
                "corner": corner, "apex_m": apex_m, "vmin": vmin,
                "brake_m": brake_m, "thr_m": thr_m,
                "exit_end_m": thr_m + accel_len,
            })
        return zones

    def _speed_on(self, zones, d: float) -> float:
        """Velocidade num conjunto de zonas — usado no passo de convergência."""
        v = self.v_max
        for z in zones:
            if z["brake_m"] <= d < z["apex_m"]:
                frac = (d - z["brake_m"]) / max(1e-6, z["apex_m"] - z["brake_m"])
                v = min(v, self.v_max + (z["vmin"] - self.v_max) * frac)
            elif z["apex_m"] <= d <= z["thr_m"]:
                v = min(v, z["vmin"])
            elif z["thr_m"] < d <= z["exit_end_m"]:
                frac = (d - z["thr_m"]) / max(1e-6, z["exit_end_m"] - z["thr_m"])
                v = min(v, z["vmin"] + (self.v_max - z["vmin"]) * frac)
        return max(40.0, v)

    def speed_at(self, d: float) -> float:
        """
        Três fases por curva: freada até o ápice, PLATÔ no ápice, e saída.

        O platô entre o ápice e o ponto de retomada é o que dá um ponto de
        retomada bem definido — sem ele, o gás saltaria de zero a 100% no
        mesmo metro em que o freio é solto, e "demorar a abrir o gás" não teria
        como ser medido nem ensinado.
        """
        return self._speed_on(self._zones, d)

    def zone_at(self, d: float) -> Optional[dict]:
        for z in self._zones:
            if z["brake_m"] <= d <= z["exit_end_m"]:
                return z
        return None

    def pedals_at(self, d: float):
        """
        `(gas, brake)` deduzidos da INCLINAÇÃO do perfil, não de qual curva
        manda no trecho.

        A versão por zona errava onde duas curvas são vizinhas: a saída da
        Descida do Lago engolia a freada da Ferradura, e a análise não achava
        ponto de frenagem nenhum lá. Olhando a derivada, quem está freando é
        quem está perdendo velocidade — independente de quantas curvas
        disputam aquele metro de pista.
        """
        v = self.speed_at(d)
        dv = self.speed_at(d + 1.0) - v
        if dv < -0.10:
            return 0.0, 0.92
        if dv > 0.10:
            return 1.0, 0.0
        if v >= self.v_max * 0.985:
            return 1.0, 0.0          # reta, pé embaixo
        return 0.45, 0.0             # platô do ápice: nem freio nem retomada

    def g_lat_at(self, d: float) -> float:
        """Força G lateral, com o sinal do lado da curva."""
        z = self.zone_at(d)
        if z is None:
            return 0.0
        corner = z["corner"]
        inicio, fim = corner.start * TRACK_LENGTH, corner.end * TRACK_LENGTH
        if not (inicio <= d <= fim):
            return 0.0
        meio = (inicio + fim) / 2.0
        largura = max(1.0, (fim - inicio) / 2.0)
        forma = max(0.0, 1.0 - abs(d - meio) / largura)
        sinal = -1.0 if (corner.direction or "L").upper() == "L" else 1.0
        return sinal * 1.9 * forma


# ---------------------------------------------------------------------------
# Uma sessão
# ---------------------------------------------------------------------------

#: Quantos quadros depois do cruzamento o jogo publica o tempo oficial.
#: O AC leva algumas dezenas de milissegundos; o app tem código específico
#: para isso e este simulador precisa exercitá-lo.
LAST_TIME_DELAY_FRAMES = 4


def _fmt_time(seconds: float) -> str:
    if seconds <= 0:
        return ""
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:06.3f}"


@dataclasses.dataclass
class LapPlan:
    """O que acontece nesta volta além de andar."""
    in_lap: bool = False           # volta de retorno aos boxes
    out_lap: bool = False          # volta de saída dos boxes
    cut_corner: Optional[int] = None   # sai da pista nesta curva
    flag: str = ""
    penalty_s: float = 0.0
    damage: float = 0.0
    tyre_temp: Optional[float] = None
    track_temp: Optional[float] = None


class Session:
    """
    Uma sessão do fim de semana. Mantém o relógio, o combustível e os tempos.

    Emite `TelemetryState` exatamente como a engine faria, para o teste poder
    passar tudo por `SessionManager.process_state` sem adaptador nenhum.
    """

    def __init__(self, session_type: str, driver: DriverStyle,
                 total_laps: int = 0, fuel: float = 60.0,
                 track_temp: float = 32.0, ambient: float = 24.0,
                 prior_last_time: str = "", sample_hz: float = HZ):
        #: A física é sempre integrada a 60 Hz; `sample_hz` só decide quantos
        #: desses quadros são ENTREGUES. Um fim de semana inteiro a 60 Hz são
        #: centenas de milhares de quadros, e a análise não fica melhor por
        #: isso: a 30 Hz, a 290 km/h, cada amostra ainda é um ponto a cada
        #: 2,7 m — bem abaixo dos 8 m que separam "freou antes" de "no ponto".
        self.sample_hz = max(1.0, min(HZ, float(sample_hz)))
        self._sample_every = max(1, int(round(HZ / self.sample_hz)))
        self.session_type = session_type
        self.driver = driver
        self.total_laps = total_laps
        self.fuel = fuel
        self.track_temp = track_temp
        self.ambient = ambient

        self.lap_number = 1
        self.completed = 0
        #: O jogo já traz o tempo da última volta de ANTES do app abrir — é o
        #: que criava uma "volta fantasma" no histórico.
        self.last_time = prior_last_time
        self.best_time = ""
        self.best_ms = 0
        self.lap_times: List[float] = []
        self._frames_since_line = 999
        self._pending_last_time = ""
        self.tyre_temp = 88.0
        self.damage = 0.0

    # -- geração de uma volta ------------------------------------------------

    def lap_frames(self, plan: LapPlan = None, pace_scale: float = 1.0):
        """
        Gera os quadros de UMA volta inteira, terminando com os primeiros
        quadros da volta seguinte (é lá que o tempo oficial aparece).
        """
        plan = plan or LapPlan()
        estilo = self.driver.for_lap(self.lap_number)
        profile = SpeedProfile(estilo, pace_scale=pace_scale)

        d = 0.0
        t = 0.0
        frames = []
        while d < TRACK_LENGTH:
            v_alvo = profile.speed_at(d)
            if plan.out_lap or plan.in_lap:
                v_alvo *= 0.80        # volta de saída/retorno é mais lenta
            v_ms = v_alvo / 3.6
            d += v_ms * DT
            t += DT
            frames.append((min(d, TRACK_LENGTH), t, v_alvo, profile))

        lap_time = t
        self.lap_times.append(lap_time)

        # --- Emite os quadros da volta ---
        # O último quadro sai sempre: é ele que fecha a volta na linha.
        for i, (d_i, t_i, v_i, prof) in enumerate(frames):
            if i % self._sample_every and i != len(frames) - 1:
                continue
            yield self._state_at(d_i, t_i, v_i, prof, plan, lap_time)

        # --- Cruzamento da linha: a volta nova começa e o tempo oficial só
        #     aparece alguns quadros depois ---
        self.completed += 1
        self.lap_number += 1
        self._pending_last_time = _fmt_time(lap_time)
        lap_ms = int(round(lap_time * 1000))
        novo_recorde = self.best_ms == 0 or lap_ms < self.best_ms
        # Volta de entrada/saída de box e volta cortada não entram como recorde
        valida = not (plan.in_lap or plan.out_lap or plan.cut_corner)
        if novo_recorde and valida:
            self.best_ms = lap_ms

        self.fuel = max(0.0, self.fuel - FUEL_PER_LAP)
        self.tyre_temp = min(112.0, self.tyre_temp + 1.2)

        for i in range(LAST_TIME_DELAY_FRAMES + 2):
            if i == LAST_TIME_DELAY_FRAMES:
                self.last_time = self._pending_last_time
                if valida and self.best_ms == lap_ms:
                    self.best_time = self._pending_last_time
            d_i = i * (v_i / 3.6) * DT
            yield self._state_at(d_i, i * DT, v_i, profile, LapPlan(), 0.0,
                                 new_lap=True)

    def _state_at(self, d: float, t: float, v_kmh: float,
                  profile: SpeedProfile, plan: LapPlan, lap_time: float,
                  new_lap: bool = False) -> TelemetryState:
        st = TelemetryState(is_connected=True)
        st.track_name = TRACK_NAME
        st.car_name = CAR_NAME
        st.track_length = TRACK_LENGTH
        st.session_type = self.session_type
        st.total_laps = self.total_laps
        st.completed_laps = self.completed
        st.lap_number = self.lap_number

        pos = min(1.0, d / TRACK_LENGTH)
        st.distance_traveled = d
        st.track_position = pos
        st.speed_kmh = v_kmh
        st.current_time = _fmt_time(t) if t > 0 else "0:00.000"
        st.last_time = self.last_time
        st.best_time = self.best_time

        gas, brake = profile.pedals_at(d)
        st.gas, st.brake = gas, brake
        st.g_lat = profile.g_lat_at(d)
        st.g_lon = -1.2 if brake > 0.1 else (0.6 if gas > 0.9 else 0.0)
        st.steer_angle = st.g_lat * 45.0
        st.steer_norm = max(-1.0, min(1.0, st.g_lat / 2.0))

        st.gear = _gear_for(v_kmh)
        st.rpm = int(4200 + (v_kmh % 60) * 55)
        st.max_rpm = 8500

        st.sector_index = 0 if pos < 1 / 3 else (1 if pos < 2 / 3 else 2)
        st.sector_count = 3

        # Traçado: um oval fechado, suficiente para o mapa e o desvio de linha
        ang = 2.0 * math.pi * pos
        st.car_x = 600.0 * math.cos(ang)
        st.car_z = 380.0 * math.sin(ang)

        st.fuel = self.fuel
        st.fuel_capacity = FUEL_CAPACITY
        temp = plan.tyre_temp if plan.tyre_temp is not None else self.tyre_temp
        st.tyre_temp = [temp] * 4
        st.tyre_temp_inner = [temp + 3] * 4
        st.tyre_temp_middle = [temp] * 4
        st.tyre_temp_outer = [temp - 4] * 4
        st.tyre_pressure = [27.4] * 4
        st.tyre_wear = [98.0] * 4
        st.brake_temp = [420.0 if brake > 0.1 else 320.0] * 4
        st.brake_bias = 0.58

        st.track_temp = plan.track_temp if plan.track_temp is not None else self.track_temp
        st.ambient_temp = self.ambient
        st.surface_grip = 0.99
        st.wind_speed = 2.0

        st.has_abs = True
        st.has_tc = True
        st.abs_intervention = 0.3 if brake > 0.5 else 0.0
        st.tc_intervention = 0.25 if (gas > 0.9 and abs(st.g_lat) > 1.0) else 0.0
        st.abs_active = st.abs_intervention > 0.02
        st.tc_active = st.tc_intervention > 0.02

        st.flag = plan.flag
        st.penalty_time = plan.penalty_s
        st.car_damage = max(self.damage, plan.damage)
        st.car_damage_parts = [st.car_damage, 0.0, 0.0, 0.0]

        st.in_pit_lane = bool((plan.in_lap and pos > 0.95)
                              or (plan.out_lap and pos < 0.05))
        st.in_pit = bool(plan.in_lap and pos > 0.99)

        # Corte de pista: quatro rodas fora dentro da curva escolhida
        st.tyres_out = 0
        if plan.cut_corner:
            corner = next((c for c in CORNERS if c.index == plan.cut_corner), None)
            if corner and corner.start <= pos <= corner.end:
                st.tyres_out = 4
        if st.in_pit or st.in_pit_lane:
            st.tyres_out = 4     # no box o jogo reporta as quatro fora

        return st


def _gear_for(v_kmh: float) -> int:
    """Marcha bruta do AC: 0=ré, 1=neutro, 2=primeira."""
    for i, limite in enumerate((70, 110, 150, 195, 240, 999)):
        if v_kmh < limite:
            return i + 2
    return 8


# ---------------------------------------------------------------------------
# O fim de semana inteiro
# ---------------------------------------------------------------------------

def rookie_style() -> DriverStyle:
    """
    O piloto de sexta de manhã: freia cedo demais em três curvas e chega
    devagar no ápice de duas. Erros grandes o bastante para o coach ver.
    """
    return DriverStyle(
        brake_early_m={5: 24.0, 7: 18.0, 1: 12.0},   # Ferradura, Pinheirinho, S
        vmin_deficit_kmh={5: 7.0, 3: 5.0},           # Ferradura, Curva do Sol
        throttle_late_m={7: 16.0},                   # Pinheirinho
        # Ele varia onde tem menos confiança — é o que dá material ao coach
        inconsistency_s=0.35,
    )


def reference_style() -> DriverStyle:
    """O piloto de referência: faz tudo no ponto. É o alvo."""
    return DriverStyle()
