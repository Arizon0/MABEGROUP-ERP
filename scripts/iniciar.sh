#!/usr/bin/env bash
# Sobe o sistema depois de reiniciar a máquina, e diz o que está no ar.
#
#   ./scripts/iniciar.sh
#
# Não precisa de túnel para funcionar: sem ele o sistema deixa de receber
# avisos instantâneos do marketplace, mas a sincronização periódica continua
# rodando a cada cinco minutos e os dados seguem atualizados.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "→ verificando o Docker"
if ! docker info >/dev/null 2>&1; then
  echo "✗ o Docker não está rodando." >&2
  echo "  Abra o Docker Desktop, espere a baleia ficar verde e rode de novo." >&2
  exit 1
fi

echo "→ buscando atualizações do código"
git pull --ff-only origin claude/marketplace-sales-consolidation-hvazwg 2>&1 | tail -2 || {
  echo "  (sem rede ou sem atualizações — seguindo com o código local)"
}

echo "→ subindo os contêineres"
docker compose up -d --build 2>&1 | tail -3

echo "→ esperando a API responder"
for _ in $(seq 1 30); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 2
done

if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  echo "✗ a API não respondeu. Veja o motivo com:" >&2
  echo "    docker compose logs api --tail 40" >&2
  exit 1
fi

pedidos=$(docker compose exec -T db psql -U marketplace -d marketplace_hub -t \
  -c "select count(*) from orders;" 2>/dev/null | tr -d ' \n' || echo "?")

echo
echo "✓ sistema no ar"
echo "  painel:   http://localhost:5173"
echo "  API:      http://localhost:8000/docs"
echo "  pedidos:  $pedidos no banco"
echo
echo "  Os dados continuam de onde pararam — o banco fica num volume do Docker"
echo "  e sobrevive a reiniciar a máquina."
echo
echo "  Para forçar uma sincronização agora:  ./scripts/sincronizar.sh"
