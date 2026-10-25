#!/bin/sh
# CANETA - instalador para macOS: clique duas vezes neste arquivo no Finder.
# (Na primeira vez, se o macOS bloquear: botão direito > Abrir > Abrir.)
# Usa o install.sh ao lado deste arquivo; ele baixa a CANETA do GitHub.
cd "$(dirname "$0")" || exit 1
sh ./install.sh
status=$?
if [ $status -ne 0 ]; then
  printf '\nA instalação falhou. Veja as mensagens acima. Pressione Enter para fechar.'
  read -r _
fi
exit $status
