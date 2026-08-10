import path from 'node:path';
import fs from 'fs-extra';
import ignore from 'ignore';
import JSZip from 'jszip';

import * as extensionConfig from '../extension.json';

function lines(value: string): Array<string> {
	return value.split(/[\r\n]+/).filter(Boolean).map(line => line.replace(/[\\/]$/, ''));
}

async function main(): Promise<void> {
	const root = path.join(__dirname, '..');
	const ignored = ignore().add(lines(await fs.readFile(path.join(root, '.edaignore'), 'utf8')));
	const entries = await fs.readdir(root, { recursive: true });
	const files = entries
		.map(entry => String(entry).replaceAll('\\', '/'))
		.filter(entry => !ignored.ignores(entry))
		.filter(entry => fs.statSync(path.join(root, entry)).isFile());

	const archive = new JSZip();
	for (const file of files)
		archive.file(file, fs.createReadStream(path.join(root, file)));

	await fs.ensureDir(path.join(__dirname, 'dist'));
	const bytes = await archive.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 9 } });
	await fs.writeFile(path.join(__dirname, 'dist', `${extensionConfig.name}_v${extensionConfig.version}.eext`), bytes);
}

void main();
