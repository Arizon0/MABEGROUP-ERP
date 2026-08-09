import { useState } from 'react'

import { login } from '@/api/client'

export function Login({ aoEntrar }: { aoEntrar: () => void }) {
  const [email, setEmail] = useState('admin@marketplacehub.com.br')
  const [senha, setSenha] = useState('admin123')
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)

  async function enviar(e: React.FormEvent) {
    e.preventDefault()
    setErro('')
    setCarregando(true)
    try {
      await login(email, senha)
      aoEntrar()
    } catch (exc) {
      setErro(exc instanceof Error ? exc.message : 'Não foi possível entrar.')
    } finally {
      setCarregando(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-sunken p-4">
      <form onSubmit={enviar} className="card w-full max-w-sm space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-ink">◆ Marketplace Hub</h1>
          <p className="card-sub mt-1">
            Vendas consolidadas de Mercado Livre e Shopee.
          </p>
        </div>

        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-soft">E-mail</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input w-full"
            autoComplete="username"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-soft">Senha</span>
          <input
            type="password"
            required
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            className="input w-full"
            autoComplete="current-password"
          />
        </label>

        {erro && (
          <p className="rounded-lg border border-bad-line bg-bad-soft px-3 py-2 text-xs text-bad">
            {erro}
          </p>
        )}

        <button type="submit" disabled={carregando} className="btn btn-primary w-full justify-center">
          {carregando ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </div>
  )
}
