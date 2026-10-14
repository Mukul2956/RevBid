import { SearchX } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState } from '@/components/ui/misc'
import { useDocumentTitle } from '@/lib/hooks'

export default function NotFoundPage() {
  useDocumentTitle('Not found')
  return (
    <EmptyState
      icon={<SearchX className="size-10" />}
      title="We couldn't find that page"
      action={
        <Link to="/" className="text-sm font-semibold text-primary underline">
          Back to live auctions
        </Link>
      }
    >
      The auction or page may have been removed, or the link is mistyped.
    </EmptyState>
  )
}
