/** Contratos da API. Valores monetários trafegam como string (ver lib/format). */

export type Canal = 'mercadolivre' | 'mercadopago' | 'shopee'

export interface Kpi {
  valor: string | number
  anterior: string | number
  variacao_pct: string
}

export interface VisaoGeral {
  periodo: { inicio: string; fim: string; dias: number }
  kpis: Record<string, Kpi>
  derivados: {
    taxa_efetiva_pct: string
    margem_contribuicao: string
    margem_pct: string
    taxa_cancelamento_pct: string
  }
  por_status: Record<string, number>
}

export interface PontoSerie {
  bucket: string
  pedidos: number
  receita_bruta: string
  receita_liquida: string
  cancelados: number
}

export interface ResumoCanal {
  channel: Canal
  pedidos: number
  receita_bruta: string
  receita_liquida: string
  taxas: string
  ticket_medio: string
  taxa_efetiva_pct: string
}

export interface LinhaProduto {
  sku: string
  titulo: string
  unidades: string
  receita_bruta: string
  cmv: string
  margem_bruta: string
  margem_pct: string
  pedidos: number
}

export interface Pedido {
  id: number
  external_id: string
  channel: Canal
  status: string
  status_raw: string
  date_created: string
  gross_amount: string
  net_amount: string
  net_source: 'computed' | 'api_reported' | 'settled'
  platform_fee: string
  payment_fee: string
  shipping_cost: string
  logistic_type: string
  ship_state: string
  buyer_nickname: string
  itens_count: number
  titulo: string
}

export interface ListaPedidos {
  itens: Pedido[]
  total: number
  limite: number
  offset: number
}

export interface EtapaCascata {
  nome: string
  valor: string
  tipo: 'inicio' | 'positivo' | 'negativo' | 'total'
}

export interface Cascata {
  periodo: { inicio: string; fim: string }
  etapas: EtapaCascata[]
  totais: Record<string, string>
  por_procedencia: { fonte: string; rotulo: string; pedidos: number; valor: string }[]
}

export interface Divergencia {
  order_id: number
  external_id: string
  channel: Canal
  date_created: string
  gross_amount: string
  expected_net: string
  settled_net: string
  divergence: string
  divergence_pct: string
  notes: string
}

export interface Anuncio {
  id: number
  external_id: string
  channel: Canal
  title: string
  sku_channel: string
  status: string
  listing_type: string
  price: string
  available_quantity: number
  sold_quantity: number
  visits_30d: number
  conversao_pct: string
  health: string | null
  thumbnail: string
  permalink: string
  em_ruptura: boolean
}

export interface SaudeEstoque {
  periodo_dias: number
  resumo: { ruptura: number; criticos: number; parados: number; saudaveis: number }
  ruptura: LinhaEstoque[]
  criticos: LinhaEstoque[]
  parados: LinhaEstoque[]
}

export interface LinhaEstoque {
  id: number
  external_id: string
  channel: Canal
  title: string
  sku_channel: string
  estoque: number
  vendas_periodo: string
  media_diaria: string
  cobertura_dias: string | null
}

export interface Conta {
  id: number
  channel: Canal
  nickname: string
  external_account_id: string
  status: 'connected' | 'expired' | 'revoked' | 'error'
  connected_at: string | null
  last_sync_at: string | null
  last_error: string
  token_expires_at: string | null
  refresh_expires_at: string | null
  has_credential: boolean
  cursors: {
    resource: string
    last_synced_at: string | null
    status: string
    failures: number
    progress_pct: number
    last_error: string
  }[]
}

export interface EventoAoVivo {
  id: string
  type: string
  tenant_id: number
  channel: string
  account_id: number | null
  occurred_at: string
  payload: Record<string, unknown>
}

export interface Pulso {
  hoje: { pedidos: number; receita_bruta: string; receita_liquida: string }
  ultima_hora: { pedidos: number }
  por_minuto: { bucket: string; pedidos: number; receita: string }[]
  agora: string
}

export interface Pendencia {
  id: number
  channel: Canal
  sku_channel: string
  sample_title: string
  occurrences: number
  last_seen_at: string | null
}

export interface Produto {
  id: number
  sku: string
  name: string
  brand: string
  unit_cost: string
  freight_in_cost: string
  other_acquisition_cost: string
  packaging_cost: string
  custo_aquisicao: string
  custo_total_unitario: string
  is_active: boolean
}

