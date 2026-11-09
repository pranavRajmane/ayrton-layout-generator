import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { GizmoHelper, GizmoViewport, Grid, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import type { DesignState, MeshRef, Vec3 } from "./api";
import {
  IngateSketch, MoldBox, PartMover, PartOutline, PartSketch, PlanCamera, PlanSketch,
  type Box, type Guides, type IngateLine, type OtherRunner, type RunnerLine, type RunnerSketch, type SprueSketch,
} from "./Setup3D";

/** What the guided set-up has the viewer do: pick and move parts in the box, or
 * sketch the sprue and runner on the parting plane (in plan view). */
export type SetupView =
  | { mode: "none" }
  | { mode: "fit"; part: string; onSelectPart: (id: string) => void; onMovePart: (d: Vec3, id?: string) => void; guides?: Guides }
  | { mode: "preview"; move: number[][]; box: Box | null }        // a suggested fit, shown before it is applied
  | { mode: "plan"; sprue: SprueSketch; runner: RunnerSketch | null; hint: string;
      others: OtherRunner[]; onPickOther: (id: string) => void; guides?: Guides }
  | { mode: "ingates"; hint: string; runners: RunnerLine[]; ingates: IngateLine[]; width: number | null;
      selected: string | null; onSelect: (id: string | null) => void;
      onCreate: (runner: string, start: [number, number], end: [number, number]) => void;
      onEdit: (id: string, runner: string, start: [number, number], end: [number, number]) => void; guides?: Guides };

/** The part (main "part" or a cavity id) a part or core mesh belongs to. */
function partOf(name: string): string {
  const bits = name.split(".");
  if (bits[0] === "part") return bits[1] ?? "part";
  return bits.length > 2 ? bits[2] : "part";              // core.<id> / core.<id>.<cavity>
}

const COLORS: Record<string, string> = { part: "#9aa4b1", gating: "#e08a3c", core: "#d8c38a", mold: "#c9b28f" };
THREE.Object3D.DEFAULT_UP.set(0, 0, 1); // the mold frame is Z-up

// a mesh that fails to load (a design session the API no longer has) is left out, not the whole view
class SkipOnError extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function Solid({ mesh, selected, onPick }: { mesh: MeshRef; selected: boolean; onPick: ((p: Vec3) => void) | null }) {
  const { scene } = useGLTF(mesh.url);
  const obj = useMemo(() => {
    const o = scene.clone(true);
    const base = COLORS[mesh.kind] ?? "#888";
    o.traverse((c) => {
      if ((c as THREE.Mesh).isMesh) {
        (c as THREE.Mesh).material = new THREE.MeshStandardMaterial({
          color: selected ? "#2f7de1" : base,
          metalness: 0.1,
          roughness: 0.7,
          transparent: mesh.kind === "part",
          opacity: mesh.kind === "part" ? 0.55 : 1,
          side: THREE.DoubleSide,
        });
      }
    });
    return o;
  }, [scene, selected, mesh.kind]);
  return (
    <primitive
      object={obj}
      onClick={
        onPick &&
        ((e: any) => {
          e.stopPropagation();
          onPick([e.point.x, e.point.y, e.point.z]);
        })
      }
    />
  );
}

function PortMarkers({ state }: { state: DesignState }) {
  const arrows = useMemo(() => {
    const out: THREE.ArrowHelper[] = [];
    for (const p of state.measurement_planes) {
      const len = p.section ? Math.max(8, Math.sqrt(p.section.area)) : 10;
      out.push(new THREE.ArrowHelper(new THREE.Vector3(...p.normal), new THREE.Vector3(...p.origin), len, 0x1aa260));
    }
    for (const f of Object.values(state.part?.ports ?? {})) {
      out.push(new THREE.ArrowHelper(new THREE.Vector3(...f.normal), new THREE.Vector3(...f.origin), 15, 0xb03ab0));
    }
    return out;
  }, [state]);
  return (
    <>
      {arrows.map((a, i) => (
        <primitive key={i} object={a} />
      ))}
    </>
  );
}

/** Frame the part (and every element) once per opened design. */
function Fit({ state, topView, extra = [] }: { state: DesignState; topView: number; extra?: Vec3[] }) {
  const { camera, controls } = useThree() as any;
  const extraKey = JSON.stringify(extra);
  useEffect(() => {
    const pts: THREE.Vector3[] = [];
    extra.forEach((p) => pts.push(new THREE.Vector3(...p)));
    if (extra.length) {
      // a preview: frame what is previewed, not where the part is now
    } else if (state.part) state.part.bbox.forEach((p) => pts.push(new THREE.Vector3(...p)));
    state.measurement_planes.forEach((p) => pts.push(new THREE.Vector3(...p.origin)));
    if (state.flask) state.flask.forEach((p) => pts.push(new THREE.Vector3(...p)));
    if (!pts.length || !controls) return;
    const box = new THREE.Box3().setFromPoints(pts);
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 20);
    if (topView > 0) camera.position.set(c.x, c.y - 0.02 * r, c.z + 2.4 * r);   // straight down (a hair off, for the orbit)
    else camera.position.set(c.x + 1.6 * r, c.y - 2.0 * r, c.z + 1.4 * r);
    camera.near = r / 100;
    camera.far = r * 100;
    camera.updateProjectionMatrix();
    controls.target.copy(c);
    controls.update();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.id, controls, state.box === null, topView, camera, extraKey]);
  return null;
}

