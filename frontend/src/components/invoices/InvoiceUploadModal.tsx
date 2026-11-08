"use client";

import { AlertTriangle, FileUp, Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api, apiErrorMessage } from "@/lib/api";
import {
  type InvoiceCreateResponse,
  type PresignResponse,
} from "@/lib/api-types";
import { calculateFileSHA256 } from "@/lib/hash";
import { toast } from "sonner";

const ALLOWED_EXTENSIONS = ["pdf", "png", "jpg", "jpeg"] as const;
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024; // mirrors backend MAX_UPLOAD_BYTES

const EXTENSION_MIME: Record<string, string> = {
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
};

type Stage = "select" | "hashing" | "presigning" | "confirming" | "uploading" | "registering";

const STAGE_LABEL: Record<Exclude<Stage, "select" | "confirming">, string> = {
  hashing: "Computing checksum…",
  presigning: "Requesting upload URL…",
  uploading: "Uploading file…",
  registering: "Registering invoice…",
};

function mimeForFile(file: File): string {
  if (file.type) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_MIME[extension] ?? "application/octet-stream";
}

/** PUT the file straight to storage with XHR so we get real upload progress. */
function uploadWithProgress(
  uploadUrl: string,
  file: File,
  onProgress: (percent: number) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", uploadUrl);
    xhr.setRequestHeader("Content-Type", mimeForFile(file));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Storage rejected the upload (HTTP ${xhr.status}).`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed due to a network error."));
    xhr.send(file);
  });
}

function validateFile(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTENSIONS.includes(extension as (typeof ALLOWED_EXTENSIONS)[number])) {
    return "Unsupported file type. Allowed: PDF, PNG, JPG.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "File exceeds the 15 MB upload limit.";
  }
  if (file.size === 0) {
    return "That file is empty.";
  }
  return null;
}

interface InvoiceUploadModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface PendingDuplicate {
  presign: PresignResponse;
  sha256: string;
}

export function InvoiceUploadModal({ open, onOpenChange }: InvoiceUploadModalProps) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [stage, setStage] = useState<Stage>("select");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [pending, setPending] = useState<PendingDuplicate | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const busy = stage === "hashing" || stage === "presigning" || stage === "uploading" || stage === "registering";

  // Reset in the event handler (not an effect): state clears exactly when the
  // modal is asked to close.
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setFile(null);
      setStage("select");
      setProgress(0);
      setError(null);
      setPending(null);
    }
    onOpenChange(next);
  };

  const handleFiles = (files: FileList | null) => {
    const selected = files?.[0];
    if (!selected) return;
    const validationError = validateFile(selected);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    setFile(selected);
    setError(null);
    setStage("select");
  };

  const runUpload = async (fileToUpload: File, presign: PresignResponse, sha256: string) => {
    setStage("uploading");
    setProgress(0);
    await uploadWithProgress(presign.upload_url, fileToUpload, setProgress);

    setStage("registering");
    const { data } = await api.post<InvoiceCreateResponse>("/api/v1/invoices", {
      storage_path: presign.storage_path,
      file_hash: sha256,
      original_filename: fileToUpload.name,
      mime_type: mimeForFile(fileToUpload),
      file_size_bytes: fileToUpload.size,
    });

    queryClient.invalidateQueries({ queryKey: ["invoices"] });
    toast.success("Invoice uploaded", {
      description: `${fileToUpload.name} is now ${data.invoice.status.toUpperCase()}.`,
    });
    handleOpenChange(false);
  };

  const startUpload = async (confirmDuplicate = false) => {
    if (!file || busy) return;
    setError(null);
    try {
      setStage("hashing");
      const sha256 = await calculateFileSHA256(file);

      setStage("presigning");
      const { data: presign } = await api.post<PresignResponse>(
        "/api/v1/uploads/presign",
        {
          filename: file.name,
          file_size_bytes: file.size,
          mime_type: mimeForFile(file),
          sha256_hash: sha256,
        }
      );

      if (presign.is_duplicate && !confirmDuplicate) {
        setPending({ presign, sha256 });
        setStage("confirming");
        return;
      }

      await runUpload(file, presign, sha256);
    } catch (uploadError) {
      setError(apiErrorMessage(uploadError));
      setStage("select");
    }
  };

  const handleConfirmDuplicate = async () => {
    if (!file || !pending) return;
    setError(null);
    try {
      await runUpload(file, pending.presign, pending.sha256);
    } catch (uploadError) {
      setError(apiErrorMessage(uploadError));
      setStage("select");
    }
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    if (busy) return;
    handleFiles(event.dataTransfer.files);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && handleOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Upload invoice</DialogTitle>
          <DialogDescription>
            PDF, PNG or JPG up to 15 MB. The file goes directly to encrypted storage — the
            server never sees the bytes.
          </DialogDescription>
        </DialogHeader>

        {stage === "confirming" && pending ? (
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
              <p>
                This exact document was already uploaded as invoice{" "}
                <span className="font-mono text-xs">{pending.presign.existing_invoice_id}</span>.
                Upload anyway?
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setStage("select");
                  setPending(null);
                }}
              >
                Cancel
              </Button>
              <Button onClick={handleConfirmDuplicate}>Continue upload</Button>
            </DialogFooter>
          </div>
        ) : stage === "select" ? (
          <div className="flex flex-col gap-4">
            <div
              role="button"
              tabIndex={0}
              onClick={() => !busy && inputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
              className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
                dragActive ? "border-primary bg-accent" : "border-border hover:bg-muted/50"
              }`}
            >
              <FileUp className="size-6 text-muted-foreground" aria-hidden />
              <p className="text-sm font-medium">
                Drag &amp; drop your invoice here, or click to browse
              </p>
              <p className="text-xs text-muted-foreground">PDF, PNG or JPG · max 15 MB</p>
              {file && (
                <p className="mt-1 text-xs text-foreground">
                  Selected: <span className="font-medium">{file.name}</span> (
                  {(file.size / (1024 * 1024)).toFixed(2)} MB)
                </p>
              )}
            </div>
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/jpeg,image/png"
              className="hidden"
              onChange={(event) => handleFiles(event.target.files)}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
                Cancel
              </Button>
              <Button onClick={() => startUpload(false)} disabled={!file || busy}>
                {busy ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Upload className="size-4" aria-hidden />
                )}
                Upload
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="flex flex-col gap-3 py-2">
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              {STAGE_LABEL[stage as Exclude<Stage, "select" | "confirming">]}
            </p>
            {stage === "uploading" && (
              <>
                <Progress value={progress} className="h-2" />
                <p className="text-right text-xs text-muted-foreground">{progress}%</p>
              </>
            )}
            <DialogFooter>
              <Button variant="outline" disabled onClick={() => undefined}>
                Working…
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
