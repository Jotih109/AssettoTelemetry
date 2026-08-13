"""
core/driving_analysis.py — Medidas de PILOTAGEM a partir dos canais da volta
============================================================================
Esta camada MEDE; quem decide o que falar é o `core/race_engineer.py`. A
separação importa porque as duas coisas erram de jeitos diferentes: uma medida
errada é um número que não bate com o gráfico, uma regra errada é um conselho
ruim dito com confiança.

Entra o mesmo dicionário de arrays que o SessionManager grava por volta
(`times`, `distance`, `speed`, `gas`, `brake`, `rpm`, `gear`, `steer`,
`g_lat`, `car_x`, `car_z`) e saem as medidas que um piloto consegue AGIR em
cima na volta seguinte:

    * pedais    — sobreposição freio/acelerador, trepidação ao soltar, pico
    * volante   — suavidade e trechos de volante travado sem o carro virar
    * motor     — trocas cedo demais, batidas no corte, marcha no ápice
    * traçado   — desvio em metros contra a linha da volta de referência

Toda medida devolve `None` quando não há dado suficiente. Isso é regra da casa:
é melhor o engenheiro ficar calado do que opinar sobre um canal que o ghost
antigo nem gravou.
"""

import bisect
import math
from typing import List, Optional

# ---------------------------------------------------------------------------
# Limiares das medidas
# ---------------------------------------------------------------------------

#: Pedal acima disto conta como "pisado".
PEDAL_ON = 0.15
#: Amostras mínimas para uma medida valer alguma coisa.
MIN_SAMPLES = 30
#: Repisada de freio, em fração do curso do pedal, que conta como degrau.
#: Menos que isto é modulação normal e ruído de célula de carga.
BRAKE_STEP = 0.12
#: Freadas mínimas na volta para a medida de degrau valer alguma coisa.
MIN_BRAKE_ZONES = 4
#: Pressão a partir da qual a freada é "de verdade" (0..1).
BRAKE_HARD = 0.50
#: Sair da freada forte para o pedal solto em menos que isto é largar de uma
#: vez. Uma soltura progressiva de entrada de curva leva meio segundo ou mais;
#: 0.15 s é o pé pulando fora do pedal.
ABRUPT_RELEASE_S = 0.15
#: Abaixo disto o carro está parado o bastante para os canais não dizerem nada.
MIN_SPEED_KMH = 40.0
#: Volante acima desta fração do máximo da volta = "muito volante".
STEER_HIGH_FRACTION = 0.6
#: G lateral abaixo desta fração do máximo = "o carro não está virando".
GLAT_LOW_FRACTION = 0.55
#: Fração do RPM máximo abaixo da qual a troca foi cedo demais.
EARLY_SHIFT_FRACTION = 0.90
#: Fração do RPM máximo a partir da qual está batendo no corte.
LIMITER_FRACTION = 0.985


def _floats(seq) -> List[float]:
    """
    Converte o canal para float, trocando o que não for número por 0.0.

    Substituir em vez de descartar é deliberado: os canais são lidos POR
    ÍNDICE (a amostra 40 do freio tem que ser a mesma amostra 40 da
    velocidade), e remover um buraco deslocaria todo o resto do canal — um
    erro silencioso e muito pior que um zero isolado.
    """
    out = []
    for v in (seq or []):
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _mean(values) -> Optional[float]:
    values = list(values)
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# Canais
# ---------------------------------------------------------------------------

