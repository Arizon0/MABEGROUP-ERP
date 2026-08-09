import { useAtrasados, useLogistica } from '@/api/queries'
import { Carregando, ErroBox, KpiCard, SeloCanal, SeloStatus, Secao, Tabela, Vazio } from '@/components/ui'
import { ROTULO_LOGISTICA, brl, data, inteiro } from '@/lib/format'

export function Logistica() {
  const visao = useLogistica(30)
  const atrasados = useAtrasados()

  if (visao.isError) return <ErroBox erro={visao.error} />

  const porStatus: { status: string; quantidade: number }[] = visao.data?.por_status ?? []
  const porCanal: {
    canal: string
    pedidos: number
    custo_frete: string
    custo_medio: string
  }[] = visao.data?.por_canal_logistico ?? []
  const prazos: { estado: string; entregas: number; atraso_medio_dias: string }[] =
    visao.data?.prazo_por_estado ?? []

  const totalEnvios = porStatus.reduce((s, p) => s + p.quantidade, 0)
  const entregues = porStatus.find((p) => p.status === 'delivered')?.quantidade ?? 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard rotulo="Envios no período" valor={totalEnvios} formato="numero" />
        <KpiCard rotulo="Entregues" valor={entregues} formato="numero" />
        <KpiCard
          rotulo="Em atraso"
          valor={(atrasados.data ?? []).length}
          formato="numero"
          dica="Passaram do prazo prometido sem entrega confirmada."
        />
        <KpiCard
          rotulo="Custo de frete"
          valor={porCanal.reduce((s, c) => s + Number(c.custo_frete || 0), 0)}
          formato="moeda"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Secao titulo="Por canal logístico" descricao="Volume e custo médio de frete por modalidade.">
          <Tabela colunas={['Canal', 'Pedidos', 'Custo total', 'Custo médio']} vazio={porCanal.length === 0}>
            {porCanal.map((c) => (
              <tr key={c.canal}>
                <td className="td">{ROTULO_LOGISTICA[c.canal] ?? c.canal}</td>
                <td className="td num">{inteiro(c.pedidos)}</td>
                <td className="td num">{brl(c.custo_frete)}</td>
                <td className="td num">{brl(c.custo_medio)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>

        <Secao titulo="Situação dos envios" descricao="Distribuição por status de rastreio.">
          <Tabela colunas={['Status', 'Quantidade']} vazio={porStatus.length === 0}>
            {porStatus.map((p) => (
              <tr key={p.status}>
                <td className="td">
                  <SeloStatus status={p.status} />
                </td>
                <td className="td num">{inteiro(p.quantidade)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      </div>

      <Secao
        titulo="Envios em atraso"
        descricao="Ordenados pelo maior atraso — é a fila de trabalho da operação, não um relatório para leitura passiva."
      >
        {atrasados.isLoading ? (
          <Carregando />
        ) : (atrasados.data ?? []).length === 0 ? (
          <Vazio titulo="Nenhum envio atrasado" descricao="Todos os envios ativos estão dentro do prazo prometido." />
        ) : (
          <Tabela colunas={['Canal', 'Envio', 'Rastreio', 'Transportadora', 'Previsto', 'Atraso', 'Destino']}>
            {(atrasados.data ?? []).map((e) => (
              <tr key={e.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={e.channel} />
                </td>
                <td className="td num text-xs">#{e.external_id}</td>
                <td className="td num text-xs">{e.tracking_number || '—'}</td>
                <td className="td text-xs">{e.carrier || '—'}</td>
                <td className="td text-xs">{data(e.estimated_delivery)}</td>
                <td className="td num font-semibold text-bad">{e.dias_de_atraso} d</td>
                <td className="td text-xs">{e.destino || '—'}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      <Secao
        titulo="Prazo por estado"
        descricao="Atraso médio nas entregas concluídas — orienta o ajuste da promessa de entrega por região."
      >
        <Tabela colunas={['Estado', 'Entregas', 'Atraso médio']} vazio={prazos.length === 0}>
          {prazos.map((p) => (
            <tr key={p.estado}>
              <td className="td">{p.estado}</td>
              <td className="td num">{inteiro(p.entregas)}</td>
              <td className="td num">{p.atraso_medio_dias} d</td>
            </tr>
          ))}
        </Tabela>
      </Secao>
    </div>
  )
}
