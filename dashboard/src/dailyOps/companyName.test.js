import { describe, expect, it } from 'vitest'
import { companyName, rowCompanyName } from './companyName.js'
import { sortRosterRows } from './rosterSort.js'

describe('companyName', () => {
  it.each([
    'Virtusa',
    'NAVIKENZ INDIA PRIVATE LIMITED',
    '2COMS Consulting Pvt. Ltd.',
    'Synergy Technologies LLC',
    'LT Mindtree',
    'Persistent Systems',
    'IBM',
    'Tcs',
  ])('keeps a company name exactly as recorded: %s', (name) => {
    expect(companyName(name)).toBe(name)
  })

  it('collapses stray whitespace and edge punctuation, nothing more', () => {
    expect(companyName('  Virtusa \n ')).toBe('Virtusa')
    expect(companyName('"Hexaware",')).toBe('Hexaware')
  })

  it.each([
    // Meeting subjects, the shapes the invite's subject line takes.
    'R1 Interview - Asha Rao',
    'Round 1 Interview |Full Stack Developer |Asha Rao',
    'Interview Scheduled for Specialist, AI SRE Engineer - Asha Rao',
    'L1 Interview : Asha Rao || ServiceNow Developer',
    'Screening call || Infosys || CTH',
    'HCLTech | Interview Schedule - TP1',
    'Technical Round 1 interview scheduled',
    'Interview with Cognizant',
    // Placeholders and things that are not an organisation.
    'Not specified in the email',
    'Unknown company',
    'N/A',
    'Contract',
    'Full time',
    'C2H',
    'Microsoft Teams',
    'HirePro',
    'hr@example.com',
    'https://example.com',
    '12345',
    '',
  ])('is unknown for %j', (value) => {
    expect(companyName(value)).toBe('')
  })

  it('is unknown for anything that is not text', () => {
    for (const value of [null, undefined, 42, { name: 'Virtusa' }, ['Virtusa']]) expect(companyName(value)).toBe('')
  })

  it("is never the candidate's or the attendee's own name", () => {
    expect(companyName('Asha Rao', ['Asha Rao'])).toBe('')
    expect(companyName('Asha Rao Technologies', ['Asha Rao'])).toBe('')
    expect(companyName('Bhavana', ['Asha Rao', 'Bhavana'])).toBe('')
    // A one-word name is only refused as the whole value, so a company that shares it survives,
    // and a name is matched as whole words, never inside one.
    expect(companyName('Infosys', ['Info'])).toBe('Infosys')
    expect(companyName('Ram Systems', ['Ram S'])).toBe('Ram Systems')
  })

  it("is never just a word of the candidate's name", () => {
    for (const value of ['Asha', 'Rao', 'RAO ASHA']) expect(companyName(value, ['Asha Rao'])).toBe('')
    expect(companyName('Rao Technologies', ['Asha Rao'])).toBe('Rao Technologies')
  })

  it('reads the row, never its role, subject, email or notes', () => {
    const row = {
      name: 'Asha Rao', interview_attendee: 'Bhavana', interview_company: '',
      interview_role: 'R1 Interview - Asha Rao', email: 'asha@virtusa.example', notes: 'Virtusa panel',
    }
    expect(rowCompanyName(row)).toBe('')
    expect(rowCompanyName({ ...row, interview_company: 'Virtusa' })).toBe('Virtusa')
    expect(rowCompanyName(undefined)).toBe('')
  })
})

describe('Missing Company First', () => {
  it('counts a recorded value that is not a company as missing', () => {
    const rows = [
      { id: 'named', date: '2026-10-19', time: '10:00', interview_company: 'Virtusa' },
      { id: 'contract', date: '2026-10-18', time: '10:00', interview_company: 'Contract' },
      { id: 'empty', date: '2026-10-17', time: '10:00', interview_company: '' },
    ]
    expect(sortRosterRows(rows, 'missing_company_first').map(row => row.id)).toEqual(['contract', 'empty', 'named'])
  })
})
