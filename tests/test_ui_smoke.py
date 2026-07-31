"""
tests/test_ui_smoke.py — Teste de fumaça da interface
====================================================
Monta a janela real, alimenta com telemetria simulada e exercita os caminhos
que costumam quebrar sem que ninguém perceba:

  * atualizar todos os cards e gráficos por centenas de quadros
  * trocar a volta de referência com ghost NOVO (todos os canais)
  * trocar a volta de referência com ghost ANTIGO (sem car_x/car_z/steer) —
    isso derrubava o pyqtgraph com "X and Y arrays must be the same shape"
  * arrastar o scrubber (modo análise) e voltar para o modo ao vivo
  * estado desconectado

Qualquer exceção em qualquer um desses caminhos reprova o teste.
Precisa de PyQt5 instalado; não precisa do jogo aberto.

    python tests/test_ui_smoke.py
"""

import os
import random
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication

import ui.main_window as mw
from core.engine import TelemetryEngine
from providers.mock import MockTelemetryProvider

results = []


def check(name, fn):
    """Executa fn(); reprova se levantar exceção."""
    try:
        fn()
        results.append((name, True, ""))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


# A engine não deve rodar: dirigimos os updates na mão, no thread da GUI
TelemetryEngine.start = lambda self: None
mw.AUTO_EXPORT_ON_BEST_LAP = False   # não sujar exportacoes/ durante o teste

