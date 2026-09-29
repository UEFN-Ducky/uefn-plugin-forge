import * as THREE from "three";
const geometries = new Map();
let lastYield = performance.now();
async function yieldStep() {
  if (typeof document === "undefined" || performance.now() - lastYield < 8) return;
  await new Promise(resolve => setTimeout(resolve, 0));
  lastYield = performance.now();
}
async function buffer(text) {
  const size = text.length * 3 / 4 - (text.endsWith("==") ? 2 : text.endsWith("=") ? 1 : 0);
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (let start = 0; start < text.length; start += 65536) {
    const bin = atob(text.slice(start, start + 65536));
    for (let i = 0; i < bin.length; i++) bytes[offset++] = bin.charCodeAt(i);
    await yieldStep();
  }
  return bytes.buffer;
}
async function normals(g) {
  const p = g.attributes.position.array, index = g.index.array, n = new Float32Array(p.length);
  for (let i = 0; i < index.length; i += 3) {
    if (i % 24576 === 0) await yieldStep();
    const a=index[i]*3,b=index[i+1]*3,c=index[i+2]*3;
    const x1=p[c]-p[b],y1=p[c+1]-p[b+1],z1=p[c+2]-p[b+2];
    const x2=p[a]-p[b],y2=p[a+1]-p[b+1],z2=p[a+2]-p[b+2];
    const x=y1*z2-z1*y2,y=z1*x2-x1*z2,z=x1*y2-y1*x2;
    for (const v of [a,b,c]) {n[v]+=x;n[v+1]+=y;n[v+2]+=z;}
  }
  for(let i=0;i<n.length;i+=3) {
    if(i%49152===0)await yieldStep();
    const len=Math.hypot(n[i],n[i+1],n[i+2])||1;n[i]/=len;n[i+1]/=len;n[i+2]/=len;
  }
  g.setAttribute("normal",new THREE.BufferAttribute(n,3));
}
async function bounds(g) {
  const p=g.attributes.position.array, min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
  for(let i=0;i<p.length;i+=3) {
    if(i%49152===0)await yieldStep();
    for(let axis=0;axis<3;axis++){min[axis]=Math.min(min[axis],p[i+axis]);max[axis]=Math.max(max[axis],p[i+axis]);}
  }
  if(!p.length){min.fill(0);max.fill(0);}
  const center=min.map((v,i)=>(v+max[i])/2);let distance=0;
  for(let i=0;i<p.length;i+=3) {
    if(i%49152===0)await yieldStep();
    distance=Math.max(distance,(p[i]-center[0])**2+(p[i+1]-center[1])**2+(p[i+2]-center[2])**2);
  }
  g.boundingBox=new THREE.Box3(new THREE.Vector3(...min),new THREE.Vector3(...max));
  g.boundingSphere=new THREE.Sphere(new THREE.Vector3(...center),Math.sqrt(distance));
}
async function prepare(p) {
  const key = p.id + ":" + p.revision;
  const g = new THREE.BufferGeometry();
  const positions = p.pb ? new Float32Array(await buffer(p.pb)) : new Float32Array(p.positions);
  const indices = p.ib ? new Uint32Array(await buffer(p.ib)) : new Uint32Array(p.indices);
  const faces = p.fb ? new Uint32Array(await buffer(p.fb)) : new Uint32Array(p.face_ids || []);
  g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  g.setIndex(new THREE.BufferAttribute(indices, 1));
  if (p.normals) g.setAttribute("normal", new THREE.Float32BufferAttribute(p.normals, 3));
  else if (p.smooth) await normals(g);
  await bounds(g);
  geometries.set(key, {g, faces});
  return {key, positions: positions.slice(), indices: indices.slice(), faces: faces.slice(),
    normals: g.attributes.normal?.array.slice(),
    box: [g.boundingBox.min.toArray(), g.boundingBox.max.toArray()],
    sphere: [g.boundingSphere.center.toArray(), g.boundingSphere.radius]};
}
async function pick(p) {
  const entry = geometries.get(p.key);
  if (!entry) return null;
  const {g, faces} = entry;
  const matrix=new THREE.Matrix4().fromArray(p.matrix);
  if(Math.abs(matrix.determinant())<1e-12)return null;
  const ray=new THREE.Ray(new THREE.Vector3(...p.origin),new THREE.Vector3(...p.direction)).applyMatrix4(matrix.clone().invert());
  if(!ray.intersectsBox(g.boundingBox))return null;
  const a=new THREE.Vector3(),b=new THREE.Vector3(),c=new THREE.Vector3(),hit=new THREE.Vector3();
  let closest=Infinity,face=null,point=null;
  for(let i=0;i<g.index.count;i+=3) {
    if(i%24576===0)await yieldStep();
    a.fromBufferAttribute(g.attributes.position,g.index.getX(i));b.fromBufferAttribute(g.attributes.position,g.index.getX(i+1));c.fromBufferAttribute(g.attributes.position,g.index.getX(i+2));
    if(!ray.intersectTriangle(a,b,c,true,hit))continue;
    const distance=hit.distanceToSquared(ray.origin);
    if(distance<closest){closest=distance;face=faces[i/3];point=hit.clone();}
  }
  if(face==null)return null;
  if(p.mode==="face")return {face};
  const edges = new Map(), vertices = new Set(), index = g.index;
  for (let i=0; i<faces.length; i++) {
    if(i%8192===0)await yieldStep();
    if (faces[i] === face) {
    const tri = [index.getX(i*3), index.getX(i*3+1), index.getX(i*3+2)];
    for(let j=0;j<3;j++) {
      vertices.add(tri[j]);
      const edge=[tri[j],tri[(j+1)%3]].sort((a,b)=>a-b), key=edge.join(",");
      edges.set(key,{edge,count:(edges.get(key)?.count||0)+1});
    }
  }
  }
  const vertex=id=>new THREE.Vector3().fromBufferAttribute(g.attributes.position,id);
  if(p.mode==="vert") return {verts:[[...vertices].sort((a,b)=>vertex(a).distanceToSquared(point)-vertex(b).distanceToSquared(point))[0]]};
  const distance=e=>new THREE.Line3(vertex(e.edge[0]),vertex(e.edge[1])).closestPointToPoint(point,true,new THREE.Vector3()).distanceToSquared(point);
  const candidates=[...edges.values()].filter(e=>e.count===1).sort((a,b)=>distance(a)-distance(b));
  return candidates.length?{edges:[candidates[0].edge]}:null;
}
async function lines(p) {
  const entry = geometries.get(p.key);
  if (!entry) return new Float32Array();
  const {g,faces}=entry, index=g.index.array, positions=g.attributes.position.array;
  const selected=new Set(), points=[], bounds=new THREE.Box3();
  const selectionFaces=p.selection.faces||[];
  for(let i=0;i<selectionFaces.length;i++){if(i%8192===0)await yieldStep();selected.add(selectionFaces[i]);}
  const point=new THREE.Vector3();
  let dense=false;
  const segment=(a,b)=>{
    for(const id of [a,b]) bounds.expandByPoint(point.fromArray(positions,id*3));
    if(points.length<360000) for(const id of [a,b]) points.push(positions[id*3],positions[id*3+1],positions[id*3+2]);
    else dense=true;
  };
  for(let i=0;i<faces.length;i++) {
    if(i%8192===0)await yieldStep();
    if(selected.has(faces[i])) {
    const a=index[i*3],b=index[i*3+1],c=index[i*3+2];segment(a,b);segment(b,c);segment(c,a);
  }
  }
  for(const [a,b] of p.selection.edges||[]) if(a>=0&&b>=0&&a*3<positions.length&&b*3<positions.length)segment(a,b);
  for(const id of p.selection.verts||[]) if(id>=0&&id*3<positions.length){
    bounds.expandByPoint(point.fromArray(positions,id*3));
    if(points.length>=360000){dense=true;continue;}
    for(let axis=0;axis<3;axis++)for(const sign of [-1,1])for(let d=0;d<3;d++)points.push(positions[id*3+d]+(axis===d?sign*6:0));
  }
  if(dense&&!bounds.isEmpty()) {
    points.length=0;
    const corners=Array.from({length:8},(_,i)=>[i&1?bounds.max.x:bounds.min.x,i&2?bounds.max.y:bounds.min.y,i&4?bounds.max.z:bounds.min.z]);
    for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit))points.push(...corners[i],...corners[i|bit]);
  }
  return {positions:new Float32Array(points),dense};
}
export async function runMeshTask(op, params) {
  if(op === "prepare") return prepare(params);
  if(op === "pick") return pick(params);
  if(op === "lines") return lines(params);
  if(op === "retain") {
    const keep = new Set(params.keys);
    for(const [key,{g}] of geometries) if(!keep.has(key)){g.dispose();geometries.delete(key);}
    return true;
  }
  throw new Error("Unknown mesh operation");
}
