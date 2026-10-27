Você ajuda a fundamentar a introdução de um artigo em $out_lang com literatura aberta encontrada pelo autor.

Referências disponíveis (use SOMENTE estas chaves; não existe nenhuma outra). Para cada uma vem a base que existe: o resumo, trechos do texto completo, o conteúdo da página (para notícias, relatórios e declarações) ou "Só o título":
$references

Tarefa:
1. Insira citações [@chave] onde uma referência da lista apoia de fato uma afirmação já existente no texto.
2. Uma referência com "Só o título" pode ser citada quando o próprio título deixa claro que a obra trata exatamente daquilo que a frase afirma (por exemplo, a frase menciona o Manifesto de Leiden e a obra é o Manifesto de Leiden). Nunca use só o título para sustentar números, resultados ou conclusões específicas.
3. Se ajudar o argumento, acrescente no máximo $max_new frases curtas de contextualização, apoiadas EXCLUSIVAMENTE no resumo, nos trechos ou no conteúdo da página acima, sempre com a citação correspondente. Nunca crie frases novas a partir de "Só o título".
4. Não invente dados, números ou conclusões que não estejam nessas bases. Não use chaves fora da lista.
5. Preserve as citações que já existem e não remova conteúdo do autor.
6. Devolva a seção inteira, em Markdown e sem o título, no campo "texto"; explique em "notas" onde e por que citou, e em quais citações você se apoiou apenas no título.

<<<SEÇÃO
$section_text
SEÇÃO>>>
