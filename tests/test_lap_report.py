"""
tests/test_lap_report.py — Testes unitários do gerador de relatórios de telemetria
=================================================================================
Valida:
  1. Geração de relatório para volta individual (solo).
  2. Geração de relatório comparativo entre duas voltas.
  3. Diagnóstico causal preciso do "porquê" (frenagem antecipada, ápice lento,
     retomada tardia, modulação de pedais, subesterço).
  4. Identificação correta de pontos positivos e negativos.
  5. Formatação em Markdown (.md) e Texto Puro (.txt).
  6. Integração com LapLibrary (generate_lap_report e export_report).
  7. Garantia de que a pasta real telemetry_data/ não é modificada.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import corner_analysis as ca
from core.lap_library import LapLibrary, LapRecord, RetentionPolicy
from core.lap_report import LapReportGenerator, LapReportResult


def _hash_directory(dir_path: str) -> str:
    """Calcula hash MD5 recursivo do diretório para garantir imutabilidade."""
    if not os.path.exists(dir_path):
        return ""
    hasher = hashlib.md5()
    for root, _, files in sorted(os.walk(dir_path)):
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            hasher.update(fname.encode("utf-8"))
            with open(fpath, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
    return hasher.hexdigest()


def _synthetic_lap(n: int = 400, *, speed_base: float = 140.0,
                   brake_early: bool = False, late_throttle: bool = False,
                   pedal_overlap: bool = False, abrupt_release: bool = False):
    """
    Gera telemetria sintética com 2 curvas nítidas:
      - C1: ~800m a ~1200m (com aproximação/frenagem anterior)
      - C2: ~2200m a ~2600m
    """
    total_dist = 4000.0
    dists = [i * (total_dist / (n - 1)) for i in range(n)]
    speeds = []
    gases = []
    brakes = []
    steers = []
    g_lats = []
    gears = []
    rpms = []

    for i in range(n):
        d = dists[i]
        # Curva 1: Ápice em ~1000m
        if 650 <= d <= 1200:
            steer = 35.0
            glat = 1.6
            gear = 3
            rpm = 6500.0

            # Frenagem C1
            brake_start = 680.0 if brake_early else 760.0
            if brake_start <= d < 950:
                brake = 0.90
                gas = 0.40 if pedal_overlap else 0.0
                spd = max(55.0, (speed_base - 10.0 if brake_early else speed_base) - (d - brake_start) * 0.3)
            elif 950 <= d < 1050:  # Ápice
                brake = 0.0 if abrupt_release else 0.15
                gas = 0.1
                spd = 65.0 if brake_early else 85.0
            else:  # Saída
                brake = 0.0
                throttle_start = 1160.0 if late_throttle else 1050.0
                gas = 1.0 if d >= throttle_start else 0.3
                spd = (70.0 if late_throttle else 85.0) + (d - 1050) * 0.20

        # Curva 2: Ápice em ~2400m
        elif 2100 <= d <= 2650:
            steer = -40.0
            glat = 1.8
            gear = 2
            rpm = 7200.0
            if 2150 <= d < 2350:
                brake = 0.85
                gas = 0.0
                spd = max(65.0, 160.0 - (d - 2150) * 0.4)
            elif 2350 <= d < 2450:
                brake = 0.10
                gas = 0.20
                spd = 75.0
            else:
                brake = 0.0
                gas = 1.0
                spd = 80.0 + (d - 2450) * 0.35

        # Retas
        else:
            steer = 0.0
            glat = 0.05
            brake = 0.0
            gas = 1.0
            gear = 5
            rpm = 7800.0
            spd = speed_base + 30.0

        speeds.append(spd)
        gases.append(gas)
        brakes.append(brake)
        steers.append(steer)
        g_lats.append(glat)
        gears.append(gear)
        rpms.append(rpm)

    # Acumula tempo a partir da velocidade física: dt = dx / v
    times = [0.0]
    for i in range(1, n):
        dx = dists[i] - dists[i - 1]
        v_ms = max(5.0, (speeds[i] + speeds[i - 1]) * 0.5) / 3.6
        times.append(times[-1] + dx / v_ms)

    return {
        "times": times,
        "distance": dists,
        "speed": speeds,
        "gas": gases,
        "brake": brakes,
        "steer": steers,
        "g_lat": g_lats,
        "gear": gears,
        "rpm": rpms,
        "abs_intervention": [0.0] * n,
        "tc_intervention": [0.0] * n,
        "car_x": [float(i) for i in range(n)],
        "car_z": [float(i % 50) for i in range(n)],
        "sector": [0 if i < n // 3 else (1 if i < 2 * n // 3 else 2) for i in range(n)]
    }


class TestLapReportGenerator(unittest.TestCase):
    """Bateria de testes unitários para a geração e exportação de relatórios."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="apex_report_test_")
        self.real_telemetry_dir = os.path.join(ROOT, "telemetry_data")
        self.initial_hash = _hash_directory(self.real_telemetry_dir)

    def tearDown(self):
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        # Garante integridade absoluta dos dados reais
        final_hash = _hash_directory(self.real_telemetry_dir)
        self.assertEqual(self.initial_hash, final_hash,
                         "A pasta telemetry_data/ foi violada ou alterada!")

    def test_single_lap_report_generation(self):
        """Gera relatório para uma volta solo sem referência externa."""
        gen = LapReportGenerator()
        lap_tel = _synthetic_lap(speed_base=150.0)

        res = gen.analyze(
            lap_tel,
            ref_telemetry=None,
            track_name="Autodromo Teste",
            car_name="GT3 Cup",
            lap_number=1,
            lap_time_str="1:39.999",
            lap_time_ms=99999,
            sector_times_ms=[33000, 34000, 32999]
        )

        self.assertIsInstance(res, LapReportResult)
        self.assertEqual(res.track, "Autodromo Teste")
        self.assertEqual(res.car, "GT3 Cup")
        self.assertEqual(res.lap_number, 1)
        self.assertTrue(len(res.corner_rows) >= 1, "Deve ter detectado curvas")

        # Formatações
        md = gen.format_markdown(res)
        txt = gen.format_plain_text(res)

        self.assertIn("# 🏁 Relatório de Desempenho", md)
        self.assertIn("Autodromo Teste", md)
        self.assertIn("1:39.999", md)

        self.assertIn("APEXVIEW — RELATÓRIO DE DESEMPENHO", txt)
        self.assertIn("Autodromo Teste", txt)
        self.assertIn("1:39.999", txt)

    def test_comparison_report_diagnostics_and_why(self):
        """
        Compara volta com erros (Lap B) contra uma volta rápida (Lap A / Referência).
        Verifica se os pontos negativos e onde melhorar mostram a causa do porquê.
        """
        gen = LapReportGenerator()
        ref_tel = _synthetic_lap(speed_base=155.0, brake_early=False, late_throttle=False)
        lap_tel = _synthetic_lap(speed_base=140.0, brake_early=True, late_throttle=True)

        res = gen.analyze(
            lap_tel,
            ref_telemetry=ref_tel,
            track_name="Interlagos",
            car_name="Porsche 911 GT3",
            lap_number=5,
            lap_time_str="1:38.500",
            lap_time_ms=98500,
            ref_lap_time_str="1:36.200",
            ref_lap_time_ms=96200,
            sector_times_ms=[32000, 33500, 33000],
            ref_sector_times_ms=[31000, 32800, 32400]
        )

        self.assertTrue(res.corners_with_losses > 0, "Deve acusar curvas com perda de tempo")
        self.assertTrue(res.total_potential_gain_s > 0, "Deve calcular tempo recuperável")
        self.assertTrue(len(res.negatives) > 0, "Deve listar pontos negativos")
        self.assertTrue(len(res.improvements) > 0, "Deve detalhar onde melhorar")

        # Valida a presença de justificativa causal ("O Porquê")
        has_why = any(item.why and len(item.why) > 20 for item in res.improvements)
        self.assertTrue(has_why, "O relatório deve conter explicação causal do porquê")

        # Valida ação prática sugerida
        has_action = any(item.action and len(item.action) > 15 for item in res.improvements)
        self.assertTrue(has_action, "O relatório deve conter orientação prática de correção")

        md = gen.format_markdown(res)
        self.assertIn("O do porquê", md)
        self.assertIn("Ação Prática", md)
        self.assertIn("Tabela de Telemetria Curva a Curva", md)

    def test_pedal_technique_diagnostics(self):
        """Valida detecção de sobreposição de pedais e soltura abrupta de freio."""
        gen = LapReportGenerator()
        tel_err = _synthetic_lap(pedal_overlap=True, abrupt_release=True)

        res = gen.analyze(tel_err, track_name="Spa", car_name="Ferrari 488")
        titles = [t.title for t in res.techniques]

        # Deve acusar sobreposição de pedais ou soltura abrupta
        has_overlap = any("Sobreposição" in t for t in titles)
        has_abrupt = any("Soltura Abrupta" in t for t in titles)
        self.assertTrue(has_overlap or has_abrupt,
                        f"Deveria acusar falha de técnica de pedais: {titles}")

    def test_lap_library_export_report(self):
        """Valida métodos generate_lap_report e export_report na LapLibrary."""
        lib = LapLibrary(data_dir=os.path.join(self.tmp_dir, "lib_dados"),
                         retention=RetentionPolicy(enabled=False))

        track, car = "Monza", "BMW M4 GT3"
        tel_fast = _synthetic_lap(speed_base=160.0)
        tel_slow = _synthetic_lap(speed_base=145.0, brake_early=True)

        rec_fast = lib.save_lap(track, car, telemetry=tel_fast,
                                lap_time_str="1:48.000", lap_number=1,
                                sector_times_ms=[35000, 35000, 38000],
                                full_lap=True, valid=True)
        rec_slow = lib.save_lap(track, car, telemetry=tel_slow,
                                lap_time_str="1:50.500", lap_number=2,
                                sector_times_ms=[36000, 36000, 38500],
                                full_lap=True, valid=True)

        # 1. Geração em memória
        md_text = lib.generate_lap_report(track, car, rec_slow, ref_rec=rec_fast, format="md")
        self.assertIsNotNone(md_text)
        self.assertIn("Monza", md_text)
        self.assertIn("1:50.500", md_text)
        self.assertIn("1:48.000", md_text)

        txt_text = lib.generate_lap_report(track, car, rec_slow, ref_rec=rec_fast, format="txt")
        self.assertIsNotNone(txt_text)
        self.assertIn("APEXVIEW", txt_text)

        # 2. Exportação para arquivo Markdown
        md_path = os.path.join(self.tmp_dir, "relatorio.md")
        ok_md = lib.export_report(track, car, rec_slow, md_path, ref_rec=rec_fast)
        self.assertTrue(ok_md)
        self.assertTrue(os.path.exists(md_path))
        with open(md_path, "r", encoding="utf-8") as f:
            read_md = f.read()
        self.assertIn("Relatório de Desempenho", read_md)

        # 3. Exportação para arquivo TXT
        txt_path = os.path.join(self.tmp_dir, "relatorio.txt")
        ok_txt = lib.export_report(track, car, rec_slow, txt_path, ref_rec=rec_fast)
        self.assertTrue(ok_txt)
        self.assertTrue(os.path.exists(txt_path))
        with open(txt_path, "r", encoding="utf-8") as f:
            read_txt = f.read()
        self.assertIn("APEXVIEW", read_txt)


if __name__ == "__main__":
    unittest.main()
