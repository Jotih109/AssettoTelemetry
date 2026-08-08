"""
test_voice.pyw — Bancada do Engenheiro de Pista (voz)
=====================================================
Ouve o engenheiro sem precisar entrar na pista.

A diferença para uma lista de frases soltas: cada botão MONTA UM ESTADO DE
TELEMETRIA e passa pelo `RaceEngineer` de verdade. O que você ouve aqui é
exatamente o que o piloto ouviria naquela situação — incluindo a severidade,
que decide a prioridade na fila de voz. Frase escrita à mão na bancada envelhece
e passa a mentir; estado montado, não.

    python test_voice.pyw
"""

import os
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

# Garante import do pacote core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.corner_analysis as ca
from core.models import TelemetryState
from core.race_engineer import ATTENTION, CRITICAL, INFO, RaceEngineer
from core.voice import (
    PRIORITY_CRITICAL, PRIORITY_LOW, PRIORITY_NORMAL, VoiceEngine,
)

#: Severidade -> prioridade na fila (o mesmo mapa que a janela principal usa).
VOICE_PRIORITY = {CRITICAL: PRIORITY_CRITICAL, ATTENTION: PRIORITY_NORMAL,
                  INFO: PRIORITY_LOW}

SEVERITY_COLOR = {CRITICAL: "#F87171", ATTENTION: "#FACC15", INFO: "#38BDF8"}


# ---------------------------------------------------------------------------
# Cenários — estado de telemetria, não frase pronta
# ---------------------------------------------------------------------------

#: Situações ao vivo: (rótulo do botão, campos do TelemetryState).
LIVE_SCENARIOS = [
    ("Delta perdendo",      dict(delta_time=0.45)),
    ("Delta voando",        dict(delta_time=-0.28)),
    ("Setor 1 verde",       dict(s1_delta=-0.09)),
    ("Setor 2 vermelho",    dict(s2_delta=0.31)),
    ("Melhor volta na mão", dict(track_position=0.97, delta_time=-0.30)),
    ("Melhor volta ameaçada", dict(track_position=0.96, delta_time=0.10)),
    ("ABS atuando",         dict(abs_intervention=0.9)),
    ("TC cortando",         dict(tc_intervention=0.9)),
    ("Pneu superaquecido",  dict(tyre_temp=[120.0, 85.0, 85.0, 85.0])),
    ("Pneu esquentando",    dict(tyre_temp=[108.0, 85.0, 85.0, 85.0])),
    ("Pneus frios",         dict(tyre_temp=[55.0] * 4)),
    ("Freios fervendo",     dict(brake_temp=[900.0] * 4)),
    ("Combustível baixo",   dict(fuel_laps_remaining=1.8)),
    ("Combustível crítico", dict(fuel_laps_remaining=0.8)),
    ("Combustível tranquilo", dict(fuel_laps_remaining=8.4, session_type="Race")),
    ("Pista verde",         dict(surface_grip=0.90)),
    ("Vento forte",         dict(wind_speed=12.0)),
    ("Limites de pista",    dict(tyres_out=4)),
    ("Bandeira amarela",    dict(flag="AMARELA")),
    ("Bandeira azul",       dict(flag="AZUL")),
    ("Bandeira preta",      dict(flag="PRETA")),
    ("Penalidade",          dict(penalty_time=5.0)),
    ("Limitador ligado",    dict(pit_limiter=True, speed_kmh=120.0)),
    ("Dano no carro",       dict(car_damage=35.0)),
    ("Dano grave",          dict(car_damage=70.0)),
    ("Última volta",        dict(total_laps=10, completed_laps=9)),
    ("Bandeira quadriculada", dict(flag="XADREZ")),
]

TRACK_LENGTH = 4309.0
SAMPLES = 200


def _state(**kw) -> TelemetryState:
    """Carro saudável em pista — a base sobre a qual cada cenário muda uma coisa."""
    st = TelemetryState(is_connected=True, car_name="Porsche Cup",
                        track_name="Interlagos")
    st.speed_kmh = 180.0
    st.tyre_temp = [85.0] * 4
    st.brake_temp = [400.0] * 4
    st.track_temp = 30.0
    st.surface_grip = 1.0
    st.max_rpm = 8000.0
    st.track_length = TRACK_LENGTH
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _comparison(index, nome, delta_t=None, d_vmin=None, d_brake=None,
                d_throttle=None, v_min=95.0, start=0.1, end=0.2, v_min_m=750.0):
    """Uma curva comparada com a referência, com os deltas pedidos."""
    c = ca.Corner(index=index, name=nome, start=start, end=end)
    lap = ca.CornerMetrics(corner=c, v_min=v_min, v_min_m=v_min_m,
                           braking_point_m=500.0, throttle_point_m=700.0,
                           entry_time=10.0,
                           exit_time=13.0 + (delta_t or 0.0))
    ref = ca.CornerMetrics(
        corner=c,
        v_min=(v_min - d_vmin) if d_vmin is not None else None,
        v_min_m=v_min_m,
        braking_point_m=(500.0 - d_brake) if d_brake is not None else None,
        throttle_point_m=(700.0 - d_throttle) if d_throttle is not None else None,
        entry_time=10.0, exit_time=13.0)
    return ca.CornerComparison(corner=c, lap=lap, ref=ref)


