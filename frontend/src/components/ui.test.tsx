import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { KpiCard, SeloProcedencia, SeloStatus } from './ui'

describe('KpiCard', () => {
  it('formata o valor como moeda e mostra a variação com sinal', () => {
    render(<KpiCard rotulo="Receita bruta" valor="62580.40" variacao="12.5" formato="moeda" />)
    expect(screen.getByText('R$ 62.580,40')).toBeInTheDocument()
    // A seta carrega a informação mesmo para quem não distingue as cores.
    expect(screen.getByText(/▲/)).toBeInTheDocument()
  })

  it('indica queda quando a variação é negativa', () => {
    render(<KpiCard rotulo="Pedidos" valor={404} variacao="-8.2" formato="numero" />)
    expect(screen.getByText(/▼/)).toBeInTheDocument()
  })
})

describe('SeloProcedencia', () => {
  it('distingue valor liquidado de estimado — a diferença que evita o painel divergir do extrato', () => {
    const { rerender } = render(<SeloProcedencia fonte="settled" />)
    expect(screen.getByText('Liquidado')).toBeInTheDocument()

    rerender(<SeloProcedencia fonte="computed" />)
    expect(screen.getByText('Estimado')).toBeInTheDocument()
  })
})

describe('SeloStatus', () => {
  it('traduz o status canônico para português', () => {
    render(<SeloStatus status="delivered" />)
    expect(screen.getByText('Entregue')).toBeInTheDocument()
  })

  it('exibe o valor original quando o status é desconhecido', () => {
    render(<SeloStatus status="status_novo_do_canal" />)
    expect(screen.getByText('status_novo_do_canal')).toBeInTheDocument()
  })
})
