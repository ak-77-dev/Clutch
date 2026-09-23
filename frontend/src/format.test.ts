import { describe, expect, it } from 'vitest'
import { accelerator, deltaInfo, estimateSize, fmtBytes, fmtClock, fmtDuration, fmtHours, fmtValue, hoursNumber, monogram, plural, timeAgo } from './format'

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

  it('formats playtime, sizes and timecodes', () => {
    expect([fmtHours(0), fmtHours(42), fmtHours(3725), fmtHours(59 * 60)]).toEqual(['0m', '42s', '1h 2m', '59m'])
    expect([hoursNumber(45_000), hoursNumber(600_000)]).toEqual(['12.5', '167'])
    expect([fmtBytes(512), fmtBytes(1536), fmtBytes(5 * 1024 ** 3)]).toEqual(['512 B', '1.5 KB', '5.0 GB'])
    expect([fmtClock(75.4), fmtClock(3725)]).toEqual(['1:15', '1:02:05'])
    expect([monogram('VALORANT'), monogram('Call of Duty Modern Warfare')]).toEqual(['VAL', 'COD'])
  })

  it('turns key presses into Electron accelerators', () => {
    const k = (code: string, key: string, mods: Partial<Record<'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey', boolean>> = {}) =>
      accelerator({ code, key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...mods })
    expect(k('BracketLeft', '[')).toBe('[')
    expect(k('F8', 'F8')).toBe('F8')
    expect(k('KeyK', 'k', { altKey: true })).toBe('Alt+K')
    expect(k('KeyK', 'k')).toBeNull() // bare letters would fire while typing
  })

  it('estimates recording size', () => {
    const s = { rate_control: 'quality', bitrate_mbps: 50, quality: 'high', codec: 'h264', resolution: '1080', fps: 60, buffer_seconds: 60 }
    expect(estimateSize(s).mbps).toBe(28)
    expect(estimateSize({ ...s, codec: 'hevc' }).mbps).toBeLessThan(estimateSize(s).mbps)
    expect(estimateSize({ ...s, rate_control: 'bitrate' })).toEqual({ mbps: 50, perClip: '358 MB' })
  })
})
