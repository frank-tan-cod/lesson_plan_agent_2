import { DocumentsView } from "@/components/documents-view";

import type { DocType } from "@/lib/types";

interface DocumentsPageProps {
  searchParams?: { type?: string };
}

function normalizeDocType(value?: string): DocType {
  return value === "presentation" ? "presentation" : "lesson";
}

export default function DocumentsPage({ searchParams }: DocumentsPageProps) {
  return <DocumentsView initialDocType={normalizeDocType(searchParams?.type)} />;
}
