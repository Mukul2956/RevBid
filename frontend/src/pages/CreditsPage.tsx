import { Card } from '@/components/ui/misc'
import { useDocumentTitle } from '@/lib/hooks'
import { IMAGE_CREDITS } from '@/lib/imageCredits'

export default function CreditsPage() {
  useDocumentTitle('Photo credits')
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight">Photo credits</h1>
        <p className="text-muted-foreground">
          Demo listings use openly licensed photographs from Wikimedia Commons. Thank you to the photographers
          below; each link opens the original file page with its full licence terms.
        </p>
      </div>
      <Card className="divide-y divide-border">
        {IMAGE_CREDITS.map((c) => (
          <div key={c.bike} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4 text-sm">
            <span className="font-semibold">{c.bike}</span>
            <span className="text-muted-foreground">
              {c.author || 'Unknown'} ·{' '}
              <a className="underline hover:text-foreground" href={c.source} target="_blank" rel="noreferrer">
                {c.license}
              </a>
            </span>
          </div>
        ))}
      </Card>
    </div>
  )
}
