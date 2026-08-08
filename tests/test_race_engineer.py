"""
tests/test_race_engineer.py — Engenheiro de pista (regras)
==========================================================
As regras são o coração da funcionalidade: se elas falarem bobagem, a voz vai
repetir a bobagem em voz alta. Cada teste monta uma situação onde a resposta
certa é conhecida e cobra o conselho correto — e, tão importante quanto, cobra
SILÊNCIO quando não há nada a dizer.

Coberto aqui:
  * tempo — delta ao vivo, setor que fecha, melhor volta na reta final
  * curva — freou antes / entrou devagar / demorou a acelerar / marcha /
    velocidade de saída / traçado / curva de gás cheio
  * pedais — freio e acelerador juntos, freio solto em degraus
  * volante — subesterço e suavidade contra a referência
  * motor — trocas cedo demais e batidas no corte
  * carro e pista — pneu, freio, dano, limitador, bandeira, grip, vento
  * silêncio no box, no pit lane, em replay e com o jogo pausado
  * consistência e consumo entre voltas
  * nada de conselho quando a volta está boa

O engenheiro NÃO fala de setup (câmber, pressão, ganho de force feedback): há
teste cobrando esse silêncio, porque é fácil alguém reintroduzir.

As medidas de pilotagem estão em tests/test_driving_analysis.py e a fila de voz
em tests/test_voice_queue.py.

    python tests/test_race_engineer.py
"""

import os
import re
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
               d_throttle=None, v_min=95.0, start=0.1, end=0.2, v_min_m=100.0):
    """
    Monta um CornerComparison com os deltas que o teste quer.

    Os deltas são propriedades calculadas de lap x ref, então os valores são
    montados nas duas pontas para que a subtração dê o pedido.
    """
    c = corner(index, name, start, end)
    lap = ca.CornerMetrics(corner=c, v_min=v_min, v_min_m=v_min_m,
                           braking_point_m=500.0, throttle_point_m=700.0,
                           entry_time=10.0,
                           exit_time=10.0 + (delta_t if delta_t else 0.0) + 3.0)
    ref = ca.CornerMetrics(
        corner=c,
        v_min=(v_min - d_vmin) if d_vmin is not None else None,
        v_min_m=v_min_m,
        braking_point_m=(500.0 - d_brake) if d_brake is not None else None,
        throttle_point_m=(700.0 - d_throttle) if d_throttle is not None else None,
        entry_time=10.0, exit_time=13.0)
    return ca.CornerComparison(corner=c, lap=lap, ref=ref)


def state(**kw):
    st = TelemetryState(is_connected=True, track_name="Spa", car_name="911")
    st.speed_kmh = 180.0
    st.tyre_temp = [85.0] * 4
    st.brake_temp = [400.0] * 4
    st.track_temp = 30.0
    st.surface_grip = 1.0
    st.max_rpm = 8000.0
    st.track_length = 5000.0
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def lap_channels(n=200, length=5000.0, **overrides):
    """
    Uma volta sintética plausível: acelera, freia, vira, troca de marcha.

    Serve de base neutra — cada teste substitui só o canal que quer testar.
    """
    dist = [i * (length / n) for i in range(n)]
    tel = {
        "times": [i * 0.05 for i in range(n)],
        "distance": dist,
        "speed": [150.0] * n,
        "gas": [1.0] * n,
        "brake": [0.0] * n,
        "rpm": [7800.0] * n,
        "gear": [5] * n,
        "steer": [0.0] * n,
        "g_lat": [0.0] * n,
        "car_x": [float(i) for i in range(n)],
        "car_z": [0.0] * n,
        "abs_intervention": [0.0] * n,
        "tc_intervention": [0.0] * n,
    }
    tel.update(overrides)
    return tel


def textos(advices):
    return " || ".join(a.text for a in advices)


def por_chave(advices, prefixo):
    return [a for a in advices if a.key.startswith(prefixo)]


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
    assert "freada" in t, t                    # o que fazer
    assert alvo[0].severity == ATTENTION, alvo[0].severity
    return t


def test_entrou_devagar():
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.25, d_vmin=-6.0, v_min=88.0)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "6 por hora mais devagar" in t, t
    # E diz o alvo concreto, que é o que o piloto consegue perseguir
    assert "94" in t, t
    return t


def test_demorou_a_acelerar():
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.20, d_throttle=35.0)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "35 metros depois" in t, t
    return t


def test_ponto_de_freio_quase_certo():
    """Diferença pequena não é erro: é ajuste fino, e vale dizer que está bom."""
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.10, d_brake=-5.0)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "ponto de freio bom" in t.lower(), t
    assert "5 metros" in t, t
    return t


def test_perda_sem_causa_identificada():
    """Perdeu tempo mas nenhuma métrica explica: diz a perda e não inventa nada."""
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(delta_t=0.12)])
    t = [a for a in adv if a.corner == 1][0].text
    assert "perdeu 0.12 segundos" in t, t
    assert "freada" not in t and "gás" not in t, t
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
    assert len(por_chave(adv, "corner:")) <= 3, textos(adv)
    return f"{len(por_chave(adv, 'corner:'))} curvas comentadas de 8"


