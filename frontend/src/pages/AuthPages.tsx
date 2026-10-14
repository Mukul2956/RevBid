import { zodResolver } from '@hookform/resolvers/zod'
import { KeyRound, ShieldCheck } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/field'
import { Alert, Card } from '@/components/ui/misc'
import { ApiError, fieldErrors } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { safeNext, useDocumentTitle } from '@/lib/hooks'

// Demo accounts are public on purpose (the assignment asks for shareable credentials).
const SHOW_DEMO = import.meta.env.VITE_SHOW_DEMO_CREDENTIALS !== 'false'
const DEMO = [
  { role: 'Bidder', email: 'buyer1@bikeauction.dev', password: import.meta.env.VITE_DEMO_BUYER_PASSWORD ?? 'Buyer@12345' },
  { role: 'Bidder (2nd)', email: 'buyer2@bikeauction.dev', password: import.meta.env.VITE_DEMO_BUYER_PASSWORD ?? 'Buyer@12345' },
  { role: 'Administrator', email: 'admin@bikeauction.dev', password: import.meta.env.VITE_DEMO_ADMIN_PASSWORD ?? 'Admin@12345' },
]

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="mx-auto grid max-w-md gap-4 py-4 sm:py-10">
      <div className="text-center">
        <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </div>
  )
}

const loginSchema = z.object({
  email: z.string().min(1, 'Enter your e-mail').pipe(z.email('Enter a valid e-mail')),
  password: z.string().min(1, 'Enter your password'),
})
type LoginValues = z.infer<typeof loginSchema>

export function LoginPage() {
  useDocumentTitle('Log in')
  const { login, user } = useAuth()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [formError, setFormError] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) })

  // Single place that decides where a signed-in user belongs (admins land on their console).
  // Redirecting here, rather than also calling navigate() after login(), avoids two navigations racing.
  if (user) return <Navigate to={user.role === 'ADMIN' && next === '/' ? '/admin' : next} replace />

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    try {
      await login(values.email, values.password)
    } catch (err) {
      setFormError(
        err instanceof ApiError && err.code === 'RATE_LIMITED'
          ? `Too many attempts. Try again in ${err.num('retry_after') ?? 'a minute'}s.`
          : err instanceof ApiError && err.code === 'INVALID_CREDENTIALS'
            ? 'Incorrect e-mail or password.'
            : err instanceof Error
              ? err.message
              : 'Login failed',
      )
    }
  })

  return (
    <Shell title="Welcome back" subtitle="Log in to place bids and track your auctions.">
      <Card className="p-6">
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {formError && <Alert tone="danger">{formError}</Alert>}
          <Field label="E-mail" error={errors.email?.message}>
            {(p) => <Input type="email" autoComplete="email" {...p} {...register('email')} />}
          </Field>
          <Field label="Password" error={errors.password?.message}>
            {(p) => <Input type="password" autoComplete="current-password" {...p} {...register('password')} />}
          </Field>
          <Button type="submit" className="w-full" size="lg" loading={isSubmitting}>
            Log in
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted-foreground">
          No account?{' '}
          <Link to={`/register?next=${encodeURIComponent(next)}`} className="font-semibold text-primary underline">
            Sign up
          </Link>
        </p>
      </Card>

      {SHOW_DEMO && (
        <Card className="space-y-3 border-dashed p-4">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <KeyRound className="size-4 text-primary" aria-hidden /> Demo accounts
          </p>
          <ul className="space-y-2">
            {DEMO.map((d) => (
              <li key={d.email} className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0">
                  <span className="block font-medium">{d.role}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {d.email} · {d.password}
                  </span>
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setValue('email', d.email, { shouldValidate: true })
                    setValue('password', d.password, { shouldValidate: true })
                  }}
                >
                  Fill
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </Shell>
  )
}

const registerSchema = z.object({
  display_name: z.string().trim().min(2, 'At least 2 characters').max(60),
  email: z.string().min(1, 'Enter your e-mail').pipe(z.email('Enter a valid e-mail')),
  password: z
    .string()
    .min(8, 'At least 8 characters')
    .refine((v) => /[A-Za-z]/.test(v) && /\d/.test(v), 'Include at least one letter and one digit'),
})
type RegisterValues = z.infer<typeof registerSchema>

export function RegisterPage() {
  useDocumentTitle('Create account')
  const { register: signUp, user } = useAuth()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [formError, setFormError] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema) })

  if (user) return <Navigate to={next} replace />

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    try {
      await signUp(values) // the redirect above takes over once the session exists
    } catch (err) {
      if (err instanceof ApiError && err.code === 'EMAIL_TAKEN') {
        setError('email', { message: 'An account with this e-mail already exists.' })
      } else if (err instanceof ApiError && err.code === 'VALIDATION_ERROR') {
        const fe = fieldErrors(err)
        for (const key of ['display_name', 'email', 'password'] as const) {
          if (fe[key]) setError(key, { message: fe[key] })
        }
      } else {
        setFormError(err instanceof Error ? err.message : 'Sign-up failed')
      }
    }
  })

  return (
    <Shell title="Create your account" subtitle="Free to join. Bid on inspected bikes in minutes.">
      <Card className="p-6">
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {formError && <Alert tone="danger">{formError}</Alert>}
          <Field label="Full name" error={errors.display_name?.message}>
            {(p) => <Input autoComplete="name" {...p} {...register('display_name')} />}
          </Field>
          <Field label="E-mail" error={errors.email?.message}>
            {(p) => <Input type="email" autoComplete="email" {...p} {...register('email')} />}
          </Field>
          <Field label="Password" hint="8+ characters with a letter and a digit." error={errors.password?.message}>
            {(p) => <Input type="password" autoComplete="new-password" {...p} {...register('password')} />}
          </Field>
          <Button type="submit" className="w-full" size="lg" loading={isSubmitting}>
            Create account
          </Button>
        </form>
        <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5" aria-hidden /> Passwords are hashed with Argon2id.
        </p>
        <p className="mt-2 text-center text-sm text-muted-foreground">
          Already registered?{' '}
          <Link to={`/login?next=${encodeURIComponent(next)}`} className="font-semibold text-primary underline">
            Log in
          </Link>
        </p>
      </Card>
    </Shell>
  )
}
