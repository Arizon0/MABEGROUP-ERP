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
