"""
ajustar_voz.pyw — Ajuste da voz do engenheiro, de ouvido
=========================================================

Timbre, ritmo e tom são gosto pessoal. Mudam com o headset, com a caixa de
som e com as vozes que aquele Windows tem instaladas — nenhum padrão escolhido
no código acerta para todo mundo. Esta janela fala os recados DE VERDADE do
engenheiro com os ajustes que você escolher, cronometra cada frase, e grava a
sua escolha no `config.json`.

    python ajustar_voz.pyw

Por que cronometrar
-------------------
Metade das queixas de "voz travada" não é ritmo, é FRASE COMPRIDA. Um recado
de spotter tem de caber em uns 3 segundos; acima de 5 a voz pausa na
pontuação, parece que não termina, e acelerar só troca "arrastado" por
"apressado" — o que conserta é encurtar o texto. Por isso cada frase aparece
com o tempo que levou, e as longas vêm marcadas.

Nada aqui depende do jogo aberto.
"""

import os
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QPushButton,
                             QScrollArea, QSlider, QTabWidget, QVBoxLayout,
                             QWidget)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui.theme as T
from core.config import get_config
from core.voice import (DEFAULT_PITCH, DEFAULT_RATE, PRIORITY_NORMAL,
                        VoiceEngine)
from core.voice_mix import CHANNELS, VOLUME_MAX

#: Recados reais do engenheiro, do mais curto ao mais longo.
#:
#: Testar com a frase de verdade é o ponto: qualquer ajuste soa bem em duas
#: palavras, e é a frase longa que denuncia o problema.
FRASES = [
    "Curva 7 chegando, atrasa a freada uns 12 metros",
    "Perdeu 2 décimos na Curva 5, freou cedo",
    "Isso! 3 décimos a mais na Curva 9",
    "Tá travando a roda na freada da Curva 9. Chega mais suave",
    "Tá largando o freio de uma vez na freada da Curva 4. Alivia até o ápice",
    "Tem 4 décimos na mesa: 2 décimos na Curva 5, 1 décimo na Curva 12",
    "Bandeira amarela no setor 2",
]

#: Ritmos que o botão de comparação percorre.
RITMOS_COMPARADOS = (0, 2, 4)

#: Tons que o botão de comparação percorre — do neutro ao bem deslocado.
#:
#: Deslocar o tom não muda COMO a voz é sintetizada: o Windows reamostra a
#: fala já pronta. Quanto maior o deslocamento, mais artefato, e o ouvido
#: registra isso como "metálico" ou "eletrônico" sem saber de onde vem. Este
#: A/B existe para separar as duas coisas — o timbre que você quer, e o preço
#: que ele cobra.
TONS_COMPARADOS = (0, -2, -4)

#: A partir deste deslocamento (em módulo) o artefato costuma aparecer.
TOM_ARRISCADO = 3

#: Acima disto a frase é longa demais para um recado de pista (s).
#:
#: O tempo medido é de relógio, do clique até a voz calar, e inclui uns 1,5 s
#: que o sintetizador leva para começar. Por isso o limite é 6 s e não os 4 s
#: que um recado deveria durar: descontado o arranque, 6 s de relógio já são
#: quatro segundos e meio de fala — aí a frase precisa encurtar, não acelerar.
FRASE_LONGA_S = 6.0

#: Texto do seletor quando o app escolhe a voz sozinho.
VOZ_AUTOMATICA = "(escolha automática — melhor voz em português)"


def _titulo(texto: str) -> QLabel:
    rotulo = QLabel(texto.upper())
    rotulo.setFont(T.f_title(8))
    rotulo.setStyleSheet(f"color: {T.TXT_TITLE}; letter-spacing: 1px;")
    return rotulo


def _botao(texto: str, cor: str = None) -> QPushButton:
    b = QPushButton(texto)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(f"""
        QPushButton {{
            background-color: {T.BG_HEADER};
            color: {cor or T.TXT_VALUE};
            border: 1px solid {T.BORDER};
            padding: 6px 12px;
        }}
        QPushButton:hover {{ background-color: #262c31; }}
        QPushButton:disabled {{ color: {T.TXT_DIM}; }}
    """)
    return b