def test_reforco_positivo():
    eng = RaceEngineer()
    adv = eng.analyze_lap([
        comparison(1, "C1", delta_t=0.30, d_brake=-12.0),
        comparison(2, "Eau Rouge", delta_t=-0.22),
    ])
    bons = por_chave(adv, "corner_ok:")
    assert bons, textos(adv)
    assert "Eau Rouge" in bons[0].text and "0.22" in bons[0].text
    return bons[0].text


def test_reforco_positivo_diz_o_porque():
    """
    Elogio sem motivo o piloto não consegue repetir de propósito.
    """
    eng = RaceEngineer()
    n = 200
    rapida = eng.analyze_lap(
        [comparison(2, "Mergulho", delta_t=-0.22)],
        lap_telemetry=lap_channels(n, speed=[160.0] * n),
        ref_telemetry=lap_channels(n, speed=[150.0] * n), state=state())
    bom = por_chave(rapida, "corner_ok:")[0].text
    assert "mais rápido" in bom, bom

    colada = eng.analyze_lap(
        [comparison(2, "Mergulho", delta_t=-0.22)],
        lap_telemetry=lap_channels(n), ref_telemetry=lap_channels(n),
        state=state())
    linha = por_chave(colada, "corner_ok:")[0].text
    assert "ápice" in linha, linha
    return f"{bom} || {linha}"


def test_volta_boa_nao_gera_critica():
    """Volta melhor que a referência em tudo: nada de conselho de curva."""
    eng = RaceEngineer()
    adv = eng.analyze_lap(
        [comparison(1, "C1", delta_t=-0.10), comparison(2, "C2", delta_t=-0.05)],
        lap_time_str="1:29.100", lap_delta_s=-0.15)
    assert not por_chave(adv, "corner:"), textos(adv)
    assert any("Boa volta" in a.text for a in adv), textos(adv)
    return textos(adv)[:70]


def test_ruido_ignorado():
    """Diferença de milésimos não vira conselho."""
    eng = RaceEngineer()
    adv = eng.analyze_lap([comparison(1, "C1", delta_t=0.01)])
    assert not por_chave(adv, "corner:"), textos(adv)
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
# Curva: o que só os canais brutos contam
# ---------------------------------------------------------------------------

def test_curva_marcha_errada_no_apice():
    eng = RaceEngineer()
    n = 200
    lap = lap_channels(n, gear=[3] * n)          # 3 no jogo = 2ª marcha
    ref = lap_channels(n, gear=[4] * n)          # 4 no jogo = 3ª marcha
    adv = eng.analyze_lap(
        [comparison(1, "Ferradura", delta_t=0.30, v_min_m=750.0)],
        lap_telemetry=lap, ref_telemetry=ref, state=state())
    t = [a for a in adv if a.corner == 1][0].text
    assert "2ª" in t and "3ª" in t, t
    return t


def test_curva_velocidade_de_saida():
    eng = RaceEngineer()
    n = 200
    lap = lap_channels(n, speed=[120.0] * n)
    ref = lap_channels(n, speed=[130.0] * n)
    adv = eng.analyze_lap([comparison(1, "Junção", delta_t=0.25)],
                          lap_telemetry=lap, ref_telemetry=ref, state=state())
    t = [a for a in adv if a.corner == 1][0].text
    assert "10 por hora mais devagar" in t, t
    assert "reta" in t.lower(), t
    return t


def test_curva_de_gas_cheio():
    """A referência passa sem freio; o piloto freou. É tempo de graça."""
    eng = RaceEngineer()
    n = 200
    freio = [0.0] * n
    for i in range(20, 45):
        freio[i] = 0.8
    lap = lap_channels(n, brake=freio)
    ref = lap_channels(n)                        # sem freio nenhum
    adv = eng.analyze_lap(
        [comparison(1, "Curva 3", delta_t=0.35, start=0.1, end=0.25)],
        lap_telemetry=lap, ref_telemetry=ref, state=state())
    t = [a for a in adv if a.corner == 1][0].text
    assert "sem freio" in t or "gás cheio" in t, t
    return t


def test_curva_fora_da_linha():
    eng = RaceEngineer()
    n = 200
    lap = lap_channels(n, car_z=[3.0] * n)       # 3 metros deslocado
    ref = lap_channels(n, car_z=[0.0] * n)
    adv = eng.analyze_lap(
        [comparison(1, "Curva 4", delta_t=0.28, start=0.1, end=0.25)],
        lap_telemetry=lap, ref_telemetry=ref, state=state())
    t = [a for a in adv if a.corner == 1][0].text
    assert "fora da linha" in t, t
    assert "3.0 metros" in t, t
    return t


def test_traçado_igual_nao_vira_conselho():
    eng = RaceEngineer()
    n = 200
    lap = lap_channels(n)
    adv = eng.analyze_lap(
        [comparison(1, "Curva 4", delta_t=0.28, start=0.1, end=0.25)],
        lap_telemetry=lap, ref_telemetry=lap_channels(n), state=state())
    t = [a for a in adv if a.corner == 1][0].text
    assert "fora da linha" not in t, t
    return "mesma linha: silêncio sobre traçado"


# ---------------------------------------------------------------------------
# Setores
# ---------------------------------------------------------------------------

