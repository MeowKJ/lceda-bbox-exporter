import type { BBoxExportRow } from './core';
import * as extensionConfig from '../extension.json';
import { makeBBoxRow, rowsToCsv, rowsToJson } from './core';

export { makeBBoxRow, milToMm, rowsToCsv, rowsToJson } from './core';

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function primitiveMetadata(primitive: IPCB_Primitive): Parameters<typeof makeBBoxRow>[0] {
	const primitiveId = primitive.getState_PrimitiveId();
	const primitiveType = String(primitive.getState_PrimitiveType());
	if (primitive.getState_PrimitiveType() !== EPCB_PrimitiveType.COMPONENT)
		return { primitiveId, primitiveType };

	const component = primitive as IPCB_PrimitiveComponent;
	return {
		primitiveId,
		primitiveType,
		designator: component.getState_Designator(),
		componentName: component.getState_Name() ?? component.getState_Component()?.name,
		footprintName: component.getState_Footprint()?.name,
		rotation: component.getState_Rotation(),
	};
}

async function collectRows(primitives: Array<IPCB_Primitive>): Promise<Array<BBoxExportRow>> {
	const rows: Array<BBoxExportRow> = [];
	for (const primitive of primitives) {
		const bbox = await eda.pcb_Primitive.getPrimitivesBBox([primitive]);
		if (bbox)
			rows.push(makeBBoxRow(primitiveMetadata(primitive), bbox));
	}
	return rows;
}

async function saveRows(rows: Array<BBoxExportRow>, basename: string): Promise<void> {
	if (rows.length === 0)
		throw new Error('没有找到可导出的 BBox。');

	await eda.sys_FileSystem.saveFile(
		new Blob([rowsToCsv(rows)], { type: 'text/csv;charset=utf-8' }),
		`${basename}.csv`,
	);
	await eda.sys_FileSystem.saveFile(
		new Blob([rowsToJson(rows)], { type: 'application/json;charset=utf-8' }),
		`${basename}.json`,
	);
	eda.sys_Message.showToastMessage(`已导出 ${rows.length} 条 BBox 记录。`, ESYS_ToastMessageType.SUCCESS);
}

async function runExport(work: () => Promise<void>): Promise<void> {
	try {
		await work();
	}
	catch (error) {
		eda.sys_Message.showToastMessage(errorMessage(error), ESYS_ToastMessageType.ERROR, 6);
	}
}

export function activate(status?: 'onStartupFinished', arg?: string): void {
	void status;
	void arg;
}

export function deactivate(): void {}

export async function exportSelectedBBox(): Promise<void> {
	await runExport(async () => {
		const selected = await eda.pcb_SelectControl.getAllSelectedPrimitives();
		if (selected.length === 0)
			throw new Error('请先在 PCB 或封装画布中选择至少一个图元。');
		await saveRows(await collectRows(selected), 'selected-bbox');
	});
}

export async function exportAllComponentBBox(): Promise<void> {
	await runExport(async () => {
		const components = await eda.pcb_PrimitiveComponent.getAll();
		await saveRows(await collectRows(components), 'component-bbox');
	});
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		`${extensionConfig.displayName} ${extensionConfig.version}\n\n使用官方 getPrimitivesBBox API 导出笛卡尔坐标系 BBox，并将 mil 转换为 mm。`,
		extensionConfig.displayName,
		'确定',
	);
}
