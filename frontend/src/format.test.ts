import { describe, expect, it } from 'vitest'
import { deltaInfo, fmtDuration, fmtValue, plural, timeAgo } from './format'

describe('format', () => {
  it('pluralizes character labels', () => {
    expect(['Champion', 'Agent', 'Car', 'Hero'].map(plural)).toEqual(['Champions', 'Agents', 'Cars', 'Heroes'])
  })

  it('formats metric values', () => {
    expect(fmtValue(7.234, 'float1')).toBe('7.2')
    expect(fmtValue(3.456, 'float2')).toBe('3.46')
    expect(fmtValue(58.7, 'pct')).toBe('59%')
    expect(fmtValue(4.25, 'pct')).toBe('4.3%')
    expect(fmtValue(1234.6, 'int')).toBe((1235).toLocaleString())
    expect(fmtValue(null)).toBe('—')
  })

  it('formats durations', () => {
    expect(fmtDuration(1695)).toBe('28:15')
    expect(fmtDuration(59)).toBe('0:59')
  })

  it('knows when lower is better', () => {
    expect(deltaInfo(-1.2, false).tone).toBe('up')
    expect(deltaInfo(-1.2, true).tone).toBe('down')
    expect(deltaInfo(0).tone).toBe('flat')
    expect(deltaInfo(12.4).text).toBe('▲ 12')
  })

  it('renders relative times', () => {
    const now = new Date('2026-09-22T12:00:00Z')
    expect(timeAgo('2026-09-22T11:30:00Z', now)).toBe('30m ago')
    expect(timeAgo('2026-09-22T07:00:00Z', now)).toBe('5h ago')
    expect(timeAgo('2026-09-20T12:00:00Z', now)).toBe('2d ago')
  })
})
