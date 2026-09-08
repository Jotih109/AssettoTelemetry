"""
tests/test_mapa_smoke.py — Teste de fumaça da análise pós-sessão
=================================================================
A tela `mapa.pyw` passou a ler o MESMO catálogo do dashboard. Antes ela tinha
um armazenamento só dela, com nomes de canal diferentes (`x`/`throttle` em vez
de `car_x`/`gas`), então nunca abria uma volta gravada pelo app principal —
e não havia teste nenhum que percebesse isso.

Cobre:

  * a árvore Pista > Carro > Sessão > Volta sai do catálogo
  * selecionar uma volta desenha mapa e canais
  * sobrepor várias voltas desenha o delta contra a primeira
  * catálogo vazio não quebra a tela
  * exportar CSV, fixar com alfinete e apagar

Precisa de PyQt5 e pyqtgraph; não precisa do jogo aberto.

    python tests/test_mapa_smoke.py
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
from PyQt5.QtCore import Qt

from core.lap_library import LapLibrary, RetentionPolicy

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


def load_mapa():
    """`mapa.pyw` não é importável por nome: a extensão não é .py."""
    spec = importlib.util.spec_from_file_location(
        "mapa_module", os.path.join(ROOT, "mapa.pyw"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def telemetry(n=300, *, speed=150.0, offset=0.0):
    return {
        "times": [offset + i * 0.3 for i in range(n)],
        "distance": [i * (4000.0 / (n - 1)) for i in range(n)],
        "speed": [speed + (i % 50) for i in range(n)],
        "gas": [0.9] * n,
        "brake": [0.05] * n,
        "steer": [2.0] * n,
        "gear": [4] * n,
        "rpm": [7200] * n,
        "sector": [0 if i < n // 3 else (1 if i < 2 * n // 3 else 2) for i in range(n)],
        "car_x": [float(i) for i in range(n)],
        "car_z": [float(i % 97) for i in range(n)],
    }


TRACK, CAR = "Interlagos", "Porsche Cup"

work = tempfile.mkdtemp(prefix="ac_mapa_test_")
try:
    app = QApplication.instance() or QApplication([])
    mapa = load_mapa()

    # --- Catálogo vazio: a tela abre e avisa, em vez de quebrar -------------
    empty_lib = LapLibrary(data_dir=os.path.join(work, "vazio"),
                           retention=RetentionPolicy(enabled=False))
    empty_win = None

    def build_empty():
        global empty_win
        empty_win = mapa.LapAnalysisWindow(empty_lib)
        assert empty_win.tree.topLevelItemCount() == 0
        assert "Nenhuma volta" in empty_win.lbl_status.text(), empty_win.lbl_status.text()
        # Redesenhar sem seleção nenhuma também não pode estourar
        empty_win.redraw()

    check("catálogo vazio abre a tela sem quebrar", build_empty)

    # --- Catálogo com voltas de duas sessões --------------------------------
    lib = LapLibrary(data_dir=os.path.join(work, "dados"),
                     retention=RetentionPolicy(enabled=False))
    saved = []
    for i, (sid, t_str, spd) in enumerate((
            ("s1", "1:30.000", 150.0),
            ("s1", "1:29.200", 155.0),
            ("s2", "1:31.400", 148.0))):
        saved.append(lib.save_lap(
            TRACK, CAR, telemetry=telemetry(speed=spd, offset=i * 0.1),
            lap_time_str=t_str, sector_times_ms=[30000, 30000, 29200],
            lap_number=i + 1, session_id=sid, full_lap=True, valid=True))
    # E uma volta suja, para conferir que ela aparece marcada
    suja = lib.save_lap(TRACK, CAR, telemetry=telemetry(speed=140.0),
                        lap_time_str="1:35.000", sector_times_ms=[0, 0, 0],
                        lap_number=4, session_id="s2", full_lap=True, valid=False)

    win = None

    def build():
        global win
        win = mapa.LapAnalysisWindow(lib)
        win.resize(1300, 800)

    check("a tela é construída a partir do catálogo", build)

    def tree_structure():
        assert win.tree.topLevelItemCount() == 1, win.tree.topLevelItemCount()
        top = win.tree.topLevelItem(0)
        assert TRACK in top.text(0) and CAR in top.text(0), top.text(0)
        assert top.childCount() == 2, f"{top.childCount()} sessões"
        total = sum(top.child(i).childCount() for i in range(top.childCount()))
        assert total == 4, f"{total} voltas na árvore"
        assert "4 voltas" in win.lbl_status.text(), win.lbl_status.text()

    check("a árvore mostra Pista > Carro > Sessão > Volta", tree_structure)

    def leaf_for(lap_id):
        """Encontra o item da árvore correspondente a uma volta."""
        stack = [win.tree.topLevelItem(i)
                 for i in range(win.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            data = item.data(0, Qt.UserRole)
            if data and data[2] == lap_id:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def select_one():
        item = leaf_for(saved[0].lap_id)
        assert item is not None, "volta não encontrada na árvore"
        win.tree.setCurrentItem(item)
        app.processEvents()
        assert len(win.selected) == 1, win.selected
        assert len(win._loaded) == 1
        # Um item desenhado por canal, além do cursor
        for key, _label, _scale in mapa.CHANNELS:
            plotted = win.plots[key].getPlotItem().listDataItems()
            assert plotted, f"canal {key} sem curva"
        assert win.map_plot.getPlotItem().listDataItems(), "mapa sem traçado"

    check("selecionar uma volta desenha mapa e canais", select_one)

    def overlay_and_delta():
        item_a = leaf_for(saved[0].lap_id)
        item_b = leaf_for(saved[1].lap_id)
        win.tree.clearSelection()
        item_a.setSelected(True)
        item_b.setSelected(True)
        app.processEvents()
        assert len(win.selected) == 2, win.selected
        assert len(win._loaded) == 2
        # O delta só existe com duas ou mais voltas
        delta_items = win.plots["__delta__"].getPlotItem().listDataItems()
        assert delta_items, "delta não foi desenhado com duas voltas"
        # E o delta é contra a PRIMEIRA: uma curva de delta para a segunda volta
        assert len(delta_items) >= 1

    check("sobrepor duas voltas desenha o delta entre elas", overlay_and_delta)

    def axis_switch():
        win.combo_axis.setCurrentIndex(1)   # tempo
        app.processEvents()
        assert win._loaded, "a troca de eixo apagou tudo"
        win.combo_axis.setCurrentIndex(0)   # distância
        app.processEvents()
        assert win._loaded

    check("trocar o eixo X entre distância e tempo", axis_switch)

    def readout():
        xs = win._loaded[0][2]
        win._update_readout(xs[len(xs) // 2])
        texto = win.lbl_readout.text()
        assert "km/h" in texto and "gás" in texto, texto

    check("a leitura sob o cursor mostra os valores", readout)

    def cap_de_voltas():
        """Mais voltas que as cores disponíveis não viram um novelo."""
        win.tree.selectAll()
        app.processEvents()
        assert len(win.selected) <= mapa.MAX_LAPS, len(win.selected)

    check("a sobreposição para no limite de voltas", cap_de_voltas)

    def export_csv():
        destino = os.path.join(work, "volta.csv")
        ok = lib.export_csv(TRACK, CAR, saved[0], destino)
        assert ok and os.path.exists(destino)
        linhas = open(destino, encoding="utf-8").read().strip().splitlines()
        header = linhas[0].split(",")
        assert "speed" in header and "distance" in header and "gear" in header, header
        assert len(linhas) == 301, f"{len(linhas)} linhas"   # 300 amostras + cabeçalho
        assert len(linhas[1].split(",")) == len(header)

    check("exportar a volta como CSV", export_csv)

    def export_motec():
        destino = os.path.join(work, "volta.ld")
        ok = lib.export_motec(TRACK, CAR, saved[0], destino)
        assert ok and os.path.exists(destino)
        assert os.path.exists(os.path.join(work, "volta.ldx"))
        from core.motec import read_ld_file
        parsed = read_ld_file(destino)
        assert parsed["metadata"]["marker"] == 0x40
        assert "Speed" in parsed["channels"] and "Throttle" in parsed["channels"]

    check("exportar a volta como MoTeC (.ld)", export_motec)

    def export_report_test():
        destino_md = os.path.join(work, "relatorio.md")
        ok_md = lib.export_report(TRACK, CAR, saved[0], destino_md, ref_rec=saved[1])
        assert ok_md and os.path.exists(destino_md)
        content_md = open(destino_md, encoding="utf-8").read()
        assert "Relatório de Desempenho" in content_md
        assert TRACK in content_md

        destino_txt = os.path.join(work, "relatorio.txt")
        ok_txt = lib.export_report(TRACK, CAR, saved[0], destino_txt, ref_rec=saved[1])
        assert ok_txt and os.path.exists(destino_txt)
        content_txt = open(destino_txt, encoding="utf-8").read()
        assert "APEXVIEW" in content_txt

        # Testa criação do diálogo LapReportDialog sem erros
        dlg = mapa.LapReportDialog(win, lib, TRACK, CAR, saved[0], ref_rec=saved[1])
        assert dlg.txt_content.toPlainText() or dlg._raw_content
        dlg.close()

    check("gerar e exportar relatório de desempenho (.md / .txt)", export_report_test)

    def pin_and_delete():
        item = leaf_for(suja.lap_id)
        win.tree.clearSelection()
        item.setSelected(True)
        app.processEvents()

        win.on_pin_clicked()
        assert lib.find(TRACK, CAR, suja.lap_id).pinned, "o alfinete não pegou"

        item = leaf_for(suja.lap_id)
        win.tree.clearSelection()
        item.setSelected(True)
        app.processEvents()
        win.on_delete_clicked()
        assert lib.find(TRACK, CAR, suja.lap_id) is None, "a volta não foi apagada"
        assert win.tree.topLevelItem(0).childCount() >= 1

    check("fixar com alfinete e apagar a volta", pin_and_delete)

    def volta_apagada_por_fora():
        """Arquivo removido à mão não pode derrubar a tela."""
        rec = saved[2]
        os.remove(os.path.join(lib.folder_for(TRACK, CAR), rec.file))
        fresh = LapLibrary(data_dir=lib.data_dir,
                           retention=RetentionPolicy(enabled=False))
        outra = mapa.LapAnalysisWindow(fresh)
        assert outra.tree.topLevelItemCount() == 1
        outra.close()

    check("volta apagada por fora não derruba a tela", volta_apagada_por_fora)

finally:
    try:
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass


print()
falhas = 0
for name, ok, detail in results:
    tag = "[OK ]" if ok else "[ERRO]"
    print(f"  {tag} {name}" + (f"\n         -> {detail}" if detail else ""))
    if not ok:
        falhas += 1

print()
print(f"=== {len(results) - falhas}/{len(results)} verificacoes passaram ===")
sys.exit(1 if falhas else 0)
