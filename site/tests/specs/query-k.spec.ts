import { expect, test } from '@playwright/test';

import { queryComposer, queryPanel, submit } from '../support';

test('query page warns and clamps k outside 1..=20', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Query' }).click();

  const panel = queryPanel(page);
  const kInput = panel.locator('input[type="number"]');
  await expect(kInput).toHaveAttribute('min', '1');
  await expect(kInput).toHaveAttribute('max', '20');

  const composer = queryComposer(page);
  const warning = panel.locator('.warning.inline');

  await kInput.fill('21');
  await submit(composer, 'too many');
  await expect(warning).toHaveText('Results are limited to 1–20.');
  await expect(panel.locator('.doc-card')).toHaveCount(3);

  await kInput.fill('0');
  await submit(composer, 'too few');
  await expect(warning).toHaveText('Results are limited to 1–20.');
  await expect(panel.locator('.doc-card')).toHaveCount(1);

  await kInput.fill('-1');
  await submit(composer, 'negative');
  await expect(warning).toHaveText('Results are limited to 1–20.');
  await expect(panel.locator('.doc-card')).toHaveCount(1);

  // A valid k clears the warning and renders the requested canned hits.
  await kInput.fill('5');
  await submit(composer, 'just right');
  await expect(warning).toHaveCount(0);
  await expect(panel.locator('.doc-card')).toHaveCount(3);
  await expect(composer).toHaveValue('just right');
});
