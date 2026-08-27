/** Hooks de dados (TanStack Query). Uma chave por recurso + filtros. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from './client'
import type {
  AdSpend,
  AdSpendIn,
  AnaliseMargens,
  Anuncio,
  Cascata,
  Conta,
  ContasAReceber,
  Coorte,
  CurvaABC,
  Despesa,
  Divergencia,
  DRE,
  Filtros,
  ListaPedidos,
  Mapeamento,
  MediaMovel,
  MesDRE,
  LinhaProduto,
  Pendencia,
  PontoSerie,
  Produto,
  Pulso,
  RegraImposto,
  ResumoCanal,
  ResumoTributario,
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

// --- Custos, impostos e DRE --------------------------------------------------

/** Toda escrita de custo, imposto ou despesa muda o lucro exibido em várias
 *  abas ao mesmo tempo. Invalidar só a própria lista deixaria o DRE e a visão
 *  geral mostrando o resultado anterior — por isso o cache inteiro cai. */
function useMutacaoDeCusto<TEntrada>(
  executar: (entrada: TEntrada) => Promise<unknown>,
) {
  const qc = useQueryClient()
  return useMutation<unknown, Error, TEntrada>({
    mutationFn: executar,
    onSuccess: () => qc.invalidateQueries(),
  })
}

export const useRegrasImposto = () =>
  useQuery({
    queryKey: ['tax-rules'],
    queryFn: () => api<RegraImposto[]>('/costs/tax-rules'),
    ...PADRAO,
  })

export const useDespesas = (mes?: string) =>
  useQuery({
    queryKey: ['expenses', mes ?? 'todas'],
    queryFn: () => api<Despesa[]>('/costs/expenses', { params: { mes } }),
    ...PADRAO,
  })

export const useDRE = (f: Filtros) =>
  useQuery({
    queryKey: ['dre', f],
    queryFn: () => api<DRE>('/costs/dre', { params: f }),
    ...PADRAO,
  })

export const useDREMensal = (meses = 12) =>
  useQuery({
    queryKey: ['dre-monthly', meses],
    queryFn: () => api<MesDRE[]>('/costs/dre/monthly', { params: { meses } }),
    ...PADRAO,
  })

export const useSalvarRegra = () =>
  useMutacaoDeCusto<{ id?: number } & Record<string, unknown>>(({ id, ...dados }) =>
    api(id ? `/costs/tax-rules/${id}` : '/costs/tax-rules', {
      method: id ? 'PATCH' : 'POST',
      body: JSON.stringify(dados),
    }),
  )

export const useRemoverRegra = () =>
  useMutacaoDeCusto<number>((id) => api(`/costs/tax-rules/${id}`, { method: 'DELETE' }))

export const useReapurarImpostos = () =>
  useMutacaoDeCusto<{ inicio?: string; fim?: string }>((params) =>
    api('/costs/tax-rules/apply', { method: 'POST', params }),
  )

export const useSalvarDespesa = () =>
  useMutacaoDeCusto<{ id?: number } & Record<string, unknown>>(({ id, ...dados }) =>
    api(id ? `/costs/expenses/${id}` : '/costs/expenses', {
      method: id ? 'PATCH' : 'POST',
      body: JSON.stringify(dados),
    }),
  )

export const useRemoverDespesa = () =>
  useMutacaoDeCusto<number>((id) => api(`/costs/expenses/${id}`, { method: 'DELETE' }))

export const useReplicarDespesas = () =>
  useMutacaoDeCusto<{ origem: string; destino: string }>((params) =>
    api('/costs/expenses/replicate', { method: 'POST', params }),
  )

// --- Edições e exclusões nas demais abas -------------------------------------

export const useSalvarProduto = () =>
  useMutacaoDeCusto<{ id?: number } & Record<string, unknown>>(({ id, ...dados }) =>
    api(id ? `/catalog/products/${id}` : '/catalog/products', {
      method: id ? 'PATCH' : 'POST',
      body: JSON.stringify(dados),
    }),
  )

