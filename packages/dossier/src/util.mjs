import fs from 'node:fs/promises';
import path from 'node:path';

export function resolveFrom(baseDir, p) {
  return path.isAbsolute(p) ? p : path.join(baseDir, p);
}

export async function readJson(file) {
  const raw = await fs.readFile(file, 'utf8');
  return JSON.parse(raw);
}

export async function exists(file) {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

export async function loadState(file) {
  if (!(await exists(file))) return { version: 1, daily: { date: '', spent_rmb: 0 }, topics: {} };
  return readJson(file);
}

export async function saveState(file, state) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
}

export function today() {
  return new Date().toISOString().slice(0, 10);
}

export function stamp() {
  return new Date().toISOString();
}
