/**
 * Profile + Round: a filter of its own beside Levels (Nov 2026).
 *
 * - Two checkbox lists in one menu: profiles (from the period) and the six
 *   interview rounds, ticked in any combination.
 * - Every tick applies at once (no Apply): the table and the counters re-read
 *   with `technology=...&round=...`. Ticks inside a list match any of them;
 *   the two lists must both match.
 * - It edits the same selections as the Filters panel's Profile / Technology
 *   field and the Levels control, so all three always agree, and the other
 *   filters are left as they were.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { ProfileRoundFilter } from './ProfileRoundFilter.jsx'
import { applyFilters, checkedFilterValues, openFilters, toggleFilterValue } from './filterMenuTestHelpers.js'

const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  ['Asha Varma', 'HR', 'Python', 'Bhavana'], ['Bala Kumar', 'L2', 'Java', 'Nikhila'],
  ['Chitra Rao', 'L2', 'Python', 'Bhavana'], ['Dev Prasad', 'L1', 'Java', 'Tool'],
  ['Esha Pillai', 'HR', 'Angular', 'Tool'],
].map(([name, round, technology, attendee], i) => ({
  id: `r${i}`, name, phone: `90000003${String(i).padStart(2, '0')}`, date: TODAY, time: `${10 + i}:00`,
  time_end: `${10 + i}:30`, technology, interview_round: round, interview_attendee: attendee,
  interview_attendance_status: '',
}))

/** Stands in for the server: OR within a parameter, AND across them. */
function serverRows(url) {
  const pick = (key, field) => {
    const wanted = url.searchParams.getAll(key)
    return row => wanted.length === 0 || wanted.includes(row[field])
  }
  return ROWS.filter(pick('round', 'interview_round'))
    .filter(pick('technology', 'technology'))
    .filter(pick('attendee', 'interview_attendee'))
}

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: {
          by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })),
          by_technology: [{ name: 'Python' }, { name: 'Java' }, { name: 'Angular' }],
        },
        booking_overview: { total: ROWS.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      const rows = serverRows(url)
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

const rosterCalls = () => calls.filter(u => /\/interviews\/(daily|monitor)$/.test(u.pathname))
const globalCalls = () => calls.filter(u => u.pathname.endsWith('/interviews/global'))
const shown = () => [...document.querySelectorAll('.ops-interview-table tbody tr')]
  .map(tr => ROWS.find(r => tr.textContent.includes(r.name))?.name).filter(Boolean)
const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /^Profile and round/ }))
const tick = (list, value) => fireEvent.click(within(screen.getByRole('group', { name: list })).getByRole('checkbox', { name: value }))
const ticked = list => within(screen.getByRole('group', { name: list })).getAllByRole('checkbox')
  .filter(b => b.checked).map(b => b.getAttribute('aria-label'))

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(shown()).toContain('Asha Varma'))
  await act(async () => { await Promise.resolve() })
  return view
}

beforeEach(() => {
  // The chosen mode is remembered per browser; every test starts from Both.
  try { window.localStorage.clear() } catch { /* none */ }
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  mockFetch()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the Profile + Round control', () => {
  it('lists the profiles and the six rounds, with no Apply button', () => {
    render(<ProfileRoundFilter profileOptions={['Java', 'Python']} />)
    openMenu()
    expect(within(screen.getByRole('group', { name: 'Profiles' })).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Java', 'Python'])
    expect(within(screen.getByRole('group', { name: 'Interview rounds' })).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Screening', 'L1', 'L2', 'L3', 'HR', 'Final'])
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
  })

  it('reports every tick at once with both lists, rounds in interview order', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ProfileRoundFilter profileOptions={['Java', 'Python']} onChange={onChange} />)
    openMenu()
    tick('Profiles', 'Python')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Python'], round: [] })
    rerender(<ProfileRoundFilter profiles={['Python']} profileOptions={['Java', 'Python']} onChange={onChange} />)
    tick('Interview rounds', 'HR')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Python'], round: ['HR'] })
    rerender(<ProfileRoundFilter profiles={['Python']} rounds={['HR']} profileOptions={['Java', 'Python']} onChange={onChange} />)
    tick('Interview rounds', 'L2')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Python'], round: ['L2', 'HR'] })
    tick('Profiles', 'Python')
    expect(onChange).toHaveBeenLastCalledWith({ technology: [], round: ['HR'] })
  })

  it('counts both lists on the button, names them in the tooltip, and Clear empties both', () => {
    const onChange = vi.fn()
    render(<ProfileRoundFilter profiles={['Java', 'Python']} rounds={['HR']} profileOptions={['Java', 'Python']} onChange={onChange} />)
    const trigger = screen.getByRole('button', { name: 'Profile and round (3 selected)' })
    expect(trigger).toHaveTextContent('Profile + Round(3)')
    expect(trigger.getAttribute('title')).toBe('Mode: Both\nProfiles: Java, Python\nRounds: HR')
    openMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Clear profile and round' }))
    expect(onChange).toHaveBeenLastCalledWith({ technology: [], round: [] })
  })

  it('searches a long profile list and keeps a ticked profile the period no longer has', () => {
    const many = ['Angular', 'Data Engineering', 'DevOps', 'Java', 'Python', 'React JS', '.NET']
    render(<ProfileRoundFilter profiles={['Old Stack']} profileOptions={many} />)
    openMenu()
    expect(ticked('Profiles')).toEqual(['Old Stack'])
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search profiles' }), { target: { value: 'dev' } })
    expect(within(screen.getByRole('group', { name: 'Profiles' })).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['DevOps'])
  })

  it('closes on Escape and on a click outside', () => {
    render(<div><span data-testid="out">x</span><ProfileRoundFilter /></div>)
    openMenu()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('group', { name: 'Profiles' })).toBeNull()
    openMenu()
    fireEvent.mouseDown(screen.getByTestId('out'))
    expect(screen.queryByRole('group', { name: 'Profiles' })).toBeNull()
  })
})

