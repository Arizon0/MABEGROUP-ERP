import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import type { AnaliseMargens, PedidoMargem } from '@/types/api'

import { Margens } from './Margens'

vi.mock('@/api/client', async (original) => {
  const mod = (await original()) as Record<string, unknown>
  return { ...mod, api: vi.fn() }
})
const mockApi = vi.mocked(api)

function pedido(over: Partial<PedidoMargem> = {}): PedidoMargem {
  return {
    id: 1,
    external_id: '2000001',
    channel: 'mercadolivre',
    date_created: '2026-07-31T10:00:00Z',
    status: 'delivered',
    titulo: 'Retentor Volante',
    skus: ['5338'],
    logistic_type: 'fulfillment',
    has_multiple_items: false,
    itens: 1,
    total: '33.87',
    custo: '16.02',
    frete: '7.95',
    comissao: '4.06',
    liquido: '21.86',
    net_source: 'settled',
    ads: '8.79',
    acos_pct: null,
    tacos_pct: '25.95',
    imposto: '2.78',
    margem_valor: '-5.73',
    margem_pct: '-16.92',
    diferenca_liquido: '0.00',
    alertas: [],
    ...over,
  }
}

const RESPOSTA: AnaliseMargens = {
  filtros: { recorte: 'todos', ordem: 'data', busca: null, incluir_cancelados: false },
  resumo: {
    pedidos: 709,
    negativos: 248,
    pct_negativos: '35.00',
    para_revisar: 37,
    total: '530.70',
    custo: '268.08',
    frete: '96.00',
    comissao: '62.86',
    ads: '32.11',
    imposto: '43.62',
    liquido: '371.84',
    margem_valor: '28.03',
    margem_pct: '5.28',
    prejuizo_dos_negativos: '-5.73',
    ads_nao_alocado: '0.00',
  },
  contagem_por_recorte: {
    todos: 709, negativos: 248, 'sem-custo': 12, 'sem-comissao': 3,
    'sem-frete': 8, pacotes: 20, revisar: 37,
  },
  paginacao: { pagina: 1, tamanho: 50, total: 709, paginas: 15, de: 1, ate: 50 },
  pedidos: [
    pedido(),
    pedido({
      id: 2, external_id: '2000002', titulo: 'Jogo de Anéis', skus: ['8126'],
      total: '135.87', custo: '76.02', ads: '9.35', acos_pct: '10.49',
      tacos_pct: '6.88', imposto: '11.17', margem_valor: '3.78', margem_pct: '2.78',
    }),
  ],
}

function montar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Margens />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function respostaPara(caminho: string): unknown {
  if (caminho === '/orders/margins') return RESPOSTA
  if (caminho === '/costs/ad-spend') return []
  return {}
}

