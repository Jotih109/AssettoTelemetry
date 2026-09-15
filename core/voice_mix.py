"""
core/voice_mix.py — Mesa de som do engenheiro
==============================================

O engenheiro fala de muita coisa: dica antes da curva, veredito depois,
balanço da volta, ABS, tração, bandeira, combustível, pneu, ritmo. Nem tudo
interessa ao mesmo piloto na mesma sessão — quem está aprendendo a pista quer
o coach de curva no ouvido e não quer saber de consumo; quem está em corrida
quer bandeira e combustível e não quer aula de ponto de freada.

Este módulo agrupa os recados em CANAIS e guarda o volume de cada um. É o
mesmo princípio de uma mesa de som: cada fonte tem o seu fader, e quem mistura
é você.

O que o mixer NÃO faz
---------------------
Ele mexe só na VOZ. O painel de texto continua mostrando tudo, sempre — um
canal no zero silencia a fala daquele assunto, não apaga a informação. É
deliberado: silenciar um aviso é escolher não ser interrompido, não escolher
ficar sem o dado.

Nada aqui depende de PyQt nem do sintetizador: é só classificação e números.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional

#: Volume de um canal vai de 0 (mudo) a 100 (cheio).
VOLUME_MIN = 0
VOLUME_MAX = 100


@dataclasses.dataclass(frozen=True)
class Channel:
    """Um canal da mesa: um assunto do engenheiro."""
    id: str
    label: str
    #: Uma linha explicando o que cai neste canal, para a interface.
    hint: str
    #: Prefixos de `Advice.key` que pertencem a este canal.
    prefixes: tuple
    #: Frase de exemplo, para o botão "ouvir" da mesa.
    sample: str


#: Os canais, na ordem em que aparecem na mesa — do que fala DURANTE a curva
#: ao que fala sobre a sessão inteira.
#:
#: A ordem importa: quem abre a mesa procura primeiro o assunto que o está
#: incomodando, e o que incomoda é sempre o que fala mais perto da pilotagem.
CHANNELS: List[Channel] = [
    Channel(
        id="coach_cue",
        label="Dica antes da curva",
        hint="Na reta, antes da freada: o que corrigir na curva que vem",
        prefixes=("coach_cue:",),
        sample="Curva 7 chegando, atrasa a freada uns 12 metros",
    ),
    Channel(
        id="coach_exit",
        label="Veredito na saída",
        hint="Logo depois da curva: quanto você perdeu ou ganhou nela",
        prefixes=("coach_exit:", "coach_ok:"),
        sample="Perdeu 2 décimos na Curva 5, freou cedo",
    ),
    Channel(
        id="corner",
        label="Curvas no fim da volta",
        hint="O balanço curva a curva quando a volta fecha",
        prefixes=("corner:", "corner_ok:", "coach_summary"),
        sample="Curva 5: perdeu 0,30 segundos, freou 20 metros antes",
    ),
    Channel(
        id="driving",
        label="Pedal, volante e marcha",
        hint="Vícios da volta: repisada, freio largado, subesterço, troca cedo",
        prefixes=("lap:overlap", "lap:brake_", "lap:understeer", "lap:steer_",
                  "lap:shift_", "lap:limiter", "limiter"),
        sample="Tá largando o freio de uma vez na freada da Curva 4",
    ),
    Channel(
        id="electronics",
        label="ABS e tração",
        hint="Roda travando na freada, tração cortando na saída",
        prefixes=("lap:abs", "lap:tc", "abs", "tc"),
        sample="Tá travando a roda na freada da Curva 9. Chega mais suave",
    ),
    Channel(
        id="pace",
        label="Tempo, setor e delta",
        hint="Delta contra a referência, setor que fechou, melhor volta e teto teórico",
        prefixes=("lap:melhor", "lap:pior", "lap:sector", "lap:consistencia",
                  "delta", "sector:", "best_lap", "last_lap:", "theoretical_ceiling"),
        sample="Perdemos 0,35 segundos pra referência nessa volta",
    ),
    Channel(
        id="car",
        label="Carro e consumo",
        hint="Combustível, pneu, freio quente, dano",
        prefixes=("lap:fuel", "fuel", "tyre_", "brake_hot", "damage"),
        sample="Combustível não fecha a corrida, vai precisar economizar",
    ),
    Channel(
        id="track",
        label="Pista e clima",
        hint="Asfalto esquentando, pista verde, vento",
        prefixes=("track_temp", "grip", "wind"),
        sample="O asfalto esfriou 4 graus, o grip mudou",
    ),
    Channel(
        id="flags",
        label="Bandeira e penalidade",
        hint="Bandeira, penalidade, corte de pista. Baixe por sua conta.",
        prefixes=("flag", "penalty", "cut"),
        sample="Bandeira amarela, reduza o ritmo",
    ),
]

#: Canal usado quando a chave não casa com nenhum prefixo. Existe para um
#: recado novo NUNCA nascer mudo: quem adiciona uma regra no engenheiro e
#: esquece de classificá-la vai ouvi-la, e não passar semanas sem entender por
#: que ela nunca falou.
DEFAULT_CHANNEL_ID = "pace"

_BY_ID: Dict[str, Channel] = {c.id: c for c in CHANNELS}


def channel_for(advice_key: str) -> Channel:
    """
    O canal de um recado, pela sua chave.

    Casa pelo prefixo MAIS LONGO, não pelo primeiro que servir: "lap:fuel_high"
    tem de cair em "Carro e consumo" (prefixo `lap:fuel`) e não em "Tempo e
    setor" só porque este também lista prefixos que começam com `lap:`.
    """
    chave = (advice_key or "").strip()
    melhor: Optional[Channel] = None
    melhor_tam = -1
    for canal in CHANNELS:
        for prefixo in canal.prefixes:
            if chave.startswith(prefixo) and len(prefixo) > melhor_tam:
                melhor, melhor_tam = canal, len(prefixo)
    return melhor or _BY_ID[DEFAULT_CHANNEL_ID]


@dataclasses.dataclass
class VoiceMix:
    """
    O volume de cada canal, de 0 a 100.

    Canal ausente vale 100: uma mesa gravada por uma versão anterior do app,
    sem o canal novo, não pode silenciá-lo por omissão.
    """

    levels: Dict[str, int] = dataclasses.field(default_factory=dict)

    # -- consulta ----------------------------------------------------------

    def level(self, channel_id: str) -> int:
        bruto = self.levels.get(channel_id, VOLUME_MAX)
        try:
            return max(VOLUME_MIN, min(VOLUME_MAX, int(bruto)))
        except (TypeError, ValueError):
            return VOLUME_MAX

    def set_level(self, channel_id: str, value: int) -> None:
        self.levels[channel_id] = max(VOLUME_MIN, min(VOLUME_MAX, int(value)))

    def is_muted(self, channel_id: str) -> bool:
        return self.level(channel_id) <= VOLUME_MIN

    # -- o que a voz precisa saber ----------------------------------------

    def volume_for(self, advice_key: str, master: int = 100) -> Optional[int]:
        """
        Volume final de um recado, ou None se ele não deve ser falado.

        O canal MULTIPLICA o volume geral em vez de substituí-lo: baixar o
        volume da sessão inteira tem de baixar tudo, e um canal a 50% continua
        sendo "metade do que estiver valendo".
        """
        canal = channel_for(advice_key)
        nivel = self.level(canal.id)
        if nivel <= VOLUME_MIN:
            return None
        mestre = max(VOLUME_MIN, min(VOLUME_MAX, int(master)))
        return max(1, round(mestre * nivel / 100.0))

    # -- disco -------------------------------------------------------------

    def to_dict(self) -> dict:
        """Só o que difere do cheio — mesa no padrão não polui o config."""
        return {cid: v for cid, v in sorted(self.levels.items())
                if cid in _BY_ID and v != VOLUME_MAX}

    @classmethod
    def from_dict(cls, data) -> "VoiceMix":
        mix = cls()
        if not isinstance(data, dict):
            return mix
        for cid, valor in data.items():
            if cid in _BY_ID:
                mix.set_level(cid, valor)
        return mix
