"use client";

import { BaseEdge, EdgeLabelRenderer, EdgeProps, getBezierPath } from "@xyflow/react";

export function PulseEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, markerEnd, label }: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.34 });
  return (
    <>
      <path className="pulse-edge-glow" d={path} />
      <BaseEdge id={id} path={path} markerEnd={markerEnd} className={`pulse-edge-main ${selected ? "is-selected" : ""}`} />
      <path className="pulse-edge-runner" d={path} />
      {label ? <EdgeLabelRenderer><span className="edge-label" style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}>{String(label)}</span></EdgeLabelRenderer> : null}
    </>
  );
}
