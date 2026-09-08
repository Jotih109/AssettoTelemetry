"""
core/live_coach.py — Coach de curva, com o carro andando
=========================================================

O engenheiro de pista ([core/race_engineer.py](core/race_engineer.py)) já dizia
onde o tempo tinha ido embora — mas só no fim da volta, quando o piloto já
tinha errado a mesma curva de novo. Este módulo fecha esse laço: ele fala
**antes** e **logo depois** de cada curva, enquanto dá para fazer alguma coisa
a respeito.

Dois momentos, e só dois:

* **Na aproximação** (~2 s antes da freada, na reta): uma dica curta e única,
  tirada do que o piloto vem repetindo NAQUELA curva nas últimas voltas.
  *"Ferradura, atrasa a freada uns 10 metros."* Chega com tempo de ser
  executada, e some — não é repetida na mesma volta.

* **Na saída** (~25 m depois do fim da curva): o veredito do que acabou de
  acontecer, medido nesta volta contra a referência. *"Perdeu 2 décimos ali,
  entrou devagar."* A sensação ainda está fresca no volante; no fim da volta
  já não está.

O que este módulo NÃO faz, de propósito:

* **Não fala entre a freada e a saída.** Da entrada ao ápice o piloto está
  ocupado; voz nessa janela atrapalha em vez de ajudar. Todo recado sai na
  reta anterior ou depois que o carro já está reto de novo.
* **Não comenta toda curva.** Só as duas em que o piloto mais perde, e só
  depois de ver o padrão se repetir — opinar sobre uma volta só é chute.
* **Não fala igual em toda sessão.** Em treino ele conversa; na classificação
  só na volta lançada; na corrida quase cala a boca, porque quem está brigando
  por posição não quer aula de ponto de freada.

As medidas vêm todas de `core/corner_analysis.py`; aqui só mora a decisão de
QUANDO abrir a boca e COM QUE palavras.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional

from core import corner_analysis as ca
from core.race_engineer import Advice, ATTENTION, INFO


# ---------------------------------------------------------------------------
# Quando falar
# ---------------------------------------------------------------------------

#: Antecedência PREFERIDA da dica, em segundos até a freada. O gatilho é por
#: TEMPO, não por distância: saindo de uma curva lenta, 250 m de sobra são
#: sete segundos — o piloto ouve "Ferradura chegando" e passa por outra curva
#: antes de chegar nela. Por tempo, a dica cai sempre no mesmo lugar da
#: cabeça dele, seja numa reta longa ou saindo de uma segunda marcha.
APPROACH_PREFERRED_LEAD_S = 3.0
#: Antecedência MÍNIMA. Abaixo disto o piloto ouve a frase enquanto já está
#: freando, e a dica vira atrapalho.
APPROACH_LEAD_S = 1.2
#: ...e nunca a menos disto em metros, por mais devagar que o carro esteja.
APPROACH_MIN_M = 45.0
#: Nem cedo demais — o piloto esquece antes de chegar.
#: A janela vai de APPROACH_MAX_M até a antecedência mínima, e a dica sai no
#: PRIMEIRO instante livre dentro dela. Numa pista de curvas coladas — a
#: Ferradura logo depois da Descida do Lago, aqui — a janela "ideal" cai
#: inteira dentro da curva anterior, onde o coach fica calado de propósito.
#: Com a janela larga ele aproveita a brecha que existir; com a janela
#: estreita, a dica saía colada na freada, sem tempo de ser usada.
APPROACH_MAX_M = 320.0
#: Metros depois do fim da curva em que o veredito de saída é dito. O carro já
#: está reto e acelerando; é a primeira janela livre depois do ápice.
EXIT_FEEDBACK_M = 25.0
#: Largura da janela de disparo, para o recado não se perder entre dois quadros.
TRIGGER_WINDOW_M = 140.0

#: Perda média na curva que justifica gastar uma dica com ela (s).
MIN_COACH_LOSS_S = 0.08
#: Quantas voltas medidas antes de opinar sobre uma curva. Com uma só, o que
#: parece vício é só uma volta ruim.
MIN_SAMPLES = 2
#: Peso da volta nova na média móvel de cada curva. 0.5 = a última volta pesa
#: metade — o coach acompanha quem está melhorando em vez de cobrar um erro
#: que o piloto já corrigiu.
PROFILE_ALPHA = 0.5

#: Perda na curva que rende veredito na saída (s).
EXIT_LOSS_S = 0.10
#: Ganho que rende elogio na saída (s).
EXIT_GAIN_S = 0.10

#: Intervalo mínimo entre dois recados do coach (s). O engenheiro tem o dele;
#: este é mais curto porque as frases daqui são curtas de propósito.
MIN_GAP_S = 3.0

#: Validade da dica de aproximação (s). Ela vale até a freada e nem um
#: segundo a mais: dita atrasada, o piloto ouve "Ferradura chegando" já dentro
#: da curva seguinte — e freia no lugar errado por causa do coach.
CUE_TTL_S = 3.5
#: Validade do veredito de saída (s). Um pouco mais folgada: "perdeu 2 décimos
#: ali" continua fazendo sentido alguns segundos depois, mas não a volta toda.
EXIT_TTL_S = 6.0

#: Queda de distância que caracteriza volta nova (m).
LAP_RESET_DROP_M = 100.0

#: Perda numa curva acima da qual o coach CALA A BOCA.
#: Dois segundos numa curva não é ponto de freada errado: é rodada, escapada,
#: tráfego ou volta de retorno. O piloto já sabe o que houve, e ouvir "você
#: perdeu dois segundos ali" depois de rodar é a definição de ruído.
MAX_MEANINGFUL_LOSS_S = 1.0

#: Limiares de causa — os mesmos do balanço de fim de volta, para o coach ao
#: vivo e o relatório não se contradizerem.
BRAKE_DIFF_M = 8.0
VMIN_DIFF_KMH = 2.0
THROTTLE_DIFF_M = 10.0


# ---------------------------------------------------------------------------
# Modos de sessão
# ---------------------------------------------------------------------------

MODE_PRACTICE = "practice"
MODE_QUALIFY = "qualify"
MODE_RACE = "race"


@dataclasses.dataclass
class ModeProfile:
    """Quanto o coach fala em cada tipo de sessão."""
    max_cues_per_lap: int
    max_feedback_per_lap: int
    #: Perda média mínima para valer uma dica (s)
    min_loss_s: float
    #: Elogiar quando a curva sai boa?
    praise: bool
    #: Só falar na volta lançada (classificação)
    flying_lap_only: bool = False


MODE_PROFILES: Dict[str, ModeProfile] = {
    # Treino: é para isso que ele existe. Conversa.
    MODE_PRACTICE: ModeProfile(max_cues_per_lap=2, max_feedback_per_lap=2,
                               min_loss_s=MIN_COACH_LOSS_S, praise=True),
    # Classificação: a volta é uma só. Dica antes da curva sim, veredito não —
    # o piloto não pode gastar atenção com o que já passou.
    MODE_QUALIFY: ModeProfile(max_cues_per_lap=2, max_feedback_per_lap=0,
                              min_loss_s=0.10, praise=False,
                              flying_lap_only=True),
    # Corrida: quem está brigando por posição não quer aula. Só a curva onde
    # a perda é grande o bastante para custar a posição.
    MODE_RACE: ModeProfile(max_cues_per_lap=1, max_feedback_per_lap=0,
                           min_loss_s=0.20, praise=False),
}


def mode_for_session(state) -> str:
    """Modo do coach a partir do tipo de sessão que o jogo informa."""
    if (getattr(state, "total_laps", 0) or 0) > 0:
        return MODE_RACE
    tipo = (getattr(state, "session_type", "") or "").strip().lower()
    if "race" in tipo or "corrida" in tipo:
        return MODE_RACE
    if "qual" in tipo or "hotlap" in tipo:
        return MODE_QUALIFY
    return MODE_PRACTICE


# ---------------------------------------------------------------------------
# O que o piloto vem fazendo em cada curva
# ---------------------------------------------------------------------------

#: Causas possíveis, em ordem de prioridade. O ponto de freada vem primeiro
#: porque ele é a raiz das outras duas: quem freia cedo chega devagar no ápice
#: e demora a abrir o gás. Corrigir a freada costuma resolver o resto sozinho.
CAUSE_BRAKE_EARLY = "brake_early"
CAUSE_BRAKE_LATE = "brake_late"
CAUSE_VMIN = "vmin"
CAUSE_THROTTLE = "throttle"
CAUSE_NONE = ""


@dataclasses.dataclass
class CornerProfile:
    """
    Histórico do piloto numa curva, acumulado volta após volta.

    O alvo desta curva é `target_metrics`: a MELHOR versão dela que se
    conhece — a melhor passagem do próprio piloto ou, se a volta de
    referência for mais rápida ali, a dela.

    Isto é o que faz o coach funcionar já na terceira volta de um treino, sem
    referência externa nenhuma. Comparar o piloto só com a própria melhor
    VOLTA ensina consistência, não velocidade: se ele freia cedo na Ferradura
    em todas as voltas, a melhor volta dele também freia cedo, e a comparação
    não acusa nada. Curva por curva, o melhor de cada uma somado é um alvo
    real — é assim que a telemetria de verdade acha tempo escondido.
    """
    index: int
    name: str = ""
    samples: int = 0
    #: Média móvel do tempo perdido (+) ou ganho (-) nesta curva, em segundos
    avg_loss_s: float = 0.0
    last_loss_s: float = 0.0
    #: Voltas seguidas perdendo tempo aqui
    streak: int = 0
    cause: str = CAUSE_NONE
    cause_value: float = 0.0
    #: Melhor tempo que o PRÓPRIO piloto já fez neste trecho (s), e as
    #: medidas daquela passagem. É o "você já fez melhor aqui".
    best_section_s: Optional[float] = None
    best_metrics: Optional["ca.CornerMetrics"] = None
    #: O ALVO efetivo: o mais rápido entre a melhor passagem do piloto e a da
    #: volta de referência. É contra ele que a perda é medida, e é o ponto de
    #: freada DELE que a dica de aproximação usa — mirar no ponto de freada da
    #: própria volta lenta faria a dica sair no lugar errado.
    target_section_s: Optional[float] = None
    target_metrics: Optional["ca.CornerMetrics"] = None
    #: O alvo veio do histórico (sessões anteriores), não desta sessão.
    #: Muda quantas voltas o coach precisa antes de opinar: com um alvo já
    #: estabelecido, UMA volta medida hoje basta para saber a distância até
    #: ele. Sem alvo, são duas — uma para virar alvo e outra para comparar.
    seeded: bool = False

    @property
    def is_problem(self) -> bool:
        """
        Já dá para dizer que esta curva é um problema, e não azar?

        Com alvo vindo do histórico, uma volta medida hoje já responde: o alvo
        não é chute, é uma passagem que o piloto realmente fez. Sem histórico,
        são necessárias duas — a primeira só serve para estabelecer o alvo.
        """
        minimo = 1 if self.seeded else MIN_SAMPLES
        return self.samples >= minimo and self.avg_loss_s >= MIN_COACH_LOSS_S

    def observe(self, delta_time: float, cause: str, cause_value: float) -> None:
        self.samples += 1
        if self.samples == 1:
            self.avg_loss_s = delta_time
        else:
            self.avg_loss_s = ((1.0 - PROFILE_ALPHA) * self.avg_loss_s
                               + PROFILE_ALPHA * delta_time)
        self.last_loss_s = delta_time
        self.streak = self.streak + 1 if delta_time > MIN_COACH_LOSS_S else 0
        if cause:
            self.cause = cause
            self.cause_value = cause_value


def dominant_cause(cmp_: "ca.CornerComparison"):
    """
    A causa que explica a perda naquela curva, e o número dela.

    Uma curva mal feita costuma acusar as três coisas ao mesmo tempo — freou
    cedo, chegou devagar no ápice, demorou a abrir o gás. Dizer as três é
    inútil no meio de uma reta: o piloto precisa de UMA coisa para mudar.
    """
    d_brake = cmp_.delta_braking_m
    if d_brake is not None:
        if d_brake <= -BRAKE_DIFF_M:
            return CAUSE_BRAKE_EARLY, abs(d_brake)
        if d_brake >= BRAKE_DIFF_M:
            return CAUSE_BRAKE_LATE, d_brake

    d_vmin = cmp_.delta_v_min
    if d_vmin is not None and d_vmin <= -VMIN_DIFF_KMH:
        return CAUSE_VMIN, abs(d_vmin)

    d_thr = cmp_.delta_throttle_m
    if d_thr is not None and d_thr >= THROTTLE_DIFF_M:
        return CAUSE_THROTTLE, d_thr

    return CAUSE_NONE, 0.0


def _decimos(seconds: float) -> str:
    """
    Tempo curto em palavras que se ouvem bem no volante.

    "2 décimos" entra melhor que "0,21 segundos" — e a precisão que o piloto
    perde aqui não muda nada no que ele vai fazer na curva seguinte.
    """
    s = abs(seconds)
    if s >= 1.0:
        return f"{s:.1f} segundos".replace(".", ",")
    d = int(round(s * 10))
    if d <= 0:
        return "quase nada"
    return "1 décimo" if d == 1 else f"{d} décimos"


# ---------------------------------------------------------------------------
# O coach
# ---------------------------------------------------------------------------

class LiveCoach:
    """
    Acompanha o carro na volta e fala nas duas janelas em que dá para ouvir.

    O relógio vem de fora (`now`, em segundos), como no RaceEngineer: os
    testes controlam o tempo e nada aqui depende do relógio real.
    """

    def __init__(self):
        self.corners: List[ca.Corner] = []
        self.track_length: float = 0.0
        self.mode: str = MODE_PRACTICE
        #: index da curva -> CornerMetrics da volta de referência
        self.ref_metrics: Dict[int, ca.CornerMetrics] = {}
        #: index da curva -> CornerProfile
        self.profiles: Dict[int, CornerProfile] = {}

        self._last_distance = -1.0
        self._cued: set = set()
        self._fed_back: set = set()
        self._last_spoke_at = -999.0
        self._lap_cue_count = 0
        self._lap_feedback_count = 0
        self._ref_signature = None
        self._lap_touched_pits = False
        self._bests_dirty = False

    # -- configuração --------------------------------------------------------

    def reset(self) -> None:
        """Zera tudo, inclusive o que foi aprendido. Trocou de pista ou carro."""
        self.ref_metrics.clear()
        self.profiles.clear()
        self._ref_signature = None
        self._bests_dirty = False
        self.reset_lap()
        self._last_spoke_at = -999.0

    def reset_lap(self) -> None:
        """Zera só o estado da volta: o aprendizado das curvas continua."""
        self._cued.clear()
        self._fed_back.clear()
        self._lap_cue_count = 0
        self._lap_feedback_count = 0
        self._lap_touched_pits = False

    def set_track(self, corners: List["ca.Corner"], track_length: float) -> None:
        """Curvas da pista atual. Mapa novo derruba o que foi aprendido."""
        novo = [c.index for c in (corners or [])]
        if novo != [c.index for c in self.corners] or track_length != self.track_length:
            self.profiles.clear()
            self.ref_metrics.clear()
            self._ref_signature = None
        self.corners = list(corners or [])
        self.track_length = float(track_length or 0.0)
        for c in self.corners:
            self.profiles.setdefault(
                c.index, CornerProfile(index=c.index, name=c.name or f"Curva {c.index}"))

    def set_reference(self, ref_telemetry: dict) -> None:
        """
        Mede a volta de referência curva a curva, uma vez só.

        É contra estes números que a volta em andamento é comparada. Refazer a
        conta a cada quadro seria varrer milhares de pontos 60 vezes por
        segundo para chegar sempre no mesmo resultado.
        """
        times = (ref_telemetry or {}).get("times") or []
        assinatura = (len(times), times[-1] if times else 0.0)
        if assinatura == self._ref_signature:
            return
        self._ref_signature = assinatura
        self.ref_metrics.clear()
        if not self.corners or not times:
            return
        for m in ca.analyze_lap(ref_telemetry, self.corners, self.track_length):
            self.ref_metrics[m.corner.index] = m

    def load_bests(self, bests) -> int:
        """
        Semeia o alvo de cada curva com o que foi aprendido em sessões
        anteriores (ver core/corner_bests.py). Devolve quantas curvas foram
        semeadas.

        É isto que faz o coach chegar na primeira volta do Treino 2 já sabendo
        onde o piloto perde — em vez de gastar duas voltas reconstruindo o que
        aprendeu na véspera.
        """
        n = 0
        for index, best in (bests or {}).items():
            corner = self._corner_by_index(index)
            if corner is None or not best or best.section_s <= 0:
                continue
            profile = self.profiles.setdefault(
                index, CornerProfile(index=index,
                                     name=corner.name or f"Curva {index}"))
            profile.name = corner.name or profile.name
            profile.best_section_s = best.section_s
            profile.best_metrics = best.to_metrics(corner)
            profile.target_section_s = best.section_s
            profile.target_metrics = profile.best_metrics
            profile.seeded = True
            n += 1
        self._bests_dirty = False
        return n

    def export_bests(self):
        """
        A melhor passagem que o coach conhece de cada curva, no formato do
        arquivo. Só as que ele mediu ou recebeu — nada inventado.
        """
        from core.corner_bests import CornerBest
        out = {}
        for index, profile in self.profiles.items():
            if profile.best_section_s is None or profile.best_metrics is None:
                continue
            best = CornerBest.from_metrics(profile.best_metrics)
            if best is None:
                continue
            # O índice e o nome vêm da curva do mapa em uso, não das medidas:
            # uma passagem semeada de outra sessão carrega a curva de então.
            corner = self._corner_by_index(index)
            best.index = index
            if corner is not None and corner.name:
                best.name = corner.name
            out[index] = best
        return out

    @property
    def bests_dirty(self) -> bool:
        """Alguma curva melhorou desde a última gravação?"""
        return self._bests_dirty

    def mark_bests_saved(self) -> None:
        self._bests_dirty = False

    def has_reference(self) -> bool:
        """
        O coach tem alvo para comparar?

        Basta ter medido cada curva uma vez: o melhor de cada trecho já é um
        alvo. Não precisa de volta de referência externa.
        """
        return bool(self.ref_metrics) or any(
            p.target_section_s is not None for p in self.profiles.values())

    # -- aprendizado ---------------------------------------------------------

    def on_lap_completed(self, comparisons: List["ca.CornerComparison"],
                         pit_lap: bool = False, valid: bool = True) -> None:
        """
        Atualiza o perfil de cada curva com o que a volta que fechou mostrou.

        Alimentado pela MESMA comparação que gera a tabela curva a curva, para
        o coach ao vivo e o painel nunca discordarem sobre onde o tempo foi.

        Volta de box e volta com corte de pista são IGNORADAS. Uma volta de
        retorno aos boxes é vinte segundos mais lenta: aprendendo com ela, o
        coach concluía que o piloto perdia dois segundos em cada curva da
        pista, e passava a repetir isso em todas as retas da volta seguinte.
        """
        if pit_lap or not valid:
            return
        for cmp_ in (comparisons or []):
            idx = cmp_.corner.index
            profile = self.profiles.setdefault(
                idx, CornerProfile(index=idx,
                                   name=cmp_.corner.name or f"Curva {idx}"))

            agora = cmp_.lap.section_time
            if agora is None:
                continue

            # A melhor passagem do próprio piloto nesta curva
            if profile.best_section_s is None or agora < profile.best_section_s:
                profile.best_section_s = agora
                profile.best_metrics = cmp_.lap
                self._bests_dirty = True

            # O alvo é o mais rápido entre ela e a volta de referência
            alvo_s, alvo_metrics = profile.best_section_s, profile.best_metrics
            ref_s = cmp_.ref.section_time if cmp_.ref else None
            if ref_s is not None and (alvo_s is None or ref_s < alvo_s):
                alvo_s, alvo_metrics = ref_s, cmp_.ref

            if alvo_s is None or alvo_metrics is None:
                continue
            profile.target_section_s = alvo_s
            profile.target_metrics = alvo_metrics
            contra_alvo = ca.CornerComparison(corner=cmp_.corner, lap=cmp_.lap,
                                              ref=alvo_metrics)
            cause, value = dominant_cause(contra_alvo)
            profile.observe(agora - alvo_s, cause, value)

    def time_on_the_table(self) -> float:
        """
        Quanto tempo, somando as curvas, o piloto ainda deixa na mesa.

        É a diferença entre a volta que ele faz e a volta que ele JÁ MOSTROU
        saber fazer, curva por curva — a "volta ideal" clássica da telemetria,
        só que por trecho de curva em vez de por setor, que é bem mais fino.
        """
        return sum(max(0.0, p.avg_loss_s) for p in self.profiles.values()
                   if p.samples >= MIN_SAMPLES)

    def lap_summary(self) -> Optional[Advice]:
        """
        O resumo de fim de volta que responde "onde está o meu tempo?".

        Uma frase, com o total e as duas curvas que mais pesam. É o que faz o
        piloto saber onde gastar a atenção da próxima volta — e o que mostra
        que o número não veio de lugar nenhum: cada décimo aí é a diferença
        entre o que ele fez e o que ele já fez de melhor naquela curva.
        """
        total = self.time_on_the_table()
        if total < MIN_COACH_LOSS_S * 2:
            return None
        piores = self.problem_corners(limit=2)
        if not piores:
            return None
        detalhes = ", ".join(
            f"{_decimos(p.avg_loss_s)} {'na' if _feminino(p.name) else 'no'} "
            f"{p.name}" for p in piores)
        return Advice(
            key="coach_summary", severity=INFO,
            text=f"Tem {_decimos(total)} na mesa: {detalhes}",
            detail=" | ".join(f"{p.name} {p.avg_loss_s:+.3f}s "
                              f"(melhor {p.target_section_s:.3f}s)"
                              for p in piores if p.target_section_s is not None),
            kind="lap", time_at_stake=total)

    def problem_corners(self, limit: int = None) -> List[CornerProfile]:
        """Curvas onde o piloto mais perde, da pior para a menos pior."""
        piores = [p for p in self.profiles.values() if p.is_problem]
        piores.sort(key=lambda p: -p.avg_loss_s)
        return piores[:limit] if limit else piores

    # -- o laço ao vivo ------------------------------------------------------

    def update(self, state, lap_telemetry: dict, now: float) -> List[Advice]:
        """
        Um quadro de telemetria. Devolve o que houver para falar AGORA.

        Chamada a alguns Hz pela interface — não precisa dos 60, porque as
        janelas de fala têm mais de 100 m de largura.
        """
        if not self.corners or self.track_length <= 0:
            return []

        self.mode = mode_for_session(state)
        profile = MODE_PROFILES.get(self.mode, MODE_PROFILES[MODE_PRACTICE])

        distance = float(getattr(state, "distance_traveled", 0.0) or 0.0)
        if distance < self._last_distance - LAP_RESET_DROP_M:
            self.reset_lap()
        self._last_distance = distance

        # Volta que passou pelo box é volta de saída ou de retorno: ela é
        # inteira 20 segundos mais lenta, e comentar curva a curva nela seria
        # anunciar "perdeu dois segundos" oito vezes seguidas.
        if getattr(state, "in_pit", False) or getattr(state, "in_pit_lane", False):
            self._lap_touched_pits = True

        if not self._can_speak(state, now, profile):
            return []

        out: List[Advice] = []
        # A saída vem primeiro: se as duas janelas caírem no mesmo quadro, o
        # que acabou de acontecer é mais urgente que o que ainda vai acontecer.
        adv = self._exit_verdict(distance, lap_telemetry, profile)
        if adv is None:
            adv = self._approach_cue(state, distance, profile)
        if adv is not None:
            self._last_spoke_at = now
            out.append(adv)
        return out

    def _can_speak(self, state, now: float, profile: ModeProfile) -> bool:
        """
        O piloto está numa janela em que ouvir ajuda?

        Fora da pista (box, pit lane, replay, pausa) o coach não existe. E,
        principalmente: nada de voz com o carro carregado — entre a freada e a
        saída da curva, uma frase no ouvido é atrapalho, não coaching.
        """
        if getattr(state, "is_replay", False) or getattr(state, "is_paused", False):
            return False
        if getattr(state, "in_pit", False) or getattr(state, "in_pit_lane", False):
            return False
        if (now - self._last_spoke_at) < MIN_GAP_S:
            return False
        if profile.flying_lap_only and not self._is_flying_lap(state):
            return False
        # Carro sob carga: freando ou virando de verdade
        if float(getattr(state, "brake", 0.0) or 0.0) > 0.15:
            return False
        if abs(float(getattr(state, "g_lat", 0.0) or 0.0)) > 1.2:
            return False
        return True

    @staticmethod
    def _is_flying_lap(state) -> bool:
        """
        Volta lançada: dá para cronometrar de verdade.

        Na classificação, a volta de saída e a de retorno não têm o que
        comparar — falar de ponto de freada nelas é falar por falar.
        """
        if getattr(state, "in_pit_lane", False) or getattr(state, "in_pit", False):
            return False
        return (getattr(state, "delta_time", 0.0) or 0.0) != 0.0

    # -- veredito na saída da curva -----------------------------------------

    def _exit_verdict(self, distance: float, lap_telemetry: dict,
                      profile: ModeProfile) -> Optional[Advice]:
        if self._lap_feedback_count >= profile.max_feedback_per_lap:
            return None
        if self._lap_touched_pits:
            return None
        if not self.ref_metrics or not lap_telemetry:
            return None

        for i, corner in enumerate(self.corners):
            if corner.index in self._fed_back:
                continue
            gatilho = corner.end_m(self.track_length) + EXIT_FEEDBACK_M
            if not (gatilho <= distance <= gatilho + TRIGGER_WINDOW_M):
                continue

            perfil = self.profiles.get(corner.index)
            # O alvo já foi resolvido em on_lap_completed (o mais rápido entre
            # a melhor passagem do piloto e a da referência). Sem alvo ainda,
            # a volta de referência serve.
            alvo = (perfil.target_metrics
                    if perfil is not None and perfil.target_metrics is not None
                    else self.ref_metrics.get(corner.index))
            if alvo is None or alvo.section_time is None:
                self._fed_back.add(corner.index)
                continue
            ref = alvo

            de, ate = ca.search_bounds(self.corners, i, self.track_length)
            agora = ca.analyze_corner(lap_telemetry, corner, self.track_length,
                                      search_from_m=de, search_to_m=ate)
            self._fed_back.add(corner.index)
            if agora.section_time is None:
                continue

            delta = agora.section_time - ref.section_time
            nome = corner.name or f"Curva {corner.index}"

            if delta > MAX_MEANINGFUL_LOSS_S:
                # Rodada, escapada ou tráfego: o piloto já sabe. Não é técnica.
                continue

            if delta >= EXIT_LOSS_S:
                cmp_ = ca.CornerComparison(corner=corner, lap=agora, ref=ref)
                causa, valor = dominant_cause(cmp_)
                sufixo = _CAUSE_EXIT.get(causa, "")
                texto = f"Perdeu {_decimos(delta)} na {nome}"
                if sufixo:
                    texto += f", {sufixo}"
                self._lap_feedback_count += 1
                return Advice(
                    key=f"coach_exit:{corner.index}",
                    severity=ATTENTION if delta >= 0.20 else INFO,
                    text=texto, detail=f"{delta:+.3f}s na curva",
                    corner=corner.index, kind="live", time_at_stake=delta,
                    ttl_s=EXIT_TTL_S)

            if profile.praise and delta <= -EXIT_GAIN_S:
                # Elogio vale quando é MELHORA: repetir "boa" numa curva que
                # sempre foi boa vira ruído em duas voltas.
                if perfil is None or perfil.avg_loss_s <= 0.0:
                    continue
                self._lap_feedback_count += 1
                return Advice(
                    key=f"coach_ok:{corner.index}", severity=INFO,
                    text=f"Isso! {_decimos(delta)} a mais na {nome}",
                    detail=f"{delta:+.3f}s na curva",
                    corner=corner.index, kind="live", time_at_stake=abs(delta),
                    ttl_s=EXIT_TTL_S)
        return None

    # -- dica na aproximação -------------------------------------------------

    def _approach_cue(self, state, distance: float,
                      profile: ModeProfile) -> Optional[Advice]:
        if self._lap_cue_count >= profile.max_cues_per_lap:
            return None

        alvos = [p for p in self.problem_corners()
                 if p.avg_loss_s >= profile.min_loss_s]
        if not alvos:
            return None
        alvos = alvos[:profile.max_cues_per_lap]

        velocidade_ms = max(20.0, float(getattr(state, "speed_kmh", 0.0) or 0.0) / 3.6)
        # Antecedência mínima em metros: 45 m a 60 km/h é sobra, mas a 290 km/h
        # são meio segundo. O piso é sempre o maior dos dois.
        minima = max(APPROACH_MIN_M, velocidade_ms * APPROACH_LEAD_S)

        for perfil in alvos:
            if perfil.index in self._cued:
                continue
            corner = self._corner_by_index(perfil.index)
            if corner is None:
                continue

            # O ponto de referência é a freada da MELHOR passagem conhecida
            # nesta curva; sem ela, a da volta de referência; sem nenhuma das
            # duas, a entrada da curva serve — a dica só precisa chegar antes
            # de o piloto agir.
            ponto = None
            for fonte in (perfil.target_metrics, self.ref_metrics.get(perfil.index)):
                if fonte is not None and fonte.braking_point_m is not None:
                    ponto = fonte.braking_point_m
                    break
            if ponto is None:
                ponto = corner.start_m(self.track_length)
            inicio = ponto - APPROACH_MAX_M
            fim = ponto - minima
            if fim <= inicio or not (inicio <= distance <= fim):
                continue
            # Dentro da janela, espera o momento em que faltam ~3 s de pista
            # para a freada. Se o carro estiver ocupado (freando, virando) em
            # todo esse trecho, a dica simplesmente não sai nesta volta —
            # melhor calar do que falar em cima da freada.
            if (ponto - distance) / velocidade_ms > APPROACH_PREFERRED_LEAD_S:
                continue

            self._cued.add(perfil.index)
            self._lap_cue_count += 1
            nome = corner.name or f"Curva {corner.index}"
            texto = _cue_text(nome, perfil)
            origem = "histórico" if perfil.seeded and perfil.samples == 0                 else f"{perfil.samples} volta(s)"
            return Advice(
                key=f"coach_cue:{corner.index}", severity=INFO, text=texto,
                detail=f"média {perfil.avg_loss_s:+.3f}s em {origem}",
                corner=corner.index, kind="live",
                time_at_stake=perfil.avg_loss_s, ttl_s=CUE_TTL_S)
        return None

    def _corner_by_index(self, index: int) -> Optional["ca.Corner"]:
        for c in self.corners:
            if c.index == index:
                return c
        return None


#: Complemento do veredito de saída, por causa. Curto: o carro está acelerando.
_CAUSE_EXIT = {
    CAUSE_BRAKE_EARLY: "freou cedo",
    CAUSE_BRAKE_LATE: "freou tarde demais",
    CAUSE_VMIN: "entrou devagar",
    CAUSE_THROTTLE: "demorou pra abrir o gás",
}


def _feminino(nome: str) -> bool:
    """
    "na Ferradura" x "no Pinheirinho".

    Heurística boba de propósito: nome de curva termina em 'a' na maioria dos
    casos femininos ("Ferradura", "Laranjinha", "Curva 2"), e errar o artigo
    numa frase falada custa menos que uma tabela de gêneros por pista.
    """
    nome = (nome or "").strip()
    return bool(nome) and nome[-1].lower() == "a"


def _cue_text(nome: str, perfil: CornerProfile) -> str:
    """
    A frase da aproximação. Uma ordem só, com o número junto quando ele ajuda.

    Sem número o piloto não sabe quanto mexer; com número demais ele para de
    ouvir. Metros na freada (dá para enxergar na pista) e "mais velocidade" no
    ápice (onde metro nenhum ajudaria).
    """
    metros = int(round(perfil.cause_value))
    if perfil.cause == CAUSE_BRAKE_EARLY:
        return f"{nome} chegando: atrasa a freada uns {metros} metros"
    if perfil.cause == CAUSE_BRAKE_LATE:
        return f"{nome} chegando: antecipa a freada uns {metros} metros"
    if perfil.cause == CAUSE_VMIN:
        return f"{nome} chegando: mais velocidade no ápice"
    if perfil.cause == CAUSE_THROTTLE:
        return f"{nome} chegando: abre o gás mais cedo na saída"
    return f"{nome} chegando: foco aqui, {_decimos(perfil.avg_loss_s)} em jogo"
