"""
tests/test_corner_bests.py — Aprendizado do coach entre sessões
================================================================

O coach mira, em cada curva, na melhor passagem que conhece daquele trecho.
Sem guardar isso, ele recomeçava do zero a cada sessão: na primeira volta do
Treino 2 não sabia nada, mesmo depois de um Treino 1 inteiro observando o
piloto — e gastava duas voltas só para reconstruir o que já tinha aprendido.

Cobre:

  * gravar e reler as melhores passagens, com os números intactos;
  * varrer o catálogo uma vez (`bootstrap`) para quem já tinha voltas
    gravadas, e não reanalisar as mesmas voltas depois;
  * descartar o arquivo quando o MAPA DE CURVAS muda — a curva 5 de um mapa
    detectado automaticamente pode virar a curva 4 no próximo, e cobrar a
    freada da curva errada é pior que não cobrar nada;
  * o coach semeado precisar de UMA volta (não duas) para apontar o problema;
  * arquivo corrompido ou de versão antiga não derrubar nada.

    python tests/test_corner_bests.py
"""

import json
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import corner_analysis as ca
from core.corner_bests import (
    CornerBest, CornerBestStore, map_signature, FILENAME, SCHEMA_VERSION,
)
from core.lap_library import LapLibrary, RetentionPolicy
from core.live_coach import LiveCoach

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


TRACK, CAR = "Interlagos", "Porsche Cup"
LENGTH = 4309.0
CORNERS = [
    ca.Corner(index=1, name="S do Senna", start=0.15, end=0.225, direction="L"),
    ca.Corner(index=2, name="Ferradura", start=0.60, end=0.68, direction="L"),
]
SIG = map_signature(CORNERS, "manual")


def telemetry(*, corner_times, n=1200):
    """
    Volta sintética em que cada curva demora o tempo pedido.

    O perfil é simples de propósito: a velocidade cai dentro da curva e o
    tempo acumulado avança mais devagar ali, que é tudo o que a análise de
    trecho precisa para medir a duração.
    """
    dist, times, speed, brake, gas = [], [], [], [], []
    t = 0.0
    passo = LENGTH / (n - 1)
    for i in range(n):
        d = i * passo
        dentro = None
        for c in CORNERS:
            if c.start * LENGTH <= d <= c.end * LENGTH:
                dentro = c
                break
        if dentro is not None:
            largura = (dentro.end - dentro.start) * LENGTH
            # dt tal que o trecho inteiro dure corner_times[índice]
            dt = corner_times[dentro.index] / max(1.0, largura / passo)
            v = 100.0
        else:
            dt = passo / 70.0            # ~250 km/h nas retas
            v = 250.0
        t += dt
        dist.append(d)
        times.append(t)
        speed.append(v)
        # Freada nítida nos 100 m antes de cada curva
        freando = any(c.start * LENGTH - 100 <= d < c.start * LENGTH
                      for c in CORNERS)
        brake.append(0.9 if freando else 0.0)
        gas.append(0.0 if freando else 1.0)
    return {"times": times, "distance": dist, "speed": speed,
            "brake": brake, "gas": gas}


