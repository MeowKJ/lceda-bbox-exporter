import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const bundle = fs.readFileSync(new URL('../dist/index.js', import.meta.url), 'utf8');
const context = vm.createContext({ Blob, console });
vm.runInContext(bundle, context);
const api = context.edaEsbuildExportName;

test('converts mil bbox to the compact BOM dimension row', () => {
	const row = api.makeBBoxRow({ designator: 'U1', footprintName: 'QFP-128' }, {
		minX: -100,
		minY: -50,
		maxX: 100,
		maxY: 50,
	});
	assert.deepEqual(JSON.parse(JSON.stringify(row)), {
		designator: 'U1',
		footprintName: 'QFP-128',
		xLength: 5.08,
		yWidth: 2.54,
		zHeight: '',
	});
});

test('converts official schematic 0.01 inch bbox units to mm', () => {
	const row = api.makeBBoxRow({ designator: 'U1' }, {
		minX: 0,
		minY: 0,
		maxX: 10,
		maxY: 20,
	}, api.SCHEMATIC_BBOX_UNIT_TO_MM);
	assert.deepEqual(JSON.parse(JSON.stringify(row)), {
		designator: 'U1',
		footprintName: '',
		xLength: 2.54,
		yWidth: 5.08,
		zHeight: '',
	});
});

test('CSV matches the compact Designator/Footprint/X/Y/Z format', () => {
	const row = api.makeBBoxRow({ designator: 'U1', footprintName: 'A,"B"' }, {
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
	assert.match(csv, /Designator,Footprint,X-Length of Bottom Edge on Board \(Spacing Line\),Y-Width,Z-Height/);
	assert.doesNotMatch(csv, /Primitive ID|BBox Min|3D Model|Rotation|JSON/);
});

test('derives Z height only from an explicit 3D model H parameter', () => {
	assert.equal(api.modelNameToZHeightMm('PQFP-128_L14.0-W20.0-H3.20'), 3.2);
	assert.equal(api.modelNameToZHeightMm('C0201_L0.6-W0.3-H0.3'), 0.3);
	assert.equal(api.modelNameToZHeightMm('unrelated-model'), '');
});

test('exposes CSV-only export commands', () => {
	assert.equal(typeof api.exportSelectedBBoxCsv, 'function');
	assert.equal(typeof api.exportSelectedSchematicBBoxCsv, 'function');
	assert.equal(typeof api.exportAllComponentBBoxCsv, 'function');
	assert.equal(typeof api.exportCurrentFootprintLibraryBBoxCsv, 'function');
	assert.equal(api.rowsToJson, undefined);
});

test('injects the schematic canvas context menu only for selected components', () => {
	const unchanged = { part: [{ text: '编辑属性' }] };
	assert.deepEqual(
		JSON.parse(JSON.stringify(api.appendSchematicContextMenu(unchanged, { cmdKey: 'wire', selectedIds: ['wire-1'] }))),
		unchanged,
	);

	const injected = api.appendSchematicContextMenu(
		{ part: [{ text: '编辑属性' }, 'menu-sep', { text: '删除' }] },
		{ cmdKey: 'part', selectedIds: ['u1'], target: 'part' },
	);
	const normalized = JSON.parse(JSON.stringify(injected));
	assert.equal(normalized.part.filter(item => item?.text === 'BBox 尺寸导出器').length, 1);
	assert.equal(normalized.part[2].submenu[0].text, '导出所选原理图图元 BBox CSV');
	assert.match(normalized.part[2].submenu[0].cmd, /exportSelectedSchematicBBoxCsv/);
	assert.deepEqual(
		JSON.parse(JSON.stringify(api.appendSchematicContextMenu(injected, { cmdKey: 'part', selectedIds: ['u1'] }))),
		normalized,
	);
});

test('hooks and restores the schematic message bus safely', () => {
	const replies = [];
	const bus = {
		publish(topic, message) {
			return { topic, message };
		},
		rpcReply(result, topic) {
			replies.push({ result, topic });
			return result;
		},
	};
	const originalPublish = bus.publish;
	const originalRpcReply = bus.rpcReply;
	const runtimeContext = vm.createContext({
		Blob,
		console,
		SCH: { gVars: { messageBus: bus } },
		setInterval: () => 1,
		clearInterval: () => {},
	});
	vm.runInContext(bundle, runtimeContext);
	const hookedApi = runtimeContext.edaEsbuildExportName;
	assert.equal(hookedApi.installSchematicContextMenuHook(), true);
	bus.publish('showEditorContextMenu', { cmdKey: 'part', selectedIds: ['u1'] });
	bus.rpcReply({ part: [{ text: '编辑属性' }] }, 'getContextMenu.menuData');
	assert.equal(replies[0].result.part[0].text, 'BBox 尺寸导出器');
	hookedApi.deactivate();
	assert.equal(bus.publish, originalPublish);
	assert.equal(bus.rpcReply, originalRpcReply);
});

test('injects and restores the PCB canvas context-menu hook', () => {
	const contextMenu = api.appendPcbContextMenu({ filter: [{ cmd: 'DELETE', name: '删除' }] });
	assert.equal(contextMenu.filter[0].name, '导出选中 PCB 器件 BBox CSV');
	assert.match(contextMenu.filter[0].cmd, /exportSelectedBBoxCsv/);
	assert.deepEqual(
		JSON.parse(JSON.stringify(api.appendPcbContextMenu(contextMenu))),
		JSON.parse(JSON.stringify(contextMenu)),
	);

	const published = [];
	const bus = {
		publish(topic, message) {
			published.push({ topic, message });
		},
		rpcReply() {},
	};
	const originalPublish = bus.publish;
	const runtimeContext = vm.createContext({
		Blob,
		console,
		PCB: { gVars: { messageBus: bus } },
		setInterval: () => 1,
		clearInterval: () => {},
	});
	vm.runInContext(bundle, runtimeContext);
	const hookedApi = runtimeContext.edaEsbuildExportName;
	assert.equal(hookedApi.installPcbContextMenuHook(), true);
	bus.publish('rightClickPcbMenu', { filter: [{ cmd: 'DELETE', name: '删除' }] });
	assert.equal(published[0].message.filter[0].name, '导出选中 PCB 器件 BBox CSV');
	hookedApi.deactivate();
	assert.equal(bus.publish, originalPublish);
});
