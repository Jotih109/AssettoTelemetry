import math
import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel, QHBoxLayout, QSplitter, QPushButton, QFrame, QGridLayout, QListWidget, QListWidgetItem
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont, QColor
import pyqtgraph as pg

from core.engine import TelemetryEngine
from providers.assettocorsa import AssettoCorsaTelemetryProvider
from providers.mock import MockTelemetryProvider
from core.models import TelemetryState
from core.storage import TelemetryStorageManager

MOCK_MODE = False

# --- Cores Estilo MoTeC ---
BG_MAIN = "#141414"
BG_PLOT = "#000000"
COLOR_TEXT = "#dcdcdc"
COLOR_GAS = "#00e600"
COLOR_BRAKE = "#ff3333"
COLOR_COAST = "#ffb300"
COLOR_CURSOR = "#ffffff"
COLOR_GRID = "#333333"
COLOR_GHOST = "#666666"

def parse_lap_time(time_str):
    if not time_str or time_str == "--:--.---": return 0.0
    try:
        parts = time_str.split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    except:
        return 0.0

class TrackMapWindow(QMainWindow):
    def __init__(self, engine: TelemetryEngine):
        super().__init__()
        self.setWindowTitle("MoTeC i2 Style Track Map & Telemetry")
        self.resize(1200, 800)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {COLOR_TEXT};")
        
        self.engine = engine
        self.engine.on_update.connect(self.on_telemetry_update)
        
        self.storage = TelemetryStorageManager()
        self.session_started = False
        self.current_lap_number = 0
        
        self.reset_lap_data()
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Layout Principal Horizontal (Esquerda: Sidebar | Direita: Map+Gráficos)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(5)
        
        # --- Sidebar (Esquerda) ---
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setFixedWidth(220)
        self.sidebar_frame.setStyleSheet(f"background-color: {BG_PLOT}; border: 1px solid #333;")
        sidebar_layout = QVBoxLayout(self.sidebar_frame)
        sidebar_layout.setContentsMargins(5, 5, 5, 5)
        
        lbl_sidebar = QLabel("SESSIONS & LAPS")
        lbl_sidebar.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl_sidebar.setStyleSheet("color: #888; border: none;")
        sidebar_layout.addWidget(lbl_sidebar)
        
        self.lap_list = QListWidget()
        self.lap_list.setStyleSheet("""
            QListWidget { background-color: #111; color: #ddd; border: none; }
            QListWidget::item:selected { background-color: #444; color: white; }
        """)
        self.lap_list.itemClicked.connect(self.on_lap_selected)
        sidebar_layout.addWidget(self.lap_list)
        
        self.btn_refresh_laps = QPushButton("↻ Refresh List")
        self.btn_refresh_laps.setStyleSheet("background: #333; color: white; padding: 5px;")
        self.btn_refresh_laps.clicked.connect(self.refresh_sessions_list)
        sidebar_layout.addWidget(self.btn_refresh_laps)
        
        self.main_layout.addWidget(self.sidebar_frame)
        
        # --- Área Principal (Direita) ---
        self.right_widget = QWidget()
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(5)
        
        self.is_analysis_mode = False
        
        # Top Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        
        self.info_frame = QFrame()
        self.info_frame.setStyleSheet(f"background-color: {BG_PLOT}; border: 1px solid #333;")
        info_layout = QGridLayout(self.info_frame)
        info_layout.setContentsMargins(15, 8, 15, 8)
        info_layout.setHorizontalSpacing(30)
        
        font_lbl = QFont("Segoe UI", 9, QFont.Bold)
        font_val = QFont("Consolas", 11)
        
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
        
        self.right_layout.addLayout(top_bar)
        
        # Splitter (Map + Graph)
        self.splitter = QSplitter(Qt.Vertical)
        self.right_layout.addWidget(self.splitter)
        
        # Track Map
        self.map_widget = pg.PlotWidget()
        self.map_widget.setBackground(BG_PLOT)
        self.map_widget.showGrid(x=False, y=False)
        self.map_widget.hideAxis('left')
        self.map_widget.hideAxis('bottom')
        self.map_widget.setAspectLocked(True)
        self.map_widget.setStyleSheet("border: 1px solid #333;")
        self.splitter.addWidget(self.map_widget)
        
        # Telemetry Graph
        self.telem_widget = pg.PlotWidget()
        self.telem_widget.setBackground(BG_PLOT)
        self.telem_widget.setStyleSheet("border: 1px solid #333;")
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
        
        # Visual Elements
        self.track_curve = pg.ScatterPlotItem(size=3, pen=None)
        self.map_widget.addItem(self.track_curve)
        
        self.car_marker = pg.ScatterPlotItem(size=12, pen=pg.mkPen(COLOR_CURSOR, width=2), brush=pg.mkBrush(0, 0, 0, 0))
        self.map_widget.addItem(self.car_marker)
        
        self.gas_curve = self.telem_widget.plot(pen=pg.mkPen(COLOR_GAS, width=1.5))
        self.brake_curve = self.telem_widget.plot(pen=pg.mkPen(COLOR_BRAKE, width=1.5))
        
        # Ghost curves (Reference Lap)
        self.ghost_gas_curve = self.telem_widget.plot(pen=pg.mkPen(color=COLOR_GHOST, style=Qt.DashLine, width=1.5))
        self.ghost_gas_curve.setOpacity(0.5)
        self.ghost_brake_curve = self.telem_widget.plot(pen=pg.mkPen(color=COLOR_GHOST, style=Qt.DashLine, width=1.5))
        self.ghost_brake_curve.setOpacity(0.5)
        
        self.cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(COLOR_CURSOR, width=1))
        self.cursor_line.sigDragged.connect(self.on_cursor_dragged)
        self.telem_widget.addItem(self.cursor_line)
        
        self.main_layout.addWidget(self.right_widget)
        self.refresh_sessions_list()

    def reset_lap_data(self):
        self.lap_data = {
            "distance": [], "x": [], "z": [], "speed": [],
            "throttle": [], "brake": [], "steer": [], "gear": [], "rpm": [], "delta": [], "colors": []
        }
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
            # Se voltar pro Live, limpa a tela de análise e desenha os dados ao vivo que acumulamos
            self.track_curve.setData(x=self.lap_data["x"], y=self.lap_data["z"], brush=self.lap_data["colors"])
            self.gas_curve.setData(x=self.lap_data["distance"], y=self.lap_data["throttle"])
            self.brake_curve.setData(x=self.lap_data["distance"], y=self.lap_data["brake"])
            self.ghost_gas_curve.setData(x=[], y=[])
            self.ghost_brake_curve.setData(x=[], y=[])

    def refresh_sessions_list(self):
        self.lap_list.clear()
        sessions = self.storage.load_sessions()
        for sess in sessions:
            # Sessão header
            date_str = sess.get('date', '')[:10]
            header = QListWidgetItem(f"[{date_str}] {sess.get('track_name')} - {sess.get('car_model')}")
            header.setBackground(QColor("#222"))
            header.setForeground(QColor("#aaa"))
            header.setFlags(Qt.ItemIsEnabled) # Não selecionável
            self.lap_list.addItem(header)
            
            best_idx = sess.get("fastest_lap_index", -1)
            
            for i, lap in enumerate(sess.get('laps', [])):
                is_best = "★ " if i == best_idx else "   "
                lap_text = f"{is_best}Lap {lap['lap_number']:02d}: {lap['lap_time']}"
                item = QListWidgetItem(lap_text)
                
                # Guarda dados invisíveis no item para carregar depois
                item.setData(Qt.UserRole, {
                    'session_dir': sess['session_dir'],
                    'file_path': lap['file_path'],
                    'is_best': (i == best_idx)
                })
                self.lap_list.addItem(item)
                
            self.lap_list.addItem(QListWidgetItem("")) # Spacer

    def on_lap_selected(self, item):
        data = item.data(Qt.UserRole)
        if not data: return
        
        lap_json = self.storage.load_lap_data(self.storage.base_dir, data['session_dir'], data['file_path'])
        if not lap_json: return
        
        # Muda pro modo análise automaticamente
        if not self.is_analysis_mode:
            self.toggle_mode()
            
        telemetry = lap_json.get("telemetry", {})
        dist = telemetry.get("distance", [])
        x = telemetry.get("x", [])
        z = telemetry.get("z", [])
        gas = telemetry.get("throttle", [])
        brake = telemetry.get("brake", [])
        
        # Limpa e exibe apenas a volta histórica
        colors = []
        for g, b in zip(gas, brake):
            if g > 0.1:
                intensity = min(255, int(100 + g * 155))
                colors.append(pg.mkBrush(0, intensity, 0, 255))
            elif b > 0.1:
                intensity = min(255, int(100 + b * 155))
                colors.append(pg.mkBrush(intensity, 0, 0, 255))
            else:
                colors.append(pg.mkBrush(QColor(COLOR_COAST)))
                
        self.track_curve.setData(x=x, y=z, brush=colors)
        self.gas_curve.setData(x=dist, y=gas)
        self.brake_curve.setData(x=dist, y=brake)
        
        # Guarda na lap_data TEMPORARIA pra o cursor funcionar no modo analise
        self.lap_data["distance"] = dist
        self.lap_data["x"] = x
        self.lap_data["z"] = z
        self.lap_data["throttle"] = gas
        self.lap_data["brake"] = brake
        
        if dist:
            self.cursor_line.setValue(dist[0])
            self.on_cursor_dragged()
            
    def on_cursor_dragged(self):
        if not self.is_analysis_mode or not self.lap_data["distance"]:
            return
            
        val = self.cursor_line.value()
        import bisect
        idx = bisect.bisect_left(self.lap_data["distance"], val)
        if idx >= len(self.lap_data["distance"]):
            idx = len(self.lap_data["distance"]) - 1
            
        x = self.lap_data["x"][idx]
        z = self.lap_data["z"][idx]
        gas = self.lap_data["throttle"][idx]
        brake = self.lap_data["brake"][idx]
        
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
            
        # 1. Inicia Sessão Nova se conectou agora
        if not self.session_started:
            self.storage.start_new_session(state.track_name, state.car_name)
            self.session_started = True
            self.current_lap_number = state.lap_number
            self.reset_lap_data()
            
        # 2. Detecção de Nova Volta
        if state.lap_number > self.current_lap_number:
            # Fechou a volta anterior. Salva no JSON se houver dados (e não for a volta 1 de saída)
            if len(self.lap_data["distance"]) > 50 and self.current_lap_number > 0:
                lap_time_s = parse_lap_time(state.last_time)
                # Passa uma cópia sem colors
                save_data = {k: v for k, v in self.lap_data.items() if k != 'colors'}
                self.storage.save_lap(
                    lap_number=self.current_lap_number,
                    lap_time_str=state.last_time,
                    lap_time_seconds=lap_time_s,
                    is_valid=True,
                    telemetry_data=save_data
                )
                self.refresh_sessions_list()
                
            self.current_lap_number = state.lap_number
            self.reset_lap_data()

        # 3. Coleta os Dados da Volta Atual
        x = state.car_x
        z = state.car_z
        gas = state.gas
        brake = state.brake

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
            # Acumula na memória
            self.lap_data["distance"].append(state.distance_traveled)
            self.lap_data["x"].append(x)
            self.lap_data["z"].append(z)
            self.lap_data["throttle"].append(gas)
            self.lap_data["brake"].append(brake)
            self.lap_data["speed"].append(state.speed_kmh)
            self.lap_data["steer"].append(state.steer_angle)
            self.lap_data["gear"].append(state.gear)
            self.lap_data["rpm"].append(state.rpm)
            self.lap_data["delta"].append(state.delta_time)
            self.lap_data["colors"].append(color)
            
            self.last_recorded_x = x
            self.last_recorded_z = z
            
            # 4. Atualiza a UI se estiver no Modo LIVE
            if not self.is_analysis_mode:
                self.track_curve.setData(x=self.lap_data["x"], y=self.lap_data["z"], brush=self.lap_data["colors"])
                self.gas_curve.setData(x=self.lap_data["distance"], y=self.lap_data["throttle"])
                self.brake_curve.setData(x=self.lap_data["distance"], y=self.lap_data["brake"])
                
                self.cursor_line.setValue(state.distance_traveled)
                self.car_marker.setData([x], [z])
                self.update_info_panel(state.distance_traveled, x, z, gas, brake)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    if MOCK_MODE:
        provider = MockTelemetryProvider()
    else:
        provider = AssettoCorsaTelemetryProvider()

    engine = TelemetryEngine(provider=provider, hz=60)
    engine.start()

    window = TrackMapWindow(engine)
    window.show()
    
    exit_code = app.exec_()
    engine.stop()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()