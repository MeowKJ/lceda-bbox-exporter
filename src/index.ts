import type { BBoxExportRow } from './core';
import * as extensionConfig from '../extension.json';
import { makeBBoxRow, rowsToCsv, SCHEMATIC_BBOX_UNIT_TO_MM } from './core';

export { makeBBoxRow, milToMm, modelNameToZHeightMm, rowsToCsv, SCHEMATIC_BBOX_UNIT_TO_MM } from './core';

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

const COMPONENT_LIST_RIGHT_CLICK_MENU_ID = 'componentList';
const SYMBOL_LIST_RIGHT_CLICK_MENU_ID = 'symbolList';

async function registerRightClickMenus(): Promise<void> {
	// 官方 API 当前只开放底部器件/符号/封装等列表项目的右键菜单，不能扩展画布右键。
	await Promise.all([
		eda.sys_RightClickMenu.changeMenu(COMPONENT_LIST_RIGHT_CLICK_MENU_ID, [
			{
				id: 'lceda-bbox-exporter-component-list-export',
				title: '导出选中 PCB 器件 BBox CSV',
				registerFn: 'exportSelectedBBoxCsv',
			},
		]),
		eda.sys_RightClickMenu.changeMenu(SYMBOL_LIST_RIGHT_CLICK_MENU_ID, [
			{
				id: 'lceda-bbox-exporter-symbol-list-export',
				title: '导出选中原理图图元 BBox CSV',
				registerFn: 'exportSelectedSchematicBBoxCsv',
			},
		]),
	]);
}

function primitiveMetadata(primitive: IPCB_Primitive): Parameters<typeof makeBBoxRow>[0] {
	if (primitive.getState_PrimitiveType() !== EPCB_PrimitiveType.COMPONENT)
		return {};

	const component = primitive as IPCB_PrimitiveComponent;
	return {
		designator: component.getState_Designator(),
		footprintName: component.getState_Footprint()?.name,
		model3DName: component.getState_Model3D()?.name,
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

function schematicPrimitiveMetadata(primitive: ISCH_Primitive): Parameters<typeof makeBBoxRow>[0] {
	if (primitive.getState_PrimitiveType() !== ESCH_PrimitiveType.COMPONENT)
		return {};

	const component = primitive as ISCH_PrimitiveComponent;
	return { designator: component.getState_Designator() };
}

async function collectSchematicRows(primitives: Array<ISCH_Primitive>): Promise<Array<BBoxExportRow>> {
	const rows: Array<BBoxExportRow> = [];
	for (const primitive of primitives) {
		const bbox = await eda.sch_Primitive.getPrimitivesBBox([primitive]);
		if (bbox)
			rows.push(makeBBoxRow(schematicPrimitiveMetadata(primitive), bbox, SCHEMATIC_BBOX_UNIT_TO_MM));
	}
	return rows;
}

async function getCurrentFootprintPrimitives(): Promise<Array<IPCB_Primitive>> {
	const primitiveGroups = await Promise.all([
		eda.pcb_PrimitiveArc.getAll(),
		eda.pcb_PrimitiveAttribute.getAll(),
		eda.pcb_PrimitiveDimension.getAll(),
		eda.pcb_PrimitiveFill.getAll(),
		eda.pcb_PrimitiveImage.getAll(),
		eda.pcb_PrimitiveLine.getAll(),
		eda.pcb_PrimitiveObject.getAll(),
		eda.pcb_PrimitivePad.getAll(),
		eda.pcb_PrimitivePolyline.getAll(),
		eda.pcb_PrimitivePour.getAll(),
		eda.pcb_PrimitiveRegion.getAll(),
		eda.pcb_PrimitiveString.getAll(),
		eda.pcb_PrimitiveVia.getAll(),
	]);
	return primitiveGroups.flat() as Array<IPCB_Primitive>;
}

async function saveRows(rows: Array<BBoxExportRow>, basename: string): Promise<void> {
	if (rows.length === 0)
		throw new Error('没有找到可导出的 BBox 或器件尺寸。');

	await eda.sys_FileSystem.saveFile(
		new Blob([rowsToCsv(rows)], { type: 'text/csv;charset=utf-8' }),
		`${basename}.csv`,
	);
	eda.sys_Message.showToastMessage(
		`已导出 ${rows.length} 条器件尺寸记录（CSV）。`,
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
	// 右键菜单 API 属于 beta，失败时保留顶部菜单入口，不影响导出功能。
	void registerRightClickMenus().catch(error => console.warn(errorMessage(error)));
}

export function deactivate(): void {}

export async function exportSelectedBBoxCsv(): Promise<void> {
	await runExport(async () => {
		const selected = await eda.pcb_SelectControl.getAllSelectedPrimitives();
		if (selected.length === 0)
			throw new Error('请先在 PCB 或封装画布中选择至少一个图元。');
		await saveRows(await collectRows(selected), 'selected-bbox');
	});
}

export async function exportAllComponentBBoxCsv(): Promise<void> {
	await runExport(async () => {
		const components = await eda.pcb_PrimitiveComponent.getAll();
		await saveRows(await collectRows(components), 'component-bbox');
	});
}

/** 导出原理图选中图元的官方 BBox；原理图官方单位为 0.01 inch。 */
export async function exportSelectedSchematicBBoxCsv(): Promise<void> {
	await runExport(async () => {
		const selected = await eda.sch_SelectControl.getAllSelectedPrimitives();
		if (selected.length === 0)
			throw new Error('请先在原理图画布中选择至少一个图元。');
		await saveRows(await collectSchematicRows(selected), 'schematic-selected-bbox');
	});
}

/**
 * 在封装编辑器中一次性把该封装的官方图元集合传入 getPrimitivesBBox。
 * 只读取官方 BBox 结果，不自行遍历图元计算四条边。
 */
export async function exportCurrentFootprintLibraryBBoxCsv(): Promise<void> {
	await runExport(async () => {
		const documentInfo = await eda.dmt_SelectControl.getCurrentDocumentInfo();
		if (!documentInfo || documentInfo.documentType !== EDMT_EditorDocumentType.FOOTPRINT)
			throw new Error('请先在封装编辑器中打开目标封装库，再导出该封装的官方 BBox。');

		const primitives = await getCurrentFootprintPrimitives();
		const bbox = await eda.pcb_Primitive.getPrimitivesBBox(primitives);
		if (!bbox)
			throw new Error('当前封装没有可读取的官方 BBox。');

		const libraryUuid = documentInfo.parentLibraryUuid
			?? await eda.lib_LibrariesList.getProjectLibraryUuid();
		const footprint = libraryUuid
			? await eda.lib_Footprint.get(documentInfo.uuid, libraryUuid)
			: undefined;
		await saveRows([
			makeBBoxRow({ footprintName: footprint?.name ?? documentInfo.uuid }, bbox),
		], 'footprint-bbox');
	});
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		`${extensionConfig.displayName} ${extensionConfig.version}\n\n导出 Designator、Footprint、X 长、Y 宽和 Z 高五列 CSV。PCB BBox 使用 mil，原理图 BBox 使用 0.01 inch，均转换为 mm。若 3D 模型名包含 H 高度（如 L14-W20-H3.2），将导出 Z 高；否则留空。\n\n右键入口位于底部器件/符号列表；嘉立创 EDA 当前不开放 PCB 或原理图画布右键菜单扩展。`,
		extensionConfig.displayName,
		'确定',
	);
}
