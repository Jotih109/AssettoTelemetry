import os
import sys
import tempfile
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt5.QtWidgets import QApplication
from core.lap_library import LapLibrary, LapRecord, RetentionPolicy
from ui.telemetry_studio import TelemetryStudioWindow


def test_lap_record_metadata():
    print("=== TESTE 1: METADADOS E PROPRIEDADES DO LAPRECORD ===")
    r = LapRecord(
        lap_id="test_01", track="Interlagos", car="Porsche 992 GT3",
        session_id="20260915_143000", session_type="Qualify",
        lap_number=1, lap_time_str="1:31.500", lap_time_ms=91500,
        timestamp="2026-09-15 14:30:45", full_lap=True, valid=True
    )
    print("day_key:", r.day_key)
    print("day_display:", r.day_display)
    print("session_display_name:", r.session_display_name)
    assert r.day_key == "2026-09-15"
    assert "15/09/2026" in r.day_display
    assert "Qualify" in r.session_display_name
    assert "14:30" in r.session_display_name

    d = r.to_dict()
    assert d["session_type"] == "Qualify"
    r2 = LapRecord.from_dict(d)
    assert r2.session_type == "Qualify"
    print("[OK] Metadados serializados e desserializados com sucesso.")


def test_library_persistence_and_all_records():
    print("\n=== TESTE 2: PERSISTENCIA EM DISCO E ALL_RECORDS ===")
    work_dir = tempfile.mkdtemp(prefix="telemetry_test_")
    try:
        lib = LapLibrary(data_dir=work_dir, retention=RetentionPolicy(enabled=False))
        dummy_tel = {"times": [0.0, 1.0, 2.0], "distance": [0.0, 50.0, 100.0], "speed": [100.0, 110.0, 120.0]}

        rec1 = lib.save_lap(
            "Interlagos", "Ferrari 296",
            telemetry=dummy_tel, lap_time_str="1:32.000",
            sector_times_ms=[30000, 31000, 31000],
            lap_number=1, session_id="sess_q1", session_type="Qualify",
            full_lap=True, valid=True
        )
        assert rec1 is not None
        assert rec1.session_type == "Qualify"

        rec2 = lib.save_lap(
            "Interlagos", "Ferrari 296",
            telemetry=dummy_tel, lap_time_str="1:31.800",
            sector_times_ms=[29900, 31000, 30900],
            lap_number=2, session_id="sess_q1", session_type="Qualify",
            full_lap=True, valid=True
        )

        rec3 = lib.save_lap(
            "Spa", "Porsche 992",
            telemetry=dummy_tel, lap_time_str="2:18.000",
            sector_times_ms=[40000, 50000, 48000],
            lap_number=1, session_id="sess_r1", session_type="Race",
            full_lap=True, valid=True
        )

        all_recs = lib.all_records()
        print(f"Total de registros encontrados em all_records(): {len(all_recs)}")
        assert len(all_recs) == 3

        # Recarrega o índice do disco
        lib2 = LapLibrary(data_dir=work_dir, retention=RetentionPolicy(enabled=False))
        spa_recs = lib2.records("Spa", "Porsche 992")
        assert len(spa_recs) == 1
        assert spa_recs[0].session_type == "Race"
        print("[OK] Leitura e persistência de session_type confirmadas.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_tree_hierarchy_and_filtering():
    print("\n=== TESTE 3: HIERARQUIA DA ARVORE E FILTROS DE BUSCA ===")
    app = QApplication.instance() or QApplication([])
    lib = LapLibrary(retention=RetentionPolicy(enabled=False))
    win = TelemetryStudioWindow(lib)

    # 1. Carrega sessão de demonstração
    win.load_demo_session()
    assert win.tree.topLevelItemCount() > 0

    # Modo padrão: "date"
    print("Modo selecionado:", win.combo_tree_mode.currentData())
    assert win.combo_tree_mode.currentData() == "date"

    # Nível 0: Dia
    day_item = win.tree.topLevelItem(0)
    print("Nível 0 (Dia):", day_item.text(0), "|", day_item.text(1), "|", day_item.text(2))
    assert "📅" in day_item.text(0)

    # Nível 1: Pista
    assert day_item.childCount() > 0
    trk_item = day_item.child(0)
    print("Nível 1 (Pista):", trk_item.text(0))
    assert "🏁" in trk_item.text(0)

    # Nível 2: Carro
    assert trk_item.childCount() > 0
    car_item = trk_item.child(0)
    print("Nível 2 (Carro):", car_item.text(0))
    assert "🏎️" in car_item.text(0)

    # Nível 3: Sessão
    assert car_item.childCount() > 0
    sess_item = car_item.child(0)
    print("Nível 3 (Sessão):", sess_item.text(0))
    assert "⏱️" in sess_item.text(0)
    assert "Qualify" in sess_item.text(0)

    # Nível 4: Voltas
    assert sess_item.childCount() >= 2
    lap1_item = sess_item.child(0)
    lap2_item = sess_item.child(1)
    print("Nível 4 (Volta 1):", lap1_item.text(0), lap1_item.text(1))
    print("Nível 4 (Volta 2):", lap2_item.text(0), lap2_item.text(1))
    assert "1:31.920" in lap1_item.text(1) or "1:31.920" in lap2_item.text(1)

    # Teste de alternância para Modo Pista
    print("\nAlternando para Modo Por Pista...")
    idx_track = win.combo_tree_mode.findData("track")
    win.combo_tree_mode.setCurrentIndex(idx_track)
    top_track = win.tree.topLevelItem(0)
    print("Nível 0 (Modo Pista):", top_track.text(0))
    assert "🏁" in top_track.text(0)

    # Teste de alternância para Modo Carro
    print("Alternando para Modo Por Carro...")
    idx_car = win.combo_tree_mode.findData("car")
    win.combo_tree_mode.setCurrentIndex(idx_car)
    top_car = win.tree.topLevelItem(0)
    print("Nível 0 (Modo Carro):", top_car.text(0))
    assert "🏎️" in top_car.text(0)

    # Volta ao modo Data para teste de filtro
    win.combo_tree_mode.setCurrentIndex(0)

    # Teste de busca / filtro dinâmico
    print("\nTestando busca por 'Qualify'...")
    win.txt_filter.setText("Qualify")
    app.processEvents()
    assert not win.tree.topLevelItem(0).isHidden(), "Nó raiz deve permanecer visível se filho bater"

    print("Testando busca por termo inexistente 'XyzTermoInexistente'...")
    win.txt_filter.setText("XyzTermoInexistente")
    app.processEvents()
    assert win.tree.topLevelItem(0).isHidden(), "Nó raiz deve ser ocultado se nada bater"

    # Limpa filtro
    win.txt_filter.setText("")
    app.processEvents()
    assert not win.tree.topLevelItem(0).isHidden()

    print("\n>>> TODOS OS TESTES DE ORGANIZACAO PASSARAM COM SUCESSO! <<<")


if __name__ == "__main__":
    test_lap_record_metadata()
    test_library_persistence_and_all_records()
    test_tree_hierarchy_and_filtering()
