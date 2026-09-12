import collections
import os
import time
import traceback
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QPushButton, QSizePolicy, QSplitter, QProgressBar, QSlider, QScrollArea,
    QTabWidget,
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
    CornerAnalysisTable, EngineerPanel,
)

from core import corner_analysis as ca
from core.race_engineer import RaceEngineer, ATTENTION, CRITICAL, INFO
from core.live_coach import LiveCoach
from core.corner_bests import CornerBestStore, map_signature
from core.voice import (
    VoiceEngine, PRIORITY_CRITICAL, PRIORITY_LOW, PRIORITY_NORMAL,
)
from core.paths import get_app_dir
from core.config import get_config

#: Severidade do recado -> prioridade na fila de voz. Crítico fura a fila e
#: corta a fala em andamento; INFO é o primeiro a ser descartado se a fila encher.
_VOICE_PRIORITY = {
    CRITICAL: PRIORITY_CRITICAL,
    ATTENTION: PRIORITY_NORMAL,
    INFO: PRIORITY_LOW,
}

EXPORT_DIR = get_app_dir("exportacoes")

# Padrões que agora moram no config.json (core/config.py) e podem ser mudados
# sem editar código: auto-exportar PNG a cada melhor volta, e de quantos em
# quantos quadros as curvas são redesenhadas.
#
# A engine emite a 60 Hz. Os cards numéricos acompanham tudo, mas redesenhar
# milhares de pontos 60 vezes por segundo travaria a interface — 5 quadros
# dão ~12 fps de gráfico, imperceptível ao olho.
AUTO_EXPORT_ON_BEST_LAP = True
GRAPH_REDRAW_EVERY_N_FRAMES = 5


