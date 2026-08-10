# 14 — Conectar as APIs reais (passo a passo)

Guia para sair do modo simulado e trazer os dados reais das suas contas.

> **Ordem importa.** Faça na sequência: túnel → aplicações nos portais → `.env`
> → reiniciar → conectar no painel. Pular o túnel faz os webhooks nunca
> chegarem, e você só descobre isso dias depois, quando o painel estiver
> desatualizado.

---

## Etapa 1 — Expor sua máquina para a internet (obrigatório)

Os marketplaces precisam **enviar notificações para o seu computador**. Eles não
conseguem alcançar `http://localhost:8000` — esse endereço só existe dentro do
seu Mac.

A solução é um túnel: um endereço público que encaminha para a sua máquina.

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

O terminal vai mostrar algo como:

```
https://abc-def-123.trycloudflare.com
```

**Deixe esse terminal aberto** enquanto usar o sistema. Anote essa URL — vamos
chamá-la de `SUA_URL_PUBLICA`.

> A URL gratuita muda a cada vez que você reinicia o túnel. Ao mudar, é preciso
> atualizar o `.env` e as URLs cadastradas nos portais. Para uso contínuo, vale
> um domínio próprio (Cloudflare Tunnel nomeado) ou hospedar o sistema num
> servidor — ver [`docs/09-deploy.md`](09-deploy.md).

---

## Etapa 2 — Criar as aplicações nos portais

### Mercado Livre

1. Acesse <https://developers.mercadolivre.com.br/devcenter> e faça login com a
   conta **vendedora**.
2. **Criar aplicação**. Preencha nome e descrição.
3. Em **Redirect URI**, coloque exatamente:
   ```
   SUA_URL_PUBLICA/api/v1/oauth/mercadolivre/callback
   ```
4. Em **Tópicos de notificação**, marque: `orders_v2`, `shipments`, `payments`,
   `items`, `questions`, `messages`, `post_purchase`.
5. Em **URL de callback de notificações**:
   ```
   SUA_URL_PUBLICA/api/v1/webhooks/mercadolivre
   ```
6. Em **Escopos**, marque `read` e `offline_access`.
   ⚠️ Sem `offline_access` a conexão morre em 6 horas e não renova sozinha.
7. Salve e anote **App ID** e **Secret Key**.

### Mercado Pago (opcional)

Só é necessário se você recebe por fora do Mercado Livre (link de pagamento, PIX
próprio, loja própria). O token do ML já autoriza a maior parte dos dados de
pagamento das vendas feitas no ML.

1. <https://www.mercadopago.com.br/developers/panel>
2. Crie a aplicação, anote **Client ID** e **Client Secret**.
3. Em **Webhooks**, cadastre `SUA_URL_PUBLICA/api/v1/webhooks/mercadopago` e
   anote a **assinatura secreta** gerada.

### Shopee

1. <https://open.shopee.com> → cadastre-se como parceiro.
2. Crie o app e anote **Partner ID** e **Partner Key**.
3. Cadastre a URL de redirect:
   `SUA_URL_PUBLICA/api/v1/oauth/shopee/callback`
4. Cadastre a URL de push (webhook):
   `SUA_URL_PUBLICA/api/v1/webhooks/shopee`

> ⚠️ **A Shopee exige homologação para produção.** Até ser aprovado, o app só
> funciona no ambiente de testes. Nesse período, use
> `SHOPEE_API_BASE=https://partner.test-stable.shopeemobile.com`.
> Mercado Livre e Mercado Pago não têm essa exigência para leitura.

---

## Etapa 3 — Preencher o `.env`

Gere as duas chaves de segurança:

```bash
# Chave da aplicação
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Chave do cofre de tokens (protege as credenciais dos marketplaces no banco)
docker compose run --rm api python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edite o `.env` na raiz do projeto:

```bash
# Desliga os dados simulados — este é o interruptor principal
USE_MOCK_CONNECTORS=0

SECRET_KEY=<a primeira chave gerada>
MASTER_ENCRYPTION_KEY=<a segunda chave gerada>
BUYER_HASH_PEPPER=<qualquer texto longo e aleatório>

CORS_ORIGINS=http://localhost:5173

ML_CLIENT_ID=<App ID do Mercado Livre>
ML_CLIENT_SECRET=<Secret Key do Mercado Livre>
ML_REDIRECT_URI=SUA_URL_PUBLICA/api/v1/oauth/mercadolivre/callback

MP_CLIENT_ID=<Client ID do Mercado Pago>
MP_CLIENT_SECRET=<Client Secret do Mercado Pago>
MP_REDIRECT_URI=SUA_URL_PUBLICA/api/v1/oauth/mercadopago/callback
MP_WEBHOOK_SECRET=<assinatura secreta do webhook>

SHOPEE_PARTNER_ID=<Partner ID>
SHOPEE_PARTNER_KEY=<Partner Key>
SHOPEE_REDIRECT_URI=SUA_URL_PUBLICA/api/v1/oauth/shopee/callback

