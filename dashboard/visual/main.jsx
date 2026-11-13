import { VIEW } from './mockApi.js'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from '../src/App.jsx'
import { AuthGate } from '../src/components/AuthGate.jsx'
import { ConfirmProvider } from '../src/context/ConfirmContext.jsx'
import { AuthProvider } from '../src/context/AuthContext.jsx'
import { AttendanceProvider } from '../src/attendance/AttendanceContext.jsx'
import '../src/index.css'
import '../src/businessShell.css'
import '../src/dailyOps.css'
import '../src/recruitmentMail.css'
import '../src/components/AiControlsDashboard.css'
import '../src/components/ui/CommonModal.css'
// The shared kit loads last so its tokens and ui-* rules are the final word.
import '../src/styles/tokens.css'
import '../src/styles/ui.css'

// Open the requested page by clicking its sidebar entry once the shell is up:
// the shell keeps the page in component state, so this is how a user gets there.
function openView() {
  if (!VIEW) return
  const tries = { n: 0 }
  const timer = setInterval(() => {
    const link = document.querySelector(`[data-view-id="${VIEW}"]`) ||
      [...document.querySelectorAll('.desktop-sidebar__nav button, .desktop-sidebar__nav a')].find(el => el.textContent.trim().startsWith(VIEW))
    if (link || ++tries.n > 50) { clearInterval(timer); link?.click(); document.body.dataset.harnessReady = '1' }
  }, 100)
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <AuthProvider><ConfirmProvider><AuthGate><AttendanceProvider><App /></AttendanceProvider></AuthGate></ConfirmProvider></AuthProvider>,
)
openView()
