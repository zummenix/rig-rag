import { expect, test } from '@playwright/test';

import { chatComposer, chatPanel, queryComposer, queryPanel } from '../support';

test('switches tabs and renders a composer on both pages', async ({ page }) => {
  await page.goto('/');

  // Chat is the default tab; the query page is mounted but hidden.
  await expect(chatPanel(page)).toBeVisible();
  await expect(chatComposer(page)).toBeVisible();
  await expect(queryPanel(page)).toBeHidden();

  await page.getByRole('button', { name: 'Query' }).click();
  await expect(queryPanel(page)).toBeVisible();
  await expect(queryComposer(page)).toBeVisible();
  await expect(chatPanel(page)).toBeHidden();

  await page.getByRole('button', { name: 'Chat' }).click();
  await expect(chatPanel(page)).toBeVisible();
  await expect(queryPanel(page)).toBeHidden();
});
