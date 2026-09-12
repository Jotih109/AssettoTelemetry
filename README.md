# 🏎️ ApexView — Dashboard de Telemetria para Assetto Corsa (MoTeC i2 Style)

> ⚠️ **PROJETO EM DESENVOLVIMENTO ATIVO** — Dashboard profissional de telemetria em tempo real para Assetto Corsa 1, inspirado nos layouts de engenharia de dados do **MoTeC i2 Pro**. Acompanha uma tela separada de análise pós-sessão ([mapa.pyw](mapa.pyw)).

---

## 📖 Sobre o Projeto

**ApexView** (AssettoCorsa-Telemetry) é uma ferramenta de telemetria e análise de desempenho em tempo real desenvolvida em **Python**, **PyQt5** e **PyQtGraph** para o **Assetto Corsa 1**. A aplicação acessa diretamente a **memória compartilhada** do jogo (`acpmf_physics`, `acpmf_graphics`, `acpmf_static`), processando telemetria a 60 Hz sem a necessidade de plugins externos ou configurações complexas de porta UDP.

> 💡 **Conexão Zero-Config:** Basta abrir o dashboard e entrar na pista. O sistema detecta automaticamente o jogo, conecta, sincroniza dados e reconecta se você reiniciar a sessão ou trocar de carro/pista.

---

## 🚀 Começando

```bash
pip install -r requirements.txt
python main.pyw
```

Pode abrir o dashboard antes ou depois do jogo. Ele fica em `AGUARDANDO O ASSETTO CORSA` e conecta sozinho quando você entra na pista — **não há nada a configurar no jogo**: sem porta UDP, sem plugin, sem editar arquivo do AC.

Sem o jogo instalado? `python main.pyw --mock` roda com o simulador interno.
Quer só analisar as voltas de ontem? `python mapa.pyw`.

**Pré-requisitos:** Windows (a memória compartilhada é lida via Win32/`ctypes`), Python 3.8+, Assetto Corsa 1 (launcher original ou Content Manager).

