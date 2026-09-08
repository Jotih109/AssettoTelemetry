"""
mapa.pyw — Análise pós-sessão (offline)
========================================

Abre as voltas que o dashboard gravou e deixa comparar até quatro delas
sobrepostas, com o traçado no mapa, os canais por distância e o delta contra
a primeira volta selecionada.

Não conecta no jogo e não grava nada: é a tela para depois da sessão, quando
o capacete já saiu.

    python mapa.pyw

Antes esta tela tinha um sistema de gravação SÓ DELA (`core/storage.py`,
pasta `telemetry_sessions/`), com nomes de canal diferentes dos do app
principal — `x`/`z`/`throttle` em vez de `car_x`/`car_z`/`gas`. Na prática
isso significava que ela nunca conseguiria abrir uma volta gravada pelo
dashboard. Agora as duas leem o mesmo catálogo (`core/lap_library.py`).
"""

import os
import sys
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSplitter, QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog,
    QAbstractItemView, QComboBox, QMenu, QDialog, QTextEdit,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
import pyqtgraph as pg

from core.lap_library import LapLibrary, LapRecord, RetentionPolicy
from ui import theme as T

#: Cores das voltas sobrepostas, na ordem de seleção. A primeira é a base:
#: é contra ela que o delta das outras é medido.
LAP_COLORS = ("#00e5ff", "#ffb300", "#00e676", "#ff5252")

#: Quantas voltas dá para sobrepor de uma vez. Mais que isto e os gráficos
#: viram um novelo — a leitura útil é comparar duas ou três.
MAX_LAPS = len(LAP_COLORS)

#: Canais desenhados, na ordem, com o rótulo e o fator de escala para exibição
CHANNELS = (
    ("speed", "Velocidade (km/h)", 1.0),
    ("gas", "Acelerador (%)", 100.0),
    ("brake", "Freio (%)", 100.0),
    ("steer", "Volante (°)", 1.0),
)


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


