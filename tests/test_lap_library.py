"""
tests/test_lap_library.py — Catálogo de voltas gravadas
========================================================
Cobre o que a biblioteca de voltas promete:

  * a volta vai para o disco comprimida e arredondada (era 1,5 MB por volta)
  * o índice é leve e relido sozinho, sem abrir a telemetria de ninguém
  * a telemetria só sai do disco quando alguém pede aquela volta
  * a retenção apaga volta antiga e lenta, mas nunca a melhor, a fixada
    com alfinete ou a salva à mão
  * Personal Best sai do catálogo, e volta suja ou parcial não concorre
  * arquivo apagado à mão, índice corrompido e volta de versão anterior
    não derrubam nada

Roda sem interface gráfica e sem o jogo, num diretório temporário:

    python tests/test_lap_library.py
"""

import gzip
import json
import os
import shutil
import sys
import tempfile

# O console do Windows costuma vir em cp1252, que não tem os símbolos usados
# nos rótulos das voltas (⚠ para inválida, 📌 para fixada). Sem isto o teste
# passaria e ainda assim morreria na hora de imprimir o resultado.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.lap_library import (
    LapLibrary, LapRecord, RetentionPolicy, compact_telemetry, clean_name,
)

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def _telemetry(n=200, *, start_d=0.0, end_d=7000.0, base_time=0.0):
    """Volta sintética plausível: distância crescente, canais em lockstep."""
    step = (end_d - start_d) / max(1, n - 1)
    return {
        "times": [base_time + i * 0.45 for i in range(n)],
        "distance": [start_d + i * step for i in range(n)],
        "speed": [120.0 + (i % 40) + 0.123456789 for i in range(n)],
        "gas": [0.8123456789] * n,
        "brake": [0.0] * n,
        "sector": [0 if i < n // 3 else (1 if i < 2 * n // 3 else 2) for i in range(n)],
        "rpm": [6000 + i for i in range(n)],
        "gear": [4] * n,
        "steer": [1.23456789] * n,
        "delta": [0.0] * n,
        "car_x": [float(i) + 0.123456789 for i in range(n)],
        "car_z": [float(i) * 2 for i in range(n)],
        "abs_intervention": [0.0] * n,
        "tc_intervention": [0.0] * n,
        "g_lat": [0.5] * n,
    }


TRACK, CAR = "Spa", "Test Car"

work = tempfile.mkdtemp(prefix="ac_lap_library_test_")
try:
    # --- 1. Arredondamento por canal ----------------------------------------
    raw = _telemetry(50)
    small = compact_telemetry(raw)
    check("arredondamento respeita a precisão de cada canal",
          small["speed"][0] == round(raw["speed"][0], 2)
          and small["gas"][0] == round(raw["gas"][0], 4)
          and small["car_x"][0] == round(raw["car_x"][0], 2))
    check("canais inteiros são gravados como inteiros",
          isinstance(small["rpm"][0], int) and isinstance(small["gear"][0], int)
          and isinstance(small["sector"][0], int))
    unknown = compact_telemetry({"canal_novo": [1.23456789]})
    check("canal desconhecido passa intacto (provider novo não perde dado)",
          unknown["canal_novo"] == [1.23456789])

    # --- 2. Gravação: tamanho e formato -------------------------------------
    lib_dir = os.path.join(work, "telemetry_data")
    lib = LapLibrary(data_dir=lib_dir, retention=RetentionPolicy(enabled=False))

    big = _telemetry(5400)   # 90 s a 60 Hz, uma volta de verdade
    rec = lib.save_lap(TRACK, CAR, telemetry=big, lap_time_str="1:29.500",
                       sector_times_ms=[30000, 30000, 29500], lap_number=3,
                       session_id="s1", full_lap=True, valid=True)
    check("save_lap devolve um LapRecord", isinstance(rec, LapRecord))
    lap_path = os.path.join(lib.folder_for(TRACK, CAR), rec.file)
    check("a volta é gravada em laps/ e comprimida",
          os.path.exists(lap_path) and lap_path.endswith(".json.gz"))

    on_disk = os.path.getsize(lap_path)
    naive = len(json.dumps({"metadata": {}, "telemetry": big}).encode("utf-8"))
    check("a volta em disco é bem menor que o JSON cru de antes",
          on_disk < naive / 4,
          f"{naive/1024/1024:.2f} MB cru -> {on_disk/1024:.0f} KB gravado")

    index_size = os.path.getsize(lib._index_path(TRACK, CAR))
    check("o índice é leve mesmo com a volta inteira gravada",
          index_size < 4096, f"{index_size} bytes")

    # --- 3. Índice relido do zero, sem abrir telemetria ---------------------
    lib2 = LapLibrary(data_dir=lib_dir, retention=RetentionPolicy(enabled=False))
    recs = lib2.records(TRACK, CAR)
    check("o índice é relido do disco", len(recs) == 1, f"{len(recs)} registros")
    r = recs[0]
    check("o índice guarda tempo, setores e número da volta",
          r.lap_time_str == "1:29.500" and r.lap_time_ms == 89500
          and r.sector_times_ms == [30000, 30000, 29500] and r.lap_number == 3)
    check("o índice guarda quantos pontos a volta tem",
          r.points == 5400, str(r.points))
    check("o índice guarda quais canais existem",
          "g_lat" in r.channels and "gear" in r.channels)
    check("ler o índice não carrega telemetria na memória",
          len(lib2._telemetry_cache) == 0, f"{len(lib2._telemetry_cache)} em cache")

    # --- 4. Telemetria sob demanda ------------------------------------------
    tele = lib2.load_telemetry(TRACK, CAR, r)
    check("load_telemetry devolve a volta inteira",
          tele is not None and len(tele["times"]) == 5400)
    check("os valores sobrevivem ao arredondamento",
          abs(tele["speed"][10] - big["speed"][10]) < 0.01
          and tele["rpm"][10] == big["rpm"][10])
    check("a telemetria lida fica em cache", len(lib2._telemetry_cache) == 1)
    check("o cache não cresce sem limite",
          lib2.CACHE_SIZE <= 8, f"CACHE_SIZE={lib2.CACHE_SIZE}")

    ghost = lib2.load_ghost(TRACK, CAR, r)
    check("load_ghost devolve no formato que o app já consome",
          ghost is not None and "metadata" in ghost and "telemetry" in ghost
          and ghost["metadata"]["lap_time_str"] == "1:29.500")

    # --- 5. Personal Best sai do catálogo -----------------------------------
    pb_dir = os.path.join(work, "pb")
    plib = LapLibrary(data_dir=pb_dir, retention=RetentionPolicy(enabled=False))

    def _add(time_str, **kw):
        opts = dict(full_lap=True, valid=True, session_id="s1")
        opts.update(kw)
        return plib.save_lap(TRACK, CAR, telemetry=_telemetry(120),
                             lap_time_str=time_str,
                             sector_times_ms=[30000, 30000, 30000], **opts)

    _add("1:30.000")
    fast_but_dirty = _add("1:28.000", valid=False)
    fast_but_partial = _add("1:27.000", full_lap=False)
    real_pb = _add("1:29.000")

    pb = plib.personal_best(TRACK, CAR)
    check("Personal Best é a volta mais rápida limpa e inteira",
          pb is not None and pb.lap_time_str == "1:29.000",
          str(pb.lap_time_str if pb else None))
    check("volta suja não vira Personal Best",
          pb.lap_id != fast_but_dirty.lap_id)
    check("volta parcial não vira Personal Best",
          pb.lap_id != fast_but_partial.lap_id)
    check("volta suja continua no catálogo (só não é referência)",
          any(x.lap_id == fast_but_dirty.lap_id for x in plib.records(TRACK, CAR)))
    check("bater o recorde não apaga o recorde anterior",
          len(plib.records(TRACK, CAR)) == 4, f"{len(plib.records(TRACK, CAR))}")

    # --- 6. Retenção ---------------------------------------------------------
    ret_dir = os.path.join(work, "ret")
    rlib = LapLibrary(data_dir=ret_dir,
                      retention=RetentionPolicy(enabled=True, keep_best=2,
                                                keep_recent=3))
    made = []
    # 10 voltas ficando cada vez mais lentas: as primeiras são as melhores
    for i in range(10):
        made.append(rlib.save_lap(
            TRACK, CAR, telemetry=_telemetry(80),
            lap_time_str=f"1:{30 + i}.000",
            sector_times_ms=[30000, 30000, 30000], lap_number=i + 1,
            session_id="s1", full_lap=True, valid=True))
    kept = rlib.records(TRACK, CAR)
    kept_times = sorted(r.lap_time_str for r in kept)
    check("a retenção mantém as 2 melhores e as 3 mais recentes",
          len(kept) == 5, f"{len(kept)} voltas: {kept_times}")
    check("as 2 mais rápidas ficaram",
          {"1:30.000", "1:31.000"} <= set(kept_times), str(kept_times))
    check("as 3 mais recentes ficaram",
          {"1:37.000", "1:38.000", "1:39.000"} <= set(kept_times), str(kept_times))
    lap_files = os.listdir(os.path.join(rlib.folder_for(TRACK, CAR), "laps"))
    check("a retenção apaga o arquivo do disco, não só a linha do índice",
          len(lap_files) == 5, f"{len(lap_files)} arquivos")

    # Fixada com alfinete e salva à mão são intocáveis
    ret2_dir = os.path.join(work, "ret2")
    r2 = LapLibrary(data_dir=ret2_dir,
                    retention=RetentionPolicy(enabled=True, keep_best=1,
                                              keep_recent=1))
    slow_pinned = r2.save_lap(TRACK, CAR, telemetry=_telemetry(80),
                              lap_time_str="1:59.000",
                              sector_times_ms=[0, 0, 0], lap_number=1,
                              session_id="s1", full_lap=True, valid=True)
    r2.set_pinned(TRACK, CAR, slow_pinned.lap_id, True)
    r2.save_lap(TRACK, CAR, telemetry=_telemetry(80), lap_time_str="1:58.000",
                sector_times_ms=[0, 0, 0], lap_number=2, session_id="s1",
                full_lap=True, valid=True, manual=True)
    for i in range(6):
        r2.save_lap(TRACK, CAR, telemetry=_telemetry(80),
                    lap_time_str=f"1:{40 + i}.000", sector_times_ms=[0, 0, 0],
                    lap_number=10 + i, session_id="s1", full_lap=True, valid=True)
    after = r2.records(TRACK, CAR)
    check("volta fixada com alfinete sobrevive à retenção",
          any(x.lap_id == slow_pinned.lap_id for x in after),
          str([(x.lap_time_str, x.pinned, x.manual_save) for x in after]))
    check("volta salva à mão sobrevive à retenção",
          any(x.manual_save for x in after))

    # --- 7. Resiliência ------------------------------------------------------
    # Arquivo apagado à mão sai do índice em vez de virar erro
    victim = kept[0]
    os.remove(os.path.join(rlib.folder_for(TRACK, CAR), victim.file))
    fresh = LapLibrary(data_dir=ret_dir, retention=RetentionPolicy(enabled=False))
    check("volta com arquivo apagado à mão sai do catálogo",
          all(x.lap_id != victim.lap_id for x in fresh.records(TRACK, CAR)))

    # Índice corrompido: reconstruído a partir do que houver, sem estourar
    bad_dir = os.path.join(work, "bad")
    blib = LapLibrary(data_dir=bad_dir, retention=RetentionPolicy(enabled=False))
    blib.save_lap(TRACK, CAR, telemetry=_telemetry(60), lap_time_str="1:30.000",
                  sector_times_ms=[0, 0, 0], session_id="s1", full_lap=True)
    with open(blib._index_path(TRACK, CAR), "w", encoding="utf-8") as f:
        f.write('{"schema": 2, "laps": [ {"lap_id": ')   # truncado
    blib2 = LapLibrary(data_dir=bad_dir, retention=RetentionPolicy(enabled=False))
    ok = True
    try:
        recs_bad = blib2.records(TRACK, CAR)
    except Exception as e:      # noqa: BLE001 — é exatamente isso que testamos
        ok, recs_bad = False, []
    check("índice corrompido não derruba a leitura", ok)
    check("índice corrompido é isolado como .corrupt",
          os.path.exists(blib2._index_path(TRACK, CAR) + ".corrupt"))
    check("a volta é recuperada da pasta laps/ quando o índice se perde",
          len(recs_bad) == 1 and recs_bad[0].lap_time_str == "1:30.000",
          f"{len(recs_bad)} registros")
    check("o índice é reescrito depois da recuperação",
          os.path.exists(blib2._index_path(TRACK, CAR)))

    # Telemetria corrompida devolve None em vez de estourar
    corrupt_lap = os.path.join(blib2.folder_for(TRACK, CAR), "laps", "quebrada.json.gz")
    os.makedirs(os.path.dirname(corrupt_lap), exist_ok=True)
    with open(corrupt_lap, "wb") as f:
        f.write(b"isto nao e gzip")
    fake = LapRecord(lap_id="quebrada", file="laps/quebrada.json.gz")
    check("telemetria ilegível devolve None, não exceção",
          blib2.load_telemetry(TRACK, CAR, fake) is None)

    # --- 8. Voltas de versão anterior (JSON solto na pasta) -----------------
    leg_dir = os.path.join(work, "legacy")
    leg_folder = os.path.join(leg_dir, TRACK, CAR)
    os.makedirs(leg_folder, exist_ok=True)
    legacy_payload = {
        "metadata": {"track": TRACK, "car": CAR, "lap_time_str": "1:32.100",
                     "sector_times_ms": [31000, 31000, 30100],
                     "timestamp": "2026-01-15T20:30:00", "full_lap": True},
        "telemetry": _telemetry(90),
    }
    with open(os.path.join(leg_folder, "2026-01-15_20-30_1-32.100.json"),
              "w", encoding="utf-8") as f:
        json.dump(legacy_payload, f)
    llib = LapLibrary(data_dir=leg_dir, retention=RetentionPolicy(enabled=False))
    lrecs = llib.records(TRACK, CAR)
    check("volta de versão anterior entra no catálogo",
          len(lrecs) == 1 and lrecs[0].lap_time_str == "1:32.100",
          str([(x.lap_time_str, x.legacy) for x in lrecs]))
    check("volta de versão anterior é marcada como legada", lrecs[0].legacy)
    check("volta de versão anterior continua legível de onde está",
          len(llib.load_telemetry(TRACK, CAR, lrecs[0])["times"]) == 90)
    check("a data original da volta legada é preservada",
          lrecs[0].date_str == "15/01 20:30", lrecs[0].date_str)
    # A indexação acontece uma vez: o índice já existe na segunda abertura
    check("a varredura de voltas legadas grava o índice",
          os.path.exists(llib._index_path(TRACK, CAR)))

    # --- 9. Agrupamento por sessão ------------------------------------------
    ses_dir = os.path.join(work, "ses")
    slib = LapLibrary(data_dir=ses_dir, retention=RetentionPolicy(enabled=False))
    for sid, times in (("A", ["1:30.000", "1:29.000"]), ("B", ["1:31.000"])):
        for i, t in enumerate(times):
            slib.save_lap(TRACK, CAR, telemetry=_telemetry(60), lap_time_str=t,
                          sector_times_ms=[0, 0, 0], lap_number=i + 1,
                          session_id=sid, full_lap=True, valid=True)
    sessions = slib.sessions(TRACK, CAR)
    check("as voltas são agrupadas por sessão",
          len(sessions) == 2, str([(s["session_id"], len(s["laps"])) for s in sessions]))
    sess_a = next((s for s in sessions if s["session_id"] == "A"), None)
    check("cada sessão sabe qual foi a melhor volta dela",
          sess_a is not None and sess_a["best"].lap_time_str == "1:29.000",
          str(sess_a["best"].lap_time_str if sess_a and sess_a["best"] else None))

    # --- 10. Apagar e fixar --------------------------------------------------
    doomed = slib.records(TRACK, CAR)[0]
    doomed_path = os.path.join(slib.folder_for(TRACK, CAR), doomed.file)
    check("delete apaga do índice e do disco",
          slib.delete(TRACK, CAR, doomed.lap_id)
          and not os.path.exists(doomed_path)
          and all(x.lap_id != doomed.lap_id for x in slib.records(TRACK, CAR)))
    check("delete de volta inexistente devolve False",
          slib.delete(TRACK, CAR, "nao-existe") is False)
    keeper = slib.records(TRACK, CAR)[0]
    slib.set_pinned(TRACK, CAR, keeper.lap_id, True)
    reread_lib = LapLibrary(data_dir=ses_dir, retention=RetentionPolicy(enabled=False))
    check("o alfinete sobrevive ao restart",
          any(x.lap_id == keeper.lap_id and x.pinned
              for x in reread_lib.records(TRACK, CAR)))

    # --- 11. Nomes de pasta e rótulos ---------------------------------------
    check("nome de pista com caractere inválido é limpo",
          clean_name('Spa: <GP>/2024', "X") == "Spa GP2024",
          clean_name('Spa: <GP>/2024', "X"))
    check("nome vazio cai no fallback", clean_name("   ", "UnknownTrack") == "UnknownTrack")
    lbl = LapRecord(lap_number=7, lap_time_str="1:29.500", valid=False,
                    timestamp="2026-01-15T20:30:00").label()
    check("o rótulo mostra volta, tempo, aviso de inválida e data",
          "Volta 7" in lbl and "1:29.500" in lbl and "⚠" in lbl and "15/01" in lbl, lbl)

    # --- 11b. Os padrões de retenção não podem divergir ----------------------
    # `config.json` SOBRESCREVE os padrões da dataclass. Já divergiram: a
    # dataclass prometia guardar 200 voltas e o config mandava apagar depois
    # de 40, então o app apagava volta que a documentação garantia manter.
    from core.config import AppConfig
    cfg_tmp = os.path.join(work, "cfg.json")
    cfg = AppConfig(path=cfg_tmp)
    check("o padrão de retenção do config.json bate com o da dataclass",
          cfg.retention_policy() == RetentionPolicy(),
          f"config={cfg.retention_policy()} dataclass={RetentionPolicy()}")
    check("a retenção padrão cobre um fim de semana inteiro com folga",
          RetentionPolicy().keep_recent >= 120,
          f"keep_recent={RetentionPolicy().keep_recent} "
          "(um fim de semana dá 40 a 60 voltas)")

    # --- 12. Uso de disco ----------------------------------------------------
    check("disk_usage soma os bytes gravados",
          lib.disk_usage(TRACK, CAR) >= on_disk,
          f"{lib.disk_usage(TRACK, CAR)} bytes")

finally:
    shutil.rmtree(work, ignore_errors=True)


print()
falhas = 0
for name, ok, detail in results:
    tag = "[OK ]" if ok else "[ERRO]"
    extra = f"   ({detail})" if detail else ""
    print(f"  {tag} {name}{extra}")
    if not ok:
        falhas += 1

print()
print(f"=== {len(results) - falhas}/{len(results)} verificacoes passaram ===")
sys.exit(1 if falhas else 0)
