import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

const control =
  'w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground placeholder:text-muted-foreground ' +
  'transition-colors focus-visible:border-primary disabled:opacity-60 aria-[invalid=true]:border-danger'

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(control, 'h-10', className)} {...rest} />
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(control, 'min-h-24 py-2', className)} {...rest} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(control, 'h-10 cursor-pointer pr-8', className)} {...rest}>
      {children}
    </select>
  )
}

interface FieldProps {
  label: string
  hint?: string
  error?: string
  className?: string
  children: (props: { id: string; 'aria-invalid': boolean; 'aria-describedby': string | undefined }) => ReactNode
}

/** Label + control + hint/error, wired for accessibility (htmlFor, aria-invalid, aria-describedby). */
export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId()
  const noteId = error || hint ? `${id}-note` : undefined
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children({ id, 'aria-invalid': !!error, 'aria-describedby': noteId })}
      {(error || hint) && (
        <p id={noteId} role={error ? 'alert' : undefined} className={cn('text-xs', error ? 'text-danger' : 'text-muted-foreground')}>
          {error ?? hint}
        </p>
      )}
    </div>
  )
}
