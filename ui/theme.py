"""
ui/theme.py — Paleta e helpers visuais no estilo MoTeC i2
========================================================
O i2 tem uma linguagem visual bem específica, e é ela que este módulo
centraliza para que todos os widgets fiquem coerentes:

  * Painéis achatados: canto reto, borda de 1 px, sem sombra, sem gradiente
  * Cinzas frios e escuros; a cor é reservada para os DADOS, não para a moldura
  * Títulos pequenos em maiúsculas; valores numéricos grandes e alinhados à direita
  * Densidade alta: linha de canal com ~16 px, quase sem espaçamento morto
  * Números sempre em fonte monoespaçada, para as casas decimais não "dançarem"

Use `panel_qss()` para a moldura, `channel_row()` para uma linha
"NOME .... VALOR un" e as constantes de cor para qualquer coisa dinâmica.
"""

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

# ---------------------------------------------------------------------------
# Cores
# ---------------------------------------------------------------------------

BG_APP      = "#0d0f11"   # Fundo da janela
BG_PANEL    = "#15181b"   # Fundo dos painéis/cards
BG_INSET    = "#0f1113"   # Fundo de campos "afundados" (valores, tabelas)
BG_HEADER   = "#1c2024"   # Faixa de título dos painéis

BORDER      = "#2b3137"   # Borda dos painéis
BORDER_SOFT = "#21262a"   # Divisórias internas
GRID        = "#252b30"   # Grade dos gráficos

TXT_TITLE   = "#7d8b96"   # Títulos de painel (maiúsculas)
TXT_LABEL   = "#8c9aa5"   # Nome do canal
TXT_VALUE   = "#e8edf2"   # Valor numérico
TXT_UNIT    = "#5f6b74"   # Unidade
TXT_DIM     = "#4d5761"   # Texto secundário/apagado

# Cores de canal — mesma lógica do i2: um canal, uma cor, em todo o app
CH_SPEED    = "#4da3ff"
CH_THROTTLE = "#3ddc84"
CH_BRAKE    = "#ff4d4d"
CH_RPM      = "#b07cff"
CH_STEER    = "#ffd23f"
CH_GFORCE   = "#00d0ff"

# Semântica
OK          = "#3ddc84"
WARN        = "#ffb340"
BAD         = "#ff4d4d"
COLD        = "#4d8dff"
PURPLE      = "#c07cff"   # Recorde (roxo, como nos softwares de cronometragem)

# ---------------------------------------------------------------------------
# Fontes
# ---------------------------------------------------------------------------

# Rótulos: sans condensada (o i2 usa Tahoma/Arial pequeno)
FONT_UI = "Segoe UI"
# Números: monoespaçada, para as colunas ficarem alinhadas
FONT_MONO = "Consolas"


def f_title(size=8):
    """Título de painel: pequeno, maiúsculas, com espaçamento entre letras."""
    f = QFont(FONT_UI, size, QFont.DemiBold)
    f.setLetterSpacing(QFont.PercentageSpacing, 108)
    return f


def f_label(size=8):
    """Nome de canal."""
    return QFont(FONT_UI, size)


def f_value(size=10, bold=True):
    """Valor numérico."""
    return QFont(FONT_MONO, size, QFont.Bold if bold else QFont.Normal)


def f_big(size=22):
    """Leitura grande (marcha, velocidade, RPM, delta)."""
    return QFont(FONT_MONO, size, QFont.Bold)


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------

def panel_qss(bg=BG_PANEL, border=BORDER) -> str:
    """Moldura padrão de painel: canto reto e borda de 1 px."""
    return f"background-color: {bg}; border: 1px solid {border}; border-radius: 0px;"


def inset_qss(border=BORDER_SOFT) -> str:
    """Campo afundado, para destacar um valor dentro do painel."""
    return f"background-color: {BG_INSET}; border: 1px solid {border}; border-radius: 0px;"


