# Como contribuir

A CANETA é feita em público. Erros, ideias e críticas são bem-vindos: "erro é estado, não pecado".

- **Problemas e sugestões:** abra uma *issue*. Diga o que aconteceu, o que esperava e, se puder, anexe um checkpoint `.caneta` sem dados sensíveis.
- **Código:** abra um *pull request* pequeno e focado, com testes. Antes de enviar, rode:

  ```bash
  uv sync
  uv run ruff check src tests scripts
  uv run pytest
  ```

- **Traduções:** os textos da interface ficam em `src/caneta/locales/{pt_BR,es,en}.json`, e os prompts em `src/caneta/prompts/*.md`.
- **Revistas:** o índice de revistas Diamond fica em `src/caneta/data/journals/`. Para incluir uma revista, ela precisa constar no DOAJ sem APC e sem outras taxas.
- **Princípios:** leia o [MANIFESTO](MANIFESTO.md). Mudanças que tornem a CANETA dependente de serviços fechados, ou que gerem dados bibliográficos com IA, não serão aceitas.

Toda contribuição substantiva recebe crédito pelo nome. O código é AGPL-3.0, e os textos são CC BY 4.0.
