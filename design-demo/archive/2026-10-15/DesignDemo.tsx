import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { PointerEvent, ReactNode } from 'react';

type View = 'devices' | 'device' | 'recording' | 'table' | 'graph' | 'image' | 'settings';
type Scenario = 'normal' | 'disconnected' | 'empty' | 'error';
type Layout = 'measurement' | 'camera' | 'single';
type DesignVariant = 'A' | 'A2' | 'B';
type Theme = 'dark' | 'light';
type Device = { id: string; label: string; model: string; kind: string; serial: string; address: string };
type PanelState = { id: string; view: View; target: string };
type Tree = { kind: 'panel'; id: string } | { kind: 'split'; direction: 'horizontal' | 'vertical'; ratio: number; children: [Tree, Tree] };
type DeviceSettings = { wavelength: string; range: string; bandwidth: string; internalAverage: boolean; measureInterval: string; exposure: string; gain: string; gainBoost: boolean; fps: string; csvFolder: string; csvFilename: string; videoFolder: string; videoFilename: string };
type Preferences = { theme: Theme; startupLayout: Layout; saveDirectory: string; windowWidth: string; windowHeight: string; restoreLayout: boolean };
type IconName = View | 'chevron' | 'add' | 'close' | 'maximize' | 'restore' | 'play' | 'stop' | 'folder' | 'reset' | 'check';