describe('Margens', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.mockImplementation(async (caminho: string) => respostaPara(caminho))
  })

  it('mostra a linha-resumo com pedidos, negativos e a janela', async () => {
    montar()
    const resumo = await screen.findByRole('status', { name: 'Resumo do recorte' })
    expect(resumo).toHaveTextContent('709')
    expect(resumo).toHaveTextContent('248 negativos (35%)')
    expect(resumo).toHaveTextContent('37 a revisar')
    expect(resumo).toHaveTextContent('mostrando 1–50')
  })

  it('renderiza cada custo do pedido na sua coluna', async () => {
    montar()
    const linha = (await screen.findByText('#2000001')).closest('tr')!
    const celulas = within(linha).getAllByRole('cell').map((c) => c.textContent?.trim())
    // Pedido, Data, Canal, Margem, Total, Custo, Frete, Comissão, Ads, ACOS, TACOS, Imposto
    expect(celulas[4]).toBe('R$ 33,87')
    expect(celulas[5]).toBe('R$ 16,02')
    expect(celulas[6]).toBe('R$ 7,95')
    expect(celulas[7]).toBe('R$ 4,06')
    expect(celulas[8]).toBe('R$ 8,79')
    expect(celulas[9]).toBe('—')          // ACOS sem receita atribuída
    expect(celulas[10]).toBe('25,95%')    // TACOS
    expect(celulas[11]).toBe('R$ 2,78')
  })

  it('mostra ACOS apenas quando o canal atribuiu receita', async () => {
    montar()
    const comAcos = (await screen.findByText('#2000002')).closest('tr')!
    expect(within(comAcos).getAllByRole('cell')[9]).toHaveTextContent('10,49%')
  })

  it('a pastilha de margem negativa aponta para baixo', async () => {
    montar()
    const linha = (await screen.findByText('#2000001')).closest('tr')!
    expect(within(linha).getByText(/-16,92%/)).toHaveTextContent('↘')
  })

  it('pede o recorte escolhido e volta para a primeira página', async () => {
    const user = userEvent.setup()
    montar()
    await screen.findByRole('status', { name: 'Resumo do recorte' })

    await user.click(screen.getByRole('button', { name: /Só negativos/ }))

    await waitFor(() => {
      const chamadas = mockApi.mock.calls.filter(([c]) => c === '/orders/margins')
      const ultima = chamadas.at(-1)![1] as { params: Record<string, unknown> }
      expect(ultima.params).toMatchObject({ recorte: 'negativos', pagina: 1 })
    })
  })

  it('mostra no chip quantos pedidos o recorte esconde', async () => {
    montar()
    await screen.findByText('#2000001') // espera os dados chegarem
    expect(screen.getByRole('button', { name: /Só negativos/ })).toHaveTextContent('248')
  })

  it('marca o recorte ativo para leitores de tela', async () => {
    montar()
    const todos = await screen.findByRole('button', { name: 'Todos 709' })
    expect(todos).toHaveAttribute('aria-pressed', 'true')
  })

  it('a busca só dispara ao enviar o formulário', async () => {
    const user = userEvent.setup()
    montar()
    await screen.findByRole('status', { name: 'Resumo do recorte' })
    const chamadasAntes = mockApi.mock.calls.length

    await user.type(screen.getByLabelText('Buscar pedidos'), '5338')
    expect(mockApi.mock.calls.length).toBe(chamadasAntes) // digitar não consulta

    await user.click(screen.getByRole('button', { name: 'Buscar' }))
    await waitFor(() => {
      const chamadas = mockApi.mock.calls.filter(([c]) => c === '/orders/margins')
      const ultima = chamadas.at(-1)![1] as { params: Record<string, unknown> }
      expect(ultima.params).toMatchObject({ busca: '5338' })
    })
  })

  it('avisa quando nenhum imposto está sendo aplicado', async () => {
    mockApi.mockImplementation(async (caminho: string) => {
      if (caminho === '/orders/margins') {
        return { ...RESPOSTA, resumo: { ...RESPOSTA.resumo, imposto: '0.00' } }
      }
      return respostaPara(caminho)
    })
    montar()
    expect(await screen.findByText(/Configure a regra tributária/)).toBeInTheDocument()
  })

  it('avisa quando sobra verba de Ads sem ratear', async () => {
    mockApi.mockImplementation(async (caminho: string) => {
      if (caminho === '/orders/margins') {
        return { ...RESPOSTA, resumo: { ...RESPOSTA.resumo, ads_nao_alocado: '150.00' } }
      }
      return respostaPara(caminho)
    })
    montar()
    expect(await screen.findByText(/não foram rateados/)).toBeInTheDocument()
  })

  it('explica o alerta de líquido divergente no título', async () => {
    mockApi.mockImplementation(async (caminho: string) => {
      if (caminho === '/orders/margins') {
        return {
          ...RESPOSTA,
          pedidos: [pedido({ alertas: ['liquido_diverge'] })],
        }
      }
      return respostaPara(caminho)
    })
    montar()
    const marca = await screen.findByText(/liquido diverge/)
    expect(marca).toHaveAttribute('title', expect.stringContaining('não fecha com o líquido'))
  })

  it('lança um investimento em Ads pelo painel', async () => {
    const user = userEvent.setup()
    montar()
    await screen.findByRole('status', { name: 'Resumo do recorte' })

    await user.type(screen.getByLabelText(/Investido R\$/), '150,50')
    await user.click(screen.getByRole('button', { name: 'Lançar' }))

    await waitFor(() => {
      const chamada = mockApi.mock.calls.find(
        ([c, o]) => c === '/costs/ad-spend' && (o as RequestInit)?.method === 'PUT',
      )
      expect(chamada).toBeDefined()
      const corpo = JSON.parse((chamada![1] as RequestInit).body as string)
      expect(corpo).toMatchObject({ amount: '150.50', scope: 'channel', reference: '' })
    })
  })

  it('desabilita a referência quando o escopo é o canal inteiro', async () => {
    const user = userEvent.setup()
    montar()
    await screen.findByRole('status', { name: 'Resumo do recorte' })
    expect(screen.getByLabelText(/Referência/)).toBeDisabled()
    await user.selectOptions(screen.getByLabelText(/Escopo/), 'listing')
    expect(screen.getByLabelText(/Referência/)).toBeEnabled()
  })

  it('informa quando o recorte está vazio', async () => {
    mockApi.mockImplementation(async (caminho: string) => {
      if (caminho === '/orders/margins') {
        return {
          ...RESPOSTA,
          resumo: { ...RESPOSTA.resumo, pedidos: 0, negativos: 0, para_revisar: 0 },
          paginacao: { pagina: 1, tamanho: 50, total: 0, paginas: 0, de: 0, ate: 0 },
          pedidos: [],
        }
      }
      return respostaPara(caminho)
    })
    montar()
    expect(await screen.findByText('Nenhum pedido neste recorte')).toBeInTheDocument()
  })
})