class LapChannels:
    """
    Os arrays de uma volta, alinhados por índice.

    `distance` é crescente dentro da volta, então dá para cortar um trecho por
    metragem com busca binária — é assim que cada curva é isolada.
    """

    def __init__(self, telemetry: dict):
        t = telemetry or {}
        self.times = _floats(t.get("times"))
        self.distance = _floats(t.get("distance"))
        self.speed = _floats(t.get("speed"))
        self.gas = _floats(t.get("gas"))
        self.brake = _floats(t.get("brake"))
        self.rpm = _floats(t.get("rpm"))
        self.gear = _floats(t.get("gear"))
        self.steer = _floats(t.get("steer"))
        self.g_lat = _floats(t.get("g_lat"))
        self.car_x = _floats(t.get("car_x"))
        self.car_z = _floats(t.get("car_z"))

    def __len__(self) -> int:
        return len(self.distance) or len(self.times)

    @property
    def lap_length_m(self) -> float:
        return max(self.distance) if self.distance else 0.0

    def has(self, *names, minimum: int = MIN_SAMPLES) -> bool:
        """Todos estes canais têm amostras suficientes?"""
        for name in names:
            arr = getattr(self, name, None)
            if not arr or len(arr) < minimum:
                return False
            if len(arr) < len(self) * 0.5:
                return False                    # canal truncado: não confia
        return True

    def window(self, start_m: float, end_m: float):
        """Índices [i0, i1) do trecho entre duas metragens da volta."""
        if not self.distance:
            return 0, 0
        i0 = bisect.bisect_left(self.distance, start_m)
        i1 = bisect.bisect_right(self.distance, end_m)
        return i0, min(i1, len(self.distance))

    def corner_window(self, corner, track_length: float):
        """Índices do trecho de uma `Corner` de core.corner_analysis."""
        length = track_length or self.lap_length_m
        return self.window(corner.start * length, corner.end * length)

    def index_at(self, meters: float) -> Optional[int]:
        """Índice da amostra mais próxima de uma metragem."""
        if not self.distance:
            return None
        i = bisect.bisect_left(self.distance, meters)
        if i >= len(self.distance):
            return len(self.distance) - 1
        if i > 0 and (meters - self.distance[i - 1]) < (self.distance[i] - meters):
            return i - 1
        return i

    @staticmethod
    def gear_number(raw_gear: float) -> Optional[int]:
        """
        O AC manda 0 = ré, 1 = neutro, 2 = primeira. Devolve a marcha como o
        piloto a chama (1, 2, 3...), ou None para ré/neutro.
        """
        g = int(round(raw_gear))
        return g - 1 if g >= 2 else None


# ---------------------------------------------------------------------------
# Pedais
# ---------------------------------------------------------------------------

def brake_throttle_overlap(ch: LapChannels) -> Optional[float]:
    """
    Fração das amostras de frenagem em que o acelerador também estava pisado.

    Um pouco de sobreposição é técnica (manter o turbo, estabilizar o eixo);
    muito é pé preso — o carro freia e acelera ao mesmo tempo e o tempo some
    sem aparecer em nenhum gráfico isolado.
    """
    if not ch.has("gas", "brake"):
        return None
    freando = [i for i, b in enumerate(ch.brake) if b > PEDAL_ON]
    if len(freando) < MIN_SAMPLES:
        return None
    juntos = sum(1 for i in freando if i < len(ch.gas) and ch.gas[i] > PEDAL_ON)
    return juntos / len(freando)


class BrakeReport:
    """
    Como o freio foi USADO ao longo da volta — duas falhas diferentes.

    `jitter_zones` são as freadas em que o piloto REPISOU depois de começar a
    soltar. `abrupt_zones` são as freadas em que ele largou o pedal DE UMA VEZ.
    São erros distintos e com correções distintas: o primeiro balança o carro
    na transição, o segundo tira carga do eixo dianteiro justo quando a curva
    precisa dela, e o carro para de girar na entrada.

    `abrupt_zones` fica None quando não deu para medir — a medida precisa do
    canal de tempo, e ghost antigo pode não ter.
    """

    __slots__ = ("zones", "jitter_zones", "abrupt_zones")

    def __init__(self, zones: int = 0, jitter_zones: int = 0,
                 abrupt_zones: Optional[int] = None):
        self.zones = zones                  # freadas da volta
        self.jitter_zones = jitter_zones    # quantas tiveram repisada
        self.abrupt_zones = abrupt_zones    # quantas foram largadas de uma vez

    @property
    def fraction(self) -> float:
        return self.jitter_zones / self.zones if self.zones else 0.0

    @property
    def abrupt_fraction(self) -> Optional[float]:
        if self.abrupt_zones is None or not self.zones:
            return None
        return self.abrupt_zones / self.zones


