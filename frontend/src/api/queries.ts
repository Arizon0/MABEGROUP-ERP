/** Hooks de dados (TanStack Query). Uma chave por recurso + filtros. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from './client'
import type {
  Anuncio,
  Cascata,
  Conta,
  Divergencia,
  Filtros,
  ListaPedidos,
  LinhaProduto,
  Pendencia,
  PontoSerie,
  Produto,
  Pulso,
  ResumoCanal,
  SaudeEstoque,
  Usuario,
  VisaoGeral,
} from '@/types/api'

/** Painéis com muitos cartões disparam várias consultas ao mesmo tempo; 30 s de
 *  frescor absorvem o F5 nervoso sem fazer o número parecer travado. */
const PADRAO = { staleTime: 30_000, refetchOnWindowFocus: false }

export const useUsuario = () =>
  useQuery({ queryKey: ['me'], queryFn: () => api<Usuario>('/auth/me'), staleTime: 300_000 })

export const useVisaoGeral = (f: Filtros) =>
  useQuery({
    queryKey: ['overview', f],
    queryFn: () => api<VisaoGeral>('/dashboard/overview', { params: f }),
    ...PADRAO,
  })

export const useSerie = (f: Filtros, granularidade: 'hour' | 'day' | 'month' = 'day') =>
  useQuery({
    queryKey: ['timeseries', f, granularidade],
    queryFn: () => api<PontoSerie[]>('/dashboard/timeseries', { params: { ...f, granularidade } }),
    ...PADRAO,
  })

export const usePorCanal = (f: Filtros) =>
  useQuery({
    queryKey: ['channels', f],
    queryFn: () => api<ResumoCanal[]>('/dashboard/channels', { params: f }),
    ...PADRAO,
  })

export const useRankingProdutos = (f: Filtros, limite = 20) =>
  useQuery({
    queryKey: ['ranking', f, limite],
    queryFn: () => api<LinhaProduto[]>('/dashboard/products', { params: { ...f, limite } }),
    ...PADRAO,
  })

export const usePorEstado = (f: Filtros) =>
  useQuery({
    queryKey: ['geo', f],
    queryFn: () =>
      api<{ estado: string; pedidos: number; receita_bruta: string }[]>('/dashboard/geo', {
        params: f,
      }),
    ...PADRAO,
  })

export const useMapaDeCalor = (f: Filtros) =>
  useQuery({
    queryKey: ['heatmap', f],
    queryFn: () =>
      api<{ dia_semana: number; hora: number; pedidos: number }[]>('/dashboard/heatmap', {
        params: f,
      }),
    ...PADRAO,
  })

export const usePedidos = (f: Filtros & { busca?: string; limite?: number; offset?: number }) =>
  useQuery({
    queryKey: ['orders', f],
    queryFn: () => api<ListaPedidos>('/orders', { params: f }),
    ...PADRAO,
  })

export const usePedido = (id: number | null) =>
  useQuery({
    queryKey: ['order', id],
    queryFn: () => api<Record<string, unknown>>(`/orders/${id}`),
    enabled: id !== null,
  })

export const useCascata = (f: Filtros) =>
  useQuery({
    queryKey: ['waterfall', f],
    queryFn: () => api<Cascata>('/finance/waterfall', { params: f }),
    ...PADRAO,
  })

export const useTaxas = (f: Filtros) =>
  useQuery({
    queryKey: ['fees', f],
    queryFn: () =>
      api<{ tipo: string; ocorrencias: number; valor: string; participacao_pct: string }[]>(
        '/finance/fees',
        { params: f },
      ),
    ...PADRAO,
  })

export const useConciliacao = (dias = 30) =>
  useQuery({
    queryKey: ['reconciliation', dias],
    queryFn: () =>
      api<{
        por_status: Record<string, { quantidade: number; divergencia: string }>
        total: number
        taxa_conciliacao_pct: string
      }>('/finance/reconciliation', { params: { dias } }),
    ...PADRAO,
  })

export const useDivergencias = () =>
  useQuery({
    queryKey: ['divergences'],
    queryFn: () => api<Divergencia[]>('/finance/divergences'),
    ...PADRAO,
  })

export const useFluxoDeCaixa = (dias = 30) =>
  useQuery({
    queryKey: ['cashflow', dias],
    queryFn: () =>
      api<{
        resumo: { total_liberado: string; total_previsto: string; total: string; dias: number }
        calendario: { data: string; liberado: string; previsto: string; pagamentos: number }[]
      }>('/finance/cashflow', { params: { dias } }),
    ...PADRAO,
  })

export const useAnuncios = (params: Record<string, unknown> = {}) =>
  useQuery({
    queryKey: ['listings', params],
    queryFn: () =>
      api<{ itens: Anuncio[]; total: number }>('/catalog/listings', { params }),
    ...PADRAO,
  })

export const useSaudeEstoque = (dias = 30) =>
  useQuery({
    queryKey: ['stock-health', dias],
    queryFn: () => api<SaudeEstoque>('/catalog/stock-health', { params: { dias } }),
    ...PADRAO,
  })