work = tempfile.mkdtemp(prefix="ac_ui_test_")
old_cwd = os.getcwd()
try:
    # Roda num diretório temporário para não tocar nos ghosts reais
    os.chdir(work)

    app = QApplication.instance() or QApplication([])
    provider = MockTelemetryProvider()
    provider.connect()
    engine = TelemetryEngine(provider=provider, hz=60)

    win = None

    def build():
        global win
        win = mw.DashboardMainWindow(engine)
        win.resize(1500, 900)
        win.show()

    check("janela principal é construída", build)

    def feed(n=400):
        for i in range(n):
            st = provider.get_state()
            # Campos que só o provider do AC preenche
            st.g_lat = random.uniform(-2.4, 2.4)
            st.g_lon = random.uniform(-2.0, 1.4)
            st.brake_temp = [random.uniform(150, 780) for _ in range(4)]
            st.brake_bias = 0.615
            st.surface_grip = 0.978
            st.wind_speed, st.wind_direction = 3.6, 205.0
            st.session_type, st.race_position = "Practice", 3
            st.session_time_left = 1245.0
            st.tyre_compound, st.car_damage = "Street (ST)", 4.0
            st.abs_intervention = random.random() * 0.5
            st.tc_intervention = random.random() * 0.9
            st.abs_active = st.abs_intervention > 0.02
            st.tc_active = st.tc_intervention > 0.02
            st.ffb_level = random.uniform(0.4, 0.99)
            st.fuel_capacity = 95.0
            st.car_x, st.car_z = random.uniform(-500, 500), random.uniform(-500, 500)
            base = [92, 88, 86, 85]
            st.tyre_temp_inner = [b + 2 for b in base]
            st.tyre_temp_middle = list(base)
            st.tyre_temp_outer = [b - 9 for b in base]
            win.on_telemetry_update(st)
        app.processEvents()

    check("400 quadros de telemetria sem exceção", feed)

    def switch_all_ghost_modes():
        for i in range(win.ghost_selector.combo.count()):
            win.ghost_selector.combo.setCurrentIndex(i)
            app.processEvents()
        # Garante que selecionar 'Desativado' (índice 0) limpa as curvas de ghost
        win.ghost_selector.combo.setCurrentIndex(0)
        app.processEvents()
        assert win.curve_ghost_speed.xData is None or len(win.curve_ghost_speed.xData) == 0

    check("trocar entre todos os modos de referência (ghost vazio)",
          switch_all_ghost_modes)

    def ghost_completo():
        n = 200
        win.session_manager.best_lap_ghost = {
            "metadata": {"lap_time_str": "1:29.500", "sector_times_ms": [30000, 30000, 29500]},
            "telemetry": {
                "times": [i * 0.5 for i in range(n)],
                "distance": [i * 30.0 for i in range(n)],
                "speed": [100.0 + i for i in range(n)],
                "gas": [0.8] * n, "brake": [0.1] * n, "sector": [0] * n,
                "rpm": [6000] * n, "steer": [3.0] * n, "delta": [0.0] * n,
                "car_x": [float(i) for i in range(n)],
                "car_z": [float(i * 2) for i in range(n)],
            },
        }
        win.ghost_selector.combo.setCurrentIndex(1)
        win.on_ghost_mode_changed()
        app.processEvents()

    check("referência com ghost completo", ghost_completo)

    def ghost_legado():
        # Ghost gravado por versão antiga: sem car_x, car_z, steer e delta.
        # O pyqtgraph exige X e Y do mesmo tamanho — este era o crash.
        n = 150
        win.session_manager.best_lap_ghost = {
            "metadata": {"lap_time_str": "1:31.000", "sector_times_ms": [31000, 31000, 29000]},
            "telemetry": {
                "times": [i * 0.5 for i in range(n)],
                "distance": [i * 30.0 for i in range(n)],
                "speed": [90.0 + i for i in range(n)],
                "gas": [0.7] * n, "brake": [0.2] * n, "sector": [0] * n,
                "rpm": [5500] * n,
            },
        }
        win.ghost_selector.combo.setCurrentIndex(1)
        win.on_ghost_mode_changed()
        app.processEvents()
        # E um quadro de telemetria em cima, para exercitar o delta vs esse ghost
        win.on_telemetry_update(provider.get_state())

    check("referência com ghost ANTIGO (sem car_x/steer)", ghost_legado)

    def ghost_truncado():
        # Caso patológico: canais presentes mas mais curtos que o eixo X
        win.session_manager.best_lap_ghost = {
            "metadata": {"lap_time_str": "1:32.000", "sector_times_ms": [0, 0, 0]},
            "telemetry": {
                "times": [0.0, 1.0, 2.0, 3.0, 4.0],
                "distance": [0.0, 10.0, 20.0, 30.0, 40.0],
                "speed": [10.0, 20.0],          # curto de propósito
                "gas": [], "brake": [0.5],
                "sector": [0], "rpm": [1000],
                "car_x": [1.0, 2.0, 3.0], "car_z": [1.0],
            },
        }
        win.ghost_selector.combo.setCurrentIndex(1)
        win.on_ghost_mode_changed()
        app.processEvents()

    check("referência com canais de tamanhos diferentes", ghost_truncado)

    def scrubber():
        win.on_scrubber_pressed()          # entra em modo análise
        for v in (0, 250, 500, 750, 1000):
            win.on_scrubber_moved(v)
        win.on_telemetry_update(provider.get_state())  # não deve mover o cursor
        app.processEvents()
        win.set_live_mode()                # volta para ao vivo
        win.on_telemetry_update(provider.get_state())
        app.processEvents()

    check("scrubber (modo análise) e volta ao modo ao vivo", scrubber)

    def seletor_voltas():
        win.session_manager.completed_laps.clear()
        win.session_manager.completed_laps.append({
            "lap_number": 1,
            "lap_time_str": "1:23.456",
            "metadata": {"track": "Spa", "car": "Test Car"},
            "telemetry": {
                "times": [0.0, 1.0, 2.0],
                "distance": [0.0, 20.0, 40.0],
                "speed": [100.0, 120.0, 140.0],
                "gas": [1.0, 1.0, 0.8],
                "brake": [0.0, 0.0, 0.0],
                "steer": [0.0, 5.0, -2.0],
                "car_x": [10.0, 20.0, 30.0],
                "car_z": [10.0, 20.0, 30.0]
            }
        })
        win.update_lap_selector_items()
        app.processEvents()

        assert win.lap_selector.combo.count() == 2
        win.lap_selector.btn_next.click()
        app.processEvents()
        assert win.lap_selector.combo.currentIndex() == 1
        assert not win.is_live

        win.lap_selector.btn_prev.click()
        app.processEvents()
        assert win.lap_selector.combo.currentIndex() == 0
        assert win.is_live

    check("seletor de voltas e navegação anterior/próxima", seletor_voltas)

    def test_corner_analysis_panel():
        """
        Painel Curva a Curva: mapa manual da pista MOCK carregado, uma linha
        por curva e as faixas sombreadas posicionadas nos quatro gráficos.
        """
        from providers.mock import TRACK_LENGTH

        # Volta sintética completa, com todos os canais que a análise usa
        n = 400
        step = TRACK_LENGTH / n
        telemetry = {
            "times": [i * 0.22 for i in range(n)],
            "distance": [i * step for i in range(n)],
            "speed": [90.0 + 60.0 * abs(((i % 80) / 80.0) - 0.5) for i in range(n)],
            "gas": [1.0 if (i % 80) > 40 else 0.3 for i in range(n)],
            "brake": [0.9 if (i % 80) in (30, 31, 32) else 0.0 for i in range(n)],
            "steer": [0.0] * n,
            "sector": [0] * n,
            "g_lat": [1.5 if (i % 80) > 35 else 0.05 for i in range(n)],
            "car_x": [float(i) for i in range(n)],
            "car_z": [float(i * 2) for i in range(n)],
        }
        win.session_manager.completed_laps.clear()
        win.session_manager.completed_laps.append({
            "lap_number": 3,
            "lap_time_str": "1:28.000",
            "metadata": {"track": "Mock", "car": "Mock"},
            "telemetry": telemetry,
        })
        win.update_lap_selector_items()

        win._refresh_corner_map()
        assert win._corner_map is not None, "o mapa da pista MOCK não foi carregado"
        assert win._corner_map.source == "manual"
        assert len(win._corners) == 8, f"{len(win._corners)} curvas no mapa"

        win._update_corner_analysis()
        app.processEvents()
        table = win.corner_analysis_table
        assert table.rowCount() == 8, f"tabela com {table.rowCount()} linhas"
        assert table.item(0, 0) is not None and table.item(0, 0).text() == "C1"
        # Toda linha tem as sete colunas preenchidas (mesmo que com "--")
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                assert table.item(row, col) is not None, f"célula vazia em {row},{col}"

        # Faixas nos gráficos: uma por curva, em cada um dos quatro gráficos
        assert len(win._corner_regions) >= 8
        visible = [r for regions, _ in win._corner_regions for r in regions if r.isVisible()]
        assert visible, "nenhuma faixa de curva visível"

        # Liga/desliga o destaque
        win.btn_corners.setChecked(False)
        app.processEvents()
        assert not any(r.isVisible() for regions, _ in win._corner_regions for r in regions)
        win.btn_corners.setChecked(True)
        app.processEvents()
        assert any(r.isVisible() for regions, _ in win._corner_regions for r in regions)

    check("painel curva a curva (mapa manual, tabela e faixas)",
          test_corner_analysis_panel)

    def test_corner_analysis_without_corner_map():
        """
        Pista sem mapeamento e sem volta utilizável: a tabela apenas esvazia.

        Este era o caminho perigoso — analisar uma pista desconhecida não pode
        derrubar o dashboard nem gravar mapa de lixo.
        """
        win._corner_map = None
        win._corners = []
        win._corner_track_length = 0.0
        saved_laps = list(win.session_manager.completed_laps)
        saved_state = win._last_state
        win.session_manager.completed_laps.clear()
        # Se sobrar algum ghost utilizável, a detecção automática gravaria um
        # mapa — que vai para o diretório temporário, não para o repositório
        saved_dir_fn = mw.ca.corner_maps_dir
        mw.ca.corner_maps_dir = lambda: work
        try:
            from core.models import TelemetryState
            win._last_state = TelemetryState(track_name="Pista Sem Mapa (TESTE)",
                                             track_length=3000.0)
            win._update_corner_analysis()
            app.processEvents()
            assert win.corner_analysis_table.rowCount() == 0
            assert win._corners == []
        finally:
            mw.ca.corner_maps_dir = saved_dir_fn
            win._last_state = saved_state
            win.session_manager.completed_laps.extend(saved_laps)
            win._refresh_corner_map()

    check("curva a curva em pista sem mapeamento",
          test_corner_analysis_without_corner_map)

    def test_map_base_trace_best_lap():
        win.session_manager.completed_laps.append({
            "lap_number": 2,
            "lap_time_str": "1:20.100",
            "metadata": {"track": "Spa", "car": "Test Car"},
            "telemetry": {
                "times": [0.0, 1.0],
                "car_x": [100.0, 200.0],
                "car_z": [100.0, 200.0]
            }
        })
        win._update_best_map_base_trace()
        map_w = win.sidebar_panel.track_map_card.map_widget
        assert map_w._bg_x == [100.0, 200.0]

    check("mapa cinza usa o traçado da melhor volta válida", test_map_base_trace_best_lap)

    def test_abs_and_electronics_status():
        st = provider.get_state()
        st.has_abs = True
        st.has_tc = True
        st.abs_intervention = 0.5
        st.tc_intervention = 0.4
        win.on_telemetry_update(st)
        app.processEvents()
        
        assert "I" in win.assists_card.led_abs.pill.text() and "ABS" in win.assists_card.led_abs.pill.text()
        assert "I" in win.assists_card.led_tc.pill.text() and "TC" in win.assists_card.led_tc.pill.text()
        abs_data = win.curve_brake_abs.yData
        assert abs_data is not None and len(abs_data) > 0
        tc_data = win.curve_gas_tc.yData
        assert tc_data is not None and len(tc_data) > 0

    check("ABS e TC com destaque nas curvas e eletrônica 1/0", test_abs_and_electronics_status)

    def desconectado():
        from core.models import TelemetryState
        win.on_telemetry_update(TelemetryState(is_connected=False))
        win.sidebar_panel.update_panel(TelemetryState(is_connected=False))
        app.processEvents()

    check("estado desconectado", desconectado)

    def exportar():
        path = win.export_analysis_image(auto=False)
        assert os.path.exists(path), f"imagem não foi criada: {path}"

    check("exportar imagem da análise", exportar)

    def redimensionar():
        for w, h in ((1280, 720), (1920, 1080), (1100, 700)):
            win.resize(w, h)
            app.processEvents()

    check("redimensionar a janela", redimensionar)

finally:
    os.chdir(old_cwd)
    shutil.rmtree(work, ignore_errors=True)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"\n         -> {detail}" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
