import {
  useCascata,
  useConciliacao,
  useDivergencias,
  useFluxoDeCaixa,
  useRodarConciliacao,
  useTaxas,
} from '@/api/queries'
import { GraficoCaixa, GraficoCascata } from '@/components/charts'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import { Carregando, ErroBox, KpiCard, SeloCanal, Secao, Tabela, Vazio } from '@/components/ui'
import { brl, data, inteiro, pct } from '@/lib/format'

const ROTULO_CONCILIACAO: Record<string, string> = {
  matched: 'Conciliado',
  divergent: 'Divergente',
  pending_settlement: 'Aguardando repasse',
  unmatched: 'Sem correspondência',
}

export function Faturamento() {
  const [filtros] = useFiltros()
  const cascata = useCascata(filtros)
  const taxas = useTaxas(filtros)
  const conciliacao = useConciliacao(30)
  const divergencias = useDivergencias()
  const caixa = useFluxoDeCaixa(30)
  const rodar = useRodarConciliacao()

  if (cascata.isError) return <ErroBox erro={cascata.error} />

  const t = cascata.data?.totais

  return (
    <div className="space-y-4">
      <FiltroGlobal />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard rotulo="Receita bruta" valor={t?.receita_bruta ?? 0} formato="moeda" />
        <KpiCard
          rotulo="Taxas totais"
          valor={t?.taxas_totais ?? 0}
          formato="moeda"
          dica={`${pct(t?.taxa_efetiva_pct ?? 0)} do faturamento bruto.`}
        />
        <KpiCard rotulo="Receita líquida" valor={t?.receita_liquida ?? 0} formato="moeda" />
        <KpiCard rotulo="CMV" valor={t?.cmv ?? 0} formato="moeda" dica="Custo congelado no momento da venda." />
        <KpiCard
          rotulo="Margem de contribuição"
          valor={t?.margem_contribuicao ?? 0}
          formato="moeda"
          dica={`${pct(t?.margem_pct ?? 0)} sobre o bruto.`}
        />
      </div>

      <Secao
        titulo="Do faturamento bruto ao líquido"
        descricao="Cada barra é uma dedução ou acréscimo entre o que o comprador pagou e o que o vendedor recebe."
      >
        {cascata.isLoading ? <Carregando altura="h-72" /> : <GraficoCascata etapas={cascata.data?.etapas ?? []} />}
      </Secao>

      <div className="grid gap-4 xl:grid-cols-2">
        <Secao
          titulo="Procedência do valor líquido"
          descricao="Distingue o que já é dinheiro em conta do que ainda é previsão — misturar os dois faz o painel divergir do extrato."
        >
          <Tabela colunas={['Procedência', 'Pedidos', 'Valor']} vazio={(cascata.data?.por_procedencia ?? []).length === 0}>
            {(cascata.data?.por_procedencia ?? []).map((p) => (
              <tr key={p.fonte}>
                <td className="td">{p.rotulo}</td>
                <td className="td num">{inteiro(p.pedidos)}</td>
                <td className="td num">{brl(p.valor)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>

        <Secao
          titulo="Composição das taxas"
          descricao="Detalhamento por tipo. É aqui que se percebe uma taxa de parcelamento subindo sem aviso."
        >
          {taxas.isLoading ? (
            <Carregando />
          ) : (
            <Tabela colunas={['Tipo', 'Ocorrências', 'Valor', 'Participação']} vazio={(taxas.data ?? []).length === 0}>
              {(taxas.data ?? []).map((t2) => (
                <tr key={t2.tipo}>
                  <td className="td">{t2.tipo}</td>
                  <td className="td num">{inteiro(t2.ocorrencias)}</td>
                  <td className="td num">{brl(t2.valor)}</td>
                  <td className="td num">{pct(t2.participacao_pct)}</td>
                </tr>
              ))}
            </Tabela>
          )}
        </Secao>
      </div>

      <Secao
        titulo="Fluxo de caixa projetado"
        descricao="Combina as datas de liberação informadas pelo Mercado Pago e pelo escrow da Shopee."
        acao={
          <div className="text-right text-xs">
            <p className="text-ink-muted">Próximos 30 dias</p>
            <p className="num text-sm font-semibold text-ink">{brl(caixa.data?.resumo.total ?? 0)}</p>
          </div>
        }
      >
        {caixa.isLoading ? <Carregando altura="h-56" /> : <GraficoCaixa dados={caixa.data?.calendario ?? []} />}
      </Secao>

      <Secao
        titulo="Conciliação financeira"
        descricao="Casamento entre venda, pagamento e repasse."
        acao={
          <button
            type="button"
            className="btn text-xs"
            disabled={rodar.isPending}
            onClick={() => rodar.mutate(30)}
          >
            {rodar.isPending ? 'Conciliando…' : 'Conciliar agora'}
          </button>
        }
      >
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Object.entries(conciliacao.data?.por_status ?? {}).map(([status, v]) => (
            <div key={status} className="rounded-lg border border-line p-3">
              <p className="card-sub">{ROTULO_CONCILIACAO[status] ?? status}</p>
              <p className="num mt-1 text-xl font-semibold text-ink">{inteiro(v.quantidade)}</p>
              <p className="num text-xs text-ink-muted">{brl(v.divergencia)}</p>
            </div>
          ))}
        </div>

        <div className="mt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Fila de divergências — maior impacto primeiro
          </h3>
          {divergencias.isLoading ? (
            <Carregando />
          ) : (divergencias.data ?? []).length === 0 ? (
            <Vazio
              titulo="Nenhuma divergência no período"
              descricao="Todos os pedidos conciliados batem com o valor repassado, dentro da tolerância configurada."
            />
          ) : (
            <Tabela colunas={['Pedido', 'Canal', 'Data', 'Esperado', 'Liquidado', 'Divergência', 'Diagnóstico']}>
              {(divergencias.data ?? []).map((d) => (
                <tr key={d.order_id} className="hover:bg-surface-raised">
                  <td className="td num">#{d.external_id}</td>
                  <td className="td">
                    <SeloCanal canal={d.channel} />
                  </td>
                  <td className="td">{data(d.date_created)}</td>
                  <td className="td num">{brl(d.expected_net)}</td>
                  <td className="td num">{brl(d.settled_net)}</td>
                  <td className="td num font-semibold text-bad">{brl(d.divergence)}</td>
                  <td className="td max-w-[420px] whitespace-normal text-xs text-ink-soft">{d.notes}</td>
                </tr>
              ))}
            </Tabela>
          )}
        </div>
      </Secao>
    </div>
  )
}