def _volta(**canais) -> dict:
    """Uma volta sintética limpa; cada cenário troca só o canal que interessa."""
    n = SAMPLES
    tel = {
        "times": [i * 0.05 for i in range(n)],
        "distance": [i * (TRACK_LENGTH / n) for i in range(n)],
        "speed": [150.0] * n,
        "gas": [1.0] * n,
        "brake": [0.0] * n,
        "rpm": [7800.0] * n,
        "gear": [5] * n,
        "steer": [0.0] * n,
        "g_lat": [0.0] * n,
        "car_x": [0.0] * n,
        "car_z": [0.0] * n,
        "abs_intervention": [0.0] * n,
        "tc_intervention": [0.0] * n,
    }
    tel.update(canais)
    return tel


def _canal(valor_padrao, *trechos):
    """Monta um canal com `valor_padrao` e trechos (i0, i1, valor)."""
    arr = [valor_padrao] * SAMPLES
    for i0, i1, valor in trechos:
        for i in range(i0, min(i1, SAMPLES)):
            arr[i] = valor
    return arr


def _volta_com_abs():
    return _volta(abs_intervention=[0.9 if i % 5 == 0 else 0.0
                                    for i in range(SAMPLES)])


def _volta_com_tc():
    return _volta(tc_intervention=_canal(0.0, (110, 140, 0.9)))


def _volta_com_pe_preso():
    return _volta(brake=_canal(0.0, (40, 100, 0.7)),
                  gas=_canal(1.0, (70, 100, 0.0)))


def _volta_com_freio_em_degraus():
    # O gás sai junto com o freio e a freada termina numa rampa: assim o
    # cenário isola o degrau, sem disparar também o pé preso nem a soltura seca.
    freio = [0.0] * SAMPLES
    gas = [1.0] * SAMPLES
    for k in range(5):
        base = 10 + k * 35
        for i in range(base, base + 8):
            freio[i], gas[i] = 0.9, 0.0
        for i in range(base + 8, base + 13):
            freio[i], gas[i] = 0.4, 0.0
        for i in range(base + 13, base + 18):
            freio[i], gas[i] = 0.7, 0.0
        for j in range(10):                      # alívio progressivo no fim
            freio[base + 18 + j], gas[base + 18 + j] = 0.7 - j * 0.06, 0.0
    return _volta(brake=freio, gas=gas)


def _volta_com_freio_seco():
    """Freada forte e o pé pulando fora do pedal na entrada."""
    freio = [0.0] * SAMPLES
    gas = [1.0] * SAMPLES
    for k in range(5):
        base = 15 + k * 35
        for i in range(base, base + 18):
            freio[i], gas[i] = 0.9, 0.0
        freio[base + 18], gas[base + 18] = 0.3, 0.0
    return _volta(brake=freio, gas=gas)


def _volta_com_subesterco():
    return _volta(steer=_canal(20.0, (0, 100, 100.0)),
                  g_lat=_canal(1.5, (0, 100, 0.2)))


def _volta_com_troca_cedo():
    marchas = [2] * SAMPLES
    for k in range(1, 5):
        i = k * 40
        marchas[i:] = [2 + k] * (SAMPLES - i)
    return _volta(rpm=[4000.0] * SAMPLES, gear=marchas)


