"""
tests/test_live_coach.py — Disciplina do coach de curva
========================================================

O valor do coach ao vivo não está no que ele fala: está no que ele NÃO fala.
Um coach que abre a boca na entrada da curva, que repete a mesma dica quatro
vezes na mesma volta ou que comenta a volta de retorno aos boxes é desligado
pelo piloto na primeira sessão — e aí não ajuda em nada.

Este teste cobre exatamente essas fronteiras:

  * cala a boca com o carro carregado (freando ou virando de verdade);
  * cala a boca no box, em replay e com o jogo pausado;
  * a dica chega ANTES do ponto de freada, com antecedência proporcional à
    velocidade, e uma vez só por curva por volta;
  * o veredito chega DEPOIS da saída da curva;
  * dois segundos perdidos numa curva não rendem conselho de técnica (foi
    rodada ou tráfego, e o piloto já sabe);
  * volta de box e volta com corte de pista não entram no aprendizado;
  * o alvo de cada curva é a melhor passagem conhecida, própria ou da
    referência — o que faz o coach funcionar sem referência externa.

    python tests/test_live_coach.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import TelemetryState
from core import corner_analysis as ca
from core.live_coach import (
    LiveCoach, CornerProfile, dominant_cause, mode_for_session,
    MODE_PRACTICE, MODE_QUALIFY, MODE_RACE,
    CAUSE_BRAKE_EARLY, CAUSE_BRAKE_LATE, CAUSE_VMIN, CAUSE_THROTTLE,
    APPROACH_MIN_M, APPROACH_PREFERRED_LEAD_S, APPROACH_LEAD_S,
    MIN_GAP_S, MAX_MEANINGFUL_LOSS_S, _decimos, _cue_text,
)

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


TRACK_LENGTH = 4000.0
#: Duas curvas bem separadas: uma em 25% e outra em 60% da pista
CORNERS = [
    ca.Corner(index=1, name="Ferradura", start=0.25, end=0.32, direction="L"),
    ca.Corner(index=2, name="Pinheirinho", start=0.60, end=0.66, direction="R"),
]


def state(distance, *, speed=250.0, brake=0.0, gas=1.0, g_lat=0.0,
          session="Practice", delta=-0.1, **kw):
    st = TelemetryState(is_connected=True)
    st.track_length = TRACK_LENGTH
    st.distance_traveled = distance
    st.track_position = distance / TRACK_LENGTH
    st.speed_kmh = speed
    st.brake = brake
    st.gas = gas
    st.g_lat = g_lat
    st.session_type = session
    st.delta_time = delta
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def metrics(corner, *, entry, exit_, vmin=110.0, brake_m=None, thr_m=None):
    m = ca.CornerMetrics(corner=corner)
    m.entry_time, m.exit_time = entry, exit_
    m.v_min = vmin
    m.v_min_m = (corner.start + corner.end) / 2 * TRACK_LENGTH
    m.braking_point_m = (brake_m if brake_m is not None
                         else corner.start_m(TRACK_LENGTH) - 100.0)
    m.throttle_point_m = thr_m if thr_m is not None else m.v_min_m + 20.0
    return m


def fresh_coach(*, with_reference=True):
    coach = LiveCoach()
    coach.set_track(CORNERS, TRACK_LENGTH)
    if with_reference:
        # Referência plantada à mão: assim o teste controla os números
        for c in CORNERS:
            coach.ref_metrics[c.index] = metrics(
                c, entry=0.0, exit_=6.0,
                brake_m=c.start_m(TRACK_LENGTH) - 100.0)
        coach._ref_signature = ("plantada",)
    return coach


def teach(coach, corner_index, *, loss_s, cause_offset_m=-20.0, laps=3):
    """Faz o coach aprender que o piloto perde tempo numa curva."""
    corner = next(c for c in CORNERS if c.index == corner_index)
    for i in range(laps):
        lap = metrics(corner, entry=0.0, exit_=6.0 + loss_s,
                      brake_m=corner.start_m(TRACK_LENGTH) - 100.0 + cause_offset_m)
        ref = metrics(corner, entry=0.0, exit_=6.0,
                      brake_m=corner.start_m(TRACK_LENGTH) - 100.0)
        coach.on_lap_completed([ca.CornerComparison(corner=corner, lap=lap,
                                                    ref=ref)])
    return coach.profiles[corner_index]


# ---------------------------------------------------------------------------
# 1. Modo por tipo de sessão
# ---------------------------------------------------------------------------

check("treino livre entra em modo treino",
      mode_for_session(state(0, session="Practice")) == MODE_PRACTICE)
check("classificação entra em modo classificação",
      mode_for_session(state(0, session="Qualify")) == MODE_QUALIFY)
check("hotlap conta como classificação",
      mode_for_session(state(0, session="Hotlap")) == MODE_QUALIFY)
check("corrida entra em modo corrida",
      mode_for_session(state(0, session="Race")) == MODE_RACE)
check("sessão com número de voltas definido é corrida, "
      "mesmo sem o nome dizer",
      mode_for_session(state(0, session="", total_laps=12)) == MODE_RACE)


# ---------------------------------------------------------------------------
# 2. Quando o coach TEM que ficar calado
# ---------------------------------------------------------------------------

corner1 = CORNERS[0]
ponto_freio = corner1.start_m(TRACK_LENGTH) - 100.0


def silencio(**kw):
    """Roda o coach num ponto onde ELE FALARIA, com a condição de kw."""
    coach = fresh_coach()
    teach(coach, 1, loss_s=0.30)
    # Ponto de disparo da dica: um pouco antes da freada
    d = ponto_freio - 120.0
    return coach.update(state(d, **kw), {}, now=100.0)


check("não fala com o freio pisado", not silencio(brake=0.6))
check("não fala com o carro virando de verdade", not silencio(g_lat=1.8))
check("não fala parado no box", not silencio(in_pit=True))
check("não fala no pit lane", not silencio(in_pit_lane=True))
check("não fala em replay", not silencio(is_replay=True))
check("não fala com o jogo pausado", not silencio(is_paused=True))
check("...mas fala na reta, com o carro leve", bool(silencio()))


# ---------------------------------------------------------------------------
# 3. A dica chega ANTES da freada
# ---------------------------------------------------------------------------

coach = fresh_coach()
perfil = teach(coach, 1, loss_s=0.30, cause_offset_m=-20.0)
check("o coach aprendeu a causa (freou cedo)",
      perfil.cause == CAUSE_BRAKE_EARLY, f"{perfil.cause} {perfil.cause_value:.0f}m")

disparos = []
for metro in range(0, int(TRACK_LENGTH), 5):
    for adv in coach.update(state(metro), {}, now=metro * 0.1):
        disparos.append((metro, adv))
check("a dica saiu uma vez", len(disparos) == 1,
      str([(m, a.text) for m, a in disparos]))
if disparos:
    metro, adv = disparos[0]
    check("a dica chegou ANTES do ponto de freada",
          metro < ponto_freio, f"dica em {metro} m, freada em {ponto_freio:.0f} m")
    check("a dica chegou com antecedência de sobra",
          ponto_freio - metro >= APPROACH_MIN_M,
          f"{ponto_freio - metro:.0f} m de antecedência")
    # O gatilho é por TEMPO até a freada, não por distância: saindo de uma
    # curva lenta, 250 m de sobra seriam sete segundos, e o piloto esqueceria.
    lead_s = (ponto_freio - metro) / (250.0 / 3.6)
    check("a antecedência é medida em SEGUNDOS de pista",
          APPROACH_LEAD_S <= lead_s <= APPROACH_PREFERRED_LEAD_S + 0.4,
          f"{lead_s:.1f}s de antecedência")
    check("a dica diz o que fazer e quanto",
          "atrasa a freada" in adv.text and "20 metros" in adv.text, adv.text)
    check("a dica identifica a curva pelo nome", "Ferradura" in adv.text, adv.text)

# A antecedência em SEGUNDOS tem que ser parecida em velocidades diferentes —
# é isso que o gatilho por tempo garante e o por distância não garantiria.
leads = {}
for v in (100.0, 180.0, 280.0):
    c = fresh_coach()
    teach(c, 1, loss_s=0.30)
    for metro in range(0, int(TRACK_LENGTH), 4):
        saiu = c.update(state(metro, speed=v), {}, now=metro * 0.1)
        if saiu:
            leads[v] = (ponto_freio - metro) / (v / 3.6)
            break
check("a antecedência em segundos é estável entre 100 e 280 km/h",
      len(leads) == 3
      and all(APPROACH_LEAD_S <= t <= APPROACH_PREFERRED_LEAD_S + 0.4
              for t in leads.values()),
      str({int(k): round(v, 1) for k, v in leads.items()}))

# Segunda volta: a dica pode sair de novo (uma por volta)
coach._last_spoke_at = -999.0
coach.reset_lap()
de_novo = [a for m in range(0, int(TRACK_LENGTH), 5)
           for a in coach.update(state(m), {}, now=1000.0 + m * 0.1)]
check("na volta seguinte a dica volta a sair", len(de_novo) == 1,
      f"{len(de_novo)} recados")


# ---------------------------------------------------------------------------
# 4. Uma dica por curva por volta, e o intervalo mínimo
# ---------------------------------------------------------------------------

coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
teach(coach, 2, loss_s=0.25)
todos = []
for metro in range(0, int(TRACK_LENGTH), 5):
    for adv in coach.update(state(metro), {}, now=metro * 0.5):
        todos.append((metro, adv))
chaves = [a.key for _, a in todos]
check("as duas curvas problemáticas foram avisadas",
      set(chaves) == {"coach_cue:1", "coach_cue:2"}, str(chaves))
check("nenhuma curva foi avisada duas vezes na mesma volta",
      len(chaves) == len(set(chaves)), str(chaves))
tempos = [m * 0.5 for m, _ in todos]
check("respeitou o intervalo mínimo entre recados",
      all(b - a >= MIN_GAP_S for a, b in zip(tempos, tempos[1:])),
      str([round(t, 1) for t in tempos]))

# Em corrida, só a curva pior — e só se a perda for grande
coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
teach(coach, 2, loss_s=0.25)
na_corrida = [a for m in range(0, int(TRACK_LENGTH), 5)
              for a in coach.update(state(m, session="Race", total_laps=10),
                                    {}, now=m * 0.5)]
check("na corrida sai no máximo uma dica por volta",
      len(na_corrida) <= 1, str([a.text for a in na_corrida]))

coach = fresh_coach()
teach(coach, 1, loss_s=0.12)      # perda pequena
pouco = [a for m in range(0, int(TRACK_LENGTH), 5)
         for a in coach.update(state(m, session="Race", total_laps=10),
                               {}, now=m * 0.5)]
check("na corrida uma perda pequena não vira dica", not pouco,
      str([a.text for a in pouco]))
em_treino = fresh_coach()
teach(em_treino, 1, loss_s=0.12)
muito = [a for m in range(0, int(TRACK_LENGTH), 5)
         for a in em_treino.update(state(m), {}, now=m * 0.5)]
check("...mas em treino a MESMA perda vira dica", bool(muito),
      str([a.text for a in muito]))


# ---------------------------------------------------------------------------
# 5. Classificação: só na volta lançada, e sem veredito
# ---------------------------------------------------------------------------

coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
na_saida = [a for m in range(0, int(TRACK_LENGTH), 5)
            for a in coach.update(state(m, session="Qualify", delta=0.0),
                                  {}, now=m * 0.5)]
check("na classificação o coach fica calado fora da volta lançada",
      not na_saida, str([a.text for a in na_saida]))

coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
na_lancada = [a for m in range(0, int(TRACK_LENGTH), 5)
              for a in coach.update(state(m, session="Qualify", delta=-0.2),
                                    {}, now=m * 0.5)]
check("...e fala na volta lançada", bool(na_lancada),
      str([a.text for a in na_lancada]))


# ---------------------------------------------------------------------------
# 6. O veredito na saída da curva
# ---------------------------------------------------------------------------

def lap_telemetry(*, entry_t, exit_t, brake_offset=0.0):
    """
    Telemetria parcial da volta em andamento, cobrindo a curva 1.

    Simples de propósito: o que importa aqui é o tempo de trecho, e é dele
    que o veredito é tirado.
    """
    inicio = corner1.start_m(TRACK_LENGTH)
    fim = corner1.end_m(TRACK_LENGTH)
    n = 400
    dist, times, speed, brake, gas = [], [], [], [], []
    for i in range(n):
        d = (i / (n - 1)) * (fim + 300.0)
        dist.append(d)
        if d <= inicio:
            times.append(entry_t * (d / max(1.0, inicio)))
        elif d <= fim:
            frac = (d - inicio) / max(1.0, fim - inicio)
            times.append(entry_t + (exit_t - entry_t) * frac)
        else:
            times.append(exit_t + (d - fim) * 0.01)
        speed.append(120.0)
        no_freio = (inicio - 100.0 + brake_offset) <= d <= inicio
        brake.append(0.9 if no_freio else 0.0)
        gas.append(0.0 if no_freio else 1.0)
    return {"times": times, "distance": dist, "speed": speed,
            "brake": brake, "gas": gas}


coach = fresh_coach()
coach.profiles[1].avg_loss_s = 0.20      # já era uma curva problemática
coach.profiles[1].samples = 3
tel = lap_telemetry(entry_t=10.0, exit_t=16.25)     # 6.25 s: 0.25 pior que a ref
saida = corner1.end_m(TRACK_LENGTH)
vereditos = []
for metro in range(int(saida), int(saida) + 200, 5):
    for adv in coach.update(state(metro), tel, now=500.0 + metro * 0.1):
        vereditos.append((metro, adv))
check("o veredito saiu DEPOIS da curva",
      bool(vereditos) and vereditos[0][0] > saida,
      f"saída em {saida:.0f} m, veredito em "
      f"{vereditos[0][0] if vereditos else '-'} m")
if vereditos:
    texto = vereditos[0][1].text
    check("o veredito diz quanto e onde",
          "Perdeu" in texto and "Ferradura" in texto, texto)
    check("o veredito fala em décimos, não em milésimos",
          "décimo" in texto, texto)

# Perda absurda: foi incidente, não técnica
coach = fresh_coach()
coach.profiles[1].avg_loss_s = 0.20
coach.profiles[1].samples = 3
tel_rodada = lap_telemetry(entry_t=10.0, exit_t=19.0)   # 3 s perdidos
absurdo = [a for m in range(int(saida), int(saida) + 200, 5)
           for a in coach.update(state(m), tel_rodada, now=900.0 + m * 0.1)]
check("perda absurda numa curva não rende conselho de técnica",
      not [a for a in absurdo if a.key.startswith("coach_exit")],
      str([a.text for a in absurdo]))

# Volta de box: nenhum veredito
coach = fresh_coach()
coach.profiles[1].avg_loss_s = 0.20
coach.profiles[1].samples = 3
coach.update(state(10.0, in_pit_lane=True), {}, now=1000.0)   # passou pelo box
na_volta_de_box = [a for m in range(int(saida), int(saida) + 200, 5)
                   for a in coach.update(state(m), tel, now=1100.0 + m * 0.1)]
check("volta que passou pelo box não recebe veredito nenhum",
      not [a for a in na_volta_de_box if a.key.startswith("coach_exit")],
      str([a.text for a in na_volta_de_box]))


# ---------------------------------------------------------------------------
# 7. Aprendizado: o que entra e o que não entra
# ---------------------------------------------------------------------------

coach = fresh_coach()
lap = metrics(corner1, entry=0.0, exit_=6.5)
ref = metrics(corner1, entry=0.0, exit_=6.0)
cmp_ = ca.CornerComparison(corner=corner1, lap=lap, ref=ref)

coach.on_lap_completed([cmp_], pit_lap=True)
check("volta de box não entra no aprendizado",
      coach.profiles[1].samples == 0, str(coach.profiles[1].samples))
coach.on_lap_completed([cmp_], valid=False)
check("volta com corte de pista não entra no aprendizado",
      coach.profiles[1].samples == 0, str(coach.profiles[1].samples))
coach.on_lap_completed([cmp_])
check("volta boa entra no aprendizado", coach.profiles[1].samples == 1)
check("a melhor passagem do próprio piloto é registrada",
      abs(coach.profiles[1].best_section_s - 6.5) < 1e-9,
      str(coach.profiles[1].best_section_s))
check("...e a referência mais rápida vence como ALVO",
      abs(coach.profiles[1].target_section_s - 6.0) < 1e-9
      and abs(coach.profiles[1].avg_loss_s - 0.5) < 1e-9,
      f"alvo {coach.profiles[1].target_section_s} "
      f"média {coach.profiles[1].avg_loss_s:+.3f}")

# Uma passagem melhor rebaixa o alvo
melhor = metrics(corner1, entry=0.0, exit_=5.8)
coach.on_lap_completed([ca.CornerComparison(corner=corner1, lap=melhor, ref=ref)])
check("uma passagem melhor que a referência vira o novo alvo",
      abs(coach.profiles[1].best_section_s - 5.8) < 1e-9
      and abs(coach.profiles[1].target_section_s - 5.8) < 1e-9,
      f"melhor {coach.profiles[1].best_section_s} "
      f"alvo {coach.profiles[1].target_section_s}")

# Sem referência externa nenhuma, o coach ainda tem alvo
sozinho = LiveCoach()
sozinho.set_track(CORNERS, TRACK_LENGTH)
check("sem referência o coach começa sem alvo", not sozinho.has_reference())
sozinho.on_lap_completed([ca.CornerComparison(
    corner=corner1, lap=metrics(corner1, entry=0.0, exit_=7.0), ref=None)])
check("uma volta medida já dá alvo ao coach", sozinho.has_reference())
sozinho.on_lap_completed([ca.CornerComparison(
    corner=corner1, lap=metrics(corner1, entry=0.0, exit_=7.4), ref=None)])
# A média é móvel com peso 0.5: a primeira volta entrou como 0 (ela mesma virou
# o alvo) e a segunda como +0.4, então a média fica em 0.2. É de propósito — o
# coach acompanha quem está melhorando em vez de cobrar a pior volta para sempre.
check("sem referência externa, o alvo é a melhor passagem do próprio piloto",
      abs(sozinho.profiles[1].target_section_s - 7.0) < 1e-9
      and abs(sozinho.profiles[1].avg_loss_s - 0.2) < 1e-9,
      f"alvo {sozinho.profiles[1].best_section_s:.2f}s "
      f"média {sozinho.profiles[1].avg_loss_s:+.3f}s")

# Opinar exige repetição
uma_vez = LiveCoach()
uma_vez.set_track(CORNERS, TRACK_LENGTH)
uma_vez.on_lap_completed([cmp_])
check("com UMA volta o coach não chama a curva de problema",
      not uma_vez.problem_corners(), str(uma_vez.problem_corners()))
uma_vez.on_lap_completed([cmp_])
check("com duas voltas seguidas perdendo, aí sim",
      [p.index for p in uma_vez.problem_corners()] == [1])


# ---------------------------------------------------------------------------
# 7b. Alvo vindo do histórico: o coach chega na sessão nova já sabendo
# ---------------------------------------------------------------------------

from core.corner_bests import CornerBest

semente = {1: CornerBest(index=1, name="Ferradura", section_s=6.0,
                         braking_point_m=ponto_freio, v_min=115.0,
                         v_min_m=corner1.start_m(TRACK_LENGTH) + 100.0,
                         throttle_point_m=corner1.start_m(TRACK_LENGTH) + 160.0)}

com_historico = LiveCoach()
com_historico.set_track(CORNERS, TRACK_LENGTH)
check("um coach vazio não tem alvo nenhum", not com_historico.has_reference())
check("load_bests devolve quantas curvas semeou",
      com_historico.load_bests(semente) == 1)
check("...e o coach passa a ter alvo sem ter rodado uma volta",
      com_historico.has_reference())
p1 = com_historico.profiles[1]
check("o alvo semeado traz o ponto de freada da boa passagem",
      p1.target_metrics is not None
      and p1.target_metrics.braking_point_m == ponto_freio)
check("curva semeada ainda não é problema: falta medir o piloto de hoje",
      p1.seeded and not p1.is_problem)

# UMA volta hoje já basta
com_historico.on_lap_completed([ca.CornerComparison(
    corner=corner1, lap=metrics(corner1, entry=0.0, exit_=6.28,
                                brake_m=ponto_freio - 22.0), ref=None)])
check("com histórico, UMA volta de hoje já aponta a curva",
      [p.index for p in com_historico.problem_corners()] == [1],
      str([(p.index, round(p.avg_loss_s, 3))
           for p in com_historico.problem_corners()]))
dica_semeada = [a for m in range(0, int(TRACK_LENGTH), 4)
                for a in com_historico.update(state(m), {}, now=m * 0.5)]
cues = [a for a in dica_semeada if a.key.startswith("coach_cue")]
check("...e a dica sai na volta seguinte", len(cues) == 1,
      str([a.text for a in dica_semeada]))
if cues:
    check("a dica semeada aponta a causa medida hoje",
          "atrasa a freada" in cues[0].text, cues[0].text)

# Sem histórico, a mesma volta única não rende dica
sem_historico = LiveCoach()
sem_historico.set_track(CORNERS, TRACK_LENGTH)
sem_historico.on_lap_completed([ca.CornerComparison(
    corner=corner1, lap=metrics(corner1, entry=0.0, exit_=6.28,
                                brake_m=ponto_freio - 22.0), ref=None)])
check("sem histórico, a MESMA volta única não rende dica",
      not sem_historico.problem_corners(),
      str(sem_historico.problem_corners()))

# A dica e o veredito declaram a própria validade
val = fresh_coach()
teach(val, 1, loss_s=0.30)
recados = [a for m in range(0, int(TRACK_LENGTH), 5)
           for a in val.update(state(m), {}, now=m * 0.5)]
check("a dica declara validade curta (vale até a freada)",
      bool(recados) and recados[0].ttl_s is not None and recados[0].ttl_s <= 5.0,
      f"ttl={recados[0].ttl_s if recados else None}")

# reset() não pode jogar o histórico fora sem ser mandado
apos_reset = LiveCoach()
apos_reset.set_track(CORNERS, TRACK_LENGTH)
apos_reset.load_bests(semente)
apos_reset.reset()
check("reset limpa o alvo semeado (é para pista/carro novo)",
      not apos_reset.has_reference())
check("...e semear de novo o traz de volta",
      apos_reset.load_bests(semente) == 1 and apos_reset.has_reference())


# ---------------------------------------------------------------------------
# 8. Causa dominante e texto das frases
# ---------------------------------------------------------------------------

def cmp_com(*, brake=None, vmin=None, thr=None):
    lap = metrics(corner1, entry=0.0, exit_=6.5)
    ref = metrics(corner1, entry=0.0, exit_=6.0)
    if brake is not None:
        lap.braking_point_m = ref.braking_point_m + brake
    if vmin is not None:
        lap.v_min = ref.v_min + vmin
    if thr is not None:
        lap.throttle_point_m = ref.throttle_point_m + thr
    return ca.CornerComparison(corner=corner1, lap=lap, ref=ref)


check("freou antes -> causa é o ponto de freada",
      dominant_cause(cmp_com(brake=-20.0))[0] == CAUSE_BRAKE_EARLY)
check("freou depois -> causa é o ponto de freada",
      dominant_cause(cmp_com(brake=+20.0))[0] == CAUSE_BRAKE_LATE)
check("ponto de freada certo e ápice devagar -> causa é o ápice",
      dominant_cause(cmp_com(brake=0.0, vmin=-6.0))[0] == CAUSE_VMIN)
check("só a retomada atrasada -> causa é a retomada",
      dominant_cause(cmp_com(brake=0.0, thr=+25.0))[0] == CAUSE_THROTTLE)
check("a freada tem prioridade sobre o ápice "
      "(é ela que causa o resto)",
      dominant_cause(cmp_com(brake=-20.0, vmin=-6.0))[0] == CAUSE_BRAKE_EARLY)
check("nada fora do limiar -> nenhuma causa",
      dominant_cause(cmp_com(brake=2.0, vmin=-0.5))[0] == "")

check("décimos são ditos como décimos", _decimos(0.24) == "2 décimos", _decimos(0.24))
check("um décimo no singular", _decimos(0.1) == "1 décimo", _decimos(0.1))
check("acima de um segundo usa segundos com vírgula",
      _decimos(1.35) == "1,4 segundos", _decimos(1.35))
check("perda desprezível não ganha número",
      _decimos(0.01) == "quase nada", _decimos(0.01))

frases = {
    CAUSE_BRAKE_EARLY: "atrasa a freada",
    CAUSE_BRAKE_LATE: "antecipa a freada",
    CAUSE_VMIN: "mais velocidade no ápice",
    CAUSE_THROTTLE: "abre o gás mais cedo",
}
ok_frases = True
for causa, esperado in frases.items():
    p = CornerProfile(index=1, name="Ferradura", cause=causa, cause_value=12.0,
                      avg_loss_s=0.2, samples=3)
    if esperado not in _cue_text("Ferradura", p):
        ok_frases = False
check("cada causa tem uma ordem clara e curta", ok_frases)
sem_causa = CornerProfile(index=1, name="Ferradura", avg_loss_s=0.25, samples=3)
check("sem causa identificada, a dica ainda diz quanto está em jogo",
      "em jogo" in _cue_text("Ferradura", sem_causa),
      _cue_text("Ferradura", sem_causa))


# ---------------------------------------------------------------------------
# 9. Resumo: tempo na mesa
# ---------------------------------------------------------------------------

coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
teach(coach, 2, loss_s=0.20)
resumo = coach.lap_summary()
check("o resumo soma o tempo na mesa",
      resumo is not None and "na mesa" in resumo.text, resumo.text if resumo else "")
check("o resumo cita as curvas que mais pesam",
      resumo is not None and "Ferradura" in resumo.text, resumo.text if resumo else "")
check("o resumo traz os números no detalhe, para o painel",
      resumo is not None and "melhor" in resumo.detail,
      resumo.detail if resumo else "")
check("o tempo na mesa é a soma das perdas médias",
      abs(coach.time_on_the_table()
          - sum(max(0.0, p.avg_loss_s) for p in coach.profiles.values()
                if p.samples >= 2)) < 1e-9)
limpo = fresh_coach()
check("piloto sem tempo na mesa não recebe resumo", limpo.lap_summary() is None)


# ---------------------------------------------------------------------------
# 10. reset: o que some e o que fica
# ---------------------------------------------------------------------------

coach = fresh_coach()
teach(coach, 1, loss_s=0.30)
coach.update(state(50.0), {}, now=10.0)
coach.reset_lap()
check("reset_lap preserva o que foi aprendido",
      coach.profiles[1].samples == 3
      and coach.profiles[1].target_section_s is not None)
coach.reset()
check("reset zera o aprendizado (pista ou carro novo)",
      not coach.problem_corners() and not coach.ref_metrics)
check("reset preserva as curvas da pista", len(coach.corners) == 2)

trocou = fresh_coach()
teach(trocou, 1, loss_s=0.30)
trocou.set_track([ca.Corner(index=1, name="Outra", start=0.1, end=0.2)],
                 TRACK_LENGTH)
check("trocar o mapa de curvas derruba o aprendizado antigo",
      trocou.profiles[1].samples == 0, str(trocou.profiles[1].samples))


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