def test_setores_apontam_o_pior():
    eng = RaceEngineer()
    adv = eng.analyze_lap([], sector_times_ms=[30000, 31500, 29900],
                          ref_sector_times_ms=[30100, 30000, 30000])
    setor = por_chave(adv, "lap:sector")
    assert setor, textos(adv)
    t = setor[0].text
    assert "S1 verde" in t and "S2 vermelho" in t, t
    assert "setor 2" in t, t
    return t


def test_setores_volta_bem_distribuida():
    eng = RaceEngineer()
    adv = eng.analyze_lap([], sector_times_ms=[30000, 30000, 30000],
                          ref_sector_times_ms=[30020, 30010, 30030])
    setor = por_chave(adv, "lap:sector_ok")
    assert setor, textos(adv)
    assert setor[0].severity == INFO
    return setor[0].text


def test_setor_forte_precisa_de_padrao():
    """Um setor bom numa volta é sorte; em três voltas é ponto forte."""
    eng = RaceEngineer()
    ref = [30000, 30000, 30000]
    primeira = eng.analyze_lap([], sector_times_ms=[30000, 30000, 29700],
                               ref_sector_times_ms=ref)
    assert not por_chave(primeira, "lap:sector_forte"), textos(primeira)
    for _ in range(2):
        adv = eng.analyze_lap([], sector_times_ms=[30000, 30000, 29700],
                              ref_sector_times_ms=ref)
    forte = por_chave(adv, "lap:sector_forte")
    assert forte, textos(adv)
    assert "setor 3" in forte[0].text, forte[0].text
    return forte[0].text


def test_sem_setores_fica_calado():
    eng = RaceEngineer()
    adv = eng.analyze_lap([], sector_times_ms=None, ref_sector_times_ms=None)
    assert not por_chave(adv, "lap:sector"), textos(adv)
    return "sem tempos de setor: silêncio"


# ---------------------------------------------------------------------------
# Pedais
# ---------------------------------------------------------------------------

def test_freio_e_acelerador_juntos():
    eng = RaceEngineer()
    n = 200
    freio = [0.0] * n
    gas = [1.0] * n
    for i in range(40, 100):
        freio[i] = 0.7                           # freando 60 quadros...
    for i in range(40, 70):
        gas[i] = 0.6                             # ...com o pé no gás em metade
    for i in range(70, 100):
        gas[i] = 0.0
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, brake=freio, gas=gas))
    ov = por_chave(adv, "lap:overlap")
    assert ov, textos(adv)
    assert "50%" in ov[0].detail, ov[0].detail
    return ov[0].text


def test_freio_limpo_fica_calado():
    eng = RaceEngineer()
    n = 200
    freio = [0.0] * n
    gas = [1.0] * n
    for i in range(40, 100):
        freio[i], gas[i] = 0.7, 0.0
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, brake=freio, gas=gas))
    assert not por_chave(adv, "lap:overlap"), textos(adv)
    return "pedais separados: silêncio"


def test_freio_solto_em_degraus():
    eng = RaceEngineer()
    n = 400
    freio = [0.0] * n
    # Cinco freadas, cada uma solta em degrau (cai e volta a subir)
    for k in range(5):
        base = 40 + k * 60
        for i in range(base, base + 10):
            freio[i] = 0.9
        for i in range(base + 10, base + 16):
            freio[i] = 0.4                       # soltou...
        for i in range(base + 16, base + 22):
            freio[i] = 0.7                       # ...e pisou de novo
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, brake=freio,
                                                        gas=[0.0] * n))
    jit = por_chave(adv, "lap:brake_jitter")
    assert jit, textos(adv)
    assert "5 das 5 freadas" in jit[0].detail, jit[0].detail
    return jit[0].text


# ---------------------------------------------------------------------------
# Volante
# ---------------------------------------------------------------------------

def test_freio_largado_de_uma_vez():
    """
    Erro diferente de repisar: aqui o pé pula fora do pedal e a dianteira
    perde carga bem na entrada.
    """
    eng = RaceEngineer()
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 40
        for i in range(base, base + 20):
            freio[i] = 0.9
        freio[base + 20] = 0.3
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, brake=freio,
                                                        gas=[0.0] * n))
    seco = por_chave(adv, "lap:brake_abrupt")
    assert seco, textos(adv)
    assert "solta mais suave" in seco[0].text.lower(), seco[0].text
    assert "4 das 4" in seco[0].detail, seco[0].detail
    # E não acusa degrau, que é outro problema
    assert not por_chave(adv, "lap:brake_jitter"), textos(adv)
    return seco[0].text


def test_freio_solto_progressivo_fica_calado():
    eng = RaceEngineer()
    n = 300
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 60
        for i in range(base, base + 15):
            freio[i] = 0.9
        for j in range(15):
            freio[base + 15 + j] = 0.9 - j * 0.06
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, brake=freio,
                                                        gas=[0.0] * n))
    assert not por_chave(adv, "lap:brake_"), textos(adv)
    return "soltura progressiva: silêncio"