def brake_release_report(ch: LapChannels) -> Optional[BrakeReport]:
    """
    Como cada freada da volta terminou: com repisada, largada de uma vez, ou
    aliviada progressivamente (que é o certo).

    A conta é por FREADA, não por evento: uma pista de dezoito curvas teria
    naturalmente mais degraus que uma de oito, e o número absoluto faria o
    engenheiro reclamar mais de Spa que de Interlagos sem motivo. Uma marcação
    por freada já basta — o que importa é em que fração das freadas o problema
    aparece.

    A soltura é medida em TEMPO, não em derivada instantânea: o tempo que o
    pedal levou para sair da freada forte até solto é estável mesmo com pedal
    ruidoso, e é o número que o piloto entende ("meio segundo" contra "de uma
    vez").
    """
    if not ch.has("brake"):
        return None

    mede_soltura = ch.has("times")
    rep = BrakeReport(abrupt_zones=0 if mede_soltura else None)

    def tempo(i):
        return ch.times[i] if mede_soltura and i < len(ch.times) else None

    freando = False
    soltando = False
    minimo = 1.0
    marcada = False
    pico = 0.0
    t_forte = None      # último instante com o pedal ainda na freada forte

    for i, valor in enumerate(ch.brake):
        if valor < PEDAL_ON:
            if freando and mede_soltura and pico >= BRAKE_HARD and t_forte is not None:
                t_fim = tempo(i)
                if t_fim is not None and 0 <= (t_fim - t_forte) <= ABRUPT_RELEASE_S:
                    rep.abrupt_zones += 1
            freando = soltando = marcada = False
            minimo, pico, t_forte = 1.0, 0.0, None
            continue

        if not freando:
            freando = True
            rep.zones += 1
            minimo = valor
        elif valor < minimo - 0.01:              # começou a soltar
            soltando = True
            minimo = valor
        elif soltando and not marcada and valor > minimo + BRAKE_STEP:
            rep.jitter_zones += 1
            marcada = True                       # uma marcação por freada

        pico = max(pico, valor)
        if valor >= BRAKE_HARD:
            t_forte = tempo(i)

    return rep if rep.zones >= MIN_BRAKE_ZONES else None


def peak_brake(ch: LapChannels, i0: int = 0, i1: Optional[int] = None) -> Optional[float]:
    """Maior pressão de freio no trecho (0..1)."""
    if not ch.brake:
        return None
    trecho = ch.brake[i0:i1 if i1 is not None else len(ch.brake)]
    return max(trecho) if trecho else None


def is_flat_out(ch: LapChannels, i0: int, i1: int) -> Optional[bool]:
    """A curva foi feita sem tocar no freio?"""
    trecho = ch.brake[i0:i1]
    if len(trecho) < 5:
        return None
    return max(trecho) <= PEDAL_ON


# ---------------------------------------------------------------------------
# Volante
# ---------------------------------------------------------------------------

def steering_rate(ch: LapChannels) -> Optional[float]:
    """
    Velocidade média do volante nos trechos de curva, em graus por segundo.

    Comparada com a mesma medida da volta de referência, é o número que separa
    "entrou suave" de "jogou o carro para dentro".
    """
    if not ch.has("steer", "times"):
        return None
    taxas = []
    limite = max((abs(s) for s in ch.steer), default=0.0) * 0.15
    for i in range(1, min(len(ch.steer), len(ch.times))):
        dt = ch.times[i] - ch.times[i - 1]
        if dt <= 0 or dt > 0.2:                 # buraco na gravação
            continue
        if abs(ch.steer[i]) < limite:
            continue                            # reta: não conta
        taxas.append(abs(ch.steer[i] - ch.steer[i - 1]) / dt)
    return _mean(taxas)


def understeer_fraction(ch: LapChannels) -> Optional[float]:
    """
    Fração das amostras com MUITO volante e POUCO G lateral.

    É a assinatura do subesterço visto de fora: o piloto vira mais, o carro não
    vira mais. Comparar com o máximo da própria volta deixa a medida
    independente do carro e da pista.
    """
    if not ch.has("steer", "g_lat", "speed"):
        return None
    steer_max = max((abs(s) for s in ch.steer), default=0.0)
    glat_max = max((abs(g) for g in ch.g_lat), default=0.0)
    if steer_max <= 0 or glat_max <= 0:
        return None

    virando, travado = 0, 0
    n = min(len(ch.steer), len(ch.g_lat), len(ch.speed))
    for i in range(n):
        if ch.speed[i] < MIN_SPEED_KMH:
            continue
        if abs(ch.steer[i]) < steer_max * STEER_HIGH_FRACTION:
            continue
        virando += 1
        if abs(ch.g_lat[i]) < glat_max * GLAT_LOW_FRACTION:
            travado += 1
    if virando < MIN_SAMPLES // 2:
        return None
    return travado / virando