export interface Mapeamento {
  id: number
  channel: Canal
  sku_channel: string
  product_id: number
  product_sku: string
  product_name: string
  confidence: string
}

export interface Usuario {
  id: number
  email: string
  full_name: string
  role: 'owner' | 'admin' | 'analyst' | 'viewer'
  tenant: { id: number; name: string; slug: string; plan: string } | null
}

export interface Filtros {
  // Índice aberto porque o objeto vai direto para a query string da API.
  [chave: string]: unknown
  inicio?: string
  fim?: string
  channel?: string
  account_id?: number
  status?: string
  logistic_type?: string
  state?: string
}

// --- Custos, impostos e DRE --------------------------------------------------

export interface Faixa {
  id?: number
  rbt12_ate: string
  aliquota_nominal_pct: string
  parcela_deduzir: string
}

export interface RegraImposto {
  id: number
  name: string
  kind: string
  rate_pct: string
  regime: 'fixed' | 'simples_progressive'
  annex: string
  brackets: Faixa[]
  base: 'gross_revenue' | 'gross_plus_shipping' | 'net_revenue' | string
  channel: string
  valid_from: string
  valid_to: string | null
  is_active: boolean
  notes: string
}

export interface Despesa {
  id: number
  description: string
  category: string
  amount: string
  competence_month: string
  is_recurring: boolean
  channel: string
  notes: string
}

export interface LinhaDRE {
  rotulo: string
  valor: string
  tipo: 'receita' | 'deducao' | 'subtotal' | 'resultado'
  percentual: string
  detalhe: string
}

export interface DRE {
  periodo: { inicio: string; fim: string }
  linhas: LinhaDRE[]
  indicadores: {
    pedidos: number
    unidades: string
    ticket_medio: string
    margem_contribuicao: string
    margem_contribuicao_pct: string
    lucro_operacional: string
    lucro_operacional_pct: string
    lucro_por_pedido: string
    ponto_de_equilibrio: string
    taxa_efetiva_canal_pct: string
    carga_tributaria_pct: string
  }
  despesas_por_categoria: Record<string, string>
  qualidade: {
    itens_sem_custo: number
    pedidos_sem_imposto: number
    confiavel: boolean
    aviso: string
  }
}

export interface MesDRE {
  mes: string
  receita_bruta: string
  liquido: string
  margem_contribuicao: string
  lucro_operacional: string
  lucro_pct: string
  pedidos: number
}

export interface ResumoTributario {
  aliquota_efetiva_pct: string
  rbt12: {
    acumulado: string
    meses_de_historico: number
    proporcionalizada: string
    observacao: string
  }
  regras_vigentes: {
    id: number
    name: string
    regime: string
    annex: string
    base: string
    aliquota_aplicada_pct: string
    rbt12: string | null
    excedeu_teto_do_simples: boolean
  }[]
  soma_aliquotas_pct: string
  alerta: string
}

export interface ContasAReceber {
  resumo: { total_a_receber: string; total_ja_liberado: string; atualizado_em: string }
  por_faixa: Record<string, string>
  por_provedor: {
    provedor: string
    pendente: string
    liberado: string
    pagamentos: number
    contas: number
  }[]
  observacao: string
}

// --- Análises ----------------------------------------------------------------

export interface ItemABC {
  posicao: number
  sku: string
  titulo: string
  classe: 'A' | 'B' | 'C'
  unidades: string
  receita_bruta: string
  margem_bruta: string
  participacao_pct: string
  acumulado_pct: string
}

export interface CurvaABC {
  total_receita: string
  total_itens: number
  itens: ItemABC[]
  resumo: {
    classe: string
    itens: number
    itens_pct: string
    receita: string
    receita_pct: string
    margem: string
  }[]
}

export interface Coorte {
  coortes: {
    coorte: string
    base: number
    periodos: { offset: number; compradores: number; retencao_pct: string; receita: string }[]
  }[]
  cobertura: {
    pedidos_com_comprador_pct: string
    pedidos_sem_comprador: number
    aviso: string
  }
}

export interface MediaMovel {
  janela: number
  pontos: {
    bucket: string
    pedidos: number
    receita_bruta: string
    receita_liquida: string
    cancelados: number
    media_movel_receita: string | null
    media_movel_pedidos: string | null
  }[]
  tendencia: {
    direcao: 'alta' | 'queda' | 'estável' | 'indefinida'
    variacao_pct: string
    media_atual?: string
    media_inicial?: string
  }
}