def test_subesterco():
    eng = RaceEngineer()
    n = 200
    steer = [0.0] * n
    g_lat = [0.0] * n
    for i in range(0, 100):
        steer[i] = 100.0                         # volante no talo...
        g_lat[i] = 0.2                           # ...e o carro não vira
    for i in range(100, 200):
        steer[i] = 20.0
        g_lat[i] = 1.5                           # referência de G da volta
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, steer=steer, g_lat=g_lat))
    sub = por_chave(adv, "lap:understeer")
    assert sub, textos(adv)
    assert "ângulo demais" in sub[0].text, sub[0].text
    return sub[0].detail


def test_volante_brusco_contra_a_referencia():
    eng = RaceEngineer()
    n = 200
    bruto = [(-1) ** i * 60.0 for i in range(n)]     # zigue-zague violento
    suave = [30.0 + 0.1 * i for i in range(n)]       # rampa mansa
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, steer=bruto),
                          ref_telemetry=lap_channels(n, steer=suave))
    rough = por_chave(adv, "lap:steer_rough")
    assert rough, textos(adv)
    return rough[0].detail


def test_volante_suave_ganha_elogio():
    eng = RaceEngineer()
    n = 200
    suave = [30.0 + 0.05 * i for i in range(n)]
    bruto = [30.0 + 0.5 * i for i in range(n)]
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, steer=suave,
                                                         g_lat=[1.0] * n),
                          ref_telemetry=lap_channels(n, steer=bruto))
    assert por_chave(adv, "lap:steer_smooth"), textos(adv)
    return "elogio quando o volante está mais suave que a referência"


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

def test_troca_cedo_demais():
    eng = RaceEngineer()
    n = 200
    rpm = [4000.0] * n
    gear = [2] * n
    for k in range(1, 5):                        # quatro trocas a 4.000 giros
        i = k * 40
        gear[i:] = [2 + k] * (n - i)
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, rpm=rpm, gear=gear),
                          state=state(max_rpm=8000.0))
    cedo = por_chave(adv, "lap:shift_early")
    assert cedo, textos(adv)
    assert "4 de 4" in cedo[0].detail, cedo[0].detail
    return cedo[0].text


def test_troca_na_faixa_fica_calado():
    eng = RaceEngineer()
    n = 200
    rpm = [7900.0] * n
    gear = [2] * n
    for k in range(1, 5):
        i = k * 40
        gear[i:] = [2 + k] * (n - i)
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, rpm=rpm, gear=gear),
                          state=state(max_rpm=8000.0))
    assert not por_chave(adv, "lap:shift_early"), textos(adv)
    return "trocas na faixa de potência: silêncio"


def test_batendo_no_corte():
    eng = RaceEngineer()
    n = 200
    rpm = [8000.0] * n                           # a volta inteira no corte
    gear = [2] * n
    gear[100:] = [3] * 100
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, rpm=rpm, gear=gear),
                          state=state(max_rpm=8000.0))
    corte = por_chave(adv, "lap:limiter")
    assert corte, textos(adv)
    return corte[0].text


def test_sem_rpm_maximo_nao_opina():
    """Sem o RPM máximo do carro não há como saber se 7.000 é cedo ou tarde."""
    eng = RaceEngineer()
    n = 200
    gear = [2] * n
    gear[100:] = [3] * 100
    adv = eng.analyze_lap([], lap_telemetry=lap_channels(n, rpm=[4000.0] * n,
                                                         gear=gear),
                          state=state(max_rpm=0.0))
    assert not por_chave(adv, "lap:shift"), textos(adv)
    return "sem max_rpm: silêncio sobre marcha"


# ---------------------------------------------------------------------------
# ABS / TC ao longo da volta
# ---------------------------------------------------------------------------

def test_abs_vicio_de_freada():
    eng = RaceEngineer()
    n = 200
    telem = lap_channels(n, abs_intervention=[0.9 if i % 10 == 0 else 0.0
                                              for i in range(n)],
                         distance=[i * 35.0 for i in range(n)])
    adv = eng.analyze_lap([], lap_telemetry=telem)
    abs_adv = por_chave(adv, "lap:abs")
    assert abs_adv, textos(adv)
    assert "ABS" in abs_adv[0].text and "freando" in abs_adv[0].text
    return abs_adv[0].detail


def test_abs_pontual_nao_vira_vicio():
    """Uma travada só na volta inteira não é vício de pilotagem."""
    eng = RaceEngineer()
    n = 200
    telem = lap_channels(n, abs_intervention=[0.9 if i == 50 else 0.0
                                              for i in range(n)],
                         distance=[i * 35.0 for i in range(n)])
    adv = eng.analyze_lap([], lap_telemetry=telem)
    assert not por_chave(adv, "lap:abs"), textos(adv)
    return "1 travada em 200 pontos: silêncio"


def test_tc_localiza_a_curva():
    """O aviso de TC diz em qual curva foi o pior corte."""
    eng = RaceEngineer()
    n = 200
    telem = lap_channels(n, tc_intervention=[0.9 if 110 <= i <= 128 else 0.0
                                             for i in range(n)],
                         distance=[i * 35.0 for i in range(n)])
    comps = [comparison(1, "La Source"), comparison(2, "Pouhon")]
    comps[1].corner.start, comps[1].corner.end = 0.55, 0.65
    adv = eng.analyze_lap(comps, lap_telemetry=telem)
    tc = por_chave(adv, "lap:tc")
    assert tc, textos(adv)
    assert "Pouhon" in tc[0].text, tc[0].text
    return tc[0].text