const VIEWS: { id: View; label: string }[] = [
  { id: 'devices', label: 'デバイス管理' }, { id: 'device', label: '個別デバイス' },
  { id: 'recording', label: '測定・記録' }, { id: 'table', label: 'データテーブル' },
  { id: 'graph', label: 'グラフ' }, { id: 'image', label: 'イメージ' },
  { id: 'settings', label: '設定' },
];
const DEVICES: Device[] = [
  { id: 'pm01', label: 'PM160-01', model: 'Thorlabs PM160', kind: 'パワーメータ', serial: 'P0016421', address: 'USB0::0x1313::0x8079::P0016421::INSTR' },
  { id: 'pm02', label: 'PM160-02', model: 'Thorlabs PM160', kind: 'パワーメータ', serial: 'P0016422', address: 'USB0::0x1313::0x8079::P0016422::INSTR' },
  { id: 'cam01', label: 'カメラ-01', model: 'Thorlabs uc480', kind: 'カメラ', serial: 'CAM02481', address: 'ThorCam / CAM02481' },
  { id: 'pm03', label: '参照用パワーメータ-03', model: 'Power Meter', kind: 'パワーメータ', serial: 'P0016450', address: 'USB0::0x1313::0x8079::P0016450::INSTR' },
];
// ThorCam-OPM-Control-Panelの設定UIを参照。値と範囲はデモ用で、実機接続時は機器から取得する。
const DEFAULT_SETTINGS: DeviceSettings = { wavelength: '532', range: 'auto', bandwidth: 'low', internalAverage: true, measureInterval: '0.1', exposure: '8.0', gain: '1.0', gainBoost: false, fps: '30', csvFolder: 'D:\\Experiments\\2026-10-14', csvFilename: 'power', videoFolder: 'D:\\Experiments\\2026-10-14', videoFilename: 'camera01' };
const DEFAULT_PREFERENCES: Preferences = { theme: 'dark', startupLayout: 'measurement', saveDirectory: 'D:\\Experiments\\2026-10-14', windowWidth: '1280', windowHeight: '720', restoreLayout: true };
// 各案で同じ時刻・同じ値を比較するため、乱数や実機通信を使わない固定の見本データ。
const SAMPLES = Array.from({ length: 181 }, (_, i) => ({
  seconds: i,
  pm01: 1.246 + .024 * Math.sin(i * .19) + .008 * Math.cos(i * .73) + .043 * Math.exp(-((i - 89) ** 2) / 85),
  pm02: .982 + .012 * Math.sin(i * .14 + 1.4) + .004 * Math.cos(i * .65),
}));
const LAST = SAMPLES[SAMPLES.length - 1];
const leaf = (id: string): Tree => ({ kind: 'panel', id });
function layoutState(layout: Layout): { tree: Tree; panels: Record<string, PanelState> } {
  const views: View[] = layout === 'camera' ? ['device', 'recording', 'image', 'table'] : ['devices', 'recording', 'graph', 'table'];
  const panels = Object.fromEntries(views.map((view, i) => [`p${i + 1}`, { id: `p${i + 1}`, view, target: layout === 'camera' && (i === 0 || i === 2) ? 'cam01' : 'pm01' }]));
  if (layout === 'single') return { tree: leaf('p1'), panels: { p1: { id: 'p1', view: 'graph', target: 'pm01' } } };
  return {
    panels,
    tree: { kind: 'split', direction: 'horizontal', ratio: 31, children: [
      { kind: 'split', direction: 'vertical', ratio: 48, children: [leaf('p1'), leaf('p2')] },
      { kind: 'split', direction: 'vertical', ratio: 62, children: [leaf('p3'), leaf('p4')] },
    ] },
  };
}
function removeLeaf(tree: Tree, id: string): Tree | null {
  if (tree.kind === 'panel') return tree.id === id ? null : tree;
  const a = removeLeaf(tree.children[0], id), b = removeLeaf(tree.children[1], id);
  return a && b ? { ...tree, children: [a, b] } : a ?? b;
}
function resizeSplit(tree: Tree, path: number[], ratio: number): Tree {
  if (tree.kind === 'panel') return tree;
  if (path.length === 0) return { ...tree, ratio };
  const children: [Tree, Tree] = [...tree.children];
  children[path[0]] = resizeSplit(children[path[0]], path.slice(1), ratio);
  return { ...tree, children };
}
function viewName(view: View) { return VIEWS.find(item => item.id === view)?.label ?? ''; }
function Icon({ name, size = 14 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    devices: <><rect x="3" y="4" width="14" height="5" rx="1" /><rect x="3" y="12" width="14" height="5" rx="1" /><path d="M6 6.5h1m-1 8h1m5-8h2m-2 8h2" /></>,
    device: <><path d="M3 5h14M3 10h14M3 15h14" /><rect x="6" y="3" width="3" height="4" rx=".5" /><rect x="12" y="8" width="3" height="4" rx=".5" /><rect x="5" y="13" width="3" height="4" rx=".5" /></>,
    recording: <><circle cx="10" cy="10" r="7" /><circle cx="10" cy="10" r="3" /></>,
    table: <><rect x="3" y="3" width="14" height="14" rx="1" /><path d="M3 7h14M3 12h14M8 3v14" /></>,
    graph: <><path d="M3 3v14h14M5 12l3-4 3 2 5-6" /></>,
    image: <><rect x="3" y="3" width="14" height="14" rx="1" /><circle cx="7" cy="7" r="1" /><path d="m4 15 5-5 3 3 2-2 3 4" /></>,
    settings: <><path d="m8 2-.5 2-1.6 1-2-.5-2 3 1.5 1.5v2L2 12.5l2 3 2-.5 1.6 1L8 18h4l.5-2 1.6-1 2 .5 2-3-1.5-1.5V9L18 7.5l-2-3-2 .5-1.6-1L12 2Z" /><circle cx="10" cy="10" r="2.5" /></>,
    chevron: <path d="m6 8 4 4 4-4" />,
    add: <path d="M10 4v12M4 10h12" />,
    close: <path d="m5 5 10 10M15 5 5 15" />,
    maximize: <><path d="M3 8V3h5m4 0h5v5M3 12v5h5m4 0h5v-5" /></>,
    restore: <><rect x="3" y="7" width="10" height="10" /><path d="M7 7V3h10v10h-4" /></>,
    play: <path d="m6 3 10 7-10 7Z" />,
    stop: <rect x="5" y="5" width="10" height="10" rx=".5" />,
    folder: <path d="M3 5h5l2 2h7v10H3Z" />,
    reset: <><path d="M4 8a6 6 0 1 1 1 7M3 3v5h5" /></>,
    check: <path d="m4 10 4 4 8-8" />,
  };
  return <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
function StateTag({ kind, children }: { kind: 'connected' | 'muted' | 'recording' | 'error'; children: ReactNode }) {
  return <span className={`state-tag ${kind}`}><span className="state-dot" />{children}</span>;
}
function ViewBody({ children }: { children: ReactNode }) {
  const element = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const measure = () => setCollapsed(node.getBoundingClientRect().height <= 1);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, []);
  // 内容は保持し、最小高さでは非表示・操作不可にする。再拡大しても編集中の値を失わない。
  return <div ref={element} className="view-body" inert={collapsed} aria-hidden={collapsed || undefined}><div className="view-body-content">{children}</div></div>;
}
function minimumTreeHeight(tree: Tree, panelHeight: number, gap: number): number {
  if (tree.kind === 'panel') return panelHeight;
  const a = minimumTreeHeight(tree.children[0], panelHeight, gap);
  const b = minimumTreeHeight(tree.children[1], panelHeight, gap);
  return tree.direction === 'vertical' ? a + b + gap : Math.max(a, b);
}
function verticalSplitLimits(element: HTMLDivElement, tree: Extract<Tree, { kind: 'split' }>) {
  const panel = element.querySelector('.demo-panel');
  const height = element.getBoundingClientRect().height;
  if (!panel || height <= 0) return { minimum: 16, maximum: 84 };
  const panelHeight = parseFloat(getComputedStyle(panel).minHeight);
  const gap = parseFloat(getComputedStyle(element).getPropertyValue('--split-gap'));
  const minimum = (minimumTreeHeight(tree.children[0], panelHeight, gap) + gap / 2) / height * 100;
  const maximum = 100 - (minimumTreeHeight(tree.children[1], panelHeight, gap) + gap / 2) / height * 100;
  return minimum <= maximum ? { minimum, maximum } : { minimum: 50, maximum: 50 };
}
function Split({ tree, renderPanel, onResize, path = [] }: { tree: Tree; renderPanel: (id: string) => ReactNode; onResize: (path: number[], ratio: number) => void; path?: number[] }) {
  const drag = useRef<{ start: number; length: number; vertical: boolean } | null>(null);
  const element = useRef<HTMLDivElement>(null);
  const [limits, setLimits] = useState({ minimum: 16, maximum: 84 });
  const pinnedEdge = useRef<'minimum' | 'maximum' | null>(null);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node || tree.kind !== 'split' || tree.direction !== 'vertical') return;
    const measure = () => {
      const next = verticalSplitLimits(node, tree);
      setLimits(current => current.minimum === next.minimum && current.maximum === next.maximum ? current : next);
      if (!pinnedEdge.current) {
        if (Math.abs(tree.ratio - next.minimum) < .000001) pinnedEdge.current = 'minimum';
        else if (Math.abs(tree.ratio - next.maximum) < .000001) pinnedEdge.current = 'maximum';
      }
      // 最小側の高さを保つ。割合だけを固定すると、ウィンドウ拡大時に本文が再表示される。
      const ratio = pinnedEdge.current ? next[pinnedEdge.current] : Math.max(next.minimum, Math.min(next.maximum, tree.ratio));
      if (Math.abs(ratio - tree.ratio) > .000001) onResize(path, ratio);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [tree, path, onResize]);
  if (tree.kind === 'panel') return renderPanel(tree.id);
  const ratio = tree.ratio;
  const vertical = tree.direction === 'vertical';
  const bounds = vertical ? limits : { minimum: 16, maximum: 84 };
  const setRatio = (value: number) => {
    const next = vertical && element.current ? verticalSplitLimits(element.current, tree) : bounds;
    const ratio = Math.max(next.minimum, Math.min(next.maximum, value));
    pinnedEdge.current = vertical && ratio === next.minimum ? 'minimum' : vertical && ratio === next.maximum ? 'maximum' : null;
    onResize(path, ratio);
  };
  const resize = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const coordinate = drag.current.vertical ? event.clientY : event.clientX;
    setRatio((coordinate - drag.current.start) / drag.current.length * 100);
  };
  return <div ref={element} className={`split ${vertical ? 'split-vertical' : 'split-horizontal'}`}>
    <div className="split-child" style={{ flexBasis: `calc(${ratio}% - var(--split-half-gap))` }}><Split tree={tree.children[0]} renderPanel={renderPanel} onResize={onResize} path={[...path, 0]} /></div>
    <div className="splitter" role="separator" aria-label={vertical ? '上下パネルの境界' : '左右パネルの境界'} aria-orientation={vertical ? 'horizontal' : 'vertical'} aria-valuemin={bounds.minimum} aria-valuemax={bounds.maximum} aria-valuenow={ratio} tabIndex={0}
      onPointerDown={event => {
        const rect = event.currentTarget.parentElement!.getBoundingClientRect();
        drag.current = { start: vertical ? rect.top : rect.left, length: vertical ? rect.height : rect.width, vertical };
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
      }} onPointerMove={resize} onPointerUp={event => { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={() => { drag.current = null; }}
      onKeyDown={event => { if (['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(event.key)) { event.preventDefault(); setRatio(ratio + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -2 : 2)); } }} />
    <div className="split-child" style={{ flexBasis: `calc(${100 - ratio}% - var(--split-half-gap))` }}><Split tree={tree.children[1]} renderPanel={renderPanel} onResize={onResize} path={[...path, 1]} /></div>
  </div>;
}

function PowerPlot({ target, range, comparison }: { target: string; range: string; comparison: boolean }) {
  const plotElement = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 820, height: 320 });
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      if (rect.width > 0 && rect.height > 0) setSize({ width: rect.width, height: rect.height });
    });
    if (plotElement.current) observer.observe(plotElement.current);
    return () => observer.disconnect();
  }, []);
  // 表示領域に座標系を合わせ、サイズ変更で軸文字が縦横に伸縮しないようにする。
  const { width, height } = size;
  const left = 51, right = 24, top = 26, bottom = 28;
  const duration = Number(range), start = Math.max(0, 180 - duration);
  const data = SAMPLES.filter(sample => sample.seconds >= start);
  const minimum = comparison ? .92 : target === 'pm02' ? .94 : 1.16;
  const maximum = !comparison && target === 'pm02' ? 1.04 : 1.34;
  const x = (seconds: number) => left + (seconds - start) / (180 - start) * (width - left - right);
  const y = (value: number) => top + (maximum - value) / (maximum - minimum) * (height - top - bottom);
  const path = (key: 'pm01' | 'pm02') => data.map((sample, i) => `${i === 0 ? 'M' : 'L'}${x(sample.seconds).toFixed(2)},${y(sample[key]).toFixed(2)}`).join(' ');
  const ticks = Array.from({ length: 5 }, (_, i) => minimum + (maximum - minimum) * i / 4);
  return <svg ref={plotElement} className="power-plot" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="固定の見本データによる光パワーの時系列グラフ">
    {ticks.map(value => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="plot-grid" /><text x={left - 12} y={y(value) + 4} textAnchor="end">{value.toFixed(2)}</text></g>)}
    {Array.from({ length: 7 }, (_, i) => start + (180 - start) * i / 6).map(seconds => <g key={seconds}><line x1={x(seconds)} x2={x(seconds)} y1={top} y2={height - bottom} className="plot-grid" /><text x={x(seconds)} y={height - 12} textAnchor="middle">{`${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.round(seconds % 60).toString().padStart(2, '0')}`}</text></g>)}
    <text x="16" y="14">mW</text><text x={width - right} y="14" textAnchor="end">経過時間</text>
    <path d={path(target === 'pm02' ? 'pm02' : 'pm01')} className="trace primary-trace" />
    {comparison && <path d={path(target === 'pm02' ? 'pm01' : 'pm02')} className="trace secondary-trace" />}
    <circle cx={x(180)} cy={y(target === 'pm02' ? LAST.pm02 : LAST.pm01)} r="3.5" className="trace-point" />
  </svg>;
}
function CameraFrame() {
  const size = 400;
  const cells = Array.from({ length: 48 * 36 }, (_, i) => {
    const x = i % 48, y = Math.floor(i / 48);
    const intensity = Math.exp(-(((x - 25) / 8) ** 2 + ((y - 17) / 5.5) ** 2)) * .94;
    const noise = .018 + .025 * (Math.sin(x * 12.9898 + y * 78.233) + 1) / 2;
    const v = Math.min(1, intensity + noise);
    const color = v < .22 ? `rgb(${Math.round(v * 65)},${Math.round(v * 150)},${Math.round(35 + v * 430)})` : v < .65 ? `rgb(${Math.round((v - .22) * 560)},${Math.round(90 + v * 200)},${Math.round(230 - v * 240)})` : `rgb(255,${Math.round(255 - (v - .65) * 380)},${Math.round(20 + (v - .65) * 250)})`;
    return <rect key={i} x={x * size / 48} y={y * 300 / 36} width={size / 48 + .5} height={300 / 36 + .5} fill={color} />;
  });
  return <svg className="camera-frame" viewBox="0 0 400 300" role="img" aria-label="ガウシアン分布を使ったビーム画像の見本"><g>{cells}</g><g stroke="white" strokeWidth=".65" opacity=".7"><path d="M210 125v50M185 150h50" /><rect x="143" y="92" width="135" height="112" fill="none" strokeDasharray="4 3" /></g><text x="9" y="17" fill="#fff" fontSize="9" fontFamily="monospace">CAMERA-01 · SAMPLE</text><path d="M305 277h75m-75-3v6m75-6v6" stroke="white" /><text x="342" y="269" fill="white" fontSize="9" textAnchor="middle">100 μm</text></svg>;
}

