/**
 * The company Daily Ops shows for an interview: an organisation's name, or ''.
 *
 * The stored value is shown as it was recorded, with nothing added to it.
 * Anything that cannot be a company name is shown as unknown rather than
 * passed off as one: a placeholder ("Not specified"), an employment type
 * ("Contract"), a meeting platform, the candidate's own name, an address, or
 * a meeting title ("R1 Interview - <candidate>"). The same rules decide what
 * the invite reader may store, in
 * features/ollama_invite_extract.clean_company_name; keep the two together.
 */

const MAX_LENGTH = 120
const MAX_WORDS = 10

const NO_COMPANY = new Set([
  '', 'na', 'n a', 'none', 'null', 'nil', 'unknown', 'unspecified', 'tbd', 'not visible',
  'not available', 'not mentioned', 'not specified', 'not found', 'not provided',
  'not applicable', 'company', 'company name', 'client', 'client name',
  'organisation', 'organization', 'customer',
])
const NOT_A_COMPANY = new Set([
  'hirepro', 'flocareer', 'zoom', 'zoom meeting', 'teams', 'microsoft teams', 'google meet', 'meet',
  'barraiser', 'skype', 'webex', 'cisco webex', 'gmail', 'google calendar', 'calendar', 'outlook',
  'whatsapp', 'telegram', 'linkedin', 'naukri', 'codility', 'hackerrank', 'hackerearth',
  'mettl', 'mercer mettl', 'amcat', 'cocubes', 'jobma', 'hirevue', 'interview', 'interviewer',
])
// How a job is offered, not who offers it.
const EMPLOYMENT_TYPES = new Set([
  'contract', 'contractual', 'contract to hire', 'contract to hire c2h', 'c2h', 'cth', 'c2c', 'full time',
  'fulltime', 'full time employment', 'fte', 'permanent', 'part time', 'freelance', 'freelancer',
  'internship', 'intern', 'lateral', 'walk in', 'walkin', 'remote', 'hybrid', 'onsite', 'wfh', 'wfo',
])
const NO_COMPANY_FIRST_WORDS = new Set([
  'unknown', 'unspecified', 'undefined', 'unnamed', 'not', 'no', 'none', 'null', 'tbd', 'tba', 'na', 'n',
])
const NAME_IS_MISSING = [
  'not visible', 'not mentioned', 'not specified', 'not provided', 'not available', 'not found', 'not named',
  'not stated', 'not given', 'not shown', 'not clear', 'not disclosed', 'name unknown',
]
const GENERIC_WORDS = new Set([
  'company', 'organisation', 'organization', 'client', 'customer', 'employer', 'name', 'the', 'a', 'hiring',
  'end', 'interviewing', 'firm', 'business', 'unknown', 'your', 'our', 'this', 'that',
])
// A calendar subject, not an organisation: "Round 1 Interview | Full Stack Developer | <candidate>".
const MEETING_TITLE = /\b(interview|interviews|screening|discussion|scheduled|reschedule[d]?|invitation|round\s*\d+|l[1-4]\s*(round|interview|discussion))\b/i
const SEPARATOR = /\|\||\s\|\s|\s[-–—:]\s|@/

export function companyKey(value) {
  return String(value ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ')
}

/**
 * `people` are names the company must not be: the candidate's, the attendee's.
 */
export function companyName(value, people = []) {
  if (typeof value !== 'string') return ''
  const text = value.split(/\s+/).join(' ').trim().replace(/^[\s"'`,;:\-|]+|[\s"'`,;:\-|]+$/g, '')
  if (!text || text.length > MAX_LENGTH) return ''
  const lower = text.toLowerCase()
  if (lower.includes('@') || lower.includes('://') || lower.startsWith('www.')) return ''
  if (!/[a-z]/i.test(text) || text.split(' ').length > MAX_WORDS) return ''
  const key = companyKey(text)
  if (NO_COMPANY.has(key) || NOT_A_COMPANY.has(key) || EMPLOYMENT_TYPES.has(key)) return ''
  const words = key.split(' ')
  if (NO_COMPANY_FIRST_WORDS.has(words[0]) || words.every(word => GENERIC_WORDS.has(word))) return ''
  if (NAME_IS_MISSING.some(phrase => key.includes(phrase))) return ''
  if (MEETING_TITLE.test(text) || SEPARATOR.test(text)) return ''
  for (const person of people) {
    const name = companyKey(person)
    if (name && (key === name || (name.includes(' ') && ` ${key} `.includes(` ${name} `)))) return ''
  }
  return text
}

/** The company shown on a roster row: never the role, the subject or a note. */
export function rowCompanyName(row) {
  return companyName(row?.interview_company, [row?.name, row?.interview_attendee])
}
