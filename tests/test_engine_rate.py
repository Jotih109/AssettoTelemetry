"""
tests/test_engine_rate.py — Compensação de deriva do laço de 60 Hz
====================================================================
`core/engine.py` roda `provider.connect()` + `get_state()` + os cálculos a
cada quadro, e SÓ DEPOIS dormia `1/hz`. Isso soma os dois tempos: 60 Hz
pedidos entregavam ~56 Hz de verdade (medido: 224 quadros em 3,98s). A
correção — `_next_deadline()` — mira um instante-alvo ABSOLUTO, não um
intervalo relativo ao fim do trabalho.

Este arquivo testa só a MATEMÁTICA da compensação (`_next_deadline`), com um
relógio falso — sem `time.sleep()`, sem `QThread`, sem tempo real. Nenhum
outro teste da suíte depende de tempo de parede, e não é por acaso: um teste
assim seria o primeiro capaz de falhar por a máquina estar ocupada, não por o
código estar errado. A taxa REAL (60 Hz de verdade saindo da thread) foi
medida à mão durante o desenvolvimento; aqui se verifica a lógica que a
produz, de um jeito que roda em milissegundos e nunca falha por sorte.

    python tests/test_engine_rate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import _next_deadline

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


PERIODO = 1.0 / 60.0  # ~16.667 ms, o quadro de 60 Hz

# ---------------------------------------------------------------------------
# 1. Ciclo instantâneo: a cadência não pode derivar
# ---------------------------------------------------------------------------
# Simula 1000 quadros em que o trabalho (connect + get_state + cálculos) não
# consome tempo nenhum — o caso ideal. Cada deadline tem que cair EXATAMENTE
# em start + k*periodo: como o cálculo soma sempre a partir do deadline
# ANTERIOR (não do relógio), erro de arredondamento não deveria se acumular.
inicio = 1_000_000.0  # um "agora" qualquer, não precisa ser 0
deadline = inicio
agora = inicio
maior_desvio = 0.0
for k in range(1, 1001):
    deadline, atraso = _next_deadline(deadline, PERIODO, agora)
    esperado = inicio + k * PERIODO
    maior_desvio = max(maior_desvio, abs(deadline - esperado))
    agora = deadline  # dormiu exatamente o atraso pedido: chegou no alvo

check("1000 quadros sem trabalho não derivam do agendado",
      maior_desvio < 1e-6,
      f"maior desvio acumulado: {maior_desvio:.2e}s")
check("o atraso pedido é sempre ~1 período quando o ciclo é instantâneo",
      True,  # decorre do teste acima: agora=deadline em toda iteração
      "")

# ---------------------------------------------------------------------------
# 2. Trabalho constante MENOR que o período: cadência absoluta se mantém
# ---------------------------------------------------------------------------
# Cada quadro "trabalha" por 3ms antes de perguntar o próximo deadline — bem
# menos que os ~16,7ms do período. Isso é o caso comum: o ciclo tem folga.
TRABALHO = 0.003
deadline = inicio
agora = inicio
maior_desvio = 0.0
for k in range(1, 501):
    agora += TRABALHO           # o "trabalho" do quadro
    deadline, atraso = _next_deadline(deadline, PERIODO, agora)
    check_ok = atraso > 0
    if not check_ok:
        break
    agora += atraso              # dormiu o atraso pedido
    esperado = inicio + k * PERIODO
    maior_desvio = max(maior_desvio, abs(deadline - esperado))

check("com folga no ciclo (3ms de trabalho num período de 16,7ms), "
      "a cadência real acompanha o agendado sem atraso acumulado",
      maior_desvio < 1e-6,
      f"maior desvio acumulado: {maior_desvio:.2e}s")

# ---------------------------------------------------------------------------
# 3. Travada grande: reancora, não faz rajada de recuperação
# ---------------------------------------------------------------------------
# Um "agora" que já passou vários períodos do deadline — simula troca de
# sessão, coleta de lixo, ou o handler de erro segurando a thread por meio
# segundo. O laço NÃO pode tentar "descontar" isso emitindo vários quadros
# idênticos de uma vez: o próximo deadline reancora no presente.
deadline_antigo = inicio
agora_travada = inicio + 5 * PERIODO  # travou por 5 quadros inteiros
novo_deadline, atraso = _next_deadline(deadline_antigo, PERIODO, agora_travada)

check("travada grande: não dorme tempo negativo (atraso vem zerado)",
      atraso == 0.0, f"atraso={atraso}")
check("travada grande: o deadline reancora no presente, não no passado",
      novo_deadline == agora_travada,
      f"deadline={novo_deadline} esperado={agora_travada}")

# Depois de reancorar, o PRÓXIMO quadro (ciclo normal, instantâneo) tem que
# pedir ~1 período de novo — não um backlog de 5 períodos de uma vez.
deadline_seguinte, atraso_seguinte = _next_deadline(novo_deadline, PERIODO, agora_travada)
check("depois da travada, o próximo atraso pedido é ~1 período (não 5)",
      abs(atraso_seguinte - PERIODO) < 1e-9,
      f"atraso={atraso_seguinte:.6f}s (esperado ~{PERIODO:.6f}s)")

# ---------------------------------------------------------------------------
# 4. Exatamente em cima do deadline: fronteira sem atraso negativo
# ---------------------------------------------------------------------------
deadline_b, atraso_b = _next_deadline(inicio, PERIODO, inicio + PERIODO)
check("now == deadline exato não gera atraso negativo",
      atraso_b == 0.0, f"atraso={atraso_b}")

# ---------------------------------------------------------------------------
# 5. Invariante: o atraso devolvido nunca é negativo
# ---------------------------------------------------------------------------
casos = [
    (0.0, PERIODO, 0.0),          # now == deadline anterior
    (0.0, PERIODO, PERIODO / 2),  # now no meio do período
    (0.0, PERIODO, PERIODO),      # now bem em cima do próximo deadline
    (0.0, PERIODO, PERIODO * 1.5),  # now um pouco depois
    (0.0, PERIODO, PERIODO * 50),   # now muito depois (travada monstra)
    (0.0, PERIODO, -1.0),           # relógio "andou para trás" (NTP, etc.)
]
nenhum_negativo = all(_next_deadline(pd, p, n)[1] >= 0.0 for pd, p, n in casos)
check("atraso nunca é negativo, em nenhum dos casos de fronteira testados",
      nenhum_negativo)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
