"""Ingestão de webhooks: idempotência, assinatura e fila.

As três propriedades testadas aqui são o que separa uma integração que aguenta
produção de uma que corrompe dados no primeiro pico de tráfego.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.models.channel import WebhookEvent
from app.models.enums import Canal, StatusWebhook
from app.services import webhooks

pytestmark = pytest.mark.asyncio


def corpo_ml(recurso: str = "/orders/2000012345", user_id: str = "123456789") -> bytes:
    return json.dumps(
        {
            "topic": "orders_v2",
            "resource": recurso,
            "user_id": user_id,
            "application_id": 999,
            "attempts": 1,
        }
    ).encode()


async def test_a_reentrega_do_mesmo_evento_e_ignorada(db, conta):
    """Os três marketplaces reenviam notificações — é comportamento normal.

    Sem a chave de idempotência, cada reenvio criaria um evento novo e o pedido
    seria reprocessado várias vezes.
    """
    primeiro = await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    assert primeiro.criado_agora is True

    segundo = await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    assert segundo.criado_agora is False

    total = await db.scalar(select(func.count(WebhookEvent.id)))
    assert total == 1


async def test_eventos_diferentes_geram_registros_distintos(db, conta):
    await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml("/orders/1"), {}, "")
    await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml("/orders/2"), {}, "")

    total = await db.scalar(select(func.count(WebhookEvent.id)))
    assert total == 2


async def test_vincula_o_evento_a_conta_pelo_identificador_externo(db, conta):
    await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    evento = await db.scalar(select(WebhookEvent))

    assert evento.channel_account_id == conta.id
    assert evento.tenant_id == conta.tenant_id


async def test_notificacao_de_conta_desconhecida_e_registrada_sem_vinculo(db):
    """Não é erro: só não temos o que fazer com ela."""
    await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(user_id="000"), {}, "")
    evento = await db.scalar(select(WebhookEvent))

    assert evento is not None
    assert evento.channel_account_id is None


async def test_corpo_invalido_nao_derruba_a_ingestao(db):
    """Payload malformado precisa ser gravado para diagnóstico, não descartado."""
    registro = await webhooks.registrar(db, Canal.MERCADO_LIVRE, b"isto-nao-e-json", {}, "")
    assert registro.evento_id is not None

    evento = await db.scalar(select(WebhookEvent))
    assert "_corpo_invalido" in evento.payload


async def test_assinatura_invalida_grava_mas_nao_processa(db, conta, monkeypatch):
    """Responder diferente daria ao atacante um oráculo do que é aceito.

    O evento é gravado com a marca de assinatura inválida e simplesmente não é
    processado.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "USE_MOCK_CONNECTORS", False)
    monkeypatch.setattr(settings, "MP_WEBHOOK_SECRET", "segredo")

    registro = await webhooks.registrar(
        db, Canal.MERCADO_PAGO, json.dumps({"data": {"id": "1"}}).encode(),
        {"x-signature": "ts=1,v1=falsa"}, "",
    )
    evento = await db.scalar(select(WebhookEvent))

    assert evento.signature_valid is False
    assert evento.status == StatusWebhook.FALHOU
    assert registro.criado_agora is False  # não vai para a fila

    processado = await webhooks.processar(db, evento.id)
    assert processado is False


async def test_processa_pedido_e_persiste_o_dado(db, conta):
    """Caminho feliz completo: webhook → busca do detalhe → persistência."""
    from app.models.order import Order

    registro = await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    assert await webhooks.processar(db, registro.evento_id) is True

    evento = await db.get(WebhookEvent, registro.evento_id)
    assert evento.status == StatusWebhook.CONCLUIDO
    assert evento.processed_at is not None

    pedido = await db.scalar(select(Order).where(Order.external_id == "2000012345"))
    assert pedido is not None
    assert pedido.tenant_id == conta.tenant_id


async def test_evento_morto_pode_ser_reprocessado_pela_interface(db, conta):
    registro = await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    evento = await db.get(WebhookEvent, registro.evento_id)
    evento.status = StatusWebhook.MORTO
    evento.attempts = 6
    evento.error = "falha anterior"
    await db.commit()

    assert await webhooks.reprocessar(db, evento.id) is True

    await db.refresh(evento)
    assert evento.status == StatusWebhook.CONCLUIDO
    assert evento.error == ""


async def test_o_endpoint_responde_200_mesmo_com_payload_invalido(cliente):
    """O Mercado Livre suspende aplicações que devolvem erro nas notificações."""
    resposta = await cliente.post(
        "/api/v1/webhooks/mercadolivre",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert resposta.status_code == 200

    lixo = await cliente.post("/api/v1/webhooks/shopee", content=b"nao-json")
    assert lixo.status_code == 200


async def test_fila_lista_apenas_o_que_esta_pronto(db, conta):
    from datetime import UTC, datetime, timedelta

    registro = await webhooks.registrar(db, Canal.MERCADO_LIVRE, corpo_ml(), {}, "")
    assert registro.evento_id in await webhooks.pendentes(db)

    # Um evento com backoff em curso não deve ser reprocessado antes da hora.
    evento = await db.get(WebhookEvent, registro.evento_id)
    evento.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()

    assert registro.evento_id not in await webhooks.pendentes(db)
