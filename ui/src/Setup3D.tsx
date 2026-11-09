// 3D tools for the guided set-up: the molding box and its parting plane, the
// part mover, and the plan view where the sprue and the runner are sketched on
// the parting plane.  Everything here is in the mold frame, mm, Z up.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useThree, type ThreeEvent } from "@react-three/fiber";
import { Html, Line, OrthographicCamera, TransformControls } from "@react-three/drei";
import * as THREE from "three";
import type { Vec3 } from "./api";

export type Box = { size: Vec3; parting: number; bottom: number; top: number; centre: Vec3 };
type XY = [number, number];

/** The box (wire edges, faint walls) and the parting plane across it. */
export function MoldBox({ box }: { box: Box }) {
  const [L, W, H] = box.size;
  const cz = (box.top + box.bottom) / 2;
  return (
    <group>
      <lineSegments position={[0, 0, cz]}>
        <edgesGeometry args={[new THREE.BoxGeometry(L, W, H)]} />
        <lineBasicMaterial color="#8d6e3f" />
      </lineSegments>
      <mesh position={[0, 0, cz]} raycast={() => null}>
        <boxGeometry args={[L, W, H]} />
        <meshBasicMaterial color="#c9a46a" transparent opacity={0.06} depthWrite={false} side={THREE.BackSide} />
      </mesh>
      <mesh raycast={() => null} renderOrder={1}>
        <planeGeometry args={[L, W]} />
        <meshBasicMaterial color="#3a7bd5" transparent opacity={0.12} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.PlaneGeometry(L, W)]} />
        <lineBasicMaterial color="#3a7bd5" />
      </lineSegments>
    </group>
  );
}

/** Wraps a part's meshes; while active the user drags it with the arrows, which
 * sit on the part's centre. On release the move is sent and the wrapper snaps
 * back (the new meshes arrive moved). */
export function PartMover({ active, centre, resetKey, onMove, children }: {
  active: boolean; centre: Vec3; resetKey: string; onMove: (delta: Vec3) => void; children: ReactNode;
}) {
  const ref = useRef<THREE.Group>(null!);
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  useEffect(() => {
    ref.current?.position.set(...centre);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, ...centre]);
  return (
    <>
      <group ref={ref} position={centre}>
        <group position={[-centre[0], -centre[1], -centre[2]]}>{children}</group>
      </group>
      {active && ready && (
        <TransformControls
          object={ref.current}
          mode="translate"
          size={0.9}
          onMouseUp={() => {
            const p = ref.current.position;
            const d: Vec3 = [Math.round(p.x - centre[0]), Math.round(p.y - centre[1]), Math.round(p.z - centre[2])];
            if (d.some((v) => v !== 0)) onMove(d);
            else p.set(...centre);
          }}
        />
      )}
    </>
  );
}

// ---- plan view ---------------------------------------------------------------------------

