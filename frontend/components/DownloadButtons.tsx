"use client";

import { useEffect, useState } from "react";
import { downloadUrl, isDownloadReady } from "../lib/api";

export default function DownloadButtons({
  excelUrl,
  pdfUrl,
}: {
  excelUrl: string;
  pdfUrl: string;
}) {
  const [excelReady, setExcelReady] = useState(false);
  const [pdfReady, setPdfReady] = useState(false);

  useEffect(() => {
    // The backend returns these URLs before the files exist - it builds
    // them in the background so the user isn't stuck waiting on
    // openpyxl/reportlab after everything else is already ready. Poll
    // until each one 404s no more, then swap it in as a real link.
    let cancelled = false;
    let excelDone = false;
    let pdfDone = false;
    let timeoutId: ReturnType<typeof setTimeout>;
    setExcelReady(false);
    setPdfReady(false);

    async function poll() {
      if (cancelled) return;
      const [excel, pdf] = await Promise.all([
        excelDone ? Promise.resolve(true) : isDownloadReady(excelUrl),
        pdfDone ? Promise.resolve(true) : isDownloadReady(pdfUrl),
      ]);
      if (cancelled) return;
      if (excel && !excelDone) {
        excelDone = true;
        setExcelReady(true);
      }
      if (pdf && !pdfDone) {
        pdfDone = true;
        setPdfReady(true);
      }
      if (!excelDone || !pdfDone) {
        timeoutId = setTimeout(poll, 3000);
      }
    }
    poll();

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [excelUrl, pdfUrl]);

  return (
    <div className="download-row">
      {excelReady ? (
        <a className="download-btn" href={downloadUrl(excelUrl)} target="_blank" rel="noreferrer">
          Download Excel workbook
        </a>
      ) : (
        <span className="download-btn download-btn-pending">Preparing Excel workbook…</span>
      )}
      {pdfReady ? (
        <a className="download-btn" href={downloadUrl(pdfUrl)} target="_blank" rel="noreferrer">
          Download PDF research note
        </a>
      ) : (
        <span className="download-btn download-btn-pending">Preparing PDF note…</span>
      )}
    </div>
  );
}
