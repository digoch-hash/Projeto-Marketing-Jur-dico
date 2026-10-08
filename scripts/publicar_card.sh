#!/usr/bin/env bash
# Hospeda as imagens de um card no GitHub (ramo "cards-publicos", repositorio publico) e publica no Instagram.
# Uso:  scripts/publicar_card.sh PASTA_DO_CARD ID_DA_NORMA [--dry-run]
#   PASTA_DO_CARD: a pasta gerada por `python -m app.cli render-card`.
#   --dry-run: hospeda e confere o endereco, mas NAO publica.
# Precisa das variaveis INSTAGRAM_TOKEN, INSTAGRAM_USER_ID e (login do Facebook) INSTAGRAM_LOGIN=facebook.
set -euo pipefail

PACK="${1:?Informe a pasta do card.}"
ID="${2:?Informe o id da norma.}"
DRY="${3:-}"
PY="${PY:-python}"
BR="${CARDS_BRANCH:-cards-publicos}"
ORIGIN="${CARDS_ORIGIN:-$(git remote get-url origin)}"
RAW_BASE="${CARDS_RAW_BASE:-https://raw.githubusercontent.com/digoch-hash/Projeto-Marketing-Jur-dico/$BR}"
KEEP="${CARDS_KEEP:-}"    # opcional: manter so os N cards mais recentes no ramo (apagar pode ser barrado em modo automatico)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if git ls-remote --exit-code --heads "$ORIGIN" "$BR" >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch "$BR" "$ORIGIN" "$work"
else
  git init -q "$work"
  git -C "$work" checkout -q --orphan "$BR"
  git -C "$work" remote add origin "$ORIGIN"
fi
git -C "$work" config user.name "Radar HRBio"
git -C "$work" config user.email "radar-hrbio@users.noreply.github.com"

pasta="$(date -u +%Y%m%d%H%M%S)-$ID"
"$PY" -m app.cli publish-pack --pack-dir "$PACK" --jpg-dir "$work/$pasta" --base-url "x" --prepare-only >/dev/null

# por padrao NAO apaga nada: um push com remocao pode ser barrado em modo automatico e derrubaria a publicacao.
# Se quiser limitar o tamanho do ramo, defina CARDS_KEEP=N (so os N mais recentes).
if [ -n "$KEEP" ]; then
  (cd "$work" && ls -1d [0-9]*/ 2>/dev/null | sort | head -n "-$KEEP" | xargs -r rm -rf)
fi

git -C "$work" add -A
git -C "$work" commit -q -m "Card $ID"
git -C "$work" push -q origin "HEAD:refs/heads/$BR"

base="$RAW_BASE/$pasta"
echo "Imagens hospedadas em: $base" >&2
for i in $(seq 1 24); do
  if curl -fsS -o /dev/null "$base/slide_01.jpg" 2>/dev/null; then ok=1; break; fi
  sleep 5
done
if [ -z "${ok:-}" ]; then
  echo "As imagens não ficaram acessíveis em $base (2 min). Nada foi publicado." >&2
  exit 3
fi

if [ "$DRY" = "--dry-run" ]; then
  echo "Dry-run: imagens acessíveis; nada foi publicado." >&2
  exit 0
fi
exec "$PY" -m app.cli publish-pack --pack-dir "$PACK" --jpg-dir "$(mktemp -d)" --base-url "$base"
