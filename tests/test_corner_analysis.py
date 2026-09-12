"""
tests/test_corner_analysis.py — Análise Curva a Curva
=====================================================
Testa a camada analítica com voltas sintéticas, onde a resposta certa é
conhecida por construção:

  * mapeamento manual em posição relativa E em metros
  * detecção automática por Força G lateral (com histerese e fusão)
  * reconstrução do G lateral pela curvatura (voltas antigas, sem o canal)
  * ponto de frenagem, V_min, ponto de retomada e delta por curva
  * ida e volta do JSON em track_maps/

Não precisa de PyQt nem do jogo.

    python tests/test_corner_analysis.py
"""

import json
import math
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.corner_analysis as ca

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=4).strip().splitlines()[-1]))


# ---------------------------------------------------------------------------
# Volta sintética: reta (0–1000 m) → freada → curva (1200–1500 m) → reta
# ---------------------------------------------------------------------------

TRACK_LENGTH = 2000.0


def make_lap(speed_scale=1.0, brake_at=1000.0, apex_speed=90.0,
             throttle_at=1450.0, step=5.0, with_g=True):
    """
    Constrói uma volta em passos de `step` metros.

    O tempo é integrado da velocidade, então o d.t entre duas voltas reflete de
    verdade a diferença de velocidade — é o que o teste de delta verifica.
    """
    telemetry = {k: [] for k in ("times", "distance", "speed", "gas", "brake", "g_lat")}
    t = 0.0
    d = 0.0
    while d <= TRACK_LENGTH:
        if d < brake_at:
            speed = 250.0 * speed_scale
            brake, gas = 0.0, 1.0
            g_lat = 0.0
        elif d < 1200.0:
            frac = (d - brake_at) / (1200.0 - brake_at)
            speed = (250.0 - frac * (250.0 - apex_speed)) * speed_scale
            brake, gas = 0.9, 0.0
            g_lat = 0.1
        elif d < 1500.0:
            speed = apex_speed * speed_scale
            brake = 0.0
            gas = 1.0 if d >= throttle_at else 0.4
            g_lat = 1.6                      # dentro da curva
        else:
            speed = min(250.0, apex_speed + (d - 1500.0) * 0.4) * speed_scale
            brake = 0.0
            gas = 1.0 if d >= throttle_at else 0.4
            g_lat = 0.05

        telemetry["distance"].append(d)
        telemetry["times"].append(t)
        telemetry["speed"].append(speed)
        telemetry["gas"].append(gas)
        telemetry["brake"].append(brake)
        telemetry["g_lat"].append(g_lat)

        t += step / max(1.0, speed / 3.6)
        d += step

    if not with_g:
        del telemetry["g_lat"]
    return telemetry


CORNER = ca.Corner(index=1, name="Curva 1", start=1200.0 / TRACK_LENGTH,
                   end=1500.0 / TRACK_LENGTH, direction="R")


# ---------------------------------------------------------------------------
# Mapeamento
# ---------------------------------------------------------------------------

def test_parse_relative():
    cmap = ca.parse_corner_map({
        "track": "Teste",
        "track_length": TRACK_LENGTH,
        "corners": [
            {"name": "B", "start": 0.6, "end": 0.75},
            {"name": "A", "start": 0.1, "end": 0.2, "direction": "l"},
        ],
    })
    assert cmap is not None
    assert len(cmap.corners) == 2
    # Ordenadas pela posição na pista e renumeradas
    assert [c.name for c in cmap.corners] == ["A", "B"]
    assert [c.index for c in cmap.corners] == [1, 2]
    assert cmap.corners[0].direction == "L"
    return f"{[c.name for c in cmap.corners]}"


def test_parse_meters():
    cmap = ca.parse_corner_map({
        "track_length": 2000.0,
        "corners": [{"name": "M", "start_m": 400, "end_m": 600}],
    })
    assert cmap is not None
    c = cmap.corners[0]
    assert abs(c.start - 0.2) < 1e-9 and abs(c.end - 0.3) < 1e-9
    assert abs(c.start_m(2000.0) - 400.0) < 1e-6
    return f"start={c.start} end={c.end}"


def test_parse_rejects_garbage():
    # Limites invertidos, fora de faixa, sem campos, tipo errado: todos caem
    cmap = ca.parse_corner_map({
        "corners": [
            {"name": "invertida", "start": 0.8, "end": 0.2},
            {"name": "fora", "start": -0.1, "end": 0.5},
            {"name": "acima", "start": 0.5, "end": 1.4},
            {"name": "sem limites"},
            "nem dicionario",
            {"name": "boa", "start": 0.3, "end": 0.4},
        ]
    })
    assert cmap is not None, "a curva válida deveria sobreviver"
    assert len(cmap.corners) == 1 and cmap.corners[0].name == "boa"
    # Nenhuma curva válida => None (o chamador cai no fallback)
    assert ca.parse_corner_map({"corners": [{"start": 0.9, "end": 0.1}]}) is None
    assert ca.parse_corner_map({}) is None
    assert ca.parse_corner_map(None) is None
    return "só a curva válida sobrou"


