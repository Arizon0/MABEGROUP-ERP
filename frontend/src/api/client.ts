/**
 * Cliente HTTP tipado da API.
 *
 * Concentra três responsabilidades que, espalhadas pelos componentes, viram
 * bug: anexar o token, renovar a sessão quando ele expira, e traduzir o erro
 * padronizado do backend numa mensagem legível.
 */

const BASE = import.meta.env.VITE_API_URL ?? ''
const PREFIXO = '/api/v1'

const CHAVE_ACCESS = 'mh.access_token'
const CHAVE_REFRESH = 'mh.refresh_token'

export const sessao = {
  access: () => localStorage.getItem(CHAVE_ACCESS),
  refresh: () => localStorage.getItem(CHAVE_REFRESH),
  gravar(access: string, refresh?: string | null) {
    localStorage.setItem(CHAVE_ACCESS, access)
    if (refresh) localStorage.setItem(CHAVE_REFRESH, refresh)
  },
  limpar() {
    localStorage.removeItem(CHAVE_ACCESS)
    localStorage.removeItem(CHAVE_REFRESH)
  },
  autenticado: () => Boolean(localStorage.getItem(CHAVE_ACCESS)),
}

export class ErroApi extends Error {
  constructor(
    public status: number,
    public codigo: string,
    mensagem: string,
    public detalhes: Record<string, unknown> = {},
  ) {
    super(mensagem)
    this.name = 'ErroApi'
  }
}

/** Renovação em voo, compartilhada: várias requisições paralelas expirando ao
 *  mesmo tempo devem disparar um único refresh, não um por requisição. */
let renovacaoEmCurso: Promise<boolean> | null = null

async function renovarSessao(): Promise<boolean> {
  const refresh = sessao.refresh()
  if (!refresh) return false

  renovacaoEmCurso ??= (async () => {
    try {
      const resp = await fetch(`${BASE}${PREFIXO}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!resp.ok) {
        sessao.limpar()
        return false
      }
      const dados = await resp.json()
      sessao.gravar(dados.access_token, dados.refresh_token)
      return true
    } catch {
      return false
    } finally {
      renovacaoEmCurso = null
    }
  })()

  return renovacaoEmCurso
}

type Opcoes = RequestInit & { params?: Record<string, unknown>; semAuth?: boolean }

export async function api<T = unknown>(caminho: string, opcoes: Opcoes = {}): Promise<T> {
  const { params, semAuth, ...init } = opcoes

  const url = new URL(`${BASE}${PREFIXO}${caminho}`, window.location.origin)
  for (const [chave, valor] of Object.entries(params ?? {})) {
    if (valor !== undefined && valor !== null && valor !== '') {
      url.searchParams.set(chave, String(valor))
    }
  }

  const executar = async (): Promise<Response> => {
    const headers = new Headers(init.headers)
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    const token = sessao.access()
    if (token && !semAuth) headers.set('Authorization', `Bearer ${token}`)
    return fetch(url.toString(), { ...init, headers })
  }

  let resp = await executar()

  // Uma única tentativa de renovação: se o refresh também falhar, insistir só
  // atrasaria o redirecionamento para o login.
  if (resp.status === 401 && !semAuth && (await renovarSessao())) {
    resp = await executar()
  }

  if (resp.status === 401 && !semAuth) {
    sessao.limpar()
    window.dispatchEvent(new CustomEvent('mh:sessao-expirada'))
  }

  if (!resp.ok) {
    let codigo = 'erro_http'
    let mensagem = `Falha na requisição (HTTP ${resp.status}).`
    let detalhes: Record<string, unknown> = {}
    try {
      const corpo = await resp.json()
      if (corpo?.erro) {
        codigo = corpo.erro.codigo ?? codigo
        mensagem = corpo.erro.mensagem ?? mensagem
        detalhes = corpo.erro.detalhes ?? {}
      }
    } catch {
      /* resposta sem corpo JSON — mantém a mensagem genérica */
    }
    throw new ErroApi(resp.status, codigo, mensagem, detalhes)
  }

  if (resp.status === 204) return undefined as T
  const tipo = resp.headers.get('content-type') ?? ''
  return (tipo.includes('json') ? await resp.json() : await resp.text()) as T
}

export const urlSse = (): string => {
  // EventSource não aceita cabeçalhos: o token vai na query string, tratado
  // pela dependência de autenticação do backend.
  const token = sessao.access() ?? ''
  return `${BASE}${PREFIXO}/live/stream?token=${encodeURIComponent(token)}`
}

export function urlExportacao(caminho: string, params: Record<string, unknown> = {}): string {
  const url = new URL(`${BASE}${PREFIXO}${caminho}`, window.location.origin)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
  }
  url.searchParams.set('token', sessao.access() ?? '')
  return url.toString()
}

export async function login(email: string, senha: string) {
  const dados = await api<{
    access_token: string
    refresh_token: string
    user: Record<string, unknown>
  }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, senha }),
    semAuth: true,
  })
  sessao.gravar(dados.access_token, dados.refresh_token)
  return dados
}

export async function logout() {
  const refresh = sessao.refresh()
  if (refresh) {
    await api('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => undefined)
  }
  sessao.limpar()
}
