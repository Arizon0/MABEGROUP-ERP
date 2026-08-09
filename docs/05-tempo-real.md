# 05 — Estratégia de Atualização em Tempo Real

## 5.1 O que "tempo real" significa aqui

Honestidade sobre latência, porque prometer o impossível gera suporte:

| Etapa | Latência típica | Limitante |
|---|---|---|
| Venda acontece → marketplace dispara webhook | 1–5 s | O marketplace. Fora do nosso controle. |
| Webhook chega → gravado e ACK | < 20 ms | Nosso. |
| Worker busca o detalhe na API | 200–800 ms | Rede + API externa. |
| Normaliza, persiste, publica evento | 20–80 ms | Nosso. |
| Redis pub/sub → SSE → navegador pinta | < 100 ms | Nosso. |
| **Total percebido** | **≈ 2 a 6 segundos** | |

Isso é *near real-time*, e é o melhor tecnicamente alcançável com APIs de
marketplace. Nenhum deles oferece stream persistente (tipo WebSocket do lado
deles). Quem promete "instantâneo" está fazendo polling agressivo e vai bater em
rate limit.

## 5.2 Arquitetura de três camadas redundantes

O sistema **não confia em um único mecanismo**. Webhooks se perdem — é um fato
operacional, não uma hipótese.

```
┌────────────────────────────────────────────────────────────────┐
│ CAMADA 1 — WEBHOOKS (latência baixa, entrega não garantida)     │
│ Cobre: pedidos, pagamentos, envios, perguntas, mensagens,       │
│        reclamações, alterações de anúncio                       │
│ Latência: 2–6 s      Confiabilidade: ~97%                       │
└────────────────────────────────────────────────────────────────┘
                              +
┌────────────────────────────────────────────────────────────────┐
│ CAMADA 2 — POLLING INCREMENTAL (a rede de segurança)            │
│ A cada 5 min: pedidos alterados desde a marca d'água            │
│ A cada 15 min: pagamentos e envios                              │
│ A cada 1 h: anúncios, estoque, perguntas                        │
│ A cada 6 h: escrow Shopee, reputação, campanhas                 │
│ Latência: até o intervalo   Confiabilidade: ~100%               │
└────────────────────────────────────────────────────────────────┘
                              +
┌────────────────────────────────────────────────────────────────┐
│ CAMADA 3 — RECONCILIAÇÃO DIÁRIA (a auditoria)                   │
│ 03:00 — varre os últimos 7 dias comparando contagem e soma      │
│ local vs. remota. Divergência → job de correção + alerta.       │
│ Latência: 24 h        Confiabilidade: garantia de integridade   │
└────────────────────────────────────────────────────────────────┘
```

As três camadas convergem no **mesmo UPSERT idempotente**. Processar o mesmo pedido
pelas três vias produz exatamente o mesmo estado final — essa propriedade é o que
torna a redundância barata em vez de perigosa.

## 5.3 Ingestão de webhook — o caminho de 500 ms

O Mercado Livre suspende aplicações que demoram para responder. O endpoint faz o
mínimo absoluto:

```python
@router.post("/webhooks/mercadolivre", status_code=200)
async def receber(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()                    # bytes crus, para a assinatura
    ok   = verificar_assinatura(request.headers, body)
    evento = await registrar_evento_webhook(       # INSERT ... ON CONFLICT DO NOTHING
        db, canal="mercadolivre", payload=json.loads(body), signature_valid=ok,
    )
    if evento.criado_agora:
        await fila.enqueue("processar_webhook", evento.id)
    return {"ok": True}                            # sempre 200: nunca peça reenvio
```

Quatro decisões deliberadas:

1. **Nenhuma chamada externa.** Buscar o detalhe do pedido aqui custaria 500 ms+ e
   estouraria o SLA.
2. **Sempre `200`.** Devolver `500` faz o marketplace reenviar em backoff crescente
   e, se persistir, cortar as notificações. Se algo falhou do nosso lado, o evento
   já está gravado e a fila resolve depois — reenvio não ajudaria.
3. **Idempotência no INSERT.** `idempotency_key = sha256(canal:topic:resource:...)`
   com `UNIQUE`. A entrega repetida (que os três marketplaces fazem) vira no-op.
4. **Assinatura registrada, não bloqueante.** Assinatura inválida grava
   `signature_valid=false` e **não processa** — mas ainda responde 200, para não
   dar ao atacante um oráculo que diferencia payload aceito de rejeitado.

## 5.4 Do banco ao navegador: Redis pub/sub + SSE

O desafio: a API roda em N réplicas. O navegador do seller está conectado por SSE
à réplica 2, mas o worker que processou o pedido está em outro processo. Redis
pub/sub resolve.

```
Worker processa pedido
   │
   ├─ persiste no Postgres
   └─ PUBLISH tenant:42:live '{"type":"order.created","payload":{...}}'
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    API-réplica1 API-réplica2 API-réplica3   (todas assinadas no canal)
        │           │           │
        ▼           ▼           ▼
     (ninguém)   SSE → navegador do seller   (ninguém)
```

**Por que SSE e não WebSocket:** o tráfego é 100% unidirecional (servidor →
cliente). SSE roda sobre HTTP comum, atravessa qualquer proxy corporativo,
reconecta sozinho e sabe retomar de onde parou pelo header `Last-Event-ID`.
WebSocket exigiria upgrade de protocolo, sticky session no balanceador e heartbeat
manual — o dobro de complexidade para o mesmo resultado.

