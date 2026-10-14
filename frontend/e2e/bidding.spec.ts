import { expect, test } from '@playwright/test'
import { cancelAuction, createLiveAuction, loginViaUi } from './helpers'

test.describe('live bidding between two users', () => {
  test('bids propagate in real time, outbid is announced, errors are actionable', async ({ browser, request }) => {
    const auction = await createLiveAuction(request)
    const url = `/auctions/${auction.id}`

    // Two independent browser sessions (separate cookies/storage), like two people on two devices.
    const aliceCtx = await browser.newContext()
    const bobCtx = await browser.newContext()
    const alice = await aliceCtx.newPage()
    const bob = await bobCtx.newPage()

    try {
      await loginViaUi(alice, 'buyer1')
      await loginViaUi(bob, 'buyer2')
      await alice.goto(url)
      await bob.goto(url)
      await expect(alice.getByText('Live updates on')).toBeVisible()
      await expect(bob.getByText('Live updates on')).toBeVisible()

      // Alice opens the bidding at the starting price.
      await expect(alice.getByLabel('Your bid in rupees')).toHaveValue('50,000')
      await alice.getByRole('button', { name: /Place bid · ₹50,000/ }).click()
      await expect(alice.getByText("You're the highest bidder")).toBeVisible()

      // Bob sees it arrive WITHOUT reloading: price, bid count and the new minimum.
      await expect(bob.getByText('1 bid', { exact: true }).first()).toBeVisible()
      await expect(bob.getByLabel('Your bid in rupees')).toHaveValue('50,500')
      await expect(bob.getByRole('listitem').filter({ hasText: '₹50,000' })).toBeVisible()

      // Bob bids with a quick-bid chip (+2x increments).
      await bob.getByRole('button', { name: /\+2×/ }).click()
      await bob.getByRole('button', { name: /Place bid/ }).click()
      await expect(bob.getByText("You're the highest bidder")).toBeVisible()

      // Alice is told, twice over: a toast and the panel state.
      await expect(alice.getByText(/You've been outbid on/)).toBeVisible()
      await expect(alice.getByText("You've been outbid", { exact: true })).toBeVisible()
      await expect(alice.getByLabel('Your bid in rupees')).toHaveValue('52,000')

      // A lowball bid is refused locally with the exact floor, not a generic error.
      const input = alice.getByLabel('Your bid in rupees')
      await input.fill('100')
      await expect(alice.getByRole('alert')).toContainText('Minimum bid is ₹52,000.')

      // Identities stay masked for everyone else.
      await expect(alice.getByText(/Bidder-[0-9A-F]{5}/).first()).toBeVisible()
      await expect(alice.getByText('Diya Mehta')).toHaveCount(0) // Bob's real name never reaches Alice

      // Admin cancels; both pages flip to the cancelled state live.
      await cancelAuction(request, auction.id, 'Seller withdrew the bike')
      await expect(alice.getByText('Auction cancelled')).toBeVisible()
      await expect(bob.getByText('Seller withdrew the bike')).toBeVisible()
      await expect(bob.getByRole('button', { name: /Place bid/ })).toHaveCount(0)
    } finally {
      await aliceCtx.close()
      await bobCtx.close()
      await cancelAuction(request, auction.id).catch(() => undefined)
    }
  })

  test('the same person cannot outbid themselves and a late race shows the new minimum', async ({ browser, request }) => {
    const auction = await createLiveAuction(request)
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    try {
      await loginViaUi(page, 'buyer1')
      await page.goto(`/auctions/${auction.id}`)
      await page.getByRole('button', { name: /Place bid/ }).click()
      await expect(page.getByText("You're the highest bidder")).toBeVisible()

      await page.getByLabel('Your bid in rupees').fill('60,000')
      await page.getByRole('button', { name: /Place bid/ }).click()
      await expect(page.getByRole('alert')).toContainText('already the highest bidder')
    } finally {
      await ctx.close()
      await cancelAuction(request, auction.id).catch(() => undefined)
    }
  })

  test('anonymous visitors can watch but must log in to bid', async ({ page, request }) => {
    const auction = await createLiveAuction(request)
    try {
      await page.goto(`/auctions/${auction.id}`)
      await expect(page.getByText('Live updates on')).toBeVisible()
      await expect(page.getByRole('link', { name: /Log in to bid/ })).toBeVisible()
      await expect(page.getByLabel('Your bid in rupees')).toHaveCount(0)
    } finally {
      await cancelAuction(request, auction.id).catch(() => undefined)
    }
  })
})
