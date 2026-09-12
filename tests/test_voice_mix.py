"""
tests/test_voice_mix.py — Mesa de som do engenheiro
====================================================
A mesa decide o VOLUME de cada assunto — e, no zero, se ele chega a ser
falado. Um erro aqui é silencioso por definição: o recado simplesmente não
sai, e ninguém descobre até perder uma corrida por não ouvir a bandeira.

Coberto aqui:
  * toda chave de recado do engenheiro cai num canal (nenhuma fica órfã)
  * o desempate é pelo prefixo mais longo, não pelo primeiro que servir
  * canal no zero não fala; canal parcial multiplica o volume geral
  * canal desconhecido nasce no máximo, nunca mudo
  * o que vai para o disco é só o que foi mexido

    python tests/test_voice_mix.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.voice_mix import (CHANNELS, DEFAULT_CHANNEL_ID, VOLUME_MAX, VoiceMix,
                            channel_for)

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except Exception:
        results.append((name, False,
                        traceback.format_exc(limit=4).strip().splitlines()[-1]))


#: Toda chave que o engenheiro e o coach realmente emitem hoje.
#:
#: A lista é conferida contra o código-fonte no teste `todas_as_chaves_reais`:
#: uma regra nova que ninguém classificou tem de aparecer aqui como falha, e
#: não virar um recado que nunca fala.
CHAVES_REAIS = [
    "coach_cue:5", "coach_exit:5", "coach_ok:5", "coach_summary",
    "corner:3", "corner_ok:2",
    "lap:abs", "lap:tc", "abs", "tc",
    "lap:overlap", "lap:brake_jitter", "lap:brake_abrupt", "lap:understeer",
    "lap:steer_rough", "lap:steer_smooth", "lap:shift_early", "lap:limiter",
    "limiter",
    "lap:melhor", "lap:pior", "lap:sector:1", "lap:sector_forte:2",
    "lap:sector_ok", "lap:consistencia", "lap:consistencia_ok",
    "delta", "sector:1", "best_lap", "last_lap:20",
    "lap:fuel_high", "lap:fuel_race", "fuel", "fuel_ok",
    "tyre_hot:0", "tyre_cold", "tyre_cold:1", "brake_hot", "damage",
    "track_temp", "grip", "wind",
    "flag:AMARELA", "penalty", "cut",
]


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

def test_toda_chave_cai_num_canal():
    """Nenhuma chave real fica órfã, e nenhuma cai no canal de sobra."""
    ids = {c.id for c in CHANNELS}
    orfas = []
    for chave in CHAVES_REAIS:
        canal = channel_for(chave)
        assert canal.id in ids, f"{chave} -> canal inexistente {canal.id}"
        # Cair no canal padrão significa que ninguém classificou a chave
        casou = any(chave.startswith(p) for p in canal.prefixes)
        if not casou:
            orfas.append(chave)
    assert not orfas, f"chaves sem canal próprio: {orfas}"
    return f"{len(CHAVES_REAIS)} chaves classificadas em {len(ids)} canais"


def test_desempate_pelo_prefixo_mais_longo():
    """
    "lap:fuel_high" é combustível, não ritmo.

    Vários canais listam prefixos que começam com `lap:`. Casar pelo primeiro
    que servir jogaria o aviso de consumo no canal de tempo — e quem
    silenciasse "tempo e setor" perderia o aviso de combustível sem entender
    por quê.
    """
    assert channel_for("lap:fuel_high").id == "car", channel_for("lap:fuel_high").id
    assert channel_for("lap:fuel_race").id == "car"
    assert channel_for("lap:melhor").id == "pace"
    assert channel_for("lap:abs").id == "electronics"
    assert channel_for("lap:brake_jitter").id == "driving"
    # "brake_hot" (freio quente) é carro; "lap:brake_*" (pedal) é pilotagem
    assert channel_for("brake_hot").id == "car", channel_for("brake_hot").id
    return "lap:fuel_high->car, lap:brake_jitter->driving, brake_hot->car"


def test_chave_desconhecida_nasce_audivel():
    """
    Regra nova sem classificação é FALADA, não silenciada.

    O contrário seria o pior dos mundos: alguém adiciona um aviso no
    engenheiro, ele nunca fala, e não há erro nenhum para investigar.
    """
    canal = channel_for("regra_que_ninguem_classificou")
    assert canal.id == DEFAULT_CHANNEL_ID
    mix = VoiceMix()
    assert mix.volume_for("regra_que_ninguem_classificou", master=100) == 100
    return f"cai em '{canal.label}', no volume cheio"


def test_canal_vazio_ou_nulo():
    """Chave vazia não estoura."""
    assert channel_for("").id == DEFAULT_CHANNEL_ID
    assert channel_for(None).id == DEFAULT_CHANNEL_ID
    return "chave vazia cai no canal padrão"


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def test_canal_no_zero_nao_fala():
    """Zero devolve None — o chamador não deve enfileirar nada."""
    mix = VoiceMix()
    mix.set_level("car", 0)
    assert mix.is_muted("car")
    assert mix.volume_for("fuel", master=100) is None
    assert mix.volume_for("tyre_hot:0", master=100) is None
    # e os outros canais seguem intactos
    assert mix.volume_for("coach_cue:5", master=100) == 100
    return "canal mudo devolve None; os vizinhos não mudam"


def test_canal_multiplica_o_volume_geral():
    """
    O canal é uma fração do volume geral, não um valor absoluto.

    Baixar o volume da sessão tem de baixar tudo; um canal a 50% continua
    sendo "metade do que estiver valendo".
    """
    mix = VoiceMix()
    mix.set_level("track", 50)
    assert mix.volume_for("wind", master=100) == 50
    assert mix.volume_for("wind", master=80) == 40
    assert mix.volume_for("wind", master=0) == 1     # nunca zera por engano
    return "50% de 100 = 50; 50% de 80 = 40"


def test_volume_nunca_vira_zero_por_arredondamento():
    """
    Um canal audível não pode emudecer por conta da conta.

    Zero tem UM significado — "não fale" — e ele é escolha do piloto no
    fader, não efeito colateral de 1% de 1%.
    """
    mix = VoiceMix()
    mix.set_level("car", 1)
    assert mix.volume_for("fuel", master=1) == 1
    return "1% de 1% ainda fala (no mínimo)"


def test_niveis_fora_da_faixa_sao_contidos():
    mix = VoiceMix()
    mix.set_level("car", 500)
    mix.set_level("track", -30)
    assert mix.level("car") == VOLUME_MAX
    assert mix.level("track") == 0
    return "500 -> 100, -30 -> 0"


def test_nivel_corrompido_cai_no_maximo():
    """Config editado à mão com texto no lugar de número não pode calar nada."""
    mix = VoiceMix(levels={"car": "alto", "track": None})
    assert mix.level("car") == VOLUME_MAX
    assert mix.level("track") == VOLUME_MAX
    return "valor inválido vale cheio"


# ---------------------------------------------------------------------------
# Disco
# ---------------------------------------------------------------------------

def test_grava_so_o_que_foi_mexido():
    """Mesa no padrão não polui o config.json."""
    mix = VoiceMix()
    assert mix.to_dict() == {}
    mix.set_level("car", 0)
    mix.set_level("track", 40)
    mix.set_level("pace", VOLUME_MAX)          # mexido e devolvido ao cheio
    assert mix.to_dict() == {"car": 0, "track": 40}, mix.to_dict()
    return "só car e track vão para o disco"


def test_ida_e_volta_pelo_disco():
    mix = VoiceMix()
    mix.set_level("coach_cue", 30)
    mix.set_level("flags", 0)
    de_volta = VoiceMix.from_dict(mix.to_dict())
    assert de_volta.level("coach_cue") == 30
    assert de_volta.is_muted("flags")
    assert de_volta.level("car") == VOLUME_MAX
    return "gravou e releu igual"


def test_canal_desconhecido_no_arquivo_e_ignorado():
    """
    Config de uma versão futura (ou canal removido) não derruba a mesa.

    E, principalmente: um canal que NÃO está no arquivo vale cheio. Uma mesa
    gravada antes de um canal existir não pode silenciá-lo por omissão.
    """
    mix = VoiceMix.from_dict({"canal_que_nao_existe": 0, "car": 20})
    assert mix.level("car") == 20
    assert "canal_que_nao_existe" not in mix.to_dict()
    assert mix.level("coach_cue") == VOLUME_MAX
    return "canal estranho ignorado; canal ausente vale cheio"


def test_from_dict_aceita_lixo():
    for lixo in (None, [], "texto", 42):
        assert VoiceMix.from_dict(lixo).to_dict() == {}
    return "lixo no config vira mesa padrão"


# ---------------------------------------------------------------------------
# Coerência com o código de verdade
# ---------------------------------------------------------------------------

def test_todas_as_chaves_reais_estao_na_lista():
    """
    As chaves emitidas pelo engenheiro estão todas cobertas por este teste.

    Lê o código-fonte e compara. Sem isto, uma regra nova passaria despercebida
    até alguém notar que ela nunca fala — que é o tipo de defeito que só
    aparece numa corrida.
    """
    import re
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    encontradas = set()
    for nome in ("core/race_engineer.py", "core/live_coach.py"):
        texto = open(os.path.join(raiz, nome), encoding="utf-8").read()
        # Captura a chave inteira, parando na aspa OU no início de um
        # placeholder de f-string: `key=f"coach_cue:{corner.index}"` vale como
        # "coach_cue:", que é o prefixo que a mesa realmente classifica.
        # Parar antes dos dois-pontos daria "coach_cue", que não casa com o
        # prefixo e faria o teste acusar erro onde não há.
        for m in re.finditer(r'(?:key=|add\()f?"([^"{]+)', texto):
            chave = m.group(1).strip()
            if chave:
                encontradas.add(chave)

    prefixos_testados = {c.split(":")[0] for c in CHAVES_REAIS}
    faltando = sorted(k for k in encontradas
                      if k.split(":")[0] not in prefixos_testados)
    assert not faltando, f"chaves não cobertas por este teste: {faltando}"

    # E nenhuma delas pode cair no canal de sobra sem ter sido classificada
    sem_canal = []
    for chave in encontradas:
        canal = channel_for(chave)
        if not any(chave.startswith(p) for p in canal.prefixes):
            sem_canal.append(chave)
    assert not sem_canal, f"chaves sem canal: {sem_canal}"
    return f"{len(encontradas)} chaves lidas do código, todas classificadas"


for nome, fn in [
    ("toda chave cai num canal", test_toda_chave_cai_num_canal),
    ("desempate pelo prefixo mais longo", test_desempate_pelo_prefixo_mais_longo),
    ("chave desconhecida nasce audível", test_chave_desconhecida_nasce_audivel),
    ("chave vazia não estoura", test_canal_vazio_ou_nulo),
    ("canal no zero não fala", test_canal_no_zero_nao_fala),
    ("canal multiplica o volume geral", test_canal_multiplica_o_volume_geral),
    ("volume nunca zera por arredondamento",
     test_volume_nunca_vira_zero_por_arredondamento),
    ("níveis fora da faixa são contidos", test_niveis_fora_da_faixa_sao_contidos),
    ("nível corrompido cai no máximo", test_nivel_corrompido_cai_no_maximo),
    ("grava só o que foi mexido", test_grava_so_o_que_foi_mexido),
    ("ida e volta pelo disco", test_ida_e_volta_pelo_disco),
    ("canal desconhecido no arquivo é ignorado",
     test_canal_desconhecido_no_arquivo_e_ignorado),
    ("from_dict aceita lixo", test_from_dict_aceita_lixo),
    ("as chaves reais do código estão cobertas",
     test_todas_as_chaves_reais_estao_na_lista),
]:
    check(nome, fn)

print()
fails = [r for r in results if not r[1]]
for nome, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
