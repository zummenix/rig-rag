import { expect, test } from '@playwright/test';

import { chatComposer, chatPanel } from '../support';

test('Enter submits; Shift+Enter inserts a newline without submitting', async ({
  page,
}) => {
  await page.goto('/');
  const composer = chatComposer(page);

  await composer.fill('keep');
  await composer.press('Shift+Enter');
  await expect(composer).toHaveValue('keep\n');
  await expect(chatPanel(page).locator('.turn')).toHaveCount(0);

  await composer.press('Enter');
  await expect(chatPanel(page).locator('.bubble.user')).toHaveText('keep');
  await expect(composer).toHaveValue('');
});
