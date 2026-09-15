"""
tests/test_advanced_features.py
===============================
Testes automatizados cobrindo os 5 módulos de análise e engenharia:
1. Exportação e Importação de volta externa (.apex ghost sharing)
2. Diagnóstico forense de Trail Braking (co-ativação, linearidade, anomalias)
3. Cálculo da Volta Ideal Teórica por Trechos (Corner Bests Summary & Voice Ceiling)
4. Análise de Stint, Degradação de Pneus e Janela de Box (linear regression, cliff, pit window)
5. Sobreposição da linha de referência e widgets do Studio
"""

import os
import sys
import tempfile
import shutil
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.lap_library import LapLibrary, RetentionPolicy, LapRecord
from core.driving_analysis import analyze_corner_trail_braking
from core import corner_analysis as ca
from core.corner_bests import (CornerBest, CornerBestStore, CornerBestsSummary,
                               calculate_corner_bests_summary, map_signature)
from core.session_manager import SessionManager
from core.race_engineer import RaceEngineer
from core.stint_analysis import analyze_stint, extract_stint_laps, _linear_regression


def make_dummy_telemetry(num_samples: int = 100, wear_start: float = 98.0, wear_drop: float = 0.05):
    t = [i * 0.05 for i in range(num_samples)]
    d = [i * 10.0 for i in range(num_samples)]
    spd = [150.0 + (i % 20) for i in range(num_samples)]
    gas = [0.8 for _ in range(num_samples)]
    brk = [0.0 for _ in range(num_samples)]
    steer = [0.0 for _ in range(num_samples)]
    fuel = [50.0 - (i * 0.02) for i in range(num_samples)]
    wear_fl = [wear_start - (i * wear_drop) for i in range(num_samples)]
    return {
        "times": t,
        "distance": d,
        "speed": spd,
        "gas": gas,
        "brake": brk,
        "steer": steer,
        "gear": [4] * num_samples,
        "rpm": [6000] * num_samples,
        "fuel": fuel,
        "tyre_wear_fl": wear_fl,
        "tyre_wear_fr": wear_fl,
        "tyre_wear_rl": wear_fl,
        "tyre_wear_rr": wear_fl,
        "car_x": [float(i) for i in range(num_samples)],
        "car_z": [float(i * 2) for i in range(num_samples)],
    }


class TestModule1GhostSharing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.library = LapLibrary(data_dir=self.temp_dir, retention=RetentionPolicy(enabled=True, keep_best=1, keep_recent=1))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_and_import_lap(self):
        track = "Interlagos"
        car = "Porsche 911 GT3"
        telem = make_dummy_telemetry(80)
        rec = self.library.save_lap(track, car, telemetry=telem, lap_time_str="1:32.450", sector_times_ms=[28000, 32000, 32450], lap_number=1, full_lap=True)
        self.assertIsNotNone(rec)

        # Export to .apex
        export_path = os.path.join(self.temp_dir, "test_export.apex")
        ok = self.library.export_lap_file(track, car, rec, export_path)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(export_path))
        self.assertGreater(os.path.getsize(export_path), 0)

        # Import into fresh library
        fresh_dir = tempfile.mkdtemp()
        try:
            fresh_lib = LapLibrary(data_dir=fresh_dir, retention=RetentionPolicy(enabled=True, keep_best=1, keep_recent=1))
            imp_rec = fresh_lib.import_lap_file(export_path)
            self.assertIsNotNone(imp_rec)
            self.assertEqual(imp_rec.track, track)
            self.assertEqual(imp_rec.car, car)
            self.assertTrue(imp_rec.imported)
            self.assertTrue(imp_rec.pinned)
            self.assertIn("[importada]", imp_rec.label())

            # Verify saved in catalog
            saved = fresh_lib.records(track, car)
            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0].imported)
            self.assertTrue(saved[0].pinned)

            # Test retention protection: add 4 normal laps with keep_best=1, keep_recent=1
            for i in range(4):
                fresh_lib.save_lap(track, car, telemetry=telem, lap_time_str=f"1:35.00{i}", sector_times_ms=[30000, 33000, 32000], lap_number=i+2, full_lap=True)
            
            laps_after = fresh_lib.records(track, car)
            # The imported lap must NOT be purged by retention
            imported_found = any(r.lap_id == imp_rec.lap_id for r in laps_after)
            self.assertTrue(imported_found, "Imported lap was incorrectly deleted by retention policy")
        finally:
            shutil.rmtree(fresh_dir, ignore_errors=True)


