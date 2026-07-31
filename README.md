# 🏎️ ApexView — Dashboard de Telemetria para Assetto Corsa (MoTeC i2 Style)

> ⚠️ **PROJETO EM DESENVOLVIMENTO ATIVO** — Dashboard profissional de telemetria em tempo real para Assetto Corsa 1, inspirado nos layouts de engenharia de dados do **MoTeC i2 Pro**.

---

## 📖 Sobre o Projeto

**ApexView** (AssettoCorsa-Telemetry) é uma ferramenta de telemetria e análise de desempenho em tempo real desenvolvida em **Python**, **PyQt5** e **PyQtGraph** para o **Assetto Corsa 1**. A aplicação acessa diretamente a **memória compartilhada** do jogo (`acpmf_physics`, `acpmf_graphics`, `acpmf_static`), processando telemetria a 60 Hz sem a necessidade de plugins externos ou configurações complexas de porta UDP.

> 💡 **Conexão Zero-Config:** Basta abrir o dashboard e entrar na pista. O sistema detecta automaticamente o jogo, conecta, sincroniza dados e reconecta se você reiniciar a sessão ou trocar de carro/pista.

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
- **Seletor de Referência:**
  - **Personal Best:** Melhor volta salva em disco para a combinação de pista/carro.
  - **Sessão Atual:** Melhor volta da sessão em memória.
  - **Volta Ideal Teórica (Theoretical Best):** Combinação dos melhores setores individuais (S1, S2, S3) da sessão.
  - **Desativado:** Limpa instantaneamente as curvas fantasmas dos gráficos e do mapa.
- **Métricas Superiores:**
  - **Volta Atual:** Cronômetro em tempo real.
  - **Melhor Volta:** Tempo da volta mais rápida válida.
  - **Delta Geral:** Diferença contínua em tempo real por interpolação de distância.
  - **Cards de Setores (S1, S2, S3):** Tempos atuais, tempos de referência e deltas individuais por setor.
  - **Ref / Est:** Tempo da referência e projeção estimada de conclusão de volta.

---

### 🎧 Engenheiro de Pista (análise por regras + voz)
- **Diagnóstico, não só número:** lê as métricas curva a curva e diz o que fazer. Ex.: *"Ferradura: perdeu 0.42 segundos, freou 20 metros antes. Atrase a freada"*.
- **100% local:** regras sobre a telemetria, sem serviço externo e sem modelo de linguagem — custo zero, resposta instantânea e número sempre exato (nunca inventado).
- **Seletor de modo**, no próprio painel:
  - **Fim de volta** — o balanço da volta que acabou (padrão).
  - **Ao vivo** — avisos com o carro na pista: roda travando, TC cortando, pneu superaquecido, bandeira, penalidade, combustível.
  - **Sob demanda** — nada aparece sem você clicar em **ANALISAR**.
- **Texto e voz:** painel com histórico colorido por severidade (crítico / atenção / info) e fala pelo SAPI do Windows. O botão **VOZ** desliga a fala sem apagar o texto.
- **Escolha automática da melhor voz:** prioriza as vozes **OneCore** do Windows 10/11 (`Microsoft Daniel` / `Microsoft Maria`), muito mais naturais que as `... Desktop` do SAPI clássico — que são as únicas que o Windows enumera por padrão. Em português na frente de qualquer outro idioma. Para preferir voz feminina, troque `PREFERRED_VOICE_NAME` em [core/voice.py](core/voice.py).
- **Fala em português de verdade:** o número dito usa vírgula decimal (`0,42`), senão o sintetizador lê "zero ponto quatro dois". O painel mantém o ponto, como o resto do app.
- **Não metralha o piloto:** cada regra tem tempo de espera próprio, há intervalo mínimo entre falas, e só o essencial vai para a voz (o crítico + a curva onde mais se perdeu). O resto fica no painel.
- **O que ele analisa:** perda de tempo por curva e a causa (ponto de frenagem, $V_{min}$, ponto de retomada), vício de ABS/TC ao longo da volta com a curva do pior ponto, câmber pela banda de rodagem, autonomia de combustível vs. voltas restantes, consistência entre as últimas voltas, temperatura de pneus e freios, clipping de force feedback.
- **Fica calado quando está tudo bem** — é o comportamento mais testado da funcionalidade.

