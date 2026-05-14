#!/usr/bin/env node
/** MCP server for swmm-gis skill.
 * Tools:
 * - gis_preprocess_subcatchments
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const preprocessPy = path.resolve(__dirname, "../../skills/swmm-gis/scripts/preprocess_subcatchments.py");
const qgisPrepPy = path.resolve(__dirname, "../../skills/swmm-gis/scripts/qgis_prepare_swmm_inputs.py");
const qgisPackageFinalPy = path.resolve(__dirname, "../../skills/swmm-gis/scripts/qgis_package_final_layers.py");
const areaWeightedParamsPy = path.resolve(__dirname, "../../skills/swmm-gis/scripts/area_weighted_swmm_params.py");
const basinShpToSubcatchmentsPy = path.resolve(__dirname, "../../skills/swmm-gis/scripts/basin_shp_to_subcatchments.py");

function runPy(scriptPath, args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [scriptPath, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (d) => (stdout += d.toString()));
    p.stderr.on("data", (d) => (stderr += d.toString()));
    p.on("error", reject);
    p.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `python rc=${code}`));
      else resolve(stdout);
    });
  });
}

const PreprocessArgs = z.object({
  subcatchmentsGeojson: z.string(),
  networkJson: z.string(),
  outCsv: z.string(),
  outJson: z.string(),
  idField: z.string().default("subcatchment_id"),
  outletHintField: z.string().default("outlet_hint"),
  demStatsJson: z.string().optional(),
  demStatsIdField: z.string().default("subcatchment_id"),
  defaultSlopePct: z.number().positive().optional(),
  minSlopePct: z.number().positive().optional(),
  minWidthM: z.number().positive().optional(),
  defaultCurbLengthM: z.number().min(0).optional(),
  defaultRainGage: z.string().optional(),
  maxLinkDistanceM: z.number().positive().optional(),
});

const QgisLoadArgs = z.object({
  out: z.string(),
  dem: z.string().optional(),
  boundary: z.string().optional(),
  subcatchments: z.string().optional(),
  landuse: z.string().optional(),
  soil: z.string().optional(),
  outlet: z.string().optional(),
  rainfall: z.string().optional(),
  network: z.string().optional()
});

const QgisValidateCrsArgs = z.object({
  layersManifest: z.string(),
  out: z.string()
});

const QgisNormalizeLayersArgs = z.object({
  dem: z.string(),
  boundary: z.string(),
  landuse: z.string(),
  soil: z.string(),
  outDir: z.string(),
  targetCrs: z.string().optional(),
  targetResolution: z.number().positive().optional(),
  demResampling: z.number().int().min(0).max(11).default(1),
  categoricalResampling: z.number().int().min(0).max(11).default(0),
  qgisProcess: z.string().optional(),
  projLib: z.string().optional(),
  gisbase: z.string().optional()
});

const QgisOverlayArgs = z.object({
  subcatchmentsGeojson: z.string(),
  outLanduseCsv: z.string(),
  outSoilCsv: z.string(),
  idField: z.string().default("subcatchment_id"),
  landuseField: z.string().default("landuse_class"),
  soilField: z.string().default("soil_texture")
});

const QgisImportDrainageArgs = z.object({
  networkJson: z.string(),
  outNetworkJson: z.string(),
  outQaJson: z.string()
});

const QgisExportArgs = z.object({
  runDir: z.string(),
  caseId: z.string().default("qgis-case"),
  subcatchmentsGeojson: z.string(),
  networkJson: z.string(),
  dem: z.string().optional(),
  landuseLayer: z.string().optional(),
  soilLayer: z.string().optional(),
  outlet: z.string().optional(),
  rainfall: z.string().optional(),
  idField: z.string().default("subcatchment_id"),
  outletHintField: z.string().default("outlet_hint"),
  landuseField: z.string().default("landuse_class"),
  soilField: z.string().default("soil_texture"),
  defaultRainGage: z.string().default("RG1"),
  defaultSlopePct: z.number().positive().optional(),
  minSlopePct: z.number().positive().optional(),
  minWidthM: z.number().positive().optional(),
  strictCrs: z.boolean().default(false)
});

const QgisPackageFinalLayersArgs = z.object({
  caseId: z.string(),
  subcatchments: z.string(),
  stream: z.string(),
  accumulation: z.string(),
  slope: z.string(),
  finalDir: z.string(),
  title: z.string().default(""),
  noOverview: z.boolean().default(false)
});

const QgisAreaWeightedParamsArgs = z.object({
  subcatchments: z.string(),
  landuse: z.string(),
  soil: z.string(),
  outDir: z.string(),
  idField: z.string().default("basin_id"),
  landuseField: z.string().default("CLASS"),
  soilField: z.string().default("TEXTURE"),
  landuseLookup: z.string().optional(),
  soilLookup: z.string().optional(),
  strict: z.boolean().default(false)
});

const BasinShpToSubcatchmentsArgs = z.object({
  basinShp: z.string(),
  outGeojson: z.string(),
  outCsv: z.string(),
  mode: z.enum(["by_id_field", "by_index", "largest", "all"]).default("by_id_field"),
  idField: z.string().default("OBJECTID"),
  idValue: z.union([z.string(), z.number()]).optional(),
  index: z.number().int().nonnegative().optional(),
  idPrefix: z.string().default("S"),
  outletNodeId: z.string().default("OUT1"),
  rainGageId: z.string().default("RG1"),
  defaultSlopePct: z.number().default(1.0),
  widthMethod: z.enum(["sqrt_area"]).default("sqrt_area"),
});

const server = new Server(
  { name: "swmm-gis-mcp", version: "0.2.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "gis_preprocess_subcatchments",
        description: "Convert subcatchment polygon GeoJSON + network JSON into builder-ready subcatchment CSV with deterministic width/slope/outlet linking.",
        inputSchema: {
          type: "object",
          properties: {
            subcatchmentsGeojson: { type: "string" },
            networkJson: { type: "string" },
            outCsv: { type: "string" },
            outJson: { type: "string" },
            idField: { type: "string", default: "subcatchment_id" },
            outletHintField: { type: "string", default: "outlet_hint" },
            demStatsJson: { type: "string" },
            demStatsIdField: { type: "string", default: "subcatchment_id" },
            defaultSlopePct: { type: "number", minimum: 0.000001 },
            minSlopePct: { type: "number", minimum: 0.000001 },
            minWidthM: { type: "number", minimum: 0.000001 },
            defaultCurbLengthM: { type: "number", minimum: 0 },
            defaultRainGage: { type: "string" },
            maxLinkDistanceM: { type: "number", minimum: 0.000001 }
          },
          required: ["subcatchmentsGeojson", "networkJson", "outCsv", "outJson"]
        }
      },
      {
        name: "qgis_load_layers",
        description: "Validate raw/QGIS-exported source layer paths and shapefile sidecars before SWMM data preparation.",
        inputSchema: {
          type: "object",
          properties: {
            out: { type: "string" },
            dem: { type: "string" },
            boundary: { type: "string" },
            subcatchments: { type: "string" },
            landuse: { type: "string" },
            soil: { type: "string" },
            outlet: { type: "string" },
            rainfall: { type: "string" },
            network: { type: "string" }
          },
          required: ["out"]
        }
      },
      {
        name: "qgis_validate_crs",
        description: "Write a CRS consistency report from a qgis_load_layers manifest.",
        inputSchema: {
          type: "object",
          properties: {
            layersManifest: { type: "string" },
            out: { type: "string" }
          },
          required: ["layersManifest", "out"]
        }
      },
      {
        name: "qgis_normalize_layers",
        description: "Use QGIS Processing to reproject DEM, boundary, land-use, and soil layers to one CRS and clip them by the boundary.",
        inputSchema: {
          type: "object",
          properties: {
            dem: { type: "string" },
            boundary: { type: "string" },
            landuse: { type: "string" },
            soil: { type: "string" },
            outDir: { type: "string" },
            targetCrs: { type: "string", description: "Optional target CRS auth id, WKT/PROJ string, or layer path. Defaults to boundary CRS." },
            targetResolution: { type: "number", minimum: 0 },
            demResampling: { type: "integer", minimum: 0, maximum: 11, default: 1 },
            categoricalResampling: { type: "integer", minimum: 0, maximum: 11, default: 0 },
            qgisProcess: { type: "string" },
            projLib: { type: "string" },
            gisbase: { type: "string" }
          },
          required: ["dem", "boundary", "landuse", "soil", "outDir"]
        }
      },
      {
        name: "qgis_overlay_landuse_soil",
        description: "Extract land-use and soil attributes from a QGIS overlay subcatchment GeoJSON into swmm-params CSV inputs.",
        inputSchema: {
          type: "object",
          properties: {
            subcatchmentsGeojson: { type: "string" },
            outLanduseCsv: { type: "string" },
            outSoilCsv: { type: "string" },
            idField: { type: "string", default: "subcatchment_id" },
            landuseField: { type: "string", default: "landuse_class" },
            soilField: { type: "string", default: "soil_texture" }
          },
          required: ["subcatchmentsGeojson", "outLanduseCsv", "outSoilCsv"]
        }
      },
      {
        name: "qgis_extract_slope_area_width",
        description: "Extract builder-ready subcatchment area, width, slope, and outlet links from QGIS-exported subcatchment polygons.",
        inputSchema: {
          type: "object",
          properties: {
            subcatchmentsGeojson: { type: "string" },
            networkJson: { type: "string" },
            outCsv: { type: "string" },
            outJson: { type: "string" },
            idField: { type: "string", default: "subcatchment_id" },
            outletHintField: { type: "string", default: "outlet_hint" },
            defaultRainGage: { type: "string" },
            defaultSlopePct: { type: "number", minimum: 0.000001 },
            minSlopePct: { type: "number", minimum: 0.000001 },
            minWidthM: { type: "number", minimum: 0.000001 }
          },
          required: ["subcatchmentsGeojson", "networkJson", "outCsv", "outJson"]
        }
      },
      {
        name: "qgis_import_drainage_assets",
        description: "Copy/import a prepared drainage network JSON and run swmm-network QA.",
        inputSchema: {
          type: "object",
          properties: {
            networkJson: { type: "string" },
            outNetworkJson: { type: "string" },
            outQaJson: { type: "string" }
          },
          required: ["networkJson", "outNetworkJson", "outQaJson"]
        }
      },
      {
        name: "qgis_export_swmm_intermediates",
        description: "Run the complete MVP QGIS data-side bridge and export 01_gis, 02_params, and 04_network artifacts for Agentic SWMM.",
        inputSchema: {
          type: "object",
          properties: {
            runDir: { type: "string" },
            caseId: { type: "string", default: "qgis-case" },
            subcatchmentsGeojson: { type: "string" },
            networkJson: { type: "string" },
            dem: { type: "string" },
            landuseLayer: { type: "string" },
            soilLayer: { type: "string" },
            outlet: { type: "string" },
            rainfall: { type: "string" },
            idField: { type: "string", default: "subcatchment_id" },
            outletHintField: { type: "string", default: "outlet_hint" },
            landuseField: { type: "string", default: "landuse_class" },
            soilField: { type: "string", default: "soil_texture" },
            defaultRainGage: { type: "string", default: "RG1" },
            defaultSlopePct: { type: "number", minimum: 0.000001 },
            minSlopePct: { type: "number", minimum: 0.000001 },
            minWidthM: { type: "number", minimum: 0.000001 },
            strictCrs: { type: "boolean", default: false }
          },
          required: ["runDir", "subcatchmentsGeojson", "networkJson"]
        }
      },
      {
        name: "qgis_package_final_layers",
        description: "Package QGIS/GRASS watershed outputs into a clean final_layers folder for SWMM/GIS use: subcatchments.shp, flow.shp, slope_percent.tif, outfall.shp, overview.png, and manifest.json.",
        inputSchema: {
          type: "object",
          properties: {
            caseId: { type: "string" },
            subcatchments: { type: "string", description: "Source subcatchment shapefile from the standard or entropy partition." },
            stream: { type: "string", description: "QGIS/GRASS stream raster, e.g. 01_gis/threshold_sweep/stream_100.tif." },
            accumulation: { type: "string", description: "QGIS/GRASS flow accumulation raster, e.g. 01_gis/threshold_sweep/acc_100.tif." },
            slope: { type: "string", description: "Slope percent raster generated from the DEM." },
            finalDir: { type: "string", description: "Output folder, conventionally runs/<case>/final_layers." },
            title: { type: "string", default: "" },
            noOverview: { type: "boolean", default: false }
          },
          required: ["caseId", "subcatchments", "stream", "accumulation", "slope", "finalDir"]
        }
      },
      {
        name: "qgis_area_weighted_params",
        description: "Intersect subcatchments with landuse and soil polygons, compute area fractions, and write area-weighted SWMM params JSON plus audit CSVs.",
        inputSchema: {
          type: "object",
          properties: {
            subcatchments: { type: "string", description: "Subcatchment polygon layer, e.g. final_layers/subcatchments.shp." },
            landuse: { type: "string", description: "Land-use polygon layer." },
            soil: { type: "string", description: "Soil polygon layer." },
            outDir: { type: "string", description: "Output folder for weighted_params.json and area-weight audit CSVs." },
            idField: { type: "string", default: "basin_id" },
            landuseField: { type: "string", default: "CLASS" },
            soilField: { type: "string", default: "TEXTURE" },
            landuseLookup: { type: "string" },
            soilLookup: { type: "string" },
            strict: { type: "boolean", default: false }
          },
          required: ["subcatchments", "landuse", "soil", "outDir"]
        }
      },
      {
        name: "basin_shp_to_subcatchments",
        description: "Pick one or more polygons from a municipal basin shapefile and emit SWMM-ready subcatchments.geojson + subcatchments.csv. Width defaults to sqrt(area_m2); slope defaults to 1 percent.",
        inputSchema: {
          type: "object",
          properties: {
            basinShp: { type: "string", description: "Basin shapefile or geojson path (must have a projected CRS)." },
            outGeojson: { type: "string" },
            outCsv: { type: "string" },
            mode: { type: "string", enum: ["by_id_field", "by_index", "largest", "all"], default: "by_id_field" },
            idField: { type: "string", default: "OBJECTID", description: "Field name used by mode=by_id_field." },
            idValue: { type: ["string", "number"], description: "Value to match when mode=by_id_field." },
            index: { type: "integer", minimum: 0, description: "0-based feature index when mode=by_index." },
            idPrefix: { type: "string", default: "S", description: "Prefix for generated subcatchment IDs." },
            outletNodeId: { type: "string", default: "OUT1" },
            rainGageId: { type: "string", default: "RG1" },
            defaultSlopePct: { type: "number", default: 1.0 },
            widthMethod: { type: "string", enum: ["sqrt_area"], default: "sqrt_area" }
          },
          required: ["basinShp", "outGeojson", "outCsv"]
        }
      },
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: raw } = req.params;

  if (name === "gis_preprocess_subcatchments") {
    const a = PreprocessArgs.parse(raw ?? {});

    fs.mkdirSync(path.dirname(a.outCsv), { recursive: true });
    fs.mkdirSync(path.dirname(a.outJson), { recursive: true });

    const args = [
      "--subcatchments-geojson", a.subcatchmentsGeojson,
      "--network-json", a.networkJson,
      "--out-csv", a.outCsv,
      "--out-json", a.outJson,
      "--id-field", a.idField,
      "--outlet-hint-field", a.outletHintField,
      "--dem-stats-id-field", a.demStatsIdField,
    ];
    if (a.demStatsJson !== undefined) args.push("--dem-stats-json", a.demStatsJson);
    if (a.defaultSlopePct !== undefined) args.push("--default-slope-pct", String(a.defaultSlopePct));
    if (a.minSlopePct !== undefined) args.push("--min-slope-pct", String(a.minSlopePct));
    if (a.minWidthM !== undefined) args.push("--min-width-m", String(a.minWidthM));
    if (a.defaultCurbLengthM !== undefined) args.push("--default-curb-length-m", String(a.defaultCurbLengthM));
    if (a.defaultRainGage !== undefined) args.push("--default-rain-gage", a.defaultRainGage);
    if (a.maxLinkDistanceM !== undefined) args.push("--max-link-distance-m", String(a.maxLinkDistanceM));

    const stdout = await runPy(preprocessPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_load_layers") {
    const a = QgisLoadArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.out), { recursive: true });
    const args = ["load-layers", "--out", a.out];
    for (const [flag, value] of Object.entries({
      dem: a.dem,
      boundary: a.boundary,
      subcatchments: a.subcatchments,
      landuse: a.landuse,
      soil: a.soil,
      outlet: a.outlet,
      rainfall: a.rainfall,
      network: a.network
    })) {
      if (value !== undefined) args.push(`--${flag}`, value);
    }
    const stdout = await runPy(qgisPrepPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_validate_crs") {
    const a = QgisValidateCrsArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.out), { recursive: true });
    const stdout = await runPy(qgisPrepPy, [
      "validate-crs",
      "--layers-manifest", a.layersManifest,
      "--out", a.out
    ]);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_normalize_layers") {
    const a = QgisNormalizeLayersArgs.parse(raw ?? {});
    fs.mkdirSync(a.outDir, { recursive: true });
    const args = [
      "normalize-layers",
      "--dem", a.dem,
      "--boundary", a.boundary,
      "--landuse", a.landuse,
      "--soil", a.soil,
      "--out-dir", a.outDir,
      "--dem-resampling", String(a.demResampling),
      "--categorical-resampling", String(a.categoricalResampling)
    ];
    if (a.targetCrs !== undefined) args.push("--target-crs", a.targetCrs);
    if (a.targetResolution !== undefined) args.push("--target-resolution", String(a.targetResolution));
    if (a.qgisProcess !== undefined) args.push("--qgis-process", a.qgisProcess);
    if (a.projLib !== undefined) args.push("--proj-lib", a.projLib);
    if (a.gisbase !== undefined) args.push("--gisbase", a.gisbase);
    const stdout = await runPy(qgisPrepPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_overlay_landuse_soil") {
    const a = QgisOverlayArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.outLanduseCsv), { recursive: true });
    fs.mkdirSync(path.dirname(a.outSoilCsv), { recursive: true });
    const stdout = await runPy(qgisPrepPy, [
      "overlay-landuse-soil",
      "--subcatchments-geojson", a.subcatchmentsGeojson,
      "--out-landuse-csv", a.outLanduseCsv,
      "--out-soil-csv", a.outSoilCsv,
      "--id-field", a.idField,
      "--landuse-field", a.landuseField,
      "--soil-field", a.soilField
    ]);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_extract_slope_area_width") {
    const a = PreprocessArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.outCsv), { recursive: true });
    fs.mkdirSync(path.dirname(a.outJson), { recursive: true });
    const args = [
      "--subcatchments-geojson", a.subcatchmentsGeojson,
      "--network-json", a.networkJson,
      "--out-csv", a.outCsv,
      "--out-json", a.outJson,
      "--id-field", a.idField,
      "--outlet-hint-field", a.outletHintField,
      "--dem-stats-id-field", a.demStatsIdField,
    ];
    if (a.demStatsJson !== undefined) args.push("--dem-stats-json", a.demStatsJson);
    if (a.defaultSlopePct !== undefined) args.push("--default-slope-pct", String(a.defaultSlopePct));
    if (a.minSlopePct !== undefined) args.push("--min-slope-pct", String(a.minSlopePct));
    if (a.minWidthM !== undefined) args.push("--min-width-m", String(a.minWidthM));
    if (a.defaultCurbLengthM !== undefined) args.push("--default-curb-length-m", String(a.defaultCurbLengthM));
    if (a.defaultRainGage !== undefined) args.push("--default-rain-gage", a.defaultRainGage);
    if (a.maxLinkDistanceM !== undefined) args.push("--max-link-distance-m", String(a.maxLinkDistanceM));
    const stdout = await runPy(preprocessPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_import_drainage_assets") {
    const a = QgisImportDrainageArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.outNetworkJson), { recursive: true });
    fs.mkdirSync(path.dirname(a.outQaJson), { recursive: true });
    const stdout = await runPy(qgisPrepPy, [
      "import-drainage-assets",
      "--network-json", a.networkJson,
      "--out-network-json", a.outNetworkJson,
      "--out-qa-json", a.outQaJson
    ]);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_export_swmm_intermediates") {
    const a = QgisExportArgs.parse(raw ?? {});
    const args = [
      "export-swmm-intermediates",
      "--run-dir", a.runDir,
      "--case-id", a.caseId,
      "--subcatchments-geojson", a.subcatchmentsGeojson,
      "--network-json", a.networkJson,
      "--id-field", a.idField,
      "--outlet-hint-field", a.outletHintField,
      "--landuse-field", a.landuseField,
      "--soil-field", a.soilField,
      "--default-rain-gage", a.defaultRainGage
    ];
    if (a.dem !== undefined) args.push("--dem", a.dem);
    if (a.landuseLayer !== undefined) args.push("--landuse-layer", a.landuseLayer);
    if (a.soilLayer !== undefined) args.push("--soil-layer", a.soilLayer);
    if (a.outlet !== undefined) args.push("--outlet", a.outlet);
    if (a.rainfall !== undefined) args.push("--rainfall", a.rainfall);
    if (a.defaultSlopePct !== undefined) args.push("--default-slope-pct", String(a.defaultSlopePct));
    if (a.minSlopePct !== undefined) args.push("--min-slope-pct", String(a.minSlopePct));
    if (a.minWidthM !== undefined) args.push("--min-width-m", String(a.minWidthM));
    if (a.strictCrs) args.push("--strict-crs");
    const stdout = await runPy(qgisPrepPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_package_final_layers") {
    const a = QgisPackageFinalLayersArgs.parse(raw ?? {});
    fs.mkdirSync(a.finalDir, { recursive: true });
    const args = [
      "--case-id", a.caseId,
      "--subcatchments", a.subcatchments,
      "--stream", a.stream,
      "--accumulation", a.accumulation,
      "--slope", a.slope,
      "--final-dir", a.finalDir
    ];
    if (a.title !== "") args.push("--title", a.title);
    if (a.noOverview) args.push("--no-overview");
    const stdout = await runPy(qgisPackageFinalPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "qgis_area_weighted_params") {
    const a = QgisAreaWeightedParamsArgs.parse(raw ?? {});
    fs.mkdirSync(a.outDir, { recursive: true });
    const args = [
      "--subcatchments", a.subcatchments,
      "--landuse", a.landuse,
      "--soil", a.soil,
      "--out-dir", a.outDir,
      "--id-field", a.idField,
      "--landuse-field", a.landuseField,
      "--soil-field", a.soilField
    ];
    if (a.landuseLookup !== undefined) args.push("--landuse-lookup", a.landuseLookup);
    if (a.soilLookup !== undefined) args.push("--soil-lookup", a.soilLookup);
    if (a.strict) args.push("--strict");
    const stdout = await runPy(areaWeightedParamsPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "basin_shp_to_subcatchments") {
    const a = BasinShpToSubcatchmentsArgs.parse(raw ?? {});
    fs.mkdirSync(path.dirname(a.outGeojson), { recursive: true });
    fs.mkdirSync(path.dirname(a.outCsv), { recursive: true });
    const args = [
      "--basin-shp", a.basinShp,
      "--out-geojson", a.outGeojson,
      "--out-csv", a.outCsv,
      "--mode", a.mode,
      "--id-field", a.idField,
      "--id-prefix", a.idPrefix,
      "--outlet-node-id", a.outletNodeId,
      "--rain-gage-id", a.rainGageId,
      "--default-slope-pct", String(a.defaultSlopePct),
      "--width-method", a.widthMethod,
    ];
    if (a.idValue !== undefined) args.push("--id-value", String(a.idValue));
    if (a.index !== undefined) args.push("--index", String(a.index));
    const stdout = await runPy(basinShpToSubcatchmentsPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
