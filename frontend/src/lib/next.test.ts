import { describe, expect, it } from 'vitest'
import { loginPathFor, registerPathFor, safeNextPath } from '@/lib/next'

describe('safeNextPath', () => {
  it('keeps same-origin paths, including query and fragment', () => {
    expect(safeNextPath('/scan/11/issues/5')).toBe('/scan/11/issues/5')
    expect(safeNextPath('/scan/11?tab=findings')).toBe('/scan/11?tab=findings')
    expect(safeNextPath('/')).toBe('/')
  })

  it('collapses missing or empty values to the landing page', () => {
    expect(safeNextPath(null)).toBe('/')
    expect(safeNextPath(undefined)).toBe('/')
    expect(safeNextPath('')).toBe('/')
    expect(safeNextPath('   ')).toBe('/')
  })

  it('rejects absolute external URLs', () => {
    expect(safeNextPath('https://evil.example')).toBe('/')
    expect(safeNextPath('http://evil.example')).toBe('/')
    expect(safeNextPath('javascript:alert(1)')).toBe('/')
  })

  it('rejects protocol-relative and backslash-smuggled hosts', () => {
    expect(safeNextPath('//evil.example')).toBe('/')
    expect(safeNextPath('/\\evil.example')).toBe('/')
    expect(safeNextPath('/\\/evil.example')).toBe('/')
  })

  it('rejects values containing whitespace or control characters', () => {
    expect(safeNextPath('/scan/1 2')).toBe('/')
    expect(safeNextPath('/scan/\t1')).toBe('/')
    expect(safeNextPath('/scan/\n1')).toBe('/')
  })

  it('trims surrounding whitespace from otherwise valid paths', () => {
    expect(safeNextPath('  /scan/11  ')).toBe('/scan/11')
  })
})

describe('loginPathFor / registerPathFor', () => {
  it('encode a validated destination into the query string', () => {
    expect(loginPathFor('/scan/1/issues/2')).toBe(
      '/login?next=%2Fscan%2F1%2Fissues%2F2',
    )
    expect(registerPathFor('/scan/1/issues/2')).toBe(
      '/register?next=%2Fscan%2F1%2Fissues%2F2',
    )
  })

  it('never emit an unsafe destination', () => {
    expect(loginPathFor('https://evil.example')).toBe('/login?next=%2F')
    expect(registerPathFor('//evil.example')).toBe('/register?next=%2F')
  })
})
