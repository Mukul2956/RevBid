import { expect, type APIRequestContext, type Page } from '@playwright/test'

export const ACCOUNTS = {
  buyer1: { email: 'buyer1@bikeauction.dev', password: process.env.E2E_BUYER_PASSWORD ?? 'Buyer@12345' },
  buyer2: { email: 'buyer2@bikeauction.dev', password: process.env.E2E_BUYER_PASSWORD ?? 'Buyer@12345' },
  admin: { email: 'admin@bikeauction.dev', password: process.env.E2E_ADMIN_PASSWORD ?? 'Admin@12345' },
}

const apiBase = () => `${process.env.E2E_BASE_URL ?? 'http://localhost:5173'}/api/v1`

export async function apiToken(request: APIRequestContext, who: keyof typeof ACCOUNTS): Promise<string> {
  const res = await request.post(`${apiBase()}/auth/login`, { data: ACCOUNTS[who] })
  expect(res.ok(), `login as ${who}`).toBeTruthy()
  return (await res.json()).access_token as string
}

/** Create a bike and a LIVE auction for it through the admin API; returns the auction id. */
export async function createLiveAuction(
  request: APIRequestContext,
  opts: { minutes?: number; reserve?: number } = {},
): Promise<{ id: string; title: string }> {
  const token = await apiToken(request, 'admin')
  const headers = { Authorization: `Bearer ${token}` }
  const model = `E2E-${Date.now()}-${Math.floor(Math.random() * 1000)}`

  const bike = await request.post(`${apiBase()}/admin/bikes`, {
    headers,
    data: { make: 'TestMake', model, year: 2021, km_driven: 12_000, owner_number: 1, condition: 'GOOD', description: 'E2E bike', images: [] },
  })
  expect(bike.ok()).toBeTruthy()
  const bikeId = (await bike.json()).id as string

  const { server_time } = await (await request.get(`${apiBase()}/time`)).json()
  const start = new Date(server_time)
  const end = new Date(start.getTime() + (opts.minutes ?? 30) * 60_000)
  const auction = await request.post(`${apiBase()}/admin/auctions`, {
    headers,
    data: {
      bike_id: bikeId,
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      starting_price: 5_000_000,
      min_increment: 50_000,
      reserve_price: opts.reserve ?? null,
      schedule: true,
    },
  })
  expect(auction.ok(), await auction.text()).toBeTruthy()
  const id = (await auction.json()).id as string

  // The scheduler promotes SCHEDULED -> LIVE within a tick or two.
  await expect
    .poll(async () => (await (await request.get(`${apiBase()}/auctions/${id}`)).json()).status, { timeout: 15_000 })
    .toBe('LIVE')
  return { id, title: `2021 TestMake ${model}` }
}

export async function cancelAuction(request: APIRequestContext, id: string, reason = 'E2E cleanup'): Promise<void> {
  const token = await apiToken(request, 'admin')
  await request.post(`${apiBase()}/admin/auctions/${id}/cancel`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { reason },
  })
}

export async function loginViaUi(page: Page, who: keyof typeof ACCOUNTS): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('E-mail').fill(ACCOUNTS[who].email)
  await page.getByLabel('Password').fill(ACCOUNTS[who].password)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible()
}
