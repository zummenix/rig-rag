// Playwright global setup: build the mock-backed server + the WASM UI, then
// launch two servers — a happy one and one that fails after a single delta so
// the error-state spec has something to hit.
//
// The servers stay up for the whole run; the returned function tears them down.
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

import {
  EVENT_DELAY_MS,
  FAIL_PORT,
  HAPPY_PORT,
} from './support';

const here = __dirname;
const repoRoot = path.resolve(here, '..', '..');
const siteDir = path.resolve(here, '..');
const distDir = path.join(siteDir, 'dist');
const serverBin = path.join(repoRoot, 'target', 'debug', 'mock_server');

export default async function globalSetup(): Promise<() => Promise<void>> {
  if (process.env.RIG_RAG_SKIP_BUILD !== '1') {
    console.log('[playwright] cargo build --features mock --bin mock_server');
    execFileSync('cargo', ['build', '--features', 'mock', '--bin', 'mock_server'], {
      cwd: repoRoot,
      stdio: 'inherit',
    });

    console.log('[playwright] trunk build (site/dist)');
    execFileSync('trunk', ['build'], { cwd: siteDir, stdio: 'inherit' });
  }

  if (!existsSync(serverBin)) {
    throw new Error(
      `mock_server not found at ${serverBin}. ` +
        'Drop RIG_RAG_SKIP_BUILD=1 or run `cargo build --features mock --bin mock_server`.',
    );
  }
  if (!existsSync(path.join(distDir, 'index.html'))) {
    throw new Error(`site/dist/index.html missing. Run \`trunk build\` in ${siteDir}.`);
  }

  const happy = startServer(HAPPY_PORT, []);
  const fail = startServer(FAIL_PORT, ['--fail-after', '1']);

  try {
    await waitForHealth(HAPPY_PORT);
    await waitForHealth(FAIL_PORT);
  } catch (error) {
    happy.kill();
    fail.kill();
    throw error;
  }

  return async () => {
    for (const child of [happy, fail]) {
      child.kill('SIGTERM');
    }
  };
}

function startServer(port: number, extraArgs: string[]): ChildProcess {
  const args = [
    '--bind',
    `127.0.0.1:${port}`,
    '--site-dir',
    distDir,
    '--event-delay-ms',
    String(EVENT_DELAY_MS),
    ...extraArgs,
  ];
  const child = spawn(serverBin, args, {
    cwd: repoRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const forward = (chunk: Buffer) =>
    process.stdout.write(`[mock_server:${port}] ${chunk}`);
  child.stdout?.on('data', forward);
  child.stderr?.on('data', forward);
  child.on('exit', (code, signal) => {
    if (code && code !== 0) {
      console.error(`[mock_server:${port}] exited (code=${code} signal=${signal})`);
    }
  });

  return child;
}

async function waitForHealth(port: number): Promise<void> {
  const url = `http://127.0.0.1:${port}/api/health`;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // Server not accepting connections yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`mock_server on port ${port} did not become healthy`);
}
