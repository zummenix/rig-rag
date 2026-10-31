import { expect, test } from '@playwright/test';

import { MOCK_ANSWER, MOCK_DOC_LOCATIONS, chatPanel, submitChat } from '../support';

test('groups doc cards under one collapsible section with per-doc details', async ({
  page,
}) => {
  await page.goto('/');
  await submitChat(page, 'where are tokens?');

  const panel = chatPanel(page);
  const section = panel.locator('details.chat-docs');

  await expect(section).toBeVisible();
  await expect(section).not.toHaveAttribute('open', '');
  await expect(section.locator('.chat-docs-count')).toHaveText(
    String(MOCK_DOC_LOCATIONS.length),
  );

  const cards = section.locator('details.chat-doc');
  await expect(cards).toHaveCount(MOCK_DOC_LOCATIONS.length);
  for (const [index, location] of MOCK_DOC_LOCATIONS.entries()) {
    await expect(cards.nth(index).locator('.doc-path')).toHaveText(location);
  }

  const first = cards.first();
  await expect(first).not.toHaveAttribute('open', '');

  // Open the group, then the first document on its own.
  await section.locator('.chat-docs-summary').click();
  await expect(section).toHaveAttribute('open', '');
  await first.locator('summary').click();
  await expect(first).toHaveAttribute('open', '');
  await expect(first.locator('.doc-text')).toContainText(
    'Access tokens expire after 60 minutes.',
  );
  await expect(cards.nth(1).locator('.doc-text')).toBeHidden();

  // Streaming deltas keep re-rendering the turn; the open state must survive.
  await expect(panel.locator('.bubble.assistant')).toContainText(MOCK_ANSWER);
  await expect(panel.locator('.composer-send')).toBeEnabled();
  await expect(section).toHaveAttribute('open', '');
  await expect(first).toHaveAttribute('open', '');
});