class AjusteVoz(QMainWindow):
    """
    Janela de ajuste. Um motor de voz por combinação de ajustes.

    O motor NÃO é reconfigurado no lugar: o objeto do SAPI vive na thread de
    fala e mexer nele de fora trava o processo (COM apartment-threaded). Como
    trocar de ajuste é coisa de quem está experimentando, e não do jogo
    rodando, recriar o motor é barato e seguro.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ajuste da voz do engenheiro")
        self.resize(760, 620)
        self.setStyleSheet(T.app_qss())

        self.config = get_config()
        self.mix = self.config.voice_mix()
        self._faders = {}             # id do canal -> slider
        self._engine = None
        self._chave = None            # (voz, ritmo, tom, volume) do motor atual
        self._inicio = None           # início da frase em curso
        self._rotulo_atual = ""
        self._fila = []               # comparação: [(ritmo, frase), ...]

        self._montar_ui()
        self._carregar_vozes()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    # -- interface ----------------------------------------------------------

    def _montar_ui(self):
        abas = QTabWidget()
        abas.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {T.BORDER}; }}
            QTabBar::tab {{
                background: {T.BG_PANEL}; color: {T.TXT_LABEL};
                padding: 7px 16px; border: 1px solid {T.BORDER};
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background: {T.BG_HEADER}; color: {T.TXT_VALUE};
            }}
        """)
        abas.addTab(self._aba_voz(), "VOZ")
        abas.addTab(self._aba_mesa(), "MESA DE SOM")

        central = QWidget()
        fora = QVBoxLayout(central)
        fora.setContentsMargins(10, 8, 10, 8)
        fora.setSpacing(8)
        fora.addWidget(abas, 1)

        rodape = QHBoxLayout()
        self.lb_estado = QLabel("pronto")
        self.lb_estado.setStyleSheet(f"color: {T.TXT_DIM};")
        rodape.addWidget(self.lb_estado, 1)
        b_copiar = _botao("Copiar meu ajuste")
        b_copiar.clicked.connect(self._copiar_resumo)
        rodape.addWidget(b_copiar)
        self.b_salvar = _botao("Salvar no config.json", T.OK)
        self.b_salvar.clicked.connect(self._salvar)
        rodape.addWidget(self.b_salvar)
        fora.addLayout(rodape)

        self.setCentralWidget(central)

    def _aba_voz(self) -> QWidget:
        pagina = QWidget()
        raiz = QVBoxLayout(pagina)
        raiz.setContentsMargins(12, 10, 12, 10)
        raiz.setSpacing(10)

        raiz.addWidget(_titulo("voz"))
        self.combo_voz = QComboBox()
        self.combo_voz.setStyleSheet(
            f"QComboBox {{ background: {T.BG_INSET}; border: 1px solid "
            f"{T.BORDER}; padding: 5px; }}")
        self.combo_voz.currentIndexChanged.connect(self._ajuste_mudou)
        raiz.addWidget(self.combo_voz)

        # Que motor está sintetizando. Importa quando a queixa é de TIMBRE:
        # as vozes do Windows têm um teto de naturalidade que nenhum slider
        # ultrapassa, e saber disso evita ficar caçando o ajuste perfeito num
        # motor que não tem o que se procura.
        self.lb_backend = QLabel("")
        self.lb_backend.setFont(T.f_label(8))
        self.lb_backend.setWordWrap(True)
        self.lb_backend.setStyleSheet(f"color: {T.TXT_DIM};")
        raiz.addWidget(self.lb_backend)

        raiz.addWidget(_titulo("ajuste"))
        grade = QGridLayout()
        grade.setSpacing(6)
        self.sl_ritmo = self._slider(
            grade, 0, "Ritmo", -10, 10, int(self.config.get("voice_rate")),
            "-10 arrastado   ·   +10 apressado")
        self.sl_tom = self._slider(
            grade, 1, "Tom", -10, 10, int(self.config.get("voice_pitch")),
            "-10 grave   ·   +10 agudo")
        # O aviso vive embaixo do slider e acende sozinho: o preço do tom
        # deslocado é artefato de reamostragem, e quem move o fader precisa
        # saber disso NA HORA, não depois de estranhar a voz por uma semana.
        self.lb_aviso_tom = QLabel("")
        self.lb_aviso_tom.setFont(T.f_label(8))
        self.lb_aviso_tom.setWordWrap(True)
        grade.addWidget(self.lb_aviso_tom, 3, 1, 1, 3)
        self.sl_tom.valueChanged.connect(self._avaliar_tom)
        self._avaliar_tom(self.sl_tom.value())
        self.sl_vol = self._slider(
            grade, 2, "Volume", 0, 100, int(self.config.get("voice_volume")),
            "")
        raiz.addLayout(grade)

        linha_padrao = QHBoxLayout()
        b_padrao = _botao("Voltar ao padrão do app")
        b_padrao.clicked.connect(self._restaurar_padrao)
        linha_padrao.addWidget(b_padrao)
        b_comparar = _botao(
            "Comparar ritmos "
            + " / ".join(f"{r:+d}" for r in RITMOS_COMPARADOS), T.CH_SPEED)
        b_comparar.clicked.connect(self._comparar_ritmos)
        linha_padrao.addWidget(b_comparar)
        b_tons = _botao(
            "Comparar tons "
            + " / ".join(f"{t:+d}" for t in TONS_COMPARADOS), T.CH_STEER)
        b_tons.setToolTip(
            "A mesma frase com o tom neutro e deslocado. Se a voz soa "
            "eletrônica, o culpado costuma ser o tom: o Windows reamostra a "
            "fala para deslocá-lo, e isso deixa artefato.")
        b_tons.clicked.connect(self._comparar_tons)
        linha_padrao.addWidget(b_tons)
        linha_padrao.addStretch(1)
        raiz.addLayout(linha_padrao)

        raiz.addWidget(_titulo("recados do engenheiro — clique para ouvir"))
        self.lista = QListWidget()
        self.lista.setStyleSheet(f"""
            QListWidget {{
                background: {T.BG_INSET}; border: 1px solid {T.BORDER_SOFT};
            }}
            QListWidget::item {{ padding: 5px 7px; }}
            QListWidget::item:selected {{ background: {T.BG_HEADER}; }}
        """)
        for frase in FRASES:
            self.lista.addItem(QListWidgetItem(frase))
        self.lista.setCurrentRow(3)
        self.lista.itemClicked.connect(
            lambda item: self._falar(item.text()))
        raiz.addWidget(self.lista, 1)

        linha_livre = QHBoxLayout()
        self.campo = QLineEdit()
        self.campo.setPlaceholderText("Escreva uma frase e tecle Enter…")
        self.campo.setStyleSheet(
            f"background: {T.BG_INSET}; border: 1px solid {T.BORDER}; "
            f"padding: 6px;")
        self.campo.returnPressed.connect(self._falar_livre)
        linha_livre.addWidget(self.campo, 1)
        b_falar = _botao("Falar")
        b_falar.clicked.connect(self._falar_livre)
        linha_livre.addWidget(b_falar)
        raiz.addLayout(linha_livre)

        raiz.addWidget(_titulo("o que aconteceu"))
        self.log = QListWidget()
        self.log.setMaximumHeight(130)
        self.log.setFont(T.f_label(8))
        self.log.setStyleSheet(f"""
            QListWidget {{
                background: {T.BG_INSET}; border: 1px solid {T.BORDER_SOFT};
            }}
            QListWidget::item {{ padding: 2px 6px; }}
        """)
        raiz.addWidget(self.log)
        return pagina

    # -- mesa de som --------------------------------------------------------

    def _aba_mesa(self) -> QWidget:
        """
        Um fader por assunto do engenheiro.

        A mesa mexe só na VOZ: um canal no zero cala a fala daquele assunto, e
        o painel de texto do dashboard continua mostrando tudo. Silenciar é
        escolher não ser interrompido, não escolher ficar sem o dado.
        """
        pagina = QWidget()
        raiz = QVBoxLayout(pagina)
        raiz.setContentsMargins(12, 10, 12, 10)
        raiz.setSpacing(8)

        explicacao = QLabel(
            "Volume de cada assunto. Zero cala a VOZ daquele assunto — "
            "o painel de texto do dashboard continua mostrando tudo.")
        explicacao.setWordWrap(True)
        explicacao.setFont(T.f_label(8))
        explicacao.setStyleSheet(f"color: {T.TXT_DIM};")
        raiz.addWidget(explicacao)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        dentro = QWidget()
        col = QVBoxLayout(dentro)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(4)

        for canal in CHANNELS:
            col.addWidget(self._faixa_de_canal(canal))
        col.addStretch(1)

        area.setWidget(dentro)
        raiz.addWidget(area, 1)

        linha = QHBoxLayout()
        b_tudo = _botao("Tudo no máximo")
        b_tudo.clicked.connect(lambda: self._mesa_toda(VOLUME_MAX))
        linha.addWidget(b_tudo)
        b_so_curva = _botao("Só o coach de curva", T.CH_SPEED)
        b_so_curva.setToolTip(
            "Deixa no ar só a dica antes da curva, o veredito na saída e as "
            "bandeiras. Bom para uma sessão de aprender a pista.")
        b_so_curva.clicked.connect(self._preset_coach)
        linha.addWidget(b_so_curva)
        b_corrida = _botao("Modo corrida", T.WARN)
        b_corrida.setToolTip(
            "Bandeira, combustível e carro no máximo; aula de pilotagem baixa. "
            "Quem está brigando por posição não quer ponto de freada.")
        b_corrida.clicked.connect(self._preset_corrida)
        linha.addWidget(b_corrida)
        linha.addStretch(1)
        raiz.addLayout(linha)
        return pagina

    def _faixa_de_canal(self, canal) -> QWidget:
        """Uma faixa da mesa: nome, o que cai nela, fader e botão de ouvir."""
        caixa = QFrame()
        caixa.setStyleSheet(
            f"QFrame {{ background: {T.BG_PANEL}; border: 1px solid "
            f"{T.BORDER_SOFT}; }}")
        linha = QGridLayout(caixa)
        linha.setContentsMargins(8, 5, 8, 5)
        linha.setHorizontalSpacing(8)
        linha.setVerticalSpacing(1)

        nome = QLabel(canal.label)
        nome.setFont(T.f_value(9))
        nome.setMinimumWidth(150)
        linha.addWidget(nome, 0, 0)

        dica = QLabel(canal.hint)
        dica.setFont(T.f_label(8))
        dica.setStyleSheet(f"color: {T.TXT_DIM}; border: none;")
        linha.addWidget(dica, 1, 0, 1, 2)

        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, VOLUME_MAX)
        sl.setValue(self.mix.level(canal.id))
        sl.setMinimumWidth(180)
        sl.setStyleSheet(f"""
            QSlider {{ border: none; }}
            QSlider::groove:horizontal {{ height: 4px; background: {T.BORDER}; }}
            QSlider::handle:horizontal {{
                background: {T.CH_THROTTLE}; width: 12px; margin: -5px 0;
            }}
        """)
        linha.addWidget(sl, 0, 1)

        valor = QLabel(str(sl.value()))
        valor.setFont(T.f_value(9))
        valor.setMinimumWidth(30)
        valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        valor.setStyleSheet("border: none;")
        linha.addWidget(valor, 0, 2)

        b_mudo = _botao("mudo")
        b_mudo.setMaximumWidth(64)
        linha.addWidget(b_mudo, 0, 3)

        b_ouvir = _botao("ouvir")
        b_ouvir.setMaximumWidth(64)
        b_ouvir.clicked.connect(
            lambda _=False, c=canal: self._ouvir_canal(c))
        linha.addWidget(b_ouvir, 0, 4)

        def mudou(v, cid=canal.id, lb=valor, botao=b_mudo):
            lb.setText(str(v))
            lb.setStyleSheet(
                f"border: none; color: {T.TXT_DIM if v == 0 else T.TXT_VALUE};")
            botao.setText("mudo" if v > 0 else "ligar")
            self.mix.set_level(cid, v)

        sl.valueChanged.connect(mudou)
        b_mudo.clicked.connect(
            lambda _=False, s=sl: s.setValue(0 if s.value() > 0 else VOLUME_MAX))
        mudou(sl.value())

        self._faders[canal.id] = sl
        return caixa

    def _ouvir_canal(self, canal):
        """Fala o exemplo do canal NO VOLUME DELE — é o ponto da mesa."""
        volume = self.mix.volume_for(f"{canal.prefixes[0]}0",
                                     master=self.sl_vol.value())
        if volume is None:
            self._registrar(f"{canal.label}: mudo (não fala)", T.TXT_DIM)
            return
        self._falar(canal.sample, volume=volume)

    def _mesa_toda(self, valor: int):
        for sl in self._faders.values():
            sl.setValue(valor)
        self._registrar(f"Mesa toda em {valor}", T.TXT_DIM)

    def _aplicar_preset(self, niveis: dict, rotulo: str):
        for cid, sl in self._faders.items():
            sl.setValue(niveis.get(cid, VOLUME_MAX))
        self._registrar(f"Preset: {rotulo}", T.CH_SPEED)

    def _preset_coach(self):
        self._aplicar_preset({
            "coach_cue": 100, "coach_exit": 100, "corner": 100,
            "driving": 70, "electronics": 70,
            "pace": 40, "car": 0, "track": 0, "flags": 100,
        }, "só o coach de curva")

    def _preset_corrida(self):
        self._aplicar_preset({
            "coach_cue": 0, "coach_exit": 0, "corner": 0,
            "driving": 0, "electronics": 40,
            "pace": 60, "car": 100, "track": 60, "flags": 100,
        }, "modo corrida")

    def _slider(self, grade, linha, nome, minimo, maximo, valor, dica):
        rotulo = QLabel(nome)
        rotulo.setFont(T.f_label(9))
        rotulo.setStyleSheet(f"color: {T.TXT_LABEL};")
        grade.addWidget(rotulo, linha, 0)

        sl = QSlider(Qt.Horizontal)
        sl.setRange(minimo, maximo)
        sl.setValue(max(minimo, min(maximo, valor)))
        sl.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {T.BORDER};
            }}
            QSlider::handle:horizontal {{
                background: {T.CH_SPEED}; width: 12px; margin: -5px 0;
            }}
        """)
        grade.addWidget(sl, linha, 1)

        valor_lb = QLabel(str(sl.value()))
        valor_lb.setFont(T.f_value(10))
        valor_lb.setMinimumWidth(34)
        valor_lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grade.addWidget(valor_lb, linha, 2)

        if dica:
            d = QLabel(dica)
            d.setFont(T.f_label(8))
            d.setStyleSheet(f"color: {T.TXT_DIM};")
            grade.addWidget(d, linha, 3)

        sl.valueChanged.connect(lambda v: valor_lb.setText(str(v)))
        sl.valueChanged.connect(self._ajuste_mudou)
        return sl

    def _avaliar_tom(self, valor: int):
        """Acende o aviso quando o tom está deslocado a ponto de soar processado."""
        if abs(valor) < TOM_ARRISCADO:
            self.lb_aviso_tom.setText("")
            return
        self.lb_aviso_tom.setStyleSheet(f"color: {T.WARN};")
        self.lb_aviso_tom.setText(
            f"Tom {valor:+d}: o Windows reamostra a fala para deslocar o tom, "
            f"e isso deixa a voz com jeito eletrônico. Se incomodar, use "
            f"“Comparar tons” — e prefira uma voz naturalmente mais grave a "
            f"puxar esta para baixo.")

    def _carregar_vozes(self):
        """Enumera as vozes da máquina montando um motor uma vez."""
        engine = self._motor(rate=DEFAULT_RATE, pitch=DEFAULT_PITCH, voz="")
        self.combo_voz.blockSignals(True)
        self.combo_voz.addItem(VOZ_AUTOMATICA)
        for descricao in engine.installed_voices:
            self.combo_voz.addItem(descricao)
        escolhida = str(self.config.get("voice_name") or "")
        if escolhida:
            for i in range(self.combo_voz.count()):
                if escolhida.lower() in self.combo_voz.itemText(i).lower():
                    self.combo_voz.setCurrentIndex(i)
                    break
        self.combo_voz.blockSignals(False)

        if not engine.available:
            self._registrar("Nenhum sintetizador disponível nesta máquina. "
                            "O painel de texto do engenheiro funciona igual.",
                            T.BAD)
            self.lb_backend.setText("Sem sintetizador.")
        else:
            self._registrar(f"Voz em uso: {engine.voice_name}", T.TXT_DIM)
            self._mostrar_backend(engine)

    def _mostrar_backend(self, engine):
        """
        Diz qual motor está falando — e, no SAPI, qual é o teto dele.

        Ritmo e tom ajustam o SAPI até certo ponto; o que sobra de "sintético"
        é do motor, não do ajuste. Quem quiser passar disso precisa de outro
        motor, e é melhor saber disso aqui do que depois de meia hora movendo
        sliders atrás de um som que eles não produzem.
        """
        neural = any(getattr(b, "name", "") == "Kokoro"
                     for b in getattr(engine, "_backends", [])
                     if getattr(b, "available", False))
        if neural:
            self.lb_backend.setStyleSheet(f"color: {T.OK};")
            self.lb_backend.setText("Voz neural (Kokoro) ativa.")
            return
        self.lb_backend.setStyleSheet(f"color: {T.TXT_DIM};")
        self.lb_backend.setText(
            "Motor: SAPI do Windows. Ele tem um teto de naturalidade que "
            "nenhum slider ultrapassa — se o que incomoda é o timbre "
            "“eletrônico” e não o ritmo, a voz neural resolve: "
            "pip install kokoro-onnx numpy, e os arquivos kokoro-v1.0.onnx e "
            "voices-v1.0.bin em telemetry_data/models/ (o app usa sozinho).")

    # -- motor de voz -------------------------------------------------------

    def _voz_escolhida(self) -> str:
        texto = self.combo_voz.currentText()
        return "" if texto == VOZ_AUTOMATICA else texto

    def _motor(self, rate=None, pitch=None, voz=None) -> VoiceEngine:
        """
        O motor para os ajustes atuais, reaproveitado enquanto nada mudar.

        Recriar a cada frase custaria uns 200 ms de inicialização do COM sem
        necessidade; recriar SÓ quando o ajuste muda mantém a janela responsiva
        e ainda garante que o que se ouve é o que está nos sliders.
        """
        rate = self.sl_ritmo.value() if rate is None else rate
        pitch = self.sl_tom.value() if pitch is None else pitch
        voz = self._voz_escolhida() if voz is None else voz
        volume = self.sl_vol.value()
        chave = (voz, rate, pitch, volume)
        if self._engine is not None and self._chave == chave:
            return self._engine
        if self._engine is not None:
            self._engine.stop()
        self._engine = VoiceEngine(enabled=True, rate=rate, pitch=pitch,
                                   volume=volume, preferred_voice=voz)
        self._engine.wait_ready(15.0)
        self._chave = chave
        return self._engine

    def _ajuste_mudou(self):
        """Ajuste novo: o motor atual não serve mais."""
        self.lb_estado.setText(
            f"ritmo {self.sl_ritmo.value():+d} · tom {self.sl_tom.value():+d} "
            f"· volume {self.sl_vol.value()}")

    def _restaurar_padrao(self):
        self.sl_ritmo.setValue(DEFAULT_RATE)
        self.sl_tom.setValue(DEFAULT_PITCH)
        self.sl_vol.setValue(100)
        self.combo_voz.setCurrentIndex(0)
        self._registrar(f"Padrão do app: ritmo {DEFAULT_RATE:+d}, "
                        f"tom {DEFAULT_PITCH:+d}", T.TXT_DIM)

    # -- falar --------------------------------------------------------------

    def _falar_livre(self):
        texto = self.campo.text().strip()
        if texto:
            self._falar(texto)

    def _falar(self, texto: str, rate=None, volume=None, pitch=None):
        """
        Põe a frase na fila. Ela sai quando a anterior terminar.

        Enfileirar em vez de recusar é o que faz clicar numa frase enquanto
        outra está sendo dita funcionar — e é o que mantém a comparação de
        ritmos viva: ela é só três pedidos seguidos, e um pedido recusado no
        meio interromperia a sequência sem avisar ninguém.
        """
        if not (texto or "").strip():
            return
        self._fila.append((rate, texto, volume, pitch))
        self._bombear()

    def _bombear(self):
        """Fala o próximo da fila, se não houver nada em curso."""
        import time
        if self._inicio is not None or not self._fila:
            return
        rate, texto, volume, pitch = self._fila.pop(0)
        engine = self._motor(rate=rate, pitch=pitch)
        if not engine.available:
            self._fila.clear()
            self._registrar("Sem sintetizador — nada a ouvir.", T.BAD)
            return
        ritmo = self.sl_ritmo.value() if rate is None else rate
        tom = self.sl_tom.value() if pitch is None else pitch
        nivel = "" if volume is None else f" · vol {volume}"
        self._rotulo_atual = f"ritmo {ritmo:+d} · tom {tom:+d}{nivel} · {texto}"
        self._inicio = time.monotonic()
        self.lb_estado.setText("falando…")
        engine.say(texto, priority=PRIORITY_NORMAL, ttl=600.0, volume=volume)

    def _comparar_ritmos(self):
        """Mesma frase, três ritmos, um atrás do outro."""
        frase = self._frase_escolhida()
        self._registrar(f'Comparando ritmos: "{frase[:48]}…"', T.CH_SPEED)
        for ritmo in RITMOS_COMPARADOS:
            self._falar(frase, rate=ritmo)

    def _comparar_tons(self):
        """
        Mesma frase, mesmo ritmo, três tons.

        É o teste que separa "quero a voz mais grave" de "a voz está
        eletrônica": se o artefato some no tom 0 e volta no -4, o culpado é o
        deslocamento, e a saída é escolher uma voz naturalmente mais grave em
        vez de puxar esta para baixo.
        """
        frase = self._frase_escolhida()
        self._registrar(f'Comparando tons: "{frase[:50]}…"', T.CH_STEER)
        for tom in TONS_COMPARADOS:
            self._falar(frase, pitch=tom)

    def _frase_escolhida(self) -> str:
        item = self.lista.currentItem()
        return item.text() if item else FRASES[0]

    def _tick(self):
        """
        Vigia o fim da fala, para cronometrar sem travar a janela.

        `is_idle()` não bloqueia — quem espera aqui é o timer do Qt, e a
        interface continua desenhando entre uma checagem e outra.
        """
        if self._inicio is None or self._engine is None:
            return
        if not self._engine.is_idle():
            return
        import time
        duracao = time.monotonic() - self._inicio
        self._inicio = None
        longa = duracao > FRASE_LONGA_S
        marca = "   ← longa demais para um recado" if longa else ""
        self._registrar(f"{duracao:4.1f}s   {self._rotulo_atual}{marca}",
                        T.WARN if longa else T.TXT_VALUE)
        self.lb_estado.setText("pronto")
        if self._fila:
            # Meio segundo de respiro entre uma frase e a outra: encavaladas,
            # não dá para comparar duas leituras da mesma frase.
            QTimer.singleShot(500, self._bombear)

    # -- resultado ----------------------------------------------------------

    def _registrar(self, texto: str, cor: str = None):
        item = QListWidgetItem(texto)
        if cor:
            from PyQt5.QtGui import QColor
            item.setForeground(QColor(cor))
        self.log.insertItem(0, item)
        while self.log.count() > 60:
            self.log.takeItem(self.log.count() - 1)

    def _resumo(self) -> str:
        """
        Uma linha com tudo, para colar numa conversa.

        Da mesa só entra o que foi MEXIDO: uma linha com nove canais em 100
        não diz nada a quem lê, e o que interessa é justamente o que você
        baixou ou calou.
        """
        voz = self._voz_escolhida() or "(automática)"
        base = (f'voice_name: "{voz}" · voice_rate: {self.sl_ritmo.value()} · '
                f'voice_pitch: {self.sl_tom.value()} · '
                f'voice_volume: {self.sl_vol.value()}')
        mexidos = self.mix.to_dict()
        if not mexidos:
            return base + " · mesa: tudo no máximo"
        rotulos = {c.id: c.label for c in CHANNELS}
        partes = ", ".join(f"{rotulos.get(cid, cid)} {v}"
                           for cid, v in mexidos.items())
        return base + f" · mesa: {partes}"

    def _copiar_resumo(self):
        QApplication.clipboard().setText(self._resumo())
        self._registrar(f"Copiado: {self._resumo()}", T.OK)

    def _salvar(self):
        voz = self._voz_escolhida()
        self.config.update({
            "voice_name": voz,
            "voice_rate": self.sl_ritmo.value(),
            "voice_pitch": self.sl_tom.value(),
            "voice_volume": self.sl_vol.value(),
            "voice_mix": self.mix.to_dict(),
        })
        self._registrar("Salvo no config.json (voz + mesa). Reabra o dashboard "
                        "para valer.", T.OK)

    # -- ciclo de vida ------------------------------------------------------

    def closeEvent(self, event):
        self._timer.stop()
        if self._engine is not None:
            self._engine.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    janela = AjusteVoz()
    janela.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