export const useRemoverProduto = () =>
  useMutacaoDeCusto<number>((id) => api(`/catalog/products/${id}`, { method: 'DELETE' }))

export const useDesfazerMapeamento = () =>
  useMutacaoDeCusto<number>((id) => api(`/catalog/sku-links/${id}`, { method: 'DELETE' }))

export const useCustoEmLote = () =>
  useMutacaoDeCusto<{ sku: string; unit_cost: string; packaging_cost?: string }[]>((linhas) =>
    api('/catalog/products/bulk-cost', { method: 'POST', body: JSON.stringify(linhas) }),
  )

export const useMapeamentos = () =>
  useQuery({
    queryKey: ['sku-links'],
    queryFn: () => api<Mapeamento[]>('/catalog/sku-links'),
    ...PADRAO,
  })

export const useCustoDeMidia = () =>
  useMutacaoDeCusto<{ id: number; manual_media_cost: string }>(({ id, ...dados }) =>
    api(`/marketing/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify(dados) }),
  )

export const useResumoTributario = (f: Filtros) =>
  useQuery({
    queryKey: ['tax-summary', f],
    queryFn: () => api<ResumoTributario>('/costs/tax-summary', { params: f }),
    ...PADRAO,
  })

export const useContasAReceber = () =>
  useQuery({
    queryKey: ['receivables'],
    queryFn: () => api<ContasAReceber>('/finance/receivables'),
    ...PADRAO,
  })

export const useRatearFrete = () =>
  useMutacaoDeCusto<{
    frete_total: string
    outros_custos?: string
    criterio: 'quantidade' | 'valor'
    aplicar: boolean
    itens: { sku: string; quantidade: string; valor_total?: string }[]
  }>((dados) =>
    api('/catalog/products/freight-in', { method: 'POST', body: JSON.stringify(dados) }),
  )

export const useCurvaABC = (f: Filtros, limite = 500) =>
  useQuery({
    queryKey: ['abc', f, limite],
    queryFn: () => api<CurvaABC>('/reports/abc', { params: { ...f, limite } }),
    ...PADRAO,
  })

export const useCoorte = (meses = 12, channel?: string) =>
  useQuery({
    queryKey: ['cohort', meses, channel ?? 'todos'],
    queryFn: () => api<Coorte>('/reports/cohort', { params: { meses, channel } }),
    ...PADRAO,
  })

export const useMediaMovel = (f: Filtros, janela = 7) =>
  useQuery({
    queryKey: ['moving-average', f, janela],
    queryFn: () => api<MediaMovel>('/reports/moving-average', { params: { ...f, janela } }),
    ...PADRAO,
  })

// --- Margem por pedido --------------------------------------------------------

export const useMargens = (
  f: Filtros,
  opts: {
    recorte?: string
    ordem?: string
    busca?: string
    pagina?: number
    tamanho?: number
    incluir_cancelados?: boolean
  } = {},
) =>
  useQuery({
    queryKey: ['margens', f, opts],
    queryFn: () =>
      api<AnaliseMargens>('/orders/margins', { params: { ...f, ...opts } }),
    ...PADRAO,
    // Trocar de recorte/página mantém a tabela anterior visível no lugar de
    // piscar para o esqueleto — a comparação entre recortes é o uso típico.
    placeholderData: (anterior) => anterior,
  })

export const useAdSpends = (year?: number, month?: number, channel?: string) =>
  useQuery({
    queryKey: ['ad-spend', year, month, channel],
    queryFn: () =>
      api<AdSpend[]>('/costs/ad-spend', { params: { year, month, channel } }),
    ...PADRAO,
  })

export function useSalvarAdSpend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dados: AdSpendIn) =>
      api<AdSpend>('/costs/ad-spend', { method: 'PUT', body: JSON.stringify(dados) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ad-spend'] })
      // O rateio muda a margem de todos os pedidos da competência.
      qc.invalidateQueries({ queryKey: ['margens'] })
    },
  })
}

export function useRemoverAdSpend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api(`/costs/ad-spend/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ad-spend'] })
      qc.invalidateQueries({ queryKey: ['margens'] })
    },
  })
}
