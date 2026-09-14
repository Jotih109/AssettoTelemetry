"""
tests/test_main2_smoke.py — Testes automatizados da Estação de Telemetria (main2.pyw)
=====================================================================================

Cobre:
  * Inicialização da janela TelemetryStudioWindow
  * Geração e carregamento de volta de demonstração (Mock Lap)
  * Renderização e alternância de modos de Heatmap (Freio/Aceleração, Velocidade, Marchas, Delta)
  * Inspeção ponto a ponto sincronizada (busca por proximidade no traçado)
  * Atualização dos mostradores de telemetria (HUD, pedais, volante, marcha, velocidade)
  * Mostrador do Círculo de Atrito G-G (GGCircleWidget)
  * Controlador de reprodução / replay (play, pause, velocidade e scrubber)
  * Cálculo de deltas e comparação com volta de referência
  * Tabela curva a curva e pulo de cursor para a curva clicada
  * Integração com a biblioteca de voltas (LapLibrary)

Execução:
    python tests/test_main2_smoke.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QPointF

from core.lap_library import LapLibrary, RetentionPolicy, LapRecord
from ui.telemetry_studio import (
    TelemetryStudioWindow, TrackMapProWidget, GGCircleWidget,
    PointInspectorWidget, PlaybackController, CornerTableWidget,
)

results = []


def check(name, fn):
    try:
        fn()
        results.append((name, True, ""))
    except Exception:
        if os.environ.get("SMOKE_VERBOSE"):
            traceback.print_exc()
        results.append((name, False,
                        traceback.format_exc(limit=4).strip().splitlines()[-1]))


def load_main2():
    spec = importlib.util.spec_from_file_location(
        "main2_module", os.path.join(ROOT, "main2.pyw"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dummy_telemetry(n=350, *, speed=160.0):
    return {
        "times": [i * 0.25 for i in range(n)],
        "distance": [i * (4309.0 / (n - 1)) for i in range(n)],
        "speed": [speed + 40.0 * math.sin(i / 15.0) for i in range(n)],
        "gas": [1.0 if (i % 50) < 30 else 0.0 for i in range(n)],
        "brake": [0.85 if (i % 50) >= 30 else 0.0 for i in range(n)],
        "steer": [25.0 * math.sin(i / 10.0) for i in range(n)],
        "gear": [max(1, min(6, int(i / 50) + 1)) for i in range(n)],
        "rpm": [6000 + (i % 30) * 80 for i in range(n)],
        "car_x": [100.0 * math.cos(2 * math.pi * i / n) for i in range(n)],
        "car_z": [200.0 * math.sin(2 * math.pi * i / n) for i in range(n)],
        "g_lat": [1.5 * math.sin(i / 10.0) for i in range(n)],
        "g_lon": [0.8 if (i % 50) < 30 else -1.2 for i in range(n)],
    }


import math

work = tempfile.mkdtemp(prefix="ac_main2_test_")
try:
    app = QApplication.instance() or QApplication([])
    main2_mod = load_main2()

    lib = LapLibrary(data_dir=os.path.join(work, "data"),
                     retention=RetentionPolicy(enabled=False))

    win = None

    def test_init_window():
        global win
        win = TelemetryStudioWindow(lib)
        assert win is not None
        assert win.windowTitle().startswith("ApexView")
        assert win.track_map is not None
        assert win.hud is not None
        assert win.playback is not None

    check("inicialização da janela TelemetryStudioWindow", test_init_window)

    def test_load_demo():
        win.load_demo_session()
        assert win.track_map.total_points > 50
        assert "DEMO" in win.lbl_session_title.text()
        assert win.current_tel is not None
        assert len(win.current_tel.get("speed", [])) > 0

    check("carregamento da volta demo (Mock Lap) com sucesso", test_load_demo)

    def test_heatmap_modes():
        # Testa todos os modos de cor do traçado
        for mode in ("brake_throttle", "speed", "gear", "delta", "micro_sectors", "single"):
            win.track_map.set_color_mode(mode)
            assert win.track_map.color_mode == mode
            assert len(win.track_map._cached_segments) > 0

    check("alternância dos modos de heatmap do traçado (Freio, Velocidade, Marcha, Delta, Micro-setores)", test_heatmap_modes)

    def test_sectors_and_micro_sectors():
        # Verifica a análise de setores e micro-setores
        assert win.sector_analysis is not None
        assert len(win.sector_analysis.sectors) == 3
        assert len(win.sector_analysis.micro_sectors) == 24
        assert win.sectors_ribbon is not None
        assert win.sectors_ribbon.card_s1.lbl_time.text() != "--.--- s"
        assert win.micro_table is not None
        assert win.micro_table.rowCount() == 24

        # Testa clique no micro-setor da tabela para navegação
        jumped_dist = []
        win.micro_table.sig_micro_clicked.connect(lambda d: jumped_dist.append(d))
        first_item = win.micro_table.item(3, 0)
        win.micro_table._on_item_clicked(first_item)
        assert len(jumped_dist) == 1
        assert jumped_dist[0] > 0

        # Testa clique na barra segmentada (strip) de micro-setores
        strip_jumps = []
        win.sectors_ribbon.sig_seek_distance.connect(lambda d: strip_jumps.append(d))
        win.sectors_ribbon.card_s2.strip.sig_micro_clicked.emit(1500.0)
        assert len(strip_jumps) == 1

        # Verifica sincronização com o HUD
        win._on_point_seek(10)
        assert "Micro" in win.hud.lbl_sector_badge.text()

    check("sistema de setores e micro-setores (Ribbon F1, 24 splits, navegação e HUD)", test_sectors_and_micro_sectors)

    def test_point_to_point_snap():
        # Pula para o ponto 45
        target_idx = 45
        win._on_point_seek(target_idx)
        assert win.track_map.selected_index == target_idx
        assert win.playback.current_index == target_idx
        assert win.hud.bar_gas.value() >= 0
        assert win.hud.bar_brake.value() >= 0

        # Testa atração pelo clique no mapa
        screen_pt = win.track_map.world_to_screen(
            float(win.track_map.cx[20]), float(win.track_map.cz[20]))
        received = []
        win.track_map.sig_point_selected.connect(lambda idx: received.append(idx))
        win.track_map._snap_to_mouse(screen_pt)
        assert len(received) > 0
        assert received[0] == 20

    check("inspeção ponto a ponto e snap com mouse no traçado", test_point_to_point_snap)

    def test_hud_components():
        data = {
            "dist": 1500.0,
            "tot_dist": 4309.0,
            "time": 32.450,
            "speed": 182.5,
            "gas": 0.0,
            "brake": 0.95,
            "gear": 4,
            "rpm": 7100,
            "steer": -18.5,
            "delta": 0.250,
            "lat_g": 1.45,
            "lon_g": -1.25,
            "corner_phrase": "Frenagem da Curva 1",
        }
        win.hud.update_point(data)
        assert "182.5 km/h" in win.hud.lbl_speed.text()
        assert win.hud.bar_brake.value() == 95
        assert "FRENAGEM" in win.hud.lbl_pedal_hint.text()
        assert "4ª" in win.hud.lbl_gear.text()
        assert "Frenagem da Curva 1" in win.hud.lbl_corner_name.text()

    check("atualização do HUD de telemetria (velocímetro, pedais, marcha, volante)", test_hud_components)

    def test_gg_circle():
        gg = win.hud.gg_widget
        gg.set_g_force(1.2, -0.9, [(0.5, 0.2), (0.8, -0.4), (1.2, -0.9)])
        assert gg.current_lat_g == 1.2
        assert gg.current_lon_g == -0.9
        assert len(gg.trail) == 3

    check("funcionamento do círculo de atrito G-G (Friction Circle)", test_gg_circle)

    def test_playback_controls():
        pb = win.playback
        assert not pb.is_playing
        pb.toggle_play()
        assert pb.is_playing
        assert "PAUSAR" in pb.btn_play.text()
        pb._on_tick()
        assert pb.current_index > 0
        pb.toggle_play()
        assert not pb.is_playing
        assert "REPLAY" in pb.btn_play.text()

    check("controles de reprodução do replay (Play, Pause, Avanço)", test_playback_controls)

    def test_corner_table_jump():
        table = win.corner_table
        assert table.rowCount() > 0
        jump_dist = []
        table.sig_corner_clicked.connect(lambda d: jump_dist.append(d))
        item = table.item(0, 0)
        table._on_item_clicked(item)
        assert len(jump_dist) == 1

    check("tabela curva a curva e pulo de cursor para a curva selecionada", test_corner_table_jump)

    def test_lap_comparison_and_delta():
        # Cria uma volta salva de teste no catálogo
        track, car = "Interlagos", "Porsche GT3"
        rec1 = lib.save_lap(
            track, car,
            telemetry=dummy_telemetry(300, speed=160.0),
            lap_time_str="1:35.000",
            sector_times_ms=[30000, 32000, 33000],
            lap_number=1,
            full_lap=True
        )
        rec2 = lib.save_lap(
            track, car,
            telemetry=dummy_telemetry(300, speed=155.0),
            lap_time_str="1:36.500",
            sector_times_ms=[31000, 32500, 33000],
            lap_number=2,
            full_lap=True
        )

        win.reload_catalog()
        win.load_lap(track, car, rec2)
        assert win.current_rec.lap_id == rec2.lap_id

        # Seleciona rec1 como referência no combo
        idx_ref = -1
        for i in range(win.combo_ref.count()):
            d = win.combo_ref.itemData(i)
            if d and getattr(d, "lap_id", None) == rec1.lap_id:
                idx_ref = i
                break
        if idx_ref >= 0:
            win.combo_ref.setCurrentIndex(idx_ref)
            assert win.ref_rec is not None
            assert win.ref_rec.lap_id == rec1.lap_id
            assert "delta" in win.current_tel
            assert len(win.current_tel["delta"]) > 0

    check("comparação de voltas, cálculo de delta e carro fantasma", test_lap_comparison_and_delta)

finally:
    shutil.rmtree(work, ignore_errors=True)

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print()
for name, ok, err in results:
    status = "[OK ]" if ok else "[FALHOU]"
    msg = f"  {status} {name}"
    if not ok:
        msg += f"\n         {err}"
    print(msg)

print(f"\n=== {passed}/{total} verificacoes passaram ===")
if passed < total:
    sys.exit(1)
