/**
 * How a Data Room credential is named and typed, wherever it appears (20 Oct).
 *
 * The twin of features/credential_names.py, which names records when they are
 * saved and in every export; this names them in the table and its search, so
 * the screen and the downloaded file agree. The rule:
 *
 * - spaces collapsed, and a word typed all in lower case (or a long word all in
 *   capitals) given a capital: "uday kumar" -> "Uday Kumar";
 * - a word with capitals of its own inside it left as typed: "RajaGopal";
 * - technology names spelled their own way: "Servicenow" -> "ServiceNow";
 * - a last word that only repeats the credential's type dropped, because the
 *   type is shown beside the name: "Asha Rao Naukri" (Naukri) -> "Asha Rao".
 *   A word before the end is kept, and a name is never emptied.
 */

export const TECH_NAMES = {
  servicenow: 'ServiceNow', sailpoint: 'SailPoint', salesforce: 'Salesforce', devops: 'DevOps',
  mulesoft: 'MuleSoft', peoplesoft: 'PeopleSoft', sharepoint: 'SharePoint', powerapps: 'PowerApps',
  powerbi: 'PowerBI', workday: 'Workday', databricks: 'Databricks', snowflake: 'Snowflake',
  tableau: 'Tableau', oracle: 'Oracle', kubernetes: 'Kubernetes', jira: 'Jira', linkedin: 'LinkedIn',
  github: 'GitHub', gitlab: 'GitLab', javascript: 'JavaScript', typescript: 'TypeScript',
  reactjs: 'ReactJS', nodejs: 'NodeJS', ios: 'iOS', macos: 'macOS',
  sap: 'SAP', aws: 'AWS', gcp: 'GCP', qa: 'QA', ui: 'UI', ux: 'UX', sql: 'SQL', etl: 'ETL',
  api: 'API', hr: 'HR', it: 'IT', ai: 'AI', ml: 'ML', bi: 'BI', sdet: 'SDET', crm: 'CRM',
  gmail: 'Gmail', naukri: 'Naukri', telegram: 'Telegram',
}

export const LOGINS_TYPE = 'Dashboard / Tool Login'
export const NOT_SET_TYPE = 'Not Set'
const SERVICE_TYPE_NAMES = { Naukri: 'Naukri', Gmail: 'Gmail', Telegram: 'Telegram', Tool: LOGINS_TYPE }

// Leading punctuation, the word, trailing punctuation: "(current)" -> "(", "current", ")".
const EDGE = /^([^\p{L}\p{N}_]*)(.*?)([^\p{L}\p{N}_]*)$/u

/** The type a credential is shown and exported as. */
export function credentialType(service, { login = false } = {}) {
  if (login) return LOGINS_TYPE
  return SERVICE_TYPE_NAMES[String(service || '').trim()] || NOT_SET_TYPE
}

function capitalise(word) {
  const [, lead, core, trail] = EDGE.exec(word)
  if (!core) return word
  const known = TECH_NAMES[core.toLowerCase()]
  if (known) return `${lead}${known}${trail}`
  const letters = [...core].filter(c => c.toLowerCase() !== c.toUpperCase())
  if (!letters.length) return word
  const allLower = letters.every(c => c === c.toLowerCase())
  const shouting = letters.every(c => c === c.toUpperCase()) && letters.length > 3
  if (!(allLower || shouting)) return word // capitals of its own: "RajaGopal", "QA"
  // Each part of a hyphenated or apostrophised word: "sai-kiran" -> "Sai-Kiran".
  const parts = core.toLowerCase().split(/([-'])/)
  return lead + parts.map(p => (p === '-' || p === "'" ? p : p.slice(0, 1).toUpperCase() + p.slice(1))).join('') + trail
}

const bare = word => EDGE.exec(word)[2].toLowerCase()

/** The credential's name, standardised. `service` is its stored service (Naukri, Gmail, Telegram, Tool). */
export function standardCredentialName(name, service = '', { dropType = true } = {}) {
  const text = String(name || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  const words = text.split(' ').map(capitalise)
  const typeWord = String(service || '').trim().toLowerCase()
  if (dropType && typeWord && typeWord !== 'tool') {
    while (words.length > 1 && bare(words[words.length - 1]) === typeWord) words.pop()
  }
  return words.join(' ')
}
