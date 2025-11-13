#!/usr/bin/env node
import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import YAML from 'yaml';

const DEFAULT_TARGET = path.join('features', 'graph', 'graph.yaml');

const isPlainObject = (value) =>
  Object.prototype.toString.call(value) === '[object Object]';

const KEY_PRIORITY = [
  'version',
  'id',
  'name',
  'description',
  'status',
  'group',
  'nodes',
  'edges',
  'artifacts',
  'source',
  'manifest',
  'target',
  'type',
  'metadata'
];

const compareStrings = (a = '', b = '') => a.localeCompare(b);

const compareKeys = (a, b) => {
  const indexA = KEY_PRIORITY.indexOf(a);
  const indexB = KEY_PRIORITY.indexOf(b);

  if (indexA !== -1 || indexB !== -1) {
    if (indexA === -1) return 1;
    if (indexB === -1) return -1;
    if (indexA !== indexB) return indexA - indexB;
  }

  return compareStrings(a, b);
};

const sortArray = (value) => {
  if (!Array.isArray(value)) return value;
  const sorted = value.map(sortValue);

  if (sorted.every((item) => typeof item === 'string')) {
    return [...sorted].sort(compareStrings);
  }

  if (sorted.every((item) => isPlainObject(item))) {
    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, 'id'))) {
      return [...sorted].sort((a, b) => compareStrings(a.id, b.id));
    }

    if (
      sorted.every(
        (item) =>
          Object.prototype.hasOwnProperty.call(item, 'source') &&
          Object.prototype.hasOwnProperty.call(item, 'target')
      )
    ) {
      return [...sorted].sort((a, b) => {
        const sourceComparison = compareStrings(a.source, b.source);
        if (sourceComparison !== 0) return sourceComparison;
        const targetComparison = compareStrings(a.target, b.target);
        if (targetComparison !== 0) return targetComparison;
        return compareStrings(a.type, b.type);
      });
    }

    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, 'source'))) {
      return [...sorted].sort((a, b) => compareStrings(a.source, b.source));
    }

    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, 'manifest'))) {
      return [...sorted].sort((a, b) => compareStrings(a.manifest, b.manifest));
    }
  }

  return sorted;
};

const sortObject = (value) => {
  if (!isPlainObject(value)) return value;

  return Object.entries(value)
    .sort(([a], [b]) => compareKeys(a, b))
    .reduce((acc, [key, entry]) => {
      acc[key] = sortValue(entry);
      return acc;
    }, {});
};

const sortValue = (value) => {
  if (Array.isArray(value)) return sortArray(value);
  if (isPlainObject(value)) return sortObject(value);
  return value;
};

const formatYaml = async (filePath) => {
  const absolutePath = path.resolve(filePath);
  const fileContent = await fs.readFile(absolutePath, 'utf8');
  const parsed = YAML.parse(fileContent, { prettyErrors: true });
  const sorted = sortValue(parsed);
  const formatted = YAML.stringify(sorted, { indent: 2, lineWidth: 0 }).trimEnd() + '\n';

  if (formatted !== fileContent) {
    await fs.writeFile(absolutePath, formatted, 'utf8');
  }
};

const main = async () => {
  const [, , ...args] = process.argv;
  const targets = args.length > 0 ? args : [DEFAULT_TARGET];

  await Promise.all(targets.map((target) => formatYaml(target)));
};

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
