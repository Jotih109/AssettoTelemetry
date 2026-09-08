"""
core/lap_report.py — Gerador de Relatórios Analíticos de Desempenho e Telemetria
===============================================================================
Este módulo gera relatórios técnicos de engenharia pós-sessão avaliando o
desempenho do piloto em uma volta (individual ou comparada contra uma volta de
referência / Personal Best).

O relatório analisa minuciosamente:
  1. Metadados e Resumo Geral (tempos, delta, setores, consistência).
  2. Tabela Turn-by-Turn com métricas quantitativas (V_min, freada, retomada).
  3. Principais Pontos Positivos (curvas ganhas, ápices rápidos, técnicas corretas).
  4. Principais Pontos Negativos (onde o tempo foi perdido, erros de execução).
  5. Onde Melhorar e O Porquê (diagnóstico físico causal com dados de telemetria
     e recomendação prática para a próxima volta).
  6. Avaliação Geral da Técnica de Pilotagem (pedais, volante, marchas, eletrônica).
  7. Potencial Teórico de Tempo a ser resgatado.

Suporta exportação em Markdown (.md) e Texto Puro (.txt).
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from typing import Dict, List, Optional, Tuple

from core import corner_analysis as ca
from core import driving_analysis as da
from core.paths import get_app_dir


# ---------------------------------------------------------------------------
# Estruturas de Dados do Relatório
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ReportItem:
    """Item individual de diagnóstico (positivo, negativo ou recomendação)."""
    category: str              # "positive", "negative", "improvement", "technique"
    title: str
    description: str
    why: str = ""              # Justificativa causal (O Porquê)
    action: str = ""           # Ação prática corretiva
    corner_index: Optional[int] = None
    corner_name: str = ""
    metric_detail: str = ""
    time_delta_s: Optional[float] = None
    potential_gain_s: Optional[float] = None


@dataclasses.dataclass
class CornerSummaryRow:
    """Linha de resumo comparativo para uma curva específica."""
    index: int
    name: str
    direction: str
    lap_time_s: Optional[float] = None
    ref_time_s: Optional[float] = None
    delta_time_s: Optional[float] = None
    lap_v_min: Optional[float] = None
    ref_v_min: Optional[float] = None
    delta_v_min: Optional[float] = None
    lap_brake_m: Optional[float] = None
    ref_brake_m: Optional[float] = None
    delta_brake_m: Optional[float] = None
    lap_throttle_m: Optional[float] = None
    ref_throttle_m: Optional[float] = None
    delta_throttle_m: Optional[float] = None
    lap_gear: Optional[int] = None
    ref_gear: Optional[int] = None


@dataclasses.dataclass
class LapReportResult:
    """Resultado completo da análise para montagem do relatório."""
    track: str
    car: str
    lap_number: int
    lap_time_str: str
    lap_time_s: float
    ref_lap_time_str: str = ""
    ref_lap_time_s: Optional[float] = None
    delta_lap_s: Optional[float] = None
    date_str: str = ""
    is_valid: bool = True
    
    # Setores
    lap_sectors_s: List[Optional[float]] = dataclasses.field(default_factory=list)
    ref_sectors_s: List[Optional[float]] = dataclasses.field(default_factory=list)
    sector_deltas_s: List[Optional[float]] = dataclasses.field(default_factory=list)
    
    # Tabela turn-by-turn
    corner_rows: List[CornerSummaryRow] = dataclasses.field(default_factory=list)
    
    # Seções analíticas
    positives: List[ReportItem] = dataclasses.field(default_factory=list)
    negatives: List[ReportItem] = dataclasses.field(default_factory=list)
    improvements: List[ReportItem] = dataclasses.field(default_factory=list)
    techniques: List[ReportItem] = dataclasses.field(default_factory=list)
    
    # Métricas agregadas
    total_potential_gain_s: float = 0.0
    corners_with_losses: int = 0
    corners_with_gains: int = 0


# ---------------------------------------------------------------------------
# Gerador de Relatórios
# ---------------------------------------------------------------------------

class LapReportGenerator:
    """Motor analítico de geração de relatórios de desempenho."""

    def __init__(self):
        pass

    def _resolve_corners(self, track_name: str, lap_telemetry: dict,
                         track_length: float = 0.0) -> List[ca.Corner]:
        """Carrega o mapa de curvas da pista ou detecta automaticamente."""
        slug = ca.track_slug(track_name)
        folder = ca.corner_maps_dir()

        for filename in (f"{slug}.json", f"{slug}.auto.json"):
            filepath = os.path.join(folder, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    cmap = ca.parse_corner_map(data, track_length)
                    if cmap and cmap.corners:
                        return cmap.corners
                except Exception:
                    pass

        # Fallback: detecção automática por Força G lateral
        return ca.detect_corners(lap_telemetry, track_length)

    def analyze(self, lap_telemetry: dict, ref_telemetry: Optional[dict] = None,
                track_name: str = "", car_name: str = "", lap_number: int = 0,
                lap_time_str: str = "", lap_time_ms: int = 0,
                ref_lap_time_str: str = "", ref_lap_time_ms: int = 0,
                sector_times_ms: Optional[List[int]] = None,
                ref_sector_times_ms: Optional[List[int]] = None,
                date_str: str = "", is_valid: bool = True,
                max_rpm: float = 0.0,
                corners: Optional[List[ca.Corner]] = None) -> LapReportResult:
        """
        Executa a análise aprofundada da volta e retorna LapReportResult.
        """
        lap_ch = da.LapChannels(lap_telemetry or {})
        ref_ch = da.LapChannels(ref_telemetry or {}) if ref_telemetry else None

        # Comprimento da pista
        distances = lap_telemetry.get("distance") or []
        track_length = max(distances) if distances else lap_ch.lap_length_m

        # Resolve curvas
        if not corners:
            corners = self._resolve_corners(track_name, lap_telemetry, track_length)

        # Tempos gerais
        lap_time_s = (float(lap_time_ms) / 1000.0 if lap_time_ms > 0
                      else (lap_ch.times[-1] if lap_ch.times else 0.0))
        ref_time_s = (float(ref_lap_time_ms) / 1000.0 if ref_lap_time_ms > 0
                      else (ref_ch.times[-1] if ref_ch and ref_ch.times else None))
        
        delta_lap_s = (lap_time_s - ref_time_s) if (ref_time_s and ref_time_s > 0) else None

        # Setores
        lap_sec_s: List[Optional[float]] = []
        if sector_times_ms and any(s > 0 for s in sector_times_ms):
            lap_sec_s = [float(s) / 1000.0 if s > 0 else None for s in sector_times_ms[:3]]
        
        ref_sec_s: List[Optional[float]] = []
        if ref_sector_times_ms and any(s > 0 for s in ref_sector_times_ms):
            ref_sec_s = [float(s) / 1000.0 if s > 0 else None for s in ref_sector_times_ms[:3]]

        sec_deltas: List[Optional[float]] = []
        for i in range(3):
            ls = lap_sec_s[i] if i < len(lap_sec_s) else None
            rs = ref_sec_s[i] if i < len(ref_sec_s) else None
            sec_deltas.append((ls - rs) if (ls is not None and rs is not None) else None)

        # Análise de Curvas
        comparisons = ca.compare_laps(lap_telemetry, ref_telemetry or {}, corners, track_length)

        corner_rows: List[CornerSummaryRow] = []
        positives: List[ReportItem] = []
        negatives: List[ReportItem] = []
        improvements: List[ReportItem] = []
        total_potential_gain = 0.0
        corners_with_losses = 0
        corners_with_gains = 0

        has_ref = bool(ref_telemetry and ref_telemetry.get("distance"))

        for cmp_ in comparisons:
            c = cmp_.corner
            nome = c.name or f"C{c.index}"
            direcao = c.direction or ("D" if c.direction == "R" else ("E" if c.direction == "L" else ""))
            
            lap_dt = cmp_.lap.section_time
            ref_dt = cmp_.ref.section_time if cmp_.ref else None
            d_time = cmp_.delta_time

            lap_vmin = cmp_.lap.v_min
            ref_vmin = cmp_.ref.v_min if cmp_.ref else None
            d_vmin = cmp_.delta_v_min

            lap_brake = cmp_.lap.braking_point_m
            ref_brake = cmp_.ref.braking_point_m if cmp_.ref else None
            d_brake = cmp_.delta_braking_m

            lap_thr = cmp_.lap.throttle_point_m
            ref_thr = cmp_.ref.throttle_point_m if cmp_.ref else None
            d_thr = cmp_.delta_throttle_m

            lap_gear = da.gear_at(lap_ch, cmp_.lap.v_min_m) if cmp_.lap.v_min_m else None
            ref_gear = (da.gear_at(ref_ch, cmp_.ref.v_min_m)
                        if (ref_ch and cmp_.ref and cmp_.ref.v_min_m) else None)

            corner_rows.append(CornerSummaryRow(
                index=c.index,
                name=nome,
                direction=direcao,
                lap_time_s=lap_dt,
                ref_time_s=ref_dt,
                delta_time_s=d_time,
                lap_v_min=lap_vmin,
                ref_v_min=ref_vmin,
                delta_v_min=d_vmin,
                lap_brake_m=lap_brake,
                ref_brake_m=ref_brake,
                delta_brake_m=d_brake,
                lap_throttle_m=lap_thr,
                ref_throttle_m=ref_thr,
                delta_throttle_m=d_thr,
                lap_gear=lap_gear,
                ref_gear=ref_gear
            ))

            # Velocidade de saída na curva
            fim_m = c.end * track_length
            v_exit_lap = da.speed_at(lap_ch, fim_m)
            v_exit_ref = da.speed_at(ref_ch, fim_m) if ref_ch else None
            d_exit = (v_exit_lap - v_exit_ref) if (v_exit_lap is not None and v_exit_ref is not None) else None

            # Curva de gás cheio (flat out)
            i0, i1 = lap_ch.corner_window(c, track_length)
            is_flat_lap = da.is_flat_out(lap_ch, i0, i1)
            j0, j1 = ref_ch.corner_window(c, track_length) if ref_ch else (0, 0)
            is_flat_ref = da.is_flat_out(ref_ch, j0, j1) if ref_ch else None

            # Desvio de linha
            desvio_m = (da.line_deviation_m(lap_ch, ref_ch, c.start * track_length, fim_m)
                        if ref_ch else None)

            # Classificação: Positivo vs Negativo vs Oportunidade de Melhoria
            if has_ref and d_time is not None:
                if d_time <= -0.05:  # GANHO DE TEMPO
                    corners_with_gains += 1
                    motivos = []
                    metricas = [f"Tempo ganho: {abs(d_time):.2f}s"]

                    if d_vmin is not None and d_vmin >= 2.0:
                        motivos.append(f"carregou {d_vmin:.0f} km/h a mais no ápice ({lap_vmin:.0f} vs {ref_vmin:.0f} km/h)")
                        metricas.append(f"V.min: +{d_vmin:.1f} km/h")

                    if d_exit is not None and d_exit >= 3.0:
                        motivos.append(f"saiu {d_exit:.0f} km/h mais rápido para a reta")
                        metricas.append(f"V.saída: +{d_exit:.0f} km/h")

                    if d_brake is not None and d_brake >= 8.0:
                        motivos.append(f"frenagem mais profunda e assertiva (+{d_brake:.0f}m)")
                        metricas.append(f"Freio: +{d_brake:.0f}m")

                    if d_thr is not None and d_thr <= -10.0:
                        motivos.append(f"retomou aceleração plena {abs(d_thr):.0f}m mais cedo")
                        metricas.append(f"Gás: {d_thr:.0f}m")

                    if not motivos:
                        motivos.append("contorno fluido e sem perdas de aderência")

                    desc = f"Execução superior na {nome}. Você " + " e ".join(motivos) + "."
                    positives.append(ReportItem(
                        category="positive",
                        title=f"{nome}: Ganho de {abs(d_time):.2f}s",
                        description=desc,
                        why="Velocidade de rolagem preservada e excelente tração de saída sem desestabilizar o carro.",
                        corner_index=c.index,
                        corner_name=nome,
                        metric_detail=" | ".join(metricas),
                        time_delta_s=d_time
                    ))

                elif d_time >= 0.06:  # PERDA DE TEMPO
                    corners_with_losses += 1
                    total_potential_gain += d_time
                    
                    causas = []
                    whys = []
                    actions = []
                    metricas = [f"Perda: +{d_time:.2f}s"]

                    # 1. Frenagem
                    if d_brake is not None:
                        if d_brake <= -8.0:
                            causas.append(f"freou {abs(d_brake):.0f}m antes do ponto ideal")
                            whys.append(
                                f"Frenagem antecipada ({lap_brake:.0f}m vs {ref_brake:.0f}m): desacelerar precocemente "
                                f"faz o carro percorrer metros valiosos a baixa velocidade na reta final de aproximação, "
                                f"derrubando o tempo do trecho antes mesmo de chegar na curva."
                            )
                            actions.append(f"Adie o ponto de frenagem em ~{abs(d_brake):.0f}m tomando referências visuais da pista (placas de metragem ou início de zebras).")
                            metricas.append(f"Freio: {d_brake:+.0f}m")
                        elif d_brake >= 10.0:
                            causas.append(f"passou do ponto de frenagem (+{d_brake:.0f}m)")
                            whys.append(
                                f"Frenagem excessivamente tardia (+{d_brake:.0f}m): entrar com velocidade excessiva "
                                f"sobrecarregou o eixo dianteiro, impedindo a rotação do carro para o ápice (over-braking/trail braking comprometido) "
                                f"e forçando uma trajetória espalhada."
                            )
                            actions.append(f"Antecipe a frenagem em ~{d_brake * 0.7:.0f}m para estabilizar o chassi antes de começar a virar o volante.")
                            metricas.append(f"Freio: {d_brake:+.0f}m")

                    # 2. Velocidade no Ápice
                    if d_vmin is not None and d_vmin <= -2.0:
                        causas.append(f"perdeu {abs(d_vmin):.0f} km/h no ápice ({lap_vmin:.0f} vs {ref_vmin:.0f} km/h)")
                        whys.append(
                            f"Velocidade mínima deficitária no ápice ({lap_vmin:.0f} km/h vs {ref_vmin:.0f} km/h da referência): "
                            f"o carro foi sobre-freado antes da curva ou o pedal de freio foi mantido forte demais "
                            f"durante o contorno, matando a inércia e a rolagem (rolling speed) na zebra interna."
                        )
                        actions.append("Pratique o alívio progressivo do freio (trail braking) para entrar soltando o pedal conforme vira a direção, mantendo o carro rolando mais rápido.")
                        metricas.append(f"V.min: {d_vmin:+.1f} km/h")

                    # 3. Retomada de Aceleração
                    if d_thr is not None and d_thr >= 10.0:
                        causas.append(f"abriu o acelerador {d_thr:.0f}m mais tarde")
                        whys.append(
                            f"Retomada tardia de aceleração (+{d_thr:.0f}m): a demora para aplicar 100% de acelerador "
                            f"sacrifica a velocidade de saída. Numa curva que precede uma reta, cada km/h a menos na saída "
                            f"é tempo acumulado e perdido ao longo de toda a extensão da reta seguinte."
                        )
                        actions.append("Antecipe o direcionamento do carro no ápice e desenrole o volante mais cedo para abrir o gás com confiança.")
                        metricas.append(f"Retomada: +{d_thr:.0f}m")

                    # 4. Marcha no ápice
                    if lap_gear and ref_gear and lap_gear != ref_gear:
                        causas.append(f"usou {lap_gear}ª marcha onde a referência usou {ref_gear}ª")
                        if lap_gear > ref_gear:
                            whys.append(
                                f"Marcha muito alta no ápice ({lap_gear}ª vs {ref_gear}ª): o motor caiu fora da faixa "
                                f"de torque máximo, resultando em aceleração lenta na saída da curva."
                            )
                            actions.append(f"Reduza para a {ref_gear}ª marcha para manter o motor na faixa útil de rotação e obter tração imediata.")
                        else:
                            whys.append(
                                f"Marcha muito baixa ({lap_gear}ª vs {ref_gear}ª): tornou a traseira arisca e "
                                f"forçou uma troca rápida de marcha logo na saída, perdendo tração."
                            )
                            actions.append(f"Teste contornar em {ref_gear}ª marcha para maior estabilidade e tração mais progressiva.")
                        metricas.append(f"Marcha: {lap_gear}ª x {ref_gear}ª")

                    # 5. Hesitação em curva rápida (Flat Out)
                    if is_flat_ref is True and is_flat_lap is False:
                        causas.append("acionou o freio em curva que é feita de pé cravado (flat out)")
                        whys.append(
                            "Hesitação em curva rápida: a referência contorna sem tocar no freio. O toque no freio "
                            "destruiu a pressão aerodinâmica e causou perda massiva de velocidade média."
                        )
                        actions.append("Confie na carga aerodinâmica do carro: faça a curva de pé cravado ou realize no máximo uma sutil modulação de acelerador (lift), sem pisar no freio.")
                        metricas.append("Referência: flat out (sem freio)")

                    # 6. Desvio de linha
                    if desvio_m is not None and desvio_m >= 1.5:
                        causas.append(f"traçado {desvio_m:.1f}m afastado da linha ideal")
                        whys.append(
                            f"Erro de posicionamento ({desvio_m:.1f}m fora da linha): não utilizar toda a largura da pista "
                            f"ou errar a tangência da zebra interna fecha o raio da curva e exige mais ângulo de volante, "
                            f"aumentando o arrasto dos pneus."
                        )
                        actions.append("Utilize a zebra interna no ápice e deixe o carro espalhar até a zebra externa na saída para maximizar o raio da curva.")
                        metricas.append(f"Traçado: {desvio_m:.1f}m fora")

                    if not causas:
                        causas.append("ritmo geral inferior no contorno da curva")
                        whys.append("Pequenas imprecisões no contorno e posicionamento resultaram em perda de tempo.")
                        actions.append("Revise a telemetria do setor para alinhar velocidade de entrada e ponto de reaceleração.")

                    # Item negativo
                    negatives.append(ReportItem(
                        category="negative",
                        title=f"{nome}: Perda de +{d_time:.2f}s",
                        description=f"Prejuízo na {nome}: " + " e ".join(causas[:2]) + ".",
                        why=" ".join(whys[:2]),
                        action=actions[0] if actions else "",
                        corner_index=c.index,
                        corner_name=nome,
                        metric_detail=" | ".join(metricas),
                        time_delta_s=d_time,
                        potential_gain_s=d_time
                    ))

                    # Item detalhado de melhoria
                    improvements.append(ReportItem(
                        category="improvement",
                        title=f"{nome} ({direcao}) — Oportunidade de Ganho: +{d_time:.2f}s",
                        description=f"Na {nome}, você perdeu {d_time:.2f}s para a referência.",
                        why=" ".join(whys),
                        action=" ".join(actions),
                        corner_index=c.index,
                        corner_name=nome,
                        metric_detail=" | ".join(metricas),
                        time_delta_s=d_time,
                        potential_gain_s=d_time
                    ))

            else:
                # Análise solo (sem volta de referência)
                if lap_vmin is not None:
                    if lap_vmin < 60.0 and (not lap_brake):
                        pass

        # Ordenar os negativos e melhorias pelo tempo em jogo (maior perda primeiro)
        negatives.sort(key=lambda item: -(item.time_delta_s or 0.0))
        improvements.sort(key=lambda item: -(item.time_delta_s or 0.0))
        positives.sort(key=lambda item: (item.time_delta_s or 0.0))

        # -------------------------------------------------------------------
        # Análise Global da Técnica de Pilotagem
        # -------------------------------------------------------------------
        techniques: List[ReportItem] = []

        # 1. Pedais: Sobreposição
        overlap = da.brake_throttle_overlap(lap_ch)
        if overlap is not None:
            if overlap >= 0.10:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Sobreposição Involuntária de Freio e Acelerador",
                    description=f"Você manteve acelerador e freio acionados ao mesmo tempo em {overlap * 100:.0f}% das frenagens.",
                    why="Pisar no acelerador enquanto ainda freia cria atrito parasita, superaquece os discos de freio e impede a dianteira de mergulhar adequadamente.",
                    action="Certifique-se de soltar 100% o pé do acelerador antes de acionar o pedal de freio."
                ))
            elif overlap <= 0.02:
                techniques.append(ReportItem(
                    category="technique",
                    title="✅ Transição Limpa de Pedais",
                    description="Separação impecável entre freio e acelerador durante as frenagens.",
                    why="Pedais bem dissociados evitam atrito parasita e permitem transferência de peso limpa."
                ))

        # 2. Pedais: Modulação e Soltura de Freio
        b_rep = da.brake_release_report(lap_ch)
        if b_rep:
            if b_rep.fraction >= 0.35:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Repisada no Pedal de Freio (Jitter)",
                    description=f"Detectada repisada ou modulação irregular em {b_rep.jitter_zones} de {b_rep.zones} frenagens da volta.",
                    why="Voltar a pressionar o freio depois de já ter iniciado o alívio desestabiliza a plataforma de suspensão na entrada de curva.",
                    action="Pressione o freio com força máxima no início da frenagem e alivie progressivamente até o ápice, sem hesitar ou repisar."
                ))
            if b_rep.abrupt_fraction is not None and b_rep.abrupt_fraction >= 0.35:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Soltura Abrupta do Freio na Entrada de Curva",
                    description=f"Pedal de freio foi largado de uma vez em {b_rep.abrupt_zones} de {b_rep.zones} frenagens (menos de 0.15s de transição).",
                    why="Tirar o pé do freio repentinamente faz a frente do carro subir de imediato, aliviando o peso dos pneus dianteiros justo quando você precisa que eles façam a curva, gerando subesterço.",
                    action="Mantenha o pé apoiado de leve no freio (trail braking suave) durante a tomada de curva até a tangente da zebra."
                ))
            if b_rep.fraction < 0.20 and (b_rep.abrupt_fraction is None or b_rep.abrupt_fraction < 0.20):
                techniques.append(ReportItem(
                    category="technique",
                    title="✅ Modulação Exemplar de Freio (Trail Braking)",
                    description="Alívio progressivo e controlado da pressão de freio em direção ao ápice das curvas.",
                    why="Mantém a carga ideal nos pneus dianteiros para direcionamento preciso sem desestabilizar a traseira."
                ))

        # 3. Volante: Subesterço e Suavidade
        sub = da.understeer_fraction(lap_ch)
        if sub is not None:
            if sub >= 0.30:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Excesso de Ângulo de Volante (Subesterço Forçado)",
                    description=f"Subesterço evidente em {sub * 100:.0f}% do tempo de curva (muito esterço sem ganho de Força G lateral).",
                    why="Virar o volante além do limite de aderência do pneu dianteiro satura o atrito, aquece a borracha e faz o carro escorregar em vez de contornar a curva.",
                    action="Abra a mão no volante (diminua o esterço) e use a transferência de peso dos pedais para fazer o carro rotacionar."
                ))
            elif sub <= 0.10:
                techniques.append(ReportItem(
                    category="technique",
                    title="✅ Direção Precisa e Eficiente",
                    description="Ângulo de volante proporcional à aderência lateral, sem arrasto excessivo de pneus.",
                    why="Minimiza a resistência ao rolamento e preserva a vida útil dos pneus dianteiros."
                ))

        # 4. Câmbio e Motor
        sh_rep = da.shift_report(lap_ch, max_rpm)
        if sh_rep:
            if sh_rep.early_fraction >= 0.30 and sh_rep.worst_early_rpm:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Trocas de Marcha Prematuras (Short-Shifting)",
                    description=f"{sh_rep.early} de {sh_rep.upshifts} trocas de marcha foram feitas antes da faixa ideal de potência.",
                    why=f"A pior troca ocorreu a {sh_rep.worst_early_rpm:.0f} RPM (onde o motor ainda não entrega o pico de potência).",
                    action=f"Estique a marcha até perto de {int(max_rpm / 100) * 100} RPM antes de subir marcha nas retas."
                ))
            total_samples = max(len(lap_ch), 1)
            if sh_rep.on_limiter / total_samples >= 0.02:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Retardo na Subida de Marcha (Batendo no Limitador)",
                    description=f"Motor permaneceu no limitador de giro em {sh_rep.on_limiter / total_samples * 100:.1f}% da volta.",
                    why="Bater no corte de giro estanca a aceleração e interrompe o ganho de velocidade máxima.",
                    action="Antecipe ligeiramente a troca ascendente para não deixar o motor travar no corte."
                ))

        # 5. Eletrônica (ABS / TC)
        abs_arr = lap_telemetry.get("abs_intervention") or []
        tc_arr = lap_telemetry.get("tc_intervention") or []
        if abs_arr:
            forte_abs = sum(1 for v in abs_arr if v >= 0.55)
            if forte_abs / len(abs_arr) >= 0.04:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Intervenção Frequente de ABS",
                    description=f"ABS acionado com força em {forte_abs / len(abs_arr) * 100:.1f}% da volta.",
                    why="Frear com pressão excessiva ativa a pulsação do ABS, aumentando a distância de frenagem em relação a um piloto modulando no limiar de atrito.",
                    action="Aplique pressão firme, mas diminua o pico máximo no pedal para manter o freio logo abaixo do limiar de bloqueio."
                ))
        if tc_arr:
            forte_tc = sum(1 for v in tc_arr if v >= 0.55)
            if forte_tc / len(tc_arr) >= 0.04:
                techniques.append(ReportItem(
                    category="technique",
                    title="⚠️ Cortes Repetidos do Controle de Tração (TC)",
                    description=f"TC cortou potência com intensidade em {forte_tc / len(tc_arr) * 100:.1f}% da volta.",
                    why="Pisar no acelerador bruscamente com o volante ainda muito virado aciona o corte eletrônico, prejudicando o arranque na saída de curva.",
                    action="Desenrole o volante antes de aplicar aceleração plena e seja mais progressivo no curso inicial do pedal."
                ))

        return LapReportResult(
            track=track_name or "Pista",
            car=car_name or "Carro",
            lap_number=lap_number,
            lap_time_str=lap_time_str or "0:00.000",
            lap_time_s=lap_time_s,
            ref_lap_time_str=ref_lap_time_str,
            ref_lap_time_s=ref_time_s,
            delta_lap_s=delta_lap_s,
            date_str=date_str,
            is_valid=is_valid,
            lap_sectors_s=lap_sec_s,
            ref_sectors_s=ref_sec_s,
            sector_deltas_s=sec_deltas,
            corner_rows=corner_rows,
            positives=positives,
            negatives=negatives,
            improvements=improvements,
            techniques=techniques,
            total_potential_gain_s=total_potential_gain,
            corners_with_losses=corners_with_losses,
            corners_with_gains=corners_with_gains
        )

    # -----------------------------------------------------------------------
    # Formatador Markdown (.md)
    # -----------------------------------------------------------------------

    def format_markdown(self, res: LapReportResult) -> str:
        """Gera o relatório em formato Markdown ricamente formatado."""
        lines = []

        # Título
        lines.append(f"# 🏁 Relatório de Desempenho e Engenharia de Telemetria")
        lines.append("")
        lines.append(f"> **ApexView Analytics** — Análise Turn-by-Turn e Diagnóstico de Pilotagem")
        lines.append("")

        # Metadados em Tabela
        lines.append("## 📋 Informações da Sessão")
        lines.append("")
        lines.append("| Parâmetro | Volta Analisada | Volta de Referência | Diferença (Delta) |")
        lines.append("| :--- | :--- | :--- | :--- |")
        lines.append(f"| **Pista** | `{res.track}` | `{res.track}` | — |")
        lines.append(f"| **Carro** | `{res.car}` | `{res.car}` | — |")
        lines.append(f"| **Volta Nº** | Volta {res.lap_number} | {'Referência' if res.ref_lap_time_str else '—'} | — |")
        
        status_val = "Válida" if res.is_valid else "Inválida (Corte/Penalidade)"
        delta_str = f"{res.delta_lap_s:+.3f}s" if res.delta_lap_s is not None else "—"
        ref_time = res.ref_lap_time_str or "—"
        lines.append(f"| **Tempo de Volta** | **`{res.lap_time_str}`** ({status_val}) | `{ref_time}` | **`{delta_str}`** |")

        # Setores
        for i in range(3):
            s_lap = f"{res.lap_sectors_s[i]:.3f}s" if i < len(res.lap_sectors_s) and res.lap_sectors_s[i] else "—"
            s_ref = f"{res.ref_sectors_s[i]:.3f}s" if i < len(res.ref_sectors_s) and res.ref_sectors_s[i] else "—"
            s_d = f"{res.sector_deltas_s[i]:+.3f}s" if i < len(res.sector_deltas_s) and res.sector_deltas_s[i] is not None else "—"
            lines.append(f"| **Setor {i + 1}** | `{s_lap}` | `{s_ref}` | `{s_d}` |")

        if res.date_str:
            lines.append(f"| **Data da Gravação** | {res.date_str} | — | — |")
        lines.append("")

        # Resumo Executivo
        lines.append("## 📊 Resumo Executivo")
        lines.append("")
        if res.delta_lap_s is not None:
            if res.delta_lap_s <= -0.001:
                lines.append(f"🎉 **Excelente volta!** Você bateu a volta de referência em **{abs(res.delta_lap_s):.3f} segundos**.")
            elif res.delta_lap_s <= 0.15:
                lines.append(f"⏱️ **Ritmo muito parelho:** volta concluída a apenas **+{res.delta_lap_s:.3f} segundos** da referência.")
            else:
                lines.append(f"⚠️ **Potencial na mesa:** você ficou a **+{res.delta_lap_s:.3f} segundos** da referência.")
        
        if res.total_potential_gain_s > 0:
            lines.append(f"- **Tempo Recuperável Identificado:** aproximadamente **~{res.total_potential_gain_s:.2f}s** concentrados em **{res.corners_with_losses} curva(s)**.")
        if res.corners_with_gains > 0:
            lines.append(f"- **Curvas com Vantagem Positiva:** superou a referência em **{res.corners_with_gains} curva(s)**.")
        lines.append("")

        # -------------------------------------------------------------------
        # 1. PONTOS POSITIVOS
        # -------------------------------------------------------------------
        lines.append("## 🏆 Principais Pontos Positivos")
        lines.append("")
        if res.positives:
            for item in res.positives:
                lines.append(f"### {item.title}")
                lines.append(f"- **O que você fez bem:** {item.description}")
                if item.why:
                    lines.append(f"- **Por que funcionou:** {item.why}")
                if item.metric_detail:
                    lines.append(f"- **Métricas:** `{item.metric_detail}`")
                lines.append("")
        else:
            lines.append("> Nenhuma curva superou o benchmark de forma expressiva nesta volta. Concentre-se nas áreas de melhoria abaixo para destravar ritmo.")
            lines.append("")

        # -------------------------------------------------------------------
        # 2. PONTOS NEGATIVOS
        # -------------------------------------------------------------------
        lines.append("## ⚠️ Principais Pontos Negativos")
        lines.append("")
        if res.negatives:
            for item in res.negatives[:5]:  # Top 5 perdas
                lines.append(f"### {item.title}")
                lines.append(f"- **Onde o tempo foi embora:** {item.description}")
                if item.why:
                    lines.append(f"- **Causa do Prejuízo:** {item.why}")
                if item.metric_detail:
                    lines.append(f"- **Métricas da Perda:** `{item.metric_detail}`")
                lines.append("")
        else:
            lines.append("> Não foram detectadas perdas significativas em relação ao benchmark!")
            lines.append("")

        # -------------------------------------------------------------------
        # 3. ONDE MELHORAR E O PORQUÊ (DIAGNÓSTICO DETALHADO)
        # -------------------------------------------------------------------
        lines.append("## 🎯 Onde Melhorar e O Porquê (Diagnóstico Detalhado)")
        lines.append("")
        if res.improvements:
            lines.append("Aqui estão as prioridades ordenadas por ganho de tempo, com a explicação mecânica/física do que causou a perda e o ajuste a executar:")
            lines.append("")
            for rank, item in enumerate(res.improvements[:5], start=1):
                lines.append(f"### #{rank} — {item.title}")
                lines.append(f"**1. O que aconteceu na pista:**")
                lines.append(f"{item.description} ({item.metric_detail})")
                lines.append("")
                lines.append(f"**2. O do porquê (Explicação Técnica da Telemetria):**")
                lines.append(f"{item.why}")
                lines.append("")
                lines.append(f"**3. Ação Prática para a Próxima Volta:**")
                lines.append(f"👉 **{item.action}**")
                lines.append("")
                lines.append("---")
                lines.append("")
        else:
            lines.append("> Volta consistente sem pontos críticos de perda imediata.")
            lines.append("")

        # -------------------------------------------------------------------
        # 4. TÉCNICA GERAL DE PILOTAGEM
        # -------------------------------------------------------------------
        lines.append("## 🕹️ Avaliação Geral da Técnica de Pilotagem")
        lines.append("")
        if res.techniques:
            for tech in res.techniques:
                lines.append(f"### {tech.title}")
                lines.append(f"{tech.description}")
                if tech.why:
                    lines.append(f"- *Diagnóstico:* {tech.why}")
                if tech.action:
                    lines.append(f"- *Ajuste recomendado:* {tech.action}")
                lines.append("")
        else:
            lines.append("> Dados de canais de pilotagem insuficientes para uma avaliação global.")
            lines.append("")

        # -------------------------------------------------------------------
        # 5. TABELA COMPLETA TURN-BY-TURN
        # -------------------------------------------------------------------
        lines.append("## 📊 Tabela de Telemetria Curva a Curva (Turn-by-Turn)")
        lines.append("")
        lines.append("| Curva | Tempo Volta | Tempo Ref | Delta | V.min Volta | V.min Ref | Delta V.min | Ponto Freio | Retomada | Marcha |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for row in res.corner_rows:
            dt_str = f"{row.delta_time_s:+.2f}s" if row.delta_time_s is not None else "—"
            lap_t = f"{row.lap_time_s:.2f}s" if row.lap_time_s is not None else "—"
            ref_t = f"{row.ref_time_s:.2f}s" if row.ref_time_s is not None else "—"
            
            lap_vm = f"{row.lap_v_min:.0f}" if row.lap_v_min is not None else "—"
            ref_vm = f"{row.ref_v_min:.0f}" if row.ref_v_min is not None else "—"
            d_vm = f"{row.delta_v_min:+.0f} km/h" if row.delta_v_min is not None else "—"
            
            d_brk = f"{row.delta_brake_m:+.0f}m" if row.delta_brake_m is not None else "—"
            d_thr = f"{row.delta_throttle_m:+.0f}m" if row.delta_throttle_m is not None else "—"
            
            g_str = f"{row.lap_gear or '-'}ª" if not row.ref_gear else f"{row.lap_gear or '-'}ª / {row.ref_gear}ª"
            
            lines.append(f"| **{row.name}** ({row.direction}) | {lap_t} | {ref_t} | `{dt_str}` | {lap_vm} | {ref_vm} | `{d_vm}` | `{d_brk}` | `{d_thr}` | {g_str} |")

        lines.append("")
        lines.append("---")
        lines.append("*Gerado automaticamente pelo ApexView Telemetry & Race Engineering System.*")
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Formatador Texto Puro (.txt)
    # -----------------------------------------------------------------------

    def format_plain_text(self, res: LapReportResult) -> str:
        """Gera o relatório em formato texto puro (.txt) monoespaçado e limpo."""
        w = 78
        sep = "=" * w
        sub_sep = "-" * w
        lines = []

        lines.append(sep)
        lines.append("APEXVIEW — RELATÓRIO DE DESEMPENHO E ENGENHARIA DE TELEMETRIA".center(w))
        lines.append("Análise Pós-Sessão: Pontos Positivos, Negativos e Onde Melhorar".center(w))
        lines.append(sep)
        lines.append("")

        # Metadados
        lines.append("1. INFORMAÇÕES DA SESSÃO")
        lines.append(sub_sep)
        lines.append(f"Pista              : {res.track}")
        lines.append(f"Carro              : {res.car}")
        lines.append(f"Volta Analisada    : Volta {res.lap_number} ({'Válida' if res.is_valid else 'INVÁLIDA'})")
        lines.append(f"Tempo da Volta     : {res.lap_time_str}")
        if res.ref_lap_time_str:
            lines.append(f"Tempo Referência   : {res.ref_lap_time_str}")
        if res.delta_lap_s is not None:
            lines.append(f"Diferença Total    : {res.delta_lap_s:+.3f} segundos")
        if res.date_str:
            lines.append(f"Data da Gravação   : {res.date_str}")
        
        # Setores
        for i in range(3):
            s_lap = f"{res.lap_sectors_s[i]:.3f}s" if i < len(res.lap_sectors_s) and res.lap_sectors_s[i] else "--"
            s_ref = f"{res.ref_sectors_s[i]:.3f}s" if i < len(res.ref_sectors_s) and res.ref_sectors_s[i] else "--"
            s_d = f"{res.sector_deltas_s[i]:+.3f}s" if i < len(res.sector_deltas_s) and res.sector_deltas_s[i] is not None else "--"
            lines.append(f"  * Setor {i + 1}          : {s_lap:<10} (Ref: {s_ref:<10} Delta: {s_d})")
        lines.append("")

        # Resumo
        lines.append("2. RESUMO EXECUTIVO")
        lines.append(sub_sep)
        if res.total_potential_gain_s > 0:
            lines.append(f"Tempo Potencial Recuperável: ~{res.total_potential_gain_s:.2f}s em {res.corners_with_losses} curva(s).")
        if res.corners_with_gains > 0:
            lines.append(f"Curvas com Ganho Positivo  : {res.corners_with_gains} curva(s) acima do benchmark.")
        lines.append("")

        # Positivos
        lines.append("3. PRINCIPAIS PONTOS POSITIVOS")
        lines.append(sub_sep)
        if res.positives:
            for item in res.positives:
                lines.append(f"[+] {item.title}")
                lines.append(f"    Execução   : {item.description}")
                if item.why:
                    lines.append(f"    Por quê    : {item.why}")
                if item.metric_detail:
                    lines.append(f"    Métricas   : {item.metric_detail}")
                lines.append("")
        else:
            lines.append("    Nenhum ganho expressivo detectado em relação ao benchmark.")
            lines.append("")

        # Negativos
        lines.append("4. PRINCIPAIS PONTOS NEGATIVOS")
        lines.append(sub_sep)
        if res.negatives:
            for item in res.negatives[:5]:
                lines.append(f"[-] {item.title}")
                lines.append(f"    Problema   : {item.description}")
                if item.why:
                    lines.append(f"    Causa      : {item.why}")
                if item.metric_detail:
                    lines.append(f"    Métricas   : {item.metric_detail}")
                lines.append("")
        else:
            lines.append("    Nenhuma perda significativa identificada.")
            lines.append("")

        # Onde Melhorar e O Porquê
        lines.append("5. ONDE MELHORAR E O PORQUÊ (DIAGNÓSTICO DETALHADO)")
        lines.append(sub_sep)
        if res.improvements:
            for rank, item in enumerate(res.improvements[:5], start=1):
                lines.append(f"[{rank}] {item.title}")
                lines.append(f"    O QUE HOUVE: {item.description}")
                lines.append(f"    POR QUÊ    : {item.why}")
                lines.append(f"    COMO AGIR  : {item.action}")
                lines.append("")
        else:
            lines.append("    Nenhuma curva crítica necessitando de intervenção imediata.")
            lines.append("")

        # Técnica de Pilotagem
        lines.append("6. AVALIAÇÃO DA TÉCNICA DE PILOTAGEM")
        lines.append(sub_sep)
        if res.techniques:
            for tech in res.techniques:
                lines.append(f"* {tech.title}")
                lines.append(f"  {tech.description}")
                if tech.why:
                    lines.append(f"  Diagnóstico : {tech.why}")
                if tech.action:
                    lines.append(f"  Ação        : {tech.action}")
                lines.append("")
        else:
            lines.append("    Dados insuficientes para avaliação global.")
            lines.append("")

        # Tabela Turn-by-Turn
        lines.append("7. TABELA TURN-BY-TURN")
        lines.append(sub_sep)
        header = f"{'Curva':<8} {'Tempo':<8} {'Ref':<8} {'Delta':<9} {'Vmin':<6} {'Vref':<6} {'dVmin':<8} {'dFreio':<8} {'dRetom':<8} {'Marcha':<6}"
        lines.append(header)
        lines.append("-" * len(header))
        for row in res.corner_rows:
            t_s = f"{row.lap_time_s:.2f}" if row.lap_time_s else "--"
            tr_s = f"{row.ref_time_s:.2f}" if row.ref_time_s else "--"
            dt_s = f"{row.delta_time_s:+.2f}" if row.delta_time_s is not None else "--"
            vm = f"{row.lap_v_min:.0f}" if row.lap_v_min else "--"
            vmr = f"{row.ref_v_min:.0f}" if row.ref_v_min else "--"
            dvm = f"{row.delta_v_min:+.0f}" if row.delta_v_min is not None else "--"
            dbrk = f"{row.delta_brake_m:+.0f}m" if row.delta_brake_m is not None else "--"
            dthr = f"{row.delta_throttle_m:+.0f}m" if row.delta_throttle_m is not None else "--"
            g_s = f"{row.lap_gear or '-'}" if not row.ref_gear else f"{row.lap_gear or '-'}/{row.ref_gear}"
            lines.append(f"{row.name:<8} {t_s:<8} {tr_s:<8} {dt_s:<9} {vm:<6} {vmr:<6} {dvm:<8} {dbrk:<8} {dthr:<8} {g_s:<6}")

        lines.append(sep)
        lines.append("ApexView Telemetry System".center(w))
        lines.append(sep)
        return "\n".join(lines)
