"""Execução imediata do enriquecimento, até acabar.

O cron preenche o frete aos poucos — bom para o dia a dia, ruim logo depois de
um backfill, quando há milhares de pedidos pendentes e o líquido do painel fica
inflado enquanto isso. Aqui a mesma tarefa roda em sequência até zerar a fila.

    docker compose exec api python -m app.workers.completar
"""
from __future__ import annotations

import asyncio

import structlog

from app.workers.tasks import enriquecer_pedidos

log = structlog.get_logger(__name__)


async def executar(lote: int = 200, max_rodadas: int = 200) -> None:
    total = 0
    for rodada in range(1, max_rodadas + 1):
        resultado = await enriquecer_pedidos({}, limite=lote)
        feitos = int(resultado.get("enriquecidos", 0))
        total += feitos

        print(
            f"rodada {rodada:>3}  enriquecidos {feitos:>4}  "
            f"acumulado {total:>5}  erros {resultado.get('erros', 0)}  "
            f"sem envio {resultado.get('sem_envio', 0)}",
            flush=True,
        )

        # Nada foi enriquecido e nada restou por buscar: a fila acabou. Sair no
        # primeiro lote vazio evita rodar 200 vezes à toa quando já terminou.
        if feitos == 0 and int(resultado.get("sem_envio", 0)) == 0:
            print("fila vazia — concluído.", flush=True)
            return

    print("limite de rodadas atingido; rode de novo para continuar.", flush=True)


if __name__ == "__main__":
    asyncio.run(executar())