| Pacote | Para quê |
|---|---|
| `PyQt5 >= 5.15.10` | interface |
| `pyqtgraph >= 0.13.7` | gráficos |
| `pywin32 >= 306` | **opcional** — voz pelo SAPI do Windows. Sem ele o painel de texto do engenheiro funciona igual, só não fala. |
| `kokoro-onnx` | **opcional** — voz neural em vez do SAPI. Veja [Voz neural](#-engenheiro-de-pista-análise-por-regras--voz). |

---

## 🧭 Índice

| | |
|---|---|
| [Como funciona](#-como-funciona) | o caminho do dado, do jogo até a tela |
| [Pilha de gráficos](#-área-principal--pilha-de-gráficos-estilo-motec-i2) | os quatro canais estilo MoTeC i2 |
| [Mapa da pista](#-mapa-da-pista-em-2d-e-traçado-inteligente) | traçado 2D e posição do carro |
| [Eletrônica](#-painel-de-eletrônica-e-sistemas-formato-i--0) | ABS, TC, DRS, KERS, limitador |
| [Painel lateral](#-painel-lateral-sidebar--métricas-do-veículo) | marcha, RPM, pneus, combustível |
| [Referência (ghost)](#-sistema-de-referência-ghost--métricas-de-topo) | contra qual volta você está sendo medido |
| [**Coach de curva**](#-coach-de-curva--evoluir-dirigindo-sem-parar-para-olhar-dados) | evoluir dirigindo, sem parar para ver dados |
| [Fim de semana](#-fim-de-semana-de-corrida-múltiplas-sessões) | T1, T2, T3, classificação e corrida |
| [Engenheiro de pista](#-engenheiro-de-pista-análise-por-regras--voz) | o balanço da volta, com voz |
| [Análise curva a curva](#-análise-curva-a-curva-turn-by-turn) | ponto de freada, $V_{min}$, retomada |
| [Biblioteca de voltas](#-biblioteca-de-voltas-histórico-e-exportação) | como as voltas são guardadas |
| [Análise pós-sessão](#-tela-de-análise-pós-sessão-mapapyw) | comparar até 4 voltas, offline |
| [**Relatórios de desempenho**](#-relatórios-de-desempenho--diagnóstico-causal-onde-melhorar-e-por-quê) | pontos positivos, negativos, onde melhorar e por quê |
| [**Exportação MoTeC i2**](#-exportação-motec-i2-ld--ldx-para-motec-i2-pro) | telemetria nativa no MoTeC i2 Pro |
| [Onde ficam os dados](#-onde-ficam-os-dados) | o que cada arquivo em disco é |
| [Preferências](#-preferências-persistentes-configjson) | todas as chaves do `config.json` |
| [Como executar](#-como-executar) | modos, argumentos e variáveis |
| [Testes](#-testes-automatizados) | 554 verificações |
| [Solução de problemas](#-solução-de-problemas) | quando algo não aparece |
| [O que ainda falta](#-o-que-ainda-falta--planejado) | roadmap honesto |

---

## 🔧 Como Funciona

```text
Assetto Corsa
   │  memória compartilhada (acpmf_physics / acpmf_graphics / acpmf_static)
   ▼
providers/assettocorsa.py ──► core/models.py (TelemetryState)
   │                            estrutura única que o resto do app consome
   ▼
core/engine.py — QThread a 60 Hz, reconecta sozinho
   │  sinal Qt on_update(TelemetryState)
   ▼
ui/main_window.py ─┬─► core/session_manager.py ─► core/lap_library.py  (grava)
                   │      voltas, setores, ghosts,        índice + telemetria
                   │      consumo, troca de sessão        comprimida em disco
                   │
                   ├─► core/corner_analysis.py  curva a curva: freada, V.min, retomada
                   │
                   ├─► core/race_engineer.py ─┐  o balanço da volta e os avisos ao vivo
                   ├─► core/live_coach.py ────┼─► core/voice.py  (fila com prioridade)
                   │        dica antes da     │
                   │        curva, veredito   │
                   │        na saída          │
                   └─► core/corner_bests.py ──┘  o alvo de cada curva, entre sessões
```

Três decisões explicam quase todo o resto do desenho:

1. **Só o `TelemetryState` atravessa a fronteira.** O provider traduz a memória do jogo para um dataclass, e nada acima dele sabe que existe Assetto Corsa. É o que torna viável um provider de ACC ou de outro simulador sem mexer em análise nem interface.
2. **A telemetria não fica toda na RAM.** O catálogo mantém um índice leve e lê os pontos de uma volta só quando alguém pede aquela volta. Foi o que tirou os segundos de travamento ao entrar na pista com centenas de voltas gravadas.
3. **Quem mede não decide.** As medidas ficam em `corner_analysis.py` e `driving_analysis.py`; a decisão de virar conselho — com que palavras, com que urgência, em que momento — fica em `race_engineer.py` e `live_coach.py`. Toda medida devolve `None` quando o canal não existe, então volta antiga sem o canal de marcha faz o app se calar em vez de chutar.

---

## ✅ Funcionalidades Implementadas

### 📊 Área Principal — Pilha de Gráficos (Estilo MoTeC i2)
- **4 Canais de Telemetria Empilhados:**
  - **Delta Tempo (s):** Diferença em tempo real vs. a volta de referência escolhida.
  - **Velocidade (km/h):** Curva com escala Y dinâmica.
  - **Pedais (%):** Acelerador (Verde) e Freio (Vermelho) no mesmo gráfico.
  - **Volante (°):** Ângulo de esterçamento do volante em graus reais.
- **Intervenções de Eletrônica em Destaque nas Curvas dos Pedais:**
  - **ABS no Freio (Amarelo Vibrante `#FFEA00`):** A linha de freio fica amarela nos instantes em que o ABS atua durante a frenagem (`abs_intervention > 0.02`).
  - **Controle de Tração / TC no Acelerador (Azul Royal `#1E90FF`):** A linha de acelerador fica azul royal nos trechos onde o TC reduz a potência (`tc_intervention > 0.02`).
- **Alinhamento e Escala Sincronizada:**
  - **Eixo X único:** Exibido no gráfico inferior, compartilhando a mesma régua de tempo para toda a pilha.
  - **Coluna Y de largura fixa:** Eixos Y perfeitamente alinhados na vertical.
  - **Divisórias de Setores (S1 e S2):** Linhas verticais ajustadas dinamicamente de acordo com o ghost de referência.
  - **Cursor Temporal Sincronizado:** Marcador de posição atual em tempo real e no modo scrubber.

---

### 🗺️ Mapa da Pista em 2D e Traçado Inteligente
- **Contorno Cinza da Melhor Volta Válida:** O traçado permanente da pista no mapa é calculado e atualizado automaticamente com a **Melhor Volta Válida** (Session Best / Personal Best ou volta mais rápida válida da sessão).
- **Traçado ao Vivo Colorido:** Linha em tempo real que muda de cor conforme a aceleração (verde), frenagem (vermelho) ou coasting (amarelo).
- **Marcador de Posição do Carro:** Ponto indicador da posição instantânea do veículo no circuito.
- **Camada Translúcida do Ghost:** Sobreposição suave da trajetória da volta de referência.

---

### ⚡ Painel de Eletrônica e Sistemas (Formato I / 0)
- **Status Compacto de Equipamentos (I / 0 na extrema esquerda):**
  - ` I  ABS` / ` 0  ABS` — Indica se o carro é equipado com ABS.
  - ` I  TC` / ` 0  TC` — Indica se o carro é equipado com Controle de Tração.
  - ` I  DRS` / ` 0  DRS` — Indica se o carro tem asa móvel (DRS).
  - ` I  KERS` / ` 0  KERS` — Indica presença de ERS/KERS.
  - ` I  PIT` / ` 0  PIT` — Status do limitador de velocidade de box.
  - ` I  BOX` / ` 0  BOX` — Status de entrada na linha de pit lane / boxes.
- **Alertas de Atuação Dinâmica:**
  - O indicador `ABS 1` acende/pisca em **amarelo brilhante** exclusivamente nos momentos de intervenção ativa no freio.
  - O indicador `TC 1` acende quando ocorre corte pelo controle de tração.
- **Bargraphs de Intensidade:** Barras de atuação real em percentual para ABS e TC, além da medição de Force Feedback (FFB) com alerta de clipping.

---

### 📈 Painel Lateral (Sidebar) & Métricas do Veículo
- **Mostrador de Marcha:** Destaque para N (Neutro), R (Ré) e marcha atual com alerta de corte de RPM em vermelho.
- **Velocidade e Conta-giros:** Velocidade em km/h e barra de RPM com gradiente dinâmico.
- **Volante Visual:** Mostrador gráfico com rotação síncrona ao volante do jogo.
- **Status do Carro:** Nível de combustível (L), voltas estimadas de autonomia, consumo médio (L/volta), pressão do turbo e ângulo do volante.
- **Monitor Integrado de Pneus:**
  - Temperaturas e pressões (PSI) dos 4 pneus (FL, FR, RL, RR) com percentual de desgaste.
  - **Banda de Rodagem (Interna / Meio / Externa):** Alerta laranja quando o diferencial interno-externo ultrapassa 8 °C (indício de câmber desalinhado).

---

### 🏆 Sistema de Referência (Ghost) & Métricas de Topo
- **Seletor de Referência — qualquer volta gravada serve.** Além dos modos fixos, o combo lista as voltas desta sessão e as voltas gravadas em outros dias. Dá para comparar o carro de hoje com o de semana passada, ou perseguir aquela volta específica que saiu redonda.
  - **Automática:** a referência **mais rápida** que existir (melhor volta da sessão ou Personal Best, o que for menor). Num dia ruim isso importa: comparar com a melhor volta *de hoje* deixaria o delta verde enquanto você anda segundos abaixo do que já fez naquele carro. **Exceção na corrida:** ali vale a melhor volta *daquela* sessão — perseguir a volta de classificação com tanque cheio e pneu usado daria "+2 segundos" a corrida inteira, sem dizer nada sobre a volta que acabou.
  - **Personal Best:** a volta mais rápida do catálogo para esta pista e este carro.
  - **Melhor da sessão:** a melhor volta limpa e inteira desde que o app abriu.
  - **Volta Ideal Teórica (Theoretical Best):** combinação dos melhores setores individuais (S1, S2, S3).
  - **Nenhuma:** sem ghost e **sem delta**. Desativa de verdade — antes essa opção só escondia as curvas e continuava medindo o delta contra o session best, um número que o piloto não tinha pedido.
  - **Uma volta específica:** cada item mostra tempo, data e um aviso quando a volta é suja; o tooltip traz os setores, o número de pontos e se ela tem o canal de marcha.
- **A escolha é lembrada** entre sessões (`config.json`), e acompanha a VOLTA, não a posição na lista: uma volta nova empurra o resto para baixo sem trocar a sua referência.
- **Volta suja não vira referência.** Cortar a pista (três rodas fora, a mesma régua do AC) ou tomar penalidade marca a volta como inválida: ela é gravada e aparece no histórico com ⚠, mas nunca vira Personal Best nem ghost. Perseguir um tempo que só saiu cortando a grama não ajuda ninguém.
- **Métricas Superiores:**
  - **Volta Atual:** Cronômetro em tempo real.
  - **Melhor Volta:** Tempo da volta mais rápida válida.
  - **Delta Geral:** Diferença contínua em tempo real por interpolação de distância.
  - **Cards de Setores (S1, S2, S3):** Tempos atuais, tempos de referência e deltas individuais por setor.
  - **Ref / Est:** Tempo da referência e projeção estimada de conclusão de volta.

---

### 🏁 Coach de Curva — evoluir DIRIGINDO, sem parar para olhar dados

O engenheiro de pista dizia onde o tempo foi embora — mas só no fim da volta, quando o piloto já tinha errado a mesma curva de novo. O **coach de curva** ([core/live_coach.py](core/live_coach.py)) fecha esse laço: ele fala **antes** e **logo depois** de cada curva, enquanto ainda dá para fazer algo a respeito.

**Dois momentos, e só dois:**

| Quando | O que sai | Por quê ali |
|---|---|---|
| **~3 s antes da freada**, na reta | *"Ferradura chegando: atrasa a freada uns 10 metros"* | Chega com tempo de ser ouvida e executada |
| **~25 m depois da saída** da curva | *"Perdeu 2 décimos ali, entrou devagar"* | A sensação ainda está fresca no volante |
| **No fim da volta** | *"Tem 4 décimos na mesa: 3 na Ferradura, 1 no Pinheirinho"* | É o que o piloto leva para a volta seguinte |

**O gatilho é por TEMPO até a freada, não por distância.** Saindo de uma curva lenta, 250 metros de sobra são sete segundos — o piloto ouve "Ferradura chegando", passa por outra curva e chega na Ferradura sem lembrar do recado. Medindo em segundos, a dica cai sempre no mesmo lugar da cabeça dele, seja numa reta longa ou saindo de segunda marcha (medido: 2,9 a 3,4 s entre 100 e 280 km/h).

**Funciona já na segunda volta, sem referência externa nenhuma** — e na primeira, se você já andou nessa pista com esse carro antes. O alvo de cada curva é a **melhor passagem conhecida** naquele trecho: a melhor do próprio piloto ou, se a volta de referência for mais rápida ali, a dela. Comparar o piloto só com a própria melhor *volta* ensina consistência, não velocidade — se ele freia cedo na Ferradura em todas as voltas, a melhor volta dele também freia cedo e a comparação não acusa nada. Curva por curva, o melhor de cada uma somado é um alvo real: é assim que a telemetria de verdade acha tempo escondido.

**O aprendizado não se perde entre sessões** ([core/corner_bests.py](core/corner_bests.py)). A melhor passagem de cada curva é gravada em disco (`corner_bests.json`, ~2 KB por Pista/Carro) e devolvida ao coach quando você volta à pista. A diferença é grande na prática:

| | 1ª volta medida | 2ª volta |
|---|---|---|
| **Coach do zero** (Treino 1) | média +0.000 — **não pode falar**: essa volta só serve para virar o alvo | +0.095 → fala |
| **Coach com histórico** (Treino 2) | média **+0.129 → já fala** | +0.099 → fala |

Sem isso, o coach chegava na primeira volta do Treino 2 sem saber nada — depois de um Treino 1 inteiro te observando — e gastava duas voltas reconstruindo o que já tinha aprendido na véspera. Quem já tinha voltas gravadas antes desta versão não fica de fora: na primeira vez o app varre as voltas mais rápidas do catálogo para montar o arquivo (~110 ms para cinco voltas, uma vez só). **Se o mapa de curvas mudar, o arquivo é descartado** — a curva 5 de um mapa detectado automaticamente pode virar a curva 4 no próximo, e cobrar a freada da curva errada é pior que não cobrar nada.

**Cada recado tem validade própria.** A fila de voz descartava só o que passasse de 15 s, o que serve para "asfalto esquentando" mas não para uma dica de curva: se a fila estivesse ocupada, *"Ferradura chegando: atrasa a freada"* podia ser falada dez segundos depois, já dentro de **outra** curva — mandando o piloto frear no lugar errado. Agora a dica declara que vale 3,5 s e o veredito 6 s. Recado crítico continua sem validade: bandeira preta nunca vence por idade.

**O valor está no que ele NÃO fala:**

- **Cala a boca com o carro carregado.** Nada de voz entre a freada e a saída da curva — nessa janela o piloto está ocupado, e frase no ouvido atrapalha em vez de ajudar. Também cala no box, no pit lane, em replay e com o jogo pausado.
- **Uma dica por curva por volta**, no máximo duas curvas por volta, com intervalo mínimo entre recados.
- **Não opina com uma volta só.** Sem histórico, precisa de duas: a primeira só serve para virar o alvo. Com histórico em disco, uma volta de hoje já basta — o alvo não é chute, é uma passagem que você realmente fez.
- **Dois segundos perdidos numa curva não rendem conselho de técnica.** Isso foi rodada, escapada ou tráfego — o piloto já sabe o que houve.
- **Ignora volta de saída e de retorno aos boxes.** Uma volta de retorno é 20 segundos mais lenta: aprendendo com ela, o coach concluía que o piloto perdia dois segundos em *cada* curva da pista, e passava a repetir isso em todas as retas da volta seguinte.
- **Fala diferente em cada sessão:** em **treino** conversa (2 dicas + 2 vereditos por volta e elogio quando a curva melhora); na **classificação** só na volta lançada, e sem veredito (o piloto não pode gastar atenção com o que já passou); na **corrida** quase cala a boca — uma dica por volta e só se a perda passar de 0,20 s, porque quem está brigando por posição não quer aula de ponto de freada.

**Exemplo real, saído do simulador de fim de semana** (treino livre, piloto que freia cedo na Ferradura):

```
--- volta 4 ---
   [ENG    info] Delta mais 0.10, um pouco atrás da referência
   [COACH   57%] Ferradura chegando: mais velocidade no ápice     <- 2,9 s antes da freada
   [ENG atencao] Perdeu 0.23 no setor 2
   [COACH   68%] Perdeu 1 décimo na Ferradura, entrou devagar     <- na saída da curva
   [ENG atencao] Melhor volta ameaçada, faltam 286 metros
   >>> fechou 1:23.367 | Tem 2 décimos na mesa: 1 décimo na Ferradura

--- volta 6 ---
   [COACH   57%] Ferradura chegando: mais velocidade no ápice
   >>> fechou 1:23.100        <- corrigiu: o veredito de saída parou de sair
```

---

### 🗓️ Fim de Semana de Corrida (múltiplas sessões)

Treino 1, Treino 2, Treino 3, classificação e corrida são **cinco sessões na mesma pista, com o mesmo carro** — normalmente sem fechar o jogo. O app detecta a virada sozinho, por três sinais (o tipo mudou; o contador de voltas do jogo voltou para trás; o número de voltas completadas caiu), porque nenhum deles cobre todos os casos: de Treino 1 para Treino 2 o AC continua reportando "Practice".

Na virada de sessão:

- **Zera** o histórico de voltas da tela, o seletor de voltas, os conselhos do engenheiro, as curvas aprendidas pelo coach, o ritmo e o consumo;
- **Zera** a "melhor volta da sessão" — que é o que o piloto compara com o que está fazendo *agora*;
- **Preserva** o catálogo em disco, o Personal Best, a volta ideal e o mapa de curvas da pista. Nada disso muda porque começou a classificação.

No catálogo, cada sessão fica **agrupada e identificável**: a tela de análise pós-sessão mostra `Interlagos — Porsche Cup` → `Sessão 08/09 14:35` → as voltas dela. Dá para voltar no domingo à noite e comparar a sua volta de classificação com a melhor do Treino 3.

**Na corrida, a referência "Automática" muda de propósito:** vale a melhor volta *daquela* sessão, não o Personal Best da classificação. Perseguir a volta de quali com tanque cheio e pneu usado é perseguir um tempo que o carro não tem hoje — o delta ficaria em "+2 segundos" a corrida inteira e não diria nada sobre a volta que acabou de ser feita.

---

### 🎧 Engenheiro de Pista (análise por regras + voz)
- **Diagnóstico, não só número:** lê as métricas curva a curva e diz o que fazer. Ex.: *"Ferradura: perdeu 0.42 segundos, freou 20 metros antes. Atrasa a freada"*.
- **Foco em pilotagem, não em setup:** o engenheiro fala de tempo, setor, pedal, volante, marcha e traçado — coisas que você muda na volta seguinte, sem sair da pista. Câmber, pressão e ganho de force feedback ficam de fora de propósito (há teste cobrando esse silêncio).
- **100% local:** regras sobre a telemetria, sem serviço externo e sem modelo de linguagem — custo zero, resposta instantânea e número sempre exato (nunca inventado).
- **Seletor de modo**, no próprio painel:
  - **Fim de volta** — o balanço da volta que acabou (padrão).
  - **Ao vivo** — avisos com o carro na pista: delta contra a referência, setor que fechou, melhor volta em jogo na reta final, roda travando, TC cortando, pneu superaquecido, bandeira, penalidade, combustível, limitador esquecido ligado, dano, última volta, asfalto esfriando, vento.
  - **Sob demanda** — nada aparece sem você clicar em **ANALISAR**.
- **Texto e voz:** painel com histórico colorido por severidade (crítico / atenção / info) e fala pelo SAPI do Windows. O botão **VOZ** desliga a fala sem apagar o texto.
- **Escolha automática da melhor voz:** prioriza as vozes **OneCore** do Windows 10/11 (`Microsoft Daniel` / `Microsoft Maria`), muito mais naturais que as `... Desktop` do SAPI clássico — que são as únicas que o Windows enumera por padrão. A ordem de peso é: idioma (português na frente de tudo — voz inglesa lendo português fica incompreensível) → geração (OneCore ganha da Desktop, que é de 2010) → **gênero** (masculina, por padrão) → nome. Os pesos ficam em `voice_score`, em [core/voice.py](core/voice.py).
- **Frase de spotter, não parágrafo:** o recado é a ORDEM (*"Tá travando a roda na freada da Curva 9. Chega mais suave"*); o porquê (*"freada além do limite"*) vai para o detalhe, que só aparece no painel. Recado que leva nove segundos não soa calmo, soa travado — ele pausa na pontuação e não termina — e ainda estoura a validade da fila de voz, que descarta o que passa de 15 s. Encurtar o texto cortou o tempo de fala pela metade (10,5 s → 5,4 s nos três recados mais longos).
- **Ritmo um pouco acima do natural** (`voice_rate: 2`, `voice_pitch: 0`). Uma voz sintética em ritmo neutro não soa calma, soa arrastada: falta a entonação que num humano quebraria a monotonia. O tom fica neutro porque o ajuste existia para tirar o agudo de uma voz feminina — com a voz masculina o timbre já é grave, e cada efeito a mais é uma chance a mais de soar artificial.
- **Ajuste de ouvido** (`python ajustar_voz.pyw`), em duas abas:
  - **VOZ** — sliders de ritmo, tom e volume, o seletor das vozes instaladas e os recados **de verdade** do engenheiro para ouvir. Cada frase aparece com o tempo que levou, e as longas vêm marcadas: metade da sensação de "voz travada" não é ritmo, é frase comprida. **Comparar ritmos** fala a mesma frase em `0 / +2 / +4`, e **Comparar tons** em `0 / -2 / -4`, em sequência.

    O comparador de tons existe por um motivo específico: deslocar o tom não muda como a voz é sintetizada, o Windows **reamostra a fala já pronta**. Quanto maior o deslocamento, mais artefato — e o ouvido registra isso como "metálico" ou "eletrônico" sem saber de onde vem. A partir de `±3` a janela avisa. Para um timbre mais grave, prefira uma voz naturalmente grave a puxar esta para baixo; e a janela diz qual motor está falando, porque o SAPI tem um teto de naturalidade que nenhum slider ultrapassa (acima dele, só a voz neural).
  - **MESA DE SOM** — um fader por assunto: dica antes da curva, veredito na saída, curvas no fim da volta, pedal/volante/marcha, ABS e tração, tempo e setor, carro e consumo, pista e clima, bandeiras. Cada canal tem **ouvir** (fala um exemplo no volume dele) e **mudo**. Presets prontos: *só o coach de curva* e *modo corrida*.

  **Salvar no config.json** grava voz + mesa. Timbre e o que interessa ouvir são gosto pessoal e mudam com o headset e com o tipo de sessão: nenhum padrão no código acerta para todo mundo.
- **A mesa mexe só na VOZ.** Canal no zero cala a fala daquele assunto; o painel de texto continua mostrando tudo, sempre. Silenciar é escolher não ser interrompido, não escolher ficar sem o dado. O volume do canal MULTIPLICA o volume geral, então baixar o volume da sessão baixa tudo junto — e um canal audível nunca emudece por arredondamento, porque zero tem um significado só: "não fale".
- **Recado novo nasce audível.** Uma regra que ninguém classificou cai num canal cheio em vez de ficar muda — o contrário seria um aviso que nunca fala e não deixa erro nenhum para investigar. Um teste lê as chaves direto do código-fonte e falha se alguma ficar sem canal.
- **Você escolhe a voz:** `voice_name` no `config.json` aceita um pedaço do nome (`"Daniel"`, `"Maria"`) e vence a escolha automática. As vozes instaladas variam de máquina para máquina, então o app **lista no console** o que encontrou ao iniciar — é de lá que você tira o nome a usar.
- **Fila de voz com prioridade:** um recado **crítico** fura a fila e **corta a frase em andamento** — "bandeira preta" não espera o balanço da volta terminar. A fala é síncrona, então duas frases nunca se atropelam.
- **Cada recado tem validade própria:** dica de curva vale 3,5 s, veredito de saída 6 s, recado de contexto ("asfalto esquentando") 15 s. Passado esse tempo na fila ele é descartado em vez de dito atrasado — uma dica de freada falada dez segundos depois chega em cima de OUTRA curva. Recado crítico não tem validade: bandeira preta nunca vence por idade.
- **Voz neural opcional (Kokoro):** com `kokoro-onnx` instalado e o modelo v1.0 em `telemetry_data/models/`, a síntese passa a ser neural (voz masculina `pm_alex` por padrão), com cache de áudio em disco. É bem menos robótica que qualquer voz do Windows. Sem os arquivos — ou se a síntese falhar no meio da sessão — o SAPI assume sozinho.
- **Fala em português de verdade:** o número dito usa vírgula decimal (`0,42`), senão o sintetizador lê "zero ponto quatro dois". O painel mantém o ponto, como o resto do app.
- **A curva é chamada pelo NÚMERO:** *"Curva 7"*, não *"Ferradura"*. Nome próprio obriga o piloto a traduzir nome em lugar (*"qual era a Ferradura mesmo?"*) no exato momento em que ele precisa agir, e a numeração casa com a tabela curva a curva (`C7`) e com o número desenhado na faixa do gráfico — os três lugares passam a dizer a mesma coisa. Quando a pista tem mapeamento manual com nomes, o nome vai para o **detalhe** do recado, onde o painel tem tempo de leitura que a fala não tem. Para voltar aos nomes na fala: `corner_label_style: "nome"` no `config.json`.
- **Todo recado diz ONDE:** *"Tá travando a roda **na freada da Curva 9**"*, *"Tá repisando o freio **na freada da Curva 6**"*, *"O carro não está virando **na entrada da Ferradura**"*. Numa volta de dois minutos, saber que algo acontece "em 40% das freadas" não diz em qual curva chegar diferente — a fração fica no painel, o lugar vai na voz. A gravidade de cada erro é **medida** (a profundidade da repisada, o tempo da soltura, o pico do ABS), não um booleano, para o recado apontar a pior freada e não uma qualquer.
- **Sem mapa de curvas, o recado sai sem lugar** — nunca com um lugar inventado. Um lugar errado é pior que nenhum: o piloto passa a volta seguinte trabalhando a curva errada.
- **Não metralha o piloto:** cada regra tem tempo de espera próprio, há intervalo mínimo entre falas, e só o essencial vai para a voz (o crítico + a curva onde mais se perdeu). O resto fica no painel.
- **Silêncio fora da pista:** no box, no pit lane, em replay ou com o jogo pausado o engenheiro não comenta pneu frio nem delta. Só bandeira e penalidade valem em qualquer lugar.
- **O que ele analisa:**

  | Tema | O que sai pela voz |
  |---|---|
  | **Tempo** | delta ao vivo (só quando **muda**), setor que fecha (*"bateu a referência no setor 1, ganhou 0.08"*), melhor volta ameaçada ou na mão na reta final |
  | **Setores** | resumo verde/amarelo/vermelho da volta, o setor onde o tempo está indo embora, e o **ponto forte** (só depois de virar padrão em 3 voltas) |
  | **Curva** | perda por curva e a causa: ponto de frenagem (inclusive *"ponto de freio bom, só antecipa uns 5 metros"*), $V_{min}$ com alvo concreto, ponto de retomada, **marcha no ápice** contra a referência, **velocidade de saída**, **desvio de traçado em metros**, e curva de gás cheio em que você freou |
  | **Pedais** | freio e acelerador sobrepostos, freio solto em degraus, freio **largado de uma vez** (*"solta mais suave, aliviando até o ápice"*) |
  | **Volante** | subesterço (volante travado com o carro sem virar), suavidade contra a referência — com elogio quando está melhor |
  | **Motor** | trocas cedo demais fora da faixa de potência, batidas no corte |
  | **Carro / pista** | pneu e freio quentes, pneus frios num recado só, dano (só quando **piora**), limitador ligado em pista, asfalto esfriando/esquentando, pista verde, vento forte |
  | **Combustível** | autonomia vs. voltas restantes, consumo alto da volta contra a média |
  | **Ritmo** | consistência entre as últimas voltas, dita só quando **muda** |

- **Mede antes de opinar:** as medidas de pilotagem (sobreposição de pedais, suavidade de volante, pontos de troca, desvio de traçado) ficam em [core/driving_analysis.py](core/driving_analysis.py), separadas das regras. Toda medida devolve `None` quando o canal não existe — ghost antigo sem marcha ou sem coordenada faz o engenheiro se calar, não chutar.
- **Canal de marcha:** a marcha passou a ser gravada por volta (é dela que sai o *"passou de 2ª onde a referência usa 3ª"*). Voltas gravadas **antes** desta versão não têm o canal, então o conselho de marcha só aparece quando a volta de referência também for nova.
- **Fica calado quando está tudo bem** — é o comportamento mais testado da funcionalidade.
- **Bancada de voz:** `python test_voice.pyw` abre uma janela que monta estados de telemetria e voltas sintéticas de verdade e passa pelo `RaceEngineer`, para ouvir cada aviso (e testar prioridade e preempção) sem entrar na pista.

---

### 🔀 Análise Curva a Curva (Turn-by-Turn)
- **Painel dedicado no rodapé:** uma linha por curva da pista, com o valor medido na volta analisada e o delta contra a volta de referência selecionada. A curva onde mais tempo foi perdido fica destacada em vermelho.
- **Métricas por curva:**
  - **Ponto de Frenagem** — metro em que o freio sai de ~0% e passa de 10%.
  - **Velocidade Mínima ($V_{min}$)** — menor velocidade no ápice, e o delta contra a referência.
  - **Ponto de Retomada** — metro em que o acelerador volta a 100%.
  - **Delta da Curva ($\Delta t$)** — tempo ganho/perdido **só naquele trecho**, medido por interpolação de tempo nos limites da curva.
- **Mapeamento por pista em JSON:** arquivos em `track_maps/`, com os limites em posição relativa (0.0–1.0) ou em metros. Veja `track_maps/README.md`.
- **Fallback automático, em quatro passos:** pista sem mapeamento tem as curvas achadas pela Força G lateral.
  1. **Histerese** — o trecho abre em $|G_{lat}| > 0.4$ g e só fecha abaixo de $0.25$ g, senão uma curva de raio variável viraria três.
  2. **Fusão** de trechos vizinhos (até 40 m de respiro), e **só para o mesmo lado**.
  3. **Corte na troca de mão** — chicanes e esses viram curvas separadas, cada uma com a sua mão.
  4. **Corte no vale** — numa sequência ligada o G alivia entre os ápices sem chegar a zero; esse alívio é a fronteira.

  Os passos 3 e 4 existem porque os dois primeiros, sozinhos, entregavam a pista inteira como três curvas gigantes.
- **O mapa sai de VÁRIAS voltas limpas, não de uma:** volta com corte de pista nunca semeia o mapa (antes semeava — e como o arquivo fica em disco, a geometria do corte valia para sempre; pior, a volta usada era tipicamente a primeira numa pista nova, justo a mais propensa a abrir demais). Cada volta limpa vota, e uma curva só entra se a **maioria** confirmar: um corte aparece numa volta e é rejeitado; uma curva de verdade que você cortou numa volta continua no mapa pelo voto das outras. O mapa já serve na primeira volta limpa e vai se refazendo até fechar três — a aba mostra **PARCIAL** enquanto isso.
- **Correção de detector chega em quem já usou o app:** o arquivo guarda a versão do detector e de quantas voltas saiu. Mapa de uma versão antiga, ou de menos voltas que o consenso pede, é refeito sozinho. Mapa **manual nunca é tocado** — foi você que escreveu.

  O resultado é gravado como `*.auto.json`, então a numeração não muda de volta para volta — e você pode editar o arquivo (inclusive **trocar `"Curva 9"` por `"Signes"`**), remover o `.auto` do nome, e ele passa a ser o mapeamento manual, que sempre vence e nunca é refeito.
- **Voltas antigas também funcionam:** ghosts gravados antes do canal `g_lat` têm a Força G lateral reconstruída pela curvatura do traçado ($a_{lat} = v^2 \kappa$). A curvatura é medida sobre uma base de 12 m, não entre amostras vizinhas: a 60 Hz duas amostras ficam a menos de meio metro, as coordenadas são gravadas ao centímetro, e nessa escala o ruído de arredondamento domina a conta — o $|G|$ reconstruído chegava a 6,8 g numa volta cujo pico real era 2,6 g, e a pista inteira aparecia acima do limiar de curva.
- **Destaque nos gráficos:** botão **CURVAS** sombreia os limites de cada curva nos quatro gráficos da pilha, numerados no gráfico de velocidade.
- **As faixas caem sobre a volta DESENHADA:** o eixo X dos gráficos é tempo e os limites de curva são distância, e a conversão de um para o outro só vale para a volta de onde o mapa de tempo saiu. Ao vivo o gráfico mostra a volta **em andamento**, então é o mapa de tempo dela que posiciona as faixas — e elas acompanham o carro em vez de ficarem onde a volta anterior as deixou. Antes as faixas usavam a última volta **fechada** e só eram recalculadas no fim da volta: o erro crescia ao longo da volta (mais de 1 s na penúltima curva com 1,3 s de diferença entre as duas voltas, e dezenas de segundos se a anterior tinha sido de saída de box), então o número da curva ficava sobre o trecho errado do gráfico.
- **A aba diz qual volta a tabela mede:** ao vivo os dois números vêm de voltas diferentes — as faixas sombreiam a volta em andamento, a tabela mede a última fechada (não dá para cronometrar uma curva que o carro ainda não terminou). O título da aba mostra o tempo da volta analisada para os dois não serem confundidos.

---

### 📋 Biblioteca de Voltas, Histórico e Exportação
- **Tabela de Histórico de Voltas:** Lista completa das voltas da sessão com S1, S2, S3, tempo total e $\Delta$ Best, destacando a volta mais rápida e marcando as sujas com ⚠ (que não concorrem a melhor volta).
- **Catálogo de voltas** ([core/lap_library.py](core/lap_library.py)) — a gravação foi refeita em torno de três ideias:
  - **Índice leve.** Um `index.json` por Pista/Carro guarda só o que a interface precisa para montar uma lista (tempo, setores, data, validade, canais). Alguns KB, mesmo com centenas de voltas — e é o único arquivo lido quando você entra na pista. Antes o app abria a telemetria completa de TODAS as voltas já gravadas nesse momento: com 200 voltas em disco eram centenas de MB de RAM e segundos de travamento, bem na hora de entrar na pista.
  - **Telemetria sob demanda.** Os milhares de pontos de cada volta ficam num arquivo próprio, comprimido, lido só quando aquela volta vira referência ou é aberta nos gráficos. Um cache pequeno segura as últimas.
  - **Retenção.** A pasta não cresce para sempre. Por padrão ficam as **30 mais rápidas** e as **200 mais recentes**, além das fixadas com alfinete (📌) e das salvas à mão; o resto sai sozinho. Um fim de semana de corrida dá 40 a 60 voltas, então 200 cobrem vários com folga. Ajustável em `config.json`, e `"enabled": false` desliga a limpeza.
- **Arquivos 6x menores.** Cada canal é arredondado para a precisão que ele realmente carrega e o arquivo vai comprimido. Uma volta de 90 s a 60 Hz saía a **1,5 MB**; agora fica entre **~80 e ~250 KB**, dependendo de quanto o sinal varia (medido: 51 KB no simulador do fim de semana, 180 KB numa volta do provider mock, 250 KB no pior caso possível — dados aleatórios, que não comprimem). Vale também para a volta ideal, que fica fora do catálogo (é sintética — ninguém a deu) mas tem o tamanho de uma volta inteira: crua, ela era o maior arquivo da pasta.
- **Personal Best sem perder o anterior.** O PB deixou de ser um arquivo sobrescrito a cada recorde: ele é DERIVADO do catálogo. Bater o recorde não apaga a volta antiga — ela continua na lista e pode voltar a ser referência.
- **Exportação:**
  - **MoTeC i2 Pro (.ld / .ldx):** exportação nativa no formato binário oficial do MoTeC com suporte a Pro Logging (`0xC81A4`), canais a 60 Hz e arquivo de marcas de volta/setores em XML (`.ldx`).
  - **Relatório de Desempenho (.md / .txt):** diagnóstico técnico de engenharia com pontos fortes, pontos fracos, causas raízes baseadas na física da telemetria e plano de ação corretivo para a próxima volta.
  - **CSV** de qualquer volta, pela tela de análise pós-sessão — uma coluna por canal, para abrir em planilha ou cruzar com o que você quiser.
  - **PNG** da tela de análise, manual pelo botão ou automático a cada novo Personal Best.
- **Nada se perde na atualização:** o `best_lap_ghost.json` das versões anteriores é importado para o catálogo (e fixado com alfinete) na primeira vez que você entra na pista, e as voltas soltas em JSON são catalogadas de onde estão.

---

### 📈 Tela de Análise Pós-Sessão (`mapa.pyw`)
Aplicação separada, **offline** — não conecta no jogo e não grava nada:

```bash
python mapa.pyw
```

- **Navegador do catálogo:** árvore Pista → Carro → Sessão → Volta, com o tempo, data e indicadores de cada volta.
- **Comparação de até 4 voltas sobrepostas** (Ctrl+clique), cada uma com sua cor, em velocidade, acelerador, freio e volante — mais o **delta** de cada uma contra a primeira selecionada, interpolado por distância.
- **Traçado no mapa** das voltas selecionadas e leitura dos valores sob o cursor, sincronizada entre todos os gráficos.
- **Eixo X por distância ou por tempo.**
- **Barra de Ações e Menu de Contexto:**
  - **`RELATÓRIO`:** abre o visualizador analítico completo de desempenho (pontos positivos, negativos, causas de tempo perdido e plano de ação).
  - **`MoTeC (.ld)`:** exporta a volta selecionada diretamente para o formato binário MoTeC i2 Pro com arquivo `.ldx` acompanhante.
  - **`CSV`:** exporta todos os canais e registros de amostragem da volta selecionada em planilha `.csv`.
  - **`📌 FIXAR`:** fixa com alfinete, blindando a volta contra a política de retenção e limpeza automática.
  - **`APAGAR`:** remove permanentemente as voltas selecionadas do índice e do disco.
  - **`ATUALIZAR`:** recarrega a árvore de voltas a partir do sistema de arquivos.

> Esta tela tinha um sistema de gravação só dela (`telemetry_sessions/`), com nomes de canal diferentes dos do dashboard — `x`/`z`/`throttle` em vez de `car_x`/`car_z`/`gas`. Na prática ela nunca conseguiria abrir uma volta gravada pelo app principal. Agora as duas leem o mesmo catálogo.

---

### 📊 Relatórios de Desempenho & Diagnóstico Causal (Onde Melhorar e Por Quê)

Mais do que apenas exibir curvas de telemetria na tela, o ApexView conta com um motor analítico de engenharia de pista ([core/lap_report.py](core/lap_report.py)) projetado para responder com precisão matemática às três perguntas essenciais de qualquer piloto:
1. *Onde exatamente eu ganhei ou perdi tempo?*
2. *Qual foi o mecanismo físico e dinâmico que causou essa perda?*
3. *O que devo mudar concretamente na pilotagem na próxima volta?*

O gerador pode ser acionado diretamente na tela de análise pós-sessão (`mapa.pyw`) clicando no botão **RELATÓRIO** ou clicando com o botão direito sobre qualquer volta da árvore.

#### 🔍 Como o Relatório é Estruturado:

1. **Metadados e Resumo Geral:**
   - Tempo da volta analisada, tempo da volta de referência (Personal Best ou outra volta da sessão), delta total e status de validade.
   - Decomposição por setores (**S1, S2, S3**) com os tempos individuais e os respectivos deltas contra a referência.
   - Diagnóstico de consistência e ritmo geral.

2. **Tabela Turn-by-Turn Quantitativa:**
   - Tabela comparativa curva a curva da pista com métricas extraídas por interpolação cinemática:
     - **Delta da Curva ($\Delta t$):** tempo ganho (verde) ou perdido (vermelho) no trecho delimitado da curva.
     - **Ponto de Frenagem ($m$):** metro da pista onde o pedal de freio superou 10%, comparado metro a metro contra a referência.
     - **Velocidade Mínima no Ápice ($V_{min}$ em $km/h$):** velocidade no ápice geométrico/dinâmico e déficit de velocidade.
     - **Ponto de Retomada ($m$):** metro da pista onde o acelerador alcançou 100% na saída de curva.

3. **Principais Pontos Positivos (O Que Funcionou):**
   - Curvas onde o piloto superou a volta de referência.
   - Explicação física causal do ganho: *frenagem tardia executada sem travamento de rodas*, *alta sustentação de $V_{min}$ no ápice sem espalhar*, *retomada de aceleração antecipada com tração eficiente sem intervenção abusiva do TC*.

4. **Principais Pontos Negativos (Onde o Tempo Foi Perdido):**
   - Destaque das curvas com maior perda de tempo, ranqueadas por magnitude de prejuízo (em décimos e centésimos de segundo).
   - Apontamento imediato do setor onde a volta foi comprometida.

5. **Onde Melhorar e O Porquê (Diagnóstico Causal Detalhado):**
   - Cada ponto crítico de perda é destrinchado em três dimensões claras:
     - **Diagnóstico do Problema:** Ex.: *Curva 6 (Ferradura) — Entrada lenta e freada muito antecipada (+0.340s)*.
     - **O Porquê (Causa Raiz Física):** Ex.: *O freio foi acionado 22 metros antes da referência e a velocidade no ápice caiu para 114 km/h vs 123 km/h da volta ideal. O carro desacelerou cedo demais na reta de aproximação e descarregou o eixo dianteiro antes da hora.*
     - **Ação Prática Corretiva (O que fazer):** Ex.: *Atrase o ponto de frenagem em cerca de 15 a 18 metros usando a placa dos 100m como referência; mantenha leve pressão de freio (trail braking) para ajudar a frente a apontar para o ápice.*

6. **Avaliação Geral da Técnica de Pilotagem:**
   - **Pedais (Acelerador e Freio):**
     - Detecção de **sobreposição indesejada** de acelerador e freio (overlapping).
     - Modulação de freio: identificação de **freio largado abruptamente em degrau** (que desestabiliza a transferência de carga) versus transição suave até o ápice (trail braking).
   - **Volante e Direção:**
     - Nível de suavidade e correções bruscas de volante.
     - Detecção de **subesterço severo** (ângulos altos de volante com baixa resposta de Força G lateral).
   - **Câmbio e Rotação:**
     - Comparativo de **marcha engatada no ápice** contra a referência (evitando motor sufocado em rotação baixa na saída ou corte prematuro de giro).
   - **Intervenções de Eletrônica (ABS e TC):**
     - Percentual de tempo em frenagem com atuação intensa do ABS (indício de excesso de pressão inicial no pedal).
     - Atuação de Controle de Tração (corte de potência por excesso de aceleração com o volante ainda esterçado).

7. **Potencial Teórico de Tempo:**
   - Estimativa calculada do tempo que pode ser recuperado apenas corrigindo os principais gargalos identificados.

#### 💻 Interface e Exportação no `mapa.pyw`:

- **Diálogo Interativo (`LapReportDialog`):**
  - **Seletor de Referência Dinâmico:** permite alternar entre o **Personal Best** da pista/carro ou **qualquer outra volta da sessão** gravada (ideal para comparar a evolução de um stint de corrida).
  - **Alternância de Visualização:**
    - **Modo Markdown:** renderizado com formatação visual moderna, cabeçalhos destacados, tabelas e cores temáticas.
    - **Modo Texto Puro:** formatação limpa monoespaçada em 80 colunas, com divisórias em arte ASCII e alinhamento perfeito de colunas.
  - **Copiar com 1 Clique:** botão para copiar o relatório completo para a área de transferência (ótimo para colar no Discord, WhatsApp ou bloco de notas de corrida).
  - **Exportar Arquivo:** botão para salvar o relatório em arquivo `.md` ou `.txt`.

```text
================================================================================
           APEXVIEW — RELATÓRIO DE DESEMPENHO E ENGENHARIA DE TELEMETRIA        
================================================================================

Pista: Autodromo Jose Carlos Pace
Carro: Porsche 992 GT3 Cup
Volta: 7 | Tempo: 1:32.450
Referência: Personal Best | Tempo: 1:31.980 (Delta: +0.470s)

--------------------------------------------------------------------------------
SETOR 1: 38.120s  (Delta: +0.110s)
SETOR 2: 32.480s  (Delta: +0.320s)  <-- MAIOR PERDA
SETOR 3: 21.850s  (Delta: +0.040s)
--------------------------------------------------------------------------------

>>> PRINCIPAIS PONTOS POSITIVOS:
  [+] Curva 1 (S do Senna) — Frenagem no limite e boa tração na saída (-0.080s)
      Justificativa: Ponto de freio 5m depois da referência sem travamento de ABS,
      permitindo manter velocidade de entrada com retomada limpa.

>>> ONDE MELHORAR E O PORQUÊ:
  [!] Curva 6 (Ferradura) — Entrada lenta e freada antecipada (+0.320s)
      Por que: Frenagem iniciada 19 metros antes da referência (182m vs 163m) e
      V_min no ápice de 116.2 km/h vs 124.8 km/h. O carro desacelerou cedo demais.
      Ação Prática: Atrase o freio em 15m usando a placa dos 100m como referência.
      Carregue mais velocidade para o ápice aproveitando a compressão da curva.

>>> TÉCNICA DE PILOTAGEM:
  - Pedais: Sobreposição leve de acelerador e freio detectada na entrada da Curva 4.
  - Volante: Suavidade boa, sem indícios de subesterço crônico.
  - Eletrônica: ABS atuou em 28% do tempo de frenagem forte; alivie o pedal no final.
================================================================================
```

---

### 🏎️ Exportação MoTeC i2 (.ld / .ldx) para MoTeC i2 Pro

O **MoTeC i2 Pro** é o padrão definitivo da indústria para engenharia de dados e análise de telemetria no automobilismo profissional e no automobilismo virtual competitivo. O ApexView possui um exportador nativo de baixo nível ([core/motec/](core/motec/)) capaz de converter diretamente as voltas gravadas do Assetto Corsa em arquivos de telemetria MoTeC binários (`.ld`) e metadados de marcas e setores em XML (`.ldx`).

#### ⚡ Recursos do Exportador MoTeC:

- **Compatibilidade MoTeC i2 Pro ("Pro Logging"):**
  - Arquivo binário estruturado segundo a especificação MoTeC Logged Data versão 1.1.
  - Emite a assinatura mágica de desbloqueio `PRO_LOGGING_MAGIC = 0xC81A4`, liberando todos os recursos avançados de análise matemática, gráficos multicanal e histogramas do MoTeC i2 Pro sem limitações da versão padrão.
- **Arquivo de Marcas XML (`.ldx`) Sincronizado:**
  - Gera simultaneamente o arquivo acompanhante `.ldx` no mesmo diretório com o mesmo nome do `.ld`.
  - Registra as marcas temporais exatas de abertura e fechamento de volta (beacon), além das divisórias dos setores **S1, S2 e S3**. O MoTeC abre o arquivo já com as voltas e parciais divididas na régua de tempo, sem necessidade de configuração manual de balizas.
- **Reamostragem e Interpolação Uniforme a 60 Hz:**
  - Todo o sinal temporal é alinhado a uma base fixa de 60 Hz, eliminando *jitter* temporal e inconsistências na taxa de quadros.
- **Canais de Telemetria Exportados:**

  | Canal MoTeC | Unidade | Descrição |
  |---|---|---|
  | `Speed` | km/h | Velocidade linear do veículo |
  | `Throttle` | % | Curso do pedal de acelerador (0 a 100%) |
  | `Brake` | % | Pressão do pedal de freio (0 a 100%) |
  | `Steer` | deg | Ângulo de esterçamento do volante em graus reais |
  | `Gear` | — | Marcha selecionada (-1 = Ré, 0 = Neutro, 1 a N) |
  | `RPM` | rpm | Rotações por minuto do motor |
  | `G_Lat` | g | Força G lateral (aceleração em curva) |
  | `G_Long` | g | Força G longitudinal (aceleração / frenagem) |
  | `G_Vert` | g | Força G vertical (oscilação e compressão) |
  | `SlipAngle` | deg | Ângulo de escorregamento do veículo |
  | `WheelSpeed FL` | km/h | Velocidade da roda dianteira esquerda |
  | `WheelSpeed FR` | km/h | Velocidade da roda dianteira direita |
  | `WheelSpeed RL` | km/h | Velocidade da roda traseira esquerda |
  | `WheelSpeed RR` | km/h | Velocidade da roda traseira direita |
  | `Clutch` | % | Posição da embreagem |

- **Metadados de Sessão Embutidos:**
  - Cabeçalho binário enriquecido com nome do piloto (`Driver`), carro (`Vehicle`), circuito (`Venue`) e data/hora oficial da sessão.
- **Como Exportar:**
  - Na tela de análise pós-sessão (`mapa.pyw`), clique na volta desejada e selecione o botão **`MoTeC (.ld)`** (ou clique com botão direito na volta e escolha *Exportar MoTeC i2 (.ld)*).
  - Pelo Python via `LapLibrary` ou facilitador:
    ```python
    from core.lap_library import LapLibrary
    from core.motec_exporter import export_lap_to_motec

    lib = LapLibrary()
    record = lib.best_lap("spa", "ferrari_488_gt3")
    # Exportação através da biblioteca:
    lib.export_motec("spa", "ferrari_488_gt3", record, "spa_melhor_volta.ld")
    ```

---

### 💾 Onde Ficam os Dados

Tudo é gravado ao lado do app (ou do `.exe`, quando empacotado). Nada vai para AppData, nada vai para a nuvem, nada é enviado para lugar nenhum.

```text
telemetry_data/
└── Autodromo Jose Carlos Pace/          <- nome da pista, como o jogo informa
    └── Porsche 992 GT3 Cup/             <- nome do carro
        ├── index.json                   catálogo leve: tempo, setores, data,
        │                                validade e canais de cada volta.
        │                                É o ÚNICO arquivo lido ao entrar na pista.
        ├── corner_bests.json            a melhor passagem de cada curva (~2 KB).
        │                                É daqui que o coach chega sabendo.
        ├── ideal_lap_ghost.json.gz      volta ideal: os melhores setores costurados.
        │                                Sintética, então fica fora do catálogo.
        └── laps/
            ├── 20260908-143512_L007.json.gz    uma volta, comprimida
            └── 20260908-143644_L008.json.gz    (~80 a 250 KB cada)
```

| Arquivo | Some se você apagar? |
|---|---|
| `index.json` | **Não** — é reconstruído varrendo `laps/` na próxima abertura. |
| `corner_bests.json` | **Não** — é refeito das voltas mais rápidas do catálogo (~110 ms). |
| `ideal_lap_ghost.json.gz` | **Não** — volta a ser montado conforme você fecha setores. |
| `laps/*.json.gz` | **Sim.** Essa é a telemetria de verdade. |

Além disso, na raiz: `config.json` (preferências), `exportacoes/` (PNGs) e `track_maps/` (mapeamento das curvas — os manuais são versionados, os detectados terminam em `.auto.json`).

**Arquivo corrompido nunca derruba o app.** Um JSON truncado — jogo fechado no meio de uma gravação — é isolado como `*.corrupt` e o app segue com o que sobrou. Toda gravação é atômica (arquivo temporário + `os.replace`), então nem um desligamento no pior momento deixa arquivo pela metade.

---

### ⚙️ Preferências Persistentes (`config.json`)

O arquivo nasce sozinho com os padrões, na raiz do app. Dá para editar à mão com o app fechado.

| Chave | Padrão | O que faz |
|---|---|---|
| `reference_kind` | `"auto"` | Referência selecionada: `auto`, `none`, `pb`, `session`, `ideal` ou `lap`. |
| `reference_lap_id` | `""` | Com `reference_kind: "lap"`, qual volta do catálogo usar. |
| `voice_enabled` | `true` | A voz do engenheiro começa ligada. |
| `voice_name` | `""` | Pedaço do nome da voz do Windows a usar (`"Daniel"`, `"Maria"`). Vazio = escolha automática. As vozes disponíveis são listadas no console ao iniciar. |
| `voice_rate` | `2` | Velocidade da fala, `-10` (lenta) a `+10` (rápida). Ajuste de ouvido com `python ajustar_voz.pyw`. |
| `voice_pitch` | `0` | Tom da voz, `-10` (grave) a `+10` (agudo). |
| `voice_volume` | `100` | Volume da fala, 0 a 100. |
| `voice_backend` | `"auto"` | `auto` (neural se houver, senão Windows), `kokoro` ou `sapi`. |
| `voice_prefer_male` | `true` | Preferir voz masculina quando houver uma no idioma certo. |
| `voice_mix` | `{}` | Mesa de som: volume de cada assunto, `0` (mudo) a `100`. Só o que difere de 100 é gravado; canal ausente vale cheio. Ajuste em `ajustar_voz.pyw`. |
| `corner_label_style` | `"numero"` | Como as curvas são chamadas nos recados: `numero` (`"Curva 7"`) ou `nome` (`"Ferradura"`, quando a pista tem mapeamento manual). |
| `engineer_mode` | `"lap"` | Modo do engenheiro: `lap` (fim de volta), `live` (ao vivo) ou `manual` (só no botão). |
| `auto_export_on_best_lap` | `true` | Salva um PNG da tela a cada novo Personal Best. |
| `mock_mode` | `false` | Liga o simulador interno. `--mock` e `APEXVIEW_MOCK` ganham desta chave. |
| `graph_redraw_every_n_frames` | `5` | A engine emite a 60 Hz; as curvas são redesenhadas 1 a cada N quadros (5 ≈ 12 fps de gráfico). Suba se a interface engasgar. |
| `retention.enabled` | `true` | `false` desliga a limpeza automática: nada é apagado, nunca. |
| `retention.keep_best` | `30` | Quantas voltas mais rápidas guardar para sempre. |
| `retention.keep_recent` | `200` | Quantas voltas recentes guardar (um fim de semana dá 40 a 60). |

**Preferência ruim nunca derruba o app.** Arquivo corrompido, chave que não existe mais, número onde devia haver texto: cai no padrão, avisa no console e segue. É preferência, não dado.

Duas coisas de propósito **não** estão aqui: o Personal Best e a volta ideal, que são do conjunto pista/carro e moram junto das voltas; e a melhor passagem por curva, que é aprendizado e mora em `corner_bests.json`.

---

## 📂 Estrutura do Projeto

```text
AssettoCorsa-Telemetry/
├── core/
│   ├── engine.py           # Thread a 60 Hz: leitura e emissão de sinais Qt
│   ├── models.py           # TelemetryState — estrutura de dados padronizada
│   ├── corner_analysis.py  # Análise Curva a Curva: mapas, detecção por G e métricas
│   ├── race_engineer.py    # Engenheiro de pista: regras de diagnóstico e conselho
│   ├── live_coach.py       # Coach de curva: dica antes da freada, veredito na saída
│   ├── corner_bests.py     # A melhor passagem de cada curva, guardada entre sessões
│   ├── driving_analysis.py # Medidas de pilotagem: pedais, volante, marcha, traçado
│   ├── lap_report.py       # Motor analítico: diagnóstico causal, pontos fortes/fracos e relatórios (.md/.txt)
│   ├── motec/              # Pacote exportador MoTeC i2 Pro (.ld binário e .ldx XML)
│   │   ├── channel_mapping.py # Mapeamento e interpolação uniforme de canais a 60 Hz
│   │   ├── exporter.py     # Orquestrador de exportação de voltas
│   │   ├── ld_writer.py    # Gerador binário do arquivo .ld (com Pro Logging 0xC81A4)
│   │   └── ldx_writer.py   # Gerador de marcas de volta e divisórias de setores em XML (.ldx)
│   ├── motec_exporter.py   # Ponto de entrada facilitador para exportação MoTeC i2
│   ├── voice.py            # Voz do engenheiro: fila com prioridade, SAPI/Kokoro
│   ├── voice_mix.py        # Mesa de som: volume por assunto do engenheiro
│   ├── session_manager.py  # Gerenciamento de voltas, setores, ghosts e consumo
│   ├── lap_library.py      # Catálogo de voltas: índice leve, compressão, retenção
│   ├── config.py           # Preferências do usuário (config.json)
│   └── paths.py            # Resolução de diretórios (script e .exe)
├── providers/
│   ├── base.py             # Classe abstrata de provider
│   ├── assettocorsa.py     # Provider oficial AC1 via memória compartilhada (ctypes)
│   └── mock.py             # Simulador de telemetria para testes offline
├── ui/
│   ├── theme.py            # Design system, paleta de cores e fontes MoTeC i2
│   ├── main_window.py      # Janela principal (pilha de gráficos, métricas e histórico)
│   ├── sidebar_panel.py    # Coluna lateral esquerda (marcha, pedais, mapa, pneus)
│   └── components.py       # Widgets modulares (Cards, CustomPlot, AssistLED, etc.)
├── tests/                  # Cada arquivo roda sozinho e imprime o placar
│   ├── weekend_sim.py                 # Gerador de telemetria sintética (piloto e pista)
│   ├── test_race_weekend.py           # * Fim de semana inteiro: T1, T2, T3, Q e corrida
│   ├── test_assettocorsa_provider.py  # Unidade do provider do AC (layout dos structs)
│   ├── test_corner_analysis.py        # Análise curva a curva
│   ├── test_corner_bests.py           # Aprendizado do coach entre sessões
│   ├── test_driving_analysis.py       # Medidas de pilotagem
│   ├── test_lap_library.py            # Catálogo: índice, compressão, retenção
│   ├── test_lap_report.py             # Testes do motor de diagnóstico causal e relatórios
│   ├── test_live_coach.py             # Disciplina do coach: quando fala e quando cala
│   ├── test_mapa_smoke.py             # Fumaça da análise pós-sessão
│   ├── test_motec_exporter.py         # Testes de exportação MoTeC binária (.ld) e marcas (.ldx)
│   ├── test_race_engineer.py          # Regras do engenheiro de pista
│   ├── test_session_manager.py        # Persistência, ghosts, volta suja, troca de sessão
│   ├── test_ui_smoke.py               # Fumaça da interface gráfica
│   ├── test_voice_mix.py              # Mesa de som (classificação de assuntos e faders)
│   └── test_voice_queue.py            # Fila de voz (prioridade, preempção, validade)
├── track_maps/             # Mapeamento das curvas por pista
│   ├── README.md                      # Como escrever um mapa à mão
│   └── <pista>.json                   # Manual (versionado) ou <pista>.auto.json (detectado)
│
├── main.pyw                # > Ponto de entrada do dashboard
├── mapa.pyw                # > Tela de análise pós-sessão (offline)
├── ajustar_voz.pyw         # > Ajuste da voz: ritmo, tom e timbre, escolhidos de ouvido
├── test_voice.pyw          # > Bancada de voz: ouvir cada aviso sem entrar na pista
├── mock_game.py            # > Injeta telemetria na memória compartilhada do Windows,
│                           #   para exercitar o provider REAL sem o jogo aberto
├── reset.ps1               # Atalho: avisa se o AC está aberto e sobe o dashboard
├── build_exe.bat           # Empacotamento via PyInstaller
├── requirements.txt        # Dependências Python
│
├── config.json             # Preferências (criado sozinho, fora do versionamento)
├── telemetry_data/         # Catálogo de voltas — ver "Onde ficam os dados"
└── exportacoes/            # PNGs salvos manualmente ou a cada Personal Best
```

> `config.json`, `telemetry_data/`, `exportacoes/` e `track_maps/*.auto.json` estão no `.gitignore`: são de cada instalação, não do projeto.

---

## 🚀 Como Executar

### Com o Assetto Corsa
```bash
python main.pyw
```
Pode abrir antes ou depois do jogo. Fica em `AGUARDANDO O ASSETTO CORSA` até você entrar na pista, e **reconecta sozinho** se você sair para o menu, trocar de carro ou fechar e reabrir o jogo.

### Sem o jogo — simulador interno
```bash
python main.pyw --mock          # simulador interno
python main.pyw --no-mock       # força o provider real, ignorando o config.json
```
Também vale `APEXVIEW_MOCK=1` no ambiente ou `"mock_mode": true` no `config.json`.
Precedência: **argumento -> variável de ambiente -> `config.json`**.

### Análise pós-sessão (sem o jogo)
```bash
python mapa.pyw
```

### Bancada de voz
```bash
python test_voice.pyw
```
Monta estados de telemetria e voltas sintéticas de verdade, passa pelo `RaceEngineer` e deixa você **ouvir cada aviso** — inclusive testar prioridade e preempção — sem entrar na pista. É como se afere se a voz está agradável antes de levá-la para dentro do carro.

### Exercitar o provider REAL sem o jogo
```bash
python mock_game.py     # terminal A: escreve na memória compartilhada do Windows
python main.pyw         # terminal B: SEM --mock, para usar o provider de verdade
```
O `--mock` troca o provider por um simulador interno; o `mock_game.py` é diferente e vai mais fundo: ele grava nas estruturas nativas do AC (`Localcpmf_physics` e companhia), então o caminho exercitado é o **mesmo** que roda com o jogo aberto — leitura por `ctypes`, layout dos structs e tudo.

### Executável standalone
```bash
build_exe.bat
```

---

## ⌨️ Controles da Interface

### 🖥️ Dashboard Principal (`main.pyw`)

| Onde | O que faz |
|---|---|
| **REFERÊNCIA** (combo no topo) | Contra qual volta você está sendo medido — modos fixos ou qualquer volta gravada. |
| **VOLTA** (combo + as setas) | Qual volta os gráficos mostram: "Volta Atual (Ao Vivo)" ou uma já fechada. |
| **AO VIVO / ANÁLISE** | Indica e alterna entre acompanhar o carro e congelar para analisar. |
| **Barra de posição** (rodapé) | Arrastando, vira um scrubber: percorre a volta exibida com o cursor sincronizado nos quatro gráficos e no mapa. |
| **CURVAS** | Sombreia os limites de cada curva nos gráficos, numerados no de velocidade. |
| **EXPORTAR PNG** | Salva a tela de análise em `exportacoes/`. |
| **VOZ** (painel do engenheiro) | Desliga a fala **sem** apagar o texto do painel. |
| **ANALISAR** | Roda o balanço da volta exibida na hora, em qualquer modo. |
| **Modo** (painel do engenheiro) | Fim de volta / Ao vivo / Sob demanda. |

### 🗺️ Tela de Análise Pós-Sessão (`mapa.pyw`)

| Onde | O que faz |
|---|---|
| **RELATÓRIO** | Abre o visualizador analítico: pontos fortes, fracos, causas e ações com exportação `.md`/`.txt` e cópia para clipboard. |
| **MoTeC (.ld)** | Exporta a volta selecionada para arquivo binário nativo MoTeC i2 Pro (`.ld` + `.ldx`). |
| **CSV** | Exporta todos os pontos e canais da volta selecionada em formato de planilha `.csv`. |
| **📌 FIXAR** | Protege a volta selecionada contra a política de retenção e limpeza automática. |
| **APAGAR** | Remove a volta selecionada do catálogo e apaga o arquivo `.json.gz` do disco. |
| **ATUALIZAR** | Recarrega e sincroniza a árvore de pistas, carros e sessões diretamente do disco. |
| **Ctrl + Clique** | Seleciona até 4 voltas para sobreposição gráfica simultânea com curvas de delta. |
| **Distância / Tempo** | Alterna o eixo horizontal entre metros percorridos e tempo decorrido. |

---

## 🧪 Testes Automatizados

Cada arquivo é um script que roda sozinho e imprime o placar (não precisa de pytest). **554 verificações**, todas passando:

```bash
python tests/test_race_weekend.py         # ⭐ o fim de semana inteiro, ponta a ponta
python tests/test_live_coach.py           # disciplina do coach: quando fala e quando cala
python tests/test_corner_bests.py         # aprendizado do coach entre sessões
python tests/test_lap_library.py          # catálogo: índice, compressão, retenção
python tests/test_lap_report.py           # motor de diagnóstico causal e relatórios
python tests/test_motec_exporter.py       # exportação MoTeC binária (.ld) e marcas (.ldx)
python tests/test_session_manager.py      # persistência, ghosts, volta suja, troca de sessão
python tests/test_ui_smoke.py             # interface do dashboard
python tests/test_mapa_smoke.py           # interface da análise pós-sessão
python tests/test_assettocorsa_provider.py
python tests/test_corner_analysis.py
python tests/test_driving_analysis.py
python tests/test_race_engineer.py
python tests/test_voice_queue.py
```

Ou todos de uma vez, no PowerShell:
```powershell
Get-ChildItem tests/test_*.py | ForEach-Object { python $_.FullName }
```

Os testes de interface precisam de `PyQt5` e `pyqtgraph`; os demais rodam só com a biblioteca padrão. **Nenhum deles toca nos seus dados:** todos gravam num diretório temporário e o `telemetry_data/` de verdade fica intocado.

### 🗓️ O teste do fim de semana

[tests/test_race_weekend.py](tests/test_race_weekend.py) não é um teste de unidade: é a pergunta que interessa — *depois de um fim de semana de verdade, está tudo lá e faz sentido?* Ele simula, quadro a quadro e no formato exato que o provider do AC entrega:

| | O que roda | O que é verificado |
|---|---|---|
| **Sexta T1** | 6 voltas, piloto cru | O coach **aprende** onde ele perde e fala durante a volta; voltas de box marcadas e fora da referência |
| **Sexta T2** | 5 voltas, erros corrigidos | O tempo cai, o alvo de cada curva melhora, T1 e T2 viram sessões **distintas** sem reiniciar o app |
| **Sábado T3** | 4 voltas, pista mais quente | O ritmo continua evoluindo |
| **Sábado Q** | Saída + **volta lançada** + retorno | Só a lançada vira referência; o coach não dá veredito na classificação |
| **Domingo R** | 8 voltas com bandeira amarela, corte de pista, dano, penalidade, pneu superaquecido e última volta | O engenheiro avisa cada uma; volta suja **não** vira Personal Best; o coach quase cala a boca |
| **Depois** | — | As 26 voltas no catálogo, agrupadas nas 5 sessões; qualquer uma reabre com a telemetria inteira; a tela pós-sessão monta a árvore; exportação CSV; a retenção não come o PB; 1,3 MB no total |

O piloto sintético é parametrizável ([tests/weekend_sim.py](tests/weekend_sim.py)): dá para mandá-lo frear 24 metros cedo na Ferradura, chegar 7 km/h devagar no ápice do Pinheirinho e melhorar entre uma sessão e outra — que é como se testa um coach. O fim de semana inteiro roda em **~9 segundos**.

---

## 🩺 Solução de Problemas

| Sintoma | O que é |
|---|---|
| **Fica em `AGUARDANDO O ASSETTO CORSA`** | O app só conecta com o carro EM PISTA — no menu do jogo a memória compartilhada não está publicando. Entre numa sessão. |
| **O delta fica em zero** | Não há referência utilizável ainda. O delta precisa de uma volta **inteira** (linha a linha) e **limpa**: volta de saída de box, volta cortando a pista e volta iniciada com o app já aberto no meio não servem. Feche uma volta boa e ele aparece. |
| **A referência "Nenhuma" está selecionada** | Ela desativa o delta de propósito. Troque para "Automática". |
| **Não tem voz** | Falta o `pywin32` (`pip install pywin32`). O painel de texto funciona sem ele. Confira também o botão **VOZ**. |
| **A voz soa robótica** | O app prefere as vozes **OneCore** do Windows 10/11, bem mais naturais, mas o Windows só as enumera se estiverem instaladas em *Configurações → Hora e Idioma → Voz*. Instale a voz de português e reabra o app. |
| **O painel de curvas está vazio** | A pista não tem mapeamento e ainda não houve uma volta **inteira** para detectar as curvas por Força G. Depois da primeira volta completa ele aparece, e o mapa é gravado como `.auto.json`. |
| **As curvas estão numeradas errado** | O mapa automático não conhece os nomes. Renomeie `track_maps/<pista>.auto.json` para `<pista>.json`, edite os limites e nomes (veja [track_maps/README.md](track_maps/README.md)) — o manual sempre vence. |
| **O coach não fala nada** | Ele precisa: modo **Ao vivo** no painel, um mapa de curvas, e ter medido a curva o bastante para opinar (2 voltas sem histórico, 1 com). E ele cala de propósito no box, em replay, com o jogo pausado e entre a freada e a saída da curva. |
| **O coach comenta a volta errada** | Volta de box e volta com corte de pista são ignoradas de propósito. Se ele estiver falando de uma curva que não existe mais, apague `corner_bests.json` — provavelmente o mapa mudou. |
| **A interface engasga** | Suba `graph_redraw_every_n_frames` no `config.json` (5 → 10 dá ~6 fps de gráfico, e os números continuam a 60 Hz). |
| **Uma volta boa desapareceu** | A retenção guarda as 30 mais rápidas e as 200 mais recentes. Para blindar uma volta, fixe com o alfinete (📌) na tela de análise pós-sessão, ou ponha `"retention": {"enabled": false}` no `config.json`. |
| **Aparece `Unknown Track / Unknown Car`** | Nos primeiros quadros o bloco estático do AC ainda não foi lido. O app **descarta** voltas dessa janela de propósito, para não criar pasta lixo nem misturar carros. Passa em um segundo. |
| **Apareceu um arquivo `.corrupt`** | Um JSON foi truncado (jogo fechado no meio de uma gravação). O app isolou o arquivo e seguiu. Pode apagar — nada além daquela volta se perde. |

---

## 🚧 O que Ainda Falta / Planejado

> O que já saiu do "falta" está marcado e riscado, com o link para onde foi resolvido. O que continua aberto vem depois, com o motivo de ainda não ter sido feito.

- [x] ~~**Tela de Análise Pós-Sessão (`mapa.pyw`)**~~ — feita, e agora lendo o mesmo catálogo do dashboard.
- [x] ~~**Comparação de Telemetria de Múltiplas Voltas**~~ — até 4 voltas sobrepostas na tela de análise pós-sessão. Falta trazer isso para o dashboard ao vivo.
- [x] ~~**Exportação MoTeC i2 Pro (`.ld` / `.ldx`)**~~ — feita ([core/motec/](core/motec/)), no padrão da indústria com Pro Logging (`0xC81A4`) e marcas de volta/setores em XML.
- [x] ~~**Relatórios de Desempenho & Diagnóstico Causal**~~ — feito ([core/lap_report.py](core/lap_report.py)), com análise profunda de pontos positivos, gargalos, causas físicas e plano de ação em Markdown e Texto Puro.
- [x] ~~**Configurações Persistentes do Usuário (`config.json`)**~~ — feita ([core/config.py](core/config.py)).
- [x] ~~**Coaching em tempo real durante a volta**~~ — feito ([core/live_coach.py](core/live_coach.py)).
- [x] ~~**Separação por sessão dentro do fim de semana**~~ — feito, com detecção automática.
- [x] ~~**Aprendizado do coach que persiste entre sessões**~~ — feito ([core/corner_bests.py](core/corner_bests.py)).
**No coach — as ideias que ficaram na mesa:**

- [ ] **Insistir e reconhecer.** Se você erra a mesma curva três voltas seguidas, o coach repete a frase igual. Poderia mudar o tom e ficar mais específico; e, quando você corrige, reconhecer uma vez e parar de cobrar aquela curva.
- [ ] **Volta ideal por trecho.** Ele já sabe a melhor passagem de cada curva; falta somar e dizer *"juntando seus melhores trechos: 1:22.1"*.
- [ ] **Importar uma volta de referência de fora.** Hoje o coach só consegue te comparar com você mesmo — esse é o teto dele. Uma volta rápida importada (de um amigo, de um ghost baixado) destravaria o ganho absoluto. O seletor de referência já aceita qualquer volta do catálogo; falta só um jeito de pôr uma volta de fora lá dentro.

**No resto do app:**

- [ ] **Sobreposição de múltiplas voltas no dashboard ao vivo** — hoje o ao vivo compara com uma referência de cada vez. Na tela pós-sessão já dá para sobrepor quatro.
- [ ] **Atalhos de teclado** — tudo é feito no mouse.
- [ ] **Estratégia de pit stop** — o engenheiro avisa que o combustível não fecha a corrida, mas não sugere em que volta parar.
- [ ] **Degradação de pneu ao longo do stint** — o canal de desgaste é lido e mostrado, mas ninguém acompanha a tendência para dizer "seu ritmo cai a partir da volta 12".
- [ ] **Suporte a múltiplos monitores** — desacoplar o painel lateral e a pilha de gráficos em janelas independentes.
- [ ] **Steer lock dinâmico por carro** — ler o raio máximo de esterço dos arquivos de física do veículo, em vez de assumir um valor.

**Coisas que dependem de decisão de arquitetura:**

- [ ] **Spotter de tráfego** (*"carro por dentro"*, *"colando atrás"*) — **não dá com o que existe hoje**: a memória compartilhada do AC1 publica só o SEU carro. Os adversários só aparecem pela API de plugin Python que roda DENTRO do jogo, o que significaria criar e manter um componente novo, instalado na pasta do Assetto Corsa. O que dá para fazer sem isso é a sua posição na corrida e a bandeira azul.
- [ ] **Adaptador para Assetto Corsa Competizione (ACC)** — um provider para as estruturas de memória do ACC. O `TelemetryState` já isola o resto do app do simulador, então a mudança fica contida no provider. Bônus: o ACC publica `isValidLap`, que substituiria a detecção de volta suja por rodas fora usada no AC1.

**Empacotamento:**

- [ ] **Executável standalone (`.exe`)** — o [build_exe.bat](build_exe.bat) chama o PyInstaller e gera `dist/ApexView/`, mas **ainda não foi testado de ponta a ponta**. Falta pelo menos copiar `track_maps/` para junto do executável: com o app congelado, os diretórios são resolvidos ao lado do `.exe` (ver [core/paths.py](core/paths.py)), e sem essa pasta as pistas perdem o mapeamento manual das curvas.

---

## 📜 Licença

Projeto desenvolvido para fins de análise de telemetria, engenharia de dados e aprimoramento de pilotagem no Assetto Corsa. USO PESSOAL E EDUCACIONAL. Nenhuma afiliação oficial com a **Kunos Simulazioni**.
