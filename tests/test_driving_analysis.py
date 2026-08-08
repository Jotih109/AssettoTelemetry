"""
tests/test_driving_analysis.py — Medidas de pilotagem
=====================================================
Aqui se cobra o NÚMERO, não a frase. Se a medida estiver errada, o engenheiro
vai dizer com toda a confiança uma coisa que o gráfico desmente — que é o pior
tipo de erro num painel de engenharia.

Cada teste monta um canal onde a resposta é conhecida de cabeça (metade das
freadas com o pé no gás, cinco degraus ao soltar o freio, quatro trocas a
4.000 giros) e cobra exatamente esse valor. E cobra `None` quando o canal não
existe: ghost antigo não tem marcha nem coordenada, e nesse caso a medida
precisa se calar em vez de chutar.

    python tests/test_driving_analysis.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.corner_analysis as ca
import core.driving_analysis as da

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


def canais(n=200, length=5000.0, **overrides):
    tel = {
        "times": [i * 0.05 for i in range(n)],
        "distance": [i * (length / n) for i in range(n)],
        "speed": [150.0] * n,
        "gas": [1.0] * n,
        "brake": [0.0] * n,
        "rpm": [7800.0] * n,
        "gear": [5] * n,
        "steer": [0.0] * n,
        "g_lat": [0.0] * n,
        "car_x": [0.0] * n,
        "car_z": [0.0] * n,
    }
    tel.update(overrides)
    return da.LapChannels(tel)


# ---------------------------------------------------------------------------
# Corte por metragem
# ---------------------------------------------------------------------------

def test_janela_por_metragem():
    ch = canais(200, length=5000.0)              # 25 m entre amostras
    i0, i1 = ch.window(1000.0, 1500.0)
    assert ch.distance[i0] >= 1000.0 and ch.distance[i1 - 1] <= 1500.0
    assert i1 - i0 == 21, i1 - i0
    return f"{i1 - i0} amostras entre 1000 e 1500 m"


def test_janela_de_curva():
    ch = canais(200, length=5000.0)
    c = ca.Corner(index=1, name="C1", start=0.2, end=0.3)
    i0, i1 = ch.corner_window(c, 5000.0)
    assert ch.distance[i0] >= 1000.0 and ch.distance[i1 - 1] <= 1500.0
    return f"curva 0.2-0.3 => {i1 - i0} amostras"


def test_indice_mais_proximo():
    ch = canais(200, length=5000.0)
    assert ch.index_at(1010.0) == 40, ch.index_at(1010.0)   # 1000 m
    assert ch.index_at(1020.0) == 41, ch.index_at(1020.0)   # 1025 m
    return "arredonda para a amostra mais próxima"


def test_marcha_do_jogo_para_a_do_piloto():
    """No AC 0 = ré, 1 = neutro, 2 = primeira."""
    assert da.LapChannels.gear_number(2) == 1
    assert da.LapChannels.gear_number(5) == 4
    assert da.LapChannels.gear_number(1) is None       # neutro
    assert da.LapChannels.gear_number(0) is None       # ré
    return "2 => 1ª, 5 => 4ª, neutro e ré => None"


# ---------------------------------------------------------------------------
# Pedais
# ---------------------------------------------------------------------------

def test_sobreposicao_de_pedais():
    n = 200
    freio, gas = [0.0] * n, [0.0] * n
    for i in range(40, 100):
        freio[i] = 0.7                           # 60 amostras de freada
    for i in range(40, 70):
        gas[i] = 0.6                             # metade com o pé no gás
    fr = da.brake_throttle_overlap(canais(n, brake=freio, gas=gas))
    assert abs(fr - 0.5) < 0.02, fr
    return f"{fr * 100:.0f}% da freada com os dois pedais"


def test_sem_sobreposicao():
    n = 200
    freio, gas = [0.0] * n, [1.0] * n
    for i in range(40, 100):
        freio[i], gas[i] = 0.7, 0.0
    assert da.brake_throttle_overlap(canais(n, brake=freio, gas=gas)) == 0.0
    return "pedais separados: zero"


def test_sobreposicao_sem_freada_e_none():
    """Volta sem freio nenhum não tem o que medir."""
    assert da.brake_throttle_overlap(canais()) is None
    return "sem freada: None"


def test_degraus_ao_soltar_o_freio():
    n = 400
    freio = [0.0] * n
    for k in range(5):
        base = 40 + k * 60
        for i in range(base, base + 10):
            freio[i] = 0.9
        for i in range(base + 10, base + 16):
            freio[i] = 0.4
        for i in range(base + 16, base + 22):
            freio[i] = 0.7
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.zones == 5 and rep.jitter_zones == 5, (rep.zones, rep.jitter_zones)
    assert rep.fraction == 1.0
    return "5 freadas, 5 com repisada"


def test_uma_marcacao_por_freada():
    """Três repisadas na MESMA freada contam como uma zona, não três."""
    n = 400
    freio = [0.0] * n
    for k in range(4):
        base = 40 + k * 80
        for j in range(3):                       # três degraus na mesma freada
            i = base + j * 12
            for x in range(i, i + 6):
                freio[x] = 0.9
            for x in range(i + 6, i + 12):
                freio[x] = 0.4
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.zones == 4 and rep.jitter_zones == 4, (rep.zones, rep.jitter_zones)
    return "4 freadas, 4 zonas marcadas (não 12)"


def test_freio_solto_limpo():
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 40 + k * 40
        for j in range(20):                      # rampa descendo, sem voltar
            freio[base + j] = 0.9 - j * 0.045
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.zones == 4 and rep.jitter_zones == 0, (rep.zones, rep.jitter_zones)
    return "alívio contínuo: zero repisadas em 4 freadas"


def test_modulacao_pequena_nao_e_degrau():
    """Repisada de 5% do curso é modulação normal, não erro de pilotagem."""
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 40 + k * 40
        for j in range(20):
            freio[base + j] = 0.9 - j * 0.03
        freio[base + 12] += 0.05                 # mexidinha no meio
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.jitter_zones == 0, rep.jitter_zones
    return "5% do pedal: silêncio"


def test_largou_o_freio_de_uma_vez():
    """
    Pedal cheio e, no quadro seguinte, pedal solto: soltura seca.

    A 20 Hz (0.05 s por amostra) uma soltura de 2 amostras leva 0.10 s — abaixo
    do limite de 0.15 s.
    """
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 40
        for i in range(base, base + 20):
            freio[i] = 0.9                       # freada forte...
        freio[base + 20] = 0.3                   # ...e o pé sai em 2 amostras
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.zones == 4 and rep.abrupt_zones == 4, (rep.zones, rep.abrupt_zones)
    assert rep.abrupt_fraction == 1.0
    assert rep.jitter_zones == 0, "soltura seca virou degrau"
    return "4 freadas largadas de uma vez"


def test_soltura_progressiva():
    """Alívio ao longo de meio segundo: é o certo, tem que ficar calado."""
    n = 300
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 60
        for i in range(base, base + 15):
            freio[i] = 0.9
        for j in range(15):                      # 15 amostras = 0.75 s soltando
            freio[base + 15 + j] = 0.9 - j * 0.06
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.abrupt_zones == 0, rep.abrupt_zones
    assert rep.jitter_zones == 0, rep.jitter_zones
    return "alívio de 0.75s em 4 freadas: silêncio"


def test_freada_leve_nao_conta_como_seca():
    """Tirar o pé de um toque leve no freio não é largar a freada."""
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 40
        for i in range(base, base + 20):
            freio[i] = 0.3                       # nunca chegou à freada forte
    rep = da.brake_release_report(canais(n, brake=freio))
    assert rep.abrupt_zones == 0, rep.abrupt_zones
    return "toque leve: não conta"


def test_sem_canal_de_tempo_nao_mede_soltura():
    n = 200
    freio = [0.0] * n
    for k in range(4):
        base = 30 + k * 40
        for i in range(base, base + 20):
            freio[i] = 0.9
    rep = da.brake_release_report(canais(n, brake=freio, times=[]))
    assert rep is not None and rep.zones == 4
    assert rep.abrupt_zones is None and rep.abrupt_fraction is None
    return "sem times: soltura não medida, resto continua"


def test_poucas_freadas_nao_medem():
    n = 200
    freio = [0.0] * n
    for i in range(40, 60):
        freio[i] = 0.9
    assert da.brake_release_report(canais(n, brake=freio)) is None
    return "1 freada não é amostra: None"


def test_curva_de_gas_cheio():
    n = 200
    freio = [0.0] * n
    for i in range(100, 120):
        freio[i] = 0.8
    ch = canais(n, brake=freio)
    assert da.is_flat_out(ch, 0, 50) is True
    assert da.is_flat_out(ch, 95, 130) is False
    return "detecta trecho sem freio"


# ---------------------------------------------------------------------------
# Volante
# ---------------------------------------------------------------------------

def test_taxa_de_volante():
    n = 200
    # 10 graus por amostra, amostras de 0.05 s => 200 graus por segundo
    steer = [10.0 * i for i in range(n)]
    taxa = da.steering_rate(canais(n, steer=steer))
    assert abs(taxa - 200.0) < 1.0, taxa
    return f"{taxa:.0f} graus por segundo"


def test_taxa_de_volante_ignora_reta():
    """Volante parado no zero não pode diluir a média das curvas."""
    n = 200
    steer = [0.0] * n
    for i in range(100, 200):
        steer[i] = 10.0 * (i - 100)
    taxa = da.steering_rate(canais(n, steer=steer))
    assert abs(taxa - 200.0) < 5.0, taxa
    return f"{taxa:.0f} graus por segundo, sem contar a reta"


def test_subesterco():
    n = 200
    steer, g_lat = [0.0] * n, [0.0] * n
    for i in range(0, 100):
        steer[i], g_lat[i] = 100.0, 0.2          # muito volante, pouco G
    for i in range(100, 200):
        steer[i], g_lat[i] = 20.0, 1.5
    fr = da.understeer_fraction(canais(n, steer=steer, g_lat=g_lat))
    assert fr == 1.0, fr                         # todo o trecho de volante alto
    return "100% do trecho de curva travado"


def test_sem_subesterco():
    n = 200
    steer, g_lat = [0.0] * n, [0.0] * n
    for i in range(0, 100):
        steer[i], g_lat[i] = 100.0, 1.5          # vira o volante, o carro vira
    fr = da.understeer_fraction(canais(n, steer=steer, g_lat=g_lat))
    assert fr == 0.0, fr
    return "carro acompanhando o volante: zero"


def test_subesterco_ignora_carro_parado():
    n = 200
    steer = [100.0] * n
    g_lat = [0.1] * n
    ch = canais(n, steer=steer, g_lat=g_lat, speed=[5.0] * n)
    assert da.understeer_fraction(ch) is None
    return "carro parado não gera subesterço"


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

def test_trocas_cedo():
    n = 200
    rpm = [4000.0] * n
    gear = [2] * n
    for k in range(1, 5):
        i = k * 40
        gear[i:] = [2 + k] * (n - i)
    rep = da.shift_report(canais(n, rpm=rpm, gear=gear), max_rpm=8000.0)
    assert rep.upshifts == 4 and rep.early == 4, (rep.upshifts, rep.early)
    assert rep.worst_early_rpm == 4000.0
    assert rep.early_fraction == 1.0
    return "4 trocas, todas a 4.000 de 8.000 giros"


def test_trocas_na_faixa():
    n = 200
    rpm = [7900.0] * n
    gear = [2] * n
    for k in range(1, 5):
        i = k * 40
        gear[i:] = [2 + k] * (n - i)
    rep = da.shift_report(canais(n, rpm=rpm, gear=gear), max_rpm=8000.0)
    assert rep.upshifts == 4 and rep.early == 0, (rep.upshifts, rep.early)
    return "4 trocas na faixa de potência"


def test_reducao_nao_conta_como_troca():
    n = 200
    gear = [5] * n
    gear[100:] = [3] * 100                       # reduziu
    rep = da.shift_report(canais(n, gear=gear), max_rpm=8000.0)
    assert rep is None, "reducao virou troca"
    return "reduções ignoradas"


def test_corte_do_motor():
    n = 200
    gear = [2] * n
    gear[100:] = [3] * 100
    rep = da.shift_report(canais(n, rpm=[8000.0] * n, gear=gear), max_rpm=8000.0)
    assert rep.on_limiter >= n - 2, rep.on_limiter
    return f"{rep.on_limiter} amostras no corte"


def test_sem_rpm_maximo():
    n = 200
    gear = [2] * n
    gear[100:] = [3] * 100
    assert da.shift_report(canais(n, gear=gear), max_rpm=0.0) is None
    return "sem max_rpm: None"


def test_marcha_e_velocidade_por_metragem():
    n = 200
    gear = [5] * n
    gear[100:] = [3] * 100
    speed = [100.0 + i for i in range(n)]
    ch = canais(n, gear=gear, speed=speed, length=5000.0)
    assert da.gear_at(ch, 1000.0) == 4, da.gear_at(ch, 1000.0)   # índice 40
    assert da.gear_at(ch, 3000.0) == 2, da.gear_at(ch, 3000.0)   # índice 120
    assert da.speed_at(ch, 1000.0) == 140.0
    return "marcha e velocidade lidas na metragem certa"


# ---------------------------------------------------------------------------
# Traçado
# ---------------------------------------------------------------------------

def test_desvio_de_tracado():
    n = 200
    lap = canais(n, car_x=[10.0] * n, car_z=[3.0] * n)
    ref = canais(n, car_x=[10.0] * n, car_z=[0.0] * n)
    d = da.line_deviation_m(lap, ref, 1000.0, 1500.0)
    assert abs(d - 3.0) < 0.01, d
    return f"{d:.1f} m fora da linha"


def test_mesma_linha_da_zero():
    n = 200
    lap = canais(n, car_x=[float(i) for i in range(n)])
    ref = canais(n, car_x=[float(i) for i in range(n)])
    assert da.line_deviation_m(lap, ref, 1000.0, 1500.0) == 0.0
    return "mesma linha: zero"


def test_sem_coordenadas_e_none():
    """Ghost antigo não gravou car_x/car_z: a medida se cala."""
    n = 200
    lap = canais(n, car_x=[], car_z=[])
    ref = canais(n)
    assert da.line_deviation_m(lap, ref, 1000.0, 1500.0) is None
    return "sem coordenadas: None"


def test_canais_vazios_nao_quebram():
    """Volta sem nada gravado não pode explodir em nenhuma medida."""
    vazio = da.LapChannels({})
    assert len(vazio) == 0
    assert da.brake_throttle_overlap(vazio) is None
    assert da.brake_release_report(vazio) is None
    assert da.steering_rate(vazio) is None
    assert da.understeer_fraction(vazio) is None
    assert da.shift_report(vazio, 8000.0) is None
    assert da.gear_at(vazio, 100.0) is None
    assert da.speed_at(vazio, 100.0) is None
    assert da.line_deviation_m(vazio, vazio, 0.0, 100.0) is None
    assert vazio.window(0.0, 100.0) == (0, 0)
    return "todas as medidas devolvem None"


for nome, fn in [
    ("janela por metragem", test_janela_por_metragem),
    ("janela de uma curva", test_janela_de_curva),
    ("índice da amostra mais próxima", test_indice_mais_proximo),
    ("marcha do jogo vira marcha do piloto", test_marcha_do_jogo_para_a_do_piloto),
    ("pedais: sobreposição freio/acelerador", test_sobreposicao_de_pedais),
    ("pedais: sem sobreposição", test_sem_sobreposicao),
    ("pedais: sem freada é None", test_sobreposicao_sem_freada_e_none),
    ("pedais: degraus ao soltar o freio", test_degraus_ao_soltar_o_freio),
    ("pedais: uma marcação por freada", test_uma_marcacao_por_freada),
    ("pedais: alívio contínuo não é degrau", test_freio_solto_limpo),
    ("pedais: modulação pequena não é degrau", test_modulacao_pequena_nao_e_degrau),
    ("pedais: largou o freio de uma vez", test_largou_o_freio_de_uma_vez),
    ("pedais: soltura progressiva fica calada", test_soltura_progressiva),
    ("pedais: freada leve não é soltura seca", test_freada_leve_nao_conta_como_seca),
    ("pedais: sem canal de tempo não mede soltura", test_sem_canal_de_tempo_nao_mede_soltura),
    ("pedais: poucas freadas não medem", test_poucas_freadas_nao_medem),
    ("pedais: trecho de gás cheio", test_curva_de_gas_cheio),
    ("volante: taxa em graus por segundo", test_taxa_de_volante),
    ("volante: reta não entra na média", test_taxa_de_volante_ignora_reta),
    ("volante: subesterço", test_subesterco),
    ("volante: carro acompanhando", test_sem_subesterco),
    ("volante: carro parado não conta", test_subesterco_ignora_carro_parado),
    ("motor: trocas cedo demais", test_trocas_cedo),
    ("motor: trocas na faixa", test_trocas_na_faixa),
    ("motor: redução não é troca", test_reducao_nao_conta_como_troca),
    ("motor: batendo no corte", test_corte_do_motor),
    ("motor: sem RPM máximo é None", test_sem_rpm_maximo),
    ("motor: marcha e velocidade por metragem", test_marcha_e_velocidade_por_metragem),
    ("traçado: desvio em metros", test_desvio_de_tracado),
    ("traçado: mesma linha dá zero", test_mesma_linha_da_zero),
    ("traçado: sem coordenadas é None", test_sem_coordenadas_e_none),
    ("canais vazios não quebram nada", test_canais_vazios_nao_quebram),
]:
    check(nome, fn)

print()
fails = [r for r in results if not r[1]]
for nome, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
