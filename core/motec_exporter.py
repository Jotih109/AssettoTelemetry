"""
Módulo de Conversão e Exportação MoTeC i2 (.ld) para o ApexView.

Re-exporta a API da pasta dedicada core.motec para compatibilidade direta com
qualquer importador do ecossistema ApexView.
"""

from .motec import (
    ChannelData,
    MotecExporter,
    MotecLDWriter,
    MotecLDXWriter,
    SessionMetadata,
    TARGET_FREQ_HZ,
    export_motec_ld,
    map_telemetry_to_motec_channels,
    read_ld_file,
)


def export_lap_to_motec(
    source,
    output_ld_path: str,
    track: str = None,
    car: str = None,
    driver: str = None,
    lap_number: int = None,
    **kwargs,
) -> bool:
    """
    Função facilitadora para exportar uma volta para o formato MoTeC i2 (.ld).

    :param source: Volta do catálogo LapRecord, dict, path de arquivo ou lista de TelemetryState.
    :param output_ld_path: Destino do arquivo .ld.
    :param track: Nome da pista.
    :param car: Nome do carro.
    :param driver: Nome do piloto.
    :param lap_number: Número da volta.
    :return: True em caso de sucesso, False caso contrário.
    """
    exporter = MotecExporter()
    return exporter.export(
        source,
        output_ld_path,
        track=track,
        car=car,
        driver=driver,
        lap_number=lap_number,
        **kwargs,
    )


__all__ = [
    "MotecExporter",
    "export_motec_ld",
    "export_lap_to_motec",
    "MotecLDWriter",
    "MotecLDXWriter",
    "SessionMetadata",
    "ChannelData",
    "map_telemetry_to_motec_channels",
    "read_ld_file",
    "TARGET_FREQ_HZ",
]