# ---------------------------------------------------------------------------
# Nada de setup
# ---------------------------------------------------------------------------

def test_nao_fala_de_setup():
    """
    O engenheiro é de PILOTAGEM. Câmber, pressão e ganho de force feedback são
    conversa de box — no meio de uma volta, isso é ruído.
    """
    eng = RaceEngineer()
    st = state(tyre_temp_inner=[98.0, 85.0, 85.0, 85.0],
               tyre_temp_outer=[84.0, 85.0, 85.0, 85.0],
               ffb_level=0.99, brake_bias=0.75)
    tudo = eng.analyze_lap([comparison(delta_t=0.30, d_brake=-20.0)],
                           lap_telemetry=lap_channels(), state=st)
    tudo += eng.analyze_live(st, now=10.0)
    proibidas = ("câmber", "camber", "pressão", "force feedback", "ganho",
                 "asa", "asas", "mola", "molas", "barra", "estabilizadora",
                 "diferencial", "setup", "acerto")
    # Palavra inteira: "asa" não pode casar dentro de "atrasa".
    padrao = re.compile(r"\b(" + "|".join(proibidas) + r")\b")
    for a in tudo:
        achado = padrao.search(a.display.lower())
        assert not achado, f"{achado.group(0)!r} em {a.display!r}"
    return f"{len(tudo)} recados, nenhum de setup"


# ---------------------------------------------------------------------------
# Ao vivo: tempo
# ---------------------------------------------------------------------------

def test_live_delta_perdendo():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(delta_time=0.45), now=10.0)
    d = por_chave(adv, "delta")
    assert d, textos(adv)
    assert "mais 0.45" in d[0].text and d[0].severity == ATTENTION, d[0].text
    return d[0].text


def test_live_delta_voando():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(delta_time=-0.22), now=10.0)
    d = por_chave(adv, "delta")
    assert d and "menos 0.22" in d[0].text, textos(adv)
    assert "voando" in d[0].text, d[0].text
    return d[0].text


def test_live_delta_so_fala_quando_muda():
    """Repetir "mais 0.15" a cada dez segundos não informa nada novo."""
    eng = RaceEngineer()
    assert por_chave(eng.analyze_live(state(delta_time=0.20), now=10.0), "delta")
    # Muito depois do tempo de espera, mas com o mesmo delta: silêncio
    assert not por_chave(eng.analyze_live(state(delta_time=0.21), now=100.0), "delta")
    # Mudou de verdade: volta a falar
    assert por_chave(eng.analyze_live(state(delta_time=0.60), now=200.0), "delta")
    return "fala na mudança, cala na repetição"


def test_live_delta_pequeno_e_ruido():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(delta_time=0.04), now=10.0)
    assert not por_chave(adv, "delta"), textos(adv)
    return "0.04s é ruído de medição: silêncio"


def test_live_setor_fechado():
    eng = RaceEngineer()
    ganhou = eng.analyze_live(state(s1_delta=-0.08), now=10.0)
    s = por_chave(ganhou, "sector:1")
    assert s, textos(ganhou)
    assert "setor 1" in s[0].text and "0.08" in s[0].text, s[0].text

    perdeu = eng.analyze_live(state(s1_delta=-0.08, s2_delta=0.30), now=40.0)
    s2 = por_chave(perdeu, "sector:2")
    assert s2 and s2[0].severity == ATTENTION, textos(perdeu)
    return f"{s[0].text} || {s2[0].text}"


def test_live_setor_nao_repete():
    eng = RaceEngineer()
    st = state(s1_delta=-0.08)
    assert por_chave(eng.analyze_live(st, now=10.0), "sector:1")
    assert not por_chave(eng.analyze_live(st, now=100.0), "sector:1")
    return "setor comentado uma vez só"


def test_live_melhor_volta_ameacada():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(track_position=0.96, delta_time=0.10,
                                 track_length=5000.0), now=10.0)
    b = por_chave(adv, "best_lap")
    assert b, textos(adv)
    assert "ameaçada" in b[0].text and "200 metros" in b[0].text, b[0].text
    return b[0].text


def test_live_melhor_volta_na_mao():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(track_position=0.98, delta_time=-0.30,
                                 track_length=5000.0), now=10.0)
    b = por_chave(adv, "best_lap")
    assert b and "na mão" in b[0].text, textos(adv)
    return b[0].text


def test_live_meio_da_volta_nao_fala_de_melhor_volta():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(track_position=0.40, delta_time=-0.30), now=10.0)
    assert not por_chave(adv, "best_lap"), textos(adv)
    return "só na reta final"


# ---------------------------------------------------------------------------
# Ao vivo: carro e pista
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
    pneu = por_chave(adv, "tyre_hot")
    freio = por_chave(adv, "brake_hot")
    assert pneu and pneu[0].severity == CRITICAL, textos(adv)
    assert "dianteiro esquerdo" in pneu[0].text
    assert freio, textos(adv)
    return f"{pneu[0].text} | {freio[0].text}"


