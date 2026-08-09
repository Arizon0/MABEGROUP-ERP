# 03 — Fluxos de Autenticação Oficiais

Três marketplaces, três mecanismos diferentes. Nenhum deles é OAuth "de manual",
e cada um tem uma armadilha que derruba integração em produção. Documentadas abaixo.

---

## 3.1 Mercado Livre — OAuth 2.0 Authorization Code + PKCE

### Pré-requisitos (feitos pelo cliente, uma vez)

1. Criar aplicação em <https://developers.mercadolivre.com.br/devcenter>.
2. Anotar `App ID` (client_id) e `Secret Key` (client_secret).
3. Cadastrar a **Redirect URI** exata: `https://api.seudominio.com/api/v1/oauth/mercadolivre/callback`.
   Precisa ser HTTPS e bater caractere por caractere.
4. Marcar os tópicos de notificação desejados e a URL de callback de webhooks.

### Fluxo

```
Seller clica "Conectar Mercado Livre"
   │
   ▼
Backend gera state (CSRF, 32 bytes) + code_verifier (PKCE, 64 bytes)
grava em oauth_states com TTL 10 min, code_verifier CIFRADO
   │
   ▼
302 → https://auth.mercadolivre.com.br/authorization
        ?response_type=code
        &client_id={APP_ID}
        &redirect_uri={REDIRECT_URI}
        &state={state}
        &code_challenge={BASE64URL(SHA256(code_verifier))}
        &code_challenge_method=S256
   │
   ▼  Seller autoriza no domínio do ML
   │
   ▼
GET /api/v1/oauth/mercadolivre/callback?code=TG-xxx&state=yyy
   │
   ├─ valida state (existe? não expirou? não foi usado?) → senão 400
   │
   ▼
POST https://api.mercadolibre.com/oauth/token
  grant_type=authorization_code
  client_id, client_secret, code, redirect_uri, code_verifier
   │
   ▼
{ access_token, token_type, expires_in: 21600, scope,
  user_id, refresh_token }
   │
   ├─ GET /users/me            → nickname, site_id, tipo de conta
   ├─ UPSERT channel_accounts
   ├─ INSERT channel_credentials (tokens cifrados com Fernet)
   ├─ enfileira backfill inicial (últimos 90 dias)
   └─ 302 → frontend /configuracoes?conectado=mercadolivre
```

### Ciclo de vida do token

| Item | Valor | Consequência prática |
|---|---|---|
| `access_token` | **6 horas** | Refresh proativo aos 80% de vida (≈4h48). |
| `refresh_token` | **6 meses** | Se o seller ficar 6 meses sem uso, precisa reautorizar. |
| Rotação | **Uso único** | ⚠️ **A armadilha mais séria.** Cada refresh invalida o refresh_token anterior e emite um novo. |

> ### ⚠️ Condição de corrida no refresh do Mercado Livre
> Se dois workers detectarem o token expirado ao mesmo tempo e ambos chamarem
> `/oauth/token`, o segundo usa um refresh_token **já consumido** → a conta é
> desconectada e o seller precisa reautorizar manualmente. Em produção isso
> acontece em minutos sob carga.
>
> **Solução implementada:** lock distribuído no Redis
> (`SET lock:refresh:{account_id} NX EX 30`). Quem não pega o lock espera e relê
> a credencial do banco. Sem Redis, fallback para `SELECT ... FOR UPDATE` na linha
> de `channel_credentials`. O novo refresh_token é gravado na **mesma transação**
> do consumo.

### Escopos

`read` (leitura de recursos), `write` (alteração), `offline_access`
(**obrigatório** para receber refresh_token). Sem `offline_access` a integração
morre em 6 horas.

---

## 3.2 Mercado Pago — dois modos

O Mercado Pago tem dois caminhos, e escolher o errado custa uma reescrita.

### Modo A — Credencial própria do seller (recomendado aqui)

O seller gera o próprio `Access Token` no painel do MP e cola na tela de
Configurações. Simples, sem homologação, funciona no dia 1.
**Limitação:** o token é de longa duração e não rotaciona sozinho; se vazar, o
estrago é grande. Por isso vai cifrado no cofre com a mesma proteção dos demais.

### Modo B — OAuth Marketplace (para escalar)

```
GET https://auth.mercadopago.com/authorization
    ?client_id={APP_ID}&response_type=code&platform_id=mp
    &state={state}&redirect_uri={REDIRECT_URI}
   ↓
POST https://api.mercadopago.com/oauth/token
    { client_secret, grant_type: "authorization_code", code, redirect_uri }
   ↓
{ access_token, refresh_token, user_id, expires_in: 15552000 (180 dias),
  public_key, live_mode }
```

Requer a aplicação aprovada como *marketplace* no MP. **Nota importante:** contas
Mercado Livre já vêm com Mercado Pago vinculado, e o `access_token` do ML **já
autoriza** boa parte de `/v1/payments`. Na prática, a conexão MP separada só é
necessária quando o seller recebe por fora do ML (link de pagamento, PIX próprio,
loja própria).