class LapAnalysisWindow(QMainWindow):
    """Navegador do catálogo de voltas + comparação sobreposta."""

    def __init__(self, library: LapLibrary):
        super().__init__()
        self.library = library
        #: [(track, car, LapRecord)] das voltas exibidas, na ordem de seleção
        self.selected = []
        #: [(LapRecord, telemetria, eixo_x, cor)] do que está desenhado
        self._loaded = []

        self.setWindowTitle("ApexView — Análise pós-sessão")
        self.resize(1400, 880)
        self.setStyleSheet(T.app_qss())

        self._build_ui()
        self.reload_catalog()

    # -- construção ----------------------------------------------------------

    def _build_ui(self):
        pg.setConfigOption('background', T.BG_PANEL)
        pg.setConfigOption('foreground', T.TXT_UNIT)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_browser())
        splitter.addWidget(self._build_charts())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1040])
        root.addWidget(splitter)

    def _build_browser(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("VOLTAS GRAVADAS")
        title.setFont(T.f_title(10))
        title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent;")
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Volta", "Tempo", "Data"])
        self.tree.setColumnWidth(0, 170)
        self.tree.setColumnWidth(1, 80)
        # Seleção múltipla: é o que permite sobrepor voltas
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                font-family: "{T.FONT_UI}";
                font-size: 12px;
                outline: none;
            }}
            QTreeWidget::item:selected {{
                background-color: {T.BG_HEADER}; color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER}; color: {T.TXT_TITLE};
                border: none; padding: 4px; font-size: 11px;
            }}
        """)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        layout.addWidget(self.tree, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            f"color: {T.TXT_UNIT}; background: transparent; font-size: 11px;")
        layout.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        for text, slot, tip in (
            ("ATUALIZAR", self.reload_catalog, "Relê o catálogo do disco"),
            ("📌 FIXAR", self.on_pin_clicked,
             "Protege a volta selecionada da limpeza automática"),
            ("RELATÓRIO", self.on_generate_report,
             "Gera relatório analítico completo de desempenho (pontos positivos, negativos e onde melhorar)"),
            ("CSV", self.on_export_csv,
             "Exporta a volta selecionada como CSV"),
            ("MoTeC (.ld)", self.on_export_motec,
             "Exporta a volta selecionada para o formato MoTeC i2 (.ld)"),
            ("APAGAR", self.on_delete_clicked,
             "Apaga do disco as voltas selecionadas"),
        ):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                    border: 1px solid {T.BORDER}; padding: 5px 6px;
                    font-family: "{T.FONT_UI}"; font-size: 11px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {T.BG_HEADER}; color: #fff; }}
            """)
            buttons.addWidget(btn)
        layout.addLayout(buttons)
        return panel

    def _build_charts(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.lbl_laps = QLabel("Selecione uma volta à esquerda "
                               "(Ctrl+clique compara várias)")
        self.lbl_laps.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: transparent; font-size: 12px;")
        header.addWidget(self.lbl_laps, 1)

        header.addWidget(QLabel("Eixo X:"))
        self.combo_axis = QComboBox()
        self.combo_axis.addItem("Distância (m)", "distance")
        self.combo_axis.addItem("Tempo (s)", "times")
        self.combo_axis.currentIndexChanged.connect(self.redraw)
        self.combo_axis.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; padding: 3px 8px; font-size: 12px;
            }}
        """)
        header.addWidget(self.combo_axis)
        layout.addLayout(header)

        body = QSplitter(Qt.Horizontal)

        # --- Mapa da pista ---
        self.map_plot = pg.PlotWidget()
        self.map_plot.setAspectLocked(True)
        self.map_plot.hideAxis('bottom')
        self.map_plot.hideAxis('left')
        self.map_plot.setMenuEnabled(False)
        body.addWidget(self.map_plot)

        # --- Pilha de gráficos ---
        stack_host = QWidget()
        stack = QVBoxLayout(stack_host)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)

        self.plots = {}
        self.cursors = {}
        first = None
        for key, label, _scale in CHANNELS + (("__delta__", "Delta (s)", 1.0),):
            plot = pg.PlotWidget()
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.setMenuEnabled(False)
            plot.getAxis('left').setWidth(58)
            plot.setLabel('left', label)
            if first is None:
                first = plot
            else:
                # Uma régua de X só: arrastar num gráfico move todos
                plot.setXLink(first)
            cursor = pg.InfiniteLine(angle=90, movable=False,
                                     pen=pg.mkPen("#ffffff", width=1))
            plot.addItem(cursor)
            self.cursors[key] = cursor
            self.plots[key] = plot
            stack.addWidget(plot)

        body.addWidget(stack_host)
        body.setSizes([420, 900])
        layout.addWidget(body, 1)

        self.lbl_readout = QLabel("")
        self.lbl_readout.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: {T.BG_INSET}; "
            f"border: 1px solid {T.BORDER}; padding: 4px; font-size: 12px;")
        layout.addWidget(self.lbl_readout)

        # O cursor acompanha o mouse sobre qualquer gráfico da pilha
        for plot in self.plots.values():
            plot.scene().sigMouseMoved.connect(self._make_mouse_handler(plot))
        return panel

    # -- catálogo ------------------------------------------------------------

    def reload_catalog(self):
        """Remonta a árvore Pista > Carro > Sessão > Volta."""
        self.tree.blockSignals(True)
        self.tree.clear()

        total_bytes = 0
        total_laps = 0
        for combo in self.library.catalog():
            track, car = combo["track"], combo["car"]
            total_bytes += combo["bytes"]
            total_laps += combo["laps"]

            best = combo["best"]
            top = QTreeWidgetItem([f"{track} — {car}",
                                   best.lap_time_str if best else "",
                                   f"{combo['laps']} voltas"])
            top.setForeground(0, QColor(T.TXT_TITLE))
            self.tree.addTopLevelItem(top)

            for session in self.library.sessions(track, car):
                node = QTreeWidgetItem([
                    f"Sessão {session['date_str'] or session['session_id']}",
                    session["best"].lap_time_str if session["best"] else "",
                    f"{len(session['laps'])} voltas"])
                node.setForeground(0, QColor(T.TXT_UNIT))
                top.addChild(node)

                for rec in session["laps"]:
                    leaf = QTreeWidgetItem([rec.label(with_date=False),
                                            rec.lap_time_str, rec.date_str])
                    # É por aqui que a seleção sabe qual volta é qual
                    leaf.setData(0, Qt.UserRole, (track, car, rec.lap_id))
                    if not rec.valid:
                        leaf.setForeground(0, QColor("#c98a00"))
                    if best is not None and rec.lap_id == best.lap_id:
                        leaf.setForeground(1, QColor("#00e676"))
                    node.addChild(leaf)

        self.tree.expandToDepth(0)
        self.tree.blockSignals(False)

        if total_laps:
            self.lbl_status.setText(
                f"{total_laps} voltas gravadas · {fmt_bytes(total_bytes)} em disco")
        else:
            self.lbl_status.setText(
                "Nenhuma volta gravada ainda. Rode o dashboard "
                "(main.pyw) e entre na pista.")

    def _selected_records(self):
        """[(track, car, LapRecord)] do que está marcado na árvore."""
        out = []
        for item in self.tree.selectedItems():
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            track, car, lap_id = data
            rec = self.library.find(track, car, lap_id)
            if rec is not None:
                out.append((track, car, rec))
        return out

    # -- desenho -------------------------------------------------------------

    def on_selection_changed(self):
        self.selected = self._selected_records()[:MAX_LAPS]
        self.redraw()

    def redraw(self):
        for plot in self.plots.values():
            plot.clear()
        for key, cursor in self.cursors.items():
            self.plots[key].addItem(cursor)
        self.map_plot.clear()
        self._loaded = []

        if not self.selected:
            self.lbl_laps.setText("Selecione uma volta à esquerda "
                                  "(Ctrl+clique compara várias)")
            self.lbl_readout.setText("")
            return

        axis_key = self.combo_axis.currentData()
        legend = []

        for i, (track, car, rec) in enumerate(self.selected):
            telemetry = self.library.load_telemetry(track, car, rec)
            if not telemetry:
                continue
            color = LAP_COLORS[i % len(LAP_COLORS)]
            x = telemetry.get(axis_key) or telemetry.get("times") or []
            self._loaded.append((rec, telemetry, x, color))

            for key, _label, scale in CHANNELS:
                values = telemetry.get(key) or []
                n = min(len(x), len(values))
                if n < 2:
                    continue
                self.plots[key].plot(x[:n], [v * scale for v in values[:n]],
                                     pen=pg.mkPen(color, width=1))

            cx, cz = telemetry.get("car_x") or [], telemetry.get("car_z") or []
            if len(cx) >= 2 and len(cz) >= 2:
                n = min(len(cx), len(cz))
                self.map_plot.plot(cx[:n], cz[:n], pen=pg.mkPen(color, width=2))

            legend.append(
                f'<span style="color:{color};">■</span> '
                f'{track} / {car} — Volta {rec.lap_number} '
                f'{rec.lap_time_str}{"" if rec.valid else " ⚠"}')

        self._draw_delta(axis_key)
        self.lbl_laps.setText("&nbsp;&nbsp;".join(legend))

    def _draw_delta(self, axis_key: str):
        """
        Delta das demais voltas contra a PRIMEIRA selecionada, interpolado por
        distância — comparar por tempo daria a diferença errada assim que uma
        volta ficasse para trás da outra.
        """
        plot = self.plots["__delta__"]
        if len(self._loaded) < 2:
            return
        base_rec, base_tel, _bx, _bc = self._loaded[0]
        base_d = base_tel.get("distance") or []
        base_t = base_tel.get("times") or []
        if len(base_d) < 2 or len(base_d) != len(base_t):
            return

        import bisect
        for rec, telemetry, x, color in self._loaded[1:]:
            dists = telemetry.get("distance") or []
            times = telemetry.get("times") or []
            n = min(len(dists), len(times), len(x))
            if n < 2:
                continue
            xs, deltas = [], []
            for i in range(n):
                d = dists[i]
                # Fora da faixa que a base cobre não existe delta: extrapolar
                # inventaria dezenas de segundos numa volta parcial.
                if d < base_d[0] or d > base_d[-1]:
                    continue
                j = bisect.bisect_left(base_d, d)
                if j <= 0:
                    ref = base_t[0]
                elif j >= len(base_d):
                    ref = base_t[-1]
                else:
                    d0, d1 = base_d[j - 1], base_d[j]
                    t0, t1 = base_t[j - 1], base_t[j]
                    ratio = (d - d0) / (d1 - d0) if d1 != d0 else 0.0
                    ref = t0 + ratio * (t1 - t0)
                xs.append(x[i])
                deltas.append(times[i] - ref)
            if len(xs) >= 2:
                plot.plot(xs, deltas, pen=pg.mkPen(color, width=1))
        plot.addLine(y=0, pen=pg.mkPen("#666666", style=Qt.DashLine))

    def _make_mouse_handler(self, plot):
        def handler(pos):
            if not getattr(self, "_loaded", None):
                return
            if not plot.sceneBoundingRect().contains(pos):
                return
            x = plot.getPlotItem().vb.mapSceneToView(pos).x()
            for cursor in self.cursors.values():
                cursor.setValue(x)
            self._update_readout(x)
        return handler

    def _update_readout(self, x: float):
        import bisect
        parts = []
        for rec, telemetry, xs, color in self._loaded:
            if len(xs) < 2:
                continue
            i = min(bisect.bisect_left(xs, x), len(xs) - 1)

            def at(key, scale=1.0, nd=0):
                arr = telemetry.get(key) or []
                return f"{arr[i] * scale:.{nd}f}" if i < len(arr) else "--"

            parts.append(
                f'<span style="color:{color};">'
                f'V{rec.lap_number}: {at("speed")} km/h · '
                f'gás {at("gas", 100.0)}% · freio {at("brake", 100.0)}% · '
                f'{at("gear", 1.0)}ª · {at("rpm")} rpm</span>')
        self.lbl_readout.setText("&nbsp;&nbsp;|&nbsp;&nbsp;".join(parts))

    # -- ações ---------------------------------------------------------------

    def on_pin_clicked(self):
        marcadas = self._selected_records()
        if not marcadas:
            return
        for track, car, rec in marcadas:
            self.library.set_pinned(track, car, rec.lap_id, not rec.pinned)
        self.reload_catalog()

    def on_delete_clicked(self):
        marcadas = self._selected_records()
        if not marcadas:
            return
        for track, car, rec in marcadas:
            self.library.delete(track, car, rec.lap_id)
        self.selected = []
        self.reload_catalog()
        self.redraw()

    def on_export_csv(self):
        marcadas = self._selected_records()
        if not marcadas:
            return
        track, car, rec = marcadas[0]
        sugerido = f"{track}_{car}_V{rec.lap_number}_{rec.lap_time_str}.csv"
        sugerido = sugerido.replace(":", "-").replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar volta como CSV", sugerido, "CSV (*.csv)")
        if not path:
            return
        if self.library.export_csv(track, car, rec, path):
            self.lbl_status.setText(f"Exportado: {os.path.basename(path)}")
        else:
            self.lbl_status.setText("Falha ao exportar (veja o console).")

    def on_export_motec(self):
        marcadas = self._selected_records()
        if not marcadas:
            return
        track, car, rec = marcadas[0]
        sugerido = f"{track}_{car}_V{rec.lap_number}_{rec.lap_time_str}.ld"
        sugerido = sugerido.replace(":", "-").replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar volta no formato MoTeC i2", sugerido, "MoTeC i2 Log (*.ld)")
        if not path:
            return
        if not path.lower().endswith(".ld"):
            path += ".ld"
        if self.library.export_motec(track, car, rec, path):
            self.lbl_status.setText(f"MoTeC exportado: {os.path.basename(path)}")
        else:
            self.lbl_status.setText("Falha ao exportar MoTeC (veja o console).")

    def on_generate_report(self):
        marcadas = self._selected_records()
        if not marcadas:
            self.lbl_status.setText("Selecione pelo menos uma volta para gerar o relatório.")
            return
        track, car, rec = marcadas[0]
        ref_rec = marcadas[1][2] if len(marcadas) >= 2 else None
        dlg = LapReportDialog(self, self.library, track, car, rec, ref_rec=ref_rec)
        dlg.exec_()

    def _on_tree_context_menu(self, pos):
        marcadas = self._selected_records()
        if not marcadas:
            return
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {T.BG_PANEL}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; font-size: 11px;
            }}
            QMenu::item {{
                padding: 4px 12px;
            }}
            QMenu::item:selected {{
                background-color: {T.BG_HEADER}; color: #ffffff;
            }}
        """)
        act_report = menu.addAction("📊 Gerar Relatório de Desempenho")
        act_motec = menu.addAction("Exportar MoTeC (.ld)")
        act_csv = menu.addAction("Exportar CSV")
        menu.addSeparator()
        act_pin = menu.addAction("📌 Fixar / Desafixar")
        act_del = menu.addAction("Apagar")

        action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
        if action == act_report:
            self.on_generate_report()
        elif action == act_motec:
            self.on_export_motec()
        elif action == act_csv:
            self.on_export_csv()
        elif action == act_pin:
            self.on_pin_clicked()
        elif action == act_del:
            self.on_delete_clicked()