# Quantos dias de histórico importar na primeira conexão
BACKFILL_DAYS=90
```

Substitua `SUA_URL_PUBLICA` pela URL do túnel, sem barra no final.

---

## Etapa 4 — Limpar os dados simulados e reiniciar

Os dados de demonstração ficam no banco. Como eles se misturariam ao histórico
real e contaminariam todos os relatórios, o banco é recriado do zero:

```bash
docker compose down -v     # o -v apaga os volumes, incluindo o banco
docker compose up --build
```

Confirme que o modo simulado está desligado:

```bash
curl -s http://localhost:8000/health | grep modo_simulado
# deve mostrar: "modo_simulado": false
```

Na aba **Configurações** do painel, o aviso amarelo de "Modo simulado" deve ter
sumido e os botões de conectar devem estar habilitados.

---

## Etapa 5 — Conectar as contas

1. Abra <http://localhost:5173> → **Configurações**.
2. Clique em **Conectar Mercado Livre**.
3. Você será levado ao site do Mercado Livre. Faça login com a conta vendedora e
   autorize.
4. Ao voltar, a conta aparece como **Conectada** e o backfill começa sozinho.
5. Repita para Shopee e Mercado Pago, se aplicável.

O histórico de 90 dias leva de alguns minutos a algumas horas, dependendo do
volume. A tela de Configurações mostra o progresso por recurso.

---

## Etapa 6 — Conferir se está tudo entrando

Em **Configurações → Monitor de integração**:

| O que olhar | Esperado |
|---|---|
| Webhooks nas últimas 24 h | Contador subindo conforme chegam vendas |
| Defasagem de sincronização | Abaixo de 60 min em todos os recursos |
| Situação da conta | "Conectada", sem mensagem de erro |

Faça uma venda de teste (ou espere uma real): ela deve aparecer na aba
**Ao vivo** em poucos segundos.

---

## O que é importado de cada canal

| Módulo | Mercado Livre | Shopee | Mercado Pago |
|---|---|---|---|
| Pedidos e itens | ✅ | ✅ | — |
| Envios, rastreio e custo real de frete | ✅ | ✅ | — |
| Pagamentos, taxas detalhadas e líquido | ✅ (via MP) | ✅ (escrow) | ✅ |
| Data de liberação do dinheiro | ✅ | ✅ | ✅ |
| Anúncios, variações, preço e estoque | ✅ | ✅ | — |
| Perguntas | ✅ | — | — |
| Reclamações e devoluções | ✅ | ✅ | — |
| Campanhas e promoções | — | ✅ | — |
| Reputação / saúde da conta | ✅ | ✅ | — |
| Visitas ao anúncio (conversão) | ✅ | ✅ | — |

**Não disponível por limitação das APIs** (detalhes em
[`docs/10-riscos-limitacoes.md`](10-riscos-limitacoes.md)):

- **Custo de mídia paga da Shopee** — a Ads API exige whitelist separada. Até
  lá, lance o valor manualmente por campanha na aba de Marketing.
- **Chat pré-venda do Mercado Livre** — só existem as perguntas públicas.
- **Endereço completo do comprador** — restrição de LGPD. Vem cidade e estado,
  suficiente para a análise geográfica.
- **Custo do seu produto** — nenhum marketplace conhece. Cadastre em
  **Produtos** para que a margem seja calculada.

---

## Frequência de atualização

| Dado | Atualiza |
|---|---|
| Pedidos novos e mudanças de status | Segundos (webhook) |
| Verificação de segurança dos pedidos | A cada 5 min |
| Anúncios, estoque, perguntas, reclamações, campanhas | A cada 1 h |
| Reputação e indicadores da conta | 1×/dia |
| Conciliação financeira | 1×/dia, madrugada |

O webhook dá a velocidade; o polling garante que nada se perca se uma
notificação falhar.

---

## Problemas frequentes

| Sintoma | Causa | Solução |
|---|---|---|
| Botão "Conectar" desabilitado | Credenciais não chegaram ao contêiner | Confira o `.env` e rode `docker compose down && docker compose up` |
| Ainda aparece "Modo simulado" | `USE_MOCK_CONNECTORS` continua `1` | Ajuste no `.env` e reinicie |
| `invalid_redirect_uri` | URL do portal ≠ URL do `.env` | Precisam ser idênticas, sem barra no final |
| Shopee: erro de autenticação sempre | Relógio fora de sincronia | Ajustes do Sistema → Data e Hora → sincronizar automaticamente |
| Nenhum webhook chega | Túnel caiu ou URL mudou | Reinicie o `cloudflared` e atualize as URLs nos portais |
| Conta caiu para "Token expirado" | Renovação concorrente sem Redis | Com Docker o Redis já sobe; confirme que o serviço está de pé |
| Margem aparece "—" | Produto sem custo | Cadastre o custo em **Produtos** e mapeie o SKU |

---

## Segurança — leia antes de colocar credencial real

- **Nunca versione o `.env`.** Já está no `.gitignore`, mas confirme com
  `git status` antes de qualquer commit.
- Um `access_token` do Mercado Livre com escopo de escrita permite **alterar
  preços e encerrar anúncios** da sua conta. Trate como senha de banco.
- Os tokens são cifrados no banco com a `MASTER_ENCRYPTION_KEY`. Se perder essa
  chave, as contas precisam ser reconectadas; se ela vazar junto com um dump do
  banco, os tokens ficam expostos.
- Use escopo `read` enquanto não precisar de funções de escrita.
