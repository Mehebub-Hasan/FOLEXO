"use client";

import { useEffect, useRef } from "react";

import type { BoundingBox } from "@/lib/api-types";

interface DocumentViewerProps {
  fileUrl: string;
  mimeType: string;
  /** Every box to sketch faintly (header fields + line items). */
  boxes: BoundingBox[];
  /** The focused field's box, drawn in amber on top. */
  activeBox: BoundingBox | null;
}

/**
 * Left panel of the review split-screen: the source document with an SVG
 * overlay for extraction coordinates. Boxes use normalized 0–1 polygons
 * (docs/ARCHITECTURE.md §7); the overlay stretches with the container so
 * coordinates stay correct at any viewport size.
 *
 * For images the overlay is pixel-accurate. For PDFs we overlay an <iframe> —
 * cross-origin PDF rendering prevents page-exact alignment, so boxes are
 * drawn proportionally over the visible frame (best-effort for MVP; the
 * pdf.js page-accurate renderer is a Phase 5+ upgrade).
 */
export function DocumentViewer({ fileUrl, mimeType, boxes, activeBox }: DocumentViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (activeBox) {
      containerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [activeBox]);

  const isImage = mimeType.startsWith("image/");
  const toPoints = (box: BoundingBox) =>
    box.polygon
      .map(([x, y]) => `${(x * 100).toFixed(3)},${(y * 100).toFixed(3)}`)
      .join(" ");

  return (
    <div className="flex h-full flex-col rounded-lg border bg-background">
      <div className="border-b px-4 py-2 text-sm font-medium">Document</div>
      <div ref={containerRef} className="relative min-h-96 flex-1 bg-muted/40">
        {isImage ? (
          // eslint-disable-next-line @next/next/no-img-element -- signed URL from private bucket; next/image adds no value here
          <img src={fileUrl} alt="Invoice document" className="h-full w-full object-contain" />
        ) : fileUrl ? (
          <iframe title="Invoice document" src={fileUrl} className="h-full min-h-96 w-full" />
        ) : (
          <div className="flex h-full min-h-96 items-center justify-center text-sm text-muted-foreground">
            Document preview unavailable (storage unreachable) — data below is still
            editable.
          </div>
        )}

        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden
        >
          {boxes.map((box, index) => (
            <polygon
              key={`box-${index}`}
              points={toPoints(box)}
              className="fill-indigo-400/10 stroke-indigo-400/50"
              strokeWidth="0.3"
            />
          ))}
          {activeBox && (
            <polygon
              points={toPoints(activeBox)}
              className="fill-amber-300/30 stroke-amber-500"
              strokeWidth="0.8"
            />
          )}
        </svg>
      </div>
    </div>
  );
}
