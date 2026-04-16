import { EditorWorkspace } from "@/components/editor-workspace";

export default function EditorPage({
  searchParams
}: {
  searchParams?: { type?: string };
}) {
  return <EditorWorkspace initialDocType={searchParams?.type} />;
}
