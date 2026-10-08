import { expect, test } from '@playwright/test';

import { ERROR_MESSAGE, FAIL_BASE_URL, chatComposer, chatPanel } from '../support';

// This spec talks to the second server, which is started with `--fail-after 1`.
test.use({ baseURL: FAIL_BASE_URL });

test('renders an error bubble when the turn fails', async ({ page }) => {
  await page.goto('/');

  const composer = chatComposer(page);
  await composer.fill('please fail');
  await composer.press('Enter');

  const error = chatPanel(page).locator('.bubble.error');
  await expect(error).toBeVisible();
  await expect(error).toHaveText(ERROR_MESSAGE);

  // The failed turn is done: composer re-enabled, no half-finished answer left.
  await expect(chatPanel(page).locator('.composer-send')).toBeEnabled();
});
