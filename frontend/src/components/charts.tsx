/**
 * Gráficos do painel.
 *
 * Regras aplicadas (skill de visualização de dados):
 * - eixo único sempre — nunca dois eixos Y no mesmo gráfico;
 * - cores categóricas em ordem fixa, atribuídas à entidade e não à posição no
 *   ranking, de modo que filtrar séries não repinta as que sobraram;
 * - traços finos (2px), grade e eixos recessivos, rótulos diretos seletivos;
 * - legenda sempre presente a partir de duas séries — identidade nunca depende
 *   só da cor;
 * - camada de hover por padrão, com tooltip formatado em português.
 */
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ROTULO_CANAL, brl, brlCurto, corDoCanal, inteiro, num } from '@/lib/format'

const EIXO = { fontSize: 11, fill: 'var(--text-muted)' }
const GRADE = 'var(--grid)'

const estiloTooltip = {
  background: 'var(--surface-2)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--text-primary)',
  padding: '8px 10px',
}

const rotuloDia = (valor: string): string => {
  if (!valor) return ''
  if (valor.length === 7) return valor // AAAA-MM
  const partes = valor.slice(0, 10).split('-')
  return partes.length === 3 ? `${partes[2]}/${partes[1]}` : valor
}

// --- Receita bruta × líquida -------------------------------------------------

export function GraficoReceita({
  dados,
}: {
  dados: { bucket: string; receita_bruta: string; receita_liquida: string; pedidos: number }[]
}) {
  const serie = dados.map((d) => ({
    bucket: rotuloDia(d.bucket),
    bruto: num(d.receita_bruta),
    liquido: num(d.receita_liquida),
  }))

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={serie} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="bucket" tick={EIXO} tickLine={false} axisLine={{ stroke: GRADE }} minTickGap={24} />
        <YAxis
          tick={EIXO}
          tickLine={false}
          axisLine={false}
          width={62}
          tickFormatter={(v) => brlCurto(v)}
        />
        <Tooltip
          contentStyle={estiloTooltip}
          formatter={(v: number, n: string) => [brl(v), n === 'bruto' ? 'Receita bruta' : 'Receita líquida']}
          labelFormatter={(l) => `Dia ${l}`}
        />
        <Legend
          verticalAlign="top"
          height={28}
          formatter={(v) => (
            <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              {v === 'bruto' ? 'Receita bruta' : 'Receita líquida'}
            </span>
          )}
        />
        {/* A distância visual entre as duas linhas é a leitura mais útil do
            painel: mostra o peso das taxas sobre o faturamento. */}
        <Line
          type="monotone"
          dataKey="bruto"
          stroke="var(--series-1)"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)' }}
        />
        <Line
          type="monotone"
          dataKey="liquido"
          stroke="var(--series-3)"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)' }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// --- Comparativo por marketplace --------------------------------------------

