import { useState } from 'react'

import { urlExportacao } from '@/api/client'
import {
  useCoorte,
  useCurvaABC,
  useMediaMovel,
  usePorEstado,
  useRankingProdutos,
  useSerie,
} from '@/api/queries'
import {
  COR_CLASSE,
  GraficoABC,
  GraficoAcumuladoABC,
  GraficoEstados,
  GraficoMediaMovel,
  GraficoReceita,
  MapaDeCoorte,
} from '@/components/charts'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import { AvisoQualidade, Carregando, KpiCard, Secao, Tabela, Vazio } from '@/components/ui'
import { brl, inteiro, pct } from '@/lib/format'

type Granularidade = 'hour' | 'day' | 'month'

export function Relatorios() {
  const [filtros] = useFiltros()
  const [granularidade, setGranularidade] = useState<Granularidade>('day')

  const serie = useSerie(filtros, granularidade)
  const produtos = useRankingProdutos(filtros, 50)
  const estados = usePorEstado(filtros)

  const parametrosExport = { inicio: filtros.inicio, fim: filtros.fim, channel: filtros.channel }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <FiltroGlobal />
        <div className="flex gap-2">
          <a className="btn text-xs" href={urlExportacao('/reports/orders/export', { ...parametrosExport, formato: 'csv' })}>
            ↓ Pedidos (CSV)
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/orders/export', { ...parametrosExport, formato: 'xlsx' })}>
            ↓ Pedidos (Excel)
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/financial/export', parametrosExport)}>
            ↓ Financeiro
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/reconciliation/export')}>
            ↓ Divergências
          </a>
        </div>
      </div>

      <Secao
        titulo="Evolução da receita"
        descricao="Selecione a granularidade conforme o período analisado."
        acao={
          <div className="flex overflow-hidden rounded-lg border border-line text-xs">
            {(
              [
                ['hour', 'Hora'],
                ['day', 'Dia'],
                ['month', 'Mês'],
              ] as [Granularidade, string][]
            ).map(([valor, rotulo]) => (
              <button
                key={valor}
                type="button"
                onClick={() => setGranularidade(valor)}
                className={`border-r border-line px-3 py-1.5 last:border-r-0 ${
                  granularidade === valor
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-ink-soft hover:bg-surface-raised'
                }`}
              >
                {rotulo}
              </button>
            ))}
          </div>
        }
      >
        {serie.isLoading ? <Carregando altura="h-64" /> : <GraficoReceita dados={serie.data ?? []} />}
      </Secao>

      <div className="grid gap-4 xl:grid-cols-2">
        <Secao
          titulo="Distribuição geográfica"
          descricao="Por estado de destino — a granularidade que as APIs oficiais liberam para todos os tipos de envio."
        >
          {estados.isLoading ? <Carregando /> : <GraficoEstados dados={estados.data ?? []} />}
        </Secao>

        <Secao titulo="Receita por estado" descricao="Mesma informação em tabela, para leitura exata e exportação.">
          <Tabela colunas={['UF', 'Pedidos', 'Receita bruta']} vazio={(estados.data ?? []).length === 0}>
            {(estados.data ?? []).slice(0, 12).map((e) => (
              <tr key={e.estado}>
                <td className="td">{e.estado}</td>
                <td className="td num">{inteiro(e.pedidos)}</td>
                <td className="td num">{brl(e.receita_bruta)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      </div>

      <SerieSuavizada />
      <AnaliseABC />
      <AnaliseCoorte />

      <Secao
        titulo="Ranking completo de produtos"
        descricao="Consolidado por SKU base, com margem calculada sobre o custo congelado na venda."
      >
        {produtos.isLoading ? (
          <Carregando altura="h-64" />
        ) : (
          <Tabela
            colunas={['#', 'SKU', 'Produto', 'Pedidos', 'Unidades', 'Receita', 'CMV', 'Margem bruta', 'Margem %']}
            vazio={(produtos.data ?? []).length === 0}
          >
            {(produtos.data ?? []).map((p, i) => (
              <tr key={p.sku} className="hover:bg-surface-raised">
                <td className="td num text-ink-muted">{i + 1}</td>
                <td className="td num font-medium">{p.sku}</td>
                <td className="td max-w-[280px] truncate" title={p.titulo}>
                  {p.titulo}
                </td>
                <td className="td num">{inteiro(p.pedidos)}</td>
                <td className="td num">{inteiro(p.unidades)}</td>
                <td className="td num">{brl(p.receita_bruta)}</td>
                <td className="td num">{brl(p.cmv)}</td>
                <td className="td num">{brl(p.margem_bruta)}</td>
                <td className="td num">{p.margem_pct === '—' ? '—' : pct(p.margem_pct)}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>
    </div>
  )
}

// --- Média móvel -------------------------------------------------------------

const JANELAS = [7, 14, 30]

function SerieSuavizada() {
  const [filtros] = useFiltros()
  const [janela, setJanela] = useState(7)
  const dados = useMediaMovel(filtros, janela)

  const tendencia = dados.data?.tendencia
  const cor =
    tendencia?.direcao === 'alta'
      ? 'text-good'
      : tendencia?.direcao === 'queda'
        ? 'text-bad'
        : 'text-ink-muted'
  const seta = tendencia?.direcao === 'alta' ? '▲' : tendencia?.direcao === 'queda' ? '▼' : '■'

  return (
    <Secao
      titulo="Tendência com média móvel"
      descricao="Venda de autopeça cai no fim de semana e sobe na segunda. A média móvel remove esse ciclo — a linha fina é o dia, a grossa é a tendência."
      acao={
        <div className="flex overflow-hidden rounded-lg border border-line text-xs">
          {JANELAS.map((dias) => (
            <button
              key={dias}
              type="button"
              onClick={() => setJanela(dias)}
              className={`border-r border-line px-3 py-1.5 last:border-r-0 ${
                janela === dias
                  ? 'bg-brand-soft font-medium text-brand'
                  : 'text-ink-soft hover:bg-surface-raised'
              }`}
            >
              {dias}d
            </button>
          ))}
        </div>
      }
    >
      {dados.isLoading ? (
        <Carregando altura="h-64" />
      ) : (
        <>
          <GraficoMediaMovel dados={dados.data?.pontos ?? []} janela={dados.data?.janela ?? janela} />
          {tendencia && tendencia.direcao !== 'indefinida' && (
            <p className={`mt-2 text-center text-xs ${cor}`}>
              {seta} Tendência de {tendencia.direcao}: a média móvel saiu de{' '}
              <span className="num">{brl(tendencia.media_inicial)}</span> para{' '}
              <span className="num">{brl(tendencia.media_atual)}</span> ({pct(tendencia.variacao_pct)})
            </p>
          )}
        </>
      )}
    </Secao>
  )
}

// --- Curva ABC ---------------------------------------------------------------

function AnaliseABC() {
  const [filtros] = useFiltros()
  const abc = useCurvaABC(filtros)
  const [classe, setClasse] = useState<'todas' | 'A' | 'B' | 'C'>('todas')

  const itens = abc.data?.itens ?? []
  const visiveis = classe === 'todas' ? itens : itens.filter((i) => i.classe === classe)

  return (
    <Secao
      titulo="Curva ABC de produtos"
      descricao="Classe A concentra 80% da receita, B vai até 95%, C é a cauda. O corte é sobre o acumulado — quantos itens sustentam o faturamento é o que a análise revela."
    >
      {abc.isLoading ? (
        <Carregando altura="h-72" />
      ) : itens.length === 0 ? (
        <Vazio titulo="Sem vendas no período selecionado" />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(abc.data?.resumo ?? []).map((r) => (
              <div key={r.classe} className="card">
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-3 w-3 rounded-sm"
                    style={{ background: COR_CLASSE[r.classe] }}
                  />
                  <span className="card-sub">Classe {r.classe}</span>
                </div>
                <div className="num mt-1 text-xl font-semibold text-ink">{brl(r.receita)}</div>
                <p className="card-sub mt-0.5">
                  {r.itens} SKUs ({pct(r.itens_pct)} do catálogo) · {pct(r.receita_pct)} da receita
                </p>
              </div>
            ))}
          </div>

          <GraficoABC dados={itens} />
          <GraficoAcumuladoABC dados={itens} />

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-muted">Filtrar:</span>
            {(['todas', 'A', 'B', 'C'] as const).map((valor) => (
              <button
                key={valor}
                type="button"
                onClick={() => setClasse(valor)}
                className={`btn px-2.5 py-1 text-xs ${
                  classe === valor ? 'border-brand-line bg-brand-soft text-brand' : ''
                }`}
              >
                {valor === 'todas' ? 'Todas' : `Classe ${valor}`}
              </button>
            ))}
          </div>

          <Tabela
            colunas={['#', 'Classe', 'SKU', 'Produto', 'Unidades', 'Receita', 'Margem', 'Part.', 'Acum.']}
            vazio={visiveis.length === 0}
          >
            {visiveis.slice(0, 100).map((i) => (
              <tr key={i.sku} className="hover:bg-surface-raised">
                <td className="td num text-ink-muted">{i.posicao}</td>
                <td className="td">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="inline-block h-2.5 w-2.5 rounded-sm"
                      style={{ background: COR_CLASSE[i.classe] }}
                    />
                    {i.classe}
                  </span>
                </td>
                <td className="td num font-medium">{i.sku}</td>
                <td className="td max-w-[240px] truncate" title={i.titulo}>
                  {i.titulo}
                </td>
                <td className="td num">{inteiro(i.unidades)}</td>
                <td className="td num">{brl(i.receita_bruta)}</td>
                <td className="td num">{brl(i.margem_bruta)}</td>
                <td className="td num text-xs text-ink-muted">{pct(i.participacao_pct)}</td>
                <td className="td num text-xs text-ink-muted">{pct(i.acumulado_pct)}</td>
              </tr>
            ))}
          </Tabela>
        </div>
      )}
    </Secao>
  )
}

// --- Coorte ------------------------------------------------------------------

function AnaliseCoorte() {
  const [meses, setMeses] = useState(12)
  const coorte = useCoorte(meses)

  return (
    <Secao
      titulo="Coorte de compradores"
      descricao="Cada linha é quem comprou pela primeira vez naquele mês; as colunas mostram quantos voltaram depois. Responde se o cliente é recorrente ou se cada venda custa uma aquisição nova."
      acao={
        <select
          className="input py-1 text-xs"
          value={meses}
          onChange={(e) => setMeses(Number(e.target.value))}
        >
          <option value={6}>6 meses</option>
          <option value={12}>12 meses</option>
          <option value={24}>24 meses</option>
        </select>
      }
    >
      {coorte.isLoading ? (
        <Carregando altura="h-64" />
      ) : (coorte.data?.coortes ?? []).length === 0 ? (
        <Vazio
          titulo="Sem dados de coorte"
          descricao="A análise depende do identificador de comprador, que nem todo canal expõe. Sem ele não há como saber se a mesma pessoa voltou."
        />
      ) : (
        <div className="space-y-3">
          {coorte.data?.cobertura.aviso && (
            <AvisoQualidade texto={coorte.data.cobertura.aviso} />
          )}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <KpiCard
              rotulo="Coortes analisadas"
              valor={coorte.data?.coortes.length ?? 0}
              formato="numero"
            />
            <KpiCard
              rotulo="Cobertura"
              valor={coorte.data?.cobertura.pedidos_com_comprador_pct ?? 0}
              formato="percentual"
              dica="Percentual de pedidos com identificador de comprador disponível."
            />
            <KpiCard
              rotulo="Compradores no total"
              valor={(coorte.data?.coortes ?? []).reduce((s, c) => s + c.base, 0)}
              formato="numero"
            />
          </div>
          <MapaDeCoorte coortes={coorte.data?.coortes ?? []} />
        </div>
      )}
    </Secao>
  )
}