class LapReportDialog(QDialog):
    """
    Diálogo modal de visualização e exportação do Relatório de Desempenho.
    Exibe o diagnóstico da volta (positivos, negativos, onde melhorar e o porquê),
    permite alternar a volta de referência, copiar para o clipboard ou salvar em arquivo.
    """
    def __init__(self, parent, library: LapLibrary, track: str, car: str,
                 rec: LapRecord, ref_rec: Optional[LapRecord] = None):
        super().__init__(parent)
        self.library = library
        self.track = track
        self.car = car
        self.rec = rec
        self.ref_rec = ref_rec
        self._raw_content = ""

        self.setWindowTitle(
            f"Relatório de Desempenho — {track} · {car} · Volta {rec.lap_number} ({rec.lap_time_str})")
        self.resize(960, 720)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {T.BG_APP};
                color: {T.TXT_VALUE};
                font-family: "{T.FONT_UI}";
            }}
            QLabel {{
                color: {T.TXT_VALUE};
                font-size: 12px;
            }}
            QComboBox {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                padding: 4px 8px;
                font-size: 12px;
            }}
            QPushButton {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                padding: 6px 12px;
                font-family: "{T.FONT_UI}";
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {T.BG_HEADER};
                color: #ffffff;
            }}
            QTextEdit {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                padding: 10px;
                font-family: "Consolas", "{T.FONT_MONO}", monospace;
                font-size: 12px;
                line-height: 1.4;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header com título e metadados
        header_box = QHBoxLayout()
        info_lbl = QLabel(
            f"<b>Pista:</b> {track} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Carro:</b> {car} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Volta {rec.lap_number}:</b> {rec.lap_time_str}"
        )
        info_lbl.setStyleSheet(f"font-size: 13px; color: {T.TXT_VALUE};")
        header_box.addWidget(info_lbl, 1)

        # Seletores de Referência e Formato
        header_box.addWidget(QLabel("Referência:"))
        self.combo_ref = QComboBox()
        self._populate_references()
        self.combo_ref.currentIndexChanged.connect(self._on_params_changed)
        header_box.addWidget(self.combo_ref)

        header_box.addWidget(QLabel("Formato:"))
        self.combo_format = QComboBox()
        self.combo_format.addItem("Markdown Renderizado", "md_rendered")
        self.combo_format.addItem("Markdown Código (.md)", "md_raw")
        self.combo_format.addItem("Texto Puro (.txt)", "txt")
        self.combo_format.currentIndexChanged.connect(self._on_params_changed)
        header_box.addWidget(self.combo_format)

        layout.addLayout(header_box)

        # Área de texto do relatório
        self.txt_content = QTextEdit()
        self.txt_content.setReadOnly(True)
        layout.addWidget(self.txt_content, 1)

        # Barra inferior de ações
        footer = QHBoxLayout()
        self.lbl_msg = QLabel("")
        self.lbl_msg.setStyleSheet(f"color: {T.TXT_UNIT}; font-size: 11px;")
        footer.addWidget(self.lbl_msg, 1)

        btn_copy = QPushButton("📋 COPIAR TEXTO")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.setToolTip("Copia o relatório inteiro para a área de transferência")
        btn_copy.clicked.connect(self.on_copy)
        footer.addWidget(btn_copy)

        btn_save = QPushButton("💾 SALVAR ARQUIVO...")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setToolTip("Salva o relatório em arquivo Markdown (.md) ou Texto (.txt)")
        btn_save.clicked.connect(self.on_save)
        footer.addWidget(btn_save)

        btn_close = QPushButton("FECHAR")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)

        layout.addLayout(footer)

        self._on_params_changed()

    def _populate_references(self):
        best = self.library.best_lap(self.track, self.car)
        all_recs = self.library.records(self.track, self.car)
        valid_recs = [r for r in all_recs if r.lap_id != self.rec.lap_id and r.is_reference_material]

        # 1. Opção Melhor Volta
        if best and best.lap_id != self.rec.lap_id:
            self.combo_ref.addItem(f"★ Melhor Volta (PB: {best.lap_time_str})", best)
        
        # 2. Outras voltas da sessão/catálogo
        for r in valid_recs:
            if best and r.lap_id == best.lap_id:
                continue
            self.combo_ref.addItem(f"Volta {r.lap_number} ({r.lap_time_str}) - {r.date_str}", r)

        # 3. Sem referência
        self.combo_ref.addItem("Sem Referência (Análise Solo)", None)

        # Se passou ref_rec pré-selecionado, seleciona ele
        if self.ref_rec:
            for idx in range(self.combo_ref.count()):
                item_data = self.combo_ref.itemData(idx)
                if item_data and getattr(item_data, "lap_id", None) == self.ref_rec.lap_id:
                    self.combo_ref.setCurrentIndex(idx)
                    break

    def _on_params_changed(self):
        ref_rec = self.combo_ref.currentData()
        fmt_option = self.combo_format.currentData()

        # Gera o formato base (md ou txt)
        is_txt = (fmt_option == "txt")
        base_fmt = "txt" if is_txt else "md"
        content = self.library.generate_lap_report(
            self.track, self.car, self.rec, ref_rec=ref_rec, format=base_fmt)

        if content is None:
            self.txt_content.setPlainText("Não foi possível carregar a telemetria desta volta.")
            self._raw_content = ""
            return

        self._raw_content = content

        if fmt_option == "md_rendered":
            self.txt_content.setMarkdown(content)
        else:
            self.txt_content.setPlainText(content)

        self.lbl_msg.setText("Relatório atualizado.")

    def on_copy(self):
        if not self._raw_content:
            return
        QApplication.clipboard().setText(self._raw_content)
        self.lbl_msg.setText("✓ Conteúdo copiado para a área de transferência!")

    def on_save(self):
        if not self._raw_content:
            return
        fmt_option = self.combo_format.currentData()
        ext = "txt" if fmt_option == "txt" else "md"
        filt = "Markdown (*.md);;Texto Puro (*.txt)" if ext == "md" else "Texto Puro (*.txt);;Markdown (*.md)"
        sugerido = f"Relatorio_{self.track}_{self.car}_V{self.rec.lap_number}_{self.rec.lap_time_str}.{ext}"
        sugerido = sugerido.replace(":", "-").replace(" ", "_")

        path, _ = QFileDialog.getSaveFileName(self, "Salvar Relatório de Desempenho", sugerido, filt)
        if not path:
            return

        ref_rec = self.combo_ref.currentData()
        target_fmt = "txt" if path.lower().endswith(".txt") else "md"
        if self.library.export_report(self.track, self.car, self.rec, path, ref_rec=ref_rec, format=target_fmt):
            self.lbl_msg.setText(f"✓ Relatório salvo em: {os.path.basename(path)}")
        else:
            self.lbl_msg.setText("Falha ao salvar relatório.")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Retenção desligada de propósito: esta tela só lê. Apagar volta aqui é
    # sempre decisão explícita, pelo botão APAGAR.
    library = LapLibrary(retention=RetentionPolicy(enabled=False))
    window = LapAnalysisWindow(library)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