**Fallback sem Redis:** em desenvolvimento local o barramento cai para
`InMemoryEventBus` (um `asyncio.Queue` por assinante). O código de negócio não
sabe a diferença — ambos implementam o mesmo `Protocol`. Isso é o que permite
`docker-compose up` funcionar sem Redis e o teste de SSE rodar sem infraestrutura.

### Contrato de eventos

```jsonc
{
  "id": "evt_01HZ...",             // ULID, ordenável e usado no Last-Event-ID
  "type": "order.created",         // order.created | order.updated | order.cancelled
                                   // shipment.updated | payment.approved
                                   // question.received | claim.opened | sync.completed
  "tenant_id": 42,
  "channel": "mercadolivre",
  "account_id": 7,
  "occurred_at": "2026-08-09T17:44:02Z",
  "payload": { "order_id": 1234, "external_id": "2000012345",
               "gross_amount": "129.90", "status": "paid", "items": 2 }
}
```

Heartbeat de 15 s (`: keep-alive`) impede que proxies derrubem a conexão ociosa.

## 5.5 Polling incremental por marca d'água

Cada par `(conta, recurso)` mantém uma linha em `sync_cursors`. O algoritmo:

```
1. Lê last_synced_at do cursor (ou now-90d no primeiro backfill)
2. Aplica sobreposição de 5 min: from = last_synced_at - 5min
   ↑ compensa relógio dessincronizado e escrita atrasada no lado deles.
     Sem essa sobreposição, pedidos criados durante a janela anterior somem
     para sempre — e ninguém percebe, porque nada dá erro.
3. Pagina até esgotar, respeitando o limite de janela do canal
   (ML: 30 dias por consulta;  Shopee: 15 dias por consulta)
4. UPSERT de cada registro
5. Grava last_synced_at = maior date_last_updated visto (nunca now(),
   para não pular o que estava sendo escrito no exato instante da leitura)
6. Em erro: incrementa consecutive_failures, agenda retry exponencial,
   e NÃO avança o cursor
```

O passo 5 é sutil e importante: gravar `now()` cria uma janela cega do tamanho da
duração da própria sincronização.

## 5.6 Frequências e orçamento de rate limit

| Job | Frequência | Chamadas/conta/dia | Observação |
|---|---|---|---|
| `sync_orders_recent` | 5 min | ~288 | `order.date_last_updated` |
| `sync_payments` | 15 min | ~96 | |
| `sync_shipments_active` | 15 min | ~96 | só envios não finalizados |
| `sync_listings` | 1 h | ~24 | multiget de 20 em 20 |
| `sync_questions` | 10 min | ~144 | |
| `sync_claims` | 1 h | ~24 | |
| `sync_escrow_shopee` | 6 h | ~4 | lote de 50 |
| `sync_settlements` | 1×/dia | ~3 | relatório assíncrono |
| `refresh_tokens` | 1 h | ~24 | proativo aos 80% de vida |
| `rollup_metrics` | 5 min | 0 | só banco local |
| `reconcile_daily` | 1×/dia 03:00 | ~50 | |
| **Total** | | **≈ 750–900** | Folga confortável vs. os limites praticados. |

Cada conta tem seu próprio *token bucket* no Redis. Um seller com backfill pesado
não consome a cota de outro — isolamento de ruído entre tenants.

## 5.7 Backfill inicial

Quando uma conta é conectada, o histórico não existe. O backfill roda em prioridade
baixa para não competir com a operação corrente:

```
Fase 1 (imediata, ~30 s): últimos 7 dias de pedidos → painel já mostra algo
Fase 2 (background, minutos): 90 dias de pedidos + pagamentos + envios
Fase 3 (background, horas): 24 meses, janela a janela, retomável por cursor
Fase 4: enriquecimento — escrow, billing, campanhas
```

A interface mostra a barra de progresso real (`sync_cursors.progress_pct`) em vez
de um spinner infinito. Se o processo cair, retoma do cursor persistido.

## 5.8 Frontend: SSE + TanStack Query trabalhando juntos

```ts
// hooks/useLiveEvents.ts (resumo do padrão)
const es = new EventSource(`${API}/api/v1/live/stream?token=${jwt}`);

es.addEventListener("order.created", (e) => {
  const evt = JSON.parse(e.data);
  // 1. Empurra no feed ao vivo (estado local, atualização ótica imediata)
  setFeed((f) => [evt, ...f].slice(0, 100));
  // 2. Invalida os caches afetados — o TanStack Query rebusca os números certos
  qc.invalidateQueries({ queryKey: ["overview"] });
  qc.invalidateQueries({ queryKey: ["orders"] });
  // 3. Notifica
  toast(`Novo pedido ${evt.channel} — R$ ${evt.payload.gross_amount}`);
});
```

A divisão de responsabilidade é intencional: **o SSE avisa que algo mudou; o REST
diz qual é o número correto.** Tentar manter agregados financeiros somando deltas
no cliente acumula erro de arredondamento e diverge do banco em minutos. O evento
é um gatilho, não uma fonte de verdade.

Reconexão: `EventSource` já reconecta sozinho; ao reconectar, o hook dispara um
`invalidateQueries` geral para recuperar o que passou durante a queda.
