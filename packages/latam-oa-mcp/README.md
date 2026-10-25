# latam-oa-mcp

Servidor MCP para encontrar, filtrar e baixar artigos **Diamond Open Access** (sem APC e sem outras taxas para o autor), com foco na América Latina. Os artigos baixados são convertidos para o **Open Knowledge Format (OKF v0.2)**, que facilita a leitura por agentes.

Nenhuma API exige chave.

## Dois caminhos

| | Ferramenta | O que faz | Tempo |
|---|---|---|---|
| **1. Buscar** | `search_papers` | Busca nas revistas SciELO (todas as coleções) e no DOAJ (mundo todo). Traz **até 20 artigos Diamond, os mais recentes entre os mais relevantes**, e salva **metadados + resumo** de cada um como OKF. Não baixa PDF. | ~20-60 s |
| **2. Ler inteiro** | `fetch_full_papers` | Para os artigos **escolhidos** (pelo `ref` de cada resultado), baixa o PDF e converte o artigo inteiro, atualizando no mesmo arquivo OKF a página que o caminho 1 criou. Até 20 por chamada. | ~5-20 s por artigo |

Fluxo típico de um agente:

1. `search_papers("hidroides OR hydroids OR Hydrozoa")` retorna 20 artigos com `ref`, título, ano, revista e resumo, já gravados em `<biblioteca>/hidroides/`.
2. O agente (ou o usuário) escolhe os que interessam.
3. `fetch_full_papers(papers=["10.11606/1807-0205/2021.61.06", ...], bundle="hidroides")` acrescenta o texto completo desses.
4. O agente lê `index.md`, depois `papers/index.md`, e abre cada artigo.

## Todas as ferramentas

| Ferramenta | Fonte | O que faz |
|---|---|---|
| `search_papers` | SciELO + DOAJ | **Caminho 1:** busca e salva metadados e resumo como OKF (até 20). |
| `fetch_full_papers` | Páginas das revistas | **Caminho 2:** texto completo dos artigos selecionados para o OKF. OCR opcional (`ocr=True`) para PDFs digitalizados. |
| `list_okf_bundles` | Local | Lista os bundles OKF. |
| `scielo_search` | SciELO + DOAJ + Crossref | Busca só nas revistas SciELO. Até 20 resultados, **sem salvar nada**. |
| `doaj_search_articles` | DOAJ | Busca no DOAJ. Até 20 resultados, **sem salvar nada**. |
| `search_lareferencia` / `search_oasisbr` | LA Referencia / Oasisbr | Teses, dissertações e artigos de repositórios. Até 20 resultados. Sem filtro Diamond, porque os registros não trazem ISSN. |
| `get_repository_record` | LA Referencia / Oasisbr | Traz o registro completo. |
| `scielo_find_journals` / `scielo_list_collections` / `scielo_get_journal` / `scielo_list_articles` / `scielo_get_article` | SciELO ArticleMeta | Traz o catálogo, as revistas e os artigos pelo PID. |
| `doaj_search_journals` / `doaj_check_journal` | DOAJ | Busca revistas e diz se um ISSN é Diamond. |
| `find_journal_templates` | Local | Busca templates de submissão em `Documents\journal_templates`. |

### Ordenação

`sort="recent"` é o padrão. O MCP pega os 60 resultados mais relevantes e, entre eles, devolve os 20 mais recentes. Assim a busca prioriza o que é atual sem trazer artigos que só citam o termo de passagem. Para ordenar só por relevância, use `sort="relevance"`.

### Filtro Diamond

O status vem do DOAJ: `has_apc = false` e `has_other_charges = false`. Fica em cache por 30 dias em `~/.cache/latam-oa-mcp/diamond_status.json`. Com `diamond_only=True`, que é o padrão:
- **Revista que cobra APC ou outra taxa:** é excluída (`excluded.not_diamond`).
- **Revista fora do DOAJ:** é excluída (`excluded.not_in_doaj`), porque o status não pode ser confirmado.

Para incluir tudo, passe `diamond_only=False`.

### Busca na SciELO

A SciELO não tem API pública de busca: o search.scielo.org fica atrás de um desafio anti-bot, que o MCP não tenta contornar. O `scielo_search` e o `search_papers` então:
1. Monta o catálogo de todas as revistas SciELO a partir da ArticleMeta, com cache de 30 dias.
2. Busca no DOAJ (títulos, resumos e palavras-chave) e no Crossref (títulos e autores), restrito a essas ISSNs.
3. Junta por DOI ou título (inclusive títulos bilíngues), descarta os resultados sem os termos e aplica o filtro Diamond.

Use `OR` entre sinônimos e traduções. Uma busca em todas as coleções leva de 20 a 60 segundos.

## Bundle OKF gerado

Segue a [especificação OKF v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md):