class TestModule2TrailBraking(unittest.TestCase):
    def test_smooth_trail_braking(self):
        # Brake is released from 100% to 0% as steer angle increases from 0° to 30°
        n = 30
        times = [i * 0.05 for i in range(n)]
        dist = [100.0 + i * 2.0 for i in range(n)]
        # Linear release and steer build
        brake = [1.0 - (i / (n - 1)) for i in range(n)]
        steer = [(i / (n - 1)) * 30.0 for i in range(n)]
        speed = [120.0 - i * 1.5 for i in range(n)]
        gas = [0.0] * n

        tel = {"times": times, "distance": dist, "brake": brake, "steer": steer, "speed": speed, "gas": gas}
        res = analyze_corner_trail_braking(tel, 100.0, 158.0, 140.0)

        self.assertIsNotNone(res)
        self.assertTrue(res.has_trail)
        self.assertGreater(res.coactivation_duration_s, 0.2)
        self.assertGreater(res.coactivation_samples, 5)
        self.assertGreater(res.linearity_score, 70.0)
        self.assertFalse(any("degrau" in a.lower() for a in res.anomalies))
        self.assertFalse(any("reta" in a.lower() for a in res.anomalies))

    def test_step_release_anomaly(self):
        # Abrupt step release of brake while turning
        n = 30
        times = [i * 0.05 for i in range(n)]
        dist = [100.0 + i * 2.0 for i in range(n)]
        brake = [0.9 if i < 15 else 0.0 for i in range(n)]  # Instant drop from 90% to 0%
        steer = [20.0] * n  # Turning
        speed = [100.0] * n
        gas = [0.0] * n

        tel = {"times": times, "distance": dist, "brake": brake, "steer": steer, "speed": speed, "gas": gas}
        res = analyze_corner_trail_braking(tel, 100.0, 158.0, 130.0)
        self.assertIsNotNone(res)
        self.assertTrue(any("degrau" in a.lower() for a in res.anomalies))

    def test_straight_braking_only_anomaly(self):
        # Heavy braking without steering
        n = 30
        times = [i * 0.05 for i in range(n)]
        dist = [100.0 + i * 2.0 for i in range(n)]
        brake = [1.0 - (i / (n - 1)) for i in range(n)]
        steer = [0.5] * n  # Almost straight
        speed = [120.0 - i * 2.0 for i in range(n)]
        gas = [0.0] * n

        tel = {"times": times, "distance": dist, "brake": brake, "steer": steer, "speed": speed, "gas": gas}
        res = analyze_corner_trail_braking(tel, 100.0, 158.0, 140.0)
        self.assertIsNotNone(res)
        self.assertTrue(any("reta" in a.lower() for a in res.anomalies))


CB_LENGTH = 4309.0
CB_CORNERS = [
    ca.Corner(index=1, name="S do Senna", start=0.15, end=0.225, direction="L"),
    ca.Corner(index=2, name="Ferradura", start=0.60, end=0.68, direction="L"),
]


def corner_telemetry(corner_times, n=1200):
    """Volta sintética em que cada curva dura exatamente o tempo pedido."""
    dist, times, speed, brake, gas = [], [], [], [], []
    t = 0.0
    passo = CB_LENGTH / (n - 1)
    for i in range(n):
        d = i * passo
        dentro = next((c for c in CB_CORNERS
                       if c.start * CB_LENGTH <= d <= c.end * CB_LENGTH), None)
        if dentro is not None:
            largura = (dentro.end - dentro.start) * CB_LENGTH
            dt = corner_times[dentro.index] / max(1.0, largura / passo)
            v = 100.0
        else:
            dt = passo / 70.0
            v = 250.0
        t += dt
        dist.append(d)
        times.append(t)
        speed.append(v)
        freando = any(c.start * CB_LENGTH - 100 <= d < c.start * CB_LENGTH
                      for c in CB_CORNERS)
        brake.append(0.9 if freando else 0.0)
        gas.append(0.0 if freando else 1.0)
    return {"times": times, "distance": dist, "speed": speed,
            "brake": brake, "gas": gas}


