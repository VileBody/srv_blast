import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * Страж единой шкалы UI (правила: UI_RULES.md).
 *
 * Считает по каждому файлу нарушения пяти правил и сравнивает со списком долга
 * scripts/ui-debt.json. Падает, только если в каком-то файле нарушений стало БОЛЬШЕ,
 * чем записано в долге: старое не блокирует, новое не проходит. Когда долг уменьшился,
 * подсказывает обновить список (`npm run ui:check -- --update`), чтобы планка не отползла назад.
 *
 * Строку можно исключить осознанно: комментарий `ui-allow` на ней или на строке выше
 * (иллюстрации гайдов, превью эффектов, пиксельная геометрия плеера).
 *
 *   npm run ui:check                  проверка (CI)
 *   npm run ui:check -- --update      переписать список долга по текущему коду
 *   npm run ui:check -- --report      сводка по правилам и самым «должным» файлам
 */

const root = fileURLToPath(new URL('../', import.meta.url));
const src = join(root, 'src');
const debtPath = join(root, 'scripts', 'ui-debt.json');
const args = new Set(process.argv.slice(2));

const TYPE_SCALE = new Set([12, 14, 16, 20, 24, 32]);
const RADIUS_SCALE = new Set([6, 10, 15, 25, 999]);
const HEIGHT_SCALE = new Set([24, 32, 44, 52, 60]);

const HEX = /(?<![\w&#-])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/g;
const RGBA = /\brgba?\(/g;

/* Правила: имя → функция(строка, тип файла) → число нарушений в строке */
const RULES = {
  // Кегль только из шкалы text-ui-*; произвольный text-[Npx] и font-size вне шкалы — долг
  'font-size': (line, kind) => kind === 'css'
    ? [...line.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].filter((m) => !TYPE_SCALE.has(Number(m[1]))).length
    : (line.match(/\btext-\[\d+(?:\.\d+)?px\]/g) ?? []).length,
  // Радиусы: r6, r10, r15, r25, full. Произвольные и прочие токены — долг
  radius: (line, kind) => kind === 'css'
    ? [...line.matchAll(/border-radius:\s*(\d+(?:\.\d+)?)px/g)].filter((m) => !RADIUS_SCALE.has(Number(m[1]))).length
    : (line.match(/\brounded(?:-[trblse]{1,2})?-(?:\[[^\]]+\]|r(?:9|12|20|40)\b|(?:sm|md|lg|xl|2xl|3xl)\b)/g) ?? []).length,
  // Высоты контролов 24/32/44/52/60; произвольные h-[Npx] в диапазоне контролов — долг
  height: (line, kind) => kind === 'css'
    ? 0
    : [...line.matchAll(/(?<![\w-])h-\[(\d+)px\]/g)].filter((m) => Number(m[1]) >= 24 && Number(m[1]) <= 68 && !HEIGHT_SCALE.has(Number(m[1]))).length,
  // Цвета только из токенов. В CSS определения токенов (--name: …) не считаются
  color: (line, kind) => {
    if (kind === 'css' && /^\s*--[\w-]+\s*:/.test(line)) return 0;
    return (line.match(HEX) ?? []).length + (line.match(RGBA) ?? []).length;
  },
  // Ручные подгонки текста на 1–3 px: после исправления метрик шрифта не нужны
  nudge: (line, kind) => kind === 'css'
    ? 0
    : (line.match(/-?translate-y-(?:px|\[-?[123](?:\.5)?px\])|\b-?(?:mt|mb|pt|pb)-(?:px|\[-?[123]px\])/g) ?? []).length
};

const files = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(?:tsx?|css)$/.test(name)) files.push(path);
  }
};
walk(src);

const isComment = (line) => /^\s*(?:\/\/|\/?\*)/.test(line);

/* current[rule][file] = [{line, text}] */
const current = Object.fromEntries(Object.keys(RULES).map((rule) => [rule, {}]));
for (const path of files) {
  const rel = relative(root, path).split('\\').join('/');
  const kind = path.endsWith('.css') ? 'css' : 'ts';
  const text = readFileSync(path, 'utf8');
  // Файл-эталон, перенесённый из утверждённого макета как есть (wizard12.css): его кегли и
  // отступы и есть пропорции макета, поэтому он целиком вне шкалы — метка ui-allow-file в шапке.
  if (text.includes('ui-allow-file')) continue;
  const lines = text.split('\n');
  lines.forEach((line, index) => {
    if (isComment(line) || line.includes('ui-allow') || (index > 0 && lines[index - 1].includes('ui-allow'))) return;
    for (const [rule, count] of Object.entries(RULES)) {
      const n = count(line, kind);
      if (!n) continue;
      const bucket = (current[rule][rel] ??= []);
      for (let i = 0; i < n; i += 1) bucket.push({ line: index + 1, text: line.trim().slice(0, 140) });
    }
  });
}

const counts = Object.fromEntries(Object.entries(current).map(([rule, byFile]) => [
  rule,
  Object.fromEntries(Object.entries(byFile).map(([file, hits]) => [file, hits.length]).sort(([a], [b]) => a.localeCompare(b)))
]));
const total = (rule) => Object.values(counts[rule]).reduce((a, b) => a + b, 0);

if (args.has('--update')) {
  writeFileSync(debtPath, `${JSON.stringify(counts, null, 2)}\n`);
  console.log(`ui-debt.json обновлён: ${Object.keys(RULES).map((rule) => `${rule} ${total(rule)}`).join(', ')}.`);
  process.exit(0);
}

if (args.has('--report')) {
  for (const rule of Object.keys(RULES)) {
    const top = Object.entries(counts[rule]).sort((a, b) => b[1] - a[1]).slice(0, 8);
    console.log(`\n${rule}: ${total(rule)}`);
    for (const [file, n] of top) console.log(`  ${String(n).padStart(4)}  ${file}`);
  }
  process.exit(0);
}

const debt = existsSync(debtPath) ? JSON.parse(readFileSync(debtPath, 'utf8')) : {};
const grown = [];
let shrunk = 0;
for (const rule of Object.keys(RULES)) {
  const files = new Set([...Object.keys(counts[rule]), ...Object.keys(debt[rule] ?? {})]);
  for (const file of files) {
    const now = counts[rule][file] ?? 0;
    const was = debt[rule]?.[file] ?? 0;
    if (now > was) grown.push({ rule, file, now, was, hits: current[rule][file] ?? [] });
    else if (now < was) shrunk += was - now;
  }
}

if (grown.length) {
  console.error('Новые нарушения единой шкалы UI (правила: UI_RULES.md):');
  for (const { rule, file, now, was, hits } of grown) {
    console.error(`\n  [${rule}] ${file}: было ${was}, стало ${now}. Новые среди строк ниже:`);
    for (const hit of hits) console.error(`    ${file}:${hit.line}  ${hit.text}`);
  }
  console.error('\nИспользуй токены шкалы (text-ui-*, rounded-r6/r10/r15/r25, h-ctl*, цвета из theme).');
  console.error('Осознанное исключение: комментарий ui-allow на строке или строкой выше.');
  process.exit(1);
}

console.log(`ui OK: ${Object.keys(RULES).map((rule) => `${rule} ${total(rule)}`).join(', ')}.`);
if (shrunk) console.log(`Долг уменьшился на ${shrunk}. Зафиксируй: npm run ui:check -- --update`);
