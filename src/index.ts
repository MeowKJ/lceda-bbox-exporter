import type { BBoxExportRow } from './core';
import JSZip from 'jszip';
import * as extensionConfig from '../extension.json';
import { makeBBoxRow, rowsToCsv, SCHEMATIC_BBOX_UNIT_TO_MM, truthRowsToCsv } from './core';

export { makeBBoxRow, milToMm, modelNameToZHeightMm, rowsToCsv, SCHEMATIC_BBOX_UNIT_TO_MM, truthRowsToCsv } from './core';

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

const COMPONENT_LIST_RIGHT_CLICK_MENU_ID = 'componentList';
const SYMBOL_LIST_RIGHT_CLICK_MENU_ID = 'symbolList';
const SCHEMATIC_CONTEXT_MENU_HOOK_KEY = '__lcedaBBoxExporterSchematicContextMenuHook';
const SCHEMATIC_CONTEXT_MENU_TIMER_KEY = '__lcedaBBoxExporterSchematicContextMenuTimer';
const SCHEMATIC_CONTEXT_MENU_COMMAND = `runRegisteredExtensionFn(${extensionConfig.uuid}.exportSelectedSchematicBBoxCsv)`;
const SCHEMATIC_CONTEXT_MENU_TITLE = extensionConfig.displayName;
const SCHEMATIC_CONTEXT_MENU_EXPORT_TITLE = '导出所选原理图图元 BBox CSV';
const SCHEMATIC_CONTEXT_MENU_RETRY_MS = 1000;
const PCB_CONTEXT_MENU_HOOK_KEY = '__lcedaBBoxExporterPcbContextMenuHook';
const PCB_CONTEXT_MENU_TIMER_KEY = '__lcedaBBoxExporterPcbContextMenuTimer';
const PCB_CONTEXT_MENU_COMMAND = `runRegisteredExtensionFn(${extensionConfig.uuid}.exportSelectedBBoxCsv)`;
const PCB_CONTEXT_MENU_EXPORT_TITLE = '导出选中 PCB 器件 BBox CSV';
const PCB_CONTEXT_MENU_RETRY_MS = 1000;
const MOUNTED_TRUTH_FOOTPRINT_KEY = '__lcedaBBoxExporterMountedTruthFootprint';

interface MountedTruthFootprint {
	libraryUuid: string;
	name: string;
	uuid: string;
}

interface SchematicContextMenuState {
	cmdKey?: string;
	selectedIds?: Array<string>;
	target?: string | Array<string>;
}

interface RawContextMenuItem {
	cmd?: string;
	icon?: string;
	submenu?: Array<RawContextMenuItem | string | null>;
	text?: string;
}

interface RawContextMenuData {
	part?: Array<RawContextMenuItem | string | null>;
	[key: string]: unknown;
}

type InternalPublish = (topic: string, message: unknown, ...args: Array<unknown>) => unknown;
type InternalRpcReply = (result: unknown, replyTopic: string, ...args: Array<unknown>) => unknown;

interface InternalMessageBus {
	publish: InternalPublish;
	rpcReply: InternalRpcReply;
	[key: string]: unknown;
}

interface SchematicContextMenuHookState {
	originalPublish: InternalPublish;
	originalRpcReply: InternalRpcReply;
	version: string;
}

interface SchematicContextMenuTimerState {
	timer: ReturnType<typeof setInterval>;
	version: string;
}

interface SchematicRuntime {
	SCH?: {
		gVars?: {
			messageBus?: InternalMessageBus;
		};
	};
	[SCHEMATIC_CONTEXT_MENU_HOOK_KEY]?: SchematicContextMenuHookState;
	[SCHEMATIC_CONTEXT_MENU_TIMER_KEY]?: SchematicContextMenuTimerState;
}

interface PcbContextMenuItem {
	cmd?: string;
	name?: string;
	option?: Record<string, unknown>;
	tips?: string;
	[key: string]: unknown;
}

