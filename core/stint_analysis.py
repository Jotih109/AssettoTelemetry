"""
core/stint_analysis.py — Análise de Stint, Degradação de Pneus e Estratégia de Box
==================================================================================
Módulo de engenharia de corrida para análise pós-sessão e ritmo de stint.
Extrai das voltas gravadas de uma sessão:
  * Desgaste percentual dos 4 pneus (FL, FR, RL, RR)
  * Temperatura média dos pneus por volta
  * Consumo de combustível por volta (L/volta)

Ajusta regressões lineares sobre o desgaste e a degradação do tempo de volta,
projetando o ponto de cliff do pneu e a janela ideal de parada nos boxes cruzando
a autonomia de combustível com a vida útil do composto.

**O que não está medido não é inventado.** Nem toda volta do catálogo tem os
canais de pneu e combustível — voltas antigas, gravadas antes desses canais
existirem, simplesmente não os têm. Quando falta o dado, o campo fica vazio e a
volta é marcada (`wear_measured`, `fuel_measured`, `temps_measured`); a
regressão correspondente não roda, a projeção volta zerada e o motivo entra em
`warnings`. Um cliff projetado em cima de número chutado é pior que nenhum
cliff: o piloto pararia no box confiando numa conta que ninguém fez.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from core.lap_library import LapLibrary, LapRecord

#: Abaixo disto uma regressão não diz nada: com um ponto só não há reta, e com
#: dois qualquer ruído vira tendência.
MIN_PONTOS_REGRESSAO = 3


@dataclasses.dataclass
class LapStintData:
    """Métricas de stint de uma volta individual."""
    lap_number: int
    lap_time_s: float
    lap_time_str: str
    tyre_wear: List[float]       # [FL, FR, RL, RR] em % restante (100% = novo, 0% = no osso)
    tyre_temp_avg: List[float]   # [FL, FR, RL, RR] em °C
    fuel_level: float            # Litros ao término da volta
    fuel_used: float             # Litros consumidos na volta
    is_valid: bool = True
    session_id: str = ""
    #: A volta trouxe mesmo os canais, ou o campo está vazio por falta deles?
    wear_measured: bool = False
    fuel_measured: bool = False
    temps_measured: bool = False
    #: Litros no INÍCIO da volta (só com `fuel_measured`)
    fuel_start: float = 0.0

    @property
    def worst_tyre_wear(self) -> float:
        return min(self.tyre_wear) if self.tyre_wear else 100.0

    @property
    def avg_tyre_wear(self) -> float:
        return sum(self.tyre_wear) / len(self.tyre_wear) if self.tyre_wear else 100.0


@dataclasses.dataclass
class StintAnalysisResult:
    """Resultado da modelagem matemática e projeção de estratégia do stint."""
    track: str
    car: str
    session_id: str
    total_laps_analyzed: int
    laps_data: List[LapStintData]

    # Regressão de desgaste dos pneus (FL, FR, RL, RR e Geral)
    wear_rates_per_lap: List[float]    # % de desgaste perdido por volta [FL, FR, RL, RR] (valores negativos)
    avg_wear_rate_per_lap: float       # % de desgaste médio por volta
    r2_wear: float                     # R² do ajuste de desgaste

    # Regressão de tempo de volta (degradação mecânica de ritmo)
    base_lap_time_s: float             # Tempo inicial projetado (volta 0/1)
    time_deg_slope_s: float            # Segundos perdidos por volta (+0.08 s/lap)
    r2_time_deg: float                 # R² do ajuste de tempo de volta

    # Combustível
    avg_fuel_consumption_per_lap: float  # Litros/volta
    initial_fuel: float                  # Litros no início da primeira volta do stint
    estimated_fuel_laps: int             # Voltas totais que o tanque suportaria

    # Projeção de Parada e Cliff. Todas as "voltas" abaixo são contadas DENTRO
    # do stint (1 = primeira volta analisada), não o número da volta na sessão.
    # `*_absolute` traduz para o contador que o piloto vê no carro.
    cliff_threshold_pct: float         # Limiar crítico de desgaste (ex: 40%)
    projected_cliff_lap: int           # Volta do stint em que o pior pneu atinge o cliff
    projected_fuel_out_lap: int        # Volta do stint em que o combustível acaba
    pit_window_start: int              # Volta inicial da janela
    pit_window_end: int                # Volta final da janela
    recommended_pit_lap: int           # Volta recomendada para parar
    recommended_pit_lap_absolute: int = 0

    # Parecer técnico
    recommendation_text: str = ""      # Síntese em linguagem de corrida

    #: O que NÃO pôde ser calculado, e por quê. Vazio = tudo veio de medição.
    warnings: List[str] = dataclasses.field(default_factory=list)

    @property
    def has_wear_model(self) -> bool:
        return any(r < 0.0 for r in self.wear_rates_per_lap)

    @property
    def has_fuel_model(self) -> bool:
        return self.avg_fuel_consumption_per_lap > 0.0


def _linear_regression(x: List[float], y: List[float]) -> Tuple[float, float, float]:
    """
    Ajuste por mínimos quadrados: y = slope * x + intercept.
    Devolve (slope, intercept, r_squared).
    """
    n = len(x)
    if n < 2:
        return 0.0, (y[0] if n == 1 else 0.0), 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xx = sum((xi - mean_x) ** 2 for xi in x)
    ss_yy = sum((yi - mean_y) ** 2 for yi in y)
    ss_xy = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))

    if ss_xx == 0.0:
        return 0.0, mean_y, 0.0

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    if ss_yy > 0.0:
        r2 = (ss_xy ** 2) / (ss_xx * ss_yy)
    else:
        r2 = 1.0

    return slope, intercept, max(0.0, min(1.0, r2))


def _canal(telemetry: dict, *nomes) -> Optional[List[float]]:
    """Primeiro canal não vazio entre os nomes dados, ou `None`."""
    for nome in nomes:
        arr = telemetry.get(nome)
        if isinstance(arr, list) and arr:
            return arr
    return None


def _media(arr: List[float]) -> Optional[float]:
    try:
        return sum(float(x) for x in arr) / len(arr)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def extract_stint_laps(track: str, car: str, laps: List[LapRecord],
                       library: LapLibrary) -> List[LapStintData]:
    """
    Extrai desgaste, temperatura e combustível de uma lista de voltas
    ordenadas cronologicamente da mesma sessão.

    Volta sem os canais entra na lista assim mesmo — o tempo dela ainda serve
    para a regressão de ritmo —, só que com os campos vazios e as flags
    `*_measured` em `False`.
    """
    sorted_laps = sorted(
        [r for r in laps if r.lap_time_ms > 0 and r.full_lap and not r.pit_lap],
        key=lambda r: (r.timestamp, r.lap_number)
    )
    if not sorted_laps:
        return []

    stint_laps: List[LapStintData] = []

    for idx, rec in enumerate(sorted_laps):
        lap_time_s = float(rec.lap_time_ms) / 1000.0
        telemetry = library.load_telemetry(track, car, rec) or {}

        # 1. Combustível — só o que o canal disser.
        fuel_ch = _canal(telemetry, "fuel")
        fuel_level = fuel_start = fuel_used = 0.0
        fuel_measured = False
        if fuel_ch is not None:
            try:
                fuel_start = float(fuel_ch[0])
                fuel_level = float(fuel_ch[-1])
                # Reabastecer no meio da volta não existe em pista: nível
                # subindo é dado ruim, não consumo negativo.
                fuel_used = max(0.0, fuel_start - fuel_level)
                fuel_measured = True
            except (TypeError, ValueError):
                fuel_level = fuel_start = fuel_used = 0.0
                fuel_measured = False

        # 2. Desgaste dos pneus [FL, FR, RL, RR]
        eixos = (("tyre_wear_fl", "tyre_wear_0"), ("tyre_wear_fr", "tyre_wear_1"),
                 ("tyre_wear_rl", "tyre_wear_2"), ("tyre_wear_rr", "tyre_wear_3"))
        canais_wear = [_canal(telemetry, *nomes) for nomes in eixos]
        tyre_wear: List[float] = []
        if all(c is not None for c in canais_wear):
            try:
                tyre_wear = [float(c[-1]) for c in canais_wear]
            except (TypeError, ValueError):
                tyre_wear = []
        wear_measured = len(tyre_wear) == 4

        # 3. Temperaturas médias [FL, FR, RL, RR]
        eixos_t = (("tyre_temp_fl", "tyre_temp_0"), ("tyre_temp_fr", "tyre_temp_1"),
                   ("tyre_temp_rl", "tyre_temp_2"), ("tyre_temp_rr", "tyre_temp_3"))
        canais_temp = [_canal(telemetry, *nomes) for nomes in eixos_t]
        tyre_temp_avg: List[float] = []
        if all(c is not None for c in canais_temp):
            medias = [_media(c) for c in canais_temp]
            if all(m is not None for m in medias):
                tyre_temp_avg = [round(m, 1) for m in medias]
        temps_measured = len(tyre_temp_avg) == 4

        stint_laps.append(LapStintData(
            lap_number=rec.lap_number if rec.lap_number > 0 else (idx + 1),
            lap_time_s=lap_time_s,
            lap_time_str=rec.lap_time_str,
            tyre_wear=tyre_wear,
            tyre_temp_avg=tyre_temp_avg,
            fuel_level=round(fuel_level, 2),
            fuel_used=round(fuel_used, 2),
            is_valid=rec.valid,
            session_id=rec.session_id,
            wear_measured=wear_measured,
            fuel_measured=fuel_measured,
            temps_measured=temps_measured,
            fuel_start=round(fuel_start, 2),
        ))

    return stint_laps


def _resultado_vazio(track: str, car: str, sid: str, cliff_threshold_pct: float,
                     motivo: str) -> StintAnalysisResult:
    return StintAnalysisResult(
        track=track, car=car, session_id=sid, total_laps_analyzed=0,
        laps_data=[], wear_rates_per_lap=[0.0] * 4, avg_wear_rate_per_lap=0.0,
        r2_wear=0.0, base_lap_time_s=0.0, time_deg_slope_s=0.0, r2_time_deg=0.0,
        avg_fuel_consumption_per_lap=0.0, initial_fuel=0.0, estimated_fuel_laps=0,
        cliff_threshold_pct=cliff_threshold_pct, projected_cliff_lap=0,
        projected_fuel_out_lap=0, pit_window_start=0, pit_window_end=0,
        recommended_pit_lap=0, recommended_pit_lap_absolute=0,
        recommendation_text=motivo, warnings=[motivo],
    )


def analyze_stint(track: str, car: str, session_laps: List[LapRecord],
                  library: LapLibrary,
                  cliff_threshold_pct: float = 40.0,
                  fuel_capacity: float = 100.0) -> StintAnalysisResult:
    """
    Regressão do stint e projeção de box, só com o que foi medido.

    Cada bloco (desgaste, ritmo, combustível) é calculado de forma
    independente: um stint sem canal de pneu ainda rende a degradação de
    ritmo, e o que faltou sai em `warnings`.
    """
    stint_laps = extract_stint_laps(track, car, session_laps, library)
    sid = session_laps[0].session_id if session_laps else "current"

    if not stint_laps:
        return _resultado_vazio(track, car, sid, cliff_threshold_pct,
                                "Dados insuficientes para análise de stint.")

    warnings: List[str] = []
    primeira_volta = stint_laps[0].lap_number

    def absoluta(indice_stint: int) -> int:
        """Índice dentro do stint (1..N) -> número da volta na sessão."""
        if indice_stint <= 0:
            return 0
        if indice_stint <= len(stint_laps):
            return stint_laps[indice_stint - 1].lap_number
        return primeira_volta + indice_stint - 1

    # 1. Regressão de desgaste por pneu, só sobre voltas com o canal.
    wear_idx = [float(i + 1) for i, l in enumerate(stint_laps) if l.wear_measured]
    wear_laps = [l for l in stint_laps if l.wear_measured]

    wear_rates = [0.0] * 4
    intercepts_wear = [0.0] * 4
    r2_wear_overall = 0.0

    if len(wear_laps) >= MIN_PONTOS_REGRESSAO:
        taxas, inters, r2s = [], [], []
        for wheel_idx in range(4):
            valores = [l.tyre_wear[wheel_idx] for l in wear_laps]
            slope, intercept, r2 = _linear_regression(wear_idx, valores)
            taxas.append(round(slope, 3))
            inters.append(intercept)
            r2s.append(r2)
        if any(t < 0.0 for t in taxas):
            wear_rates, intercepts_wear = taxas, inters
            r2_wear_overall = sum(r2s) / 4.0
        else:
            warnings.append(
                "Sem desgaste mensurável nas voltas analisadas: o composto não "
                "perdeu percentual suficiente para projetar cliff.")
    elif wear_laps:
        warnings.append(
            f"Só {len(wear_laps)} volta(s) com canal de desgaste — são precisas "
            f"{MIN_PONTOS_REGRESSAO} para ajustar a curva de degradação do pneu.")
    else:
        warnings.append(
            "Nenhuma volta traz os canais de desgaste de pneu: sem projeção de cliff.")

    avg_wear_rate = sum(wear_rates) / 4.0

    # 2. Regressão de degradação de tempo de volta
    validas_idx = [float(i + 1) for i, l in enumerate(stint_laps) if l.is_valid]
    validas_t = [l.lap_time_s for l in stint_laps if l.is_valid]

    if len(validas_t) >= MIN_PONTOS_REGRESSAO:
        time_slope, time_intercept, r2_time = _linear_regression(validas_idx, validas_t)
    else:
        time_slope, r2_time = 0.0, 0.0
        time_intercept = stint_laps[0].lap_time_s
        warnings.append(
            f"Só {len(validas_t)} volta(s) válida(s): degradação de ritmo não calculada.")

    # 3. Consumo de combustível
    fuel_laps = [l for l in stint_laps if l.fuel_measured and l.fuel_used > 0]
    if fuel_laps:
        avg_fuel_burn = sum(l.fuel_used for l in fuel_laps) / len(fuel_laps)
        init_fuel = next((l.fuel_start for l in stint_laps if l.fuel_measured), 0.0)
        if init_fuel <= 0:
            init_fuel = fuel_capacity
            warnings.append("Nível inicial de combustível não lido; usada a capacidade do tanque.")
        est_fuel_laps = max(1, int(init_fuel / avg_fuel_burn))
    else:
        avg_fuel_burn = 0.0
        init_fuel = 0.0
        est_fuel_laps = 0
        warnings.append(
            "Sem canal de combustível nas voltas analisadas: autonomia não estimada.")

    # 4. Projeção de Cliff de Pneu (baseado no pior pneu)
    projected_cliff = 0
    worst_tyre_name = ""
    worst_tyre_rate = 0.0
    if any(r < 0.0 for r in wear_rates):
        cliff_por_roda = []
        for wheel_idx in range(4):
            slope = wear_rates[wheel_idx]
            inter = intercepts_wear[wheel_idx]
            if slope < 0:
                cliff_por_roda.append(max(1, int(round((cliff_threshold_pct - inter) / slope))))
            else:
                cliff_por_roda.append(0)
        candidatos = [(v, i) for i, v in enumerate(cliff_por_roda) if v > 0]
        if candidatos:
            projected_cliff, worst_idx = min(candidatos)
            worst_tyre_name = ["Dianteiro Esquerdo (FL)", "Dianteiro Direito (FR)",
                               "Traseiro Esquerdo (RL)", "Traseiro Direito (RR)"][worst_idx]
            worst_tyre_rate = wear_rates[worst_idx]

    # 5. Janela de Box — só existe se algum limite for conhecido.
    limites = [v for v in (projected_cliff, est_fuel_laps) if v > 0]
    if limites:
        max_safe_lap = min(limites)
        pit_start = max(1, max_safe_lap - 4)
        pit_end = max(pit_start, max_safe_lap)
        recommended = max(pit_start, min(pit_end, int(round((pit_start + pit_end) / 2.0))))
    else:
        pit_start = pit_end = recommended = 0

    # 6. Texto de recomendação técnica
    rec_lines = []
    if avg_fuel_burn > 0:
        rec_lines.append(
            f"• Consumo Médio: {avg_fuel_burn:.2f} L/volta | Autonomia estimada: ~{est_fuel_laps} voltas.")
    if projected_cliff > 0:
        rec_lines.append(
            f"• Pneu crítico: {worst_tyre_name} com taxa de {abs(worst_tyre_rate):.2f}%/volta. "
            f"Cliff projetado na volta {projected_cliff} do stint (volta {absoluta(projected_cliff)} da sessão).")
    if time_slope > 0:
        rec_lines.append(f"• Degradação mecânica de ritmo: +{time_slope * 10:.2f} s a cada 10 voltas.")
    elif r2_time > 0:
        rec_lines.append("• Ritmo estável com degradação controlada ao longo do stint.")

    if recommended > 0:
        rec_lines.append(
            f"• ESTRATÉGIA RECOMENDADA: Janela de box entre as voltas {pit_start} e {pit_end} "
            f"do stint (Alvo ótimo: Volta {recommended} do stint = volta {absoluta(recommended)} da sessão).")
    else:
        rec_lines.append(
            "• ESTRATÉGIA: sem desgaste nem consumo medidos, não há janela de box a recomendar.")

    for aviso in warnings:
        rec_lines.append(f"• ⚠ {aviso}")

    return StintAnalysisResult(
        track=track,
        car=car,
        session_id=sid,
        total_laps_analyzed=len(stint_laps),
        laps_data=stint_laps,
        wear_rates_per_lap=wear_rates,
        avg_wear_rate_per_lap=round(avg_wear_rate, 3),
        r2_wear=round(r2_wear_overall, 3),
        base_lap_time_s=round(time_intercept, 3),
        time_deg_slope_s=round(time_slope, 4),
        r2_time_deg=round(r2_time, 3),
        avg_fuel_consumption_per_lap=round(avg_fuel_burn, 2),
        initial_fuel=round(init_fuel, 1),
        estimated_fuel_laps=est_fuel_laps,
        cliff_threshold_pct=cliff_threshold_pct,
        projected_cliff_lap=projected_cliff,
        projected_fuel_out_lap=est_fuel_laps,
        pit_window_start=pit_start,
        pit_window_end=pit_end,
        recommended_pit_lap=recommended,
        recommended_pit_lap_absolute=absoluta(recommended),
        recommendation_text="\n".join(rec_lines),
        warnings=warnings,
    )