#: Situações de fim de volta: (rótulo, kwargs de `analyze_lap`).
LAP_SCENARIOS = [
    ("Boa volta", dict(comparisons=[_comparison(1, "S do Senna", delta_t=-0.20)],
                       lap_time_str="1:29.215", lap_delta_s=-0.45)),
    ("Volta pior", dict(comparisons=[], lap_time_str="1:30.010", lap_delta_s=0.80)),
    ("Setores: perdendo no S2", dict(
        comparisons=[], sector_times_ms=[30000, 31500, 29900],
        ref_sector_times_ms=[30100, 30000, 30000])),
    ("Setores: volta distribuída", dict(
        comparisons=[], sector_times_ms=[30000, 30000, 30000],
        ref_sector_times_ms=[30020, 30010, 30030])),
    ("Freou antes", dict(comparisons=[
        _comparison(1, "Curva 1", delta_t=0.42, d_brake=-15.0)])),
    ("Ponto de freio quase certo", dict(comparisons=[
        _comparison(1, "Curva 1", delta_t=0.10, d_brake=-5.0)])),
    ("Entrou devagar no ápice", dict(comparisons=[
        _comparison(3, "Ferradura", delta_t=0.35, d_vmin=-6.0, v_min=88.0)])),
    ("Demorou a acelerar", dict(comparisons=[
        _comparison(4, "Laranjinha", delta_t=0.28, d_throttle=12.0)])),
    ("Marcha errada no ápice", dict(
        comparisons=[_comparison(3, "Ferradura", delta_t=0.30)],
        lap_telemetry=_volta(gear=[3] * SAMPLES),
        ref_telemetry=_volta(gear=[4] * SAMPLES), state=_state())),
    ("Saída de curva lenta", dict(
        comparisons=[_comparison(2, "Junção", delta_t=0.25)],
        lap_telemetry=_volta(speed=[120.0] * SAMPLES),
        ref_telemetry=_volta(speed=[132.0] * SAMPLES), state=_state())),
    ("Curva de gás cheio virou freada", dict(
        comparisons=[_comparison(5, "Curva do Sol", delta_t=0.35,
                                 start=0.1, end=0.25)],
        lap_telemetry=_volta(brake=_canal(0.0, (20, 45, 0.8))),
        ref_telemetry=_volta(), state=_state())),
    ("Fora da linha ideal", dict(
        comparisons=[_comparison(4, "Curva 4", delta_t=0.28, start=0.1, end=0.25)],
        lap_telemetry=_volta(car_z=[3.0] * SAMPLES),
        ref_telemetry=_volta(), state=_state())),
    ("Reforço positivo", dict(comparisons=[
        _comparison(2, "Mergulho", delta_t=-0.22)])),
    ("Balanço completo (3 curvas)", dict(comparisons=[
        _comparison(1, "Curva 1", delta_t=0.42, d_brake=-15.0),
        _comparison(3, "Ferradura", delta_t=0.30, d_vmin=-6.0),
        _comparison(4, "Laranjinha", delta_t=0.18, d_throttle=14.0),
        _comparison(6, "Mergulho", delta_t=-0.15)],
        lap_time_str="1:30.400", lap_delta_s=0.75,
        sector_times_ms=[30000, 31200, 29900],
        ref_sector_times_ms=[30100, 30000, 30000])),
    ("Freio e acelerador juntos", dict(
        comparisons=[], lap_telemetry=_volta_com_pe_preso())),
    ("Freio solto em degraus", dict(
        comparisons=[], lap_telemetry=_volta_com_freio_em_degraus())),
    ("Freio largado de uma vez", dict(
        comparisons=[], lap_telemetry=_volta_com_freio_seco())),
    ("Subesterço", dict(comparisons=[], lap_telemetry=_volta_com_subesterco())),
    ("Volante brusco", dict(
        comparisons=[],
        lap_telemetry=_volta(steer=[(-1) ** i * 60.0 for i in range(SAMPLES)]),
        ref_telemetry=_volta(steer=[30.0 + 0.1 * i for i in range(SAMPLES)]))),
    ("Trocando cedo demais", dict(
        comparisons=[], lap_telemetry=_volta_com_troca_cedo(), state=_state())),
    ("Batendo no corte", dict(
        comparisons=[], lap_telemetry=_volta(rpm=[8000.0] * SAMPLES,
                                             gear=_canal(2, (100, SAMPLES, 3))),
        state=_state())),
    # As faixas cobrem o pico do canal para o aviso apontar a curva certa.
    ("Vício de ABS", dict(comparisons=[
        _comparison(1, "Curva 1", start=0.0, end=0.10)],
        lap_telemetry=_volta_com_abs())),
    ("Vício de TC", dict(comparisons=[
        _comparison(1, "Curva 1", start=0.0, end=0.10),
        _comparison(2, "Junção", start=0.54, end=0.72)],
        lap_telemetry=_volta_com_tc())),
    ("Combustível não fecha a corrida", dict(comparisons=[], state=_state(
        total_laps=20, completed_laps=5, fuel_laps_remaining=6.0))),
]


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------

class VoiceTestWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ApexView — Bancada de Voz do Engenheiro")
        self.resize(1150, 780)
        self.setMinimumSize(950, 600)
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #121316; color: #E2E8F0;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                border: 1px solid #2D3748; border-radius: 6px;
                margin-top: 10px; font-weight: bold; color: #38BDF8;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 5px;
            }
            QPushButton {
                background-color: #1E293B; border: 1px solid #334155;
                border-radius: 4px; color: #F8FAFC; padding: 6px 12px;
                font-size: 12px; text-align: left;
            }
            QPushButton:hover { background-color: #0284C7; border-color: #38BDF8; }
            QPushButton:pressed { background-color: #0369A1; }
            QLineEdit, QComboBox {
                background-color: #0F172A; border: 1px solid #334155;
                border-radius: 4px; color: #F8FAFC; padding: 6px;
            }
            QListWidget {
                background-color: #0F172A; border: 1px solid #334155;
                border-radius: 6px; padding: 4px;
            }
        """)

        self.voice = VoiceEngine(enabled=True, backend="auto")
        self.engineer = RaceEngineer()

        self._setup_ui()

        # O backend é escolhido na thread de voz: o status só fica correto
        # depois disso, então a tela pergunta em vez de adivinhar.
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(200)
        self._update_status()

    # -- interface --------------------------------------------------------

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_custom_row())

        splitter = QSplitter(Qt.Horizontal)
        esquerda = QWidget()
        col = QVBoxLayout(esquerda)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._build_live_box())
        col.addWidget(self._build_lap_box())
        col.addWidget(self._build_queue_box())
        splitter.addWidget(esquerda)
        splitter.addWidget(self._build_log_box())
        splitter.setSizes([680, 450])
        layout.addWidget(splitter)

    def _build_header(self) -> QGroupBox:
        box = QGroupBox("Motor de voz")
        row = QHBoxLayout(box)

        self.lbl_status = QLabel("Escolhendo backend...")
        self.lbl_status.setFont(QFont("Segoe UI", 10, QFont.Bold))

        self.chk_voice = QCheckBox("Voz ligada")
        self.chk_voice.setChecked(True)
        self.chk_voice.stateChanged.connect(self._on_toggle_voice)

        btn_clear = QPushButton("Esvaziar fila")
        btn_clear.clicked.connect(self.voice.clear)

        row.addWidget(self.lbl_status)
        row.addStretch()
        row.addWidget(self.chk_voice)
        row.addWidget(btn_clear)
        return box

    def _build_custom_row(self) -> QGroupBox:
        box = QGroupBox("Fala personalizada")
        row = QHBoxLayout(box)

        self.txt_custom = QLineEdit()
        self.txt_custom.setPlaceholderText("Digite um texto e tecle Enter...")
        self.txt_custom.returnPressed.connect(self._speak_custom)

        self.combo_priority = QComboBox()
        self.combo_priority.addItems(["Crítico", "Normal", "Baixa"])
        self.combo_priority.setCurrentIndex(1)
        self.combo_priority.setToolTip("Prioridade na fila de voz")

        btn = QPushButton("Falar")
        btn.clicked.connect(self._speak_custom)

        row.addWidget(self.txt_custom, stretch=1)
        row.addWidget(self.combo_priority)
        row.addWidget(btn)
        return box

    def _build_live_box(self) -> QGroupBox:
        box = QGroupBox("1. Ao vivo — o estado vai para analyze_live()")
        grid = QGridLayout(box)
        for i, (rotulo, campos) in enumerate(LIVE_SCENARIOS):
            btn = QPushButton(rotulo)
            btn.clicked.connect(lambda _ch, c=campos, r=rotulo: self._run_live(c, r))
            grid.addWidget(btn, i // 3, i % 3)
        return box

    def _build_lap_box(self) -> QGroupBox:
        box = QGroupBox("2. Fim de volta — vai para analyze_lap()")
        grid = QGridLayout(box)
        for i, (rotulo, kwargs) in enumerate(LAP_SCENARIOS):
            btn = QPushButton(rotulo)
            btn.clicked.connect(lambda _ch, k=kwargs, r=rotulo: self._run_lap(k, r))
            grid.addWidget(btn, i // 2, i % 2)
        return box

    def _build_queue_box(self) -> QGroupBox:
        box = QGroupBox("3. Fila — prioridade e preempção")
        row = QHBoxLayout(box)

        btn_preempt = QPushButton("Balanço + crítico no meio")
        btn_preempt.setToolTip("O crítico deve CORTAR a frase em andamento")
        btn_preempt.clicked.connect(self._test_preemption)

        btn_flood = QPushButton("Encher a fila (6 recados)")
        btn_flood.setToolTip("A fila guarda 3: os menos importantes caem")
        btn_flood.clicked.connect(self._test_flood)

        row.addWidget(btn_preempt)
        row.addWidget(btn_flood)
        row.addStretch()
        return box

    def _build_log_box(self) -> QGroupBox:
        box = QGroupBox("O que foi para a voz")
        col = QVBoxLayout(box)
        self.list_log = QListWidget()
        self.list_log.setWordWrap(True)
        self.list_log.itemDoubleClicked.connect(self._replay_item)
        dica = QLabel("Duplo clique em um item para repetir. A cor é a severidade.")
        dica.setStyleSheet("color: #94A3B8; font-size: 11px;")
        col.addWidget(self.list_log)
        col.addWidget(dica)
        return box

    # -- ações ------------------------------------------------------------

    def _update_status(self):
        if not self.voice._ready.is_set():
            return
        self._status_timer.stop()
        if self.voice.available:
            self.lbl_status.setText(f"Voz ativa: {self.voice.voice_name}")
            self.lbl_status.setStyleSheet("color: #4ADE80;")
        else:
            self.lbl_status.setText(
                "Nenhum sintetizador disponível (instale pywin32)")
            self.lbl_status.setStyleSheet("color: #F87171;")

    def _on_toggle_voice(self, estado):
        self.voice.enabled = (estado == Qt.Checked)
        if not self.voice.enabled:
            self.voice.clear()

    def _speak_custom(self):
        texto = self.txt_custom.text().strip()
        if not texto:
            return
        prioridade = [PRIORITY_CRITICAL, PRIORITY_NORMAL,
                      PRIORITY_LOW][self.combo_priority.currentIndex()]
        severidade = [CRITICAL, ATTENTION, INFO][self.combo_priority.currentIndex()]
        self._speak(texto, severidade, prioridade, "manual")
        self.txt_custom.clear()

    def _run_live(self, campos: dict, rotulo: str):
        # Engenheiro novo a cada clique: o tempo de espera das regras é para a
        # pista, na bancada ele só atrapalharia.
        eng = RaceEngineer()
        advices = eng.analyze_live(_state(**campos), now=0.0)
        self._emit(advices, rotulo)

    def _run_lap(self, kwargs: dict, rotulo: str):
        eng = RaceEngineer()
        advices = eng.analyze_lap(**kwargs)
        self._emit(advices, rotulo)

    def _emit(self, advices: list, rotulo: str):
        if not advices:
            self._log(f"(silêncio — nada a dizer em '{rotulo}')", INFO)
            return
        # Mesma escolha da janela principal: o painel mostra tudo, a voz recebe
        # o essencial.
        falados = RaceEngineer.pick_for_voice(advices, limit=2)
        for advice in advices:
            if advice in falados:
                self._speak(advice.spoken, advice.severity,
                            VOICE_PRIORITY.get(advice.severity, PRIORITY_NORMAL),
                            rotulo)
            else:
                self._log(f"[só no painel] {advice.display}", advice.severity)

    def _test_preemption(self):
        self._speak("Curva um, perdeu 0,42 segundos, freou 15 metros antes. "
                    "Atrase a freada", ATTENTION, PRIORITY_NORMAL, "preempção")
        QTimer.singleShot(1200, lambda: self._speak(
            "Bandeira preta, entra nos boxes", CRITICAL, PRIORITY_CRITICAL,
            "preempção"))

    def _test_flood(self):
        for i in range(1, 5):
            self._speak(f"Recado de baixa prioridade número {i}", INFO,
                        PRIORITY_LOW, "enchente")
        self._speak("Combustível crítico, menos de uma volta", CRITICAL,
                    PRIORITY_CRITICAL, "enchente")
        self._speak("Pneu dianteiro esquerdo está esquentando", ATTENTION,
                    PRIORITY_NORMAL, "enchente")

    def _speak(self, texto: str, severidade: str, prioridade: int, origem: str):
        self.voice.say(texto, priority=prioridade)
        self._log(f"[{origem}] {texto}", severidade, texto)

    def _log(self, linha: str, severidade: str, replay: str = ""):
        item = QListWidgetItem(linha)
        item.setForeground(QColor(SEVERITY_COLOR.get(severidade, "#F8FAFC")))
        item.setData(Qt.UserRole, replay)
        self.list_log.insertItem(0, item)

    def _replay_item(self, item: QListWidgetItem):
        texto = item.data(Qt.UserRole)
        if texto:
            self.voice.say(texto)

    def closeEvent(self, event):
        self.voice.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    janela = VoiceTestWindow()
    janela.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