```
<LATAM_OA_LIBRARY>/<bundle>/          (padrão: ~/Documents/diamond_papers_okf/<bundle>)
  index.md                            okf_version: "0.2"; sumário
  log.md                              histórico de buscas e downloads (mais recente primeiro)
  papers/index.md                     lista com título, ano e resumo de uma linha
  papers/<slug>.md                    type: Scientific Article
  journals/<issn>.md                  type: Journal (evidência Diamond do DOAJ)
  references/papers/<slug>.pdf        PDF original
```

Cada artigo tem:
- **Frontmatter:** `title`, `description` (primeira frase do resumo), `resource` (link do DOI), `tags` (palavras-chave), `authors`, `year`, `journal`, `issn`, `doi`, `license`, `open_access: diamond`, `fulltext` (`pdf-layout`, `pdf-plain-text`, `pdf-ocr`, `pdf-not-converted` ou `abstract-only`), `status`, `generated`, e `sources` (PDF local, PDF do editor, página do artigo e registro da revista).
- **Corpo:** `# Abstract` e `# Full text`.

O campo `fulltext` indica o estado da página:

| Valor | Significado |
|---|---|
| `not-requested` | Caminho 1: só metadados e resumo. A página explica como pedir o texto completo. |
| `pdf-layout` | Texto completo em Markdown estruturado (caso normal). |
| `pdf-plain-text` | Texto simples, usado quando a conversão estruturada passou do tempo limite. |
| `pdf-ocr` | PDF digitalizado convertido por OCR (só com `ocr=True`). Pode ter erros de reconhecimento. |
| `pdf-not-converted` | O PDF foi baixado, mas não tinha texto extraível (digitalizado, sem OCR). |
| `abstract-only` | O caminho 2 não achou o PDF; ficaram só os metadados e o resumo. |

O PDF é localizado pela página do editor: a meta tag `citation_pdf_url` (padrão em SciELO e OJS) e os links `/article/download/` do OJS. Se não encontrar, tenta os links de texto completo do DOAJ. O caminho 2 nunca duplica um artigo: ele encontra pelo DOI a página criada pelo caminho 1 e a atualiza. O caminho 1 nunca apaga um texto completo já baixado.

A conversão PDF→Markdown usa o [PyMuPDF](https://pymupdf.readthedocs.io) 2.x (pymupdf4llm). Ela roda num subprocesso, porque a biblioteca escreve no stdout, e tem um limite de 240 s por PDF. O OCR usa o RapidOCR, leva cerca de 10-15 s por página e fica limitado às primeiras 40 páginas. **Licença:** o PyMuPDF é AGPL-3.0. Para uso local isso não importa; para redistribuir o servidor, avalie a licença.

## Instalação

Requer [uv](https://docs.astral.sh/uv/) e Python 3.11 ou mais recente.

```bash
cd C:\Users\lucas\Documents\latam-oa-mcp
uv sync
```

### Claude Code

```bash
claude mcp add latam-oa --scope user -- uv --directory C:\Users\lucas\Documents\latam-oa-mcp run latam-oa-mcp
```

### Claude Desktop / outros clientes (JSON)

```json
{
  "mcpServers": {
    "latam-oa": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\lucas\\Documents\\latam-oa-mcp", "run", "latam-oa-mcp"],
      "env": {
        "LATAM_OA_LIBRARY": "C:\\Users\\lucas\\Documents\\diamond_papers_okf",
        "LATAM_OA_TEMPLATES_DIR": "C:\\Users\\lucas\\Documents\\journal_templates"
      }
    }
  }
}
```

Variáveis de ambiente opcionais:
- `LATAM_OA_LIBRARY`: onde ficam os bundles OKF.
- `LATAM_OA_CACHE_DIR`: a pasta dos caches.
- `LATAM_OA_TEMPLATES_DIR`: a pasta dos templates de submissão.
- `CROSSREF_MAILTO`: um e-mail que coloca as requisições no "polite pool" do Crossref, que é mais rápido.

## Teste

O teste sobe o servidor via stdio e chama todas as ferramentas contra as APIs reais. Ele também roda os dois caminhos num bundle temporário: confere que são 20 resultados, em ordem do mais recente para o mais antigo, todos Diamond, que a página é atualizada sem duplicar e que o bundle é conforme ao OKF (§11 da especificação).

```bash
uv run python tests/test_server.py
```

## Limitações

- **Cobertura do `scielo_search`:** só encontra artigos que estão no DOAJ ou que têm DOI no Crossref.
- **Revistas fora do DOAJ:** ficam de fora no modo Diamond, mesmo que sejam gratuitas. O DOAJ também pode estar desatualizado; confirme a política de taxas no site da revista.
- **Download:** alguns editores bloqueiam acesso automatizado ou não publicam PDF. Nesses casos o artigo fica como `abstract-only`.
- **PDFs digitalizados:** sem `ocr=True`, ficam como `pdf-not-converted`. Com OCR, o texto é legível, mas às vezes junta palavras ou perde acentos.
- **Redalyc:** não está incluída, porque não há API pública ativa confirmada. Os artigos dela aparecem no DOAJ e na LA Referencia.
