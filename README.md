# 🏎️ Claudio — Dashboard de Telemetria para Assetto Corsa

> ⚠️ **PROJETO EM DESENVOLVIMENTO** — Este projeto ainda está incompleto. Algumas funcionalidades podem estar ausentes, instáveis ou sujeitas a mudanças significativas sem aviso prévio. Contribuições e sugestões são bem-vindas!

---

## 📖 Sobre o Projeto

**Claudio** é um dashboard de telemetria em tempo real desenvolvido em **Python** para o simulador **Assetto Corsa 1**. A aplicação lê a **memória compartilhada** do jogo (`acpmf_physics`, `acpmf_graphics`, `acpmf_static`), processa as informações e as exibe em uma interface gráfica moderna construída com **PyQt5** e **PyQtGraph**.

> 💡 **Não é preciso configurar nada no jogo.** O Assetto Corsa publica a telemetria em memória compartilhada automaticamente — basta abrir o AC e entrar na pista. O dashboard conecta e reconecta sozinho.

O provider do **Automobilista 2** (UDP / pCars2) continua no repositório em `providers/automobilista2.py`, caso você queira voltar a usá-lo.

O objetivo é fornecer ao piloto uma ferramenta de análise de desempenho semelhante às usadas em equipes de motorsport real — permitindo comparar voltas, identificar onde o tempo é perdido e acompanhar o estado do carro em tempo real.

---

## ✅ Funcionalidades Implementadas

### Painel Lateral (Sidebar)
- **Marcha atual** em destaque com cor dinâmica (Neutro = verde, Ré = vermelho, Redline = vermelho)
- **Velocidade (km/h)** em tempo real
- **Barra de RPM** com gradiente de cor baseado no percentual do RPM máximo
- **Bargraphs verticais** de Acelerador e Freio (GAS / BRK)
- **Dados do carro:** Combustível restante (L) com alerta de tanque baixo, voltas estimadas, consumo médio, pressão do turbo e ângulo do volante
- **Temperaturas e pressões** dos 4 pneus individualmente (FL, FR, RL, RR) com desgaste **e temperaturas Interna / Meio / Externa da banda** (destaque laranja quando a diferença interna-externa passa de 8 °C — indício de câmber desalinhado)
- **Eletrônica:** ABS, TC, Pit Limiter, DRS, KERS e BOX, com **barras de intensidade real de intervenção** do ABS e do TC (o AC informa o quanto o sistema está atuando, não só ligado/desligado) e nível de **force feedback com aviso de clipping**
- **Seletor de Referência (Ghost):** Permite alternar entre Personal Best, Sessão Atual, Volta Ideal ou nenhum

### Faixa de Análise (embaixo dos gráficos)
- **Força G:** diagrama G-G (lateral × longitudinal) com rastro dos últimos ~1,5 s — mostra na hora se o envelope de aderência está sendo usado por inteiro
- **Freios:** temperatura dos 4 discos com faixa de trabalho colorida + distribuição de frenagem (*brake bias*)
- **Condições da pista:** temperatura ambiente e da pista, **aderência da pista** (`surfaceGrip` — pista verde × pista gomada) e vento com direção
- **Sessão:** tipo de sessão, posição, voltas, tempo restante, composto de pneu, danos e **bandeiras** (azul, amarela, preta, xadrez, penalidade)

### Área Principal — Pilha de Gráficos (estilo MoTeC i2)
- Quatro canais empilhados: **Velocidade, Acelerador, Freio e Motor (RPM)**, cada um com a curva da volta atual sobre a curva fantasma da referência
- **Eixo X único**, exibido só no gráfico de baixo — os quatro compartilham a mesma escala de tempo, como no i2
- **Coluna do eixo Y de largura fixa**, para os quatro gráficos ficarem alinhados na mesma vertical
- **Nome do canal + valor ao vivo** no canto superior esquerdo de cada gráfico, sobre a grade
- Linhas verticais de separação de **S1** e **S2** atualizadas conforme o ghost selecionado
- Cursor vermelho de posição temporal sincronizado em todos os gráficos
- Escala X automática baseada no tempo da melhor volta; escala Y dinâmica para velocidade e RPM
- As curvas são redesenhadas a ~12 Hz (`GRAPH_REDRAW_EVERY_N_FRAMES` em `ui/main_window.py`)
  enquanto os mostradores numéricos acompanham os 60 Hz da engine — redesenhar milhares de
  pontos 60 vezes por segundo travava a interface

