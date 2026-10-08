import { expect, test } from '@playwright/test';

import { MOCK_DOC_LOCATIONS, chatPanel, submitChat } from '../support';

test('renders doc cards collapsed and expands them on click', async ({ page }) => {
  await page.goto('/');
  await submitChat(page, 'where are tokens?');

  const cards = chatPanel(page).locator('.doc-card');
  await expect(cards).toHaveCount(MOCK_DOC_LOCATIONS.length);
  for (const [index, location] of MOCK_DOC_LOCATIONS.entries()) {
    await expect(cards.nth(index).locator('.doc-path')).toHaveText(location);
  }
  await expect(cards.first().locator('.doc-text')).toContainText(
    'Access tokens expire after 60 minutes.',
  );

  const first = cards.first();
  const lineClamp = () =>
    first
      .locator('.doc-text')
      .evaluate((el) => getComputedStyle(el).getPropertyValue('-webkit-line-clamp'));

  // Collapsed by default: clamped to 4 lines, no `expanded` class.
  await expect(first).not.toHaveClass(/expanded/);
  expect(await lineClamp()).toBe('4');

  await first.click();

  // Expanding lifts the clamp.
  await expect(first).toHaveClass(/expanded/);
  expect(await lineClamp()).not.toBe('4');
});