describe('Profile-wise, Round-wise and Both', () => {
  const lists = () => ['Profiles', 'Interview rounds'].filter(name => screen.queryByRole('group', { name }))
  const mode = () => screen.getAllByRole('radio').find(r => r.getAttribute('aria-checked') === 'true').textContent
  const pick = label => fireEvent.click(screen.getByRole('radio', { name: label }))

  beforeEach(() => { try { window.localStorage.clear() } catch { /* none */ } })

  it('offers the three modes, starting on Both with both lists', () => {
    render(<ProfileRoundFilter profileOptions={['Java', 'Python']} />)
    openMenu()
    expect(screen.getAllByRole('radio').map(r => r.textContent)).toEqual(['Profile-wise', 'Round-wise', 'Both'])
    expect(mode()).toBe('Both')
    expect(lists()).toEqual(['Profiles', 'Interview rounds'])
  })

  it('shows only the relevant checkboxes for each mode', () => {
    render(<ProfileRoundFilter profileOptions={['Java', 'Python']} />)
    openMenu()
    pick('Profile-wise')
    expect(lists()).toEqual(['Profiles'])
    pick('Round-wise')
    expect(lists()).toEqual(['Interview rounds'])
    pick('Both')
    expect(lists()).toEqual(['Profiles', 'Interview rounds'])
  })

  it('keeps multi-select inside a mode', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ProfileRoundFilter profileOptions={['Java', 'Python']} onChange={onChange} />)
    openMenu()
    pick('Profile-wise')
    tick('Profiles', 'Java')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Java'], round: [] })
    rerender(<ProfileRoundFilter profiles={['Java']} profileOptions={['Java', 'Python']} onChange={onChange} />)
    tick('Profiles', 'Python')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Java', 'Python'], round: [] })
  })

  it('applies a mode change at once: the hidden list stops filtering', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <ProfileRoundFilter profiles={['Java']} rounds={['HR', 'Final']} profileOptions={['Java', 'Python']} onChange={onChange} />,
    )
    openMenu()
    pick('Profile-wise')
    expect(onChange).toHaveBeenLastCalledWith({ technology: ['Java'], round: [] })
    rerender(<ProfileRoundFilter profiles={['Java']} profileOptions={['Java', 'Python']} onChange={onChange} />)
    expect(mode()).toBe('Profile-wise')
    pick('Round-wise')
    expect(onChange).toHaveBeenLastCalledWith({ technology: [], round: [] })
  })

  it('switching to Both clears nothing', () => {
    const onChange = vi.fn()
    render(<ProfileRoundFilter profiles={['Java']} profileOptions={['Java']} onChange={onChange} />)
    openMenu()
    pick('Profile-wise')
    pick('Both')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows Both when the hidden list is ticked elsewhere, so no filter is hidden', () => {
    const { rerender } = render(<ProfileRoundFilter profileOptions={['Java']} />)
    openMenu()
    pick('Profile-wise')
    // Levels ticks a round while this menu is on Profile-wise.
    rerender(<ProfileRoundFilter rounds={['L2']} profileOptions={['Java']} />)
    expect(mode()).toBe('Both')
    expect(lists()).toEqual(['Profiles', 'Interview rounds'])
    // Cleared there: back to the chosen mode.
    rerender(<ProfileRoundFilter rounds={[]} profileOptions={['Java']} />)
    expect(mode()).toBe('Profile-wise')
  })

  it('remembers the chosen mode in this browser', () => {
    const { unmount } = render(<ProfileRoundFilter profileOptions={['Java']} />)
    openMenu()
    pick('Round-wise')
    unmount()
    render(<ProfileRoundFilter profileOptions={['Java']} />)
    openMenu()
    expect(mode()).toBe('Round-wise')
    expect(screen.getByRole('button', { name: /^Profile and round/ }).getAttribute('title')).toBe('Filter by profile and interview round')
  })

  it('names the mode in the tooltip once something is ticked', () => {
    render(<ProfileRoundFilter rounds={['HR']} />)
    openMenu()
    pick('Round-wise')
    expect(screen.getByRole('button', { name: /^Profile and round/ }).getAttribute('title')).toBe('Mode: Round-wise\nRounds: HR')
  })
})

