/**
 * Assinatura do stream SSE do painel ao vivo.
 *
 * Divisão de responsabilidade deliberada: **o evento avisa que algo mudou; o
 * REST diz qual é o número certo.** Manter agregados financeiros somando deltas
 * no cliente acumula erro de arredondamento e diverge do banco em minutos — por
 * isso o handler empurra o item no feed (estado local, barato) e invalida os
 * caches afetados, deixando o servidor recalcular os totais.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { urlSse } from '@/api/client'
import type { EventoAoVivo } from '@/types/api'

const TIPOS = [
  'order.created',
  'order.updated',
  'order.cancelled',
  'shipment.updated',
  'payment.approved',
  'question.received',
  'claim.opened',
  'sync.completed',
  'alert.raised',
] as const

const LIMITE_FEED = 100

export type EstadoConexao = 'conectando' | 'ao-vivo' | 'reconectando' | 'offline'

export function useLive(ativo = true) {
  const qc = useQueryClient()
  const [eventos, setEventos] = useState<EventoAoVivo[]>([])
  const [estado, setEstado] = useState<EstadoConexao>('conectando')
  const [contador, setContador] = useState({ pedidos: 0, receita: 0 })
  const fonteRef = useRef<EventSource | null>(null)

  const registrar = useCallback(
    (evento: EventoAoVivo) => {
      setEventos((atual) => [evento, ...atual].slice(0, LIMITE_FEED))

      if (evento.type === 'order.created') {
        const bruto = Number(evento.payload?.gross_amount ?? 0)
        setContador((c) => ({
          pedidos: c.pedidos + 1,
          receita: c.receita + (Number.isFinite(bruto) ? bruto : 0),
        }))
      }

      const afetados: Record<string, string[]> = {
        'order.created': ['overview', 'timeseries', 'channels', 'orders', 'pulse', 'ranking'],
        'order.updated': ['overview', 'orders', 'pulse'],
        'order.cancelled': ['overview', 'timeseries', 'orders', 'pulse'],
        'payment.approved': ['overview', 'waterfall', 'cashflow', 'reconciliation'],
        'shipment.updated': ['logistics', 'delayed', 'orders'],
        'question.received': ['support', 'questions'],
        'claim.opened': ['support'],
        'sync.completed': ['accounts', 'integration-monitor'],
        'alert.raised': ['alerts'],
      }
      for (const chave of afetados[evento.type] ?? []) {
        qc.invalidateQueries({ queryKey: [chave] })
      }
    },
    [qc],
  )

  useEffect(() => {
    if (!ativo) return

    let fechado = false
    setEstado('conectando')

    const fonte = new EventSource(urlSse())
    fonteRef.current = fonte

    fonte.addEventListener('connected', () => setEstado('ao-vivo'))

    for (const tipo of TIPOS) {
      fonte.addEventListener(tipo, (e) => {
        try {
          registrar(JSON.parse((e as MessageEvent).data) as EventoAoVivo)
        } catch {
          /* evento malformado: ignorar sem derrubar o stream */
        }
      })
    }

    fonte.onerror = () => {
      if (fechado) return
      // O EventSource reconecta sozinho. Ao voltar, revalidamos tudo para
      // recuperar o que aconteceu durante a queda — o stream não reenvia o
      // que passou.
      setEstado('reconectando')
      if (fonte.readyState === EventSource.CLOSED) setEstado('offline')
      qc.invalidateQueries()
    }

    return () => {
      fechado = true
      fonte.close()
      fonteRef.current = null
    }
  }, [ativo, registrar, qc])

  const limparFeed = useCallback(() => setEventos([]), [])

  return { eventos, estado, contador, limparFeed }
}
