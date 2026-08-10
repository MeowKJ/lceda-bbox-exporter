import antfu from '@antfu/eslint-config';

export default antfu({
	formatters: false,
	stylistic: {
		indent: 'tab',
		quotes: 'single',
		semi: true,
	},
	typescript: true,
	ignores: ['build/dist/', 'dist/', 'node_modules/', '.npm-cache/', '.eslintcache', 'debug.log'],
	rules: {
		'antfu/no-top-level-await': 'off',
		'node/prefer-global/process': 'off',
		'test/no-import-node-test': 'off',
	},
});
