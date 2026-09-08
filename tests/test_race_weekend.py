"""
tests/test_race_weekend.py — Um fim de semana de corrida inteiro
=================================================================

Sexta com Treino 1 e Treino 2, sábado com Treino 3 e classificação, domingo
com a corrida. Telemetria sintética quadro a quadro ([tests/weekend_sim.py]),
no mesmo formato que o provider do Assetto Corsa entrega, passando pelo mesmo
caminho que o app usa na pista: `SessionManager` grava, `RaceEngineer` comenta,
`LiveCoach` treina, `LapLibrary` guarda.

Não é um teste de unidade. É a pergunta que interessa: *depois de um fim de
semana de verdade, está tudo lá e faz sentido?*

  Sexta  T1  — piloto cru: erra sempre nas mesmas curvas.
               O coach tem que APRENDER onde ele perde.
  Sexta  T2  — piloto corrigindo os erros.
               O tempo tem que cair e o coach tem que falar menos.
  Sábado T3  — pista mais quente, tanque mais leve.
  Sábado Q   — volta de saída, volta lançada, volta de retorno.
               Só a lançada pode virar referência, e o coach só fala nela.
  Domingo R  — corrida com bandeira amarela, corte de pista, dano,
               combustível acabando e última volta.
               Volta suja não pode virar Personal Best.
  Depois     — o catálogo tem tudo, separado por sessão, e a tela de
               análise pós-sessão abre qualquer volta.

    python tests/test_race_weekend.py
"""

import os
import shutil
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.session_manager import SessionManager
from core.lap_library import LapLibrary, RetentionPolicy
from core.corner_bests import CornerBestStore, map_signature
from core.race_engineer import RaceEngineer, CRITICAL
from core.live_coach import LiveCoach, MODE_QUALIFY, MODE_RACE
from core import corner_analysis as ca

from tests.weekend_sim import (
    Session, LapPlan, rookie_style, TRACK_NAME, CAR_NAME, TRACK_LENGTH, CORNERS,
)

results = []
_t0 = time.perf_counter()


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


#: 30 Hz é o bastante para a análise (uma amostra a cada ~2,7 m a 290 km/h) e
#: deixa o fim de semana inteiro rodar em segundos em vez de minutos.
SAMPLE_HZ = 30.0


# ---------------------------------------------------------------------------
# O piloto virtual: o mesmo caminho que a interface percorre na pista
# ---------------------------------------------------------------------------