export const usePendencias = () =>
  useQuery({
    queryKey: ['sku-pendencies'],
    queryFn: () => api<Pendencia[]>('/catalog/sku-pendencies'),
    ...PADRAO,
  })

export const useProdutos = () =>
  useQuery({
    queryKey: ['products'],
    queryFn: () => api<Produto[]>('/catalog/products'),
    ...PADRAO,
  })

export const useLogistica = (dias = 30) =>
  useQuery({
    queryKey: ['logistics', dias],
    queryFn: () => api<Record<string, any>>('/logistics/overview', { params: { dias } }),
    ...PADRAO,
  })

export const useAtrasados = () =>
  useQuery({
    queryKey: ['delayed'],
    queryFn: () => api<Record<string, any>[]>('/logistics/delayed'),
    ...PADRAO,
  })

export const useAtendimento = (dias = 30) =>
  useQuery({
    queryKey: ['support', dias],
    queryFn: () => api<Record<string, any>>('/support/overview', { params: { dias } }),
    ...PADRAO,
  })

export const usePerguntas = (apenasPendentes = true) =>
  useQuery({
    queryKey: ['questions', apenasPendentes],
    queryFn: () =>
      api<Record<string, any>[]>('/support/questions', {
        params: { apenas_pendentes: apenasPendentes },
      }),
    ...PADRAO,
  })

export const useCampanhas = () =>
  useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Record<string, any>[]>('/marketing/campaigns'),
    ...PADRAO,
  })

export const useContas = () =>
  useQuery({ queryKey: ['accounts'], queryFn: () => api<Conta[]>('/accounts'), ...PADRAO })

export const useCanaisDisponiveis = () =>
  useQuery({
    queryKey: ['channels-available'],
    queryFn: () =>
      api<{
        modo_simulado: boolean
        canais: { channel: string; rotulo: string; configurado: boolean }[]
      }>('/accounts/channels'),
    staleTime: 600_000,
  })

export const usePulso = () =>
  useQuery({
    queryKey: ['pulse'],
    queryFn: () => api<Pulso>('/live/pulse'),
    // O SSE avisa que algo mudou, mas um intervalo curto garante que o painel
    // continue correto mesmo se a conexão cair sem o navegador perceber.
    refetchInterval: 60_000,
  })

export const useFeed = () =>
  useQuery({
    queryKey: ['feed'],
    queryFn: () => api<Record<string, any>[]>('/live/feed'),
    staleTime: 10_000,
  })

export const useMonitorIntegracao = () =>
  useQuery({
    queryKey: ['integration-monitor'],
    queryFn: () => api<Record<string, any>>('/settings/integration-monitor'),
    refetchInterval: 60_000,
  })

export const useWebhooks = (status?: string) =>
  useQuery({
    queryKey: ['webhooks', status],
    queryFn: () => api<Record<string, any>[]>('/settings/webhooks', { params: { status } }),
    ...PADRAO,
  })

export const useAuditoria = () =>
  useQuery({
    queryKey: ['audit'],
    queryFn: () => api<Record<string, any>[]>('/settings/audit'),
    ...PADRAO,
  })

export const useAlertas = () =>
  useQuery({
    queryKey: ['alerts'],
    queryFn: () => api<Record<string, any>[]>('/settings/alerts'),
    ...PADRAO,
  })

// --- Mutações ---------------------------------------------------------------

export function useConectarCanal() {
  return useMutation({
    mutationFn: (canal: string) =>
      api<{ authorization_url: string }>(`/oauth/${canal}/authorize`),
    onSuccess: (dados) => {
      // O redirecionamento precisa acontecer no navegador, não na chamada XHR.
      window.location.href = dados.authorization_url
    },
  })
}

export function useSincronizar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ contaId, completo }: { contaId: number; completo?: boolean }) =>
      api(`/accounts/${contaId}/sync`, { method: 'POST', params: { completo } }),
    onSuccess: () => qc.invalidateQueries(),
  })
}

export function useRevogarConta() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (contaId: number) => api(`/accounts/${contaId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  })
}

export function useRenovarToken() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (contaId: number) => api(`/accounts/${contaId}/refresh-token`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  })
}

export function useMapearSku() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dados: { channel: string; sku_channel: string; product_id: number }) =>
      api<{ mensagem: string; dados: Record<string, number> }>('/catalog/sku-links', {
        method: 'POST',
        body: JSON.stringify(dados),
      }),
    onSuccess: () => {
      // O mapeamento retroalimenta pedidos já importados: a margem muda em
      // vários painéis, então o cache inteiro é revalidado.
      qc.invalidateQueries()
    },
  })
}

export function useRodarConciliacao() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, number>({
    mutationFn: (dias: number) =>
      api('/finance/reconciliation/run', { method: 'POST', params: { dias } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reconciliation'] }),
  })
}

export function useReprocessarWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api(`/settings/webhooks/${id}/reprocess`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks'] }),
  })
}
