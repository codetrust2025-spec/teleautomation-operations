/**
 * WhatsApp follow-up from a Daily Ops booking card (owner, 24 Nov).
 *
 * The icon beside the candidate's number opens WhatsApp with a short message
 * already written from the card's own data -- the candidate's name, the company
 * and the interview round:
 *   - Pending:  asks the candidate to confirm their attendance;
 *   - Attended: asks how it went and for the result;
 *   - anything else: a plain follow-up about that interview.
 * Nothing is sent: WhatsApp opens with the text ready, and the operator sends it.
 */
/** The number in wa.me form (country code, digits only), or '' when unusable. */
export function whatsappNumber(phone, defaultCountry = '91') {
  let digits = String(phone || '').replace(/\D/g, '')
  if (digits.length === 11 && digits.startsWith('0')) digits = digits.slice(1)
  if (digits.length === 10) return defaultCountry + digits
  return digits.length >= 11 && digits.length <= 15 ? digits : ''
}

/** "your L1 interview with Accenture" -- each part only when the card has it. */
function interviewPhrase({ company, round }) {
  const what = round === 'Assessment' ? 'assessment' : `${round ? `${round} ` : ''}interview`
  return `your ${what}${company ? ` with ${company}` : ''}`
}

/**
 * The message for one card. `round` is the card's known round ('' when it has
 * none) and `status` its resolved attendance status ('' while Pending).
 */
export function whatsappMessage({ name, company, round, status }) {
  const who = String(name || '').trim()
  const hi = who ? `Hi ${who},` : 'Hi,'
  const interview = interviewPhrase({ company: String(company || '').trim(), round: String(round || '').trim() })
  const state = String(status || '').trim().toLowerCase()
  if (!state || state === 'pending') return `${hi} please confirm your attendance for ${interview}.`
  if (state === 'attended') return `${hi} how did ${interview} go? Please share the result when you hear back.`
  return `${hi} following up on ${interview}.`
}

/** The wa.me link with the message filled in, or '' when there is no usable number. */
export function whatsappLink(card) {
  const number = whatsappNumber(card?.phone)
  return number ? `https://wa.me/${number}?text=${encodeURIComponent(whatsappMessage(card))}` : ''
}
