import { useState } from 'react'

import {
  useAnuncios,
  useDesfazerMapeamento,
  useMapeamentos,
  useMapearSku,
  usePendencias,
  useProdutos,
  useRatearFrete,
  useRemoverProduto,
  useSalvarProduto,
  useSaudeEstoque,
} from '@/api/queries'
import {
  AvisoQualidade,
  BotaoExcluir,
  Campo,
  Carregando,
  ErroBox,
  KpiCard,
  Modal,
  SeloCanal,
  Secao,
  Tabela,
  Vazio,
} from '@/components/ui'
import { brl, data, inteiro, pct } from '@/lib/format'
import type { Produto } from '@/types/api'

export function Produtos() {
  const [apenasRuptura, setApenasRuptura] = useState(false)
  const [busca, setBusca] = useState('')

  const anuncios = useAnuncios({ apenas_ruptura: apenasRuptura, busca: busca || undefined })
  const saude = useSaudeEstoque(30)
  const pendencias = usePendencias()
  const produtos = useProdutos()
  const mapear = useMapearSku()

  if (anuncios.isError) return <ErroBox erro={anuncios.error} />

  const r = saude.data?.resumo

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          rotulo="Em ruptura"
          valor={r?.ruptura ?? 0}
          formato="numero"
          dica="Estoque zerado com venda nos últimos 30 dias."
        />
        <KpiCard
          rotulo="Estoque crítico"
          valor={r?.criticos ?? 0}
          formato="numero"
          dica="Menos de 7 dias de cobertura pelo giro atual."
        />
        <KpiCard rotulo="Parados" valor={r?.parados ?? 0} formato="numero" dica="Com estoque e sem venda no período." />
        <KpiCard rotulo="Saudáveis" valor={r?.saudaveis ?? 0} formato="numero" />
      </div>

      {(saude.data?.ruptura ?? []).length > 0 && (
        <Secao
          titulo="Ruptura — atenção imediata"
          descricao="Produtos com histórico de venda e estoque zerado. Cada dia parado é receita perdida."
        >
          <Tabela colunas={['Canal', 'SKU', 'Produto', 'Vendas 30d', 'Média/dia']}>
            {(saude.data?.ruptura ?? []).slice(0, 15).map((l) => (
              <tr key={l.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={l.channel} />
                </td>
                <td className="td num text-xs">{l.sku_channel || '—'}</td>
                <td className="td max-w-[320px] truncate">{l.title}</td>
                <td className="td num">{inteiro(l.vendas_periodo)}</td>
                <td className="td num">{l.media_diaria}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      )}

      <Secao
        titulo="Anúncios"
        descricao="Catálogo consolidado de todos os canais conectados."
        acao={
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              placeholder="Buscar…"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              className="input py-1 text-xs"
            />
            <label className="flex items-center gap-1.5 text-xs text-ink-soft">
              <input
                type="checkbox"
                checked={apenasRuptura}
                onChange={(e) => setApenasRuptura(e.target.checked)}
              />
              Só ruptura
            </label>
          </div>
        }
      >
        {anuncios.isLoading ? (
          <Carregando altura="h-64" />
        ) : (
          <Tabela
            colunas={['Canal', 'SKU', 'Anúncio', 'Preço', 'Estoque', 'Vendidos', 'Visitas 30d', 'Conversão']}
            vazio={(anuncios.data?.itens ?? []).length === 0}
          >
            {(anuncios.data?.itens ?? []).map((a) => (
              <tr key={a.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={a.channel} />
                </td>
                <td className="td num text-xs">{a.sku_channel || '—'}</td>
                <td className="td max-w-[300px] truncate" title={a.title}>
                  {a.title}
                </td>
                <td className="td num">{brl(a.price)}</td>
                <td className={`td num ${a.em_ruptura ? 'font-semibold text-bad' : ''}`}>
                  {a.em_ruptura ? '0 · ruptura' : inteiro(a.available_quantity)}
                </td>
                <td className="td num">{inteiro(a.sold_quantity)}</td>
                <td className="td num">{inteiro(a.visits_30d)}</td>
                <td className="td num">{a.conversao_pct === '—' ? '—' : pct(a.conversao_pct)}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      <Secao
        titulo="SKUs sem de-para"
        descricao="Códigos vistos na importação sem produto correspondente. A pendência não bloqueia o pedido — só deixa a margem daquele item indisponível."
      >
        {pendencias.isLoading ? (
          <Carregando />
        ) : (pendencias.data ?? []).length === 0 ? (
          <Vazio titulo="Nenhuma pendência" descricao="Todos os SKUs importados estão mapeados a um produto interno." />
        ) : (
          <Tabela colunas={['Canal', 'SKU do canal', 'Exemplo', 'Ocorrências', 'Visto em', 'Vincular a']}>
            {(pendencias.data ?? []).map((p) => (
              <tr key={p.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={p.channel} />
                </td>
                <td className="td num font-medium">{p.sku_channel}</td>
                <td className="td max-w-[260px] truncate text-xs">{p.sample_title}</td>
                <td className="td num">{inteiro(p.occurrences)}</td>
                <td className="td text-xs">{data(p.last_seen_at)}</td>
                <td className="td">
                  <select
                    className="input py-1 text-xs"
                    defaultValue=""
                    disabled={mapear.isPending}
                    onChange={(e) => {
                      if (!e.target.value) return
                      mapear.mutate({
                        channel: p.channel,
                        sku_channel: p.sku_channel,
                        product_id: Number(e.target.value),
                      })
                    }}
                  >
                    <option value="">Selecionar produto…</option>
                    {(produtos.data ?? []).map((prod) => (
                      <option key={prod.id} value={prod.id}>
                        {prod.sku} — {prod.name}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </Tabela>
        )}
        {mapear.isSuccess && (
          <p className="mt-2 text-xs text-good">
            {mapear.data?.mensagem} — {mapear.data?.dados?.itens_atualizados ?? 0} itens e{' '}
            {mapear.data?.dados?.pedidos_recalculados ?? 0} pedidos recalculados com o custo do produto.
          </p>
        )}
      </Secao>

      <CadastroDeProdutos />
      <RateioDeFrete />
      <MapeamentosConfigurados />
    </div>
  )
}

// --- Cadastro de produtos ----------------------------------------------------

const PRODUTO_VAZIO = {
  sku: '',
  name: '',
  brand: '',
  category: '',
  unit_cost: '',
  freight_in_cost: '',
  other_acquisition_cost: '',
  packaging_cost: '',
  ncm: '',
  ean: '',
}

function CadastroDeProdutos() {
  const produtos = useProdutos()
  const salvar = useSalvarProduto()
  const remover = useRemoverProduto()
  const [edicao, setEdicao] = useState<(typeof PRODUTO_VAZIO & { id?: number }) | null>(null)

  const semCusto = (produtos.data ?? []).filter((p) => Number(p.unit_cost) <= 0).length

  return (
    <Secao
      titulo="Produtos internos"
      descricao="O custo cadastrado aqui é congelado na venda: alterá-lo muda a margem das vendas futuras, nunca a das já registradas."
      acao={
        <button
          type="button"
          className="btn btn-primary text-xs"
          onClick={() => setEdicao({ ...PRODUTO_VAZIO })}
        >
          Novo produto
        </button>
      }
    >
      {semCusto > 0 && (
        <div className="mb-3">
          <AvisoQualidade
            texto={`${semCusto} produtos sem custo cadastrado. Enquanto o custo for zero, a margem desses itens aparece maior do que é.`}
          />
        </div>
      )}

      {produtos.isLoading ? (
        <Carregando altura="h-40" />
      ) : (produtos.data ?? []).length === 0 ? (
        <Vazio
          titulo="Nenhum produto cadastrado"
          descricao="O produto interno é o que liga os SKUs dos canais a um custo único."
        />
      ) : (
        <Tabela
          colunas={[
            'SKU', 'Produto', 'Fornecedor', 'Frete compra', 'Aquisição',
            'Embalagem', 'Custo total', 'Situação', '',
          ]}
        >
          {(produtos.data ?? []).map((p: Produto) => (
            <tr key={p.id} className="hover:bg-surface-raised">
              <td className="td num font-medium">{p.sku}</td>
              <td className="td max-w-[220px] truncate">{p.name}</td>
              <td className={`td num ${Number(p.unit_cost) <= 0 ? 'text-warn' : ''}`}>
                {brl(p.unit_cost)}
              </td>
              <td className={`td num ${Number(p.freight_in_cost) <= 0 ? 'text-ink-muted' : ''}`}>
                {brl(p.freight_in_cost)}
              </td>
              <td className="td num">{brl(p.custo_aquisicao)}</td>
              <td className="td num">{brl(p.packaging_cost)}</td>
              <td className="td num font-medium">{brl(p.custo_total_unitario)}</td>
              <td className="td text-xs">
                {p.is_active ? (
                  <span className="text-good">Ativo</span>
                ) : (
                  <span className="text-ink-muted">Inativo</span>
                )}
              </td>
              <td className="td">
                <div className="flex justify-end gap-1">
                  <button
                    type="button"
                    className="btn px-2 py-1 text-xs"
                    onClick={() =>
                      setEdicao({
                        id: p.id,
                        sku: p.sku,
                        name: p.name,
                        brand: p.brand,
                        category: '',
                        unit_cost: p.unit_cost,
                        freight_in_cost: p.freight_in_cost,
                        other_acquisition_cost: p.other_acquisition_cost,
                        packaging_cost: p.packaging_cost,
                        ncm: '',
                        ean: '',
                      })
                    }
                  >
                    Editar
                  </button>
                  <BotaoExcluir
                    aoConfirmar={() => remover.mutate(p.id)}
                    ocupado={remover.isPending && remover.variables === p.id}
                  />
                </div>
              </td>
            </tr>
          ))}
        </Tabela>
      )}

      {remover.isSuccess && (
        <p className="mt-2 text-xs text-ink-muted">
          Produto com vendas registradas é desativado em vez de apagado: o histórico de margem
          continua consultável.
        </p>
      )}

      <Modal
        aberto={edicao !== null}
        titulo={edicao?.id ? 'Editar produto' : 'Novo produto'}
        aoFechar={() => setEdicao(null)}
      >
        {edicao && (
          <form
            className="space-y-3"
            onSubmit={(evento) => {
              evento.preventDefault()
              salvar.mutate(
                {
                  ...edicao,
                  unit_cost: edicao.unit_cost || '0',
                  freight_in_cost: edicao.freight_in_cost || '0',
                  other_acquisition_cost: edicao.other_acquisition_cost || '0',
                  packaging_cost: edicao.packaging_cost || '0',
                },
                { onSuccess: () => setEdicao(null) },
              )
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <Campo rotulo="SKU">
                <input
                  required
                  className="input"
                  value={edicao.sku}
                  onChange={(e) => setEdicao({ ...edicao, sku: e.target.value })}
                />
              </Campo>
              <Campo rotulo="Marca">
                <input
                  className="input"
                  value={edicao.brand}
                  onChange={(e) => setEdicao({ ...edicao, brand: e.target.value })}
                />
              </Campo>
            </div>
            <Campo rotulo="Nome">
              <input
                className="input"
                value={edicao.name}
                onChange={(e) => setEdicao({ ...edicao, name: e.target.value })}
              />
            </Campo>
            <div className="grid grid-cols-2 gap-3">
              <Campo rotulo="Custo unitário (R$)" dica="O que você paga ao fornecedor.">
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  className="input"
                  value={edicao.unit_cost}
                  onChange={(e) => setEdicao({ ...edicao, unit_cost: e.target.value })}
                />
              </Campo>
              <Campo
                rotulo="Frete de compra (R$)"
                dica="Transporte do fornecedor até o galpão, por unidade. Use o rateio abaixo para calcular a partir do frete total da nota."
              >
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  className="input"
                  value={edicao.freight_in_cost}
                  onChange={(e) => setEdicao({ ...edicao, freight_in_cost: e.target.value })}
                />
              </Campo>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Campo
                rotulo="Outros custos de aquisição (R$)"
                dica="Seguro de carga, desembaraço, ICMS-ST não recuperável."
              >
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  className="input"
                  value={edicao.other_acquisition_cost}
                  onChange={(e) =>
                    setEdicao({ ...edicao, other_acquisition_cost: e.target.value })
                  }
                />
              </Campo>
              <Campo rotulo="Embalagem (R$)" dica="Caixa, plástico e etiqueta por unidade.">
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  className="input"
                  value={edicao.packaging_cost}
                  onChange={(e) => setEdicao({ ...edicao, packaging_cost: e.target.value })}
                />
              </Campo>
            </div>
            <p className="rounded-lg border border-line bg-surface-raised p-2 text-[11px] text-ink-muted">
              Custo total no CMV:{' '}
              <span className="num font-medium text-ink">
                {brl(
                  Number(edicao.unit_cost || 0) +
                    Number(edicao.freight_in_cost || 0) +
                    Number(edicao.other_acquisition_cost || 0) +
                    Number(edicao.packaging_cost || 0),
                )}
              </span>{' '}
              — congelado na venda; alterar aqui não muda a margem já registrada.
            </p>
            {salvar.isError && <ErroBox erro={salvar.error} />}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn text-xs" onClick={() => setEdicao(null)}>
                Cancelar
              </button>
              <button type="submit" className="btn btn-primary text-xs" disabled={salvar.isPending}>
                {salvar.isPending ? 'Salvando…' : 'Salvar'}
              </button>
            </div>
          </form>
        )}
      </Modal>
    </Secao>
  )
}

// --- Frete de compra ---------------------------------------------------------

type LinhaDaNota = { sku: string; quantidade: string; valor_total: string }

function RateioDeFrete() {
  const produtos = useProdutos()
  const ratear = useRatearFrete()

  const [freteTotal, setFreteTotal] = useState('')
  const [outros, setOutros] = useState('')
  const [criterio, setCriterio] = useState<'quantidade' | 'valor'>('quantidade')
  const [linhas, setLinhas] = useState<LinhaDaNota[]>([
    { sku: '', quantidade: '', valor_total: '' },
  ])
  const [previa, setPrevia] = useState<Record<string, string> | null>(null)

  const validas = linhas.filter((l) => l.sku && Number(l.quantidade) > 0)

  const enviar = (aplicar: boolean) => {
    ratear.mutate(
      {
        frete_total: freteTotal || '0',
        outros_custos: outros || '0',
        criterio,
        aplicar,
        itens: validas.map((l) => ({
          sku: l.sku,
          quantidade: l.quantidade,
          valor_total: l.valor_total || '0',
        })),
      },
      {
        onSuccess: (resposta) => {
          const dados = (resposta as { dados?: { itens?: { sku: string; frete_por_unidade: string }[] } })
            .dados
          setPrevia(
            Object.fromEntries(
              (dados?.itens ?? []).map((i) => [i.sku, i.frete_por_unidade]),
            ),
          )
          if (aplicar) setLinhas([{ sku: '', quantidade: '', valor_total: '' }])
        },
      },
    )
  }

  return (
    <Secao
      titulo="Frete de compra — rateio da nota do fornecedor"
      descricao="O frete chega como um valor único na nota, mas o custo é por unidade. Contabilmente ele integra o custo de aquisição do estoque: fora do CMV, o lucro aparece maior do que é."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Campo rotulo="Frete total da nota (R$)">
          <input
            type="number"
            step="0.01"
            min="0"
            className="input"
            value={freteTotal}
            onChange={(e) => setFreteTotal(e.target.value)}
          />
        </Campo>
        <Campo rotulo="Outros custos (R$)" dica="Seguro, desembaraço, ICMS-ST.">
          <input
            type="number"
            step="0.01"
            min="0"
            className="input"
            value={outros}
            onChange={(e) => setOutros(e.target.value)}
          />
        </Campo>
        <Campo
          rotulo="Critério de rateio"
          dica={
            criterio === 'quantidade'
              ? 'Divide igualmente por unidade — bom quando os itens têm porte parecido.'
              : 'Proporcional ao valor da linha — evita que a peça barata receba a mesma parcela da cara.'
          }
        >
          <select
            className="input"
            value={criterio}
            onChange={(e) => setCriterio(e.target.value as 'quantidade' | 'valor')}
          >
            <option value="quantidade">Por quantidade</option>
            <option value="valor">Por valor</option>
          </select>
        </Campo>
      </div>

      <div className="mt-3 space-y-2">
        {linhas.map((linha, indice) => (
          <div key={indice} className="grid gap-2 sm:grid-cols-[2fr,1fr,1fr,auto]">
            <select
              className="input py-1 text-xs"
              value={linha.sku}
              onChange={(e) => {
                const copia = [...linhas]
                copia[indice] = { ...linha, sku: e.target.value }
                setLinhas(copia)
              }}
            >
              <option value="">Produto…</option>
              {(produtos.data ?? []).map((p) => (
                <option key={p.id} value={p.sku}>
                  {p.sku} — {p.name}
                </option>
              ))}
            </select>
            <input
              type="number"
              min="0"
              placeholder="Quantidade"
              className="input py-1 text-xs"
              value={linha.quantidade}
              onChange={(e) => {
                const copia = [...linhas]
                copia[indice] = { ...linha, quantidade: e.target.value }
                setLinhas(copia)
              }}
            />
            <input
              type="number"
              min="0"
              step="0.01"
              placeholder={criterio === 'valor' ? 'Valor da linha' : 'Valor (opcional)'}
              className="input py-1 text-xs"
              value={linha.valor_total}
              onChange={(e) => {
                const copia = [...linhas]
                copia[indice] = { ...linha, valor_total: e.target.value }
                setLinhas(copia)
              }}
            />
            <div className="flex items-center gap-2">
              {previa?.[linha.sku] && (
                <span className="num text-xs text-good" title="Frete por unidade">
                  {brl(previa[linha.sku])}/un
                </span>
              )}
              <button
                type="button"
                className="btn px-2 py-1 text-xs"
                onClick={() => setLinhas(linhas.filter((_, i) => i !== indice))}
                disabled={linhas.length === 1}
                aria-label="Remover linha"
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>

      {ratear.isError && (
        <div className="mt-3">
          <ErroBox erro={ratear.error} />
        </div>
      )}

      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          className="btn text-xs"
          onClick={() =>
            setLinhas([...linhas, { sku: '', quantidade: '', valor_total: '' }])
          }
        >
          Adicionar item
        </button>
        <button
          type="button"
          className="btn text-xs"
          disabled={validas.length === 0 || ratear.isPending}
          onClick={() => enviar(false)}
        >
          Simular
        </button>
        <button
          type="button"
          className="btn btn-primary text-xs"
          disabled={validas.length === 0 || ratear.isPending}
          onClick={() => enviar(true)}
        >
          {ratear.isPending ? 'Aplicando…' : 'Aplicar aos produtos'}
        </button>
      </div>
    </Secao>
  )
}

// --- De-para configurado -----------------------------------------------------

function MapeamentosConfigurados() {
  const mapeamentos = useMapeamentos()
  const desfazer = useDesfazerMapeamento()

  return (
    <Secao
      titulo="De-para configurado"
      descricao="Vínculos ativos entre o SKU do canal e o produto interno. Desfazer devolve o SKU à lista de pendências — o custo já congelado nas vendas antigas não muda."
    >
      {mapeamentos.isLoading ? (
        <Carregando altura="h-32" />
      ) : (mapeamentos.data ?? []).length === 0 ? (
        <Vazio titulo="Nenhum de-para configurado" />
      ) : (
        <Tabela colunas={['Canal', 'SKU do canal', 'Produto interno', 'Origem', '']}>
          {(mapeamentos.data ?? []).map((m) => (
            <tr key={m.id} className="hover:bg-surface-raised">
              <td className="td">
                <SeloCanal canal={m.channel} />
              </td>
              <td className="td num font-medium">{m.sku_channel}</td>
              <td className="td max-w-[280px] truncate">
                {m.product_sku} — {m.product_name}
              </td>
              <td className="td text-xs text-ink-muted">
                {m.confidence === 'manual' ? 'Manual' : 'Automático'}
              </td>
              <td className="td">
                <div className="flex justify-end">
                  <BotaoExcluir
                    rotulo="Desfazer"
                    confirmacao="Desfazer vínculo?"
                    aoConfirmar={() => desfazer.mutate(m.id)}
                    ocupado={desfazer.isPending && desfazer.variables === m.id}
                  />
                </div>
              </td>
            </tr>
          ))}
        </Tabela>
      )}
    </Secao>
  )
}
