// Shared constants and selectors for the Playwright suite.
//
// Imported by both `global-setup.ts` (which binds these ports) and the specs
// (which navigate to them). Playwright runs the global setup in the main
// process and the specs in worker processes, so ports travel through this
// module rather than environment variables.
import { type Locator, type Page } from '@playwright/test';

/** Port the happy-path mock server binds (also the default `baseURL`). */
export const HAPPY_PORT = 3555;
/** Port the failing mock server (`--fail-after 1`) binds. */
export const FAIL_PORT = 3556;

export const HAPPY_BASE_URL = `http://127.0.0.1:${HAPPY_PORT}`;
export const FAIL_BASE_URL = `http://127.0.0.1:${FAIL_PORT}`;

/**
 * Per-event mock delay, in ms. The built-in 5ms default is too fast for a
 * browser to render pieces of the answer, so the e2e raises it to observe
 * incremental streaming (see `mock_server --event-delay-ms`).
 */
export const EVENT_DELAY_MS = 150;

/** Canned values from `src/serve/mock.rs`, mirrored here for assertions. */
export const MOCK_ANSWER = 'Hello, mock world!';
export const MOCK_DOC_LOCATIONS = [
  'docs/auth.md:10-18',
  'docs/auth.md:20-26',
  'docs/faq.md:1-6',
];
export const ERROR_MESSAGE = 'mock completer failed after 1 deltas';

// Both pages stay mounted (hidden via `display`), so scope every selector to
// its own panel: the chat panel carries `chat`, the query panel does not.
export const chatPanel = (page: Page): Locator => page.locator('section.panel.chat');
export const queryPanel = (page: Page): Locator =>
  page.locator('section.panel:not(.chat)');
export const chatComposer = (page: Page): Locator =>
  chatPanel(page).locator('.composer-input');
export const queryComposer = (page: Page): Locator =>
  queryPanel(page).locator('.composer-input');

/** Types `text` into a composer and submits it with Enter. */
export async function submit(composer: Locator, text: string): Promise<void> {
  await composer.fill(text);
  await composer.press('Enter');
}

/** Submits a chat turn through the chat composer. */
export async function submitChat(page: Page, text: string): Promise<void> {
  await submit(chatComposer(page), text);
}
