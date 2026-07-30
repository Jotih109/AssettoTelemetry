import collections
import os
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QPushButton, QSizePolicy, QSplitter, QProgressBar, QSlider, QScrollArea,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor
import pyqtgraph as pg

from core.models import TelemetryState
from core.engine import TelemetryEngine
from core.session_manager import SessionManager

from ui import theme as T
from ui.sidebar_panel import SidebarPanel
from ui.components import (
    TopMetricCard, SectorsCard, CustomPlot, LapHistoryTable, GhostSelectorCard, LapSelectorCard,
    GForceCard, WeatherCard, SessionCard, AssistsCard, BrakesCard, TireCard,
)

# Auto-exporta uma imagem PNG da análise sempre que uma nova Melhor Volta (Best Lap)
# for concluída. Desligue se preferir só exportar manualmente pelo botão da UI.
AUTO_EXPORT_ON_BEST_LAP = True
EXPORT_DIR = "exportacoes"

# A engine emite a 60 Hz. Os cards numéricos acompanham tudo, mas as curvas
# dos gráficos são redesenhadas 1 a cada N quadros — redesenhar milhares de
# pontos 60 vezes por segundo travaria a interface. 5 => ~12 fps de gráfico.
GRAPH_REDRAW_EVERY_N_FRAMES = 5


