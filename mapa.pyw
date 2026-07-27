import math
import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel, QHBoxLayout, QSplitter, QPushButton, QFrame, QGridLayout
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont, QColor
import pyqtgraph as pg

# --- Importações da Engine Unificada (Assetto Corsa) ---
from core.engine import TelemetryEngine
from providers.assettocorsa import AssettoCorsaTelemetryProvider
from providers.mock import MockTelemetryProvider
from core.models import TelemetryState

# --------------------------------------------------------------------------
# MOCK_MODE
# --------------------------------------------------------------------------
# True  -> usa o simulador interno de telemetria sem o jogo aberto
# False -> usa a Memória Compartilhada do Assetto Corsa 1 
#          (ou do mock_game.py se ele estiver rodando).
MOCK_MODE = False

# --- Cores Estilo MoTeC ---
BG_MAIN = "#141414"
BG_PLOT = "#000000"
COLOR_TEXT = "#dcdcdc"
COLOR_GAS = "#00e600"    # MoTeC green
COLOR_BRAKE = "#ff3333"  # MoTeC red
COLOR_COAST = "#ffb300"  # Yellow for coasting
COLOR_CURSOR = "#ffffff"
COLOR_GRID = "#333333"

class TrackMapWindow(QMainWindow):
    def __init__(self, engine: TelemetryEngine):
        super().__init__()
        self.setWindowTitle("MoTeC i2 Style Track Map")
        self.resize(1024, 768)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {COLOR_TEXT};")
        
        # Conecta o sinal da Engine à interface
        self.engine = engine
        self.engine.on_update.connect(self.on_telemetry_update)
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(5, 5, 5, 5)
        self.layout.setSpacing(5)
        self.is_analysis_mode = False
        
        # --- Top Bar (Toolbar) ---
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        
        # Info Panel for Cursor Values
        self.info_frame = QFrame()
        self.info_frame.setStyleSheet(f"background-color: {BG_PLOT}; border: 1px solid #333;")
        info_layout = QGridLayout(self.info_frame)
        info_layout.setContentsMargins(15, 8, 15, 8)
        info_layout.setHorizontalSpacing(30)
        
        font_lbl = QFont("Segoe UI", 9, QFont.Bold)
        font_val = QFont("Consolas", 11)
        
        # Setup labels in a grid
        self.lbl_dist = self._create_value_label("DIST", COLOR_TEXT, font_lbl, font_val, info_layout, 0)
        self.lbl_x = self._create_value_label("X POS", COLOR_TEXT, font_lbl, font_val, info_layout, 1)
        self.lbl_z = self._create_value_label("Z POS", COLOR_TEXT, font_lbl, font_val, info_layout, 2)
        self.lbl_gas = self._create_value_label("THROTTLE", COLOR_GAS, font_lbl, font_val, info_layout, 3)
        self.lbl_brake = self._create_value_label("BRAKE", COLOR_BRAKE, font_lbl, font_val, info_layout, 4)
        
        self.btn_mode = QPushButton("► LIVE")
        self.btn_mode.setFixedSize(140, 40)
        self.btn_mode.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.btn_mode.setStyleSheet("""
            QPushButton { background: #333; color: white; border: 1px solid #555; }
            QPushButton:hover { background: #444; }
        """)
        self.btn_mode.clicked.connect(self.toggle_mode)
        
        top_bar.addWidget(self.info_frame)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_mode)
        
        self.layout.addLayout(top_bar)
        
        # --- Splitter ---
        self.splitter = QSplitter(Qt.Vertical)
        self.layout.addWidget(self.splitter)
        
        # --- Track Map ---
        self.map_widget = pg.PlotWidget()
        self.map_widget.setBackground(BG_PLOT)
        self.map_widget.showGrid(x=False, y=False)
        self.map_widget.hideAxis('left')
        self.map_widget.hideAxis('bottom')
        self.map_widget.setAspectLocked(True)
        self.map_widget.setStyleSheet("border: 1px solid #333;")
        self.splitter.addWidget(self.map_widget)
        
        # --- Telemetry Graph ---
        self.telem_widget = pg.PlotWidget()
        self.telem_widget.setBackground(BG_PLOT)
        self.telem_widget.setStyleSheet("border: 1px solid #333;")
        
        # Configure Grid
        self.telem_widget.showGrid(x=True, y=True, alpha=0.3)
        self.telem_widget.getAxis('left').setPen(pg.mkPen(color=COLOR_GRID))
        self.telem_widget.getAxis('bottom').setPen(pg.mkPen(color=COLOR_GRID))
        self.telem_widget.getAxis('left').setTextPen(pg.mkPen(color=COLOR_TEXT))
        self.telem_widget.getAxis('bottom').setTextPen(pg.mkPen(color=COLOR_TEXT))
        
        self.telem_widget.setYRange(-0.05, 1.05)
        self.telem_widget.setLabel('left', 'Pedal Input', color=COLOR_TEXT)
        self.telem_widget.setLabel('bottom', 'Distance (m)', color=COLOR_TEXT)
        self.splitter.addWidget(self.telem_widget)
        
        self.splitter.setSizes([500, 300])
        
        # --- Map Elements ---
        self.track_curve = pg.ScatterPlotItem(size=3, pen=None)
        self.map_widget.addItem(self.track_curve)
        
        self.car_marker = pg.ScatterPlotItem(size=12, pen=pg.mkPen(COLOR_CURSOR, width=2), brush=pg.mkBrush(0, 0, 0, 0))
        self.map_widget.addItem(self.car_marker)
        
        # --- Telemetry Elements ---
        self.gas_curve = self.telem_widget.plot(pen=pg.mkPen(COLOR_GAS, width=1.5))
        self.brake_curve = self.telem_widget.plot(pen=pg.mkPen(COLOR_BRAKE, width=1.5))
        
        self.ghost_gas_curve = self.telem_widget.plot(pen=pg.mkPen(color=COLOR_GAS, style=Qt.DashLine, width=1))
        self.ghost_gas_curve.setOpacity(0.4)
        self.ghost_brake_curve = self.telem_widget.plot(pen=pg.mkPen(color=COLOR_BRAKE, style=Qt.DashLine, width=1))
        self.ghost_brake_curve.setOpacity(0.4)
        
        self.cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(COLOR_CURSOR, width=1))
        self.cursor_line.sigDragged.connect(self.on_cursor_dragged)
        self.telem_widget.addItem(self.cursor_line)
        
        # Data
        self.track_x = []
        self.track_z = []
        self.track_colors = []
        
        self.dist_array = []
        self.gas_array = []
        self.brake_array = []
        self.current_dist = 0.0
        
        self.last_recorded_x = None
        self.last_recorded_z = None

    def _create_value_label(self, title, color, font_lbl, font_val, layout, col):
        lbl_title = QLabel(title)
        lbl_title.setFont(font_lbl)
        lbl_title.setStyleSheet(f"color: #888; border: none;")
        
        lbl_val = QLabel("0.0")
        lbl_val.setFont(font_val)
        lbl_val.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")
        
        layout.addWidget(lbl_title, 0, col, Qt.AlignLeft | Qt.AlignBottom)
        layout.addWidget(lbl_val, 1, col, Qt.AlignLeft | Qt.AlignTop)
        
        return lbl_val

    def toggle_mode(self):
        self.is_analysis_mode = not self.is_analysis_mode
        self.cursor_line.setMovable(self.is_analysis_mode)
        
        if self.is_analysis_mode:
            self.btn_mode.setText("❚❚ ANALYSIS")
            self.btn_mode.setStyleSheet("""
                QPushButton { background: #660000; color: white; border: 1px solid #ff3333; }
                QPushButton:hover { background: #880000; }
            """)
        else:
            self.btn_mode.setText("► LIVE")
            self.btn_mode.setStyleSheet("""
                QPushButton { background: #333; color: white; border: 1px solid #555; }
                QPushButton:hover { background: #444; }
            """)
            
    def on_cursor_dragged(self):
        if not self.is_analysis_mode or not self.dist_array:
            return
            
        val = self.cursor_line.value()
        
        import bisect
        idx = bisect.bisect_left(self.dist_array, val)
        if idx >= len(self.dist_array):
            idx = len(self.dist_array) - 1
            
        x = self.track_x[idx]
        z = self.track_z[idx]
        gas = self.gas_array[idx]
        brake = self.brake_array[idx]
        
        self.car_marker.setData([x], [z])
        self.update_info_panel(val, x, z, gas, brake)

    def update_info_panel(self, dist, x, z, gas, brake):
        self.lbl_dist.setText(f"{dist:.1f} m")
        self.lbl_x.setText(f"{x:.1f}")
        self.lbl_z.setText(f"{z:.1f}")
        self.lbl_gas.setText(f"{gas*100:.0f} %")
        self.lbl_brake.setText(f"{brake*100:.0f} %")

    @pyqtSlot(TelemetryState)
    def on_telemetry_update(self, state: TelemetryState):
        if not state.is_connected:
            if not self.is_analysis_mode:
                self.lbl_dist.setText("WAITING...")
            return
            
        x = state.car_x
        z = state.car_z
        gas = state.gas
        brake = state.brake

        # Determina a cor do rastro baseada nos pedais (MoTeC style)
        if gas > 0.1:
            intensity = min(255, int(100 + gas * 155))
            color = pg.mkBrush(0, intensity, 0, 255)
        elif brake > 0.1:
            intensity = min(255, int(100 + brake * 155))
            color = pg.mkBrush(intensity, 0, 0, 255)
        else:
            color = pg.mkBrush(QColor(COLOR_COAST)) 
            
        if self.last_recorded_x is not None:
            dist_delta = math.hypot(x - self.last_recorded_x, z - self.last_recorded_z)
        else:
            dist_delta = 0.0
            
        if self.last_recorded_x is None or dist_delta > 1.0:
            self.current_dist += dist_delta
            
            self.track_x.append(x)
            self.track_z.append(z)
            self.track_colors.append(color)
            
            self.dist_array.append(self.current_dist)
            self.gas_array.append(gas)
            self.brake_array.append(brake)
            
            self.last_recorded_x = x
            self.last_recorded_z = z
            
            if len(self.track_x) > 6000:
                self.track_x = self.track_x[-6000:]
                self.track_z = self.track_z[-6000:]
                self.track_colors = self.track_colors[-6000:]
                self.dist_array = self.dist_array[-6000:]
                self.gas_array = self.gas_array[-6000:]
                self.brake_array = self.brake_array[-6000:]
                
            self.track_curve.setData(x=self.track_x, y=self.track_z, brush=self.track_colors)
            
            self.gas_curve.setData(x=self.dist_array, y=self.gas_array)
            self.brake_curve.setData(x=self.dist_array, y=self.brake_array)
            
            if len(self.dist_array) > 100:
                ghost_dist = self.dist_array[100:]
                ghost_gas = self.gas_array[:-100]
                ghost_brake = self.brake_array[:-100]
                self.ghost_gas_curve.setData(x=ghost_dist, y=ghost_gas)
                self.ghost_brake_curve.setData(x=ghost_dist, y=ghost_brake)
                
            if not self.is_analysis_mode:
                self.cursor_line.setValue(self.current_dist)
                self.car_marker.setData([x], [z])
                self.update_info_panel(self.current_dist, x, z, gas, brake)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    if MOCK_MODE:
        print("[*] MOCK_MODE ativo — usando simulador interno de telemetria.")
        provider = MockTelemetryProvider()
    else:
        print("[*] Aguardando o Assetto Corsa (ou mock_game.py via Shared Memory)...")
        provider = AssettoCorsaTelemetryProvider()

    # Inicia a engine de telemetria a 60 Hz
    engine = TelemetryEngine(provider=provider, hz=60)
    engine.start()

    window = TrackMapWindow(engine)
    window.show()
    
    exit_code = app.exec_()
    
    # Ao fechar a janela, para a engine
    engine.stop()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()