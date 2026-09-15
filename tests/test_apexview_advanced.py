import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt5.QtWidgets import QApplication
from core.lap_library import LapLibrary, RetentionPolicy
from ui.telemetry_studio import TelemetryStudioWindow

def test_all():
    app = QApplication.instance() or QApplication([])
    library = LapLibrary(retention=RetentionPolicy(enabled=False))
    win = TelemetryStudioWindow(library)

    print("=== TESTE 1: CARREGAMENTO DUAL E SELECAO DE VOLTA ===")
    print("Titulo da Sessao:", win.lbl_session_title.text())
    print("Contagem do Combo Ref:", win.combo_ref.count())
    print("Opcao Selecionada no Combo Ref:", win.combo_ref.currentText())
    assert win.combo_ref.count() >= 2, "Combo Ref deve conter opcoes de comparacao!"
    assert win.ref_rec is not None, "Volta de referencia deve estar ativa!"
    assert len(win.track_map.ref_cx) > 0, "Tracado de referencia deve estar carregado no mapa!"

    print("\n=== TESTE 2: DELTA TEMPORAL E RENDERIZACAO ===")
    deltas = win.current_tel.get("delta", [])
    assert len(deltas) > 0, "Canal de delta deve ser calculado!"
    min_d, max_d = min(deltas), max(deltas)
    print(f"Delta Range: {min_d:+.4f} s a {max_d:+.4f} s")
    delta_plot = win.plots["delta"]
    yr = delta_plot.viewRange()[1]
    print(f"Delta Y Range: [{yr[0]:.3f}, {yr[1]:.3f}]")
    assert yr[0] <= -0.400 and yr[1] >= 0.400, "Escala minima deve ser de pelo menos -400ms a +400ms!"

    print("\n=== TESTE 3: DIAGRAMA GG POS-SESSAO (FRICTION CIRCLE) ===")
    gg = win.hud.gg_widget
    print("Modo Pos-Treino ativo:", gg.post_session_mode)
    assert gg.post_session_mode is True, "Modo Pos-Treino deve ser o padrao!"
    print("Pontos no Scatter Ativo:", len(gg.all_lat_g))
    print("Pontos no Scatter Ref:", len(gg.ref_lat_g))
    assert len(gg.all_lat_g) > 0, "Scatter plot deve ter dados carregados!"
    gg.resize(150, 150)
    assert gg._cached_scatter_pixmap is not None, "Pixmap de scatter plot deve ser gerado e cacheado!"

    print("\n=== TESTE 4: TABELA DE CURVAS FORENSE ===")
    rows = win.corner_table.rowCount()
    print(f"Total de curvas analisadas: {rows}")
    assert rows > 0, "Tabela de curvas deve ter linhas preenchidas!"
    for r in range(rows):
        curva = win.corner_table.item(r, 0).text()
        frenagem = win.corner_table.item(r, 1).text()
        vmin = win.corner_table.item(r, 2).text()
        retomada = win.corner_table.item(r, 3).text()
        tempo = win.corner_table.item(r, 4).text()
        delta = win.corner_table.item(r, 5).text()
        print(f"[{curva}] Freio: {frenagem} | Vmin: {vmin} | Retomada: {retomada} | Tempo: {tempo} | Delta: {delta}")
        assert vmin != "--", f"V_min na curva {curva} nao pode ser vazio!"
        assert tempo != "--", f"Tempo na curva {curva} nao pode ser vazio!"

    print("\n=== TESTE 5: SINCRONIZACAO POR DISTANCIA NORMALIZADA ===")
    mid_idx = win.track_map.total_points // 2
    win._on_point_seek(mid_idx)
    assert win.track_map.selected_index == mid_idx
    print(f"Index ativo: {win.track_map.selected_index}/{win.track_map.total_points} | Index Ref: {win.track_map.ref_selected_index}/{len(win.track_map.ref_cx)}")
    assert win.track_map.ref_selected_index > 0, "Carro de referencia deve avancar sincronizado!"

    print("\n>>> TODOS OS 5 TESTES PASSARAM COM SUCESSO! <<<")

if __name__ == "__main__":
    test_all()
