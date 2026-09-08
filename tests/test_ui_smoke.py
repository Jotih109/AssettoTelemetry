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
from ui.components import EngineerPanel
from core.engine import TelemetryEngine
from providers.mock import MockTelemetryProvider

results = []


def check(name, fn):
    """Executa fn(); reprova se levantar exceção."""
    try:
        fn()
        results.append((name, True, ""))
    except Exception:
        if os.environ.get("SMOKE_VERBOSE"):
            traceback.print_exc()
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


# A engine não deve rodar: dirigimos os updates na mão, no thread da GUI
TelemetryEngine.start = lambda self: None
mw.AUTO_EXPORT_ON_BEST_LAP = False   # não sujar exportacoes/ durante o teste

work = tempfile.mkdtemp(prefix="ac_ui_test_")
old_cwd = os.getcwd()
try:
    # Roda num diretório temporário para não tocar nos ghosts reais
    os.chdir(work)

    # `get_base_dir()` resolve pelo __file__, não pelo diretório de trabalho:
    # sem redirecioná-lo o teste gravaria voltas no telemetry_data/ e um
    # config.json de verdade no repositório — e as voltas de uma execução
    # vazariam para a seguinte, fazendo o resultado depender do histórico.
    # Cada `from ... import` criou um nome próprio, então todos são apontados.
    import core.paths as _paths
    import core.config as _config
    import core.session_manager as _sm
    import core.lap_library as _ll

    _real_base = _paths.get_base_dir()

    def _tmp_app_dir(subfolder):
        # Só o que o teste ESCREVE vai para o temporário. `track_maps/` é
        # material de leitura versionado no repositório: redirecioná-lo
        # esconderia o mapa da pista MOCK que o teste de curvas usa.
        root = work if subfolder == "telemetry_data" else _real_base
        return _paths.ensure_dir(os.path.join(root, subfolder))

    # `core.paths` fica intacto de propósito: `get_app_dir()` resolve
    # `get_base_dir` pelos globais do módulo no momento da chamada, então
    # trocá-lo lá arrastaria junto o track_maps/ de quem importou get_app_dir
    # antes (core.corner_analysis).
    _config.get_base_dir = lambda: work
    _sm.get_app_dir = _tmp_app_dir
    _ll.get_app_dir = _tmp_app_dir
    # O AppConfig é um singleton de processo: zera para ele nascer no temporário
    _config._shared = None

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

    def seletor_ordem_mais_recente_primeiro():
        """
        Ordem do seletor: "Ao Vivo" primeiro e, depois, da volta mais recente
        para a mais antiga. Cada item carrega em `itemData` o índice real em
        completed_laps — a posição na lista NÃO é o índice da volta.
        """
        def fake_lap(num, t_str):
            return {
                "lap_number": num, "lap_time_str": t_str,
                "metadata": {"track": "Spa", "car": "Test Car"},
                "telemetry": {
                    "times": [0.0, 1.0, 2.0], "distance": [0.0, 20.0, 40.0],
                    "speed": [100.0, 120.0, 140.0], "gas": [1.0, 1.0, 0.8],
                    "brake": [0.0, 0.0, 0.0], "steer": [0.0, 5.0, -2.0],
                    "car_x": [10.0, 20.0, 30.0], "car_z": [10.0, 20.0, 30.0],
                },
            }

        win.session_manager.completed_laps.clear()
        for num, t in ((1, "1:23.456"), (2, "1:22.100"), (3, "1:24.900")):
            win.session_manager.completed_laps.append(fake_lap(num, t))
        win.update_lap_selector_items()
        app.processEvents()

        combo = win.lap_selector.combo
        assert combo.count() == 4
        assert "Ao Vivo" in combo.itemText(0)
        assert combo.itemData(0) is None
        textos = [combo.itemText(i) for i in range(1, 4)]
        assert textos[0].startswith("Volta 3"), textos
        assert textos[1].startswith("Volta 2"), textos
        assert textos[2].startswith("Volta 1"), textos
        # itemData aponta para o índice real na lista de voltas concluídas
        assert [combo.itemData(i) for i in range(1, 4)] == [2, 1, 0]

        # Selecionar a primeira volta da lista abre a MAIS RECENTE (volta 3)
        combo.setCurrentIndex(1)
        app.processEvents()
        assert not win.is_live
        assert "3" in win.btn_live_state.text(), win.btn_live_state.text()

        # Uma volta nova empurra as outras para baixo; a seleção continua na
        # mesma volta 3, agora na posição 2
        win.session_manager.completed_laps.append(fake_lap(4, "1:21.750"))
        win.update_lap_selector_items()
        app.processEvents()
        assert combo.count() == 5
        assert combo.itemText(1).startswith("Volta 4")
        assert combo.currentIndex() == 2, combo.currentIndex()
        assert combo.itemData(combo.currentIndex()) == 2

        win.lap_selector.combo.setCurrentIndex(0)
        app.processEvents()
        assert win.is_live

    check("seletor lista Ao Vivo e depois da volta mais recente para a mais antiga",
          seletor_ordem_mais_recente_primeiro)

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
        # O traçado cinza sai da volta mais rápida do CATÁLOGO, não de uma
        # telemetria embutida na lista da sessão: `completed_laps` guarda só
        # metadados desde a biblioteca de voltas.
        sm = win.session_manager
        track, car = sm._track_car if sm._track_car[0] else ("Spa", "Test Car")
        sm._track_car = (track, car)
        sm.library.save_lap(
            track, car,
            telemetry={"times": [0.0, 1.0], "distance": [0.0, 3000.0],
                       "car_x": [100.0, 200.0], "car_z": [100.0, 200.0]},
            lap_time_str="1:20.100", sector_times_ms=[27000, 27000, 26100],
            lap_number=2, session_id="teste", full_lap=True, valid=True)
        # Sem traçado nos ghosts em memória, a busca cai no catálogo. Eles são
        # devolvidos no fim: os testes seguintes contam com uma referência
        # utilizável, e um ghost vazio esquecido aqui deixava o engenheiro
        # sem o que comparar lá na frente.
        ghosts = (sm.session_best_lap_ghost, sm.best_lap_ghost)
        try:
            sm.session_best_lap_ghost = sm._empty_ghost()
            sm.best_lap_ghost = sm._empty_ghost()
            win._update_best_map_base_trace()
            map_w = win.sidebar_panel.track_map_card.map_widget
            assert map_w._bg_x == [100.0, 200.0], map_w._bg_x
        finally:
            sm.session_best_lap_ghost, sm.best_lap_ghost = ghosts

    check("mapa cinza usa o traçado da melhor volta válida", test_map_base_trace_best_lap)

    def test_reference_can_be_any_saved_lap():
        """Escolher uma volta específica como referência, não só os 4 modos."""
        sm = win.session_manager
        track, car = sm._track_car
        rec = sm.library.save_lap(
            track, car,
            telemetry={"times": [i * 0.5 for i in range(60)],
                       "distance": [i * 50.0 for i in range(60)],
                       "speed": [150.0] * 60, "gas": [1.0] * 60,
                       "brake": [0.0] * 60, "steer": [0.0] * 60,
                       "sector": [0] * 60, "rpm": [7000] * 60,
                       "car_x": [float(i) for i in range(60)],
                       "car_z": [float(i) for i in range(60)]},
            lap_time_str="1:18.500", sector_times_ms=[26000, 26000, 26500],
            lap_number=9, session_id="outro-dia", full_lap=True, valid=True)

        win.refresh_reference_choices()
        combo = win.ghost_selector.combo
        idx = win.ghost_selector.index_of(win.ghost_selector.REF_LAP, rec.lap_id)
        assert idx >= 0, "a volta gravada não entrou no seletor de referência"

        combo.setCurrentIndex(idx)
        app.processEvents()
        assert win.ghost_selector.selection() == (win.ghost_selector.REF_LAP,
                                                  rec.lap_id)
        ghost = win.reference_ghost()
        assert ghost["metadata"]["lap_time_str"] == "1:18.500", ghost["metadata"]
        assert len(ghost["telemetry"]["times"]) == 60
        # A escolha fica gravada para a próxima sessão
        assert win.config.reference() == (win.ghost_selector.REF_LAP, rec.lap_id)

    check("qualquer volta gravada pode virar a referência",
          test_reference_can_be_any_saved_lap)

    def test_reference_none_really_disables():
        """'Nenhuma' agora zera o delta em vez de cair no session best."""
        gs = win.ghost_selector
        assert gs.set_selection(gs.REF_NONE)
        app.processEvents()
        assert gs.shows_ghost() is False
        assert win.reference_ghost()["telemetry"]["times"] == []
        # E a automática volta a encontrar uma referência sozinha
        assert gs.set_selection(gs.REF_AUTO)
        app.processEvents()

    check("referência 'Nenhuma' desativa de verdade",
          test_reference_none_really_disables)

    def test_reference_survives_lap_list_growth():
        """Uma volta nova não pode roubar a referência da volta escolhida."""
        sm = win.session_manager
        track, car = sm._track_car
        chosen = sm.library.save_lap(
            track, car,
            telemetry={"times": [i * 0.5 for i in range(40)],
                       "distance": [i * 75.0 for i in range(40)]},
            lap_time_str="1:19.000", sector_times_ms=[0, 0, 0], lap_number=11,
            session_id="outro-dia", full_lap=True, valid=True)
        win.refresh_reference_choices()
        gs = win.ghost_selector
        assert gs.set_selection(gs.REF_LAP, chosen.lap_id)

        # Chega mais uma volta: a lista é refeita e todos os itens se movem
        sm.library.save_lap(
            track, car,
            telemetry={"times": [i * 0.5 for i in range(40)],
                       "distance": [i * 75.0 for i in range(40)]},
            lap_time_str="1:21.000", sector_times_ms=[0, 0, 0], lap_number=12,
            session_id="outro-dia", full_lap=True, valid=True)
        win.refresh_reference_choices()
        assert gs.selection() == (gs.REF_LAP, chosen.lap_id), gs.selection()
        gs.set_selection(gs.REF_AUTO)

    check("a referência acompanha a volta, não a posição na lista",
          test_reference_survives_lap_list_growth)

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

    def test_live_coach_is_wired():
        """
        O coach de curva chega ao painel PELA interface, não só no teste dele.

        Cobre a ligação: mapa de curvas -> coach -> painel do engenheiro. Já
        houve regressão exatamente aqui — o coach funcionando sozinho e nunca
        chamado pela janela.
        """
        from core.live_coach import CornerProfile
        painel = win.engineer_panel
        painel.combo_mode.setCurrentIndex(EngineerPanel.MODE_LIVE)
        app.processEvents()

        win._refresh_corner_map()
        assert win._corners, "sem curvas, o coach não tem o que treinar"

        # Planta um problema conhecido na primeira curva do mapa
        curva = win._corners[0]
        win.coach.set_track(win._corners, win._corner_track_length or 4309.0)
        perfil = win.coach.profiles[curva.index]
        perfil.name = curva.name or f"Curva {curva.index}"
        perfil.samples = 4
        perfil.avg_loss_s = 0.32
        perfil.cause = "brake_early"
        perfil.cause_value = 15.0

        antes = painel.list_messages.count()
        win.coach._last_spoke_at = -999.0
        win.coach.reset_lap()

        # Passa o carro pela reta que antecede a curva, em modo ao vivo
        alvo = curva.start * (win._corner_track_length or 4309.0)
        st = provider.get_state()
        st.session_type = "Practice"
        st.brake, st.gas, st.g_lat = 0.0, 1.0, 0.0
        st.speed_kmh = 240.0
        st.in_pit = st.in_pit_lane = False
        for metro in range(max(0, int(alvo) - 500), int(alvo), 5):
            st.distance_traveled = float(metro)
            st.track_position = metro / (win._corner_track_length or 4309.0)
            win._engineer_clock += 0.25
            win._engineer_live_tick(st)
        app.processEvents()

        assert painel.list_messages.count() > antes,             "o coach não chegou ao painel pela interface"
        textos = [painel.list_messages.item(i).text()
                  for i in range(painel.list_messages.count())]
        assert any("chegando" in t for t in textos),             f"nenhuma dica de aproximação no painel: {textos[-3:]}"

    check("coach de curva ligado na interface", test_live_coach_is_wired)

    def test_engineer_panel():
        """
        Painel do Engenheiro: os três modos, o botão de voz e o ANALISAR.

        A voz é substituída por uma dublê que só anota o que seria falado, para
        o teste não fazer barulho nem depender do SAPI.
        """
        faladas = []
        prioridades = []
        validades = []
        real_voice = win.voice

        class VozFalsa:
            enabled = True
            def say(self, texto, priority=None, ttl=None):
                faladas.append(texto)
                prioridades.append(priority)
                validades.append(ttl)
            def clear(self):
                faladas.clear()
                prioridades.clear()
                validades.clear()
            def stop(self): pass

        win.voice = VozFalsa()
        try:
            painel = win.engineer_panel

            # --- Modo "Ao vivo": problema no carro gera recado e fala ---
            painel.combo_mode.setCurrentIndex(painel.MODE_LIVE)
            painel.btn_voice.setChecked(True)
            st = provider.get_state()
            st.abs_intervention = 0.9
            st.tyre_temp = [120.0, 85.0, 85.0, 85.0]
            st.flag = "AMARELA"
            win.engineer.reset()
            win._engineer_clock = 1000.0
            win._engineer_live_tick(st)
            app.processEvents()
            assert painel.list_messages.count() >= 3, painel.list_messages.count()
            assert faladas, "nada foi falado no modo ao vivo"
            # O mais grave (bandeira/pneu crítico) é o que vai para a voz
            assert "amarela" in faladas[0].lower() or "superaquecido" in faladas[0].lower(), faladas
            # ...e vai com prioridade de crítico, para furar a fila de voz
            from core.voice import PRIORITY_CRITICAL
            assert prioridades[0] == PRIORITY_CRITICAL, prioridades

            # --- Botão de voz desliga a fala, mas não o texto ---
            faladas.clear()
            painel.btn_voice.setChecked(False)
            win.engineer.reset()
            win._engineer_clock = 2000.0
            antes = painel.list_messages.count()
            win._engineer_live_tick(st)
            app.processEvents()
            assert painel.list_messages.count() > antes, "texto parou junto com a voz"
            assert not faladas, f"falou com a voz desligada: {faladas}"

            # --- Modo "Sob demanda": telemetria não gera recado sozinha ---
            painel.combo_mode.setCurrentIndex(painel.MODE_MANUAL)
            win.engineer.reset()
            win._engineer_clock = 3000.0
            antes = painel.list_messages.count()
            win._engineer_live_tick(st)
            win._engineer_lap_report(st)
            app.processEvents()
            assert painel.list_messages.count() == antes, "falou sem ser chamado"

            # --- ...mas o botão ANALISAR funciona em qualquer modo ---
            painel.btn_voice.setChecked(True)
            faladas.clear()
            win.engineer.reset()
            win._engineer_clock = 4000.0
            painel.btn_analyze.click()
            app.processEvents()
            assert painel.list_messages.count() > antes, "ANALISAR não produziu nada"

            # --- Modo "Fim de volta": o balanço sai ao fechar a volta ---
            painel.combo_mode.setCurrentIndex(painel.MODE_LAP)
            win.engineer.reset()
            win._engineer_clock = 5000.0
            antes = painel.list_messages.count()
            win._engineer_on_lap_completed(st)
            app.processEvents()
            assert painel.list_messages.count() > antes, "fim de volta não gerou balanço"
        finally:
            win.voice = real_voice
            win.engineer_panel.combo_mode.setCurrentIndex(EngineerPanel.MODE_LAP)

    check("painel do engenheiro (3 modos, voz e ANALISAR)", test_engineer_panel)

    def test_engineer_survives_broken_data():
        """
        O engenheiro é auxiliar: se ele explodir, o dashboard não pode cair.
        """
        original = win.engineer.analyze_live

        def explode(*a, **kw):
            raise RuntimeError("falha proposital do engenheiro")

        win.engineer.analyze_live = explode
        win.engineer_panel.combo_mode.setCurrentIndex(win.engineer_panel.MODE_LIVE)
        try:
            for _ in range(20):     # passa do intervalo de 15 quadros
                win.on_telemetry_update(provider.get_state())
            app.processEvents()
        finally:
            win.engineer.analyze_live = original
            win.engineer_panel.combo_mode.setCurrentIndex(EngineerPanel.MODE_LAP)

    check("falha do engenheiro não derruba o dashboard",
          test_engineer_survives_broken_data)

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

    # Este vem por ÚLTIMO de propósito: começar uma sessão nova
    # apaga o histórico da tela e o aprendizado do coach, e todo
    # teste depois dele encontraria a janela em branco.
    def test_new_session_resets_the_screen():
        """
        Treino 1 -> Treino 2 sem fechar o jogo: a tela tem que virar a página.

        O catálogo em disco guarda tudo, mas o histórico DA TELA precisa
        mostrar só a sessão em curso — senão a tabela de voltas nunca mais
        bate com o que o piloto está fazendo agora.
        """
        sm = win.session_manager
        antes_catalogo = len(sm.saved_laps())
        idx_antes = sm.session_index

        sm.start_new_session("Qualify")
        st = provider.get_state()
        st.session_type = "Qualify"
        win.on_telemetry_update(st)
        app.processEvents()

        assert sm.session_index > idx_antes, "a sessão não avançou"
        assert win._session_index_seen == sm.session_index,             "a tela não percebeu a sessão nova"
        assert win.lap_history_table.rowCount() <= 1,             f"histórico não foi zerado: {win.lap_history_table.rowCount()} linhas"
        assert not sm.historic_laps or len(sm.historic_laps) <= 1
        assert len(sm.saved_laps()) == antes_catalogo,             "a sessão nova não pode mexer no catálogo em disco"
        assert not win.coach.problem_corners(),             "o coach devia recomeçar o aprendizado na sessão nova"

    check("sessão nova zera a tela e preserva o catálogo",
          test_new_session_resets_the_screen)

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
