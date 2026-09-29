import { runMeshTask } from "./mesh-engine.js";
self.onmessage = async ({data}) => {
  try {
    const result = await runMeshTask(data.op, data.params);
    const transfer = data.op === "prepare" ? [result.positions.buffer, result.indices.buffer, result.faces.buffer, ...(result.normals ? [result.normals.buffer] : [])] : [];
    if(data.op === "lines" && result.positions) transfer.push(result.positions.buffer);
    self.postMessage({id:data.id,result},transfer);
  } catch(error) { self.postMessage({id:data.id,error:String(error.message||error)}); }
};
self.postMessage({ready:true});