def app_qss() -> str:
    """Folha de estilo global da janela."""
    return f"""
        QMainWindow {{ background-color: {BG_APP}; }}
        QWidget {{
            background-color: {BG_APP};
            color: {TXT_VALUE};
            font-family: "{FONT_UI}";
        }}
        QToolTip {{
            background-color: {BG_PANEL}; color: {TXT_VALUE};
            border: 1px solid {BORDER}; padding: 3px;
        }}
        QScrollBar:vertical {{
            background: {BG_APP}; width: 9px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {BORDER}; min-height: 24px; border-radius: 0px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #3a4249; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


# ---------------------------------------------------------------------------
# Blocos reutilizáveis
# ---------------------------------------------------------------------------

class Panel(QFrame):
    """
    Painel no estilo i2: faixa de título em maiúsculas e corpo abaixo.

    Adicione conteúdo em `self.body` (um QVBoxLayout já configurado com
    margens apertadas).
    """

    def __init__(self, title: str = None, body_margins=(7, 5, 7, 6), spacing=3):
        super().__init__()
        self.setStyleSheet(panel_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if title:
            self.lbl_title = QLabel(title.upper())
            self.lbl_title.setFont(f_title())
            self.lbl_title.setStyleSheet(
                f"color: {TXT_TITLE}; background-color: {BG_HEADER};"
                f"border: none; border-bottom: 1px solid {BORDER};"
                "padding: 3px 7px;"
            )
            outer.addWidget(self.lbl_title)

        body_host = QFrame()
        body_host.setStyleSheet("background: transparent; border: none;")
        self.body = QVBoxLayout(body_host)
        self.body.setContentsMargins(*body_margins)
        self.body.setSpacing(spacing)
        outer.addWidget(body_host)

        # Compatibilidade com o código antigo, que usava `main_layout`
        self.main_layout = self.body


def channel_row(name: str, value: str = "--", unit: str = "",
                value_size=9, label_size=8):
    """
    Uma linha de canal: NOME à esquerda, VALOR (+unidade) à direita.

    Retorna (widget, lbl_value). Guarde o `lbl_value` para atualizar depois.
    Formato usado em todos os painéis numéricos, como nas tabelas do i2.
    """
    row = QFrame()
    row.setStyleSheet("background: transparent; border: none;")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    lbl_name = QLabel(name)
    lbl_name.setFont(f_label(label_size))
    lbl_name.setStyleSheet(f"color: {TXT_LABEL}; background: transparent; border: none;")

    lbl_val = QLabel(value)
    lbl_val.setFont(f_value(value_size))
    lbl_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl_val.setStyleSheet(f"color: {TXT_VALUE}; background: transparent; border: none;")

    lay.addWidget(lbl_name)
    lay.addStretch()
    lay.addWidget(lbl_val)

    if unit:
        lbl_unit = QLabel(unit)
        lbl_unit.setFont(f_label(label_size - 1))
        lbl_unit.setStyleSheet(f"color: {TXT_UNIT}; background: transparent; border: none;")
        lbl_unit.setFixedWidth(28)
        lay.addWidget(lbl_unit)

    row.lbl_value = lbl_val
    row.lbl_name = lbl_name
    return row, lbl_val


def separator() -> QFrame:
    """Linha divisória horizontal de 1 px."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {BORDER_SOFT}; border: none;")
    return line


def temp_color(temp: float, cold=70.0, hot=105.0, critical=120.0) -> str:
    """Cor de temperatura de pneu (azul frio → verde ideal → laranja → vermelho)."""
    if temp < cold:
        return COLD
    if temp >= critical:
        return BAD
    if temp >= hot:
        return WARN
    return OK


def brake_temp_color(temp: float) -> str:
    """Cor de temperatura de freio (faixa de trabalho típica: 350–650 °C)."""
    if temp < 200:
        return COLD
    if temp > 800:
        return BAD
    if temp > 650:
        return WARN
    return OK
