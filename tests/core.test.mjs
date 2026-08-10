import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const bundle = fs.readFileSync(new URL('../dist/index.js', import.meta.url), 'utf8');
const context = vm.createContext({ Blob, console });
vm.runInContext(bundle, context);
const api = context.edaEsbuildExportName;

test('converts mil bbox to millimetres and derives size', () => {
	const row = api.makeBBoxRow({ primitiveId: 'U1', primitiveType: 'COMPONENT' }, {
		minX: -100,
		minY: -50,
		maxX: 100,
		maxY: 50,
	});
	assert.deepEqual(JSON.parse(JSON.stringify(row)), {
		primitiveId: 'U1',
		primitiveType: 'COMPONENT',
		designator: '',
		componentName: '',
		footprintName: '',
		model3DName: '',
		rotation: '',
		minX: -2.54,
		minY: -1.27,
		maxX: 2.54,
		maxY: 1.27,
		width: 5.08,
		height: 2.54,
		xLength: 5.08,
		yWidth: 2.54,
		zHeight: '',
		zHeightSource: 'unavailable',
		unit: 'mm',
	});
});

test('CSV includes BOM and escapes names', () => {
	const row = api.makeBBoxRow({ primitiveId: '1', primitiveType: 'X', componentName: 'A,"B"' }, {
		minX: 0,
		minY: 0,
		maxX: 1000,
		maxY: 1000,
	});
	const csv = api.rowsToCsv([row]);
	assert.equal(csv.charCodeAt(0), 0xFEFF);
	assert.match(csv, /"A,""B"""/);
	assert.match(csv, /25\.4/);
	assert.match(csv, /X-Length of Bottom Edge on Board \(Spacing Line\)/);
	assert.match(csv, /Y-Width,Z-Height/);
});

test('derives Z height only from an explicit 3D model H parameter', () => {
	assert.equal(api.modelNameToZHeightMm('PQFP-128_L14.0-W20.0-H3.20'), 3.2);
	assert.equal(api.modelNameToZHeightMm('C0201_L0.6-W0.3-H0.3'), 0.3);
	assert.equal(api.modelNameToZHeightMm('unrelated-model'), '');
});

test('JSON records coordinate system and unit', () => {
	const parsed = JSON.parse(api.rowsToJson([]));
	assert.equal(parsed.schemaVersion, 1);
	assert.equal(parsed.coordinateSystem, 'cartesian');
	assert.equal(parsed.unit, 'mm');
});

test('exposes one save-dialog command for each export format', () => {
	assert.equal(typeof api.exportSelectedBBoxCsv, 'function');
	assert.equal(typeof api.exportSelectedBBoxJson, 'function');
	assert.equal(typeof api.exportAllComponentBBoxCsv, 'function');
	assert.equal(typeof api.exportAllComponentBBoxJson, 'function');
});
