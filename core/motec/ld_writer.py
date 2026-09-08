"""
Escritor binário do formato MoTeC i2 (.ld).

Implementa o layout binário de arquivos Logged Data (.ld) do MoTeC com suporte
completo ao recurso 'Pro Logging' (MoTeC i2 Pro).
"""

from __future__ import annotations

import datetime
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Union
import numpy as np


# Assinaturas e tamanhos do formato MoTeC LD
LD_MARKER = 0x40                 # 64 decimal
PRO_LOGGING_MAGIC = 0xC81A4      # Habilita MoTeC i2 Pro
DEVICE_SERIAL = 0x1F44
DEVICE_TYPE = b"ADL\x00\x00\x00\x00\x00"
DEVICE_VERSION = 420
DEVICE_MAGIC = 0xADB0

HEAD_FORMAT = '<' + (
    "I4x"     # 0x00: marker (0x40), 4 bytes padding
    "II"      # 0x08: first_channel_meta_ptr, first_channel_data_ptr
    "20x"     # 0x10: padding
    "I"       # 0x24: event_ptr
    "24x"     # 0x28: padding
    "HHH"     # 0x40: unknown1, unknown2, unknown3
    "I"       # 0x46: device serial
    "8s"      # 0x4A: device type
    "H"       # 0x52: device version
    "H"       # 0x54: unknown magic
    "I"       # 0x56: num_channels
    "4x"      # 0x5A: padding
    "16s"     # 0x5E: date ("DD/MM/YYYY")
    "16x"     # 0x6E: padding
    "16s"     # 0x7E: time ("HH:MM:SS")
    "16x"     # 0x8E: padding
    "64s"     # 0x9E: driver
    "64s"     # 0xDE: vehicle
    "64x"     # 0x11E: padding
    "64s"     # 0x15E: venue
    "64x"     # 0x19E: padding
    "1024x"   # 0x1DE: padding
    "I"       # 0x5DE: pro logging magic (0xc81a4)
    "66x"     # 0x5E2: padding
    "64s"     # 0x624: short comment
    "126x"    # 0x664: padding (total: 1762 bytes)
)

EVENT_FORMAT = '<64s64s1024sH'  # total: 1154 bytes

CHAN_FORMAT = '<' + (
    "IIII"    # prev_meta_ptr, next_meta_ptr, data_ptr, data_len
    "H"       # id (counter)
    "HHH"     # datatype_a, datatype, freq
    "hhhh"    # shift, mul, scale, dec
    "32s"     # name
    "8s"      # short name
    "12s"     # unit
    "40x"     # padding (total: 124 bytes)
)

HEAD_SIZE = struct.calcsize(HEAD_FORMAT)    # 1762
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)  # 1154
CHAN_SIZE = struct.calcsize(CHAN_FORMAT)    # 124


def _pad_string(text: str, length: int) -> bytes:
    """Codifica string em ASCII com terminação nula e preenchimento."""
    encoded = text.encode('ascii', errors='replace')[:length]
    return encoded.ljust(length, b'\x00')


@dataclass
class ChannelData:
    """Dados e descritor de um canal MoTeC."""
    name: str
    short_name: str
    unit: str
    data: np.ndarray
    freq: int = 60
    dtype: np.dtype = np.dtype(np.float32)


@dataclass
class SessionMetadata:
    """Metadados do cabeçalho da sessão MoTeC."""
    driver: str = "ApexView Driver"
    vehicle: str = "RaceCar"
    venue: str = "Circuit"
    event_name: str = "ApexView Session"
    session_name: str = "Practice"
    comment: str = "Exported from ApexView Telemetry"
    short_comment: str = "ApexView"
    date_time: Optional[datetime.datetime] = None


