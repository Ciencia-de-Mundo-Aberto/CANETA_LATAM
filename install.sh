#!/bin/sh
# CANETA - instalador para Linux (e macOS pelo terminal)
#   Na pasta de release:  sh install.sh
#   Pela internet:        curl -LsSf https://raw.githubusercontent.com/LucaswasTaken/CANETA_LATAM/main/install.sh | sh
# Instala o uv (que traz o próprio Python), baixa a CANETA do GitHub e abre o app.
# Opções: CANETA_SEMANTIC=1 (busca por significado); CANETA_OCR=1; CANETA_REF=main (ou outra tag/branch);
# CANETA_SOURCE=pasta local do código.
set -eu

REPO="https://github.com/LucaswasTaken/CANETA_LATAM"
REF="${CANETA_REF:-v0.1.4}"

printf '\n  CANETA - Central de Assistência e Navegação para Escrita de Textos Avançados\n\n'

if ! command -v uv >/dev/null 2>&1; then
  echo "[1/2] Instalando o uv (gerenciador de Python da Astral)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
else
  echo "[1/2] uv já instalado."
fi

echo "[2/2] Baixando e instalando a CANETA $REF do GitHub (1 a 3 minutos)..."
# Sempre pelo endereço, nunca pelo nome: no PyPI existe outro pacote chamado 'caneta'.
# Opcional: CANETA_OCR=1 inclui o OCR para PDFs digitalizados (+~80 MB). PDFs recentes não precisam.
EXTRA=""
if [ "${CANETA_OCR:-}" = "1" ]; then EXTRA="--with rapidocr-onnxruntime>=1.4.4"; fi
# Opcional: CANETA_SEMANTIC=1 inclui a busca por significado (+~550 MB), que melhora muito as sugestões
# de revista. Se ela já estava ativa, a atualização a mantém.
SEMANTIC="${CANETA_SEMANTIC:-}"
CFG="${CANETA_HOME:-$HOME/.caneta}/config.json"
if [ -f "$CFG" ] && grep -Eq '"semantic": *true' "$CFG"; then SEMANTIC=1; fi
if [ "$SEMANTIC" = "1" ]; then EXTRA="$EXTRA --with fastembed>=0.9"; fi
if [ -n "${CANETA_SOURCE:-}" ]; then
  uv tool install --python 3.12 --force $EXTRA "$CANETA_SOURCE"
else
  uv tool install --python 3.12 --force $EXTRA "caneta @ git+$REPO@$REF"
fi
uv tool update-shell >/dev/null 2>&1 || true

BIN="$(uv tool dir --bin)/caneta"
if [ "$SEMANTIC" = "1" ]; then
  echo "      Baixando o modelo da busca por significado (~0,5 GB, só na primeira vez)..."
  "$BIN" --enable-semantic || echo "      (não foi ativada; tente depois em Configurações)"
fi
printf '\n  Pronto! Para abrir depois, rode: caneta\n  (abra um novo terminal se o comando ainda não for encontrado)\n'
printf '  O terminal precisa ficar aberto enquanto você usa a CANETA.\n\n'
exec "$BIN"