def test_live_pneus_frios_em_um_recado_so():
    """
    Carro inteiro frio é UMA informação, não quatro.

    Na volta de saída do box os quatro pneus estão frios; quatro frases para
    dizer isso enchem a fila de voz e empurram para fora o que importa.
    """
    eng = RaceEngineer()
    adv = eng.analyze_live(state(tyre_temp=[55.0] * 4), now=10.0)
    frios = por_chave(adv, "tyre_cold")
    assert len(frios) == 1, textos(adv)
    assert "Pneus" in frios[0].text, frios[0].text

    eng2 = RaceEngineer()
    adv2 = eng2.analyze_live(state(tyre_temp=[55.0, 85.0, 85.0, 85.0]), now=10.0)
    um = por_chave(adv2, "tyre_cold")
    assert len(um) == 1 and "dianteiro esquerdo" in um[0].text, textos(adv2)
    return f"{frios[0].text} ({frios[0].detail})"


def test_live_combustivel_e_bandeira():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(fuel_laps_remaining=0.8, flag="AZUL"), now=10.0)
    chaves = {a.key for a in adv}
    assert "fuel" in chaves and "flag:AZUL" in chaves, chaves
    assert adv[0].severity == CRITICAL, adv[0].severity
    return textos(adv)[:80]


def test_live_combustivel_tranquilo_so_em_corrida():
    """
    Num treino livre de tanque cheio, "dá pra mais 30 voltas" é ruído puro.
    """
    eng = RaceEngineer()
    adv = eng.analyze_live(state(fuel_laps_remaining=8.4, session_type="Race"),
                           now=10.0)
    ok = por_chave(adv, "fuel_ok")
    assert ok and "8 voltas" in ok[0].text, textos(adv)

    treino = RaceEngineer().analyze_live(
        state(fuel_laps_remaining=30.0, session_type="Practice"), now=10.0)
    assert not por_chave(treino, "fuel_ok"), textos(treino)
    return ok[0].text


def test_live_penalidade_e_corta_caminho():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(penalty_time=5.0, tyres_out=4), now=10.0)
    chaves = {a.key for a in adv}
    assert "penalty" in chaves and "cut" in chaves, chaves
    return textos(adv)[:80]


def test_live_pista_esfriando_e_esquentando():
    eng = RaceEngineer()
    eng.analyze_live(state(track_temp=34.0), now=10.0)      # referência
    frio = eng.analyze_live(state(track_temp=28.0), now=200.0)
    assert any("esfriando" in a.text for a in frio), textos(frio)
    quente = eng.analyze_live(state(track_temp=36.0), now=400.0)
    assert any("mais quente" in a.text for a in quente), textos(quente)
    return "asfalto esfriando e esquentando"


def test_live_pista_verde_e_vento():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(surface_grip=0.90, wind_speed=12.0), now=10.0)
    chaves = {a.key for a in adv}
    assert "grip" in chaves and "wind" in chaves, chaves
    return textos(adv)[:90]


def test_live_carro_saudavel_fica_calado():
    """Nada errado: o engenheiro não fala. É o teste mais importante da voz."""
    eng = RaceEngineer()
    adv = eng.analyze_live(state(), now=10.0)
    assert adv == [], textos(adv)
    return "silêncio absoluto"


def test_live_no_box_fica_calado():
    """
    Parado no box com pneu frio e delta ruim: nada disso é notícia.

    É o ruído que faz o piloto desligar a voz — e aí ele deixa de ouvir o que
    importa também.
    """
    eng = RaceEngineer()
    st = state(tyre_temp=[40.0] * 4, in_pit=True, speed_kmh=0.0, delta_time=2.0)
    assert eng.analyze_live(st, now=10.0) == []

    st_lane = state(tyre_temp=[40.0] * 4, in_pit_lane=True, speed_kmh=60.0)
    assert eng.analyze_live(st_lane, now=20.0) == []
    return "box e pit lane em silêncio"


def test_live_replay_e_pausa_ficam_calados():
    eng = RaceEngineer()
    assert eng.analyze_live(state(is_replay=True, tyre_temp=[130.0] * 4), now=1.0) == []
    assert eng.analyze_live(state(is_paused=True, tyre_temp=[130.0] * 4), now=2.0) == []
    return "replay e pausa em silêncio"


def test_live_bandeira_vale_ate_no_box():
    """Bandeira e penalidade não dependem de estar em pista."""
    eng = RaceEngineer()
    adv = eng.analyze_live(state(in_pit=True, flag="PRETA", penalty_time=5.0), now=10.0)
    chaves = {a.key for a in adv}
    assert "flag:PRETA" in chaves and "penalty" in chaves, chaves
    return textos(adv)[:70]


def test_live_limitador_ligado_na_pista():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(pit_limiter=True, speed_kmh=120.0), now=10.0)
    lim = por_chave(adv, "limiter")
    assert lim and lim[0].severity == CRITICAL, textos(adv)
    parado = eng.analyze_live(state(pit_limiter=True, in_pit_lane=True,
                                    speed_kmh=60.0), now=30.0)
    assert not por_chave(parado, "limiter"), textos(parado)
    return lim[0].text


