"""
run_tests.py — Roda a suíte inteira e devolve um código de saída honesto
========================================================================

    python run_tests.py              # tudo
    python run_tests.py -k corner    # só os arquivos cujo nome casa
    python run_tests.py -v           # mostra a saída de cada teste
    python run_tests.py --list       # só lista o que seria rodado

Por que um runner próprio, e não `pytest` ou `unittest discover`:

* `unittest discover` não enxerga esta pasta — `tests/` não é um pacote, e
  transformá-la em um faria os testes em formato de script (a maioria daqui)
  rodarem na hora do import, fora de qualquer controle.
* Cada arquivo de `tests/` já é um programa completo: roda sozinho, imprime o
  próprio placar e termina com código 0 ou 1. Isso é bom — dá para depurar um
  teste rodando só ele. O que faltava era alguém somar os placares.

Então este runner executa cada arquivo em um processo separado e junta o
resultado. Processo separado também isola o Qt: uma janela que trave ou um
`QApplication` já criado não contamina os arquivos seguintes.

O Qt roda em modo `offscreen`, então nada pisca na tela e a suíte funciona em
máquina sem monitor (CI incluído).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(ROOT, "tests")


def _console_utf8():
    """
    O console do Windows abre em cp1252, e aí acento e emoji viram lixo — ou
    pior, um `UnicodeEncodeError` no meio de um teste que tinha passado.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


_console_utf8()


def descobrir(padrao: str = "") -> list:
    """
    Arquivos de teste, em ordem alfabética, filtrados por `padrao`.

    Só `.py`. Um `.pyw` é, por definição, um programa de janela: em `tests/` o
    que tem essa extensão são bancadas para usar com a mão e o ouvido
    (`test_voice.pyw` abre a janela e fica em `app.exec_()`). Rodá-las aqui
    travaria a suíte para sempre, esperando alguém fechar a janela.
    """
    if not os.path.isdir(TESTS_DIR):
        return []
    nomes = sorted(
        n for n in os.listdir(TESTS_DIR)
        if n.startswith("test_") and n.endswith(".py")
    )
    if padrao:
        alvo = padrao.lower()
        nomes = [n for n in nomes if alvo in n.lower()]
    return nomes


def ambiente() -> dict:
    env = dict(os.environ)
    # Sem janela na tela: a suíte precisa rodar em máquina sem monitor.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Os testes imprimem acentos e emojis; o console do Windows é cp1252 e
    # estouraria em UnicodeEncodeError no meio de um teste que passou.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def rodar(nome: str, env: dict, verboso: bool):
    """`(ok, segundos, saida)` de um arquivo de teste."""
    inicio = time.monotonic()
    proc = subprocess.run(
        [sys.executable, os.path.join("tests", nome)],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    duracao = time.monotonic() - inicio
    saida = (proc.stdout or "") + (proc.stderr or "")
    if verboso:
        print(saida)
    return proc.returncode == 0, duracao, saida


def resumo_de(saida: str) -> str:
    """
    A última linha de placar que o teste imprimiu.

    Os arquivos usam dois formatos: `=== 36/36 verificacoes passaram ===` nos
    escritos como script, e `OK` / `FAILED (...)` nos de `unittest`.
    """
    for linha in reversed(saida.splitlines()):
        t = linha.strip()
        if t.startswith("===") and "verificac" in t:
            return t.strip("= ").strip()
        if t == "OK" or t.startswith("FAILED") or t.startswith("Ran "):
            return t
    # Alguns arquivos só usam `assert` e não imprimem placar nenhum. Passaram
    # se o processo saiu com 0, mas é honesto dizer que não há número aqui.
    return "(sem placar; validado pelo código de saída)"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description="Roda a suíte de testes do ApexView.")
    parser.add_argument("-k", dest="padrao", default="",
                        help="roda só os arquivos cujo nome contém este texto")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="mostra a saída completa de cada teste")
    parser.add_argument("--list", action="store_true",
                        help="lista os arquivos e sai, sem rodar nada")
    args = parser.parse_args(argv)

    nomes = descobrir(args.padrao)
    if not nomes:
        alvo = f" para '{args.padrao}'" if args.padrao else ""
        print(f"Nenhum teste encontrado{alvo} em {TESTS_DIR}")
        return 1

    if args.list:
        for n in nomes:
            print(n)
        return 0

    env = ambiente()
    largura = max(len(n) for n in nomes)
    falhados = []
    inicio = time.monotonic()

    print(f"Rodando {len(nomes)} arquivo(s) de teste\n")
    for nome in nomes:
        print(f"  {nome:<{largura}} ", end="", flush=True)
        ok, duracao, saida = rodar(nome, env, args.verbose)
        placar = resumo_de(saida)
        marca = "ok  " if ok else "FALHOU"
        print(f"{marca} {duracao:5.1f}s  {placar}")
        if not ok:
            falhados.append((nome, saida))

    total = time.monotonic() - inicio
    print()
    if falhados:
        for nome, saida in falhados:
            print("=" * 72)
            print(f"FALHOU: {nome}")
            print("=" * 72)
            # Sem -v a saída já foi engolida; aqui ela importa.
            if not args.verbose:
                print(saida.rstrip()[-4000:])
            print()
        print(f"{len(falhados)} de {len(nomes)} arquivo(s) FALHARAM  ({total:.1f}s)")
        return 1

    print(f"Todos os {len(nomes)} arquivos passaram  ({total:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
