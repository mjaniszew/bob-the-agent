import { readFileSync, writeFileSync } from 'fs';

const template = readFileSync('/app/config/hermes.template.yaml', 'utf8');

const config = JSON.parse(template.replace(/\$\{(\w+)\}/g, (_, key) => {
  const val = process.env[key];
  if (!val) throw new Error(`Missing env var: ${key}`);
  return val;
}));

writeFileSync('/opt/data/config.yaml', JSON.stringify(config, null, 2));
console.log('First run: config.yaml generated');