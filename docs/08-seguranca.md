# 08 — Estratégia de Segurança

## 8.1 Modelo de ameaças

O ativo mais valioso do sistema **não são os dados de venda — são os tokens de
marketplace**. Um `access_token` do Mercado Livre com escopo `write` permite alterar
preços, encerrar anúncios e responder clientes em nome do seller. O vazamento de
um token é incidente mais grave que o vazamento do banco inteiro.

| Ameaça | Impacto | Mitigação |
|---|---|---|
| Vazamento de token de marketplace | Crítico | Cofre cifrado, chave fora do banco, rotação, escopo mínimo |
| Vazamento do dump do banco | Alto | Tokens ilegíveis sem a chave; dado pessoal pseudonimizado |
| Cross-tenant (ver dados de outro seller) | Crítico | Escopo em 3 camadas + RLS + teste automatizado |
| Webhook forjado | Alto | Validação HMAC de assinatura obrigatória |
| Replay de webhook | Médio | Chave de idempotência + validação de `timestamp` |
| SQL injection | Alto | ORM parametrizado; zero SQL por concatenação |
| Escalada de privilégio | Alto | RBAC verificado no servidor, nunca só na interface |
| Roubo de sessão | Médio | JWT curto + refresh rotativo + fingerprint |
| Exfiltração por exportação | Médio | Exportação registrada em auditoria, link com TTL |

## 8.2 Cofre de tokens

```
Token em claro ──▶ Fernet(chave_v{n}) ──▶ BYTEA no Postgres
                          │
                   MASTER_ENCRYPTION_KEY
                   (variável de ambiente / AWS KMS / Secrets Manager)
                   NUNCA no banco, NUNCA no repositório
```

- **Fernet** = AES-128-CBC + HMAC-SHA256, com IV aleatório e timestamp. Autenticado:
  adulterar o ciphertext é detectado na descriptografia.
- **`key_version`** em cada credencial permite rotacionar a chave sem downtime:
  a chave nova cifra as escritas, as chaves antigas continuam decifrando as
  leituras até a re-cifragem em lote terminar.
- **Nunca logar token**, nem truncado. O logger tem um filtro (`redact_secrets`)
  que substitui qualquer padrão parecido com token por `***`, inclusive dentro de
  payloads de erro do httpx — que é exatamente onde tokens vazam para o Sentry.
- **Escopo mínimo**: `read` + `offline_access` por padrão. `write` só é pedido se o
  tenant ativar funções de escrita.
- Revogação apaga a credencial (hard delete, é o único caso) e registra em auditoria.

## 8.3 Isolamento entre tenants — defesa em profundidade

```
1. JWT carrega tenant_id assinado ────────► não é falsificável sem a chave
2. get_tenant_scope() injeta o filtro ────► toda query de negócio é escopada
3. RLS no Postgres (produção) ────────────► rede de segurança contra bug de código
```

O teste `test_isolamento_tenant.py` cria dois tenants, popula ambos e verifica que
o tenant A recebe `404` (não `403` — não confirmamos a existência do recurso) em
todos os endpoints com ID do tenant B. Roda no CI e bloqueia o merge.

## 8.4 Segurança da API

| Controle | Implementação |
|---|---|
| Autenticação | JWT HS256 (assimétrico RS256 em multi-serviço), 30 min |
| Refresh | Token opaco de 30 dias, hash no banco, rotativo, revogável |
| Senhas | bcrypt cost 12; política de mínimo 10 caracteres |
| MFA | TOTP opcional; obrigatório para `owner` no plano Enterprise |
| Rate limit | Por IP e por usuário; login com backoff progressivo após 5 falhas |
| CORS | Lista explícita de origens em produção — `*` só em desenvolvimento |
| Headers | HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, CSP |
| Validação | Pydantic v2 em toda entrada; rejeita campo desconhecido |
| Erros | Mensagem genérica ao cliente; detalhe só no log correlacionado |
| Tamanho | Limite de corpo de requisição e de upload |

## 8.5 Segurança dos webhooks

1. **Assinatura HMAC** validada com `hmac.compare_digest` (tempo constante — a
   comparação com `==` vaza informação por timing).
2. **Corpo cru** para a verificação; JSON reserializado invalida a assinatura.
3. **Janela de timestamp** de ±5 min bloqueia replay.
4. **Idempotência** por `UNIQUE (idempotency_key)`.
5. **Sempre HTTP 200**, mesmo em assinatura inválida — não damos ao atacante um
   oráculo que diferencia payload aceito de rejeitado. O evento fica gravado com
   `signature_valid=false` e não é processado.
6. **Allowlist de IP** quando o marketplace publica as faixas (defesa adicional,
   nunca a única).

## 8.6 LGPD e dados pessoais

| Dado | Tratamento |
|---|---|
| ID do comprador | SHA-256 com *pepper* em `buyer_hash`. Permite análise de recompra sem guardar identidade. |
| Nome/nickname | Guardado só quando a API fornece e o tenant ativa; mascarado para papel `viewer`. |
| Endereço | Apenas cidade/estado por padrão; endereço completo só quando há necessidade logística real. |
| CPF/CNPJ | **Não armazenado.** Nenhuma funcionalidade atual justifica. |
| E-mail/telefone | Não armazenados. |

Base legal: execução de contrato (o seller precisa operar seus pedidos) e legítimo
interesse (análise agregada). Retenção alinhada à obrigação fiscal (5 anos para
documento financeiro). Direito de eliminação atendido por anonimização — o registro
financeiro permanece, a identidade não. Todo acesso a dado pessoal passa por
`audit_logs`.

## 8.7 Segredos e infraestrutura

- Segredos por variável de ambiente, provisionados por AWS Secrets Manager, Doppler
  ou o cofre do provedor. **Nunca** em `.env` versionado — o `.gitignore` cobre, e
  o CI roda *secret scanning* que falha o build se um segredo aparecer no diff.
- `.env.example` com todas as chaves e valores falsos, para documentar sem vazar.
- TLS 1.2+ obrigatório em toda comunicação, inclusive banco (`sslmode=require`).
- Banco em sub-rede privada; acesso administrativo só por bastion/túnel.
- Backup diário com PITR de 7 dias; **teste de restauração mensal** — backup não
  testado não é backup.
- Dependências: `pip-audit` e `npm audit` no CI; Dependabot semanal.
- Imagem Docker sem root, base `slim`, multi-stage, scan com Trivy.

## 8.8 Resposta a incidentes

| Incidente | Ação imediata |
|---|---|
| Token de marketplace vazado | Revogar no DevCenter/Open Platform, apagar credencial, notificar o tenant, forçar reautorização |
| Chave mestra comprometida | Rotacionar chave, re-cifrar todas as credenciais, invalidar sessões, auditar acessos |
| Acesso cross-tenant detectado | Suspender o endpoint afetado, auditar logs, notificar afetados (prazo ANPD: 2 dias úteis) |
| Webhook forjado processado | Reprocessar a partir do último estado íntegro, revalidar dados do período |