### Métricas de Topo
- **Volta Atual:** Tempo da volta em andamento
- **Melhor Volta:** Melhor tempo válido da sessão (≥ 30s para ignorar saídas dos boxes)
- **Delta Geral:** Diferença em tempo real vs. referência selecionada (`+X.XXs` / `-X.XXs`)
- **Setores S1, S2, S3:** Tempos do setor atual + tempo da referência + delta individual por setor
- **Ref / Est:** Tempo da volta de referência e projeção estimada de conclusão da volta atual

### Sistema de Referência (Ghost)
- **Personal Best:** Melhor volta pessoal salva em disco (persiste entre sessões)
- **Sessão Atual:** Melhor volta da sessão em andamento (apenas na memória)
- **Volta Ideal Teórica (Theoretical Best):** Costura automática (*splicing*) dos melhores setores já rodados — forma uma volta impossível que serve como referência máxima
- **Live Delta** calculado por interpolação de distância percorrida, não por tempo bruto — muito mais preciso em pistas com variação de ritmo

### Histórico de Voltas
- Tabela ao vivo com todas as voltas completadas na sessão
- Exibe tempos de S1, S2, S3 e Total
- Delta vs. melhor volta da sessão em cada linha
- Destaque automático na volta mais rápida

### Persistência e Exportação
- Voltas e ghosts salvos em JSON organizados por `pista/carro/` dentro da pasta `telemetry_data/`
- Exportação manual de screenshot (`.png`) da análise completa pelo botão **"Exportar Análise (Imagem)"**
- Exportação automática de imagem a cada novo **Personal Best** concluído (configurável via `AUTO_EXPORT_ON_BEST_LAP`)

### Modo Mock (Teste Offline)
- `MockTelemetryProvider` interno para simular uma corrida sem precisar abrir o jogo
- Útil para testar a interface, ajustar gráficos e verificar lógica de setores/delta

---

## 🚧 O que Ainda Está Incompleto / Planejado

> Esta seção lista funcionalidades que **ainda não foram implementadas** ou que estão parcialmente prontas.

- [ ] **Mapa da pista** — visualização do traçado com posição do carro em tempo real (`mapa.py` em desenvolvimento)
- [ ] **Comparação lado a lado de múltiplas voltas** — sobreposição de mais de 2 voltas nos gráficos
- [ ] **Tela de análise pós-sessão** — revisão detalhada offline de voltas salvas
- [ ] **Suporte a múltiplos monitores** — janelas separadas para sidebar e gráficos
- [ ] **Configurações persistentes** — salvar preferências do usuário (referência padrão, tema, etc.)
- [ ] **Suporte ao Assetto Corsa Competizione (ACC)** — o ACC usa os mesmos nomes de blocos de memória, mas com structs diferentes; exigiria um provider próprio
- [ ] **Steer lock por carro** — o AC entrega o volante normalizado (-1 a 1); hoje o ângulo em graus usa uma constante (`STEER_LOCK_DEG`, padrão 240°) ajustável em `providers/assettocorsa.py`
- [x] ~~**Teste do provider**~~ — `tests/test_assettocorsa_provider.py` simula os blocos de memória do AC e valida os 54 campos lidos (rode com o jogo fechado)
- [ ] **Testes automatizados** — falta cobertura da UI e do `SessionManager`
- [ ] **Instalador / Executável** — distribuição como `.exe` para Windows ainda não disponível

---

## 📂 Estrutura do Projeto

```text
Claudio-main/
├── core/
│   ├── engine.py           # Thread a 60 Hz: captura estado e emite sinal Qt
│   ├── models.py           # TelemetryState — modelo padronizado de dados
│   └── session_manager.py  # Lógica de voltas, setores, splicing, ghost e consumo
├── providers/
│   ├── base.py             # Classe base abstrata (interface do provider)
│   ├── assettocorsa.py     # Provider do AC1 via memória compartilhada  ← em uso
│   ├── automobilista2.py   # Provider UDP do AMS2 (protocolo pCars2) — legado
│   └── mock.py             # Simulador interno para testes sem o jogo
├── ui/
│   ├── theme.py            # Paleta, fontes e painéis no estilo MoTeC i2
│   ├── main_window.py      # Janela principal: gráficos, métricas e histórico
│   ├── sidebar_panel.py    # Painel lateral com mostradores do carro
│   ├── bottom_strip.py     # Faixa de análise: força G, freios, pista e pneus
│   ├── components.py       # Todos os widgets reutilizáveis (Cards, Plots, etc.)
│   └── mapa.py             # [EM DESENVOLVIMENTO] Visualização do mapa da pista
├── tests/
│   └── test_assettocorsa_provider.py  # Simula a memória do AC e valida o provider
├── exportacoes/            # Screenshots PNG exportadas automaticamente
├── telemetry_data/         # Ghosts e histórico de voltas em JSON (por pista/carro)
├── main.pyw                # Ponto de entrada da aplicação
├── mock_game.py            # Emulador de pacotes UDP (só serve para o AMS2)
├── requirements.txt        # Dependências Python
├── reset.bat               # Inicia o dashboard (CMD)
└── reset.ps1               # Inicia o dashboard (PowerShell)
```

