import {
  useAuditoria,
  useCanaisDisponiveis,
  useConectarCanal,
  useContas,
  useMonitorIntegracao,
  useRenovarToken,
  useReprocessarWebhook,
  useRevogarConta,
  useSincronizar,
  useWebhooks,
} from '@/api/queries'
import { Carregando, ErroBox, SeloCanal, Secao, Tabela, Vazio } from '@/components/ui'
import { ROTULO_CANAL, dataHora, inteiro } from '@/lib/format'

const ESTILO_CONTA: Record<string, string> = {
  connected: 'text-good',
  expired: 'text-warn',
  error: 'text-bad',
  revoked: 'text-ink-muted',
}

const ROTULO_CONTA: Record<string, string> = {
  connected: 'Conectada',
  expired: 'Token expirado',
  error: 'Com erro',
  revoked: 'Revogada',
}

export function Configuracoes() {
  const contas = useContas()
  const canais = useCanaisDisponiveis()
  const monitor = useMonitorIntegracao()
  const webhooks = useWebhooks()
  const auditoria = useAuditoria()

  const conectar = useConectarCanal()
  const sincronizar = useSincronizar()
  const revogar = useRevogarConta()
  const renovar = useRenovarToken()
  const reprocessar = useReprocessarWebhook()

  if (contas.isError) return <ErroBox erro={contas.error} />

  return (
    <div className="space-y-4">
      {canais.data?.modo_simulado && (
        <div className="rounded-lg border border-warn-line bg-warn-soft p-3 text-sm text-warn">
          <strong>Modo simulado ativo.</strong> Os dados exibidos são gerados localmente, sem
          nenhuma chamada às APIs dos marketplaces. Para conectar contas reais, configure as
          credenciais das aplicações e defina <code>USE_MOCK_CONNECTORS=0</code>.
        </div>
      )}

      <Secao titulo="Conectar marketplace" descricao="A autorização é feita no site do próprio marketplace.">
        <div className="grid gap-3 sm:grid-cols-3">
          {(canais.data?.canais ?? []).map((c) => (
            <div key={c.channel} className="rounded-lg border border-line p-4">
              <SeloCanal canal={c.channel} />
              <p className="card-sub mt-2">
                {c.configurado
                  ? 'Pronto para conectar.'
                  : 'Credenciais da aplicação não configuradas no servidor.'}
              </p>
              <button
                type="button"
                className="btn btn-primary mt-3 w-full justify-center text-xs"
                disabled={!c.configurado || conectar.isPending}
                onClick={() => conectar.mutate(c.channel)}
              >
                Conectar {ROTULO_CANAL[c.channel] ?? c.channel}
              </button>
            </div>
          ))}
        </div>
      </Secao>

      <Secao titulo="Contas conectadas" descricao="Estado das credenciais e da sincronização de cada loja.">
        {contas.isLoading ? (
          <Carregando />
        ) : (contas.data ?? []).length === 0 ? (
          <Vazio titulo="Nenhuma conta conectada" descricao="Conecte um marketplace acima para começar a importar dados." />
        ) : (
          <div className="space-y-3">
            {(contas.data ?? []).map((c) => (
              <div key={c.id} className="rounded-lg border border-line p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <SeloCanal canal={c.channel} />
                    <p className="mt-1 text-sm font-medium text-ink">{c.nickname || `Conta ${c.id}`}</p>
                    <p className="card-sub">
                      ID externo {c.external_account_id} ·{' '}
                      <span className={ESTILO_CONTA[c.status] ?? ''}>
                        {ROTULO_CONTA[c.status] ?? c.status}
                      </span>
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn text-xs"
                      disabled={sincronizar.isPending}
                      onClick={() => sincronizar.mutate({ contaId: c.id, completo: true })}
                    >
                      Sincronizar
                    </button>
                    <button
                      type="button"
                      className="btn text-xs"
                      disabled={renovar.isPending}
                      onClick={() => renovar.mutate(c.id)}
                    >
                      Renovar token
                    </button>
                    <button
                      type="button"
                      className="btn text-xs text-bad"
                      onClick={() => {
                        if (
                          confirm(
                            'Revogar o acesso desta conta? As credenciais serão apagadas. ' +
                              'O histórico de pedidos e o financeiro são preservados.',
                          )
                        ) {
                          revogar.mutate(c.id)
                        }
                      }}
                    >
                      Revogar
                    </button>
                  </div>
                </div>

                <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3 text-xs lg:grid-cols-4">
                  <div>
                    <dt className="text-ink-muted">Conectada em</dt>
                    <dd className="text-ink">{dataHora(c.connected_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Última sincronização</dt>
                    <dd className="text-ink">{dataHora(c.last_sync_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Token expira em</dt>
                    <dd className="text-ink">{dataHora(c.token_expires_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Reautorização até</dt>
                    <dd className="text-ink">{dataHora(c.refresh_expires_at)}</dd>
                  </div>
                </dl>

                {c.last_error && <p className="mt-2 text-xs text-bad">{c.last_error}</p>}

                {c.cursors.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {c.cursors.map((cur) => (
                      <span
                        key={cur.resource}
                        className="rounded-md border border-line px-2 py-1 text-[11px] text-ink-soft"
                        title={cur.last_error || 'Sem erros'}
                      >
                        {cur.resource}: {dataHora(cur.last_synced_at)}
                        {cur.failures > 0 && <span className="ml-1 text-bad">· {cur.failures} falhas</span>}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Secao>

      <Secao
        titulo="Monitor de integração"
        descricao="É onde se descobre que uma conta parou de receber dados — antes que o buraco apareça no relatório."
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Webhooks nas últimas 24 h
            </h3>
            <Tabela
              colunas={['Situação', 'Quantidade']}
              vazio={(monitor.data?.webhooks_24h?.por_status ?? []).length === 0}
            >
              {(monitor.data?.webhooks_24h?.por_status ?? []).map(
                (s: { status: string; quantidade: number }) => (
                  <tr key={s.status}>
                    <td className="td">{s.status}</td>
                    <td className="td num">{inteiro(s.quantidade)}</td>
                  </tr>
                ),
              )}
            </Tabela>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Defasagem de sincronização
            </h3>
            <Tabela
              colunas={['Conta', 'Recurso', 'Atraso', 'Situação']}
              vazio={(monitor.data?.sincronizacao ?? []).length === 0}
            >
              {(monitor.data?.sincronizacao ?? []).map((s: Record<string, any>, i: number) => (
                <tr key={i}>
                  <td className="td text-xs">{s.nickname || s.channel}</td>
                  <td className="td text-xs">{s.resource}</td>
                  <td
                    className={`td num text-xs ${
                      Number(s.atraso_minutos ?? 0) > 60 ? 'font-semibold text-bad' : ''
                    }`}
                  >
                    {s.atraso_minutos != null ? `${s.atraso_minutos} min` : '—'}
                  </td>
                  <td className="td text-xs">{s.status}</td>
                </tr>
              ))}
            </Tabela>
          </div>
        </div>
      </Secao>

      <Secao titulo="Últimos webhooks" descricao="Eventos com falha podem ser reprocessados individualmente.">
        {webhooks.isLoading ? (
          <Carregando />
        ) : (
          <Tabela
            colunas={['Canal', 'Tópico', 'Recurso', 'Situação', 'Tentativas', 'Recebido em', '']}
            vazio={(webhooks.data ?? []).length === 0}
          >
            {(webhooks.data ?? []).map((w) => (
              <tr key={w.id}>
                <td className="td">
                  <SeloCanal canal={w.channel} />
                </td>
                <td className="td text-xs">{w.topic}</td>
                <td className="td max-w-[200px] truncate text-xs">{w.resource}</td>
                <td className={`td text-xs ${w.status === 'dead' ? 'text-bad' : ''}`}>{w.status}</td>
                <td className="td num text-xs">{w.attempts}</td>
                <td className="td text-xs">{dataHora(w.received_at)}</td>
                <td className="td">
                  {(w.status === 'dead' || w.status === 'failed') && (
                    <button
                      type="button"
                      className="btn px-2 py-0.5 text-xs"
                      disabled={reprocessar.isPending}
                      onClick={() => reprocessar.mutate(w.id)}
                    >
                      Reprocessar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      <Secao titulo="Auditoria" descricao="Registro de tudo que um usuário fez no sistema.">
        {auditoria.isLoading ? (
          <Carregando />
        ) : (
          <Tabela colunas={['Ação', 'Entidade', 'Usuário', 'IP', 'Data']} vazio={(auditoria.data ?? []).length === 0}>
            {(auditoria.data ?? []).slice(0, 40).map((a) => (
              <tr key={a.id}>
                <td className="td text-xs">{a.action}</td>
                <td className="td text-xs">
                  {a.entity_type} {a.entity_id}
                </td>
                <td className="td num text-xs">{a.user_id ?? '—'}</td>
                <td className="td num text-xs">{a.ip || '—'}</td>
                <td className="td text-xs">{dataHora(a.created_at)}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>
    </div>
  )
}