def test_live_dano_so_avisa_quando_piora():
    """A mesma amassada não pode ser anunciada a cada tempo de espera."""
    eng = RaceEngineer()
    primeiro = eng.analyze_live(state(car_damage=30.0), now=10.0)
    assert por_chave(primeiro, "damage"), textos(primeiro)

    igual = eng.analyze_live(state(car_damage=30.0), now=200.0)
    assert not por_chave(igual, "damage"), textos(igual)

    pior = eng.analyze_live(state(car_damage=70.0), now=400.0)
    dano = por_chave(pior, "damage")
    assert dano and dano[0].severity == CRITICAL, textos(pior)
    return "avisa em 30%, cala em 30%, volta a avisar em 70%"


def test_live_ultima_volta():
    eng = RaceEngineer()
    adv = eng.analyze_live(state(total_laps=10, completed_laps=9), now=10.0)
    assert por_chave(adv, "last_lap"), textos(adv)
    meio = eng.analyze_live(state(total_laps=10, completed_laps=4), now=100.0)
    assert not por_chave(meio, "last_lap"), textos(meio)
    return "última volta anunciada uma vez"


# ---------------------------------------------------------------------------
# Escolha do que falar
# ---------------------------------------------------------------------------

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
    assert adv.spoken.replace("0,42", "0.42") == adv.text
    return adv.spoken


def test_intervalo_minimo_entre_falas():
    eng = RaceEngineer()
    assert eng.should_speak(100.0)
    eng.mark_spoken(100.0)
    assert not eng.should_speak(101.0), "falou 1s depois da anterior"
    assert eng.should_speak(105.0), "não liberou depois do intervalo"
    return "intervalo entre falas respeitado"


# ---------------------------------------------------------------------------
# Entre voltas
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


def test_consistencia_nao_se_repete():
    """
    O mesmo veredito não volta toda volta.

    "Ritmo consistente" repetido a cada volta é ruído; consistência só é
    notícia quando MUDA.
    """
    eng = RaceEngineer()
    for ms in (89000, 89150, 89050, 89200):
        primeiro = eng.register_lap_time(ms)
    assert primeiro is not None and primeiro.key == "lap:consistencia_ok"
    assert eng.register_lap_time(89100) is None, "repetiu o mesmo veredito"
    assert eng.register_lap_time(89050) is None, "repetiu o mesmo veredito"

    mudou = None
    for ms in (92000, 92500):
        mudou = eng.register_lap_time(ms) or mudou
    assert mudou is not None and mudou.key == "lap:consistencia", mudou
    return "fala na mudança, cala na repetição"


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


def test_consumo_alto():
    eng = RaceEngineer()
    # Três voltas gastando 2 L, depois uma gastando 3 L
    for litros in (50.0, 48.0, 46.0, 44.0):
        eng.register_fuel(litros)
    aviso = eng.register_fuel(41.0)
    assert aviso is not None and aviso.key == "lap:fuel_high", aviso
    assert "3.00 L" in aviso.detail and "2.00 L" in aviso.detail, aviso.detail
    return aviso.text


def test_consumo_na_media_fica_calado():
    eng = RaceEngineer()
    aviso = None
    for litros in (50.0, 48.0, 46.0, 44.0, 42.0):
        aviso = eng.register_fuel(litros)
    assert aviso is None, aviso
    return "consumo estável: silêncio"


def test_reabastecimento_nao_conta():
    """Encheu o tanque no pit: não é volta de consumo negativo."""
    eng = RaceEngineer()
    eng.register_fuel(20.0)
    assert eng.register_fuel(60.0) is None
    return "reabastecimento ignorado"


def test_combustivel_nao_fecha_a_corrida():
    eng = RaceEngineer()
    st = state(total_laps=20, completed_laps=5, fuel_laps_remaining=6.0)
    adv = eng.analyze_lap([], state=st)
    fuel = por_chave(adv, "lap:fuel_race")
    assert fuel, textos(adv)
    assert "6.0 voltas de autonomia para 15 restantes" in fuel[0].detail
    return fuel[0].detail


def test_combustivel_suficiente_fica_calado():
    eng = RaceEngineer()
    st = state(total_laps=20, completed_laps=5, fuel_laps_remaining=18.0)
    adv = eng.analyze_lap([], state=st)
    assert not por_chave(adv, "lap:fuel_race"), textos(adv)
    return "autonomia suficiente: silêncio"