class DashboardMainWindow(QMainWindow):
    GRAPH_EVERY_N_FRAMES = GRAPH_REDRAW_EVERY_N_FRAMES

    def __init__(self, engine: TelemetryEngine):
        super().__init__()
        self.engine = engine
        
        self.setWindowTitle("ApexView — Assetto Corsa Telemetry Pro")
        self.setGeometry(50, 50, 1400, 850)
        self.setMinimumSize(1280, 720)

        self.setStyleSheet(T.app_qss())

        # Preferências do usuário (config.json na raiz do app). Lidas antes de
        # montar a interface: a retenção de voltas e a referência padrão
        # dependem delas.
        self.config = get_config()
        self.session_manager = SessionManager(
            retention=self.config.retention_policy())
        #: Referência escolhida na sessão anterior, reaplicada quando o
        #: catálogo de voltas da pista/carro é carregado
        self._pending_reference = None
        self._ref_cache_key = None
        self._ref_cache_value = None
        # Preferência do usuário, limitada pelo interruptor do módulo: o teste
        # de fumaça zera AUTO_EXPORT_ON_BEST_LAP para não sujar exportacoes/.
        self.auto_export_on_best = bool(
            self.config.get("auto_export_on_best_lap"))
        self.graph_every_n_frames = max(
            1, int(self.config.get("graph_redraw_every_n_frames")))
        self.last_track_car_signature = ""
        self._last_time_seen = ""
        self._graph_x_max = 120.0
        self._last_exported_best = ""
        self._last_state = None

        # --- Análise Curva a Curva ---
        self._corner_map = None          # core.corner_analysis.CornerMap
        self._corners = []               # lista de Corner do mapa em uso
        self._corner_track_length = 0.0
        self._corner_regions = []        # faixas sombreadas nos gráficos
        self._corner_comparisons = []
        self.show_corner_regions = True

        # --- Engenheiro de pista ---
        self.engineer = RaceEngineer()
        #: Coach de curva: fala ANTES e LOGO DEPOIS de cada curva, enquanto o
        #: piloto ainda pode fazer alguma coisa a respeito (core/live_coach.py)
        self.coach = LiveCoach()
        #: Guarda a melhor passagem de cada curva entre sessões, para o coach
        #: não recomeçar do zero no Treino 2 (core/corner_bests.py)
        self.corner_bests = CornerBestStore(self.session_manager.library)
        self._coach_seed_signature = None
        self._corner_best_cache = ({}, [])
        # Como as curvas são chamadas nos recados (número, por padrão). É
        # preferência do piloto, então mora no config; o módulo de análise
        # guarda só o valor em uso, para não depender da configuração.
        estilo = str(self.config.get("corner_label_style") or "")
        if estilo in (ca.CORNER_LABEL_NUMBER, ca.CORNER_LABEL_NAME):
            ca.label_style = estilo

        # Mesa de som: o volume de cada assunto do engenheiro. Lida uma vez;
        # a janela de ajuste (ajustar_voz.pyw) grava no config e o dashboard
        # pega na próxima abertura.
        self.voice_mix = self.config.voice_mix()

        # Voz montada a partir das preferências: o timbre é gosto pessoal, e
        # as vozes instaladas mudam de máquina para máquina.
        self.voice = VoiceEngine(
            enabled=True,
            rate=int(self.config.get("voice_rate")),
            pitch=int(self.config.get("voice_pitch")),
            volume=int(self.config.get("voice_volume")),
            backend=str(self.config.get("voice_backend") or "auto"),
            preferred_voice=str(self.config.get("voice_name") or ""),
            prefer_male=bool(self.config.get("voice_prefer_male")))
        self._engineer_last_lap_count = 0
        self._engineer_clock = 0.0   # segundos desde o início da sessão
        self._session_index_seen = 0
        self._laps_recorded_seen = 0

        self.init_ui()

        # O seletor de Referência é conectado uma única vez, em init_ui().
        # Havia um segundo connect() aqui: o Qt permite ligar o mesmo slot duas
        # vezes, então cada troca de referência redesenhava tudo em dobro.

        # Restaura a referência que estava selecionada na última sessão. As
        # voltas do catálogo ainda não foram lidas (só chegam ao entrar na
        # pista), então uma referência do tipo "lap" é reaplicada em
        # refresh_reference_choices().
        kind, lap_id = self.config.reference()
        self._pending_reference = (kind, lap_id)
        self.ghost_selector.combo.blockSignals(True)
        self.ghost_selector.set_selection(kind, lap_id)
        self.ghost_selector.combo.blockSignals(False)

        self._restore_engineer_preferences()

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

        # Liga/desliga as faixas sombreadas das curvas sobre os gráficos
        self.btn_corners = QPushButton("CURVAS")
        self.btn_corners.setFont(T.f_title(8))
        self.btn_corners.setCursor(Qt.PointingHandCursor)
        self.btn_corners.setCheckable(True)
        self.btn_corners.setChecked(True)
        self.btn_corners.setToolTip("Destaca os limites de cada curva nos gráficos")
        self.btn_corners.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_INSET};
                color: {T.TXT_LABEL};
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; color: {T.TXT_VALUE}; }}
            QPushButton:checked {{ color: {T.CH_SPEED}; border: 1px solid {T.CH_SPEED}; }}
        """)
        self.btn_corners.toggled.connect(self.on_corner_regions_toggled)

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
        graph_header_row.addWidget(self.btn_corners, alignment=Qt.AlignVCenter)
        graph_header_row.addSpacing(4)
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
                
        # Painel Curva a Curva — mesma faixa do histórico, à direita dele
        self.corner_analysis_table = CornerAnalysisTable()
        self.corner_analysis_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.corner_analysis_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Painel do Engenheiro de Pista — recados, seletor de modo e voz
        self.engineer_panel = EngineerPanel()
        self.engineer_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Dentro da aba, a faixa de título do card é redundante com o rótulo
        # da própria aba — e no rodapé de 180 px cada pixel na vertical conta
        self.engineer_panel.lbl_title.setVisible(False)
        self.engineer_panel.setStyleSheet(
            self.engineer_panel.styleSheet().replace(
                f"border: 1px solid {T.BORDER};", "border: none;"))

        # Curva a curva e Engenheiro são as duas leituras da MESMA volta e se
        # olha uma por vez. Em abas, elas dividem uma coluna do rodapé em vez de
        # disputar largura com os cards do carro — nove colunas não cabiam em
        # 1900 px, e o resultado era "SESSÃO — PRACTIC" e o painel de pneus
        # cortados na borda.
        self.analysis_tabs = QTabWidget()
        self.analysis_tabs.setDocumentMode(True)
        # Rótulos curtos: os dois precisam caber na barra de abas de ~300 px,
        # senão o Qt corta o texto e põe setas de rolagem
        self.analysis_tabs.addTab(self.corner_analysis_table, "CURVAS")
        self.analysis_tabs.addTab(self.engineer_panel, "ENGENHEIRO")
        self.analysis_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background-color: {T.BG_PANEL};
                border: 1px solid {T.BORDER}; border-radius: 0px;
            }}
            QTabBar::tab {{
                background-color: {T.BG_HEADER}; color: {T.TXT_TITLE};
                border: 1px solid {T.BORDER}; border-bottom: none;
                padding: 3px 10px; margin-right: 1px;
                font-family: "{T.FONT_UI}"; font-size: 8pt; font-weight: bold;
            }}
            QTabBar::tab:selected {{
                background-color: {T.BG_PANEL}; color: {T.TXT_VALUE};
            }}
            QTabBar::tab:hover {{ color: {T.TXT_VALUE}; }}
        """)
        self.engineer_panel.btn_analyze.clicked.connect(self.on_engineer_analyze_clicked)
        self.engineer_panel.btn_voice.toggled.connect(self.on_engineer_voice_toggled)
        self.engineer_panel.combo_mode.currentIndexChanged.connect(
            self.on_engineer_mode_changed)

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
        # O painel de curvas precisa de ~195 px para mostrar as três colunas
        # inteiras: com menos que isso, a coluna de delta — a que interessa —
        # aparecia cortada ("+0.2…").
        # A coluna de análise (abas Curva a curva / Engenheiro) precisa de ~300 px:
        # é o que faz o texto do engenheiro quebrar em linhas legíveis e a coluna
        # de delta das curvas aparecer inteira.
        # Freios, Pneus e Eletrônica ficam FORA daqui de propósito: o conteúdo
        # deles (grade 2x2 com temperatura de 3 dígitos, pílulas "I KERS") já
        # define uma largura mínima maior que qualquer número que eu escrevesse
        # aqui — e um mínimo explícito MENOR sobrepõe o que o Qt calcula, que
        # era o que fazia "FL 180 °C" e "I ABS" aparecerem com letra comida.
        MIN_W = {
            # 165 na sessão porque o título dela carrega o tipo:
            # "SESSÃO — PRACTICE" não cabia em 140 e virava "SESSÃO — PRACTIC"
            "history": 360, "analise": 300, "weather": 140, "session": 165,
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
        self.analysis_tabs.setMinimumWidth(MIN_W["analise"])
        footer_layout.addWidget(self.analysis_tabs, stretch=3)
        for key, card in (("gforce", self.gforce_card), ("weather", self.weather_card),
                          ("session", self.session_card), ("assists", self.assists_card),
                          ("brakes", self.brakes_card), ("tires", self.tire_card)):
            if key in MIN_W:
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

    def _combo_lap_index(self, combo_idx: int):
        """
        Índice em `completed_laps` para uma posição do combo (None = Ao Vivo).

        A posição no combo NÃO é o índice da volta: a lista é exibida da volta
        mais recente para a mais antiga, então cada item carrega o índice real
        em `itemData`.
        """
        if combo_idx <= 0:
            return None
        data = self.lap_selector.combo.itemData(combo_idx)
        return data if isinstance(data, int) else None

    def update_lap_selector_items(self):
        """
        Preenche o seletor: "Ao Vivo" primeiro e, abaixo, as voltas concluídas
        da mais recente para a mais antiga — a volta que você acabou de fazer é
        a que você quer abrir, e ela fica sempre no topo da lista.
        """
        if not hasattr(self, 'lap_selector'): return
        completed = self.session_manager.completed_laps
        combo = self.lap_selector.combo

        items = []
        for i in range(len(completed) - 1, -1, -1):
            lap = completed[i]
            num = lap.get("lap_number", i + 1)
            t_str = lap.get("lap_time_str", "--:--.---")
            items.append((f"Volta {num} — {t_str}", i))

        # Mesma quantidade: só acerta textos e dados (o tempo da volta pode
        # chegar depois do item ter sido criado)
        if combo.count() == len(items) + 1:
            for pos, (text, lap_i) in enumerate(items, start=1):
                if combo.itemText(pos) != text:
                    combo.setItemText(pos, text)
                if combo.itemData(pos) != lap_i:
                    combo.setItemData(pos, lap_i)
            return

        prev_lap_i = self._combo_lap_index(combo.currentIndex())
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Volta Atual (Ao Vivo)", None)
        for text, lap_i in items:
            combo.addItem(text, lap_i)

        # Uma volta nova empurra todas as outras uma posição para baixo; a
        # seleção acompanha a MESMA volta, não a mesma posição da lista.
        new_pos = 0
        if prev_lap_i is not None:
            for pos in range(1, combo.count()):
                if combo.itemData(pos) == prev_lap_i:
                    new_pos = pos
                    break
        combo.setCurrentIndex(new_pos)

        combo.blockSignals(False)
        self.lap_selector._update_nav_buttons()

    def on_selected_lap_changed(self, idx: int):
        if idx == 0:
            self.set_live_mode()
            self._update_corner_analysis()
            return

        self.is_live = False
        completed = self.session_manager.completed_laps
        lap_i = self._combo_lap_index(idx)
        if lap_i is None or lap_i >= len(completed):
            return

        lap_info = completed[lap_i]
        lap_num = lap_info.get("lap_number", lap_i + 1)
        self.btn_live_state.setText(f"[ 📊 VOLTA {lap_num} ]")
        self.btn_live_state.setStyleSheet(self.btn_live_state.styleSheet().replace("#ff3333", "#eedd82"))

        self.render_selected_lap(lap_info)

    def render_selected_lap(self, lap_info: dict):
        # A telemetria vem do catálogo sob demanda: `completed_laps` guarda só
        # os metadados da volta.
        telemetry = self.session_manager.telemetry_for(lap_info)
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

        delta_arr = self._calc_lap_delta(telemetry, self.reference_ghost())

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

        # A tabela curva a curva acompanha a volta exibida nos gráficos
        self._update_corner_analysis()

    def _calc_lap_delta(self, lap_telemetry: dict, ref_ghost: dict) -> list:
        times = lap_telemetry.get("times", [])
        distances = lap_telemetry.get("distance", [])
        ref_times = ref_ghost.get("telemetry", {}).get("times", [])
        ref_distances = ref_ghost.get("telemetry", {}).get("distance", [])

        if not times or not distances or not ref_times or not ref_distances:
            return [0.0] * len(times)

        import bisect
        deltas = []
        # Fora da faixa de distância coberta pela referência não existe delta:
        # extrapolar o primeiro/último ponto do fantasma inventava dezenas de
        # segundos quando a referência era uma volta parcial.
        ref_first, ref_last = ref_distances[0], ref_distances[-1]
        for t, d in zip(times, distances):
            if d <= 0 or t <= 0 or d < ref_first or d > ref_last:
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
        
    def _displayed_lap_telemetry(self) -> dict:
        """
        Telemetria da volta que está desenhada nos gráficos: a selecionada no
        seletor de voltas ou, no modo ao vivo, a volta em andamento.
        """
        if hasattr(self, "lap_selector"):
            lap_i = self._combo_lap_index(self.lap_selector.combo.currentIndex())
            completed = self.session_manager.completed_laps
            if lap_i is not None and lap_i < len(completed):
                telemetry = self.session_manager.telemetry_for(completed[lap_i])
                if telemetry.get("times"):
                    return telemetry
        return self.session_manager.current_lap_data

    def on_scrubber_moved(self, value):
        if self.is_live: return
        self.lbl_track_pos_pct.setText(f"{value / 10.0:.1f}%")
        
        # O cursor anda sobre a volta que está DESENHADA. Antes ele andava
        # sobre o ghost de referência quando havia uma escolhida, e sobre a
        # volta atual quando não havia: arrastar o cursor movia o marcador do
        # mapa por uma volta enquanto os gráficos embaixo eram de outra.
        lap_data = self._displayed_lap_telemetry()
        distances = lap_data.get("distance", [])
        track_len = (getattr(self._last_state, 'track_length', 0.0) or 0.0
                     if self._last_state is not None else 0.0)
        if track_len <= 0:
            track_len = max(distances) if distances else 4309.0

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
        ghost = self.reference_ghost().get("telemetry", {})
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
    # Análise Curva a Curva (Turn-by-Turn)
    # -----------------------------------------------------------------------

    def _corner_reference_traces(self) -> list:
        """
        Voltas que servem de base para DETECTAR as curvas da pista.

        Três exigências, e a terceira é a que mais importa:

        * **Volta inteira.** Detectar em cima de meia volta só encontra as
          curvas daquela metade — foi assim que o mapa automático de Spa ficou
          com curvas apenas até 54% da pista.
        * **Canais suficientes:** distância e G lateral (ou o traçado X/Z, de
          onde o G lateral é reconstruído por curvatura).
        * **Volta LIMPA.** Volta com corte de pista não pode moldar o mapa: a
          geometria do corte entra como se fosse o traçado, e como o mapa é
          gravado em disco aquilo ficava valendo para sempre. E a volta que
          semeava o mapa era tipicamente a primeira da sessão numa pista
          nova — justo a mais propensa a abrir demais.

        Devolve até CONSENSUS_LAPS voltas, da mais recente para a mais antiga.
        Com várias, `build_consensus_corner_map` mantém só as curvas que a
        maioria confirma, e um corte isolado deixa de ter voto suficiente.
        """
        length = float(getattr(self._last_state, "track_length", 0.0) or 0.0)
        sm = self.session_manager

        def usable(telem):
            return (telem and len(telem.get("distance", [])) >= 50
                    and (telem.get("g_lat") or len(telem.get("car_x", [])) >= 50)
                    and ca.lap_coverage(telem, length) >= 0.95)

        traces = []
        vistas = set()

        def add(telem):
            """
            Junta a volta, se ela servir e ainda não estiver na lista.

            A identidade é uma assinatura barata (quantos pontos, e o tempo do
            último), não o conteúdo: comparar dois dicionários de sete mil
            amostras canal a canal para descobrir que são a mesma volta é
            trabalho à toa — e é o caso COMUM, porque a melhor volta da sessão
            costuma ser também o recorde pessoal.
            """
            if not usable(telem):
                return len(traces) >= ca.CONSENSUS_LAPS
            times = telem.get("times") or []
            assinatura = (len(times), round(times[-1], 3) if times else 0.0)
            if assinatura not in vistas:
                vistas.add(assinatura)
                traces.append(telem)
            return len(traces) >= ca.CONSENSUS_LAPS

        # Os dois ghosts já estão na memória e são, por construção, voltas
        # limpas e inteiras (ver SessionManager.is_reference_material).
        for ghost in (sm.session_best_lap_ghost, sm.best_lap_ghost):
            if add(ghost.get("telemetry", {})):
                return traces

        # Depois o catálogo, da volta mais recente para a mais antiga. O índice
        # diz quais voltas são inteiras, limpas e que canais elas têm, então só
        # as candidatas de verdade saem do disco.
        for record in sm.completed_laps[::-1]:
            rec = record.get("record")
            if rec is None or not rec.full_lap or rec.points < 50:
                continue
            if not rec.valid or rec.pit_lap:
                continue                        # cortou a pista, ou passou no box
            if not ("g_lat" in rec.channels or "car_x" in rec.channels):
                continue
            if add(sm.telemetry_for(record)):
                break
        return traces

    def _refresh_corner_map(self):
        """
        Resolve qual mapeamento de curvas usar para a pista atual.

        Ordem: `track_maps/<pista>.json` (manual) → `<pista>.auto.json` →
        detecção por Força G em cima da melhor volta disponível (que é então
        gravada como .auto.json, para a numeração das curvas não mudar a cada
        volta e para você poder editá-la e promovê-la a manual).
        """
        state = self._last_state
        track = getattr(state, "track_name", "") if state else ""
        length = float(getattr(state, "track_length", 0.0) or 0.0) if state else 0.0

        cmap = ca.load_corner_map(track, length)

        # Detecta quando não há mapa nenhum, e TAMBÉM quando o mapa automático
        # que existe saiu de uma volta incompleta — nesse caso ele conhece só as
        # curvas de um pedaço da pista e precisa ser refeito.
        if cmap is None or cmap.is_provisional:
            traces = self._corner_reference_traces()
            # Só vale refazer se houver MAIS voltas limpas do que as que
            # geraram o mapa atual. Sem esta guarda, um mapa provisório de
            # duas voltas seria redetectado no fim de toda volta cortada, sem
            # nunca mudar de resultado.
            se_paga = (cmap is None
                       or cmap.detector_version < ca.DETECTOR_VERSION
                       or cmap.coverage < 0.95
                       or len(traces) > cmap.laps_used)
            if traces and se_paga:
                fresh = ca.build_consensus_corner_map(track, traces, length)
                if fresh and fresh.corners:
                    path = ca.save_corner_map(fresh, auto=True)
                    voltas = (f"{fresh.laps_used} volta(s) limpa(s)")
                    if cmap is None:
                        motivo = f"detectadas automaticamente de {voltas}"
                    elif cmap.detector_version < ca.DETECTOR_VERSION:
                        motivo = ("refeitas (o mapa anterior era de uma versao "
                                  f"antiga do detector) de {voltas}")
                    elif cmap.coverage < 0.95:
                        motivo = ("refeitas (o mapa anterior saiu de uma volta "
                                  f"incompleta) de {voltas}")
                    else:
                        motivo = f"refeitas com {voltas}"
                    # ASCII de propósito: no console do Windows (cp1252) um
                    # caractere como "→" levanta UnicodeEncodeError, e este
                    # print roda dentro da atualização da telemetria
                    print(f"[CornerAnalysis] {len(fresh.corners)} curvas {motivo} "
                          f"para '{track}'"
                          + (f" -> {os.path.basename(path)}" if path else ""))
                    cmap = fresh
            elif cmap is not None and not traces:
                print(f"[CornerAnalysis] Mapa automático de '{track}' saiu de "
                      f"{cmap.laps_used} volta(s) e cobre "
                      f"{cmap.coverage * 100:.0f}% da pista; será refeito "
                      f"quando houver volta limpa e completa gravada.")

        self._corner_map = cmap
        self._corners = list(cmap.corners) if cmap else []
        # Os limites da curva são posições relativas (0..1). Converter para
        # metros precisa da MESMA escala que gerou o eixo de distância da volta
        # (distance = track_position * track_length do provider). Usar o
        # track_length gravado no arquivo do mapa desalinhava as faixas quando
        # os dois valores não batiam.
        self._corner_track_length = length or (cmap.track_length if cmap else 0.0)

        # A contagem de curvas (e o aviso de mapa parcial) vai no rótulo da aba
        if self._corners:
            titulo = f"CURVAS ({len(self._corners)})"
            if cmap and cmap.is_provisional:
                # "PARCIAL" avisa que o mapa ainda vai mudar: ele já serve,
                # mas saiu de menos voltas limpas do que o consenso pede.
                titulo += " PARCIAL"
        else:
            titulo = "CURVAS"
        self.analysis_tabs.setTabText(0, titulo)
        self.analysis_tabs.setTabToolTip(
            0, "Análise curva a curva: tempo no trecho e delta vs referência")

    def _corner_analysis_lap(self):
        """
        Volta que a tabela analisa: a selecionada no seletor de voltas ou,
        no modo ao vivo, a última volta concluída.

        Analisar a volta em andamento a 60 Hz custaria caro e mostraria curvas
        pela metade; a leitura útil é sempre da volta que acabou de fechar.
        """
        completed = self.session_manager.completed_laps
        if not completed:
            return None, "", {}
        lap_i = None
        if hasattr(self, "lap_selector"):
            lap_i = self._combo_lap_index(self.lap_selector.combo.currentIndex())
        if lap_i is not None and lap_i < len(completed):
            lap = completed[lap_i]
        else:
            # No modo ao vivo, a última volta INTEIRA. Uma volta parcial (app
            # aberto no meio dela) daria uma tabela toda "--", porque a maioria
            # das curvas não tem telemetria.
            lap = completed[-1]
            for candidate in reversed(completed):
                if candidate.get("metadata", {}).get("full_lap") is not False:
                    lap = candidate
                    break
        return (self.session_manager.telemetry_for(lap),
                lap.get("lap_time_str", ""), lap.get("metadata", {}))

    def _seed_coach_from_history(self):
        """
        Dá ao coach o que ele já aprendeu nesta pista, com este carro.

        Roda uma vez por combinação (pista, carro, mapa de curvas). Lê o
        arquivo de melhores passagens e, se ele estiver vazio ou desatualizado,
        varre as voltas mais rápidas do catálogo para preenchê-lo — o que
        custa ~110 ms para cinco voltas e acontece uma vez só, não a cada
        sessão.
        """
        track, car = self.session_manager.track_car
        if not track or not self._corners:
            return
        fonte = self._corner_map.source if self._corner_map else ""
        assinatura = map_signature(self._corners, fonte)
        if self._coach_seed_signature == (track, car, assinatura):
            return
        self._coach_seed_signature = (track, car, assinatura)

        bests, vistas = self.corner_bests.load(track, car, assinatura)
        bests, vistas, lidas = self.corner_bests.bootstrap(
            track, car, self._corners,
            self._corner_track_length or getattr(self._last_state, "track_length", 0.0),
            known=bests, seen_laps=vistas)
        if lidas:
            self.corner_bests.save(track, car, assinatura, bests, vistas)
        self._corner_best_cache = (bests, vistas)

        self.coach.set_track(self._corners,
                             self._corner_track_length
                             or getattr(self._last_state, "track_length", 0.0))
        n = self.coach.load_bests(bests)
        if n:
            print(f"[Coach] {n} curva(s) com melhor passagem conhecida"
                  + (f" ({lidas} volta(s) do catálogo analisadas)" if lidas else "")
                  + ": o coach já sabe onde você perde.")

    def _persist_corner_bests(self):
        """Grava as passagens que melhoraram nesta volta."""
        if not self.coach.bests_dirty:
            return
        track, car = self.session_manager.track_car
        if not track or not self._coach_seed_signature:
            return
        assinatura = self._coach_seed_signature[2]
        bests, vistas = self._corner_best_cache
        atuais = self.coach.export_bests()
        for index, best in atuais.items():
            anterior = bests.get(index)
            if anterior is None or best.section_s < anterior.section_s:
                bests[index] = best
        entry = (self.session_manager.completed_laps[-1]
                 if self.session_manager.completed_laps else {})
        record = entry.get("record")
        if record is not None and record.lap_id not in vistas:
            vistas.append(record.lap_id)
        self._corner_best_cache = (bests, vistas)
        self.corner_bests.save(track, car, assinatura, bests, vistas)
        self.coach.mark_bests_saved()

    def _update_corner_analysis(self):
        """
        Recalcula a tabela curva a curva e as faixas nos gráficos.

        Blindado: esta análise é auxiliar e roda a partir do mesmo slot que
        atualiza o dashboard ao vivo. Uma falha aqui não pode levar o resto da
        tela com ela — o erro aparece uma vez no console e a análise para.
        """
        try:
            self._do_update_corner_analysis()
        except Exception:
            if not getattr(self, "_corner_analysis_failed", False):
                self._corner_analysis_failed = True
                print("[CornerAnalysis] Falha ao atualizar a análise curva a curva; "
                      "o dashboard continua normalmente:")
                traceback.print_exc()

    def _do_update_corner_analysis(self):
        # Um mapa provisório (detectado em volta incompleta) é reavaliado a cada
        # volta nova, até aparecer uma volta inteira para refazê-lo
        if not self._corners or (self._corner_map and self._corner_map.is_provisional):
            self._refresh_corner_map()
        if not self._corners:
            self.corner_analysis_table.setRowCount(0)
            self._hide_corner_regions()
            return

        lap_telemetry, lap_time_str, _ = self._corner_analysis_lap()
        if not lap_telemetry:
            self.corner_analysis_table.setRowCount(0)
            self._hide_corner_regions()
            return

        ref_telemetry = self.reference_ghost().get("telemetry", {})

        length = self._corner_track_length or max(lap_telemetry.get("distance", [0.0]) or [0.0])
        self._corner_comparisons = ca.compare_laps(
            lap_telemetry, ref_telemetry, self._corners, length)
        self.corner_analysis_table.update_corners(self._corner_comparisons)
        # A TABELA analisa a volta fechada; as FAIXAS têm de cair sobre a volta
        # que está no gráfico, que ao vivo é outra.
        self._update_corner_regions(self._plotted_lap_telemetry(), length)
        self._label_corner_tab(lap_time_str)

    def _label_corner_tab(self, lap_time_str: str = ""):
        """
        Diz na aba QUAL volta a tabela está analisando.

        Ao vivo os dois números na tela vêm de voltas diferentes: as faixas
        sombreiam a volta em andamento (é ela que está desenhada), e a tabela
        mede a última volta fechada — não dá para cronometrar uma curva que o
        carro ainda não terminou. Sem dizer isso, o piloto lê o delta da
        tabela como se fosse da curva que está sombreada no gráfico.
        """
        if not self._corners:
            return
        titulo = f"CURVAS ({len(self._corners)})"
        if self._corner_map and self._corner_map.is_provisional:
            titulo += " PARCIAL"
        if lap_time_str:
            titulo += f" · {lap_time_str}"
        self.analysis_tabs.setTabText(0, titulo)
        linhas = ["Análise curva a curva: tempo no trecho e delta vs referência"]
        if lap_time_str:
            linhas.append(f"A tabela mede a volta {lap_time_str}.")
        if self.is_live:
            linhas.append("As faixas nos gráficos acompanham a volta EM "
                          "ANDAMENTO, que é a desenhada.")
        self.analysis_tabs.setTabToolTip(0, "\n".join(linhas))

    # --- Faixas sombreadas das curvas sobre os gráficos --------------------

    def _plotted_lap_telemetry(self) -> dict:
        """
        A volta que está DESENHADA nos gráficos agora.

        Não é a mesma coisa que a volta ANALISADA. O eixo X dos gráficos é
        tempo, e os limites de curva são distância: a conversão de um para o
        outro só vale para a volta de onde o mapa de tempo saiu.

        Ao vivo, o gráfico mostra a volta EM ANDAMENTO, enquanto a tabela
        analisa a última volta fechada. Usar o mapa de tempo da volta fechada
        para posicionar as faixas sobre a volta em andamento desalinha tudo, e
        o erro CRESCE ao longo da volta: com 1,3 s de diferença entre as duas,
        a faixa da penúltima curva cai 1,1 s fora do lugar; se a volta anterior
        foi de saída de box, são dezenas de segundos.
        """
        if self.is_live:
            return self.session_manager.current_lap_data or {}
        telemetry, _, _ = self._corner_analysis_lap()
        return telemetry

    def _corner_plots(self):
        return (self.plot_delta, self.plot_speed, self.plot_pedals, self.plot_steer)

    def _ensure_corner_regions(self, count: int):
        """Cria (e reaproveita) as faixas: uma por curva em cada gráfico."""
        brush = pg.mkBrush(QColor(77, 163, 255, 22))
        pen = pg.mkPen(None)
        while len(self._corner_regions) < count:
            regions = []
            for plot in self._corner_plots():
                region = pg.LinearRegionItem(values=(0.0, 0.0), movable=False,
                                             brush=brush, pen=pen)
                for line in region.lines:
                    line.setPen(pg.mkPen(None))
                    line.setHoverPen(pg.mkPen(None))
                region.setZValue(-100)   # atrás das curvas de telemetria
                region.setVisible(False)
                plot.addItem(region)
                regions.append(region)
            label = pg.TextItem("", color=T.TXT_LABEL, anchor=(0.5, 1.0))
            label.setVisible(False)
            self.plot_speed.addItem(label)
            self._corner_regions.append((regions, label))

    def _hide_corner_regions(self):
        self._corner_region_spans = {}
        for regions, label in self._corner_regions:
            for region in regions:
                region.setVisible(False)
            label.setVisible(False)

    def _update_corner_regions(self, lap_telemetry: dict, track_length: float):
        """
        Posiciona as faixas no eixo X dos gráficos, que é TEMPO.

        Os limites da curva são definidos em distância, então cada limite é
        convertido para o instante em que a volta analisada passou por ele.
        """
        if not self.show_corner_regions:
            self._hide_corner_regions()
            return

        distances = lap_telemetry.get("distance", [])
        times = lap_telemetry.get("times", [])
        if len(distances) < 2 or len(times) < 2:
            self._hide_corner_regions()
            return

        self._ensure_corner_regions(len(self._corners))
        # Número da curva no alto do gráfico de velocidade, onde não briga
        # com a curva de dados
        label_y = self._speed_y_max * 0.98

        # Onde cada faixa já está, para não repintar o que não mudou. Ao vivo
        # esta função roda a ~12 Hz, e mover quatro faixas por curva a cada
        # quadro repinta os quatro gráficos à toa — as curvas já percorridas
        # têm instante FIXO (o tempo em que se passou por uma distância não
        # muda depois), então quase nada precisa mesmo ser mexido.
        anterior = getattr(self, "_corner_region_spans", {})
        atual = {}

        for i, corner in enumerate(self._corners):
            regions, label = self._corner_regions[i]
            t0 = ca.time_at_distance(lap_telemetry, corner.start_m(track_length))
            t1 = ca.time_at_distance(lap_telemetry, corner.end_m(track_length))
            if t0 is None or t1 is None or t1 <= t0:
                # Curva que a volta desenhada ainda não alcançou: sem faixa.
                # Ao vivo é o comportamento certo — a faixa aparece conforme o
                # carro passa por ela, sempre alinhada com o que está no
                # gráfico, em vez de sombrear um trecho que ainda não existe.
                if anterior.get(corner.index) is not None:
                    for region in regions:
                        region.setVisible(False)
                    label.setVisible(False)
                continue
            atual[corner.index] = (t0, t1)
            if anterior.get(corner.index) == (t0, t1):
                continue                        # já está no lugar
            for region in regions:
                region.setRegion((t0, t1))
                region.setVisible(True)
            label.setText(str(corner.index))
            label.setPos((t0 + t1) / 2.0, label_y)
            label.setVisible(True)
        self._corner_region_spans = atual

        # Sobras do mapa anterior (pista com menos curvas) ficam escondidas
        for regions, label in self._corner_regions[len(self._corners):]:
            for region in regions:
                region.setVisible(False)
            label.setVisible(False)

    def on_corner_regions_toggled(self, checked: bool):
        self.show_corner_regions = checked
        if not checked:
            self._hide_corner_regions()
        else:
            self._update_corner_analysis()

    # -----------------------------------------------------------------------
    # Engenheiro de pista
    # -----------------------------------------------------------------------

    #: Modo do engenheiro <-> chave gravada no config.json. O índice do combo
    #: não vai para o disco: mudar a ordem dos itens trocaria a preferência de
    #: todo mundo em silêncio.
    _ENGINEER_MODE_KEYS = {EngineerPanel.MODE_LAP: "lap",
                           EngineerPanel.MODE_LIVE: "live",
                           EngineerPanel.MODE_MANUAL: "manual"}

    def on_engineer_voice_toggled(self, checked: bool):
        self.voice.enabled = checked
        if not checked:
            self.voice.clear()   # cala o que ainda não foi falado
        self.config.set("voice_enabled", bool(checked))

    def on_engineer_mode_changed(self, idx: int):
        nomes = {EngineerPanel.MODE_LAP: "no fim de cada volta",
                 EngineerPanel.MODE_LIVE: "ao vivo, durante a volta",
                 EngineerPanel.MODE_MANUAL: "só quando você pedir"}
        print(f"[Engenheiro] Modo: {nomes.get(idx, '?')}")
        self.config.set("engineer_mode", self._ENGINEER_MODE_KEYS.get(idx, "lap"))

    def _restore_engineer_preferences(self):
        """Reaplica voz e modo do engenheiro salvos na sessão anterior."""
        voice_on = bool(self.config.get("voice_enabled"))
        self.voice.enabled = voice_on
        panel = self.engineer_panel
        panel.btn_voice.blockSignals(True)
        panel.btn_voice.setChecked(voice_on)
        panel.btn_voice.blockSignals(False)

        saved = self.config.engineer_mode()
        idx = next((i for i, key in self._ENGINEER_MODE_KEYS.items()
                    if key == saved), EngineerPanel.MODE_LAP)
        panel.combo_mode.blockSignals(True)
        panel.combo_mode.setCurrentIndex(idx)
        panel.combo_mode.blockSignals(False)

    def on_engineer_analyze_clicked(self):
        """Botão ANALISAR: roda o balanço da volta exibida, em qualquer modo."""
        self._engineer_lap_report(self._last_state, forced=True)

    def _engineer_emit(self, advices: list, header: str = "", speak_limit: int = 1):
        """
        Joga os recados no painel e fala os mais importantes.

        A lista do painel cresce por cima, então a inserção é de trás para
        frente: o recado mais importante acaba no topo, logo abaixo do
        cabeçalho da volta.
        """
        if not advices:
            return
        for advice in reversed(advices):
            self.engineer_panel.add_advice(advice)
        if header:
            self.engineer_panel.add_separator(header)

        if not self.engineer_panel.voice_on or speak_limit <= 0:
            return
        now = self._engineer_clock
        # O intervalo mínimo vale entre BLOCOS: as frases de um mesmo bloco vão
        # para a fila e são ditas em sequência
        if not self.engineer.should_speak(now):
            return
        falou = False
        for advice in self.engineer.pick_for_voice(advices, limit=speak_limit):
            # A mesa de som decide o volume DESTE assunto — e se ele deve ser
            # falado. Canal no zero devolve None: a voz cala, o painel de
            # texto acima continua mostrando o recado.
            volume = self.voice_mix.volume_for(advice.key, master=self.voice.volume)
            if volume is None:
                continue
            # `spoken`, não `text`: decimal com vírgula para a fala sair natural.
            # A severidade vira prioridade na fila de voz: um recado crítico
            # corta o balanço da volta em vez de esperar a vez.
            self.voice.say(advice.spoken,
                           priority=_VOICE_PRIORITY.get(advice.severity,
                                                        PRIORITY_NORMAL),
                           ttl=advice.ttl_s, volume=volume)
            falou = True
        # O tempo de espera entre falas só corre quando algo foi REALMENTE
        # dito: um bloco inteiro silenciado pela mesa não pode calar o
        # engenheiro pelos próximos segundos.
        if falou:
            self.engineer.mark_spoken(now)

    def _reference_lap_time_str(self) -> str:
        return self.reference_ghost().get("metadata", {}).get("lap_time_str", "") or ""

    def _engineer_lap_report(self, state, forced: bool = False):
        """
        Balanço da volta: onde perdeu tempo, por quê e o que tentar.

        `forced=True` vem do botão ANALISAR e ignora o modo selecionado.
        """
        if state is None:
            return
        if not forced and self.engineer_panel.mode != EngineerPanel.MODE_LAP:
            return

        telemetry, lap_time_str, metadata = self._corner_analysis_lap()
        if not telemetry:
            return

        lap_ms = self._parse_time_ms(lap_time_str)
        ref_ms = self._parse_time_ms(self._reference_lap_time_str())
        lap_delta = ((lap_ms - ref_ms) / 1000.0
                     if lap_ms > 0 and ref_ms > 0 else None)

        # A volta de referência entra inteira: é dela que saem as comparações de
        # marcha no ápice, velocidade de saída e traçado.
        ref_ghost = self.reference_ghost()

        advices = self.engineer.analyze_lap(
            self._corner_comparisons, lap_telemetry=telemetry, state=state,
            lap_time_str=lap_time_str, lap_delta_s=lap_delta,
            ref_telemetry=ref_ghost.get("telemetry", {}),
            sector_times_ms=metadata.get("sector_times_ms"),
            ref_sector_times_ms=ref_ghost.get("metadata", {}).get("sector_times_ms"))

        # O resumo do coach fecha o balanço: quanto tempo ainda há na mesa e
        # em que curvas ele está. Vem por último de propósito — é o recado que
        # o piloto leva para a volta seguinte.
        resumo = self.coach.lap_summary()
        if resumo is not None:
            advices = list(advices) + [resumo]

        if not advices:
            return
        header = f"Volta {lap_time_str}" if lap_time_str else "Volta"
        # No fim de volta o piloto está na reta: dá para ouvir dois recados
        self._engineer_emit(advices, header=header, speak_limit=2)

    def _engineer_on_lap_completed(self, state):
        """Chamado uma vez por volta concluída."""
        # O coach aprende com a MESMA comparação que alimenta a tabela de
        # curvas — mas nunca com uma volta de box ou com corte de pista.
        entry = (self.session_manager.completed_laps[-1]
                 if self.session_manager.completed_laps else {})
        self._seed_coach_from_history()
        self.coach.on_lap_completed(self._corner_comparisons,
                                    pit_lap=bool(entry.get("pit_lap")),
                                    valid=bool(entry.get("valid", True)))
        self._persist_corner_bests()

        if entry.get("pit_lap"):
            # Balanço de uma volta de saída/retorno não diz nada: ela é vinte
            # segundos mais lenta por definição.
            return

        self._engineer_lap_report(state)

        # Ritmo e consumo só existem na comparação entre voltas: é aqui que a
        # volta que fechou entra na conta.
        _, lap_time_str, _ = self._corner_analysis_lap()
        entre_voltas = [
            self.engineer.register_lap_time(self._parse_time_ms(lap_time_str)),
            self.engineer.register_fuel(getattr(state, "fuel", 0.0)),
        ]
        avisos = [a for a in entre_voltas if a is not None]
        if avisos and self.engineer_panel.mode != EngineerPanel.MODE_MANUAL:
            self._engineer_emit(avisos, speak_limit=0)

    def _engineer_live_tick(self, state):
        """
        Avisos com o carro na pista, quando o modo é 'Ao vivo'.

        Duas fontes: o engenheiro (bandeira, pneu, delta, combustível) e o
        coach de curva (dica antes da curva, veredito na saída). O coach entra
        depois porque o que ele diz é conselho — bandeira preta vem primeiro.
        """
        if self.engineer_panel.mode != EngineerPanel.MODE_LIVE:
            return
        advices = self.engineer.analyze_live(state, self._engineer_clock)

        self.coach.set_track(self._corners, self._corner_track_length
                             or getattr(state, "track_length", 0.0))
        self.coach.set_reference(self.reference_ghost().get("telemetry", {}))
        advices += self.coach.update(state, self.session_manager.current_lap_data,
                                     self._engineer_clock)
        if advices:
            self._engineer_emit(advices, speak_limit=1)

    # -----------------------------------------------------------------------
    # Main telemetry update slot
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Referência (volta fantasma)
    # -----------------------------------------------------------------------

    def reference_ghost(self) -> dict:
        """
        Ghost da referência selecionada. Ponto único de verdade.

        Antes cada trecho da janela chamava `_reference_ghost_for_index()` com
        o índice cru do combo (0..3) — nove lugares dependendo da ORDEM dos
        itens de um menu. Agora o combo diz o que ele é (`kind`, `lap_id`) e a
        resolução acontece só aqui.

        O resultado é memorizado por quadro: a 60 Hz, uma volta do catálogo
        seria procurada no índice dezenas de vezes por segundo à toa.
        """
        kind, lap_id = self.ghost_selector.selection()
        cache_key = (kind, lap_id, self.session_manager.ghosts_revision)
        if getattr(self, "_ref_cache_key", None) == cache_key:
            return self._ref_cache_value

        ghost = self._resolve_reference(kind, lap_id)
        self._ref_cache_key = cache_key
        self._ref_cache_value = ghost
        return ghost

    def _resolve_reference(self, kind: str, lap_id: str) -> dict:
        sm = self.session_manager
        G = GhostSelectorCard

        if kind == G.REF_NONE:
            # "Nenhuma" agora desativa de verdade. Antes ela só escondia as
            # curvas e continuava medindo o delta contra o session best — um
            # número que o piloto não tinha pedido e não sabia de onde vinha.
            return sm._empty_ghost()
        if kind == G.REF_PB:
            return sm.best_lap_ghost
        if kind == G.REF_SESSION:
            return sm.session_best_lap_ghost
        if kind == G.REF_IDEAL:
            return sm.ideal_lap_ghost
        if kind == G.REF_LAP and lap_id:
            ghost = sm.ghost_for_lap_id(lap_id)
            if ghost.get("telemetry", {}).get("times"):
                return ghost
            # A volta escolhida sumiu do disco: melhor cair na automática do
            # que deixar o piloto sem delta nenhum e sem saber por quê.
            print(f"[Referência] Volta {lap_id} não está mais disponível: "
                  "voltando para a referência automática.")
            self.ghost_selector.set_selection(G.REF_AUTO)

        return self.best_available_reference()

    def best_available_reference(self) -> dict:
        """
        A referência da opção "Automática".

        Em treino e classificação, a MAIS RÁPIDA que existir. Antes esta
        função devolvia a melhor volta da sessão sempre que houvesse uma,
        mesmo com o Personal Best mais rápido — e num dia ruim isso fazia o
        piloto perseguir o próprio ritmo ruim, com o delta verde enquanto ele
        andava segundos abaixo do que já tinha feito ali.

        NA CORRIDA é diferente, e de propósito: vale a melhor volta DESTA
        sessão. Perseguir a volta de classificação com o tanque cheio e pneu
        usado é perseguir um tempo que o carro não tem hoje — o delta ficaria
        em "+2 segundos" a corrida inteira e não diria nada sobre a volta que
        acabou de ser feita.
        """
        sm = self.session_manager
        session_ghost = sm.session_best_lap_ghost
        tem_sessao = bool(session_ghost.get("telemetry", {}).get("times"))

        state = self._last_state
        em_corrida = state is not None and RaceEngineer._is_race(state)
        if em_corrida and tem_sessao:
            return session_ghost

        candidatos = []
        for ghost in (session_ghost, sm.best_lap_ghost):
            if not ghost.get("telemetry", {}).get("times"):
                continue
            ms = self._parse_time_ms(
                ghost.get("metadata", {}).get("lap_time_str", "") or "")
            candidatos.append((ms if ms > 0 else 9999999, ghost))
        if not candidatos:
            return sm._empty_ghost()
        return min(candidatos, key=lambda item: item[0])[1]

    def reference_shows_ghost(self) -> bool:
        """As curvas fantasma devem aparecer nos gráficos e no mapa?"""
        return self.ghost_selector.shows_ghost()

    def refresh_reference_choices(self):
        """
        Refaz a lista do seletor com as voltas que existem agora.

        Chamado quando uma volta fecha e ao trocar de pista/carro. As voltas
        desta sessão vêm primeiro; abaixo, as gravadas em outros dias.
        """
        sm = self.session_manager
        session_ids = {e.get("record").lap_id for e in sm.completed_laps
                       if e.get("record") is not None}
        saved = [r for r in sm.saved_laps(only_reference_material=True)
                 if r.lap_id not in session_ids]

        combo = self.ghost_selector.combo
        combo.blockSignals(True)
        self.ghost_selector.populate(
            session_laps=list(reversed(sm.completed_laps)),
            saved_laps=saved)

        # A referência salva na sessão anterior pode ser uma volta específica,
        # que só existe na lista depois que o catálogo desta pista/carro é
        # lido. Tenta reaplicá-la até conseguir; se a volta não existir mais
        # (retenção, arquivo apagado, outra pista), desiste e fica no que está.
        if self._pending_reference is not None:
            kind, lap_id = self._pending_reference
            if self.ghost_selector.set_selection(kind, lap_id):
                self._pending_reference = None
            elif kind != GhostSelectorCard.REF_LAP:
                self._pending_reference = None
        combo.blockSignals(False)

        self._ref_cache_key = None

    def on_telemetry_update(self, state: TelemetryState):
        if not state.is_connected:
            return

        self._last_state = state
        # Relógio do engenheiro: base dos tempos de espera entre recados
        self._engineer_clock = time.monotonic()

        # 1. PROCESS STATE FIRST! This calculates Live Delta and Sectors,
        #    using whichever reference lap is currently selected in the sidebar.
        reference_ghost = self.reference_ghost()
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
            wind_dir=state.wind_direction,
            rain=getattr(state, 'rain_intensity', ''),
            grip_status=getattr(state, 'track_grip_status', ''),
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
        if (AUTO_EXPORT_ON_BEST_LAP and self.auto_export_on_best
                and best_time_str != "--:--.---"
                and best_time_str != self._last_exported_best):
            self._last_exported_best = best_time_str
            completed_lap_number = max(1, state.lap_number - 1)
            self.export_analysis_image(auto=True, lap_number=completed_lap_number, lap_time_str=best_time_str)

        # Escala dinâmica do eixo X dos gráficos, baseada na melhor volta / comprimento da pista
        self._update_graph_scale(state, best_time_str)

        # Tempo da referência: sai SEMPRE dos metadados do próprio ghost, para
        # ficar coerente com a telemetria que o delta está usando. Um único
        # `reference_ghost()` cobre os cinco modos e qualquer volta do catálogo
        # — antes eram quatro ramos dependendo da posição no combo.
        ref_lap_str = (reference_ghost.get("metadata", {}).get("lap_time_str", "")
                       or "--:--.---")
        has_valid_reference = self._parse_time_ms(ref_lap_str) > 0

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
            self.setWindowTitle(f"ApexView — {state.track_name} | {state.car_name}")
            loaded = self.session_manager.auto_load_ghosts(state)
            # O catálogo desta pista/carro acabou de ser lido: agora as voltas
            # gravadas em outros dias podem entrar no seletor de referência —
            # e a referência salva no config.json pode ser reaplicada.
            self.refresh_reference_choices()
            if loaded:
                self.on_ghost_mode_changed()
            self._update_best_map_base_trace()
            self.update_lap_selector_items()
            # Pista nova: recarrega/redetecta o mapeamento de curvas
            self._refresh_corner_map()
            self._seed_coach_from_history()
            self._update_corner_analysis()

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
        self._graph_frame_skip = ((getattr(self, "_graph_frame_skip", 0) + 1)
                                  % self.graph_every_n_frames)
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
                live_delta = self._calc_lap_delta(curr, self.reference_ghost())

                self.curve_delta.setData(curr["times"], live_delta)
                self.curve_speed.setData(curr["times"], curr["speed"])
                self.curve_gas.setData(curr["times"], gas_100)
                self.curve_gas_tc.setData(curr["times"], gas_tc_100)
                self.curve_brake.setData(curr["times"], brake_100)
                self.curve_brake_abs.setData(curr["times"], brake_abs_100)
                self.curve_steer.setData(curr["times"], curr.get("steer", []))

                # As faixas de curva acompanham a volta em andamento. Sem
                # isto elas ficavam onde a volta ANTERIOR as havia deixado,
                # e o piloto lia "curva 7" sobre o trecho errado do gráfico.
                if self._corners and self._corner_track_length:
                    self._update_corner_regions(curr, self._corner_track_length)

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
            
        # --- Engenheiro ao vivo -------------------------------------------
        # A 60 Hz não faz sentido: os avisos têm tempo de espera de segundos.
        # ~4 Hz já pega qualquer travamento de roda ou corte de TC.
        self._engineer_frame_skip = (getattr(self, "_engineer_frame_skip", 0) + 1) % 15
        if self._engineer_frame_skip == 0:
            try:
                self._engineer_live_tick(state)
            except Exception:
                if not getattr(self, "_engineer_failed", False):
                    self._engineer_failed = True
                    print("[Engenheiro] Falha na análise ao vivo; "
                          "o dashboard continua normalmente:")
                    traceback.print_exc()

        # --- Sessão nova dentro do mesmo fim de semana ---------------------
        # Treino 1 -> Treino 2 -> classificação -> corrida, sem fechar o jogo.
        # O SessionManager já separou as voltas no catálogo; aqui a TELA
        # precisa acompanhar, senão o histórico continua mostrando a sessão
        # anterior e a tabela de voltas nunca mais bate com a realidade.
        if self.session_manager.session_index != self._session_index_seen:
            self._session_index_seen = self.session_manager.session_index
            self._on_new_session(state)

        # --- Update Lap History dynamically by Lap ID ---
        self._update_live_lap_history(state)
        
    def _on_new_session(self, state: TelemetryState):
        """
        Zera o que é DA SESSÃO e preserva o que é do fim de semana.

        Some: histórico de voltas da tela, seletor de voltas, conselhos do
        engenheiro, curvas aprendidas pelo coach, ritmo e consumo.
        Fica: catálogo em disco, Personal Best, volta ideal e o mapa de curvas
        da pista — nada disso muda porque começou a classificação.
        """
        tipo = getattr(state, "session_type", "") or ""
        print(f"[Dashboard] Sessão nova{f' ({tipo})' if tipo else ''}: "
              "histórico da tela zerado.")

        self.lap_history_table.setRowCount(0)
        self.lap_history_table._best_row = -1
        self._lap_row_map = {}
        self._last_historic_count = -1
        self._last_scrolled_lap = -1
        self._session_max_speed = 0.0
        self._last_exported_best = ""

        self.engineer.reset()
        # O coach recomeça a observar o piloto de hoje, mas NÃO esquece as
        # melhores passagens: elas são do conjunto pista/carro e valem para o
        # fim de semana inteiro. É a diferença entre chegar no Treino 2
        # sabendo onde você perde e gastar duas voltas redescobrindo.
        self.coach.reset()
        self._coach_seed_signature = None
        self._seed_coach_from_history()
        self._corner_comparisons = []
        self.corner_analysis_table.setRowCount(0)

        self.engineer_panel.clear_messages()
        self.engineer_panel.add_separator(
            f"— {tipo or 'Nova sessão'} —")

        self.set_live_mode()
        self.update_lap_selector_items()
        self.refresh_reference_choices()

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
        # A comparação é por DESIGUALDADE, não por "cresceu": numa sessão nova
        # a lista é zerada, e a tabela da tela precisa acompanhar a queda.
        historic_count = len(self.session_manager.historic_laps)
        if getattr(self, '_last_historic_count', -1) != historic_count:
            self._last_historic_count = historic_count
            self.update_lap_selector_items()
            # A volta que acabou de fechar já pode ser escolhida como referência
            self.refresh_reference_choices()
            self._update_best_map_base_trace()
            # Volta fechada: é o momento de reavaliar as curvas
            self._update_corner_analysis()
            # ...e de o engenheiro dar o balanço dela (a tabela de curvas já
            # está recalculada, é dela que sai o "onde" e o "por quê")
            try:
                # Só quando uma volta REALMENTE fechou. `historic_laps` também
                # muda ao ser zerada numa sessão nova, e aí não há balanço a dar.
                if self.session_manager.laps_recorded != self._laps_recorded_seen:
                    self._laps_recorded_seen = self.session_manager.laps_recorded
                    self._engineer_on_lap_completed(state)
            except Exception:
                if not getattr(self, "_engineer_failed", False):
                    self._engineer_failed = True
                    print("[Engenheiro] Falha no balanço da volta; "
                          "o dashboard continua normalmente:")
                    traceback.print_exc()

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

                # Volta suja (corte de pista ou penalidade): fica marcada e
                # NÃO concorre a melhor volta. Ela continua na tabela de
                # propósito — o tempo aconteceu, você só não pode usá-lo.
                if not lap_data.get("valid", True):
                    num_item = self.lap_history_table.item(row_idx, 0)
                    if num_item is not None and "⚠" not in num_item.text():
                        num_item.setText(f"{lap_num} ⚠")
                        num_item.setForeground(QColor("#c98a00"))
                        num_item.setToolTip(
                            "Volta suja: cortou a pista ou tomou penalidade")
                    continue

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

        if candidates:
            candidates.sort(key=lambda item: item[0])
            best_cx, best_cz = candidates[0][1], candidates[0][2]
            self.sidebar_panel.track_map_card.map_widget.set_base_trace(best_cx, best_cz)
            return

        # 3. Nenhum dos dois ghosts serve: a volta mais rápida do catálogo que
        #    tenha traçado. O índice responde qual é sem abrir arquivo nenhum;
        #    só a vencedora sai do disco. Antes esta busca abria a telemetria
        #    de todas as voltas da sessão a cada troca de pista.
        sm = self.session_manager
        with_trace = [r for r in sm.saved_laps()
                      if "car_x" in r.channels and r.lap_time_ms > 30000]
        if not with_trace:
            return
        best = min(with_trace, key=lambda r: r.lap_time_ms)
        telem = sm.telemetry_for(best)
        cx, cz = telem.get("car_x", []), telem.get("car_z", [])
        if len(cx) >= 2:
            self.sidebar_panel.track_map_card.map_widget.set_base_trace(cx, cz)

    def _clear_ghost_curves(self):
        self.curve_ghost_speed.setData([], [])
        self.curve_ghost_gas.setData([], [])
        self.curve_ghost_brake.setData([], [])
        self.curve_ghost_steer.setData([], [])
        self.sidebar_panel.track_map_card.map_widget.set_data([], [], [], [])

    def on_ghost_mode_changed(self):
        """Redesenha tudo o que depende da referência e guarda a escolha."""
        self._ref_cache_key = None
        kind, lap_id = self.ghost_selector.selection()
        self.config.set_reference(kind, lap_id)
        # Escolha explícita do piloto vence a preferência que ainda esperava
        # ser reaplicada — senão o próximo refresh a desfaria.
        self._pending_reference = None

        ghost = (self.reference_ghost().get("telemetry", {})
                 if self.reference_shows_ghost() else {})

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
            self._clear_ghost_curves()

        if hasattr(self, 'lap_selector') and self.lap_selector.combo.currentIndex() > 0:
            self.on_selected_lap_changed(self.lap_selector.combo.currentIndex())

        # Os deltas por curva são medidos contra a referência selecionada
        self._update_corner_analysis()

    def closeEvent(self, event):
        print("Parando Thread de Telemetria...")
        self.engine.stop()
        self.voice.stop()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Recalcula as proporções dos gráficos no splitter (25% cada)
        if hasattr(self, 'plot_splitter'):
            total_h = self.plot_splitter.height()
            part = total_h // 4
            self.plot_splitter.setSizes([part, part, part, total_h - (part * 3)])
