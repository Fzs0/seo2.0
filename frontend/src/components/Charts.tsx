import type { CSSProperties } from 'react'

/**
 * Lightweight SVG trend lines (no chart library) matching the
 * reference design tokens. Numbers come from data hooks.
 */

interface SparklineProps {
  values: number[]
  width?: number
  height?: number
  color: string
  fill?: boolean
  fillColor?: string
  showEndDot?: boolean
  strokeWidth?: number
  style?: CSSProperties
}

export function Sparkline({
  values,
  width = 90,
  height = 28,
  color,
  fill = false,
  fillColor,
  showEndDot = false,
  strokeWidth = 2.5,
  style,
}: SparklineProps) {
  if (values.length < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const range = max - min || 1
  const stepX = width / (values.length - 1)

  const points = values.map((v, i) => {
    const x = i * stepX
    const y = height - ((v - min) / range) * (height - 4) - 2
    return [x, y] as const
  })

  const polyline = points
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ')

  const areaPath = `M0,${height} ${points
    .map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ')} L${width},${height} Z`

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={style}
      aria-hidden="true"
    >
      {fill && fillColor && <path d={areaPath} fill={fillColor} opacity={0.7} />}
      <polyline
        points={polyline}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {showEndDot && (
        <circle
          cx={points[points.length - 1][0]}
          cy={points[points.length - 1][1]}
          r={3}
          fill={color}
        />
      )}
    </svg>
  )
}

interface BarsProps {
  values: number[]
  width?: number
  height?: number
  color: string
  gap?: number
}

export function Bars({
  values,
  width = 90,
  height = 32,
  color,
  gap = 2,
}: BarsProps) {
  if (values.length === 0) return null
  const max = Math.max(...values)
  const totalGap = gap * (values.length - 1)
  const barW = Math.max(3, (width - totalGap) / values.length)
  const usableH = height - 4

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      {values.map((v, i) => {
        const h = (v / max) * usableH
        const x = i * (barW + gap)
        const y = height - h - 2
        return (
          <rect
            key={i}
            x={x.toFixed(1)}
            y={y.toFixed(1)}
            width={barW.toFixed(1)}
            height={Math.max(2, h).toFixed(1)}
            rx={2}
            fill={color}
          />
        )
      })}
    </svg>
  )
}

interface ProgressBarProps {
  value: number // 0..100
  height?: number
  color: string
  background?: string
  trackRadius?: number
}

/** Used inside KPI cards (the rounded bar at bottom of certain KPIs) */
export function InlineProgress({
  value,
  height = 6,
  color,
  background = 'rgba(23,22,20,0.08)',
  trackRadius = 999,
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div
      style={{
        position: 'absolute',
        left: 18,
        right: 18,
        bottom: 14,
        height,
        borderRadius: trackRadius,
        background,
        overflow: 'hidden',
      }}
      aria-hidden="true"
    >
      <div
        style={{
          width: `${clamped}%`,
          height: '100%',
          background: color,
          borderRadius: trackRadius,
        }}
      />
    </div>
  )
}

interface DonutProps {
  value: number // 0..100
  size?: number
  stroke?: number
  color: string
  trackColor?: string
  label?: string
}

export function Donut({
  value,
  size = 52,
  stroke = 9,
  color,
  trackColor = 'rgba(23,22,20,0.1)',
  label,
}: DonutProps) {
  const r = size / 2 - stroke / 2
  const c = 2 * Math.PI * r
  const clamped = Math.max(0, Math.min(100, value))
  const dash = `${(clamped / 100) * c} ${c}`

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={trackColor}
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeDasharray={dash}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      {label && (
        <text
          x="50%"
          y="50%"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="11"
          fontWeight={700}
          fill="var(--ink-900)"
        >
          {label}
        </text>
      )}
    </svg>
  )
}

/** Two-tone triangle decoration seen in the gold "高优先级" KPI */
export function PriorityDecor({ width = 70, height = 40 }: { width?: number; height?: number }) {
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      <circle cx={24} cy={16} r={13} fill="#b7c48e" opacity={0.85} />
      <path d={`M38 ${height} L54 14 L70 ${height} Z`} fill="#93a96c" opacity={0.9} />
    </svg>
  )
}

interface TrianglePeakProps {
  width?: number
  height?: number
}

/** Decorative peak for the "AI Status" KPI */
export function AiPeakDecor({ width = 90, height = 24 }: TrianglePeakProps) {
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      <polyline
        points={`0,18 15,14 30,16 45,10 60,12 75,6 ${width},8`}
        fill="none"
        stroke="#8faecb"
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

interface BarDecorProps {
  width?: number
  height?: number
  gradient?: 'gold' | 'green' | 'pink' | 'blue'
}

const barPalettes = {
  gold: ['#e2c376', '#e2c376', '#d9b25c', '#d9b25c', '#c9a03c', '#c9a03c', '#b98f2e'],
  green: ['#b7c48e', '#b7c48e', '#b7c48e', '#a3b87a', '#a3b87a', '#93a96c', '#93a96c', '#93a96c'],
  pink: ['#e5a395', '#e5a395', '#d77e6c', '#d77e6c', '#c4685a', '#c4685a', '#c4685a'],
  blue: ['#cbd9e6', '#a9c0d6', '#7c9ec1'],
} as const

export function BarDecor({ width = 80, height = 32, gradient = 'gold' }: BarDecorProps) {
  const palette = barPalettes[gradient]
  const total = palette.length
  const gap = 4
  const barW = (width - gap * (total - 1)) / total
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      {palette.map((color, i) => {
        const ratio = (i + 1) / total
        const h = 4 + ratio * (height - 4)
        const x = i * (barW + gap)
        return (
          <rect
            key={i}
            x={x.toFixed(1)}
            y={(height - h).toFixed(1)}
            width={barW.toFixed(1)}
            height={h.toFixed(1)}
            rx={2}
            fill={color}
          />
        )
      })}
    </svg>
  )
}