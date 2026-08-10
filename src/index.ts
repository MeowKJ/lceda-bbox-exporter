import type { BBoxExportRow } from './core';
import * as extensionConfig from '../extension.json';
import { makeBBoxRow, rowsToCsv, rowsToJson } from './core';

export { makeBBoxRow, milToMm, modelNameToZHeightMm, rowsToCsv, rowsToJson } from './core';

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
		model3DName: component.getState_Model3D()?.name,
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

type ExportFormat = 'csv' | 'json';

async function saveRows(rows: Array<BBoxExportRow>, basename: string, format: ExportFormat): Promise<void> {
	if (rows.length === 0)
		throw new Error('没有找到可导出的 BBox 或器件尺寸。');

	const content = format === 'csv' ? rowsToCsv(rows) : rowsToJson(rows);
	const mimeType = format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8';
	await eda.sys_FileSystem.saveFile(new Blob([content], { type: mimeType }), `${basename}.${format}`);
	eda.sys_Message.showToastMessage(
		`已导出 ${rows.length} 条器件尺寸记录（${format.toUpperCase()}）。`,
		ESYS_ToastMessageType.SUCCESS,
	);
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
	// 嘉立创 EDA 3.2.149 在桌面端不会总是自动应用 manifest 的 headerMenus。
	// 显式替换为本扩展声明的菜单，确保 PCB/封装上下文都能看到入口。
	void eda.sys_HeaderMenu.replaceHeaderMenus(extensionConfig.headerMenus);
}

export function deactivate(): void {}

async function exportSelectedBBox(format: ExportFormat): Promise<void> {
	await runExport(async () => {
		const selected = await eda.pcb_SelectControl.getAllSelectedPrimitives();
		if (selected.length === 0)
			throw new Error('请先在 PCB 或封装画布中选择至少一个图元。');
		await saveRows(await collectRows(selected), 'selected-bbox', format);
	});
}

async function exportAllComponentBBox(format: ExportFormat): Promise<void> {
	await runExport(async () => {
		const components = await eda.pcb_PrimitiveComponent.getAll();
		await saveRows(await collectRows(components), 'component-bbox', format);
	});
}

export async function exportSelectedBBoxCsv(): Promise<void> {
	await exportSelectedBBox('csv');
}

export async function exportSelectedBBoxJson(): Promise<void> {
	await exportSelectedBBox('json');
}

export async function exportAllComponentBBoxCsv(): Promise<void> {
	await exportAllComponentBBox('csv');
}

export async function exportAllComponentBBoxJson(): Promise<void> {
	await exportAllComponentBBox('json');
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		`${extensionConfig.displayName} ${extensionConfig.version}\n\n使用官方 getPrimitivesBBox API 导出灰色 BBox 的 X 长、Y 宽。若 3D 模型名包含 H 高度（如 L14-W20-H3.2），将同步导出 Z 高；否则 Z 高留空。`,
		extensionConfig.displayName,
		'确定',
	);
}
