#!/usr/bin/env bash
# Sincroniza uma conta conectada, em segundo plano.
#
# Existe porque a alternativa — exportar um token numa aba, lembrar de usá-lo na
# mesma aba antes de expirar em 30 minutos, e não fechar o terminal durante um
# backfill de 20 minutos — falha de três formas diferentes e nenhuma delas dá
# mensagem clara: a requisição sai sem autenticação e volta "não autorizado".
#
#   ./scripts/sincronizar.sh            # conta 1, sincronização completa
#   ./scripts/sincronizar.sh 2          # conta 2
#   ./scripts/sincronizar.sh 1 rapida   # só pedidos, sem anúncios/perguntas
set -euo pipefail

CONTA="${1:-1}"
MODO="${2:-completa}"
API="${API_URL:-http://localhost:8000}"
EMAIL="${ADMIN_EMAIL:-admin@marketplacehub.com.br}"
SENHA="${ADMIN_PASSWORD:-admin123}"
SAIDA="${SAIDA:-/tmp/sync-conta-$CONTA.log}"

completo=true
[ "$MODO" = "rapida" ] && completo=false

echo "→ autenticando em $API"
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"senha\":\"$SENHA\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)

if [ -z "$TOKEN" ]; then
  echo "✗ login falhou. Verifique ADMIN_EMAIL e ADMIN_PASSWORD no .env," >&2
  echo "  e se a API responde:  curl $API/health" >&2
  exit 1
fi

echo "→ sincronizando conta $CONTA (completa=$completo)"
echo "  saída: $SAIDA"

# `nohup` porque o backfill roda dentro da requisição e pode levar dezenas de
# minutos: sem ele, fechar o terminal cancela a importação no meio.
nohup curl -s -X POST "$API/api/v1/accounts/$CONTA/sync?completo=$completo" \
  -H "Authorization: Bearer $TOKEN" > "$SAIDA" 2>&1 &

echo "✓ rodando em segundo plano (pid $!)"
echo
echo "  acompanhar:  docker compose exec db psql -U marketplace -d marketplace_hub -c 'select count(*) from orders;'"
echo "  resultado:   cat $SAIDA | python3 -m json.tool"