describe('on Daily Ops', () => {
  beforeEach(() => { try { window.localStorage.clear() } catch { /* none */ } })

  it('applies each mode click and each tick to the table at once', async () => {
    await renderPanel()
    openMenu()
    fireEvent.click(screen.getByRole('radio', { name: 'Both' }))
    tick('Profiles', 'Python')
    tick('Interview rounds', 'L2')
    await waitFor(() => expect(shown()).toEqual(['Chitra Rao']))

    // Profile-wise: the round filter goes at once, Python rows of any round.
    fireEvent.click(screen.getByRole('radio', { name: 'Profile-wise' }))
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('technology')).toEqual(['Python'])
      expect(last.getAll('round')).toEqual([])
    })
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Chitra Rao']))
    tick('Profiles', 'Angular')
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Chitra Rao', 'Esha Pillai']))

    // Round-wise: the profile filter goes at once; tick HR and L1.
    fireEvent.click(screen.getByRole('radio', { name: 'Round-wise' }))
    tick('Interview rounds', 'HR')
    tick('Interview rounds', 'L1')
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('technology')).toEqual([])
      expect(last.getAll('round')).toEqual(['L1', 'HR'])
    })
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Dev Prasad', 'Esha Pillai']))
  })
})

describe('on Daily Ops (shared selections)', () => {
  it('re-reads the table and the counters on every tick, in any combination', async () => {
    await renderPanel()
    openMenu()

    tick('Profiles', 'Python')
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('technology')).toEqual(['Python']))
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Chitra Rao']))

    tick('Interview rounds', 'L2')
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('technology')).toEqual(['Python'])
      expect(last.getAll('round')).toEqual(['L2'])
    })
    await waitFor(() => expect(shown()).toEqual(['Chitra Rao']))

    tick('Profiles', 'Java')
    tick('Interview rounds', 'HR')
    await waitFor(() => {
      const last = globalCalls().at(-1).searchParams
      expect(last.getAll('technology')).toEqual(['Python', 'Java'])
      expect(last.getAll('round')).toEqual(['L2', 'HR'])
    })
    // (Python or Java) and (L2 or HR): Esha is HR but Angular, Dev is Java but L1.
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Bala Kumar', 'Chitra Rao']))
  })

  it('agrees with Levels and with the Filters panel, both ways', async () => {
    await renderPanel()
    openMenu()
    tick('Profiles', 'Java')
    tick('Interview rounds', 'L1')
    fireEvent.keyDown(document, { key: 'Escape' })

    // Levels shows the round ticked here.
    fireEvent.click(screen.getByRole('button', { name: /^Interview levels/ }))
    expect(within(screen.getByRole('group', { name: 'Interview levels' })).getByRole('checkbox', { name: 'L1' })).toBeChecked()
    fireEvent.keyDown(document, { key: 'Escape' })

    // The Filters panel shows both, and an Apply there shows back here.
    openFilters()
    expect(checkedFilterValues('Technology filter')).toEqual(['Java'])
    expect(checkedFilterValues('Candidate interview level filter')).toEqual(['L1'])
    toggleFilterValue('Technology filter', 'Angular')
    applyFilters()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Profile and round (3 selected)' })).toBeInTheDocument())
    openMenu()
    // Listed in the period's (alphabetical) order.
    expect(ticked('Profiles')).toEqual(['Angular', 'Java'])
  })

  it('leaves the other filters as they were', async () => {
    await renderPanel()
    openFilters()
    toggleFilterValue('Attendee filter', 'Bhavana')
    applyFilters()
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('attendee')).toEqual(['Bhavana']))

    openMenu()
    tick('Interview rounds', 'HR')
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('round')).toEqual(['HR'])
      expect(last.getAll('attendee')).toEqual(['Bhavana'])
    })
    await waitFor(() => expect(shown()).toEqual(['Asha Varma']))
  })

  it('sits between Levels and Filters, and Today stays beside Filters', async () => {
    await renderPanel()
    const order = [...document.querySelector('.ops-roster-controls__filters').children]
      .map(el => el.classList.contains('ops-profile-round-filter') ? 'profile-round' : el.className.split(' ')[0])
    const at = name => order.indexOf(name)
    expect(at('profile-round')).toBe(at('ops-levels-filter') + 1)
    expect(at('ops-filter-dropdown-wrap')).toBe(at('profile-round') + 1)
    expect(at('ops-roster-today')).toBe(at('ops-filter-dropdown-wrap') + 1)
  })
})
