"""
Orquestrador principal de exportação MoTeC i2 (.ld e .ldx).

Unifica a leitura de fontes variadas de dados de telemetria do ApexView
(catálogo LapLibrary, arquivos .json.gz, dicts de telemetria ou TelemetryState)
e coordena a escrita dos arquivos .ld e .ldx compatíveis com MoTeC i2 Pro.
"""

from __future__ import annotations

import datetime
import os
from typing import Any, Dict, List, Optional, Union

from ..lap_library import read_json_file
from .channel_mapping import TARGET_FREQ_HZ, map_telemetry_to_motec_channels
from .ld_writer import MotecLDWriter, SessionMetadata
from .ldx_writer import MotecLDXWriter


class MotecExporter:
    """Exportador de voltas e sessões para arquivos MoTeC i2 Pro (.ld / .ldx)."""

    def __init__(self, target_freq: float = TARGET_FREQ_HZ):
        self.target_freq = target_freq

    def export(
        self,
        source: Union[str, Dict[str, Any], List[Any]],
        output_ld_path: str,
        *,
        track: Optional[str] = None,
        car: Optional[str] = None,
        driver: Optional[str] = None,
        lap_number: Optional[int] = None,
        lap_time_s: Optional[float] = None,
        sector_times_s: Optional[List[float]] = None,
        session_name: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> bool:
        """
        Exporta os dados de uma volta para um arquivo .ld e seu arquivo .ldx acompanhante.

        :param source: Caminho do arquivo (.json.gz / .json), dict com telemetria,
                       ou lista de instâncias TelemetryState.
        :param output_ld_path: Caminho completo do arquivo de saída .ld.
        :param track: Nome da pista / circuito (opcional se contido no source).
        :param car: Nome do veículo / carro (opcional se contido no source).
        :param driver: Nome do piloto.
        :param lap_number: Número da volta.
        :param lap_time_s: Tempo de volta em segundos.
        :param sector_times_s: Tempos dos setores em segundos.
        :param session_name: Nome da sessão (Practice, Qualify, Race).
        :param comment: Comentário descritivo adicional.
        :return: True se a exportação foi bem-sucedida, False caso contrário.
        """
        try:
            telemetry, meta = self._parse_source(source)
            if not telemetry:
                print("[MotecExporter] Erro: Dados de telemetria vazios ou inválidos.")
                return False

            # Mescla metadados explícitos com os extraídos da fonte
            final_track = track or meta.get("track") or meta.get("track_name") or "Circuit"
            final_car = car or meta.get("car") or meta.get("car_name") or "RaceCar"
            final_driver = driver or meta.get("player_name") or meta.get("driver") or "ApexView Driver"
            final_lap_num = lap_number if lap_number is not None else meta.get("lap_number", 1)
            final_session = session_name or meta.get("session_type") or "ApexView Session"

            # Tempo e data
            ts_str = meta.get("timestamp")
            dt_obj = None
            if ts_str:
                try:
                    # ISO format ou prefixo
                    dt_obj = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Mapeia e reamostra canais para 60 Hz
            channels, total_duration_s = map_telemetry_to_motec_channels(
                telemetry, freq=self.target_freq
            )

            # Define tempo de volta para o .ldx
            effective_lap_time = lap_time_s
            if effective_lap_time is None or effective_lap_time <= 0:
                raw_lt = meta.get("lap_time_ms")
                if raw_lt and raw_lt > 0:
                    effective_lap_time = float(raw_lt) / 1000.0
                else:
                    effective_lap_time = total_duration_s

            # Setores
            effective_sectors = sector_times_s
            if not effective_sectors and "sector_times_ms" in meta:
                raw_sectors = meta["sector_times_ms"]
                if isinstance(raw_sectors, (list, tuple)) and any(s > 0 for s in raw_sectors):
                    effective_sectors = [float(s) / 1000.0 for s in raw_sectors]

            # 1. Garante que a pasta de destino exista
            os.makedirs(os.path.dirname(os.path.abspath(output_ld_path)), exist_ok=True)

            # 2. Escreve o arquivo binário .ld
            ld_meta = SessionMetadata(
                driver=str(final_driver),
                vehicle=str(final_car),
                venue=str(final_track),
                event_name=f"{final_track} - {final_car}",
                session_name=f"Lap {final_lap_num} ({final_session})",
                comment=comment or f"ApexView Telemetry Export - Lap {final_lap_num}",
                short_comment=f"V{final_lap_num} ApexView",
                date_time=dt_obj,
            )
            MotecLDWriter.write(output_ld_path, ld_meta, channels)

            # 3. Escreve o arquivo de marcas .ldx acompanhante
            base, _ = os.path.splitext(output_ld_path)
            ldx_path = base + ".ldx"
            MotecLDXWriter.write(
                ldx_path,
                lap_time_s=effective_lap_time,
                lap_number=int(final_lap_num),
                sector_times_s=effective_sectors,
            )

            return True

        except Exception as exc:
            import traceback
            print(f"[MotecExporter] Falha ao exportar MoTeC: {exc}")
            traceback.print_exc()
            return False

    def _parse_source(
        self, source: Union[str, Dict[str, Any], List[Any]]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Interpreta qualquer fonte válida suportada pelo ApexView."""
        meta: Dict[str, Any] = {}
        telemetry: Dict[str, Any] = {}

        # Caso 1: Caminho de arquivo no disco (.json.gz ou .json)
        if isinstance(source, str):
            if not os.path.exists(source):
                return {}, {}
            content = read_json_file(source)
            if not isinstance(content, dict):
                return {}, {}
            if "telemetry" in content:
                telemetry = content.get("telemetry") or {}
                meta = content.get("metadata") or {}
            else:
                telemetry = content

        # Caso 2: Dicionário Python
        elif isinstance(source, dict):
            if "telemetry" in source:
                telemetry = source.get("telemetry") or {}
                meta = source.get("metadata") or {}
            else:
                telemetry = source
                meta = {k: v for k, v in source.items() if not isinstance(v, (list, tuple))}

        # Caso 3: Lista de objetos TelemetryState ou dicts de amostras
        elif isinstance(source, (list, tuple)):
            telemetry, meta = self._convert_state_sequence(source)

        return telemetry, meta

    def _convert_state_sequence(
        self, states: Sequence[Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Converte uma sequência de objetos TelemetryState em dicionário de colunas."""
        if not states:
            return {}, {}

        telemetry: Dict[str, List[Any]] = {
            "times": [], "distance": [], "speed": [], "gas": [], "brake": [],
            "steer": [], "gear": [], "rpm": [], "g_lat": [], "g_lon": [],
            "g_vert": [], "abs_intervention": [], "tc_intervention": [],
            "car_x": [], "car_z": [],
            "tyre_pressure_fl": [], "tyre_pressure_fr": [], "tyre_pressure_rl": [], "tyre_pressure_rr": [],
            "tyre_temp_fl": [], "tyre_temp_fr": [], "tyre_temp_rl": [], "tyre_temp_rr": [],
        }

        first = states[0]
        meta: Dict[str, Any] = {
            "track": getattr(first, "track_name", ""),
            "car": getattr(first, "car_name", ""),
            "player_name": getattr(first, "player_name", ""),
            "session_type": getattr(first, "session_type", ""),
            "lap_number": getattr(first, "lap_number", 1),
        }

        t_cur = 0.0
        for i, s in enumerate(states):
            # Se tiver tempo decorrido ou simula a 60 Hz
            t_cur += 1.0 / self.target_freq
            telemetry["times"].append(t_cur)
            telemetry["distance"].append(getattr(s, "distance_traveled", float(i)))
            telemetry["speed"].append(getattr(s, "speed_kmh", 0.0))
            telemetry["gas"].append(getattr(s, "gas", 0.0))
            telemetry["brake"].append(getattr(s, "brake", 0.0))
            telemetry["steer"].append(getattr(s, "steer_angle", 0.0))
            telemetry["gear"].append(getattr(s, "gear", 1))
            telemetry["rpm"].append(getattr(s, "rpm", 0.0))
            telemetry["g_lat"].append(getattr(s, "g_lat", 0.0))
            telemetry["g_lon"].append(getattr(s, "g_lon", 0.0))
            telemetry["g_vert"].append(getattr(s, "g_vert", 1.0))
            telemetry["abs_intervention"].append(getattr(s, "abs_intervention", 0.0))
            telemetry["tc_intervention"].append(getattr(s, "tc_intervention", 0.0))
            telemetry["car_x"].append(getattr(s, "car_x", 0.0))
            telemetry["car_z"].append(getattr(s, "car_z", 0.0))

            tp = getattr(s, "tyre_pressure", [26.0, 26.0, 25.0, 25.0])
            if len(tp) >= 4:
                telemetry["tyre_pressure_fl"].append(tp[0])
                telemetry["tyre_pressure_fr"].append(tp[1])
                telemetry["tyre_pressure_rl"].append(tp[2])
                telemetry["tyre_pressure_rr"].append(tp[3])

            tt = getattr(s, "tyre_temp", [85.0, 85.0, 83.0, 83.0])
            if len(tt) >= 4:
                telemetry["tyre_temp_fl"].append(tt[0])
                telemetry["tyre_temp_fr"].append(tt[1])
                telemetry["tyre_temp_rl"].append(tt[2])
                telemetry["tyre_temp_rr"].append(tt[3])

        return telemetry, meta


def export_motec_ld(
    source: Union[str, Dict[str, Any], List[Any]],
    output_ld_path: str,
    **kwargs: Any,
) -> bool:
    """Função de conveniência para exportar telemetria no formato MoTeC i2 (.ld)."""
    exporter = MotecExporter()
    return exporter.export(source, output_ld_path, **kwargs)
