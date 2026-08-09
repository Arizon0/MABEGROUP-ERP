import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { logout, sessao } from '@/api/client'
import { useUsuario } from '@/api/queries'
import { Login } from '@/pages/Login'
import { AoVivo } from '@/pages/AoVivo'
import { Configuracoes } from '@/pages/Configuracoes'
import { Faturamento } from '@/pages/Faturamento'
import { Logistica } from '@/pages/Logistica'
import { Marketing } from '@/pages/Marketing'
import { Pedidos } from '@/pages/Pedidos'
import { Produtos } from '@/pages/Produtos'
import { Relatorios } from '@/pages/Relatorios'
import { Atendimento } from '@/pages/Atendimento'
import { VisaoGeralPage } from '@/pages/VisaoGeral'

const ABAS = [
  { para: '/', rotulo: 'Visão geral', icone: '◈' },
  { para: '/ao-vivo', rotulo: 'Ao vivo', icone: '◉' },
  { para: '/faturamento', rotulo: 'Faturamento', icone: '₿' },
  { para: '/pedidos', rotulo: 'Pedidos', icone: '☰' },
  { para: '/produtos', rotulo: 'Produtos', icone: '▤' },
  { para: '/logistica', rotulo: 'Logística', icone: '⇢' },
  { para: '/atendimento', rotulo: 'Atendimento', icone: '☺' },
  { para: '/marketing', rotulo: 'Marketing', icone: '◐' },
  { para: '/relatorios', rotulo: 'Relatórios', icone: '▦' },
  { para: '/configuracoes', rotulo: 'Configurações', icone: '⚙' },
]

function useTema() {
  const [tema, setTema] = useState<'light' | 'dark'>(
    () => (localStorage.getItem('mh.tema') as 'light' | 'dark') ?? 'light',
  )
  useEffect(() => {
    document.documentElement.dataset.theme = tema
    localStorage.setItem('mh.tema', tema)
  }, [tema])
  return { tema, alternar: () => setTema((t) => (t === 'dark' ? 'light' : 'dark')) }
}

function Casca({ children }: { children: React.ReactNode }) {
  const { data: usuario } = useUsuario()
  const { tema, alternar } = useTema()
  const local = useLocation()
  const [menuAberto, setMenuAberto] = useState(false)

  useEffect(() => setMenuAberto(false), [local.pathname])

  return (
    <div className="flex min-h-screen flex-col bg-surface-sunken lg:flex-row">
      <aside
        className={`${
          menuAberto ? 'block' : 'hidden'
        } shrink-0 border-b border-line bg-surface lg:block lg:w-60 lg:border-b-0 lg:border-r`}
      >
        <div className="hidden items-center gap-2 border-b border-line px-4 py-4 lg:flex">
          <span className="text-lg">◆</span>
          <div>
            <p className="text-sm font-semibold text-ink">Marketplace Hub</p>
            <p className="text-[11px] text-ink-muted">{usuario?.tenant?.name ?? '—'}</p>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 p-2">
          {ABAS.map((aba) => (
            <NavLink
              key={aba.para}
              to={aba.para}
              end={aba.para === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                  isActive
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-ink-soft hover:bg-surface-raised'
                }`
              }
            >
              <span aria-hidden className="w-4 text-center opacity-70">
                {aba.icone}
              </span>
              {aba.rotulo}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3">
          <button
            type="button"
            className="btn px-2 py-1 lg:hidden"
            onClick={() => setMenuAberto((v) => !v)}
            aria-label="Alternar menu"
          >
            ☰
          </button>
          <div className="min-w-0 flex-1 lg:hidden">
            <p className="truncate text-sm font-semibold text-ink">Marketplace Hub</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={alternar}
              className="btn px-2 py-1"
              aria-label="Alternar tema claro e escuro"
              title="Alternar tema"
            >
              {tema === 'dark' ? '☾' : '☀'}
            </button>
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium text-ink">{usuario?.email ?? '—'}</p>
              <p className="text-[11px] text-ink-muted">{usuario?.role ?? ''}</p>
            </div>
            <button
              type="button"
              className="btn px-2 py-1 text-xs"
              onClick={async () => {
                await logout()
                window.location.reload()
              }}
            >
              Sair
            </button>
          </div>
        </header>

        <main className="min-w-0 flex-1 space-y-4 p-4">{children}</main>
      </div>
    </div>
  )
}

export default function App() {
  const [autenticado, setAutenticado] = useState(sessao.autenticado())

  useEffect(() => {
    const aoExpirar = () => setAutenticado(false)
    window.addEventListener('mh:sessao-expirada', aoExpirar)
    return () => window.removeEventListener('mh:sessao-expirada', aoExpirar)
  }, [])

  if (!autenticado) return <Login aoEntrar={() => setAutenticado(true)} />

  return (
    <Casca>
      <Routes>
        <Route path="/" element={<VisaoGeralPage />} />
        <Route path="/ao-vivo" element={<AoVivo />} />
        <Route path="/faturamento" element={<Faturamento />} />
        <Route path="/pedidos" element={<Pedidos />} />
        <Route path="/produtos" element={<Produtos />} />
        <Route path="/logistica" element={<Logistica />} />
        <Route path="/atendimento" element={<Atendimento />} />
        <Route path="/marketing" element={<Marketing />} />
        <Route path="/relatorios" element={<Relatorios />} />
        <Route path="/configuracoes" element={<Configuracoes />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Casca>
  )
}
