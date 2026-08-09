import { describe, expect, it } from 'vitest'

import { brl, desde, inteiro, num, pct } from './format'

describe('conversão de valores monetários', () => {
  it('lê o decimal que vem como string da API sem perder centavos', () => {
    // O backend envia string de propósito: JSON não tem decimal exato.
    expect(num('62580.40')).toBe(62580.4)
    expect(brl('62580.40')).toBe('R$ 62.580,40')
  })

  it('trata ausência de valor como zero, em vez de exibir NaN', () => {
    expect(num(null)).toBe(0)
    expect(num(undefined)).toBe(0)
    expect(num('')).toBe(0)
    expect(brl(null)).toBe('R$ 0,00')
  })

  it('não quebra com valor não numérico vindo da API', () => {
    expect(num('indisponível')).toBe(0)
  })

  it('formata percentual no padrão brasileiro', () => {
    expect(pct('18.44')).toBe('18,4%')
    expect(pct('18.44', 2)).toBe('18,44%')
  })

  it('formata inteiros com separador de milhar', () => {
    expect(inteiro('1234')).toBe('1.234')
  })
})

describe('tempo relativo do feed ao vivo', () => {
  it('mostra "agora" para eventos recém-chegados', () => {
    expect(desde(new Date())).toBe('agora')
  })

  it('conta em minutos dentro da primeira hora', () => {
    expect(desde(new Date(Date.now() - 5 * 60_000))).toBe('há 5 min')
  })

  it('devolve travessão quando não há data', () => {
    expect(desde(null)).toBe('—')
  })
})
