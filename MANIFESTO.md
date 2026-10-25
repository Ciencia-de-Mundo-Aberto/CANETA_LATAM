# Manifesto da CANETA

**C.A.N.E.T.A.** — Central de Assistência e Navegação para Escrita de Textos Avançados
*(es: Central de Asistencia y Navegación para Escritura de Textos Avanzados)*

> Este manifesto estende o manifesto [*Livros de Mundo Aberto*](https://github.com/LucaswasTaken/Aprendizado-de-Mundo-Aberto-em-Petroleo/blob/main/livro/editorial/manifesto.md) para a escrita de artigos científicos, a inteligência artificial e a soberania latino-americana. Texto sob [CC BY 4.0](LICENSE-docs).

## Por que a CANETA existe

A América Latina forma pesquisadoras e pesquisadores capazes de mudar a sociedade, e paga duas vezes pelo que eles produzem.

- **O ciclo atual.** A pesquisa é financiada com dinheiro público. O artigo é escrito, avaliado e editado de graça por autores, revisores e editores. Os direitos são entregues a editoras comerciais, e o Estado paga de novo para ler o resultado ou para publicá-lo em acesso aberto.
- **O preço no Brasil.** Em 2026, a CAPES fechou acordos de três anos com Elsevier, Springer Nature e ACM. O investimento é de cerca de **US$ 215 milhões, aproximadamente R$ 1 bilhão** (2026-2028), para leitura e isenção de taxas de publicação.[^acordos]
- **Um terceiro pagamento.** Sobre esse mesmo acervo, chegou uma ferramenta de IA proprietária, o LeapSpace da Elsevier, oferecida pelo Portal de Periódicos CAPES.[^leapspace] O conhecimento é produzido de graça; paga-se para publicar, paga-se para ler e paga-se para usar a ferramenta que lê por você.
- **A alternativa já existe.** A América Latina construiu um caminho próprio: a **SciELO**, desde 1997-1998, a **Redalyc**, o **LA Referencia** e centenas de revistas universitárias **Diamond Open Access**, gratuitas para ler e gratuitas para publicar.

A CANETA é uma ferramenta aberta para escrever dentro desse caminho.

## O que a CANETA faz

A CANETA é um assistente que acompanha o autor do rascunho à submissão, **uma seção por vez**:

- recebe um artigo pronto ou uma ideia acompanhada do material de resultados;
- divide o trabalho em seções;
- submete cada seção a revisores independentes;
- só sugere passar adiante depois que o autor corrige a seção atual.

Toda citação e toda afirmação vêm de **fontes abertas encontradas** (SciELO, DOAJ, Redalyc, LA Referencia) ou do material do próprio autor. Nunca de referências inventadas por um modelo de linguagem.

## Princípios

1. **Dinheiro público, conhecimento público.** Devolver ao público a pesquisa que ele financiou é obrigação, não generosidade.
2. **Sem cerca nas duas pontas.** Nem taxa para publicar (APC), nem paywall para ler. A CANETA só sugere revistas **Diamond Open Access** e verifica esse status no DOAJ.
3. **Padronize os dados, não a aparência.** Citações são dados: DOI, autores, ano, revista. A CANETA as guarda como dados abertos (CSL-JSON) e formata qualquer estilo automaticamente. Vírgula e versalete não são ciência.
4. **A forma pertence ao autor.** A formatação é programável e reproduzível (pandoc), e nunca fica presa a um formato proprietário.
5. **Revisão é necessária; monopólio sobre ela, não.** Os revisores da CANETA apontam, citando o trecho exato, e **o autor decide** cada apontamento.
6. **Erro é estado, não pecado.** Cada versão de cada seção fica guardada e pode ser restaurada. Corrigir à vista de todos é parte do método.
7. **IA opcional, transparente e substituível.** É possível usar a CANETA sem nenhum modelo de linguagem. Quando há IA, o autor escolhe qual (paga, gratuita ou local), e cada uso fica registrado. A IA escreve e revisa; quem busca as fontes é a própria CANETA, para que se saiba exatamente o que o modelo viu.
8. **Soberania latino-americana.** Os dados ficam com o autor: a CANETA roda no computador dele. As fontes priorizadas são as da nossa região, e a interface fala português, espanhol e inglês.
9. **Abertura irreversível.** O código é **AGPL-3.0**: quem oferecer a CANETA como serviço precisa abrir suas modificações. Os textos são **CC BY 4.0**.

## Convite

Use, copie, traduza, critique e melhore. Problemas e propostas entram como *issues* e *pull requests*, e toda contribuição substantiva recebe crédito pelo nome. Para assinar este manifesto, abra um *pull request* acrescentando seu nome abaixo.

### Assinaturas

- *(abra um pull request para assinar)*

[^acordos]: CAPES/MEC, acordos transformativos 2026-2028 (Portaria CAPES nº 120/2024, alterada pela Portaria nº 224/2026). Veja, por exemplo, [Springer Nature: Transformative agreement for CAPES](https://www.springernature.com/gp/open-science/oa-agreements/brazil/capes) e [Revista Oeste: "Governo gasta R$ 1 bilhão para isentar taxas de publicações científicas"](https://revistaoeste.com/politica/governo-gasta-r-1-bilhao-para-isentar-taxas-de-publicacoes-cientificas/).
[^leapspace]: [IPEN/gov.br: "Plataforma LeapSpace de IA está disponível pela Capes"](https://www.gov.br/ipen/pt-br/biblioteca/noticias-da-biblioteca/plataforma-leapspace-de-ia-esta-disponivel-pela-capes).
