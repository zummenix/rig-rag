import { expect, test } from '@playwright/test';

import {
  MOCK_DOC_LOCATIONS,
  queryComposer,
  queryPanel,
  submit,
} from '../support';

test('renders search results as prominent, always-open cards', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Query' }).click();

  const panel = queryPanel(page);
  await submit(queryComposer(page), 'where are tokens?');

  const results = panel.locator('.search-result');
  await expect(results).toHaveCount(MOCK_DOC_LOCATIONS.length);

  for (const [index, location] of MOCK_DOC_LOCATIONS.entries()) {
    const result = results.nth(index);
    await expect(result.locator('.search-path')).toHaveText(location);
    await expect(result.locator('.search-rank')).toHaveText(`#${index + 1}`);
    await expect(result.locator('.search-text')).toBeVisible();
  }

  // Search results are never wrapped in a collapsible element.
  await expect(panel.locator('details')).toHaveCount(0);
  await expect(results.first().locator('.search-text')).toContainText(
    'Access tokens expire after 60 minutes.',
  );
});