interface PcbContextMenuState {
	filter?: Array<PcbContextMenuItem>;
	[key: string]: unknown;
}

interface PcbContextMenuHookState {
	hooks: Array<{
		bus: InternalMessageBus;
		originalPublish: InternalPublish;
	}>;
	version: string;
}

interface PcbContextMenuTimerState {
	timer: ReturnType<typeof setInterval>;
	version: string;
}

interface PcbRuntime {
	/** 嘉立创 PCB 3.2.149 暴露的内部桥接总线。 */
	MSG_BUS_PCB?: InternalMessageBus;
	[PCB_CONTEXT_MENU_HOOK_KEY]?: PcbContextMenuHookState;
	[PCB_CONTEXT_MENU_TIMER_KEY]?: PcbContextMenuTimerState;
}

function getPcbMessageBuses(): Array<InternalMessageBus> {
	const runtimes: Array<PcbRuntime> = [globalThis as unknown as PcbRuntime];
	const visitedWindows = new Set<Window>();
	const visitWindow = (candidate: Window): void => {
		if (visitedWindows.has(candidate))
			return;
		visitedWindows.add(candidate);
		runtimes.push(candidate as unknown as PcbRuntime);
		for (let index = 0; index < candidate.frames.length; index++) {
			try {
				visitWindow(candidate.frames[index]);
			}
			catch {
				// 忽略不可访问的跨域 frame；EDA PCB 画布与扩展为同源。
			}
		}
	};
	if (typeof window !== 'undefined') {
		try {
			// 扩展自身位于独立 iframe，PCB 文档可位于顶层窗口的任意嵌套 frame。
			visitWindow(window.top ?? window);
		}
		catch {
			visitWindow(window);
		}
	}
	return [...new Set(runtimes
		.map(runtime => runtime.MSG_BUS_PCB)
		.filter((bus): bus is InternalMessageBus => bus !== undefined))];
}

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

function isSchematicComponentContext(context: SchematicContextMenuState | undefined): boolean {
	const target = Array.isArray(context?.target) ? context.target[0] : context?.target;
	return (context?.cmdKey === 'part' || target === 'part')
		&& (context?.selectedIds?.length ?? 0) > 0;
}

/**
 * 将导出入口插到原理图画布中器件的右键菜单。
 *
 * 嘉立创公开 API 尚未开放原理图画布右键注册；这里采用与去耦喵相同的
 * messageBus 菜单数据 Hook。菜单本身仍只调用本扩展的公开导出函数，读取
 * BBox 仍完全使用 eda.sch_Primitive.getPrimitivesBBox()。
 */
export function appendSchematicContextMenu(
	menuData: RawContextMenuData | undefined,
	context: SchematicContextMenuState | undefined,
): RawContextMenuData | undefined {
	if (!menuData || !isSchematicComponentContext(context) || !Array.isArray(menuData.part))
		return menuData;
	if (menuData.part.some(item => typeof item === 'object' && item?.text === SCHEMATIC_CONTEXT_MENU_TITLE))
		return menuData;

	const part = [...menuData.part];
	const extensionItem: RawContextMenuItem = {
		icon: 'eda-component',
		submenu: [{
			cmd: SCHEMATIC_CONTEXT_MENU_COMMAND,
			icon: 'eda-component',
			text: SCHEMATIC_CONTEXT_MENU_EXPORT_TITLE,
		}],
		text: SCHEMATIC_CONTEXT_MENU_TITLE,
	};
	const firstSeparator = part.indexOf('menu-sep');
	if (firstSeparator >= 0)
		part.splice(firstSeparator + 1, 0, extensionItem, 'menu-sep');
	else
		part.unshift(extensionItem, 'menu-sep');
	return { ...menuData, part };
}