/** Triangles (flat x, y, z lists, mold frame) drawn over the part: picked or flagged faces. */
function Overlay({ meshes, color }: { meshes: number[][]; color: string }) {
  const geo = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(meshes.flat(), 3));
    g.computeVertexNormals();
    return g;
  }, [meshes]);
  if (!meshes.length) return null;
  return (
    <mesh geometry={geo} renderOrder={2}>
      <meshBasicMaterial color={color} side={THREE.DoubleSide} transparent opacity={0.75} depthTest={false} />
    </mesh>
  );
}

export default function Viewer({
  state, selected, onSelect, onPartClick, overlays = [], setup = { mode: "none" }, plan = false, onPlan,
}: {
  state: DesignState; selected: string | null; onSelect: (inst: string | null) => void;
  onPartClick?: (p: Vec3) => void; overlays?: { meshes: number[][]; color: string }[]; setup?: SetupView;
  plan?: boolean; onPlan?: (plan: boolean) => void;
}) {
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const busyMode = setup.mode !== "none";                 // clicks go to the set-up tool, not to picking
  const isPart = (m: MeshRef) => m.kind === "part" || m.kind === "core";
  const solids = (ms: MeshRef[]) =>
    ms.map((m) => {
      const inst = m.name.startsWith("gating.") ? m.name.slice(7) : null;
      let pick: ((p: Vec3) => void) | null = (p: Vec3) => (m.kind === "part" && onPartClick ? onPartClick(p) : onSelect(inst ?? null));
      if (setup.mode === "fit") pick = isPart(m) ? () => setup.onSelectPart(partOf(m.name)) : null;
      else if (busyMode) pick = null;
      return (
        <SkipOnError key={m.url}>
          <Solid mesh={m} selected={(inst !== null && inst === selected) || (setup.mode === "fit" && isPart(m) && partOf(m.name) === setup.part && (state.parts?.length ?? 0) > 1)} onPick={pick} />
        </SkipOnError>
      );
    });
  const shown = state.meshes.filter((m) => m.kind !== "mold");
  const box = state.box as Box | null;
  const planOn = plan && !!box;
  const moving = setup.mode === "fit" ? setup.part : setup.mode === "preview" ? "part" : null;
  const movingPart = state.parts?.find((p) => p.id === moving) ?? null;
  const moved = shown.filter((m) => isPart(m) && partOf(m.name) === moving);
  const centre: Vec3 = movingPart ? (movingPart.bbox[0].map((v, i) => (v + movingPart.bbox[1][i]) / 2) as Vec3) : [0, 0, 0];
  return (
    <div className="viewer">
      <Canvas camera={{ position: [400, -500, 350], up: [0, 0, 1], near: 1, far: 20000 }} onPointerMissed={() => onSelect(null)}>
        <ambientLight intensity={0.6} />
        <directionalLight position={[300, -200, 600]} intensity={1.6} />
        <directionalLight position={[-300, 300, -200]} intensity={0.5} />
        {planOn && <PlanCamera box={box!} />}
        <Suspense fallback={null}>
          {/* glTF from OCCT is in metres and Y-up: back to mm, Z-up */}
          {setup.mode === "preview" ? (
            <group matrixAutoUpdate={false} matrix={new THREE.Matrix4().set(...(setup.move.flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]))}>
              <group rotation={[Math.PI / 2, 0, 0]} scale={1000}>
                {solids(moved)}
              </group>
            </group>
          ) : (
            <PartMover active={setup.mode === "fit" && !!movingPart && !planOn} centre={centre} resetKey={moved.map((m) => m.url).join("|")}
              onMove={(d) => setup.mode === "fit" && setup.onMovePart(d)}>
              <group rotation={[Math.PI / 2, 0, 0]} scale={1000}>
                {solids(moved)}
              </group>
            </PartMover>
          )}
          <group rotation={[Math.PI / 2, 0, 0]} scale={1000}>
            {solids(shown.filter((m) => !moved.includes(m)))}
          </group>
        </Suspense>
        {!planOn && <PortMarkers state={state} />}
        {overlays.map((o, i) => (
          <Overlay key={i} meshes={o.meshes} color={o.color} />
        ))}
        {setup.mode === "preview" ? (
          setup.box && <MoldBox box={setup.box} />
        ) : box ? (
          <MoldBox box={box} />
        ) : (
          state.flask && (
            <mesh position={state.flask[0].map((v, i) => (v + state.flask![1][i]) / 2) as any}>
              <boxGeometry args={state.flask[0].map((v, i) => state.flask![1][i] - v) as any} />
              <meshBasicMaterial color="#8d6e3f" wireframe />
            </mesh>
          )
        )}
        {box && (planOn || setup.mode === "plan" || setup.mode === "ingates") && <PartOutline lines={state.parting_outline ?? []} />}
        {box && planOn && setup.mode === "fit" && (
          <PartSketch box={box} selected={setup.part} onSelect={setup.onSelectPart} guides={setup.guides}
            onMove={(id, d) => setup.onMovePart(d, id)}
            parts={(state.parts ?? []).map((p) => ({ id: p.id, name: p.name, lo: [p.bbox[0][0], p.bbox[0][1]], hi: [p.bbox[1][0], p.bbox[1][1]] }))} />
        )}
        {box && setup.mode === "ingates" && (
          <IngateSketch box={box} runners={setup.runners} ingates={setup.ingates} width={setup.width}
            outline={state.parting_outline ?? []} selected={setup.selected} onSelect={setup.onSelect}
            onCreate={setup.onCreate} onEdit={setup.onEdit} onCursor={setCursor} guides={setup.guides} />
        )}
        {box && setup.mode === "plan" && (
          <PlanSketch box={box} sprue={setup.sprue} runner={setup.runner} others={setup.others}
            onPickOther={setup.onPickOther} onCursor={setCursor} guides={setup.guides} />
        )}
        <Grid
          rotation={[Math.PI / 2, 0, 0]}
          args={[2000, 2000]}
          cellSize={10}
          sectionSize={100}
          cellColor="#c9ced6"
          sectionColor="#8d96a3"
          fadeDistance={planOn ? 100000 : 2500}
          infiniteGrid
        />
        <OrbitControls
          makeDefault
          enableRotate={!planOn}
          zoomToCursor={planOn}
          mouseButtons={planOn ? { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN } : undefined}
        />
        {!planOn && (
          <Fit state={state} topView={0} extra={setup.mode === "preview" && setup.box
            ? [[-setup.box.size[0] / 2, -setup.box.size[1] / 2, setup.box.bottom], [setup.box.size[0] / 2, setup.box.size[1] / 2, setup.box.top]]
            : []} />
        )}
        {!planOn && (
          <GizmoHelper alignment="bottom-right" margin={[70, 70]}>
            <GizmoViewport />
          </GizmoHelper>
        )}
      </Canvas>
      {box && onPlan && (
        <div className="viewtoggle">
          <button className={planOn ? "on" : ""} onClick={() => onPlan(true)}>Plan</button>
          <button className={!planOn ? "on" : ""} onClick={() => onPlan(false)}>3D</button>
        </div>
      )}
      {(setup.mode === "plan" || setup.mode === "ingates") && setup.hint && <div className="viewhint">{setup.hint}</div>}
      {setup.mode === "fit" && planOn && <div className="viewhint">Drag a part to move it · it snaps to line up with the other parts and the box · hold {/Mac/.test(navigator.platform) ? "Option ⌥" : "Alt"} for free</div>}
      {planOn && cursor && <div className="viewcursor">x {cursor[0].toFixed(0)} · y {cursor[1].toFixed(0)} mm</div>}
    </div>
  );
}
