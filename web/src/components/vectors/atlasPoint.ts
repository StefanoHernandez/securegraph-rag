export interface AtlasPoint {
  id: string;
  x: number;
  y: number | null;
  z: number | null;
  chunk_id: string;
  document_id: string;
  classification: string;
  text_preview: string;
  source_uri?: string | null;
  last_trace_id?: string | null;
}

export type AtlasViewMode = "2d" | "3d";