/** 安装原理图画布右键菜单 Hook；客户端内部接口不可用时安全地返回 false。 */
export function installSchematicContextMenuHook(): boolean {
	const runtime = globalThis as unknown as SchematicRuntime;
	const bus = runtime.SCH?.gVars?.messageBus;
	if (!bus || typeof bus.publish !== 'function' || typeof bus.rpcReply !== 'function')
		return false;

	const existing = runtime[SCHEMATIC_CONTEXT_MENU_HOOK_KEY];
	if (existing?.version === extensionConfig.version)
		return true;
	if (existing) {
		bus.publish = existing.originalPublish;
		bus.rpcReply = existing.originalRpcReply;
	}

	let latestContext: SchematicContextMenuState | undefined;
	const originalPublish = bus.publish;
	const originalRpcReply = bus.rpcReply;
	bus.publish = function (topic, message, ...args) {
		if (topic === 'showEditorContextMenu') {
			latestContext = Array.isArray(message)
				? message[0] as SchematicContextMenuState
				: message as SchematicContextMenuState;
		}
		return originalPublish.call(this, topic, message, ...args);
	};
	bus.rpcReply = function (result, replyTopic, ...args) {
		const nextResult = String(replyTopic).includes('menuData')
			? appendSchematicContextMenu(result as RawContextMenuData, latestContext)
			: result;
		return originalRpcReply.call(this, nextResult, replyTopic, ...args);
	};
	runtime[SCHEMATIC_CONTEXT_MENU_HOOK_KEY] = {
		originalPublish,
		originalRpcReply,
		version: extensionConfig.version,
	};
	return true;
}

function startSchematicContextMenuHook(): void {
	installSchematicContextMenuHook();
	const runtime = globalThis as unknown as SchematicRuntime;
	const existingTimer = runtime[SCHEMATIC_CONTEXT_MENU_TIMER_KEY];
	if (existingTimer?.version === extensionConfig.version)
		return;
	if (existingTimer)
		clearInterval(existingTimer.timer);
	runtime[SCHEMATIC_CONTEXT_MENU_TIMER_KEY] = {
		timer: setInterval(installSchematicContextMenuHook, SCHEMATIC_CONTEXT_MENU_RETRY_MS),
		version: extensionConfig.version,
	};
}

function stopSchematicContextMenuHook(): void {
	const runtime = globalThis as unknown as SchematicRuntime;
	const timer = runtime[SCHEMATIC_CONTEXT_MENU_TIMER_KEY];
	if (timer?.version === extensionConfig.version) {
		clearInterval(timer.timer);
		delete runtime[SCHEMATIC_CONTEXT_MENU_TIMER_KEY];
	}
	const hook = runtime[SCHEMATIC_CONTEXT_MENU_HOOK_KEY];
	const bus = runtime.SCH?.gVars?.messageBus;
	if (hook?.version === extensionConfig.version && bus) {
		bus.publish = hook.originalPublish;
		bus.rpcReply = hook.originalRpcReply;
		delete runtime[SCHEMATIC_CONTEXT_MENU_HOOK_KEY];
	}
}

/**
 * PCB 画布右键菜单由 PCB.gVars.messageBus 的 rightClickPcbMenu 事件生成。
 * 这里插入一个直接项目，命令仍调用公开的 PCB BBox 导出函数。
 */
export function appendPcbContextMenu(menu: PcbContextMenuState | undefined): PcbContextMenuState | undefined {
	if (!menu || !Array.isArray(menu.filter))
		return menu;
	if (menu.filter.some(item => item.cmd === PCB_CONTEXT_MENU_COMMAND))
		return menu;
	return {
		...menu,
		filter: [{
			cmd: PCB_CONTEXT_MENU_COMMAND,
			name: PCB_CONTEXT_MENU_EXPORT_TITLE,
			option: { disabledI18n: true },
			tips: '',
		}, ...menu.filter],
	};
}

