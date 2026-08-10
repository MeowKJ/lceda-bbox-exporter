import process from 'node:process';
import esbuild from 'esbuild';

import common from './esbuild.common';

(async () => {
	const context = await esbuild.context(common);
	await context.rebuild();
	await context.dispose();
	process.exit();
})();