def test_slug():
    assert ca.track_slug("Autodromo Jose Carlos Pace — Grand Prix (MOCK)") == \
        "autodromo_jose_carlos_pace_grand_prix_mock"
    assert ca.track_slug("") == "unknown_track"
    assert ca.track_slug("ks_barcelona/layout") == "ks_barcelona_layout"
    return ca.track_slug("Spa-Francorchamps")


def test_save_load_roundtrip():
    """Grava, lê de volta e confirma a precedência do manual sobre o automático."""
    work = tempfile.mkdtemp(prefix="ac_corners_")
    original = ca.corner_maps_dir
    ca.corner_maps_dir = lambda: work
    try:
        auto = ca.CornerMap(track="Pista X", track_length=TRACK_LENGTH,
                            corners=[ca.Corner(1, "C1", 0.1, 0.2)], source="auto")
        ca.save_corner_map(auto, auto=True)

        loaded = ca.load_corner_map("Pista X", TRACK_LENGTH)
        assert loaded is not None and loaded.source == "auto"
        assert loaded.corners[0].name == "C1"

        manual = ca.CornerMap(track="Pista X", track_length=TRACK_LENGTH,
                              corners=[ca.Corner(1, "Junção", 0.3, 0.4)], source="manual")
        ca.save_corner_map(manual, auto=False)

        loaded = ca.load_corner_map("Pista X", TRACK_LENGTH)
        assert loaded.source == "manual", "o manual precisa vencer o automático"
        assert loaded.corners[0].name == "Junção"

        assert ca.load_corner_map("Pista Inexistente", TRACK_LENGTH) is None
        return "manual vence auto"
    finally:
        ca.corner_maps_dir = original
        shutil.rmtree(work, ignore_errors=True)


def test_corrupt_map_file():
    """Arquivo truncado/corrompido é ignorado, não derruba nada."""
    work = tempfile.mkdtemp(prefix="ac_corners_")
    original = ca.corner_maps_dir
    ca.corner_maps_dir = lambda: work
    try:
        with open(os.path.join(work, "pista_y.json"), "w", encoding="utf-8") as f:
            f.write('{"corners": [{"start": 0.1,')   # JSON truncado
        assert ca.load_corner_map("Pista Y", TRACK_LENGTH) is None
        return "ignorado sem exceção"
    finally:
        ca.corner_maps_dir = original
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Detecção automática
# ---------------------------------------------------------------------------

def test_detect_from_g_lat():
    corners = ca.detect_corners(make_lap(), TRACK_LENGTH)
    assert len(corners) == 1, f"esperava 1 curva, achei {len(corners)}"
    c = corners[0]
    start_m, end_m = c.start_m(TRACK_LENGTH), c.end_m(TRACK_LENGTH)
    # A suavização espalha os limites em alguns metros; 60 m de tolerância
    assert abs(start_m - 1200.0) < 60.0, f"início em {start_m:.0f}m"
    assert abs(end_m - 1500.0) < 60.0, f"fim em {end_m:.0f}m"
    assert c.name == "Curva 1" and c.direction == "R"
    return f"{c.name} {start_m:.0f}–{end_m:.0f}m ({c.direction})"


def test_detect_ignores_noise():
    """Um pico curtíssimo de G (correção de volante) não é uma curva."""
    lap = make_lap()
    for i in range(20, 23):
        lap["g_lat"][i] = 1.2
    corners = ca.detect_corners(lap, TRACK_LENGTH)
    assert len(corners) == 1, f"o pico virou curva: {len(corners)} curvas"
    return "pico de 15 m descartado"


def test_detect_merges_esses():
    """Dois arcos separados por menos de MERGE_GAP_M contam como uma curva."""
    lap = make_lap()
    for i, d in enumerate(lap["distance"]):
        if 1200.0 <= d < 1330.0 or 1350.0 <= d < 1500.0:
            lap["g_lat"][i] = 1.6
        elif 1330.0 <= d < 1350.0:
            lap["g_lat"][i] = 0.05      # respiro de 20 m entre os arcos
    corners = ca.detect_corners(lap, TRACK_LENGTH)
    assert len(corners) == 1, f"o esse virou {len(corners)} curvas"
    return "arcos fundidos"