class MotecLDWriter:
    """Gerador binário de arquivos .ld compatíveis com MoTeC i2 Pro."""

    @staticmethod
    def write(filepath: str, metadata: SessionMetadata, channels: List[ChannelData]) -> None:
        """
        Escreve um arquivo .ld completo no caminho especificado.
        """
        dt = metadata.date_time or datetime.datetime.now()
        date_str = dt.strftime("%d/%m/%Y")
        time_str = dt.strftime("%H:%M:%S")

        num_channels = len(channels)
        event_ptr = HEAD_SIZE
        first_channel_meta_ptr = HEAD_SIZE + EVENT_SIZE if num_channels > 0 else 0
        first_channel_data_ptr = first_channel_meta_ptr + (num_channels * CHAN_SIZE) if num_channels > 0 else 0

        with open(filepath, "wb") as f:
            # 1. Escreve cabeçalho principal (ldHead)
            head_bytes = struct.pack(
                HEAD_FORMAT,
                LD_MARKER,
                first_channel_meta_ptr,
                first_channel_data_ptr,
                event_ptr,
                1, 0x4240, 0xF,
                DEVICE_SERIAL,
                DEVICE_TYPE,
                DEVICE_VERSION,
                DEVICE_MAGIC,
                num_channels,
                _pad_string(date_str, 16),
                _pad_string(time_str, 16),
                _pad_string(metadata.driver, 64),
                _pad_string(metadata.vehicle, 64),
                _pad_string(metadata.venue, 64),
                PRO_LOGGING_MAGIC,
                _pad_string(metadata.short_comment, 64),
            )
            f.write(head_bytes)

            # 2. Escreve cabeçalho do evento (ldEvent)
            event_bytes = struct.pack(
                EVENT_FORMAT,
                _pad_string(metadata.event_name, 64),
                _pad_string(metadata.session_name, 64),
                _pad_string(metadata.comment, 1024),
                0,  # venue_ptr (0 = embutido)
            )
            f.write(event_bytes)

            # 3. Calcula offsets e escreve cabeçalhos dos canais (ldChan)
            cur_data_ptr = first_channel_data_ptr
            chan_headers = []

            for i, chan in enumerate(channels):
                chan_meta_ptr = first_channel_meta_ptr + (i * CHAN_SIZE)
                prev_meta_ptr = first_channel_meta_ptr + ((i - 1) * CHAN_SIZE) if i > 0 else 0
                next_meta_ptr = first_channel_meta_ptr + ((i + 1) * CHAN_SIZE) if i < (num_channels - 1) else 0

                raw_data = np.ascontiguousarray(chan.data, dtype=np.float32)
                data_len = len(raw_data)
                data_bytes_len = raw_data.nbytes

                # Tipo 0x07 = float, tipo 4 = 4 bytes (float32)
                dtype_a = 0x07
                dtype_b = 4

                chan_header = struct.pack(
                    CHAN_FORMAT,
                    prev_meta_ptr,
                    next_meta_ptr,
                    cur_data_ptr,
                    data_len,
                    0x2EE1 + i,       # ID do canal
                    dtype_a,
                    dtype_b,
                    int(chan.freq),
                    0, 1, 1, 0,       # shift, mul, scale, dec
                    _pad_string(chan.name, 32),
                    _pad_string(chan.short_name, 8),
                    _pad_string(chan.unit, 12),
                )
                chan_headers.append((chan_header, raw_data))
                cur_data_ptr += data_bytes_len

            # Grava todos os cabeçalhos de canal em sequência
            for header_byte, _ in chan_headers:
                f.write(header_byte)

            # 4. Grava os blocos de dados dos canais
            for _, raw_data in chan_headers:
                f.write(raw_data.tobytes())


def read_ld_file(filepath: str) -> dict:
    """
    Função utilitária de teste e validação: lê e decodifica um arquivo .ld.
    Retorna dicionário com metadados e canais extraídos.
    """
    with open(filepath, "rb") as f:
        content = f.read()

    # Desempacota cabeçalho
    head_raw = struct.unpack(HEAD_FORMAT, content[:HEAD_SIZE])
    (
        marker, meta_ptr, data_ptr, event_ptr,
        _, _, _,
        dev_serial, dev_type, dev_ver, dev_magic, num_channels,
        date_b, time_b, driver_b, vehicle_b, venue_b,
        pro_magic, comment_b
    ) = head_raw

    def clean_str(b: bytes) -> str:
        return b.decode("ascii", errors="replace").rstrip("\x00").strip()

    metadata = {
        "marker": marker,
        "first_meta_ptr": meta_ptr,
        "first_data_ptr": data_ptr,
        "event_ptr": event_ptr,
        "dev_serial": dev_serial,
        "dev_type": clean_str(dev_type),
        "dev_version": dev_ver,
        "num_channels": num_channels,
        "date": clean_str(date_b),
        "time": clean_str(time_b),
        "driver": clean_str(driver_b),
        "vehicle": clean_str(vehicle_b),
        "venue": clean_str(venue_b),
        "pro_magic": pro_magic,
        "comment": clean_str(comment_b),
    }

    # Desempacota evento se existir
    if event_ptr > 0 and len(content) >= (event_ptr + EVENT_SIZE):
        ev_raw = struct.unpack(EVENT_FORMAT, content[event_ptr:event_ptr + EVENT_SIZE])
        metadata["event_name"] = clean_str(ev_raw[0])
        metadata["session_name"] = clean_str(ev_raw[1])
        metadata["long_comment"] = clean_str(ev_raw[2])

    # Desempacota canais
    channels = {}
    cur_meta = meta_ptr
    while cur_meta > 0 and (cur_meta + CHAN_SIZE) <= len(content):
        chan_raw = struct.unpack(CHAN_FORMAT, content[cur_meta:cur_meta + CHAN_SIZE])
        (
            prev_pos, next_pos, d_ptr, d_len,
            c_id, dt_a, dt_b, freq,
            shift, mul, scale, dec,
            name_b, short_name_b, unit_b
        ) = chan_raw

        name = clean_str(name_b)
        short_name = clean_str(short_name_b)
        unit = clean_str(unit_b)

        # Lê amostras (float32)
        end_d = d_ptr + (d_len * 4)
        if end_d <= len(content):
            samples = np.frombuffer(content[d_ptr:end_d], dtype=np.float32)
        else:
            samples = np.array([], dtype=np.float32)

        channels[name] = {
            "name": name,
            "short_name": short_name,
            "unit": unit,
            "data": samples,
            "freq": freq,
            "datapos": d_ptr,
            "data_len": d_len,
            "prevpos": prev_pos,
            "nextpos": next_pos,
        }
        cur_meta = next_pos

    return {
        "metadata": metadata,
        "channels": channels,
    }
