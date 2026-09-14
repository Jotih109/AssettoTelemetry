"""
main2.pyw — Estação de Análise de Telemetria Ponto a Ponto (MoTeC Style Telemetry Studio)
=======================================================================================

Ponto de entrada dedicado para análise detalhada de telemetria offline e pós-sessão,
focado na experiência de engenharia de corrida real (estilo MoTeC i2 Pro, VRS, Popometer):

  * Traçado 2D Interativo: veja a linha exata percorrida pelo carro na pista.
  * Heatmap de Frenagem e Aceleração: identifique visualmente onde você começou a frear,
    onde fez trail braking aliviando o pedal na entrada, e onde retomou acelerador pleno.
  * Marcadores de Curvas e Ápices: pontos de início de frenagem com velocidade (km/h)
    e velocidade mínima no ápice marcados diretamente sobre a pista.
  * Inspeção Ponto a Ponto Sincronizada: passe o mouse ou clique no traçado ou gráficos
    para inspecionar qualquer metro da volta em tempo real.
  * Círculo de Atrito G-G (Friction Circle): veja o aproveitamento do limite de aderência.
  * HUD de Telemetria: velocímetro digital, barras de pedais, ângulo de volante, marcha, RPM.
  * Replay com Velocidade Variável: assista à volta se desenhar na pista com play/pause e scrubber.
  * Comparação com Volta Rápida: sobreponha duas voltas com dois carros na pista e deltas de tempo.

Como executar:
    python main2.pyw
"""

import argparse
import os
import sys
import traceback
from PyQt5.QtWidgets import QApplication

from core.lap_library import LapLibrary, RetentionPolicy
from ui.telemetry_studio import TelemetryStudioWindow


def _install_crash_guard():
    """Garante que exceções não tratadas sejam registradas no console sem abortar bruscamente."""
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        print("[!] Erro não tratado na Telemetria:")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def main():
    _install_crash_guard()

    parser = argparse.ArgumentParser(
        prog="main2.pyw", add_help=True,
        description="ApexView Telemetry Studio — Análise Ponto a Ponto de Telemetria"
    )
    parser.add_argument("--demo", action="store_true", help="Inicia carregando a volta de demonstração")
    args, _ = parser.parse_known_args()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    # Inicializa a biblioteca de voltas (sem retenção agressiva no modo de leitura)
    library = LapLibrary(retention=RetentionPolicy(enabled=False))

    window = TelemetryStudioWindow(library)

    if args.demo:
        window.load_demo_session()

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