class DashboardMainWindow(QMainWindow):
    GRAPH_EVERY_N_FRAMES = GRAPH_REDRAW_EVERY_N_FRAMES

    def __init__(self, engine: TelemetryEngine):
        super().__init__()
        self.engine = engine
        
        self.setWindowTitle("Telemetry Pro - Analysis Tool")
        self.setGeometry(50, 50, 1400, 850)
        self.setMinimumSize(1280, 720)

        self.setStyleSheet(T.app_qss())
        
        self.session_manager = SessionManager()
        self.last_track_car_signature = ""
        self._last_time_seen = ""
        self._graph_x_max = 120.0
        self._last_exported_best = ""
        self._last_state = None
        
        self.init_ui()
        
        # Conecta o seletor de Referência
        self.ghost_selector.combo.currentIndexChanged.connect(self.on_ghost_mode_changed)
        
        # Conecta o sinal da Thread (Engine) para atualizar a UI
        self.engine.on_update.connect(self.on_telemetry_update)
        
        # Inicia a Thread
        self.engine.start()

    def init_ui(self):
        pg.setConfigOption('background', T.BG_PANEL)
        pg.setConfigOption('foreground', T.TXT_UNIT)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        # --- Top Split: Sidebar (Esq) + Main Area (Dir) ---
        top_split = QHBoxLayout()
        top_split.setSpacing(4)
        
        # Sidebar
        self.sidebar_panel = SidebarPanel()
        top_split.addWidget(self.sidebar_panel)
        
        # Main Area (Direita)
        right_area = QVBoxLayout()
        right_area.setSpacing(4)

        # Metrics Row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(4)
        
        self.card_current = TopMetricCard("Volta atual", "--:--.---")
        self.card_best = TopMetricCard("Melhor volta", "--:--.---")
        self.card_delta = TopMetricCard("Delta geral", "+0.00s", is_delta=True)
        self.card_sectors = SectorsCard()
        
        metrics_row.addWidget(self.card_current)
        metrics_row.addWidget(self.card_best)
        metrics_row.addWidget(self.card_delta)
        metrics_row.addWidget(self.card_sectors, stretch=1)
        
        right_area.addLayout(metrics_row)
        
        self.is_live = True
        
        self.btn_live_state = QPushButton("[ 🔴 AO VIVO ]")
        self.btn_live_state.setFont(T.f_title(9))
        self.btn_live_state.setCursor(Qt.PointingHandCursor)
        self.btn_live_state.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_INSET};
                color: #ff3333;
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                padding: 4px 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; }}
        """)
        self.btn_live_state.clicked.connect(self.set_live_mode)
        
        # Barra de ferramentas dos gráficos: legenda | referência | Ref/Est | exportar
        self.lbl_graph_legend = QLabel("──  volta atual        ──  referência")
        self.lbl_graph_legend.setFont(T.f_label(8))
        self.lbl_graph_legend.setStyleSheet(f"color: {T.TXT_UNIT};")

        self.lbl_ref_est_laps = QLabel("Última: --:--.---   Ref: --:--.---   Est: --:--.---")
        self.lbl_ref_est_laps.setFont(QFont(T.FONT_MONO, 11, QFont.Bold))
        self.lbl_ref_est_laps.setStyleSheet(f"color: {T.TXT_LABEL};")

        self.btn_export = QPushButton("EXPORTAR PNG")
        self.btn_export.setFont(T.f_title(8))
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_INSET};
                color: {T.TXT_LABEL};
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; color: {T.TXT_VALUE}; }}
            QPushButton:pressed {{ background-color: {T.BG_APP}; }}
        """)
        self.btn_export.clicked.connect(self.on_export_clicked)

        graph_header_row = QHBoxLayout()
        graph_header_row.setContentsMargins(0, 0, 0, 0)
        graph_header_row.addWidget(self.btn_live_state, alignment=Qt.AlignVCenter)
        graph_header_row.addSpacing(15)
        graph_header_row.addWidget(self.lbl_graph_legend, alignment=Qt.AlignVCenter)
        graph_header_row.addStretch()
        
        self.lap_selector = LapSelectorCard()
        self.lap_selector.combo.currentIndexChanged.connect(self.on_selected_lap_changed)
        graph_header_row.addWidget(self.lap_selector, alignment=Qt.AlignVCenter)
        graph_header_row.addSpacing(15)

        self.ghost_selector = GhostSelectorCard()
        self.ghost_selector.combo.currentIndexChanged.connect(self.on_ghost_mode_changed)
        graph_header_row.addWidget(self.ghost_selector, alignment=Qt.AlignVCenter)
        graph_header_row.addSpacing(15)
        
        graph_header_row.addWidget(self.lbl_ref_est_laps, alignment=Qt.AlignVCenter)
        graph_header_row.addSpacing(15)
        graph_header_row.addWidget(self.btn_export, alignment=Qt.AlignVCenter)
        right_area.addLayout(graph_header_row)

        # --- Pilha de gráficos, no estilo i2 -------------------------------
        # O eixo X (tempo) aparece SÓ no último gráfico: os quatro compartilham
        # a mesma escala, e repetir a régua em cada um só rouba espaço vertical.
        self._speed_y_max = 300.0  # escala Y dinâmica de velocidade

        cursor_pen = pg.mkPen(color="#888888", width=1.0)
        sector_pen = pg.mkPen(color=T.BORDER, width=1.0, style=Qt.DashLine)

        def _sector_lines(plot):
            """Adiciona as divisórias verticais de S1/S2 a um gráfico."""
            l1 = pg.InfiniteLine(pos=31.6, angle=90, pen=sector_pen)
            l2 = pg.InfiniteLine(pos=63.3, angle=90, pen=sector_pen)
            plot.addItem(l1)
            plot.addItem(l2)
            return l1, l2

        # Delta Tempo
        self.plot_delta = CustomPlot("Delta Tempo", color="#FF9100", unit="s")
        self.plot_delta.setYRange(-1.0, 1.0)
        self.plot_delta.setXRange(0, 120)
        self.plot_delta.setLimits(xMin=0, xMax=120, yMin=-10.0, yMax=10.0, minXRange=5, maxXRange=120)
        self.cursor_delta = pg.InfiniteLine(pos=0, angle=90, pen=cursor_pen)
        self.plot_delta.addItem(self.cursor_delta)
        self.zero_line_delta = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color="#555555", style=Qt.DashLine))
        self.plot_delta.addItem(self.zero_line_delta)
        self.sector1_line_delta, self.sector2_line_delta = _sector_lines(self.plot_delta)

        # Marcadores de setor — só no gráfico do topo, para não poluir a pilha
        self.s1_text_delta = pg.TextItem("S1", color=T.TXT_UNIT, anchor=(0, 1))
        self.s1_text_delta.setPos(31.6, 0.8)
        self.plot_delta.addItem(self.s1_text_delta)

        self.s2_text_delta = pg.TextItem("S2", color=T.TXT_UNIT, anchor=(0, 1))
        self.s2_text_delta.setPos(63.3, 0.8)
        self.plot_delta.addItem(self.s2_text_delta)

        # Velocidade
        self.plot_speed = CustomPlot("Velocidade", color=T.CH_SPEED, unit="km/h")
        self.plot_speed.setYRange(0, 300)
        self.plot_speed.setXRange(0, 120)
        self.plot_speed.setLimits(xMin=0, xMax=120, yMin=0, yMax=500, minXRange=5, maxXRange=120)
        self.plot_speed.setXLink(self.plot_delta)
        self.cursor_speed = pg.InfiniteLine(pos=0, angle=90, pen=cursor_pen)
        self.plot_speed.addItem(self.cursor_speed)
        self.sector1_line_speed, self.sector2_line_speed = _sector_lines(self.plot_speed)

        # Pedais
        self.plot_pedals = CustomPlot("Pedais", color=T.CH_THROTTLE, unit="%")
        self.plot_pedals.setYRange(-5, 110)
        self.plot_pedals.setXRange(0, 120)
        self.plot_pedals.setLimits(xMin=0, xMax=120, yMin=-5, yMax=110, minXRange=5, maxXRange=120)
        self.plot_pedals.setXLink(self.plot_delta)
        self.cursor_pedals = pg.InfiniteLine(pos=0, angle=90, pen=cursor_pen)
        self.plot_pedals.addItem(self.cursor_pedals)
        self.sector1_line_pedals, self.sector2_line_pedals = _sector_lines(self.plot_pedals)

        # Volante — último da pilha, é o único que mostra a régua de tempo
        self.plot_steer = CustomPlot("Volante", color="#FFEA00", unit="°", show_x=True)
        self.plot_steer.setYRange(-180, 180)
        self.plot_steer.setXRange(0, 120)
        self.plot_steer.setLimits(xMin=0, xMax=120, yMin=-1080, yMax=1080, minXRange=5, maxXRange=120)
        self.plot_steer.setXLink(self.plot_delta)
        self.cursor_steer = pg.InfiniteLine(pos=0, angle=90, pen=cursor_pen)
        self.plot_steer.addItem(self.cursor_steer)
        self.sector1_line_steer, self.sector2_line_steer = _sector_lines(self.plot_steer)

        # Curvas: a referência (ghost) é traçada mais grossa e translúcida,
        # sempre ATRÁS da volta atual — o mesmo esquema de sobreposição do i2.
        def _ghost_pen(hex_color):
            c = QColor(hex_color)
            c.setAlpha(150)
            return pg.mkPen(color=c, width=2.5, style=Qt.DashLine)

        self.curve_ghost_speed = self.plot_speed.plot(pen=_ghost_pen(T.CH_SPEED))
        self.curve_ghost_gas = self.plot_pedals.plot(pen=_ghost_pen(T.CH_THROTTLE))
        self.curve_ghost_brake = self.plot_pedals.plot(pen=_ghost_pen(T.CH_BRAKE))
        self.curve_ghost_steer = self.plot_steer.plot(pen=_ghost_pen("#FFEA00"))

        self.curve_delta = self.plot_delta.plot(pen=pg.mkPen(color="#FF9100", width=1.8))
        self.curve_speed = self.plot_speed.plot(pen=pg.mkPen(color=T.CH_SPEED, width=1.8))
        self.curve_gas = self.plot_pedals.plot(pen=pg.mkPen(color=T.CH_THROTTLE, width=1.8))
        self.curve_gas_tc = self.plot_pedals.plot(pen=pg.mkPen(color="#1E90FF", width=2.4))
        self.curve_brake = self.plot_pedals.plot(pen=pg.mkPen(color=T.CH_BRAKE, width=1.8))
        self.curve_brake_abs = self.plot_pedals.plot(pen=pg.mkPen(color="#FFEA00", width=2.4))
        self.curve_steer = self.plot_steer.plot(pen=pg.mkPen(color="#FFEA00", width=1.8))

        self.plot_splitter = QSplitter(Qt.Vertical)
        self.plot_splitter.setHandleWidth(2)
        self.plot_splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: {T.BG_APP}; }}")
        
        self.plot_delta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.plot_speed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.plot_pedals.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.plot_steer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        self.plot_delta.setMinimumHeight(50)
        self.plot_speed.setMinimumHeight(50)
        self.plot_pedals.setMinimumHeight(50)
        self.plot_steer.setMinimumHeight(50)
        
        self.plot_splitter.addWidget(self.plot_delta)
        self.plot_splitter.addWidget(self.plot_speed)
        self.plot_splitter.addWidget(self.plot_pedals)
        self.plot_splitter.addWidget(self.plot_steer)
        # Mesma altura para todos; o de baixo ganha um pouco por causa do eixo X
        for i in range(4):
            self.plot_splitter.setStretchFactor(i, 1)
        self.plot_splitter.setSizes([200, 200, 200, 230])

        right_area.addWidget(self.plot_splitter, stretch=1)

        # Barra de progresso da posição na pista
        track_pos_row = QHBoxLayout()
        track_pos_row.setContentsMargins(0, 2, 0, 0)
        lbl_pos = QLabel("VOLTA")
        lbl_pos.setFont(T.f_title(7))
        lbl_pos.setStyleSheet(f"color: {T.TXT_UNIT};")
        lbl_pos.setFixedWidth(CustomPlot.Y_GUTTER)
        self.track_pos_slider = QSlider(Qt.Horizontal)
        self.track_pos_slider.setRange(0, 1000)
        self.track_pos_slider.setValue(0)
        self.track_pos_slider.setFixedHeight(15)
        self.track_pos_slider.setCursor(Qt.PointingHandCursor)
        self.track_pos_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border: 1px solid {T.BORDER_SOFT};
                height: 7px;
                background: {T.BG_INSET};
                margin: 0px 0;
            }}
            QSlider::handle:horizontal {{
                background: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                width: 15px;
                margin: -4px 0;
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {T.CH_SPEED};
            }}
        """)
        self.track_pos_slider.sliderPressed.connect(self.on_scrubber_pressed)
        self.track_pos_slider.valueChanged.connect(self.on_scrubber_moved)
        self.lbl_track_pos_pct = QLabel("0.0%")
        self.lbl_track_pos_pct.setFont(QFont(T.FONT_MONO, 8))
        self.lbl_track_pos_pct.setStyleSheet(f"color: {T.TXT_UNIT};")
        self.lbl_track_pos_pct.setFixedWidth(46)
        self.lbl_track_pos_pct.setAlignment(Qt.AlignRight)
        track_pos_row.addWidget(lbl_pos)
        track_pos_row.addWidget(self.track_pos_slider)
        track_pos_row.addWidget(self.lbl_track_pos_pct)
        right_area.addLayout(track_pos_row)
        
        top_split.addLayout(right_area)
        
        # --- Faixa inferior: histórico de voltas + painéis de análise -------
        self.lap_history_table = LapHistoryTable()
        self.lap_history_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.lap_history_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        main_layout.addLayout(top_split, stretch=1)

        history_panel = T.Panel(title="Histórico de voltas", body_margins=(0, 0, 0, 0))
        history_panel.body.addWidget(self.lap_history_table)
        history_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.lap_history_table.setStyleSheet(
            self.lap_history_table.styleSheet().replace(
                f"border: 1px solid {T.BORDER};", "border: none;"))
                
        self.gforce_card = GForceCard()
        self.weather_card = WeatherCard()
        self.session_card = SessionCard()
        self.assists_card = AssistsCard()
        self.brakes_card = BrakesCard()
        self.tire_card = TireCard()

        # Larguras mínimas por painel: com stretch puro, o histórico era
        # esmagado a ~300 px e as colunas S3/Tempo/Δ Best ficavam invisíveis,
        # e a unidade do vento aparecia cortada no painel da pista.
        # Somam 1484 px com os espaçamentos: cabe inteiro numa janela de 1500 px
        MIN_W = {
            "history": 380, "gforce": 150, "weather": 155,
            "session": 160, "assists": 155, "brakes": 210, "tires": 250,
        }

        cards = (self.gforce_card, self.weather_card, self.session_card,
                 self.assists_card, self.brakes_card, self.tire_card)

        for card in cards:
            card.setStyleSheet(card.styleSheet().replace(f"border: 1px solid {T.BORDER};", "border: none;"))
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            if hasattr(card, 'body'):
                card.body.setContentsMargins(4, 4, 4, 4)

        bottom_row_widget = QWidget()
        footer_layout = QHBoxLayout(bottom_row_widget)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)

        history_panel.setMinimumWidth(MIN_W["history"])
        footer_layout.addWidget(history_panel, stretch=3)
        for key, card in (("gforce", self.gforce_card), ("weather", self.weather_card),
                          ("session", self.session_card), ("assists", self.assists_card),
                          ("brakes", self.brakes_card), ("tires", self.tire_card)):
            card.setMinimumWidth(MIN_W[key])
            footer_layout.addWidget(card, stretch=1)
        
        bottom_row_widget.setFixedHeight(180)
        bottom_row_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Somados, os mínimos dos painéis passam de 1500 px. Em telas menores
        # (1366x768, por exemplo) isso empurraria a janela além do monitor ou
        # cortaria colunas. Numa faixa rolável na horizontal, nada é cortado:
        # em tela larga aparece tudo, em tela estreita você arrasta.
        footer_scroll = QScrollArea()
        footer_scroll.setWidget(bottom_row_widget)
        footer_scroll.setWidgetResizable(True)
        footer_scroll.setFrameShape(QScrollArea.NoFrame)
        footer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        footer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        footer_scroll.setFixedHeight(180 + 12)   # +12 para a barra de rolagem
        footer_scroll.setStyleSheet(f"""
            QScrollArea {{ background: {T.BG_APP}; border: none; }}
            QScrollBar:horizontal {{ background: {T.BG_APP}; height: 8px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: {T.BORDER}; min-width: 40px; }}
            QScrollBar::handle:horizontal:hover {{ background: #3a4249; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        """)

        main_layout.addWidget(footer_scroll, stretch=0)
 
    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_time_ms(t_str: str) -> int:
        """Parse '1:29.650' or '--:--.---' to milliseconds."""
        try:
            if not t_str or '-' in t_str:
                return 0
            if '.' in t_str:
                min_sec, millis = t_str.rsplit('.', 1)
                parts = min_sec.split(':')
                minutes = int(parts[0]) if len(parts) >= 2 else 0
                seconds = int(parts[-1])
                return (minutes * 60 * 1000) + (seconds * 1000) + int(millis.ljust(3, '0')[:3])
        except Exception:
            pass
        return 0

    @staticmethod
    def _format_ms(ms: int) -> str:
        """Format milliseconds back to 'M:SS.mmm'."""
        if ms <= 0:
            return "--:--.---"
        minutes = ms // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"{minutes}:{seconds:02d}.{millis:03d}"

    def _calc_sector_delta(self, current_str: str, ref_str: str) -> str:
        """Returns '+0.123s' / '-0.123s' or '' if data not available."""
        curr_ms = self._parse_time_ms(current_str)
        ref_ms  = self._parse_time_ms(ref_str)
        if curr_ms <= 0 or ref_ms <= 0:
            return ""
        delta_s = (curr_ms - ref_ms) / 1000.0
        return f"{delta_s:+.3f}s"

    def _projected_lap(self, best_time_str: str, delta_s: float) -> str:
        """Best time ± delta → projected finish time string."""
        best_ms = self._parse_time_ms(best_time_str)
        if best_ms <= 0:
            return "--:--.---"
        projected_ms = int(best_ms + delta_s * 1000)
        return self._format_ms(max(0, projected_ms))

    def _update_graph_scale(self, state: TelemetryState, best_time_str: str):
        """
        Ajusta o eixo X (tempo) dos gráficos:
        - Se já existe uma melhor volta, usa BestLapTime * 1.05.
        - Caso contrário, usa um padrão de 2 minutos (120s) conforme solicitado pelo usuário.
        """
        best_ms = self._parse_time_ms(best_time_str)
        if best_ms > 0:
            target = (best_ms / 1000.0) * 1.05
        else:
            target = 120.0  # Padrão inicial de 2 minutos

        target = max(10.0, target)

        # Só reaplica os limites quando a mudança é relevante (evita "tremer" o gráfico)
        if abs(target - self._graph_x_max) > 1.0:
            self._graph_x_max = target
            for plot in (self.plot_delta, self.plot_speed, self.plot_pedals, self.plot_steer):
                plot.setXRange(0, target, padding=0)
                plot.setLimits(xMin=0, xMax=target, minXRange=5, maxXRange=target)

    # -----------------------------------------------------------------------
    # Exportação de imagem (PNG)
    # -----------------------------------------------------------------------

    def export_analysis_image(self, auto: bool = False, lap_number: int = None, lap_time_str: str = None) -> str:
        """
        Salva um snapshot (PNG) do dashboard completo em EXPORT_DIR.
        Chamado manualmente pelo botão "Exportar Análise (Imagem)" ou
        automaticamente quando AUTO_EXPORT_ON_BEST_LAP=True e uma nova
        Melhor Volta é concluída.
        """
        os.makedirs(EXPORT_DIR, exist_ok=True)

        if lap_number is None:
            lap_number = getattr(self._last_state, "lap_number", 0) if self._last_state else 0
        if lap_time_str is None:
            lap_time_str = self.card_best.lbl_val.text()

        safe_time = lap_time_str.replace(":", "-").replace(".", "-")
        prefix = "BestLap" if auto else "Analise"
        filename = f"{prefix}_Volta{lap_number}_{safe_time}.png"
        path = os.path.join(EXPORT_DIR, filename)

        pixmap = self.centralWidget().grab()
        if pixmap.save(path, "PNG"):
            print(f"[Export] Imagem da análise salva em: {path}")
        else:
            print(f"[Export] Falha ao salvar imagem em: {path}")
        return path

    def on_export_clicked(self):
        self.export_analysis_image(auto=False)

    def set_live_mode(self):
        self.is_live = True
        self.btn_live_state.setText("[ 🔴 AO VIVO ]")
        self.btn_live_state.setStyleSheet(self.btn_live_state.styleSheet().replace("#eedd82", "#ff3333"))
        if hasattr(self, 'lap_selector') and self.lap_selector.combo.currentIndex() != 0:
            self.lap_selector.combo.blockSignals(True)
            self.lap_selector.combo.setCurrentIndex(0)
            self.lap_selector.combo.blockSignals(False)
            self.lap_selector._update_nav_buttons()

    def update_lap_selector_items(self):
        if not hasattr(self, 'lap_selector'): return
        completed = self.session_manager.completed_laps
        target_count = len(completed) + 1
        if self.lap_selector.combo.count() == target_count:
            for i, lap in enumerate(completed, start=1):
                num = lap.get("lap_number", i)
                t_str = lap.get("lap_time_str", "--:--.---")
                item_text = f"Volta {num} — {t_str}"
                if self.lap_selector.combo.itemText(i) != item_text:
                    self.lap_selector.combo.setItemText(i, item_text)
            return

        curr_idx = self.lap_selector.combo.currentIndex()
        self.lap_selector.combo.blockSignals(True)
        self.lap_selector.combo.clear()
        self.lap_selector.combo.addItem("Volta Atual (Ao Vivo)")

        for i, lap in enumerate(completed, start=1):
            num = lap.get("lap_number", i)
            t_str = lap.get("lap_time_str", "--:--.---")
            self.lap_selector.combo.addItem(f"Volta {num} — {t_str}")

        if curr_idx < self.lap_selector.combo.count():
            self.lap_selector.combo.setCurrentIndex(curr_idx)
        else:
            self.lap_selector.combo.setCurrentIndex(0)

        self.lap_selector.combo.blockSignals(False)
        self.lap_selector._update_nav_buttons()

    def on_selected_lap_changed(self, idx: int):
        if idx == 0:
            self.set_live_mode()
            return
        
        self.is_live = False
        completed = self.session_manager.completed_laps
        if idx - 1 >= len(completed):
            return

        lap_info = completed[idx - 1]
        lap_num = lap_info.get("lap_number", idx)
        self.btn_live_state.setText(f"[ 📊 VOLTA {lap_num} ]")
        self.btn_live_state.setStyleSheet(self.btn_live_state.styleSheet().replace("#ff3333", "#eedd82"))

        self.render_selected_lap(lap_info)

    def render_selected_lap(self, lap_info: dict):
        telemetry = lap_info.get("telemetry", {})
        times = telemetry.get("times", [])
        if not times:
            return

        gas_100 = [g * 100.0 for g in telemetry.get("gas", [])]
        tc_arr = telemetry.get("tc_intervention", [])
        gas_tc_100 = [
            gas_100[i] if (i < len(tc_arr) and tc_arr[i] > 0.02) else float('nan')
            for i in range(len(gas_100))
        ]

        brake_100 = [b * 100.0 for b in telemetry.get("brake", [])]
        abs_arr = telemetry.get("abs_intervention", [])
        brake_abs_100 = [
            brake_100[i] if (i < len(abs_arr) and abs_arr[i] > 0.02) else float('nan')
            for i in range(len(brake_100))
        ]

        ref_idx = self.ghost_selector.combo.currentIndex()
        ref_ghost = self._reference_ghost_for_index(ref_idx)
        delta_arr = self._calc_lap_delta(telemetry, ref_ghost)

        self.curve_delta.setData(times, delta_arr)
        self.curve_speed.setData(times, telemetry.get("speed", []))
        self.curve_gas.setData(times, gas_100)
        self.curve_gas_tc.setData(times, gas_tc_100)
        self.curve_brake.setData(times, brake_100)
        self.curve_brake_abs.setData(times, brake_abs_100)
        self.curve_steer.setData(times, telemetry.get("steer", []))

        max_time = max(times) if times else 120.0
        self.plot_delta.setXRange(0, max_time)
        self.plot_speed.setXRange(0, max_time)
        self.plot_pedals.setXRange(0, max_time)
        self.plot_steer.setXRange(0, max_time)

        self.sidebar_panel.track_map_card.map_widget.set_live_data(
            telemetry.get("car_x", []), telemetry.get("car_z", []),
            telemetry.get("gas", []), telemetry.get("brake", [])
        )

    def _calc_lap_delta(self, lap_telemetry: dict, ref_ghost: dict) -> list:
        times = lap_telemetry.get("times", [])
        distances = lap_telemetry.get("distance", [])
        ref_times = ref_ghost.get("telemetry", {}).get("times", [])
        ref_distances = ref_ghost.get("telemetry", {}).get("distance", [])

        if not times or not distances or not ref_times or not ref_distances:
            return [0.0] * len(times)

        import bisect
        deltas = []
        for t, d in zip(times, distances):
            if d <= 0 or t <= 0:
                deltas.append(0.0)
                continue
            idx = bisect.bisect_left(ref_distances, d)
            if idx == 0:
                ref_t = ref_times[0]
            elif idx >= len(ref_distances):
                ref_t = ref_times[-1]
            else:
                d0, d1 = ref_distances[idx-1], ref_distances[idx]
                t0, t1 = ref_times[idx-1], ref_times[idx]
                ratio = (d - d0) / (d1 - d0) if d1 != d0 else 0.0
                ref_t = t0 + ratio * (t1 - t0)
            deltas.append(round(t - ref_t, 3))
        return deltas
        
    def on_scrubber_pressed(self):
        self.is_live = False
        self.btn_live_state.setText("[ ⏸ ANÁLISE ]")
        self.btn_live_state.setStyleSheet(self.btn_live_state.styleSheet().replace("#ff3333", "#eedd82"))
        
    def on_scrubber_moved(self, value):
        if self.is_live: return
        self.lbl_track_pos_pct.setText(f"{value / 10.0:.1f}%")
        
        idx = self.ghost_selector.combo.currentIndex()
        ghost = self._reference_ghost_for_index(idx)
        if idx == 0:
            lap_data = self.session_manager.current_lap_data
            track_len = getattr(self._last_state, 'track_length', 4309.0) if hasattr(self, '_last_state') else 4309.0
        else:
            lap_data = ghost.get("telemetry", {})
            distances = lap_data.get("distance", [])
            track_len = max(distances) if distances else 4309.0
            
        distances = lap_data.get("distance", [])
        times = lap_data.get("times", [])
        x_arr = lap_data.get("car_x", [])
        z_arr = lap_data.get("car_z", [])
        
        if not distances or not times: return
        
        target_dist = (value / 1000.0) * track_len
        
        import bisect
        i = bisect.bisect_left(distances, target_dist)
        if i >= len(distances): i = len(distances) - 1
        
        target_time = times[i]
        
        self.cursor_delta.setValue(target_time)
        self.cursor_speed.setValue(target_time)
        self.cursor_pedals.setValue(target_time)
        self.cursor_steer.setValue(target_time)
        
        if i < len(x_arr) and i < len(z_arr):
            gas_val = lap_data.get("gas", [])[i] if i < len(lap_data.get("gas", [])) else 0.0
            brake_val = lap_data.get("brake", [])[i] if i < len(lap_data.get("brake", [])) else 0.0
            self.sidebar_panel.track_map_card.map_widget.set_marker(x_arr[i], z_arr[i], gas_val, brake_val)
            self.sidebar_panel.track_map_card.map_widget.set_live_data(
                x_arr[:i+1], z_arr[:i+1],
                lap_data.get("gas", [])[:i+1], lap_data.get("brake", [])[:i+1]
            )

    def _update_sector_lines(self):
        """Reposition S1/S2 vertical lines using actual sector boundaries from the selected reference ghost."""
        idx = self.ghost_selector.combo.currentIndex()
        ghost = self._reference_ghost_for_index(idx).get("telemetry", {})
        times = ghost.get("times", [])
        sectors = ghost.get("sector", [])
        if len(times) < 2 or len(sectors) < 2:
            return
        s1_end = s2_end = None
        for i in range(1, len(sectors)):
            if s1_end is None and sectors[i] == 1 and sectors[i - 1] == 0:
                s1_end = times[i]
            if s2_end is None and sectors[i] == 2 and sectors[i - 1] == 1:
                s2_end = times[i]
            if s1_end and s2_end:
                break
        if s1_end:
            self.sector1_line_delta.setValue(s1_end)
            self.sector1_line_speed.setValue(s1_end)
            self.sector1_line_pedals.setValue(s1_end)
            self.sector1_line_steer.setValue(s1_end)
            self.s1_text_delta.setPos(s1_end, 0.8)
        if s2_end:
            self.sector2_line_delta.setValue(s2_end)
            self.sector2_line_speed.setValue(s2_end)
            self.sector2_line_pedals.setValue(s2_end)
            self.sector2_line_steer.setValue(s2_end)
            self.s2_text_delta.setPos(s2_end, 0.8)

    # -----------------------------------------------------------------------
    # Main telemetry update slot
    # -----------------------------------------------------------------------

    def _reference_ghost_for_index(self, idx: int) -> dict:
        """Maps the Ghost Selector combo index to the corresponding stored ghost.

        Quando idx == 0 ('Desativado'), as curvas do ghost não são exibidas
        nos gráficos, mas ainda usamos o session best como referência numérica
        (delta, ref, est). Assim o piloto sempre vê valores significativos.
        """
        if idx == 1:   # Personal Best
            return self.session_manager.best_lap_ghost
        elif idx == 2:  # Session Record
            return self.session_manager.session_best_lap_ghost
        elif idx == 3:  # Ideal Lap
            return self.session_manager.ideal_lap_ghost
        # idx == 0 (Desativado): usa session best como referência automática
        # se disponível, senão usa o personal best, senão ghost vazio.
        sbg = self.session_manager.session_best_lap_ghost
        if sbg.get("telemetry", {}).get("times"):
            return sbg
        blg = self.session_manager.best_lap_ghost
        if blg.get("telemetry", {}).get("times"):
            return blg
        return self.session_manager._empty_ghost()

    def on_telemetry_update(self, state: TelemetryState):
        if not state.is_connected:
            return

        self._last_state = state

        # 1. PROCESS STATE FIRST! This calculates Live Delta and Sectors,
        #    using whichever reference lap is currently selected in the sidebar.
        idx = self.ghost_selector.combo.currentIndex()
        reference_ghost = self._reference_ghost_for_index(idx)
        self.session_manager.process_state(state, reference_ghost=reference_ghost)
        self._update_sector_lines()
        
        # Inject calculated fuel avg into state for sidebar to display
        state._fuel_avg = self.session_manager.avg_fuel_per_lap
        
        # Recalculate fuel_laps_remaining using our computed avg
        if self.session_manager.avg_fuel_per_lap > 0 and state.fuel > 0:
            state.fuel_laps_remaining = state.fuel / self.session_manager.avg_fuel_per_lap
        
        self.sidebar_panel.update_panel(state)
        # Atualiza métricas do rodapé unificado
        self.gforce_card.update_g(state.g_lat, state.g_lon)
        self.weather_card.update_weather(
            ambient=state.ambient_temp,
            track=state.track_temp,
            grip=state.surface_grip,
            wind_speed=state.wind_speed,
            wind_dir=state.wind_direction
        )
        self.session_card.update_session(state)
        self.assists_card.update_electronics(state)
        self.brakes_card.update_brakes(state.brake_temp, state.brake_bias)
        
        for i, box in enumerate((self.tire_card.t_fl, self.tire_card.t_fr,
                                 self.tire_card.t_rl, self.tire_card.t_rr)):
            self.tire_card.update_tire(
                box,
                state.tyre_temp[i], state.tyre_pressure[i], state.tyre_wear[i],
                t_inner=state.tyre_temp_inner[i],
                t_middle=state.tyre_temp_middle[i],
                t_outer=state.tyre_temp_outer[i],
            )

        # Valor ao vivo de cada canal, no canto do respectivo gráfico
        self.plot_delta.set_live_value(f"{state.delta_time:+.2f}")
        self.plot_speed.set_live_value(f"{state.speed_kmh:.0f}")
        gas_pct = int(state.gas * 100)
        brk_pct = int(state.brake * 100)
        self.plot_pedals.set_live_value(
            f"<span style='color:{T.CH_THROTTLE};'>{gas_pct}</span> <span style='color:#aaaaaa;'>/</span> <span style='color:{T.CH_BRAKE};'>{brk_pct}</span>",
            is_html=True
        )
        self.plot_steer.set_live_value(f"{state.steer_angle:.0f}")

        # --- Lap time cards ---
        curr_time_str = state.current_time if state.current_time else "--:--.---"
        best_time_str = state.best_time if state.best_time else "--:--.---"
        # Validate best_time: reject if < 30s (pit exit glitch)
        if self._parse_time_ms(best_time_str) < 30000:
            best_time_str = self.session_manager.session_best_lap_ghost["metadata"].get("lap_time_str", "--:--.---") or "--:--.---"
        self.card_current.set_value(curr_time_str)
        self.card_best.set_value(best_time_str)

        # Auto-exporta uma imagem sempre que uma NOVA melhor volta é registrada
        if AUTO_EXPORT_ON_BEST_LAP and best_time_str != "--:--.---" and best_time_str != self._last_exported_best:
            self._last_exported_best = best_time_str
            completed_lap_number = max(1, state.lap_number - 1)
            self.export_analysis_image(auto=True, lap_number=completed_lap_number, lap_time_str=best_time_str)

        # Escala dinâmica do eixo X dos gráficos, baseada na melhor volta / comprimento da pista
        self._update_graph_scale(state, best_time_str)

        # Determine Reference Time based on Ghost Selector (idx already read above)
        # Always pull the time from the ghost's own metadata to stay consistent with
        # the telemetry data used for delta calculation.
        has_valid_reference = False
        ref_lap_str = "--:--.---"

        if idx == 0:  # Desativado — usa session best ou personal best como ref automática
            sbg_str = self.session_manager.session_best_lap_ghost["metadata"].get("lap_time_str", "") or ""
            blg_str = self.session_manager.best_lap_ghost["metadata"].get("lap_time_str", "") or ""
            ref_lap_str = sbg_str or blg_str or "--:--.---"
        elif idx == 1: # Personal Best
            ref_lap_str = (
                self.session_manager.best_lap_ghost["metadata"].get("lap_time_str", "")
                or best_time_str
            )
        elif idx == 2: # Session Record
            ref_lap_str = self.session_manager.session_best_lap_ghost["metadata"].get("lap_time_str", "--:--.---") or "--:--.---"
        elif idx == 3: # Ideal Lap
            ref_lap_str = self.session_manager.ideal_lap_ghost["metadata"].get("lap_time_str", "--:--.---") or "--:--.---"

        if self._parse_time_ms(ref_lap_str) > 0:
            has_valid_reference = True

        # Delta card + projected/reference lap
        delta_val = state.delta_time if has_valid_reference else 0.0
        if has_valid_reference:
            self.card_delta.set_value(f"{delta_val:+.2f}s", delta_val)
        else:
            self.card_delta.set_value("+0.00s", 0.0)
            self.card_delta.lbl_val.setStyleSheet("color: #888888; font-weight: bold;")
        
        # Reference & Estimated lap
        best_ms = self._parse_time_ms(ref_lap_str)
        last_lap = state.last_time if getattr(state, 'last_time', None) else "--:--.---"
        
        if has_valid_reference and best_ms > 0:
            est_ms = best_ms + int(delta_val * 1000)
            last_str = f"Última: {last_lap}"
            ref_str = f"Ref: {ref_lap_str}"
            est_str = f"Est: {self._format_ms(max(0, est_ms))}"
            self.lbl_ref_est_laps.setText(f"<div style='text-align: right;'>"
                                          f"<span style='color: #aaaaaa;'>{last_str}</span>&nbsp;&nbsp;&nbsp;&nbsp;"
                                          f"<span style='color: #eedd82;'>{ref_str}</span>&nbsp;&nbsp;&nbsp;&nbsp;"
                                          f"<span style='color: #aaaaaa;'>{est_str}</span>"
                                          f"</div>")
        else:
            self.lbl_ref_est_laps.setText(f"<div style='text-align: right;'>"
                                          f"<span style='color: #aaaaaa;'>Última: {last_lap}</span>&nbsp;&nbsp;&nbsp;&nbsp;"
                                          f"<span style='color: #888888;'>Ref: --:--.---</span>&nbsp;&nbsp;&nbsp;&nbsp;"
                                          f"<span style='color: #888888;'>Est: --:--.---</span>"
                                          f"</div>")
        
        # --- Sectors ---
        # Garantindo gatilho seguro usando os tempos salvos pelo session_manager
        def format_ms(ms):
            if ms <= 0: return "--:--.---"
            m = ms // 60000
            s = (ms % 60000) // 1000
            mls = ms % 1000
            if m > 0: return f"{m}:{s:02d}.{mls:03d}"
            return f"{s}.{mls:03d}"

        s1_ms = self.session_manager.current_sector_times[0]
        s2_ms = self.session_manager.current_sector_times[1]
        s3_ms = self.session_manager.current_sector_times[2]
        
        s1_val = format_ms(s1_ms)
        s2_val = format_ms(s2_ms)
        s3_val = format_ms(s3_ms)
        
        # Enquanto o setor atual ainda está rolando, podemos deixar o superior como o tempo de volta correndo ou vazio
        if state.sector_index == 0 and s1_ms == 0:
            s1_val = state.current_time  # fallback rolling time
        elif state.sector_index == 1 and s2_ms == 0:
            # We are in sector 2, s1_val is locked. We can show rolling time in S2
            pass
            
        # Deltas de setor seguem a mesma referência selecionada no Ghost Selector
        pb_times = self.session_manager.current_reference_sector_ms
        pb1, pb2, pb3 = format_ms(pb_times[0]), format_ms(pb_times[1]), format_ms(pb_times[2])
        d1, d2, d3 = "", "", ""
        
        if has_valid_reference:
            d1 = self._calc_sector_delta(s1_val, pb1) if s1_ms > 0 else ""
            d2 = self._calc_sector_delta(s2_val, pb2) if s2_ms > 0 else ""
            d3 = self._calc_sector_delta(s3_val, pb3) if s3_ms > 0 else ""
        
        self.card_sectors.update_sectors(
            s1_val, s2_val, s3_val,
            pb1, pb2, pb3,
            d1, d2, d3
        )
        
        # --- Ghost / sector lines ---
        signature = f"{state.track_name}_{state.car_name}"
        if signature != self.last_track_car_signature and signature != "Unknown Track_Unknown Car":
            self.last_track_car_signature = signature
            self._session_max_speed = 0.0
            # Atualiza título da janela com pista e carro
            self.setWindowTitle(f"Telemetry Pro — {state.track_name} | {state.car_name}")
            if self.session_manager.auto_load_ghosts(state):
                self.on_ghost_mode_changed()
            self._update_best_map_base_trace()
            self.update_lap_selector_items()
  
        # --- Track position progress bar ---
        track_pos = getattr(state, 'track_position', 0.0)
        if self.is_live:
            self.track_pos_slider.setValue(int(track_pos * 1000))
            self.lbl_track_pos_pct.setText(f"{track_pos * 100:.1f}%")

        # --- Cursor + curvas dos gráficos ---------------------------------
        # Os mostradores numéricos acompanham os 60 Hz da engine, mas redesenhar
        # as 4 curvas nessa taxa satura a thread da interface: cada volta
        # acumula milhares de pontos e o setData tem custo proporcional.
        # Os gráficos são atualizados a ~12 Hz, que já é imperceptível ao olho.
        curr = self.session_manager.current_lap_data
        self._graph_frame_skip = (getattr(self, "_graph_frame_skip", 0) + 1) % GRAPH_REDRAW_EVERY_N_FRAMES
        if len(curr["times"]) > 0 and self._graph_frame_skip == 0:
            if self.is_live:
                current_time_sec = curr["times"][-1]
                self.cursor_delta.setValue(current_time_sec)
                self.cursor_speed.setValue(current_time_sec)
                self.cursor_pedals.setValue(current_time_sec)
                self.cursor_steer.setValue(current_time_sec)

                if len(curr.get("car_x", [])) > 0 and len(curr.get("car_z", [])) > 0:
                    gas_val = curr.get("gas", [0.0])[-1] if curr.get("gas") else 0.0
                    brake_val = curr.get("brake", [0.0])[-1] if curr.get("brake") else 0.0
                    self.sidebar_panel.track_map_card.map_widget.set_marker(curr["car_x"][-1], curr["car_z"][-1], gas_val, brake_val)
                    self.sidebar_panel.track_map_card.map_widget.set_live_data(
                        curr.get("car_x", []), curr.get("car_z", []),
                        curr.get("gas", []), curr.get("brake", [])
                    )

            # --- Graph data ---
            live_delta = []
            if self.is_live:
                gas_100 = [g * 100.0 for g in curr["gas"]]
                tc_arr = curr.get("tc_intervention", [])
                gas_tc_100 = [
                    gas_100[i] if (i < len(tc_arr) and tc_arr[i] > 0.02) else float('nan')
                    for i in range(len(gas_100))
                ]

                brake_100 = [b * 100.0 for b in curr["brake"]]
                abs_arr = curr.get("abs_intervention", [])
                brake_abs_100 = [
                    brake_100[i] if (i < len(abs_arr) and abs_arr[i] > 0.02) else float('nan')
                    for i in range(len(brake_100))
                ]

                # Recalcula o delta ao vivo usando interpolação por distância
                # (igual ao render_selected_lap) para garantir que o gráfico
                # funciona independentemente do momento em que o ghost foi carregado.
                ref_idx = self.ghost_selector.combo.currentIndex()
                ref_ghost = self._reference_ghost_for_index(ref_idx)
                live_delta = self._calc_lap_delta(curr, ref_ghost)

                self.curve_delta.setData(curr["times"], live_delta)
                self.curve_speed.setData(curr["times"], curr["speed"])
                self.curve_gas.setData(curr["times"], gas_100)
                self.curve_gas_tc.setData(curr["times"], gas_tc_100)
                self.curve_brake.setData(curr["times"], brake_100)
                self.curve_brake_abs.setData(curr["times"], brake_abs_100)
                self.curve_steer.setData(curr["times"], curr.get("steer", []))

            # Escala Y dinâmica para velocidade
            if curr["speed"]:
                self._session_max_speed = max(getattr(self, '_session_max_speed', 0.0), max(curr["speed"]))
                max_speed = self._session_max_speed
                # Adiciona 20 km/h de margem e arredonda para o próximo múltiplo de 20
                # (se o valor já for múltiplo exato de 50, usa 50; caso contrário, 20)
                target_y = max_speed + 20
                if target_y <= 100:
                    step = 20
                elif target_y <= 250:
                    step = 20
                else:
                    step = 50
                import math
                rounded_y = math.ceil(target_y / step) * step
                if rounded_y != self._speed_y_max:
                    self._speed_y_max = rounded_y
                    self.plot_speed.setYRange(0, self._speed_y_max, padding=0)
                    self.plot_speed.setLimits(yMin=0, yMax=max(self._speed_y_max + 50, 350))

            # Escala Y dinâmica para Delta
            delta_arr = live_delta if self.is_live else curr.get("delta", [])
            if delta_arr:
                max_d = max([abs(d) for d in delta_arr] + [1.0])
                target_d = max_d * 1.2
                self.plot_delta.setYRange(-target_d, target_d, padding=0)
            
        # --- Update Lap History dynamically by Lap ID ---
        self._update_live_lap_history(state)
        
    def _update_live_lap_history(self, state: TelemetryState):
        from PyQt5.QtWidgets import QTableWidgetItem
        from PyQt5.QtCore import Qt
        
        def format_ms(ms):
            if ms <= 0: return "--:--.---"
            m = ms // 60000
            s = (ms % 60000) // 1000
            mls = ms % 1000
            if m > 0: return f"{m}:{s:02d}.{mls:03d}"
            return f"{s}.{mls:03d}"

        def set_cell(row, col, text):
            item = self.lap_history_table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self.lap_history_table.setItem(row, col, item)
            elif item.text() != text:
                item.setText(text)

        def ensure_row(lap_num):
            """Return (row_idx, is_new) for the given lap number."""
            if not hasattr(self, '_lap_row_map'):
                self._lap_row_map = {}
            if lap_num in self._lap_row_map:
                return self._lap_row_map[lap_num], False
            
            row_idx = self.lap_history_table.rowCount()
            self.lap_history_table.insertRow(row_idx)
            num_item = QTableWidgetItem(str(lap_num))
            num_item.setTextAlignment(Qt.AlignCenter)
            self.lap_history_table.setItem(row_idx, 0, num_item)
            for col in range(1, 6):
                ph = QTableWidgetItem("--:--.---" if col < 5 else "")
                ph.setTextAlignment(Qt.AlignCenter)
                self.lap_history_table.setItem(row_idx, col, ph)
            self._lap_row_map[lap_num] = row_idx
            return row_idx, True

        # --- 1. Re-sync completed laps ONLY when historic_laps changes ---
        historic_count = len(self.session_manager.historic_laps)
        if getattr(self, '_last_historic_count', -1) != historic_count:
            self._last_historic_count = historic_count
            self.update_lap_selector_items()
            self._update_best_map_base_trace()

            best_time_ms = 0
            best_row_idx = -1

            for lap_data in self.session_manager.historic_laps:
                lap_num = lap_data.get("lap_number", 0)
                if lap_num <= 0:
                    continue
                row_idx, _ = ensure_row(lap_num)
                set_cell(row_idx, 1, lap_data.get("s1", "--:--.---"))
                set_cell(row_idx, 2, lap_data.get("s2", "--:--.---"))
                set_cell(row_idx, 3, lap_data.get("s3", "--:--.---"))
                total_str = lap_data.get("total_time", "--:--.---")
                set_cell(row_idx, 4, total_str)
                lap_ms = self._parse_time_ms(total_str)
                if lap_ms > 0:
                    if best_time_ms == 0 or lap_ms < best_time_ms:
                        best_time_ms = lap_ms
                        best_row_idx = row_idx

            if best_time_ms > 0:
                for lap_data in self.session_manager.historic_laps:
                    lap_num = lap_data.get("lap_number", 0)
                    if lap_num <= 0:
                        continue
                    row_idx, _ = ensure_row(lap_num)
                    total_str = lap_data.get("total_time", "--:--.---")
                    lap_ms = self._parse_time_ms(total_str)
                    if lap_ms > 0:
                        delta_ms = lap_ms - best_time_ms
                        delta_str = "BEST" if delta_ms == 0 else f"+{delta_ms/1000.0:.3f}s"
                        set_cell(row_idx, 5, delta_str)

            prev_best = self.lap_history_table._best_row
            if best_row_idx != prev_best:
                self.lap_history_table.highlight_best_lap(best_row_idx, prev_best)
                self.lap_history_table._best_row = best_row_idx

        # --- 2. Active lap row: update current sector times ---
        lap_number = state.lap_number
        if lap_number > 0:
            row_idx, _ = ensure_row(lap_number)
            sm = self.session_manager
            s1 = format_ms(sm.current_sector_times[0])
            s2 = format_ms(sm.current_sector_times[1])
            s3 = format_ms(sm.current_sector_times[2])
            if sm.current_sector_times[0] > 0:
                set_cell(row_idx, 1, s1)
            if sm.current_sector_times[1] > 0:
                set_cell(row_idx, 2, s2)
            if sm.current_sector_times[2] > 0:
                set_cell(row_idx, 3, s3)

        if getattr(self, '_last_scrolled_lap', -1) != lap_number:
            self.lap_history_table.scrollToBottom()
            self._last_scrolled_lap = lap_number

    def add_lap_to_history(self, lap_data: dict):
        # Historic data is managed by _update_live_lap_history via session_manager.historic_laps
        pass

    def _update_best_map_base_trace(self):
        """Fixa o traçado cinza permanente da pista no mapa usando a MELHOR
        volta válida disponível (Session Best, Personal Best ou a volta mais rápida da sessão)."""
        candidates = []

        # 1. Session Best da sessão atual
        sbg = self.session_manager.session_best_lap_ghost
        sbg_t = sbg.get("telemetry", {})
        sbg_str = sbg.get("metadata", {}).get("lap_time_str", "")
        sbg_ms = self._parse_time_ms(sbg_str)
        if len(sbg_t.get("car_x", [])) >= 2 and sbg_ms > 30000:
            candidates.append((sbg_ms, sbg_t["car_x"], sbg_t["car_z"]))

        # 2. Personal Best gravado em disco
        blg = self.session_manager.best_lap_ghost
        blg_t = blg.get("telemetry", {})
        blg_str = blg.get("metadata", {}).get("lap_time_str", "")
        blg_ms = self._parse_time_ms(blg_str)
        if len(blg_t.get("car_x", [])) >= 2 and blg_ms > 30000:
            candidates.append((blg_ms, blg_t["car_x"], blg_t["car_z"]))

        # 3. Voltas concluídas na sessão atual
        for lap in self.session_manager.completed_laps:
            t_str = lap.get("lap_time_str", "--:--.---")
            ms = self._parse_time_ms(t_str)
            telem = lap.get("telemetry", {})
            cx, cz = telem.get("car_x", []), telem.get("car_z", [])
            if len(cx) >= 2 and ms > 30000:
                candidates.append((ms, cx, cz))

        if candidates:
            candidates.sort(key=lambda item: item[0])
            best_cx, best_cz = candidates[0][1], candidates[0][2]
            self.sidebar_panel.track_map_card.map_widget.set_base_trace(best_cx, best_cz)
        elif self.session_manager.completed_laps:
            last_telem = self.session_manager.completed_laps[-1].get("telemetry", {})
            cx, cz = last_telem.get("car_x", []), last_telem.get("car_z", [])
            if len(cx) >= 2:
                self.sidebar_panel.track_map_card.map_widget.set_base_trace(cx, cz)

    def on_ghost_mode_changed(self):
        idx = self.ghost_selector.combo.currentIndex()
        ghost = None
        
        if idx == 0:
            self.curve_ghost_speed.setData([], [])
            self.curve_ghost_gas.setData([], [])
            self.curve_ghost_brake.setData([], [])
            self.curve_ghost_steer.setData([], [])
            self.sidebar_panel.track_map_card.map_widget.set_data([], [], [], [])
            return
        elif idx == 1:
            ghost = self.session_manager.best_lap_ghost.get("telemetry", {})
        elif idx == 2:
            ghost = self.session_manager.session_best_lap_ghost.get("telemetry", {})
        elif idx == 3:
            ghost = self.session_manager.ideal_lap_ghost.get("telemetry", {})
            
        if ghost and len(ghost.get("times", [])) > 0:
            x_data = ghost.get("times", [])
            n = len(x_data)

            def channel(key, scale=1.0):
                """
                Lê um canal do ghost já ajustado ao tamanho do eixo X.

                Ghosts gravados por versões antigas do app não têm todos os
                canais (car_x/car_z e steer são recentes). O pyqtgraph exige
                X e Y do mesmo tamanho, então completamos com zeros ou
                truncamos em vez de deixar estourar.
                """
                arr = ghost.get(key) or []
                arr = [v * scale for v in arr[:n]]
                if len(arr) < n:
                    arr += [0.0] * (n - len(arr))
                return arr

            self.curve_ghost_speed.setData(x_data, channel("speed"))
            self.curve_ghost_gas.setData(x_data, channel("gas", 100.0))
            self.curve_ghost_brake.setData(x_data, channel("brake", 100.0))
            self.curve_ghost_steer.setData(x_data, channel("steer"))

            self.sidebar_panel.track_map_card.map_widget.set_data(
                ghost.get("car_x", []), ghost.get("car_z", []),
                ghost.get("gas", []), ghost.get("brake", [])
            )
        else:
            self.curve_ghost_speed.setData([], [])
            self.curve_ghost_gas.setData([], [])
            self.curve_ghost_brake.setData([], [])
            self.curve_ghost_steer.setData([], [])
            self.sidebar_panel.track_map_card.map_widget.set_data([], [], [], [])

        if hasattr(self, 'lap_selector') and self.lap_selector.combo.currentIndex() > 0:
            self.on_selected_lap_changed(self.lap_selector.combo.currentIndex())

    def closeEvent(self, event):
        print("Parando Thread de Telemetria...")
        self.engine.stop() 
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Recalcula as proporções dos gráficos no splitter (25% cada)
        if hasattr(self, 'plot_splitter'):
            total_h = self.plot_splitter.height()
            part = total_h // 4
            self.plot_splitter.setSizes([part, part, part, total_h - (part * 3)])