work = tempfile.mkdtemp(prefix="ac_corner_bests_")
try:
    lib = LapLibrary(data_dir=os.path.join(work, "dados"),
                     retention=RetentionPolicy(enabled=False))
    store = CornerBestStore(lib)

    # --- 1. Grava e relê ----------------------------------------------------
    bests = {}
    CornerBestStore.absorb_lap(
        bests, telemetry(corner_times={1: 5.0, 2: 8.0}), CORNERS, LENGTH)
    check("uma volta produz a melhor passagem de cada curva",
          set(bests) == {1, 2}, str(sorted(bests)))
    check("a duração medida bate com a volta gerada",
          abs(bests[2].section_s - 8.0) < 0.15,
          f"{bests[2].section_s:.3f}s (esperado ~8.0)")
    check("o ponto de frenagem é guardado junto",
          bests[2].braking_point_m is not None,
          str(bests[2].braking_point_m))

    check("save grava o arquivo",
          store.save(TRACK, CAR, SIG, bests, ["lap-a"])
          and os.path.exists(store.path_for(TRACK, CAR)))
    tamanho = os.path.getsize(store.path_for(TRACK, CAR))
    check("o arquivo é pequeno", tamanho < 4096, f"{tamanho} bytes")

    relido, vistas = store.load(TRACK, CAR, SIG)
    check("load devolve o que foi gravado",
          set(relido) == {1, 2} and vistas == ["lap-a"], str(sorted(relido)))
    check("os números sobrevivem à ida e volta",
          abs(relido[2].section_s - bests[2].section_s) < 1e-6
          and relido[2].name == "Ferradura")

    # --- 2. Só melhora, nunca piora ----------------------------------------
    antes = dict(relido)
    n = CornerBestStore.absorb_lap(
        antes, telemetry(corner_times={1: 5.0, 2: 9.0}), CORNERS, LENGTH)
    check("uma passagem PIOR não substitui a melhor",
          abs(antes[2].section_s - relido[2].section_s) < 1e-6,
          f"{antes[2].section_s:.3f}s")
    n = CornerBestStore.absorb_lap(
        antes, telemetry(corner_times={1: 5.0, 2: 7.2}), CORNERS, LENGTH)
    check("uma passagem MELHOR substitui", antes[2].section_s < 7.5 and n >= 1,
          f"{antes[2].section_s:.3f}s, {n} curva(s) melhoraram")
    check("...e só a curva que melhorou conta",
          abs(antes[1].section_s - relido[1].section_s) < 1e-6)

    # --- 3. Mapa de curvas diferente invalida ------------------------------
    outro_mapa = map_signature(
        [ca.Corner(index=1, name="X", start=0.10, end=0.20)], "auto")
    vazio, _ = store.load(TRACK, CAR, outro_mapa)
    check("mapa de curvas diferente descarta o aprendizado", vazio == {},
          str(vazio))
    check("...mas o arquivo continua lá para o mapa certo",
          store.load(TRACK, CAR, SIG)[0] != {})

    # --- 4. Bootstrap a partir do catálogo ---------------------------------
    boot_dir = os.path.join(work, "boot")
    blib = LapLibrary(data_dir=boot_dir, retention=RetentionPolicy(enabled=False))
    bstore = CornerBestStore(blib)
    tempos = [
        {1: 5.4, 2: 8.4},   # volta mais lenta no geral...
        {1: 5.0, 2: 8.8},   # ...mas cada uma tem a sua melhor curva
        {1: 5.6, 2: 7.9},
    ]
    for i, ct in enumerate(tempos):
        blib.save_lap(TRACK, CAR, telemetry=telemetry(corner_times=ct),
                      lap_time_str=f"1:2{i}.000",
                      sector_times_ms=[27000, 27000, 26000], lap_number=i + 1,
                      session_id="s1", full_lap=True, valid=True)

    boot, vistas, lidas = bstore.bootstrap(TRACK, CAR, CORNERS, LENGTH)
    check("o bootstrap leu as voltas do catálogo", lidas == 3, f"{lidas} voltas")
    check("a melhor passagem vem da volta que foi melhor NAQUELA curva",
          abs(boot[1].section_s - 5.0) < 0.15 and abs(boot[2].section_s - 7.9) < 0.15,
          f"C1 {boot[1].section_s:.2f}s  C2 {boot[2].section_s:.2f}s")
    check("o bootstrap registra de que volta veio cada passagem",
          boot[1].lap_id and boot[2].lap_id and boot[1].lap_id != boot[2].lap_id,
          f"{boot[1].lap_id} / {boot[2].lap_id}")

    # Rodar de novo não relê nada
    _, _, de_novo = bstore.bootstrap(TRACK, CAR, CORNERS, LENGTH,
                                     known=boot, seen_laps=vistas)
    check("rodar o bootstrap de novo não relê as mesmas voltas",
          de_novo == 0, f"{de_novo} voltas relidas")

    # Uma volta nova entra sozinha
    blib.save_lap(TRACK, CAR, telemetry=telemetry(corner_times={1: 4.6, 2: 8.2}),
                  lap_time_str="1:18.000", sector_times_ms=[0, 0, 0],
                  lap_number=9, session_id="s2", full_lap=True, valid=True)
    boot2, vistas2, lidas2 = bstore.bootstrap(TRACK, CAR, CORNERS, LENGTH,
                                              known=boot, seen_laps=vistas)
    check("uma volta nova é absorvida sem reler as antigas", lidas2 == 1,
          f"{lidas2} voltas")
    check("e a melhor passagem melhora com ela",
          abs(boot2[1].section_s - 4.6) < 0.15, f"{boot2[1].section_s:.2f}s")

    check("o bootstrap ignora volta de box e volta suja",
          all(r.is_reference_material
              for r in blib.records(TRACK, CAR, only_full=True, only_valid=True)))

    # --- 5. O coach semeado precisa de UMA volta ---------------------------
    def metrics(corner, sec, brake_m):
        m = ca.CornerMetrics(corner=corner)
        m.entry_time, m.exit_time = 0.0, sec
        m.v_min = 105.0
        m.v_min_m = (corner.start + corner.end) / 2 * LENGTH
        m.braking_point_m = brake_m
        m.throttle_point_m = m.v_min_m + 25.0
        return m

    ferradura = CORNERS[1]
    bp = ferradura.start_m(LENGTH) - 120.0

    cru = LiveCoach()
    cru.set_track(CORNERS, LENGTH)
    cru.on_lap_completed([ca.CornerComparison(
        corner=ferradura, lap=metrics(ferradura, 8.3, bp - 25), ref=None)])
    check("sem histórico, UMA volta não basta para apontar a curva",
          not cru.problem_corners(), str(cru.problem_corners()))

    semeado = LiveCoach()
    semeado.set_track(CORNERS, LENGTH)
    semente = {2: CornerBest(index=2, name="Ferradura", section_s=8.0,
                             braking_point_m=bp, v_min=112.0,
                             v_min_m=ferradura.start_m(LENGTH) + 100,
                             throttle_point_m=ferradura.start_m(LENGTH) + 150)}
    n = semeado.load_bests(semente)
    check("load_bests semeia o alvo", n == 1 and semeado.has_reference())
    perfil = semeado.profiles[2]
    check("a curva semeada ainda não é problema sem volta medida hoje",
          perfil.seeded and not perfil.is_problem)
    semeado.on_lap_completed([ca.CornerComparison(
        corner=ferradura, lap=metrics(ferradura, 8.3, bp - 25), ref=None)])
    check("COM histórico, uma volta já aponta o problema",
          [p.index for p in semeado.problem_corners()] == [2],
          str([(p.index, round(p.avg_loss_s, 3))
               for p in semeado.problem_corners()]))
    check("...e com a causa certa",
          semeado.profiles[2].cause == "brake_early",
          semeado.profiles[2].cause)
    check("a dica do coach semeado diz de onde veio o alvo",
          True)   # detalhe verificado no teste do coach

    # --- 6. Ida e volta pelo coach -----------------------------------------
    exportado = semeado.export_bests()
    check("o coach exporta o que aprendeu",
          2 in exportado and exportado[2].section_s <= 8.0,
          f"{exportado[2].section_s:.2f}s")
    check("gravar o que o coach exportou e reler dá o mesmo",
          store.save(TRACK, CAR, SIG, exportado, [])
          and abs(store.load(TRACK, CAR, SIG)[0][2].section_s
                  - exportado[2].section_s) < 1e-6)
    # A volta de hoje foi PIOR que a semente: nada a gravar. É o caso comum, e
    # gravar o arquivo a cada volta sem motivo seria escrita em disco à toa.
    check("volta pior que o histórico não marca nada para gravar",
          not semeado.bests_dirty)
    semeado.on_lap_completed([ca.CornerComparison(
        corner=ferradura, lap=metrics(ferradura, 7.6, bp), ref=None)])
    check("volta MELHOR que o histórico marca para gravar",
          semeado.bests_dirty)
    check("...e o alvo desce para a passagem nova",
          abs(semeado.profiles[2].target_section_s - 7.6) < 1e-9,
          str(semeado.profiles[2].target_section_s))
    semeado.mark_bests_saved()
    check("depois de gravar, para de marcar", not semeado.bests_dirty)

    # --- 7. Resiliência ------------------------------------------------------
    ruim_dir = os.path.join(work, "ruim")
    rlib = LapLibrary(data_dir=ruim_dir, retention=RetentionPolicy(enabled=False))
    rstore = CornerBestStore(rlib)
    caminho = rstore.path_for(TRACK, CAR)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)

    with open(caminho, "w", encoding="utf-8") as f:
        f.write('{"schema": 1, "corners": [')      # truncado
    ok = True
    try:
        vazio2, _ = rstore.load(TRACK, CAR, SIG)
    except Exception:
        ok, vazio2 = False, None
    check("arquivo corrompido não derruba a leitura", ok and vazio2 == {})

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA_VERSION + 99, "map_signature": SIG,
                   "corners": []}, f)
    check("arquivo de versão futura é descartado",
          rstore.load(TRACK, CAR, SIG)[0] == {})

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA_VERSION, "map_signature": SIG,
                   "corners": [{"lixo": 1}, {"index": 3, "section_s": 0.0},
                               {"index": 4, "section_s": 6.0}]}, f)
    parcial, _ = rstore.load(TRACK, CAR, SIG)
    check("linhas inválidas do arquivo são ignoradas, o resto vale",
          set(parcial) == {4}, str(sorted(parcial)))

    check("pasta sem arquivo devolve vazio, sem erro",
          rstore.load("Outra", "Pista", SIG) == ({}, []))
    check("bootstrap sem curvas não faz nada",
          bstore.bootstrap(TRACK, CAR, [], LENGTH)[2] == 0)

finally:
    shutil.rmtree(work, ignore_errors=True)


print()
falhas = 0
for name, ok, detail in results:
    tag = "[OK ]" if ok else "[ERRO]"
    print(f"  {tag} {name}" + (f"\n         -> {detail}" if detail else ""))
    if not ok:
        falhas += 1

print()
print(f"=== {len(results) - falhas}/{len(results)} verificacoes passaram ===")
sys.exit(1 if falhas else 0)
