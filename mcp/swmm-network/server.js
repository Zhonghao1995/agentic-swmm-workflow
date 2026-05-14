import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-network/scripts');
const importScript = path.join(scriptsDir, 'network_import.py');
const cityAdapterScript = path.join(scriptsDir, 'city_network_adapter.py');
const qaScript = path.join(scriptsDir, 'network_qa.py');
const exportScript = path.join(scriptsDir, 'network_to_inp.py');
const prepareStormInputsScript = path.join(scriptsDir, 'prepare_storm_inputs.py');
const reorientPipesScript = path.join(scriptsDir, 'reorient_pipes.py');
const inferOutfallScript = path.join(scriptsDir, 'infer_outfall.py');
const assignSubcatchmentOutletsScript = path.join(scriptsDir, 'assign_subcatchment_outlets.py');
const snapPipeEndpointsScript = path.join(scriptsDir, 'snap_pipe_endpoints.py');

function runPython(script, args) {
  const proc = spawnSync('python3', [script, ...args], { encoding: 'utf8' });
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || `python failed: ${proc.status}`).trim());
  }
  return proc.stdout;
}

const server = new McpServer({ name: 'swmm-network-mcp', version: '0.1.0' });

server.tool(
  'import_network',
  'Import conduit/junction/outfall data into the SWMM network JSON schema',
  {
    conduitsPath: z.string(),
    junctionsPath: z.string(),
    outfallsPath: z.string(),
    mappingPath: z.string(),
    outputPath: z.string().optional()
  },
  async ({ conduitsPath, junctionsPath, outfallsPath, mappingPath, outputPath }) => {
    const outJson = outputPath || path.join(os.tmpdir(), `swmm-network-${Date.now()}.json`);
    const out = runPython(importScript, [
      '--conduits', conduitsPath,
      '--junctions', junctionsPath,
      '--outfalls', outfallsPath,
      '--mapping', mappingPath,
      '--out', outJson
    ]);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outJson}` }
      ]
    };
  }
);

server.tool(
  'import_city_network',
  'Import structured city pipe/surface asset exports into dual-system-ready SWMM network JSON',
  {
    pipesCsvPath: z.string().optional(),
    pipesGeojsonPath: z.string().optional(),
    junctionsCsvPath: z.string().optional(),
    junctionsGeojsonPath: z.string().optional(),
    outfallsCsvPath: z.string().optional(),
    outfallsGeojsonPath: z.string().optional(),
    mappingPath: z.string(),
    outputPath: z.string().optional()
  },
  async ({
    pipesCsvPath,
    pipesGeojsonPath,
    junctionsCsvPath,
    junctionsGeojsonPath,
    outfallsCsvPath,
    outfallsGeojsonPath,
    mappingPath,
    outputPath
  }) => {
    const outJson = outputPath || path.join(os.tmpdir(), `swmm-city-network-${Date.now()}.json`);
    const args = ['--mapping-json', mappingPath, '--out', outJson];
    if (pipesCsvPath) args.push('--pipes-csv', pipesCsvPath);
    if (pipesGeojsonPath) args.push('--pipes-geojson', pipesGeojsonPath);
    if (junctionsCsvPath) args.push('--junctions-csv', junctionsCsvPath);
    if (junctionsGeojsonPath) args.push('--junctions-geojson', junctionsGeojsonPath);
    if (outfallsCsvPath) args.push('--outfalls-csv', outfallsCsvPath);
    if (outfallsGeojsonPath) args.push('--outfalls-geojson', outfallsGeojsonPath);
    const out = runPython(cityAdapterScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outJson}` }
      ]
    };
  }
);

server.tool(
  'qa',
  'Run QA checks on a SWMM network JSON model',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const out = runPython(qaScript, [networkJsonPath]);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'export_inp',
  'Export a SWMM network JSON model to INP sections',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const tmp = path.join(os.tmpdir(), `swmm-network-${Date.now()}.inp`);
    const out = runPython(exportScript, [networkJsonPath, '--out', tmp]);
    return { content: [{ type: 'text', text: out }, { type: 'text', text: `WROTE:${tmp}` }] };
  }
);

server.tool(
  'summary',
  'Return a lightweight summary of a SWMM network JSON model',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const obj = JSON.parse(fs.readFileSync(networkJsonPath, 'utf8'));
    const conduits = obj.conduits || [];
    const layers = [...new Set(conduits.map((c) => c.system_layer).filter(Boolean))].sort();
    const summary = {
      junction_count: (obj.junctions || []).length,
      outfall_count: (obj.outfalls || []).length,
      conduit_count: conduits.length,
      total_conduit_length: conduits.reduce((acc, c) => acc + Number(c.length || 0), 0),
      system_layers: layers,
      dual_system_ready: Boolean((obj.meta || {}).dual_system_ready || layers.length > 1)
    };
    return { content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }] };
  }
);

