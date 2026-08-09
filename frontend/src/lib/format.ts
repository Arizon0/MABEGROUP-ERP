/**
 * Formatação para leitura humana.
 *
 * Os valores monetários chegam da API como **string**, de propósito: JSON não
 * tem decimal exato e converter para `number` no transporte reintroduziria o
 * erro de arredondamento que o backend evita com `Decimal`. A conversão para
 * número acontece só aqui, na borda de apresentação.
 */

const MOEDA = new Intl.NumberFormat('pt-BR', {
  style: 'currency',
  currency: 'BRL',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const COMPACTO = new Intl.NumberFormat('pt-BR', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

const INTEIRO = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 })

export const num = (valor: string | number | null | undefined): number => {
  if (valor === null || valor === undefined || valor === '') return 0
  const n = typeof valor === 'number' ? valor : Number(valor)
  return Number.isFinite(n) ? n : 0
}

/**
 * O `Intl` insere espaço não-quebrável (U+00A0) depois de "R$". Ao copiar um
 * valor do painel para uma planilha, esse caractere invisível faz a célula ser
 * lida como texto em vez de número — então normalizamos para espaço comum.
 */
export const brl = (valor: string | number | null | undefined): string =>
  MOEDA.format(num(valor)).replace(/ /g, ' ')

/** Versão curta para eixos e cartões estreitos: R$ 62,6 mil. */
export const brlCurto = (valor: string | number | null | undefined): string =>
  `R$ ${COMPACTO.format(num(valor))}`

export const inteiro = (valor: string | number | null | undefined): string =>
  INTEIRO.format(num(valor))

export const pct = (valor: string | number | null | undefined, casas = 1): string =>
  `${num(valor).toFixed(casas).replace('.', ',')}%`

export const dataHora = (valor: string | Date | null | undefined): string => {
  if (!valor) return '—'
  const d = typeof valor === 'string' ? new Date(valor) : valor
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

export const data = (valor: string | Date | null | undefined): string => {
  if (!valor) return '—'
  const d = typeof valor === 'string' ? new Date(valor) : valor
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('pt-BR')
}

/** "há 3 min" — usado no feed ao vivo, onde a hora exata importa menos que a recência. */
export const desde = (valor: string | Date | null | undefined): string => {
  if (!valor) return '—'
  const d = typeof valor === 'string' ? new Date(valor) : valor
  const seg = Math.floor((Date.now() - d.getTime()) / 1000)
  if (seg < 60) return 'agora'
  if (seg < 3600) return `há ${Math.floor(seg / 60)} min`
  if (seg < 86400) return `há ${Math.floor(seg / 3600)} h`
  return `há ${Math.floor(seg / 86400)} d`
}

export const isoDia = (d: Date): string => d.toISOString().slice(0, 10)

export const diasAtras = (dias: number): Date => {
  const d = new Date()
  d.setDate(d.getDate() - dias)
  return d
}

export const ROTULO_CANAL: Record<string, string> = {
  mercadolivre: 'Mercado Livre',
  mercadopago: 'Mercado Pago',
  shopee: 'Shopee',
}

export const ROTULO_STATUS: Record<string, string> = {
  pending: 'Pendente',
  paid: 'Pago',
  processing: 'Processando',
  shipped: 'Enviado',
  delivered: 'Entregue',
  cancelled: 'Cancelado',
  returned: 'Devolvido',
  ready_to_ship: 'Pronto para envio',
  in_transit: 'Em trânsito',
  not_delivered: 'Não entregue',
}

export const ROTULO_LOGISTICA: Record<string, string> = {
  fulfillment: 'ML Full',
  self_service: 'ML Flex',
  cross_docking: 'ML Coleta',
  drop_off: 'Agência/Correios',
  shopee_xpress: 'Shopee Xpress',
  other: 'Outro',
}

/** Cor categórica do canal — ordem fixa, nunca gerada dinamicamente. */
export const CORES_CANAL: Record<string, string> = {
  mercadolivre: 'var(--series-1)',
  shopee: 'var(--series-2)',
  mercadopago: 'var(--series-3)',
}

export const corDoCanal = (canal: string): string => CORES_CANAL[canal] ?? 'var(--text-muted)'