---

## 🛠️ Requisitos

- **Python 3.8** ou superior
- **Assetto Corsa 1** instalado (para uso com dados reais) — funciona com o launcher original e com o Content Manager
- Sistema operacional: **Windows** (a leitura de memória compartilhada usa a API do Windows)

### Dependências Python

```
PyQt5>=5.15.10
pyqtgraph>=0.13.7
```

---

## 📦 Instalação

**1. Clone ou baixe o repositório:**
```bash
git clone https://github.com/seu-usuario/claudio.git
cd claudio
```

**2. (Recomendado) Crie um ambiente virtual:**
```bash
python -m venv venv
venv\Scripts\activate
```

**3. Instale as dependências:**
```bash
pip install -r requirements.txt
```

---

## ⚙️ Configuração no Assetto Corsa

**Nenhuma.** 🎉

O Assetto Corsa 1 publica a telemetria em memória compartilhada sempre que está rodando — não existe porta UDP, IP ou opção de telemetria para habilitar. Só abra o jogo e entre na pista.

Detalhes técnicos, caso interesse:

| Bloco de memória | Conteúdo | Frequência |
|------------------|----------|------------|
| `Local\acpmf_physics` | Pedais, RPM, pneus, freios, força G, danos | ~333 Hz |
| `Local\acpmf_graphics` | Tempos, setores, posição, sessão, bandeiras | ~60 Hz |
| `Local\acpmf_static` | Carro, pista, RPM máx., tanque, comprimento | 1× por sessão |

O provider abre os blocos apenas para **leitura** (`OpenFileMapping`), então não interfere no jogo nem em outros apps de telemetria rodando ao mesmo tempo.

---

## 🚀 Como Executar

### ▶️ Modo Normal (com o jogo rodando)

```bash
python main.pyw
```

Pode abrir o dashboard antes ou depois do jogo — ele fica em "Aguardando o Assetto Corsa" e conecta sozinho quando você entra na pista. Se você voltar ao menu ou fechar o AC, ele volta a aguardar e reconecta na próxima sessão.

---

### 🧪 Modo Simulação (sem o jogo — teste offline)

Ative o modo mock no código: em `main.pyw`, altere:
```python
MOCK_MODE = True
```

O `MockTelemetryProvider` simula uma corrida completa (voltas, setores, pneus, freios, força G) para testar a interface sem abrir o jogo.

---

## 🧹 Scripts Auxiliares

Atalhos para iniciar o dashboard:

**PowerShell:**
```powershell
.\reset.ps1
```

**CMD:**
```cmd
reset.bat
```

---

## 🗂️ Como os Dados São Salvos

Os dados de telemetria são organizados automaticamente em:

```
telemetry_data/
└── NomeDaPista/
    └── NomeDoCarro/
        ├── best_lap_ghost.json       # Melhor volta pessoal (persiste entre sessões)
        ├── ideal_lap_ghost.json      # Volta ideal teórica (melhor setor de cada)
        └── 2026-07-24_19-30_1-44-527.json   # Cada volta completada
```

Cada arquivo JSON contém:
- **`metadata`:** pista, carro, tempo de volta, tempos de setores e timestamp
- **`telemetry`:** arrays de tempo, distância, velocidade, acelerador, freio, RPM e setor

---

## 🤝 Contribuindo

Este projeto está em desenvolvimento ativo e contribuições são bem-vindas!

Se quiser ajudar:
1. Faça um fork do repositório
2. Crie uma branch para sua feature: `git checkout -b feature/minha-feature`
3. Faça commit das suas alterações: `git commit -m 'Adiciona minha feature'`
4. Envie para o fork: `git push origin feature/minha-feature`
5. Abra um Pull Request

---

## 📜 Licença

Projeto desenvolvido para fins de análise de telemetria e aprimoramento de pilotagem no Assetto Corsa.  
Uso pessoal e educacional. Nenhuma afiliação com a **Kunos Simulazioni**.
