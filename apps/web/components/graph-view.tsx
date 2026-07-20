"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { ProjectDetail } from "@/lib/types";

export function GraphView({ graph }: { graph: ProjectDetail["graph"] }) {
  const nodes = useMemo<Node[]>(
    () =>
      graph.nodes.map((item, index) => ({
        id: item.id,
        position: { x: (index % 4) * 250, y: Math.floor(index / 4) * 160 },
        data: { label: item.name },
        style: {
          width: 190,
          border: "1px solid #cbd5e1",
          borderRadius: 16,
          padding: 14,
          background: item.provenance === "source_supported" ? "#ecfdf5" : "#ffffff",
          color: "#18212f",
          fontWeight: 650,
          boxShadow: "0 8px 26px rgba(30, 41, 59, .08)",
        },
      })),
    [graph.nodes],
  );
  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((item) => ({
        id: item.id,
        source: item.source,
        target: item.target,
        label: item.type.replaceAll("_", " "),
        markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
        style: { stroke: "#64748b" },
        labelStyle: { fill: "#475569", fontSize: 11, fontWeight: 600 },
      })),
    [graph.edges],
  );
  if (!nodes.length) {
    return <EmptyGraph />;
  }
  return (
    <div className="h-[620px] overflow-hidden rounded-2xl border border-border bg-slate-50">
      <ReactFlow nodes={nodes} edges={edges} fitView minZoom={0.25} maxZoom={1.8}>
        <Background color="#dbe3ed" gap={22} />
        <MiniMap pannable zoomable className="!rounded-xl !border !border-border" />
        <Controls className="!overflow-hidden !rounded-xl !border-border !shadow-sm" />
      </ReactFlow>
    </div>
  );
}

function EmptyGraph() {
  return (
    <div className="grid h-[520px] place-items-center rounded-2xl border border-dashed border-slate-300 bg-slate-50">
      <div className="max-w-sm text-center">
        <p className="font-serif text-2xl font-semibold">The graph will grow here</p>
        <p className="mt-2 text-sm leading-6 text-slate-500">Only concepts connected to validated evidence are added automatically.</p>
      </div>
    </div>
  );
}