export default function DesignDemo() {
  const [variant, setVariant] = useState<DesignVariant>(() => {
    const value = new URLSearchParams(location.search).get('variant');
    return value === 'A' || value === 'B' ? value : 'A2';
  });
  const [layout, setLayout] = useState<Layout>('measurement');
  const [workspace, setWorkspace] = useState(() => layoutState('measurement'));
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [scenario, setScenario] = useState<Scenario>('normal');
  const [connected, setConnected] = useState<Record<string, boolean>>({ pm01: true, pm02: true, cam01: true, pm03: false });
  const [acquiring, setAcquiring] = useState(true);
  const [recording, setRecording] = useState(true);
  const [settings, setSettings] = useState<Record<string, DeviceSettings>>({});
  const [drafts, setDrafts] = useState<Record<string, DeviceSettings>>({});
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [preferenceDrafts, setPreferenceDrafts] = useState<Record<string, Preferences>>({});
  const [graphRanges, setGraphRanges] = useState<Record<string, string>>({});
  const [comparisons, setComparisons] = useState<Record<string, boolean>>({});
  const [following, setFollowing] = useState<Record<string, boolean>>({});
  const [imageZooms, setImageZooms] = useState<Record<string, string>>({});
  const [status, setStatus] = useState('見本データを表示しています');
  const [focused, setFocused] = useState('p3');
  const [maximized, setMaximized] = useState<string | null>(null);
  const nextId = useRef(5);
  const isConnected = (id: string) => scenario !== 'disconnected' && Boolean(connected[id]);
  const updatePanel = (id: string, update: Partial<PanelState>) => setWorkspace(current => ({ ...current, panels: { ...current.panels, [id]: { ...current.panels[id], ...update } } }));
  const chooseLayout = (value: Layout) => { setLayout(value); setWorkspace(layoutState(value)); setMaximized(null); setLayoutRevision(i => i + 1); setFocused(value === 'single' ? 'p1' : 'p3'); };
  const chooseVariant = (value: DesignVariant) => { setVariant(value); const url = new URL(location.href); url.searchParams.set('variant', value); history.replaceState(null, '', url); };
  const currentSettings = (target: string) => settings[target] ?? { ...DEFAULT_SETTINGS, csvFilename: target === 'cam01' ? 'camera01_timestamps' : `${target}_power` };
  const draftKey = (panel: PanelState) => `${panel.id}:${panel.target}`;
  const editSetting = (panel: PanelState, key: keyof DeviceSettings, value: string | boolean) => {
    const id = draftKey(panel);
    setDrafts(current => ({ ...current, [id]: { ...(current[id] ?? currentSettings(panel.target)), [key]: value } }));
  };
  const editPreference = (panelId: string, key: keyof Preferences, value: string | boolean) => {
    setPreferenceDrafts(current => ({ ...current, [panelId]: { ...(current[panelId] ?? preferences), [key]: value } }));
  };
  const resetPreferenceDraft = (panelId: string) => {
    setPreferenceDrafts(current => { const next = { ...current }; delete next[panelId]; return next; });
  };
  const addPanel = () => {
    const id = `p${nextId.current++}`;
    setWorkspace(current => ({ tree: { kind: 'split', direction: 'horizontal', ratio: 70, children: [current.tree, leaf(id)] }, panels: { ...current.panels, [id]: { id, view: 'graph', target: 'pm01' } } }));
    setMaximized(null); setLayoutRevision(i => i + 1); setFocused(id);
  };
  const closePanel = (id: string) => {
    if (Object.keys(workspace.panels).length === 1) return;
    setWorkspace(current => { const panels = { ...current.panels }; delete panels[id]; return { tree: removeLeaf(current.tree, id)!, panels }; });
    setMaximized(null); setLayoutRevision(i => i + 1);
  };
  const blankState = (view: View, target?: string): ReactNode => {
    if (scenario === 'disconnected' || (target && !isConnected(target))) return <div className="empty-state"><Icon name="devices" size={24} /><strong>デバイスが接続されていません</strong><p>デバイス管理ビューで接続してください。</p></div>;
    if (scenario === 'empty' || target === 'pm03') return <div className="empty-state"><Icon name={view} size={24} /><strong>表示するデータがありません</strong><p>測定・記録ビューでデータ取得を開始してください。</p></div>;
    return null;
  };
  const deviceSelect = (panel: PanelState, filter?: string) => <select className="device-select" aria-label={`${panel.id}の表示対象`} value={panel.target} onChange={event => updatePanel(panel.id, { target: event.target.value })}>
    {DEVICES.filter(device => !filter || device.kind === filter).map(device => <option key={device.id} value={device.id}>{device.label}</option>)}
  </select>;
  const panelBody = (panel: PanelState): { body: ReactNode; footer: ReactNode } => {
    if (panel.view === 'settings') {
      const draft = preferenceDrafts[panel.id] ?? preferences;
      return { body: <>
        <div className="view-toolbar"><span className="muted">アプリ・ウィンドウ設定</span><span className="toolbar-spacer" /><span className="muted">{preferenceDrafts[panel.id] ? '未適用の変更' : '適用済み'}</span></div>
        <div className="view-scroll preferences-scroll">
          <div className="section"><h3>アプリ <span className="muted">全ウィンドウで共通</span></h3>
            <label className="setting-row"><span>配色</span><select aria-label={`${panel.id}の配色`} value={draft.theme ?? 'dark'} onChange={event => editPreference(panel.id, 'theme', event.target.value)}><option value="dark">ダーク</option><option value="light">ライト</option></select></label>
            <label className="setting-row"><span>起動時のレイアウト</span><select aria-label={`${panel.id}の起動時のレイアウト`} value={draft.startupLayout} onChange={event => editPreference(panel.id, 'startupLayout', event.target.value)}><option value="measurement">測定</option><option value="camera">カメラ</option><option value="single">1パネル</option></select></label>
            <label className="setting-row"><span>標準の保存先</span><input aria-label={`${panel.id}の標準の保存先`} value={draft.saveDirectory} onChange={event => editPreference(panel.id, 'saveDirectory', event.target.value)} /></label>
          </div>
          <div className="section"><h3>ウィンドウ <span className="muted">このウィンドウ</span></h3>
            <label className="setting-row"><span>起動時の幅</span><span className="unit-input"><input type="number" min="640" aria-label={`${panel.id}の起動時の幅`} value={draft.windowWidth} onChange={event => editPreference(panel.id, 'windowWidth', event.target.value)} /><span>px</span></span></label>
            <label className="setting-row"><span>起動時の高さ</span><span className="unit-input"><input type="number" min="420" aria-label={`${panel.id}の起動時の高さ`} value={draft.windowHeight} onChange={event => editPreference(panel.id, 'windowHeight', event.target.value)} /><span>px</span></span></label>
            <label className="setting-row"><span>前回の配置を復元</span><span><input type="checkbox" aria-label={`${panel.id}の前回の配置を復元`} checked={draft.restoreLayout} onChange={event => editPreference(panel.id, 'restoreLayout', event.target.checked)} /> 有効</span></label>
          </div>
          <div className="section action-row"><button className="accent-button" onClick={() => { setPreferences(draft); resetPreferenceDraft(panel.id); setStatus('アプリ・ウィンドウ設定を適用しました'); }}><Icon name="check" />設定を適用</button><button onClick={() => resetPreferenceDraft(panel.id)}>元に戻す</button></div>
        </div>
        </>, footer: <><span>設定値はこのデモ内で共有</span><span>全体設定</span></> };
    }
    const device = DEVICES.find(item => item.id === panel.target)!;
    if (panel.view === 'devices') return { body: <>
      <div className="view-toolbar"><button onClick={() => setStatus('探索結果：見本の4台を表示しました')}><Icon name="reset" />スキャン</button><span className="muted">4台</span><span className="toolbar-spacer" /><button disabled={isConnected(panel.target)} onClick={() => { setConnected(current => ({ ...current, [panel.target]: true })); setStatus(`${device.label}を接続状態にしました`); }}>接続</button><button disabled={!isConnected(panel.target)} onClick={() => { setConnected(current => ({ ...current, [panel.target]: false })); setStatus(`${device.label}を未接続状態にしました`); }}>切断</button></div>
      <div className="device-list" role="listbox" aria-label={`${panel.id}のデバイス一覧`}>
        {DEVICES.map(item => <button key={item.id} role="option" aria-selected={item.id === panel.target} className={`device-row ${item.id === panel.target ? 'selected' : ''}`} onClick={() => updatePanel(panel.id, { target: item.id })}>
          <Icon name={item.kind === 'カメラ' ? 'image' : 'devices'} size={17} /><span className="device-label"><strong>{item.label}</strong><small>{item.model}</small></span><StateTag kind={isConnected(item.id) ? 'connected' : 'muted'}>{isConnected(item.id) ? '接続済み' : '未接続'}</StateTag>
        </button>)}
      </div>
      <details className="device-details"><summary>選択したデバイスの詳細</summary><div className="section"><dl><dt>種類</dt><dd>{device.kind}</dd><dt>シリアル</dt><dd className="numeric">{device.serial}</dd><dt>アドレス</dt><dd className="numeric address">{device.address}</dd></dl></div></details>
      </>, footer: <><span>接続済み {DEVICES.filter(item => isConnected(item.id)).length} / 4</span><span>{device.label}</span></> };
    if (panel.view === 'device') {
      const draft = drafts[draftKey(panel)] ?? currentSettings(panel.target);
      const recordingLocked = recording && scenario !== 'error' && isConnected(panel.target);
      return { body: <><div className="view-toolbar">{deviceSelect(panel)}<span className="toolbar-spacer" /><StateTag kind={isConnected(panel.target) ? 'connected' : 'muted'}>{isConnected(panel.target) ? '接続済み' : '未接続'}</StateTag></div>
        <div className="view-scroll"><div className="section"><h3>機器情報</h3><dl><dt>モデル</dt><dd>{device.model}</dd><dt>シリアル</dt><dd className="numeric">{device.serial}</dd></dl></div>
          <div className="section device-parameter-section"><h3 id={`${panel.id}-device-settings-title`}>{device.kind === 'カメラ' ? '撮像設定' : '測定設定'}</h3>
            <fieldset className="device-settings-fields" aria-labelledby={`${panel.id}-device-settings-title`} disabled={!isConnected(panel.target)}>
              {device.kind === 'カメラ' ? <>
                <label className="setting-row"><span>露光時間</span><span className="unit-input"><input type="number" min="0" max="100" step="0.01" aria-label={`${panel.id}の露光時間`} value={draft.exposure} onChange={event => editSetting(panel, 'exposure', event.target.value)} /><span>ms</span></span></label>
                <label className="setting-row"><span>ゲイン</span><input type="number" min="1" max="100" step="0.2" aria-label={`${panel.id}のゲイン`} value={draft.gain} onChange={event => editSetting(panel, 'gain', event.target.value)} /></label>
                <label className="setting-row"><span>ゲインブースト</span><span><input type="checkbox" aria-label={`${panel.id}のゲインブースト`} checked={draft.gainBoost} onChange={event => editSetting(panel, 'gainBoost', event.target.checked)} /> 有効</span></label>
                <label className="setting-row"><span>撮影FPS</span><span className="unit-input"><input type="number" min="1" step="1" list={`${panel.id}-fps-options`} disabled={recordingLocked} aria-label={`${panel.id}の撮影FPS`} value={draft.fps} onChange={event => editSetting(panel, 'fps', event.target.value)} /><span>fps</span></span></label>
                <datalist id={`${panel.id}-fps-options`}>{[1, 5, 10, 15, 20, 30].map(fps => <option key={fps} value={fps} />)}</datalist>
              </> : <>
                <label className="setting-row"><span>波長</span><span className="unit-input"><input type="number" min="400" max="1100" step="1" aria-label={`${panel.id}の波長`} value={draft.wavelength} onChange={event => editSetting(panel, 'wavelength', event.target.value)} /><span>nm</span></span></label>
                <label className="setting-row"><span>測定レンジ</span><select aria-label={`${panel.id}の測定レンジ`} value={draft.range} onChange={event => editSetting(panel, 'range', event.target.value)}><option value="auto">自動</option>{['1000', '100', '10', '1', '0.1', '0.01'].map(range => <option key={range} value={range}>{range} mW</option>)}</select></label>
                <label className="setting-row"><span>帯域幅</span><select aria-label={`${panel.id}の帯域幅`} value={draft.bandwidth} onChange={event => editSetting(panel, 'bandwidth', event.target.value)}><option value="low">低（low）</option><option value="high">高（high）</option></select></label>
                <label className="setting-row"><span>内部平均化</span><span><input type="checkbox" aria-label={`${panel.id}の内部平均化`} checked={draft.internalAverage} onChange={event => editSetting(panel, 'internalAverage', event.target.checked)} /> 有効</span></label>
                <label className="setting-row"><span>測定周期</span><span className="unit-input"><input type="number" min="0.1" max="2" step="0.1" disabled={recordingLocked} aria-label={`${panel.id}の測定周期`} value={draft.measureInterval} onChange={event => editSetting(panel, 'measureInterval', event.target.value)} /><span>s</span></span></label>
              </>}
            </fieldset>
            {recordingLocked && <p className="field-note">記録中は{device.kind === 'カメラ' ? '撮影FPS' : '測定周期'}と記録出力を変更できません。測定・記録ビューで記録を停止してください。</p>}
          </div>
          <details className="section device-recording-settings"><summary>記録出力</summary>
            <fieldset className="device-settings-fields" aria-label="記録出力" disabled={!isConnected(panel.target) || recordingLocked}>
              {device.kind === 'カメラ' && <>
                <label className="setting-row"><span>動画保存先</span><input aria-label={`${panel.id}の動画保存先`} value={draft.videoFolder} onChange={event => editSetting(panel, 'videoFolder', event.target.value)} /></label>
                <label className="setting-row"><span>動画ファイル名</span><span className="unit-input file-input"><input aria-label={`${panel.id}の動画ファイル名`} value={draft.videoFilename} onChange={event => editSetting(panel, 'videoFilename', event.target.value)} /><span>.mp4</span></span></label>
              </>}
              <label className="setting-row"><span>{device.kind === 'カメラ' ? '時刻CSV保存先' : 'CSV保存先'}</span><input aria-label={`${panel.id}のCSV保存先`} value={draft.csvFolder} onChange={event => editSetting(panel, 'csvFolder', event.target.value)} /></label>
              <label className="setting-row"><span>{device.kind === 'カメラ' ? '時刻CSVファイル名' : 'CSVファイル名'}</span><span className="unit-input file-input"><input aria-label={`${panel.id}のCSVファイル名`} value={draft.csvFilename} onChange={event => editSetting(panel, 'csvFilename', event.target.value)} /><span>.csv</span></span></label>
            </fieldset>
          </details>
        </div>
        <div className="section action-row device-actions"><button className="accent-button" disabled={!isConnected(panel.target)} onClick={() => { setSettings(current => ({ ...current, [panel.target]: draft })); setStatus(`${device.label}の設定を反映しました（見本）`); }}><Icon name="check" />設定を適用</button><button onClick={() => setDrafts(current => ({ ...current, [draftKey(panel)]: currentSettings(panel.target) }))}>元に戻す</button></div>
        </>, footer: <><span>適用済み：{device.kind === 'カメラ' ? `${currentSettings(panel.target).exposure} ms / ${currentSettings(panel.target).fps} fps` : `${currentSettings(panel.target).wavelength} nm / ${currentSettings(panel.target).measureInterval} s`}</span><span>{device.label}</span></> };
    }
    if (panel.view === 'recording') return { body: <><div className="view-toolbar"><StateTag kind={acquiring && scenario !== 'disconnected' ? 'connected' : 'muted'}>{acquiring && scenario !== 'disconnected' ? '取得中' : '停止中'}</StateTag><span className="toolbar-spacer" /><span className="numeric">00:03:00</span></div>
      <div className="view-scroll"><div className="section"><h3>データ取得</h3><div className="acquisition-devices">{DEVICES.slice(0, 3).map(item => <div key={item.id}><span>{item.label}</span><StateTag kind={acquiring && isConnected(item.id) ? 'connected' : 'muted'}>{acquiring && isConnected(item.id) ? '取得中' : '停止'}</StateTag></div>)}</div><div className="action-row"><button disabled={acquiring || scenario === 'disconnected'} onClick={() => { setAcquiring(true); setStatus('見本のデータ取得を開始しました'); }}><Icon name="play" />取得開始</button><button disabled={!acquiring} onClick={() => { setAcquiring(false); setRecording(false); setStatus('見本のデータ取得と記録を停止しました'); }}><Icon name="stop" />取得停止</button></div></div>
        <div className="section"><h3>ファイル記録 <StateTag kind={recording && scenario !== 'error' ? 'recording' : scenario === 'error' ? 'error' : 'muted'}>{scenario === 'error' ? '保存エラー' : recording ? '記録中' : '停止中'}</StateTag></h3><label className="stacked-field">既定の保存先<span className="path-field"><input aria-label={`${panel.id}の保存先`} defaultValue="D:\\Experiments\\2026-10-14" /><button title="保存先を選択（見本）" aria-label="保存先を選択" onClick={() => setStatus('このデモでは見本の保存先を使用します')}><Icon name="folder" /></button></span></label><label className="setting-row"><span>記録名</span><input aria-label={`${panel.id}の記録名`} defaultValue="beam_stability_01" /></label><div className="action-row"><button className="accent-button" disabled={recording || !acquiring || scenario === 'error'} onClick={() => { setRecording(true); setStatus('見本の記録を開始しました'); }}><Icon name="recording" />記録開始</button><button disabled={!recording} onClick={() => { setRecording(false); setStatus('見本の記録を停止しました。データ取得は継続します'); }}><Icon name="stop" />記録停止</button></div><p className="field-note">機器ごとの保存先・ファイル名は個別デバイスで設定</p>{scenario === 'error' && <div className="error-message">保存先へ書き込めません。保存先と空き容量を確認してください。</div>}</div>
      </div></>, footer: <><span>デバイス別CSV</span><span className="numeric">見本：18,432点</span></> };
    if (panel.view === 'graph') {
      const target = device.kind === 'カメラ' ? 'pm01' : panel.target;
      const graphPanel = { ...panel, target };
      return { body: <><div className="view-toolbar">{deviceSelect(graphPanel, 'パワーメータ')}<label className="inline-field">範囲<select aria-label={`${panel.id}の表示範囲`} value={graphRanges[panel.id] ?? '180'} onChange={event => setGraphRanges(current => ({ ...current, [panel.id]: event.target.value }))}><option value="60">直近1分</option><option value="120">直近2分</option><option value="180">直近3分</option></select></label><span className="toolbar-spacer" /><button aria-pressed={following[panel.id] ?? true} className={(following[panel.id] ?? true) ? 'toggled' : ''} onClick={() => setFollowing(current => ({ ...current, [panel.id]: !(current[panel.id] ?? true) }))}>追従</button></div>
        {blankState('graph', target) ?? <><div className="plot-legend"><span><i className="line-key" />{DEVICES.find(item => item.id === target)?.label}</span><label><input type="checkbox" checked={comparisons[panel.id] ?? true} onChange={event => setComparisons(current => ({ ...current, [panel.id]: event.target.checked }))} /><i className="line-key secondary" />{target === 'pm02' ? 'PM160-01' : 'PM160-02'}</label><span className="toolbar-spacer" /><span className="muted">光パワー</span></div><div className="plot-area"><PowerPlot target={target} range={graphRanges[panel.id] ?? '180'} comparison={comparisons[panel.id] ?? true} /></div><div className="readout-strip"><span>最新値 <strong className="numeric">{(target === 'pm02' ? LAST.pm02 : LAST.pm01).toFixed(4)} <small>mW</small></strong></span><span>波長 <b className="numeric">{currentSettings(target).wavelength} nm</b></span><span>表示 <b className="numeric">{Number(graphRanges[panel.id] ?? 180) + 1}点</b></span><span className="toolbar-spacer" /><StateTag kind={acquiring && isConnected(target) ? 'connected' : 'muted'}>{acquiring && isConnected(target) ? '取得中' : '停止中'}</StateTag></div></>}
        </>, footer: <><span>{(following[panel.id] ?? true) ? '表示範囲：最新に追従' : '表示範囲：固定'}</span><span>時系列 · 見本データ</span></> };
    }
    if (panel.view === 'table') return { body: <><div className="view-toolbar"><span className="muted">対象</span>{deviceSelect(panel)}<span className="toolbar-spacer" /><span className="numeric muted">最新40点</span></div>
      {blankState('table', panel.target) ?? <div className="table-scroll"><table><thead><tr><th>経過時間 <small>s</small></th><th>時刻</th><th>デバイス</th><th className="align-right">光パワー <small>mW</small></th></tr></thead><tbody>{panel.target === 'cam01' ? <tr><td colSpan={4} className="table-empty">カメラ画像はイメージビューで確認できます。</td></tr> : [...SAMPLES].reverse().slice(0, 40).map(sample => <tr key={sample.seconds}><td className="numeric">{sample.seconds.toFixed(3)}</td><td className="numeric">14:{`${32 + Math.floor(sample.seconds / 60)}`.padStart(2, '0')}:{`${sample.seconds % 60}`.padStart(2, '0')}.000</td><td>{device.label}</td><td className="numeric align-right">{(panel.target === 'pm02' ? sample.pm02 : sample.pm01).toFixed(4)}</td></tr>)}</tbody></table></div>}
      </>, footer: <><span>単位：mW</span><span>グラフと共通の見本データ</span></> };
    return { body: <><div className="view-toolbar">{deviceSelect({ ...panel, target: 'cam01' }, 'カメラ')}<label className="inline-field">倍率<select aria-label={`${panel.id}の画像倍率`} value={imageZooms[panel.id] ?? 'fit'} onChange={event => setImageZooms(current => ({ ...current, [panel.id]: event.target.value }))}><option value="fit">全体表示</option><option value="100">100%</option><option value="150">150%</option></select></label><span className="toolbar-spacer" /><span className="muted">疑似カラー</span></div>
      {blankState('image', 'cam01') ?? <div className={`image-area zoom-${imageZooms[panel.id] ?? 'fit'}`}><div className="image-mat"><CameraFrame /></div><div className="image-caption"><span>ガウシアン分布の見本</span><span className="numeric">640 × 480 px</span></div></div>}
      </>, footer: <><span>露光 <b className="numeric">{currentSettings('cam01').exposure} ms</b> / <b className="numeric">{currentSettings('cam01').fps} fps</b></span><span>ライブ画像の外観見本</span></> };
  };
  const renderPanel = (id: string) => {
    const panel = workspace.panels[id];
    if (!panel) return null;
    const { body, footer } = panelBody(panel);
    return <section className={`demo-panel ${focused === id ? 'focused-panel' : ''}`} aria-label={`${id} ${viewName(panel.view)}`} onPointerDown={() => setFocused(id)} onFocus={() => setFocused(id)}>
      <header className="panel-heading"><div className="view-switch"><Icon name={panel.view} /><Icon name="chevron" size={9} /><select aria-label={`${id}のビュー切り替え`} value={panel.view} onChange={event => updatePanel(id, { view: event.target.value as View })}>{VIEWS.map(view => <option key={view.id} value={view.id}>{view.label}</option>)}</select></div><span className="panel-title">{viewName(panel.view)}</span><span className="toolbar-spacer" /><span className="panel-number">{id.replace('p', '')}</span><button className="icon-button" title={maximized === id ? '配置に戻す' : 'このパネルを最大化'} aria-label={`${id}を${maximized === id ? '元の配置に戻す' : '最大化'}`} onClick={() => setMaximized(current => current === id ? null : id)}><Icon name={maximized === id ? 'restore' : 'maximize'} /></button><button className="icon-button" title="パネルを閉じる" aria-label={`${id}を閉じる`} disabled={Object.keys(workspace.panels).length === 1} onClick={() => closePanel(id)}><Icon name="close" /></button></header>
      <div className={`panel-content ${panel.view}-content`}><ViewBody>{scenario === 'error' && panel.view !== 'recording' && panel.view !== 'settings' && <div className="error-message compact-error">記録エラー：測定・記録ビューで保存先を確認してください。</div>}{body}</ViewBody><div className="view-footer">{footer}</div></div>
    </section>;
  };
  return <div className={`design-demo variant-${variant} theme-${preferences.theme ?? 'dark'}`}>
    <div className="comparison-bar"><span className="comparison-label">デザイン比較</span><div className="variant-tabs" aria-label="デザイン案"><button aria-pressed={variant === 'A2'} onClick={() => chooseVariant('A2')}><b>A2</b> Blender参考</button><button aria-pressed={variant === 'A'} onClick={() => chooseVariant('A')}><b>A</b> 調整前</button><button aria-pressed={variant === 'B'} onClick={() => chooseVariant('B')}><b>B</b> 読みやすさ</button></div><span className="variant-description">{variant === 'A2' ? '文字11px / 行22px / ヘッダー26px' : variant === 'A' ? '文字12px / 行24px' : '文字13px / 行30px'}</span><span className="toolbar-spacer" /><label className="scenario-label">表示状態<select value={scenario} aria-label="表示状態の見本" onChange={event => setScenario(event.target.value as Scenario)}><option value="normal">正常</option><option value="disconnected">未接続</option><option value="empty">データなし</option><option value="error">保存エラー</option></select></label><span className="demo-badge">デモ · 見本データ</span></div>
    <div className="workspace-bar"><span className="app-mark"><Icon name="devices" size={16} /></span><span className="workspace-title">計測ワークスペース</span><span className="toolbar-spacer" /><div className="layout-tabs" aria-label="レイアウト"><button className={layout === 'measurement' ? 'active' : ''} onClick={() => chooseLayout('measurement')}>測定</button><button className={layout === 'camera' ? 'active' : ''} onClick={() => chooseLayout('camera')}>カメラ</button><button className={layout === 'single' ? 'active' : ''} onClick={() => chooseLayout('single')}>1パネル</button></div><button onClick={addPanel}><Icon name="add" />パネル追加</button><button className="icon-button" title="現在のレイアウトを初期配置に戻す" aria-label="配置を初期状態に戻す" onClick={() => chooseLayout(layout)}><Icon name="reset" /></button></div>
    <main className="workspace">{maximized ? renderPanel(maximized) : <Split key={layoutRevision} tree={workspace.tree} renderPanel={renderPanel} onResize={(path, ratio) => setWorkspace(current => ({ ...current, tree: resizeSplit(current.tree, path, ratio) }))} />}</main>
    <footer className="workspace-footer"><span className="status-message">{status}</span><span className="footer-hint">左上でビュー切替 · 境界をドラッグしてサイズ変更</span><span className="numeric">{maximized ? 1 : Object.keys(workspace.panels).length} パネル</span></footer>
  </div>;
}
