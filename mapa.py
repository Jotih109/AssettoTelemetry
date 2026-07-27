import math
import sys
import socket
import struct
import threading
import time
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel, QHBoxLayout, QSplitter, QPushButton
from PyQt5.QtCore import QTimer, Qt
import pyqtgraph as pg

# --- UDP Constantes ---
UDP_HOST = "0.0.0.0"
UDP_PORT = 5606

PKT_CAR_PHYSICS = 0
PKT_TIMINGS = 3

MOCK_MODE = True  # Mude para False para usar o jogo AMS2 real


class TrackMapData:
    def __init__(self):
        self.lock = threading.Lock()
        self.viewed_index = 0
        self.current_x = 0
        self.current_z = 0
        self.current_gas = 0.0
        self.current_brake = 0.0
        self.is_active = False

map_data = TrackMapData()

def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(2.0)
    
    try:
        sock.bind((UDP_HOST, UDP_PORT))
        print(f"Escutando AMS2 UDP na porta {UDP_PORT}...")
    except Exception as e:
        print(f"Erro ao bindar UDP: {e}")
        return

    while True:
        try:
            data, _ = sock.recvfrom(2048)
            if len(data) < 12:
                continue
                
            pkt_type = data[10]
            
            with map_data.lock:
                if pkt_type == PKT_CAR_PHYSICS:
                    # offset 12: sViewedParticipantIndex (int8)
                    if len(data) >= 15:
                        v_idx = struct.unpack_from("<b", data, 12)[0]
                        if v_idx >= 0:
                            map_data.viewed_index = v_idx
                            map_data.is_active = True
                            # offsets 13 e 14 são acelerador e freio unfiltered (0-255)
                            map_data.current_gas = data[13] / 255.0
                            map_data.current_brake = data[14] / 255.0
                            
                elif pkt_type == PKT_TIMINGS:
                    if not map_data.is_active:
                        continue
                        
                    idx = map_data.viewed_index
                    PART_OFFSET = 33
                    PART_SIZE = 48
                    
                    b = PART_OFFSET + (idx * PART_SIZE)
                    if b + 6 <= len(data):
                        # int16_t sWorldPosition[3]; (X=0, Y=2, Z=4)
                        x = struct.unpack_from("<h", data, b)[0]
                        z = struct.unpack_from("<h", data, b + 4)[0]
                        map_data.current_x = x
                        map_data.current_z = z

        except socket.timeout:
            with map_data.lock:
                map_data.is_active = False
        except OSError:
            break
        except Exception as e:
            print(f"Erro no parse: {e}")

class TrackMapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AMS2 Track Map Prototype")
        self.resize(800, 800)
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.is_analysis_mode = False
        
        top_bar = QHBoxLayout()
        self.header = QLabel(" Dirija pelo circuito para desenhar o mapa. Mantenha proporção 1:1.")
        self.header.setStyleSheet("color: white; background: #222; padding: 10px; font-family: Consolas;")
        
        self.btn_mode = QPushButton("❚❚ Pausar / Modo Análise")
        self.btn_mode.setStyleSheet("background: #555; color: white; font-weight: bold; padding: 10px;")
        self.btn_mode.clicked.connect(self.toggle_mode)
        
        top_bar.addWidget(self.header)
        top_bar.addWidget(self.btn_mode)
        self.layout.addLayout(top_bar)
        
        # Splitter principal para o mapa em cima e o gráfico embaixo
        self.splitter = QSplitter(Qt.Vertical)
        self.layout.addWidget(self.splitter)
        
        # Plot Widget (Mapa 2D)
        self.map_widget = pg.PlotWidget()
        self.map_widget.setBackground('#111111')
        self.map_widget.showGrid(x=False, y=False)
        self.map_widget.hideAxis('left')
        self.map_widget.hideAxis('bottom')
        self.map_widget.setAspectLocked(True) # PROPORÇÃO 1:1
        self.splitter.addWidget(self.map_widget)
        
        # Plot Widget (Telemetria Contínua Estilo MoTeC)
        self.telem_widget = pg.PlotWidget()
        self.telem_widget.setBackground('#111111')
        self.telem_widget.showGrid(x=True, y=True, alpha=0.3)
        self.telem_widget.setYRange(0, 1.05)
        self.telem_widget.setLabel('left', 'Pedal %')
        self.telem_widget.setLabel('bottom', 'Distância')
        self.splitter.addWidget(self.telem_widget)
        
        # Proporção 60% Mapa, 40% Gráfico
        self.splitter.setSizes([600, 400])
        
        # Elementos visuais do Mapa
        self.track_curve = pg.ScatterPlotItem(size=6, pen=None)
        self.map_widget.addItem(self.track_curve)
        
        self.car_marker = pg.ScatterPlotItem(size=14, pen=pg.mkPen(None), brush=pg.mkBrush(255, 255, 0, 255))
        self.map_widget.addItem(self.car_marker)
        
        # Elementos visuais da Telemetria (Pedais)
        self.gas_curve = self.telem_widget.plot(pen=pg.mkPen('#00ff00', width=2))
        self.brake_curve = self.telem_widget.plot(pen=pg.mkPen('#ff0000', width=2))
        
        # Ghost Lap
        self.ghost_gas_curve = self.telem_widget.plot(pen=pg.mkPen(color=(0, 255, 0, 100), style=Qt.DashLine, width=2))
        self.ghost_brake_curve = self.telem_widget.plot(pen=pg.mkPen(color=(255, 0, 0, 100), style=Qt.DashLine, width=2))
        
        # Cursor Sincronizado
        self.cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('y', width=2))
        self.cursor_line.sigDragged.connect(self.on_cursor_dragged)
        self.telem_widget.addItem(self.cursor_line)
        
        # Dados da pista e telemetria
        self.track_x = []
        self.track_z = []
        self.track_colors = []
        
        self.dist_array = []
        self.gas_array = []
        self.brake_array = []
        self.current_dist = 0.0
        
        self.last_recorded_x = None
        self.last_recorded_z = None
        
        # Timer de atualização a 60Hz
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_map)
        self.timer.start(1000 // 60)

    def toggle_mode(self):
        self.is_analysis_mode = not self.is_analysis_mode
        self.cursor_line.setMovable(self.is_analysis_mode)
        
        if self.is_analysis_mode:
            self.btn_mode.setText("► Voltar ao Vivo")
            self.btn_mode.setStyleSheet("background: #aa0000; color: white; font-weight: bold; padding: 10px;")
        else:
            self.btn_mode.setText("❚❚ Pausar / Modo Análise")
            self.btn_mode.setStyleSheet("background: #555; color: white; font-weight: bold; padding: 10px;")
            
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
        self.header.setText(f"[ANÁLISE] Dist: {val:.1f}m | X: {x:.0f}, Z: {z:.0f} | Acel: {gas*100:.0f}% Freio: {brake*100:.0f}%")

    def update_map(self):
        with map_data.lock:
            if not map_data.is_active:
                if not self.is_analysis_mode:
                    self.header.setText(" Aguardando dados do AMS2...")
                return
                
            x = map_data.current_x
            z = map_data.current_z
            gas = map_data.current_gas
            brake = map_data.current_brake

        # Atualiza posição do carro no mapa e texto (APENAS SE MODO AO VIVO)
        if not self.is_analysis_mode:
            self.header.setText(f" Carro em (X: {x:.1f}, Z: {z:.1f}) | Acel: {gas*100:.0f}% Freio: {brake*100:.0f}%")
            self.car_marker.setData([x], [z])
        
        # Determina a cor do rastro baseada nos pedais
        if gas > 0.1:
            intensity = min(255, int(100 + gas * 155))
            color = pg.mkBrush(0, intensity, 0, 255)
        elif brake > 0.1:
            intensity = min(255, int(100 + brake * 155))
            color = pg.mkBrush(intensity, 0, 0, 255)
        else:
            color = pg.mkBrush(255, 255, 0, 255) # Amarelo (Coasting)
            
        # Calcula distância percorrida para o Eixo X
        if self.last_recorded_x is not None:
            dist_delta = math.hypot(x - self.last_recorded_x, z - self.last_recorded_z)
        else:
            dist_delta = 0.0
            
        # Desenha rastro da pista e atualiza arrays de telemetria se o carro moveu
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
            
            # Limita array para ~1 volta (aprox 6000 pontos)
            if len(self.track_x) > 6000:
                self.track_x = self.track_x[-6000:]
                self.track_z = self.track_z[-6000:]
                self.track_colors = self.track_colors[-6000:]
                self.dist_array = self.dist_array[-6000:]
                self.gas_array = self.gas_array[-6000:]
                self.brake_array = self.brake_array[-6000:]
                
            self.track_curve.setData(x=self.track_x, y=self.track_z, brush=self.track_colors)
            
            # Atualiza Gráfico de Telemetria Contínua
            self.gas_curve.setData(x=self.dist_array, y=self.gas_array)
            self.brake_curve.setData(x=self.dist_array, y=self.brake_array)
            
            # Simula Ghost Lap com um leve delay (offset) e freio levemente atrasado
            if len(self.dist_array) > 100:
                ghost_dist = self.dist_array[100:]
                ghost_gas = self.gas_array[:-100]
                ghost_brake = self.brake_array[:-100]
                self.ghost_gas_curve.setData(x=ghost_dist, y=ghost_gas)
                self.ghost_brake_curve.setData(x=ghost_dist, y=ghost_brake)
                
                
            # Move o Cursor Vertical apenas se estiver no modo ao vivo
            if not self.is_analysis_mode:
                self.cursor_line.setValue(self.current_dist)

def generate_track_spline():
    """Gera um traçado suave de pista (estilo Interlagos) usando Catmull-Rom Spline"""
    keypoints = [
        (0.0, 0.0),            # Reta dos boxes
        (-100.0, 600.0),       # S do Senna
        (-250.0, 500.0),       # Curva 2
        (-150.0, 300.0),       # Curva do Sol
        (200.0, -400.0),       # Descida do Lago
        (400.0, -150.0),       # Ferradura
        (500.0, 50.0),         # Laranjinha
        (350.0, 350.0),        # Pinheirinho
        (150.0, 200.0),        # Bico de Pato
        (100.0, -50.0),        # Mergulho
        (20.0, -300.0),        # Junção
        (-40.0, -100.0),       # Subida dos boxes
    ]
    
    # Para fechar o loop do spline perfeitamente
    pts = [keypoints[-1]] + keypoints + [keypoints[0], keypoints[1]]
    
    track_path = []
    for i in range(1, len(pts) - 2):
        P0, P1, P2, P3 = pts[i-1], pts[i], pts[i+1], pts[i+2]
        
        # Maior densidade de pontos = mais suavidade
        num_points = 50 
        for t_step in range(num_points):
            t = t_step / num_points
            t2 = t * t
            t3 = t2 * t
            
            x = 0.5 * ((2 * P1[0]) + 
                       (-P0[0] + P2[0]) * t + 
                       (2*P0[0] - 5*P1[0] + 4*P2[0] - P3[0]) * t2 + 
                       (-P0[0] + 3*P1[0] - 3*P2[0] + P3[0]) * t3)
            
            z = 0.5 * ((2 * P1[1]) + 
                       (-P0[1] + P2[1]) * t + 
                       (2*P0[1] - 5*P1[1] + 4*P2[1] - P3[1]) * t2 + 
                       (-P0[1] + 3*P1[1] - 3*P2[1] + P3[1]) * t3)
            track_path.append((x, z))
            
    return track_path

def mock_driver():
    """Simula um carro percorrendo o traçado com velocidade variável"""
    track_path = generate_track_spline()
    total_points = len(track_path)
    
    idx = 0.0
    while True:
        with map_data.lock:
            map_data.is_active = True
            
            current_pt = track_path[int(idx) % total_points]
            map_data.current_x = current_pt[0]
            map_data.current_z = current_pt[1]
            
        # Calcula velocidade variável baseada na curva (distância para os próximos pontos)
        # Se os próximos pontos mudam de direção drasticamente, diminui a velocidade
        next_idx_1 = (int(idx) + 5) % total_points
        next_idx_2 = (int(idx) + 15) % total_points
        
        p1 = track_path[int(idx) % total_points]
        p2 = track_path[next_idx_1]
        p3 = track_path[next_idx_2]
        
        # Simples vetor direcional
        dx1, dz1 = p2[0] - p1[0], p2[1] - p1[1]
        dx2, dz2 = p3[0] - p2[0], p3[1] - p2[1]
        
        # Produto escalar para ver a curvatura (1.0 = reta, menor = curva)
        len1 = math.hypot(dx1, dz1)
        len2 = math.hypot(dx2, dz2)
        
        speed = 2.0  # velocidade base
        gas_sim = 0.0
        brake_sim = 0.0
        
        if len1 > 0 and len2 > 0:
            dot = (dx1 * dx2 + dz1 * dz2) / (len1 * len2)
            # dot = 1 (reta) -> alta velocidade | dot < 0.8 (curva) -> freia
            curve_factor = max(0.2, (dot ** 3)) 
            speed = 0.5 + (2.5 * curve_factor)
            
            # Mock dos pedais baseado na curvatura prevista
            if dot > 0.98:
                gas_sim = 1.0
                brake_sim = 0.0
            elif dot < 0.85:
                gas_sim = 0.0
                brake_sim = min(1.0, (0.85 - dot) * 6)
            else:
                gas_sim = 0.0
                brake_sim = 0.0 # Coasting
                
        with map_data.lock:
            map_data.current_gas = gas_sim
            map_data.current_brake = brake_sim
            
        idx += speed
        if idx >= total_points:
            idx -= total_points
            
        time.sleep(1.0 / 60.0)

if __name__ == "__main__":
    if MOCK_MODE:
        print("Rodando em MOCK_MODE (Simulador Ativado)...")
        t = threading.Thread(target=mock_driver, daemon=True)
    else:
        print("Rodando em MODO REAL (Aguardando AMS2)...")
        t = threading.Thread(target=udp_listener, daemon=True)
        
    t.start()
    
    app = QApplication(sys.argv)
    window = TrackMapWindow()
    window.show()
    sys.exit(app.exec_())