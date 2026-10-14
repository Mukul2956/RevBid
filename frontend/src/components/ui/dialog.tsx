import * as RadixDialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

interface ModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children?: ReactNode
  footer?: ReactNode
  className?: string
}

/** Accessible modal (focus trap, Esc to close, aria labelling) via Radix; bottom sheet on phones. */
export function Modal({ open, onOpenChange, title, description, children, footer, className }: ModalProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-[2px]" />
        <RadixDialog.Content
          className={cn(
            'fixed z-50 flex max-h-[92dvh] w-full flex-col gap-4 overflow-y-auto border border-border bg-card p-6 shadow-xl',
            'bottom-0 left-0 rounded-t-2xl pb-safe',
            'sm:top-1/2 sm:left-1/2 sm:bottom-auto sm:max-w-lg sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl',
            className,
          )}
        >
          <div className="space-y-1 pr-8">
            <RadixDialog.Title className="text-lg font-bold">{title}</RadixDialog.Title>
            {description ? (
              <RadixDialog.Description className="text-sm text-muted-foreground">
                {description}
              </RadixDialog.Description>
            ) : (
              <RadixDialog.Description className="sr-only">{title}</RadixDialog.Description>
            )}
          </div>
          {children}
          {footer && <div className="flex flex-wrap justify-end gap-2 pt-2">{footer}</div>}
          <RadixDialog.Close
            aria-label="Close"
            className="absolute top-4 right-4 cursor-pointer rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-5" />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}