for nome, fn in [
    ("curva: freou antes", test_freou_antes),
    ("curva: entrou devagar no ápice", test_entrou_devagar),
    ("curva: demorou a acelerar", test_demorou_a_acelerar),
    ("curva: ponto de freio quase certo", test_ponto_de_freio_quase_certo),
    ("curva: perda sem causa não inventa motivo", test_perda_sem_causa_identificada),
    ("curva: prioridade pelo tempo perdido", test_prioridade_por_tempo_perdido),
    ("curva: limita a quantidade de conselhos", test_limita_quantidade_de_curvas),
    ("curva: reforço positivo onde ganhou", test_reforco_positivo),
    ("curva: reforço positivo diz o porquê", test_reforco_positivo_diz_o_porque),
    ("curva: marcha errada no ápice", test_curva_marcha_errada_no_apice),
    ("curva: velocidade de saída", test_curva_velocidade_de_saida),
    ("curva: de gás cheio virou freada", test_curva_de_gas_cheio),
    ("curva: fora da linha da referência", test_curva_fora_da_linha),
    ("curva: mesmo traçado não vira conselho", test_traçado_igual_nao_vira_conselho),
    ("volta boa não gera crítica", test_volta_boa_nao_gera_critica),
    ("ruído de milésimos é ignorado", test_ruido_ignorado),
    ("sem referência, silêncio sobre curvas", test_sem_referencia_nao_fala_de_curva),
    ("setores: aponta o pior", test_setores_apontam_o_pior),
    ("setores: volta bem distribuída", test_setores_volta_bem_distribuida),
    ("setores: ponto forte precisa de padrão", test_setor_forte_precisa_de_padrao),
    ("setores: sem tempos, silêncio", test_sem_setores_fica_calado),
    ("pedais: freio e acelerador juntos", test_freio_e_acelerador_juntos),
    ("pedais: freio limpo fica calado", test_freio_limpo_fica_calado),
    ("pedais: freio solto em degraus", test_freio_solto_em_degraus),
    ("pedais: freio largado de uma vez", test_freio_largado_de_uma_vez),
    ("pedais: soltura progressiva fica calada", test_freio_solto_progressivo_fica_calado),
    ("volante: subesterço", test_subesterco),
    ("volante: brusco contra a referência", test_volante_brusco_contra_a_referencia),
    ("volante: suave ganha elogio", test_volante_suave_ganha_elogio),
    ("motor: troca cedo demais", test_troca_cedo_demais),
    ("motor: troca na faixa fica calado", test_troca_na_faixa_fica_calado),
    ("motor: batendo no corte", test_batendo_no_corte),
    ("motor: sem RPM máximo não opina", test_sem_rpm_maximo_nao_opina),
    ("ABS: vício de freada na volta", test_abs_vicio_de_freada),
    ("ABS: travada isolada não vira vício", test_abs_pontual_nao_vira_vicio),
    ("TC: aponta a curva do pior corte", test_tc_localiza_a_curva),
    ("não fala de setup", test_nao_fala_de_setup),
    ("ao vivo: delta perdendo tempo", test_live_delta_perdendo),
    ("ao vivo: delta voando", test_live_delta_voando),
    ("ao vivo: delta só fala quando muda", test_live_delta_so_fala_quando_muda),
    ("ao vivo: delta pequeno é ruído", test_live_delta_pequeno_e_ruido),
    ("ao vivo: setor que fechou", test_live_setor_fechado),
    ("ao vivo: setor não repete", test_live_setor_nao_repete),
    ("ao vivo: melhor volta ameaçada", test_live_melhor_volta_ameacada),
    ("ao vivo: melhor volta na mão", test_live_melhor_volta_na_mao),
    ("ao vivo: melhor volta só na reta final", test_live_meio_da_volta_nao_fala_de_melhor_volta),
    ("ao vivo: ABS e TC", test_live_abs_e_tc),
    ("ao vivo: tempo de espera entre repetições", test_live_respeita_tempo_de_espera),
    ("ao vivo: pneu e freio superaquecidos", test_live_pneu_e_freio),
    ("ao vivo: pneus frios em um recado só", test_live_pneus_frios_em_um_recado_so),
    ("ao vivo: combustível e bandeira", test_live_combustivel_e_bandeira),
    ("ao vivo: combustível tranquilo só em corrida", test_live_combustivel_tranquilo_so_em_corrida),
    ("ao vivo: penalidade e corta-caminho", test_live_penalidade_e_corta_caminho),
    ("ao vivo: asfalto esfriando e esquentando", test_live_pista_esfriando_e_esquentando),
    ("ao vivo: pista verde e vento", test_live_pista_verde_e_vento),
    ("ao vivo: carro saudável fica calado", test_live_carro_saudavel_fica_calado),
    ("ao vivo: no box e no pit lane fica calado", test_live_no_box_fica_calado),
    ("ao vivo: replay e pausa ficam calados", test_live_replay_e_pausa_ficam_calados),
    ("ao vivo: bandeira vale até no box", test_live_bandeira_vale_ate_no_box),
    ("ao vivo: limitador ligado na pista", test_live_limitador_ligado_na_pista),
    ("ao vivo: dano só avisa quando piora", test_live_dano_so_avisa_quando_piora),
    ("ao vivo: última volta", test_live_ultima_volta),
    ("escolha do que vai para a voz", test_escolha_do_que_falar),
    ("texto falado usa vírgula decimal", test_texto_falado_usa_virgula),
    ("intervalo mínimo entre falas", test_intervalo_minimo_entre_falas),
    ("consistência ruim", test_consistencia_ruim),
    ("consistência boa", test_consistencia_boa),
    ("consistência não se repete", test_consistencia_nao_se_repete),
    ("consistência exige voltas suficientes", test_consistencia_precisa_de_voltas),
    ("consistência ignora volta de box", test_consistencia_ignora_volta_de_box),
    ("consumo alto na volta", test_consumo_alto),
    ("consumo na média fica calado", test_consumo_na_media_fica_calado),
    ("reabastecimento não conta como consumo", test_reabastecimento_nao_conta),
    ("combustível não fecha a corrida", test_combustivel_nao_fecha_a_corrida),
    ("combustível suficiente fica calado", test_combustivel_suficiente_fica_calado),
]:
    check(nome, fn)

print()
fails = [r for r in results if not r[1]]
for nome, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