def test_g_lat_from_curvature():
    """
    Volta antiga (sem canal g_lat): o G lateral vem da curvatura do traçado.

    Círculo de raio 50 m a 90 km/h => a_lat = v²/r ≈ 12.5 m/s² ≈ 1.27 g.
    """
    n = 240
    radius = 50.0
    speed_kmh = 90.0
    telemetry = {
        "times": [i * 0.1 for i in range(n)],
        "distance": [i * 2.0 for i in range(n)],
        "speed": [speed_kmh] * n,
        "car_x": [radius * math.cos(i * 2 * math.pi / n) for i in range(n)],
        "car_z": [radius * math.sin(i * 2 * math.pi / n) for i in range(n)],
    }
    g = ca.lateral_g_series(telemetry)
    middle = g[n // 4]
    expected = ((speed_kmh / 3.6) ** 2 / radius) / 9.81
    assert abs(middle - expected) < 0.15, f"g={middle:.2f}, esperado {expected:.2f}"
    return f"g={middle:.2f} (esperado {expected:.2f})"


def test_build_auto_map():
    cmap = ca.build_auto_corner_map("Pista Auto", make_lap(), TRACK_LENGTH)
    assert cmap is not None and cmap.source == "auto"
    assert cmap.track == "Pista Auto" and cmap.track_length == TRACK_LENGTH
    # Volta em linha reta não gera mapa nenhum
    flat = make_lap()
    flat["g_lat"] = [0.0] * len(flat["g_lat"])
    assert ca.build_auto_corner_map("Reta", flat, TRACK_LENGTH) is None
    return f"{len(cmap.corners)} curva(s)"


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def test_metrics_basic():
    m = ca.analyze_corner(make_lap(), CORNER, TRACK_LENGTH)
    assert m.braking_point_m is not None
    assert abs(m.braking_point_m - 1000.0) <= 5.0, f"freio em {m.braking_point_m}"
    assert abs(m.v_min - 90.0) < 1.0, f"v_min={m.v_min}"
    assert 1200.0 <= m.v_min_m <= 1500.0
    assert m.throttle_point_m is not None
    assert abs(m.throttle_point_m - 1450.0) <= 5.0, f"retomada em {m.throttle_point_m}"
    assert m.section_time is not None and m.section_time > 0
    return (f"freio {m.braking_point_m:.0f}m, v_min {m.v_min:.0f}, "
            f"retomada {m.throttle_point_m:.0f}m, {m.section_time:.3f}s")


def test_metrics_missing_channels():
    """Volta vazia ou sem canais não levanta exceção — só devolve None."""
    m = ca.analyze_corner({}, CORNER, TRACK_LENGTH)
    assert m.v_min is None and m.section_time is None and not m.has_data
    m = ca.analyze_corner({"distance": [0.0, 100.0], "times": [0.0, 1.0]},
                          CORNER, TRACK_LENGTH)
    assert m.v_min is None and m.braking_point_m is None
    return "sem exceção"


def test_metrics_no_braking():
    """Curva tomada sem freio: ponto de frenagem fica None, o resto é medido."""
    lap = make_lap()
    lap["brake"] = [0.0] * len(lap["brake"])
    m = ca.analyze_corner(lap, CORNER, TRACK_LENGTH)
    assert m.braking_point_m is None
    assert m.v_min is not None
    return "freio None, v_min medido"


def test_metrics_no_full_throttle():
    """Se o acelerador nunca chega a 100%, a retomada fica None."""
    lap = make_lap()
    lap["gas"] = [min(0.8, g) for g in lap["gas"]]
    m = ca.analyze_corner(lap, CORNER, TRACK_LENGTH)
    assert m.throttle_point_m is None
    return "retomada None"


def test_compare_slower_lap():
    """
    Volta 8% mais lenta na curva: perde tempo, perde V_min.

    Como o ponto de frenagem e a retomada são iguais, os deltas de distância
    ficam em zero — é a checagem de que nada foi trocado de lugar.
    """
    fast = make_lap(apex_speed=90.0)
    slow = make_lap(apex_speed=83.0)
    cmps = ca.compare_laps(slow, fast, [CORNER], TRACK_LENGTH)
    assert len(cmps) == 1
    c = cmps[0]
    assert c.delta_time is not None and c.delta_time > 0, f"d.t={c.delta_time}"
    assert c.delta_v_min is not None and c.delta_v_min < 0, f"d.v={c.delta_v_min}"
    assert abs(c.delta_braking_m) < 1e-6
    return f"d.t={c.delta_time:+.3f}s d.v_min={c.delta_v_min:+.1f}"


def test_compare_later_braking():
    late = make_lap(brake_at=1060.0)
    early = make_lap(brake_at=1000.0)
    c = ca.compare_laps(late, early, [CORNER], TRACK_LENGTH)[0]
    assert c.delta_braking_m is not None
    assert abs(c.delta_braking_m - 60.0) <= 10.0, f"d.freio={c.delta_braking_m}"
    return f"d.freio={c.delta_braking_m:+.0f}m"


def test_compare_later_throttle():
    late = make_lap(throttle_at=1520.0)
    early = make_lap(throttle_at=1450.0)
    c = ca.compare_laps(late, early, [CORNER], TRACK_LENGTH)[0]
    assert c.delta_throttle_m is not None
    assert abs(c.delta_throttle_m - 70.0) <= 10.0, f"d.retomada={c.delta_throttle_m}"
    return f"d.retomada={c.delta_throttle_m:+.0f}m"


def test_compare_without_reference():
    """Sem referência, as métricas saem e os deltas ficam None."""
    cmps = ca.compare_laps(make_lap(), {}, [CORNER], TRACK_LENGTH)
    c = cmps[0]
    assert c.ref is None
    assert c.delta_time is None and c.delta_v_min is None
    assert c.delta_braking_m is None and c.delta_throttle_m is None
    assert c.lap.v_min is not None, "a volta em si continua sendo medida"
    return "deltas None, métricas presentes"


def test_sequential_corners_dont_share_braking():
    """
    Duas curvas em sequência não podem apontar a MESMA freada.

    A janela de busca do ponto de frenagem olha 300 m para trás; sem limitar
    pelo fim da curva anterior, a curva 2 encontraria a freada da curva 1.
    """
    c1 = ca.Corner(1, "C1", 1200.0 / TRACK_LENGTH, 1400.0 / TRACK_LENGTH)
    c2 = ca.Corner(2, "C2", 1450.0 / TRACK_LENGTH, 1650.0 / TRACK_LENGTH)
    lap = make_lap()   # a única freada é em 1000 m, antes da C1
    m1, m2 = ca.analyze_lap(lap, [c1, c2], TRACK_LENGTH)
    assert m1.braking_point_m is not None
    assert abs(m1.braking_point_m - 1000.0) <= 5.0
    assert m2.braking_point_m is None, \
        f"C2 herdou a freada da C1 ({m2.braking_point_m})"
    return f"C1={m1.braking_point_m:.0f}m, C2=None"


def test_worst_corner():
    c1 = ca.Corner(1, "C1", 0.10, 0.20)
    c2 = ca.Corner(2, "C2", 0.60, 0.75)
    fast = make_lap(apex_speed=90.0)
    slow = make_lap(apex_speed=80.0)
    cmps = ca.compare_laps(slow, fast, [CORNER, c1, c2], TRACK_LENGTH)
    worst = ca.worst_corner(cmps)
    assert worst is not None and worst.corner is CORNER, "a curva real é onde se perde"
    # Volta idêntica à referência: ninguém perde nada
    assert ca.worst_corner(ca.compare_laps(fast, fast, [CORNER], TRACK_LENGTH)) is None
    return f"pior: {worst.corner.name} ({worst.delta_time:+.3f}s)"


def test_lap_coverage():
    full = make_lap()
    assert ca.lap_coverage(full, TRACK_LENGTH) > 0.99

    # Volta gravada a partir de 54% da pista (app aberto no meio da volta)
    half = {k: v[len(v) // 2:] for k, v in full.items()}
    cov = ca.lap_coverage(half, TRACK_LENGTH)
    assert 0.45 < cov < 0.55, cov
    assert ca.lap_coverage({}, TRACK_LENGTH) == 0.0
    return f"inteira=1.00 metade={cov:.2f}"


def test_auto_map_records_coverage():
    """
    O mapa automático registra de quanto da volta ele saiu.

    Foi um mapa tirado de meia volta que deixou o Spa com curvas só até 54% da
    pista — as faixas nos gráficos não batiam com a volta, e nada refazia o
    arquivo porque ele "existia".
    """
    full = make_lap()
    cmap = ca.build_auto_corner_map("Pista", full, TRACK_LENGTH)
    assert cmap.coverage > 0.99, cmap.coverage
    # Cobertura inteira, mas UMA volta só: continua provisório de propósito.
    # Uma volta sozinha não tem como saber se o que ela mostrou é a pista ou
    # um corte de pista — é para isso que existe o consenso.
    assert cmap.laps_used == 1
    assert cmap.is_provisional, "mapa de uma volta ainda pode ser refeito"

    half = {k: v[len(v) // 2:] for k, v in full.items()}
    partial = ca.build_auto_corner_map("Pista", half, TRACK_LENGTH)
    assert partial is not None, "a meia volta ainda detecta a curva que ela contém"
    assert partial.coverage < 0.6, partial.coverage
    assert partial.is_provisional, "mapa de meia volta tem de ser provisório"

    # Mapa manual nunca é provisório, mesmo sem o campo coverage no arquivo
    manual = ca.parse_corner_map({"corners": [{"start": 0.1, "end": 0.2}]})
    assert manual.coverage == 1.0 and not manual.is_provisional
    return f"inteira={cmap.coverage:.2f} parcial={partial.coverage:.2f}"


def test_legacy_auto_map_is_provisional():
    """
    Arquivo .auto.json gravado antes do campo `coverage` (como o spa.auto.json
    que ficou com curvas só até 54%) vale como provisório, para ser refeito.
    """
    work = tempfile.mkdtemp(prefix="ac_corners_")
    original = ca.corner_maps_dir
    ca.corner_maps_dir = lambda: work
    try:
        # Sem "coverage": exatamente o formato antigo
        with open(os.path.join(work, "spa.auto.json"), "w", encoding="utf-8") as f:
            json.dump({"track": "Spa", "track_length": 7004.0, "source": "auto",
                       "corners": [{"name": "C1", "start": 0.098, "end": 0.132},
                                   {"name": "C2", "start": 0.44, "end": 0.538}]}, f)
        loaded = ca.load_corner_map("Spa", 7004.0)
        assert loaded is not None and loaded.source == "auto"
        assert loaded.coverage == 0.0
        assert loaded.is_provisional, "mapa auto sem coverage tem de ser provisório"

        # Já um manual sem coverage é definitivo
        with open(os.path.join(work, "monza.json"), "w", encoding="utf-8") as f:
            json.dump({"track": "Monza",
                       "corners": [{"name": "T1", "start": 0.1, "end": 0.2}]}, f)
        m = ca.load_corner_map("Monza", 5793.0)
        assert not m.is_provisional
        return "auto sem coverage = provisório, manual = definitivo"
    finally:
        ca.corner_maps_dir = original
        shutil.rmtree(work, ignore_errors=True)


def test_shipped_mock_map():
    """O mapa manual do provider MOCK que vem no repositório é válido."""
    from providers.mock import TRACK_NAME, TRACK_LENGTH as MOCK_LENGTH
    cmap = ca.load_corner_map(TRACK_NAME, MOCK_LENGTH)
    assert cmap is not None, "track_maps/ deveria trazer o mapa da pista MOCK"
    assert cmap.source == "manual"
    assert len(cmap.corners) >= 6
    for c in cmap.corners:
        assert 0.0 <= c.start < c.end <= 1.0
        assert c.length_m(MOCK_LENGTH) > 50.0
    return f"{len(cmap.corners)} curvas, {cmap.corners[0].name}"


# ---------------------------------------------------------------------------
# Detecção: os defeitos que faziam Paul Ricard virar 3 curvas
# ---------------------------------------------------------------------------

def _pista_sintetica(corners, track_length, hz=60.0, arredonda=True):
    """
    Volta sintética a partir de uma lista de curvas.

    `corners` é uma lista de (centro_m, comprimento_m, pico_g, mao). O traçado
    2D é integrado do G lateral e da velocidade, e as coordenadas saem
    arredondadas ao centímetro — como o `lap_library` as grava. Esse
    arredondamento é o ponto: era ele que fazia a curvatura por três pontos
    vizinhos explodir.
    """
    def g_em(d):
        g = 0.0
        for centro, comp, pico, mao in corners:
            meia = comp / 2.0
            dist = abs(d - centro)
            if dist > meia * 1.9:
                continue
            if dist <= meia * 0.55:
                f = 1.0
            else:
                x = min(1.0, (dist - meia * 0.55) / (meia * 1.35))
                f = math.cos(x * math.pi / 2)
            g += mao * pico * f
        return g

    def v_em(d):
        v = 280.0
        for centro, comp, pico, mao in corners:
            meia = comp / 2.0
            dist = abs(d - centro)
            if dist > meia * 4.0:
                continue
            x = min(1.0, dist / (meia * 4.0))
            v = min(v, 110.0 + (280.0 - 110.0) * x * x)
        return v

    t = d = 0.0
    heading = px = pz = 0.0
    dt = 1.0 / hz
    tel = {k: [] for k in ("times", "distance", "speed", "gas", "brake",
                           "g_lat", "car_x", "car_z")}
    while d < track_length:
        v = v_em(d)
        g = g_em(d)
        tel["times"].append(t)
        tel["distance"].append(d)
        tel["speed"].append(v)
        tel["g_lat"].append(g)
        tel["gas"].append(1.0)
        tel["brake"].append(0.0)
        v_ms = v / 3.6
        if v_ms > 1.0:
            heading += (g * 9.81 / v_ms) * dt
        px += v_ms * math.cos(heading) * dt
        pz += v_ms * math.sin(heading) * dt
        tel["car_x"].append(round(px, 2) if arredonda else px)
        tel["car_z"].append(round(pz, 2) if arredonda else pz)
        t += dt
        d += v_ms * dt
    return tel


#: Quinze curvas espaçadas como as de Paul Ricard, com duas chicanes e duas
#: sequências ligadas — a forma de pista que o detector antigo colapsava.
_PISTA_15 = [
    (760, 120, 2.3, +1), (1010, 95, 2.2, -1),
    (1240, 80, 2.0, +1), (1345, 80, 2.0, -1),        # chicane
    (1760, 130, 2.4, +1), (2060, 100, 1.9, -1),
    (3400, 70, 1.8, +1), (3485, 70, 1.8, -1),        # chicane
    (4060, 130, 2.6, +1),
    (4520, 150, 2.1, +1), (4780, 95, 2.0, -1), (4960, 100, 1.9, +1),
    (5240, 120, 2.0, +1), (5400, 110, 1.9, -1),
    (5620, 110, 2.2, +1),
]
_PISTA_15_LEN = 5842.0


def test_reconstrucao_g_nao_explode_com_coordenada_arredondada():
    """
    O bug de fundo: |G| reconstruído da geometria dava 6.8 g de pico.

    A curvatura era medida entre amostras VIZINHAS. A 60 Hz e 100 km/h duas
    amostras ficam a 46 cm, as coordenadas são gravadas ao centímetro, e numa
    reta os três pontos são quase colineares: o ruído de arredondamento
    dominava a conta. Pior, o módulo era tirado ANTES da suavização, então o
    ruído retificado virava um degrau positivo — a volta inteira ficava acima
    do limiar de 0.4 g e a pista toda saía como uma curva só.
    """
    tel = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    real = [abs(v) for v in tel["g_lat"]]
    sem_canal = dict(tel, g_lat=[])
    rec = ca.lateral_g_series(sem_canal)

    pico_real, pico_rec = max(real), max(rec)
    media_real = sum(real) / len(real)
    media_rec = sum(rec) / len(rec)
    assert pico_rec < pico_real * 1.35, \
        f"pico reconstruído {pico_rec:.2f} g contra {pico_real:.2f} g real"
    assert media_rec < media_real * 1.35, \
        f"média reconstruída {media_rec:.2f} g contra {media_real:.2f} g real"
    return (f"pico {pico_rec:.2f} g (real {pico_real:.2f}), "
            f"média {media_rec:.2f} g (real {media_real:.2f})")


def test_detecta_pista_inteira_pelos_dois_caminhos():
    """
    Quinze curvas têm de sair como ~quinze — pelo canal g_lat E pela
    geometria. Antes: 11 pelo canal, 1 pela geometria.
    """
    tel = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    achadas = {}
    for rotulo, t in (("canal", tel), ("geometria", dict(tel, g_lat=[]))):
        corners = ca.detect_corners(t, _PISTA_15_LEN)
        achadas[rotulo] = len(corners)
        assert len(corners) >= 13, \
            f"{rotulo}: {len(corners)} curvas para uma pista de 15"
        # Cada curva detectada tem de cair sobre uma curva real
        for c in corners:
            centro = (c.start + c.end) / 2 * _PISTA_15_LEN
            perto = min(abs(centro - real[0]) for real in _PISTA_15)
            assert perto < 110.0, \
                f"{rotulo}: {c.name} em {centro:.0f}m não casa com curva real"
    return f"canal={achadas['canal']} geometria={achadas['geometria']} (de 15)"


def test_chicane_nao_vira_uma_curva():
    """
    Troca de mão sempre separa: uma chicane são duas curvas, com as duas mãos
    corretas. Antes os sinais opostos se cancelavam na soma e a chicane saía
    como uma curva só, de mão indefinida.
    """
    chicane = [(1000, 80, 2.0, +1), (1105, 80, 2.0, -1)]
    tel = _pista_sintetica(chicane, 2500.0)
    corners = ca.detect_corners(tel, 2500.0)
    assert len(corners) == 2, f"a chicane virou {len(corners)} curva(s)"
    maos = [c.direction for c in corners]
    assert maos == ["R", "L"], f"mãos erradas: {maos}"
    return f"2 curvas, mãos {maos}"


def test_sequencia_ligada_e_dividida_no_vale():
    """
    Duas curvas do MESMO lado, ligadas, em que o G alivia mas nunca chega a
    zero. A histerese sozinha entregava um bloco único; o corte no vale as
    separa.
    """
    seq = [(1000, 120, 2.2, +1), (1260, 110, 2.0, +1)]
    tel = _pista_sintetica(seq, 2600.0)
    corners = ca.detect_corners(tel, 2600.0)
    assert len(corners) == 2, f"a sequência virou {len(corners)} curva(s)"
    assert all(c.direction == "R" for c in corners), "mão errada"
    return "2 curvas do mesmo lado separadas no vale"


def test_curva_longa_unica_nao_e_partida():
    """
    O contrapeso do teste acima: uma curva longa e contínua (uma parabólica)
    continua sendo UMA curva. Um corte por vale agressivo a partiria em duas e
    o coach passaria a inventar um ponto de freada no meio dela.
    """
    tel = _pista_sintetica([(1000, 260, 2.0, +1)], 2600.0)
    corners = ca.detect_corners(tel, 2600.0)
    assert len(corners) == 1, f"a parabólica virou {len(corners)} curvas"
    return "curva longa preservada"


def test_mapa_auto_de_versao_antiga_e_refeito():
    """
    Um `.auto.json` gravado por uma versão ANTERIOR do detector é provisório.

    É o que faz a correção do detector chegar em quem já usou o app. O arquivo
    antigo existe e cobre a volta inteira, então nada pediria para refazê-lo —
    e a pista continuaria com as curvas erradas que o detector antigo achou.
    """
    base = {"track": "Pista X", "track_length": 5842.0, "source": "auto",
            "coverage": 1.0, "laps_used": ca.CONSENSUS_LAPS,
            "corners": [{"name": "C1", "start": 0.1, "end": 0.35},
                        {"name": "C2", "start": 0.55, "end": 0.72}]}

    antigo = ca.parse_corner_map(dict(base))
    antigo.source = "auto"
    assert antigo.detector_version == 1, antigo.detector_version
    assert antigo.is_provisional, "mapa de versão antiga deveria ser refeito"

    atual = ca.parse_corner_map(dict(base, detector_version=ca.DETECTOR_VERSION))
    atual.source = "auto"
    assert not atual.is_provisional, "mapa da versão atual não deve ser refeito"

    manual = ca.parse_corner_map(dict(base, source="manual"))
    manual.source = "manual"
    assert not manual.is_provisional, "mapa manual nunca é refeito"

    # Um arquivo sem `laps_used` saiu de uma volta só: também é refeito
    sem_voltas = dict(base)
    sem_voltas.pop("laps_used")
    uma_volta = ca.parse_corner_map(dict(sem_voltas,
                                         detector_version=ca.DETECTOR_VERSION))
    uma_volta.source = "auto"
    assert uma_volta.laps_used == 1 and uma_volta.is_provisional

    # E o mapa recém-detectado nasce carimbado com a versão corrente
    novo = ca.build_auto_corner_map("Pista X", make_lap(), TRACK_LENGTH)
    assert novo.detector_version == ca.DETECTOR_VERSION
    return f"versão antiga refeita, atual mantida (detector v{ca.DETECTOR_VERSION})"


# ---------------------------------------------------------------------------
# Consenso de voltas limpas — o corte de pista não molda mais o mapa
# ---------------------------------------------------------------------------

def _pista_com_corte(corners, track_length, corta_de, corta_ate):
    """
    A mesma pista, mas com a volta CORTADA num trecho.

    O corte é modelado como o que ele é: o piloto passou reto por onde havia
    curva. O G lateral vai a zero naquele trecho, e no lugar aparece uma
    guinada curta para voltar à pista — que o detector, sozinho, leria como
    uma curva que não existe.
    """
    limpos = [c for c in corners
              if not (corta_de <= c[0] <= corta_ate)]
    # A guinada de volta à pista, logo depois do corte
    limpos.append((corta_ate + 30.0, 70.0, 1.6, -1))
    return _pista_sintetica(sorted(limpos, key=lambda c: c[0]), track_length)


def test_consenso_rejeita_curva_que_so_uma_volta_viu():
    """
    Uma curva que só a volta cortada mostrou não entra no mapa.

    Era o defeito: o mapa saía de UMA volta. Se ela tivesse um corte, a
    geometria do corte virava o mapa — e como o mapa é gravado em disco,
    ficava para sempre.
    """
    limpa = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    cortada = _pista_com_corte(_PISTA_15, _PISTA_15_LEN, 4400.0, 5000.0)

    # Só a volta cortada: a geometria do corte entra (é tudo que se conhece)
    so_cortada = ca.build_consensus_corner_map("P", [cortada], _PISTA_15_LEN)
    assert so_cortada.laps_used == 1
    assert so_cortada.is_provisional, \
        "mapa de uma volta tem de continuar provisório"

    # Com duas voltas limpas ao lado dela, a maioria manda
    mapa = ca.build_consensus_corner_map(
        "P", [cortada, limpa, dict(limpa)], _PISTA_15_LEN)
    assert mapa is not None and mapa.laps_used == 3
    assert not mapa.is_provisional, "3 voltas limpas deviam fixar o mapa"

    # A guinada do corte (≈5030 m) não pode ter virado curva
    centros = [(c.start + c.end) / 2 * _PISTA_15_LEN for c in mapa.corners]
    assert not any(abs(x - 5065.0) < 60.0 for x in centros), \
        f"a geometria do corte entrou no mapa: {[round(x) for x in centros]}"

    # E as curvas de verdade do trecho cortado continuam lá, porque as outras
    # duas voltas as viram
    for real in (4520.0, 4780.0, 4960.0):
        assert any(abs(x - real) < 110.0 for x in centros), \
            f"a curva real em {real:.0f} m foi perdida"
    return (f"{len(mapa.corners)} curvas de 3 voltas; "
            f"corte rejeitado, curvas reais mantidas")


def test_consenso_de_uma_volta_e_igual_a_deteccao_simples():
    """Com uma volta só o resultado é o mesmo — e fica marcado como tal."""
    limpa = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    um = ca.build_consensus_corner_map("P", [limpa], _PISTA_15_LEN)
    direto = ca.detect_corners(limpa, _PISTA_15_LEN)
    assert len(um.corners) == len(direto), f"{len(um.corners)} != {len(direto)}"
    assert um.laps_used == 1 and um.is_provisional
    return f"{len(um.corners)} curvas, provisório (1 volta)"


def test_consenso_ignora_volta_incompleta():
    """
    Meia volta não vota. O voto "não é curva" dela na metade que não percorreu
    derrubaria curvas de verdade.
    """
    limpa = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    metade = {k: v[:len(v) // 2] for k, v in limpa.items()}
    mapa = ca.build_consensus_corner_map(
        "P", [limpa, metade, dict(limpa)], _PISTA_15_LEN)
    assert mapa.laps_used == 2, f"a volta pela metade votou ({mapa.laps_used})"
    inteiro = ca.build_consensus_corner_map("P", [limpa, dict(limpa)],
                                            _PISTA_15_LEN)
    assert len(mapa.corners) == len(inteiro.corners)
    return f"meia volta descartada; {len(mapa.corners)} curvas"


def test_consenso_mantem_a_mao_das_curvas():
    """A mão de cada curva sai da maioria, não da última volta lida."""
    limpa = _pista_sintetica(_PISTA_15, _PISTA_15_LEN)
    mapa = ca.build_consensus_corner_map(
        "P", [limpa, dict(limpa), dict(limpa)], _PISTA_15_LEN)
    maos = [c.direction for c in mapa.corners]
    assert all(m in ("L", "R") for m in maos), maos
    # A chicane de 1240/1345 tem de sair com as duas mãos
    por_centro = {round((c.start + c.end) / 2 * _PISTA_15_LEN): c.direction
                  for c in mapa.corners}
    perto = lambda alvo: next(d for m, d in por_centro.items()
                              if abs(m - alvo) < 80)
    assert perto(1240.0) != perto(1345.0), "a chicane saiu com uma mão só"
    return f"mãos: {''.join(maos)}"


for name, fn in [
    ("mapa manual em posição relativa (ordenado e renumerado)", test_parse_relative),
    ("mapa manual em metros", test_parse_meters),
    ("mapa com linhas inválidas é filtrado", test_parse_rejects_garbage),
    ("slug do nome da pista", test_slug),
    ("gravar/ler mapa e precedência manual > auto", test_save_load_roundtrip),
    ("arquivo de mapa corrompido é ignorado", test_corrupt_map_file),
    ("detecção automática por G lateral", test_detect_from_g_lat),
    ("detecção descarta pico curto de G", test_detect_ignores_noise),
    ("detecção funde esse em uma curva", test_detect_merges_esses),
    ("G lateral reconstruído pela curvatura", test_g_lat_from_curvature),
    ("build_auto_corner_map", test_build_auto_map),
    ("métricas da curva (freio, v_min, retomada, tempo)", test_metrics_basic),
    ("métricas com canais faltando", test_metrics_missing_channels),
    ("curva sem frenagem", test_metrics_no_braking),
    ("curva sem acelerador pleno", test_metrics_no_full_throttle),
    ("comparação: volta mais lenta perde tempo e v_min", test_compare_slower_lap),
    ("comparação: freada mais tarde", test_compare_later_braking),
    ("comparação: retomada mais tarde", test_compare_later_throttle),
    ("comparação sem volta de referência", test_compare_without_reference),
    ("curvas em sequência não dividem a mesma freada", test_sequential_corners_dont_share_braking),
    ("pior curva da volta", test_worst_corner),
    ("cobertura da volta", test_lap_coverage),
    ("mapa automático registra a cobertura", test_auto_map_records_coverage),
    ("mapa .auto antigo (sem cobertura) é provisório", test_legacy_auto_map_is_provisional),
    ("mapa do MOCK que vem no repositório", test_shipped_mock_map),
    ("G reconstruído não explode com coordenada arredondada",
     test_reconstrucao_g_nao_explode_com_coordenada_arredondada),
    ("pista de 15 curvas detectada pelos dois caminhos",
     test_detecta_pista_inteira_pelos_dois_caminhos),
    ("chicane não vira uma curva", test_chicane_nao_vira_uma_curva),
    ("sequência ligada é dividida no vale",
     test_sequencia_ligada_e_dividida_no_vale),
    ("curva longa única não é partida", test_curva_longa_unica_nao_e_partida),
    ("mapa .auto de versão antiga é refeito",
     test_mapa_auto_de_versao_antiga_e_refeito),
    ("consenso rejeita curva que só uma volta viu",
     test_consenso_rejeita_curva_que_so_uma_volta_viu),
    ("consenso de uma volta = detecção simples",
     test_consenso_de_uma_volta_e_igual_a_deteccao_simples),
    ("consenso ignora volta incompleta",
     test_consenso_ignora_volta_incompleta),
    ("consenso mantém a mão das curvas",
     test_consenso_mantem_a_mao_das_curvas),
]:
    check(name, fn)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
