"""
tests/test_voice_queue.py — Fila de fala do engenheiro
======================================================
A fila é o que separa um spotter de um leitor de mensagens: ela decide o que
é dito, em que ordem, e o que NÃO chega a ser dito. Nada aqui toca a placa de
som — só a lógica, que é onde os erros doem.

Coberto aqui:
  * prioridade: crítico passa na frente do que já estava esperando
  * descarte: quando a fila enche, cai o mais antigo da menor prioridade
  * validade: recado velho não é falado atrasado (mas crítico nunca vence)
  * repetição: a mesma frase não entra duas vezes
  * pontuação das vozes do Windows

    python tests/test_voice_queue.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.voice import (
    MAX_AGE_S, PRIORITY_CRITICAL, PRIORITY_LOW, PRIORITY_NORMAL, VoiceEngine,
    _SpeechQueue,
)

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


def fila(maxsize=3):
    return _SpeechQueue(maxsize=maxsize)


# ---------------------------------------------------------------------------
# Ordem
# ---------------------------------------------------------------------------

def test_critico_fura_a_fila():
    q = fila()
    q.put("balanço da volta", PRIORITY_NORMAL, now=0.0)
    q.put("bandeira preta", PRIORITY_CRITICAL, now=1.0)
    assert q.get().text == "bandeira preta"
    assert q.get().text == "balanço da volta"
    return "crítico primeiro, mesmo chegando depois"


def test_ordem_de_chegada_dentro_da_prioridade():
    q = fila()
    q.put("primeira", PRIORITY_NORMAL, now=0.0)
    q.put("segunda", PRIORITY_NORMAL, now=1.0)
    assert [q.get().text, q.get().text] == ["primeira", "segunda"]
    return "empate na prioridade decide por chegada"


def test_peek_para_preempcao():
    """Quem está falando consulta isto para saber se deve cortar a frase."""
    q = fila()
    assert q.peek_priority() is None
    q.put("pneu esquentando", PRIORITY_NORMAL, now=0.0)
    assert q.peek_priority() == PRIORITY_NORMAL
    q.put("combustível acabando", PRIORITY_CRITICAL, now=1.0)
    assert q.peek_priority() == PRIORITY_CRITICAL
    return "a fila avisa quando chegou algo urgente"


# ---------------------------------------------------------------------------
# Descarte
# ---------------------------------------------------------------------------

def test_descarta_o_mais_antigo_da_menor_prioridade():
    q = fila(maxsize=2)
    q.put("elogio antigo", PRIORITY_LOW, now=0.0)
    q.put("elogio novo", PRIORITY_LOW, now=1.0)
    q.put("atenção", PRIORITY_NORMAL, now=2.0)
    ditos = [q.get().text, q.get().text]
    assert "elogio antigo" not in ditos, ditos
    assert ditos == ["atenção", "elogio novo"], ditos
    return "caiu o elogio mais velho, não o aviso"


def test_critico_nunca_e_descartado():
    q = fila(maxsize=2)
    q.put("bandeira preta", PRIORITY_CRITICAL, now=0.0)
    q.put("aviso 1", PRIORITY_NORMAL, now=1.0)
    q.put("aviso 2", PRIORITY_NORMAL, now=2.0)
    q.put("aviso 3", PRIORITY_NORMAL, now=3.0)
    assert q.get().text == "bandeira preta"
    return "o crítico sobreviveu a 3 avisos comuns"


def test_nao_duplica_a_mesma_frase():
    q = fila()
    q.put("ABS atuando forte", PRIORITY_NORMAL, now=0.0)
    q.put("ABS atuando forte", PRIORITY_NORMAL, now=0.1)
    assert q.get().text == "ABS atuando forte"
    assert q.peek_priority() is None, "entrou duas vezes"
    return "frase repetida entra uma vez só"


def test_frase_repetida_mais_urgente_e_promovida():
    """
    A mesma frase pode voltar mais grave: o pneu que estava "esquentando"
    passa a estar "superaquecido" com o MESMO texto na fila.

    Se o dedupe simplesmente descartasse a nova, o recado urgente ficaria
    valendo como recado comum — não cortaria a fala em andamento e ainda
    poderia ser descartado por idade.
    """
    q = fila()
    q.put("Pneu dianteiro esquerdo superaquecido", PRIORITY_LOW, now=0.0)
    q.put("Pneu dianteiro esquerdo superaquecido", PRIORITY_CRITICAL, now=1.0)

    assert q.peek_priority() == PRIORITY_CRITICAL, "não promoveu"
    fala = q.get()
    assert fala.priority == PRIORITY_CRITICAL, fala.priority
    assert q.peek_priority() is None, "entrou duas vezes"
    # E, promovida, não vence mais por idade
    assert not fala.is_stale(now=1.0 + MAX_AGE_S + 10.0)
    return "frase repetida sobe de prioridade em vez de ser engolida"


def test_frase_repetida_menos_urgente_nao_rebaixa():
    """O contrário não vale: crítico na fila não pode virar recado comum."""
    q = fila()
    q.put("Bandeira preta, entra nos boxes", PRIORITY_CRITICAL, now=0.0)
    q.put("Bandeira preta, entra nos boxes", PRIORITY_LOW, now=1.0)
    assert q.peek_priority() == PRIORITY_CRITICAL, "rebaixou o crítico"
    return "crítico na fila não é rebaixado por repetição"


# ---------------------------------------------------------------------------
# Validade
# ---------------------------------------------------------------------------

def test_recado_velho_perde_a_validade():
    """
    O piloto já passou da curva: falar agora é pior que não falar.
    """
    q = fila()
    q.put("curva 3, freou antes", PRIORITY_NORMAL, now=0.0)
    velho = q.get()
    assert velho.is_stale(now=MAX_AGE_S + 1.0), "recado antigo seguiu válido"
    assert not velho.is_stale(now=1.0), "descartou um recado fresco"
    return f"validade de {MAX_AGE_S:.0f}s respeitada"


def test_critico_nao_vence():
    q = fila()
    q.put("bandeira preta", PRIORITY_CRITICAL, now=0.0)
    assert not q.get().is_stale(now=9999.0), "descartou um recado crítico"
    return "crítico é dito mesmo se demorou"


# ---------------------------------------------------------------------------
# Encerramento
# ---------------------------------------------------------------------------

def test_fila_fechada_libera_a_thread():
    """`get()` bloqueia; sem isto o app não fecharia."""
    q = fila()
    q.put("qualquer coisa", PRIORITY_NORMAL, now=0.0)
    q.close()
    assert q.get() is None, "a fila fechada ainda entrega fala"
    q.put("depois de fechada", PRIORITY_NORMAL, now=1.0)
    assert q.get() is None, "aceitou fala depois de fechada"
    return "fechou, calou"


# ---------------------------------------------------------------------------
# Escolha da voz do Windows
# ---------------------------------------------------------------------------

def test_pontuacao_de_voz():
    """
    Português ganha de inglês (voz inglesa lendo português fica ilegível), e a
    OneCore ganha da "Desktop" — mesma locutora, geração de 2010.
    """
    nota = VoiceEngine.voice_score
    onecore_pt = nota("Microsoft Daniel - Portuguese (Brazil)")
    desktop_pt = nota("Microsoft Maria Desktop - Portuguese(Brazil)")
    desktop_en = nota("Microsoft Zira Desktop - English (United States)")
    onecore_en = nota("Microsoft Aria - English (United States)")

    assert onecore_pt > desktop_pt > desktop_en, (onecore_pt, desktop_pt, desktop_en)
    assert desktop_pt > onecore_en, "idioma tem que pesar mais que a geração"
    return f"pt OneCore={onecore_pt} > pt Desktop={desktop_pt} > en={onecore_en}"


def test_motor_desligado_nao_enfileira():
    """Com a voz desligada, `say()` não guarda nada para falar depois."""
    engine = VoiceEngine(enabled=False, backend="sapi")
    try:
        engine.say("não deve falar", PRIORITY_CRITICAL)
        assert engine._queue.peek_priority() is None, "enfileirou com a voz desligada"
    finally:
        engine.stop()
    assert not engine._thread.is_alive(), "a thread de voz não encerrou"
    return "desligada não enfileira e encerra limpo"


for nome, fn in [
    ("crítico fura a fila", test_critico_fura_a_fila),
    ("ordem de chegada dentro da prioridade", test_ordem_de_chegada_dentro_da_prioridade),
    ("a fila avisa que chegou algo urgente", test_peek_para_preempcao),
    ("descarta o mais antigo da menor prioridade", test_descarta_o_mais_antigo_da_menor_prioridade),
    ("crítico nunca é descartado", test_critico_nunca_e_descartado),
    ("não duplica a mesma frase", test_nao_duplica_a_mesma_frase),
    ("frase repetida mais urgente é promovida", test_frase_repetida_mais_urgente_e_promovida),
    ("frase repetida menos urgente não rebaixa", test_frase_repetida_menos_urgente_nao_rebaixa),
    ("recado velho perde a validade", test_recado_velho_perde_a_validade),
    ("crítico não vence por idade", test_critico_nao_vence),
    ("fila fechada libera a thread", test_fila_fechada_libera_a_thread),
    ("pontuação de escolha da voz", test_pontuacao_de_voz),
    ("voz desligada não enfileira", test_motor_desligado_nao_enfileira),
]:
    check(nome, fn)

print()
fails = [r for r in results if not r[1]]
for nome, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
