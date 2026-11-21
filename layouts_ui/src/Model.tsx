// 3D views: a part as placed for parting, the modulus heatmap with hot spots and ingate candidates,
// and a built layout. Mold frame, mm, Z up (the editor's convention).
import { Component, Suspense, useEffect, useMemo, type ReactNode } from "react";
import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { Html, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

// glTF from OCCT is in metres and Y-up
const GLTF_TO_MM = new THREE.Matrix4().compose(
  new THREE.Vector3(), new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0)), new THREE.Vector3(1000, 1000, 1000));

class Skip extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? null : this.props.children; }
}

function Fit({ lo, hi }: { lo: number[]; hi: number[] }) {
  const { camera, controls } = useThree() as any;
  const key = [...lo, ...hi].map((x) => x.toFixed(0)).join(",");
  useEffect(() => {
    const c = new THREE.Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
    const r = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 100;
    camera.position.set(c.x + r * 1.5, c.y - r * 2.1, c.z + r * 1.6);
    camera.near = r / 200; camera.far = r * 50; camera.updateProjectionMatrix();
    controls?.target.copy(c); controls?.update();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export function Scene({ lo, hi, children }: { lo: number[]; hi: number[]; children: ReactNode }) {
  return (
    <Canvas camera={{ fov: 35, position: [400, -600, 400] }} gl={{ antialias: true }}>
      <color attach="background" args={["#eef1f4"]} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[3, -4, 6]} intensity={1.6} />
      <directionalLight position={[-4, 3, 2]} intensity={0.5} />
      <OrbitControls makeDefault />
      <Fit lo={lo} hi={hi} />
      <Suspense fallback={null}>{children}</Suspense>
    </Canvas>
  );
}

function Gltf({ url, color, matrix, opacity = 1 }: { url: string; color: string; matrix?: number[][]; opacity?: number }) {
  const { scene } = useGLTF(url);
  const obj = useMemo(() => {
    const o = scene.clone(true);
    o.traverse((c) => {
      if ((c as THREE.Mesh).isMesh) {
        (c as THREE.Mesh).material = new THREE.MeshStandardMaterial({
          color, metalness: 0.1, roughness: 0.65, transparent: opacity < 1, opacity, side: THREE.DoubleSide });
      }
    });
    return o;
  }, [scene, color, opacity]);
  const m = useMemo(() => {
    const M = new THREE.Matrix4();
    if (matrix) M.set(...(matrix.flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
    return M.multiply(GLTF_TO_MM);
  }, [matrix]);
  return <group matrixAutoUpdate={false} matrix={m}><primitive object={obj} /></group>;
}

/** The uploaded part moved as a parting suggestion would, with the parting plane at z = 0. */
export function PartingPreview({ url, move, lo, hi }: { url: string; move: number[][]; lo: number[]; hi: number[] }) {
  const s = Math.max(hi[0] - lo[0], hi[1] - lo[1]) * 1.4 || 200;
  return (
    <Scene lo={lo} hi={hi}>
      <Skip><Gltf url={url} color="#9fb3bd" matrix={move} /></Skip>
      <mesh position={[(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0]}>
        <planeGeometry args={[s, s]} />
        <meshBasicMaterial color="#2f7de1" transparent opacity={0.18} side={THREE.DoubleSide} />
      </mesh>
    </Scene>
  );
}

// blue (thin) -> red (heavy)
const RAMP = ["#2c5c9c", "#3fa7b5", "#e7d46a", "#e0742c", "#b3261e"].map((c) => new THREE.Color(c));
function ramp(t: number): THREE.Color {
  const x = Math.min(Math.max(t, 0), 1) * (RAMP.length - 1);
  const i = Math.min(Math.floor(x), RAMP.length - 2);
  return RAMP[i].clone().lerp(RAMP[i + 1], x - i);
}

export type Marker = { id: string; at: number[]; normal: number[]; disabled: boolean; flagged: boolean };

/** The part coloured by local modulus, its hot spots, and the ingate candidates (click one to switch it
 * off or on). In tag mode a click on the part reports the B-rep face under it. */
export function Heatmap({ mesh, hotSpots, markers, tagged, onMarker, onFace, range }: {
  mesh: { positions: number[]; triangles: number[]; faces: number[]; modulus: number[] };
  hotSpots: { id: string; position: number[]; radius: number }[]; markers: Marker[]; tagged: Set<number>;
  onMarker: (id: string) => void; onFace: ((face: number) => void) | null; range: [number, number];
}) {
  const geo = useMemo(() => {
    // unindexed, so a tagged face can be coloured on its own triangles
    const P = mesh.positions, T = mesh.triangles, F = mesh.faces, M = mesh.modulus;
    const n = T.length;
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    const tag = new THREE.Color("#7a4fd6");
    for (let k = 0; k < n; k++) {
      const v = T[k];
      pos.set([P[3 * v], P[3 * v + 1], P[3 * v + 2]], 3 * k);
      const c = tagged.has(F[Math.floor(k / 3)]) ? tag : ramp((M[v] - range[0]) / Math.max(range[1] - range[0], 1e-9));
      col.set([c.r, c.g, c.b], 3 * k);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.computeVertexNormals();
    return g;
  }, [mesh, tagged, range]);
  const lo = [0, 1, 2].map((i) => Math.min(...mesh.positions.filter((_, k) => k % 3 === i)));
  const hi = [0, 1, 2].map((i) => Math.max(...mesh.positions.filter((_, k) => k % 3 === i)));
  const size = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onFace || e.faceIndex == null) return;
    e.stopPropagation();
    onFace(mesh.faces[e.faceIndex]);
  };
  return (
    <Scene lo={lo} hi={hi}>
      <mesh geometry={geo} onClick={click}>
        <meshStandardMaterial vertexColors roughness={0.7} metalness={0.05} side={THREE.DoubleSide} transparent opacity={0.92} />
      </mesh>
      {hotSpots.map((h) => (
        <mesh key={h.id} position={h.position as [number, number, number]}>
          <sphereGeometry args={[Math.max(h.radius * 0.35, size * 0.006), 16, 12]} />
          <meshStandardMaterial color="#b3261e" emissive="#5a0f0a" />
        </mesh>
      ))}
      {markers.map((m) => {
        const L = size * 0.06;
        const dir = new THREE.Vector3(m.normal[0], m.normal[1], 0).normalize();
        const at = new THREE.Vector3(m.at[0], m.at[1], m.at[2]);
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().negate());
        const tip = at.clone().add(dir.clone().multiplyScalar(L));
        const colour = m.disabled ? "#9aa1a8" : m.flagged ? "#e0a800" : "#1a8a4a";
        return (
          <group key={m.id} onClick={(e) => { e.stopPropagation(); onMarker(m.id); }}>
            <mesh position={tip.clone().add(at).multiplyScalar(0.5)} quaternion={q}>
              <coneGeometry args={[L * 0.18, L, 12]} />
              <meshStandardMaterial color={colour} />
            </mesh>
            <Html position={tip.toArray()} center distanceFactor={size * 2.2} style={{ pointerEvents: "none" }}>
              <span style={{ fontSize: 11, fontFamily: "ui-monospace, monospace", color: colour, background: "rgba(255,255,255,.85)", padding: "0 3px", borderRadius: 3 }}>{m.id}</span>
            </Html>
          </group>
        );
      })}
    </Scene>
  );
}

/** A built layout: cavities in grey, the gating in orange. */
export function Built({ cavities, gating, lo, hi }: { cavities: string; gating: string; lo: number[]; hi: number[] }) {
  return (
    <Scene lo={lo} hi={hi}>
      <Skip><Gltf url={cavities} color="#9fb3bd" /></Skip>
      <Skip><Gltf url={gating} color="#d9641e" /></Skip>
    </Scene>
  );
}
