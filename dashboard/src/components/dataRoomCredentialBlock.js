/**
 * What "Copy all" puts on the clipboard for a dashboard login: the site, the
 * username, the password when the server returns one, and the role.
 */
export function formatCredentialBlock(site, row, { isAdmin = false } = {}) {
  const lines = [
    `Site: ${site}`,
    `Username: ${row.username}`,
  ]
  // Handler passwords are hashed and the server no longer returns them, so
  // there is nothing here to copy and nothing to read back. Printing the line
  // anyway produced "Password: undefined", which reads as a broken row rather
  // than as a password that deliberately cannot be recovered.
  if (row.password) {
    lines.push(`Password: ${row.password}`)
  } else {
    lines.push('Password: Not available (hashed)')
  }
  if (isAdmin) {
    lines.push('Role: admin (full dashboard)')
  } else {
    lines.push('Role: handler')
    if (row.reference) lines.push(`Reference: ${row.reference}`)
  }
  return lines.join('\n')
}
