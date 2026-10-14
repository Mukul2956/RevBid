import { expect, test } from '@playwright/test'
import { cancelAuction, createLiveAuction } from './helpers'

// A phone viewport: lists are tall and scroll, which is exactly where scroll-restoration bugs bite.
test.use({ viewport: { width: 390, height: 844 } })

test.describe('scroll behaviour', () => {
  let ids: string[] = []

  test.beforeEach(async ({ request }) => {
    // Own the data: enough live auctions that the list is genuinely taller than the screen.
    ids = []
    for (let i = 0; i < 4; i++) ids.push((await createLiveAuction(request)).id)
  })

  test.afterEach(async ({ request }) => {
    for (const id of ids) await cancelAuction(request, id).catch(() => undefined)
  })

  test('opening an auction from a scrolled list starts at the top of the detail page', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('a[href^="/auctions/"]')
    await page.evaluate(() => window.scrollTo(0, 400))
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100)

    await page.locator('a[href^="/auctions/"]').first().click()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    expect(await page.evaluate(() => window.scrollY)).toBe(0)
  })

  test('changing a filter does not jump the list back to the top', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('a[href^="/auctions/"]')
    await page.evaluate(() => window.scrollTo(0, 400))

    await page.getByLabel('Sort').selectOption('newest') // changes the query string, not the path

    await expect(page).toHaveURL(/sort=newest/)
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100)
  })
})
