import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const src = join(root, 'src');
const locales = ['ru', 'en'];

const flatten = (value, prefix = '', out = new Set()) => {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === 'object') flatten(child, path, out);
    else out.add(path);
  }
  return out;
};

const dictionaries = Object.fromEntries(locales.map((locale) => [
  locale,
  flatten(JSON.parse(readFileSync(join(src, 'i18n', 'locales', `${locale}.json`), 'utf8'))),
]));
const pluralSuffix = /_(?:zero|one|two|few|many|other)$/;
const canonical = Object.fromEntries(locales.map((locale) => [
  locale,
  new Set([...dictionaries[locale]].map((key) => key.replace(pluralSuffix, ''))),
]));

const files = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(?:ts|tsx)$/.test(name)) files.push(path);
  }
};
walk(src);

const missing = [];
const literalPattern = /\bt\(\s*(['"])([^'"\n]+)\1/g;
for (const file of files) {
  const source = readFileSync(file, 'utf8');
  for (const match of source.matchAll(literalPattern)) {
    for (const locale of locales) {
      if (!canonical[locale].has(match[2])) missing.push(`${locale}: ${match[2]} (${relative(root, file)})`);
    }
  }
}

const asymmetric = [];
for (const key of canonical.ru) if (!canonical.en.has(key)) asymmetric.push(`en: ${key}`);
for (const key of canonical.en) if (!canonical.ru.has(key)) asymmetric.push(`ru: ${key}`);

if (missing.length || asymmetric.length) {
  if (missing.length) console.error(`Missing literal keys:\n${missing.join('\n')}`);
  if (asymmetric.length) console.error(`Locale mismatch:\n${asymmetric.join('\n')}`);
  process.exit(1);
}
console.log(`i18n OK: ${dictionaries.ru.size} symmetric keys, ${files.length} source files checked.`);
