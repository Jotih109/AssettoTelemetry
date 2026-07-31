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
    assert c.name == "C1" and c.direction == "R"
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
    ("mapa do MOCK que vem no repositório", test_shipped_mock_map),
]:
    check(name, fn)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