---

### 🔀 Análise Curva a Curva (Turn-by-Turn)
- **Painel dedicado no rodapé:** uma linha por curva da pista, com o valor medido na volta analisada e o delta contra a volta de referência selecionada. A curva onde mais tempo foi perdido fica destacada em vermelho.
- **Métricas por curva:**
  - **Ponto de Frenagem** — metro em que o freio sai de ~0% e passa de 10%.
  - **Velocidade Mínima ($V_{min}$)** — menor velocidade no ápice, e o delta contra a referência.
  - **Ponto de Retomada** — metro em que o acelerador volta a 100%.
  - **Delta da Curva ($\Delta t$)** — tempo ganho/perdido **só naquele trecho**, medido por interpolação de tempo nos limites da curva.
- **Mapeamento por pista em JSON:** arquivos em `track_maps/`, com os limites em posição relativa (0.0–1.0) ou em metros. Veja `track_maps/README.md`.
- **Fallback automático:** pista sem mapeamento tem as curvas detectadas por Força G lateral ($|G_{lat}| > 0.4$ g, com histerese e fusão de esses). O resultado é gravado como `*.auto.json`, então a numeração não muda de volta para volta — e você pode editar o arquivo, remover o `.auto` e ele passa a ser o mapeamento manual (que sempre vence).
- **Voltas antigas também funcionam:** ghosts gravados antes do canal `g_lat` têm a Força G lateral reconstruída pela curvatura do traçado ($a_{lat} = v^2 \kappa$).
- **Destaque nos gráficos:** botão **CURVAS** sombreia os limites de cada curva nos quatro gráficos da pilha, numerados no gráfico de velocidade.

---

### 📋 Histórico, Persistência e Exportação
- **Tabela de Histórico de Voltas:** Lista completa das voltas da sessão com S1, S2, S3, tempo total e $\Delta$ Best, destacando automaticamente a volta mais rápida.
- **Gravação Automática em JSON:** Estrutura organizada por `telemetry_data/NomeDaPista/NomeDoCarro/`.
- **Exportação de Imagens (PNG):**
  - Botão de exportação manual da tela de análise em imagem HD.
  - Exportação automática ao registrar um novo Personal Best (`AUTO_EXPORT_ON_BEST_LAP`).

---

### 🧪 Modo Mock (Simulação Offline)
- **`MockTelemetryProvider`:** Permite executar o aplicativo sem o jogo rodando, gerando dados de telemetria simulados para testar interface, gráficos e comportamentos.

---

## 🚧 O que Ainda Falta / Planejado

> Esta seção lista funcionalidades planejadas ou em fase de prototipagem que **ainda não estão integradas na versão principal**:

- [ ] **Tela de Análise Pós-Sessão Pós-Corrida (`mapa.pyw`)** — Aplicação secundária para carregar arquivos JSON gravados e analisar sessões salvas offline com mapas interativos.
- [ ] **Comparação de Telemetria de 3+ Voltas Simultâneas** — Permitir a sobreposição de múltiplas voltas nos mesmos gráficos ao mesmo tempo.
- [ ] **Suporte a Múltiplos Monitores** — Desacoplamento do painel lateral e da pilha de gráficos em janelas independentes.
- [ ] **Configurações Persistentes do Usuário (`config.json`)** — Salvar preferências do usuário (referência padrão ao abrir, atalhos de teclado).
- [ ] **Adaptador para Assetto Corsa Competizione (ACC)** — Implementação de um provider específico para as estruturas de memória compartilhada do ACC.
- [ ] **Steer Lock Dinâmico por Carro** — Leitura automática do raio máximo de esterço a partir dos arquivos de física do veículo.
- [ ] **Compilação em Executável Standalone (`.exe`)** — Empacotamento via PyInstaller para uso direto no Windows sem necessidade de Python instalado.