class TestModule3CornerBestsSummary(unittest.TestCase):
    """
    A ideal teórica é `PB - Σ (tempo da curva no PB - melhor tempo da curva)`.
    Sem o mapa de curvas não há como saber onde o PB perdeu, e aí o resumo
    precisa admitir que não sabe — devolver o próprio PB como "ideal" faria a
    tela mostrar o mesmo tempo com outro nome.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lib = LapLibrary(data_dir=self.temp_dir)
        self.track, self.car = "Interlagos", "Porsche Cup"
        # PB: 8,0 s na curva 1 e 9,0 s na curva 2
        self.pb = self.lib.save_lap(
            self.track, self.car, telemetry=corner_telemetry({1: 8.0, 2: 9.0}),
            lap_time_str="1:40.000", sector_times_ms=[33000, 33000, 34000],
            lap_number=1, full_lap=True)
        # Melhores passagens: 0,6 s + 0,5 s melhores que as do PB
        self.bests = {
            1: CornerBest(index=1, name="S do Senna", section_s=7.4),
            2: CornerBest(index=2, name="Ferradura", section_s=8.5),
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ideal_desconta_o_ganho_de_cada_curva(self):
        summary = calculate_corner_bests_summary(
            self.track, self.car, self.bests, self.pb, self.lib,
            corners=CB_CORNERS, track_length=CB_LENGTH)

        self.assertEqual(summary.pb_time_str, "1:40.000")
        self.assertEqual(summary.corners_improved, 2)
        # ~1,1 s de ganho; a folga cobre a granularidade das amostras
        self.assertAlmostEqual(summary.delta_s, 1.1, delta=0.15)
        self.assertAlmostEqual(summary.ideal_time_s, 100.0 - summary.delta_s, places=3)
        self.assertLess(summary.ideal_time_s, summary.pb_time_s)
        self.assertNotEqual(summary.ideal_time_str, "--:--.---")
        self.assertTrue(summary.delta_str.endswith(" s"))

    def test_sem_mapa_de_curvas_nao_inventa_ideal(self):
        summary = calculate_corner_bests_summary(
            self.track, self.car, self.bests, self.pb, self.lib)

        self.assertEqual(summary.ideal_time_s, 0.0)
        self.assertEqual(summary.ideal_time_str, "--:--.---")
        self.assertEqual(summary.delta_s, 0.0)
        # O PB continua disponível para a tela, só não vira "ideal".
        self.assertEqual(summary.pb_time_str, "1:40.000")
        self.assertGreater(summary.pb_time_s, 0.0)

    def test_get_summary_le_o_arquivo_gravado_pelo_coach(self):
        """
        O arquivo é gravado com a assinatura completa do mapa (origem inclusa),
        mas quem só soma os trechos não conhece a origem. A leitura precisa
        casar pela geometria, senão a feature inteira volta vazia.
        """
        store = CornerBestStore(self.lib)
        assinatura = map_signature(CB_CORNERS, "manual")
        store.save(self.track, self.car, assinatura, self.bests, [self.pb.lap_id])

        # Como a interface chama: sem assinatura, só com o mapa em mãos.
        summary = store.get_summary(self.track, self.car,
                                    corners=CB_CORNERS, track_length=CB_LENGTH)
        self.assertEqual(summary.total_corners, 2)
        self.assertGreater(summary.ideal_time_s, 0.0)
        self.assertGreater(summary.delta_s, 0.0)

        # Com a assinatura exata também.
        exato = store.get_summary(self.track, self.car, signature=assinatura,
                                  corners=CB_CORNERS, track_length=CB_LENGTH)
        self.assertEqual(exato.ideal_time_s, summary.ideal_time_s)

    def test_mapa_com_outra_geometria_e_descartado(self):
        store = CornerBestStore(self.lib)
        store.save(self.track, self.car, map_signature(CB_CORNERS, "manual"),
                   self.bests, [self.pb.lap_id])

        outro_mapa = [ca.Corner(index=1, name="Outra", start=0.30, end=0.40, direction="R")]
        summary = store.get_summary(self.track, self.car,
                                    corners=outro_mapa, track_length=CB_LENGTH)
        self.assertEqual(summary.total_corners, 0)
        self.assertEqual(summary.ideal_time_s, 0.0)

    def test_race_engineer_theoretical_ceiling(self):
        engineer = RaceEngineer()
        # 1:25.500 = 85.5s (86 seconds -> 1 minuto e 26 segundos)
        adv = engineer.announce_theoretical_ceiling(85.5, now=10.0)
        self.assertIsNotNone(adv)
        self.assertIn("teto atual", adv.text.lower())
        self.assertIn("1 minuto", adv.text.lower())
        self.assertIn("26 segundos", adv.text.lower())

        # Second call within cooldown must return None
        adv2 = engineer.announce_theoretical_ceiling(85.5, now=20.0)
        self.assertIsNone(adv2)


class TestModule4StintAnalysis(unittest.TestCase):
    def test_linear_regression(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        slope, intercept, _ = _linear_regression(xs, ys)
        self.assertAlmostEqual(slope, 2.0, places=4)
        self.assertAlmostEqual(intercept, 0.0, places=4)

    def test_analyze_stint(self):
        temp_dir = tempfile.mkdtemp()
        try:
            lib = LapLibrary(data_dir=temp_dir)
            track = "Interlagos"
            car = "Porsche Cup"
            recs = []
            for i in range(1, 5):
                telem = make_dummy_telemetry(num_samples=60, wear_start=100.0 - i * 3.0, wear_drop=0.03)
                r = lib.save_lap(track, car, telemetry=telem, lap_time_str=f"1:{35+i}.000",
                                 sector_times_ms=[30000, 32000, 33000], lap_number=i, full_lap=True)
                recs.append(r)

            res = analyze_stint(track, car, recs, lib, cliff_threshold_pct=40.0, fuel_capacity=60.0)
            self.assertEqual(res.total_laps_analyzed, 4)
            self.assertGreater(res.avg_fuel_consumption_per_lap, 0.0)
            self.assertGreater(res.projected_cliff_lap, 0)
            self.assertGreater(res.pit_window_end, 0)
            self.assertTrue(len(res.recommendation_text) > 0)

            # Tudo veio de canal medido: nada a ressalvar.
            self.assertEqual(res.warnings, [])
            self.assertTrue(all(l.wear_measured and l.fuel_measured for l in res.laps_data))
            self.assertTrue(res.has_wear_model)
            self.assertTrue(res.has_fuel_model)

            # A taxa tem que bater com o desgaste que a telemetria realmente
            # descreve: 3 %/volta entre voltas consecutivas do fixture.
            for taxa in res.wear_rates_per_lap:
                self.assertAlmostEqual(taxa, -3.0, delta=0.2)
            self.assertGreater(res.r2_wear, 0.95)

            # A janela é contada dentro do stint e traduzida para a sessão.
            self.assertLessEqual(res.pit_window_start, res.recommended_pit_lap)
            self.assertLessEqual(res.recommended_pit_lap, res.pit_window_end)
            self.assertGreater(res.recommended_pit_lap_absolute, 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sem_canal_de_pneu_nao_projeta_cliff(self):
        """
        Volta antiga, gravada antes dos canais de pneu existirem, não pode
        virar um cliff projetado: o piloto pararia no box por causa de um
        número que ninguém mediu.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            lib = LapLibrary(data_dir=temp_dir)
            track, car = "Interlagos", "Porsche Cup"
            recs = []
            for i in range(1, 6):
                telem = make_dummy_telemetry(num_samples=60)
                for canal in ("tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl",
                              "tyre_wear_rr", "fuel"):
                    telem.pop(canal, None)
                recs.append(lib.save_lap(track, car, telemetry=telem,
                                         lap_time_str=f"1:{35+i}.000",
                                         sector_times_ms=[30000, 32000, 33000],
                                         lap_number=i, full_lap=True))

            res = analyze_stint(track, car, recs, lib)

            self.assertEqual(res.total_laps_analyzed, 5)
            self.assertFalse(any(l.wear_measured for l in res.laps_data))
            self.assertFalse(any(l.fuel_measured for l in res.laps_data))
            self.assertFalse(res.has_wear_model)
            self.assertFalse(res.has_fuel_model)

            # Nada de cliff, autonomia ou janela inventados.
            self.assertEqual(res.projected_cliff_lap, 0)
            self.assertEqual(res.estimated_fuel_laps, 0)
            self.assertEqual(res.recommended_pit_lap, 0)
            self.assertEqual(res.wear_rates_per_lap, [0.0] * 4)
            self.assertEqual(res.r2_wear, 0.0)

            # E o motivo precisa aparecer para quem lê a tela.
            self.assertTrue(res.warnings)
            self.assertIn("desgaste", " ".join(res.warnings).lower())
            self.assertIn("combustível", " ".join(res.warnings).lower())
            self.assertIn("⚠", res.recommendation_text)

            # O ritmo, esse sim, continua sendo calculado.
            self.assertGreater(res.time_deg_slope_s, 0.0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_pneu_sem_desgaste_nao_vira_degradacao(self):
        """Desgaste constante = sem modelo, não uma taxa forjada de -1,2 %/volta."""
        temp_dir = tempfile.mkdtemp()
        try:
            lib = LapLibrary(data_dir=temp_dir)
            track, car = "Monza", "Formula"
            recs = []
            for i in range(1, 6):
                telem = make_dummy_telemetry(num_samples=60, wear_start=100.0, wear_drop=0.0)
                recs.append(lib.save_lap(track, car, telemetry=telem,
                                         lap_time_str=f"1:3{i}.000",
                                         sector_times_ms=[30000, 32000, 33000],
                                         lap_number=i, full_lap=True))

            res = analyze_stint(track, car, recs, lib)
            self.assertFalse(res.has_wear_model)
            self.assertEqual(res.projected_cliff_lap, 0)
            self.assertEqual(res.r2_wear, 0.0)
            self.assertTrue(any("desgaste mensurável" in w.lower() for w in res.warnings))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
