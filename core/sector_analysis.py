"""
core/sector_analysis.py — Análise de Setores e Micro-setores (Mini-sectors)
===========================================================================

Módulo analítico desacoplado da interface gráfica: calcula os limites de setores
oficiais (S1, S2, S3) e subdivide cada setor em micro-setores contínuos de alta
resolução (padrão de transmissão de Fórmula 1 e telemetria MoTeC / WEC).

Fornece:
  * Detecção robusta de limites de setores via canal `sector`, tempos ou metragem.
  * Fatiamento em 24 micro-setores (8 por setor oficial).
  * Cálculo de deltas ponto a ponto por micro-setor contra volta de referência.
  * Cores de status de alta fidelidade (Roxo/Recorde, Verde/Ganho, Amarelo/Perda, Neutro).
  * Estatísticas de velocidade (média, mínima no ápice, máxima) por mini-setor.
  * Tempo ideal teórico (Optimal Lap) baseado na combinação dos melhores setores.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# Cores padronizadas para setores e micro-setores (padrão broadcast F1 / MoTeC)
COLOR_PURPLE  = "#d500f9"   # Roxo luminoso (Melhor tempo / Ganho expressivo)
COLOR_GREEN   = "#00e676"   # Verde vibrante (Mais rápido que referência)
COLOR_YELLOW  = "#ffd600"   # Amarelo/Ouro (Mais lento que referência)
COLOR_NEUTRAL = "#00e5ff"   # Ciano neutro (Análise solo ou delta zero)
COLOR_MUTED   = "#455a64"   # Cinza escuro (Inativo / Vazio)


@dataclass
class SectorInfo:
    """Informações detalhadas de um setor oficial (S1, S2 ou S3)."""
    index: int                          # 0=S1, 1=S2, 2=S3
    name: str                           # "S1", "S2", "S3"
    start_m: float
    end_m: float
    time_s: float
    ref_time_s: Optional[float] = None
    delta_s: Optional[float] = None
    status: str = "neutral"             # "purple", "green", "yellow", "neutral"
    color: str = COLOR_NEUTRAL
    is_personal_best: bool = False

    @property
    def length_m(self) -> float:
        return max(0.0, self.end_m - self.start_m)

    @property
    def formatted_time(self) -> str:
        if self.time_s <= 0:
            return "--.---"
        return f"{self.time_s:.3f} s"

    @property
    def formatted_delta(self) -> str:
        if self.delta_s is None:
            return "--"
        sign = "+" if self.delta_s >= 0 else ""
        return f"{sign}{self.delta_s:.3f} s"


@dataclass
class MicroSectorInfo:
    """Informações de um micro-setor (mini-sector / split de alta resolução)."""
    index: int                          # 0 .. total_micro - 1
    sector_index: int                   # 0=S1, 1=S2, 2=S3
    sector_name: str                    # "S1", "S2", "S3"
    start_m: float
    end_m: float
    length_m: float
    time_s: float
    ref_time_s: Optional[float] = None
    delta_s: Optional[float] = None
    avg_speed: float = 0.0              # km/h
    min_speed: float = 0.0              # km/h
    max_speed: float = 0.0              # km/h
    status: str = "neutral"             # "purple", "green", "yellow", "neutral"
    color: str = COLOR_NEUTRAL

    @property
    def formatted_time(self) -> str:
        if self.time_s <= 0:
            return "--.---"
        return f"{self.time_s:.3f} s"

    @property
    def formatted_delta(self) -> str:
        if self.delta_s is None:
            return "--"
        sign = "+" if self.delta_s >= 0 else ""
        return f"{sign}{self.delta_s:.3f} s"


@dataclass
class LapSectorAnalysis:
    """Análise consolidada de setores e micro-setores de uma volta."""
    sectors: List[SectorInfo] = field(default_factory=list)
    micro_sectors: List[MicroSectorInfo] = field(default_factory=list)
    total_time_s: float = 0.0
    ref_total_time_s: Optional[float] = None
    total_delta_s: Optional[float] = None
    theoretical_best_s: float = 0.0
    ideal_gain_s: float = 0.0
    track_length: float = 0.0

    def get_sector(self, index: int) -> Optional[SectorInfo]:
        if 0 <= index < len(self.sectors):
            return self.sectors[index]
        return None

    def get_micro_sector(self, index: int) -> Optional[MicroSectorInfo]:
        if 0 <= index < len(self.micro_sectors):
            return self.micro_sectors[index]
        return None


# ---------------------------------------------------------------------------
# Funções de Interpolação e Detecção
# ---------------------------------------------------------------------------

def _interp_time_at_dist(distances: List[float], times: List[float], dist: float) -> float:
    """Interpola linearmente o tempo exato (s) para uma determinada distância (m)."""
    if not distances or not times:
        return 0.0
    if dist <= distances[0]:
        return times[0]
    if dist >= distances[-1]:
        return times[-1]

    idx = bisect.bisect_left(distances, dist)
    if idx <= 0:
        return times[0]
    if idx >= len(distances):
        return times[-1]

    d0, d1 = distances[idx - 1], distances[idx]
    t0, t1 = times[idx - 1], times[idx]
    if d1 == d0:
        return t0
    ratio = (dist - d0) / (d1 - d0)
    return t0 + ratio * (t1 - t0)


def detect_sector_boundaries(
    telemetry: dict,
    track_length: float,
    sector_times_ms: Optional[List[int]] = None
) -> Tuple[float, float]:
    """
    Determina os pontos de transição S1 -> S2 e S2 -> S3 em metros.

    Prioridades de detecção:
      1. Canal oficial `sector` registrado na telemetria (0 -> 1 e 1 -> 2).
      2. Interpolação via `times` usando `sector_times_ms`.
      3. Fallback geométrico (1/3 e 2/3 da extensão da pista).
    """
    distances = telemetry.get("distance") or []
    sectors = telemetry.get("sector") or []
    times = telemetry.get("times") or []

    s1_m: Optional[float] = None
    s2_m: Optional[float] = None

    # Método 1: Canal de setores gravado na telemetria do simulador
    if len(distances) >= 10 and len(sectors) == len(distances):
        for i in range(1, len(sectors)):
            if s1_m is None and sectors[i] == 1 and sectors[i - 1] == 0:
                s1_m = float(distances[i])
            elif s2_m is None and sectors[i] == 2 and sectors[i - 1] == 1:
                s2_m = float(distances[i])

    # Método 2: Tempos de setores conhecidos (ms)
    if (s1_m is None or s2_m is None) and sector_times_ms and len(sector_times_ms) >= 2:
        s1_time_s = sector_times_ms[0] / 1000.0 if sector_times_ms[0] > 0 else None
        s2_time_s = ((sector_times_ms[0] + sector_times_ms[1]) / 1000.0
                     if sector_times_ms[0] > 0 and sector_times_ms[1] > 0 else None)

        if len(distances) >= 2 and len(times) == len(distances):
            if s1_m is None and s1_time_s is not None and s1_time_s <= times[-1]:
                idx1 = bisect.bisect_left(times, s1_time_s)
                if 0 <= idx1 < len(distances):
                    s1_m = float(distances[idx1])
            if s2_m is None and s2_time_s is not None and s2_time_s <= times[-1]:
                idx2 = bisect.bisect_left(times, s2_time_s)
                if 0 <= idx2 < len(distances):
                    s2_m = float(distances[idx2])

    # Método 3: Fallback padrão de proporção (1/3 e 2/3 da pista)
    tot_len = track_length if track_length > 100.0 else (distances[-1] if distances else 4309.0)
    if s1_m is None or s1_m <= 10.0 or s1_m >= tot_len * 0.8:
        s1_m = tot_len * 0.3333
    if s2_m is None or s2_m <= s1_m or s2_m >= tot_len * 0.95:
        s2_m = tot_len * 0.6667

    return round(s1_m, 1), round(s2_m, 1)


# ---------------------------------------------------------------------------
# Análise Completa de Setores e Micro-setores
# ---------------------------------------------------------------------------

def analyze_sectors_and_micro(
    current_tel: dict,
    ref_tel: Optional[dict] = None,
    num_micro_per_sector: int = 8,
    best_sector_times_s: Optional[List[float]] = None
) -> LapSectorAnalysis:
    """
    Executa a partição e análise em alta resolução da volta:
      * 3 Setores oficiais (S1, S2, S3) com tempos e deltas.
      * N micro-setores por setor (padrão 8 por setor = 24 splits no total).
      * Métricas completas de velocidade (média, mín, máx) por mini-setor.
      * Determinação de status de cores F1 (Roxo / Verde / Amarelo / Neutro).
    """
    cur_dists = current_tel.get("distance") or []
    cur_times = current_tel.get("times") or []
    cur_speeds = current_tel.get("speed") or []

    if len(cur_dists) < 2 or len(cur_times) < 2:
        return LapSectorAnalysis()

    track_len = float(cur_dists[-1])
    s1_end_m, s2_end_m = detect_sector_boundaries(current_tel, track_len)

    ref_dists = (ref_tel.get("distance") or []) if ref_tel else []
    ref_times = (ref_tel.get("times") or []) if ref_tel else []

    has_ref = len(ref_dists) >= 2 and len(ref_times) >= 2

    # Metragens de início e fim dos 3 setores
    sec_bounds = [
        (0.0, s1_end_m, "S1"),
        (s1_end_m, s2_end_m, "S2"),
        (s2_end_m, track_len, "S3"),
    ]

    sectors: List[SectorInfo] = []
    micro_sectors: List[MicroSectorInfo] = []

    total_cur_time = cur_times[-1] - cur_times[0]
    total_ref_time = (ref_times[-1] - ref_times[0]) if has_ref else None
    total_delta = (total_cur_time - total_ref_time) if total_ref_time is not None else None

    micro_counter = 0

    for s_idx, (sec_start, sec_end, sec_name) in enumerate(sec_bounds):
        # Tempos do setor oficial
        cur_s_t0 = _interp_time_at_dist(cur_dists, cur_times, sec_start)
        cur_s_t1 = _interp_time_at_dist(cur_dists, cur_times, sec_end)
        s_time_s = max(0.0, cur_s_t1 - cur_s_t0)

        s_ref_time_s: Optional[float] = None
        s_delta_s: Optional[float] = None
        s_status = "neutral"
        s_color = COLOR_NEUTRAL

        if has_ref:
            ref_s_t0 = _interp_time_at_dist(ref_dists, ref_times, sec_start)
            ref_s_t1 = _interp_time_at_dist(ref_dists, ref_times, sec_end)
            s_ref_time_s = max(0.0, ref_s_t1 - ref_s_t0)
            s_delta_s = s_time_s - s_ref_time_s

            if best_sector_times_s and s_idx < len(best_sector_times_s) and best_sector_times_s[s_idx] > 0:
                if s_time_s <= best_sector_times_s[s_idx] + 0.005:
                    s_status = "purple"
                    s_color = COLOR_PURPLE

            if s_status != "purple":
                if s_delta_s <= -0.010:
                    s_status = "green"
                    s_color = COLOR_GREEN
                elif s_delta_s >= 0.010:
                    s_status = "yellow"
                    s_color = COLOR_YELLOW
                else:
                    s_status = "neutral"
                    s_color = COLOR_NEUTRAL

        sectors.append(SectorInfo(
            index=s_idx,
            name=sec_name,
            start_m=sec_start,
            end_m=sec_end,
            time_s=s_time_s,
            ref_time_s=s_ref_time_s,
            delta_s=s_delta_s,
            status=s_status,
            color=s_color,
            is_personal_best=(s_status == "purple")
        ))

        # Subdivisão em Micro-setores (num_micro_per_sector)
        sec_dist = sec_end - sec_start
        for m in range(num_micro_per_sector):
            m_start = sec_start + (m / num_micro_per_sector) * sec_dist
            m_end = sec_start + ((m + 1) / num_micro_per_sector) * sec_dist
            m_len = max(1.0, m_end - m_start)

            t0 = _interp_time_at_dist(cur_dists, cur_times, m_start)
            t1 = _interp_time_at_dist(cur_dists, cur_times, m_end)
            m_time_s = max(0.001, t1 - t0)

            m_ref_time_s: Optional[float] = None
            m_delta_s: Optional[float] = None
            m_status = "neutral"
            m_color = COLOR_NEUTRAL

            if has_ref:
                rt0 = _interp_time_at_dist(ref_dists, ref_times, m_start)
                rt1 = _interp_time_at_dist(ref_dists, ref_times, m_end)
                m_ref_time_s = max(0.001, rt1 - rt0)
                m_delta_s = m_time_s - m_ref_time_s

                # Critérios de coloração de micro-setores (F1 Style)
                if m_delta_s <= -0.060:
                    m_status = "purple"
                    m_color = COLOR_PURPLE
                elif m_delta_s <= -0.006:
                    m_status = "green"
                    m_color = COLOR_GREEN
                elif m_delta_s >= 0.006:
                    m_status = "yellow"
                    m_color = COLOR_YELLOW
                else:
                    m_status = "neutral"
                    m_color = COLOR_NEUTRAL

            # Extração das velocidades no trecho
            idx_start = bisect.bisect_left(cur_dists, m_start)
            idx_end = bisect.bisect_right(cur_dists, m_end)
            sub_speeds = cur_speeds[idx_start:idx_end] if idx_end > idx_start else []

            if sub_speeds:
                min_spd = float(min(sub_speeds))
                max_spd = float(max(sub_speeds))
            else:
                s_est = (m_len / m_time_s) * 3.6
                min_spd = max_spd = s_est

            avg_spd = (m_len / m_time_s) * 3.6

            micro_sectors.append(MicroSectorInfo(
                index=micro_counter,
                sector_index=s_idx,
                sector_name=sec_name,
                start_m=round(m_start, 1),
                end_m=round(m_end, 1),
                length_m=round(m_len, 1),
                time_s=m_time_s,
                ref_time_s=m_ref_time_s,
                delta_s=m_delta_s,
                avg_speed=avg_spd,
                min_speed=min_spd,
                max_speed=max_spd,
                status=m_status,
                color=m_color
            ))
            micro_counter += 1

    # Volta ideal teórica (Theoretical / Optimal Lap)
    theo_best = 0.0
    for s in sectors:
        if s.ref_time_s is not None and s.ref_time_s > 0:
            theo_best += min(s.time_s, s.ref_time_s)
        else:
            theo_best += s.time_s

    ideal_gain = max(0.0, total_cur_time - theo_best) if theo_best > 0 else 0.0

    return LapSectorAnalysis(
        sectors=sectors,
        micro_sectors=micro_sectors,
        total_time_s=total_cur_time,
        ref_total_time_s=total_ref_time,
        total_delta_s=total_delta,
        theoretical_best_s=theo_best,
        ideal_gain_s=ideal_gain,
        track_length=track_len
    )


def get_micro_sector_at(
    analysis: LapSectorAnalysis,
    distance_m: float
) -> Tuple[int, Optional[MicroSectorInfo]]:
    """Localiza instantaneamente qual micro-setor contém a distância especificada."""
    if not analysis.micro_sectors:
        return -1, None

    # Busca binária pelos pontos de início
    dists = [m.start_m for m in analysis.micro_sectors]
    idx = bisect.bisect_right(dists, distance_m) - 1
    idx = max(0, min(len(analysis.micro_sectors) - 1, idx))
    return idx, analysis.micro_sectors[idx]
