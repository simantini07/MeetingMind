/**
 * Bubble background — adapted from Animate UI
 * @see https://animate-ui.com/docs/components/backgrounds/bubble
 * @see https://github.com/imskyleen/animate-ui/blob/main/apps/www/components/backgrounds/bubble.tsx
 */
import { motion, useMotionValue, useSpring } from 'framer-motion'

function cn(...parts) {
  return parts.filter(Boolean).join(' ')
}

const defaultColors = {
  first: '34,211,238',
  second: '167,139,250',
  third: '59,130,246',
  fourth: '236,72,153',
  fifth: '45,212,191',
  sixth: '129,140,248',
}

export default function BubbleBackground({
  className,
  children,
  interactive = true,
  transition = { stiffness: 100, damping: 20 },
  colors = defaultColors,
  ...props
}) {
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)
  const springX = useSpring(mouseX, transition)
  const springY = useSpring(mouseY, transition)

  const handleMove = (e) => {
    if (!interactive) return
    const rect = e.currentTarget.getBoundingClientRect()
    mouseX.set(e.clientX - rect.left - rect.width / 2)
    mouseY.set(e.clientY - rect.top - rect.height / 2)
  }

  const handleLeave = () => {
    mouseX.set(0)
    mouseY.set(0)
  }

  return (
    <div
      data-slot="bubble-background"
      className={cn(
        'relative size-full min-h-full overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950',
        className,
      )}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      {...props}
    >
      <style>
        {`
          [data-slot="bubble-background"] {
            --first-color: ${colors.first};
            --second-color: ${colors.second};
            --third-color: ${colors.third};
            --fourth-color: ${colors.fourth};
            --fifth-color: ${colors.fifth};
            --sixth-color: ${colors.sixth};
          }
        `}
      </style>

      <svg xmlns="http://www.w3.org/2000/svg" className="absolute left-0 top-0 h-0 w-0" aria-hidden>
        <defs>
          <filter id="meetingmind-goo">
            <feGaussianBlur in="SourceGraphic" stdDeviation="10" result="blur" />
            <feColorMatrix
              in="blur"
              mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -8"
              result="goo"
            />
            <feBlend in="SourceGraphic" in2="goo" />
          </filter>
        </defs>
      </svg>

      {/* Screen blend reads clearly on dark bg; no extra opacity wrapper (that hid everything). */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{ filter: 'url(#meetingmind-goo) blur(56px)' }}
        aria-hidden
      >
        <motion.div
          className="absolute left-[10%] top-[10%] size-[80%] rounded-full mix-blend-screen bg-[radial-gradient(circle_at_center,rgba(var(--first-color),0.22)_0%,rgba(var(--first-color),0)_58%)]"
          animate={{ y: [-50, 50, -50] }}
          transition={{ duration: 30, ease: 'easeInOut', repeat: Infinity }}
        />

        <motion.div
          className="absolute inset-0 flex origin-[calc(50%-400px)] items-center justify-center"
          animate={{ rotate: 360 }}
          transition={{ duration: 20, ease: 'linear', repeat: Infinity }}
        >
          <div className="left-[10%] top-[10%] size-[80%] rounded-full mix-blend-screen bg-[radial-gradient(circle_at_center,rgba(var(--second-color),0.2)_0%,rgba(var(--second-color),0)_58%)]" />
        </motion.div>

        <motion.div
          className="absolute inset-0 flex origin-[calc(50%+400px)] items-center justify-center"
          animate={{ rotate: 360 }}
          transition={{ duration: 40, ease: 'linear', repeat: Infinity }}
        >
          <div className="absolute left-[calc(50%-500px)] top-[calc(50%+200px)] size-[80%] rounded-full bg-[radial-gradient(circle_at_center,rgba(var(--third-color),0.18)_0%,rgba(var(--third-color),0)_58%)] mix-blend-screen" />
        </motion.div>

        <motion.div
          className="absolute left-[10%] top-[10%] size-[80%] rounded-full mix-blend-screen bg-[radial-gradient(circle_at_center,rgba(var(--fourth-color),0.16)_0%,rgba(var(--fourth-color),0)_58%)]"
          animate={{ x: [-50, 50, -50] }}
          transition={{ duration: 40, ease: 'easeInOut', repeat: Infinity }}
        />

        <motion.div
          className="absolute inset-0 flex origin-[calc(50%-800px)_calc(50%+200px)] items-center justify-center"
          animate={{ rotate: 360 }}
          transition={{ duration: 20, ease: 'linear', repeat: Infinity }}
        >
          <div className="absolute left-[calc(50%-80%)] top-[calc(50%-80%)] size-[160%] rounded-full mix-blend-screen bg-[radial-gradient(circle_at_center,rgba(var(--fifth-color),0.14)_0%,rgba(var(--fifth-color),0)_58%)]" />
        </motion.div>

        {interactive && (
          <motion.div
            className="absolute size-full rounded-full mix-blend-screen bg-[radial-gradient(circle_at_center,rgba(var(--sixth-color),0.14)_0%,rgba(var(--sixth-color),0)_52%)]"
            style={{ x: springX, y: springY }}
          />
        )}
      </div>

      {/* Light veil so UI text wins; bubbles stay visible underneath */}
      <div
        className="pointer-events-none absolute inset-0 z-[1] bg-gradient-to-b from-slate-950/55 via-slate-950/35 to-slate-950/60"
        aria-hidden
      />

      <div className="relative z-10">{children}</div>
    </div>
  )
}
