import { expect, test } from '@playwright/test';

import { chatPanel, submitChat } from '../support';

test('previews the latest thinking line while collapsed and hides it when open', async ({
  page,
}) => {
  await page.goto('/');
  await submitChat(page, 'why are tokens expiring?');

  const thinking = chatPanel(page).locator('details.thinking');
  await expect(thinking).toBeVisible();
  await expect(thinking).not.toHaveAttribute('open', '');

  // The collapsed summary tracks the newest non-empty line of the stream.
  const preview = thinking.locator('.thinking-last-line');
  await expect(preview).toBeVisible();
  await expect(preview).toHaveText('Let me think.');
  await expect(thinking.locator('pre')).toContainText('Let me think.');

  await thinking.locator('.thinking-summary').click();
  await expect(thinking).toHaveAttribute('open', '');
  await expect(preview).toBeHidden();
});