### Validação de assinatura do webhook (obrigatória)

O MP envia o header `x-signature: ts=1704908010,v1=abc123...`. A validação:

```python
manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
expected = hmac.new(WEBHOOK_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
hmac.compare_digest(expected, v1)   # comparação em tempo constante
```

Sem isso, qualquer um que descubra a URL injeta pagamentos falsos no seu banco.

---

## 3.3 Shopee Open Platform — autorização por loja + assinatura HMAC

A Shopee **não usa OAuth padrão**. Toda requisição é assinada individualmente.

### Pré-requisitos

1. Conta em <https://open.shopee.com>, criar App → obter `partner_id` e `partner_key`.
2. Cadastrar a URL de redirect e a URL de push (webhook).
3. App em `test-stable` (sandbox) até ser aprovado para produção.

### Fluxo de autorização

```
Backend monta a URL assinada:
  base_string = f"{partner_id}{path}{timestamp}"        # path = /api/v2/shop/auth_partner
  sign        = HMAC_SHA256(partner_key, base_string).hexdigest()

302 → https://partner.shopeemobile.com/api/v2/shop/auth_partner
        ?partner_id={id}&timestamp={ts}&sign={sign}&redirect={REDIRECT_URI}
   ↓  seller autoriza a loja
   ↓
GET {REDIRECT_URI}?code=xxx&shop_id=123456   (ou &main_account_id= para multi-loja)
   ↓
POST /api/v2/auth/token/get     { code, shop_id, partner_id }
   ↓
{ access_token, refresh_token, expire_in: 14400 }
```

### Assinatura de TODA chamada autenticada

```python
base_string = f"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"
sign        = HMAC_SHA256(partner_key, base_string).hexdigest()
# vai na query string: partner_id, timestamp, access_token, shop_id, sign
```

Três detalhes que quebram a integração se ignorados:

- `timestamp` em **segundos** UTC, com tolerância de **±5 minutos**. Relógio do
  servidor fora de sincronia = 100% de erro de autenticação. Use NTP.
- A ordem de concatenação da `base_string` é fixa e não é a ordem alfabética.
- Endpoints públicos (`/api/v2/public/*`) assinam **sem** `access_token` e `shop_id`.

### Ciclo de vida

| Item | Valor |
|---|---|
| `access_token` | **4 horas** |
| `refresh_token` | **30 dias**, e **rotaciona** a cada uso |
| Reautorização | Obrigatória se passar 30 dias sem refresh |

O refresh da Shopee é mais agressivo que o do ML: o cron roda de hora em hora e
renova qualquer token com menos de 1 hora de vida restante.

### Validação do push (webhook)

```python
base_string = f"{callback_url}|{raw_body}"
expected = HMAC_SHA256(partner_key, base_string).hexdigest()
# comparar com o header `Authorization`
```

⚠️ Use o **corpo cru** (bytes exatos recebidos), nunca o JSON reserializado — a
reserialização muda espaços e ordem de chaves e invalida a assinatura.

---

## 3.4 Autenticação dos usuários do próprio SaaS

Camada separada e independente das credenciais de marketplace:

- **Login** `POST /api/v1/auth/login` → JWT de acesso (30 min) + refresh token
  opaco (30 dias, rotativo, armazenado com hash em `user_sessions`).
- **Senha** com bcrypt (cost 12).
- **RBAC** com quatro papéis:

| Papel | Permissões |
|---|---|
| `owner` | Tudo, incluindo faturamento do SaaS e exclusão do tenant. |
| `admin` | Conectar/revogar contas, gerenciar usuários, ver financeiro. |
| `analyst` | Ler tudo, exportar relatórios. Não conecta contas nem altera dados. |
| `viewer` | Somente leitura dos dashboards. Sem exportação. |

- **MFA (TOTP)** opcional por usuário, obrigatório para `owner` em planos
  Enterprise.
- Todo JWT carrega `tenant_id`; a dependência `get_current_user` injeta o escopo em
  todas as queries.

---

## 3.5 Matriz comparativa

| Aspecto | Mercado Livre | Mercado Pago | Shopee |
|---|---|---|---|
| Protocolo | OAuth 2.0 + PKCE | OAuth 2.0 | Assinatura HMAC-SHA256 |
| Vida do access token | 6 h | 180 dias | 4 h |
| Vida do refresh token | 6 meses | 180 dias | 30 dias |
| Refresh rotativo | ✅ (uso único) | ✅ | ✅ |
| Assina cada requisição | ❌ (Bearer) | ❌ (Bearer) | ✅ (obrigatório) |
| Sandbox | Contas de teste | ✅ (credenciais test) | ✅ (`test-stable`) |
| Homologação prévia | Não para leitura | Sim para modo marketplace | **Sim, obrigatória** |
| Valida webhook por assinatura | Parcial (IP/segredo) | ✅ `x-signature` | ✅ `Authorization` |