/** 安装 PCB 画布右键菜单 Hook；内部接口不可用时保留现有稳定入口。 */
export function installPcbContextMenuHook(): boolean {
	const runtime = globalThis as unknown as PcbRuntime;
	const buses = getPcbMessageBuses().filter(bus => typeof bus.publish === 'function');
	if (buses.length === 0)
		return false;

	let hookState = runtime[PCB_CONTEXT_MENU_HOOK_KEY];
	if (hookState?.version !== extensionConfig.version) {
		for (const hook of hookState?.hooks ?? [])
			hook.bus.publish = hook.originalPublish;
		hookState = { hooks: [], version: extensionConfig.version };
		runtime[PCB_CONTEXT_MENU_HOOK_KEY] = hookState;
	}
	for (const bus of buses) {
		if (hookState.hooks.some(hook => hook.bus === bus))
			continue;
		const originalPublish = bus.publish;
		bus.publish = function (topic, message, ...args) {
			const nextMessage = topic === 'rightClickPcbMenu'
				? appendPcbContextMenu(message as PcbContextMenuState)
				: message;
			return originalPublish.call(this, topic, nextMessage, ...args);
		};
		hookState.hooks.push({ bus, originalPublish });
	}
	return true;
}

function startPcbContextMenuHook(): void {
	installPcbContextMenuHook();
	const runtime = globalThis as unknown as PcbRuntime;
	const existingTimer = runtime[PCB_CONTEXT_MENU_TIMER_KEY];
	if (existingTimer?.version === extensionConfig.version)
		return;
	if (existingTimer)
		clearInterval(existingTimer.timer);
	runtime[PCB_CONTEXT_MENU_TIMER_KEY] = {
		timer: setInterval(installPcbContextMenuHook, PCB_CONTEXT_MENU_RETRY_MS),
		version: extensionConfig.version,
	};
}

