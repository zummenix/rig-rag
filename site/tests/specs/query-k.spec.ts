import { expect, test } from '@playwright/test';

import { queryComposer, queryPanel, submit } from '../support';

test('query page enforces k in 1..=20', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Query' }).click();

  const panel = queryPanel(page);
  const kInput = panel.locator('input[type="number"]');
  await expect(kInput).toHaveAttribute('min', '1');
  await expect(kInput).toHaveAttribute('max', '20');

  const composer = queryComposer(page);
  const inlineError = panel.locator('.error.inline');

  await kInput.fill('21');
  await submit(composer, 'too many');
  await expect(inlineError).toHaveText('k must be between 1 and 20');

  await kInput.fill('0');
  await submit(composer, 'too few');
  await expect(inlineError).toHaveText('k must be between 1 and 20');

  // A valid k clears the error and renders the canned hits.
  await kInput.fill('5');
  await submit(composer, 'just right');
  await expect(inlineError).toHaveCount(0);
  await expect(panel.locator('.doc-card')).toHaveCount(3);
  await expect(composer).toHaveValue('just right');
});
