"""
tests/test_race_engineer.py — Engenheiro de pista (regras)
==========================================================
As regras são o coração da funcionalidade: se elas falarem bobagem, a voz vai
repetir a bobagem em voz alta. Cada teste monta uma situação onde a resposta
certa é conhecida e cobra o conselho correto — e, tão importante quanto, cobra
SILÊNCIO quando não há nada a dizer.

Coberto aqui:
  * diagnóstico da curva (freou antes / entrou devagar / demorou a acelerar)
  * prioridade: a curva onde mais se perdeu vem primeiro
  * tempo de espera: o mesmo aviso não se repete a cada quadro
  * avisos ao vivo (ABS, TC, pneu, freio, combustível, bandeira, penalidade)
  * consistência entre voltas
  * nada de conselho quando a volta está boa

    python tests/test_race_engineer.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.corner_analysis as ca
from core.models import TelemetryState
from core.race_engineer import RaceEngineer, INFO, ATTENTION, CRITICAL

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


# ---------------------------------------------------------------------------
# Fábricas
# ---------------------------------------------------------------------------

def corner(index=1, name="C1", start=0.1, end=0.2):
    return ca.Corner(index=index, name=name, start=start, end=end)


def comparison(index=1, name="C1", delta_t=None, d_vmin=None, d_brake=None,
               d_throttle=None, v_min=95.0):
    """
    Monta um CornerComparison com os deltas que o teste quer.

    Os deltas são propriedades calculadas de lap x ref, então os valores são
    montados nas duas pontas para que a subtração dê o pedido.
    """
    c = corner(index, name)
    lap = ca.CornerMetrics(corner=c, v_min=v_min, v_min_m=100.0,
                           braking_point_m=500.0, throttle_point_m=700.0,
                           entry_time=10.0,
                           exit_time=10.0 + (delta_t if delta_t else 0.0) + 3.0)
    ref = ca.CornerMetrics(
        corner=c,
        v_min=(v_min - d_vmin) if d_vmin is not None else None,
        v_min_m=100.0,
        braking_point_m=(500.0 - d_brake) if d_brake is not None else None,
        throttle_point_m=(700.0 - d_throttle) if d_throttle is not None else None,
        entry_time=10.0, exit_time=13.0)
    return ca.CornerComparison(corner=c, lap=lap, ref=ref)


def state(**kw):
    st = TelemetryState(is_connected=True, track_name="Spa", car_name="911")
    st.tyre_temp = [85.0] * 4
    st.brake_temp = [400.0] * 4
    st.tyre_temp_inner = [85.0] * 4
    st.tyre_temp_outer = [85.0] * 4
    st.ffb_level = 0.5
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def textos(advices):
    return " || ".join(a.text for a in advices)


# ---------------------------------------------------------------------------
# Diagnóstico de curva
# ---------------------------------------------------------------------------

def test_freou_antes():
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.30, d_brake=-20.0)])
    alvo = [a for a in adv if a.corner == 1]
    assert alvo, "nenhum conselho para a curva"
    t = alvo[0].text
    assert t.startswith("C1:"), t              # diz QUAL curva
    assert "perdeu 0.30 segundos" in t, t      # quanto custou
    assert "20 metros antes" in t, t           # o que aconteceu
    assert "Atrase a freada" in t, t           # o que fazer
    assert alvo[0].severity == ATTENTION, alvo[0].severity
    return t


def test_entrou_devagar():
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.25, d_vmin=-6.0, v_min=88.0)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "6 por hora mais devagar" in t, t
    assert "freio" in t.lower(), t
    return t


def test_demorou_a_acelerar():
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.20, d_throttle=35.0)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "35 metros depois" in t, t
    return t


def test_perda_sem_causa_identificada():
    """Perdeu tempo mas nenhuma métrica explica: diz a perda e não invent nada."""
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.12)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "perdeu 0.12 segundos" in t, t
    assert "freada" not in t and "acelerador" not in t, t
    return t


def test_prioridade_por_tempo_perdido():
    eng = RaceEngineer()
    adv = eng.analyze_lap([
        comparison(1, "C1", delta_t=0.05),
        comparison(2, "C2", delta_t=0.45, d_brake=-15.0),
        comparison(3, "C3", delta_t=0.20, d_vmin=-4.0),
    ])
    curvas = [a.corner for a in adv if a.corner is not None and a.time_at_stake > 0]
    assert curvas[0] == 2, f"ordem: {curvas}"
    return f"ordem das curvas: {curvas}"


def test_limita_quantidade_de_curvas():
    eng = RaceEngineer()
    muitas = [comparison(i, f"C{i}", delta_t=0.10 + i * 0.01) for i in range(1, 9)]
    adv = eng.analyze_lap(muitas)
    perdas = [a for a in adv if a.key.startswith("corner:")]
    assert len(perdas) <= 3, f"{len(perdas)} conselhos de curva"
    return f"{len(perdas)} curvas comentadas de 8"


def test_reforco_positivo():
    eng = RaceEngineer()
    adv = eng.analyze_lap([
        comparison(1, "C1", delta_t=0.30, d_brake=-12.0),
        comparison(2, "Eau Rouge", delta_t=-0.22),
    ])
    bons = [a for a in adv if a.key.startswith("corner_ok:")]
    assert bons, textos(adv)
    assert "Eau Rouge" in bons[0].text and "Mantenha" in bons[0].text
    return bons[0].text


def test_volta_boa_nao_gera_critica():
    """Volta melhor que a referência em tudo: nada de conselho de curva."""
    eng = RaceEngineer()
    adv = eng.analyze_lap(
        [comparison(1, "C1", delta_t=-0.10), comparison(2, "C2", delta_t=-0.05)],
        lap_time_str="1:29.100", lap_delta_s=-0.15)
    perdas = [a for a in adv if a.key.startswith("corner:")]
    assert not perdas, textos(adv)
    assert any("Boa volta" in a.text for a in adv), textos(adv)
    return textos(adv)[:70]


def test_ruido_ignorado():
    """Diferença de milésimos não vira conselho."""
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(1, "C1", delta_t=0.01)])
    assert not [a for a in adv if a.key.startswith("corner:")], textos(adv)
    return "0.01s ignorado"


def test_sem_referencia_nao_fala_de_curva():
    """Sem volta de referência os deltas são None: nada a dizer sobre curvas."""
    eng = RaceEngineer()
    c = corner()
    sem_ref = ca.CornerComparison(
        corner=c,
        lap=ca.CornerMetrics(corner=c, v_min=90.0, entry_time=1.0, exit_time=4.0),
        ref=None)
    adv = eng.analyze_lap([sem_ref])
    assert not [a for a in adv if a.corner is not None], textos(adv)
    return "silêncio sem referência"


# ---------------------------------------------------------------------------
# ABS / TC ao longo da volta
# ---------------------------------------------------------------------------

def test_abs_vicio_de_freada():
    eng = RaceEngineer()
    n = 200
    telem = {
        "abs_intervention": [0.9 if i % 10 == 0 else 0.0 for i in range(n)],
        "tc_intervention": [0.0] * n,
        "distance": [i * 35.0 for i in range(n)],
    }
    adv = eng.analyze_lap([], lap_telemetry=telem)
    abs_adv = [a for a in adv if a.key == "lap:abs"]
    assert abs_adv, textos(adv)
    assert "travando a roda" in abs_adv[0].text
    return abs_adv[0].detail


def test_abs_pontual_nao_vira_vicio():
    """Uma travada só na volta inteira não é vício de pilotagem."""
    eng = RaceEngineer()
    n = 200
    telem = {
        "abs_intervention": [0.9 if i == 50 else 0.0 for i in range(n)],
        "tc_intervention": [0.0] * n,
        "distance": [i * 35.0 for i in range(n)],
    }
    adv = eng.analyze_lap([], lap_telemetry=telem)
    assert not [a for a in adv if a.key == "lap:abs"], textos(adv)
    return "1 travada em 200 pontos: silêncio"


def test_tc_localiza_a_curva():
    """O aviso de TC diz em qual curva foi o pior corte."""
    eng = RaceEngineer()
    n = 200
    # Pico de TC dentro da faixa da curva "Pouhon" (55%..65% de 7000 m)
    telem = {
        "tc_intervention": [0.9 if 110 <= i <= 128 else 0.0 for i in range(n)],
        "abs_intervention": [0.0] * n,
        "distance": [i * 35.0 for i in range(n)],
    }
    comps = [comparison(1, "La Source"), comparison(2, "Pouhon")]
    comps[1].corner.start, comps[1].corner.end = 0.55, 0.65
    adv = eng.analyze_lap(comps, lap_telemetry=telem)
    tc = [a for a in adv if a.key == "lap:tc"]
    assert tc, textos(adv)
    assert "Pouhon" in tc[0].text, tc[0].text
    return tc[0].text


# ---------------------------------------------------------------------------
# Carro (pneus, câmber, combustível)
# ---------------------------------------------------------------------------

def test_camber():
    eng = RaceEngineer()
    st = state(tyre_temp_inner=[98.0, 85.0, 85.0, 85.0],
               tyre_temp_outer=[84.0, 85.0, 85.0, 85.0])
    adv = eng.analyze_lap([], state=st)
    camber = [a for a in adv if a.key.startswith("lap:camber")]
    assert camber, textos(adv)
    assert "dianteiro esquerdo" in camber[0].text
    assert "reduza" in camber[0].text.lower()
    return camber[0].text


def test_camber_dentro_da_janela_fica_calado():
    eng = RaceEngineer()
    st = state(tyre_temp_inner=[88.0] * 4, tyre_temp_outer=[85.0] * 4)
    adv = eng.analyze_lap([], state=st)
    assert not [a for a in adv if a.key.startswith("lap:camber")], textos(adv)
    return "3 °C de diferença: silêncio"


def test_combustivel_nao_fecha_a_corrida():
    eng = RaceEngineer()
    st = state(total_laps=20, completed_laps=5, fuel_laps_remaining=6.0)
    adv = eng.analyze_lap([], state=st)
    fuel = [a for a in adv if a.key == "lap:fuel_race"]
    assert fuel, textos(adv)
    assert "6.0 voltas de autonomia para 15 restantes" in fuel[0].detail
    return fuel[0].detail


def test_combustivel_suficiente_fica_calado():
    eng = RaceEngineer()
    st = state(total_laps=20, completed_laps=5, fuel_laps_remaining=18.0)
    adv = eng.analyze_lap([], state=st)
    assert not [a for a in adv if a.key == "lap:fuel_race"], textos(adv)
    return "autonomia suficiente: silêncio"


# ---------------------------------------------------------------------------
# Ao vivo
# ---------------------------------------------------------------------------

def test_live_abs_e_tc():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(abs_intervention=0.8, tc_intervention=0.7), now=100.0)
    chaves = {a.key for a in adv}
    assert "abs" in chaves and "tc" in chaves, chaves
    return textos(adv)[:80]


def test_live_respeita_tempo_de_espera():
    """O mesmo aviso não pode sair a cada quadro."""
    eng = RaceEngineer()
    st = state(abs_intervention=0.9)
    primeiro = eng.analyze_live(st, now=100.0)
    logo_depois = eng.analyze_live(st, now=100.5)
    bem_depois = eng.analyze_live(st, now=140.0)
    assert primeiro, "primeiro aviso não saiu"
    assert not logo_depois, "repetiu meio segundo depois"
    assert bem_depois, "não voltou a avisar depois da espera"
    return "1 aviso, silêncio em +0.5s, novo aviso em +40s"


def test_live_pneu_e_freio():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(tyre_temp=[118.0, 85.0, 85.0, 85.0],
                                 brake_temp=[850.0, 400.0, 400.0, 400.0]),
                           now=10.0)
    pneu = [a for a in adv if a.key.startswith("tyre_hot")]
    freio = [a for a in adv if a.key == "brake_hot"]
    assert pneu and pneu[0].severity == CRITICAL, textos(adv)
    assert "dianteiro esquerdo" in pneu[0].text
    assert freio, textos(adv)
    return f"{pneu[0].text} | {freio[0].text}"


def test_live_combustivel_e_bandeira():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(fuel_laps_remaining=0.8, flag="AZUL"), now=10.0)
    chaves = {a.key for a in adv}
    assert "fuel" in chaves and "flag:AZUL" in chaves, chaves
    # Bandeira e combustível crítico vêm antes de qualquer coisa
    assert adv[0].severity == CRITICAL, adv[0].severity
    return textos(adv)[:80]


def test_live_penalidade_e_corta_caminho():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(penalty_time=5.0, tyres_out=4), now=10.0)
    chaves = {a.key for a in adv}
    assert "penalty" in chaves and "cut" in chaves, chaves
    return textos(adv)[:80]


def test_live_carro_saudavel_fica_calado():
    """Nada errado: o engenheiro não fala. É o teste mais importante da voz."""
    eng = RaceEngineer()
    adv = eng.analyze_live(state(), now=10.0)
    assert adv == [], textos(adv)
    return "silêncio absoluto"


def test_escolha_do_que_falar():
    """
    A voz recebe o essencial: o crítico e a curva onde mais se perdeu.

    O painel de texto mostra tudo; falar tudo em voz alta seria pior que não
    falar. E a curva que custou mais tempo precisa passar na frente de avisos
    de rotina, porque é ela que muda a próxima volta.
    """
    eng = RaceEngineer()
    adv = eng.analyze_lap(
        [comparison(1, "C1", delta_t=0.08),
         comparison(2, "Ferradura", delta_t=0.42, d_brake=-20.0)],
        state=state(total_laps=20, completed_laps=5, fuel_laps_remaining=6.0),
        lap_time_str="1:30.100", lap_delta_s=0.6)

    falados = eng.pick_for_voice(adv, limit=2)
    assert len(falados) == 2, len(falados)
    assert any("Ferradura" in a.text for a in falados), textos(falados)

    # Com um crítico na mesa, ele vem primeiro
    adv_crit = eng.analyze_live(state(fuel_laps_remaining=0.5), now=1.0) + adv
    falados = eng.pick_for_voice(adv_crit, limit=2)
    assert falados[0].severity == CRITICAL, falados[0].severity
    assert any("Ferradura" in a.text for a in falados), textos(falados)
    return textos(falados)[:90]


def test_texto_falado_usa_virgula():
    """
    O que vai para a voz usa vírgula decimal; o painel mantém o ponto.

    Em português, "0.42" é lido como "zero PONTO quarenta e dois" — soa errado.
    """
    eng = RaceEngineer()
    adv = [a for a in eng.analyze_lap([comparison(delta_t=0.42, d_brake=-20.0)])
           if a.corner == 1][0]
    assert "0.42" in adv.text, adv.text          # painel
    assert "0,42" in adv.spoken, adv.spoken      # voz
    assert "0.42" not in adv.spoken, adv.spoken
    # E nada mais é alterado
    assert adv.spoken.replace("0,42", "0.42") == adv.text
    return adv.spoken


def test_pontuacao_de_voz():
    """
    A escolha da voz: português ganha de inglês, e a versão OneCore ganha da
    "Desktop" (mesma locutora, geração antiga e mecânica do SAPI).
    """
    from core.voice import VoiceEngine
    nota = VoiceEngine.voice_score

    onecore_pt = nota("Microsoft Daniel - Portuguese (Brazil)")
    desktop_pt = nota("Microsoft Maria Desktop - Portuguese(Brazil)")
    desktop_en = nota("Microsoft Zira Desktop - English (United States)")

    assert onecore_pt > desktop_pt, (onecore_pt, desktop_pt)
    assert desktop_pt > desktop_en, (desktop_pt, desktop_en)
    assert nota("") == 50 or nota("") >= 0
    return f"OneCore pt={onecore_pt} > Desktop pt={desktop_pt} > Desktop en={desktop_en}"


def test_intervalo_minimo_entre_falas():
    eng = RaceEngineer()
    assert eng.should_speak(100.0)
    eng.mark_spoken(100.0)
    assert not eng.should_speak(101.0), "falou 1s depois da anterior"
    assert eng.should_speak(105.0), "não liberou depois do intervalo"
    return "intervalo entre falas respeitado"


# ---------------------------------------------------------------------------
# Consistência
# ---------------------------------------------------------------------------

def test_consistencia_ruim():
    eng = RaceEngineer()
    aviso = None
    for ms in (89000, 91500, 89200, 92000):
        aviso = eng.register_lap_time(ms)
    assert aviso is not None and aviso.key == "lap:consistencia", aviso
    assert "3.0 segundos" in aviso.text, aviso.text
    return aviso.text


def test_consistencia_boa():
    eng = RaceEngineer()
    aviso = None
    for ms in (89000, 89150, 89050, 89200):
        aviso = eng.register_lap_time(ms)
    assert aviso is not None and aviso.key == "lap:consistencia_ok", aviso
    return aviso.text


def test_consistencia_precisa_de_voltas():
    eng = RaceEngineer()
    assert eng.register_lap_time(89000) is None
    assert eng.register_lap_time(89100) is None
    return "só opina com voltas suficientes"


def test_consistencia_ignora_volta_de_box():
    eng = RaceEngineer()
    for ms in (89000, 89100, 89050):
        eng.register_lap_time(ms)
    assert eng.register_lap_time(12000) is None, "volta de 12s entrou na conta"
    return "volta curta descartada"


for nome, fn in [
    ("curva: freou antes", test_freou_antes),
    ("curva: entrou devagar no ápice", test_entrou_devagar),
    ("curva: demorou a acelerar", test_demorou_a_acelerar),
    ("curva: perda sem causa não inventa motivo", test_perda_sem_causa_identificada),
    ("curva: prioridade pelo tempo perdido", test_prioridade_por_tempo_perdido),
    ("curva: limita a quantidade de conselhos", test_limita_quantidade_de_curvas),
    ("curva: reforço positivo onde ganhou", test_reforco_positivo),
    ("volta boa não gera crítica", test_volta_boa_nao_gera_critica),
    ("ruído de milésimos é ignorado", test_ruido_ignorado),
    ("sem referência, silêncio sobre curvas", test_sem_referencia_nao_fala_de_curva),
    ("ABS: vício de freada na volta", test_abs_vicio_de_freada),
    ("ABS: travada isolada não vira vício", test_abs_pontual_nao_vira_vicio),
    ("TC: aponta a curva do pior corte", test_tc_localiza_a_curva),
    ("câmber pela banda de rodagem", test_camber),
    ("câmber na janela fica calado", test_camber_dentro_da_janela_fica_calado),
    ("combustível não fecha a corrida", test_combustivel_nao_fecha_a_corrida),
    ("combustível suficiente fica calado", test_combustivel_suficiente_fica_calado),
    ("ao vivo: ABS e TC", test_live_abs_e_tc),
    ("ao vivo: tempo de espera entre repetições", test_live_respeita_tempo_de_espera),
    ("ao vivo: pneu e freio superaquecidos", test_live_pneu_e_freio),
    ("ao vivo: combustível e bandeira", test_live_combustivel_e_bandeira),
    ("ao vivo: penalidade e corta-caminho", test_live_penalidade_e_corta_caminho),
    ("ao vivo: carro saudável fica calado", test_live_carro_saudavel_fica_calado),
    ("escolha do que vai para a voz", test_escolha_do_que_falar),
    ("texto falado usa vírgula decimal", test_texto_falado_usa_virgula),
    ("pontuação de escolha da voz", test_pontuacao_de_voz),
    ("intervalo mínimo entre falas", test_intervalo_minimo_entre_falas),
    ("consistência ruim", test_consistencia_ruim),
    ("consistência boa", test_consistencia_boa),
    ("consistência exige voltas suficientes", test_consistencia_precisa_de_voltas),
    ("consistência ignora volta de box", test_consistencia_ignora_volta_de_box),
]:
    check(nome, fn)

print()
fails = [r for r in results if not r[1]]
for nome, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
