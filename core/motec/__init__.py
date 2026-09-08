"""
Pacote MoTeC i2 Exporter para o ApexView.

Exporta voltas de telemetria no formato binário MoTeC i2 (.ld) e arquivo
acompanhante de marcas XML (.ldx) compatíveis com o MoTeC i2 Pro.
"""

from .channel_mapping import TARGET_FREQ_HZ, map_telemetry_to_motec_channels
from .exporter import MotecExporter, export_motec_ld
from .ld_writer import (
    CHAN_FORMAT,
    CHAN_SIZE,
    ChannelData,
    EVENT_FORMAT,
    EVENT_SIZE,
    HEAD_FORMAT,
    HEAD_SIZE,
    LD_MARKER,
    MotecLDWriter,
    PRO_LOGGING_MAGIC,
    SessionMetadata,
    read_ld_file,
)
from .ldx_writer import MotecLDXWriter

__all__ = [
    "MotecExporter",
    "export_motec_ld",
    "MotecLDWriter",
    "MotecLDXWriter",
    "SessionMetadata",
    "ChannelData",
    "map_telemetry_to_motec_channels",
    "read_ld_file",
    "TARGET_FREQ_HZ",
    "HEAD_FORMAT",
    "HEAD_SIZE",
    "EVENT_FORMAT",
    "EVENT_SIZE",
    "CHAN_FORMAT",
    "CHAN_SIZE",
    "LD_MARKER",
    "PRO_LOGGING_MAGIC",
]
