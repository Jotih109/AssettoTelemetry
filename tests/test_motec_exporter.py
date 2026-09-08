"""
Testes unitários do exportador de telemetria MoTeC i2 (.ld / .ldx).

Valida a geração binária, integridade dos cabeçalhos e offsets,
reamostragem a 60 Hz, arquivo XML .ldx e proteção dos arquivos de telemetria.
"""

import hashlib
import os
import shutil
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.lap_library import LapLibrary, LapRecord, RetentionPolicy
from core.models import TelemetryState
from core.motec import (
    HEAD_FORMAT,
    HEAD_SIZE,
    LD_MARKER,
    PRO_LOGGING_MAGIC,
    MotecExporter,
    export_motec_ld,
    read_ld_file,
)
from core.motec.channel_mapping import _convert_ac_gear
from tests.weekend_sim import CAR_NAME, DriverStyle, SpeedProfile, TRACK_NAME


def file_hash(path: str) -> str:
    """Calcula hash SHA256 do arquivo."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestMotecExporter(unittest.TestCase):
    """Bateria de testes para o exportador MoTeC."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="apex_motec_test_")

    def tearDown(self):
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)

    def test_synthetic_lap_export_and_binary_integrity(self):
        """Gera uma volta sintética com TelemetryState e valida cabeçalhos MoTeC."""
        # 1. Simula uma volta sintética via SpeedProfile do fim de semana
        style = DriverStyle()
        profile = SpeedProfile(style)
        track_len = 4309.0
        dt = 1.0 / 60.0
        states = []
        d = 0.0
        t = 0.0

        while d < track_len:
            v_kmh = profile.speed_at(d)
            gas, brake = profile.pedals_at(d)
            st = TelemetryState(is_connected=True)
            st.track_name = TRACK_NAME
            st.car_name = CAR_NAME
            st.distance_traveled = d
            st.speed_kmh = v_kmh
            st.gas = gas
            st.brake = brake
            st.steer_angle = profile.g_lat_at(d) * 35.0
            st.g_lat = profile.g_lat_at(d)
            st.g_lon = 0.5 if gas > 0.5 else (-1.0 if brake > 0.1 else 0.0)
            st.g_vert = 1.0
            st.rpm = int(3000 + v_kmh * 20)
            st.gear = 3 if v_kmh > 120 else 2
            st.abs_intervention = 0.6 if brake > 0.8 else 0.0
            st.tc_intervention = 0.4 if gas > 0.9 and v_kmh < 100 else 0.0
            st.tyre_pressure = [26.4, 26.5, 25.1, 25.2]
            st.tyre_temp = [84.0, 85.2, 82.1, 82.5]
            states.append(st)

            step_m = max(1.0, (v_kmh / 3.6) * dt)
            d += step_m
            t += dt

        out_ld = os.path.join(self.tmp_dir, "synthetic_lap.ld")
        out_ldx = os.path.join(self.tmp_dir, "synthetic_lap.ldx")

        # 2. Exporta para MoTeC
        exporter = MotecExporter(target_freq=60.0)
        success = exporter.export(
            states,
            out_ld,
            track="Interlagos",
            car="Porsche 911 GT3",
            driver="Ayrton",
            lap_number=1,
            lap_time_s=t,
        )
        self.assertTrue(success, "Exportação para MoTeC falhou")
        self.assertTrue(os.path.exists(out_ld), "Arquivo .ld não foi criado")
        self.assertTrue(os.path.exists(out_ldx), "Arquivo .ldx não foi criado")

        # 3. Validação binária estrita dos primeiros 1762 bytes (ldHead)
        with open(out_ld, "rb") as f:
            header_bytes = f.read(HEAD_SIZE)
        self.assertEqual(len(header_bytes), HEAD_SIZE)

        unpacked = struct.unpack(HEAD_FORMAT, header_bytes)
        marker = unpacked[0]
        first_chan_meta = unpacked[1]
        first_chan_data = unpacked[2]
        event_ptr = unpacked[3]
        num_chans = unpacked[11]
        pro_magic = unpacked[17]

        self.assertEqual(marker, LD_MARKER, f"Marcador MoTeC deve ser 0x40, obteve {hex(marker)}")
        self.assertEqual(pro_magic, PRO_LOGGING_MAGIC, "Pro Logging magic inválido")
        self.assertEqual(event_ptr, 1762, "Offset do ldEvent deve ser exatamente 1762")
        self.assertEqual(first_chan_meta, 1762 + 1154, "Offset do primeiro ldChan deve ser 2916")
        self.assertGreater(num_chans, 15, "Deve haver pelo menos 15 canais mapeados")
        self.assertEqual(
            first_chan_data,
            first_chan_meta + (num_chans * 124),
            "Offset dos dados deve seguir imediatamente após os cabeçalhos de canais",
        )

        # 4. Decodificação e verificação de canais essenciais
        parsed = read_ld_file(out_ld)
        self.assertEqual(parsed["metadata"]["driver"], "Ayrton")
        self.assertEqual(parsed["metadata"]["venue"], "Interlagos")
        self.assertEqual(parsed["metadata"]["vehicle"], "Porsche 911 GT3")

        chans = parsed["channels"]
        required_channels = [
            "Speed", "Throttle", "Brake", "Steer", "Gear", "RPM",
            "G_Lat", "G_Long", "G_Vert", "ABS_Active", "TC_Active",
            "Tyre_Press_FL", "Tyre_Press_FR", "Tyre_Press_RL", "Tyre_Press_RR",
            "Tyre_Temp_FL", "Tyre_Temp_FR", "Tyre_Temp_RL", "Tyre_Temp_RR",
        ]
        for ch in required_channels:
            self.assertIn(ch, chans, f"Canal essencial '{ch}' ausente no arquivo exportado")
            self.assertEqual(chans[ch]["freq"], 60, f"Frequência do canal '{ch}' deve ser 60 Hz")
            self.assertGreater(len(chans[ch]["data"]), 50, f"Canal '{ch}' tem poucas amostras")

        # Verifica ranges de valores
        throttle = chans["Throttle"]["data"]
        brake = chans["Brake"]["data"]
        speed = chans["Speed"]["data"]
        gear = chans["Gear"]["data"]

        self.assertTrue(np.all(throttle >= 0.0) and np.all(throttle <= 100.0), "Throttle fora da faixa 0-100%")
        self.assertTrue(np.all(brake >= 0.0) and np.all(brake <= 100.0), "Brake fora da faixa 0-100%")
        self.assertTrue(np.all(speed >= 0.0), "Speed negativo encontrado")
        self.assertTrue(np.all(gear >= -1.0) and np.all(gear <= 8.0), "Gear fora da faixa válida MoTeC")

        # 5. Validação do arquivo XML .ldx
        tree = ET.parse(out_ldx)
        root = tree.getroot()
        self.assertEqual(root.tag, "LDXFile")
        beacons = root.findall(".//MarkerGroup[@Name='Beacons']/Marker")
        self.assertGreaterEqual(len(beacons), 1, "Arquivo .ldx deve conter pelo menos 1 marcador de volta")

    def test_lap_library_export_and_data_integrity(self):
        """Valida LapLibrary.export_motec e garante que telemetry_data não é alterado."""
        lib_root = os.path.join(self.tmp_dir, "telemetry_lib")
        os.makedirs(lib_root, exist_ok=True)

        library = LapLibrary(data_dir=lib_root, retention=RetentionPolicy(enabled=False))
        track = "Interlagos"
        car = "Porsche_911"
        laps_dir = os.path.join(lib_root, track, car, "laps")
        os.makedirs(laps_dir, exist_ok=True)

        # Salva uma volta sintética no catálogo
        lap_file = os.path.join(laps_dir, "lap_001.json.gz")
        dummy_telemetry = {
            "times": [0.0, 0.5, 1.0, 1.5, 2.0],
            "distance": [0.0, 20.0, 45.0, 75.0, 110.0],
            "speed": [100.0, 120.0, 135.0, 150.0, 160.0],
            "gas": [1.0, 1.0, 0.9, 0.5, 0.0],
            "brake": [0.0, 0.0, 0.1, 0.6, 1.0],
            "steer": [0.0, 5.0, 12.0, 8.0, 2.0],
            "gear": [2, 2, 3, 3, 4],
            "rpm": [5000, 5800, 6400, 5500, 6100],
            "g_lat": [0.0, 0.4, 1.1, 0.7, 0.1],
            "abs_intervention": [0.0, 0.0, 0.0, 0.3, 0.8],
            "tc_intervention": [0.1, 0.0, 0.0, 0.0, 0.0],
        }
        from core.lap_library import write_json_gzip_atomic
        write_json_gzip_atomic(lap_file, {
            "metadata": {
                "track": track,
                "car": car,
                "lap_time_str": "1:29.500",
                "lap_time_ms": 89500,
                "sector_times_ms": [28000, 31000, 30500],
                "lap_number": 1,
                "valid": True,
            },
            "telemetry": dummy_telemetry,
        })

        # Hash antes da exportação
        hash_before = file_hash(lap_file)

        # Cria LapRecord correspondente
        rec = LapRecord(
            lap_id="lap_001",
            track=track,
            car=car,
            lap_number=1,
            lap_time_str="1:29.500",
            lap_time_ms=89500,
            file="laps/lap_001.json.gz",
            sector_times_ms=(28000, 31000, 30500),
        )

        export_dest = os.path.join(self.tmp_dir, "export_output.ld")
        success = library.export_motec(track, car, rec, export_dest)
        self.assertTrue(success, "library.export_motec retornou False")
        self.assertTrue(os.path.exists(export_dest))
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir, "export_output.ldx")))

        # Garante que o arquivo original de telemetria NÃO sofreu nenhuma modificação
        hash_after = file_hash(lap_file)
        self.assertEqual(
            hash_before, hash_after,
            "CRÍTICO: O arquivo de telemetria original em telemetry_data/ foi modificado!",
        )

    def test_ac_gear_conversion(self):
        """Valida que marchas do Assetto Corsa são convertidas para o padrão MoTeC."""
        # AC: 0=Ré, 1=Neutro, 2=1ª, 3=2ª, 4=3ª
        raw_ac = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        converted = _convert_ac_gear(raw_ac)
        expected = np.array([-1.0, 0.0, 1.0, 2.0, 3.0], dtype=np.float32)
        np.testing.assert_array_equal(converted, expected)

    def test_invalid_data_graceful_handling(self):
        """Garante tratamento gracioso sem exceção ao passar dados inválidos/vazios."""
        exporter = MotecExporter()
        out_file = os.path.join(self.tmp_dir, "should_not_exist.ld")

        # Dicionário vazio
        result = exporter.export({}, out_file)
        self.assertFalse(result)
        self.assertFalse(os.path.exists(out_file))

        # Arquivo inexistente
        result2 = exporter.export("caminho_inexistente_123.json.gz", out_file)
        self.assertFalse(result2)
        self.assertFalse(os.path.exists(out_file))


if __name__ == "__main__":
    unittest.main()
