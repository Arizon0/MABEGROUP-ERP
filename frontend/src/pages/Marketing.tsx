import { useState } from 'react'

import { useCampanhas, useCustoDeMidia } from '@/api/queries'
import {
  Campo,
  Carregando,
  ErroBox,
  Modal,
  SeloCanal,
  Secao,
  Tabela,
  Vazio,
} from '@/components/ui'
import { brl, data, inteiro } from '@/lib/format'

export function Marketing() {
  const campanhas = useCampanhas()
  const lancarCusto = useCustoDeMidia()
  const [edicao, setEdicao] = useState<{ id: number; nome: string; valor: string } | null>(null)

  if (campanhas.isError) return <ErroBox erro={campanhas.error} />

  return (
    <div className="space-y-4">
      <Secao
        titulo="Campanhas e promoções"
        descricao="Responde à pergunta que o vendedor realmente faz: essa promoção deu lucro?"
      >
        {campanhas.isLoading ? (
          <Carregando altura="h-48" />
        ) : (campanhas.data ?? []).length === 0 ? (
          <Vazio
            titulo="Nenhuma campanha sincronizada"
            descricao="Cupons e promoções aparecem aqui assim que houver uma conta conectada com campanhas ativas. O custo de mídia pode ser lançado manualmente quando a API de anúncios não estiver liberada."
          />
        ) : (
          <Tabela
            colunas={['Canal', 'Campanha', 'Tipo', 'Período', 'Itens', 'Pedidos', 'Receita', 'Custo de mídia', 'Resultado', '']}
          >
            {(campanhas.data ?? []).map((c) => (
              <tr key={c.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={c.channel} />
                </td>
                <td className="td max-w-[220px] truncate">{c.name || `#${c.external_id}`}</td>
                <td className="td text-xs">{c.type}</td>
                <td className="td text-xs">
                  {data(c.start_at)} — {data(c.end_at)}
                </td>
                <td className="td num">{inteiro(c.itens)}</td>
                <td className="td num">{inteiro(c.pedidos)}</td>
                <td className="td num">{brl(c.receita_gerada)}</td>
                <td className="td num">{brl(c.custo_midia)}</td>
                <td
                  className={`td num font-medium ${
                    Number(c.resultado) >= 0 ? 'text-good' : 'text-bad'
                  }`}
                >
                  {brl(c.resultado)}
                </td>
                <td className="td">
                  <div className="flex justify-end">
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      onClick={() =>
                        setEdicao({
                          id: c.id,
                          nome: c.name || `#${c.external_id}`,
                          valor: c.custo_midia,
                        })
                      }
                    >
                      Custo de mídia
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </Tabela>
        )}

        <Modal
          aberto={edicao !== null}
          titulo="Lançar custo de mídia"
          descricao={edicao?.nome}
          aoFechar={() => setEdicao(null)}
        >
          {edicao && (
            <form
              className="space-y-3"
              onSubmit={(evento) => {
                evento.preventDefault()
                lancarCusto.mutate(
                  { id: edicao.id, manual_media_cost: edicao.valor || '0' },
                  { onSuccess: () => setEdicao(null) },
                )
              }}
            >
              <Campo
                rotulo="Investimento em mídia (R$)"
                dica="Some aqui o que foi gasto em anúncios para esta campanha. O resultado passa a descontar esse valor da receita gerada."
              >
                <input
                  required
                  type="number"
                  step="0.01"
                  min="0"
                  className="input"
                  value={edicao.valor}
                  onChange={(e) => setEdicao({ ...edicao, valor: e.target.value })}
                />
              </Campo>
              {lancarCusto.isError && <ErroBox erro={lancarCusto.error} />}
              <div className="flex justify-end gap-2 pt-1">
                <button type="button" className="btn text-xs" onClick={() => setEdicao(null)}>
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="btn btn-primary text-xs"
                  disabled={lancarCusto.isPending}
                >
                  {lancarCusto.isPending ? 'Salvando…' : 'Salvar'}
                </button>
              </div>
            </form>
          )}
        </Modal>
      </Secao>

      <div className="card">
        <h3 className="card-title">Sobre os dados de mídia paga</h3>
        <p className="card-sub mt-1 max-w-3xl">
          A API de anúncios da Shopee exige autorização adicional (whitelist) que não vem com a
          autorização padrão do aplicativo, e o Mercado Livre não expõe o custo de mídia por campanha
          de forma consolidada. Enquanto essa liberação não existir, o custo pode ser lançado
          manualmente por campanha — o cálculo de rentabilidade e de retorno sobre investimento passa
          a funcionar normalmente com esse valor.
        </p>
      </div>
    </div>
  )
}