/** A camera straight down on the parting plane, framing the box; pan and zoom only. */
export function PlanCamera({ box }: { box: Box }) {
  const { size, controls } = useThree() as any;
  const cam = useRef<THREE.OrthographicCamera>(null!);
  const [L, W] = box.size;
  useEffect(() => {
    const c = cam.current;
    if (!c) return;
    c.position.set(0, 0, box.top + 2000);
    c.zoom = Math.min(size.width / (L * 1.25), size.height / (W * 1.25));
    c.updateProjectionMatrix();
    if (controls) {
      controls.target.set(0, 0, 0);
      controls.update();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [L, W, box.top, controls]);
  return <OrthographicCamera ref={cam} makeDefault up={[0, 1, 0]} near={1} far={20000} position={[0, 0, box.top + 2000]} />;
}

/** Where the part meets the parting plane, drawn over everything. */
export function PartOutline({ lines }: { lines: [number, number][][] }) {
  return (
    <>
      {lines.map((l, i) => (
        <Line key={i} raycast={() => null} points={l.map(([x, y]) => [x, y, 0.05] as Vec3)} color="#1d2733" lineWidth={1.5} depthTest={false} renderOrder={5} />
      ))}
    </>
  );
}

const SNAP_MM = 5;
const AXIS_DEG = 6;

/** Grid snap, and square to the previous point when within a few degrees of an axis; Alt: free. */
function snap(p: XY, prev: XY | null, free: boolean): XY {
  if (free) return [Math.round(p[0]), Math.round(p[1])];
  let x = Math.round(p[0] / SNAP_MM) * SNAP_MM;
  let y = Math.round(p[1] / SNAP_MM) * SNAP_MM;
  if (prev) {
    const a = (Math.atan2(Math.abs(y - prev[1]), Math.abs(x - prev[0])) * 180) / Math.PI;
    if (a < AXIS_DEG) y = prev[1];
    else if (a > 90 - AXIS_DEG) x = prev[0];
  }
  return [x, y];
}

const dist = (a: XY, b: XY) => Math.hypot(b[0] - a[0], b[1] - a[1]);

// ---- alignment: snap onto the x or y of what is already placed, and show it --------------------

/** Lines to line up with: the x and y of the sprue, runner points, ingate ends, part centres and edges. */
export type Guides = { xs: number[]; ys: number[] };
type Aligned = { p: XY; gx: number | null; gy: number | null };
const ALIGN_PX = 7;                                     // how close (on screen) counts as lined up

/** Grid snap and squaring to the previous point (snap), then onto a guide within ALIGN_PX on screen;
 * free (Alt/Option) does none of it. gx, gy: the guide lines it took (for drawing them). */
function align(raw: XY, prev: XY | null, free: boolean, guides: Guides | undefined, mmPerPx: number,
  skip?: XY | null): Aligned {
  if (free) return { p: [Math.round(raw[0]), Math.round(raw[1])], gx: null, gy: null };
  let [x, y] = snap(raw, prev, false);
  let gx: number | null = null, gy: number | null = null;
  if (prev && y === prev[1] && raw[1] !== prev[1]) gy = prev[1];
  if (prev && x === prev[0] && raw[0] !== prev[0]) gx = prev[0];
  const tol = ALIGN_PX * mmPerPx;
  const near = (v: number, list: number[], own?: number) => {
    let best: number | null = null;
    for (const g of list) {
      if (own !== undefined && Math.abs(g - own) < 1e-6) continue;   // the point's own place before the drag
      if (Math.abs(g - v) <= tol && (best === null || Math.abs(g - v) < Math.abs(best - v))) best = g;
    }
    return best;
  };
  const ax = near(raw[0], guides?.xs ?? [], skip?.[0]);
  const ay = near(raw[1], guides?.ys ?? [], skip?.[1]);
  if (ax !== null) { x = ax; gx = ax; }
  if (ay !== null) { y = ay; gy = ay; }
  return { p: [x, y], gx, gy };
}

/** The guide lines a point is lined up on, across the box. */
function GuideLines({ box, gx, gy }: { box: Box; gx: number | null; gy: number | null }) {
  const [L, W] = box.size;
  return (
    <>
      {gx !== null && <Line raycast={() => null} points={[[gx, -W / 2, 3], [gx, W / 2, 3]]} color="#d0309a" lineWidth={1} dashed dashSize={5} gapSize={4} depthTest={false} renderOrder={7} />}
      {gy !== null && <Line raycast={() => null} points={[[-L / 2, gy, 3], [L / 2, gy, 3]]} color="#d0309a" lineWidth={1} dashed dashSize={5} gapSize={4} depthTest={false} renderOrder={7} />}
    </>
  );
}

/** mm per screen pixel in the plan view's camera. */
function useMmPerPx(): number {
  const { camera } = useThree() as any;
  return camera?.isOrthographicCamera ? 1 / (camera.zoom || 1) : 1;
}
/** The middle of a segment, moved off it sideways by d (to its left), so a label clears the band. */
function beside(a: XY, b: XY, d: number): XY {
  const L = dist(a, b) || 1;
  return [(a[0] + b[0]) / 2 - ((b[1] - a[1]) / L) * d, (a[1] + b[1]) / 2 + ((b[0] - a[0]) / L) * d];
}
const v3 = (p: XY, z = 0.2): Vec3 => [p[0], p[1], z];

/** The runner's footprint on the parting plane: a band of width w along the points. */
function Band({ pts, w, color, opacity }: { pts: XY[]; w: number; color: string; opacity: number }) {
  return (
    <>
      {pts.slice(1).map((b, i) => {
        const a = pts[i];
        const L = dist(a, b);
        if (L < 1e-6) return null;
        return (
          <mesh key={i} raycast={() => null} position={v3([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], 0.3)}
            rotation={[0, 0, Math.atan2(b[1] - a[1], b[0] - a[0])]} renderOrder={4}>
            <planeGeometry args={[L, w]} />
            <meshBasicMaterial color={color} transparent opacity={opacity} depthTest={false} />
          </mesh>
        );
      })}
      {pts.slice(1, -1).map((p, i) => (
        <mesh key={`j${i}`} raycast={() => null} position={v3(p, 0.3)} renderOrder={4}>
          <circleGeometry args={[w / 2, 24]} />
          <meshBasicMaterial color={color} transparent opacity={opacity} depthTest={false} />
        </mesh>
      ))}
    </>
  );
}

function Label({ at, text }: { at: XY; text: string }) {
  return (
    <Html position={v3(at, 1)} center style={{ pointerEvents: "none" }} zIndexRange={[10, 0]}>
      <div className="dim">{text}</div>
    </Html>
  );
}

/** A round handle the user grabs; screen-size independent enough at plan zooms. */
function Handle({ at, r, color, onDown, onClick, title }: {
  at: XY; r: number; color: string; onDown?: (e: ThreeEvent<PointerEvent>) => void;
  onClick?: (e: ThreeEvent<MouseEvent>) => void; title?: string;
}) {
  return (
    <mesh position={v3(at, 2)} renderOrder={6} onPointerDown={onDown} userData={{ title }}
      onClick={(e) => { e.stopPropagation(); onClick?.(e); }}>
      <circleGeometry args={[r, 24]} />
      {/* transparent so it draws in the same pass as the bands and planes, after them */}
      <meshBasicMaterial color={color} depthTest={false} transparent opacity={1} />
    </mesh>
  );
}

export type SprueSketch = { at: XY | null; dTop: number; dBottom: number; canPlace: boolean; onPlace: (p: XY) => void };
export type RunnerSketch = {
  width: number;                // at the parting plane
  points: XY[];                 // after the start under the sprue
  drawing: boolean;
  onAdd: (p: XY) => void;
  onFinish: (at?: XY) => void;     // at: where a double-click finished it
  onChange: (points: XY[]) => void;   // an edit of the placed runner (drag, insert, delete)
  start?: XY | null;                  // where it starts (a branch point); default the sprue; null: not chosen yet
  selected: number | null;
  onSelect: (i: number | null) => void;
};

export type OtherRunner = { id: string; start: XY; points: XY[]; width: number };

/** Plan-view sketching on the parting plane: drop and drag the sprue, draw and edit a
 * runner; the sprue's other runners are shown faint, a click on one picks it. */
export function PlanSketch({ box, sprue, runner, others = [], onPickOther, onCursor, guides }: {
  box: Box; sprue: SprueSketch; runner: RunnerSketch | null; others?: OtherRunner[];
  onPickOther?: (id: string) => void; onCursor: (p: XY | null) => void; guides?: Guides;
}) {
  const { controls } = useThree() as any;
  const mmPerPx = useMmPerPx();
  const [lined, setLined] = useState<{ gx: number | null; gy: number | null }>({ gx: null, gy: null });
  const [L, W] = box.size;
  const [cursor, setCursor] = useState<XY | null>(null);
  // The drag lives in a ref: a quick click can end (pointerup) before React has re-rendered
  // after it began, so the end must not depend on state from that render.
  type Drag = { what: "sprue" } | { what: "point"; i: number };
  const live = useRef<{ drag: Drag | null; at: XY | null; from: XY | null; edit: XY[] | null }>(
    { drag: null, at: null, from: null, edit: null });
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);
  const { drag, at: dragAt, edit } = live.current;

  const own = runner?.start === undefined ? sprue.at : runner.start;     // the sprue, or a branch point
  const start: XY | null = drag?.what === "sprue" && dragAt && runner?.start === undefined ? dragAt : own;
  const base = edit ?? runner?.points ?? [];
  const pts = base.map((p, i) => (drag?.what === "point" && drag.i === i && dragAt ? dragAt : p));
  const chain: XY[] = start ? [start, ...pts] : pts;
  const last = chain.length ? chain[chain.length - 1] : null;

  const beginDrag = (e: ThreeEvent<PointerEvent>, d: Drag, at: XY, points?: XY[]) => {
    e.stopPropagation();
    if (controls) controls.enabled = false;
    live.current = { drag: d, at, from: at, edit: d.what === "point" ? points ?? runner?.points ?? null : null };
    redraw();
  };
  const endDrag = () => {
    const g = live.current;
    if (!g.drag) return;
    const inserted = !!g.edit && !!runner && g.edit.length !== runner.points.length;   // a bend from a white dot
    const moved = !!g.at && (inserted || !g.from || dist(g.at, g.from) > 0.5);
    if (moved && g.at) {
      const d = g.drag, p = g.at;
      if (d.what === "sprue") sprue.onPlace(p);
      else if (runner && g.edit) runner.onChange(g.edit.map((q, i) => (i === d.i ? p : q)));
    }
    live.current = { drag: null, at: null, from: null, edit: null };
    if (controls) controls.enabled = true;
    redraw();
  };
  const endRef = useRef(endDrag);
  endRef.current = endDrag;
  useEffect(() => {
    const up = () => endRef.current();
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);

  const aligned = (e: ThreeEvent<PointerEvent | MouseEvent>, prev: XY | null, skip?: XY | null) =>
    align([e.point.x, e.point.y], prev, e.nativeEvent.altKey, guides, mmPerPx, skip);
  const at = (e: ThreeEvent<PointerEvent | MouseEvent>, prev: XY | null): XY => aligned(e, prev).p;

  const drawingNow = !!runner?.drawing;
  const ghostSprue = !drag && !sprue.at && sprue.canPlace ? cursor : drag?.what === "sprue" ? dragAt : null;
  const segs = chain.slice(1).map((b, i) => [chain[i], b] as [XY, XY]);

  return (
    <>
      {/* the sketch surface: the parting plane inside the box */}
      <mesh
        position={[0, 0, 0.1]}
        onPointerMove={(e) => {
          const prev = drag?.what === "point" ? chain[drag.i] : drawingNow ? last : null;
          const a = aligned(e, drag ? (drag.what === "point" ? prev : null) : prev, live.current.from);
          const p = a.p;
          setLined({ gx: a.gx, gy: a.gy });
          if (drag) { live.current.at = p; redraw(); }
          setCursor(p);
          onCursor(p);
        }}
        onPointerOut={() => { setCursor(null); onCursor(null); setLined({ gx: null, gy: null }); }}
        onClick={(e) => {
          if (e.delta > 4) return;                                   // a pan, not a click
          const p = at(e, drawingNow ? last : null);
          if (!sprue.at && sprue.canPlace) sprue.onPlace(p);
          else if (drawingNow && runner && (!last || dist(p, last) > 0.5)) runner.onAdd(p);
          else runner?.onSelect(null);
        }}
        onDoubleClick={(e) => drawingNow && runner?.onFinish(at(e, last))}
      >
        <planeGeometry args={[L, W]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* the sprue: ghost while placing or dragging, a grab handle once placed */}
      {ghostSprue && (
        <group>
          <mesh raycast={() => null} position={v3(ghostSprue, 1)} renderOrder={5}>
            <ringGeometry args={[sprue.dBottom / 2, sprue.dTop / 2, 40]} />
            <meshBasicMaterial color="#e08a3c" transparent opacity={0.55} depthTest={false} />
          </mesh>
          <mesh raycast={() => null} position={v3(ghostSprue, 1)} renderOrder={5}>
            <circleGeometry args={[sprue.dBottom / 2, 40]} />
            <meshBasicMaterial color="#e08a3c" transparent opacity={0.3} depthTest={false} />
          </mesh>
        </group>
      )}
      {sprue.at && (
        <Handle at={drag?.what === "sprue" && dragAt ? dragAt : sprue.at!} r={Math.max(sprue.dTop / 2, 8)} color={drag?.what === "sprue" ? "#c76a1c" : "#e08a3c"}
          onDown={(e) => beginDrag(e, { what: "sprue" }, sprue.at!)} title="sprue" />
      )}

      {(drag || drawingNow || ghostSprue) && <GuideLines box={box} gx={lined.gx} gy={lined.gy} />}

      {/* the other runners: faint; a click on one of their points picks it */}
      {others.map((o) => (
        <group key={o.id}>
          <Band pts={[o.start, ...o.points]} w={o.width} color="#b98a5e" opacity={0.3} />
          {!drawingNow && o.points.map((p, i) => (
            <Handle key={i} at={p} r={Math.max(o.width / 4, 4)} color="#8a94a3"
              onDown={(e) => e.stopPropagation()} onClick={() => onPickOther?.(o.id)} />
          ))}
        </group>
      ))}

      {/* the runner */}
      {runner && chain.length > 1 && (
        <Band pts={chain} w={runner.width} color="#e08a3c" opacity={drawingNow || drag ? 0.6 : 0.35} />
      )}
      {runner && drawingNow && last && cursor && dist(last, cursor) > 0.5 && (
        <>
          <Band pts={[last, cursor]} w={runner.width} color="#e08a3c" opacity={0.25} />
          <Line raycast={() => null} points={[v3(last, 1.5), v3(cursor, 1.5)]} color="#c76a1c" lineWidth={1.5} dashed dashSize={4} gapSize={3} depthTest={false} renderOrder={6} />
          <Label at={beside(last, cursor, runner.width / 2 + 9)} text={`${dist(last, cursor).toFixed(0)} mm`} />
        </>
      )}
      {runner && segs.map(([a, b], i) => (
        <Label key={`l${i}`} at={beside(a, b, runner.width / 2 + 9)} text={`${dist(a, b).toFixed(0)} mm`} />
      ))}
      {runner && !drawingNow && !drag && segs.map(([a, b], i) => (
        // the middle of each segment: grab it to put a bend there
        <Handle key={`m${i}`} at={[(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]} r={Math.max(runner.width / 5, 3)} color="#ffffff"
          onDown={(e) => {
            const mid: XY = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
            beginDrag(e, { what: "point", i }, mid, [...runner.points.slice(0, i), mid, ...runner.points.slice(i)]);
          }} title="add a bend" />
      ))}
      {runner && pts.map((p, i) => (
        <Handle key={`p${i}`} at={p} r={Math.max(runner.width / 3, 5)}
          color={runner.selected === i ? "#0b3d91" : "#2f7de1"}
          onDown={(e) => { if (!drawingNow) { runner.onSelect(i); beginDrag(e, { what: "point", i }, p); } }} />
      ))}
    </>
  );
}

// ---- ingates: dragged from a runner to the part --------------------------------------------

export type RunnerLine = { id: string; chain: XY[]; width: number };
export type IngateLine = { id: string; runner: string; start: XY; end: XY; width: number };

/** The nearest point of a polyline to p, and how far away it is. */
export function nearestOn(chain: XY[], p: XY): { at: XY; d: number } {
  let best = { at: chain[0], d: Infinity };
  for (let i = 1; i < chain.length; i++) {
    const [a, b] = [chain[i - 1], chain[i]];
    const L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2));
    const q: XY = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    const d = dist(p, q);
    if (d < best.d) best = { at: q, d };
  }
  return best;
}

const OUTLINE_SNAP_MM = 8;

/** Plan-view ingates: press on a runner and drag to the part's edge (the end snaps onto the
 * part's outline); drag either end of a placed ingate to move it; a click picks it. */
export function IngateSketch({ box, runners, ingates, width, outline, selected, onSelect, onCreate, onEdit, onCursor, guides }: {
  box: Box; runners: RunnerLine[]; ingates: IngateLine[]; width: number | null; outline: XY[][];
  selected: string | null; onSelect: (id: string | null) => void;
  onCreate: (runner: string, start: XY, end: XY) => void;
  onEdit: (id: string, runner: string, start: XY, end: XY) => void;
  onCursor: (p: XY | null) => void; guides?: Guides;
}) {
  const { controls } = useThree() as any;
  const mmPerPx = useMmPerPx();
  const [lined, setLined] = useState<{ gx: number | null; gy: number | null }>({ gx: null, gy: null });
  const [L, W] = box.size;
  type Drag = { what: "new" | "start" | "end"; id?: string; runner: string; start: XY; end: XY };
  const live = useRef<Drag | null>(null);
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);

  const onRunner = (p: XY): { runner: string; at: XY } | null => {
    let best: { runner: string; at: XY; d: number } | null = null;
    for (const r of runners) {
      const n = nearestOn(r.chain, p);
      if (n.d <= r.width / 2 + 4 && (!best || n.d < best.d)) best = { runner: r.id, at: n.at, d: n.d };
    }
    return best && { runner: best.runner, at: [Math.round(best.at[0]), Math.round(best.at[1])] };
  };
  const toPart = (p: XY, from: XY, free: boolean): XY => {
    let best: { at: XY; d: number } | null = null;
    for (const l of outline) {
      const n = nearestOn(l, p);
      if (n.d <= OUTLINE_SNAP_MM && (!best || n.d < best.d)) best = n;
    }
    if (best) { setLined({ gx: null, gy: null }); return [Math.round(best.at[0] * 10) / 10, Math.round(best.at[1] * 10) / 10]; }
    const a = align(p, from, free, guides, mmPerPx);
    setLined({ gx: a.gx, gy: a.gy });
    return a.p;
  };

  const begin = (e: ThreeEvent<PointerEvent>, d: Drag) => {
    e.stopPropagation();
    if (controls) controls.enabled = false;
    live.current = d;
    onSelect(d.id ?? null);
    redraw();
  };
  const end = () => {
    const d = live.current;
    if (!d) return;
    live.current = null;
    if (controls) controls.enabled = true;
    if (dist(d.start, d.end) > 3) {
      if (d.what === "new") onCreate(d.runner, d.start, d.end);
      else {
        const g = ingates.find((x) => x.id === d.id)!;
        if (dist(g.start, d.start) > 0.5 || dist(g.end, d.end) > 0.5 || g.runner !== d.runner) onEdit(d.id!, d.runner, d.start, d.end);
      }
    }
    redraw();
  };
  const endRef = useRef(end);
  endRef.current = end;
  useEffect(() => {
    const up = () => endRef.current();
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);

  const d = live.current;
  const shown = ingates.map((g) => (d && d.id === g.id ? { ...g, start: d.start, end: d.end, runner: d.runner } : g));

  return (
    <>
      <mesh
        position={[0, 0, 0.1]}
        onPointerDown={(e) => {
          if (!width || e.nativeEvent.button !== 0) return;
          const p: XY = [e.point.x, e.point.y];
          const r = onRunner(p);
          if (r) begin(e, { what: "new", runner: r.runner, start: r.at, end: r.at });
        }}
        onPointerMove={(e) => {
          const p: XY = [e.point.x, e.point.y];
          onCursor([Math.round(p[0]), Math.round(p[1])]);
          const g = live.current;
          if (!g) return;
          if (g.what === "start") {
            const r = onRunner(p);
            if (r) { g.start = r.at; g.runner = r.runner; }
          } else {
            g.end = toPart(p, g.start, e.nativeEvent.altKey);
          }
          redraw();
        }}
        onPointerOut={() => onCursor(null)}
        onClick={(e) => { if (e.delta <= 4 && !live.current) onSelect(null); }}
      >
        <planeGeometry args={[L, W]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>

      {live.current && <GuideLines box={box} gx={lined.gx} gy={lined.gy} />}
      {shown.map((g) => (
        <group key={g.id}>
          <Band pts={[g.start, g.end]} w={g.width} color={g.id === selected ? "#1f8a55" : "#3aa76d"} opacity={0.75} />
          <Label at={beside(g.start, g.end, g.width / 2 + 9)} text={`${dist(g.start, g.end).toFixed(0)} mm`} />
          <Handle at={g.start} r={Math.max(g.width / 5, 4)} color="#ffffff"
            onDown={(e) => begin(e, { what: "start", id: g.id, runner: g.runner, start: g.start, end: g.end })} />
          <Handle at={g.end} r={Math.max(g.width / 5, 4)} color={g.id === selected ? "#0b5d33" : "#1f8a55"}
            onDown={(e) => begin(e, { what: "end", id: g.id, runner: g.runner, start: g.start, end: g.end })} />
        </group>
      ))}
      {d && d.what === "new" && width && dist(d.start, d.end) > 0.5 && (
        <>
          <Band pts={[d.start, d.end]} w={width} color="#3aa76d" opacity={0.5} />
          <Label at={beside(d.start, d.end, width / 2 + 9)} text={`${dist(d.start, d.end).toFixed(0)} mm`} />
        </>
      )}
    </>
  );
}

// ---- parts dragged in plan view, lining up with each other ----------------------------------------

export type PartFoot = { id: string; name: string; lo: XY; hi: XY };

/** In plan view a part is dragged by its footprint; its centre or an edge snaps onto another part's
 * centre or edge, the box's centre lines or walls, within a few pixels; the lines it took are shown. */
export function PartSketch({ box, parts, selected, onSelect, onMove, guides }: {
  box: Box; parts: PartFoot[]; selected: string | null; onSelect: (id: string) => void;
  onMove: (id: string, d: Vec3) => void; guides?: Guides;
}) {
  const { controls } = useThree() as any;
  const mmPerPx = useMmPerPx();
  const [L, W] = box.size;
  const live = useRef<{ id: string; from: XY; d: XY; gx: number | null; gy: number | null } | null>(null);
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);

  const lineUp = (p: PartFoot, raw: XY, free: boolean): { d: XY; gx: number | null; gy: number | null } => {
    if (free) return { d: [Math.round(raw[0]), Math.round(raw[1])], gx: null, gy: null };
    const others = parts.filter((q) => q.id !== p.id);
    const xs = [0, -L / 2, L / 2, ...(guides?.xs ?? []), ...others.flatMap((q) => [q.lo[0], q.hi[0], (q.lo[0] + q.hi[0]) / 2])];
    const ys = [0, -W / 2, W / 2, ...(guides?.ys ?? []), ...others.flatMap((q) => [q.lo[1], q.hi[1], (q.lo[1] + q.hi[1]) / 2])];
    const tol = ALIGN_PX * mmPerPx;
    const fit = (feats: number[], lines: number[], d: number): [number, number | null] => {
      let best: [number, number | null] = [Math.round(d), null];
      let gap = tol;
      for (const f of feats) for (const g of lines) {
        const e = Math.abs(f + d - g);
        if (e <= gap) { gap = e; best = [g - f, g]; }
      }
      return best;
    };
    const [dx, gx] = fit([p.lo[0], (p.lo[0] + p.hi[0]) / 2, p.hi[0]], xs, raw[0]);
    const [dy, gy] = fit([p.lo[1], (p.lo[1] + p.hi[1]) / 2, p.hi[1]], ys, raw[1]);
    return { d: [dx, dy], gx, gy };
  };

  const end = () => {
    const g = live.current;
    if (!g) return;
    live.current = null;
    if (controls) controls.enabled = true;
    if (Math.hypot(g.d[0], g.d[1]) > 0.5) onMove(g.id, [g.d[0], g.d[1], 0]);
    redraw();
  };
  const endRef = useRef(end);
  endRef.current = end;
  useEffect(() => {
    const up = () => endRef.current();
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);

  const g = live.current;
  return (
    <>
      <mesh position={[0, 0, box.top + 2]}
        onPointerMove={(e) => {
          const c = live.current;
          if (!c) return;
          const p = parts.find((q) => q.id === c.id)!;
          const r = lineUp(p, [e.point.x - c.from[0], e.point.y - c.from[1]], e.nativeEvent.altKey);
          live.current = { ...c, d: r.d, gx: r.gx, gy: r.gy };
          redraw();
        }}>
        <planeGeometry args={[L * 3, W * 3]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {parts.map((p) => {
        const [w, h] = [p.hi[0] - p.lo[0], p.hi[1] - p.lo[1]];
        const d = g && g.id === p.id ? g.d : [0, 0];
        const c: Vec3 = [(p.lo[0] + p.hi[0]) / 2 + d[0], (p.lo[1] + p.hi[1]) / 2 + d[1], box.top + 3];
        const on = p.id === selected;
        return (
          <group key={p.id}>
            <mesh position={c} renderOrder={6}
              onPointerDown={(e) => {
                e.stopPropagation();
                if (controls) controls.enabled = false;
                onSelect(p.id);
                live.current = { id: p.id, from: [e.point.x, e.point.y], d: [0, 0], gx: null, gy: null };
                redraw();
              }}>
              <planeGeometry args={[w, h]} />
              <meshBasicMaterial color="#2f7de1" transparent opacity={g?.id === p.id ? 0.18 : on ? 0.1 : 0.02} depthTest={false} />
            </mesh>
            <Line raycast={() => null} color={on ? "#2f7de1" : "#8a94a3"} lineWidth={on ? 2 : 1} depthTest={false} renderOrder={7}
              dashed={g?.id === p.id} dashSize={6} gapSize={4}
              points={[[c[0] - w / 2, c[1] - h / 2, c[2]], [c[0] + w / 2, c[1] - h / 2, c[2]], [c[0] + w / 2, c[1] + h / 2, c[2]],
                       [c[0] - w / 2, c[1] + h / 2, c[2]], [c[0] - w / 2, c[1] - h / 2, c[2]]]} />
          </group>
        );
      })}
      {g && <GuideLines box={box} gx={g.gx} gy={g.gy} />}
      {!g && <Lined box={box} parts={parts} />}
      {g && <Label at={[(parts.find((q) => q.id === g.id)!.lo[0] + parts.find((q) => q.id === g.id)!.hi[0]) / 2 + g.d[0],
                         parts.find((q) => q.id === g.id)!.hi[1] + g.d[1] + 12]}
        text={`moved ${g.d[0].toFixed(0)}, ${g.d[1].toFixed(0)} mm`} />}
    </>
  );
}

/** Where parts already line up (a centre or an edge at the same x or y): faint lines through them. */
function Lined({ box, parts }: { box: Box; parts: PartFoot[] }) {
  const [L, W] = box.size;
  const xs = new Set<number>(), ys = new Set<number>();
  const fx = (p: PartFoot) => [p.lo[0], (p.lo[0] + p.hi[0]) / 2, p.hi[0]];
  const fy = (p: PartFoot) => [p.lo[1], (p.lo[1] + p.hi[1]) / 2, p.hi[1]];
  for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) {
    for (const a of fx(parts[i])) for (const b of fx(parts[j])) if (Math.abs(a - b) < 0.5) xs.add(Math.round(a * 10) / 10);
    for (const a of fy(parts[i])) for (const b of fy(parts[j])) if (Math.abs(a - b) < 0.5) ys.add(Math.round(a * 10) / 10);
  }
  return (
    <>
      {[...xs].map((x) => <Line key={`x${x}`} raycast={() => null} points={[[x, -W / 2, 3], [x, W / 2, 3]]} color="#1aa260" lineWidth={1} dashed dashSize={3} gapSize={5} transparent opacity={0.7} depthTest={false} renderOrder={6} />)}
      {[...ys].map((y) => <Line key={`y${y}`} raycast={() => null} points={[[-L / 2, y, 3], [L / 2, y, 3]]} color="#1aa260" lineWidth={1} dashed dashSize={3} gapSize={5} transparent opacity={0.7} depthTest={false} renderOrder={6} />)}
    </>
  );
}
