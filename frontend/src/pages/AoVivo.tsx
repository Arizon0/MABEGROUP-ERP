import { useEffect, useState } from 'react'

import { useFeed, usePulso } from '@/api/queries'
import { GraficoPorMinuto } from '@/components/charts'
import { Carregando, KpiCard, SeloCanal, SeloStatus, Secao, Vazio } from '@/components/ui'
import { useLive, type EstadoConexao } from '@/hooks/useLive'
import { brl, desde, inteiro } from '@/lib/format'

const CONEXAO: Record<EstadoConexao, { texto: string; classe: string; pulsa: boolean }> = {
  'ao-vivo': { texto: 'AO VIVO', classe: 'border-good-line bg-good-soft text-good', pulsa: true },
  conectando: { texto: 'Conectando…', classe: 'border-warn-line bg-warn-soft text-warn', pulsa: false },
  reconectando: {
    texto: 'Reconectando…',
    classe: 'border-warn-line bg-warn-soft text-warn',
    pulsa: false,
  },
  offline: { texto: 'Sem conexão', classe: 'border-bad-line bg-bad-soft text-bad', pulsa: false },
}

export function AoVivo() {
  const { eventos, estado, contador, limparFeed } = useLive(true)
  const pulso = usePulso()
  const feedInicial = useFeed()
  const [notificar, setNotificar] = useState(false)

  // Sem esta carga inicial a tela abriria vazia até a próxima venda — que pode
  // levar horas. O SSE só entrega o que acontecer daqui para frente.
  const [feed, setFeed] = useState<Record<string, unknown>[]>([])
  useEffect(() => {
    if (feedInicial.data) setFeed(feedInicial.data)
  }, [feedInicial.data])

  useEffect(() => {
    if (!notificar || eventos.length === 0) return
    const ultimo = eventos[0]
    if (ultimo.type !== 'order.created') return
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('Novo pedido', {
        body: `${ultimo.channel} — ${brl(String(ultimo.payload?.gross_amount ?? 0))}`,
      })
    }
  }, [eventos, notificar])

  const status = CONEXAO[estado]
  const linhas = [
    ...eventos.map((e) => ({
      chave: e.id,
      novo: true,
      tipo: e.type,
      canal: e.channel,
      externo: String(e.payload?.external_id ?? ''),
      titulo: String(e.payload?.title ?? ''),
      valor: String(e.payload?.gross_amount ?? '0'),
      situacao: String(e.payload?.status ?? ''),
      uf: String(e.payload?.ship_state ?? ''),
      quando: e.occurred_at,
    })),
    ...feed.map((p) => ({
      chave: `feed-${p.id}`,
      novo: false,
      tipo: String(p.type ?? 'order.created'),
      canal: String(p.channel ?? ''),
      externo: String(p.external_id ?? ''),
      titulo: String(p.title ?? ''),
      valor: String(p.gross_amount ?? '0'),
      situacao: String(p.status ?? ''),
      uf: String(p.ship_state ?? ''),
      quando: String(p.occurred_at ?? ''),
    })),
  ].slice(0, 120)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${status.classe}`}
          >
            <span
              aria-hidden
              className={`h-2 w-2 rounded-full bg-current ${status.pulsa ? 'animate-pulse' : ''}`}
            />
            {status.texto}
          </span>
          <span className="text-xs text-ink-muted">
            Atualização por eventos do marketplace — normalmente 2 a 6 segundos após a venda.
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn text-xs"
            onClick={async () => {
              if ('Notification' in window && Notification.permission !== 'granted') {
                await Notification.requestPermission()
              }
              setNotificar((v) => !v)
            }}
          >
            {notificar ? '🔔 Notificações ligadas' : '🔕 Ativar notificações'}
          </button>
          <button type="button" className="btn text-xs" onClick={limparFeed}>
            Limpar sessão
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard rotulo="Pedidos hoje" valor={pulso.data?.hoje.pedidos ?? 0} formato="numero" />
        <KpiCard rotulo="Receita hoje" valor={pulso.data?.hoje.receita_bruta ?? 0} formato="moeda" />
        <KpiCard rotulo="Líquido hoje" valor={pulso.data?.hoje.receita_liquida ?? 0} formato="moeda" />
        <KpiCard rotulo="Última hora" valor={pulso.data?.ultima_hora.pedidos ?? 0} formato="numero" />
        <KpiCard
          rotulo="Nesta sessão"
          valor={`${inteiro(contador.pedidos)} · ${brl(contador.receita)}`}
          dica="Contagem desde que esta tela foi aberta."
        />
      </div>

      <Secao titulo="Volume por minuto" descricao="Pedidos criados nos últimos 60 minutos.">
        {pulso.isLoading ? (
          <Carregando altura="h-32" />
        ) : (
          <GraficoPorMinuto dados={pulso.data?.por_minuto ?? []} />
        )}
      </Secao>

      <Secao titulo="Feed de eventos" descricao="Pedidos e eventos em ordem cronológica reversa.">
        {linhas.length === 0 ? (
          <Vazio
            titulo="Aguardando eventos"
            descricao="Assim que um pedido ou atualização chegar de um marketplace conectado, ele aparece aqui automaticamente."
          />
        ) : (
          <ul className="divide-y divide-line">
            {linhas.map((l) => (
              <li
                key={l.chave}
                className={`flex flex-wrap items-center gap-3 py-2.5 ${l.novo ? 'destacar' : ''}`}
              >
                <SeloCanal canal={l.canal} />
                <span className="num text-xs text-ink-muted">#{l.externo}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink" title={l.titulo}>
                  {l.titulo || '—'}
                </span>
                {l.uf && <span className="text-xs text-ink-muted">{l.uf}</span>}
                {l.situacao && <SeloStatus status={l.situacao} />}
                <span className="num text-sm font-medium text-ink">{brl(l.valor)}</span>
                <span className="w-16 text-right text-xs text-ink-muted">{desde(l.quando)}</span>
              </li>
            ))}
          </ul>
        )}
      </Secao>
    </div>
  )
}
