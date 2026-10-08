import { expect, test } from '@playwright/test';

import { MOCK_ANSWER, chatPanel, submitChat } from '../support';

test('streams the answer incrementally, then settles to the final text', async ({
  page,
}) => {
  await page.goto('/');

  const answer = chatPanel(page).locator('.bubble.assistant');
  const send = chatPanel(page).locator('.composer-send');

  await submitChat(page, 'stream this');

  // Poll the answer bubble and record each distinct text it passes through.
  // `innerText` excludes the typing cursor once it is hidden, so the settled
  // value equals the mock answer exactly.
  const seen: string[] = [];
  await expect
    .poll(
      async () => {
        const text = (await answer.innerText()).trim();
        if (text && text !== '…' && !seen.includes(text)) {
          seen.push(text);
        }
        return text;
      },
      { intervals: [50], timeout: 15_000 },
    )
    .toBe(MOCK_ANSWER);

  // The answer arrived in more than one piece rather than all at once.
  expect(seen.length).toBeGreaterThan(1);
  expect(seen.at(-1)).toBe(MOCK_ANSWER);

  // The turn settled: cursor gone, composer enabled again.
  await expect(answer.locator('.cursor')).toBeHidden();
  await expect(send).toBeEnabled();
});