---

## 📂 Estrutura do Projeto

```text
AssettoCorsa-Telemetry/
├── core/
│   ├── engine.py           # Thread a 60 Hz: leitura e emissão de sinais Qt
│   ├── models.py           # TelemetryState — estrutura de dados padronizada
│   ├── corner_analysis.py  # Análise Curva a Curva: mapas, detecção por G e métricas
│   ├── race_engineer.py    # Engenheiro de pista: regras de diagnóstico e conselho
│   ├── voice.py            # Voz do engenheiro (SAPI do Windows, opcional)
│   ├── session_manager.py  # Gerenciamento de voltas, setores, ghosts e consumo
│   └── storage.py          # Utilitários de gravação e leitura de dados
├── providers/
│   ├── base.py             # Classe abstrata de provider
│   ├── assettocorsa.py     # Provider oficial AC1 via memória compartilhada (ctypes)
│   └── mock.py             # Simulador de telemetria para testes offline
├── ui/
│   ├── theme.py            # Design system, paleta de cores e fontes MoTeC i2
│   ├── main_window.py      # Janela principal (pilha de gráficos, métricas e histórico)
│   ├── sidebar_panel.py    # Coluna lateral esquerda (marcha, pedais, mapa, pneus)
│   └── components.py       # Widgets modulares (Cards, CustomPlot, AssistLED, etc.)
├── tests/
│   ├── test_assettocorsa_provider.py  # Testes de unidade do provider do AC
│   ├── test_corner_analysis.py        # Testes da análise curva a curva
│   ├── test_race_engineer.py          # Testes das regras do engenheiro de pista
│   ├── test_session_manager.py        # Testes de persistência e validação de ghosts
│   └── test_ui_smoke.py               # Testes de fumaça da interface gráfica
├── track_maps/             # Mapeamento das curvas por pista (manual e detectado)
├── exportacoes/            # Screenshots PNG salvas manualmente ou via Best Lap
├── telemetry_data/         # Ghosts e voltas gravadas em JSON (por Pista/Carro)
├── main.pyw                # Ponto de entrada do aplicativo
├── mapa.pyw                # [Em desenvolvimento] Tela pós-sessão offline
├── mock_game.py            # Utilitário para injetar telemetria na memória compartilhada
└── requirements.txt        # Dependências Python
```

---

## 🛠️ Pré-Requisitos

- **Sistema Operacional:** Windows (leitura de memória compartilhada usando Win32 API / `ctypes`)
- **Python:** 3.8 ou superior
- **Simulador:** Assetto Corsa 1 (funciona com launcher original ou Content Manager)

### Dependências Python
```bash
pip install -r requirements.txt
```
* `PyQt5 >= 5.15.10`
* `pyqtgraph >= 0.13.7`

---

## 🚀 Como Executar

### ▶️ Modo Normal (com Assetto Corsa)
```bash
python main.pyw
```
O aplicativo pode ser aberto antes ou depois de iniciar o jogo. Ele permanecerá em `AGUARDANDO O ASSETTO CORSA` até que você entre na pista.

### 🧪 Modo Simulação (Offline)
Para rodar sem o jogo aberto, ative o modo mock em `main.pyw`:
```python
MOCK_MODE = True
```
Em seguida execute:
```bash
python main.pyw
```

---

## 🧪 Testes Automatizados

Para rodar a suíte completa de testes unitários e de interface:
```bash
python tests/test_ui_smoke.py
python tests/test_session_manager.py
python tests/test_assettocorsa_provider.py
python tests/test_corner_analysis.py
python tests/test_race_engineer.py
```

---

## 📜 Licença

Projeto desenvolvido para fins de análise de telemetria, engenharia de dados e aprimoramento de pilotagem no Assetto Corsa. USO PESSOAL E EDUCACIONAL. Nenhuma afiliação oficial com a **Kunos Simulazioni**.