# ---------------------------------------------------------------------------
# Motor e transmissão
# ---------------------------------------------------------------------------

class ShiftReport:
    """Resumo das trocas de marcha da volta."""

    __slots__ = ("upshifts", "early", "on_limiter", "worst_early_rpm", "max_rpm")

    def __init__(self, upshifts=0, early=0, on_limiter=0,
                 worst_early_rpm=None, max_rpm=0.0):
        self.upshifts = upshifts
        self.early = early                      # trocas abaixo da faixa de potência
        self.on_limiter = on_limiter            # amostras batendo no corte
        self.worst_early_rpm = worst_early_rpm
        self.max_rpm = max_rpm

    @property
    def early_fraction(self) -> float:
        return self.early / self.upshifts if self.upshifts else 0.0


def shift_report(ch: LapChannels, max_rpm: float) -> Optional[ShiftReport]:
    """
    Onde as marchas foram trocadas em relação à faixa útil do motor.

    `max_rpm` vem do bloco estático do carro — sem ele não há como saber se
    7.000 giros é cedo ou tarde.
    """
    if not ch.has("rpm", "gear") or not max_rpm or max_rpm <= 0:
        return None

    rep = ShiftReport(max_rpm=max_rpm)
    n = min(len(ch.rpm), len(ch.gear))
    for i in range(1, n):
        if ch.rpm[i] >= max_rpm * LIMITER_FRACTION:
            rep.on_limiter += 1
        if ch.gear[i] <= ch.gear[i - 1] or ch.gear[i] < 2:
            continue                            # não é subida de marcha
        rep.upshifts += 1
        # O RPM da troca é o do quadro ANTERIOR: no quadro da troca ele já caiu.
        rpm_troca = ch.rpm[i - 1]
        if rpm_troca < max_rpm * EARLY_SHIFT_FRACTION:
            rep.early += 1
            if rep.worst_early_rpm is None or rpm_troca < rep.worst_early_rpm:
                rep.worst_early_rpm = rpm_troca
    return rep if rep.upshifts else None


def gear_at(ch: LapChannels, meters: float) -> Optional[int]:
    """Marcha engatada numa metragem da volta (1, 2, 3... ou None)."""
    if not ch.gear:
        return None
    i = ch.index_at(meters)
    if i is None or i >= len(ch.gear):
        return None
    return LapChannels.gear_number(ch.gear[i])


def speed_at(ch: LapChannels, meters: float) -> Optional[float]:
    """Velocidade numa metragem da volta, em km/h."""
    if not ch.speed:
        return None
    i = ch.index_at(meters)
    if i is None or i >= len(ch.speed):
        return None
    return ch.speed[i]


# ---------------------------------------------------------------------------
# Traçado
# ---------------------------------------------------------------------------

def line_deviation_m(lap: LapChannels, ref: LapChannels,
                     start_m: float, end_m: float) -> Optional[float]:
    """
    Distância média, em metros, entre a linha da volta e a da referência num
    trecho.

    As duas voltas são casadas por metragem percorrida, não por tempo: é o
    único jeito de comparar traçado sem que a diferença de velocidade
    contamine a medida.
    """
    if not (lap.car_x and lap.car_z and ref.car_x and ref.car_z):
        return None
    if not (lap.distance and ref.distance):
        return None

    i0, i1 = lap.window(start_m, end_m)
    if i1 - i0 < 5:
        return None

    desvios = []
    for i in range(i0, i1):
        if i >= len(lap.car_x) or i >= len(lap.car_z):
            break
        j = ref.index_at(lap.distance[i])
        if j is None or j >= len(ref.car_x) or j >= len(ref.car_z):
            continue
        dx = lap.car_x[i] - ref.car_x[j]
        dz = lap.car_z[i] - ref.car_z[j]
        desvios.append(math.hypot(dx, dz))
    if len(desvios) < 5:
        return None
    return _mean(desvios)
