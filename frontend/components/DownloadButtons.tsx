import { downloadUrl } from "../lib/api";

export default function DownloadButtons({
  excelUrl,
  pdfUrl,
}: {
  excelUrl: string;
  pdfUrl: string;
}) {
  return (
    <div className="download-row">
      <a className="download-btn" href={downloadUrl(excelUrl)} target="_blank" rel="noreferrer">
        Download Excel workbook
      </a>
      <a className="download-btn" href={downloadUrl(pdfUrl)} target="_blank" rel="noreferrer">
        Download PDF research note
      </a>
    </div>
  );
}
