import fs from 'node:fs/promises';
import path from 'node:path';
import JSZip from 'jszip';

const root = process.cwd();
const manifest = JSON.parse(await fs.readFile(path.join(root, 'extension.json'), 'utf8'));
const archivePath = path.join(root, 'build', 'dist', `${manifest.name}_v${manifest.version}.eext`);
const archive = await JSZip.loadAsync(await fs.readFile(archivePath));
const required = ['extension.json', 'dist/index.js'];
const missing = required.filter(file => !archive.file(file));
const forbiddenPrefixes = ['.git/', '.npm-cache/', 'node_modules/', 'src/', 'tests/'];
const forbidden = Object.keys(archive.files).filter(file => forbiddenPrefixes.some(prefix => file.startsWith(prefix)));

if (missing.length)
	throw new Error(`Package missing: ${missing.join(', ')}`);
if (forbidden.length || archive.file('package.json'))
	throw new Error(`Package contains development files: ${forbidden.slice(0, 5).join(', ')}`);

console.warn(`Verified ${path.relative(root, archivePath)} (${Object.keys(archive.files).length} entries).`);
