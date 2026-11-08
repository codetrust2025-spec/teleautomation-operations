import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const sound = vi.hoisted(() => ({ start: vi.fn(() => true), stop: vi.fn(), volume: vi.fn() }))
vi.mock('./sounds/selectionAlarm.js', () => ({
  ALARM_SECONDS: 30,
  startSelectionAlarm: sound.start,
  stopSelectionAlarm: sound.stop,
  setSelectionAlarmVolume: sound.volume,
}))

import { SelectionAlarmBanner, SelectionAlarmControls } from './SelectionAlarmBanner.jsx'
import { __resetSelectionAlarm, getSelectionAlarmState, raiseSelectionAlarm } from './selectionAlarm.js'

beforeEach(() => { __resetSelectionAlarm(); sound.start.mockClear(); sound.stop.mockClear() })
afterEach(() => { cleanup(); __resetSelectionAlarm() })

describe('Selection alarm banner', () => {
  it('shows nothing until a genuine selection arrives', () => {
    const { container } = render(<SelectionAlarmBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the candidate and company, and Stop Sound acknowledges and hides it', () => {
    render(<SelectionAlarmBanner />)
    act(() => {
      raiseSelectionAlarm({ classification: 'joining_confirmed', notification_id: 'j-1', status: 'Joining Confirmed', candidate_name: 'Asha Example', company_name: 'Example Corp' })
    })
    expect(screen.getByRole('alertdialog', { name: 'Selection alert' })).toHaveTextContent('Joining Confirmed')
    expect(screen.getByText('Asha Example · Example Corp')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop Sound' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(getSelectionAlarmState().pending).toEqual([])
    expect(sound.stop).toHaveBeenCalled()
  })

  it('Mute keeps the banner up and is remembered', () => {
    render(<SelectionAlarmBanner />)
    act(() => { raiseSelectionAlarm({ classification: 'offer_received', notification_id: 'o-1' }) })
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    expect(screen.getByRole('button', { name: 'Unmute' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Sound muted')
    expect(getSelectionAlarmState().muted).toBe(true)
  })

  it('the volume slider sets the alarm volume', () => {
    render(<SelectionAlarmControls />)
    fireEvent.change(screen.getByLabelText('Selection alarm volume'), { target: { value: '35' } })
    expect(getSelectionAlarmState().volume).toBeCloseTo(0.35)
    expect(screen.getByText('35%')).toBeInTheDocument()
  })

  it('Test alarm plays one burst and can be stopped', () => {
    render(<SelectionAlarmControls showTest />)
    fireEvent.click(screen.getByRole('button', { name: 'Test alarm' }))
    expect(sound.start).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Stop test' }))
    expect(sound.stop).toHaveBeenCalled()
  })
})
