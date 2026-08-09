import { useAtendimento, usePerguntas } from '@/api/queries'
import { Carregando, ErroBox, KpiCard, SeloCanal, Secao, Tabela, Vazio } from '@/components/ui'
import { dataHora, inteiro, pct } from '@/lib/format'

export function Atendimento() {
  const visao = useAtendimento(30)
  const perguntas = usePerguntas(true)

  if (visao.isError) return <ErroBox erro={visao.error} />

  const p = visao.data?.perguntas
  const r = visao.data?.reclamacoes
  const a = visao.data?.avaliacoes

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          rotulo="Perguntas sem resposta"
          valor={p?.nao_respondidas ?? 0}
          formato="numero"
          dica="O tempo de resposta é fator de ranqueamento no Mercado Livre."
        />
        <KpiCard
          rotulo="Tempo médio de resposta"
          valor={p?.tempo_medio_resposta_min != null ? `${p.tempo_medio_resposta_min} min` : '—'}
        />
        <KpiCard
          rotulo="Reclamações"
          valor={r?.total ?? 0}
          formato="numero"
          dica={`Taxa de ${pct(r?.taxa_pct ?? 0)} sobre os pedidos do período.`}
        />
        <KpiCard rotulo="Nota média" valor={a?.media ?? '—'} />
      </div>

      <Secao
        titulo="Perguntas aguardando resposta"
        descricao="Ordenadas pela mais recente; o cronômetro mostra há quanto tempo o comprador espera."
      >
        {perguntas.isLoading ? (
          <Carregando />
        ) : (perguntas.data ?? []).length === 0 ? (
          <Vazio
            titulo="Nenhuma pergunta pendente"
            descricao="Todas as perguntas dos anúncios conectados já foram respondidas."
          />
        ) : (
          <Tabela colunas={['Canal', 'Pergunta', 'Anúncio', 'Recebida em', 'Aguardando']}>
            {(perguntas.data ?? []).map((q) => (
              <tr key={q.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={q.channel} />
                </td>
                <td className="td max-w-[420px] whitespace-normal text-sm">{q.text}</td>
                <td className="td num text-xs">{q.external_listing_id || '—'}</td>
                <td className="td text-xs">{dataHora(q.date_created)}</td>
                <td
                  className={`td num text-xs ${
                    Number(q.horas_aguardando ?? 0) > 12 ? 'font-semibold text-bad' : 'text-ink-soft'
                  }`}
                >
                  {q.horas_aguardando != null ? `${q.horas_aguardando} h` : '—'}
                </td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      <div className="grid gap-4 xl:grid-cols-2">
        <Secao titulo="Reclamações por situação" descricao="Reclamações, mediações e devoluções abertas no período.">
          <Tabela colunas={['Situação', 'Quantidade']} vazio={(r?.por_status ?? []).length === 0}>
            {(r?.por_status ?? []).map((s: { status: string; quantidade: number }) => (
              <tr key={s.status}>
                <td className="td">{s.status}</td>
                <td className="td num">{inteiro(s.quantidade)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>

        <Secao titulo="Distribuição de avaliações" descricao="Notas recebidas no período.">
          <Tabela colunas={['Nota', 'Quantidade']} vazio={(a?.distribuicao ?? []).length === 0}>
            {(a?.distribuicao ?? []).map((n: { nota: number; quantidade: number }) => (
              <tr key={n.nota}>
                <td className="td">{'★'.repeat(n.nota) || '—'}</td>
                <td className="td num">{inteiro(n.quantidade)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      </div>
    </div>
  )
}