function stopPcbContextMenuHook(): void {
	const runtime = globalThis as unknown as PcbRuntime;
	const timer = runtime[PCB_CONTEXT_MENU_TIMER_KEY];
	if (timer?.version === extensionConfig.version) {
		clearInterval(timer.timer);
		delete runtime[PCB_CONTEXT_MENU_TIMER_KEY];
	}
	const hook = runtime[PCB_CONTEXT_MENU_HOOK_KEY];
	if (hook?.version === extensionConfig.version) {
		for (const installedHook of hook.hooks)
			installedHook.bus.publish = installedHook.originalPublish;
		delete runtime[PCB_CONTEXT_MENU_HOOK_KEY];
	}
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

export async function collectRows(primitives: Array<IPCB_Primitive>): Promise<Array<BBoxExportRow>> {
	const rows: Array<BBoxExportRow> = [];
	const zeroDegreeBBoxes = new Map<string, Awaited<ReturnType<typeof eda.pcb_Primitive.getPrimitivesBBox>>>();
	for (const primitive of primitives) {
		let bbox: Awaited<ReturnType<typeof eda.pcb_Primitive.getPrimitivesBBox>>;
		if (primitive.getState_PrimitiveType() === EPCB_PrimitiveType.COMPONENT) {
			const component = primitive as IPCB_PrimitiveComponent;
			const footprint = component.getState_Footprint();
			if (!footprint)
				throw new Error(`器件 ${component.getState_Designator() ?? component.getState_PrimitiveId()} 未关联封装，无法导出 0° BBox。`);
			const cacheKey = `${footprint.libraryUuid}:${footprint.uuid}`;
			bbox = zeroDegreeBBoxes.get(cacheKey);
			if (!bbox) {
				// 使用同一封装在远离画布的位置临时创建 0° 副本，读取官方
				// BBox 后立刻删除。不会改动原器件的旋转、属性或网络。
				const temporary = await eda.pcb_PrimitiveComponent.create(
					footprint,
					component.getState_Layer(),
					component.getState_X() + 1_000_000,
					component.getState_Y() + 1_000_000,
					0,
					true,
				);
				if (!temporary)
					throw new Error(`无法创建封装 ${footprint.name ?? footprint.uuid} 的 0° 临时副本。`);
				try {
					bbox = await eda.pcb_Primitive.getPrimitivesBBox([temporary]);
				}
				finally {
					await eda.pcb_PrimitiveComponent.delete(temporary);
				}
				if (!bbox)
					throw new Error(`无法读取封装 ${footprint.name ?? footprint.uuid} 的官方 0° BBox。`);
				zeroDegreeBBoxes.set(cacheKey, bbox);
			}
		}
		else {
			bbox = await eda.pcb_Primitive.getPrimitivesBBox([primitive]);
		}
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
	startSchematicContextMenuHook();
	startPcbContextMenuHook();
}

export function deactivate(): void {
	stopSchematicContextMenuHook();
	stopPcbContextMenuHook();
}

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

/**
 * 开发期官方真值导出：保留最终 X/Y 尺寸与 EDA/API 版本，供离线解析器
 * 逐项对照。内部四边界只参与计算，不写入 CSV。
 */
export async function exportCurrentFootprintTruthCsv(): Promise<void> {
	await runExport(async () => {
		const documentInfo = await eda.dmt_SelectControl.getCurrentDocumentInfo();
		if (!documentInfo || documentInfo.documentType !== EDMT_EditorDocumentType.FOOTPRINT)
			throw new Error('请先在封装编辑器中打开目标封装，再导出官方真值 CSV。');
		const bbox = await eda.pcb_Primitive.getPrimitivesBBox(await getCurrentFootprintPrimitives());
		if (!bbox)
			throw new Error('当前封装没有可读取的官方 BBox。');
		const libraryUuid = documentInfo.parentLibraryUuid
			?? await eda.lib_LibrariesList.getProjectLibraryUuid();
		const footprint = libraryUuid
			? await eda.lib_Footprint.get(documentInfo.uuid, libraryUuid)
			: undefined;
		await eda.sys_FileSystem.saveFile(
			new Blob([truthRowsToCsv([{
				bbox,
				footprintName: footprint?.name ?? documentInfo.uuid,
				footprintUuid: documentInfo.uuid,
				edaVersion: eda.sys_Environment.getEditorCurrentVersion(),
				extensionVersion: extensionConfig.version,
			}])], { type: 'text/csv;charset=utf-8' }),
			'footprint-bbox-truth.csv',
		);
		eda.sys_Message.showToastMessage('已导出当前封装官方真值 CSV。', ESYS_ToastMessageType.SUCCESS);
	});
}

/**
 * 开发期 PCB 0° 真值导出：在当前 PCB 的固定坐标创建外部库封装临时副本，
 * 读取官方器件 BBox 后立即删除。固定坐标可避免四位小数结果随现有器件位置变化。
 */
export async function exportCurrentFootprintPcbTruthCsv(): Promise<void> {
	await runExport(async () => {
		const documentInfo = await eda.dmt_SelectControl.getCurrentDocumentInfo();
		if (!documentInfo || documentInfo.documentType !== EDMT_EditorDocumentType.PCB)
			throw new Error('请先切换到一个可写 PCB 文档。');
		const mounted = (globalThis as unknown as Record<string, unknown>)[MOUNTED_TRUTH_FOOTPRINT_KEY] as MountedTruthFootprint | undefined;
		if (!mounted)
			throw new Error('请先在封装页执行“开发：挂载 .elibz2 真值库”，再切回 PCB。');
		const footprint = await eda.lib_Footprint.get(mounted.uuid, mounted.libraryUuid);
		if (!footprint)
			throw new Error('无法从已挂载的外部库读取封装索引。');
		const temporary = await eda.pcb_PrimitiveComponent.create(
			footprint,
			EPCB_LayerId.TOP,
			1_000_000,
			1_000_000,
			0,
			true,
		);
		if (!temporary)
			throw new Error('请先切换到一个可写 PCB 文档，再执行 PCB 0° 真值导出。');
		let bbox: Awaited<ReturnType<typeof eda.pcb_Primitive.getPrimitivesBBox>>;
		try {
			bbox = await eda.pcb_Primitive.getPrimitivesBBox([temporary]);
		}
		finally {
			await eda.pcb_PrimitiveComponent.delete(temporary);
		}
		if (!bbox)
			throw new Error('临时 PCB 器件没有可读取的官方 BBox。');
		await eda.sys_FileSystem.saveFile(
			new Blob([truthRowsToCsv([{
				bbox,
				footprintName: footprint.name ?? mounted.name,
				footprintUuid: mounted.uuid,
				edaVersion: eda.sys_Environment.getEditorCurrentVersion(),
				extensionVersion: extensionConfig.version,
			}])], { type: 'text/csv;charset=utf-8' }),
			'footprint-pcb-zero-truth.csv',
		);
	});
}

/** 使用公开外部库 API 挂载一个 .elibz2，供开发期官方真值抽样。 */
export async function mountElibz2TruthLibrary(): Promise<void> {
	await runExport(async () => {
		const selected = await eda.sys_FileSystem.openReadFileDialog('elibz2', false);
		const file = Array.isArray(selected) ? selected[0] : selected;
		if (!file)
			return;
		const archive = await JSZip.loadAsync(await file.arrayBuffer());
		const metadataEntry = Object.values(archive.files).find(entry => /(?:^|\/)footprint2\.json$/i.test(entry.name));
		const elibuEntry = Object.values(archive.files).find(entry => entry.name.toLowerCase().endsWith('.elibu'));
		if (!metadataEntry || !elibuEntry)
			throw new Error('所选 .elibz2 缺少 footprint2.json 或 .elibu。');
		const metadata = JSON.parse(await metadataEntry.async('text')) as {
			footprints?: Record<string, { display_title?: string; title?: string }>;
		};
		const [footprintUuid, footprintMetadata] = Object.entries(metadata.footprints ?? {})[0] ?? [];
		if (!footprintUuid || !footprintMetadata)
			throw new Error('所选 .elibz2 没有可挂载的封装元数据。');
		const footprintName = footprintMetadata.display_title ?? footprintMetadata.title ?? footprintUuid;
		const item = {
			data: await elibuEntry.async('blob'),
			name: footprintName,
			uuid: footprintUuid,
		};
		const libraryUuid = await eda.lib_LibrariesList.registerExtendLibrary(
			`BBox 真值临时库 - ${footprintName}`,
			{
				footprint: {
					getClassificationTree: async () => [],
					getDetail: async uuid => uuid === footprintUuid ? item : undefined,
					getList: async () => ({ count: 1, lists: [item], page: 1, pageSize: 1, totalPage: 1 }),
				},
			},
		);
		if (!libraryUuid)
			throw new Error('嘉立创 EDA 未返回临时外部库 UUID。');
		(globalThis as unknown as Record<string, unknown>)[MOUNTED_TRUTH_FOOTPRINT_KEY] = {
			libraryUuid,
			name: footprintName,
			uuid: footprintUuid,
		} satisfies MountedTruthFootprint;
		const tabId = await eda.dmt_EditorControl.openLibraryDocument(
			libraryUuid,
			ELIB_LibraryType.FOOTPRINT,
			footprintUuid,
		);
		if (!tabId)
			throw new Error('临时外部库已注册，但嘉立创 EDA 未能打开封装文档。');
		eda.sys_Message.showToastMessage(
			`已挂载并打开 ${footprintName}（${footprintUuid}）。`,
			ESYS_ToastMessageType.SUCCESS,
		);
	});
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		`${extensionConfig.displayName} ${extensionConfig.version}\n\n导出 Designator、Footprint、X 长、Y 宽和 Z 高五列 CSV。PCB BBox 使用 mil，原理图 BBox 使用 0.01 inch，均转换为 mm。若 3D 模型名包含 H 高度（如 L14-W20-H3.2），将导出 Z 高；否则留空。\n\n右键入口位于底部器件/符号列表；嘉立创 EDA 当前不开放 PCB 或原理图画布右键菜单扩展。`,
		extensionConfig.displayName,
		'确定',
	);
}
