# CANETA

**C.A.N.E.T.A. — Central de Assistência e Navegação para Escrita de Textos Avançados**
*(es: Central de Asistencia y Navegación para Escritura de Textos Avanzados)*

Assistente **aberto** de escrita científica que trabalha **uma seção por vez**, com citações vindas de fontes abertas e foco em revistas **Diamond Open Access** da América Latina: gratuitas para ler **e** para publicar. É uma resposta pública a ferramentas proprietárias como o Elsevier LeapSpace, que cobram de volta pelo acesso ao conhecimento que a própria universidade produziu e revisou. Leia o [MANIFESTO](MANIFESTO.md).

[![CI](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/actions/workflows/ci.yml/badge.svg)](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/actions/workflows/ci.yml)
![versão](https://img.shields.io/badge/vers%C3%A3o-0.1.9-123c3a)
![licença](https://img.shields.io/badge/licen%C3%A7a-AGPL--3.0-f2c14e)

[Português](#português) · [Español](#español) · [English](#english)

> **Estado:** versão 0.1 (alfa). Funciona de ponta a ponta, mas a interface e os formatos ainda podem mudar. Relatos de problemas são muito bem-vindos em [Issues](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/issues).

---

## Português

### Instalação

Todos os instaladores baixam a CANETA **deste repositório**. Instalam também o [uv](https://docs.astral.sh/uv/), que traz o próprio Python, e criam o comando `caneta`. **Não é preciso** instalar Python, Node nem pandoc. Leva de 1 a 3 minutos e precisa de internet.

**Windows: o jeito mais fácil**

1. Baixe o **`CANETA-Instalador-Windows.exe`** na página de [Releases](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/releases/latest).
2. Dê dois cliques e depois clique em **Instalar e abrir**.
3. O instalador ainda não tem assinatura digital paga. Se o Windows avisar que "protegeu o computador", clique em **Mais informações → Executar assim mesmo**.

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.ps1 | iex
```

**macOS / Linux**

```bash
curl -LsSf https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.sh | sh
```

Os Releases também trazem `.zip` com `install.bat` (Windows), `Instalar CANETA.command` (macOS) e `install.sh` (Linux), para quem prefere clicar.

**Depois de instalar**
- A CANETA abre no navegador. Para abrir de novo, use **Menu Iniciar → CANETA** no Windows ou o comando `caneta`.
- A janela do servidor precisa ficar aberta enquanto você escreve. Feche-a para encerrar.
- Tudo roda **só no seu computador** (`127.0.0.1`). Os manuscritos ficam em `Documentos/CANETA`.

**Opções do instalador por linha de comando**

| Variável | Efeito |
|---|---|
| `CANETA_SEMANTIC=1` | Inclui a **busca por significado** (+≈550 MB, download único), que melhora muito as sugestões de revista para textos ensaísticos ou interdisciplinares. No `.exe`, é uma caixa de seleção. Se ela já estava ativa, as atualizações a mantêm. Também pode ser ativada depois, em Configurações. |
| `CANETA_OCR=1` | Inclui OCR para PDFs **digitalizados** (+≈80 MB). PDFs digitais comuns não precisam dele. No `.exe`, é uma caixa de seleção. |
| `CANETA_REF=main` | Instala outra tag ou branch. O padrão é a versão do instalador (`v0.1.9`). |

**Desinstalar**

```bash
uv tool uninstall caneta
```

### O que a CANETA faz

- **Dois começos.**
  - **Importar um artigo pronto:** `.docx`, `.odt`, `.md`, `.tex` ou o **`.zip` de um projeto LaTeX**, com vários arquivos `\input`/`\include` e `.bib`. O texto é dividido em seções. Título, autores e resumo são lidos do próprio documento, e as referências do `.bib` são importadas.
  - **Começar do zero:** você escreve uma tela por vez, com a ajuda do seu material (resultados, artigos e notas em `.zip`; PDFs são convertidos automaticamente).
- **Revisão seção por seção.**
  - Revisores independentes (editor, metodólogo, referências e, se você quiser, um adversário) apontam problemas **citando o trecho exato**. Apontamentos sem trecho literal são descartados.
  - Você aceita, edita, rejeita (com motivo) ou adia cada um.
- **Edições pontuais por pedido.** Você seleciona um trecho, diz o que quer e recebe a mudança como **diff**. Nada é reescrito sem você ver antes.
- **Citações abertas e verificáveis.**
  - Na **introdução**, a CANETA busca literatura Diamond Open Access na **SciELO**, no **DOAJ** e na **LA Referencia** e sugere citações.
  - Nos **resultados e na discussão**, ela baixa o **texto completo** de trabalhos relacionados para comparar resultados e confere os números no original.
  - A IA **nunca** gera dados bibliográficos. Citações inventadas são removidas automaticamente.
- **Escolha da revista.**
  - A CANETA sugere as 5 revistas mais próximas do seu texto, entre 118 revistas Diamond (com Qualis 2021-2024, citações e escopo). Também dá para escolher qualquer outra.
  - O template é baixado do site da própria revista.
  - Opcionalmente, a **busca por significado** (embeddings multilíngues locais, ≈0,5 GB, ativada em Configurações) melhora as sugestões entre português, espanhol e inglês.
- **Exportação.**
  - **Word:** usa o template da revista. Equações LaTeX viram equações nativas do Word, numeradas.
  - **LaTeX (`.zip`):** usa o template LaTeX da revista quando ela tem um, com `references.bib` e `\citep`/`\citet`.
  - Também exporta Markdown, PDF (se houver TeX instalado), BibTeX e CSL-JSON.
  - Os estilos APA, ABNT e Vancouver são aplicados automaticamente.
- **Autores e afiliações.** Nomes, afiliações, e-mail de correspondência e ORCID, recolhidos do LaTeX original ou do ORCID, entram no Word, no PDF e no LaTeX. Para revistas **duplo-cego**, o manuscrito sai anônimo, a CANETA gera uma folha de rosto separada e avisa onde o texto ainda identifica os autores.
- **Checkpoint.** Salve e retome o projeto inteiro num único arquivo `.caneta`, em qualquer computador.
- **Transparência.** Cada chamada de IA (provedor, modelo e hash do texto enviado) fica registrada no projeto.

### Escolha da IA, na instalação ou quando quiser

| Modo | Opções | Modelo recomendado |
|---|---|---|
| **Pago** | Claude CLI, Codex CLI (se você já usa), chave da API Anthropic, chave da API OpenAI | `claude-haiku-5-5`, `gpt-6-luna` |
| **Gratuito** | NVIDIA NIM (chave gratuita em [build.nvidia.com](https://build.nvidia.com); botão **Testar modelos** para os Nemotron), Codex CLI com conta gratuita do ChatGPT | `nvidia/nemotron-3.5-lightning-30b-a3b`, `gpt-6-luna` |
| **Local** | Ollama, se você tiver hardware | o que você instalar |
| **Sem IA** | A escrita é sua; a CANETA busca citações, sugere revistas, verifica a fundamentação e corrige a gramática (LanguageTool) | — |

A própria CANETA busca as fontes e as coloca no texto enviado ao modelo. Por isso **qualquer provedor** funciona do mesmo jeito, e sabemos exatamente o que o modelo viu.

**Privacidade:** com provedores na nuvem e o LanguageTool público, o texto da seção sai da sua máquina. Para trabalhos sensíveis, use o Ollama ou o modo sem IA.

### Estrutura do repositório

```
src/caneta/              aplicativo (FastAPI + htmx, roda localmente)
packages/latam-oa-mcp/   servidor MCP de busca em fontes abertas (SciELO, DOAJ, LA Referencia, Oasisbr)
install.ps1, install.sh  instaladores de um comando (baixam deste repositório)
packaging/               instalador gráfico do Windows (.exe) e lançadores .bat/.command
scripts/                 geração do release e dos dados de revistas
tests/                   testes (pytest)
```

O [`latam-oa-mcp`](packages/latam-oa-mcp) também funciona sozinho, como servidor MCP para Claude Code, Codex ou outro agente.

### Para desenvolver

```bash
git clone https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM && cd CANETA_LATAM
uv sync
uv run caneta
uv run pytest
uv run ruff check src tests scripts packaging
```

Para gerar os instaladores (o `.exe` só pode ser gerado no Windows), use o comando abaixo. Antes de compartilhar, publique a tag `v<versão>`, porque é dela que os instaladores baixam.

```bash
uv run python scripts/build_release.py
```

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Para citar a CANETA, use o [CITATION.cff](CITATION.cff).

### Licenças

- **Código:** [AGPL-3.0](LICENSE). Quem oferecer a CANETA como serviço precisa abrir suas modificações.
- **Textos** (manifesto, documentação): [CC BY 4.0](LICENSE-docs).
- **Estilos CSL:** CC BY-SA 3.0.
- **Bibliotecas web incluídas:** licenças em [`src/caneta/web/static/vendor/LICENSES.md`](src/caneta/web/static/vendor/LICENSES.md).
- Os **templates das revistas não são redistribuídos.** São baixados do site de cada revista quando você a escolhe.

---

## Español

CANETA es un asistente **abierto** de escritura científica, **sección por sección**, enfocado en revistas de **Acceso Abierto Diamante** de América Latina.

- Importa un artículo (`.docx`, `.odt`, `.md`, `.tex` o el `.zip` de un proyecto LaTeX) o empieza desde cero.
- Cada sección se revisa de forma independiente. Los revisores citan el fragmento exacto y tú decides cada observación.
- Las citas vienen de **SciELO**, **DOAJ** y **LA Referencia**; la IA nunca las inventa.
- Sugiere las 5 revistas más cercanas a tu texto y exporta a Word o LaTeX con su plantilla.
- La IA es opcional: de pago (Claude, Codex), gratuita (NVIDIA NIM, Codex gratuito), local (Ollama) o **sin IA**.

**Instalación:**
- **Windows:** descarga `CANETA-Instalador-Windows.exe` en [Releases](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/releases/latest), o ejecuta `irm https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.ps1 | iex`.
- **macOS y Linux:** `curl -LsSf https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.sh | sh`.
- Luego ejecuta `caneta`.

## English

CANETA is an **open** scientific writing assistant that works **one section at a time**, focused on Latin American **Diamond Open Access** journals.

- Import a paper (`.docx`, `.odt`, `.md`, `.tex` or a LaTeX project `.zip`) or start from scratch.
- Each section is reviewed independently. Reviewers quote the exact passage and you decide each finding.
- Citations come from **SciELO**, **DOAJ** and **LA Referencia** and are never invented by the AI.
- It suggests the 5 closest journals and exports to Word or LaTeX with the journal's template.
- AI is optional: paid (Claude, Codex), free (NVIDIA NIM, free Codex), local (Ollama) or **no AI**.

**Install:**
- **Windows:** download `CANETA-Instalador-Windows.exe` from [Releases](https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/releases/latest), or run `irm https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.ps1 | iex`.
- **macOS and Linux:** `curl -LsSf https://raw.githubusercontent.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM/main/install.sh | sh`.
- Then run `caneta`.