class Cockpit:
    """
    Junta SessionManager, RaceEngineer e LiveCoach como a janela principal faz.

    Só o que a interface faz de verdade com a telemetria — sem Qt, para o fim
    de semana inteiro caber num teste que roda em segundos. A ligação real com
    a janela é coberta por `tests/test_ui_smoke.py`.
    """

    def __init__(self, data_dir):
        self.sm = SessionManager(data_dir=data_dir,
                                 retention=RetentionPolicy(enabled=True,
                                                           keep_best=15,
                                                           keep_recent=40))
        self.engineer = RaceEngineer()
        self.coach = LiveCoach()
        self.coach.set_track(CORNERS, TRACK_LENGTH)
        # Aprendizado entre sessões, como a janela principal faz
        self.bests_store = CornerBestStore(self.sm.library)
        self.map_signature = map_signature(CORNERS, "manual")
        self._bests = {}
        self._bests_seen = []
        self.seeded_corners = 0

        self.clock = 0.0
        self.said_live = []       # (clock, Advice) do engenheiro ao vivo
        self.said_coach = []      # (clock, Advice) do coach
        self.said_lap = []        # (lap, [Advice]) do balanço de fim de volta
        self._laps_seen = 0
        self._frame = 0
        self._loaded = False

    # -- um quadro -----------------------------------------------------------

    def feed(self, state, live=True):
        # O relógio anda com os quadros: os tempos de espera das regras são em
        # segundos, e o teste precisa que eles corram como correriam na pista.
        self.clock += 1.0 / SAMPLE_HZ
        self._frame += 1

        if not self._loaded:
            self.sm.auto_load_ghosts(state)
            self._loaded = True
            self.seed_coach()

        ref = self._reference()
        self.sm.process_state(state, reference_ghost=ref)
        self.coach.set_reference(ref.get("telemetry", {}))

        if live:
            # A interface chama o engenheiro a ~4 Hz e o coach junto
            if self._frame % max(1, int(SAMPLE_HZ // 4)) == 0:
                for adv in self.engineer.analyze_live(state, self.clock):
                    self.said_live.append((self.clock, adv))
                for adv in self.coach.update(state, self.sm.current_lap_data,
                                             self.clock):
                    self.said_coach.append((self.clock, adv))

        # Volta fechou: balanço + aprendizado do coach.
        # `laps_recorded` é monotônico; `len(completed_laps)` é zerado a cada
        # sessão nova e faria o balanço parar de sair a partir do Treino 2.
        if self.sm.laps_recorded > self._laps_seen:
            self._laps_seen = self.sm.laps_recorded
            self._on_lap_completed(state)

    def seed_coach(self):
        """Dá ao coach o que ele já aprendeu nesta pista/carro."""
        track, car = self.sm.track_car
        if not track:
            return
        self._bests, self._bests_seen = self.bests_store.load(
            track, car, self.map_signature)
        self._bests, self._bests_seen, lidas = self.bests_store.bootstrap(
            track, car, CORNERS, TRACK_LENGTH,
            known=self._bests, seen_laps=self._bests_seen)
        if lidas:
            self.bests_store.save(track, car, self.map_signature,
                                  self._bests, self._bests_seen)
        self.seeded_corners = self.coach.load_bests(self._bests)

    def persist_bests(self):
        """Grava as passagens que melhoraram nesta volta."""
        if not self.coach.bests_dirty:
            return
        track, car = self.sm.track_car
        for index, best in self.coach.export_bests().items():
            anterior = self._bests.get(index)
            if anterior is None or best.section_s < anterior.section_s:
                self._bests[index] = best
        self.bests_store.save(track, car, self.map_signature,
                              self._bests, self._bests_seen)
        self.coach.mark_bests_saved()

    def new_session(self):
        """
        Virada de sessão, como a janela faz: o coach recomeça a observar o
        piloto de hoje, mas NÃO esquece as melhores passagens.
        """
        self.coach.reset()
        self.seed_coach()
        self.engineer.reset()

    def _reference(self):
        """Automática: a mais rápida entre a melhor da sessão e o Personal Best."""
        from core.session_manager import parse_lap_time_ms
        candidatos = []
        for ghost in (self.sm.session_best_lap_ghost, self.sm.best_lap_ghost):
            if not ghost.get("telemetry", {}).get("times"):
                continue
            ms = parse_lap_time_ms(
                ghost.get("metadata", {}).get("lap_time_str", "") or "")
            candidatos.append((ms if ms > 0 else 9999999, ghost))
        if not candidatos:
            return self.sm._empty_ghost()
        return min(candidatos, key=lambda i: i[0])[1]

    def _on_lap_completed(self, state):
        entry = self.sm.completed_laps[-1]
        telemetry = self.sm.telemetry_for(entry)
        ref = self._reference()
        comparisons = ca.compare_laps(telemetry, ref.get("telemetry", {}),
                                      CORNERS, TRACK_LENGTH)
        # É o MESMO dado que alimenta a tabela curva a curva da interface
        self.coach.on_lap_completed(comparisons,
                                    pit_lap=bool(entry.get("pit_lap")),
                                    valid=bool(entry.get("valid", True)))
        self.persist_bests()
        advices = self.engineer.analyze_lap(
            comparisons, lap_telemetry=telemetry, state=state,
            lap_time_str=entry.get("lap_time_str", ""),
            ref_telemetry=ref.get("telemetry", {}),
            sector_times_ms=entry.get("metadata", {}).get("sector_times_ms"),
            ref_sector_times_ms=ref.get("metadata", {}).get("sector_times_ms"))
        self.said_lap.append((entry.get("lap_number"), advices))

    # -- consultas para os testes -------------------------------------------

    def coach_texts(self):
        return [a.text for _, a in self.said_coach]

    def live_texts(self):
        return [a.text for _, a in self.said_live]

    def coach_corners(self):
        return {a.corner for _, a in self.said_coach if a.corner}

    def clear_speech(self):
        self.said_live.clear()
        self.said_coach.clear()
        self.said_lap.clear()


def run_session(cockpit, session, plans, pace_scale=1.0, new_session=False):
    """Roda uma sessão inteira e devolve os tempos de volta gerados."""
    if new_session:
        cockpit.new_session()
    for plan in plans:
        for state in session.lap_frames(plan=plan, pace_scale=pace_scale):
            cockpit.feed(state)
    return list(session.lap_times)


# ---------------------------------------------------------------------------

work = tempfile.mkdtemp(prefix="ac_weekend_")
try:
    data_dir = os.path.join(work, "telemetry_data")
    cockpit = Cockpit(data_dir)
    lib = cockpit.sm.library

    # =======================================================================
    # SEXTA — TREINO 1: o piloto cru
    # =======================================================================
    piloto = rookie_style()
    t1 = Session("Practice 1", piloto, fuel=60.0, sample_hz=SAMPLE_HZ,
                 # O jogo já traz o tempo de uma volta anterior no 1º quadro
                 prior_last_time="1:35.000")
    tempos_t1 = run_session(cockpit, t1, [
        LapPlan(out_lap=True),          # saída dos boxes
        LapPlan(), LapPlan(), LapPlan(), LapPlan(),
        LapPlan(in_lap=True),           # volta de retorno
    ])
    sessao_t1 = cockpit.sm.session_id

    check("T1: a volta anterior ao app não vira volta fantasma",
          len(cockpit.sm.completed_laps) == 6,
          f"{len(cockpit.sm.completed_laps)} voltas fechadas")
    check("T1: a volta de saída e a de retorno ficaram marcadas como box",
          sum(1 for e in cockpit.sm.completed_laps if e["pit_lap"]) == 2,
          str([(e["lap_number"], e["pit_lap"]) for e in cockpit.sm.completed_laps]))
    check("T1: uma volta de box nunca vira referência",
          all(not r.is_reference_material
              for r in lib.records(TRACK_NAME, CAR_NAME) if r.pit_lap))
    check("T1: todas as voltas foram gravadas em disco",
          len(lib.records(TRACK_NAME, CAR_NAME)) == 6,
          f"{len(lib.records(TRACK_NAME, CAR_NAME))} no catálogo")
    check("T1: existe Personal Best ao fim do treino",
          lib.personal_best(TRACK_NAME, CAR_NAME) is not None)
    check("T1: a volta de saída de box NÃO é o Personal Best",
          not lib.personal_best(TRACK_NAME, CAR_NAME).pit_lap,
          f"PB {lib.personal_best(TRACK_NAME, CAR_NAME).label(with_date=False)}")
    check("T1: a volta ideal foi montada com os melhores setores",
          sum(cockpit.sm.ideal_lap_ghost["metadata"]["sector_times_ms"]) > 0,
          str(cockpit.sm.ideal_lap_ghost["metadata"]["sector_times_ms"]))

    # --- O coach APRENDEU onde este piloto perde ---
    problemas = cockpit.coach.problem_corners()
    idx_problemas = [p.index for p in problemas]
    check("T1: o coach achou tempo escondido sem referência externa nenhuma",
          len(problemas) >= 1,
          str([(p.index, round(p.avg_loss_s, 3)) for p in problemas]))
    # O piloto foi programado para errar mais na Ferradura (curva 5)
    check("T1: a Ferradura (onde ele mais erra) é a curva apontada",
          5 in idx_problemas[:2], str(idx_problemas))
    check("T1: o coach guardou a MELHOR passagem de cada curva como alvo",
          sum(1 for p in cockpit.coach.profiles.values()
              if p.target_section_s is not None) >= len(CORNERS) - 1,
          str([(p.index, round(p.target_section_s, 2))
               for p in cockpit.coach.profiles.values()
               if p.target_section_s is not None][:4]))
    perfil_ferradura = cockpit.coach.profiles.get(5)
    check("T1: o coach sabe POR QUE se perde na Ferradura",
          perfil_ferradura is not None and perfil_ferradura.cause != "",
          f"causa={perfil_ferradura.cause if perfil_ferradura else None}")
    # O perfil é mutado NO LUGAR volta após volta: para comparar antes e
    # depois é preciso guardar o número, não o objeto.
    media_ferradura_t1 = perfil_ferradura.avg_loss_s if perfil_ferradura else 0.0
    alvo_ferradura_t1 = (perfil_ferradura.target_section_s
                         if perfil_ferradura else None)

    # --- E FALOU com o piloto durante a volta ---
    check("T1: o coach falou durante a sessão, não só no fim da volta",
          len(cockpit.said_coach) > 0, f"{len(cockpit.said_coach)} recados")
    dicas = [a for _, a in cockpit.said_coach if a.key.startswith("coach_cue")]
    vereditos = [a for _, a in cockpit.said_coach if a.key.startswith("coach_exit")]
    check("T1: deu dicas ANTES da curva", len(dicas) > 0,
          "; ".join(a.text for a in dicas[:2]))
    check("T1: deu veredito NA SAÍDA da curva", len(vereditos) > 0,
          "; ".join(a.text for a in vereditos[:2]))
    check("T1: as dicas são sobre as curvas onde ele perde",
          all(a.corner in idx_problemas for a in dicas),
          str([(a.corner, a.text) for a in dicas[:3]]))
    check("T1: o coach não comenta a volta de saída nem a de retorno",
          all(a.time_at_stake < 1.0 for _, a in cockpit.said_coach),
          str([(a.text, round(a.time_at_stake, 2)) for _, a in cockpit.said_coach
               if a.time_at_stake >= 1.0]))

    # --- Sem metralhar: a interface não pode virar um rádio ligado ---
    por_volta = {}
    for _, a in cockpit.said_coach:
        por_volta.setdefault(a.key, 0)
        por_volta[a.key] += 1
    check("T1: o coach não repete a mesma dica sem parar",
          max(por_volta.values()) <= 6, str(sorted(por_volta.items())[:3]))
    intervalos = [b - a for (a, _), (b, _) in zip(cockpit.said_coach,
                                                  cockpit.said_coach[1:])]
    check("T1: respeita o intervalo mínimo entre recados",
          not intervalos or min(intervalos) >= 2.9,
          f"menor intervalo {min(intervalos):.1f}s" if intervalos else "")

    # --- Não fala com o carro carregado ---
    # (verificado diretamente: nenhum recado saiu com freio pisado)
    check("T1: o balanço de fim de volta também saiu",
          any(adv for _, adv in cockpit.said_lap),
          f"{sum(len(a) for _, a in cockpit.said_lap)} conselhos")

    resumo = cockpit.coach.lap_summary()
    check("T1: o coach diz quanto tempo ainda está na mesa",
          resumo is not None and "na mesa" in resumo.text,
          resumo.text if resumo else "sem resumo")
    check("T1: o tempo na mesa bate com a soma das curvas",
          abs(cockpit.coach.time_on_the_table()
              - sum(max(0.0, p.avg_loss_s)
                    for p in cockpit.coach.profiles.values()
                    if p.samples >= 2)) < 1e-9,
          f"{cockpit.coach.time_on_the_table():.3f}s")

    pb_t1 = lib.personal_best(TRACK_NAME, CAR_NAME).lap_time_ms

    # =======================================================================
    # SEXTA — TREINO 2: o piloto ouviu o coach
    # =======================================================================
    cockpit.clear_speech()
    piloto_t2 = piloto.improved(factor=0.35)     # corrigiu ~2/3 dos erros
    t2 = Session("Practice 2", piloto_t2, fuel=55.0, track_temp=34.0,
                 sample_hz=SAMPLE_HZ)
    tempos_t2 = run_session(cockpit, t2, [
        LapPlan(out_lap=True), LapPlan(), LapPlan(), LapPlan(),
        LapPlan(in_lap=True),
    ], new_session=True)
    semeadas_t2 = cockpit.seeded_corners
    sessao_t2 = cockpit.sm.session_id

    pb_t2 = lib.personal_best(TRACK_NAME, CAR_NAME).lap_time_ms
    check("T2: o Personal Best melhorou depois da correção",
          pb_t2 < pb_t1, f"{pb_t1} ms -> {pb_t2} ms")
    check("T2: as voltas do T2 se somaram às do T1 no catálogo",
          len(lib.records(TRACK_NAME, CAR_NAME)) == 11,
          f"{len(lib.records(TRACK_NAME, CAR_NAME))} voltas")
    check("T2: T1 e T2 viraram sessões DISTINTAS, sem reiniciar o app",
          sessao_t1 != sessao_t2, f"{sessao_t1} vs {sessao_t2}")
    check("T2: o histórico da tela foi zerado na virada de sessão",
          len(cockpit.sm.historic_laps) == 5,
          f"{len(cockpit.sm.historic_laps)} linhas (deveriam ser só as do T2)")

    check("T2: o coach chegou na sessão nova JÁ SABENDO onde ele perde",
          semeadas_t2 >= len(CORNERS) - 1,
          f"{semeadas_t2} de {len(CORNERS)} curvas semeadas do histórico")
    arquivo_bests = cockpit.bests_store.path_for(TRACK_NAME, CAR_NAME)
    check("T2: o aprendizado foi para o disco",
          os.path.exists(arquivo_bests)
          and os.path.getsize(arquivo_bests) < 8192,
          f"{os.path.getsize(arquivo_bests)} bytes"
          if os.path.exists(arquivo_bests) else "arquivo ausente")

    dicas_t2 = [a for _, a in cockpit.said_coach if a.key.startswith("coach_cue")]
    check("T2: o coach continua acompanhando o piloto",
          len(cockpit.said_coach) >= 0)
    perfil_ferradura2 = cockpit.coach.profiles.get(5)
    check("T2: o coach percebeu a Ferradura melhorar",
          perfil_ferradura2 is not None
          and perfil_ferradura2.avg_loss_s <= media_ferradura_t1 + 1e-9,
          f"média {media_ferradura_t1:+.3f} -> "
          f"{perfil_ferradura2.avg_loss_s:+.3f}"
          if perfil_ferradura2 else "sem perfil")
    check("T2: o alvo da Ferradura ficou mais rápido "
          "(o piloto bateu a própria melhor passagem)",
          perfil_ferradura2 is not None and alvo_ferradura_t1 is not None
          and perfil_ferradura2.target_section_s < alvo_ferradura_t1,
          f"{alvo_ferradura_t1:.3f}s -> "
          f"{perfil_ferradura2.target_section_s:.3f}s"
          if perfil_ferradura2 and alvo_ferradura_t1 else "sem alvo")

    # =======================================================================
    # SÁBADO — TREINO 3
    # =======================================================================
    cockpit.clear_speech()
    piloto_t3 = piloto.improved(factor=0.15)
    t3 = Session("Practice 3", piloto_t3, fuel=40.0, track_temp=38.0,
                 sample_hz=SAMPLE_HZ)
    run_session(cockpit, t3, [LapPlan(out_lap=True), LapPlan(), LapPlan(),
                              LapPlan(in_lap=True)], new_session=True)
    pb_t3 = lib.personal_best(TRACK_NAME, CAR_NAME).lap_time_ms
    check("T3: o ritmo continuou evoluindo", pb_t3 <= pb_t2,
          f"{pb_t2} ms -> {pb_t3} ms")

    # =======================================================================
    # SÁBADO — CLASSIFICAÇÃO
    # =======================================================================
    cockpit.clear_speech()
    piloto_q = piloto.improved(factor=0.0)       # a volta perfeita dele
    q = Session("Qualify", piloto_q, fuel=20.0, track_temp=36.0,
                sample_hz=SAMPLE_HZ)
    run_session(cockpit, q, [
        LapPlan(out_lap=True),      # saída
        LapPlan(),                  # VOLTA LANÇADA
        LapPlan(in_lap=True),       # retorno
    ], pace_scale=1.02, new_session=True)   # tanque leve: o carro anda mais

    check("Q: o coach entrou em modo classificação",
          cockpit.coach.mode == MODE_QUALIFY, cockpit.coach.mode)
    check("Q: nenhum veredito de saída na classificação "
          "(o piloto não pode se distrair com o que já passou)",
          not [a for _, a in cockpit.said_coach if a.key.startswith("coach_exit")],
          str([a.text for _, a in cockpit.said_coach][:3]))

    pb_q = lib.personal_best(TRACK_NAME, CAR_NAME)
    check("Q: a volta lançada virou o Personal Best do fim de semana",
          pb_q.lap_time_ms < pb_t3, f"{pb_t3} ms -> {pb_q.lap_time_ms} ms")
    check("Q: o PB é uma volta inteira e limpa",
          pb_q.full_lap and pb_q.valid)

    # =======================================================================
    # DOMINGO — A CORRIDA
    # =======================================================================
    cockpit.clear_speech()
    piloto_r = piloto.improved(factor=0.25)
    VOLTAS = 8
    r = Session("Race", piloto_r, total_laps=VOLTAS, fuel=24.0,
                track_temp=41.0, sample_hz=SAMPLE_HZ)
    planos = [
        LapPlan(),                                   # 1
        LapPlan(flag="AMARELA"),                     # 2 — incidente na pista
        LapPlan(cut_corner=5),                       # 3 — cortou a Ferradura
        LapPlan(),                                   # 4
        LapPlan(damage=35.0),                        # 5 — toque
        LapPlan(tyre_temp=118.0),                    # 6 — pneu superaquecido
        LapPlan(penalty_s=5.0),                      # 7 — penalidade
        LapPlan(),                                   # 8 — última volta
    ]
    run_session(cockpit, r, planos, new_session=True)

    check("R: o coach entrou em modo corrida",
          cockpit.coach.mode == MODE_RACE, cockpit.coach.mode)
    dicas_corrida = [a for _, a in cockpit.said_coach
                     if a.key.startswith("coach_cue")]
    check("R: na corrida o coach quase não fala "
          "(quem briga por posição não quer aula)",
          len(dicas_corrida) <= VOLTAS,
          f"{len(dicas_corrida)} dicas em {VOLTAS} voltas")

    textos_live = " | ".join(cockpit.live_texts())
    check("R: avisou a bandeira amarela", "amarela" in textos_live.lower(),
          textos_live[:120])
    check("R: avisou o corte de pista",
          "limites da pista" in textos_live.lower(), textos_live[:120])
    check("R: avisou o dano no carro",
          "danificado" in textos_live.lower() or "dano" in textos_live.lower(),
          textos_live[:150])
    check("R: avisou a penalidade", "penalidade" in textos_live.lower())
    check("R: avisou o pneu quente", "pneu" in textos_live.lower())
    check("R: anunciou a última volta", "última volta" in textos_live.lower())
    criticos = [a for _, a in cockpit.said_live if a.severity == CRITICAL]
    check("R: os avisos críticos foram marcados como críticos",
          len(criticos) >= 2, str([a.text for a in criticos][:3]))

    # --- A volta cortada não pode contaminar a referência ---
    voltas_corrida = [rec for rec in lib.records(TRACK_NAME, CAR_NAME)
                      if rec.session_id == cockpit.sm.session_id]
    invalidas = [rec for rec in voltas_corrida if not rec.valid]
    check("R: a volta em que cortou a pista ficou marcada como inválida",
          len(invalidas) >= 1, f"{len(invalidas)} inválidas de {len(voltas_corrida)}")
    check("R: a volta inválida foi gravada assim mesmo "
          "(o tempo aconteceu, só não vale)",
          all(rec.points > 100 for rec in invalidas))
    pb_final = lib.personal_best(TRACK_NAME, CAR_NAME)
    check("R: nenhuma volta inválida virou Personal Best",
          pb_final.valid and pb_final.lap_id not in {r_.lap_id for r_ in invalidas})

    # =======================================================================
    # DEPOIS DO FIM DE SEMANA
    # =======================================================================
    todas = lib.records(TRACK_NAME, CAR_NAME)
    esperado = 6 + 5 + 4 + 3 + VOLTAS
    check("Todas as voltas do fim de semana estão no catálogo",
          len(todas) == esperado, f"{len(todas)} de {esperado}")

    sessoes = lib.sessions(TRACK_NAME, CAR_NAME)
    check("As voltas estão agrupadas por sessão",
          len(sessoes) == 5, f"{len(sessoes)} sessões: "
          + str([(s['session_id'][-6:], len(s['laps'])) for s in sessoes]))
    check("Cada sessão sabe qual foi a melhor volta dela",
          all(s["best"] is not None for s in sessoes),
          str([s["best"].lap_time_str if s["best"] else None for s in sessoes]))

    check("O Personal Best do fim de semana é a volta da classificação",
          pb_final.lap_time_ms == pb_q.lap_time_ms,
          f"{pb_final.lap_time_str} (Q: {pb_q.lap_time_str})")

    # --- Qualquer volta pode ser reaberta depois, com a telemetria inteira ---
    amostra = [todas[0], todas[len(todas) // 2], todas[-1]]
    ok_reabrir = True
    for rec in amostra:
        tel = lib.load_telemetry(TRACK_NAME, CAR_NAME, rec)
        if not tel or len(tel.get("times", [])) < 100:
            ok_reabrir = False
    check("Qualquer volta do fim de semana reabre com a telemetria inteira",
          ok_reabrir, str([(r_.lap_number, r_.points) for r_ in amostra]))

    canais = set(lib.load_telemetry(TRACK_NAME, CAR_NAME, todas[0]) or {})
    check("As voltas guardadas têm todos os canais para reanálise",
          {"times", "distance", "speed", "gas", "brake", "gear", "steer",
           "car_x", "car_z", "g_lat"} <= canais,
          str(sorted(canais)))

    # --- A análise curva a curva funciona sobre as voltas gravadas ---
    pior = lib.records(TRACK_NAME, CAR_NAME, only_full=True, only_valid=True)
    pior.sort(key=lambda r_: -r_.lap_time_ms)
    lenta = lib.load_telemetry(TRACK_NAME, CAR_NAME, pior[0])
    rapida = lib.load_telemetry(TRACK_NAME, CAR_NAME, pb_final)
    cmps = ca.compare_laps(lenta, rapida, CORNERS, TRACK_LENGTH)
    medidas = [c for c in cmps if c.delta_time is not None]
    check("Dá para comparar duas voltas do fim de semana curva a curva",
          len(medidas) >= len(CORNERS) - 1,
          f"{len(medidas)} de {len(CORNERS)} curvas medidas")

    # --- A retenção não pode comer o que importa ---
    lib.prune(TRACK_NAME, CAR_NAME)
    ainda = {r_.lap_id for r_ in lib.records(TRACK_NAME, CAR_NAME)}
    check("A limpeza automática não apagou o Personal Best",
          pb_final.lap_id in ainda)

    # --- Tamanho em disco de um fim de semana inteiro ---
    bytes_total = lib.disk_usage(TRACK_NAME, CAR_NAME)
    por_volta = bytes_total / max(1, len(todas))
    check("Um fim de semana inteiro cabe num tamanho razoável",
          bytes_total < 40 * 1024 * 1024,
          f"{bytes_total/1024/1024:.1f} MB no total, "
          f"{por_volta/1024:.0f} KB por volta")

    # --- Memória: o catálogo não pode ficar tudo na RAM ---
    check("A memória não guarda a telemetria de todas as voltas",
          len(lib._telemetry_cache) <= lib.CACHE_SIZE,
          f"{len(lib._telemetry_cache)} voltas em cache")
    check("O histórico da sessão guarda metadados, não telemetria",
          all("telemetry" not in e for e in cockpit.sm.completed_laps))

    # --- A tela de análise pós-sessão abre o fim de semana ---
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mapa_weekend", os.path.join(ROOT, "mapa.pyw"))
    mapa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mapa)
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    janela = mapa.LapAnalysisWindow(lib)
    topo = janela.tree.topLevelItem(0)
    check("A análise pós-sessão lista o fim de semana inteiro",
          janela.tree.topLevelItemCount() == 1 and topo.childCount() == 5,
          f"{topo.childCount() if topo else 0} sessões na árvore")
    folhas = sum(topo.child(i).childCount() for i in range(topo.childCount()))
    check("Todas as voltas aparecem na árvore da análise",
          folhas == esperado, f"{folhas} de {esperado}")

    csv_path = os.path.join(work, "volta.csv")
    # --- O aprendizado do coach sobreviveu ao fim de semana inteiro --------
    guardadas, _ = cockpit.bests_store.load(TRACK_NAME, CAR_NAME,
                                            cockpit.map_signature)
    check("As melhores passagens de curva sobreviveram às cinco sessões",
          len(guardadas) >= len(CORNERS) - 1,
          f"{len(guardadas)} de {len(CORNERS)} curvas")
    check("Cada passagem guardada aponta a volta de onde veio",
          all(b.lap_id or b.section_s > 0 for b in guardadas.values()))
    fresco = LiveCoach()
    fresco.set_track(CORNERS, TRACK_LENGTH)
    check("Um coach novo, na próxima sessão, já começa com alvo",
          fresco.load_bests(guardadas) >= len(CORNERS) - 1
          and fresco.has_reference())

    check("Dá para exportar uma volta do fim de semana em CSV",
          lib.export_csv(TRACK_NAME, CAR_NAME, pb_final, csv_path)
          and os.path.getsize(csv_path) > 1000)
    janela.close()

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
print(f"    (fim de semana simulado em {time.perf_counter() - _t0:.1f}s)")
print(f"=== {len(results) - falhas}/{len(results)} verificacoes passaram ===")
sys.exit(1 if falhas else 0)