export function GraficoCanais({
  dados,
}: {
  dados: { channel: string; receita_bruta: string; receita_liquida: string }[]
}) {
  const serie = dados.map((d) => ({
    nome: ROTULO_CANAL[d.channel] ?? d.channel,
    canal: d.channel,
    bruto: num(d.receita_bruta),
  }))

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={serie} layout="vertical" margin={{ top: 4, right: 64, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tick={EIXO} tickLine={false} axisLine={false} tickFormatter={(v) => brlCurto(v)} />
        <YAxis type="category" dataKey="nome" tick={EIXO} tickLine={false} axisLine={false} width={110} />
        <Tooltip contentStyle={estiloTooltip} formatter={(v: number) => [brl(v), 'Receita bruta']} cursor={{ fill: 'var(--grid)' }} />
        {/* Poucas barras: rótulo direto dispensa a legenda e evita que a
            identidade dependa apenas da cor. */}
        <Bar dataKey="bruto" radius={[0, 4, 4, 0]} barSize={22} label={{
          position: 'right',
          formatter: (v: number) => brlCurto(v),
          fill: 'var(--text-secondary)',
          fontSize: 11,
        }}>
          {serie.map((d) => (
            <Cell key={d.canal} fill={corDoCanal(d.canal)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// --- Cascata do bruto ao líquido --------------------------------------------

export function GraficoCascata({
  etapas,
}: {
  etapas: { nome: string; valor: string; tipo: string }[]
}) {
  // Cada barra flutuante é representada por uma base transparente somada ao
  // deslocamento — é assim que se desenha waterfall com barras empilhadas.
  let acumulado = 0
  const serie = etapas.map((e) => {
    const valor = num(e.valor)
    const total = e.tipo === 'total' || e.tipo === 'inicio'
    const base = total ? 0 : valor >= 0 ? acumulado : acumulado + valor
    const altura = total ? Math.abs(valor) : Math.abs(valor)
    if (!total) acumulado += valor
    else acumulado = valor
    return {
      nome: e.nome,
      base,
      altura,
      valor,
      tipo: e.tipo,
    }
  })

  const cor = (tipo: string, valor: number) =>
    tipo === 'total' || tipo === 'inicio'
      ? 'var(--series-1)'
      : valor >= 0
        ? 'var(--diverge-pos)'
        : 'var(--diverge-neg)'

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={serie} margin={{ top: 16, right: 8, bottom: 60, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="nome"
          tick={{ ...EIXO, fontSize: 10 }}
          tickLine={false}
          axisLine={{ stroke: GRADE }}
          angle={-35}
          textAnchor="end"
          height={70}
          interval={0}
        />
        <YAxis tick={EIXO} tickLine={false} axisLine={false} width={62} tickFormatter={(v) => brlCurto(v)} />
        <Tooltip
          contentStyle={estiloTooltip}
          cursor={{ fill: 'var(--grid)' }}
          formatter={(_v, _n, item: any) => [brl(item?.payload?.valor ?? 0), item?.payload?.nome ?? '']}
          labelFormatter={() => ''}
        />
        <ReferenceLine y={0} stroke={GRADE} />
        {/* Base invisível que posiciona a barra flutuante. */}
        <Bar dataKey="base" stackId="c" fill="transparent" isAnimationActive={false} />
        <Bar dataKey="altura" stackId="c" radius={[4, 4, 0, 0]}>
          {serie.map((d, i) => (
            <Cell key={i} fill={cor(d.tipo, d.valor)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// --- Volume por minuto (painel ao vivo) -------------------------------------

export function GraficoPorMinuto({
  dados,
}: {
  dados: { bucket: string; pedidos: number }[]
}) {
  const serie = dados.map((d) => ({ hora: d.bucket.slice(11), pedidos: d.pedidos }))

  if (serie.length === 0) {
    return (
      <div className="flex h-[140px] items-center justify-center text-xs text-ink-muted">
        Nenhum pedido na última hora.
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={140}>
      <BarChart data={serie} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="hora" tick={EIXO} tickLine={false} axisLine={{ stroke: GRADE }} minTickGap={30} />
        <YAxis tick={EIXO} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
        <Tooltip
          contentStyle={estiloTooltip}
          cursor={{ fill: 'var(--grid)' }}
          formatter={(v: number) => [inteiro(v), 'Pedidos']}
          labelFormatter={(l) => `${l}`}
        />
        <Bar dataKey="pedidos" fill="var(--series-1)" radius={[4, 4, 0, 0]} barSize={8} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// --- Distribuição geográfica -------------------------------------------------

export function GraficoEstados({
  dados,
}: {
  dados: { estado: string; pedidos: number; receita_bruta: string }[]
}) {
  const serie = dados.slice(0, 10).map((d) => ({ estado: d.estado, receita: num(d.receita_bruta) }))

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={serie} margin={{ top: 4, right: 56, bottom: 4, left: 4 }} layout="vertical">
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tick={EIXO} tickLine={false} axisLine={false} tickFormatter={(v) => brlCurto(v)} />
        <YAxis type="category" dataKey="estado" tick={EIXO} tickLine={false} axisLine={false} width={40} />
        <Tooltip contentStyle={estiloTooltip} cursor={{ fill: 'var(--grid)' }} formatter={(v: number) => [brl(v), 'Receita bruta']} />
        <Bar dataKey="receita" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={14} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// --- Fluxo de caixa projetado ------------------------------------------------

export function GraficoCaixa({
  dados,
}: {
  dados: { data: string; liberado: string; previsto: string }[]
}) {
  const serie = dados.map((d) => ({
    dia: rotuloDia(d.data),
    liberado: num(d.liberado),
    previsto: num(d.previsto),
  }))

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={serie} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="dia" tick={EIXO} tickLine={false} axisLine={{ stroke: GRADE }} minTickGap={20} />
        <YAxis tick={EIXO} tickLine={false} axisLine={false} width={62} tickFormatter={(v) => brlCurto(v)} />
        <Tooltip
          contentStyle={estiloTooltip}
          cursor={{ fill: 'var(--grid)' }}
          formatter={(v: number, n: string) => [brl(v), n === 'liberado' ? 'Liberado' : 'Previsto']}
        />
        <Legend
          verticalAlign="top"
          height={26}
          formatter={(v) => (
            <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              {v === 'liberado' ? 'Liberado' : 'Previsto'}
            </span>
          )}
        />
        {/* 2px de folga entre os segmentos empilhados, para que a fronteira
            fique legível mesmo quando as cores são próximas. */}
        <Bar dataKey="liberado" stackId="a" fill="var(--series-1)" barSize={14} />
        <Bar dataKey="previsto" stackId="a" fill="var(--series-3)" radius={[4, 4, 0, 0]} barSize={14} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// --- Curva ABC (Pareto) ------------------------------------------------------

/** Cor por classe. Rampa ordinal de uma matiz: A/B/C medem importância —
 *  magnitude, não identidade —, então a codificação é sequencial. */
export const COR_CLASSE: Record<string, string> = {
  A: 'var(--ramp-5)',
  B: 'var(--ramp-3)',
  C: 'var(--ramp-1)',
}

export function GraficoABC({
  dados,
  limite = 40,
}: {
  dados: { sku: string; classe: string; receita_bruta: string; acumulado_pct: string }[]
  limite?: number
}) {
  // Pareto com muitos SKUs vira uma parede de barras de 1px. Cortar no topo e
  // dizer quantos ficaram de fora é mais honesto do que renderizar ilegível.
  const serie = dados.slice(0, limite).map((d) => ({
    sku: d.sku,
    receita: num(d.receita_bruta),
    acumulado: num(d.acumulado_pct),
    classe: d.classe,
  }))
  const ocultos = Math.max(0, dados.length - limite)

  return (
    <div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={serie} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="sku"
            tick={EIXO}
            tickLine={false}
            axisLine={{ stroke: GRADE }}
            minTickGap={16}
          />
          {/* Eixo único: a curva de acumulado vive no gráfico de linha abaixo,
              e não num segundo eixo Y sobreposto — dois eixos fariam a relação
              entre barra e curva parecer o que a escala escolheu, não o que os
              dados dizem. */}
          <YAxis
            tick={EIXO}
            tickLine={false}
            axisLine={false}
            width={62}
            tickFormatter={(v) => brlCurto(v)}
          />
          <Tooltip
            contentStyle={estiloTooltip}
            formatter={(v: number) => [brl(v), 'Receita']}
            labelFormatter={(l) => `SKU ${l}`}
          />
          <Bar dataKey="receita" radius={[4, 4, 0, 0]} maxBarSize={26}>
            {serie.map((d) => (
              <Cell key={d.sku} fill={COR_CLASSE[d.classe] ?? 'var(--ramp-1)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {ocultos > 0 && (
        <p className="mt-1 text-center text-[11px] text-ink-muted">
          Mostrando os {limite} maiores de {dados.length} SKUs — os {ocultos} restantes
          estão na tabela abaixo.
        </p>
      )}
    </div>
  )
}

export function GraficoAcumuladoABC({
  dados,
  limite = 40,
}: {
  dados: { sku: string; acumulado_pct: string }[]
  limite?: number
}) {
  const serie = dados.slice(0, limite).map((d) => ({
    sku: d.sku,
    acumulado: num(d.acumulado_pct),
  }))

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={serie} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="sku" tick={EIXO} tickLine={false} axisLine={{ stroke: GRADE }} minTickGap={16} />
        <YAxis
          tick={EIXO}
          tickLine={false}
          axisLine={false}
          width={44}
          domain={[0, 100]}
          tickFormatter={(v) => `${v}%`}
        />
        <Tooltip
          contentStyle={estiloTooltip}
          formatter={(v: number) => [`${v.toFixed(1)}%`, 'Receita acumulada']}
          labelFormatter={(l) => `Até o SKU ${l}`}
        />
        {/* As linhas de corte explicam a classificação sem exigir legenda de
            cor: quem lê vê onde A termina e B começa. */}
        <ReferenceLine
          y={80}
          stroke="var(--status-warning)"
          strokeDasharray="4 4"
          label={{ value: 'A · 80%', position: 'insideTopRight', fontSize: 10, fill: 'var(--text-muted)' }}
        />
        <ReferenceLine
          y={95}
          stroke="var(--text-muted)"
          strokeDasharray="4 4"
          label={{ value: 'B · 95%', position: 'insideTopRight', fontSize: 10, fill: 'var(--text-muted)' }}
        />
        <Line
          type="monotone"
          dataKey="acumulado"
          stroke="var(--ramp-4)"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)' }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// --- Série com média móvel ---------------------------------------------------

export function GraficoMediaMovel({
  dados,
  janela,
}: {
  dados: { bucket: string; receita_bruta: string; media_movel_receita: string | null }[]
  janela: number
}) {
  const serie = dados.map((d) => ({
    bucket: rotuloDia(d.bucket),
    diario: num(d.receita_bruta),
    media: d.media_movel_receita === null ? null : num(d.media_movel_receita),
  }))

  const rotulo = (chave: string) => (chave === 'diario' ? 'Receita do dia' : `Média de ${janela} dias`)

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={serie} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={GRADE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="bucket" tick={EIXO} tickLine={false} axisLine={{ stroke: GRADE }} minTickGap={24} />
        <YAxis tick={EIXO} tickLine={false} axisLine={false} width={62} tickFormatter={(v) => brlCurto(v)} />
        <Tooltip
          contentStyle={estiloTooltip}
          formatter={(v: number, n: string) => [brl(v), rotulo(n)]}
          labelFormatter={(l) => `Dia ${l}`}
        />
        <Legend
          verticalAlign="top"
          height={28}
          formatter={(v) => (
            <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{rotulo(v)}</span>
          )}
        />
        {/* Mesma medida com dois níveis de suavização, então a mesma matiz em
            dois pesos: o dia é o ruído (traço fino e recuado), a média é o
            sinal (traço cheio). Duas matizes sugeririam grandezas diferentes. */}
        <Line
          type="monotone"
          dataKey="diario"
          stroke="var(--ramp-1)"
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)' }}
        />
        <Line
          type="monotone"
          dataKey="media"
          stroke="var(--ramp-5)"
          strokeWidth={2}
          dot={false}
          connectNulls={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)' }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// --- Coorte de compradores ---------------------------------------------------

/** Faixas de retenção. Discretas de propósito: cinco degraus se distinguem a
 *  olho, um gradiente contínuo não — e a leitura aqui é comparativa. */
const FAIXAS_RETENCAO = [
  { ate: 5, cor: 'var(--ramp-1)', clara: true },
  { ate: 15, cor: 'var(--ramp-2)', clara: true },
  { ate: 30, cor: 'var(--ramp-3)', clara: false },
  { ate: 60, cor: 'var(--ramp-4)', clara: false },
  { ate: 100, cor: 'var(--ramp-5)', clara: false },
]

function faixaDe(pct: number) {
  return FAIXAS_RETENCAO.find((f) => pct <= f.ate) ?? FAIXAS_RETENCAO[FAIXAS_RETENCAO.length - 1]
}

export function MapaDeCoorte({
  coortes,
}: {
  coortes: { coorte: string; base: number; periodos: { offset: number; compradores: number; retencao_pct: string }[] }[]
}) {
  if (coortes.length === 0) return null

  const maxOffset = Math.max(...coortes.map((c) => Math.max(...c.periodos.map((p) => p.offset))))
  const colunas = Array.from({ length: maxOffset + 1 }, (_, i) => i)

  return (
    <div className="space-y-3">
      {/* A tabela rola dentro do próprio contêiner: uma coorte de 24 meses não
          pode empurrar a página inteira para o lado. */}
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-0.5 text-xs">
          <thead>
            <tr>
              <th className="th sticky left-0 z-10 bg-surface px-2 py-1 text-left">Coorte</th>
              <th className="th px-2 py-1 text-right">Base</th>
              {colunas.map((offset) => (
                <th key={offset} className="th px-2 py-1 text-center">
                  {offset === 0 ? 'Mês 0' : `+${offset}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {coortes.map((linha) => {
              const porOffset = new Map(linha.periodos.map((p) => [p.offset, p]))
              return (
                <tr key={linha.coorte}>
                  <td className="sticky left-0 z-10 whitespace-nowrap bg-surface px-2 py-1 font-medium text-ink">
                    {linha.coorte}
                  </td>
                  <td className="num px-2 py-1 text-right text-ink-soft">{linha.base}</td>
                  {colunas.map((offset) => {
                    const celula = porOffset.get(offset)
                    if (!celula) {
                      return <td key={offset} className="px-2 py-1" aria-hidden />
                    }
                    const pct = num(celula.retencao_pct)
                    const faixa = faixaDe(pct)
                    return (
                      <td
                        key={offset}
                        className="num rounded px-2 py-1 text-center tabular-nums"
                        style={{
                          background: faixa.cor,
                          // O rótulo em toda célula é o que dispensa depender da
                          // cor para ler o valor; a cor só ordena o olhar.
                          color: faixa.clara ? 'var(--text-primary)' : '#ffffff',
                        }}
                        title={`${celula.compradores} de ${linha.base} compradores voltaram`}
                      >
                        {pct.toFixed(0)}%
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
        <span>Retenção:</span>
        {FAIXAS_RETENCAO.map((faixa, indice) => (
          <span key={faixa.ate} className="inline-flex items-center gap-1">
            <span
              aria-hidden
              className="inline-block h-3 w-3 rounded-sm"
              style={{ background: faixa.cor }}
            />
            {indice === 0 ? `até ${faixa.ate}%` : `${FAIXAS_RETENCAO[indice - 1].ate}–${faixa.ate}%`}
          </span>
        ))}
      </div>
    </div>
  )
}
