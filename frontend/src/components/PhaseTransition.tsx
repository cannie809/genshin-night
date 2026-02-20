import { useEffect, useRef, useState } from 'react'
import { useGameStore } from '../store/gameStore'

type TransitionType = 'night' | 'day' | null

/** Determine if a phase string is night or day. */
function phaseGroup(phase: string): 'night' | 'day' | null {
  if (phase.startsWith('NIGHT_')) return 'night'
  if (phase.startsWith('DAY_')) return 'day'
  return null
}

export const TRANSITION_DURATION = 2800 // total overlay visible time (ms)

export function PhaseTransition() {
  const { gameState, phaseTransition, setPhaseTransition } = useGameStore()
  const prevGroupRef = useRef<string | null>(null)
  const [transition, setTransition] = useState<TransitionType>(null)
  const [fading, setFading] = useState(false)

  // Manual trigger from store (used by ActionPanel for "进入夜晚")
  useEffect(() => {
    if (!phaseTransition) return
    setTransition(phaseTransition)
    setFading(false)
    setPhaseTransition(null) // clear trigger
  }, [phaseTransition])

  // Track phase group for prevGroupRef (used externally if needed)
  useEffect(() => {
    if (!gameState) return
    const currentGroup = phaseGroup(gameState.phase)
    if (currentGroup) {
      prevGroupRef.current = currentGroup
    }
  }, [gameState?.phase])

  // Animation timing: fade-out then remove
  useEffect(() => {
    if (!transition) return
    const fadeTimer = setTimeout(() => setFading(true), TRANSITION_DURATION - 800)
    const removeTimer = setTimeout(() => setTransition(null), TRANSITION_DURATION)
    return () => {
      clearTimeout(fadeTimer)
      clearTimeout(removeTimer)
    }
  }, [transition])

  if (!transition) return null

  return transition === 'night' ? (
    <NightTransition fading={fading} />
  ) : (
    <DayTransition fading={fading} />
  )
}

/** Full-screen night transition — moon rises with stars. */
function NightTransition({ fading }: { fading: boolean }) {
  return (
    <div
      className={`pointer-events-none fixed inset-0 z-[100] flex items-center justify-center transition-opacity duration-700 ${fading ? 'opacity-0' : 'opacity-100'}`}
    >
      {/* Dark overlay */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#0a0a2e] via-[#0d0d3a] to-[#05051a] opacity-95" />

      {/* Stars */}
      <div className="absolute inset-0 overflow-hidden">
        {Array.from({ length: 30 }).map((_, i) => (
          <Star key={i} index={i} />
        ))}
      </div>

      {/* Moon */}
      <div className="relative animate-[moon-rise_1.2s_ease-out_both]">
        <div className="relative">
          {/* Moon glow */}
          <div className="absolute inset-0 -m-8 animate-pulse rounded-full bg-indigo-400/20 blur-3xl" />
          {/* Moon body */}
          <svg
            width="120"
            height="120"
            viewBox="0 0 120 120"
            className="drop-shadow-[0_0_30px_rgba(199,210,254,0.4)]"
          >
            <defs>
              <radialGradient id="moonGrad" cx="40%" cy="35%">
                <stop offset="0%" stopColor="#e0e7ff" />
                <stop offset="60%" stopColor="#c7d2fe" />
                <stop offset="100%" stopColor="#a5b4fc" />
              </radialGradient>
            </defs>
            {/* Main moon circle */}
            <circle cx="60" cy="60" r="45" fill="url(#moonGrad)" />
            {/* Craters */}
            <circle cx="42" cy="45" r="8" fill="#a5b4fc" opacity="0.3" />
            <circle cx="70" cy="55" r="5" fill="#a5b4fc" opacity="0.25" />
            <circle cx="55" cy="72" r="6" fill="#a5b4fc" opacity="0.2" />
            <circle cx="75" cy="38" r="4" fill="#a5b4fc" opacity="0.2" />
          </svg>
        </div>
      </div>

      {/* Text */}
      <div className="absolute bottom-1/3 animate-[fade-up_1s_ease-out_0.5s_both] text-center">
        <p className="font-display text-3xl font-bold tracking-[0.3em] text-indigo-200/90">
          夜幕降临
        </p>
        <p className="mt-2 text-sm tracking-widest text-indigo-300/50">NIGHT FALLS</p>
      </div>
    </div>
  )
}

/** Full-screen day transition — sun rises with rays. */
function DayTransition({ fading }: { fading: boolean }) {
  return (
    <div
      className={`pointer-events-none fixed inset-0 z-[100] flex items-center justify-center transition-opacity duration-700 ${fading ? 'opacity-0' : 'opacity-100'}`}
    >
      {/* Warm overlay */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#1a0f00] via-[#2d1800] to-[#0f0a00] opacity-95" />

      {/* Sun + Rays rise together */}
      <div className="relative animate-[sun-rise_1.2s_ease-out_both]">
        {/* Light rays — behind sun, anchored at sun center */}
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ overflow: 'visible' }}
        >
          <div className="animate-[sun-rays-spin_8s_linear_infinite] opacity-30">
            {Array.from({ length: 12 }).map((_, i) => (
              <div
                key={i}
                className="absolute origin-center"
                style={{
                  width: '4px',
                  height: '300px',
                  background: 'linear-gradient(to bottom, rgba(251,191,36,0.6), transparent)',
                  left: '50%',
                  top: '50%',
                  transform: `translate(-50%, -100%) rotate(${i * 30}deg)`,
                  transformOrigin: 'bottom center',
                }}
              />
            ))}
          </div>
        </div>
        {/* Sun body + glow */}
        <div className="relative">
          {/* Sun glow */}
          <div className="absolute inset-0 -m-12 animate-pulse rounded-full bg-amber-400/25 blur-3xl" />
          <div className="absolute inset-0 -m-6 rounded-full bg-orange-300/15 blur-2xl" />
          {/* Sun body */}
          <svg
            width="120"
            height="120"
            viewBox="0 0 120 120"
            className="drop-shadow-[0_0_40px_rgba(251,191,36,0.5)]"
          >
            <defs>
              <radialGradient id="sunGrad" cx="45%" cy="40%">
                <stop offset="0%" stopColor="#fef3c7" />
                <stop offset="40%" stopColor="#fbbf24" />
                <stop offset="100%" stopColor="#f59e0b" />
              </radialGradient>
            </defs>
            <circle cx="60" cy="60" r="42" fill="url(#sunGrad)" />
          </svg>
        </div>
      </div>

      {/* Text */}
      <div className="absolute bottom-1/3 animate-[fade-up_1s_ease-out_0.5s_both] text-center">
        <p className="font-display text-3xl font-bold tracking-[0.3em] text-amber-200/90">天亮了</p>
        <p className="mt-2 text-sm tracking-widest text-amber-300/50">DAY BREAKS</p>
      </div>
    </div>
  )
}

/** Individual twinkling star. */
function Star({ index }: { index: number }) {
  // Deterministic pseudo-random positioning based on index
  const x = (index * 37 + 13) % 100
  const y = (index * 53 + 7) % 80
  const size = (index % 3) + 1
  const delay = (index * 131) % 2000
  const duration = 1500 + ((index * 71) % 1500)

  return (
    <div
      className="absolute animate-[twinkle_var(--dur)_ease-in-out_var(--delay)_infinite] rounded-full bg-white"
      style={
        {
          left: `${x}%`,
          top: `${y}%`,
          width: `${size}px`,
          height: `${size}px`,
          '--delay': `${delay}ms`,
          '--dur': `${duration}ms`,
        } as React.CSSProperties
      }
    />
  )
}
