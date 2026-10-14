import { expect, test } from '@playwright/test'
import { ACCOUNTS, cancelAuction, loginViaUi } from './helpers'

test.describe('accounts and access control', () => {
  test('a new visitor can register, is signed in, and can log out', async ({ page }) => {
    const email = `e2e-${Date.now()}@example.com`
    await page.goto('/register')
    await page.getByLabel('Full name').fill('E2E Tester')
    await page.getByLabel('E-mail').fill(email)
    await page.getByLabel('Password').fill('Sup3rSecret')
    await page.getByRole('button', { name: 'Create account' }).click()

    await expect(page).toHaveURL('/')
    await page.getByRole('button', { name: 'Account menu' }).click()
    await expect(page.getByText(email)).toBeVisible()
    await page.getByRole('menuitem', { name: 'Log out' }).click()
    await expect(page.getByRole('link', { name: 'Sign up' })).toBeVisible()
  })

  test('wrong credentials give one generic message', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('E-mail').fill(ACCOUNTS.buyer1.email)
    await page.getByLabel('Password').fill('definitely-wrong')
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page.getByRole('alert')).toContainText('Incorrect e-mail or password')
  })

  test('registration validates before submitting', async ({ page }) => {
    await page.goto('/register')
    await page.getByLabel('Password').fill('short')
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByText('At least 8 characters')).toBeVisible()
    await expect(page.getByText('Enter a valid e-mail').or(page.getByText('Enter your e-mail'))).toBeVisible()
  })

  test('protected pages bounce to login and return afterwards', async ({ page }) => {
    await page.goto('/my-bids')
    await expect(page).toHaveURL(/\/login\?next=%2Fmy-bids/)
    await page.getByLabel('E-mail').fill(ACCOUNTS.buyer1.email)
    await page.getByLabel('Password').fill(ACCOUNTS.buyer1.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page).toHaveURL('/my-bids')
    await expect(page.getByRole('heading', { name: 'My bids' })).toBeVisible()
  })

  test('open-redirect attempts via ?next are ignored', async ({ page }) => {
    await page.goto('/login?next=https://evil.example/phish')
    await page.getByLabel('E-mail').fill(ACCOUNTS.buyer1.email)
    await page.getByLabel('Password').fill(ACCOUNTS.buyer1.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page).toHaveURL('/')
  })

  test('bidders cannot open the admin console; admins land there', async ({ page }) => {
    await loginViaUi(page, 'buyer1')
    await page.goto('/admin')
    await expect(page).toHaveURL('/')
    await expect(page.getByRole('link', { name: 'Admin' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Account menu' }).click()
    await page.getByRole('menuitem', { name: 'Log out' }).click()

    await page.goto('/login')
    await page.getByLabel('E-mail').fill(ACCOUNTS.admin.email)
    await page.getByLabel('Password').fill(ACCOUNTS.admin.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page).toHaveURL('/admin')
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  })

  test('the session survives a page reload (refresh-cookie restore)', async ({ page }) => {
    await loginViaUi(page, 'buyer2')
    await page.reload()
    await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible()
  })

  test('admin can create and schedule an auction through the form', async ({ page, request }) => {
    await loginViaUi(page, 'admin')
    await page.goto('/admin/bikes')
    await page.getByRole('button', { name: 'Add bike' }).click()
    const model = `FormBike-${Date.now()}`
    await page.getByLabel('Make', { exact: true }).fill('E2E')
    await page.getByLabel('Model', { exact: true }).fill(model)
    await page.getByRole('button', { name: 'Add bike' }).last().click()
    await expect(page.getByText(`${model}`)).toBeVisible()

    await page.goto('/admin/auctions/new')
    // the bike form defaults to (current year - 2)
    const year = new Date().getFullYear() - 2
    await page.getByLabel('Bike', { exact: true }).selectOption({ label: `${year} E2E ${model}` })
    await page.getByLabel('Starting price').fill('75,000')
    await page.getByRole('button', { name: 'Create & schedule' }).click()
    await expect(page.getByRole('heading', { name: new RegExp(model) })).toBeVisible()
    await expect(page.getByText('LIVE').or(page.getByText('Upcoming')).first()).toBeVisible()

    // Leave no live auction behind in the shared dev database.
    const id = page.url().split('/').pop()!
    await cancelAuction(request, id)
  })
})