server.tool(
  'prepare_storm_inputs',
  'Clip raw municipal storm pipe (and optional manhole) shapefiles to a basin and emit adapter-ready GeoJSON plus a filled mapping.json. Does NOT pick the outfall (use infer_outfall) or fix flow direction (use reorient_pipes).',
  {
    pipesShpPath: z.string(),
    manholesShpPath: z.string().optional(),
    basinClipGeojsonPath: z.string(),
    mappingTemplatePath: z.string(),
    outDir: z.string(),
    caseName: z.string(),
    sourceDescription: z.string(),
    diameterPolicy: z.string().optional(),
  },
  async ({
    pipesShpPath,
    manholesShpPath,
    basinClipGeojsonPath,
    mappingTemplatePath,
    outDir,
    caseName,
    sourceDescription,
    diameterPolicy,
  }) => {
    const args = [
      '--pipes-shp', pipesShpPath,
      '--basin-clip', basinClipGeojsonPath,
      '--mapping-template', mappingTemplatePath,
      '--out-dir', outDir,
      '--case-name', caseName,
      '--source-description', sourceDescription,
    ];
    if (manholesShpPath) args.push('--manholes-shp', manholesShpPath);
    if (diameterPolicy) args.push('--diameter-policy', diameterPolicy);
    const out = runPython(prepareStormInputsScript, args);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'reorient_pipes',
  'Reorient LineString pipes so geometry direction matches flow direction (BFS from outfall vertices). Use after infer_outfall and before import_city_network when input pipes came from raw municipal shapefiles whose vertex order is digitisation order, not flow order.',
  {
    pipesGeojsonPath: z.string(),
    outfallsGeojsonPath: z.string(),
    outPath: z.string(),
    coordinatePrecision: z.number().int().min(0).max(9).optional(),
  },
  async ({ pipesGeojsonPath, outfallsGeojsonPath, outPath, coordinatePrecision }) => {
    const args = [
      '--pipes-geojson', pipesGeojsonPath,
      '--outfalls-geojson', outfallsGeojsonPath,
      '--out', outPath,
    ];
    if (coordinatePrecision !== undefined) {
      args.push('--coordinate-precision', String(coordinatePrecision));
    }
    const out = runPython(reorientPipesScript, args);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'infer_outfall',
  'Pick a single SWMM outfall point from a pipe LineString layer. Two modes: endpoint_nearest_watercourse (default; needs a watercourse geojson) or lowest_endpoint (uses minimum y coord, no watercourse needed). Emits outfalls.geojson with one Point (node_id=OUT1, type=FREE).',
  {
    pipesGeojsonPath: z.string(),
    watercourseGeojsonPath: z.string().optional(),
    mode: z.enum(['endpoint_nearest_watercourse', 'lowest_endpoint']).optional(),
    outPath: z.string(),
  },
  async ({ pipesGeojsonPath, watercourseGeojsonPath, mode, outPath }) => {
    const args = [
      '--pipes-geojson', pipesGeojsonPath,
      '--out', outPath,
    ];
    if (mode) args.push('--mode', mode);
    if (watercourseGeojsonPath) args.push('--watercourse-geojson', watercourseGeojsonPath);
    const out = runPython(inferOutfallScript, args);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'assign_subcatchment_outlets',
  'Rewrite the outlet column of a subcatchments CSV so each subcatchment drains into a real network node (not the literal outfall). Without this step the pipe network sits idle in the SWMM model. Three modes: nearest_junction (default; needs network.json), nearest_catch_basin (needs a manhole/catch-basin geojson), manual_lookup (needs a 2-col CSV).',
  {
    subcatchmentsCsvIn: z.string(),
    subcatchmentsGeojson: z.string(),
    outCsv: z.string(),
    mode: z.enum(['nearest_junction', 'nearest_catch_basin', 'manual_lookup']).optional(),
    networkJsonPath: z.string().optional(),
    includeOutfallsAsCandidates: z.boolean().optional(),
    candidatesGeojsonPath: z.string().optional(),
    candidatesIdField: z.string().optional(),
    lookupCsvPath: z.string().optional(),
  },
  async ({
    subcatchmentsCsvIn, subcatchmentsGeojson, outCsv, mode,
    networkJsonPath, includeOutfallsAsCandidates,
    candidatesGeojsonPath, candidatesIdField, lookupCsvPath,
  }) => {
    const args = [
      '--subcatchments-csv-in', subcatchmentsCsvIn,
      '--subcatchments-geojson', subcatchmentsGeojson,
      '--out-csv', outCsv,
    ];
    if (mode) args.push('--mode', mode);
    if (networkJsonPath) args.push('--network-json', networkJsonPath);
    if (includeOutfallsAsCandidates) args.push('--include-outfalls-as-candidates');
    if (candidatesGeojsonPath) args.push('--candidates-geojson', candidatesGeojsonPath);
    if (candidatesIdField) args.push('--candidates-id-field', candidatesIdField);
    if (lookupCsvPath) args.push('--lookup-csv', lookupCsvPath);
    const out = runPython(assignSubcatchmentOutletsScript, args);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'snap_pipe_endpoints',
  'Cluster nearby pipe LineString endpoints (sub-millimetre to centimetre vertex drift) and snap each cluster to its centroid so that adjacent pipes share identical endpoint coordinates. Without this, import_city_network treats drifting endpoints as separate junctions and the network ends up as disconnected fragments. Run between prepare_storm_inputs and infer_outfall.',
  {
    pipesGeojsonPath: z.string(),
    toleranceM: z.number().nonnegative(),
    outPath: z.string(),
  },
  async ({ pipesGeojsonPath, toleranceM, outPath }) => {
    const args = [
      '--pipes-geojson', pipesGeojsonPath,
      '--tolerance-m', String(toleranceM),
      '--out', outPath,
    ];
    const out = runPython(snapPipeEndpointsScript, args);
    return { content: [{ type: 'text', text: out }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
